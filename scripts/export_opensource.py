"""把跑步那部分抽成一个可以公开的独立项目。

**为什么是脚本而不是手工复制一份。**

手工分叉出去的开源版会立刻开始漂移：主仓库修了 bug，开源版还是旧的；
开源版收了 PR，主仓库不知道。几个月之后两边就没法互相同步了。

写成脚本之后，「开源版」不是一份拷贝，而是**主仓库的一个投影**：
主仓库有了新功能，重跑一次这个脚本就带过去了。
开源版独有的东西（README、LICENSE、.env.example）放在 `opensource/` 里，
作为覆盖层贴上去——**这一层是唯一需要手工维护的部分**。

**排除什么，比包含什么更重要。**

三类东西绝对不能出去：

1. **密钥**：`.env`、`config.toml`（B 站 Cookie）——靠白名单而不是黑名单，
   只有明确列出的路径会被复制。
2. **个人数据**：`data/` 整个目录。里面有体重、伤病、比赛成绩、聊天记录。
3. **版权内容**：`data/knowledge/*/books/` 下是买来的电子书，
   把它们连同抽出来的 chunks 一起发出去等于在分发盗版。

复制完之后还会**再扫一遍产物**（见 `scan_for_secrets`）。白名单已经挡住了
绝大部分，但扫描挡的是另一类错误：**某天有人往一个已经在白名单里的文件里
写了一个密钥**。白名单是按路径判断的，它看不见文件内容变化。

用法：

    uv run python scripts/export_opensource.py --out ../coros-running-agent
    uv run python scripts/export_opensource.py --out /tmp/check --dry-run
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
OVERLAY_DIR = ROOT_DIR / "opensource"

# 白名单：只有这些路径会被复制过去。
# 用白名单而不是「复制全部再删掉几个」——**漏删一个的后果远大于漏加一个**。
INCLUDE: tuple[str, ...] = (
    "src",
    "agents/__init__.py",
    "agents/coros_report",
    "scripts/ingest_books.py",
    "scripts/sync_bilibili.py",
    "scripts/archive_all_fit.py",
    "scripts/inspect_chunks.py",
    "evals",
    "web",
    "deploy",
    "assets/brand",
    "docs/rag-pipeline.md",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
)

# 即使落在白名单目录里，这些也要跳过
EXCLUDE_NAMES = frozenset({"__pycache__", ".DS_Store", ".pytest_cache"})
EXCLUDE_SUFFIXES = (".pyc", ".toml.local")
# config.toml 里是 B 站的 sessdata / bili_jct，主仓库靠 .gitignore 挡着，
# 这里要再挡一次——**别人的仓库没有你的 .gitignore**。
EXCLUDE_FILES = frozenset({"config.toml", ".env"})

# 只跑步那一套，所以这两个能力包不导出。registry 是扫描目录的，
# 目录不在，能力就不在，不需要改任何代码（见 src/registry.py）。
DROPPED_CAPABILITIES = ("kitchen_assistant", "photo_memory")

# 这些工具跟着上面两个能力一起消失，评测里的期望值要相应去掉
DROPPED_TOOLS = frozenset({"kitchen", "photo", "list_races"})


def iter_source_files() -> list[Path]:
    """按白名单列出要复制的文件，返回相对 ROOT_DIR 的路径。"""
    found: list[Path] = []
    for entry in INCLUDE:
        path = ROOT_DIR / entry
        if not path.exists():
            print(f"  ! 白名单里的 {entry} 不存在，跳过")
            continue
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            parts = set(candidate.relative_to(ROOT_DIR).parts)
            if parts & EXCLUDE_NAMES:
                continue
            if candidate.name in EXCLUDE_FILES:
                continue
            if candidate.name.endswith(EXCLUDE_SUFFIXES):
                continue
            found.append(candidate.relative_to(ROOT_DIR))
    return found


def rewrite_routing_dataset(data: list[dict]) -> list[dict]:
    """路由评测：去掉厨房/照片的用例，并从工具表期望里摘掉消失的工具。

    这里**不能**改成「跑一遍实际的工具表再写回去」——那样评测就变成了
    自己证明自己，永远不会红。期望值必须是手写的，只是按删掉的能力做减法。
    """
    kept = []
    for case in data:
        command = (case.get("expected_route") or {}).get("command")
        if case.get("channel") == "cooking" or command in {"kitchen", "photo"}:
            continue
        if "expect_tools" in case:
            case = dict(case)
            case["expect_tools"] = [
                tool for tool in case["expect_tools"] if tool not in DROPPED_TOOLS
            ]
        kept.append(case)
    return kept


def rewrite_trajectory_dataset(data: list[dict]) -> list[dict]:
    """真实模型轨迹评测：丢掉依赖已删能力的用例。"""
    kept = []
    for case in data:
        expected = set(case.get("must_call_any", []))
        if expected & DROPPED_TOOLS:
            continue
        case = dict(case)
        case["must_not_call"] = [
            tool for tool in case.get("must_not_call", []) if tool not in DROPPED_TOOLS
        ]
        kept.append(case)
    return kept


def rewrite_runner(text: str) -> str:
    """把 photo_memory 那一套从总入口里摘掉。"""
    block = """    print()
    photo_metrics, photo_results, photo_spec = run_photo_memory_suite()
    passed = (
        _print_suite_result("photo_memory", photo_metrics, photo_results, photo_spec)
        and passed
    )

