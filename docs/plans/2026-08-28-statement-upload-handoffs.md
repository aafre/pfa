# Statement Upload — Task Handoffs

Seven tasks. Each fenced block below is a complete, self-contained prompt for a fresh session or subagent.

- **Worktree:** `C:\projects\Personal Finance Agent\pfa-statement-upload` (branch `feat/statement-upload`)
- **Spec:** `docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md`
- **Plan:** `docs/plans/2026-08-28-statement-upload-implementation-plan.md`
- **Baseline at handoff:** 38 tests pass, ruff clean, mypy strict clean.

## Dependency graph

```
T1 ──┬── T2 ── T3 ── T4 ──┐
     │                    ├── T7
     └── T5 ── T6 ────────┘
```

After T1 lands, the **(T2 → T3 → T4)** chain and the **(T5 → T6)** chain are independent and can run in
parallel. They touch disjoint files except `src/pfa/config.py` and `pyproject.toml`; each prompt states
which keys it owns there.

---

## T1 — Contracts, CSV extractor, ImportService refactor

```
Work in the worktree C:\projects\Personal Finance Agent\pfa-statement-upload (branch feat/statement-upload).

Read first:
- docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md (the approved spec)
- docs/plans/2026-08-28-statement-upload-implementation-plan.md, sections A1 and A2
- src/pfa/ingestion/service.py, src/pfa/ingestion/parsers/csv.py, src/pfa/ingestion/fingerprint.py

Task: introduce format-neutral extraction contracts and split ImportService's single persist-while-parsing
loop into separate validate / dedupe / commit stages. No database schema changes, no API changes, no
frontend. This is pure refactor plus new contracts.

1. Create src/pfa/ingestion/candidates.py with StatementSource, CandidateIssue, CandidateTransaction,
   ExtractionResult, and a StatementExtractor Protocol, exactly as specified in plan section A1. Issue
   codes live here as module-level string constants so API, services and tests share one vocabulary.
   This module must import nothing from pdfplumber, PIL, csv, or sqlalchemy - it is pure data.

2. Move src/pfa/ingestion/parsers/csv.py to src/pfa/ingestion/extractors/csv.py (use git mv) and grow it
   into CsvStatementExtractor implementing the protocol. Keep read_csv_rows as the internal reader with its
   current behavior intact: header aliases, utf-8-sig BOM handling, line numbers starting at 2. Add
   csv.Sniffer detection for comma / semicolon / tab when unambiguous, and a debit/credit two-column mode.
   When both a debit and a credit column hold a value on one row, or neither is usable, emit the
   AMBIGUOUS_SIGN issue - never infer a sign silently. Delete the now-empty parsers/ package.

3. Refactor src/pfa/ingestion/service.py into three stages that reuse the existing helpers verbatim
   (_parse_date, _parse_amount, normalize_description, merchant_from_description, transaction_fingerprint,
   classify_known, _classification_from_rule):
   - validate(candidates): date, description, amount, currency-is-GBP, and enum membership of any
     source-provided kind/category/transfer_purpose. Note that a bad kind currently only fails at
     classification time (service.py:63-66); it must now fail during validation so it blocks preview too.
     Attach CandidateIssues; never raise.
   - resolve_duplicates(candidates, uow): move the existing occurrence counter (service.py:110, 132-148)
     and the uow.transactions.find_fingerprint lookup out intact. Sets duplicate_of.
   - commit(candidates, uow, source_label): rule match, classification, accounts.get_or_create,
     TransactionModel, transactions.add - for included, non-duplicate, non-error rows only.
   Public surface: import_result(extraction, *, source_label, account_override=None, dry_run=False) and a
   thin import_csv(path, dry_run=False) adapter over CsvStatementExtractor.

CRITICAL behavior lock: import_csv must keep its exact current semantics AND its exact current error
strings, for example "row 4: invalid date 'x'" and "unsupported currency 'EUR'; PFA v0.1 supports GBP
only". Issue codes are added ALONGSIDE those messages, not instead of them. tests/integration/test_cli.py
and tests/integration/test_api.py must pass completely unmodified - if you find yourself editing them,
stop and reconsider the refactor instead.

src/pfa/cli/app.py must not change.

Add tests, following house style (no conftest.py, tmp_path per test, descriptive
test_<subject>_<behavior> names, self-contained):
- tests/unit/test_statement_candidates.py: validation issue codes, amount/sign normalization, and
  occurrence-aware fingerprints for repeated identical rows within one statement.
- tests/unit/test_csv_extractor.py: header aliases, BOM, comma/semicolon/tab delimiters, blank lines,
  line numbers, missing header row, debit/credit two-column mode, and both-columns-populated ->
  AMBIGUOUS_SIGN.

Gates (all must pass before you commit):
  uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
mypy is strict mode repo-wide; ruff line-length is 100.

Commit on green with a conventional-commit message. Report what you changed and confirm the existing
integration tests were not modified.
```

