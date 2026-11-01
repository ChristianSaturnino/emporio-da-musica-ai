"""Order lookup: verification, minimized output and no leaks, without any LLM."""

import logging
import sqlite3
from contextlib import closing
from datetime import date

import pytest

from emporio_ai.domain.schemas import OrderAccess, OrderStatus, OrderSummary
from emporio_ai.tools.orders import get_order_status

# Customers who placed the orders used below.
ORDER_OWNERS = {1: 3, 8: 2, 9: 6, 13: 8, 15: 18, 17: 15}


def customer_row(db_path, customer_id):
    with closing(sqlite3.connect(db_path)) as connection:
        return connection.execute(
            "SELECT name, phone, email, city FROM customers WHERE customer_id = ?", (customer_id,)
        ).fetchone()


@pytest.fixture
def owner_email(db_path):
    def email_of(order_id):
        return customer_row(db_path, ORDER_OWNERS[order_id])[2]

    return email_of


# Verification


def test_right_email_gets_the_order(repo, owner_email):
    result = get_order_status(repo, 8, owner_email(8))
    assert result.access is OrderAccess.VERIFIED
    assert result.order.order_id == 8


def test_email_comparison_ignores_case_and_spaces(repo, owner_email):
    result = get_order_status(repo, 8, f"  {owner_email(8).upper()}  ")
    assert result.access is OrderAccess.VERIFIED


@pytest.mark.parametrize("email", [None, "", "   "])
def test_missing_email_is_asked_for(repo, email):
    result = get_order_status(repo, 8, email)
    assert result.access is OrderAccess.EMAIL_REQUIRED
    assert result.order is None


def test_missing_order_id_is_asked_for_even_with_a_valid_email(repo, owner_email):
    result = get_order_status(repo, None, owner_email(8))
    assert result.access is OrderAccess.ORDER_ID_REQUIRED
    assert result.order is None


def test_nothing_given_asks_for_the_order_id(repo):
    assert get_order_status(repo).access is OrderAccess.ORDER_ID_REQUIRED


def test_email_of_another_customer_is_rejected(repo, owner_email):
    result = get_order_status(repo, 8, owner_email(1))
    assert result.access is OrderAccess.NOT_VERIFIED
    assert result.order is None


@pytest.mark.parametrize(
    "email",
    ["nobody@example.com", "not an email", "%", "_", "' OR '1'='1", "'; DROP TABLE orders; --"],
)
def test_invalid_email_is_rejected(repo, email):
    assert get_order_status(repo, 8, email).access is OrderAccess.NOT_VERIFIED


@pytest.mark.parametrize("order_id", [999, 0, -1])
def test_unknown_order_is_rejected(repo, owner_email, order_id):
    assert get_order_status(repo, order_id, owner_email(8)).access is OrderAccess.NOT_VERIFIED


# Nothing reveals whether an order exists


def test_wrong_email_and_unknown_order_are_indistinguishable(repo):
    wrong_email = get_order_status(repo, 8, "nobody@example.com")
    unknown_order = get_order_status(repo, 999, "nobody@example.com")
    assert wrong_email == unknown_order
    assert repr(wrong_email) == repr(unknown_order)


def test_valid_email_on_an_unknown_order_looks_like_a_wrong_email(repo, owner_email):
    assert get_order_status(repo, 999, owner_email(8)) == get_order_status(repo, 8, owner_email(1))


def test_missing_email_is_the_same_for_existing_and_unknown_orders(repo):
    assert get_order_status(repo, 8) == get_order_status(repo, 999)


def test_rejected_results_carry_no_order_data(repo):
    result = get_order_status(repo, 8, "nobody@example.com")
    assert result.order is None
    assert result.notes == ()
    assert "BRJL5544332BR" not in repr(result)


# What each order status shows


