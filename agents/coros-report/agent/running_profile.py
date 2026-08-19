import json
from copy import deepcopy
from typing import Any

from src.runtime.llm import complete_json
from src.runtime.memory import get_agent_memory, update_agent_memory


AGENT_NAME = "coros-report"

PROFILE_EXTRACT_PROMPT = """
你从用户消息中抽取跑步长期记忆。只返回 JSON，不要解释。

只抽取用户明确说出的信息；不要推断、不要补全、不要根据常识猜。

返回格式：
{
  "profile_patch": {
    "body_metrics": {
      "age": null,
      "height_cm": null,
      "weight_kg": null
    },
    "current_times": {
      "half_marathon": null,
      "marathon": null,
      "five_k": null,
      "ten_k": null
    },
    "goals": [
      {
        "distance": "marathon",
        "target_time": "3:30:00",
        "target_date": "2026-12-01"
      }
    ],
    "training_context": {
      "training_days_per_week": null,
      "weekly_mileage_km": null,
      "recent_long_run_km": null
    },
    "race_notes": [
      {
        "distance": "marathon",
        "time": "4:30:00",
        "issue": "补给不足 / 抽筋 / 后半程掉速 / 天气热 / 未说明"
      }
    ],
    "injury_notes": [],
    "preferences": []
  },
  "should_update": true,
  "update_summary": "一句话说明抽取到了什么；没有抽取到则为空字符串"
}

规则：
- “半马140”规范化为 "1:40:00"；“全马430”规范化为 "4:30:00"。
- “半马1:40”“全马4小时30”也规范化为 HH:MM:SS。
- 如果用户只是提问，没有提供明确个人资料，should_update=false。
- 不要抽取医疗诊断。疼痛、伤病历史只放 injury_notes 的自然语言备注。
- profile_patch 中没有值的字段保留 null 或空数组。
""".strip()


def _clean_scalar(value: Any) -> Any:
    if value in ("", None, [], {}):
        return None
    return value


def _merge_dict(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(existing)
    for key, value in patch.items():
        if isinstance(value, dict):
            current = merged.get(key)
            if not isinstance(current, dict):
                current = {}
            merged[key] = _merge_dict(current, value)
            continue

        cleaned = _clean_scalar(value)
        if cleaned is not None:
            merged[key] = cleaned
    return merged


def _append_unique(
    existing_items: list[Any],
    patch_items: list[Any],
    limit: int = 20,
) -> list[Any]:
    items = list(existing_items)
    seen = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in items}
    for item in patch_items:
        if item in (None, "", [], {}):
            continue
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        items.append(item)
        seen.add(key)
    return items[-limit:]


def _normalize_patch(raw_patch: Any) -> dict[str, Any]:
    if not isinstance(raw_patch, dict):
        return {}

    normalized: dict[str, Any] = {}
    for key in ("body_metrics", "current_times", "training_context"):
        value = raw_patch.get(key)
        if isinstance(value, dict):
            cleaned = {
                item_key: item_value
                for item_key, item_value in value.items()
                if _clean_scalar(item_value) is not None
            }
            if cleaned:
                normalized[key] = cleaned

    for key in ("goals", "race_notes", "injury_notes", "preferences"):
        value = raw_patch.get(key)
        if isinstance(value, list):
            cleaned_items = [item for item in value if _clean_scalar(item) is not None]
            if cleaned_items:
                normalized[key] = cleaned_items

    return normalized


def _merge_profile(existing_profile: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = _merge_dict(existing_profile, patch)
    for key in ("goals", "race_notes", "injury_notes", "preferences"):
        existing_items = existing_profile.get(key, [])
        patch_items = patch.get(key, [])
        if isinstance(existing_items, list) and isinstance(patch_items, list):
            merged[key] = _append_unique(existing_items, patch_items)
    return merged


def read_athlete_profile() -> dict[str, Any]:
    """读取当前的跑步长期档案。"""
    agent_memory = get_agent_memory(AGENT_NAME)
    profile = agent_memory.get("athlete_profile", {})
    return profile if isinstance(profile, dict) else {}


def apply_profile_patch(raw_patch: dict[str, Any]) -> str:
    """把一份档案补丁合并进长期记忆。

    供 save_running_profile 工具调用，由模型决定什么时候写。
    """
    patch = _normalize_patch(raw_patch)
    if not patch:
        return "没有可写入的内容。"

    updated_profile = _merge_profile(read_athlete_profile(), patch)
    update_agent_memory(AGENT_NAME, {"athlete_profile": updated_profile})
    return f"已写入长期记忆：{', '.join(sorted(patch))}"


async def update_running_profile_from_message(message: str) -> str:
    extraction = await complete_json(
        PROFILE_EXTRACT_PROMPT,
        f"用户消息：\n{message}",
    )
    if not extraction.get("should_update"):
        return ""

    patch = _normalize_patch(extraction.get("profile_patch"))
    if not patch:
        return ""

    agent_memory = get_agent_memory(AGENT_NAME)
    existing_profile = agent_memory.get("athlete_profile", {})
    if not isinstance(existing_profile, dict):
        existing_profile = {}

    updated_profile = _merge_profile(existing_profile, patch)
    update_agent_memory(AGENT_NAME, {"athlete_profile": updated_profile})

    summary = extraction.get("update_summary")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return "已更新跑步长期记忆。"
