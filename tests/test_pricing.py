"""Availability and pricing rules, in plain Python."""

from decimal import Decimal

import pytest

from emporio_ai.domain.pricing import (
    PIX_DISCOUNT_PERCENT,
    apply_discount,
    availability_of,
    quote_price,
    round_money,
)
from emporio_ai.domain.schemas import Availability, ProductStatus, Promotion


def promotion(percent, promotion_id=1, label="Oferta"):
    return Promotion(
        promotion_id=promotion_id, product_id=1, discount_percent=percent, description=label
    )


@pytest.mark.parametrize(
    ("status", "stock", "expected"),
    [
        (ProductStatus.ACTIVE, 5, Availability.AVAILABLE),
        (ProductStatus.ACTIVE, 0, Availability.OUT_OF_STOCK),
        (ProductStatus.DISCONTINUED, 13, Availability.DISCONTINUED),
        (ProductStatus.DISCONTINUED, 0, Availability.DISCONTINUED),
        (ProductStatus.COMING_SOON, 2, Availability.COMING_SOON),
    ],
)
def test_availability_puts_status_before_stock(status, stock, expected):
    assert availability_of(status, stock) is expected


@pytest.mark.parametrize(
    ("amount", "expected"),
    [("408.405", "408.41"), ("408.404", "408.40"), ("0.005", "0.01"), ("10", "10.00")],
)
def test_round_money_rounds_half_up_to_cents(amount, expected):
    assert str(round_money(Decimal(amount))) == expected


@pytest.mark.parametrize(
    ("price", "percent", "expected"),
    [
        ("2199", 5, "2089.05"),
        ("429.90", 5, "408.41"),  # Exact half cent: bankers' rounding would give 408.40.
        ("359", 10, "323.10"),
        ("549", 20, "439.20"),
        ("2399", 18, "1967.18"),
        ("5999", 8, "5519.08"),
    ],
)
def test_apply_discount(price, percent, expected):
    assert str(apply_discount(Decimal(price), percent)) == expected


def test_pix_discount_matches_the_manual():
    assert PIX_DISCOUNT_PERCENT == 5


def test_quote_without_promotion_offers_pix_on_list_price():
    quote = quote_price(Decimal("2199"), [], Availability.AVAILABLE)
    assert quote.list_price == Decimal("2199.00")
    assert quote.pix_price == Decimal("2089.05")
    assert quote.effective_price == Decimal("2199.00")
    assert quote.promotion_percent is None
    assert quote.promotional_price is None


def test_quote_with_promotion_shows_original_percent_and_final_price():
    quote = quote_price(Decimal("549"), [promotion(20, label="Desconto")], Availability.AVAILABLE)
    assert quote.list_price == Decimal("549.00")
    assert quote.promotion_percent == 20
    assert quote.promotion_label == "Desconto"
    assert quote.promotional_price == Decimal("439.20")
    assert quote.effective_price == Decimal("439.20")


def test_pix_does_not_stack_with_promotion():
    assert quote_price(Decimal("549"), [promotion(20)], Availability.AVAILABLE).pix_price is None


def test_only_one_promotion_applies_and_the_largest_wins():
    promotions = [promotion(10, promotion_id=1), promotion(30, 2), promotion(20, 3)]
    quote = quote_price(Decimal("100"), promotions, Availability.AVAILABLE)
    assert quote.promotion_percent == 30
    assert quote.effective_price == Decimal("70.00")


def test_promotions_with_same_percent_resolve_deterministically():
    promotions = [promotion(10, 2, label="Segunda"), promotion(10, 1, label="Primeira")]
    quote = quote_price(Decimal("100"), promotions, Availability.AVAILABLE)
    assert quote.promotion_label == "Primeira"


@pytest.mark.parametrize(
    "availability",
    [Availability.OUT_OF_STOCK, Availability.DISCONTINUED, Availability.COMING_SOON],
)
def test_unavailable_product_only_shows_list_price(availability):
    quote = quote_price(Decimal("100"), [promotion(50)], availability)
    assert quote.effective_price == Decimal("100.00")
    assert quote.promotional_price is None
    assert quote.promotion_percent is None
    assert quote.pix_price is None


def test_quote_amounts_are_decimal():
    quote = quote_price(Decimal("599.9"), [], Availability.AVAILABLE)
    assert isinstance(quote.pix_price, Decimal)
    assert str(quote.list_price) == "599.90"
    assert str(quote.pix_price) == "569.91"