---

## T2 — Import batch persistence

```
Work in the worktree C:\projects\Personal Finance Agent\pfa-statement-upload (branch feat/statement-upload).
T1 (extraction contracts + ImportService refactor) has already landed - read
src/pfa/ingestion/candidates.py first, it defines the CandidateTransaction shape you must serialize.

Read first:
- docs/plans/2026-08-28-statement-upload-implementation-plan.md, section A3
- docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md, "Data contracts" section
- src/pfa/db/models.py, src/pfa/db/repositories.py, src/pfa/db/unit_of_work.py
- alembic/versions/0001_initial.py (the migration house style)

Task: persist import batches. One table only - candidate rows are staged as a JSON blob, NOT a second
table. This was a deliberate decision: candidates live 24 hours and are only ever read whole, so a row
model plus repository plus indexes would be ~150 lines serving no query.

1. Add ImportBatchModel to src/pfa/db/models.py per plan section A3. Primary key is a uuid4().hex string.
   Statuses: preview_ready | blocked | committed | discarded | expired | failed. (The spec also lists
   "extracting"; reserve it in the response contract but do not use it - extraction is synchronous.)
   candidates_json is nullable Text and gets nulled on expiry and after commit. Also persist issues_json,
   counts_json, and committed_transaction_ids_json.

2. Write alembic/versions/0002_import_batches.py with down_revision = "0001_initial". Hand-write it in the
   style of 0001_initial.py: explicit op.create_table with sa.Column definitions mirroring the ORM columns
   one-to-one, explicit op.create_index calls, and a symmetric downgrade. Do not autogenerate.

3. Add ImportBatchRepository to src/pfa/db/repositories.py (get, add, list_expired, and whatever the
   lifecycle needs) and wire it into UnitOfWork in src/pfa/db/unit_of_work.py.

4. Add candidate serialization helpers - to and from the JSON blob - next to the contracts in
   src/pfa/ingestion/candidates.py. Round-tripping a list of CandidateTransaction must be lossless.

Constraints:
- Raw uploaded bytes are NEVER persisted. No BLOB column, no base64.
- Do not touch src/pfa/api/app.py or anything under src/pfa/web/ - that is T3 and T4.

Add tests (house style: no conftest.py, tmp_path, real Alembic upgrade - see tests/integration/test_api.py
lines 9-27 for the pattern):
- Migration applies and rolls back cleanly (extend or mirror tests/integration/test_migrations.py).
- Candidate JSON round-trip is lossless, including issues and raw_fields.

Gates: uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
mypy strict, ruff line-length 100. Commit on green.
```

---

## T3 — Upload policy, batch lifecycle, API endpoints

