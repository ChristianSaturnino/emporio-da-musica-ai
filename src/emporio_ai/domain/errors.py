"""Explicit domain errors."""


class DatasetError(Exception):
    """A source dataset is missing or invalid. Messages name the location, never the row values."""


class DatabaseNotFoundError(Exception):
    """The SQLite database has not been created yet."""


class ConfigurationError(Exception):
    """A required setting is missing, such as the API key."""


class LLMUnavailableError(Exception):
    """The model provider could not be reached or refused the request."""


class LLMSetupError(LLMUnavailableError):
    """The provider refused the key, the model or the account's credit, so retrying cannot help."""


class LLMOutputError(Exception):
    """The model answered, but its output cannot be used."""
