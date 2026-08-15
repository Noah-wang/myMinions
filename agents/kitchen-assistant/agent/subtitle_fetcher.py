import asyncio
import os
from pathlib import Path


AGENT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = AGENT_DIR / "config.toml"


def bilibili_config_path() -> Path:
    configured = os.getenv("BILIBILI_SUBTITLE_CONFIG")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_CONFIG_PATH


async def fetch_bilibili_subtitle(video_input: str) -> str:
    config_path = bilibili_config_path()
    if not config_path.exists():
        raise RuntimeError(f"Bilibili config not found: {config_path}")

    process = await asyncio.create_subprocess_exec(
        "bilibili-subtitle-fetch",
        "fetch",
        video_input,
        "--config",
        str(config_path),
        "--output-format",
        "text",
        "--no-clipboard",
        "--no-asr",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()

    output = stdout.decode("utf-8", errors="replace").strip()
    error = stderr.decode("utf-8", errors="replace").strip()

    if process.returncode != 0:
        raise RuntimeError(error or output or "Bilibili subtitle fetch failed.")

    if not output:
        raise RuntimeError("Bilibili subtitle fetch returned empty text.")

    return output
