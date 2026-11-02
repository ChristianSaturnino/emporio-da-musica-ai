"""Measure the real agent on fixed conversations. This calls the OpenAI API, which costs a little.

The dev set is for finding problems and may guide changes. The held-out set is only for measuring,
so its score is closer to what new questions would get. The cases came from the same process as the
code, so this is a regression check and not a benchmark.

A case's checks apply to its last message. "checks_by_turn" adds checks for any other message, by
its number starting at 1. Most cases do not use it, so most middle messages have no checks of their
own. A technical failure or an empty reply at any message fails the case, with or without them.

Exit codes: 0 when everything ran and passed, 1 when a case failed, 3 when no case failed but the
cost limit stopped the run, and 4 when it could not start. Argparse uses 2 for bad arguments.
"""

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any

from emporio_ai.agent import prompts
from emporio_ai.agent.graph import _clean_actions
from emporio_ai.agent.llm import OpenAILLM
from emporio_ai.agent.service import ConversationService
from emporio_ai.config import PROJECT_ROOT, get_settings
from emporio_ai.data.policy_index import build_policy_index
from emporio_ai.data.repository import Repository, connect_read_only
from emporio_ai.domain.normalization import normalize_text
from emporio_ai.domain.schemas import ActionRequest, IntentType, ParsedTurn
from emporio_ai.observability import events

EVALS_DIR = PROJECT_ROOT / "evals"
SET_NAMES = (
    "dev",
    "heldout",
    "final",
    "shopper",
    "fresh",
    "clean",
    "attack",
    "attack_fresh",
    "attack_final",
    "calendar",
    "review",
    "review_final",
    "review_last",
    "closing",
    "service",
    "fixes",
)
EXIT_OK = 0
EXIT_CASES_FAILED = 1
EXIT_STOPPED_BY_COST_LIMIT = 3
EXIT_COULD_NOT_START = 4
# Input and output list prices per 1M tokens, from the OpenAI pricing pages when these were written.
# Costs are estimates from these prices and the tokens the API reports, ignoring cached-input
# discounts. A model that is not listed here has no estimate, rather than a guessed one.
PRICES_PER_MILLION = {
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-6-luna": (Decimal("0.10"), Decimal("0.50")),
}
# What a turn replies when something broke. A reply check does not count here, as it is not a fault.
_TECHNICAL_FAILURE_MESSAGES = (
    prompts.PROVIDER_ERROR_MESSAGE,
    prompts.INVALID_OUTPUT_MESSAGE,
    prompts.TOOL_ERROR_MESSAGE,
    prompts.UNEXPECTED_ERROR_MESSAGE,
)
_EMAIL_PLACEHOLDER = re.compile(r"\{email_(\d+)\}")
_SECTION_ID = re.compile(r'"section_id": "([\d.]+)"')


@dataclass
class RecordingLLM:
    """Passes calls on to the real model, keeping what the last turn understood and was told."""

    inner: Any
    parsed: ParsedTurn | None = None
    facts: str = ""

    def parse_turn(self, messages: list[dict[str, str]]) -> ParsedTurn:
        self.parsed = None
        self.facts = ""
        self.parsed = self.inner.parse_turn(messages)
        return self.parsed

    def compose_reply(self, messages: list[dict[str, str]]) -> str:
        # On a rewrite, the last message is the reminder, and the facts are in the first attempt.
        self.facts = self.facts or messages[-1]["content"]
        return self.inner.compose_reply(messages)


@dataclass
class CaseResult:
    case_id: str
    problems: list[str] = field(default_factory=list)
    reply: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    replies: list[str] = field(default_factory=list)
    # Stopped by the cost limit before its last message, so it neither passed nor failed.
    interrupted: bool = False

    @property
    def passed(self) -> bool:
        return not self.problems and not self.interrupted

    @property
    def failed(self) -> bool:
        return bool(self.problems)


