from __future__ import annotations

import json
import time
from pathlib import Path

from pfa.ai.agents.categorizer import LocalTransactionClassifier
from pfa.config import get_settings


def main() -> None:
    cases = [
        json.loads(line)
        for line in Path(__file__).with_name("classifier.jsonl").read_text().splitlines()
        if line
    ]
    classifier = LocalTransactionClassifier(get_settings())
    kind_correct = category_correct = exact_correct = 0
    errors: list[dict[str, object]] = []
    started = time.perf_counter()
    for case in cases:
        result = classifier.classify(str(case["description"]), 1000)
        actual_kind = result.kind.value if result else "unknown"
        actual_category = result.category.value if result and result.category else None
        kind_correct += actual_kind == case["kind"]
        category_correct += actual_category == case["category"]
        exact = actual_kind == case["kind"] and actual_category == case["category"]
        exact_correct += exact
        if not exact:
            errors.append(
                {"expected": case, "actual": {"kind": actual_kind, "category": actual_category}}
            )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    total = len(cases)
    print(
        json.dumps(
            {
                "cases": total,
                "kind_accuracy": kind_correct / total,
                "category_accuracy": category_correct / total,
                "exact_accuracy": exact_correct / total,
                "latency_ms": elapsed_ms,
                "errors": errors,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
