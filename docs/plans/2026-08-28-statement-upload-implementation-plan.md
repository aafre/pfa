# Statement Upload + PDF Extraction — Implementation Plan

Spec: `pfa-ui-top-class/docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md` (approved).

## Context

PFA can already ingest transactions well — `ImportService.import_csv` parses, validates, classifies,
fingerprints, dedupes and persists in one pass (`src/pfa/ingestion/service.py:103-194`). What it cannot
do is accept a file from a human. `POST /imports` takes a **server-local path** (`src/pfa/api/app.py:85`),
the CLI takes a path, and the dashboard's Import nav item calls `preventDefault()` and shows a
"coming next" toast (`src/pfa/web/app.js:102`). So the only way to import money data today is to drop a
file somewhere the server can see and run a command.

Two things block a real upload journey:

1. **No file boundary.** No multipart endpoint, no upload policy, no temp-file lifecycle.
2. **No preview boundary.** `import_csv` is a single loop that validates *and persists* in the same
   iteration. There is no point at which candidate rows exist, are reviewable, and have not yet touched
   the ledger. The spec's core rule — "No transaction may enter the authoritative ledger before the user
   sees a preview and confirms it" — is structurally impossible against the current code.

Intended outcome: a user drops a `.csv` or `.pdf` on the Import page, sees exactly what PFA extracted with
per-row provenance and issues, excludes what's wrong, and commits the rest atomically. Digital PDFs use
native `pdfplumber` text/table extraction; image-only pages fall back to local Tesseract, are visibly
marked, and are review-gated. Nothing leaves the machine.

## Delivery shape

**Milestone A — CSV end-to-end (shippable on its own).** Contracts, the preview/commit refactor, batch
persistence, multipart upload, and the real Import page. At the end of A a user can upload a CSV from the
browser, review it, and commit it.

**Milestone B — PDF + OCR.** `PdfStatementExtractor`, page-selective Tesseract fallback, and the
PDF-specific issue codes/UI states. Plugs into A's preview and commit layers without changing them.

Out of scope: the Activity ledger page (separate plan). Everything in the spec's "Not required in this
slice" stays out.

---

# Milestone A — CSV end-to-end

## A1. Format-neutral contracts

New: `src/pfa/ingestion/candidates.py`. Pure data, no I/O, no library types.

```python
@dataclass(frozen=True, slots=True)
class StatementSource:  # the only thing an extractor receives
    path: Path  # staged temp file, generated name
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str


@dataclass(slots=True)
class CandidateIssue:
    code: str  # "INVALID_DATE", "AMBIGUOUS_SIGN", "OCR_LOW_CONFIDENCE", ...
    message: str  # user-facing, sanitized
    severity: str  # "error" (blocking) | "warning"


@dataclass(slots=True)
class CandidateTransaction:
    candidate_id: str  # stable within batch, e.g. "c7"
    transaction_date: str | None
    posted_date: str | None
    raw_description: str
    normalized_description: str
    amount_minor: int | None  # absolute magnitude, matches TransactionModel
    direction: str | None  # "debit" | "credit"
    currency: str
    account_hint: str | None
    external_id: str | None
    kind: str | None  # source-provided, optional
    category: str | None
    transfer_purpose: str | None
    source_format: str  # "csv" | "pdf"
    source_line: int | None  # CSV line number
    source_page: int | None  # PDF page number
    extraction_method: str  # "csv" | "pdf_text" | "ocr"
    raw_fields: dict[str, str]  # original extracted values, shown as provenance
    issues: list[CandidateIssue]
    duplicate_of: int | None  # ledger transaction id
    included: bool = True
    # state property -> "error" | "warning" | "valid"


@dataclass(slots=True)
class ExtractionResult:
    candidates: list[CandidateTransaction]
    extractor: str  # "csv/1", "pdfplumber/0.11.7"
    page_count: int | None
    detected_account: str | None
    detected_currency: str | None
    issues: list[CandidateIssue]  # batch-level


class StatementExtractor(Protocol):
    name: str

    def extract(self, source: StatementSource) -> ExtractionResult: ...
```

Issue codes live as module constants here so API, service and tests share one vocabulary. No `pdfplumber`,
`PIL`, or `csv` object ever crosses this boundary.

## A2. Refactor `ImportService` behind the new boundary

`src/pfa/ingestion/service.py` — split the one loop into three stages, reusing the existing helpers
verbatim (`_parse_date`, `_parse_amount`, `normalize_description`, `merchant_from_description`,
`transaction_fingerprint`, `classify_known`, `_classification_from_rule`).

