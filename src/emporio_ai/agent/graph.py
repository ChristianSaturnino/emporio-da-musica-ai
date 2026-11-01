"""The conversation workflow: understand -> validate -> execute -> respond.

The model is called in understand and, when there is something to write about, in respond. Validate
and execute are plain Python, and there is no loop: each turn takes this path once.
"""

import json
import logging
import re
import time
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
from emporio_ai.domain.normalization import normalize_text
from emporio_ai.domain.pricing import format_brl
from emporio_ai.domain.schemas import (
    ActionRequest,
    IntentType,
    LookupStatus,
    OrderAccess,
)
from emporio_ai.domain.scope import find_unsold_item, mentions_store_subject
from emporio_ai.observability.events import elapsed_ms, log_event
from emporio_ai.tools.catalog import (
    MAX_RESULTS,
    find_category_mention,
    get_product_details,
    get_product_price,
    is_generic,
    search_products,
)
from emporio_ai.tools.orders import get_order_status
from emporio_ai.tools.policies import search_policies
from emporio_ai.tools.store import get_store_information

MAX_ACTIONS_PER_TURN = 2
MAX_HISTORY_MESSAGES = 6
MAX_EARLIER_CUSTOMER_MESSAGES = 2
_COMPOSE_ATTEMPTS = 2

# Identifiers and search internals that the reply has no use for.
_HIDDEN_FACT_FIELDS = frozenset({"product_id", "keywords"})
_CLOSING_WORDS = re.compile(r"obrigad|valeu|agradec|tchau|ate mais|ate logo")
_NON_REQUESTS = frozenset({IntentType.GREETING, IntentType.OUT_OF_SCOPE})
_PRODUCT_LOOKUPS = frozenset({IntentType.PRODUCT_DETAILS, IntentType.PRODUCT_PRICE})
_CANNOT_CARRY_POLICY_TOPIC = _PRODUCT_LOOKUPS | {IntentType.PRODUCT_SEARCH, IntentType.ORDER_STATUS}


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
    last_result_ids: list[int]
    pending_order_id: int | None


def _fixed(intent: IntentType, result: OutcomeResult, message: str) -> ToolOutcome:
    return ToolOutcome(intent=intent, result=result, message=message)


def _run_search(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    # "O que vocês vendem?" names nothing, and the cheapest products would all be ukuleles.
    has_filter = (
        action.max_price_brl is not None
        or action.on_promotion
        or action.only_cheapest
        or action.only_most_expensive
    )
    if not has_filter and is_generic(action.product_query) and is_generic(action.category):
        return _fixed(action.intent, OutcomeResult.NEEDS_INPUT, prompts.CLARIFICATION_MESSAGE)

    result = search_products(
        turn.repo,
        term=action.product_query,
        category=action.category,
        max_price=action.max_price_brl,
        only_available=bool(action.in_stock),
        on_promotion=bool(action.on_promotion),
        limit=1 if action.only_cheapest or action.only_most_expensive else MAX_RESULTS,
        most_expensive_first=bool(action.only_most_expensive),
    )
    if result.unsold_item:
        # An accessory only counts if the customer asked for it. The model's own rewording must not
        # turn "presente para quem toca guitarra" into a refusal.
        mention = find_category_mention(turn.repo, turn.user_message)
        if mention and not find_unsold_item(turn.user_message):
            return _run_search(
                turn, action.model_copy(update={"product_query": None, "category": mention})
            )
        return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.UNSOLD_ITEM_MESSAGE)
    if result.unknown_category:
        # The model can write a category of its own, like "presentes para guitarrista", when the
        # customer's own words name an instrument the store carries.
        mention = find_category_mention(turn.repo, turn.user_message)
        if not mention:
            return _fixed(action.intent, OutcomeResult.NO_RESULT, prompts.UNKNOWN_CATEGORY_MESSAGE)
        return _run_search(turn, action.model_copy(update={"category": mention}))
    if not result.products:
        return _fixed(action.intent, OutcomeResult.NO_RESULT, prompts.NO_PRODUCTS_MESSAGE)
    # "Esse" only has one meaning when a single product was shown. A list is kept for "o segundo".
    turn.last_result_ids = [offer.product_id for offer in result.products]
    if len(result.products) == 1:
        turn.last_product_id = result.products[0].product_id
    facts = _to_facts(result)
    if action.only_cheapest or action.only_most_expensive:
        # The answer is settled. With the match count in view, the real model said it could not
        # confirm which product was the cheapest and offered to keep looking.
        del facts["total_matches"]
        facts["ranking"] = "most_expensive" if action.only_most_expensive else "cheapest"
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=facts)


