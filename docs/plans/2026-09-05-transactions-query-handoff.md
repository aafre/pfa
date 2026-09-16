# Handoff — item #2 done (`GET /transactions` no longer scans the table)

Continues `docs/plans/2026-09-05-post-v0.2-handoff.md`. Item #2 of that list is
closed; items #1, #3, #4, #5 are untouched and still the open queue.

## What changed

**`src/pfa/db/repositories.py`** — `TransactionRepository`:

- New `query(*, start, end, account_id, limit)`. Date range, account and row cap
  all go into the `SELECT`; `transaction_date` is already indexed. With `limit`
  it orders `date DESC, id DESC`, takes `LIMIT n`, then reverses in Python, so
  callers still get oldest-first *newest n* rows — same set the old
  `rows[-limit:]` produced.
- `between(start, end)` is now a one-line delegate to `query`.
- New `months(currency=None)` — `SELECT DISTINCT transaction_date`, folded to
  `YYYY-MM` in Python. Deliberately not `strftime` in SQL: keeps it off SQLite
  specifics, and distinct dates is a small column scan.

**`src/pfa/api/app.py`**:

- `transactions()` dropped `services.uow.transactions.all()` + the Python
  filters. A `month` is turned into bounds with the existing
  `analytics.service.month_bounds(_month(month))`.
- `account_id` is now typed `int | None`, so junk gives a 422 from FastAPI
  instead of the old silent `str(...) == str(...)` compare that matched nothing.
- New `GET /transactions/months?currency=` → `["2025-04", …]`, oldest first.

**`src/pfa/web/app.js`** — both `?limit=500` pulls are gone:

- `latestMonthWithData(currency)` calls `/transactions/months?currency=…`, and
  falls back to the unscoped list. The `ponytail:` comment about reading 500
  rows went with it.
- `bootstrapDashboard` fetches `/transactions?limit=1` purely to learn the
  freshest transaction's currency, then defers to `latestMonthWithData`. It no
  longer sorts a 500-row array client-side.

## Verified

Quality gate green: `ruff check`, `ruff format --check`, `mypy src` (60 files),
`pytest` — 151 passed.

Test: `tests/integration/test_api.py::test_transactions_month_filter_and_chat_currency`
gained asserts for `/transactions/months` (both forms), `?limit=1` returning the
newest row, and an unmatched `account_id` returning `[]`.

Manual run against the real dev database (`data/pfa.db`, 429 rows, GBP + INR),
server on **port 8010** (8000 left free):

```
uv run uvicorn pfa.api.app:app --host 127.0.0.1 --port 8010
```

- `/transactions/months` → the six months with data; `?currency=INR` → one;
  `?currency=GBP` → five. Consistent with the row counts in the DB.
- `?month=2025-07&limit=500` → 111 rows, first `2025-07-01`, last `2025-07-31`.
- `?account_id=<n>` returns only that account; `?account_id=abc` and
  `?month=nope` both 422.
- Dashboard in Chrome: bootstrap issued exactly three calls —
  `/transactions?limit=1`, `/transactions/months?currency=INR`,
  `/transactions?month=2026-08&limit=200` — and landed on the newest currency's
  latest month. Switching the currency selector to GBP fired
  `/transactions/months?currency=GBP` and moved to August 2025. Activity Ledger
  rendered "Showing 74 of 74". No console errors, no `limit=500` request
  anywhere.
- Latency ~9ms per call on this dataset (not a meaningful benchmark at 429 rows;
  the point is the query shape, not the number).

## Deliberately not done

- No keyset pagination and no `(currency, transaction_date)` composite index.
  The month index carries this size fine; add them when a month stops fitting
  the 500-row cap.
- `/transactions` still has no `offset`. The ledger loads a whole month at
  `limit=200`; a month over 200 rows silently truncates to the newest 200. That
  cap predates this change but is now the only remaining unbounded-ish path —
  worth a look before any multi-account year.
- `cash_position` (`/analytics/cash`) still passes `uow.transactions.all()`.
  It genuinely needs every row to compute a balance, so it was left alone; if it
  ever gets slow, the fix is a stored running balance, not a narrower query.

## Repo state

Working tree has the above changes **uncommitted**, on top of `7c4c112`. The
`git add`/`commit`/`push` in the previous session was blocked by the permission
classifier, so `main` and `v0.2.0` are **still local only**, and these untracked
docs are still unstaged: `CLAUDE.md`, `PRODUCT.md`, `VALIDATION_REPORT.md`,
`.python-version`, `docs/plans/2026-08-30-typed-accounts-and-transfer-events.md`,
`docs/reports/`. All were grepped for account numbers / sort codes / IBANs —
clean. Push with:

```
git add -A && git commit && git push origin main --tags
```
