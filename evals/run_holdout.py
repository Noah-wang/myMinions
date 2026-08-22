"""留出集：只在重大决策时跑一次的干净尺子。

**这个脚本刻意不接进 `run_evals.py`。**

留出集的价值完全来自「它从来没有参与过任何决定」。一旦你开始每次改动都跑它、
并且照着结果调参数，它就退化成了第二个训练集，和 rag_retrieval.json 没有区别。

它的建立过程说明了这一点：k=4 在调过参的那 30 道题上是完美的饱和点
（hit@k 0.97 → 1.00），而在这 26 道未见题目上，k=3 和 k=4 **一模一样**。
那个「饱和点」是那 30 道题的性质，不是语料的性质。

用法：
- 重大改动（换切片策略、换嵌入模型、加 reranker）之后跑一次做最终验收
- 跑完不要照着失败用例调参数——那样这把尺子就废了
- 真需要反复迭代时，先写一批新题目，把这批降级成普通评测集

题目的构造纪律（见 build 过程）：
1. 只取现有 30 题没问过的主题
2. 题目必须改写措辞，**不能包含标准答案的关键词**——否则检索退化成字面匹配，
   分数虚高。建立时有 3 道题因此被剔重写。
3. 标准答案的关键词必须真的出现在语料里，否则那道题永远不可能通过，
   那是题目的缺陷而不是检索的缺陷。
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "evals" / "judges"))
sys.path.insert(0, str(ROOT_DIR / "agents" / "coros-report" / "agent"))

from dotenv import load_dotenv  # noqa: E402

# 必须先加载 .env 再 import 检索模块。少了这一步，嵌入未配置，
# search_knowledge 会**静默**退回 BM25 关键词检索——分数照样出来，
# 只是量的是另一套系统。第一次跑这个脚本就踩了：hit@1 从 0.85 掉到 0.58，
# 差点被当成留出集上的真实表现。
load_dotenv(ROOT_DIR / ".env")

import rag_retrieval  # noqa: E402

from src.runtime.rag import DEFAULT_TOP_K, search_knowledge  # noqa: E402

DATASET = ROOT_DIR / "evals" / "datasets" / "rag_retrieval_holdout.json"


async def main() -> None:
    cases = json.loads(DATASET.read_text(encoding="utf-8"))
    ranks: list[tuple[str, int | None]] = []

    for case in cases:
        chunks = await search_knowledge(case["question"], limit=DEFAULT_TOP_K)
        rank = None
        for position, chunk in enumerate(chunks, start=1):
            if rag_retrieval._matches(chunk, case):
                rank = position
                break
        ranks.append((case["id"], rank))

    total = len(ranks)
    hit = sum(1 for _, r in ranks if r) / total
    hit1 = sum(1 for _, r in ranks if r == 1) / total
    mrr = sum(1 / r for _, r in ranks if r) / total
    missed = [case_id for case_id, r in ranks if not r]

    # 检索模式必须打出来。静默退化是这个项目踩过不止一次的坑：
    # 指标看着正常，量的却是另一套系统。
    print(f"检索模式: {rag_retrieval.retrieval_mode()}")
    print(f"留出集 {total} 题 · k={DEFAULT_TOP_K}")
    print(f"  hit@k {hit:.2f}   hit@1 {hit1:.2f}   MRR {mrr:.2f}")
    if missed:
        print(f"  漏掉: {missed}")
    print()
    print("提醒：不要照着上面的失败用例调参数。这把尺子的价值来自它没被用过。")


if __name__ == "__main__":
    asyncio.run(main())
