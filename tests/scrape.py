# Copyright 2005-2012 Ka-Ping Yee
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Python module for web browsing and scraping.

Done:
  - navigate to absolute and relative URLs
  - follow links in page or region
  - find strings or regular expressions: first, all, split
  - find tags: first, last, next, previous, all, splittag
  - find elements: first, last, next, previous, enclosing, all
  - set form fields
  - submit forms
  - strip tags from arbitrary strings of HTML
  - support HTTPS
  - handle entities > 255 and Unicode documents
  - accept and store cookies during redirection
  - store and send cookies according to domain and path
  - submit forms with file upload

To do:
  - split by element
  - detect ends of elements in most cases even if matching end tags are missing
  - make the line breaks in striptags correctly reflect whitespace significance
  - handle <![CDATA[ marked sections ]]>
  - use Regions in striptags instead of duplicating work?
  - remove dependency on urllib.urlencode
"""

__author__ = 'Ka-Ping Yee <ping@zesty.ca>'
__date__ = '$Date: 2012/09/22 00:00:00 $'.split()[1].replace('/', '-')
__version__ = '$Revision: 1.44 $'

from urlparse import urlsplit, urljoin
from htmlentitydefs import name2codepoint
import re
import sys

import lxml.etree

RE_TYPE = type(re.compile(''))

def regex(template, *params, **kwargs):
    """Compile a regular expression, substituting in any passed parameters
    for placeholders of the form __0__, __1__, __2__, etc. in the template.
    Specify the named argument 'flags' to set regular expression compilation
    flags; by default, DOTALL is set ('.' matches anything including '\n')."""
    flags = kwargs.get('flags', re.DOTALL)
    for i, param in enumerate(params):
        template = template.replace('__%d__' % i, param)
    return re.compile(template, flags)

def iregex(template, *params, **kwargs):
    """Compile a regular expression, substituting in any passed parameters
    for placeholders of the form __0__, __1__, __2__, etc. in the template.
    Specify the named argument 'flags' to set regular expression compilation
    flags; by default, DOTALL and IGNORECASE are set."""
    kwargs['flags'] = kwargs.get('flags', 0) | re.IGNORECASE
    return regex(template, *params, **kwargs)

class ScrapeError(Exception):
    pass

def request(scheme, method, host, path, headers, data='', verbose=0):
    """Make an HTTP or HTTPS request; return the entire reply as a string."""
    request = method + ' ' + path + ' HTTP/1.0\r\n'
    for name, value in headers.items():
        capname = '-'.join([part.capitalize() for part in name.split('-')])
        request += capname + ': ' + str(value) + '\r\n'
    request += '\r\n' + data
    host, port = host.split('@')[-1], [80, 443][scheme == 'https']
    if ':' in host:
        host, port = host.split(':', 1)

    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if verbose >= 3:
        print >>sys.stderr, 'connect:', host, port
    sock.connect((host, int(port)))
    file = scheme == 'https' and socket.ssl(sock) or sock.makefile()
    if verbose >= 3:
        print >>sys.stderr, ('\r\n' + request.rstrip()).replace(
            '\r\n', '\nrequest: ').lstrip()
    file.write(request)
    if hasattr(file, 'flush'):
        file.flush()
    chunks = []
    try:
        while not (chunks and len(chunks[-1]) == 0):
            chunks.append(file.read())
    except socket.error:
        pass
    return ''.join(chunks)

def shellquote(text):
    """Quote a string literal for /bin/sh."""
    return "'" + text.replace("'", "'\\''") + "'"

def curl(url, headers={}, data=None, verbose=0):
    """Use curl to make a request; return the entire reply as a string."""
    import os, tempfile
    fd, tempname = tempfile.mkstemp(prefix='scrape')
    command = 'curl --include --insecure --silent --max-redirs 0'
    if data:
        if not isinstance(data, str): # Unicode not allowed here
            data = urlencode(data)
        command += ' --data ' + shellquote(data)
    for name, value in headers.iteritems():
        command += ' --header ' + shellquote('%s: %s' % (name, value))
    command += ' ' + shellquote(url)
    if verbose >= 3:
        print >>sys.stderr, 'execute:', command
    os.system(command + ' > ' + tempname)
    reply = open(tempname).read()
    os.remove(tempname)
    return reply

def getcookies(cookiejar, host, path):
    """Get a dictionary of the cookies from 'cookiejar' that apply to the
    given request host and request path."""
    cookies = {}
    for cdomain in cookiejar:
        if ('.' + host).endswith(cdomain):
            for cpath in cookiejar[cdomain]:
                if path.startswith(cpath):
                    for key, value in cookiejar[cdomain][cpath].items():
                        cookies[key] = value
    return cookies

def setcookies(cookiejar, host, lines):
    """Store cookies in 'cookiejar' according to the given Set-Cookie
    header lines."""
    for line in lines:
        pairs = [(part.strip().split('=', 1) + [''])[:2]
                 for part in line.split(';')]
        (name, value), attrs = pairs[0], dict(pairs[1:])
        cookiejar.setdefault(attrs.get('domain', host), {}
                ).setdefault(attrs.get('path', '/'), {})[name] = value

RAW = object() # This sentinel value for 'charset' means "don't decode".

def fetch(url, data='', agent=None, referrer=None, charset=None, verbose=0,
          cookiejar={}, type=None, method=None):
    """Make an HTTP or HTTPS request.  If 'data' is given, do a POST;
    otherwise do a GET.  If 'agent' and/or 'referrer' are given, include
    them as User-Agent and Referer headers in the request, respectively.
    'cookiejar' should have the form {domain: {path: {name: value, ...}}};
    cookies will be sent from it and received cookies will be stored in it.
    Return the 5-element tuple (url, status, message, headers, content)
    where 'url' is the final URL retrieved, 'status' is the integer status
    code, 'message' is the reply status message, 'headers' is a dictionary of
    HTTP headers, and 'content' is a string containing the received content.
    For multiple occurrences of the same header, 'headers' will contain a
    single key-value pair where the values are joined together with newlines.
    If the Content-Type header specifies a 'charset' parameter, 'content'
    will be a Unicode string, decoded using the given charset.  Giving the
    'charset' argument overrides any received 'charset' parameter; a charset
    of RAW ensures that the content is left undecoded in an 8-bit string."""
    scheme, host, path, query, fragment = urlsplit(url)
    host = host.split('@')[-1]

    # Prepare the POST data.
    if not method:
        method = data and 'POST' or 'GET'

    if not data:
        data_str = ''
    elif isinstance(data, str):
        data_str = data
    elif isinstance(data, unicode):
        data_str = data.encode('utf-8')
    elif isinstance(data, dict):
        # urlencode() supports both of a dict of str and a dict of unicode.
        data_str = urlencode(data)
    else:
        raise Exception('Unexpected type for data: %r' % data)

    # Get the cookies to send with this request.
    cookieheader = '; '.join([
        '%s=%s' % pair for pair in getcookies(cookiejar, host, path).items()])

    # Make the HTTP headers to send.
    headers = {'host': host, 'accept': '*/*'}
    if data_str:
        headers['content-type'] = 'application/x-www-form-urlencoded'
        headers['content-length'] = len(data_str)
    if agent:
        headers['user-agent'] = agent
    if referrer:
        headers['referer'] = referrer
    if cookieheader:
        headers['cookie'] = cookieheader
    if type:
        headers['content-type'] = type

    # Make the HTTP or HTTPS request using Python or cURL.
    if verbose:
        print >>sys.stderr, '>', method, url
    import socket
    if scheme == 'http' or scheme == 'https' and hasattr(socket, 'ssl'):
        if query:
            path += '?' + query
        reply = request(scheme, method, host, path, headers, data_str, verbose)
    elif scheme == 'https':
        reply = curl(url, headers, data_str, verbose)
    else:
        raise ValueError, scheme + ' not supported'

    # Take apart the HTTP reply.
    headers, head, content = {}, reply, ''
    if '\r\n\r\n' in reply:
        head, content = (reply.split('\r\n\r\n', 1) + [''])[:2]
    else:  # Non-conformant reply.  Bummer!
        match = re.search('\r?\n[ \t]*\r?\n', reply)
        if match:
            head, content = head[:match.start()], head[match.end():]
    head = head.replace('\r\n', '\n').replace('\r', '\n')
    response, head = head.split('\n', 1)
    if verbose >= 3:
        print >>sys.stderr, 'reply:', response.rstrip()
    status = int(response.split()[1])
    message = ' '.join(response.split()[2:])
    for line in head.split('\n'):
        if verbose >= 3:
            print >>sys.stderr, 'reply:', line.rstrip()
        name, value = line.split(': ', 1)
        name = name.lower()
        if name in headers:
            headers[name] += '\n' + value
        else:
            headers[name] = value
    if verbose >= 2:
        print >>sys.stderr, 'content: %d byte%s\n' % (
            len(content), content != 1 and 's' or '')
    if verbose >= 3:
        for line in content.rstrip('\n').split('\n'):
            print >>sys.stderr, 'content: ' + repr(line + '\n')

    # Store any received cookies.
    if 'set-cookie' in headers:
        setcookies(cookiejar, host, headers['set-cookie'].split('\n'))

    return url, status, message, headers, content

def multipart_encode(data, charset):
    """Encode 'data' for a multipart post. If any of the values is of type file,
    the content of the file is read and added to the output. If any of the
    values is of type unicode, it is encoded using 'charset'. Returns a pair
    of the encoded string and content type string, which includes the multipart
    boundary used."""
    import mimetools, mimetypes
    boundary = mimetools.choose_boundary()
    encoded = []
    for key, value in data.iteritems():
        encoded.append('--%s' % boundary)
        if isinstance(value, file):
            fd = value
            filename = fd.name.split('/')[-1]
            content_type = (mimetypes.guess_type(filename)[0] or
                'application/octet-stream')
            encoded.append('Content-Disposition: form-data; ' +
                           'name="%s"; filename="%s"' % (key, filename))
            encoded.append('Content-Type: %s' % content_type)
            fd.seek(0)
            value = fd.read()
        else:
            encoded.append('Content-Disposition: form-data; name="%s"' % key)
            if isinstance(value, unicode):
                value = value.encode(charset)
        encoded.append('')  # empty line
        encoded.append(value)
    encoded.append('--' + boundary + '--')
    encoded.append('')  # empty line
    encoded.append('')  # empty line
    encoded = '\r\n'.join(encoded)
    content_type = 'multipart/form-data; boundary=%s' % boundary
    return encoded, content_type

class Session:
    """A Web-browsing session.  Exposed attributes:

        agent   - the User-Agent string (clients can set this attribute)
        url     - the last successfully fetched URL
        status  - the status code of the last request
        message - the status message of the last request
        headers - the headers of the last request as a dictionary
        content - the content of the last fetched document
        doc     - the Region spanning the last fetched document
    """

    def __init__(self, agent=None, verbose=0):
        """Specify 'agent' to set the User-Agent.  Set 'verbose' to 1, 2, or
        3 to display status messages on stderr during document retrieval."""
        self.agent = agent
        self.url = self.status = self.message = self.content = self.doc = None
        self.verbose = verbose
        self.headers = {}
        self.cookiejar = {}
        self.history = []

    def go(self, url, data='', redirects=10, referrer=True, charset=None,
           type=None):
        """Navigate to a given URL.  If the URL is relative, it is resolved
        with respect to the current URL.  If 'data' is provided, do a POST;
        otherwise do a GET.  Follow redirections up to 'redirects' times.
        If 'referrer' is given, send it as the referrer; if 'referrer' is
        True (default), send the current URL as the referrer; if 'referrer'
        is a false value, send no referrer.  If 'charset' is given, it
        overrides any received 'charset' parameter; setting 'charset' to RAW
        leaves the content undecoded in an 8-bit string.  If the document is
        successfully fetched, return a Region spanning the entire document.
        Any relevant previously stored cookies will be included in the
        request, and any received cookies will be stored for future use."""
        historyentry = (self.url, self.status, self.message,
                        self.headers, self.content, self.doc)
        url = self.resolve(url)
        if referrer is True:
            referrer = self.url

        while 1:
            (self.url, self.status, self.message, self.headers,
             content_bytes) = fetch(
                url, data, self.agent, referrer, charset, self.verbose,
                self.cookiejar, type)
            if redirects:
                if self.status in [301, 302] and 'location' in self.headers:
                    url, data = urljoin(url, self.headers['location']), ''
                    redirects -= 1
                    continue
            break

        self.history.append(historyentry)

        self.doc = Document(content_bytes, headers=self.headers, charset=charset)
        self.content = self.doc.content
        self.charset = self.doc.charset
        return self.doc

    def back(self):
        """Restore the state of this session before the previous request."""
        (self.url, self.status, self.message,
         self.headers, self.content, self.doc) = self.history.pop()
        return self.url

    def follow(self, anchor, context=None):
        """If 'anchor' is an element, follow the link in its 'href' attribute;
        if 'anchor' is a string or compiled RE, find the first link with that
        anchor text, and follow it.  If 'context' is specified, a matching link
        is searched only inside the 'context' element, instead of the whole
        document.

        e.g.:
          self.s.follow('Click here')
          self.s.follow(self.s.doc.cssselect_one('a.link'))
        """
        if isinstance(anchor, Region) or isinstance(context, Region):
            # TODO(ichikawa) Remove this after we stop using Region.
            link = anchor
            if isinstance(anchor, basestring) or type(anchor) is RE_TYPE:
                link = (context or self.doc).first('a', content=anchor)
            if not link:
                raise ScrapeError('link %r not found' % anchor)
            if not link.get('href', ''):
                raise ScrapeError('link %r has no href' % link)
            href = link['href']

        else:
            if isinstance(anchor, basestring):
                link = None
                for l in (context or self.doc).cssselect('a'):
                    if get_all_text(l) == anchor:
                        link = l
                        break
            elif isinstance(anchor, RE_TYPE):
                link = None
                for l in (context or self.doc).cssselect('a'):
                    if re.search(anchor, get_all_text(l)):
                        link = l
                        break
            elif isinstance(anchor, lxml.etree._Element):
                link = anchor
            else:
                raise ScrapeError('Unexpected type for anchor: %r' % anchor)

            if link is None:
                raise ScrapeError('link %r not found' % anchor)
            href = link.get('href')
            if not href:
                raise ScrapeError('link %r has no href' % link)

        return self.go(href)

    def follow_button(self, button):
        """Follow the forward URL specified in the button's onclick handler."""
        if not button:
            raise ScrapeError('button %r not found' % button)
        location_match = re.search(r'location\.href=[\'"]([^\'"]+)',
                                   button.get('onclick', ''))
        if not location_match:
            raise ScrapeError('button %r has no forward URL' % button)
        return self.go(location_match.group(1))

    def submit(self, elem, paramdict=None, url=None, redirects=10, **params):
        """Submit a form, optionally by clicking a given button.  The 'elem'
        argument should be of type lxml.etree._Element and can be the form
        itself or a button in the form to click.  Obtain the parameters to
        submit by (a) starting with the 'paramdict' dictionary if specified, or
        the default parameter values as returned by get_form_params; then (b)
        adding or replacing parameters in this dictionary according to the
        keyword arguments.  The 'url' argument overrides the form's action
        attribute and submits the form elsewhere.  After submission, follow
        redirections up to 'redirects' times.

        e.g.:
          self.s.submit(self.s.doc.cssselect_one('form'))
          self.s.submit(self.s.doc.cssselect_one('input[type="submit"]'))
        """

        if isinstance(elem, Region):  # for backward compatibility.
            form = elem if elem.tagname == 'form' else elem.enclosing('form')
            if not form:
                raise ScrapeError('%r is not contained in a form' % elem)
            form_params = form.params
        else:
            try:
                if elem.tag == 'form':
                    form = elem
                else:
                    form = elem.iterancestors('form').next()
            except IndexError:
                raise ScrapeError('%r is not contained in a form' % elem)
            form_params = get_form_params(form)

        p = paramdict.copy() if paramdict is not None else form_params
        # Include the (name, value) attributes of a submit button as part of the
        # parameters e.g. <input type="submit" name="action" value="add">
        if elem.get('name'):
            p[elem.get('name')] = elem.get('value', '')
        p.update(params)

        method = form.get('method', '').lower() or 'get'
        url = url or form.get('action', self.url)
        multipart_post = any(map(lambda v: isinstance(v, file), p.itervalues()))
        if multipart_post:
            param_str, content_type = multipart_encode(p, self.doc.charset)
        else:
            param_str, content_type = urlencode(p, self.doc.charset), None
        if method == 'get':
            if multipart_post:
                raise ScrapeError('can not upload a file with a GET request')
            return self.go(url + '?' + param_str, '', redirects)
        elif method == 'post':
            return self.go(url, param_str, redirects, type=content_type)
        else:
            raise ScrapeError('unknown form method %r' % method)

    def resolve(self, url):
        """Resolve a URL with respect to the current location."""
        if self.url and not (
            url.startswith('http://') or url.startswith('https://')):
            url = urljoin(self.url, url)
        return url

    def setcookie(self, cookieline):
        """Put a cookie in this session's cookie jar.  'cookieline' should
        have the format "<name>=<value>; domain=<domain>; path=<path>"."""
        scheme, host, path, query, fragment = urlsplit(self.url)
        host = host.split('@')[-1]
        setcookies(self.cookiejar, host, [cookieline])

