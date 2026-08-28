# Personal Finance Agent

A 100% local personal finance intelligence system that runs entirely on your machine.

PFA understands your financial position over time, explains where money is going, detects anomalies and behavioural drift, models future decisions, and enforces deterministic budget tracking without leaking data to cloud APIs.

## Architecture Principles

- **Zero Data Leakage:** Powered by local models via Ollama.
- **Deterministic Compute Isolation:** Math, aggregations, and balance histories are calculated in Python/SQLite. The LLM acts strictly as a decision, routing, and interpretation engine.
- **Type-Safe Agent Loop:** Built with PydanticAI for strict schema enforcement, structured outputs, and self-healing validation retries.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Fast Python package manager)
- [Ollama](https://ollama.com/) running locally

```bash
ollama pull qwen3:4b
```





The goal is:

A private financial intelligence system that understands my financial position over time, explains where my money is going, detects poor financial behaviour, models decisions, tracks goals, and helps me systematically improve savings and investing.

It should eventually answer questions such as:

Where did my money go this month?

Why was August more expensive than July?

How has my lifestyle spending changed over 12 months?

What subscriptions am I barely using?

What expenses have crept upward?

What percentage of income am I saving?

How much have I invested this year?

Am I maintaining enough cash?

If I buy a £2,000 laptop now, what does it do to my
3-month cash-flow plan?

I'm thinking about increasing my ISA contribution from
£500 to £800. Can I sustainably afford it?

What financial decisions did I make this year that,
in hindsight, hurt my goals?

At my current trajectory, where will my cash/investments
be in 6/12/24 months?

What should I focus on financially this month?

Eventually:

PFA > Give me my August review.

INCOME
£x

ESSENTIAL SPENDING
£x

DISCRETIONARY SPENDING
£x

SAVINGS
£x

INVESTMENTS
£x

DEBT PAYMENTS
£x

SAVINGS RATE
x%

Compared with your six-month baseline...

Three things changed materially:
...

Your biggest avoidable drag was...
...

At the current spending trajectory...
...

Three actions worth considering:
1. ...
2. ...
3. ...

Evidence:
- 14 transactions...
- Jul-Aug comparison...
- recurring payments...

But every number there originates from deterministic financial code.

Not from the LLM.



```text
                  ┌────────────────────────┐
                  │          LLM           │
                  │                        │
                  │ interpret              │
                  │ reason                 │
                  │ select tools           │
                  │ explain                │
                  │ identify patterns      │
                  └───────────┬────────────┘
                              │
                           TOOLS
                              │
                  ┌───────────▼────────────┐
                  │ DETERMINISTIC ENGINE   │
                  │                        │
                  │ arithmetic             │
                  │ SQL                    │
                  │ balances               │
                  │ cash flow              │
                  │ ratios                 │
                  │ projections            │
                  │ validation             │
                  └───────────┬────────────┘
                              │
                  ┌───────────▼────────────┐
                  │     SOURCE OF TRUTH    │
                  │                        │
                  │ transactions           │
                  │ accounts               │
                  │ assets                 │
                  │ liabilities            │
                  │ budgets                │
                  │ goals                  │
                  └────────────────────────┘
```



If PFA tells you:

"You spent £632.17 eating out in August."

the model must not have calculated £632.17.

A tool should have returned:

```json
{
  "period": "2026-08",
  "category": "eating_out",
  "total_minor_units": 63217,
  "currency": "GBP",
  "transaction_count": 31
}
```

The LLM merely explains it.

This single design principle is transferable to risk systems, compliance systems, enterprise agents, operations agents and most serious AI applications.

## Implemented architecture

The production package is under `src/pfa`:

- typed domain and integer minor-unit money model
- SQLite persistence through SQLAlchemy 2 and Alembic
- idempotent, row-tolerant CSV ingestion with provenance and rule-first classification
- deterministic spending, cashflow, savings, trend, anomaly, recurring, budget, goal, and scenario calculations
- one PydanticAI advisor using local Ollama and narrow read-only tools
- Typer/Rich CLI and FastAPI API with OpenAPI docs
- local health reporting and graceful model unavailability

Calculations define savings rate as `(saving transfers + investment transfers) / income` for the period.
For paired owned-account transfers, only the debit/outgoing side contributes to that numerator.
Spending includes classified expenses and fees, less refunds. Cash withdrawals affect cash position
but are not spending until the underlying purchase is classified. PFA treats withdrawals as movement
from bank cash to physical cash, so tracked total cash is unchanged. Refunds reduce spending in the
month the refund posts, even when the original purchase was in an earlier month. Owned-account
transfers do not count as income or spending.

## Requirements and setup

Requires Python 3.12+, `uv`, and optionally Ollama for AI features. The deterministic application
works without Ollama.

```bash
uv sync
uv run pfa db migrate
ollama pull qwen3:4b
```

Configuration is local by default. Copy `.env.example` to `.env` to change `PFA_DATABASE_URL`,
`PFA_OLLAMA_BASE_URL`, `PFA_MODEL`, or `PFA_LOG_LEVEL`.

## Demo journey

The checked-in dataset is synthetic and contains June–August 2026 salary, household bills,
groceries, restaurants, transport, subscriptions, transfers, savings, investments, a refund,
cash withdrawal, shopping spike, and debt repayment.

```bash
uv run pfa import data/demo_transactions.csv
uv run pfa summary month --month 2026-08
uv run pfa transactions list
uv run pfa ask "Why was August more expensive than July?"
uv run pfa ask "What categories have increased over the last three months?"
uv run pfa ask "What recurring payments do I have?"
uv run pfa ask "Can I afford a £2,000 purchase without reducing planned monthly savings?"
uv run pfa review --month 2026-08
uv run pfa budget set 800 --category eating_out --month 2026-08 --discretionary
uv run pfa goals add "Emergency fund" 10000 --type emergency_fund
```

`pfa import` reports malformed rows and leaves unresolved expenses marked for review if Ollama
is unavailable. Repeat imports are safe. Use `--dry-run` to validate without persistence.

## Statement ingestion

### Available now

- `pfa import <path.csv>` imports a local UTF-8 CSV through the CLI; `--dry-run` validates without
  persistence.
- The CSV adapter accepts common aliases for date, description, amount, account, and transaction ID.
  It supports signed amounts, several UK/US date formats, row-level errors, duplicate detection, and
  manual review for unresolved classifications.
- Version 0.1 is GBP-only. Bank-specific debit/credit columns may require conversion to a signed
  `amount` column.
- `POST /imports` currently accepts a server-local CSV path. It is not a browser file-upload endpoint.
- The dashboard's Import navigation item is a placeholder; users cannot yet upload statements from
  the browser.

### Planned statement upload

The next ingestion slice adds browser upload, preview-before-commit, actionable row errors, import
history, and adapters for CSV and text-based PDF statements. Initial PDF support deliberately excludes
scanned/image-only statements and OCR; unreadable PDFs must fail clearly without changing financial
state. See the [statement upload and PDF extraction requirements](docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md).

## API

```bash
uv run uvicorn pfa.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/docs`. Main routes include `/health`, `/imports`, `/transactions`,
`/analytics/monthly`, `/analytics/categories`, `/budgets`, `/goals`, `/scenarios/purchase`,
`/reviews/monthly`, and `/chat`. Import requests use a local CSV path so financial data never
needs to leave the machine.

## Testing and evals

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run pfa eval-classifier  # requires a running Ollama model
```

The normal test suite never calls Ollama. The classifier eval is a four-case structured-output
compatibility smoke, not a statistically meaningful model-quality benchmark. It reports configured
model availability, kind/category/exact accuracy, failure reasons, and latency. Signed amounts are
part of every case. The runtime classifier only handles rows left unresolved by deterministic rules.

## Privacy, safety, and limitations

PFA has no hosted telemetry or cloud model requirement. The SQLite database and imported files
remain local, and logs avoid raw transaction payloads. The initial API assumes deliberate localhost
binding and has no authentication platform. PFA is advisory and read-only: it cannot transfer
money, execute trades, initiate payments, alter bank accounts, or promise investment returns.

Current limitations include the absence of browser uploads and PDF extraction, bank-specific CSV
quirks, manual handling of opening balances, and
heuristic recurring/anomaly detection. Recurring evidence supports weekly, monthly, and quarterly
cadences; annual schedules and missing-month recovery are not inferred. Future work: the statement
upload slice described above, stronger evals for groundedness, and opt-in audit logs for any future
write proposal flow. Version 0.1 is GBP-only and rejects other currencies rather than summing them.
Exact same-day duplicates without bank external IDs are preserved within a file by occurrence order;
across partial/overlapping files, external IDs remain the only unambiguous identity. Rows unresolved
while Ollama is unavailable enter the manual correction queue; automatic deferred reclassification is
not implemented. Credit-card payment matching is not modeled: when underlying card purchases are
also imported, users must classify the bank-to-card payment as a transfer to avoid double counting.

See [docs/architecture.md](docs/architecture.md) and [docs/ai-engineering.md](docs/ai-engineering.md)
for boundary and learning notes.
