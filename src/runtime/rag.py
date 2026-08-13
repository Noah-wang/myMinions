import json
import math
import re
from pathlib import Path
from typing import Any

from src.runtime.embeddings import embed_texts, embedding_configured, get_embedding_model


ROOT_DIR = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT_DIR / "data" / "knowledge" / "coros-report"
RAG_DIR = KNOWLEDGE_DIR
CHUNKS_PATH = KNOWLEDGE_DIR / "chunks.json"
INDEX_PATH = KNOWLEDGE_DIR / "index.json"
EMBEDDINGS_PATH = KNOWLEDGE_DIR / "embeddings.json"


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    english = re.findall(r"[a-z0-9]+", lowered)
    chinese = []
    for sequence in re.findall(r"[\u4e00-\u9fff]+", lowered):
        for size in (2, 3, 4):
            chinese.extend(
                sequence[index : index + size]
                for index in range(0, max(len(sequence) - size + 1, 0))
            )

    synonyms = {
        "阈值": ["threshold", "tempo", "lactate", "乳酸", "门槛"],
        "阈值跑": ["threshold", "tempo", "lactate", "乳酸", "门槛", "门槛跑"],
        "节奏": ["tempo", "threshold"],
        "节奏跑": ["tempo", "threshold"],
        "轻松": ["easy", "recovery"],
        "轻松跑": ["easy", "recovery"],
        "恢复": ["recovery", "easy"],
        "间歇": ["interval", "repetition"],
        "配速": ["pace"],
        "心率": ["heart", "rate"],
        "马拉松": ["marathon"],
    }
    expanded = []
    for term, related_tokens in synonyms.items():
        if term in lowered:
            expanded.extend(related_tokens)

    return english + chinese + expanded


def _load_chunks() -> list[dict[str, Any]]:
    if not CHUNKS_PATH.exists():
        return []
    return json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))


def _load_index() -> dict[str, Any]:
    if not INDEX_PATH.exists():
        return {}
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def _load_embeddings() -> dict[str, Any]:
    if not EMBEDDINGS_PATH.exists():
        return {}
    return json.loads(EMBEDDINGS_PATH.read_text(encoding="utf-8"))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for left_value, right_value in zip(left, right, strict=False):
        dot += left_value * right_value
        left_norm += left_value * left_value
        right_norm += right_value * right_value
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def _phrase_boost(query: str, chunk_text: str) -> float:
    boost = 0.0
    phrase_groups = [
        (["阈值", "阈值跑"], ["乳酸门槛", "门槛跑", "t（乳酸门槛）"]),
        (["节奏", "节奏跑"], ["节奏跑", "t配速", "乳酸门槛"]),
        (["轻松", "轻松跑"], ["轻松跑", "e代表轻松跑", "e跑"]),
        (["间歇", "间歇训练"], ["间歇训练", "i（间歇）", "i训练"]),
    ]
    lowered_query = query.lower()
    lowered_chunk = chunk_text.lower()
    for query_terms, chunk_terms in phrase_groups:
        if any(term in lowered_query for term in query_terms) and any(
            term in lowered_chunk for term in chunk_terms
        ):
            boost += 100.0
    return boost


def search_knowledge_keyword(query: str, limit: int = 5) -> list[dict[str, Any]]:
    chunks = _load_chunks()
    index = _load_index()
    token_index = index.get("tokens", {})
    query_tokens = set(_tokenize(query))
    if not chunks or not query_tokens:
        return []

    scored: list[tuple[float, dict[str, Any]]] = []
    total_chunks = max(len(chunks), 1)
    document_frequency: dict[str, int] = {}
    for tokens in token_index.values():
        for token in set(tokens):
            document_frequency[token] = document_frequency.get(token, 0) + 1

    for chunk in chunks:
        chunk_tokens = token_index.get(chunk["id"], _tokenize(chunk["text"]))
        if not chunk_tokens:
            continue

        token_counts: dict[str, int] = {}
        for token in chunk_tokens:
            token_counts[token] = token_counts.get(token, 0) + 1

        score = 0.0
        for token in query_tokens:
            count = token_counts.get(token, 0)
            if count == 0:
                continue
            df = document_frequency.get(token, 1)
            idf = math.log((total_chunks + 1) / (df + 1)) + 1
            score += count * idf

        score += _phrase_boost(query, chunk["text"])

        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored[:limit]]


async def search_knowledge(query: str, limit: int = 5) -> list[dict[str, Any]]:
    chunks = _load_chunks()
    embeddings = _load_embeddings()
    items = embeddings.get("items", [])
    if not chunks or not items or not embedding_configured():
        return search_knowledge_keyword(query, limit)
    if embeddings.get("model") != get_embedding_model():
        return search_knowledge_keyword(query, limit)

    chunks_by_id = {chunk["id"]: chunk for chunk in chunks}
    vectors_by_id = {
        item["id"]: item["embedding"]
        for item in items
        if item.get("id") in chunks_by_id and item.get("embedding")
    }
    if len(vectors_by_id) != len(chunks):
        return search_knowledge_keyword(query, limit)

    query_vector = (await embed_texts([query]))[0]
    scored = [
        (_cosine_similarity(query_vector, vector), chunks_by_id[chunk_id])
        for chunk_id, vector in vectors_by_id.items()
    ]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored[:limit]]


def format_context(chunks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        lines.append(f"[{index}] {chunk['source']} p.{chunk['page']}\n{chunk['text']}")
    return "\n\n".join(lines)