# This pattern has been carefully tuned, but re.search can still cause a
# stack overflow.  Try re.search('(a|b)*', 'a'*10000), for example.
tagcontent_re = r'''(('[^']*'|"[^"]*"|--([^-]+|-[^-]+)*--|-(?!-)|[^'">-])*)'''

def tag_re(tagname_re):
    return '<' + tagname_re + tagcontent_re + '>'

anytag_re = tag_re(r'(\?|!\w*|/?[a-zA-Z_:][\w:.-]*)')
tagpat = re.compile(anytag_re)

# This pattern matches a character entity reference (a decimal numeric
# references, a hexadecimal numeric reference, or a named reference).
charrefpat = re.compile(r'&(#(\d+|x[\da-fA-F]+)|[\w.:-]+);?')

def htmldecode(text):
    """Decode HTML entities in the given text."""
    if type(text) is unicode:
        uchr = unichr
    else:
        uchr = lambda value: value > 127 and unichr(value) or chr(value)
    def entitydecode(match, uchr=uchr):
        entity = match.group(1)
        if entity.startswith('#x'):
            return uchr(int(entity[2:], 16))
        elif entity.startswith('#'):
            return uchr(int(entity[1:]))
        elif entity in name2codepoint:
            return uchr(name2codepoint[entity])
        else:
            return match.group(0)
    return charrefpat.sub(entitydecode, text)