- `validate(candidates)` — date, description, amount, currency-is-GBP, and **enum membership of a
  source-provided `kind`/`category`/`transfer_purpose`**. Today a bad `kind` only fails at classification
  time (`service.py:63-66`); it must fail at validation so it blocks preview too. Attach issues; never raise.
- `resolve_duplicates(candidates, uow)` — the existing occurrence counter (`service.py:110,132-148`) plus
  `uow.transactions.find_fingerprint`, moved out intact. Sets `duplicate_of`.
- `commit(candidates, uow, source_label)` — rule match → classification → `accounts.get_or_create` →
  `TransactionModel` → `transactions.add`, for included, non-duplicate, non-error rows only.

Public surface:

```python
def import_result(extraction, *, source_label, account_override=None, dry_run=False) -> ImportResult
def import_csv(path: Path, dry_run: bool = False) -> ImportResult   # thin CLI/legacy adapter
```

**Behavior lock:** `import_csv` keeps its exact current semantics and error strings
(`"row {line}: invalid date 'x'"`, `"unsupported currency 'X'; PFA v0.1 supports GBP only"`, etc.) so
`tests/integration/test_cli.py` and `test_api.py` pass untouched. Issue *codes* are added alongside the
existing messages, not instead of them.

Move `src/pfa/ingestion/parsers/csv.py` → `src/pfa/ingestion/extractors/csv.py` and grow it into
`CsvStatementExtractor`: keep `read_csv_rows` as the internal reader (aliases, `utf-8-sig`, line numbers
from 2), add `csv.Sniffer` for `, ; \t` when unambiguous, add the debit/credit two-column mode, emit
candidates. Delete `parsers/`.

## A3. Import batch persistence

One table. Candidates are staged as a JSON blob, per the confirmed decision — they live 24 hours and are
only ever read whole.

`src/pfa/db/models.py`:

```python
class ImportBatchModel(Base):
    __tablename__ = "import_batches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)   # uuid4().hex
    original_filename: Mapped[str] = mapped_column(String(255))
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    extractor: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), index=True)
    destination_account: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detected_account / detected_currency: Mapped[str | None]
    statement_start / statement_end: Mapped[date | None]
    page_count: Mapped[int | None]
    candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # nulled on expiry/commit
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    counts_json: Mapped[str] = mapped_column(Text, default="{}")
    committed_transaction_ids_json: Mapped[str | None]
    created_at / updated_at / expires_at / committed_at
```

Status: `preview_ready | blocked | committed | discarded | expired | failed` (`extracting` is reserved in
the response contract but unused while extraction is synchronous).

- Migration `alembic/versions/0002_import_batches.py`, `down_revision = "0001_initial"`, hand-written
  `op.create_table` + explicit `op.create_index` in the style of `0001_initial.py`.
- `ImportBatchRepository` in `src/pfa/db/repositories.py`; wire into `UnitOfWork`
  (`src/pfa/db/unit_of_work.py`).
- Raw bytes are never persisted. `TransactionModel.import_source` gets `f"import_batch:{batch_id}"` for
  uploads (CLI keeps `str(path)`) — provenance without leaking a filename or path into the ledger.

## A4. Upload policy and staging

New: `src/pfa/ingestion/upload.py`.

- `stage_upload(file: UploadFile, settings) -> StatementSource` — reject early on `Content-Length` >
  `max_upload_bytes`, then copy in 64 KiB chunks to `{upload_dir}/{uuid4().hex}{ext}`, aborting and
  unlinking the moment the running total exceeds the cap. SHA-256 is computed during the same pass.
  Filename is generated; the original name is metadata only, never a path component.
- Signature check: `.pdf` must start `%PDF-`; `.csv` must decode as UTF-8/UTF-8-BOM. Extension and media
  type are checked too, but neither alone is sufficient.
- `sweep_upload_dir(settings)` — deletes stragglers, called from the FastAPI `lifespan` startup
  (`src/pfa/api/app.py:63`) alongside `sweep_expired_batches`.
- Every endpoint deletes its staged file in a `finally`, success or failure.

New `Settings` fields (`src/pfa/config.py`, `PFA_` prefix, same `Field(...)` bounds style):
`upload_dir=Path("data/uploads")`, `max_upload_bytes=15*1024*1024`, `max_pdf_pages=100`,
`max_candidate_rows=10_000`, `import_batch_ttl_hours=24`, `extraction_timeout_seconds=60`, plus the OCR
fields in B1.

> **Deviation to note:** Starlette materialises `UploadFile` before the handler runs, so the cap is enforced
> at `Content-Length` and again while copying, not inside the multipart parser. Memory stays bounded
> because Starlette spools above 1 MB to disk. Parsing multipart by hand to gain a stricter guarantee is
> not worth it for a localhost-bound app.

