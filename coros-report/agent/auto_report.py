import json
import os
from datetime import date, timedelta
from typing import Any

import discord

from prompt import REPORT_SYSTEM_PROMPT
from src.integrations.coros_mcp import call_coros_tool
from src.runtime.llm import complete_text
from src.runtime.memory import format_memory_for_prompt, get_agent_memory, update_agent_memory
from src.runtime.scheduler import add_interval_job


AGENT_NAME = "coros-report"
_job_running = False


def _date_text(day: date) -> str:
    return day.strftime("%Y%m%d")


def _activity_query_arguments() -> dict[str, Any]:
    lookback_days = int(os.getenv("COROS_AUTO_REPORT_LOOKBACK_DAYS", "7"))
    today = date.today()
    start_date = today - timedelta(days=max(lookback_days, 1))
    return {
        "startDate": _date_text(start_date),
        "endDate": _date_text(today),
        "sportTypeCodes": [65535],
        "minDistanceKm": 0,
        "maxDistanceKm": 1000,
        "minDurationMinutes": 0,
        "maxDurationMinutes": 1440,
        "maxAveragePace": "",
        "locationKeyword": "",
        "limit": 10,
    }


def _parse_json_text(text: str) -> Any:
    stripped = text.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _expand_payload(value: Any) -> list[Any]:
    expanded = [value]
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parsed = _parse_json_text(item["text"])
                    if parsed is not None:
                        expanded.extend(_expand_payload(parsed))
        for key in ("data", "records", "activities", "list", "items", "result"):
            child = value.get(key)
            if child is not None:
                expanded.extend(_expand_payload(child))
    elif isinstance(value, list):
        for item in value:
            expanded.extend(_expand_payload(item))
    return expanded


def _activity_records(payload: Any) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _expand_payload(payload):
        if not isinstance(item, dict):
            continue
        if item.get("labelId") is not None and item.get("sportType") is not None:
            records.append(item)
    return records


def _timestamp(activity: dict[str, Any]) -> int:
    for key in ("endTimestamp", "startTimestamp", "timestamp"):
        value = activity.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def activity_key(activity: dict[str, Any]) -> str:
    parts = [
        str(activity.get("labelId", "")),
        str(activity.get("sportType", "")),
        str(activity.get("startTimestamp", "")),
        str(activity.get("endTimestamp", "")),
    ]
    return ":".join(parts)


async def latest_coros_activity() -> dict[str, Any] | None:
    try:
        payload = await call_coros_tool("querySportRecords", _activity_query_arguments())
    except Exception:
        payload = await call_coros_tool("querySportRecords", {})
    records = _activity_records(payload)
    if not records:
        return None
    return max(records, key=_timestamp)


def should_send_activity(activity: dict[str, Any]) -> bool:
    latest_reported_id = get_agent_memory(AGENT_NAME).get("latest_reported_activity_id")
    return latest_reported_id != activity_key(activity)


def has_reported_activity() -> bool:
    return get_agent_memory(AGENT_NAME).get("latest_reported_activity_id") is not None


def mark_activity_reported(activity: dict[str, Any]) -> None:
    update_agent_memory(
        AGENT_NAME,
        {
            "latest_reported_activity_id": activity_key(activity),
            "latest_reported_activity": {
                "labelId": activity.get("labelId"),
                "sportType": activity.get("sportType"),
                "startTimestamp": activity.get("startTimestamp"),
                "endTimestamp": activity.get("endTimestamp"),
            },
        },
    )


