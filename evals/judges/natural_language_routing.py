import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

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


class _FakeChannel:
    def __init__(self, channel_id: int) -> None:
        self.id = channel_id


def judge_loop_tools(
    orchestrator: MainAgentOrchestrator,
    case: dict[str, Any],
) -> CaseResult:
    """主 Agent 循环在这个频道能看到哪些工具。

    权限现在挂在工具表上：写工具在只读入口根本不出现。这比事后拦截强，
    因为模型看不见的工具不可能被调用。所以这张表本身必须有守卫。
    """
    tools = orchestrator._loop_tools(
        None,
        _FakeChannel(_channel_id(case["channel"])),
        read_only=case.get("read_only", False),
    )
    actual = sorted(tool.name for tool in tools)
    expected = sorted(case["expect_tools"])
    return CaseResult(
        case_id=case["id"],
        passed=actual == expected,
        tags=("loop_tools",),
        expected={"tools": expected},
        actual={"tools": actual},
    )


def judge_case(
    orchestrator: MainAgentOrchestrator,
    case: dict[str, Any],
) -> CaseResult:
    if case.get("kind") == "loop_tools":
        return judge_loop_tools(orchestrator, case)

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
        "loop_tool_exposure": _tag_score(results, "loop_tools"),
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