def _contains(text: str, expected: str) -> bool:
    return normalize_text(expected) in normalize_text(text)


def _slot_matches(actions: Sequence[Any], slot: str, expected: Any) -> bool:
    for action in actions:
        value = getattr(action, slot, None)
        if value is None:
            continue
        if isinstance(expected, str):
            if _contains(str(value), expected):
                return True
        elif isinstance(expected, bool):
            if value is expected:
                return True
        elif Decimal(str(value)) == Decimal(str(expected)):
            return True
    return False


def is_technical_failure(reply: str) -> bool:
    """Tell a turn that broke from one that answered, as only checking what a reply lacks cannot.

    chat() only returns text, and these messages are the text it gives for a failure.
    """
    return not reply.strip() or any(text in reply for text in _TECHNICAL_FAILURE_MESSAGES)


def score_case(
    case: dict[str, Any], parsed: ParsedTurn | None, facts: str, reply: str
) -> CaseResult:
    result = CaseResult(case_id=case["id"], reply=reply)
    actions = _clean_actions(parsed.actions) if parsed else []
    result.actions = [a.model_dump(exclude_none=True, mode="json") for a in actions]

    intents = sorted(action.intent.value for action in actions)
    intent_ok = any(sorted(allowed) == intents for allowed in case.get("intents", [intents]))
    result.checks["intent"] = intent_ok
    if not intent_ok:
        result.problems.append(f"intents {intents}, expected {case['intents']}")

    slots_ok = True
    for slot, expected in case.get("slots", {}).items():
        if not _slot_matches(actions, slot, expected):
            slots_ok = False
            result.problems.append(f"slot {slot}: expected {expected}")
    result.checks["slots"] = slots_ok

    found_sections = set(_SECTION_ID.findall(facts))
    missing = set(case.get("sections", [])) - found_sections
    result.checks["sections"] = not missing
    if missing:
        result.problems.append(f"sections {sorted(found_sections)}, missing {sorted(missing)}")

    reply_ok = True
    for expected in case.get("has", []):
        options = expected if isinstance(expected, list) else [expected]
        if not any(_contains(reply, option) for option in options):
            reply_ok = False
            result.problems.append(f"reply lacks {options}")
    for forbidden in case.get("lacks", []):
        if _contains(reply, forbidden):
            reply_ok = False
            result.problems.append(f"reply has forbidden {forbidden!r}")
    result.checks["reply"] = reply_ok
    return result


def run_case(
    make_service: Callable[[], ConversationService],
    recorder: RecordingLLM,
    case: dict[str, Any],
    fill_emails: Callable[[str], str],
    may_continue: Callable[[], bool] = lambda: True,
) -> CaseResult:
    service = make_service()
    checks_by_turn = case.get("checks_by_turn", {})
    messages = case["messages"]
    replies: list[str] = []
    problems: list[str] = []
    checks: dict[str, bool] = {}
    result = CaseResult(case_id=case["id"])
    for number, message in enumerate(messages, start=1):
        if not may_continue():
            return CaseResult(
                case_id=case["id"],
                problems=problems,
                reply=replies[-1] if replies else "",
                replies=replies,
                interrupted=True,
            )
        # Some turns call no model, so what is left here could belong to an earlier message.
        recorder.parsed, recorder.facts = None, ""
        reply = service.chat(case["id"], fill_emails(message))
        replies.append(reply)
        if is_technical_failure(reply):
            problems.append(f"message {number}: technical failure instead of an answer")
            checks["reply"] = False
        parsed = recorder.parsed
        if parsed is None and reply in (prompts.GREETING_MESSAGE, prompts.FAREWELL_MESSAGE):
            parsed = ParsedTurn(actions=[ActionRequest(intent=IntentType.GREETING)])
        if str(number) in checks_by_turn:
            turn_case = {"id": case["id"], **checks_by_turn[str(number)]}
            turn_result = score_case(turn_case, parsed, recorder.facts, reply)
            problems += [f"message {number}: {problem}" for problem in turn_result.problems]
            for check, ok in turn_result.checks.items():
                checks[check] = checks.get(check, True) and ok
        if number == len(messages):
            result = score_case(case, parsed, recorder.facts, reply)
    result.problems = problems + result.problems
    result.checks = {
        check: ok and checks.get(check, True) for check, ok in result.checks.items()
    }
    result.replies = replies
    return result