```
Work in the worktree C:\projects\Personal Finance Agent\pfa-statement-upload (branch feat/statement-upload).
T1 (contracts + refactor) and T2 (ImportBatchModel + migration + repository) have landed. Read
src/pfa/ingestion/candidates.py and src/pfa/db/models.py first.

Read first:
- docs/plans/2026-08-28-statement-upload-implementation-plan.md, sections A4 and A5
- docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md, "HTTP API" and "Security and privacy"
- src/pfa/api/app.py (single-module app, no routers - that IS the house pattern, keep it)
- src/pfa/services/runtime.py (open_services / close_services session boundary)

Task: multipart upload, bounded staging, batch lifecycle, and the new endpoints. CSV only - the PDF
extractor arrives separately and must plug in without changing anything you write here.

1. src/pfa/ingestion/upload.py - stage_upload(file, settings) -> StatementSource. Reject early when
   Content-Length exceeds max_upload_bytes, then copy in 64 KiB chunks to
   {upload_dir}/{uuid4().hex}{ext}, aborting and unlinking the instant the running total exceeds the cap.
   Compute SHA-256 in the same pass. The filename is generated - the original name is metadata only and
   must never become a path component. Validate magic bytes (.pdf must start %PDF-, .csv must decode as
   UTF-8/UTF-8-BOM) as well as extension and media type; extension alone is insufficient. Add
   sweep_upload_dir(settings) for startup cleanup. Every endpoint deletes its staged file in a finally
   block, on success and on failure alike.

2. src/pfa/ingestion/batches.py - create_batch, load_batch, apply_patch, commit_batch, discard_batch,
   expire_if_due, sweep_expired_batches. Extraction is synchronous. Expiry is lazy: every read/patch/commit
   checks expires_at and, if due, flips status to expired, nulls candidates_json, and the endpoint returns
   410. The startup sweep catches batches nobody revisits. Do NOT add a scheduler or background task.

3. Endpoints in src/pfa/api/app.py, each following the existing open_services / try / finally-or-except
   shape, with response schemas declared inline at the top of the file next to TransactionResponse:
   - POST   /imports/preview             multipart: file + optional account form field
   - GET    /imports/{batch_id}          full state; must survive a browser refresh; never returns raw bytes
   - PATCH  /imports/{batch_id}          {account, excluded_candidate_ids, amount_mode}; revalidate and
                                         recompute duplicates
   - POST   /imports/{batch_id}/commit   requires preview_ready, unexpired, no blocking errors among
                                         included rows
   - DELETE /imports/{batch_id}          discard uncommitted; committed -> 409
   - GET    /accounts                    ~6 lines over uow.accounts.all(); the Import page needs a
                                         destination picker
   Keep the existing POST /imports but mark it deprecated=True in OpenAPI.

   New endpoints return errors as detail={"code": ..., "message": ...} so the UI can branch on a code.
   This is additive - do not change the existing string-detail endpoints.

4. Wire sweep_upload_dir and sweep_expired_batches into the FastAPI lifespan startup (app.py:63).

5. Add settings to src/pfa/config.py - YOU OWN ONLY THESE KEYS (a parallel task owns the OCR/PDF ones):
   upload_dir=Path("data/uploads"), max_upload_bytes=15*1024*1024, max_candidate_rows=10_000,
   import_batch_ttl_hours=24, extraction_timeout_seconds=60. Use the same Field(...) bounds style as the
   existing agent_* settings.

6. Add python-multipart to pyproject.toml dependencies - FastAPI raises at route-definition time without it.

Security requirements that are not optional: never accept a client-supplied filesystem path; do not log
file contents, descriptions, account numbers, or extracted rows; redact likely account identifiers from
user-facing batch errors; turn parser exceptions into sanitized batch issues that expose no local paths,
stack traces, or raw statement text.

Commit atomicity is already provided by the existing session boundary - commit_batch runs inside one
open_services session and close_services(engine, services, False) rolls everything back on exception. Do
not invent a second transaction mechanism.

Add tests/integration/test_imports_api.py (house style: no conftest.py, tmp_path, real Alembic upgrade,
TestClient as a context manager):
- preview -> patch (exclude a row, set account) -> commit, with correct counts at each step
- re-upload of the same CSV reports duplicates and inserts zero new rows
- fake .pdf and oversize each return a specific code AND leave upload_dir empty afterwards
- expired batch returns 410; discard works; a committed batch cannot be deleted
- GET after a simulated refresh restores the preview
- an injected failure in TransactionRepository.add on the third row inserts NONE of them
- no raw uploaded bytes anywhere in SQLite

tests/integration/test_api.py and test_cli.py must still pass unmodified.

Gates: uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
mypy strict, ruff line-length 100. Commit on green.
```

---

## T4 — Import page

