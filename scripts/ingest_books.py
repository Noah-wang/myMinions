from src.runtime.rag import KNOWLEDGE_DIR
import json
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader


ROOT_DIR = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = ROOT_DIR / "data" / "knowledge" / "coros-report"
BOOKS_DIR = KNOWLEDGE_DIR / "books"
CHUNKS_PATH = KNOWLEDGE_DIR / "chunks.json"
INDEX_PATH = KNOWLEDGE_DIR / "index.json"

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 180


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
    chinese = re.findall(r"[\u4e00-\u9fff]{2,}", lowered)
    return english + chinese


def main() -> None:
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


if __name__ == "__main__":
    main()
