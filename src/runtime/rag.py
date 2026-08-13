import json
import math
import re
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT_DIR / "data" / "knowledge" / "coros-report"
CHUNKS_PATH = KNOWLEDGE_DIR / "chunks.json"
INDEX_PATH = KNOWLEDGE_DIR / "index.json"


def _tokenize(text: str) -> list[str]:
    lowered = text.lower()
    english = re.findall(r"[a-z0-9]+", lowered)
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    return english + chinese


def _load_chunks() -> list[dict[str, Any]]:
    if not CHUNKS_PATH.exists():
        return []
    return json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))


def _load_index() -> dict[str, Any]:
    if not INDEX_PATH.exists():
        return {}
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


def search_knowledge(query: str, limit: int = 5) -> list[dict[str, Any]]:
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

        if score > 0:
            scored.append((score, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [chunk for _, chunk in scored[:limit]]


def format_context(chunks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        lines.append(f"[{index}] {chunk['source']} p.{chunk['page']}\n{chunk['text']}")
    return "\n\n".join(lines)