def htmlencode(text):
    """Use HTML entities to encode special characters in the given text."""
    text = text.replace('&', '&amp;')
    text = text.replace('"', '&quot;')
    text = text.replace('<', '&lt;')
    text = text.replace('>', '&gt;')
    return text

urlquoted = dict((chr(i), '%%%02X' % i) for i in range(256))
urlquoted.update(dict((c, c) for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' +
                                      'abcdefghijklmnopqrstuvwxyz' +
                                      '0123456789._-'))
def urlquote(text, charset='utf-8'):
    if type(text) is unicode:
        text = text.encode(charset)
    return ''.join(map(urlquoted.get, text))

def urlencode(params, charset='utf-8'):
    pairs = ['%s=%s' % (
                 urlquote(key, charset),
                 urlquote(value, charset).replace('%20', '+'))
             for key, value in params.items()]
    return '&'.join(pairs)

def no_groups(re):
    return re.replace('(', '(?:').replace('(?:?', '(?')

tagsplitter = re.compile(no_groups(anytag_re))
parasplitter = re.compile(no_groups(tag_re('(p|table|form)')), re.I)
linesplitter = re.compile(no_groups(tag_re('(div|br|tr)')), re.I)
cdatapat = re.compile(r'<(!\s*--|style\b|script\b)', re.I)
endcdatapat = {'!': re.compile(r'--\s*>'),
               'script': re.compile(r'</script[^>]*>', re.I),
               'style': re.compile(r'</style[^>]*>', re.I)}

