from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import httpx

from pfa.ai.agents.categorizer import LocalTransactionClassifier
from pfa.config import get_settings


def main() -> None:
    dataset = Path(__file__).with_name("classifier.jsonl")
    raw_dataset = dataset.read_text()
    cases = [json.loads(line) for line in raw_dataset.splitlines() if line]
    if not cases or any(
        not case.get("case_id")
        or not case.get("description")
        or not isinstance(case.get("amount_minor"), int)
        for case in cases
    ):
        raise SystemExit(
            "classifier dataset requires case_id, description, and integer amount_minor"
        )
    if len({case["case_id"] for case in cases}) != len(cases):
        raise SystemExit("classifier dataset case_id values must be unique")
    settings = get_settings()
    try:
        response = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=2)
        response.raise_for_status()
        names = {str(item.get("name")) for item in response.json().get("models", [])}
    except Exception:
        names = set()
    if settings.model not in names:
        print(
            json.dumps(
                {
                    "status": "model_unavailable",
                    "model": settings.model,
                    "dataset_sha256": hashlib.sha256(raw_dataset.encode()).hexdigest(),
                    "cases": len(cases),
                },
                indent=2,
            )
        )
        raise SystemExit(2)
    classifier = LocalTransactionClassifier(settings)
    kind_correct = category_correct = exact_correct = 0
    kind_pairs: list[tuple[str, str]] = []
    errors: list[dict[str, object]] = []
    started = time.perf_counter()
    for case in cases:
        result = classifier.classify(str(case["description"]), int(case["amount_minor"]))
        actual_kind = result.kind.value if result else "unknown"
        actual_category = result.category.value if result and result.category else None
        kind_correct += actual_kind == case["kind"]
        category_correct += actual_category == case["category"]
        exact = actual_kind == case["kind"] and actual_category == case["category"]
        exact_correct += exact
        kind_pairs.append((str(case["kind"]), actual_kind))
        if not exact:
            errors.append(
                {
                    "case_id": case["case_id"],
                    "expected": {"kind": case["kind"], "category": case["category"]},
                    "actual": {"kind": actual_kind, "category": actual_category},
                    "confidence": result.confidence if result else None,
                    "reason": result.reason if result else "no valid model classification",
                }
            )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    total = len(cases)
    labels = sorted({actual for pair in kind_pairs for actual in pair})
    confusion = {
        expected: {
            actual: sum(pair == (expected, actual) for pair in kind_pairs) for actual in labels
        }
        for expected in labels
    }
    kind_f1: list[float] = []
    for label in labels:
        true_positive = confusion[label][label]
        false_positive = sum(confusion[other][label] for other in labels if other != label)
        false_negative = sum(confusion[label][other] for other in labels if other != label)
        denominator = 2 * true_positive + false_positive + false_negative
        kind_f1.append(2 * true_positive / denominator if denominator else 0.0)
    print(
        json.dumps(
            {
                "status": "completed",
                "model": settings.model,
                "dataset_sha256": hashlib.sha256(raw_dataset.encode()).hexdigest(),
                "cases": total,
                "kind_accuracy": kind_correct / total,
                "kind_macro_f1": sum(kind_f1) / len(kind_f1) if kind_f1 else 0.0,
                "kind_confusion": confusion,
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
