"""检索质量评分器。

和路由评测不同，这个评测**必须打真实的检索链路**：查询要过 embedding，
候选要过真实索引。所以它只能在有 chunks.json + embeddings.json 的环境跑，
索引缺失时整个 suite 跳过而不是判失败。

判定标准不用页码，用"检索回来的块里有没有出现该出现的关键词"。
页码会随分块策略变化而漂移，而分块策略正是这个评测要衡量的东西，
拿会漂移的东西当基准等于没有基准。
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.runtime.embeddings import embedding_configured, get_embedding_model
from src.runtime.rag import (
    CHUNKS_PATH,
    DEFAULT_TOP_K,
    EMBEDDINGS_PATH,
    search_knowledge,
)


# 必须和生产用的条数一致。之前这里写死 5 而生产是 3，
# 导致评测报出的覆盖率比线上实际高一档。
TOP_K = DEFAULT_TOP_K


@dataclass
class RetrievalCaseResult:
    case_id: str
    passed: bool
    rank: int | None
    expected: dict[str, Any]
    actual: dict[str, Any] = field(default_factory=dict)


def index_available() -> tuple[bool, str]:
    if not CHUNKS_PATH.exists():
        return False, "没有 chunks.json，先跑 scripts/ingest_books.py"
    if not EMBEDDINGS_PATH.exists():
        return False, "没有 embeddings.json"
    if not embedding_configured():
        return False, "EMBEDDING_API_KEY 未配置"
    return True, ""


def retrieval_mode() -> str:
    """报告实际走的是向量检索还是关键词兜底。

    rag.py 在向量数量对不上或模型不一致时会静默降级到关键词检索。
    如果不报告这一点，指标好看但可能根本没测到向量链路。
    """
    chunks = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    payload = json.loads(EMBEDDINGS_PATH.read_text(encoding="utf-8"))
    items = payload.get("items", [])
    if payload.get("model") != get_embedding_model():
        return f"关键词兜底（索引模型 {payload.get('model')} 与当前模型不一致）"

    # 父子块之后向量数不再等于块数，要比的是"向量覆盖了多少个父块"。
    # 这个判断必须和 rag.search_knowledge 里的兜底条件保持一致。
    chunk_ids = {chunk["id"] for chunk in chunks}
    covered = {
        (item.get("parent_id") or item.get("id"))
        for item in items
        if item.get("embedding")
    } & chunk_ids
    if len(covered) != len(chunks):
        return f"关键词兜底（向量只覆盖 {len(covered)}/{len(chunks)} 个块）"

    layers = "父子块" if any(item.get("parent_id") for item in items) else "单层块"
    return f"向量检索（{layers}，{len(items)} 条向量 / {len(chunks)} 个块）"


def _chunk_kind(chunk: dict[str, Any]) -> str:
    kind = chunk.get("kind")
    if kind:
        return str(kind)
    return "video" if str(chunk.get("source", "")).endswith(".md") else "book"


def _matches(chunk: dict[str, Any], case: dict[str, Any]) -> bool:
    expected_kind = case.get("expected_kind")
    if expected_kind and _chunk_kind(chunk) != expected_kind:
        return False
    keywords = case.get("must_contain_any", [])
    text = chunk.get("text", "")
    return any(keyword in text for keyword in keywords)


async def judge_case(case: dict[str, Any]) -> RetrievalCaseResult:
    chunks = await search_knowledge(case["question"], limit=TOP_K)

    rank = None
    for position, chunk in enumerate(chunks, start=1):
        if _matches(chunk, case):
            rank = position
            break

    return RetrievalCaseResult(
        case_id=case["id"],
        passed=rank is not None,
        rank=rank,
        expected={
            "kind": case.get("expected_kind"),
            "keywords": case.get("must_contain_any", []),
        },
        actual={
            "top_k": [
                f"{_chunk_kind(c)} {c.get('source', '')[:16]} p.{c.get('page')}"
                for c in chunks
            ]
        },
    )


def judge_category_filter(case: dict[str, Any]) -> RetrievalCaseResult:
    """分类过滤：纯函数，用构造好的块测，不碰真实索引也不调嵌入。

    最危险的失败模式是**过滤过头返回空**——那会让检索静默失效，
    而且看起来像「知识库里没有」。所以未知分类必须退回全部而不是空：
    查得宽最多是结果不够准，返回空是功能直接不可用。
    """
    from src.runtime.rag import filter_by_category

    chunks = [
        {"id": f"c{i}", "category": category}
        for i, category in enumerate(case["categories"])
    ]
    result = filter_by_category(chunks, case["filter"])
    actual = {"kept": [c["category"] for c in result]}
    expected = {"kept": case["expect_kept"]}
    return RetrievalCaseResult(
        case_id=case["id"],
        passed=actual == expected,
        rank=1 if actual == expected else None,
        expected=expected,
        actual=actual,
    )


def score_results(
    results: list[RetrievalCaseResult],
    dataset: list[dict[str, Any]],
) -> dict[str, float]:
    if not results:
        return {}

    kinds = {case["id"]: case.get("expected_kind") for case in dataset}

    def _rate(subset: list[RetrievalCaseResult]) -> float:
        return sum(r.passed for r in subset) / len(subset) if subset else 1.0

    def _hit_at_1(subset: list[RetrievalCaseResult]) -> float:
        return sum(r.rank == 1 for r in subset) / len(subset) if subset else 1.0

    book = [r for r in results if kinds.get(r.case_id) == "book"]
    video = [r for r in results if kinds.get(r.case_id) == "video"]
    pinpoint = [r for r in results if r.case_id.startswith("pinpoint_")]

    return {
        # hit@k 在小语料上很容易饱和到 1.00，只能发现变差、发现不了变好。
        # hit@1 衡量排序精度，才是判断分块粒度好坏的那把尺子。
        "hit_rate_at_k": _rate(results),
        "hit_rate_at_1": _hit_at_1(results),
        "mrr_at_k": sum(1 / r.rank for r in results if r.rank) / len(results),
        "book_hit_rate": _rate(book),
        "video_hit_rate": _rate(video),
        # 定点事实类问题单独出指标：它们对块粒度最敏感，
        # 大块把具体数字埋进一大段论述里，向量会被稀释。
        "pinpoint_hit_rate_at_1": _hit_at_1(pinpoint),
        "pinpoint_hit_rate_at_k": _rate(pinpoint),
    }