```
Work in the worktree C:\projects\Personal Finance Agent\pfa-statement-upload (branch feat/statement-upload).
T1-T3 have landed; the import API is live. Start by reading the new endpoints and response schemas in
src/pfa/api/app.py so the frontend matches the real contract.

Read first:
- docs/plans/2026-08-28-statement-upload-implementation-plan.md, section A6
- docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md, "UI requirements"
- docs/plans/2026-08-28-pfa-ui-ux-plan.md (visual direction and accessibility bar)
- src/pfa/web/index.html, app.js, styles.css

Stack reality: vanilla HTML/CSS/JS, no build step, no package.json, no framework. FastAPI serves
index.html at GET / and mounts /static. Keep it that way - do not introduce a bundler or a dependency.

Task: turn the Import nav item from a "coming next" toast into a real page, as an in-shell hash-routed
view (this shape was chosen deliberately: one shell, shared nav, shared tokens).

1. src/pfa/web/index.html - wrap the current dashboard body in <section id="view-overview">, add a sibling
   <section id="view-import" hidden> containing: a dropzone plus a real label-wrapped <input type="file">
   that works by keyboard; copy naming accepted formats, the 15 MiB cap, the OCR review warning, and that
   processing is local; summary cards for rows / valid / duplicates / warnings / errors; filter chips for
   all / actionable / errors / warnings / duplicates / excluded; a preview table with date, description,
   debit/credit, amount, account, provenance and status; expandable row detail showing the source line or
   page and the original extracted values; a destination account select; a commit button that states its
   disabled reason explicitly; a confirmation step; and a post-commit summary with next actions. Bump the
   script cache-buster app.js?v=5 -> ?v=6.

2. src/pfa/web/app.js - replace the nav toast interceptor at line 102 with a small hashchange router
   (~8 lines) that toggles hidden and the is-active nav class, and calls initImport() when the route is
   "import". Overview keeps its existing FALLBACK_DATA demo behavior untouched.

3. src/pfa/web/import.js (new) - all import logic: upload, render, filter, exclude, patch, commit, discard,
   plus the empty / extracting / expired / failed / OCR-unavailable / low-confidence / unsupported-PDF
   states. Use fetch with an indeterminate "Extracting..." state rather than pulling in XMLHttpRequest
   for a progress bar.

4. src/pfa/web/styles.css - build on the existing --ink / --navy / --orange / --radius tokens. No new
   palette.

Non-negotiable: state language stays distinct throughout - uploaded, extracted, ready, committed. A
preview is NEVER labelled "imported". Accessibility per the UI/UX plan: semantic headings, labelled
controls, visible focus, keyboard operation, 44px-class targets, contrast-safe semantic colours, no
color-only meaning, reduced-motion support.

Verify by driving the real app, not by reasoning about it:
  uv run pfa db migrate
  uv run uvicorn pfa.api.app:app --host 127.0.0.1 --port 8000
Open http://127.0.0.1:8000/#import, upload data/demo_transactions.csv, and walk the whole journey: preview
appears with per-row status, exclude a row, commit, confirm the summary counts. Then confirm
data/uploads/ is empty afterwards. Use the Chrome MCP browser tools to click through it and screenshot the
result.

Gates: uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
Commit on green, and include the screenshots or a description of what you saw in your report.
```

---

## T5 — PDF extractor (native)

