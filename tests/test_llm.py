"""The OpenAI adapter, against a stand-in client: no network and no API key."""

from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import ValidationError

from emporio_ai.agent.llm import OpenAILLM
from emporio_ai.agent.prompts import UNDERSTAND_SYSTEM_PROMPT
from emporio_ai.domain.errors import LLMOutputError, LLMSetupError, LLMUnavailableError
from emporio_ai.domain.schemas import ActionRequest, IntentType, ParsedTurn

PARSED = ParsedTurn(
    actions=[ActionRequest(intent=IntentType.GREETING)], needs_clarification=False
)
MESSAGES = [{"role": "user", "content": "Oi"}]


def parse_response(content='{"actions": [{"intent": "greeting"}], "needs_clarification": false}'):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


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

    create = _next


def make_llm(*responses, model="test-model", **kwargs):
    completions = FakeCompletions(*responses)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return OpenAILLM(api_key="not-a-real-key", model=model, client=client, **kwargs), completions


def api_error():
    request = httpx.Request("POST", "https://example.test")
    return openai.APIConnectionError(request=request)


# Parsing


def test_parse_returns_the_structure():
    llm, completions = make_llm(parse_response())
    assert llm.parse_turn(MESSAGES) == PARSED
    call = completions.calls[0]
    assert call["model"] == "test-model"
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_completion_tokens"] > 0
    assert call["store"] is False


@pytest.mark.parametrize(
    "unusable",
    [
        pytest.param('{"actions": [{"intent": "not_an_intent"}]}', id="unknown-intent"),
        pytest.param('{"actions": [{"intent": "greeting", "store_info_type": "x"}]}', id="field"),
        pytest.param('{"actions": [], "commentary": "x"}', id="field-outside-actions"),
        pytest.param('{"actions": [{"intent": "gree', id="cut-off"),
        pytest.param("Não é JSON", id="not-json"),
        pytest.param(None, id="refusal"),
    ],
)
def test_parse_retries_once_on_unusable_output(unusable):
    llm, completions = make_llm(parse_response(unusable), parse_response())
    assert llm.parse_turn(MESSAGES) == PARSED
    assert len(completions.calls) == 2


def test_parse_gives_up_after_the_retry():
    bad = parse_response("Não é JSON")
    llm, completions = make_llm(bad, bad, parse_response())
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


def test_prompt_lists_every_field_the_model_may_write():
    # The model only knows the fields through the prompt, and an unknown one is rejected.
    for field in (*ActionRequest.model_fields, *ParsedTurn.model_fields):
        assert field in UNDERSTAND_SYSTEM_PROMPT, field


def test_prompt_asks_for_json_as_the_provider_requires():
    assert "JSON" in UNDERSTAND_SYSTEM_PROMPT


def test_a_turn_can_leave_out_what_does_not_apply():
    assert ParsedTurn.model_validate_json("{}") == ParsedTurn(actions=[], needs_clarification=False)


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


# Setup problems


def status_error(kind, status, code=None):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
    return kind("refused", response=response, body={"code": code} if code else None)


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(status_error(openai.AuthenticationError, 401), id="bad-key"),
        pytest.param(status_error(openai.NotFoundError, 404), id="unknown-model"),
        pytest.param(status_error(openai.PermissionDeniedError, 403), id="no-access"),
        pytest.param(status_error(openai.BadRequestError, 400), id="rejected-setting"),
        pytest.param(
            status_error(openai.RateLimitError, 429, code="insufficient_quota"), id="no-credit"
        ),
    ],
)
def test_setup_problems_are_told_apart(error):
    # With a key that could not use the model, the person running it was told to try again.
    for call in ("parse_turn", "compose_reply"):
        llm, _ = make_llm(error)
        with pytest.raises(LLMSetupError):
            getattr(llm, call)(MESSAGES)


@pytest.mark.parametrize(
    "error",
    [
        api_error(),
        status_error(openai.RateLimitError, 429),
        status_error(openai.InternalServerError, 500),
    ],
    ids=["connection", "rate-limit", "server"],
)
def test_passing_problems_are_not_setup_problems(error):
    llm, _ = make_llm(error)
    with pytest.raises(LLMUnavailableError) as caught:
        llm.parse_turn(MESSAGES)
    assert not isinstance(caught.value, LLMSetupError)


def test_provider_failure_logs_its_type_and_nothing_from_the_provider(caplog):
    llm, _ = make_llm(status_error(openai.AuthenticationError, 401))
    with caplog.at_level("ERROR", logger="emporio_ai.events"), pytest.raises(LLMSetupError):
        llm.parse_turn(MESSAGES)
    assert "AuthenticationError" in caplog.text
    assert "refused" not in caplog.text
