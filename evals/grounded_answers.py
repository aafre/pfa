from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from pydantic_ai import UsageLimits

from pfa.ai.agents.advisor import build_advisor
from pfa.ai.deps import FinanceDependencies
from pfa.ai.models import available_models
from pfa.config import get_settings
from pfa.db.engine import init_db, make_engine, make_session_factory
from pfa.db.models import AccountModel, TransactionModel
from pfa.services.runtime import close_services, open_services


def _seed_database(database_url: str) -> None:
    settings = get_settings().model_copy(update={"database_url": database_url})
    engine = make_engine(settings)
    init_db(engine)
    with make_session_factory(engine)() as session:
        account = AccountModel(name="Eval Current", account_type="current", currency="GBP")
        session.add(account)
        session.flush()
        rows = [
            ("TEST MARKET GROCERIES", 12_345, "debit", "expense", "groceries"),
            ("CAFE TEST", 7_000, "debit", "expense", "eating_out"),
            ("SAMPLE EMPLOYER PAY", 500_000, "credit", "income", None),
        ]
        for index, (description, amount, direction, kind, category) in enumerate(rows, start=1):
            session.add(
                TransactionModel(
                    account_id=account.id,
                    transaction_date=date(2026, 8, index),
                    raw_description=description,
                    normalized_description=description.lower(),
                    merchant="Test Market" if index == 1 else description.title(),
                    amount_minor=amount,
                    flow_direction=direction,
                    currency="GBP",
                    kind=kind,
                    category=category,
                    classification_source="import",
                    import_source="eval:grounded_answers",
                    fingerprint=f"{index:064d}",
                )
            )
        session.commit()
    engine.dispose()


def _load_cases() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("grounded_answers.jsonl")
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    ids = [case.get("case_id") for case in cases]
    if not cases or any(not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("grounded-answer cases require unique case_id values")
    return cases


def main() -> None:
    cases = _load_cases()
    base_settings = get_settings()
    if base_settings.model not in (available_models(base_settings) or set()):
        print(json.dumps({"status": "model_unavailable", "model": base_settings.model}))
        raise SystemExit(2)

    passed = 0
    failures: list[dict[str, str]] = []
    with tempfile.TemporaryDirectory(prefix="pfa-grounded-eval-") as directory:
        database_url = f"sqlite:///{Path(directory, 'eval.db')}"
        _seed_database(database_url)
        settings = base_settings.model_copy(
            update={"database_url": database_url, "upload_dir": Path(directory, "uploads")}
        )
        engine, services = open_services(settings)
        try:
            advisor = build_advisor(settings)
            dependencies = FinanceDependencies(services.analytics, services.planning)
            for case in cases:
                result = advisor.run_sync(
                    case["question"],
                    deps=dependencies,
                    usage_limits=UsageLimits(request_limit=settings.agent_request_limit),
                )
                calls = {
                    str(part.tool_name)
                    for message in result.all_messages()
                    for part in getattr(message, "parts", ())
                    if getattr(part, "tool_name", None)
                }
                answer = str(result.output)
                checks = {
                    "expected_tool": case["expected_tool"] in calls,
                    "expected_fact": case["expected_display"] in answer,
                }
                if all(checks.values()):
                    passed += 1
                else:
                    failures.append(
                        {
                            "case_id": case["case_id"],
                            **{key: "pass" if value else "fail" for key, value in checks.items()},
                        }
                    )
        finally:
            close_services(engine, services)

    total = len(cases)
    print(
        json.dumps(
            {
                "status": "completed",
                "model": base_settings.model,
                "cases": total,
                "passed": passed,
                "pass_rate": passed / total,
                "failures": failures,
            },
            indent=2,
        )
    )
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