"""
    return text.replace(block, "")


def transform(relative: Path, raw: bytes) -> bytes | None:
    """按需要改写单个文件。返回 None 表示这个文件不导出。"""
    name = relative.as_posix()

    if name == "pyproject.toml":
        text = raw.decode("utf-8")
        text = text.replace('name = "myminions"', 'name = "coros-running-agent"')
        text = text.replace(
            'description = "Personal multi-capability agent runtime."',
            'description = "Self-hosted COROS running coach agent."',
        )
        return text.encode("utf-8")

    if name in {
        "evals/datasets/photo_memory.json",
        "evals/judges/photo_memory.py",
        "evals/specs/photo_memory.json",
    }:
        return None

    if name == "evals/datasets/natural_language_routing.json":
        data = rewrite_routing_dataset(json.loads(raw.decode("utf-8")))
        return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    if name == "evals/datasets/agent_trajectory.json":
        data = rewrite_trajectory_dataset(json.loads(raw.decode("utf-8")))
        return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    if name == "evals/run_evals.py":
        text = rewrite_runner(raw.decode("utf-8"))
        text = text.replace(
            "def run_photo_memory_suite",
            "def _unused_run_photo_memory_suite",
        )
        return text.encode("utf-8")

    return raw


# 扫描产物用的规则。写得保守——**宁可误报**，因为误报的代价是人看一眼，
# 漏报的代价是密钥进了公开仓库。
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"sk-[A-Za-z0-9]{20,}", "疑似 API key"),
    (r"tvly-[A-Za-z0-9_-]{10,}", "疑似 Tavily key"),
    (r"\b\d{1,3}(?:\.\d{1,3}){3}\b", "疑似服务器 IP"),
    (r"SESSDATA\s*=\s*[A-Za-z0-9%_-]{10,}", "疑似 B 站 Cookie"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "疑似邮箱"),
)

# 明确允许的例外：评测用的假密钥、文档里的示例、本地回环地址
SECRET_ALLOWLIST = (
    "sk-testkey1234567890",
    "sk-your-key-here",
    "127.0.0.1",
    "0.0.0.0",
    "255.255.255.255",
    "1.2.3.4",
    "tvly-your-key-here",
)


def scan_for_secrets(root: Path) -> list[str]:
    """扫描产物里的疑似密钥和个人信息，返回发现列表。"""
    findings: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or set(path.relative_to(root).parts) & EXCLUDE_NAMES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # 二进制文件（图片等）跳过
        for line_no, line in enumerate(text.splitlines(), 1):
            for pattern, label in SECRET_PATTERNS:
                for match in re.findall(pattern, line):
                    if any(allowed in match for allowed in SECRET_ALLOWLIST):
                        continue
                    findings.append(
                        f"{path.relative_to(root)}:{line_no} {label}: {match[:60]}"
                    )
    return findings


def export(out_dir: Path, dry_run: bool) -> int:
    files = iter_source_files()
    print(f"白名单命中 {len(files)} 个文件")

    if dry_run:
        for relative in files[:20]:
            print(f"  {relative}")
        print(f"  ... 共 {len(files)} 个")
        return 0

    if out_dir.exists():
        # 只清代码，保留目标仓库的 .git —— 否则每次导出都会把历史删掉
        for child in out_dir.iterdir():
            if child.name == ".git":
                continue
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for relative in files:
        content = transform(relative, (ROOT_DIR / relative).read_bytes())
        if content is None:
            continue
        target = out_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written += 1

    for relative in DROPPED_CAPABILITIES:
        assert not (out_dir / "agents" / relative).exists(), f"{relative} 不该被导出"

    overlay = 0
    if OVERLAY_DIR.exists():
        for path in sorted(OVERLAY_DIR.rglob("*")):
            if not path.is_file() or set(path.relative_to(OVERLAY_DIR).parts) & EXCLUDE_NAMES:
                continue
            target = out_dir / path.relative_to(OVERLAY_DIR)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            overlay += 1

    print(f"复制 {written} 个文件，覆盖层 {overlay} 个 → {out_dir}")

    findings = scan_for_secrets(out_dir)
    if findings:
        print("\n扫描发现可疑内容，导出的内容需要人工确认后再公开：")
        for item in findings[:40]:
            print(f"  {item}")
        print(f"  共 {len(findings)} 处")
        return 1

    print("密钥扫描通过：没有发现 API key、服务器地址、Cookie 或邮箱。")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="导出目录")
    parser.add_argument("--dry-run", action="store_true", help="只列出会复制什么")
    args = parser.parse_args()
    sys.exit(export(Path(args.out).expanduser().resolve(), args.dry_run))


if __name__ == "__main__":
    main()
