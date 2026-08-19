import os
import threading
import time
from typing import Any


RUNNING_COACH_TOPIC = "running-coach"

MAX_TURNS = 6
MAX_MESSAGE_CHARS = 1200
MAX_PENDING_QUESTIONS = 6
DEFAULT_IDLE_MINUTES = 60

_lock = threading.Lock()
_sessions: dict[tuple[str, str], dict[str, Any]] = {}


def _idle_timeout_seconds() -> float:
    value = os.getenv("CONVERSATION_IDLE_MINUTES", str(DEFAULT_IDLE_MINUTES))
    try:
        minutes = float(value)
    except ValueError:
        minutes = DEFAULT_IDLE_MINUTES
    return max(minutes, 1.0) * 60


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
        session = {"messages": [], "pending_questions": [], "updated_at": now}
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


def append_turn(
    conversation_id: str | None,
    topic: str,
    user_text: str,
    assistant_text: str,
) -> None:
    if not conversation_id or not user_text.strip() or not assistant_text.strip():
        return

    with _lock:
        session = _touch_session((conversation_id, topic), time.time())
        messages = session["messages"]
        messages.append({"role": "user", "content": _truncate(user_text)})
        messages.append({"role": "assistant", "content": _truncate(assistant_text)})
        overflow = len(messages) - MAX_TURNS * 2
        if overflow > 0:
            del messages[:overflow]


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


def clear_history(conversation_id: str | None, topic: str) -> None:
    if not conversation_id:
        return

    with _lock:
        _sessions.pop((conversation_id, topic), None)
