"""Deterministic checks around the model: what goes in and what comes out."""

import re
from decimal import Decimal, InvalidOperation

from emporio_ai.agent.prompts import (
    COMPOSE_SYSTEM_PROMPT,
    MASKED_EMAIL,
    UNDERSTAND_SYSTEM_PROMPT,
)
from emporio_ai.domain.normalization import normalize_text, word_tokens

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "R$ 5 mil" is 5000. Read as 5, it got correct replies about a price limit discarded.
_AMOUNT = re.compile(r"R\$\s*(\d[\d.,]*\d|\d)(\s*(?:mil\b|k\b))?", re.IGNORECASE)
_TRACKING_CODE = re.compile(r"\bBR[A-Z0-9]{9}BR\b")
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_STORED_PERCENT = re.compile(r'"promotion_percent":\s*(\d+)')
_PHONE = re.compile(r"\b\d{4,5}-\d{4}\b")
# Markdown images and links are caught here too, as both need an address.
_URL = re.compile(r"(?:https?://|www\.)[^\s)\]>\"']+")
_FREE_OF_CHARGE = re.compile(r"\b(?:gratis|gratuit|de graca|por conta da casa|cortesia)")
# Long enough that a rule the reply is right to follow, like the PIX one, is not taken for a leak.
_PROMPT_WINDOW = 12


def _windows(words: list[str]) -> set[tuple[str, ...]]:
    return {
        tuple(words[start : start + _PROMPT_WINDOW])
        for start in range(len(words) - _PROMPT_WINDOW + 1)
    }


_PROMPT_TEXT = _windows(word_tokens(UNDERSTAND_SYSTEM_PROMPT + " " + COMPOSE_SYSTEM_PROMPT))


def mask_emails(text: str) -> tuple[str, str | None]:
    """Replace emails with a placeholder, returning the masked text and the first email found."""
    match = _EMAIL.search(text)
    return _EMAIL.sub(MASKED_EMAIL, text), (match.group() if match else None)


def _amounts(text: str) -> set[Decimal]:
    amounts = set()
    for raw, thousands in _AMOUNT.findall(text):
        try:
            amount = Decimal(raw.replace(".", "").replace(",", "."))
        except InvalidOperation:
            continue
        amounts.add(amount * 1000 if thousands else amount)
    return amounts


def repeats_amounts(text: str, other: str) -> bool:
    """Tell whether every amount in the text already appears in the other text."""
    amounts = _amounts(text)
    return bool(amounts) and amounts <= _amounts(other)


def _percents(text: str) -> set[str]:
    return {value.replace(".", ",") for value in _PERCENT.findall(text)}


def find_unsupported_claims(reply: str, evidence: str) -> list[str]:
    """Name the kinds of claim in the reply that do not appear in the evidence.

    Checks what is checkable without understanding the sentence: amounts, percentages, codes,
    contacts, addresses, free offers and the prompts themselves. Evidence has to come from the
    store, as a customer who writes "R$ 1,00" must not be able to vouch for it. It returns kinds
    rather than values, so the result is safe to log.
    """
    unsupported = []
    if _amounts(reply) - _amounts(evidence):
        unsupported.append("amount")
    if _percents(reply) - _percents(evidence) - set(_STORED_PERCENT.findall(evidence)):
        unsupported.append("percent")
    if set(_TRACKING_CODE.findall(reply)) - set(_TRACKING_CODE.findall(evidence)):
        unsupported.append("tracking_code")
    if {e.casefold() for e in _EMAIL.findall(reply)} - {
        e.casefold() for e in _EMAIL.findall(evidence)
    }:
        unsupported.append("email")
    if set(_PHONE.findall(reply)) - set(_PHONE.findall(evidence)):
        unsupported.append("phone")
    known_text = evidence.casefold()
    if any(url.rstrip(".,;:!?").casefold() not in known_text for url in _URL.findall(reply)):
        unsupported.append("url")
    if _FREE_OF_CHARGE.search(normalize_text(reply)) and not _FREE_OF_CHARGE.search(
        normalize_text(evidence)
    ):
        unsupported.append("free_offer")
    if _windows(word_tokens(reply)) & _PROMPT_TEXT:
        unsupported.append("prompt_text")
    return unsupported
