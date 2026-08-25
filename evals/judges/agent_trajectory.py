"""主 Agent 轨迹评测：唯一一套真的调模型的评测。

另外五套都用桩或构造好的模型输出，好处是可重复、能离线跑、不花钱，
代价是**把聊天模型换掉，93 个用例照样全绿**。这一套补的就是那个洞。

**断言轨迹，不断言文本。**

模型每次措辞都不一样，断言最终答案的字面内容必然会飘，飘几次之后
这套测试就没人看了。而真实的缺陷恰恰在轨迹这一层——「我一共跑过几场比赛」
被查成训练流水，错的是选错了数据源，不是话说得不好。

**不依赖具体数据。** 断言「回答里有 8 场」会在用户新增一场比赛时红掉，
那是数据变了不是代码坏了。所以只断言调用了哪些工具、没调用哪些。

**阈值不是 1.0。** 模型有随机性，一次跑挂不代表坏了。用多次重复
取通过率，并把阈值定在能发现真实退化、又不会被噪声触发的位置。
"""

import asyncio
from dataclasses import dataclass, field
from typing import Any

from src.ask import answer_open_question
from src.orchestrator import MainAgentOrchestrator


@dataclass
class TrajectoryCaseResult:
    case_id: str
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any] = field(default_factory=dict)


class _FakeChannel:
    """只提供 id 和 send。轨迹评测不关心发出去的内容。"""

    def __init__(self, channel_id: int = 111) -> None:
        self.id = channel_id
        self.sent: list[str] = []

    async def send(self, content: str = "", **_: Any) -> None:
        if content:
            self.sent.append(content)


async def _run_once(
    orchestrator: MainAgentOrchestrator,
    turns: list[str],
    channel_id: int,
) -> tuple[list[list[str]], str]:
    """跑完一个用例的所有轮次，返回每轮调用的工具名和最后一轮的回答。

    多轮是为了测「拿历史当数据源」那类缺陷：第一轮问出答案之后，
    第二轮追问相关问题时模型很容易不查就答，还会把没查过的细节编出来。
    """
    channel = _FakeChannel(channel_id)
    conversation_id = f"eval-traj-{id(channel)}"
    per_turn: list[list[str]] = []
    answer = ""

    for turn in turns:
        called: list[str] = []

        async def on_tool(name: str, _why: str, sink: list[str] = called) -> None:
            sink.append(name)

        tools = orchestrator._loop_tools(None, channel, read_only=False)
        answer = await answer_open_question(
            turn, tools, conversation_id=conversation_id, on_tool=on_tool
        )
        per_turn.append(called)

    return per_turn, answer


def _check(case: dict[str, Any], per_turn: list[list[str]]) -> tuple[bool, dict]:
    """只看最后一轮的调用。前面几轮是为了把上下文铺出来。"""
    called = per_turn[-1] if per_turn else []
    problems: list[str] = []

    must_call_any = case.get("must_call_any", [])
    if must_call_any and not any(name in called for name in must_call_any):
        problems.append(f"没有调用 {must_call_any} 中的任何一个")

    for name in case.get("must_not_call", []):
        if name in called:
            problems.append(f"不该调用 {name}")

    if case.get("must_call_something") and not called:
        problems.append("一个工具都没调，可能是拿历史里的答案硬答")

    return not problems, {"called": called, "problems": problems}


def judge_case(
    orchestrator: MainAgentOrchestrator,
    case: dict[str, Any],
    repeat: int,
) -> TrajectoryCaseResult:
    runs, details = 0, []
    for _ in range(max(repeat, 1)):
        per_turn, _answer = asyncio.run(
            _run_once(orchestrator, case["turns"], case.get("channel_id", 111))
        )
        ok, detail = _check(case, per_turn)
        runs += int(ok)
        details.append(detail)

    total = max(repeat, 1)
    return TrajectoryCaseResult(
        case_id=case["id"],
        passed=runs == total,
        expected={
            "must_call_any": case.get("must_call_any", []),
            "must_not_call": case.get("must_not_call", []),
            "pass_rate": 1.0,
        },
        actual={"pass_rate": runs / total, "runs": details},
    )


def score_results(results: list[TrajectoryCaseResult]) -> dict[str, float]:
    if not results:
        return {"tool_choice_accuracy": 1.0}
    total = sum(r.actual.get("pass_rate", 0.0) for r in results)
    return {"tool_choice_accuracy": total / len(results)}