def test_shipped_order_has_tracking(repo, owner_email):
    result = get_order_status(repo, 8, owner_email(8))
    order = result.order
    assert order.status is OrderStatus.SHIPPED
    assert order.order_date == date(2026, 2, 15)
    assert order.tracking_code == "BRJL5544332BR"
    assert order.estimated_delivery == date(2026, 2, 28)
    assert [(item.product_name, item.quantity) for item in order.items] == [
        ("Kala KA-C Concert Mogno", 1)
    ]
    assert len(result.notes) == 1


def test_delivered_order(repo, owner_email):
    order = get_order_status(repo, 1, owner_email(1)).order
    assert order.status is OrderStatus.DELIVERED
    assert order.tracking_code == "BRAB1234567BR"


@pytest.mark.parametrize(
    ("order_id", "status"), [(13, OrderStatus.PENDING), (9, OrderStatus.CONFIRMED)]
)
def test_order_not_shipped_has_no_tracking_and_says_so(repo, owner_email, order_id, status):
    result = get_order_status(repo, order_id, owner_email(order_id))
    assert result.order.status is status
    assert result.order.tracking_code is None
    assert result.order.estimated_delivery is None
    assert any("Não informe nem presuma" in note for note in result.notes)


def test_cancelled_order_does_not_expose_the_reason(repo, owner_email):
    result = get_order_status(repo, 17, owner_email(17))
    assert result.order.status is OrderStatus.CANCELLED
    assert result.order.tracking_code is None
    assert any("cancelado" in note for note in result.notes)
    # The stored note exists, but it stays out of the result.
    assert repo.get_order(17).notes
    assert repo.get_order(17).notes not in repr(result)


def test_order_with_several_items(repo, owner_email):
    items = get_order_status(repo, 15, owner_email(15)).order.items
    assert [item.product_id for item in items] == [82, 106]


def test_every_verified_order_says_the_data_is_a_snapshot(repo, owner_email):
    for order_id in ORDER_OWNERS:
        notes = get_order_status(repo, order_id, owner_email(order_id)).notes
        assert any("snapshot" in note for note in notes)


# Personal data stays out


def test_summary_only_has_the_allowed_fields():
    assert set(OrderSummary.model_fields) == {
        "order_id",
        "status",
        "order_date",
        "items",
        "tracking_code",
        "estimated_delivery",
    }


def test_result_has_no_personal_data(repo, db_path, owner_email):
    result = get_order_status(repo, 8, owner_email(8))
    output = repr(result) + result.model_dump_json()
    for value in customer_row(db_path, ORDER_OWNERS[8]):
        assert value not in output


def test_result_has_no_payment_or_total(repo, owner_email):
    output = get_order_status(repo, 8, owner_email(8)).model_dump_json()
    assert "debit" not in output
    assert "349" not in output


@pytest.mark.parametrize("order_id", [8, 999])
def test_lookup_logs_no_personal_data(repo, owner_email, caplog, order_id):
    email = owner_email(8)
    with caplog.at_level(logging.DEBUG):
        get_order_status(repo, order_id, email)
    assert email not in caplog.text


def test_lookup_changes_nothing(repo, db_path, owner_email):
    def snapshot():
        with closing(sqlite3.connect(db_path)) as connection:
            return connection.execute("SELECT * FROM orders ORDER BY order_id").fetchall()

    before = snapshot()
    get_order_status(repo, 8, owner_email(8))
    assert snapshot() == before


# Known limit of the simulated check


def test_shared_email_passes_for_orders_of_the_customer_who_has_it(repo, db_path):
    # Customers 11 and 43 share an email and customer 11 placed order 10. The data cannot tell them
    # apart, so customer 43 would also pass. This is documented as a known limitation.
    shared_email = customer_row(db_path, 11)[2]
    assert customer_row(db_path, 43)[2] == shared_email
    assert get_order_status(repo, 10, shared_email).access is OrderAccess.VERIFIED
    assert get_order_status(repo, 8, shared_email).access is OrderAccess.NOT_VERIFIED
