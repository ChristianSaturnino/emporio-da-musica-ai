"""The evaluation script: its case files and scoring. No model is called here."""

import re
from decimal import Decimal

import pytest

from emporio_ai.agent.prompts import (
    AMBIGUOUS_PRODUCT_MESSAGE,
    COMPOSE_SYSTEM_PROMPT,
    GREETING_MESSAGE,
    INVALID_OUTPUT_MESSAGE,
    OUT_OF_SCOPE_MESSAGE,
    PROVIDER_ERROR_MESSAGE,
    TOOL_ERROR_MESSAGE,
    UNDERSTAND_SYSTEM_PROMPT,
    UNEXPECTED_ERROR_MESSAGE,
    UNVERIFIED_REPLY_MESSAGE,
    WHICH_OF_THE_LIST_MESSAGE,
    WHICH_PRODUCT_MESSAGE,
)
from emporio_ai.agent.service import ConversationService
from emporio_ai.domain.errors import LLMUnavailableError
from emporio_ai.domain.normalization import normalize_text
from emporio_ai.domain.schemas import IntentType, ParsedTurn
from scripts.run_eval import (
    EXIT_CASES_FAILED,
    EXIT_OK,
    EXIT_STOPPED_BY_COST_LIMIT,
    SET_NAMES,
    CaseResult,
    RecordingLLM,
    _UsageCounter,
    exit_code,
    load_cases,
    run_case,
    run_sets,
    score_case,
    summarize,
)
from tests.helpers import FakeLLM, act, order, price, search, turn


def plain_words(text):
    return " ".join(re.sub(r"[^\w ]", " ", normalize_text(text)).split())


@pytest.mark.parametrize("name", SET_NAMES)
def test_prompts_do_not_contain_the_phrases_they_are_measured_on(name):
    # Copying test phrases into the prompt once inflated a score. Measuring a prompt on its own
    # examples says nothing about new questions.
    prompts_text = plain_words(UNDERSTAND_SYSTEM_PROMPT + " " + COMPOSE_SYSTEM_PROMPT)
    copied = {
        message
        for case in load_cases(name)
        for message in case["messages"]
        if "{" not in message
        and len(plain_words(message).split()) >= 2
        and plain_words(message) in prompts_text
    }
    assert not copied


@pytest.mark.parametrize("name", SET_NAMES)
def test_case_files_are_well_formed(name):
    cases = load_cases(name)
    assert cases
    assert len({case["id"] for case in cases}) == len(cases)
    known_intents = {intent.value for intent in IntentType}
    for case in cases:
        assert case["messages"]
        assert {i for allowed in case.get("intents", []) for i in allowed} <= known_intents
        for number in case.get("checks_by_turn", {}):
            assert 1 <= int(number) <= len(case["messages"])


def test_case_sets_do_not_overlap():
    seen = set()
    for name in SET_NAMES:
        messages = {tuple(plain_words(m) for m in c["messages"]) for c in load_cases(name)}
        assert not seen & messages
        seen |= messages


def test_passing_case():
    case = {
        "id": "x",
        "intents": [["product_search"]],
        "slots": {"category": "viol", "max_price_brl": 1000, "in_stock": True},
        "has": ["Tagima", ["nao existe", "R$"]],
        "lacks": ["Yamaha"],
    }
    parsed = turn(search(category="violões", max_price_brl=Decimal("1000"), in_stock=True))
    result = score_case(case, parsed, "", "O Tagima sai por R$ 429,90.")
    assert result.passed, result.problems


def test_comparison_ignores_case_and_accents():
    case = {"id": "x", "has": ["catalogo"]}
    assert score_case(case, turn(), "", "Fora do CATÁLOGO").passed


@pytest.mark.parametrize(
    ("slots", "parsed"),
    [
        ({"in_stock": True}, turn(search(in_stock=False))),
        ({"in_stock": True}, turn(search())),
        ({"max_price_brl": 1000}, turn(search(max_price_brl=Decimal("900")))),
        ({"order_id": 8}, turn(order(order_id=9))),
        ({"category": "viol"}, turn(search(category="ukulele"))),
    ],
)
def test_wrong_slot_fails_without_crashing(slots, parsed):
    assert not score_case({"id": "x", "slots": slots}, parsed, "", "").passed


