# Handoff: continuation gate absorbs broken data rows (regression from H-1/H-3)

Follow-up to `docs/plans/2026-08-29-pdf-extraction-codex-brief.md`, which you implemented.
That batch is correct on its own terms - H-1 and H-3 both work, and the AMEX banner fixture
pins the bug it was meant to pin. This brief fixes one regression it introduced.

## The bug

`_merge_continuations` (`src/pfa/ingestion/extractors/pdf.py`) treats every line that fails
`_is_plausible_data_row` as a wrapped description. A row that filled its date and amount
cells but whose *format* is not yet supported now gets absorbed into the row above it. It
used to survive as its own candidate and fail validation visibly.

Reproduce with a single page, columns `[72.0, 160.0, 400.0]`:

```
Date        Description           Amount
01/08/2026  Tesco Metro           -12.50
21 Jul 25   E.ON NEXT COVENTRY   -295.79     <- H-5 date format, not yet supported
02/08/2026  Salary               2000.00
03/08/2026  Card payment         450.62CR    <- H-6 credit marker, not yet supported
```

Today's extractor returns **two** candidates for four transactions, and no issues at all:

```
01/08/2026 | Tesco Metro E.ON NEXT COVENTRY | 1250   debit
02/08/2026 | Salary Card payment            | 200000 credit
batch issues: []
```

Two real transactions are gone and two descriptions are corrupted, with nothing flagged
anywhere. The preview shows two clean valid rows and the batch commits.

This is the failure mode the whole extraction review exists to prevent: silent, plausible-
looking, wrong. It is worse than the garbage rows H-3 removed, because garbage is visible.
It also bites precisely on the formats in the real corpus (`21 Jul 25`, `450.62CR`), so it
is live today, not hypothetical.

## The rule

Only a **structurally empty** date/amount cell makes a wrapped description. A line that put
something in a date or money column and failed to parse is a **broken transaction row**: it
stays its own candidate, and validation reports it.

- date cell and all amount cells empty -> continuation or noise (join if close, else drop).
  This is the current behaviour and it is right; H-3's win depends on it.
- date or amount cell non-empty but unparseable -> keep as its own candidate.

## Order of work

1. Write the failing test first, from the page above. Assert four candidates, with
   `raw_description` values `Tesco Metro`, `E.ON NEXT COVENTRY`, `Salary`, `Card payment` -
   no description carrying another row's text. Run it, paste the failure.
2. Fix `_merge_continuations` so the continuation path is gated on emptiness rather than on
   parseability.
3. Delete `UNJOINED_CONTINUATION` from `src/pfa/ingestion/candidates.py`. Your batch removed
   its only producer and nothing emits it now.
4. Re-run the fixture green, then the full gate.

## Guardrails

- These two tests must stay green and must not be edited. They are the H-1/H-3 result and
  this fix must not undo them:
  - `test_amex_banner_header_does_not_turn_statement_chatter_into_candidates`
  - `test_header_with_zero_plausible_data_rows_reports_pdf_not_extractable`
  - `test_continuation_line_far_from_any_row_is_dropped_as_noise` (an orphan with genuinely
    empty date and amount cells is still dropped)
- Do not add support for the `21 Jul 25` or `450.62CR` formats. That is H-5 and H-6, a later
  batch. In this batch those rows must surface as **blocking validation errors**, which is
  the honest outcome. Making them import is out of scope and will be rejected.
- No new dependencies, no binary fixtures, no changes outside `pdf.py`, `candidates.py` and
  `tests/unit/test_pdf_extractor.py`.
- Known and deliberately NOT in scope: `_DATE_PATTERNS` in `pdf.py` duplicates the list in
  `service._parse_date`. H-5 will merge them into `candidates.py`. Leave it alone.

## Gate

```
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

All four must pass; 100 tests pass today. Report the failing output from step 1, the diff,
and the final gate.
