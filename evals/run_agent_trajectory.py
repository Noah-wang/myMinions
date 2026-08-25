"""跑主 Agent 轨迹评测。**这一套真的调模型，会花钱。**

所以它刻意不接进 `run_evals.py`——那五套要能随时跑、离线跑、不产生费用。
这一套的定位是：改了提示词、工具表或循环逻辑之后，部署前跑一次。

```bash
uv run python evals/run_agent_trajectory.py            # 每个用例跑一次
uv run python evals/run_agent_trajectory.py --repeat 3 # 跑三次，区分偶发和真退化
```

`--repeat` 是用来判断「红了是噪声还是真坏了」的。模型有随机性，
单次跑挂不能说明问题；同一个用例三次挂两次，那才是信号。
"""

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv  # noqa: E402

# 必须在 import 之前加载。少了这一步模型和搜索都没配置，
# 跑出来的是另一套系统的行为——这个坑在 run_holdout 上踩过一次。
load_dotenv(ROOT_DIR / ".env")

from evals.judges import agent_trajectory  # noqa: E402

from src.orchestrator import MainAgentOrchestrator  # noqa: E402

SPEC_PATH = ROOT_DIR / "evals" / "specs" / "agent_trajectory.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1, help="每个用例重复几次")
    parser.add_argument("--only", type=str, default="", help="只跑 id 包含这个字符串的用例")
    args = parser.parse_args()

    spec = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    dataset = json.loads((ROOT_DIR / spec["dataset"]).read_text(encoding="utf-8"))
    if args.only:
        dataset = [case for case in dataset if args.only in case["id"]]

    orchestrator = MainAgentOrchestrator()
    threshold = spec["metrics"]["tool_choice_accuracy"]["threshold"]

    print(f"主 Agent 轨迹评测 · {len(dataset)} 个用例 × {args.repeat} 次")
    print("（这一套会真的调用模型）\n")

    results = []
    for case in dataset:
        result = agent_trajectory.judge_case(orchestrator, case, args.repeat)
        results.append(result)
        rate = result.actual["pass_rate"]
        mark = "OK  " if result.passed else "FAIL"
        print(f"  {mark} {case['id']:<34} 通过率 {rate:.2f}")
        if not result.passed:
            for run in result.actual["runs"]:
                if run["problems"]:
                    print(f"        调用了 {run['called']}")
                    for problem in run["problems"]:
                        print(f"        · {problem}")

    metrics = agent_trajectory.score_results(results)
    score = metrics["tool_choice_accuracy"]
    ok = score >= threshold
    print()
    print(f"tool_choice_accuracy: {score:.2f} >= {threshold} {'PASS' if ok else 'FAIL'}")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