def test_wrong_intent_fails():
    case = {"id": "x", "intents": [["product_price"]]}
    assert not score_case(case, turn(search()), "", "").passed


def test_order_of_two_intents_does_not_matter():
    case = {"id": "x", "intents": [["product_price", "product_search"]]}
    assert score_case(case, turn(search(), price(product_query="a")), "", "").passed


def test_padding_is_cleaned_before_scoring_like_in_the_real_flow():
    case = {"id": "x", "intents": [["order_status"]]}
    parsed = turn(order(order_id=8), act(IntentType.OUT_OF_SCOPE))
    assert score_case(case, parsed, "", "").passed


def test_no_structure_fails_a_case_that_expects_an_intent():
    case = {"id": "x", "intents": [["greeting"]]}
    assert not score_case(case, None, "", "").passed


def test_empty_parse_is_scored_as_no_intents():
    case = {"id": "x", "intents": [["out_of_scope"]]}
    parsed = ParsedTurn(actions=[], needs_clarification=True)
    assert not score_case(case, parsed, "", "").passed


def test_sections_are_read_from_the_facts():
    case = {"id": "x", "sections": ["4.1", "8.1"]}
    facts = '<dados>[{"section_id": "4.1"}, {"section_id": "8.1"}]</dados>'
    assert score_case(case, turn(), facts, "").passed
    assert not score_case(case, turn(), '{"section_id": "4.1"}', "").passed


def test_forbidden_text_fails():
    case = {"id": "x", "lacks": ["Paris"]}
    assert not score_case(case, turn(), "", "A capital é Paris").passed


class _Stub:
    def __init__(self, reply):
        self.reply = reply

    def chat(self, _session, _message):
        return self.reply


def test_greeting_answered_without_a_model_call_counts_as_a_greeting():
    recorder = RecordingLLM(inner=None)
    case = {"id": "x", "messages": ["oi"], "intents": [["greeting"]]}
    result = run_case(lambda: _Stub(GREETING_MESSAGE), recorder, case, str)
    assert result.passed, result.problems


def test_what_an_earlier_message_understood_does_not_leak_into_the_score():
    recorder = RecordingLLM(inner=None, parsed=turn(search()))
    case = {"id": "x", "messages": ["oi"], "intents": [["product_search"]]}
    assert not run_case(lambda: _Stub("Texto"), recorder, case, str).passed


