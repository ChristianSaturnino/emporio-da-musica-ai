"""Structured events: a turn can be followed, and nothing personal is written."""

import json
import logging
from types import SimpleNamespace

import pytest

from emporio_ai.agent.llm import OpenAILLM
from emporio_ai.domain.errors import LLMOutputError, LLMUnavailableError
from emporio_ai.domain.schemas import StoreTopic
from emporio_ai.observability import events
from tests.helpers import FakeLLM, order, search, store, turn

SESSION = "visitor-with-a-readable-id"


@pytest.fixture
def logged(caplog):
    def read():
        return [json.loads(record.getMessage()) for record in caplog.records]

    with caplog.at_level(logging.INFO, logger=events.logger.name):
        yield read


def names(entries):
    return [entry["event"] for entry in entries]


def last(entries, name):
    return [entry for entry in entries if entry["event"] == name][-1]


def test_successful_turn_produces_the_planned_events_in_order(make_service, logged):
    make_service(FakeLLM(turn(store(StoreTopic.HOURS)))).chat(SESSION, "Abre domingo?")
    assert names(logged()) == ["turn_started", "intent_parsed", "tool_executed", "turn_completed"]


def test_events_of_a_turn_share_its_ids(make_service, logged):
    make_service(FakeLLM(turn(store(StoreTopic.HOURS)))).chat(SESSION, "Abre domingo?")
    entries = logged()
    assert len({entry["turn_id"] for entry in entries}) == 1
    assert len({entry["session_id"] for entry in entries}) == 1


def test_each_turn_has_its_own_id_in_the_same_session(make_service, logged):
    service = make_service(FakeLLM(turn(store(StoreTopic.HOURS)), turn(store(StoreTopic.HOURS))))
    service.chat(SESSION, "Abre domingo?")
    service.chat(SESSION, "E sábado?")
    entries = logged()
    assert len({entry["turn_id"] for entry in entries}) == 2
    assert len({entry["session_id"] for entry in entries}) == 1


def test_session_id_is_opaque(make_service, caplog, logged):
    make_service(FakeLLM(turn(store(StoreTopic.HOURS)))).chat(SESSION, "Abre domingo?")
    assert SESSION not in caplog.text


def test_events_carry_intent_tool_result_and_latency(make_service, logged):
    make_service(FakeLLM(turn(search(category="violões")))).chat(SESSION, "Violões?")
    entries = logged()
    parsed = last(entries, "intent_parsed")
    assert parsed["intents"] == ["product_search"]
    assert parsed["action_count"] == 1
    assert parsed["prompt_version"]
    executed = last(entries, "tool_executed")
    assert executed["intent"] == "product_search"
    assert executed["tool"] == "search"
    assert executed["result"] == "success"
    assert executed["latency_ms"] >= 0
    completed = last(entries, "turn_completed")
    assert completed["result"] == "success"
    assert completed["latency_ms"] >= 0


@pytest.mark.parametrize(
    ("parsed", "result"),
    [
        (search(product_query="pedal"), "blocked"),
        (search(product_query="theremin"), "no_result"),
        (order(order_id=8), "needs_input"),
    ],
)
def test_tool_result_says_what_happened(make_service, logged, parsed, result):
    make_service(FakeLLM(turn(parsed))).chat(SESSION, "Mensagem")
    assert last(logged(), "tool_executed")["result"] == result


@pytest.mark.parametrize(
    ("llm_kwargs", "turns", "result"),
    [
        ({}, [LLMUnavailableError("x")], "provider_error"),
        ({}, [LLMOutputError("x")], "validation_error"),
        ({"reply": LLMUnavailableError("x")}, [turn(store(StoreTopic.HOURS))], "provider_error"),
        ({"reply": LLMOutputError("x")}, [turn(store(StoreTopic.HOURS))], "validation_error"),
        ({"reply": "Custa R$ 1,00."}, [turn(store(StoreTopic.HOURS))], "reply_rejected"),
    ],
)
def test_failed_turn_is_reported_with_its_reason(make_service, logged, llm_kwargs, turns, result):
    make_service(FakeLLM(*turns, **llm_kwargs)).chat(SESSION, "Mensagem")
    entries = logged()
    assert last(entries, "turn_failed")["result"] == result
    assert "turn_completed" not in names(entries)


