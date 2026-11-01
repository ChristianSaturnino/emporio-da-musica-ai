"""Typed records returned by the data layer."""

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
