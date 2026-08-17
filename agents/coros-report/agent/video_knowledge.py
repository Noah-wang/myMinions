import asyncio
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from src.integrations.bilibili_subtitle import fetch_bilibili_subtitle


ROOT_DIR = Path(__file__).resolve().parents[3]
VIDEOS_DIR = ROOT_DIR / "data" / "knowledge" / "coros-report" / "videos"
INGEST_SCRIPT = ROOT_DIR / "scripts" / "ingest_books.py"


def _safe_slug(value: str) -> str:
    match = re.search(r"BV[a-zA-Z0-9]+", value)
    if match is not None:
        return match.group(0)

    slug = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", value).strip("-")
    return slug[:60] or "running-video"


def _source_path(video_input: str) -> Path:
    slug = _safe_slug(video_input)
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return VIDEOS_DIR / f"{timestamp}-{slug}.md"


def _document_text(video_input: str, subtitle: str) -> str:
    imported_at = datetime.now(UTC).isoformat(timespec="seconds")
    return f"""# Running Video Knowledge

Source: {video_input}
Imported at: {imported_at}
Content type: Bilibili subtitle transcript

## Transcript

{subtitle.strip()}
"""


async def _rebuild_knowledge_index() -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(INGEST_SCRIPT),
        cwd=str(ROOT_DIR),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()
    if process.returncode != 0:
        raise RuntimeError(error or output or "Knowledge ingest failed.")
    return output


async def import_running_video_knowledge(video_input: str) -> str:
    if not video_input.strip():
        return "请提供 B站 BV号或视频链接。"

    subtitle = await fetch_bilibili_subtitle(video_input.strip())
    VIDEOS_DIR.mkdir(parents=True, exist_ok=True)
    path = _source_path(video_input.strip())
    path.write_text(_document_text(video_input.strip(), subtitle), encoding="utf-8")

    ingest_output = await _rebuild_knowledge_index()
    return (
        "已把这个跑步视频导入知识库。\n"
        f"- 保存位置：{path}\n"
        f"- 字幕长度：{len(subtitle)} 字符\n"
        f"- 重建结果：\n{ingest_output}"
    )
