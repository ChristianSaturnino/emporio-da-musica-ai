"""Import the challenge CSV files into a local SQLite database."""

import csv
import json
import sqlite3
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from emporio_ai.domain.errors import DatasetError
from emporio_ai.domain.normalization import normalize_email, normalize_text

_SCHEMA = (
    """
    CREATE TABLE categories (
        category_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT
    )
    """,
    """
    CREATE TABLE products (
        product_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        category_id INTEGER NOT NULL REFERENCES categories (category_id),
        description TEXT,
        price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
        stock_quantity INTEGER NOT NULL CHECK (stock_quantity >= 0),
        status TEXT NOT NULL CHECK (status IN ('active', 'discontinued', 'coming_soon')),
        specs TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE promotions (
        promotion_id INTEGER PRIMARY KEY,
        product_id INTEGER NOT NULL REFERENCES products (product_id),
        discount_percent INTEGER NOT NULL CHECK (discount_percent BETWEEN 1 AND 100),
        description TEXT,
        is_active INTEGER NOT NULL CHECK (is_active IN (0, 1))
    )
    """,
    """
    CREATE TABLE customers (
        customer_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT,
        email TEXT NOT NULL,
        city TEXT
    )
    """,
    """
    CREATE TABLE orders (
        order_id INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
        order_date TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('pending', 'confirmed', 'shipped', 'delivered', 'cancelled')),
        total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
        payment_method TEXT,
        tracking_code TEXT,
        estimated_delivery TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE order_items (
        order_id INTEGER NOT NULL REFERENCES orders (order_id),
        product_id INTEGER NOT NULL REFERENCES products (product_id),
        quantity INTEGER NOT NULL CHECK (quantity > 0),
        PRIMARY KEY (order_id, product_id)
    )
    """,
    "CREATE INDEX idx_products_category ON products (category_id)",
    "CREATE INDEX idx_promotions_product ON promotions (product_id)",
    "CREATE INDEX idx_customers_email ON customers (email)",
)


def _optional(row: dict[str, str | None], column: str) -> str | None:
    return (row[column] or "").strip() or None


def _required(row: dict[str, str | None], column: str) -> str:
    value = _optional(row, column)
    if value is None:
        raise ValueError(f"column '{column}' is empty")
    return value


def _integer(row: dict[str, str | None], column: str) -> int:
    try:
        return int(_required(row, column))
    except ValueError as exc:
        raise ValueError(f"column '{column}' is not a valid integer") from exc


def _cents(row: dict[str, str | None], column: str) -> int:
    """Convert a BRL amount to integer cents, rejecting anything that is not exact."""
    try:
        amount = Decimal(_required(row, column))
    except InvalidOperation as exc:
        raise ValueError(f"column '{column}' is not a valid amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"column '{column}' is not a valid amount")
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise ValueError(f"column '{column}' has fractions of a cent")
    return int(cents)


def _required_iso_date(row: dict[str, str | None], column: str) -> str:
    value = _required(row, column)
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"column '{column}' is not an ISO date") from exc
    return value


def _iso_date(row: dict[str, str | None], column: str) -> str | None:
    return _required_iso_date(row, column) if _optional(row, column) else None