```
Work in the worktree C:\projects\Personal Finance Agent\pfa-statement-upload (branch feat/statement-upload).
T1 (extraction contracts) has landed. Read src/pfa/ingestion/candidates.py and
src/pfa/ingestion/extractors/csv.py first - you are writing a sibling extractor against the same protocol.

This task is independent of T2/T3/T4 and may be running in parallel with them. Stay inside the files
listed below to avoid collisions.

Read first:
- docs/plans/2026-08-28-statement-upload-implementation-plan.md, sections B1, B2, B4
- docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md, "PDF extraction requirements"

Dependencies to add to pyproject.toml: pdfplumber>=0.11.4 only. It brings pdfminer.six (MIT), Pillow
(MIT-CMU) and pypdfium2 (Apache-2.0/BSD-3) transitively. Do NOT add PyMuPDF (AGPL - explicitly excluded by
the spec), pdf2image (needs a poppler binary), or pytesseract. After uv sync, record the resolved
pypdfium2 version from uv.lock in your report - pypdfium2 is the chosen page renderer and the spec
requires it be recorded. You reach it through pdfplumber's own Page.to_image(resolution=...); do not
import it directly.

Task: src/pfa/ingestion/extractors/pdf.py - PdfStatementExtractor implementing StatementExtractor.

1. Open with pdfplumber.open. PDFPasswordIncorrect -> PDF_ENCRYPTED. Page count over max_pdf_pages ->
   PDF_TOO_MANY_PAGES. Candidates over max_candidate_rows -> TOO_MANY_ROWS. (The %PDF- signature check
   already happens upstream in the upload policy.)
2. Per page: try page.extract_table() first; if it yields nothing usable, fall back to grouping
   page.extract_words() into lines by top within a tolerance.
3. Header matching must be EXPLICIT and tested - an alias map for date, description|details|narrative,
   debit|paid out|withdrawn, credit|paid in|received, amount|value, balance, reference|transaction id.
   No fuzzy or heuristic guessing.
4. Balance is mapped but NEVER emitted as a transaction. Reconciliation mismatches produce warnings only.
5. Amount normalization handles (1,234.56), 1,234.56-, currency symbols, thousands separators and unicode
   minus. Two sign sources that disagree, or a parenthesised value in a credit column, -> AMBIGUOUS_SIGN
   as a blocking error.
6. Wrapped descriptions join into the previous candidate ONLY when the line has no date, no amount, and
   sits within line_height * 1.6 of it. Anything else becomes an UNJOINED_CONTINUATION warning row - never
   silently discarded.
7. Every candidate carries source_page, its line or table position, and its raw text.
8. Zero candidates after both passes -> PDF_NOT_EXTRACTABLE, with copy suggesting a CSV download or a
   better-quality statement.

Leave a clean seam for OCR: the page loop should call a page-word provider that a follow-up task can swap
for an OCR path. Do not implement OCR here.

Settings you own in src/pfa/config.py (a parallel task owns the upload_* keys - add only this one):
max_pdf_pages=100.

tests/fixtures/pdf_builder.py - NO binary fixtures in the repo. Write a ~40-line raw-PDF text writer that
emits a content stream of positioned Tj operators. This needs no dependency and makes layout variants
(wrapped descriptions, debit/credit columns, a balance column, parenthesised amounts) one-line changes.

tests/unit/test_pdf_extractor.py - a digital fixture yields the expected dates, descriptions, signed minor
units and page provenance, and NO balance rows; header matching; wrapped-description joins; parenthesised
and trailing-minus amounts; encrypted PDFs (monkeypatch pdfplumber.open to raise PDFPasswordIncorrect
rather than shipping an encrypted binary); over-page-limit.

Gates: uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
mypy strict, ruff line-length 100. Commit on green and report the pypdfium2 version.
```

---

## T6 — Page-selective OCR fallback

