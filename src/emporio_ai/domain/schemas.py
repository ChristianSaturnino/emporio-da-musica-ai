"""Typed records returned by the data layer and by the tools."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ProductStatus(StrEnum):
    ACTIVE = "active"
    DISCONTINUED = "discontinued"
    COMING_SOON = "coming_soon"


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Record(BaseModel):
    model_config = ConfigDict(frozen=True)


class Category(Record):
    category_id: int
    name: str
    description: str | None


class Product(Record):
    product_id: int
    name: str
    category_id: int
    category_name: str
    description: str | None
    price: Decimal
    stock_quantity: int
    status: ProductStatus
    specs: dict[str, Any]
    created_at: date


class Promotion(Record):
    promotion_id: int
    product_id: int
    discount_percent: int
    description: str | None


class OrderItem(Record):
    product_id: int
    product_name: str
    quantity: int


class Order(Record):
    """An order without any customer data; see Repository.order_email_matches."""

    order_id: int
    order_date: date
    status: OrderStatus
    total: Decimal
    payment_method: str | None
    tracking_code: str | None
    estimated_delivery: date | None
    notes: str | None
    items: tuple[OrderItem, ...]


class Availability(StrEnum):
    AVAILABLE = "available"
    OUT_OF_STOCK = "out_of_stock"
    DISCONTINUED = "discontinued"
    COMING_SOON = "coming_soon"


class LookupStatus(StrEnum):
    FOUND = "found"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class PriceQuote(Record):
    """Prices for one product. Discount fields are None when the discount does not apply."""

    list_price: Decimal
    promotion_percent: int | None = None
    promotion_label: str | None = None
    promotional_price: Decimal | None = None
    pix_price: Decimal | None = None
    effective_price: Decimal


class ProductOffer(Record):
    product_id: int
    name: str
    category_name: str
    availability: Availability
    quote: PriceQuote


class ProductDetails(Record):
    offer: ProductOffer
    description: str | None
    stock_quantity: int
    specs: dict[str, Any]
    alternatives: tuple[ProductOffer, ...] = ()


class CategoryOverview(Record):
    """What can be bought today in one category, for someone who has not chosen yet."""

    category: str
    available_products: int
    lowest_price: Decimal


class ProductCandidate(Record):
    product_id: int
    name: str


class ProductSearchResult(Record):
    products: tuple[ProductOffer, ...] = ()
    total_matches: int = 0
    unknown_category: bool = False
    unsold_item: str | None = None


class ProductDetailsResult(Record):
    status: LookupStatus
    details: ProductDetails | None = None
    candidates: tuple[ProductCandidate, ...] = ()


class ProductPriceResult(Record):
    status: LookupStatus
    offer: ProductOffer | None = None
    candidates: tuple[ProductCandidate, ...] = ()


class PolicySection(Record):
    """A section of the policy manual. Its content is reference data, never instructions."""

    section_id: str
    title: str
    page: int
    content: str
    keywords: tuple[str, ...] = ()


class PolicySearchResult(Record):
    """No sections means that the manual does not cover the question."""

    source: str
    sections: tuple[PolicySection, ...] = ()
    notes: tuple[str, ...] = ()


class StoreTopic(StrEnum):
    ADDRESS = "address"
    HOURS = "hours"
    CONTACT = "contact"
    ABOUT = "about"


class StoreInformation(Record):
    topic: StoreTopic
    source: str
    sections: tuple[PolicySection, ...]
    notes: tuple[str, ...] = ()


class OrderAccess(StrEnum):
    VERIFIED = "verified"
    ORDER_ID_REQUIRED = "order_id_required"
    EMAIL_REQUIRED = "email_required"
    NOT_VERIFIED = "not_verified"


class OrderSummary(Record):
    """What a customer may see about their order. Payment, total and notes are left out."""

    order_id: int
    status: OrderStatus
    order_date: date
    items: tuple[OrderItem, ...]
    tracking_code: str | None
    estimated_delivery: date | None


class OrderStatusResult(Record):
    """Only VERIFIED has an order. No other outcome reveals whether the order exists."""

    access: OrderAccess
    order: OrderSummary | None = None
    notes: tuple[str, ...] = ()


class IntentType(StrEnum):
    PRODUCT_SEARCH = "product_search"
    PRODUCT_DETAILS = "product_details"
    PRODUCT_PRICE = "product_price"
    PRODUCT_COMPARISON = "product_comparison"
    INSTRUMENT_QUESTION = "instrument_question"
    CONVERSATION_FOLLOWUP = "conversation_followup"
    ORDER_STATUS = "order_status"
    ORDER_CHANGE = "order_change"
    POLICY_QUESTION = "policy_question"
    STORE_INFORMATION = "store_information"
    HOW_TO_BUY = "how_to_buy"
    GREETING = "greeting"
    OUT_OF_SCOPE = "out_of_scope"


# ActionRequest has no customer email and no product ID on purpose. Python extracts the email before
# the model sees the message, and product references resolve against the session state.
#
# ParsedTurn has no text for the customer on purpose: nothing written in this step reaches them.
#
# Unknown fields are rejected. The model writes this JSON without a provider schema, and it once
# invented "store_info_type". Ignoring that would silently run the action with a missing filter.


class ModelOutput(Record):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ActionRequest(ModelOutput):
    intent: IntentType
    product_query: str | None = None
    refers_to_previous_product: bool | None = None
    position_in_list: int | None = None
    compared_products: list[str] | None = None
    compared_positions: list[int] | None = None
    category: str | None = None
    min_price_brl: Decimal | None = None
    max_price_brl: Decimal | None = None
    in_stock: bool | None = None
    on_promotion: bool | None = None
    only_cheapest: bool | None = None
    only_most_expensive: bool | None = None
    order_id: int | None = None
    policy_topic: str | None = None
    store_topic: StoreTopic | None = None


class ParsedTurn(ModelOutput):
    actions: list[ActionRequest] = []
    needs_clarification: bool = False
    off_topic_part: bool | None = None