class _Script:
    """A conversation whose replies are given in advance."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def chat(self, _session, _message):
        return self.replies.pop(0)


def conversation(*replies):
    return lambda: _Script(*replies)


def recorder():
    return RecordingLLM(inner=None)


def test_facts_are_kept_when_a_reply_is_rewritten():
    # After a rewrite, the last message is the reminder, so correct replies failed their sections.
    class Inner:
        def compose_reply(self, _messages):
            return "Resposta"

    recording = RecordingLLM(inner=Inner())
    recording.compose_reply([{"role": "user", "content": '{"section_id": "3.1"}'}])
    recording.compose_reply([{"role": "user", "content": "Reescreva"}])
    assert '"section_id": "3.1"' in recording.facts


def test_failure_in_the_middle_of_a_conversation_is_not_hidden():
    # Only the last message used to be scored.
    case = {
        "id": "x",
        "messages": ["um", "dois"],
        "checks_by_turn": {"1": {"has": ["Telecaster"]}},
        "has": ["certo"],
    }
    result = run_case(conversation("errado", "certo"), recorder(), case, str)
    assert not result.passed
    assert result.problems == ["message 1: reply lacks ['Telecaster']"]
    assert result.checks["reply"] is False
    assert result.replies == ["errado", "certo"]


def test_every_turn_passes_when_each_is_right():
    case = {
        "id": "x",
        "messages": ["um", "dois", "três"],
        "checks_by_turn": {"1": {"has": ["a"]}, "2": {"lacks": ["z"]}},
        "has": ["c"],
    }
    assert run_case(conversation("a", "b", "c"), recorder(), case, str).passed


def test_checks_for_the_last_turn_can_also_be_given_by_number():
    case = {"id": "x", "messages": ["um"], "checks_by_turn": {"1": {"has": ["ausente"]}}}
    assert not run_case(conversation("resposta"), recorder(), case, str).passed


def test_a_case_with_no_turn_checks_works_as_before():
    case = {"id": "x", "messages": ["um", "dois"], "has": ["fim"]}
    assert run_case(conversation("qualquer", "fim"), recorder(), case, str).passed


# A turn that broke must not pass because it lacks what a case forbids


FX_03_FIRST_REPLIES = ("Takamine GD20 sai por R$ 2.199,00.", "Temos a Fender Telecaster.")


def fx_03():
    return next(case for case in load_cases("fixes") if case["id"] == "fx-03")


@pytest.mark.parametrize(
    "last_reply",
    [
        PROVIDER_ERROR_MESSAGE,
        INVALID_OUTPUT_MESSAGE,
        TOOL_ERROR_MESSAGE,
        UNEXPECTED_ERROR_MESSAGE,
        "",
        "   ",
        "Abrimos de segunda a sexta, das 9h às 18h.",
        "Não encontrei esse produto no nosso catálogo.",
        "Qual é o número do seu pedido?",
    ],
)
def test_fx_03_rejects_what_does_not_ask_which_guitar(last_reply):
    result = run_case(conversation(*FX_03_FIRST_REPLIES, last_reply), recorder(), fx_03(), str)
    assert not result.passed
    assert result.checks["reply"] is False


@pytest.mark.parametrize(
    "last_reply",
    [
        f"{WHICH_OF_THE_LIST_MESSAGE}\n- Fender Telecaster Player Series",
        WHICH_PRODUCT_MESSAGE,
        AMBIGUOUS_PRODUCT_MESSAGE,
        "Qual guitarra você quer saber o preço?",
        "Claro! De qual delas você está falando?",
        "Pode me dizer qual das guitarras te interessou?",
        "Me passa o nome da guitarra, por favor.",
    ],
)
def test_fx_03_accepts_any_way_of_asking_which_guitar(last_reply):
    result = run_case(conversation(*FX_03_FIRST_REPLIES, last_reply), recorder(), fx_03(), str)
    assert result.passed, result.problems


def test_fx_03_still_rejects_the_old_price():
    last_reply = "De qual produto? O Takamine sai por R$ 2.199,00."
    assert not run_case(
        conversation(*FX_03_FIRST_REPLIES, last_reply), recorder(), fx_03(), str
    ).passed


def test_score_case_of_fx_03_rejects_the_provider_error_by_itself():
    assert not score_case(fx_03(), None, "", PROVIDER_ERROR_MESSAGE).passed


@pytest.mark.parametrize(
    "broken",
    [
        PROVIDER_ERROR_MESSAGE,
        f"O horário é das 9h às 18h.\n\n{PROVIDER_ERROR_MESSAGE}",
        INVALID_OUTPUT_MESSAGE,
        TOOL_ERROR_MESSAGE,
        UNEXPECTED_ERROR_MESSAGE,
        "",
    ],
)
@pytest.mark.parametrize("failing_turn", [0, 1], ids=["middle", "last"])
def test_technical_failure_fails_a_case_with_only_negative_checks(broken, failing_turn):
    replies = ["Tudo certo.", "Tudo certo."]
    replies[failing_turn] = broken
    case = {"id": "x", "messages": ["um", "dois"], "lacks": ["2.199,00"]}
    result = run_case(conversation(*replies), recorder(), case, str)
    assert not result.passed
    assert result.failed
    assert result.checks["reply"] is False
    assert result.problems == [
        f"message {failing_turn + 1}: technical failure instead of an answer"
    ]


def test_a_declined_reply_is_not_a_technical_failure():
    # Keeping an unconfirmed reply from the customer is the reply check working.
    case = {"id": "x", "messages": ["um"]}
    for reply in (UNVERIFIED_REPLY_MESSAGE, OUT_OF_SCOPE_MESSAGE):
        assert run_case(conversation(reply), recorder(), case, str).passed


def test_technical_failure_is_seen_through_the_real_service(repo, policy_index):
    # The real graph, turning a provider error from a fake model into a reply for the customer.
    llm = FakeLLM(
        turn(price(product_query="Takamine GD20")),
        turn(search(category="guitarras")),
        LLMUnavailableError("indisponível"),
    )
    recording = RecordingLLM(inner=llm)
    result = run_case(
        lambda: ConversationService(recording, repo, policy_index), recording, fx_03(), str
    )
    assert result.replies[-1] == PROVIDER_ERROR_MESSAGE
    assert not result.passed


def test_fx_03_passes_through_the_real_service_with_a_correct_understanding(repo, policy_index):
    llm = FakeLLM(
        turn(price(product_query="Takamine GD20")),
        turn(search(category="guitarras")),
        turn(price(refers_to_previous_product=True)),
        reply="Temos a Fender Telecaster Player Series.",
    )
    recording = RecordingLLM(inner=llm)
    result = run_case(
        lambda: ConversationService(recording, repo, policy_index), recording, fx_03(), str
    )
    assert result.passed, result.problems


def test_conversation_cut_by_the_cost_limit_is_interrupted_and_not_scored():
    answers = iter([True, False])
    case = {"id": "x", "messages": ["um", "dois"], "has": ["nunca chega"]}
    result = run_case(conversation("a", "b"), recorder(), case, str, lambda: next(answers))
    assert result.interrupted
    assert not result.passed
    assert not result.failed
    assert result.replies == ["a"]


def test_failure_found_before_the_interruption_still_counts():
    answers = iter([True, False])
    case = {"id": "x", "messages": ["um", "dois"], "checks_by_turn": {"1": {"has": ["b"]}}}
    result = run_case(conversation("a", "b"), recorder(), case, str, lambda: next(answers))
    assert result.interrupted
    assert result.failed


@pytest.mark.parametrize(
    ("results", "stopped", "expected"),
    [
        ([CaseResult("a")], False, EXIT_OK),
        ([CaseResult("a", problems=["x"])], False, EXIT_CASES_FAILED),
        ([CaseResult("a")], True, EXIT_STOPPED_BY_COST_LIMIT),
        ([CaseResult("a", interrupted=True)], False, EXIT_STOPPED_BY_COST_LIMIT),
        ([CaseResult("a", problems=["x"])], True, EXIT_CASES_FAILED),
    ],
    ids=["all-passed", "failed", "stopped", "cut-short", "failed-and-stopped"],
)
def test_exit_code_tells_the_outcome_apart(results, stopped, expected):
    assert exit_code(results, stopped) == expected


def test_run_stops_at_the_limit_and_counts_what_did_not_run():
    ran = []

    def run_one(case):
        ran.append(case["id"])
        return CaseResult(case["id"])

    budget = iter([True, True, False])
    results, not_run = run_sets(["calendar"], run_one, lambda: next(budget, False))
    assert ran == ["t-01", "t-02"]
    assert len(results["calendar"]) == 2
    assert not_run == len(load_cases("calendar")) - 2


def test_run_can_be_limited_to_some_cases():
    results, not_run = run_sets(
        ["calendar"], lambda case: CaseResult(case["id"]), lambda: True, only="t-01"
    )
    assert [result.case_id for result in results["calendar"]] == ["t-01"]
    assert not_run == 0


def counted(model, input_tokens=1_000_000, output_tokens=1_000_000):
    usage = _UsageCounter(model)
    usage.input_tokens, usage.output_tokens = input_tokens, output_tokens
    return usage


def test_cost_is_estimated_only_for_a_model_with_a_known_price():
    assert counted("gpt-6-luna").estimated_cost == Decimal("0.60")
    assert "Estimated cost" in counted("gpt-6-luna").cost_report()


def test_unknown_model_gets_no_guessed_price():
    # Unknown models used to be priced as gpt-4o-mini without saying so.
    usage = counted("modelo-desconhecido")
    assert usage.estimated_cost is None
    assert "not estimated" in usage.cost_report()
    assert not usage.reached(Decimal("0"))


def test_limit_is_reached_only_at_the_estimate():
    assert counted("gpt-6-luna").reached(Decimal("0.60"))
    assert not counted("gpt-6-luna").reached(Decimal("0.61"))


def test_summary_counts_each_check():
    good = score_case({"id": "a"}, turn(), "", "")
    bad = score_case({"id": "b", "has": ["ausente"]}, turn(), "", "")
    summary = summarize([good, bad])
    assert (summary["cases"], summary["passed"], summary["reply"]) == (2, 1, 1)
