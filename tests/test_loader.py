"""CSV import: exact counts, valid relations, idempotence and rejection of bad data."""

import shutil
import sqlite3
from contextlib import closing

import pytest

from emporio_ai.data.loader import build_database
from emporio_ai.domain.errors import DatasetError

EXPECTED_COUNTS = {
    "categories": 9,
    "products": 65,
    "promotions": 25,
    "customers": 50,
    "orders": 20,
    "order_items": 22,
}


def count_rows(db_path, table):
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.fixture
def dataset_copy(data_dir, tmp_path):
    copy = tmp_path / "data"
    copy.mkdir()
    for table in EXPECTED_COUNTS:
        shutil.copy(data_dir / f"{table}.csv", copy)
    return copy


def replace_in_csv(dataset_dir, file_name, old, new):
    path = dataset_dir / file_name
    content = path.read_text(encoding="utf-8")
    assert old in content, f"test setup: {old!r} not found in {file_name}"
    path.write_text(content.replace(old, new), encoding="utf-8")


def test_build_returns_exact_counts(data_dir, tmp_path):
    assert build_database(data_dir, tmp_path / "test.db") == EXPECTED_COUNTS


@pytest.mark.parametrize(("table", "expected"), EXPECTED_COUNTS.items())
def test_tables_hold_exact_counts(db_path, table, expected):
    assert count_rows(db_path, table) == expected


@pytest.mark.parametrize(
    "query",
    [
        "SELECT COUNT(*) FROM products JOIN categories USING (category_id)",
        "SELECT COUNT(*) FROM promotions JOIN products USING (product_id)",
        "SELECT COUNT(*) FROM orders JOIN customers USING (customer_id)",
        "SELECT COUNT(*) FROM order_items JOIN orders USING (order_id)",
        "SELECT COUNT(*) FROM order_items JOIN products USING (product_id)",
    ],
)
def test_joins_keep_every_row(db_path, query):
    child_table = query.split("FROM ")[1].split(" JOIN")[0]
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute(query).fetchone()[0] == EXPECTED_COUNTS[child_table]


def test_blank_optional_values_become_null(db_path):
    with closing(sqlite3.connect(db_path)) as connection:
        blanks = connection.execute(
            "SELECT COUNT(*) FROM orders WHERE tracking_code = '' OR notes = ''"
        ).fetchone()[0]
        without_tracking = connection.execute(
            "SELECT COUNT(*) FROM orders WHERE tracking_code IS NULL"
        ).fetchone()[0]
    assert blanks == 0
    assert without_tracking == 12


def test_rebuild_does_not_duplicate_and_discards_stale_rows(data_dir, tmp_path):
    path = tmp_path / "test.db"
    build_database(data_dir, path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("DELETE FROM order_items WHERE order_id = 1")
        connection.execute("INSERT INTO categories (category_id, name) VALUES (99, 'Stale')")
        connection.commit()

    build_database(data_dir, path)

    for table, expected in EXPECTED_COUNTS.items():
        assert count_rows(path, table) == expected


@pytest.mark.parametrize(
    ("file_name", "old", "new"),
    [
        pytest.param("products.csv", "96,799.9,", '96,"799,9",', id="comma-decimal-price"),
        pytest.param("products.csv", "96,799.9,", "96,,", id="blank-price"),
        pytest.param("products.csv", "96,799.9,", "96,-1,", id="negative-price"),
        pytest.param("products.csv", "96,799.9,", "96,10.001,", id="fraction-of-cent"),
        pytest.param("products.csv", "96,799.9,", "96,NaN,", id="nan-price"),
        pytest.param("orders.csv", "8,2,2026-02-15", "8,2,15/02/2026", id="non-iso-date"),
        pytest.param("orders.csv", ",shipped,", ",lost,", id="unknown-order-status"),
        pytest.param("orders.csv", "8,2,2026-02-15", "8,999,2026-02-15", id="orphan-customer"),
        pytest.param("order_items.csv", "8,1,120", "8,1,999", id="orphan-product"),
        pytest.param("order_items.csv", "8,1,120", "8,0,120", id="zero-quantity"),
        pytest.param("promotions.csv", "12,127,10,", "12,127,101,", id="discount-over-100"),
        pytest.param("categories.csv", "category_id,name", "id,name", id="missing-column"),
    ],
)
def test_invalid_data_is_rejected(dataset_copy, tmp_path, file_name, old, new):
    replace_in_csv(dataset_copy, file_name, old, new)
    with pytest.raises(DatasetError):
        build_database(dataset_copy, tmp_path / "test.db")


def test_missing_file_is_reported(dataset_copy, tmp_path):
    (dataset_copy / "orders.csv").unlink()
    with pytest.raises(DatasetError, match="orders.csv"):
        build_database(dataset_copy, tmp_path / "test.db")


def test_error_names_location_but_not_row_values(dataset_copy, tmp_path):
    replace_in_csv(dataset_copy, "customers.csv", "(67) 99812-3456,lucas.mendes@jmail.com", ",")
    with pytest.raises(DatasetError) as error:
        build_database(dataset_copy, tmp_path / "test.db")
    message = str(error.value)
    assert "customers.csv" in message
    assert "Lucas" not in message


def test_failed_rebuild_keeps_existing_database(data_dir, dataset_copy, tmp_path):
    path = tmp_path / "test.db"
    build_database(data_dir, path)
    # Passes row parsing and only fails on the foreign key, after the tables were dropped.
    replace_in_csv(dataset_copy, "order_items.csv", "8,1,120", "8,1,999")

    with pytest.raises(DatasetError):
        build_database(dataset_copy, path)

    for table, expected in EXPECTED_COUNTS.items():
        assert count_rows(path, table) == expected