```
Work in the worktree C:\projects\Personal Finance Agent\pfa-statement-upload (branch feat/statement-upload).
T5 (native PDF extractor) has landed. Read src/pfa/ingestion/extractors/pdf.py first - it left a seam
where a page-word provider can be swapped for an OCR path.

Read first:
- docs/plans/2026-08-28-statement-upload-implementation-plan.md, sections B3 and B4
- docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md, "PDF extraction requirements" and
  "Deferred follow-up"

IMPORTANT ENVIRONMENT FACT: Tesseract is NOT installed on this machine and is not on PATH. The
OCR-unavailable path is therefore the default local experience and must be genuinely actionable. Drive the
OCR success tests with a stubbed runner; the real binary path is exercised only by the unavailable/error
tests, which must pass on a machine without Tesseract. Do not install Tesseract.

Task: src/pfa/ingestion/extractors/ocr.py - bounded, page-selective, local OCR.

1. Invoke Tesseract directly, no pytesseract wrapper:
   subprocess.run(["tesseract", "stdin", "stdout", "-l", lang, "--psm", "6", "tsv"], input=png_bytes,
                  timeout=settings.ocr_timeout_seconds)
   Parse the TSV with csv.DictReader(delimiter="\t") to recover left/top/width/height/conf/text. That is
   ~20 lines, gives the time bound the spec requires, and turns FileNotFoundError into OCR_UNAVAILABLE
   cleanly. Positional TSV - never plain text output; row and column reconstruction needs the coordinates.

2. Eligibility, which must be tested and must NOT be "every page": a page qualifies only when
   len(page.extract_text() or "") < MIN_TEXT_CHARS (~40), or it has no words and an image covering >=60%
   of the page area. Run native extraction first, always.

3. Render only eligible pages, at ocr_dpi, via pdfplumber's page.to_image().original -> PNG bytes.

4. ONE OCR per page per batch, enforced by a dict[int, list[OcrWord]] cache keyed by page number.

5. Every OCR-derived candidate and field is marked extraction_method="ocr" and carries a warning.

6. Any word feeding a date, amount digit, decimal separator, currency symbol, or debit/credit marker with
   conf < ocr_min_confidence is an OCR_LOW_CONFIDENCE BLOCKING error until the user confirms or excludes
   the row. OCR output is never authoritative financial data.

7. Tesseract missing or exiting non-zero -> batch issue OCR_UNAVAILABLE with an actionable message. The
   digital-text pages of the same statement must still preview fine.

Settings you own in src/pfa/config.py: ocr_enabled=True, ocr_language="eng", ocr_dpi=300,
ocr_timeout_seconds=30.0, ocr_min_confidence=80.0.

Scanned test fixture: render rows to an image with Pillow (already present transitively via pdfplumber)
and image.save(path) - Pillow writes image-only PDFs natively. Still no binary fixtures in the repo.

tests/unit/test_ocr_fallback.py must prove all four of the spec's acceptance points:
- native-text pages NEVER invoke the runner (use a counting stub and assert zero calls)
- OCR runs at most ONCE per eligible page
- missing Tesseract produces OCR_UNAVAILABLE, actionably
- a low-confidence amount BLOCKS commit

Gates: uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
mypy strict, ruff line-length 100. Commit on green.
```

---

## T7 — Observability, docs, regression sweep

```
Work in the worktree C:\projects\Personal Finance Agent\pfa-statement-upload (branch feat/statement-upload).
T1-T6 have all landed. This is the hardening and documentation pass - the last item in the spec's
suggested implementation order.

Read first:
- docs/plans/2026-08-28-statement-upload-pdf-extraction-design.md, "Observability", "Security and privacy",
  and especially "Acceptance criteria" (15 numbered items)
- docs/plans/2026-08-28-statement-upload-implementation-plan.md, "Docs" section
- src/pfa/observability.py (TimedOperation is the existing instrumentation boundary - reuse it)

Tasks:

1. Observability. Record structured, content-free events via TimedOperation for: upload accepted/rejected,
   extraction duration, extractor name, page and row counts, issue-code counts, preview ready,
   discarded/expired, and commit result. Logs must contain NO filenames, descriptions, raw rows, hashes, or
   account identifiers. Audit the code added by T3-T6 for leaks and fix any you find.

2. Walk all 15 acceptance criteria in the spec and verify each one against the actual code and tests. For
   any that is not genuinely covered, add the missing test. Report a criterion-by-criterion table with
   evidence - a test name or a file:line - for each. Do not mark anything satisfied that you have not
   actually run.

3. README.md - replace the "Available now" / "Planned statement upload" split under "Statement ingestion"
   with the real capability: browser upload, preview-before-commit, CSV plus best-effort digital and simply
   scanned PDF, GBP-only, OCR requires review AND a locally installed Tesseract, unsupported layouts fail
   clearly, uploaded bytes are deleted after extraction and never stored. Update the API section with the
   new endpoints and note that POST /imports is deprecated. Update the limitations paragraph - it
   currently claims browser upload and PDF extraction are absent. Record the pdfplumber and pypdfium2
   versions. Be accurate about limits: this is best-effort for supported layouts, NOT a claim of universal
   bank-PDF or OCR support.

4. docs/architecture.md - note the extractor / preview / commit boundary in the ingestion bullet, and add
   PDF/upload to the mermaid flowchart's ingestion node.

5. Full regression sweep:
     uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
   Then a manual end-to-end pass: uv run pfa db migrate, start uvicorn, upload a CSV and a PDF through
   http://127.0.0.1:8000/#import, and confirm data/uploads/ is empty afterwards and that
   "select status, candidates_json is null from import_batches" shows committed batches with staged rows
   released.

Report honestly: if any acceptance criterion is not met, say so plainly rather than papering over it.
Commit on green.
```