async def generate_auto_activity_report(activity: dict[str, Any]) -> str:
    label_id = activity.get("labelId")
    sport_type = activity.get("sportType")
    if label_id is None or sport_type is None:
        raise RuntimeError("Latest COROS activity is missing labelId or sportType.")

    tool_results: list[dict[str, Any]] = [
        {
            "tool": {"name": "querySportRecords", "arguments": _activity_query_arguments()},
            "ok": True,
            "result": {"selected_latest_activity": activity},
        }
    ]
    detail_args = {"labelId": str(label_id), "sportType": int(sport_type)}
    for tool_name, arguments in [
        ("getActivityDetail", detail_args),
        ("queryActivityLapData", detail_args),
        ("queryRecoveryStatus", {}),
        ("queryTrainingLoadAssessment", {}),
    ]:
        try:
            result = await call_coros_tool(tool_name, arguments)
            tool_results.append(
                {"tool": {"name": tool_name, "arguments": arguments}, "ok": True, "result": result}
            )
        except Exception as exc:
            tool_results.append(
                {"tool": {"name": tool_name, "arguments": arguments}, "ok": False, "error": str(exc)}
            )

    memory = format_memory_for_prompt(AGENT_NAME)
    return await complete_text(
        REPORT_SYSTEM_PROMPT,
        f"""
User request:
自动检测到一条新的 COROS 运动。请只分析 selected_latest_activity 对应的这一次运动，并生成运动后报告。

User memory:
{memory}

COROS tool calls and results:
{json.dumps(tool_results, ensure_ascii=False, indent=2)}

Generate the workout report from the available COROS data.
""".strip(),
    )


def _auto_report_enabled() -> bool:
    return os.getenv("COROS_AUTO_REPORT_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _send_on_first_run() -> bool:
    return os.getenv("COROS_AUTO_REPORT_SEND_ON_FIRST_RUN", "false").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _poll_minutes() -> int:
    value = int(os.getenv("COROS_AUTO_REPORT_POLL_MINUTES", "15"))
    return max(value, 1)


def _configured_channel_id() -> int | None:
    value = os.getenv("DISCORD_RUNNING_CHANNEL_ID")
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


async def _send_chunks(channel: discord.abc.Messageable, text: str) -> None:
    chunk_size = 1800
    for start in range(0, len(text), chunk_size):
        await channel.send(text[start : start + chunk_size])


async def _report_channel(client: discord.Client) -> discord.abc.Messageable | None:
    channel_id = _configured_channel_id()
    if channel_id is None:
        return None

    channel = client.get_channel(channel_id)
    if channel is not None:
        return channel

    fetched = await client.fetch_channel(channel_id)
    if callable(getattr(fetched, "send", None)):
        return fetched
    return None


async def check_and_send_coros_auto_report(
    client: discord.Client,
    notify_no_change: bool = False,
    send_on_first_run: bool | None = None,
    force_send: bool = False,
) -> str:
    global _job_running
    if _job_running:
        return "COROS auto report skipped: previous job is still running."

    _job_running = True
    try:
        channel = await _report_channel(client)
        if channel is None:
            return "COROS auto report skipped: DISCORD_RUNNING_CHANNEL_ID is invalid."

        activity = await latest_coros_activity()
        if activity is None:
            return "COROS auto report skipped: no recent activity found."

        if not force_send and not should_send_activity(activity):
            message = "COROS auto report skipped: no new activity."
            if notify_no_change:
                await channel.send("没有检测到新的 COROS 运动。")
            return message

        should_send_first = (
            _send_on_first_run() if send_on_first_run is None else send_on_first_run
        )
        if not force_send and not has_reported_activity() and not should_send_first:
            mark_activity_reported(activity)
            return "COROS auto report initialized with latest activity."

        await channel.send("检测到新的 COROS 运动，正在自动生成报告...")
        report = await generate_auto_activity_report(activity)
        await _send_chunks(channel, report)
        mark_activity_reported(activity)
        return "COROS auto report sent."
    except Exception as exc:
        return f"COROS auto report failed: {exc}"
    finally:
        _job_running = False


async def _scheduled_check(client: discord.Client) -> None:
    result = await check_and_send_coros_auto_report(client)
    if result != "COROS auto report skipped: no new activity.":
        print(result)


def register_coros_auto_report(client: discord.Client) -> None:
    if not _auto_report_enabled():
        print("COROS auto report scheduler is disabled.")
        return

    add_interval_job(
        "coros-auto-report",
        _scheduled_check,
        _poll_minutes(),
        args=[client],
    )
    print(f"COROS auto report scheduler started: every {_poll_minutes()} minutes.")