## A5. Batch lifecycle service + API

New: `src/pfa/ingestion/batches.py` — `create_batch`, `load_batch`, `apply_patch`, `commit_batch`,
`discard_batch`, `expire_if_due`, `sweep_expired_batches`. Extraction runs synchronously; the response
shape already allows an async status later.

Expiry is lazy: any read/patch/commit checks `expires_at`, and if due flips status to `expired`, nulls
`candidates_json`, and returns `410`. The startup sweep catches batches nobody revisits. No scheduler.

Endpoints go in `src/pfa/api/app.py` (single-module app, no routers — that's the house pattern), each
using the existing `open_services` / `try` / `finally`-or-`except` shape, with response schemas declared
inline at the top of the file next to `TransactionResponse`.

| Endpoint | Notes |
|---|---|
| `POST /imports/preview` | `multipart/form-data`: `file`, optional `account` form field. Returns batch id, status, counts, detected metadata, candidates, issues. |
| `GET /imports/{batch_id}` | Full batch state. Survives a browser refresh. Never returns raw bytes. |
| `PATCH /imports/{batch_id}` | `{account, excluded_candidate_ids, amount_mode}`. Revalidates and recomputes duplicates. |
| `POST /imports/{batch_id}/commit` | Requires `preview_ready`, unexpired, no blocking errors among included rows. Atomic. |
| `DELETE /imports/{batch_id}` | Discards an uncommitted batch and its staged rows. Committed batches → `409`. |
| `GET /accounts` | New, ~6 lines over `uow.accounts.all()`. The Import page needs a destination picker. |
| `POST /imports` | Kept, `deprecated=True` in OpenAPI. Never called from the browser. |

Errors on the new endpoints use `detail={"code": ..., "message": ...}` so the UI can branch on a code. This
is additive — existing string-`detail` endpoints are untouched. Parser exceptions are caught and turned
into sanitized batch issues; no paths, stack traces, or statement text reach the API or the logs.

Commit atomicity comes free from the existing session boundary: `commit_batch` runs inside one
`open_services` session and `close_services(engine, services, False)` rolls the whole thing back on any
exception (`src/pfa/services/runtime.py:21-41`).

Add `python-multipart` to `pyproject.toml` — FastAPI raises at route definition without it.

## A6. Import page

Confirmed shape: one shell, hash-routed view.

- `src/pfa/web/index.html` — wrap the current dashboard body in `<section id="view-overview">`, add a
  sibling `<section id="view-import" hidden>`: dropzone + real `<input type="file">` (label-wrapped,
  keyboard-operable), copy naming accepted formats / 15 MiB cap / OCR review warning / local-processing;
  summary cards (rows, valid, duplicates, warnings, errors); filter chips (all / actionable / errors /
  warnings / duplicates / excluded); preview table with date, description, debit/credit, amount, account,
  provenance, status; expandable row detail showing source line-or-page and `raw_fields`; destination
  account select; commit button with an explicit disabled reason; confirmation step and post-commit
  summary. Bump `app.js?v=5` → `?v=6`.
- `src/pfa/web/app.js` — replace the nav toast interceptor (line 102) with an ~8-line `hashchange` router
  toggling `hidden` and `is-active`; call `initImport()` when the route is `import`. Overview keeps its
  `FALLBACK_DATA` demo behavior.
- `src/pfa/web/import.js` (new) — all import logic: upload, render, filter, exclude, patch, commit,
  discard, and the empty / extracting / expired / failed / OCR-unavailable / low-confidence /
  unsupported-PDF states.
- `src/pfa/web/styles.css` — import view styles built on the existing `--ink/--navy/--orange/--radius`
  tokens; no new palette.

Language stays distinct throughout: **uploaded → extracted → ready → committed**. A preview is never
called "imported".

---

# Milestone B — PDF + OCR

## B1. Dependencies and the renderer decision

The spec requires the renderer be recorded here.

| Dependency | License | Role |
|---|---|---|
| `pdfplumber >= 0.11.4` | MIT | Native text, words with coordinates, tables |
| `pdfminer.six` (transitive) | MIT | PDF parsing, encryption detection |
| `pypdfium2` (transitive) | Apache-2.0 / BSD-3-Clause | **The page renderer** |
| `Pillow` (transitive) | MIT-CMU | Image buffer for OCR and test fixtures |
| Tesseract | Apache-2.0 | External binary, invoked via `subprocess` |

**Renderer: `pypdfium2`, reached through `pdfplumber`'s own `Page.to_image(resolution=...)`.** pdfplumber
0.11 replaced its Wand/ImageMagick backend with pypdfium2, so this is a permissive-licensed renderer we get
for free with the extractor — no second PDF dependency, no poppler binary, no PyMuPDF licensing question.
At implementation time record the resolved `pypdfium2` version from `uv.lock` in the README.

**No `pytesseract`.** Tesseract is invoked directly: `subprocess.run(["tesseract", "stdin", "stdout", "-l",
lang, "--psm", "6", "tsv"], input=png_bytes, timeout=settings.ocr_timeout_seconds)`, parsed with
`csv.DictReader(delimiter="\t")`. That is ~20 lines, gives us the timeout bound the spec asks for,
turns `FileNotFoundError` into `OCR_UNAVAILABLE` cleanly, and is one less thing to install and mock.

New settings: `ocr_enabled=True`, `ocr_language="eng"`, `ocr_dpi=300`, `ocr_timeout_seconds=30.0`,
`ocr_min_confidence=80.0`.

> Tesseract is **not installed on this machine**. The OCR-unavailable path is therefore the default local
> experience and must be genuinely actionable; OCR success tests drive a stubbed runner (see B4).

## B2. `PdfStatementExtractor`

New: `src/pfa/ingestion/extractors/pdf.py`.

1. Verify `%PDF-` (already done in A4), open with `pdfplumber.open`. `PDFPasswordIncorrect` →
   `PDF_ENCRYPTED`. Page count over `max_pdf_pages` → `PDF_TOO_MANY_PAGES`. Candidates over
   `max_candidate_rows` → `TOO_MANY_ROWS`.
2. Per page: `page.extract_table()` first; if that yields nothing usable, fall back to grouping
   `page.extract_words()` into lines by `top` within a tolerance.
3. **Header matching is explicit and tested** — an alias map for `date`, `description|details|narrative`,
   `debit|paid out|withdrawn`, `credit|paid in|received`, `amount|value`, `balance`,
   `reference|transaction id`. No fuzzy guessing.
4. **Balance is mapped but never emitted as a transaction.** Reconciliation mismatches produce warnings only.
5. Amount normalization handles `(1,234.56)`, `1,234.56-`, `£`, thousands separators, and unicode minus.
   Two populated sign sources that disagree (or a parenthesised value in a credit column) →
   `AMBIGUOUS_SIGN`, blocking.
6. Wrapped descriptions join into the previous candidate **only** when the line has no date, no amount, and
   sits within `line_height * 1.6` of it. Anything else becomes an `UNJOINED_CONTINUATION` warning row —
   never silently dropped.
7. Every candidate carries `source_page`, its line/table position, and its raw text.
8. Zero candidates after both passes → `PDF_NOT_EXTRACTABLE`, with copy suggesting a CSV download or a
   better-quality statement.

## B3. Page-selective OCR

New: `src/pfa/ingestion/extractors/ocr.py`.

- **Eligibility** (tested, and *not* "every page"): a page qualifies only when
  `len(page.extract_text() or "") < MIN_TEXT_CHARS` (~40), or it has no words and an image covering ≥60% of
  the page area.
- Render only eligible pages at `ocr_dpi` via `page.to_image().original` → PNG bytes → Tesseract TSV.
- **One OCR per page per batch**, enforced by a `dict[int, list[OcrWord]]` cache keyed by page number.
- Word boxes and confidences are preserved and reused for the same line/column reconstruction as B2.
- Every OCR-derived candidate and field is marked `extraction_method="ocr"` and carries a warning.
- Any word feeding a date, amount digit, decimal separator, currency symbol, or debit/credit marker with
  `conf < ocr_min_confidence` → `OCR_LOW_CONFIDENCE`, **blocking** until the user confirms or excludes.
- Tesseract missing or non-zero exit → batch issue `OCR_UNAVAILABLE` with an actionable message. The
  digital-text pages of the same statement still preview fine.

## B4. PDF fixtures — no binaries in the repo

`tests/fixtures/pdf_builder.py`:

- **Digital PDF**: ~40-line raw-PDF text writer (a content stream of positioned `Tj` operators). No new
  dependency, and layout variants — wrapped descriptions, debit/credit columns, a balance column,
  parenthesised amounts — become one-line changes.
- **Scanned PDF**: render the same rows to an image with Pillow (already present via pdfplumber) and
  `image.save(path)` — Pillow writes image-only PDFs natively.
- **Encrypted PDF**: monkeypatch `pdfplumber.open` to raise `PDFPasswordIncorrect`. This tests *our*
  handling rather than pdfminer's, and needs no encryptor dependency.
- **Fake `.pdf`**, **oversize**, **over-page-limit**: generated inline in `tmp_path`.
- **OCR success**: inject a stub runner returning canned TSV. The real Tesseract path is exercised only by
  the unavailable/error tests, which pass on a machine without the binary.

---

## Files touched

| Area | Files |
|---|---|
| New contracts | `src/pfa/ingestion/candidates.py` |
| Extractors | `src/pfa/ingestion/extractors/{csv,pdf,ocr}.py` (csv moved from `parsers/csv.py`; delete `parsers/`) |
| Services | `src/pfa/ingestion/{service,batches,upload}.py` |
| Persistence | `src/pfa/db/{models,repositories,unit_of_work}.py`, `alembic/versions/0002_import_batches.py` |
| API | `src/pfa/api/app.py` (new endpoints + lifespan sweeps + `GET /accounts` + deprecate `POST /imports`) |
| Config | `src/pfa/config.py` |
| Frontend | `src/pfa/web/{index.html,app.js,styles.css}` + new `src/pfa/web/import.js` |
| Deps | `pyproject.toml`: `python-multipart` (A), `pdfplumber` (B) |
| Docs | `README.md` §Statement ingestion, §API, §Privacy/limitations; `docs/architecture.md` boundaries |

`src/pfa/cli/app.py` is unchanged — it keeps calling `import_csv(path)`.

## Deliberate simplifications

The spec's architecture diagram names five services. This plan consolidates them into
`extractors/*` + `ImportService` (validate / dedupe / commit stages) + `batches.py` (lifecycle), because
`StatementNormalizationService`, `ImportPreviewService` and `ImportCommitService` would each be a
single-caller class over functions that already exist. The `StatementExtractor` protocol boundary the spec
actually depends on — library objects never crossing it, extractors never writing to the ledger — is
preserved exactly. Say the word if you want the five-class layout instead.

## Verification

House test style: no `conftest.py`, `tmp_path` per test, real Alembic upgrade, `TestClient` as a context
manager (see `tests/integration/test_api.py:9-27`).

New tests:

- `tests/unit/test_statement_candidates.py` — validation issue codes, amount/sign normalization,
  occurrence-aware fingerprints for repeated identical rows.
- `tests/unit/test_csv_extractor.py` — header aliases, BOM, `, ; \t` delimiters, blank lines, line numbers,
  missing header, debit/credit two-column mode, both-columns-populated → `AMBIGUOUS_SIGN`.
- `tests/unit/test_pdf_extractor.py` (B) — digital fixture yields expected dates, descriptions, signed
  minor units, page provenance, **no balance rows**; header matching; wrapped-description joins;
  parenthesised and trailing-minus amounts.
- `tests/unit/test_ocr_fallback.py` (B) — native-text pages never invoke the runner (counting stub asserts
  zero calls); OCR runs **at most once** per eligible page; missing Tesseract → `OCR_UNAVAILABLE`;
  low-confidence amount blocks commit.
- `tests/integration/test_imports_api.py` — preview → patch (exclude a row, set account) → commit;
  re-upload reports duplicates and inserts zero rows; encrypted / fake `.pdf` / oversize / over-page-limit
  each return a specific code **and leave `upload_dir` empty**; expired batch → `410`; discard; committed
  batch cannot be deleted; `GET` after a simulated refresh restores the preview; injected `add()` failure
  on row 3 inserts **none**; no raw bytes anywhere in SQLite.

Regression: `tests/integration/{test_api,test_cli,test_migrations}.py` must pass unmodified.

```bash
cd pfa-ui-top-class
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy src                 # strict mode is house-wide; new code must be fully typed
uv run pytest
```

Manual end-to-end:

```bash
uv run pfa db migrate
uv run uvicorn pfa.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/#import`, drop `data/demo_transactions.csv`, confirm the preview shows rows and
duplicates (the demo data is already imported, so every row should read as a duplicate), exclude a row,
commit, and confirm the counts. Then re-check: `data/uploads/` is empty, and
`sqlite3 data/pfa.db "select status, candidates_json is null from import_batches"` shows the committed
batch with its staged rows released.

## Docs

README: replace the "Available now" / "Planned statement upload" split with the real capability —
browser upload, preview-before-commit, CSV and best-effort digital/simply-scanned PDF, GBP-only, OCR
requires review and a locally installed Tesseract, unsupported layouts fail clearly, uploaded bytes are
deleted after extraction and never stored. `docs/architecture.md`: note the extractor/preview/commit
boundary in the `ingestion` bullet.
