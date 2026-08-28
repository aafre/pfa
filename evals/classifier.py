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
        if not exact:
            errors.append(
                {
                    "transaction": case["description"],
                    "expected": {"kind": case["kind"], "category": case["category"]},
                    "actual": {"kind": actual_kind, "category": actual_category},
                    "confidence": result.confidence if result else None,
                    "reason": result.reason if result else "no valid model classification",
                }
            )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    total = len(cases)
    print(
        json.dumps(
            {
                "status": "completed",
                "model": settings.model,
                "dataset_sha256": hashlib.sha256(raw_dataset.encode()).hexdigest(),
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
