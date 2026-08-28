import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR))

from src.orchestrator import MainAgentOrchestrator
from src.ask import build_main_prompt
from src.runtime.flow_map import MODULES, TOOL_MODULES, module_for  # noqa: E402


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


def judge_flow_map(
    orchestrator: MainAgentOrchestrator,
    case: dict[str, Any],
) -> CaseResult:
    """每个能被模型调用的工具，都要能在架构图上找到落点。

    没映射的工具会静默归到「主 Agent 循环」，图上看起来像什么都没查——
    这种错不会报异常，只会让图说谎。所以加一条守卫：
    新增工具时忘了更新 flow_map，这里会红。
    """
    tools = orchestrator._loop_tools(
        None,
        _FakeChannel(_channel_id(case["channel"])),
        read_only=case.get("read_only", False),
    )
    # no_lookup_needed 是「不用查任何数据」的出口，本来就不该有数据模块
    unmapped = sorted(
        tool.name
        for tool in tools
        if tool.name not in TOOL_MODULES and tool.name != "no_lookup_needed"
    )
    unknown = sorted(
        name for name, module in TOOL_MODULES.items() if module not in MODULES
    )
    return CaseResult(
        case_id=case["id"],
        passed=not unmapped and not unknown,
        tags=("flow_map",),
        expected={"unmapped": [], "unknown_module": []},
        actual={"unmapped": unmapped, "unknown_module": unknown},
    )


def judge_prompt_scope(
    orchestrator: MainAgentOrchestrator,
    case: dict[str, Any],
) -> CaseResult:
    """系统提示里的「你能查什么」必须和实际工具表一致。

    真实回归：为了给开源版剥离厨房和照片，角色提示词被改窄成
    「管着他记录下来的跑步训练数据」。工具表没变，`photo` 还在里面，
    但模型照着自我描述回答「我没有看照片的功能」。

    **提示词里的自我描述和工具表对不上时，模型信前者。**
    所以这条守的不是措辞，是「两者不能脱节」这个契约。
    """
    tools = orchestrator._loop_tools(
        None,
        _FakeChannel(_channel_id(case["channel"])),
        read_only=case.get("read_only", False),
    )
    names = {tool.name for tool in tools}
    prompt = build_main_prompt(names)

    expected_labels = sorted(
        {MODULES[module_for(name)] for name in names} & set(MODULES.values())
    )
    # 只检查数据类模块：entry/loop/answer 不是「能查的东西」
    expected_labels = [
        label for label in expected_labels
        if label not in {MODULES["entry"], MODULES["loop"], MODULES["answer"]}
    ]
    missing = [label for label in expected_labels if label not in prompt]

    return CaseResult(
        case_id=case["id"],
        passed=not missing,
        tags=("prompt_scope",),
        expected={"mentioned": expected_labels},
        actual={"missing": missing},
    )


def judge_case(
    orchestrator: MainAgentOrchestrator,
    case: dict[str, Any],
) -> CaseResult:
    if case.get("kind") == "prompt_scope":
        return judge_prompt_scope(orchestrator, case)

    if case.get("kind") == "flow_map":
        return judge_flow_map(orchestrator, case)

    if case.get("kind") == "loop_tools":
        return judge_loop_tools(orchestrator, case)

    allowed_commands = orchestrator._allowed_natural_language_commands(
        _channel_id(case["channel"])
    )
    if case.get("kind") == "direct_route":
        route = orchestrator._route_from_direct_intent(
            case["message"],
            allowed_commands,
            f"eval:{case['id']}",
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
        "flow_map_coverage": _tag_score(results, "flow_map"),
        "prompt_scope_correctness": _tag_score(results, "prompt_scope"),
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
