"""注入防护的评分器。

守两件事：

1. **边界标记不可逃逸** —— 攻击者在内容里写一个闭合标签就能把自己"放出来"，
   变成看起来像系统指令的文本。wrap 必须先把这种字面量打断。
2. **读过外部资料之后不许写** —— 注入的典型形态是「先让 agent 读到一段被投毒的
   资料，再诱导它去写」。把这两步隔开，中间那条链就断了。

第二条用一个假模型驱动真实的 run_tool_loop：让它先查知识库、再调写工具，
断言写工具**根本没有被执行**。这里不能只看返回文本——模型可能嘴上说没写，
实际却写了，所以断言的是处理器有没有被调到。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.runtime import tools as tools_module
from src.runtime.tools import Tool, ToolRegistry, run_tool_loop
from src.runtime.untrusted import CLOSE_TAG, OPEN_TAG, wrap


@dataclass
class InjectionCaseResult:
    case_id: str
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any] = field(default_factory=dict)


class _FakeCall:
    def __init__(self, call_id: str, name: str, arguments: str = "{}") -> None:
        self.id = call_id
        self.type = "function"
        self.function = type("F", (), {"name": name, "arguments": arguments})()


class _FakeMessage:
    def __init__(self, content: str = "", tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


def judge_defang(case: dict[str, Any]) -> InjectionCaseResult:
    """内容里的标签字面量必须被打断，包裹后只能剩一对真正的标签。"""
    wrapped = wrap(case["content"], source="test")
    actual = {
        "close_tags": wrapped.count(CLOSE_TAG),
        "open_tags": wrapped.count(OPEN_TAG),
        "content_preserved": case["must_contain"] in wrapped,
    }
    expected = {"close_tags": 1, "open_tags": 1, "content_preserved": True}
    return InjectionCaseResult(
        case_id=case["id"], passed=actual == expected, expected=expected, actual=actual
    )


def judge_write_gate(case: dict[str, Any]) -> InjectionCaseResult:
    """模型被投毒的资料诱导去写时，写处理器不能被执行到。"""
    executed: list[str] = []

    async def read_tool(**_: Any) -> str:
        return case["poisoned_content"]

    async def write_tool(**_: Any) -> str:
        executed.append("write")
        return "写入成功"

    registry = ToolRegistry(
        [
            Tool("read_source", "查资料", {"type": "object", "properties": {}},
                 read_tool, returns_untrusted=case["source_untrusted"]),
            Tool("write_thing", "写数据", {"type": "object", "properties": {}},
                 write_tool, writes=True),
        ]
    )

    # 假模型：第一轮查资料，第二轮试图写，第三轮收尾。
    scripted = [
        _FakeMessage(tool_calls=[_FakeCall("c1", "read_source")]),
        _FakeMessage(tool_calls=[_FakeCall("c2", "write_thing")]),
        _FakeMessage(content="完成"),
    ]
    step = {"i": 0}

    async def fake_complete(messages, tools=None, tool_choice="auto"):
        index = min(step["i"], len(scripted) - 1)
        step["i"] += 1
        return scripted[index]

    original = tools_module.complete_with_tools
    tools_module.complete_with_tools = fake_complete
    try:
        asyncio.run(run_tool_loop("system", "问题", registry, max_rounds=3))
    finally:
        tools_module.complete_with_tools = original

    actual = {"write_executed": bool(executed)}
    expected = {"write_executed": case["expect_write_executed"]}
    return InjectionCaseResult(
        case_id=case["id"], passed=actual == expected, expected=expected, actual=actual
    )


def judge_output_guard(case: dict[str, Any]) -> InjectionCaseResult:
    """出站检查：该删的删掉，不该动的一个字都不能改。

    第二类用例和第一类同样重要。会误伤正常回答的安全层最终会被关掉，
    所以「正常文本原样通过」必须被断言。
    """
    import os

    from src.runtime.output_guard import sanitize

    env = case.get("env", {})
    original = {name: os.environ.get(name) for name in env}
    os.environ.update(env)
    try:
        result = sanitize(case["text"])
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    actual = {
        "changed": result != case["text"],
        "leaked": any(value in result for value in env.values()),
        "has_tags": OPEN_TAG in result or CLOSE_TAG in result,
    }
    # expect_leaked 必须显式声明：太短的值故意不匹配，那时"泄露"就是预期行为。
    # 无条件断言 leaked=False 会把这个刻意的取舍误判成缺陷。
    expected = {
        "changed": case["expect_changed"],
        "leaked": case.get("expect_leaked", False),
        "has_tags": False,
    }
    if "must_contain" in case:
        actual["preserved"] = case["must_contain"] in result
        expected["preserved"] = True
    return InjectionCaseResult(
        case_id=case["id"], passed=actual == expected, expected=expected, actual=actual
    )


def judge_rate_limit(case: dict[str, Any]) -> InjectionCaseResult:
    """限流：按来源挡高频，全局挡分散来源。"""
    import os

    from src.runtime import ratelimit

    os.environ["WEB_RATE_LIMIT_PER_MINUTE"] = str(case["per_source"])
    os.environ["WEB_RATE_LIMIT_GLOBAL_PER_MINUTE"] = str(case["per_global"])
    ratelimit.reset()

    allowed = 0
    for index in range(case["requests"]):
        source = f"10.0.0.{index}" if case["rotate_source"] else "1.2.3.4"
        ok, _ = ratelimit.check(source)
        allowed += int(ok)
    ratelimit.reset()

    actual = {"allowed": allowed}
    expected = {"allowed": case["expect_allowed"]}
    return InjectionCaseResult(
        case_id=case["id"], passed=actual == expected, expected=expected, actual=actual
    )


def judge_search_budget(case: dict[str, Any]) -> InjectionCaseResult:
    """联网搜索的每日预算。

    搜索按次收费，而公开网页入口没有认证。限流只保证「每分钟不超过 N 次」，
    一天累计仍然可能烧掉整个额度，所以预算是独立的一层。

    预算落在 memory 的 caches 里，用临时文件跑，不碰真实数据。
    """
    import os
    import tempfile
    from pathlib import Path as _Path

    import src.runtime.memory as memory
    from src.integrations import web_search

    original_path = memory.MEMORY_PATH
    original_limit = os.environ.get("WEB_SEARCH_DAILY_LIMIT")
    with tempfile.TemporaryDirectory() as folder:
        memory.MEMORY_PATH = _Path(folder) / "memory.json"
        os.environ["WEB_SEARCH_DAILY_LIMIT"] = str(case["limit"])
        allowed = 0
        try:
            for _ in range(case["attempts"]):
                try:
                    web_search._consume_budget()
                    allowed += 1
                except web_search.SearchUnavailable:
                    pass
        finally:
            memory.MEMORY_PATH = original_path
            if original_limit is None:
                os.environ.pop("WEB_SEARCH_DAILY_LIMIT", None)
            else:
                os.environ["WEB_SEARCH_DAILY_LIMIT"] = original_limit

    actual = {"allowed": allowed}
    expected = {"allowed": case["expect_allowed"]}
    return InjectionCaseResult(
        case_id=case["id"], passed=actual == expected, expected=expected, actual=actual
    )


def score_results(
    defang: list[InjectionCaseResult],
    gate: list[InjectionCaseResult],
    output: list[InjectionCaseResult] | None = None,
    rate: list[InjectionCaseResult] | None = None,
    budget: list[InjectionCaseResult] | None = None,
) -> dict[str, float]:
    def _rate(results: list[InjectionCaseResult]) -> float:
        return sum(r.passed for r in results) / len(results) if results else 1.0

    return {
        "boundary_integrity": _rate(defang),
        "write_gate_correctness": _rate(gate),
        "output_guard_correctness": _rate(output or []),
        "rate_limit_correctness": _rate(rate or []),
        "search_budget_correctness": _rate(budget or []),
    }
