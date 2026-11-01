"""State carried through the graph and between turns."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypedDict

from emporio_ai.domain.schemas import ActionRequest, IntentType


class TurnFailure(StrEnum):
    PROVIDER_ERROR = "provider_error"
    VALIDATION_ERROR = "validation_error"
    REPLY_REJECTED = "reply_rejected"


class OutcomeResult(StrEnum):
    SUCCESS = "success"
    NO_RESULT = "no_result"
    NEEDS_INPUT = "needs_input"
    BLOCKED = "blocked"
    TOOL_ERROR = "tool_error"


@dataclass(frozen=True)
class ToolOutcome:
    """What one action produced: facts for the model to write about, or a fixed message, or both."""

    intent: IntentType
    result: OutcomeResult
    facts: dict[str, Any] | None = None
    message: str | None = None


class ConversationState(TypedDict, total=False):
    # Kept between turns. Messages never hold a customer email, which is masked before this point.
    messages: list[dict[str, str]]
    last_product_id: int | None
    last_result_ids: list[int]
    pending_order_id: int | None

    # Valid for one turn only.
    user_message: str
    customer_email: str | None
    actions: list[ActionRequest]
    dropped_actions: bool
    needs_clarification: bool
    failure: TurnFailure | None
    outcomes: list[ToolOutcome]
    reply: str
