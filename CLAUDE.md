# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Everything runs through `uv` (never bare `python`/`pytest`):

```bash
uv sync                       # install locked deps
uv run pfa db migrate         # required before any CLI/API workflow
uv run pfa <cmd>              # CLI entrypoint (pfa.cli.app:app)
uv run uvicorn pfa.api.app:app --host 127.0.0.1 --port 8000
uv run pfa eval-classifier    # classifier smoke, needs a running Ollama model
```

Full quality gate — all four must pass before claiming work is complete (matches CI):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

mypy runs in `strict` mode over `src` only. Single test: `uv run pytest -k 'test_name'`.

## Hard rules

- **The LLM never computes financial facts.** Every number a user sees must come from deterministic
  Python/SQL in `analytics`, `planning`, or `domain`, surfaced through a typed read-only tool. The
  agent interprets and explains; it does not calculate. Adding a number to a prompt/response that no
  tool returned is a bug.
- **Local models only.** No cloud/hosted LLM APIs, no telemetry. Ollama at `PFA_OLLAMA_BASE_URL` is
  the only inference path. Live AI paths are fine to exercise, but `uv run pytest` must stay offline —
  the normal suite never calls Ollama.
- **Money is integer minor units.** Use `domain.money.Money` and `Decimal`; binary floats must never
  reach a monetary calculation. v0.1 is GBP-only and rejects other currencies rather than summing them.
- **Schema changes go through Alembic** (`alembic/versions/`). Runtime service code never creates or
  upgrades tables.

## Domain semantics

Getting these wrong silently corrupts reported figures:

- Owned-account transfers are persisted for audit but excluded from both income and spending.
  For paired transfers, only the debit/outgoing side contributes to a metric.
- `savings rate = (saving transfers + investment transfers) / income` for the period.
- Spending = classified expenses + fees − refunds. A refund reduces spending in the month it posts,
  even if the purchase was earlier.
- Cash withdrawals move bank cash to physical cash: total tracked cash is unchanged, and the amount
  is not spending until the underlying purchase is classified.

Layer boundaries and deliberate constraints: @docs/architecture.md
Agent design, tool contracts, and grounding rules: @docs/ai-engineering.md

## Repo etiquette

- Work on a feature branch and open a PR; never commit directly to `main`.
- Conventional commits: `type(scope): subject` (e.g. `fix(analytics): ...`). The `F-NN` IDs in older
  commits refer to a completed validation pass — don't invent new ones.
- `.env` is local and gitignored; copy `.env.example` and use `PFA_`-prefixed settings.
