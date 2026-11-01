"""Deterministic checks around the model: what goes in and what comes out."""

import re
from decimal import Decimal, InvalidOperation

from emporio_ai.agent.prompts import MASKED_EMAIL

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_AMOUNT = re.compile(r"R\$\s*(\d[\d.,]*\d|\d)")
_TRACKING_CODE = re.compile(r"\bBR[A-Z0-9]{9}BR\b")


def mask_emails(text: str) -> tuple[str, str | None]:
    """Replace emails with a placeholder, returning the masked text and the first email found."""
    match = _EMAIL.search(text)
    return _EMAIL.sub(MASKED_EMAIL, text), (match.group() if match else None)


def _amounts(text: str) -> set[Decimal]:
    amounts = set()
    for raw in _AMOUNT.findall(text):
        try:
            amounts.add(Decimal(raw.replace(".", "").replace(",", ".")))
        except InvalidOperation:
            continue
    return amounts


def find_unsupported_claims(reply: str, evidence: str) -> list[str]:
    """Name the kinds of claim in the reply that do not appear in the evidence.

    Checks what is checkable without understanding the sentence: amounts, tracking codes and email
    addresses. It returns kinds rather than values, so the result is safe to log.
    """
    unsupported = []
    if _amounts(reply) - _amounts(evidence):
        unsupported.append("amount")
    if set(_TRACKING_CODE.findall(reply)) - set(_TRACKING_CODE.findall(evidence)):
        unsupported.append("tracking_code")
    if {e.casefold() for e in _EMAIL.findall(reply)} - {
        e.casefold() for e in _EMAIL.findall(evidence)
    }:
        unsupported.append("email")
    return unsupported