def striptags(html):
    """Strip HTML tags from the given string, yielding line breaks for DIV,
       BR, or TR tags and blank lines for P, TABLE, or FORM tags."""

    # Remove comments and elements with CDATA content (<script> and <style>).
    # These are special cases because tags are not parsed in their content.
    chunks, pos = [], 0
    while 1:
        startmatch = cdatapat.search(html, pos)
        if not startmatch:
            break
        tagname = startmatch.group(1).rstrip('-').strip()
        tagname = tagname.lower().encode('utf-8')
        endmatch = endcdatapat[tagname].search(html, startmatch.end())
        if not endmatch:
            break
        chunks.append(html[pos:startmatch.start()])
        pos = endmatch.end()
    chunks.append(html[pos:])
    html = ''.join(chunks)

    # Break up the text into paragraphs and lines, then remove all other tags.
    paragraphs = []
    for paragraph in parasplitter.split(html):
        lines = []
        for line in linesplitter.split(paragraph):
            line = ''.join(tagsplitter.split(line))
            line = htmldecode(line)
            nbsp = (type(line) is unicode) and u'\xa0' or '\xa0'
            line = line.replace(nbsp, ' ')
            lines.append(' '.join(line.split()))
        paragraph = '\n'.join(lines)
        if type(paragraph) is str:
            try:
                paragraph.decode('ascii')
            except:
                # Assume Latin-1 for characters > 127.
                paragraph = paragraph.decode('latin-1')
        paragraphs.append(paragraph)
    return re.sub('\n\n+', '\n\n', '\n\n'.join(paragraphs)).strip()

attr_re = r'''\s*([\w:.-]+)(\s*=\s*('[^']*'|"[^"]*"|[^\s>]*))?'''
attrpat = re.compile(attr_re)

def parseattrs(text):
    """Turn a string of name=value pairs into an attribute dictionary."""
    attrs = {}
    pos = 0
    while 1:
        match = attrpat.search(text, pos)
        if not match:
            break
        pos = match.end()
        name, value = match.group(1), match.group(3) or ''
        if value[:1] in ["'", '"']:
            value = value[1:-1]
        try:
            name = str(name)
        except:
            pass
        try:
            value = str(value)
        except:
            pass
        attrs[name.lower()] = htmldecode(value)
    return attrs

