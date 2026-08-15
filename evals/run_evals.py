import json
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "evals" / "judges"))
sys.path.insert(0, str(ROOT_DIR / "agents" / "kitchen-assistant" / "agent"))
sys.path.insert(0, str(ROOT_DIR / "agents" / "coros-report" / "agent"))

from natural_language_routing import (  # noqa: E402
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


def _metric_threshold(spec: dict[str, Any], metric_name: str) -> float:
    return float(spec["metrics"][metric_name]["threshold"])


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
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
