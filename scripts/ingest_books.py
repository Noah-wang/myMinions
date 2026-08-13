import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from pypdf import PdfReader
from dotenv import load_dotenv

from src.runtime.embeddings import embed_texts, embedding_configured, get_embedding_model


KNOWLEDGE_DIR = ROOT_DIR / "data" / "knowledge" / "coros-report"
BOOKS_DIR = KNOWLEDGE_DIR / "books"
CHUNKS_PATH = KNOWLEDGE_DIR / "chunks.json"
INDEX_PATH = KNOWLEDGE_DIR / "index.json"
EMBEDDINGS_PATH = KNOWLEDGE_DIR / "embeddings.json"

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180
DEFAULT_EMBEDDING_BATCH_SIZE = 20


def normalize_text(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_pdf(path: Path) -> list[dict[str, Any]]:
    reader = PdfReader(path)
    pages: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = normalize_text(text)
        if text:
            pages.append({"page": index, "text": text})
    return pages


def chunk_page(book_name: str, page: int, text: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk_text = text[start:end].strip()
        if chunk_text:
            chunks.append(
                {
                    "id": f"{book_name}:p{page}:c{len(chunks) + 1}",
                    "source": book_name,
                    "page": page,
                    "text": chunk_text,
                }
            )
        if end == len(text):
            break
        start = max(0, end - CHUNK_OVERLAP)
    return chunks


def tokenize(text: str) -> list[str]:
    lowered = text.lower()
    english = re.findall(r"[a-z0-9]+", lowered)
    chinese = []
    for sequence in re.findall(r"[\u4e00-\u9fff]+", lowered):
        for size in (2, 3, 4):
            chinese.extend(
                sequence[index : index + size]
                for index in range(0, max(len(sequence) - size + 1, 0))
            )
    return english + chinese


async def write_embeddings(chunks: list[dict[str, Any]]) -> None:
    batch_size = int(os.getenv("EMBEDDING_BATCH_SIZE", DEFAULT_EMBEDDING_BATCH_SIZE))
    items: list[dict[str, Any]] = []
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        vectors = await embed_texts([chunk["text"] for chunk in batch])
        for chunk, vector in zip(batch, vectors, strict=True):
            items.append({"id": chunk["id"], "embedding": vector})
        print(f"Embedded {len(items)}/{len(chunks)} chunks")

    payload = {
        "model": get_embedding_model(),
        "chunk_count": len(chunks),
        "items": items,
    }
    EMBEDDINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main() -> None:
    load_dotenv(ROOT_DIR / ".env")
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)

    chunks: list[dict[str, Any]] = []
    for pdf_path in sorted(BOOKS_DIR.glob("*.pdf")):
        pages = extract_pdf(pdf_path)
        for page in pages:
            chunks.extend(chunk_page(pdf_path.name, page["page"], page["text"]))

    index = {
        "chunk_count": len(chunks),
        "sources": sorted({chunk["source"] for chunk in chunks}),
        "tokens": {chunk["id"]: tokenize(chunk["text"]) for chunk in chunks},
    }

    CHUNKS_PATH.write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"Wrote {len(chunks)} chunks to {CHUNKS_PATH}")
    for source in index["sources"]:
        print(f"- {source}")

    if embedding_configured():
        asyncio.run(write_embeddings(chunks))
        print(f"Wrote embeddings to {EMBEDDINGS_PATH}")
    else:
        print("Skipped embeddings because EMBEDDING_API_KEY is not set.")


if __name__ == "__main__":
    main()
