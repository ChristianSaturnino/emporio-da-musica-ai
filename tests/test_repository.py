"""Read-only queries over the real challenge data, without any LLM."""

import sqlite3
from contextlib import closing
from datetime import date
from decimal import Decimal

import pytest

from emporio_ai.data.repository import MAX_LIMIT, Repository, connect_read_only
from emporio_ai.domain.errors import DatabaseNotFoundError
from emporio_ai.domain.schemas import Order, OrderStatus, ProductStatus


def customer_email(db_path, customer_id):
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(
            "SELECT email FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()[0]


def names(products):
    return [product.name for product in products]


# Database access


def test_connection_rejects_writes(db_path):
    with closing(connect_read_only(db_path)) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("DELETE FROM products")


def test_missing_database_has_actionable_error(tmp_path):
    with pytest.raises(DatabaseNotFoundError, match="seed_db"):
        Repository(tmp_path / "missing.db")


def test_database_without_schema_has_actionable_error(tmp_path):
    path = tmp_path / "empty.db"
    sqlite3.connect(path).close()
    with pytest.raises(DatabaseNotFoundError, match="seed_db"):
        Repository(path)


# Products


def test_get_product_returns_typed_record(repo):
    product = repo.get_product(95)
    assert product.name == "Takamine GD20 Dreadnought Natural"
    assert product.price == Decimal("2199.00")
    assert isinstance(product.price, Decimal)
    assert product.status is ProductStatus.ACTIVE
    assert product.category_name == "Violões"
    assert isinstance(product.specs, dict)
    assert isinstance(product.created_at, date)


def test_price_keeps_exact_cents(repo):
    assert str(repo.get_product(96).price) == "799.90"


def test_unknown_product_returns_none(repo):
    assert repo.get_product(1) is None


@pytest.mark.parametrize(
    ("product_id", "status", "stock"),
    [
        (96, ProductStatus.ACTIVE, 0),
        (136, ProductStatus.DISCONTINUED, 13),
        (130, ProductStatus.COMING_SOON, 2),
    ],
)
def test_status_and_stock_are_loaded_independently(repo, product_id, status, stock):
    product = repo.get_product(product_id)
    assert (product.status, product.stock_quantity) == (status, stock)


def test_find_by_term_matches_all_words_in_any_order(repo):
    assert names(repo.find_products("gd20 takamine")) == ["Takamine GD20 Dreadnought Natural"]


@pytest.mark.parametrize("term", ["Barítono Cedar", "BARITONO CEDAR", "  baritono   cedar "])
def test_find_ignores_case_accents_and_spacing(repo, term):
    assert names(repo.find_products(term)) == ["Kala KA-ABP-CTG Barítono Cedar"]


def test_find_returns_every_match(repo):
    assert names(repo.find_products("kala baritono")) == [
        "Kala KA-ABP-CTG Barítono Cedar",
        "Kala KA-B Barítono Mogno",
    ]


def test_find_by_category(repo):
    products = repo.find_products(category_id=2, limit=MAX_LIMIT)
    assert len(products) == 4
    assert {product.category_name for product in products} == {"Baixos"}


def test_find_combines_term_and_category(repo):
    assert repo.find_products("takamine", category_id=5)
    assert repo.find_products("takamine", category_id=1) == []


def test_find_without_match_returns_empty(repo):
    assert repo.find_products("instrumento inexistente") == []


def test_find_applies_limit_and_clamps_it(repo):
    assert len(repo.find_products()) == 20
    assert len(repo.find_products(limit=3)) == 3
    assert len(repo.find_products(limit=0)) == 1
    assert len(repo.find_products(limit=10_000)) == 65


def test_find_order_is_stable(repo):
    first = [product.product_id for product in repo.find_products(limit=MAX_LIMIT)]
    second = [product.product_id for product in repo.find_products(limit=MAX_LIMIT)]
    assert first == second
    assert names(repo.find_products("takamine")) == sorted(names(repo.find_products("takamine")))


@pytest.mark.parametrize(
    "term",
    [
        "'; DROP TABLE products; --",
        "' OR '1'='1",
        '" OR ""="',
        "%",
        "_",
        "\\",
        "100%",
        "a_b",
    ],
)
def test_special_characters_do_not_change_the_query(repo, term):
    assert repo.find_products(term) == []
    assert len(repo.find_products(limit=MAX_LIMIT)) == 65


def test_categories_are_listed_in_stable_order(repo):
    categories = repo.list_categories()
    assert [category.category_id for category in categories] == list(range(1, 10))
    assert categories[4].name == "Violões"


# Promotions


@pytest.mark.parametrize(
    ("product_id", "percent"),
    [(127, 10), (121, 20), (90, 18), (94, 8)],
)
def test_active_promotions(repo, product_id, percent):
    promotions = repo.get_active_promotions(product_id)
    assert [promotion.discount_percent for promotion in promotions] == [percent]


@pytest.mark.parametrize("product_id", [95, 96, 93, 81])
def test_inactive_or_absent_promotions_are_not_returned(repo, product_id):
    assert repo.get_active_promotions(product_id) == []


# Orders


def test_shipped_order_has_tracking_and_items(repo):
    order = repo.get_order(8)
    assert order.status is OrderStatus.SHIPPED
    assert order.tracking_code == "BRJL5544332BR"
    assert order.estimated_delivery == date(2026, 2, 28)
    assert order.total == Decimal("349.90")
    assert [(item.product_id, item.quantity) for item in order.items] == [(120, 1)]
    assert order.items[0].product_name == repo.get_product(120).name


def test_unshipped_order_has_null_tracking(repo):
    order = repo.get_order(9)
    assert order.status is OrderStatus.CONFIRMED
    assert order.tracking_code is None
    assert order.estimated_delivery is None


def test_order_with_several_items_in_stable_order(repo):
    assert [item.product_id for item in repo.get_order(15).items] == [82, 106]


def test_cancelled_order_keeps_notes(repo):
    order = repo.get_order(17)
    assert order.status is OrderStatus.CANCELLED
    assert order.notes


def test_stored_total_is_not_recalculated_from_items(repo):
    order = repo.get_order(3)
    assert order.total == Decimal("3450.00")
    assert order.items[0].quantity * repo.get_product(130).price != order.total


def test_unknown_order_returns_none(repo):
    assert repo.get_order(999) is None


def test_order_record_has_no_customer_fields():
    assert not {"customer_id", "name", "email", "phone", "city"} & set(Order.model_fields)


def test_order_repr_has_no_customer_data(repo, db_path):
    assert customer_email(db_path, 2) not in repr(repo.get_order(8))


def test_repository_has_no_customer_lookup():
    public_methods = [name for name in dir(Repository) if not name.startswith("_")]
    assert not [name for name in public_methods if "customer" in name]


# Order ownership check


def test_email_of_order_owner_matches(repo, db_path):
    assert repo.order_email_matches(8, customer_email(db_path, 2))


def test_email_match_ignores_case_and_surrounding_spaces(repo, db_path):
    assert repo.order_email_matches(8, f"  {customer_email(db_path, 2).upper()} ")


def test_email_of_another_customer_does_not_match(repo, db_path):
    assert not repo.order_email_matches(8, customer_email(db_path, 3))


@pytest.mark.parametrize("email", ["", "   ", "%", "' OR '1'='1", "nobody@example.com"])
def test_invalid_email_does_not_match(repo, email):
    assert not repo.order_email_matches(8, email)


def test_unknown_order_does_not_match(repo, db_path):
    assert not repo.order_email_matches(999, customer_email(db_path, 2))


def test_shared_email_only_matches_orders_of_its_customers(repo, db_path):
    # Customers 11 and 43 share an email in the dataset; customer 11 owns order 10.
    shared_email = customer_email(db_path, 11)
    assert customer_email(db_path, 43) == shared_email
    assert repo.order_email_matches(10, shared_email)
    assert not repo.order_email_matches(8, shared_email)