class _UsageCounter:
    def __init__(self, model: str) -> None:
        self.prices = PRICES_PER_MILLION.get(model)
        self.input_tokens = 0
        self.output_tokens = 0
        self.latencies: dict[str, list[int]] = {"parse": [], "compose": [], "turn": []}

    def filter(self, record: Any) -> bool:
        entry = json.loads(record.getMessage())
        if entry["event"] == "llm_call":
            self.input_tokens += entry["input_tokens"]
            self.output_tokens += entry["output_tokens"]
            self.latencies[entry["kind"]].append(entry["latency_ms"])
        elif entry["event"] == "turn_completed":
            self.latencies["turn"].append(entry["latency_ms"])
        return False

    def latency_report(self) -> str:
        parts = []
        for kind, values in self.latencies.items():
            if values:
                ordered = sorted(values)
                p90 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.9))]
                parts.append(f"{kind} median {median(ordered):.0f} ms, p90 {p90} ms")
        return "; ".join(parts)

    @property
    def estimated_cost(self) -> Decimal | None:
        if self.prices is None:
            return None
        input_price, output_price = self.prices
        return (self.input_tokens * input_price + self.output_tokens * output_price) / Decimal(
            1_000_000
        )

    def reached(self, limit: Decimal) -> bool:
        cost = self.estimated_cost
        return cost is not None and cost >= limit

    def cost_report(self) -> str:
        tokens = f"Tokens: input {self.input_tokens}, output {self.output_tokens}."
        cost = self.estimated_cost
        if cost is None:
            return f"{tokens} Cost not estimated: no known price for this model."
        return f"{tokens} Estimated cost: US$ {cost:.4f}, at list prices, with no cache discounts."


def _email_filler(db_path: Path) -> Callable[[str], str]:
    with closing(connect_read_only(db_path)) as connection:
        owners = {
            row["order_id"]: row["email"]
            for row in connection.execute(
                "SELECT o.order_id, c.email FROM orders AS o "
                "JOIN customers AS c ON c.customer_id = o.customer_id"
            )
        }
    return lambda text: _EMAIL_PLACEHOLDER.sub(lambda m: owners[int(m.group(1))], text)


def load_cases(name: str) -> list[dict[str, Any]]:
    return json.loads((EVALS_DIR / f"cases_{name}.json").read_text(encoding="utf-8"))


def summarize(results: Sequence[CaseResult]) -> dict[str, Any]:
    total = len(results)
    return {
        "cases": total,
        "passed": sum(r.passed for r in results),
        "failed": sum(r.failed for r in results),
        "interrupted": sum(r.interrupted and not r.failed for r in results),
        **{
            check: sum(r.checks.get(check, False) for r in results)
            for check in ("intent", "slots", "sections", "reply")
        },
    }


def exit_code(results: Sequence[CaseResult], stopped_by_cost_limit: bool) -> int:
    if any(result.failed for result in results):
        return EXIT_CASES_FAILED
    if stopped_by_cost_limit or any(result.interrupted for result in results):
        return EXIT_STOPPED_BY_COST_LIMIT
    return EXIT_OK


