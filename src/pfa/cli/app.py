from __future__ import annotations

from datetime import date
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from pfa.ai.agents.advisor import build_advisor
from pfa.ai.agents.categorizer import LocalTransactionClassifier
from pfa.ai.deps import FinanceDependencies
from pfa.config import get_settings
from pfa.db.engine import init_db, make_engine
from pfa.ingestion.service import ImportService
from pfa.services.health import health_report
from pfa.services.review import monthly_review_evidence
from pfa.services.runtime import close_services, open_services

app = typer.Typer(help="PFA local personal finance intelligence")
db_app = typer.Typer(help="Database commands")
transactions_app = typer.Typer(help="Transaction commands")
summary_app = typer.Typer(help="Summary commands")
budget_app = typer.Typer(help="Budget commands")
goals_app = typer.Typer(help="Goal commands")
app.add_typer(db_app, name="db")
app.add_typer(transactions_app, name="transactions")
app.add_typer(summary_app, name="summary")
app.add_typer(budget_app, name="budget")
app.add_typer(goals_app, name="goals")
console = Console()


def parse_month(value: str | None) -> date:
    if not value:
        today = date.today()
        return today.replace(day=1)
    try:
        year, month = (int(item) for item in value.split("-"))
        return date(year, month, 1)
    except (ValueError, TypeError) as exc:
        raise typer.BadParameter("month must use YYYY-MM") from exc


def print_money(minor: int) -> str:
    return f"£{minor / 100:,.2f}"


@db_app.command("init")
def db_init() -> None:
    settings = get_settings()
    engine = make_engine(settings)
    init_db(engine)
    engine.dispose()
    console.print(f"Database ready: {settings.database_url}")


@db_app.command("migrate")
def db_migrate() -> None:
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    command.upgrade(config, "head")
    console.print("Database migrated")


@app.command("import")
def import_transactions(path: Path, dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    engine, services = open_services(get_settings())
    try:
        importer = ImportService(services.uow, LocalTransactionClassifier(get_settings()))
        report = importer.import_csv(path, dry_run)
        console.print(
            f"Imported {report.imported}; duplicates {report.duplicates}; "
            f"requires classification {report.requires_classification}"
        )
        for error in report.errors:
            console.print(f"[red]{error}[/red]")
        close_services(engine, services, not dry_run)
    except Exception:
        close_services(engine, services, False)
        raise


@summary_app.command("month")
def summary_month(month: str | None = typer.Option(None, "--month")) -> None:
    engine, services = open_services(get_settings())
    try:
        summary = services.analytics.monthly_summary(parse_month(month))
        table = Table(title=f"PFA summary {summary.period}")
        table.add_column("Measure")
        table.add_column("Amount", justify="right")
        for label, value in (
            ("Income", summary.income_minor),
            ("Spending", summary.spending_minor),
            ("Essential", summary.essential_spending_minor),
            ("Discretionary", summary.discretionary_spending_minor),
            ("Savings", summary.savings_minor),
            ("Investments", summary.investments_minor),
            ("Net cashflow", summary.net_cashflow_minor),
        ):
            table.add_row(label, print_money(value))
        table.add_row("Savings rate", f"{summary.savings_rate_percent:.2f}%")
        console.print(table)
    finally:
        close_services(engine, services)


@transactions_app.command("list")
def transactions_list(limit: int = typer.Option(50, min=1, max=500)) -> None:
    engine, services = open_services(get_settings())
    try:
        table = Table(title="Transactions")
        for column in ("Date", "Description", "Amount", "Kind", "Category"):
            table.add_column(column)
        for row in services.uow.transactions.all()[-limit:]:
            sign = "" if row.kind in {"income", "refund"} else "-"
            table.add_row(
                row.transaction_date.isoformat(),
                row.raw_description,
                f"{sign}{print_money(row.amount_minor)}",
                row.kind,
                row.category or "review",
            )
        console.print(table)
    finally:
        close_services(engine, services)


@transactions_app.command("uncategorized")
def transactions_uncategorized() -> None:
    engine, services = open_services(get_settings())
    try:
        for row in services.uow.transactions.uncategorized():
            console.print(
                f"{row.id}: {row.transaction_date} {row.raw_description} "
                f"{print_money(row.amount_minor)}"
            )
    finally:
        close_services(engine, services)


@app.command("ask")
def ask(question: str) -> None:
    settings = get_settings()
    engine, services = open_services(settings)
    try:
        result = build_advisor(settings).run_sync(
            question, deps=FinanceDependencies(services.analytics, services.planning)
        )
        console.print(result.output)
    finally:
        close_services(engine, services)


@app.command("review")
def review_month(month: str | None = typer.Option(None, "--month")) -> None:
    engine, services = open_services(get_settings())
    try:
        evidence = monthly_review_evidence(services.analytics, parse_month(month))
        console.print_json(data=evidence)
    finally:
        close_services(engine, services)


@budget_app.command("show")
def budget_show(month: str | None = typer.Option(None, "--month")) -> None:
    engine, services = open_services(get_settings())
    try:
        for status in services.analytics.budget_status(parse_month(month)):
            console.print(status.model_dump_json())
    finally:
        close_services(engine, services)


@goals_app.command("list")
def goals_list() -> None:
    engine, services = open_services(get_settings())
    try:
        for goal in services.analytics.goal_progress():
            console.print(
                f"{goal.name}: {goal.progress_percent:.1f}% "
                f"({print_money(goal.current_minor)} / {print_money(goal.target_minor)})"
            )
    finally:
        close_services(engine, services)


@app.command("health")
def health() -> None:
    console.print_json(data=health_report(get_settings()))


if __name__ == "__main__":
    app()
