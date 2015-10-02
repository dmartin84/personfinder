# coding:utf-8

import jautils

from unidecode import unidecode

import os.path
import re
import logging
def read_dictionary(file_name):
    """
    Reads dictionary file.
    Args:
        file_name: file name.
                   format: kanji + '\t' + yomigana
    Return:
        {kanj: yomigana, ...}
    """
    dictionary = {}
    try:
        if os.path.exists(file_name):
            with open(file_name, 'r') as f:
                for line in f:
                    kanji, hiragana = line.rstrip('\n').split('\t')
                    kanji = kanji.decode('utf-8')
                    hiragana = hiragana.decode('utf-8')
                    if kanji in dictionary:
                        dictionary[kanji].add(hiragana)
                    else:
                        dictionary[kanji] = set([hiragana])

    except IOError:
        return None
    return dictionary

JAPANESE_NAME_DICTIONARY = read_dictionary('japanese_name_dict.txt')
JAPANESE_LOCATION_DICTIONARY = read_dictionary('jp_location_dict.txt')


def has_kanji(word):
    """
    Returns whether word contains kanji or not.
    """
    # [\u3400-\u9fff]: kanji
    # TODO: update this range (some of kanji characters out of this range.)
    return re.match(ur'([\u3400-\u9fff])', word)


def romanize_single_japanese_word_by_name_dict(word):
    """
    This method romanizes japanese name by using name dictionary.
    If word isn't found in dictionary, this method doesn't
    apply romanize.
    This method can return multiple romanizations.
    (because there are multiple ways to read the same kanji name in japanese)
    This method doesn't support romanizing full names using first/last
    names in the dictionary.
    Returns:
        [romanized_jp_name, ...]
    """
    if not word:
        return ['']

    if word in JAPANESE_NAME_DICTIONARY:
        yomigana_list = JAPANESE_NAME_DICTIONARY[word]
        return [jautils.hiragana_to_romaji(yomigana)
                for yomigana in yomigana_list]

    return [word]


def romanize_japanese_name_by_name_dict(word, for_index=True):
    """
    This method romanizes japanese name by using name dictionary.
    If word isn't found in dictionary, this method doesn't
    apply romanize.
    This method can return multiple romanizations.
    (because there are multiple ways to read the same kanji name in japanese)
    Args:
        for_index: Set this to True for indexing purpose.
                   Set this to False when you want to romanize query text.
    Returns:
        [romanized_jp_name, and romanized_jp_name(split_word), ...]
    """
    if not word:
        return ['']

    words = []
    for index in xrange(1, len(word)):
        # Splits the word to support romanizing fullname without space.
        # If the query is a full name without white space(e.g., "山田太郎"),
        # and the first/last name is in the dictionary,
        # but the full name is not in the dictionary,
        # it can still return romanization "yamadataro".
        first_part = word[:index]
        last_part = word[index:]
        romanized_first_parts = romanize_single_japanese_word_by_name_dict(
            first_part)
        romanized_last_parts = romanize_single_japanese_word_by_name_dict(
            last_part)
        for romanized_first_part in romanized_first_parts:
            for romanized_last_part in romanized_last_parts:
                if romanized_first_part != first_part and \
                        romanized_last_part != last_part:
                    words.append(romanized_first_part+romanized_last_part)
                    # if record contains a full name with kanji (e.g., "山田太郎"),
                    # we need to add first/last name. ("taro", "yamada")
                    # we don't need these fields for search queries
                    # because we should return search result for search queries.
                    # ("yamadataro")
                    # e.g., if there is a record [name = "山田太郎"],
                    # you can search by query: "yamada", "taro" or "yamadataro"
                    # if you add these fields for query (e.g., "山田太郎"),
                    # results contain records which can be search by "yamada"
                    if for_index:
                        words.append(romanized_first_part)
                        words.append(romanized_last_part)
    words.extend(romanize_single_japanese_word_by_name_dict(word))
    return list(set(words))


def romanize_japanese_location(word):
    """
    This method romanizes japanese location by using name dictionary.
    If word isn't found in dictionary, this method doesn't
    apply romanize.
    This method can return multiple romanizations.
    (because there are multiple ways to read the same kanji location in japanese)
    Returns:
        [romanized_jp_location, ...]
    """
    if not word:
        return ['']

    if word in JAPANESE_LOCATION_DICTIONARY:
        yomigana_list = JAPANESE_LOCATION_DICTIONARY[word]
        return [jautils.hiragana_to_romaji(yomigana)
                for yomigana in yomigana_list]

    return [word]


def romanize_word_by_unidecode(word):
    """
    This method romanizes all languages by unidecode.
    If word is hiragana or katakana, it is romanized by jautils.
    kanji is romanized in Chinese way.
    Args:
        word: should be script varianted
    Returns:
        an array of romanzied_word by unidecode [romanized_word]
    """
    if not word:
        return ['']

    if jautils.should_normalize(word):
        hiragana_word = jautils.normalize(word)
        return [jautils.hiragana_to_romaji(hiragana_word)]
    romanized_word = unidecode(word)
    return [romanized_word.strip()]


def romanize_search_query(word):
    """
    This method romanizes all languages for search query.
    If word is hiragana or katakana, it is romanized by jautils.
    Args:
        word: should be script varianted
    Returns:
        [romanized_word, ... ]   
        (if word can be romanized by unidecode and jp_dictionary,
        returns multiple romanizations.)
    """
    if not word:
        return []

    romanized_words = []
    if has_kanji(word):
        romanized_words = romanize_japanese_name_by_name_dict(word,
                                                              for_index=False)
        romanized_words.extend(romanize_japanese_location(word))

    if jautils.should_normalize(word):
        hiragana_word = jautils.normalize(word)
        romanized_words.append(jautils.hiragana_to_romaji(hiragana_word))

    romanized_word = unidecode(word)
    romanized_words.append(romanized_word.strip())
    return romanized_words
