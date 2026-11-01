"""Public chat API, which any interface can sit on top of."""

import logging

from emporio_ai.agent import prompts
from emporio_ai.agent.graph import build_graph
from emporio_ai.agent.guards import mask_emails
from emporio_ai.agent.llm import LLMClient, OpenAILLM
from emporio_ai.agent.state import ConversationState
from emporio_ai.config import Settings, get_settings
from emporio_ai.data.policy_index import PolicyIndex, build_policy_index
from emporio_ai.data.repository import Repository
from emporio_ai.domain.errors import ConfigurationError

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 1000


class ConversationService:
    """Keeps a light state per session, in memory, and always answers with a customer message."""

    def __init__(self, llm: LLMClient, repo: Repository, policy_index: PolicyIndex) -> None:
        self._graph = build_graph(llm, repo, policy_index)
        self._sessions: dict[str, ConversationState] = {}

    def chat(self, session_id: str, message: str) -> str:
        text = message.strip()
        if not text:
            return prompts.EMPTY_MESSAGE
        if len(text) > MAX_MESSAGE_LENGTH:
            return prompts.TOO_LONG_MESSAGE

        # The email is split off here: from now on the conversation only holds the masked text, and
        # the address itself lives for this one turn.
        masked_message, customer_email = mask_emails(text)
        session = self._sessions.get(session_id, {})
        try:
            result = self._graph.invoke(
                {
                    "messages": session.get("messages", []),
                    "last_product_id": session.get("last_product_id"),
                    "pending_order_id": session.get("pending_order_id"),
                    "user_message": masked_message,
                    "customer_email": customer_email,
                }
            )
        except Exception as exc:
            # The type is enough to diagnose; messages could carry customer data.
            logger.error("turn failed: %s", type(exc).__name__)
            return prompts.UNEXPECTED_ERROR_MESSAGE

        self._sessions[session_id] = {
            "messages": result["messages"],
            "last_product_id": result.get("last_product_id"),
            "pending_order_id": result.get("pending_order_id"),
        }
        return result["reply"]

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


def create_service(settings: Settings | None = None) -> ConversationService:
    settings = settings or get_settings()
    if not settings.openai_api_key:
        raise ConfigurationError("OPENAI_API_KEY is not set. Add it to your .env file.")
    return ConversationService(
        llm=OpenAILLM(api_key=settings.openai_api_key, model=settings.openai_model),
        repo=Repository(settings.db_path),
        policy_index=build_policy_index(settings.data_dir / "politicas_da_loja.pdf"),
    )
