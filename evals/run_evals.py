import asyncio
import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from evals.judges.natural_language_routing import (  # noqa: E402
    CaseResult,
    configure_eval_environment,
    judge_case,
    score_results,
)
from src.orchestrator import MainAgentOrchestrator  # noqa: E402


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def run_natural_language_routing_suite() -> tuple[dict[str, float], list[CaseResult], dict[str, Any]]:
    spec = _load_json(ROOT_DIR / "evals" / "specs" / "natural_language_routing.json")
    dataset = _load_json(ROOT_DIR / spec["dataset"])

    configure_eval_environment()
    orchestrator = MainAgentOrchestrator()

    case_results = [judge_case(orchestrator, case) for case in dataset]
    metrics = score_results(case_results)
    return metrics, case_results, spec


def run_rag_retrieval_suite() -> tuple[dict[str, float], list[Any], dict[str, Any], str]:
    """跑真实检索链路。索引缺失时返回跳过原因而不是判失败。

    这里才加载 .env：路由评测靠 configure_eval_environment 构造受控环境，
    提前加载真实配置会污染它。
    """
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")

    from evals.judges import rag_retrieval

    spec = _load_json(ROOT_DIR / "evals" / "specs" / "rag_retrieval.json")
    dataset = _load_json(ROOT_DIR / spec["dataset"])

    available, reason = rag_retrieval.index_available()
    if not available:
        return {}, [], spec, reason

    async def _run() -> list[Any]:
        return [await rag_retrieval.judge_case(case) for case in dataset]

    case_results = asyncio.run(_run())
    metrics = rag_retrieval.score_results(case_results, dataset)
    return metrics, case_results, spec, ""


def run_photo_memory_suite() -> tuple[dict[str, float], list[Any], dict[str, Any]]:
    """纯函数评测，不需要索引也不需要外部服务，任何环境都能跑。"""
    from evals.judges import photo_memory

    spec = _load_json(ROOT_DIR / "evals" / "specs" / "photo_memory.json")
    dataset = _load_json(ROOT_DIR / spec["dataset"])

    extraction = [photo_memory.judge_extraction(case) for case in dataset["extraction"]]
    search = [
        photo_memory.judge_search(case, dataset["fixtures"])
        for case in dataset["search"]
    ]
    merge = [
        photo_memory.judge_merge(case, dataset["fixtures"])
        for case in dataset.get("merge", [])
    ]
    intent_guard = [
        photo_memory.judge_intent_guard(case, dataset["fixtures"])
        for case in dataset.get("intent_guard", [])
    ]
    metrics = photo_memory.score_results(extraction, search, merge, intent_guard)
    return metrics, extraction + search + merge + intent_guard, spec


def _metric_threshold(spec: dict[str, Any], metric_name: str) -> float:
    return float(spec["metrics"][metric_name]["threshold"])


def run_conversation_persistence_suite() -> tuple[dict[str, float], list[Any], dict[str, Any]]:
    """纯本地评测：临时目录 + 桩压缩，不调模型也不碰真实 data/。"""
    from evals.judges import conversation_persistence

    spec = _load_json(ROOT_DIR / "evals" / "specs" / "conversation_persistence.json")
    dataset = _load_json(ROOT_DIR / spec["dataset"])

    results = [conversation_persistence.judge_case(case) for case in dataset]
    metrics = conversation_persistence.score_results(results)
    return metrics, results, spec


def run_prompt_injection_suite() -> tuple[dict[str, float], list[Any], dict[str, Any]]:
    """纯本地评测：假模型驱动真实循环，不调外部服务。"""
    from evals.judges import prompt_injection

    spec = _load_json(ROOT_DIR / "evals" / "specs" / "prompt_injection.json")
    dataset = _load_json(ROOT_DIR / spec["dataset"])

    defang = [prompt_injection.judge_defang(c) for c in dataset["defang"]]
    gate = [prompt_injection.judge_write_gate(c) for c in dataset["write_gate"]]
    output = [prompt_injection.judge_output_guard(c) for c in dataset.get("output_guard", [])]
    rate = [prompt_injection.judge_rate_limit(c) for c in dataset.get("rate_limit", [])]
    metrics = prompt_injection.score_results(defang, gate, output, rate)
    return metrics, defang + gate + output + rate, spec


def _print_suite_result(
    suite_name: str,
    metrics: dict[str, float],
    case_results: list[CaseResult],
    spec: dict[str, Any],
) -> bool:
    print(f"Suite: {suite_name}")
    print(f"Cases: {sum(result.passed for result in case_results)}/{len(case_results)} passed")

    suite_passed = True
    for metric_name, value in metrics.items():
        threshold = _metric_threshold(spec, metric_name)
        metric_passed = value >= threshold
        suite_passed = suite_passed and metric_passed
        status = "PASS" if metric_passed else "FAIL"
        print(f"- {metric_name}: {value:.2f} >= {threshold:.2f} {status}")

    failures = [result for result in case_results if not result.passed]
    if failures:
        print("Failures:")
        for failure in failures:
            print(f"- {failure.case_id}")
            print(f"  expected: {failure.expected}")
            print(f"  actual:   {failure.actual}")

    return suite_passed


def main() -> None:
    metrics, case_results, spec = run_natural_language_routing_suite()
    passed = _print_suite_result("natural_language_routing", metrics, case_results, spec)

    print()
    photo_metrics, photo_results, photo_spec = run_photo_memory_suite()
    passed = (
        _print_suite_result("photo_memory", photo_metrics, photo_results, photo_spec)
        and passed
    )

    print()
    conv_metrics, conv_results, conv_spec = run_conversation_persistence_suite()
    passed = (
        _print_suite_result(
            "conversation_persistence", conv_metrics, conv_results, conv_spec
        )
        and passed
    )

    print()
    inj_metrics, inj_results, inj_spec = run_prompt_injection_suite()
    passed = (
        _print_suite_result("prompt_injection", inj_metrics, inj_results, inj_spec)
        and passed
    )

    print()
    rag_metrics, rag_results, rag_spec, skip_reason = run_rag_retrieval_suite()
    if skip_reason:
        print("Suite: rag_retrieval")
        print(f"SKIPPED: {skip_reason}")
    else:
        from evals.judges import rag_retrieval

        print(f"Retrieval mode: {rag_retrieval.retrieval_mode()}")
        passed = (
            _print_suite_result("rag_retrieval", rag_metrics, rag_results, rag_spec)
            and passed
        )

    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