def test_invalid_action_is_reported_as_a_validation_error(make_service, logged):
    make_service(FakeLLM(turn(order(order_id=-1)))).chat(SESSION, "Pedido")
    assert last(logged(), "turn_failed")["result"] == "validation_error"


def test_rejected_reply_says_what_kind_of_claim_without_the_value(make_service, caplog, logged):
    llm = FakeLLM(turn(store(StoreTopic.HOURS)), reply="Custa R$ 12.345,67.")
    make_service(llm).chat(SESSION, "Mensagem")
    assert last(logged(), "reply_rejected")["unsupported"] == ["amount"]
    assert "12.345" not in caplog.text


def test_tool_error_gives_only_the_error_type(make_service, monkeypatch, caplog, logged):
    from emporio_ai.agent import graph as graph_module

    def broken(*_, **__):
        raise RuntimeError("cliente@example.com")

    monkeypatch.setattr(graph_module, "search_products", broken)
    make_service(FakeLLM(turn(search(category="violões")))).chat(SESSION, "Violões?")
    executed = last(logged(), "tool_executed")
    assert executed["result"] == "tool_error"
    assert executed["error_type"] == "RuntimeError"
    assert "cliente@example.com" not in caplog.text


def test_unexpected_error_gives_only_the_error_type(make_service, monkeypatch, caplog, logged):
    service = make_service(FakeLLM())

    def broken(_):
        raise RuntimeError("cliente@example.com")

    monkeypatch.setattr(service._graph, "invoke", broken)
    service.chat(SESSION, "Oi")
    failed = last(logged(), "turn_failed")
    assert (failed["result"], failed["error_type"]) == ("unexpected_error", "RuntimeError")
    assert "cliente@example.com" not in caplog.text


def test_full_order_flow_writes_nothing_personal(make_service, owner_email, caplog, logged):
    email = owner_email(2)
    llm = FakeLLM(turn(order(order_id=8)), turn(order()))
    service = make_service(llm)
    service.chat(SESSION, "Sou a Ana, onde está meu pedido 8?")
    service.chat(SESSION, f"Meu e-mail é {email}")
    text = caplog.text
    assert logged()
    assert email not in text
    assert "Ana" not in text
    assert "onde está" not in text
    assert "BRJL5544332BR" not in text
    assert "Kala KA-C" not in text


def test_unknown_fields_are_dropped(logged):
    events.log_event("custom", result="ok", message="texto do cliente", email="a@b.com")
    assert logged() == [{"event": "custom", "result": "ok"}]


def test_prompt_text_is_never_logged(make_service, caplog, logged):
    make_service(FakeLLM(turn(store(StoreTopic.ADDRESS)))).chat(SESSION, "Endereço?")
    assert "Rua 14 de Maio" not in caplog.text
    assert "Você é o" not in caplog.text


def test_token_usage_is_logged_for_real_model_calls(logged):
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20)
    content = '{"actions": [{"intent": "greeting"}]}'
    completion = SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )
    completions = SimpleNamespace(create=lambda **_: completion)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    OpenAILLM(api_key="not-a-real-key", model="m", client=client).parse_turn([])
    entry = last(logged(), "llm_call")
    assert (entry["kind"], entry["input_tokens"], entry["output_tokens"]) == ("parse", 100, 20)
    assert entry["latency_ms"] >= 0


def test_configure_logging_can_be_called_repeatedly():
    events.configure_logging()
    events.configure_logging()
    assert len(events.logger.handlers) == 1
    assert events.logger.level == logging.INFO
