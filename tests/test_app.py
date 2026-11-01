"""The Streamlit app and the CLI, with a fake service: no network, no API key and no cost."""

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

import main as cli
from emporio_ai.config import PROJECT_ROOT
from emporio_ai.domain.errors import DatabaseNotFoundError, DatasetError

APP_PATH = str(PROJECT_ROOT / "app.py")


class FakeService:
    def __init__(self, reply="Resposta do assistente."):
        self.reply = reply
        self.calls = []
        self.resets = []

    def chat(self, session_id, message):
        self.calls.append((session_id, message))
        return self.reply

    def reset(self, session_id):
        self.resets.append(session_id)


@pytest.fixture(autouse=True)
def clear_cached_service():
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


@pytest.fixture
def start_app(monkeypatch):
    def start(factory):
        monkeypatch.setattr("emporio_ai.agent.service.create_service", factory)
        return AppTest.from_file(APP_PATH, default_timeout=30).run()

    return start


def send(app, message):
    app.chat_input[0].set_value(message)
    return app.run()


def bubbles(app):
    return [(m.name, m.markdown[0].value) for m in app.chat_message]


def page_text(app):
    return " ".join(
        element.value
        for group in (app.markdown, app.warning, app.caption, app.error, app.code)
        for element in group
    )


# Normal use


def test_app_starts_without_errors(start_app):
    app = start_app(FakeService)
    assert not app.exception
    assert not app.error
    assert app.title[0].value == "🎸 Empório da Música"
    assert len(app.chat_input) == 1


def test_demo_notice_is_visible(start_app):
    app = start_app(FakeService)
    warning = app.sidebar.warning[0].value
    assert "Demonstração" in warning
    assert "fictícios" in warning
    assert "tempo real" in warning


def test_says_order_check_is_not_real_authentication(start_app):
    app = start_app(FakeService)
    assert "não é uma autenticação real" in app.sidebar.caption[0].value


def test_message_gets_a_reply(start_app):
    service = FakeService()
    app = send(start_app(lambda: service), "Oi")
    assert not app.exception
    assert bubbles(app) == [("user", "Oi"), ("assistant", "Resposta do assistente.")]
    assert service.calls[0][1] == "Oi"


def test_history_is_kept_and_uses_one_session(start_app):
    service = FakeService()
    app = start_app(lambda: service)
    app = send(app, "Primeira")
    app = send(app, "Segunda")
    assert [text for _, text in bubbles(app)] == [
        "Primeira",
        "Resposta do assistente.",
        "Segunda",
        "Resposta do assistente.",
    ]
    assert service.calls[0][0] == service.calls[1][0]


def test_rerun_does_not_call_the_service_again(start_app):
    service = FakeService()
    app = send(start_app(lambda: service), "Oi")
    app.run()
    assert len(service.calls) == 1
    assert len(app.chat_message) == 2


def test_two_visitors_have_different_sessions(start_app):
    service = FakeService()
    send(start_app(lambda: service), "Oi")
    send(AppTest.from_file(APP_PATH, default_timeout=30).run(), "Olá")
    assert len({session for session, _ in service.calls}) == 2


def test_amounts_are_escaped_so_they_are_not_read_as_a_formula(start_app):
    reply = "Custa R$ 2.199,00, ou R$ 2.089,05 no PIX."
    app = send(start_app(lambda: FakeService(reply)), "Violões até R$ 1.000?")
    assert bubbles(app) == [
        ("user", "Violões até R\\$ 1.000?"),
        ("assistant", "Custa R\\$ 2.199,00, ou R\\$ 2.089,05 no PIX."),
    ]


def test_amounts_stay_escaped_in_the_history(start_app):
    app = send(start_app(lambda: FakeService("R$ 10,00 e R$ 20,00")), "Oi")
    app = app.run()
    assert bubbles(app)[1][1] == "R\\$ 10,00 e R\\$ 20,00"


def test_service_receives_the_original_text_without_escaping(start_app):
    service = FakeService()
    send(start_app(lambda: service), "Até R$ 1.000")
    assert service.calls[0][1] == "Até R$ 1.000"


def test_example_questions_are_escaped(start_app):
    app = start_app(FakeService)
    examples = " ".join(element.value for element in app.sidebar.markdown)
    assert "R\\$ 1.000" in examples


# Clearing the conversation


def test_clear_empties_the_chat_and_starts_a_new_session(start_app):
    service = FakeService()
    app = send(start_app(lambda: service), "Oi")
    first_session = service.calls[0][0]

    app.button(key="clear_conversation").click()
    app = app.run()
    assert not app.exception
    assert len(app.chat_message) == 0
    assert service.resets == [first_session]

    app = send(app, "De novo")
    assert service.calls[1][0] != first_session


def test_clear_without_any_message_is_harmless(start_app):
    app = start_app(FakeService)
    app.button(key="clear_conversation").click()
    assert not app.run().exception


# Setup problems are explained, without a stack trace


def test_missing_api_key_is_explained(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    app = AppTest.from_file(APP_PATH, default_timeout=30).run()
    assert not app.exception
    assert "OPENAI_API_KEY" in app.error[0].value
    assert ".env" in app.code[0].value
    assert len(app.chat_input) == 0


def test_app_recovers_once_the_problem_is_fixed(start_app, monkeypatch):
    def missing():
        raise DatabaseNotFoundError("not ready")

    app = start_app(missing)
    assert app.error

    monkeypatch.setattr("emporio_ai.agent.service.create_service", FakeService)
    app = app.run()
    assert not app.error
    assert len(app.chat_input) == 1


@pytest.mark.parametrize(
    ("error", "expected_command"),
    [
        (DatabaseNotFoundError("not ready"), "scripts/seed_db.py"),
        (DatasetError("orders.csv: file not found"), "scripts/inspect_data.py"),
    ],
)
def test_data_problems_are_explained(start_app, error, expected_command):
    def broken():
        raise error

    app = start_app(broken)
    assert not app.exception
    assert app.error
    assert expected_command in app.code[0].value
    assert len(app.chat_input) == 0


def test_key_is_never_shown(start_app, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    app = send(start_app(FakeService), "Oi")
    assert "not-a-real-key" not in page_text(app)


# Command line


def test_cli_chats_until_the_exit_command():
    service = FakeService()
    answers = iter(["Oi", "SAIR", "Nunca lida"])
    output = []
    cli.run_chat(service, read=lambda _: next(answers), write=output.append)
    assert service.calls == [(cli.SESSION_ID, "Oi")]
    assert output[-1] == "Assistente: Resposta do assistente."


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
def test_cli_ends_cleanly_when_input_closes(error):
    def closed(_):
        raise error

    cli.run_chat(FakeService(), read=closed, write=lambda _: None)


def test_cli_explains_a_missing_key(monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert cli.main() == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().out
