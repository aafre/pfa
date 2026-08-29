# Real-statement extraction: findings and fix brief

Date: 2026-08-29
Branch verified: `feat/statement-upload-integration` (PR #8, stacked on PR #7)
Corpus: 12 real statements plus 8 real CSV exports across three issuers (HSBC current account,
HSBC Visa credit card, American Express). Those files live outside the repo and are NOT
committed. All identifiers below are redacted; only layout shape is reproduced.

## Bottom line

The stack's gates are green (93 tests, ruff/format/mypy clean) and the synthetic PDF fixture
works end to end. Against real statements the picture is different:

| Source | Rows extracted | Valid | Outcome |
|---|---:|---:|---|
| AMEX PDF x4 | 233-269 | **0** | commit blocked, `BATCH_HAS_BLOCKING_ERRORS` |
| HSBC current account PDF x4 | 29-63 | **0** | commit blocked |
| HSBC credit card PDF x4 | 0 | 0 | `PDF_NOT_EXTRACTABLE` |
| AMEX CSV x6 | 35-65 | 35-65 | **commits, but every sign is wrong** |
| HSBC CSV x2 | 29 | 0 | commit blocked |

**No PDF statement in the corpus imports.** The only format that commits is the AMEX CSV, and
it silently inverts the entire ledger. Fix C-1 first; it is the only finding that corrupts
committed data.

---

## C-1 - Critical - unsigned credit-card CSVs import every charge as income

**What breaks:** AMEX CSV exports carry unsigned positive amounts, because on a credit-card
statement a positive figure is a charge (money out). `_parse_amount` reads the sign from the
value alone (`src/pfa/ingestion/service.py:65-71`), so `101.20` becomes `sign=+1` ->
`flow_direction=credit` -> `kind=income`. Nothing flags it: 35/35 rows validate clean and the
commit succeeds.

**Observed after committing `AMEX/latest.csv`:**

```
2025-08-19  AMZNMKTPLACE*<redacted>        2592  credit  income
2025-08-19  MARYLEBONE STATION LONDON       700  credit  income
2025-08-21  LIME*PASS <redacted>           3699  credit  income
```

Every purchase is booked as income. Analytics, budgets and goals all consume this.

**Why preview-before-commit does not save the user:** the preview only protects against rows
the validator can see are wrong. A sign convention is a property of the *source*, not of any
row, so no per-row check can detect it.

**Fix direction:** the statement source needs an explicit sign convention. Options, cheapest
first: (a) an `amount_sign` field on the preview/PATCH contract (`as_written` | `invert` |
`debit_positive`) that the user confirms before commit, defaulting to `as_written`;
(b) infer it from account type when the destination account is known to be a credit card.
Do NOT infer silently from the data - an all-positive statement is genuinely ambiguous
between "credit card" and "a month with no refunds".

**Test to add:** an unsigned credit-card CSV committed with the credit-card convention
produces `flow_direction=debit`; with the default convention it is either flagged or left
`as_written` - never silently inverted.

---

## H-1 - High - a page-banner line is mistaken for the transaction header

**What breaks:** `_header_columns` (`src/pfa/ingestion/extractors/pdf.py:157-163`) accepts any
line containing a cell that matches the `date` alias, and requires nothing else. Every AMEX
page carries a metadata strip:

```
Prepared for      Membership Number       Date
<NAME>            xxxx-xxxxxx-<redacted>  19/08/25
```

The trailing `Date` matches. The extractor locks columns to `[(519.7, 'date')]` - a single
column - and then treats **every subsequent line on the page as a data row**, snapping each
cell to the nearest (only) column. Result: 264 candidates whose `transaction_date` holds
marketing copy and whose description is empty.

**Evidence:** a probe over all 8 AMEX pages reports `HEADER MATCHED -> [(519.7, 'date')]` on
every page, always on the `Prepared for Membership Number Date` line, never on the real
`Date Date Transaction Details Foreign Spend Amount` line further down.

**Fix direction:** require a header to map a date column AND at least one amount-bearing
column (`amount` | `debit` | `credit`) before accepting it. That single condition eliminates
this false match. Keep scanning later lines for a better header rather than locking the first.

---

## H-2 - High - header matching is exact-match only

**What breaks:** `_match_header` (`src/pfa/ingestion/extractors/pdf.py:77-82`) lowercases and
collapses whitespace, then requires the cell to equal an alias exactly. Real headers never do:

| Real header cell | Alias that should match | Why it fails |
|---|---|---|
| `£Paid out` | `paid out` | currency symbol glued to the token |
| `£Paid in` | `paid in` | same |
| `£Balance` | `balance` | same |
| `Payment type and details` | `details` | alias is a substring, not the whole cell |
| `Amount £` | `amount` | trailing symbol |
| `TransactionDate` | `date` | no space between words in the source PDF |
| `ReceivedByUs` | (a second date column) | same |

**Evidence:** the HSBC current account matches its real header line
`Date  Payment type and details  £Paid out  £Paid in  £Balance` but recovers only
`[(53.3, 'date')]`. All three money columns and the description column are lost, so every row
has an empty description and `amount_minor=None`.

**Fix direction:** normalize the cell before matching - strip currency symbols (`£$€`) and
punctuation, split space-less/camel-case runs - then match on token containment rather than
whole-cell equality. Keep it explicit and table-driven; the spec forbids fuzzy guessing, and
containment is still deterministic. Every alias added needs a test.

---

## H-3 - High - no plausibility gate on data rows

**What breaks:** in `_word_rows` (`src/pfa/ingestion/extractors/pdf.py:181-205`), once
`columns` is set every following line becomes a `_RawRow` unconditionally. Page footers, legal
text, address blocks and marketing paragraphs all become candidates.

This is what turns H-1 and H-2 from "extracts nothing" into "extracts 269 pieces of noise". It
is also why the HSBC credit card behaves *better* than AMEX: it finds no header at all and
returns an honest `PDF_NOT_EXTRACTABLE`, which is the correct failure.

**Fix direction:** a line becomes a candidate only if its date cell parses as a date and it
carries at least one parseable amount. Lines failing both are continuation-or-noise: apply the
existing continuation rule, else drop them. If a page yields a header but zero plausible rows,
emit `PDF_NOT_EXTRACTABLE` rather than a wall of error rows.

---

## H-4 - High - multi-line headers are unsupported

Both issuers split the header across two physical lines. Only the second is examined.

**AMEX:**

```
Transaction  Process
Date         Date      Transaction Details      Foreign Spend  Amount £
Jul31        Jul31     PAYMENT RECEIVED - THANK YOU            1,940.64
                                                               CR
Jul21        Jul21     ZETTLE *<redacted> MACCLESFIELD         6.30
```

Two date columns whose distinguishing words ("Transaction", "Process") live on the line above.

**HSBC credit card:**

```
                                    Amount
ReceivedByUs   TransactionDate      Details
21 Jul 25      20 Jul 25            E.ON NEXT COVENTRY            295.79
                                    DIRECT DEBIT PAYMENT          450.62CR
```

`Amount` sits on its own line, above and to the right of the column it labels.

**Fix direction:** when a line yields a partial header, try merging it with the line above (and
below) by x-overlap before rejecting it. Cap the lookahead at one line each way.

---

## H-5 - High - real date formats are unsupported

`_parse_date` (`src/pfa/ingestion/service.py:56-62`) accepts `%Y-%m-%d`, `%d/%m/%Y`,
`%d-%m-%Y` and `%m/%d/%Y` only.

| Issuer | Format | Example | Note |
|---|---|---|---|
| HSBC (both) | `%d %b %y` | `21 Jul 25` | |
| AMEX | `%b%d` | `Jul31`, `Aug4` | **no year at all** |

AMEX dates carry no year, so the year must come from the statement period. A statement
spanning a year boundary (Dec -> Jan) must not put January in the wrong year.

**Fix direction:** add `%d %b %y` and `%d %b %Y`. For the year-less form, resolve against the
statement end date and roll back a year when the result would be in the future relative to it.
This needs `statement_start`/`statement_end` populated, which `create_batch` currently never
does (`src/pfa/ingestion/batches.py:123-136`; recorded as claim 26 in the earlier verification).

---

## H-6 - High - the `CR` credit marker is unsupported

Both issuers mark credits with `CR` rather than a sign or a separate column, in two shapes:

- HSBC credit card: **suffix on the amount** - `450.62CR`
- AMEX: **on its own line below the amount** - `1,940.64` then `CR`

`_parse_amount` strips `,` and `£` only, so `450.62CR` raises `invalid amount`. Untreated, a
payment *to* the card reads as another charge.

**Fix direction:** recognize a trailing `CR`/`DR` token, and the own-line variant during
continuation joining, and let it set direction the same way the debit/credit columns do. Two
sign sources that disagree must still raise `AMBIGUOUS_SIGN`.

---

## H-7 - High - headerless CSVs are rejected

Both HSBC CSV exports ship with no header row:

```
14/08/2025,<merchant> <location> GB,-14.38
13/08/2025,<merchant> <location> GB,-1.95
```

`read_csv_rows` requires `reader.fieldnames` and otherwise raises `CSV has no header row`
(`src/pfa/ingestion/extractors/csv.py:44`). In practice `DictReader` consumes the first
transaction as the header, so all 29 rows fail validation and one real transaction disappears
without comment.

**Fix direction:** when no cell in the first row matches any known alias but the row's shape is
(date, text, amount), treat the file as headerless and assign positional columns. Emit a
warning naming the assumed column order so the preview shows the assumption. Keep the existing
`CSV has no header row` error for genuinely unrecognizable files - `tests/integration/test_cli.py`
and `tests/integration/test_api.py` depend on the current message and must not be edited.

---

## M-1 - Medium - the two extractors disagree on their alias vocabulary

`src/pfa/ingestion/extractors/csv.py:18-20` already knows `money out` / `money in`; the PDF map
(`src/pfa/ingestion/extractors/pdf.py:46-54`) does not. Monzo, Starling and Lloyds all use
those headers. One shared alias table should serve both extractors - the T1 brief put issue
codes in `candidates.py` for exactly this reason, and header aliases belong there too.

## M-2 - Medium - committed batches retain their staged rows

`select status, candidates_json is null from import_batches` returns `0` for committed batches.
The T2 brief says `candidates_json` "gets nulled on expiry and after commit", and T7's manual
acceptance step checks precisely this. Expiry nulls it; `commit_batch` does not. Either null it
on commit or amend both briefs - but the code and the stated privacy boundary must agree.

---

## Carried over, still open

From `statement-upload-stack-verification-2026-08-29.md`. F-01, F-02, F-03, F-04, F-08 and
F-11 are fixed in PRs #7 and #8; these are not:

- **F-05** - the size cap runs after Starlette has already spooled the multipart body. Accepted
  for loopback-only use; becomes blocking if the server is ever exposed.
- **F-06** - the content gate establishes "decodes as UTF-8", not "is a CSV". Casing is fixed;
  a bounded semantic header check is not.
- **F-07** - the continuation threshold `line_height * 1.6` can absorb an unrelated nearest line
  into a description (`extractors/pdf.py:202-235`). H-3's plausibility gate overlaps this; fix
  them together.
- **F-09** - the extraction timeout is nominal, not cancellation. PR #7 stopped it leaking the
  staged file; a runaway parser still runs to completion.
- **F-12** - `tests/integration/test_migrations.py:13-19` treats every non-`base` revision as an
  upgrade, so a downgrade through that helper silently no-ops.
- **F-13** - balance reconciliation produces no warnings. The approved design says "may", so
  this is optional; the T5 brief was stronger.
- **F-14** - `amount_mode` in PATCH is an unconstrained `str`; unknown values succeed as no-ops
  (`src/pfa/api/app.py:122-125`, `src/pfa/ingestion/batches.py:226-229`).

## Not built at all

- **T4** - the Import page. There is no browser workflow; everything above was driven over HTTP.
  Acceptance criteria 1, 5 and 10 cannot pass without it.
- **T7** - observability, README/architecture docs, regression sweep. Criterion 14 fails; the
  README still describes browser upload and PDF extraction as planned.

---

## Suggested order

1. **C-1** - sign convention. The only finding that corrupts committed data.
2. **H-3 + H-1** - plausibility gate and the two-column header requirement. Together they turn
   every silent-garbage case into an honest `PDF_NOT_EXTRACTABLE`, a safe resting state.
3. **H-2, H-4, H-5, H-6** - the actual layout support, one issuer at a time, each with a fixture
   built by `tests/fixtures/pdf_builder.py`. No binary fixtures in the repo.
4. **H-7, M-1, M-2** - CSV headerless mode, shared alias table, commit-time purge.
5. **T4**, then **T7**.

## How to reproduce any of the above

```
cd pfa-stack-verify
uv run pfa db migrate
uv run uvicorn pfa.api.app:app --host 127.0.0.1 --port 8000
curl.exe -s -X POST http://127.0.0.1:8000/imports/preview -F "file=@<statement>;type=application/pdf" -F "account=Test"
```

Gates for any change:
`uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`
(mypy strict, ruff line-length 100, 93 tests currently passing).
