"""Model provider adapter. The rest of the agent only knows the LLMClient protocol."""

import logging
import time
from typing import Protocol

import openai
from pydantic import ValidationError

from emporio_ai.domain.errors import LLMOutputError, LLMSetupError, LLMUnavailableError
from emporio_ai.domain.schemas import ParsedTurn
from emporio_ai.observability.events import elapsed_ms, log_event

ChatMessage = dict[str, str]

_TIMEOUT_SECONDS = 20
_TRANSPORT_RETRIES = 1
_PARSE_ATTEMPTS = 2
# Caps on what one call can cost. Reasoning tokens count against them: a parse measured at 243 of
# reasoning and 425 in all, so a cap of 400 made harder messages fail on every attempt.
_MAX_PARSE_TOKENS = 1000
_MAX_REPLY_TOKENS = 1500
# Plain JSON instead of the provider's strict schema, which forces every field to be written even
# when null. Measured on the same messages: 30 output tokens instead of 102, and 1106 ms instead of
# 1520 ms. ParsedTurn still validates the result, so nothing unchecked goes further.
_JSON_OUTPUT = {"type": "json_object"}
# The requests are fixed, so these point to the key, the model or a setting, not to a message.
_SETUP_ERRORS = (
    openai.AuthenticationError,
    openai.BadRequestError,
    openai.NotFoundError,
    openai.PermissionDeniedError,
)


class LLMClient(Protocol):
    def parse_turn(self, messages: list[ChatMessage]) -> ParsedTurn: ...

    def compose_reply(self, messages: list[ChatMessage]) -> str: ...


def _log_usage(kind: str, completion: object, started: float) -> None:
    usage = getattr(completion, "usage", None)
    if usage is not None:
        details = getattr(usage, "completion_tokens_details", None)
        log_event(
            "llm_call",
            kind=kind,
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            reasoning_tokens=getattr(details, "reasoning_tokens", None),
            latency_ms=elapsed_ms(started),
        )


def _provider_error(exc: openai.APIError) -> LLMUnavailableError:
    """Log the type and tell setup problems from passing ones.

    Someone running this with a key that cannot use the model was told to try again in a moment,
    and nothing in the logs said why.
    """
    error_type = type(exc).__name__
    log_event("llm_failed", level=logging.ERROR, error_type=error_type)
    if isinstance(exc, _SETUP_ERRORS) or getattr(exc, "code", None) == "insufficient_quota":
        return LLMSetupError(error_type)
    return LLMUnavailableError(error_type)


class OpenAILLM:
    def __init__(
        self,
        api_key: str,
        model: str,
        client: openai.OpenAI | None = None,
        reasoning_effort: str = "none",
    ) -> None:
        self._model = model
        self._reasoning_effort = reasoning_effort
        self._client = client or openai.OpenAI(
            api_key=api_key, timeout=_TIMEOUT_SECONDS, max_retries=_TRANSPORT_RETRIES
        )

    def _sampling_options(self, temperature: float) -> dict[str, float | str]:
        """Older models take a temperature. Newer ones reject it and take a reasoning effort."""
        if self._model.startswith("gpt-4"):
            return {"temperature": temperature}
        return {"reasoning_effort": self._reasoning_effort}

    def parse_turn(self, messages: list[ChatMessage]) -> ParsedTurn:
        """Ask for a ParsedTurn as JSON and validate it, retrying once on unusable output."""
        last_error: Exception | None = None
        for _ in range(_PARSE_ATTEMPTS):
            started = time.perf_counter()
            try:
                completion = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    response_format=_JSON_OUTPUT,
                    max_completion_tokens=_MAX_PARSE_TOKENS,
                    store=False,
                    **self._sampling_options(temperature=0),
                )
            except openai.APIError as exc:
                raise _provider_error(exc) from exc

            _log_usage("parse", completion, started)
            try:
                # Also covers invalid JSON, such as an answer cut off by the token cap.
                return ParsedTurn.model_validate_json(completion.choices[0].message.content or "")
            except ValidationError as exc:
                last_error = exc
        raise LLMOutputError("the model gave no usable structure") from last_error

    def compose_reply(self, messages: list[ChatMessage]) -> str:
        started = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                max_completion_tokens=_MAX_REPLY_TOKENS,
                store=False,
                **self._sampling_options(temperature=0.3),
            )
        except openai.APIError as exc:
            raise _provider_error(exc) from exc

        _log_usage("compose", completion, started)
        choice = completion.choices[0]
        content = (choice.message.content or "").strip()
        # A reply cut off by the token cap could end in the middle of a condition.
        if choice.finish_reason != "stop" or not content:
            raise LLMOutputError(f"unusable reply: finish_reason={choice.finish_reason}")
        return content
