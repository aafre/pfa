# Handoff: PDF extractor, batch 1 (H-1 + H-3)

Read `docs/plans/2026-08-29-real-statement-extraction-findings.md` first. It is the spec.
This file is only the scope, the order and the guardrails.

## Scope of this handoff

**H-3** (plausibility gate on data rows) and **H-1** (a page banner mistaken for the
transaction header). Nothing else. H-2, H-4, H-5, H-6, H-7 and M-1 are explicitly out of
scope and belong to a later batch.

Together these two turn every silent-garbage case into an honest `PDF_NOT_EXTRACTABLE`.
That is a safe resting state: the goal of this batch is **not** to make real statements
import. It is to stop them importing garbage. Do not attempt layout support to make a
fixture pass - if a real layout still yields nothing after this batch, that is the
correct outcome.

## Order of work

1. **Fixtures first, and they must fail.** Build them with `tests/fixtures/pdf_builder.py`
   (`build_pdf` / `statement_page`) - no binary fixtures, no new dependencies. Two are
   needed, both reproducing the shapes quoted in the findings doc:
   - an AMEX-shaped page whose `Prepared for / Membership Number / Date` banner strip sits
     above the real `Date Date Transaction Details Foreign Spend Amount` header, with
     footer and marketing lines below the transactions;
   - a page that matches a header but has no plausible data rows at all.
   Run them and paste the failing output before changing any source. Today the first
   fixture produces a wall of candidates whose description is empty; that is the bug.
2. **H-3** - `_word_rows` (`src/pfa/ingestion/extractors/pdf.py:181-205`). A line becomes a
   candidate only if its date cell parses as a date **and** it carries at least one
   parseable amount. Otherwise apply the existing continuation rule, else drop it. A page
   with a header but zero plausible rows emits `PDF_NOT_EXTRACTABLE`.
3. **H-1** - `_header_columns` (`pdf.py:157-163`). A header must map a date column **and**
   at least one of `amount` | `debit` | `credit`. Keep scanning later lines for a better
   header instead of locking the first match.
4. Re-run the fixtures green, then the full gate.

## Guardrails

- No new dependencies. No binary fixtures in the repo.
- Do not edit the existing assertions in `tests/integration/test_cli.py` or
  `tests/integration/test_api.py`. If one of them fails, that is a regression in your
  change, not a stale test.
- Do not touch `src/pfa/ingestion/batches.py`, `src/pfa/api/app.py` or the `amount_sign`
  work - C-1 and M-2 landed separately and are unrelated to this batch.
- `statement_start` / `statement_end` are never populated by `create_batch`. H-5 needs
  them; this batch does not. Leave it alone and say so if you trip over it.
- Every new alias or accepted header shape needs a test. The spec forbids fuzzy guessing:
  matching stays deterministic and table-driven.

## Gate

```
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

All four must pass. 98 tests pass today; mypy is strict, ruff line-length 100.

## Report back

The failing fixture output from step 1, the diff, and the final gate output. If H-3's
plausibility gate turns out to overlap F-07 (the `line_height * 1.6` continuation
threshold absorbing an unrelated line), say so rather than fixing F-07 quietly.
