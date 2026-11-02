"""JSON events for following a turn. Only allowed fields are written, never message text."""

import hashlib
import json
import logging
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

logger = logging.getLogger("emporio_ai.events")

# Anything else passed to log_event is dropped, so a careless call cannot log a message or an email.
_ALLOWED_FIELDS = frozenset(
    {
        "action_count",
        "dropped_actions",
        "error_type",
        "input_tokens",
        "intent",
        "intents",
        "kind",
        "latency_ms",
        "needs_clarification",
        "off_topic_part",
        "output_tokens",
        "prompt_version",
        "reasoning_tokens",
        "result",
        "tool",
        "unsupported",
    }
)

_turn_fields: ContextVar[dict[str, str] | None] = ContextVar("turn_fields", default=None)


def _opaque(session_id: str) -> str:
    return hashlib.sha256(session_id.encode()).hexdigest()[:12]


@contextmanager
def turn_context(session_id: str) -> Iterator[None]:
    """Attach a session and turn ID to every event logged inside the block."""
    token = _turn_fields.set({"session_id": _opaque(session_id), "turn_id": uuid.uuid4().hex[:12]})
    try:
        yield
    finally:
        _turn_fields.reset(token)


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    allowed = {key: value for key, value in fields.items() if key in _ALLOWED_FIELDS}
    payload = {"event": event, **(_turn_fields.get() or {}), **allowed}
    logger.log(level, json.dumps(payload, ensure_ascii=False, default=str))


def elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def configure_logging() -> None:
    """Send events to stderr. Safe to call again, since Streamlit reruns the script constantly."""
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
