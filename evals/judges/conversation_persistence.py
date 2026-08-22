"""会话持久化的评分器。

这套守的是一类**不会报错的失败**：历史悄悄丢了，功能看起来一切正常，
只有用户追问「你刚才说的那个配速」时才暴露。所以断言的是不变量，
不是输出长得对不对。

三条不变量：
1. 进程重启后历史还在（内存 dict 清空 ≠ 数据消失）
2. 压缩只缩小内存窗口，磁盘上的原文一条不少
3. 闲置超时切出的会话边界，重启重建后和线上实时跑出来的一致

每个用例跑在自己的临时目录里，不碰真实的 data/。压缩用桩函数，不调模型。
"""

import asyncio
import importlib
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ConversationCaseResult:
    case_id: str
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any] = field(default_factory=dict)


STUB_SUMMARY = "【摘要】压缩桩"


def _fresh_module(folder: str, *, compression: bool, idle_minutes: str = "60"):
    """在指定目录上重新加载一次 conversation 模块。

    重新 import 就是在模拟进程重启：模块级的 _sessions 是全新的空 dict，
    能读到什么就完全取决于磁盘上的日志。
    """
    os.environ["CONVERSATION_DIR"] = folder
    os.environ["CONVERSATION_COMPRESSION_ENABLED"] = "true" if compression else "false"
    os.environ["CONVERSATION_IDLE_MINUTES"] = idle_minutes

    import src.runtime.conversation as conversation

    module = importlib.reload(conversation)

    async def _stub(_system: str, _user: str) -> str:
        return STUB_SUMMARY

    module.complete_text = _stub
    return module


def _contents(messages: tuple[dict[str, str], ...]) -> list[str]:
    return [message["content"] for message in messages]


def judge_case(case: dict[str, Any]) -> ConversationCaseResult:
    with tempfile.TemporaryDirectory() as folder:
        actual = asyncio.run(_run_case(case, folder))
    expected = case["expect"]
    return ConversationCaseResult(
        case_id=case["id"],
        passed=actual == expected,
        expected=expected,
        actual=actual,
    )


async def _run_case(case: dict[str, Any], folder: str) -> dict[str, Any]:
    turns = case["turns"]
    conversation_id = case.get("conversation_id", "eval-channel")
    topic = case.get("topic", "running-coach")

    module = _fresh_module(folder, compression=case.get("compression", False))

    for index in range(1, turns + 1):
        await module.append_turn(conversation_id, topic, f"问题{index}", f"回答{index}")

    if case.get("pending"):
        module.set_pending_questions(conversation_id, topic, case["pending"])
    if case.get("clear_after_write"):
        module.clear_history(conversation_id, topic)
    if case.get("gap_before_last_turns"):
        _inject_gap(folder, case["gap_before_last_turns"])

    # 重新加载 = 重启。之后读到的一切都来自磁盘。
    module = _fresh_module(folder, compression=case.get("compression", False))

    result: dict[str, Any] = {
        "window": _contents(module.get_history(conversation_id, topic)),
        "full": _contents(module.read_full_history(conversation_id, topic)),
    }
    if case.get("compression"):
        result["has_summary"] = bool(module.get_summary(conversation_id, topic))
    if case.get("pending"):
        result["pending"] = list(module.get_pending_questions(conversation_id, topic))
    return result


def _inject_gap(folder: str, keep_recent_entries: int) -> None:
    """把靠前的记录挪到很久以前，制造一次闲置超时。

    直接改时间戳而不是真的等待，否则这条用例要跑一个小时。
    """
    path = next(Path(folder).rglob("*.jsonl"))
    entries = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    now = time.time()
    boundary = len(entries) - keep_recent_entries
    for index, entry in enumerate(entries):
        entry["ts"] = now - 10800 if index < boundary else now - 5
    path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )


def score_results(results: list[ConversationCaseResult]) -> dict[str, float]:
    passed = sum(result.passed for result in results)
    return {
        "persistence_correctness": passed / len(results) if results else 1.0,
    }
