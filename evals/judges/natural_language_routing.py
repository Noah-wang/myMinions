import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "agents" / "kitchen-assistant" / "agent"))
sys.path.insert(0, str(ROOT_DIR / "agents" / "coros-report" / "agent"))

from src.orchestrator import MainAgentOrchestrator  # noqa: E402


RUNNING_CHANNEL_ID = "111"
COOKING_CHANNEL_ID = "222"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    passed: bool
    tags: tuple[str, ...]
    expected: object
    actual: object


def configure_eval_environment() -> None:
    os.environ["DISCORD_RUNNING_CHANNEL_ID"] = RUNNING_CHANNEL_ID
    os.environ["DISCORD_COOKING_CHANNEL_ID"] = COOKING_CHANNEL_ID
    os.environ["NATURAL_LANGUAGE_ROUTING_CONFIDENCE"] = "0.7"


def judge_case(
    orchestrator: MainAgentOrchestrator,
    case: dict[str, Any],
) -> CaseResult:
    allowed_commands = orchestrator._allowed_natural_language_commands(
        _channel_id(case["channel"])
    )
    route = orchestrator._route_from_llm_response(
        case["llm_response"],
        case["message"],
        allowed_commands,
    )

    actual = None
    if route is not None:
        actual = {
            "command": route.command_name,
            "argument": route.argument,
        }

    expected = case["expected_route"]
    return CaseResult(
        case_id=case["id"],
        passed=actual == expected,
        tags=tuple(case.get("tags", [])),
        expected=expected,
        actual=actual,
    )


def score_results(results: list[CaseResult]) -> dict[str, float]:
    return {
        "route_accuracy": _tag_score(results, "positive"),
        "rejection_accuracy": _tag_score(results, "negative"),
        "cross_channel_rejection": _tag_score(results, "cross_channel"),
        "low_confidence_rejection": _tag_score(results, "low_confidence"),
        "invalid_argument_rejection": _tag_score(results, "invalid_argument"),
    }


def _tag_score(results: list[CaseResult], tag: str) -> float:
    tagged = [result for result in results if tag in result.tags]
    if not tagged:
        return 1.0
    passed = [result for result in tagged if result.passed]
    return len(passed) / len(tagged)


def _channel_id(name: str) -> int:
    if name == "running":
        return int(RUNNING_CHANNEL_ID)
    if name == "cooking":
        return int(COOKING_CHANNEL_ID)
    raise ValueError(f"Unknown channel: {name}")