MISSING = object()  # the sentinel for "not present"

PRESENT = lambda x: 1  # the sentinel for "present with any value"

ANY = lambda x: x.strip()  # the sentinel for "contains non-whitespace"

def NUMERIC(x):  # the sentinel for "contains a numeric value"
    try:
        getnumber(x)
        return 1
    except:
        return 0

def matchcontent(specimen, desired):
    """Match a string specimen to a desired string or compiled RE."""
    if hasattr(desired, 'match'):
        return desired.match(specimen)
    elif callable(desired):
        return desired(specimen)
    else:
        return specimen == desired

def matchattrs(specimen, desired):
    """Match an attribute dictionary to a dictionary of desired attribute
    values, where each value can be a string or a compiled RE.  For
    convenience, the keys of the dictionary have their underscores turned
    into hyphens, and trailing underscores are removed."""
    for name, value in desired.items():
        name = name.rstrip('_').replace('_', '-')
        if value is MISSING:
            if name in specimen:
                return 0
        else:
            if not (name in specimen and matchcontent(specimen[name], value)):
                return 0
    return 1

class Region(object):
    """A Region object represents a contiguous region of a document (in terms
    of a starting and ending position in the document string) together with
    an associated HTML or XML tag and its attributes.  Dictionary-like access
    retrieves the name-value pairs in the attributes.  Various other methods
    allow slicing up a Region into subregions and searching within, before,
    or after a Region for tags or elements.  For a Region that represents a
    single tag, the starting and ending positions are the start and end of
    the tag itself.  For a Region that represents an element, the starting
    and ending positions are just after the starting tag and just before the
    ending tag, respectively."""

    def __init__(self, parent, start=0, end=None, starttag=None, endtag=None,
                 charset=None):
        """Create a Region.  The 'parent' argument is a string or another
        Region.  The 'start' and 'end' arguments, if given, are non-negative
        indices into the original string (not into the parent region).  The
        'starttag' and 'endtag' arguments are indices into an internal array
        of tags, intended for use by the implementation only. The 'charset'
        argument is the charset of the document e.g. 'utf-8'.
        """
        if isinstance(parent, basestring):
            self.document = parent
            self.tags = self.scantags(self.document)
            self.charset = charset
        else:
            self.document = parent.document
            self.tags = parent.tags
            self.charset = charset or parent.charset
        if end is None:
            end = len(self.document)
        self.start, self.end = start, end
        self.tagname, self.attrs = None, {}

        # If only starttag is specified, this Region is a tag.
        # If starttag and endtag are specified, this Region is an element.
        self.starttag, self.endtag = starttag, endtag
        if starttag is not None:
            self.start, self.end, self.tagname, self.attrs = self.tags[starttag]
        if endtag is not None:
            self.start, self.end = self.tags[starttag][1], self.tags[endtag][0]

        # Find the minimum and maximum indices of tags within this Region.
        if starttag and endtag:
            self.tagmin, self.tagmax = starttag + 1, endtag - 1
        else:
            self.tagmin, self.tagmax = len(self.tags), -1
            for i, (start, end, tagname, attrs) in enumerate(self.tags):
                if start >= self.start and i < self.tagmin:
                    self.tagmin = i
                if end <= self.end and i > self.tagmax:
                    self.tagmax = i

    def __repr__(self):
        if self.tagname:
            attrs = ''.join([' %s=%r' % item for item in self.attrs.items()])
            return '<Region %d:%d %s%s>' % (
                self.start, self.end, self.tagname, attrs)
        else:
            return '<Region %d:%d>' % (self.start, self.end)

    def __str__(self):
        return self.content

    # Utilities that operate on the array of scanned tags.
    def scantags(self, document):
        """Generate a list of all the tags in a document."""
        tags = []
        pos = 0
        while 1:
            match = tagpat.search(document, pos)
            if not match:
                break
            start, end = match.span()
            tagname = match.group(1).lower().encode('utf-8')
            attrs = match.group(2)
            tags.append([start, end, tagname, attrs])
            if tagname in ['script', 'style']:
                match = endcdatapat[tagname].search(document, end)
                if not match:
                    break
                start, end = match.span()
                tags.append([start, end, '/' + tagname, ''])
            pos = end
        return tags

    def matchtag(self, i, tagname, attrs):
        """Return 1 if the ith tag matches the given tagname and attributes."""
        itagname, iattrs = self.tags[i][2], self.tags[i][3]
        if itagname[:1] not in ['', '/']:
            if itagname == tagname or tagname is None:
                if isinstance(iattrs, basestring):
                    if itagname[:1] in ['?', '!']:
                        self.tags[i][3] = iattrs = {}
                    else:
                        self.tags[i][3] = iattrs = parseattrs(iattrs)
                return matchattrs(iattrs, attrs)

    def findendtag(self, starttag, enders=[], outside=0):
        """Find the index of the tag that ends an element, given the index of
        its start tag, by scanning for a balanced matching end tag or a tag
        whose name is in 'enders'.  'enders' may contain plain tag names (for
        start tags) or tag names prefixed with '/' (for end tags).  If
        'outside' is 0, scan within the current region; if 'outside' is 1,
        scan starting from the end of the current region onwards."""
        if isinstance(enders, basestring):
            enders = enders.split()
        tagname = self.tags[starttag][2]
        depth = 1
        for i in range(starttag + 1, len(self.tags)):
            if self.tags[i][2] == '/' + tagname:
                depth -= 1
            if depth == 0 or depth == 1 and self.tags[i][2] in enders:
                if not outside and i <= self.tagmax:
                    return i
                if outside and i > self.tagmax:
                    return i
                break
            if self.tags[i][2] == tagname:
                depth += 1

    def matchelement(self, starttag, content=None, enders=[], outside=0):
        """If the element with the given start tag matches the given content,
        return the index of the tag that ends the element.  The end of the
        element is found by scanning for either a balanced matching end
        tag or tag whose name is in 'enders'.  'enders' may contain plain tag
        names (for start tags) or tag names prefixed with '/' (for end tags).
        If 'outside' is 0, scan within the current region; if 'outside' is 1,
        scan starting from the end of the current region onwards."""
        endtag = self.findendtag(starttag, enders, outside)
        if endtag is not None:
            start, end = self.tags[starttag][1], self.tags[endtag][0]
            if content is None or matchcontent(
                striptags(self.document[start:end]), content):
                return endtag

    # Provide the "content" and "text" attributes to access the contents.
    content = property(lambda self: self.document[self.start:self.end])
    text = property(lambda self: striptags(self.content))

    # Provide information on forms.
    def get_params(self):
        """Get a dictionary of default values for all the form parameters.
        If there is a <input type=file> tag, it tries to open a file object."""
        if self.tagname == 'form':
            params = {}
            for input in self.alltags('input'):
                if 'name' in input and 'disabled' not in input:
                    type = input.get('type', 'text').lower()
                    if type in ['text', 'password', 'hidden'] or (
                       type in ['checkbox', 'radio'] and 'checked' in input):
                        params[input['name']] = input.get('value', '')
            for select in self.all('select'):
                if 'disabled' not in select:
                    selections = [option['value']
                                  for option in select.alltags('option')
                                  if 'selected' in option]
                    if 'multiple' in select:
                        params[select['name']] = selections
                    elif selections:
                        params[select['name']] = selections[0]
            for textarea in self.all('textarea'):
                if 'disabled' not in textarea and 'readonly' not in textarea:
                    params[textarea['name']] = textarea.content
            return params

    def get_buttons(self):
        """Get a list of all the form submission buttons."""
        if self.tagname == 'form':
            return [tag for tag in self.alltags('input')
                        if (tag.get('type', 'text').lower()
                            in ['submit', 'image'])
               ] + [tag for tag in self.alltags('button')
                        if tag.get('type', '').lower() in ['submit', '']]

    params = property(get_params)
    buttons = property(get_buttons)

    # Provide access to numeric content.
    def get_number(self):
        return getnumber(self.text)

    number = property(get_number)

    # Provide a dictionary-like interface to the tag attributes.
    def __contains__(self, name):
        return name in self.attrs

    def __getitem__(self, name):
        if isinstance(name, slice):
            return self.__getslice__(name.start, name.stop)
        if name in self.attrs:
            return self.attrs[name]
        raise AttributeError('no attribute named %r' % name)

    def get(self, name, default=None):
        if name in self.attrs:
            return self.attrs[name]
        return default

    def keys(self):
        return self.attrs.keys()

    # Report the length of the region.
    def __len__(self):
        return self.end - self.start

    # Access subregions by slicing.  The starting and ending positions of a
    # slice can be given as string positions within the region (just like
    # slicing a string), or as regions.  A slice between two regions begins
    # at the end of the start region and ends at the start of the end region.
    def __getslice__(self, start, end):
        if start is None:
            start = 0
        if end is None:
            end = len(self)
        if hasattr(start, 'end'):
            start = start.end
        elif start < 0:
            start += self.end
        else:
            start += self.start
        if hasattr(end, 'start'):
            end = end.start
        elif end < 0:
            end += self.end
        else:
            end += self.start
        return Region(self, max(self.start, start), min(self.end, end))

    def after(self):
        """Return the Region for everything after this Region."""
        return Region(self, self.end)

    def before(self):
        """Return the Region for everything before this Region."""
        return Region(self, 0, self.start)

    # Search for text.
    def find(self, target, group=0):
        """Search this Region for a string or a compiled RE and return a
        Region representing the match.  If 'group' is given, it specifies
        which grouped subexpression should be returned as the match."""
        if hasattr(target, 'search'):
            match = target.search(self.content)
            if match:
                return self[match.start(group):match.end(group)]
        else:
            start = self.content.find(target)
            if start > -1:
                return self[start:start+len(target)]
        raise ScrapeError('no match found for %r' % target)

    def findall(self, target, group=0):
        """Search this Region for a string or a compiled RE and return a
        sequence of Regions representing all the matches."""
        pos = 0
        content = self.content
        matches = []
        if hasattr(target, 'search'):
            while 1:
                match = target.search(content, pos)
                if not match:
                    break
                start, pos = match.span(group)
                matches.append(self[start:pos])
        else:
            while 1:
                start = content.find(target, pos)
                if start < 0:
                    break
                pos = start + len(target)
                matches.append(self[start:pos])
        return matches

    def split(self, separator):
        """Find all occurrences of the given string or compiled RE and use
        them as separators to split this Region into a sequence of Regions."""
        pos = 0
        content = self.content
        matches = []
        if hasattr(separator, 'search'):
            while 1:
                match = separator.search(content, pos)
                if not match:
                    break
                start, end = match.span(0)
                matches.append(self[pos:start])
                pos = end
            matches.append(self[pos:])
        else:
            while 1:
                start = content.find(separator, pos)
                if start < 0:
                    break
                end = start + len(separator)
                matches.append(self[pos:start])
                pos = end
            matches.append(self[pos:])
        return matches

    # Search for tags.
    def firsttag(self, tagname=None, **attrs):
        """Return the Region for the first tag entirely within this Region
        with the given tag name and attributes."""
        for i in range(self.tagmin, self.tagmax + 1):
            if self.matchtag(i, tagname, attrs):
                return Region(self, 0, 0, i)
        tag = tagname is None and 'tag' or '<%s> tag' % tagname
        a = attrs and ' matching %r' % attrs or ''
        raise ScrapeError('no %s found%s' % (tag, a))

    def lasttag(self, tagname=None, **attrs):
        """Return the Region for the last tag entirely within this Region
        with the given tag name and attributes."""
        for i in range(self.tagmax, self.tagmin - 1, -1):
            if self.matchtag(i, tagname, attrs):
                return Region(self, 0, 0, i)
        tag = tagname is None and 'tag' or '<%s> tag' % tagname
        a = attrs and ' matching %r' % attrs or ''
        raise ScrapeError('no %s found%s' % (tag, a))

    def alltags(self, tagname=None, **attrs):
        """Return a list of Regions for all the tags entirely within this
        Region with the given tag name and attributes."""
        tags = []
        for i in range(self.tagmin, self.tagmax + 1):
            if self.matchtag(i, tagname, attrs):
                tags.append(Region(self, 0, 0, i))
        return tags

    def nexttag(self, tagname=None, **attrs):
        """Return the Region for the nearest tag after the end of this Region
        with the given tag name and attributes."""
        return self.after().firsttag(tagname, **attrs)

    def previoustag(self, tagname=None, **attrs):
        """Return the Region for the nearest tag before the start of this
        Region with the given tag name and attributes."""
        return self.before().lasttag(tagname, **attrs)

    def splittag(self, tagname=None, **attrs):
        """Split this Region into a list of the subregions separated by tags
        with the given tag name and attributes."""
        subregions, start = [], 0
        for tag in self.alltags(tagname, **attrs):
            subregions.append(self[start:tag])
            start = tag
        subregions.append(self[tag:])
        return subregions

    # Search for elements.
    def first(self, tagname=None, content=None, enders=[], **attrs):
        """Return the Region for the first element entirely within this Region
        with the given tag name, content, and attributes.  The element ends at
        a balanced matching end tag or any tag listed in 'enders'.  'enders' may
        may contain plain tag names (for start tags) or tag names prefixed with
        '/' (for end tags).  The element content is passed through striptags()
        for comparison.  If 'content' has a match() method, the stripped content
        is passed to it; otherwise it is compared directly as a string."""
        for starttag in range(self.tagmin, self.tagmax + 1):
            if self.matchtag(starttag, tagname, attrs):
                endtag = self.matchelement(starttag, content, enders)
                if endtag is not None:
                    return Region(self, 0, 0, starttag, endtag)
        tag = tagname is None and 'element' or '<%s> element' % tagname
        a = attrs and ' matching %r' % attrs or ''
        c = content is not None and ' with content %r' % content or ''
        raise ScrapeError('no %s found%s%s' % (tag, a, c))

    def last(self, tagname=None, content=None, enders=[], **attrs):
        """Return the Region for the last element entirely within this Region
        with the given tag name, content, and attributes.  The element ends at
        a balanced matching end tag or at any tag listed in 'enders'."""
        for starttag in range(self.tagmax, self.tagmin - 1, -1):
            if self.matchtag(starttag, tagname, attrs):
                endtag = self.matchelement(starttag, content, enders)
                if endtag is not None:
                    return Region(self, 0, 0, starttag, endtag)
        tag = tagname is None and 'element' or '<%s> element' % tagname
        a = attrs and ' matching %r' % attrs or ''
        c = content is not None and ' with content %r' % content or ''
        raise ScrapeError('no %s found%s%s' % (tag, a, c))

    def all(self, tagname=None, content=None, enders=[], **attrs):
        """Return Regions for all non-overlapping elements entirely within
        this Region with the given tag name, content, and attributes, where
        each element ends at a balanced matching end tag or any tag listed
        in 'enders'."""
        elements = []
        starttag = self.tagmin
        while starttag <= self.tagmax:
            if self.matchtag(starttag, tagname, attrs):
                endtag = self.matchelement(starttag, content, enders)
                if endtag is not None:
                    elements.append(Region(self, 0, 0, starttag, endtag))
                    starttag = endtag - 1
            starttag += 1
        return elements

    def next(self, tagname=None, content=None, enders=[], **attrs):
        """Return the Region for the nearest element after the end of this
        Region with the given tag name, content, and attributes.  The element
        ends at a balanced matching end tag or any tag listed in 'enders'."""
        return self.after().first(tagname, content, enders, **attrs)

    def previous(self, tagname=None, content=None, enders=[], **attrs):
        """Return the Region for the nearest element before the start of this
        Region with the given tag name, content, and attributes.  The element
        ends at a balanced matching end tag or any tag listed in 'enders'."""
        return self.before().last(tagname, content, enders, **attrs)

    def enclosing(self, tagname=None, content=None, enders=[], **attrs):
        """Return the Region for the nearest element that encloses this Region
        with the given tag name, content, and attributes.  The element ends at
        ends at a balanced matching end tag or any tag listed in 'enders'."""
        if self.starttag and self.endtag: # skip this Region's own start tag
            laststarttag = self.starttag - 1
        else:
            laststarttag = self.tagmin - 1
        for starttag in range(laststarttag, -1, -1):
            if self.matchtag(starttag, tagname, attrs):
                endtag = self.matchelement(starttag, content, enders, outside=1)
                if endtag is not None:
                    return Region(self, 0, 0, starttag, endtag)
        tag = tagname is None and 'element' or '<%s> element' % tagname
        a = attrs and ' matching %r' % attrs or ''
        c = content is not None and ' with content %r' % content or ''
        raise ScrapeError('no %s found%s%s' % (tag, a, c))

