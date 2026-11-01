"""The conversation workflow: understand -> validate -> execute -> respond.

The model is called in understand and, when there is something to write about, in respond. Validate
and execute are plain Python, and there is no loop: each turn takes this path once.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from emporio_ai.agent import prompts
from emporio_ai.agent.guards import find_unsupported_claims
from emporio_ai.agent.llm import ChatMessage, LLMClient
from emporio_ai.agent.state import ConversationState, OutcomeResult, ToolOutcome, TurnFailure
from emporio_ai.data.policy_index import PolicyIndex
from emporio_ai.data.repository import Repository
from emporio_ai.domain.errors import LLMOutputError, LLMUnavailableError
from emporio_ai.domain.pricing import format_brl
from emporio_ai.domain.schemas import (
    ActionRequest,
    IntentType,
    LookupStatus,
    OrderAccess,
)
from emporio_ai.domain.scope import find_unsold_item
from emporio_ai.tools.catalog import (
    MAX_RESULTS,
    get_product_details,
    get_product_price,
    search_products,
)
from emporio_ai.tools.orders import get_order_status
from emporio_ai.tools.policies import search_policies
from emporio_ai.tools.store import get_store_information

logger = logging.getLogger(__name__)

MAX_ACTIONS_PER_TURN = 2
MAX_HISTORY_MESSAGES = 6

# Identifiers and search internals that the reply has no use for.
_HIDDEN_FACT_FIELDS = frozenset({"product_id", "keywords"})


def _to_facts(value: Any) -> Any:
    """Turn tool results into plain data for the prompt, with amounts already formatted."""
    if isinstance(value, BaseModel):
        return _to_facts(value.model_dump())
    if isinstance(value, dict):
        return {
            key: _to_facts(item)
            for key, item in value.items()
            if key not in _HIDDEN_FACT_FIELDS and item not in (None, (), [], {})
        }
    if isinstance(value, list | tuple):
        return [_to_facts(item) for item in value]
    if isinstance(value, Decimal):
        return format_brl(value)
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, Enum):
        return value.value
    return value


@dataclass
class _Turn:
    """What action handlers need, including references they may update during the turn."""

    repo: Repository
    policy_index: PolicyIndex
    user_message: str
    customer_email: str | None
    last_product_id: int | None
    pending_order_id: int | None


def _fixed(intent: IntentType, result: OutcomeResult, message: str) -> ToolOutcome:
    return ToolOutcome(intent=intent, result=result, message=message)


def _run_search(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    result = search_products(
        turn.repo,
        term=action.product_query,
        category=action.category,
        max_price=action.max_price_brl,
        only_available=bool(action.in_stock),
        on_promotion=bool(action.on_promotion),
        limit=1 if action.only_cheapest else MAX_RESULTS,
    )
    if result.unsold_item:
        return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.UNSOLD_ITEM_MESSAGE)
    if result.unknown_category:
        return _fixed(action.intent, OutcomeResult.NO_RESULT, prompts.UNKNOWN_CATEGORY_MESSAGE)
    if not result.products:
        return _fixed(action.intent, OutcomeResult.NO_RESULT, prompts.NO_PRODUCTS_MESSAGE)
    # "Esse" only has one meaning when a single product was shown.
    if len(result.products) == 1:
        turn.last_product_id = result.products[0].product_id
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=_to_facts(result))


def _run_product_lookup(turn: _Turn, action: ActionRequest, lookup: Callable) -> ToolOutcome:
    if action.product_query and find_unsold_item(action.product_query):
        return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.UNSOLD_ITEM_MESSAGE)

    if action.refers_to_previous_product and turn.last_product_id is not None:
        result = lookup(turn.repo, product_id=turn.last_product_id)
    elif action.product_query:
        result = lookup(turn.repo, name=action.product_query)
    else:
        return _fixed(action.intent, OutcomeResult.NEEDS_INPUT, prompts.WHICH_PRODUCT_MESSAGE)

    if result.status is LookupStatus.NOT_FOUND:
        return _fixed(action.intent, OutcomeResult.NO_RESULT, prompts.PRODUCT_NOT_FOUND_MESSAGE)
    if result.status is LookupStatus.AMBIGUOUS:
        names = "\n".join(f"- {candidate.name}" for candidate in result.candidates)
        message = f"{prompts.AMBIGUOUS_PRODUCT_MESSAGE}\n{names}"
        return _fixed(action.intent, OutcomeResult.NEEDS_INPUT, message)

    found = getattr(result, "details", None) or result
    offer = found.offer
    turn.last_product_id = offer.product_id
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=_to_facts(found))


def _run_details(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    return _run_product_lookup(turn, action, get_product_details)


def _run_price(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    return _run_product_lookup(turn, action, get_product_price)


def _run_order(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    order_id = action.order_id or turn.pending_order_id
    result = get_order_status(turn.repo, order_id, turn.customer_email)
    if result.access is OrderAccess.ORDER_ID_REQUIRED:
        return _fixed(action.intent, OutcomeResult.NEEDS_INPUT, prompts.ORDER_ID_REQUIRED_MESSAGE)
    if result.access is OrderAccess.EMAIL_REQUIRED:
        turn.pending_order_id = order_id
        return _fixed(action.intent, OutcomeResult.NEEDS_INPUT, prompts.EMAIL_REQUIRED_MESSAGE)
    if result.access is OrderAccess.NOT_VERIFIED:
        turn.pending_order_id = order_id
        return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.NOT_VERIFIED_MESSAGE)
    turn.pending_order_id = None
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=_to_facts(result))


def _run_policy(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    result = search_policies(turn.policy_index, action.policy_topic or turn.user_message)
    if not result.sections and action.policy_topic:
        result = search_policies(turn.policy_index, turn.user_message)
    if not result.sections:
        return _fixed(action.intent, OutcomeResult.NO_RESULT, prompts.NO_POLICY_MESSAGE)
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=_to_facts(result))


def _run_store(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    if action.store_topic is None:
        return _run_policy(turn, action)
    result = get_store_information(turn.policy_index, action.store_topic)
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=_to_facts(result))


def _run_greeting(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    return _fixed(action.intent, OutcomeResult.SUCCESS, prompts.GREETING_MESSAGE)


def _run_out_of_scope(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    if find_unsold_item(turn.user_message):
        return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.UNSOLD_ITEM_MESSAGE)
    return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.OUT_OF_SCOPE_MESSAGE)


# The complete list of what the agent can do. An intent runs its handler and nothing else.
_HANDLERS: dict[IntentType, Callable[[_Turn, ActionRequest], ToolOutcome]] = {
    IntentType.PRODUCT_SEARCH: _run_search,
    IntentType.PRODUCT_DETAILS: _run_details,
    IntentType.PRODUCT_PRICE: _run_price,
    IntentType.ORDER_STATUS: _run_order,
    IntentType.POLICY_QUESTION: _run_policy,
    IntentType.STORE_INFORMATION: _run_store,
    IntentType.GREETING: _run_greeting,
    IntentType.OUT_OF_SCOPE: _run_out_of_scope,
}


def _is_valid(action: ActionRequest) -> bool:
    if action.max_price_brl is not None and action.max_price_brl < 0:
        return False
    return action.order_id is None or action.order_id > 0


def _understand_messages(state: ConversationState, repo: Repository) -> list[ChatMessage]:
    context = []
    last_product_id = state.get("last_product_id")
    product = repo.get_product(last_product_id) if last_product_id is not None else None
    if product:
        context.append(f"Produto em foco na conversa: {product.name}.")
    if state.get("pending_order_id") is not None:
        context.append(f"Pedido aguardando verificação: {state['pending_order_id']}.")
    messages: list[ChatMessage] = [{"role": "system", "content": prompts.UNDERSTAND_SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": "Contexto: " + " ".join(context)})
    messages.extend(state.get("messages", []))
    messages.append({"role": "user", "content": state["user_message"]})
    return messages


def _facts_for_prompt(outcomes: list[ToolOutcome]) -> str:
    items = []
    for outcome in outcomes:
        item: dict[str, Any] = {"intent": outcome.intent.value}
        if outcome.facts:
            item["facts"] = outcome.facts
        if outcome.message:
            item["fixed_message"] = outcome.message
        items.append(item)
    # Retrieved text cannot close the tag around it, since "<" never appears literally.
    return json.dumps(items, ensure_ascii=False).replace("<", "\\u003c")


def build_graph(
    llm: LLMClient, repo: Repository, policy_index: PolicyIndex
) -> CompiledStateGraph:
    def understand(state: ConversationState) -> dict[str, Any]:
        try:
            parsed = llm.parse_turn(_understand_messages(state, repo))
        except LLMUnavailableError as exc:
            logger.warning("understand failed: provider_error (%s)", exc)
            return {"failure": TurnFailure.PROVIDER_ERROR}
        except LLMOutputError:
            logger.warning("understand failed: validation_error")
            return {"failure": TurnFailure.VALIDATION_ERROR}

        actions = parsed.actions
        clarification = None if actions else parsed.clarification_question
        return {"actions": actions, "clarification": clarification, "failure": None}

    def validate(state: ConversationState) -> dict[str, Any]:
        actions = list(state.get("actions", []))
        if not all(_is_valid(action) for action in actions):
            return {"failure": TurnFailure.VALIDATION_ERROR}

        # An email answering a pending request is an order lookup, whatever the model made of it.
        has_order_action = any(a.intent is IntentType.ORDER_STATUS for a in actions)
        if (
            state.get("customer_email")
            and state.get("pending_order_id") is not None
            and not has_order_action
        ):
            actions = [
                ActionRequest(intent=IntentType.ORDER_STATUS),
                *[a for a in actions if a.intent is not IntentType.OUT_OF_SCOPE],
            ]

        update: dict[str, Any] = {
            "actions": actions[:MAX_ACTIONS_PER_TURN],
            "dropped_actions": len(actions) > MAX_ACTIONS_PER_TURN,
        }
        if not actions:
            update["clarification"] = state.get("clarification") or prompts.INVALID_OUTPUT_MESSAGE
        return update

    def execute(state: ConversationState) -> dict[str, Any]:
        turn = _Turn(
            repo=repo,
            policy_index=policy_index,
            user_message=state["user_message"],
            customer_email=state.get("customer_email"),
            last_product_id=state.get("last_product_id"),
            pending_order_id=state.get("pending_order_id"),
        )
        outcomes = []
        for action in state["actions"]:
            try:
                outcomes.append(_HANDLERS[action.intent](turn, action))
            except Exception as exc:
                # The type is enough to diagnose; messages could carry customer data.
                logger.error("tool failed: %s (%s)", action.intent.value, type(exc).__name__)
                outcomes.append(
                    _fixed(action.intent, OutcomeResult.TOOL_ERROR, prompts.TOOL_ERROR_MESSAGE)
                )
        return {
            "outcomes": outcomes,
            "last_product_id": turn.last_product_id,
            "pending_order_id": turn.pending_order_id,
        }

    def respond(state: ConversationState) -> dict[str, Any]:
        reply = _write_reply(state)
        history = [
            *state.get("messages", []),
            {"role": "user", "content": state["user_message"]},
            {"role": "assistant", "content": reply},
        ]
        return {"reply": reply, "messages": history[-MAX_HISTORY_MESSAGES:]}

    def _write_reply(state: ConversationState) -> str:
        failure = state.get("failure")
        if failure is TurnFailure.PROVIDER_ERROR:
            return prompts.PROVIDER_ERROR_MESSAGE
        if failure is TurnFailure.VALIDATION_ERROR:
            return prompts.INVALID_OUTPUT_MESSAGE

        clarification = state.get("clarification")
        if clarification:
            if find_unsupported_claims(clarification, state["user_message"]):
                return prompts.INVALID_OUTPUT_MESSAGE
            return clarification

        outcomes = state.get("outcomes", [])
        if all(outcome.facts is None for outcome in outcomes):
            # Nothing to write about, so no second call is spent.
            reply = "\n\n".join(outcome.message for outcome in outcomes if outcome.message)
        else:
            reply = _compose(state, outcomes)
        if state.get("dropped_actions"):
            reply = f"{reply}\n\n{prompts.DROPPED_ACTIONS_MESSAGE}"
        return reply

    def _compose(state: ConversationState, outcomes: list[ToolOutcome]) -> str:
        facts = _facts_for_prompt(outcomes)
        user_message = state["user_message"]
        messages: list[ChatMessage] = [
            {"role": "system", "content": prompts.COMPOSE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"<dados>{facts}</dados>\n\nMensagem do cliente: {user_message}",
            },
        ]
        try:
            reply = llm.compose_reply(messages)
        except (LLMUnavailableError, LLMOutputError) as exc:
            logger.warning("respond failed: provider_error (%s)", type(exc).__name__)
            return prompts.PROVIDER_ERROR_MESSAGE

        # The fixed messages and policy text are part of the facts, so they count as evidence.
        unsupported = find_unsupported_claims(reply, f"{facts} {user_message}")
        if unsupported:
            logger.warning("reply rejected, unsupported: %s", ", ".join(unsupported))
            return prompts.UNVERIFIED_REPLY_MESSAGE
        return reply

    def after_understand(state: ConversationState) -> str:
        return "respond" if state.get("failure") else "validate"

    def after_validate(state: ConversationState) -> str:
        return "execute" if state.get("actions") and not state.get("failure") else "respond"

    graph = StateGraph(ConversationState)
    graph.add_node("understand", understand)
    graph.add_node("validate", validate)
    graph.add_node("execute", execute)
    graph.add_node("respond", respond)
    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", after_understand, ["validate", "respond"])
    graph.add_conditional_edges("validate", after_validate, ["execute", "respond"])
    graph.add_edge("execute", "respond")
    graph.add_edge("respond", END)
    return graph.compile()
