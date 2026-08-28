"""报告主仓库和开源版之间的漂移，供逐条移植。

**为什么不是「一键覆盖」。**

最初的想法是把开源版做成主仓库的投影：跑一次脚本就整体覆盖过去。
实际发布之后发现这个假设是错的——开源版有**真实的、不该被冲掉的分叉**：

- `flow_map.py` 多了 `observation` / `llm` / `reflection` 等展示节点，
  少了 `races` / `kitchen`
- README 被扩写过，加了界面截图和「接入其他应用」，去掉了评测那一节
- systemd 单元换了名字，提示词里的例子换成了跑步场景

覆盖式同步会把这些全部抹掉。所以这个脚本**只读、只报告**：
列出两边都有、但内容不一样的文件，我按需要逐条移植。

**同步的是能力，不是文件。** 主仓库修了一个 bug、加了一个机制，
开源版应该拿到同样的**行为**，但落地形式可以不同——
比如主仓库的 `SCOPE_MODULE_ORDER` 含 `races`/`kitchen`，
开源版那两个模块不存在，照抄反而会报错。

用法：

    uv run python scripts/sync_opensource.py
    uv run python scripts/sync_opensource.py --show src/ask.py
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT_DIR.parent / "coros-running-agent"

# 只比这些目录。data/、.env、evals/ 不参与——
# 开源版刻意不带评测，而 data/ 永远不出去。
COMPARE_ROOTS = ("src", "agents/coros_report", "scripts", "web", "deploy")

# 这些文件两边本来就该不一样，不用每次都报出来当噪音
EXPECTED_DIVERGENCE = frozenset(
    {
        "src/runtime/flow_map.py",  # 开源版模块表不同（无 races/kitchen，多展示节点）
        "src/registry.py",          # 文档字符串里的命令例子不同
        "web/index.html",           # 品牌名与截图
        "web/app.js",
        "web/data.html",
        "web/data.js",
        "deploy/systemd/README.md",
        "pyproject.toml",
        "uv.lock",
    }
)


def compare(target: Path, show: str | None) -> int:
    if not target.exists():
        print(f"开源版目录不存在：{target}")
        return 1

    if show:
        source = ROOT_DIR / show
        mirror = target / show
        if not source.exists() or not mirror.exists():
            print(f"{show} 在某一侧不存在")
            return 1
        subprocess.run(["diff", "-u", str(source), str(mirror)])
        return 0

    drifted: list[str] = []
    expected: list[str] = []
    only_here: list[str] = []

    for root in COMPARE_ROOTS:
        base = ROOT_DIR / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.name in {"config.toml", ".DS_Store"} or path.suffix == ".pyc":
                continue
            relative = path.relative_to(ROOT_DIR).as_posix()
            mirror = target / relative
            if not mirror.exists():
                only_here.append(relative)
            elif path.read_bytes() != mirror.read_bytes():
                (expected if relative in EXPECTED_DIVERGENCE else drifted).append(relative)

    if drifted:
        print(f"需要检查的漂移（{len(drifted)} 个）：")
        for item in drifted:
            print(f"  {item}")
        print("\n看具体差异：uv run python scripts/sync_opensource.py --show <路径>")
    else:
        print("共有文件没有意外漂移。")

    if only_here:
        print(f"\n只在主仓库有（{len(only_here)} 个），多半是不该导出的能力：")
        for item in only_here[:15]:
            print(f"  {item}")

    if expected:
        print(f"\n已知的合理差异（{len(expected)} 个），不用管：")
        for item in expected:
            print(f"  {item}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=str(DEFAULT_TARGET), help="开源版仓库路径")
    parser.add_argument("--show", help="打印某个文件的两侧差异")
    args = parser.parse_args()
    sys.exit(compare(Path(args.target).expanduser().resolve(), args.show))


if __name__ == "__main__":
    main()