class Document(Region):
    """A document returned as an HTTP response.
    """

    def __init__(self, content_bytes, headers, charset):
        """charset is used to decode content_bytes. If charset is None, it uses
        the charset in headers['content-type'].
        """
        self.content_bytes = content_bytes
        self.charset = charset

        if 'content-type' in headers:
            fields = headers['content-type'].split(';')
            content_type = fields[0]
            for field in fields[1:]:
                if not self.charset and field.strip().startswith('charset='):
                    self.charset = field.strip()[8:]
                    break
        else:
            content_type = None

        if self.charset and self.charset is not RAW:
            content = content_bytes.decode(self.charset)
        else:
            # TODO(ichikawa): Consider setting None here, and use
            #   content_bytes instead, after removing Region class.
            content = content_bytes

        super(Document, self).__init__(content, charset=self.charset)

        if not content or not self.charset or self.charset == RAW:
            self.__etree_doc = None
        elif content_type == 'text/html':
            self.__etree_doc = lxml.etree.HTML(
                content_bytes, parser=lxml.etree.HTMLParser(encoding=self.charset))
        elif content_type == 'text/xml':
            self.__etree_doc = lxml.etree.XML(
                content_bytes, parser=lxml.etree.XMLParser(encoding=self.charset))
        else:
            self.__etree_doc = None

    def cssselect(self, expr, **kwargs):
        """Evaluate a CSS selector expression against the document, and returns a
        list of lxml.etree._Element instances.

        e.g.,
          self.s.doc.cssselect('.my-class-name')

        See http://lxml.de/api/lxml.etree._Element-class.html#cssselect for
        details.

        This method is available only if:
          - the content-type is either text/html or text/xml
          - charset is known (either from charset parameter of the constructor
            or from the header)
          - charset is not RAW
        """
        return self.__get_etree_doc().cssselect(expr, **kwargs)

    def cssselect_one(self, expr, **kwargs):
        """Evaluate a CSS selector expression against the document, and returns a
        single lxml.etree._Element instance.

        Throws AssertionError if zero or multiple elements match the expression.

        e.g.,
          self.s.doc.cssselect_one('.my-class-name')

        See http://lxml.de/api/lxml.etree._Element-class.html#cssselect for
        details.

        This method is available only if:
          - the content-type is either text/html or text/xml
          - charset is known (either from charset parameter of the constructor
            or from the header)
          - charset is not RAW
        """
        elems = self.cssselect(expr, **kwargs)
        assert elems, (
            'cssselect_one(%r) was called, but there are no matching elements.'
                % expr)
        assert len(elems) == 1, (
            'cssselect_one(%r) was called, but there are multiple matching '
            'elements: %r' % (expr, elems))
        return elems[0]

    def xpath(self, path, **kwargs):
        """Evaluate an XPath expression against the document, and returns a
        list of lxml.etree.ElementBase instances.

        It is generally recommended to use cssselect() instead if it can be
        expressed with CSS selector.

        See http://lxml.de/api/lxml.etree._Element-class.html#xpath for
        details.

        This method is available only if:
          - the content-type is either text/html or text/xml
          - charset is known (either from charset parameter of the constructor
            or from the header)
          - charset is not RAW
        """
        return self.__get_etree_doc().xpath(path, **kwargs)

    def __get_etree_doc(self):
        assert self.__etree_doc is not None, (
            'The content type is neither text/html nor text/xml, '
            'charset is RAW, or the document is empty.')
        return self.__etree_doc


