"""The OpenAI adapter, against a stand-in client: no network and no API key."""

import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from openai.lib._pydantic import to_strict_json_schema
from pydantic import ValidationError

from emporio_ai.agent.llm import OpenAILLM
from emporio_ai.domain.errors import LLMOutputError, LLMUnavailableError
from emporio_ai.domain.schemas import ActionRequest, IntentType, ParsedTurn

PARSED = ParsedTurn(
    actions=[ActionRequest(intent=IntentType.GREETING)], needs_clarification=False
)
MESSAGES = [{"role": "user", "content": "Oi"}]


def parse_response(parsed=PARSED):
    message = SimpleNamespace(parsed=parsed, refusal=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def create_response(content="Olá!", finish_reason="stop"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish_reason)])


class FakeCompletions:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    parse = _next
    create = _next


def make_llm(*responses, model="test-model", **kwargs):
    completions = FakeCompletions(*responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAILLM(api_key="not-a-real-key", model=model, client=client, **kwargs), completions


def api_error():
    request = httpx.Request("POST", "https://example.test")
    return openai.APIConnectionError(request=request)


def validation_error():
    try:
        ParsedTurn.model_validate({"actions": [{"intent": "not_an_intent"}]})
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a validation error")


# Parsing


def test_parse_returns_the_structure():
    llm, completions = make_llm(parse_response())
    assert llm.parse_turn(MESSAGES) == PARSED
    call = completions.calls[0]
    assert call["model"] == "test-model"
    assert call["response_format"] is ParsedTurn
    assert call["max_completion_tokens"] > 0
    assert call["store"] is False


@pytest.mark.parametrize(
    "first_failure",
    [
        pytest.param(validation_error, id="invalid-schema"),
        pytest.param(lambda: json.JSONDecodeError("bad", "x", 0), id="invalid-json"),
        pytest.param(lambda: parse_response(parsed=None), id="refusal"),
    ],
)
def test_parse_retries_once_on_unusable_output(first_failure):
    llm, completions = make_llm(first_failure(), parse_response())
    assert llm.parse_turn(MESSAGES) == PARSED
    assert len(completions.calls) == 2


def test_parse_gives_up_after_the_retry():
    llm, completions = make_llm(validation_error(), validation_error(), parse_response())
    with pytest.raises(LLMOutputError):
        llm.parse_turn(MESSAGES)
    assert len(completions.calls) == 2


def test_provider_failure_is_not_retried_here():
    # The SDK already retries transport errors, and a second loop here would multiply the attempts.
    llm, completions = make_llm(api_error(), parse_response())
    with pytest.raises(LLMUnavailableError):
        llm.parse_turn(MESSAGES)
    assert len(completions.calls) == 1


# Token caps


def test_parse_cap_leaves_room_for_reasoning():
    # 425 tokens were measured for one real message, 243 of them reasoning. A cap of 400 made it
    # fail on every attempt, and the customer was told the message was not understood.
    llm, completions = make_llm(parse_response())
    llm.parse_turn(MESSAGES)
    assert completions.calls[0]["max_completion_tokens"] >= 2 * 425


def test_cut_off_structure_is_retried_and_then_reported():
    error = openai.LengthFinishReasonError(completion=SimpleNamespace(usage=None))
    llm, completions = make_llm(error, error, parse_response())
    with pytest.raises(LLMOutputError):
        llm.parse_turn(MESSAGES)
    assert len(completions.calls) == 2


# Sampling options


def test_older_models_get_a_temperature_and_no_reasoning_effort():
    llm, completions = make_llm(parse_response(), create_response(), model="gpt-4o-mini")
    llm.parse_turn(MESSAGES)
    llm.compose_reply(MESSAGES)
    assert completions.calls[0]["temperature"] == 0
    assert completions.calls[1]["temperature"] == 0.3
    assert all("reasoning_effort" not in call for call in completions.calls)


def test_newer_models_get_a_reasoning_effort_and_no_temperature():
    # gpt-6-luna answers 400 to any temperature other than the default.
    llm, completions = make_llm(
        parse_response(), create_response(), model="gpt-6-luna", reasoning_effort="low"
    )
    llm.parse_turn(MESSAGES)
    llm.compose_reply(MESSAGES)
    assert all(call["reasoning_effort"] == "low" for call in completions.calls)
    assert all("temperature" not in call for call in completions.calls)


# Schema


def schema_keys(node):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from schema_keys(value)
    elif isinstance(node, list):
        for value in node:
            yield from schema_keys(value)


def test_strict_schema_has_no_defaults():
    # As far as we know, strict mode rejects "default", and only None defaults are stripped for us.
    schema = to_strict_json_schema(ParsedTurn)
    assert "default" not in set(schema_keys(schema))
    assert "needs_clarification" in schema["required"]


def test_schema_sends_no_code_comments_to_the_model():
    # Pydantic turns class docstrings into descriptions, which cost tokens on every call.
    assert "description" not in set(schema_keys(to_strict_json_schema(ParsedTurn)))


def test_model_has_no_way_to_choose_an_email_or_product_id():
    fields = set(ActionRequest.model_fields)
    assert not {"customer_email", "email", "product_id"} & fields


@pytest.mark.parametrize(
    "action",
    [
        {"intent": "execute_sql"},
        {"intent": "greeting", "max_price_brl": "muito"},
        {"intent": "order_status", "order_id": "oito"},
        {"intent": "store_information", "store_topic": "senhas"},
    ],
)
def test_invalid_actions_are_rejected(action):
    with pytest.raises(ValidationError):
        ParsedTurn.model_validate({"actions": [action], "needs_clarification": False})


def test_turn_needs_every_required_field():
    with pytest.raises(ValidationError):
        ParsedTurn.model_validate({"actions": []})


def test_price_is_read_as_decimal():
    turn = ParsedTurn.model_validate_json(
        '{"actions": [{"intent": "product_search", "max_price_brl": 1000.5}],'
        ' "needs_clarification": false}'
    )
    assert str(turn.actions[0].max_price_brl) == "1000.5"


# Writing


def test_compose_returns_the_text():
    llm, completions = make_llm(create_response("  Olá!  "))
    assert llm.compose_reply(MESSAGES) == "Olá!"
    assert completions.calls[0]["max_completion_tokens"] > 0
    assert completions.calls[0]["store"] is False


@pytest.mark.parametrize(
    "response",
    [
        pytest.param(create_response("Cortada no mei", finish_reason="length"), id="cut-off"),
        pytest.param(create_response("Bloqueada", finish_reason="content_filter"), id="filtered"),
        pytest.param(create_response(""), id="empty"),
        pytest.param(create_response(None), id="none"),
    ],
)
def test_compose_rejects_unusable_replies(response):
    llm, _ = make_llm(response)
    with pytest.raises(LLMOutputError):
        llm.compose_reply(MESSAGES)


def test_compose_reports_provider_failure():
    llm, _ = make_llm(api_error())
    with pytest.raises(LLMUnavailableError):
        llm.compose_reply(MESSAGES)
