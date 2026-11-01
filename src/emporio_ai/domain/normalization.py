"""Text normalization shared by data loading and lookups."""

import unicodedata


def normalize_text(text: str) -> str:
    """Lowercase, remove accents and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(without_accents.casefold().split())


def normalize_email(email: str) -> str:
    return email.strip().casefold()
