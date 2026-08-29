"""命令行查看知识库分块。

两种用法，回答两个不同的问题：

    体检——「切得好不好？」
        uv run python scripts/inspect_chunks.py
        uv run python scripts/inspect_chunks.py 亚瑟士

    看内容——「到底切成了什么？」
        uv run python scripts/inspect_chunks.py --show 亚瑟士
        uv run python scripts/inspect_chunks.py --show 亚瑟士 --full
        uv run python scripts/inspect_chunks.py --show Daniels --limit 3 --children

体检的逻辑在 src/runtime/knowledge_health.py，和 Agent 的
inspect_knowledge_index 工具共用，保证人看到的和 Agent 看到的是同一份数据。

--show 是纯读，直接读 chunks.json 和 embeddings.json。**父块和子块要分开看**：
父块是投喂给模型的单位，子块是拿去匹配的单位，检索命中的是子块、喂进去的是父块。
只看父块会不明白「为什么这段被检索到了」。
"""

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.runtime.knowledge_health import (  # noqa: E402
    CHUNKS_PATH,
    EMBEDDINGS_PATH,
    format_report,
    inspect_knowledge_index,
)

PREVIEW_CHARS = 300


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def show_chunks(keyword: str, limit: int, full: bool, with_children: bool) -> int:
    chunks = _load(CHUNKS_PATH, [])
    if not chunks:
        print("知识库是空的。先跑 scripts/ingest_books.py。")
        return 1

    needle = keyword.lower()
    hits = [
        c
        for c in chunks
        if needle in str(c.get("source", "")).lower()
        or needle in str(c.get("text", "")).lower()
    ]
    if not hits:
        sources = sorted({str(c.get("source", "")) for c in chunks})
        print(f"没有匹配「{keyword}」的块。现有来源：")
        for name in sources[:20]:
            print(f"  - {name}")
        if len(sources) > 20:
            print(f"  ...共 {len(sources)} 个来源")
        return 1

    # 子块正文**没有落盘**——embeddings.json 只存 id / parent_id / hash / embedding。
    # 所以这里用建索引时的同一个切分函数当场还原，保证看到的和实际匹配的一致。
    children_by_parent: dict[str, list[dict]] = {}
    if with_children:
        from scripts.ingest_books import split_children

        payload = _load(EMBEDDINGS_PATH, {})
        vector_ids = {str(i.get("id")) for i in payload.get("items", [])}
        for chunk in hits[:limit]:
            kids = [k for k in split_children(chunk) if str(k.get("id")) in vector_ids]
            children_by_parent[str(chunk.get("id"))] = kids

    print(f"匹配 {len(hits)} 个块，显示前 {min(limit, len(hits))} 个\n")
    for chunk in hits[:limit]:
        text = str(chunk.get("text", ""))
        body = text if full else text[:PREVIEW_CHARS]
        truncated = "" if full or len(text) <= PREVIEW_CHARS else f"\n… 还有 {len(text) - PREVIEW_CHARS} 字符（--full 看全文）"

        print("=" * 72)
        print(f"id       {chunk.get('id')}")
        print(f"来源     {chunk.get('source')}")
        meta = [f"分类 {chunk.get('category') or '-'}"]
        if chunk.get("uploader"):
            meta.append(f"UP主 {chunk['uploader']}")
        page = chunk.get("page")
        if page is not None:
            end = chunk.get("page_end")
            meta.append(f"页 {page}" + (f"-{end}" if end and end != page else ""))
        meta.append(f"{len(text)} 字符")
        print(f"元信息   {' · '.join(meta)}")

        # context 是导入时加的文档级前缀（标题、UP 主），它**参与嵌入**，
        # 所以它算检索到这一块的原因之一，值得单独看。
        if chunk.get("context"):
            print(f"文档前缀 {chunk['context']}")

        print("-" * 72)
        print(body + truncated)

        if with_children:
            kids = children_by_parent.get(str(chunk.get("id")), [])
            print(f"\n  ↳ 子块 {len(kids)} 个（真正拿去匹配的单位）")
            for kid in kids:
                kid_text = str(kid.get("text", ""))[:120]
                print(f"    [{kid.get('id')}] {kid_text}")
        print()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="查看知识库分块：不带 --show 是体检，带 --show 是看内容"
    )
    parser.add_argument("source", nargs="?", help="体检时只看这个来源关键字")
    parser.add_argument("--show", metavar="关键字", help="按来源或正文关键字打印实际切出来的块")
    parser.add_argument("--limit", type=int, default=5, help="--show 时最多打印几个，默认 5")
    parser.add_argument("--full", action="store_true", help="打印整块，不截断")
    parser.add_argument("--children", action="store_true", help="连子块一起显示")
    args = parser.parse_args()

    if args.show:
        sys.exit(show_chunks(args.show, args.limit, args.full, args.children))

    report = inspect_knowledge_index(args.source)
    print(format_report(report))
    sys.exit(1 if "error" in report else 0)


if __name__ == "__main__":
    main()
