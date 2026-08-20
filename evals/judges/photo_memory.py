"""照片记忆的评分器。

和检索评测不同，这一套是**纯函数**——元数据抽取和照片搜索都不调外部服务，
所以任何环境都能跑，也不需要跳过逻辑。

搜索用夹具而不是真实的 photos.json：评测必须可重复，
不能因为用户又存了几张照片就变绿或变红。
"""

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import photo_intent
import photo_store


@dataclass
class PhotoCaseResult:
    case_id: str
    passed: bool
    expected: dict[str, Any]
    actual: dict[str, Any] = field(default_factory=dict)


def judge_extraction(case: dict[str, Any]) -> PhotoCaseResult:
    """只检查用例声明了的字段，没声明的不管。"""
    text = case["text"]
    expected = case["expect"]
    extractors = {
        "event": photo_store.extract_event,
        "race_date": photo_store.extract_race_date,
        "result": photo_store.extract_result,
    }
    actual = {key: extractors[key](text) for key in expected}
    return PhotoCaseResult(
        case_id=case["id"],
        passed=actual == expected,
        expected=expected,
        actual=actual,
    )


def judge_search(
    case: dict[str, Any],
    fixtures: list[dict[str, Any]],
) -> PhotoCaseResult:
    """按夹具搜索，要求返回的集合和期望**完全一致**。

    只查召回会漏掉这次真正的 bug：原实现返回的是全部照片，
    召回率满分，但精确率崩了。
    """
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "photos.json"
        path.write_text(json.dumps(fixtures, ensure_ascii=False), encoding="utf-8")
        original = photo_store.PHOTOS_PATH
        photo_store.PHOTOS_PATH = path
        try:
            records = photo_store.search_photos(case["query"])
        finally:
            photo_store.PHOTOS_PATH = original

    actual = sorted(str(record.get("event", "")) for record in records)
    expected = sorted(case["expect_events"])
    return PhotoCaseResult(
        case_id=case["id"],
        passed=actual == expected,
        expected={"events": expected},
        actual={"events": actual},
    )


def judge_merge(
    case: dict[str, Any],
    fixtures: list[dict[str, Any]],
) -> PhotoCaseResult:
    """合并会删掉一整组照片，最重要的性质是**一张都不能丢**。"""
    import copy

    with tempfile.TemporaryDirectory() as folder:
        photos = Path(folder) / "photos.json"
        pending = Path(folder) / "pending.json"
        photos.write_text(
            json.dumps(copy.deepcopy(fixtures), ensure_ascii=False), encoding="utf-8"
        )
        pending.write_text("{}", encoding="utf-8")
        original = (photo_store.PHOTOS_PATH, photo_store.PENDING_PATH)
        photo_store.PHOTOS_PATH, photo_store.PENDING_PATH = photos, pending
        try:
            before = sum(len(f.get("files", [])) for f in fixtures)
            message = photo_store.merge_groups(case["source"], case["target"])
            remaining = photo_store._photos()
        finally:
            photo_store.PHOTOS_PATH, photo_store.PENDING_PATH = original

    after = sum(len(record.get("files", [])) for record in remaining)
    target = next((r for r in remaining if r.get("id") == case["target"]), None)

    actual: dict[str, Any] = {
        "groups": sorted(str(r.get("event", "")) for r in remaining),
        "total_photos": after,
        "target_photos": len(target.get("files", [])) if target else 0,
    }
    expected: dict[str, Any] = {
        "groups": sorted(case["expect_groups"]),
        "total_photos": before,
        "target_photos": case.get("expect_target_photos", actual["target_photos"]),
    }
    if "expect_target_result" in case:
        actual["target_result"] = str(target.get("result", "")) if target else ""
        expected["target_result"] = case["expect_target_result"]
    if case.get("expect_error"):
        actual["rejected"] = "找不到" in message or "同一组" in message
        expected["rejected"] = True

    return PhotoCaseResult(
        case_id=case["id"],
        passed=actual == expected,
        expected=expected,
        actual=actual,
    )


def judge_intent_guard(
    case: dict[str, Any],
    fixtures: list[dict[str, Any]],
) -> PhotoCaseResult:
    """意图识别的越权校验。

    这一层是纯函数，跑它不需要调模型——模型输出什么都可能，
    校验层的职责就是把越权的动作拉回来，所以直接喂构造好的输出。
    """
    known_ids = {str(f["id"]) for f in fixtures}
    result = photo_intent._sanitize(
        case["raw"],
        case.get("text", ""),
        case["has_attachments"],
        known_ids,
        case.get("pending_id", ""),
    )
    expected = case["expect"]
    actual = {key: result[key] for key in expected}
    return PhotoCaseResult(
        case_id=case["id"],
        passed=actual == expected,
        expected=expected,
        actual=actual,
    )


def score_results(
    extraction: list[PhotoCaseResult],
    search: list[PhotoCaseResult],
    merge: list[PhotoCaseResult] | None = None,
    intent_guard: list[PhotoCaseResult] | None = None,
) -> dict[str, float]:
    def _rate(results: list[PhotoCaseResult]) -> float:
        return sum(r.passed for r in results) / len(results) if results else 1.0

    return {
        "extraction_accuracy": _rate(extraction),
        "search_exact_match": _rate(search),
        "merge_correctness": _rate(merge or []),
        "intent_guard_accuracy": _rate(intent_guard or []),
    }
