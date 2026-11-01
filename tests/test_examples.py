"""The transcript recorder, with a fake model. The recorded files themselves are checked by hand."""

from datetime import date

from emporio_ai.domain.normalization import normalize_text
from emporio_ai.domain.schemas import IntentType
from scripts.record_examples import (
    CONVERSATIONS,
    ORDER_OWNER_EMAIL,
    ORDER_OWNER_LABEL,
    render_markdown,
    run_conversation,
    write_examples,
)
from tests.helpers import FakeLLM, act, turn


def greeting_llm(count):
    return FakeLLM(*[turn(act(IntentType.GREETING)) for _ in range(count)])


def message_count():
    return sum(len(conversation.messages) for conversation in CONVERSATIONS)


def test_number_of_conversations_is_what_the_challenge_asks_for():
    assert 3 <= len(CONVERSATIONS) <= 5
    assert len({conversation.slug for conversation in CONVERSATIONS}) == len(CONVERSATIONS)


def test_conversations_cover_the_scenarios_suggested_by_the_challenge():
    all_messages = normalize_text(
        " ".join(message for c in CONVERSATIONS for message in c.messages)
    )
    assert "violoes disponiveis custando ate r$1000" in all_messages
    assert "endereco da loja" in all_messages
    assert "quanto custa o takamine gd20" in all_messages
    assert "me arrependi da minha compra, posso devolver meu pedido" in all_messages


def test_the_real_email_is_not_written(make_service, owner_email, tmp_path):
    email = owner_email(2)
    llm = greeting_llm(message_count())
    service = make_service(llm)
    paths = write_examples(service, CONVERSATIONS, tmp_path, email, "test-model", date(2026, 11, 1))

    assert len(paths) == len(CONVERSATIONS)
    written = "".join(path.read_text(encoding="utf-8") for path in paths)
    assert email not in written
    assert ORDER_OWNER_EMAIL not in written
    assert ORDER_OWNER_LABEL in written


def test_the_service_receives_the_real_email_and_the_transcript_shows_the_label():
    class RecordingService:
        received = []

        def chat(self, session_id, message):
            self.received.append(message)
            return "ok"

    conversation = next(c for c in CONVERSATIONS if c.slug == "04_order_verification")
    exchanges = run_conversation(RecordingService(), conversation, "owner@example.com")
    assert RecordingService.received[2] == "owner@example.com"
    assert exchanges[2][0] == ORDER_OWNER_LABEL


def test_replies_are_written_exactly_as_given():
    conversation = CONVERSATIONS[0]
    reply = "Linha com **negrito** e R$ 1.000,00\n\n- item"
    text = render_markdown(conversation, [("Oi", reply)], "test-model", date(2026, 11, 1))
    assert f"**Assistente:** {reply}\n" in text
    assert "**Cliente:** Oi" in text


def test_transcript_says_how_it_was_recorded():
    text = render_markdown(CONVERSATIONS[0], [], "test-model", date(2026, 11, 1))
    assert "test-model" in text
    assert "2026-11-01" in text
    assert "not edited" in text
