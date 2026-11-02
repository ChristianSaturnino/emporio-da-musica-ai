"""Public chat API, which any interface can sit on top of."""

import logging
import time

from emporio_ai.agent import prompts
from emporio_ai.agent.graph import build_graph
from emporio_ai.agent.guards import mask_emails
from emporio_ai.agent.llm import LLMClient, OpenAILLM
from emporio_ai.agent.state import ConversationState
from emporio_ai.config import Settings, get_settings
from emporio_ai.data.policy_index import PolicyIndex, build_policy_index
from emporio_ai.data.repository import Repository
from emporio_ai.domain.errors import ConfigurationError
from emporio_ai.observability.events import elapsed_ms, log_event, turn_context

MAX_MESSAGE_LENGTH = 1000
# Sessions live in memory, so a long-running app must forget the oldest ones.
MAX_SESSIONS = 1000


class ConversationService:
    """Keeps a light state per session, in memory, and always answers with a customer message."""

    def __init__(self, llm: LLMClient, repo: Repository, policy_index: PolicyIndex) -> None:
        self._graph = build_graph(llm, repo, policy_index)
        self._sessions: dict[str, ConversationState] = {}

    def chat(self, session_id: str, message: str) -> str:
        text = message.strip()
        # "??" or an emoji has nothing to interpret, so no call is spent on it.
        if not any(character.isalnum() for character in text):
            return prompts.EMPTY_MESSAGE
        if len(text) > MAX_MESSAGE_LENGTH:
            return prompts.TOO_LONG_MESSAGE

        # The email is split off here: from now on the conversation only holds the masked text, and
        # the address itself lives for this one turn.
        masked_message, customer_email = mask_emails(text)
        session = self._sessions.get(session_id, {})
        started = time.perf_counter()
        with turn_context(session_id):
            log_event("turn_started")
            try:
                result = self._graph.invoke(
                    {
                        "messages": session.get("messages", []),
                        "last_product_id": session.get("last_product_id"),
                        "last_result_ids": session.get("last_result_ids", []),
                        "pending_order_id": session.get("pending_order_id"),
                        "verified_order_id": session.get("verified_order_id"),
                        "user_message": masked_message,
                        "customer_email": customer_email,
                    }
                )
            except Exception as exc:
                # The type is enough to diagnose; messages could carry customer data.
                log_event(
                    "turn_failed",
                    level=logging.ERROR,
                    result="unexpected_error",
                    error_type=type(exc).__name__,
                    latency_ms=elapsed_ms(started),
                )
                return prompts.UNEXPECTED_ERROR_MESSAGE

            failure = result.get("failure")
            if failure:
                log_event(
                    "turn_failed",
                    level=logging.WARNING,
                    result=failure.value,
                    latency_ms=elapsed_ms(started),
                )
            else:
                log_event("turn_completed", result="success", latency_ms=elapsed_ms(started))

        self._sessions.pop(session_id, None)
        self._sessions[session_id] = {
            "messages": result["messages"],
            "last_product_id": result.get("last_product_id"),
            "last_result_ids": result.get("last_result_ids", []),
            "pending_order_id": result.get("pending_order_id"),
            "verified_order_id": result.get("verified_order_id"),
        }
        if len(self._sessions) > MAX_SESSIONS:
            # Dictionaries keep insertion order, and active sessions were just moved to the end.
            del self._sessions[next(iter(self._sessions))]
        return result["reply"]

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


def create_service(settings: Settings | None = None) -> ConversationService:
    settings = settings or get_settings()
    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not set. Add it to your .env file.")
    return ConversationService(
        llm=OpenAILLM(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            reasoning_effort=settings.openai_reasoning_effort,
        ),
        repo=Repository(settings.db_path),
        policy_index=build_policy_index(settings.data_dir / "politicas_da_loja.pdf"),
    )
