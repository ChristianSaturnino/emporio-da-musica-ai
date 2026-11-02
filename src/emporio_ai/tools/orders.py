"""Order tool: show an order's status only to someone who knows its ID and customer email."""

from emporio_ai.data.repository import Repository
from emporio_ai.domain.schemas import (
    Order,
    OrderAccess,
    OrderStatus,
    OrderStatusResult,
    OrderSummary,
)

_SNAPSHOT_NOTE = (
    "Estes dados vêm de um snapshot, não do sistema em tempo real. A data de entrega é a "
    "estimativa registrada, não uma confirmação atual."
)
_NO_TRACKING_NOTE = "Este pedido não tem código de rastreio registrado. Não informe nem presuma um."
_CANCELLED_NOTE = (
    "O pedido consta como cancelado. O motivo não está disponível aqui: oriente falar com a equipe."
)


def _notes_for(order: Order) -> tuple[str, ...]:
    notes = [_SNAPSHOT_NOTE]
    if order.tracking_code is None:
        notes.append(_NO_TRACKING_NOTE)
    if order.status is OrderStatus.CANCELLED:
        notes.append(_CANCELLED_NOTE)
    return tuple(notes)


def get_order_status(
    repo: Repository, order_id: int | None = None, customer_email: str | None = None
) -> OrderStatusResult:
    """Return an order's minimized summary if the email belongs to the customer who placed it.

    A wrong email and an unknown order give the same result, and a missing email is asked for
    before the order is even looked up, so nothing here confirms that an order exists. An email on
    its own finds nothing: there is no lookup by customer.
    """
    if order_id is None:
        return OrderStatusResult(access=OrderAccess.ORDER_ID_REQUIRED)
    if not customer_email or not customer_email.strip():
        return OrderStatusResult(access=OrderAccess.EMAIL_REQUIRED)
    if not repo.order_email_matches(order_id, customer_email):
        return OrderStatusResult(access=OrderAccess.NOT_VERIFIED)
    return get_verified_order(repo, order_id)


def get_verified_order(repo: Repository, order_id: int) -> OrderStatusResult:
    """Return the summary of an order whose email check has already passed in this conversation."""
    order = repo.get_order(order_id)
    if order is None:
        return OrderStatusResult(access=OrderAccess.NOT_VERIFIED)
    return OrderStatusResult(
        access=OrderAccess.VERIFIED,
        order=OrderSummary(
            order_id=order.order_id,
            status=order.status,
            order_date=order.order_date,
            items=order.items,
            tracking_code=order.tracking_code,
            estimated_delivery=order.estimated_delivery,
        ),
        notes=_notes_for(order),
    )
