"""What the store sells: instruments only, never accessories (policy manual, sections 1 and 7.1)."""

import re

from emporio_ai.domain.normalization import normalize_text, word_tokens

# The manual lists examples ("acessórios como..."), so a few other common accessories are included.
# Words are in the singular form produced by word_tokens.
_UNSOLD_ITEMS = frozenset(
    {
        "acessorio",
        "afinador",
        "afinadore",
        "amp",
        "amplificador",
        "amplificadore",
        "amps",
        "bag",
        "bags",
        "baqueta",
        "cabo",
        "capa",
        "case",
        "corda",
        "correia",
        "palheta",
        "pedal",
    }
)

# "Cordas" also describes instruments: "violão de 7 cordas", "Cordas Orquestrais".
_INSTRUMENT_PHRASES = re.compile(
    r"\d+ cordas?|cordas? orquestra\w*|instrumentos? de cordas?|com cordas?"
)


# Someone using these words is talking about the store, even if vaguely. They deserve a question
# back, not a refusal. The words are compared by their first letters, like in policy search.
_STORE_WORD_ROOTS = frozenset(
    {
        "apren",
        "compr",
        "escol",
        "indic",
        "instr",
        "music",
        "nylon",
        "pedid",
        "preco",
        "prese",
        "produ",
        "recom",
        "tocar",
        "vende",
    }
)


def mentions_store_subject(text: str) -> bool:
    return any(word[:5] in _STORE_WORD_ROOTS for word in word_tokens(text))


def find_unsold_item(text: str) -> str | None:
    """Return the first accessory named in the text, or None."""
    without_instruments = _INSTRUMENT_PHRASES.sub(" ", normalize_text(text))
    for word in word_tokens(without_instruments):
        if word in _UNSOLD_ITEMS:
            return word
    return None
