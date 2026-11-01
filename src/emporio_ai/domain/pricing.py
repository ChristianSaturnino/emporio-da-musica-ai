"""Availability and pricing rules from the store policy manual (sections 3, 6 and 7.3)."""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from emporio_ai.domain.schemas import Availability, PriceQuote, ProductStatus, Promotion

PIX_DISCOUNT_PERCENT = 5

_CENT = Decimal("0.01")


def round_money(amount: Decimal) -> Decimal:
    return amount.quantize(_CENT, rounding=ROUND_HALF_UP)


def format_brl(amount: Decimal) -> str:
    """Format an amount the Brazilian way: R$ 2.199,00."""
    formatted = f"{round_money(amount):,.2f}"
    return "R$ " + formatted.translate(str.maketrans(",.", ".,"))


def apply_discount(price: Decimal, percent: int) -> Decimal:
    return round_money(price * (100 - percent) / 100)


def availability_of(status: ProductStatus, stock_quantity: int) -> Availability:
    """Status comes first: a discontinued product with units left is still not on sale."""
    if status is ProductStatus.DISCONTINUED:
        return Availability.DISCONTINUED
    if status is ProductStatus.COMING_SOON:
        return Availability.COMING_SOON
    return Availability.AVAILABLE if stock_quantity > 0 else Availability.OUT_OF_STOCK


def quote_price(
    list_price: Decimal,
    active_promotions: Sequence[Promotion],
    availability: Availability,
) -> PriceQuote:
    """Build the price quote for a product.

    Discounts are not cumulative: a promotional price excludes the PIX discount. A product that
    cannot be bought only shows its list price, since promotions depend on stock.
    """
    list_price = round_money(list_price)
    if availability is not Availability.AVAILABLE:
        return PriceQuote(list_price=list_price, effective_price=list_price)

    if not active_promotions:
        return PriceQuote(
            list_price=list_price,
            pix_price=apply_discount(list_price, PIX_DISCOUNT_PERCENT),
            effective_price=list_price,
        )

    # The data has at most one active promotion per product; if that changes, only one applies.
    promotion = min(active_promotions, key=lambda p: (-p.discount_percent, p.promotion_id))
    promotional_price = apply_discount(list_price, promotion.discount_percent)
    return PriceQuote(
        list_price=list_price,
        promotion_percent=promotion.discount_percent,
        promotion_label=promotion.description,
        promotional_price=promotional_price,
        effective_price=promotional_price,
    )
