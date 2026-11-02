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
        "encordoamento",
        "metronomo",
        "microfone",
        "palheta",
        "pedal",
    }
)

# "Cordas" also describes instruments: "violão de 7 cordas", "Cordas Orquestrais".
_INSTRUMENT_PHRASES = re.compile(
    r"\d+ cordas?|cordas? orquestra\w*|(?:instrumentos?|viol(?:ao|oes)) de cordas?|com cordas?"
)


# Someone using these words is talking about the store, even if vaguely. They deserve a question
# back, not a refusal. The words are compared by their first letters, like in policy search.
_STORE_WORD_ROOTS = frozenset(
    {
        "apren",
        "compr",
        "devol",
        "escol",
        "frete",
        "garan",
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
        "troca",
        "vende",
    }
)


# Among those, the words of someone looking for something to buy.
_SHOPPING_WORD_ROOTS = frozenset({"apren", "compr", "escol", "indic", "instr", "prese", "produ"})


# Words that only make sense for the store's policies, not for how an instrument works.
_POLICY_WORD_ROOTS = frozenset({"bolet", "devol", "frete", "garan", "parce", "reemb", "troca"})


def mentions_a_policy_subject(text: str) -> bool:
    return any(word[:5] in _POLICY_WORD_ROOTS for word in word_tokens(text))


def is_shopping_without_a_product(text: str) -> bool:
    return any(
        word[:5] in _SHOPPING_WORD_ROOTS and not word.startswith("instruc")
        for word in word_tokens(text)
    )


def mentions_store_subject(text: str) -> bool:
    # "Ignore as instruções" shares its first letters with "instrumentos".
    return any(
        word[:5] in _STORE_WORD_ROOTS and not word.startswith("instruc")
        for word in word_tokens(text)
    )


def find_unsold_item(text: str) -> str | None:
    """Return the first accessory named in the text, or None."""
    without_instruments = _INSTRUMENT_PHRASES.sub(" ", normalize_text(text))
    for word in word_tokens(without_instruments):
        if word in _UNSOLD_ITEMS:
            return word
    return None
