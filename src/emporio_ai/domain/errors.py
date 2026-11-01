"""Explicit domain errors."""


class DatasetError(Exception):
    """A source dataset is missing or invalid. Messages name the location, never the row values."""


class DatabaseNotFoundError(Exception):
    """The SQLite database has not been created yet."""
