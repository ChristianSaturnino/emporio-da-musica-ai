"""The evaluation script: its case files and scoring. No model is called here."""

import re
from decimal import Decimal

import pytest

from emporio_ai.agent.prompts import COMPOSE_SYSTEM_PROMPT, UNDERSTAND_SYSTEM_PROMPT
from emporio_ai.domain.normalization import normalize_text
from emporio_ai.domain.schemas import IntentType, ParsedTurn
from scripts.run_eval import SET_NAMES, load_cases, score_case, summarize
from tests.helpers import act, order, price, search, turn


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


def test_summary_counts_each_check():
    good = score_case({"id": "a"}, turn(), "", "")
    bad = score_case({"id": "b", "has": ["ausente"]}, turn(), "", "")
    summary = summarize([good, bad])
    assert (summary["cases"], summary["passed"], summary["reply"]) == (2, 1, 1)