def read(path):
    """Read and return the entire contents of the file at the given path."""
    return open(path).read()

def write(path, text):
    """Write the given text to a file at the given path."""
    file = open(path, 'w')
    file.write(text)
    file.close()

def load(path):
    """Return the deserialized contents of the file at the given path."""
    import marshal
    return marshal.load(open(path))

def dump(path, data):
    """Serialize the given data and write it to a file at the given path."""
    import marshal
    file = open(path, 'w')
    marshal.dump(data, file)
    file.close()

def getnumber(text):
    """Find and parse an integer or floating-point number in the given text,
       ignoring commas, percentage signs, and non-numeric words."""
    for word in striptags(text).replace(',', '').replace('%', ' ').split():
        try:
            return int(word)
        except:
            try:
                return float(word)
            except:
                continue
    raise ScrapeError('no number found in %r' % text)

def get_all_text(elem):
    """Returns all texts in the subtree of the lxml.etree._Element, which is
    returned by Document.cssselect() etc.
    """
    text = ''.join(elem.itertext())
    return re.sub(r'\s+', ' ', text).strip()

def get_form_params(form):
    """Get a dictionary of default values for all the form parameters.
    If there is a <input type=file> tag, it tries to open a file object."""
    params = {}
    for input in form.cssselect('input'):
        if input.get('name') and input.get('disabled') is None:
            type = input.get('type', 'text').lower()
            if type in ['text', 'password', 'hidden'] or (
               type in ['checkbox', 'radio'] and input.get('checked') is not None):
                params[input.get('name')] = input.get('value', '')
    for select in form.cssselect('select'):
        if select.get('disabled') is None:
            selections = [option.get('value', '')
                          for option in select.cssselect('option')
                          if option.get('selected') is not None]
            if select.get('multiple') is not None:
                params[select.get('name')] = selections
            elif selections:
                params[select.get('name')] = selections[0]
    for textarea in form.cssselect('textarea'):
        if textarea.get('disabled') is None:
            params[textarea.get('name')] = textarea.text or ''
    return params

s = Session()