def _json_object(row: dict[str, str | None], column: str) -> str:
    value = _required(row, column)
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        raise ValueError(f"column '{column}' is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"column '{column}' is not a JSON object")
    return value


def _parse_category(row: dict[str, str | None]) -> tuple[Any, ...]:
    return _integer(row, "category_id"), _required(row, "name"), _optional(row, "description")


def _parse_product(row: dict[str, str | None]) -> tuple[Any, ...]:
    name = _required(row, "name")
    return (
        _integer(row, "product_id"),
        name,
        normalize_text(name),
        _integer(row, "category_id"),
        _optional(row, "description"),
        _cents(row, "price_brl"),
        _integer(row, "stock_quantity"),
        _required(row, "status"),
        _json_object(row, "specs"),
        _required_iso_date(row, "created_at"),
    )


def _parse_promotion(row: dict[str, str | None]) -> tuple[Any, ...]:
    return (
        _integer(row, "promotion_id"),
        _integer(row, "product_id"),
        _integer(row, "discount_percent"),
        _optional(row, "description"),
        _integer(row, "is_active"),
    )


def _parse_customer(row: dict[str, str | None]) -> tuple[Any, ...]:
    return (
        _integer(row, "customer_id"),
        _required(row, "name"),
        _optional(row, "phone"),
        normalize_email(_required(row, "email")),
        _optional(row, "city"),
    )


def _parse_order(row: dict[str, str | None]) -> tuple[Any, ...]:
    return (
        _integer(row, "order_id"),
        _integer(row, "customer_id"),
        _required_iso_date(row, "order_date"),
        _required(row, "status"),
        _cents(row, "total_brl"),
        _optional(row, "payment_method"),
        _optional(row, "tracking_code"),
        _iso_date(row, "estimated_delivery"),
        _optional(row, "notes"),
    )


def _parse_order_item(row: dict[str, str | None]) -> tuple[Any, ...]:
    return _integer(row, "order_id"), _integer(row, "product_id"), _integer(row, "quantity")


_RowParser = Callable[[dict[str, str | None]], tuple[Any, ...]]

# Parents come before children so that foreign keys can be enforced during the import.
_TABLES: dict[str, tuple[_RowParser, tuple[str, ...]]] = {
    "categories": (_parse_category, ("category_id", "name", "description")),
    "products": (
        _parse_product,
        (
            "product_id",
            "name",
            "normalized_name",
            "category_id",
            "description",
            "price_cents",
            "stock_quantity",
            "status",
            "specs",
            "created_at",
        ),
    ),
    "promotions": (
        _parse_promotion,
        ("promotion_id", "product_id", "discount_percent", "description", "is_active"),
    ),
    "customers": (_parse_customer, ("customer_id", "name", "phone", "email", "city")),
    "orders": (
        _parse_order,
        (
            "order_id",
            "customer_id",
            "order_date",
            "status",
            "total_cents",
            "payment_method",
            "tracking_code",
            "estimated_delivery",
            "notes",
        ),
    ),
    "order_items": (_parse_order_item, ("order_id", "product_id", "quantity")),
}


def _read_csv(path: Path, parse_row: _RowParser) -> list[tuple[Any, ...]]:
    if not path.exists():
        raise DatasetError(f"{path.name}: file not found")
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        rows = []
        for row in reader:
            try:
                rows.append(parse_row(row))
            except KeyError as exc:
                raise DatasetError(f"{path.name}: missing column {exc}") from exc
            except ValueError as exc:
                raise DatasetError(f"{path.name}: line {reader.line_num}: {exc}") from exc
    return rows


def build_database(data_dir: Path, db_path: Path) -> dict[str, int]:
    """Rebuild the database from the CSV files and return the row count of each table.

    The rebuild runs in a single transaction: if it fails, an existing database stays untouched.
    """
    rows_by_table = {
        table: _read_csv(data_dir / f"{table}.csv", parse_row)
        for table, (parse_row, _) in _TABLES.items()
    }

    db_path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None leaves transaction control to the explicit BEGIN and COMMIT below,
    # which also makes the DROP and CREATE statements part of the transaction.
    connection = sqlite3.connect(db_path, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN")
        for table in reversed(_TABLES):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        for statement in _SCHEMA:
            connection.execute(statement)
        for table, (_, columns) in _TABLES.items():
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                rows_by_table[table],
            )
        connection.execute("COMMIT")
    except sqlite3.IntegrityError as exc:
        raise DatasetError(f"constraint violated during import: {exc}") from exc
    finally:
        connection.close()

    return {table: len(rows) for table, rows in rows_by_table.items()}