def run_sets(
    names: Sequence[str],
    run_one: Callable[[dict[str, Any]], CaseResult],
    may_continue: Callable[[], bool],
    only: str | None = None,
) -> tuple[dict[str, list[CaseResult]], int]:
    """Run the sets until the end or the cost limit, returning the results and the cases not run."""
    results: dict[str, list[CaseResult]] = {name: [] for name in names}
    not_run = 0
    for name in names:
        for case in load_cases(name):
            if only and not case["id"].startswith(only):
                continue
            if not_run or not may_continue():
                not_run += 1
                continue
            results[name].append(run_one(case))
    return results, not_run


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sets", nargs="+", choices=SET_NAMES)
    parser.add_argument(
        "--max-cost",
        type=Decimal,
        default=Decimal("0.10"),
        help=(
            "stop starting new messages once the estimated cost reaches this. It is not a hard "
            "ceiling: it is checked before each message, so the message in progress can exceed it"
        ),
    )
    parser.add_argument(
        "--allow-unknown-price",
        action="store_true",
        help="run a model with no known price, which leaves no cost estimate and no cost limit",
    )
    parser.add_argument("--only", help="only run cases whose ID starts with this")
    parser.add_argument("--model", help="override the configured model")
    parser.add_argument("--effort", help="override the configured reasoning effort")
    parser.add_argument("--json", type=Path, help="also write the full results to this file")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="hide failures, so that a baseline of the held-out set cannot influence changes",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.openai_api_key:
        print("OPENAI_API_KEY is not set")
        return EXIT_COULD_NOT_START
    model = args.model or settings.openai_model
    usage = _UsageCounter(model)
    if usage.prices is None and not args.allow_unknown_price:
        print(
            f"No known price for {model}, so --max-cost cannot be applied. Add it to "
            "PRICES_PER_MILLION or pass --allow-unknown-price to run with no cost limit."
        )
        return EXIT_COULD_NOT_START
    repo = Repository(settings.db_path)
    policy_index = build_policy_index(settings.data_dir / "politicas_da_loja.pdf")
    recorder = RecordingLLM(
        OpenAILLM(
            api_key=settings.openai_api_key,
            model=model,
            reasoning_effort=args.effort or settings.openai_reasoning_effort,
        )
    )
    fill_emails = _email_filler(settings.db_path)

    def make_service() -> ConversationService:
        return ConversationService(recorder, repo, policy_index)

    events.logger.addFilter(usage)
    events.logger.setLevel("INFO")

    def may_continue() -> bool:
        return not usage.reached(args.max_cost)

    results_by_set, not_run = run_sets(
        args.sets,
        lambda case: run_case(make_service, recorder, case, fill_emails, may_continue),
        may_continue,
        args.only,
    )

    output: dict[str, Any] = {"model": model}
    for name, results in results_by_set.items():
        summary = summarize(results)
        print(f"\n=== {name} ({model}) ===")
        print(json.dumps(summary))
        for result in results:
            if not result.passed and not args.summary_only:
                print(f"\n{'FAIL' if result.failed else 'INTERRUPTED'} {result.case_id}")
                for problem in result.problems:
                    print(f"  - {problem}")
                print(f"  actions: {result.actions}")
                print(f"  reply: {result.reply[:320]!r}")
        output[name] = {
            "summary": summary,
            "results": [
                {
                    "id": r.case_id,
                    "passed": r.passed,
                    "interrupted": r.interrupted,
                    "problems": r.problems,
                    "reply": r.reply,
                    "replies": r.replies,
                }
                for r in results
            ],
        }

    all_results = [result for results in results_by_set.values() for result in results]
    interrupted = sum(result.interrupted for result in all_results)
    stopped = bool(not_run or interrupted)
    if stopped:
        print(f"\nINCOMPLETE: cost limit reached, {interrupted} cut short, {not_run} not run.")
    else:
        print("\nCOMPLETE: every selected case ran.")
    print(usage.cost_report())
    print(f"Latency: {usage.latency_report()}")
    if args.json:
        output["complete"] = not stopped
        output["cases_not_run"] = not_run
        args.json.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
    return exit_code(all_results, stopped)


if __name__ == "__main__":
    sys.exit(main())