def _run_product_lookup(turn: _Turn, action: ActionRequest, lookup: Callable) -> ToolOutcome:
    if action.product_query and find_unsold_item(action.product_query):
        return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.UNSOLD_ITEM_MESSAGE)

    position = action.position_in_list
    chosen_from_list = position is not None and 1 <= position <= len(turn.last_result_ids)
    if chosen_from_list:
        result = lookup(turn.repo, product_id=turn.last_result_ids[position - 1])
    elif action.refers_to_previous_product and turn.last_product_id is not None:
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
    facts = _to_facts(found)
    if chosen_from_list:
        # Seeing one product, the real model told customers it was the only one the store had.
        facts["chosen_from_list"] = True
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=facts)


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
    # Both, because a topic can lose the decisive word: "boquilha tem troca?" came back as "troca",
    # which missed the section saying mouthpieces cannot be exchanged.
    query = f"{action.policy_topic or ''} {turn.user_message}"
    result = search_policies(turn.policy_index, query)
    if not result.sections:
        return _fixed(action.intent, OutcomeResult.NO_RESULT, prompts.NO_POLICY_MESSAGE)
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=_to_facts(result))


def _run_store(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    if action.store_topic is None:
        return _run_policy(turn, action)
    result = get_store_information(turn.policy_index, action.store_topic)
    return ToolOutcome(action.intent, OutcomeResult.SUCCESS, facts=_to_facts(result))


def _run_greeting(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    is_closing = _CLOSING_WORDS.search(normalize_text(turn.user_message))
    message = prompts.FAREWELL_MESSAGE if is_closing else prompts.GREETING_MESSAGE
    return _fixed(action.intent, OutcomeResult.SUCCESS, message)


def _run_how_to_buy(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    if find_unsold_item(turn.user_message):
        return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.UNSOLD_ITEM_MESSAGE)
    return _fixed(action.intent, OutcomeResult.SUCCESS, prompts.HOW_TO_BUY_MESSAGE)


def _run_out_of_scope(turn: _Turn, action: ActionRequest) -> ToolOutcome:
    if find_unsold_item(turn.user_message):
        return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.UNSOLD_ITEM_MESSAGE)
    # A customer wrongly refused is worse than one asked a question they did not need.
    if mentions_store_subject(turn.user_message):
        return _fixed(action.intent, OutcomeResult.NEEDS_INPUT, prompts.CLARIFICATION_MESSAGE)
    return _fixed(action.intent, OutcomeResult.BLOCKED, prompts.OUT_OF_SCOPE_MESSAGE)


# The complete list of what the agent can do. An intent runs its handler and nothing else.
_HANDLERS: dict[IntentType, Callable[[_Turn, ActionRequest], ToolOutcome]] = {
    IntentType.PRODUCT_SEARCH: _run_search,
    IntentType.PRODUCT_DETAILS: _run_details,
    IntentType.PRODUCT_PRICE: _run_price,
    IntentType.ORDER_STATUS: _run_order,
    IntentType.POLICY_QUESTION: _run_policy,
    IntentType.STORE_INFORMATION: _run_store,
    IntentType.HOW_TO_BUY: _run_how_to_buy,
    IntentType.GREETING: _run_greeting,
    IntentType.OUT_OF_SCOPE: _run_out_of_scope,
}


def _is_valid(action: ActionRequest) -> bool:
    if action.max_price_brl is not None and action.max_price_brl < 0:
        return False
    return action.order_id is None or action.order_id > 0


def _as_details(action: ActionRequest) -> ActionRequest:
    return action.model_copy(update={"intent": IntentType.PRODUCT_DETAILS})


def _split_policy_topic(action: ActionRequest) -> list[ActionRequest]:
    if not action.policy_topic or action.intent not in _CANNOT_CARRY_POLICY_TOPIC:
        return [action]
    return [
        action.model_copy(update={"policy_topic": None}),
        ActionRequest(intent=IntentType.POLICY_QUESTION, policy_topic=action.policy_topic),
    ]


def _clean_actions(actions: list[ActionRequest]) -> list[ActionRequest]:
    """Remove the padding seen from the real model, which tends to fill both action slots.

    An order question came with an extra out_of_scope that made the reply refuse itself, and a
    single search came twice. A greeting or refusal only stands when nothing else was asked, and
    a price and details request for one product becomes details, which include the price.

    The opposite also happened: "Preço do Yamaha C40 e aceita boleto?" came as a single price action
    carrying a policy topic, so the policy question went unanswered. Those are split in two.
    """
    requests = [a for a in actions if a.intent not in _NON_REQUESTS] or actions[:1]
    requests = [part for action in requests for part in _split_policy_topic(action)]
    cleaned: list[ActionRequest] = []
    for action in requests:
        twin = next(
            (
                index
                for index, kept in enumerate(cleaned)
                if action.intent in _PRODUCT_LOOKUPS
                and kept.intent in _PRODUCT_LOOKUPS
                and _as_details(kept) == _as_details(action)
            ),
            None,
        )
        if twin is not None:
            cleaned[twin] = _as_details(action)
        elif action not in cleaned:
            cleaned.append(action)
    return cleaned


def _understand_messages(state: ConversationState, repo: Repository) -> list[ChatMessage]:
    context = []
    last_product_id = state.get("last_product_id")
    product = repo.get_product(last_product_id) if last_product_id is not None else None
    if product:
        context.append(f"Produto em foco na conversa: {product.name}.")
    listed = [repo.get_product(product_id) for product_id in state.get("last_result_ids", [])]
    if len(listed) > 1:
        names = "; ".join(f"{i}. {p.name}" for i, p in enumerate(listed, start=1) if p)
        context.append(f"Última lista mostrada ao cliente: {names}.")
    if state.get("pending_order_id") is not None:
        context.append(f"Pedido aguardando verificação: {state['pending_order_id']}.")
    messages: list[ChatMessage] = [{"role": "system", "content": prompts.UNDERSTAND_SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": "Contexto: " + " ".join(context)})
    messages.extend(state.get("messages", []))
    messages.append({"role": "user", "content": state["user_message"]})
    return messages


def _required_additions(outcomes: list[ToolOutcome], reply: str) -> list[str]:
    """Sentences for what the reply must carry but the model left out.

    Seen in real runs: a verified order without its tracking code, and a promotion without its
    percentage, which the manual requires (section 6.2).
    """
    additions = []
    for outcome in outcomes:
        facts = outcome.facts or {}
        tracking_code = facts.get("order", {}).get("tracking_code")
        if tracking_code and tracking_code not in reply:
            additions.append(f"Código de rastreio: {tracking_code}.")
        offers = [facts["offer"]] if "offer" in facts else facts.get("products", [])
        for offer in offers:
            quote = offer["quote"]
            percent = quote.get("promotion_percent")
            if percent and f"{percent}%" not in reply.replace(" %", "%"):
                additions.append(
                    f"{offer['name']}: {percent}% de desconto, de {quote['list_price']} "
                    f"por {quote['promotional_price']}."
                )
    return additions


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
        except LLMUnavailableError:
            return {"failure": TurnFailure.PROVIDER_ERROR}
        except LLMOutputError:
            return {"failure": TurnFailure.VALIDATION_ERROR}

        log_event(
            "intent_parsed",
            intents=[action.intent.value for action in parsed.actions],
            action_count=len(parsed.actions),
            needs_clarification=parsed.needs_clarification,
            prompt_version=prompts.PROMPT_VERSION,
        )
        return {
            "actions": parsed.actions,
            "needs_clarification": parsed.needs_clarification,
            "failure": None,
        }

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
            actions = [ActionRequest(intent=IntentType.ORDER_STATUS), *actions]

        # The model has refused "quero comprar um violão". Whatever it made of the message, naming
        # an instrument the store carries is a request to see it, unless it is for an accessory.
        message = state["user_message"]
        was_refused = all(a.intent is IntentType.OUT_OF_SCOPE for a in actions)
        if was_refused and not find_unsold_item(message):
            mention = find_category_mention(repo, message)
            if mention:
                actions = [ActionRequest(intent=IntentType.PRODUCT_SEARCH, category=mention)]

        actions = _clean_actions(actions)
        return {
            "actions": actions[:MAX_ACTIONS_PER_TURN],
            "dropped_actions": len(actions) > MAX_ACTIONS_PER_TURN,
        }

    def execute(state: ConversationState) -> dict[str, Any]:
        turn = _Turn(
            repo=repo,
            policy_index=policy_index,
            user_message=state["user_message"],
            customer_email=state.get("customer_email"),
            last_product_id=state.get("last_product_id"),
            last_result_ids=state.get("last_result_ids", []),
            pending_order_id=state.get("pending_order_id"),
        )
        outcomes = []
        for action in state["actions"]:
            handler = _HANDLERS[action.intent]
            started = time.perf_counter()
            error_type = None
            try:
                outcome = handler(turn, action)
            except Exception as exc:
                # The type is enough to diagnose; messages could carry customer data.
                error_type = type(exc).__name__
                outcome = _fixed(
                    action.intent, OutcomeResult.TOOL_ERROR, prompts.TOOL_ERROR_MESSAGE
                )
            log_event(
                "tool_executed",
                level=logging.ERROR if error_type else logging.INFO,
                intent=action.intent.value,
                tool=handler.__name__.removeprefix("_run_"),
                result=outcome.result.value,
                latency_ms=elapsed_ms(started),
                error_type=error_type,
            )
            outcomes.append(outcome)
        # Explaining how to buy right after declining an accessory contradicts the refusal.
        if any(outcome.message == prompts.UNSOLD_ITEM_MESSAGE for outcome in outcomes):
            outcomes = [o for o in outcomes if o.message != prompts.HOW_TO_BUY_MESSAGE]
        return {
            "outcomes": outcomes,
            "last_product_id": turn.last_product_id,
            "last_result_ids": turn.last_result_ids,
            "pending_order_id": turn.pending_order_id,
        }

    def respond(state: ConversationState) -> dict[str, Any]:
        reply, failure = _write_reply(state)
        history = [
            *state.get("messages", []),
            {"role": "user", "content": state["user_message"]},
            {"role": "assistant", "content": reply},
        ]
        return {"reply": reply, "failure": failure, "messages": history[-MAX_HISTORY_MESSAGES:]}

    def _write_reply(state: ConversationState) -> tuple[str, TurnFailure | None]:
        failure = state.get("failure")
        if failure is TurnFailure.PROVIDER_ERROR:
            return prompts.PROVIDER_ERROR_MESSAGE, failure
        if failure is TurnFailure.VALIDATION_ERROR:
            return prompts.INVALID_OUTPUT_MESSAGE, failure

        if not state.get("actions"):
            # The real model has returned no actions for prompt injection, with or without asking
            # for clarification. Either way the customer gets a fixed message.
            if state.get("needs_clarification") or mentions_store_subject(state["user_message"]):
                return prompts.CLARIFICATION_MESSAGE, None
            return prompts.OUT_OF_SCOPE_MESSAGE, None

        outcomes = state.get("outcomes", [])
        if all(outcome.facts is None for outcome in outcomes):
            # Nothing to write about, so no second call is spent.
            messages = dict.fromkeys(outcome.message for outcome in outcomes if outcome.message)
            reply = "\n\n".join(messages)
        else:
            reply, failure = _compose(state, outcomes)
        if state.get("dropped_actions") and failure is None:
            reply = f"{reply}\n\n{prompts.DROPPED_ACTIONS_MESSAGE}"
        return reply, failure

    def _compose(
        state: ConversationState, outcomes: list[ToolOutcome]
    ) -> tuple[str, TurnFailure | None]:
        facts = _facts_for_prompt(outcomes)
        user_message = state["user_message"]
        # Only what the customer said, so the reply does not ask again what it was already told.
        earlier = [m["content"] for m in state.get("messages", []) if m["role"] == "user"]
        earlier = earlier[-MAX_EARLIER_CUSTOMER_MESSAGES:]
        context = "".join(f"Mensagem anterior do cliente: {text}\n" for text in earlier)
        prompt = f"<dados>{facts}</dados>\n\n{context}Mensagem do cliente: {user_message}"
        messages: list[ChatMessage] = [
            {"role": "system", "content": prompts.COMPOSE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]
        # The fixed messages and policy text are part of the facts, so they count as evidence, and
        # so does a limit the customer wrote differently: "800 reais" can come back as "R$ 800".
        limits = " ".join(
            format_brl(action.max_price_brl)
            for action in state.get("actions", [])
            if action.max_price_brl is not None
        )
        evidence = f"{facts} {context} {user_message} {limits}"

        for _ in range(_COMPOSE_ATTEMPTS):
            try:
                reply = llm.compose_reply(messages)
            except LLMUnavailableError:
                return prompts.PROVIDER_ERROR_MESSAGE, TurnFailure.PROVIDER_ERROR
            except LLMOutputError:
                return prompts.PROVIDER_ERROR_MESSAGE, TurnFailure.VALIDATION_ERROR

            unsupported = find_unsupported_claims(reply, evidence)
            if not unsupported:
                return "\n\n".join([reply, *_required_additions(outcomes, reply)]), None
            log_event("reply_rejected", level=logging.WARNING, unsupported=unsupported)
            messages = [
                *messages,
                {"role": "assistant", "content": reply},
                {"role": "user", "content": prompts.REJECTED_REPLY_REMINDER},
            ]
        return prompts.UNVERIFIED_REPLY_MESSAGE, TurnFailure.REPLY_REJECTED

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
