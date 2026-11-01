"""Model provider adapter. The rest of the agent only knows the LLMClient protocol."""

import json
from typing import Protocol

import openai
from pydantic import ValidationError

from emporio_ai.domain.errors import LLMOutputError, LLMUnavailableError
from emporio_ai.domain.schemas import ParsedTurn

ChatMessage = dict[str, str]

_TIMEOUT_SECONDS = 30
_TRANSPORT_RETRIES = 1
_PARSE_ATTEMPTS = 2
# Caps on what one call can cost. Both are generous for the structure and replies expected here.
_MAX_PARSE_TOKENS = 400
_MAX_REPLY_TOKENS = 700


class LLMClient(Protocol):
    def parse_turn(self, messages: list[ChatMessage]) -> ParsedTurn: ...

    def compose_reply(self, messages: list[ChatMessage]) -> str: ...


class OpenAILLM:
    def __init__(self, api_key: str, model: str, client: openai.OpenAI | None = None) -> None:
        self._model = model
        self._client = client or openai.OpenAI(
            api_key=api_key, timeout=_TIMEOUT_SECONDS, max_retries=_TRANSPORT_RETRIES
        )

    def parse_turn(self, messages: list[ChatMessage]) -> ParsedTurn:
        """Ask for a ParsedTurn using native structured output, retrying once on unusable output."""
        last_error: Exception | None = None
        for _ in range(_PARSE_ATTEMPTS):
            try:
                completion = self._client.chat.completions.parse(
                    model=self._model,
                    messages=messages,
                    response_format=ParsedTurn,
                    temperature=0,
                    max_completion_tokens=_MAX_PARSE_TOKENS,
                    store=False,
                )
            except openai.APIError as exc:
                raise LLMUnavailableError(type(exc).__name__) from exc
            except (
                ValidationError,
                json.JSONDecodeError,
                openai.LengthFinishReasonError,
                openai.ContentFilterFinishReasonError,
            ) as exc:
                last_error = exc
                continue

            parsed = completion.choices[0].message.parsed
            if parsed is not None:
                return parsed
            last_error = None
        raise LLMOutputError("the model gave no usable structure") from last_error

    def compose_reply(self, messages: list[ChatMessage]) -> str:
        try:
            completion = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.3,
                max_completion_tokens=_MAX_REPLY_TOKENS,
                store=False,
            )
        except openai.APIError as exc:
            raise LLMUnavailableError(type(exc).__name__) from exc

        choice = completion.choices[0]
        content = (choice.message.content or "").strip()
        # A reply cut off by the token cap could end in the middle of a condition.
        if choice.finish_reason != "stop" or not content:
            raise LLMOutputError(f"unusable reply: finish_reason={choice.finish_reason}")
        return content
