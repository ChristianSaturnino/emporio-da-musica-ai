"""Measure the real agent on a fixed set of cases. This calls the OpenAI API, which costs a little.

The dev set is for finding problems and may guide changes. The held-out set is only for measuring,
so its score is closer to what new questions would get. Both were written by hand by the author, so
they are a regression check and not a benchmark.
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
)
# Input and output prices per 1M tokens, from the OpenAI pricing pages. Cached input is cheaper, so
# the costs reported here are an upper bound.
PRICES_PER_MILLION = {
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-6-luna": (Decimal("0.10"), Decimal("0.50")),
}
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
        self.facts = messages[-1]["content"]
        return self.inner.compose_reply(messages)


@dataclass
class CaseResult:
    case_id: str
    problems: list[str] = field(default_factory=list)
    reply: str = ""
    actions: list[dict[str, Any]] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.problems


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
) -> CaseResult:
    service = make_service()
    reply = ""
    for message in case["messages"]:
        # Some turns call no model, so what is left here could belong to an earlier message.
        recorder.parsed, recorder.facts = None, ""
        reply = service.chat(case["id"], fill_emails(message))
    parsed = recorder.parsed
    if parsed is None and reply in (prompts.GREETING_MESSAGE, prompts.FAREWELL_MESSAGE):
        parsed = ParsedTurn(actions=[ActionRequest(intent=IntentType.GREETING)])
    return score_case(case, parsed, recorder.facts, reply)


class _UsageCounter:
    def __init__(self, model: str) -> None:
        self.input_price, self.output_price = PRICES_PER_MILLION.get(
            model, PRICES_PER_MILLION["gpt-4o-mini"]
        )
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
    def cost(self) -> Decimal:
        return (
            self.input_tokens * self.input_price + self.output_tokens * self.output_price
        ) / Decimal(1_000_000)


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
        **{
            check: sum(r.checks.get(check, False) for r in results)
            for check in ("intent", "slots", "sections", "reply")
        },
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sets", nargs="+", choices=SET_NAMES)
    parser.add_argument("--max-cost", type=Decimal, default=Decimal("0.10"), help="stop at this")
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
        return 1
    model = args.model or settings.openai_model
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

    usage = _UsageCounter(model)
    events.logger.addFilter(usage)
    events.logger.setLevel("INFO")

    output: dict[str, Any] = {"model": model}
    for name in args.sets:
        cases = [c for c in load_cases(name) if not args.only or c["id"].startswith(args.only)]
        results = []
        for case in cases:
            if usage.cost >= args.max_cost:
                print(f"Stopped at the cost limit, after {len(results)} of {len(cases)} cases")
                break
            results.append(run_case(make_service, recorder, case, fill_emails))

        summary = summarize(results)
        print(f"\n=== {name} ({model}) ===")
        print(json.dumps(summary))
        for result in results:
            if not result.passed and not args.summary_only:
                print(f"\nFAIL {result.case_id}")
                for problem in result.problems:
                    print(f"  - {problem}")
                print(f"  actions: {result.actions}")
                print(f"  reply: {result.reply[:320]!r}")
        output[name] = {
            "summary": summary,
            "results": [
                {"id": r.case_id, "passed": r.passed, "problems": r.problems, "reply": r.reply}
                for r in results
            ],
        }

    print(
        f"\nTokens: input {usage.input_tokens}, output {usage.output_tokens}. "
        f"Cost: at most US$ {usage.cost:.4f}"
    )
    print(f"Latency: {usage.latency_report()}")
    if args.json:
        args.json.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
