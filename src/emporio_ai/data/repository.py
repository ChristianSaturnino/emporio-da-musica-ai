"""Read-only, parameterized queries over the local SQLite database."""

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import date
from decimal import Decimal
from pathlib import Path

from emporio_ai.domain.errors import DatabaseNotFoundError
from emporio_ai.domain.normalization import normalize_email, tokenize
from emporio_ai.domain.schemas import Category, Order, OrderItem, Product, Promotion

DEFAULT_LIMIT = 20
MAX_LIMIT = 100

_PRODUCT_SELECT = """
    SELECT p.product_id, p.name, p.category_id, c.name AS category_name, p.description,
           p.price_cents, p.stock_quantity, p.status, p.specs, p.created_at
    FROM products AS p
    JOIN categories AS c ON c.category_id = p.category_id
"""


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise DatabaseNotFoundError(_not_found_message(db_path))
    connection = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _not_found_message(db_path: Path) -> str:
    return f"Database {db_path.name} is not ready. Run: python scripts/seed_db.py"


def _money(cents: int) -> Decimal:
    return Decimal(cents).scaleb(-2)


def _optional_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _escape_like(token: str) -> str:
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_product(row: sqlite3.Row) -> Product:
    return Product(
        product_id=row["product_id"],
        name=row["name"],
        category_id=row["category_id"],
        category_name=row["category_name"],
        description=row["description"],
        price=_money(row["price_cents"]),
        stock_quantity=row["stock_quantity"],
        status=row["status"],
        specs=json.loads(row["specs"]),
        created_at=date.fromisoformat(row["created_at"]),
    )


class Repository:
    """Each call opens its own connection, so one instance can be shared across threads.

    There is deliberately no method that returns customer data: orders are only reachable by ID
    and ownership can only be checked through a yes/no answer.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        with closing(connect_read_only(db_path)) as connection:
            has_schema = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'products'"
            ).fetchone()
        if has_schema is None:
            raise DatabaseNotFoundError(_not_found_message(db_path))

    def list_categories(self) -> list[Category]:
        with closing(connect_read_only(self._db_path)) as connection:
            rows = connection.execute(
                "SELECT category_id, name, description FROM categories ORDER BY category_id"
            ).fetchall()
        return [Category(**dict(row)) for row in rows]

    def get_product(self, product_id: int) -> Product | None:
        with closing(connect_read_only(self._db_path)) as connection:
            row = connection.execute(
                f"{_PRODUCT_SELECT} WHERE p.product_id = ?", (product_id,)
            ).fetchone()
        return _to_product(row) if row else None

    def find_products(
        self,
        term: str | None = None,
        category_ids: Sequence[int] | None = None,
        limit: int = DEFAULT_LIMIT,
        include_description: bool = False,
    ) -> list[Product]:
        """Find products matching every word of the term, ignoring case, accents and plurals.

        The term is matched against name and category, plus the description if requested.
        Only fixed clauses go into the SQL text; every value is bound as a parameter.
        """
        column = "p.search_full" if include_description else "p.search_name"
        clauses = []
        params: list[str | int] = []
        for token in tokenize(term or ""):
            clauses.append(f"{column} LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(token)}%")
        if category_ids is not None:
            placeholders = ", ".join("?" for _ in category_ids)
            clauses.append(f"p.category_id IN ({placeholders})")
            params.extend(category_ids)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, MAX_LIMIT)))

        with closing(connect_read_only(self._db_path)) as connection:
            rows = connection.execute(
                f"{_PRODUCT_SELECT} {where} ORDER BY p.search_name, p.product_id LIMIT ?",
                params,
            ).fetchall()
        return [_to_product(row) for row in rows]

    def get_active_promotions(self, product_id: int) -> list[Promotion]:
        with closing(connect_read_only(self._db_path)) as connection:
            rows = connection.execute(
                """
                SELECT promotion_id, product_id, discount_percent, description
                FROM promotions
                WHERE product_id = ? AND is_active = 1
                ORDER BY promotion_id
                """,
                (product_id,),
            ).fetchall()
        return [Promotion(**dict(row)) for row in rows]

    def get_order(self, order_id: int) -> Order | None:
        with closing(connect_read_only(self._db_path)) as connection:
            row = connection.execute(
                """
                SELECT order_id, order_date, status, total_cents, payment_method,
                       tracking_code, estimated_delivery, notes
                FROM orders
                WHERE order_id = ?
                """,
                (order_id,),
            ).fetchone()
            if row is None:
                return None
            items = connection.execute(
                """
                SELECT i.product_id, p.name AS product_name, i.quantity
                FROM order_items AS i
                JOIN products AS p ON p.product_id = i.product_id
                WHERE i.order_id = ?
                ORDER BY i.product_id
                """,
                (order_id,),
            ).fetchall()
        return Order(
            order_id=row["order_id"],
            order_date=date.fromisoformat(row["order_date"]),
            status=row["status"],
            total=_money(row["total_cents"]),
            payment_method=row["payment_method"],
            tracking_code=row["tracking_code"],
            estimated_delivery=_optional_date(row["estimated_delivery"]),
            notes=row["notes"],
            items=tuple(OrderItem(**dict(item)) for item in items),
        )

    def order_email_matches(self, order_id: int, email: str) -> bool:
        """Tell whether the email belongs to the customer who placed this order."""
        with closing(connect_read_only(self._db_path)) as connection:
            match = connection.execute(
                """
                SELECT 1
                FROM orders AS o
                JOIN customers AS c ON c.customer_id = o.customer_id
                WHERE o.order_id = ? AND c.email = ?
                """,
                (order_id, normalize_email(email)),
            ).fetchone()
        return match is not None
