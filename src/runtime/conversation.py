import os
import threading
import time
from typing import Any

from src.runtime.llm import complete_text


RUNNING_COACH_TOPIC = "running-coach"
PHOTO_MEMORY_TOPIC = "photo-memory"

MAX_TURNS = 6
MAX_MESSAGE_CHARS = 1200
MAX_PENDING_QUESTIONS = 6
DEFAULT_IDLE_MINUTES = 60

# 窗口溢出时，一次折叠多少轮进摘要。批量折叠是为了避免每轮都触发一次压缩调用。
COMPRESS_BATCH_TURNS = 3
SUMMARY_MAX_CHARS = 700

SUMMARY_PROMPT = """
你在压缩一段跑步教练和用户的对话历史，供教练在后续对话中继续参考。

只输出压缩后的摘要正文，不要任何解释、标题或前后缀。

必须保留：
- 用户说过的所有具体事实：成绩、周跑量、配速、训练安排、休息日、伤病、比赛经历和当时发生了什么、目标和日期。
- 教练已经给出的结论和建议方向。
- 教练问过但用户还没有回答的问题。

要求：
- 数字、配速、日期一律原样保留，不要改写成约数或范围。
- 不要新增任何原文里没有的信息，不要推断。
- 如果同一件事前后有更新，以最新的说法为准。
- 用中文，不超过 700 字。
""".strip()

_lock = threading.Lock()
_sessions: dict[tuple[str, str], dict[str, Any]] = {}


def _idle_timeout_seconds() -> float:
    value = os.getenv("CONVERSATION_IDLE_MINUTES", str(DEFAULT_IDLE_MINUTES))
    try:
        minutes = float(value)
    except ValueError:
        minutes = DEFAULT_IDLE_MINUTES
    return max(minutes, 1.0) * 60


def _compression_enabled() -> bool:
    value = os.getenv("CONVERSATION_COMPRESSION_ENABLED", "true")
    return value.lower() not in {"0", "false", "no", "off"}


def _truncate(text: str) -> str:
    cleaned = text.strip()
    if len(cleaned) <= MAX_MESSAGE_CHARS:
        return cleaned
    return f"{cleaned[:MAX_MESSAGE_CHARS].rstrip()}..."


def _live_session(key: tuple[str, str], now: float) -> dict[str, Any] | None:
    """读取未过期的会话，过期的直接丢弃。必须在持锁状态下调用。"""
    session = _sessions.get(key)
    if session is None:
        return None
    if now - session["updated_at"] > _idle_timeout_seconds():
        del _sessions[key]
        return None
    return session


def _touch_session(key: tuple[str, str], now: float) -> dict[str, Any]:
    """读取会话，过期或不存在时新建。必须在持锁状态下调用。"""
    session = _live_session(key, now)
    if session is None:
        session = {
            "messages": [],
            "summary": "",
            "pending_questions": [],
            "updated_at": now,
        }
        _sessions[key] = session
    session["updated_at"] = now
    return session


def get_history(conversation_id: str | None, topic: str) -> tuple[dict[str, str], ...]:
    if not conversation_id:
        return ()

    with _lock:
        session = _live_session((conversation_id, topic), time.time())
        if session is None:
            return ()
        return tuple(dict(message) for message in session["messages"])


def get_summary(conversation_id: str | None, topic: str) -> str:
    """获取更早之前对话被压缩成的摘要，没有则返回空字符串。"""
    if not conversation_id:
        return ""

    with _lock:
        session = _live_session((conversation_id, topic), time.time())
        if session is None:
            return ""
        return session["summary"]


def _format_turns(messages: list[dict[str, str]]) -> str:
    lines = []
    for message in messages:
        speaker = "用户" if message["role"] == "user" else "教练"
        lines.append(f"{speaker}：{message['content']}")
    return "\n\n".join(lines)


async def _compress(previous_summary: str, messages: list[dict[str, str]]) -> str:
    existing = f"已有摘要：\n{previous_summary}\n\n" if previous_summary else ""
    summary = await complete_text(
        SUMMARY_PROMPT,
        f"{existing}需要并入摘要的对话：\n{_format_turns(messages)}",
    )
    cleaned = summary.strip()
    if len(cleaned) > SUMMARY_MAX_CHARS:
        cleaned = f"{cleaned[:SUMMARY_MAX_CHARS].rstrip()}..."
    return cleaned


async def append_turn(
    conversation_id: str | None,
    topic: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """写入一轮对话。窗口溢出时把最老的几轮压缩成摘要，而不是直接丢弃。"""
    if not conversation_id or not user_text.strip() or not assistant_text.strip():
        return

    key = (conversation_id, topic)
    with _lock:
        session = _touch_session(key, time.time())
        messages = session["messages"]
        messages.append({"role": "user", "content": _truncate(user_text)})
        messages.append({"role": "assistant", "content": _truncate(assistant_text)})

        if len(messages) <= MAX_TURNS * 2:
            return

        # 取出最老的一批，先从窗口里摘掉再去压缩，避免持锁跨越网络调用。
        batch_size = min(COMPRESS_BATCH_TURNS * 2, len(messages) - 2)
        folding = messages[:batch_size]
        del messages[:batch_size]
        previous_summary = session["summary"]

    if not _compression_enabled():
        return

    try:
        summary = await _compress(previous_summary, folding)
    except Exception as exc:
        # 压缩失败就退回原来的行为：这批对话被丢弃，但摘要和后续对话不受影响。
        print(f"conversation compression failed: {exc}", flush=True)
        return

    with _lock:
        session = _sessions.get(key)
        if session is not None:
            session["summary"] = summary


def last_user_message(conversation_id: str | None, topic: str) -> str:
    for message in reversed(get_history(conversation_id, topic)):
        if message["role"] == "user":
            return message["content"]
    return ""


def set_pending_questions(
    conversation_id: str | None,
    topic: str,
    questions: list[str],
) -> None:
    """记下 Agent 这一轮追问了什么。下一条消息会被当成对这些问题的回答。

    传入空列表即表示这一轮没有追问，等价于清除 pending。
    """
    if not conversation_id:
        return

    cleaned = [question.strip() for question in questions if question.strip()]
    with _lock:
        session = _touch_session((conversation_id, topic), time.time())
        session["pending_questions"] = cleaned[:MAX_PENDING_QUESTIONS]


def get_pending_questions(conversation_id: str | None, topic: str) -> tuple[str, ...]:
    if not conversation_id:
        return ()

    with _lock:
        session = _live_session((conversation_id, topic), time.time())
        if session is None:
            return ()
        return tuple(session["pending_questions"])


def set_context_value(
    conversation_id: str | None,
    topic: str,
    key: str,
    value: object,
) -> None:
    if not conversation_id or not key:
        return

    with _lock:
        session = _touch_session((conversation_id, topic), time.time())
        context = session.setdefault("context", {})
        if isinstance(context, dict):
            context[key] = value


def get_context_value(
    conversation_id: str | None,
    topic: str,
    key: str,
) -> object | None:
    if not conversation_id or not key:
        return None

    with _lock:
        session = _live_session((conversation_id, topic), time.time())
        if session is None:
            return None
        context = session.get("context")
        if not isinstance(context, dict):
            return None
        return context.get(key)


def clear_history(conversation_id: str | None, topic: str) -> None:
    if not conversation_id:
        return

    with _lock:
        _sessions.pop((conversation_id, topic), None)
