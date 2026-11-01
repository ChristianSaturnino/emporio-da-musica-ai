"""Text normalization shared by data loading and lookups."""

import unicodedata

_PLURAL_SUFFIXES = (("oes", "ao"), ("aes", "ao"), ("ais", "al"), ("eis", "el"), ("s", ""))
_MIN_STEMMED_LENGTH = 5
_WORD_SEPARATORS = str.maketrans("-/(),", "     ")


def normalize_text(text: str) -> str:
    """Lowercase, remove accents and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().split())


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def _stem(token: str) -> str:
    """Reduce a Portuguese plural to its singular form, so "violões" matches "violão"."""
    if len(token) < _MIN_STEMMED_LENGTH or not token.isalpha():
        return token
    for suffix, replacement in _PLURAL_SUFFIXES:
        if token.endswith(suffix):
            return token.removesuffix(suffix) + replacement
    return token


def tokenize(text: str) -> list[str]:
    """Split text into normalized singular words. Other punctuation stays in the token."""
    return [_stem(word) for word in normalize_text(text).translate(_WORD_SEPARATORS).split()]


def build_search_text(*texts: str) -> str:
    """Build the text that lookups match against.

    Hyphenated codes are also kept without the hyphen, so "gf3d" finds "GF-3D".
    """
    tokens = [token for text in texts for token in tokenize(text)]
    compact_codes = [
        word.replace("-", "")
        for text in texts
        for word in normalize_text(text).split()
        if "-" in word
    ]
    return " ".join(tokens + compact_codes)
