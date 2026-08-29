# Statement Upload and PDF Extraction Requirements

Status: approved for implementation  
Scope decision: native PDF extraction first; basic local Tesseract OCR fallback for scanned pages  
Target: next Activity + Import vertical slice

## Objective

Let a local user upload a bank statement from the browser, inspect exactly what PFA extracted, fix or
exclude questionable rows, and explicitly commit valid transactions. Support the existing generic CSV
format and digital or simply scanned PDF statements without weakening PFA's deterministic, local-first financial
boundary.

Success means a user can import a supported statement without placing a file in a server-known path or
using the CLI. No transaction may enter the authoritative ledger before the user sees a preview and
confirms it.

## Current state

PFA already has a useful transaction ingestion core:

- `ImportService.import_csv(Path)` parses, normalizes, classifies, deduplicates, and persists rows.
- The CSV parser accepts common header aliases, UTF-8 BOM, signed amounts, several date formats, and
  optional bank transaction IDs.
- Duplicate fingerprints make exact re-imports idempotent while preserving legitimate repeated rows
  within one statement.
- Row errors are reported, dry-run rolls back state, unresolved expenses enter review, and persisted
  merchant rules are reused.
- The CLI imports local CSV files and `POST /imports` accepts a JSON server-local path.

The missing product boundary is file upload and review. The current API is path-based, has no import
batch lifecycle, and cannot receive browser file bytes. The dashboard Import route is a placeholder.
There is no PDF extractor.

## Scope

### Required

1. Browser drag/drop and file picker for `.csv` and `.pdf` statements.
2. Multipart upload endpoint with bounded, local temporary storage.
3. Shared source pipeline that accepts an uploaded stream or trusted CLI path.
4. Generic CSV extraction, preserving all current behavior.
5. Native PDF extraction plus bounded local Tesseract OCR fallback with source-page provenance.
6. Preview-before-commit workflow with row warnings and blocking errors.
7. Duplicate reporting against the existing ledger and within the uploaded statement.
8. Import batch persistence, status, counts, file metadata, and audit-safe error details.
9. Explicit commit, discard, and automatic expiry of uncommitted batches.
10. README and API documentation describing supported files and limitations.

### Not required in this slice

- Advanced image preprocessing, handwriting recognition, photographed statements, or a promise that
  every scanned layout is supported.
- Password-protected or encrypted PDFs.
- Guaranteed support for every bank layout.
- Cloud extraction services or sending statement content to an LLM.
- Non-GBP ledger aggregation.
- Automatic opening-balance creation.
- Credit-card payment matching.
- Editing the authoritative ledger through the preview table after commit.

## User journey

1. User opens Import and selects or drops one CSV or PDF, no larger than 15 MiB.
2. UI names supported formats and explains that scanned PDFs use OCR and require closer review.
3. PFA validates file signature, size, page count, and encryption before extraction.
4. PFA extracts candidate transactions locally and deletes the temporary raw file after extraction,
   whether extraction succeeds or fails.
5. UI shows statement metadata, detected account/currency/date range, row counts, duplicates, warnings,
   and errors.
6. User selects the destination account and resolves any required mapping or sign ambiguity.
7. User may exclude individual candidate rows. The original extracted value remains visible as
   provenance.
8. Commit remains disabled while blocking errors exist.
9. User confirms import. PFA revalidates the batch, persists eligible transactions in one transaction,
   records the result, and shows imported/duplicate/excluded/error counts.
10. Re-uploading the same statement previews existing transactions as duplicates and creates no new
    ledger entries unless genuinely new rows exist.

## Architecture

```text
Browser Import UI
    -> multipart upload
FastAPI import endpoints
    -> UploadPolicy: size, signature, encryption, page and row limits
    -> temporary file with generated name
StatementExtractionService
    -> CsvStatementExtractor | PdfStatementExtractor
    -> PDF native text/table extraction via pdfplumber
    -> page-level OCR fallback via renderer + Tesseract TSV/hOCR
    -> CandidateTransaction[] + source provenance + warnings
StatementNormalizationService
    -> dates, signed amounts, currency, account hints, external IDs
ImportPreviewService
    -> validation + existing fingerprint dedupe + batch persistence
User confirmation
    -> ImportCommitService / existing repositories
    -> SQLite authoritative ledger + immutable batch result
```

Introduce a format-neutral `StatementExtractor` protocol. It accepts a controlled source and returns an
`ExtractionResult`; extractors do not write to the ledger. Move reusable normalization, classification,
and fingerprint behavior behind a format-neutral import service. Retain a thin `import_csv(Path)` adapter
temporarily for CLI compatibility.

AI must not extract dates, balances, or amounts. Deterministic parsers own financial values. Existing
local classification may run after extraction; model unavailability must leave rows reviewable rather
than fail the statement.

## Chosen open-source stack

- `pdfplumber` is the default digital-PDF extractor. It provides characters, words, coordinates, lines,
  rectangles, and configurable table extraction while remaining compatible with PFA's MIT license.
- Tesseract is the optional local OCR engine for pages without usable native text. Consume positional
  TSV or hOCR output rather than plain text so row and column reconstruction retains coordinates.
- The PDF renderer must use an MIT-, BSD-, or Apache-compatible dependency. Record the selected renderer
  and version in the implementation plan; Tesseract does not accept PDF input directly.
- OCR is page-selective. Run native extraction first and OCR only pages that meet explicit no-text or
  unusable-text criteria. Cache one OCR result per page within the batch.
- PyMuPDF is not the default because its AGPL/commercial licensing conflicts with this MIT project unless
  the project makes a separate licensing decision. It may be reconsidered only after that review.
- MarkItDown and LangChain Extract are not authoritative ingestion dependencies. MarkItDown targets
  Markdown/LLM workflows, and LangChain Extract uses model-driven schema extraction. Either may be used
  in isolated experiments, but neither may determine ledger dates, signs, or amounts.

The extractor protocol must keep these libraries replaceable. Library-specific objects cannot cross the
`StatementExtractor` boundary.

## Data contracts

### Candidate transaction

Each candidate must contain:

- stable candidate ID within the batch;
- transaction date and optional posted date;
- raw and normalized description;
- absolute minor-unit amount plus debit/credit direction;
- ISO currency;
- optional account hint and external transaction ID;
- optional source-provided kind/category/transfer purpose;
- source format, page number or CSV line number, and original extracted fields;
- validation state: `valid`, `warning`, or `error`;
- zero or more machine-readable issue codes with user-facing messages;
- duplicate state and matched ledger transaction ID when available;
- included/excluded state.

### Import batch

Persist:

- generated batch ID;
- original filename, media type, size, SHA-256, extractor name/version;
- status: `extracting`, `preview_ready`, `blocked`, `committed`, `discarded`, `expired`, or `failed`;
- created, updated, expiry, and committed timestamps;
- detected account, currency, statement date range, and page count when available;
- candidate rows or an equivalent normalized staging representation;
- counts for total, valid, warning, error, duplicate, excluded, and imported rows;
- sanitized batch-level errors and warnings;
- committed transaction IDs.

Do not persist raw PDF/CSV bytes by default. Keep uncommitted normalized batches for 24 hours, then mark
them expired and remove staged row data. Committed batch metadata remains for provenance.

## HTTP API

### `POST /imports/preview`

Accept `multipart/form-data` with one `file`. Optional fields may include destination account ID and a
known adapter/profile identifier. Return `202` or `200` with batch ID, status, summary, detected metadata,
candidate rows, and issues. Initial implementation may extract synchronously if it remains within the
request timeout; the response contract must allow later asynchronous extraction.

### `GET /imports/{batch_id}`

Return batch state, preview metadata, candidate rows, issues, and counts. Never return raw statement
bytes.

### `PATCH /imports/{batch_id}`

Allow destination-account selection, supported column/sign mapping, and candidate inclusion changes.
Revalidate and recalculate duplicates after changes.

### `POST /imports/{batch_id}/commit`

Require a `preview_ready` batch with no blocking errors. Recheck expiry and ledger duplicates. Commit all
included non-duplicate rows atomically and return the immutable result summary.

### `DELETE /imports/{batch_id}`

Discard an uncommitted batch and staged data. Committed batches cannot be deleted through this endpoint.

Keep the existing path-based `POST /imports` only while CLI/internal callers migrate. Mark it deprecated
in OpenAPI and never call it from the browser.

## CSV requirements

- Preserve existing aliases and behavior.
- Accept UTF-8 and UTF-8 BOM. Other encodings fail with an actionable message in this slice.
- Detect comma, tab, and semicolon delimiters when unambiguous.
- Support either one signed `amount` column or separate debit/credit columns selected during preview.
- Never infer a debit/credit sign silently when both columns contain values or neither is usable.
- Ignore blank lines. Preserve CSV line number for every candidate and error.
- Detect missing required semantic fields—date, description, and amount—before commit.

## PDF extraction requirements

- Verify `%PDF-` signature and reject extension-only masquerading.
- Maximum 15 MiB, 100 pages, and 10,000 extracted candidate rows per upload.
- Reject encrypted/password-protected PDFs with a specific error.
- Attempt native text, word-coordinate, and table extraction with `pdfplumber` before OCR.
- Define and test the page-level OCR trigger. At minimum, consider a page OCR-eligible when it contains
  no native text, is substantially image-covered, or has too little usable text to identify a transaction
  structure. Do not OCR every page by default.
- Render only OCR-eligible pages at a bounded resolution, then invoke local Tesseract with the configured
  statement language. Preserve word boxes and confidence from TSV or hOCR output.
- Mark every OCR-derived candidate and field with `extraction_method=ocr`. Low-confidence dates, decimal
  separators, amount digits, currency, debit/credit markers, and signs are blocking errors until the user
  confirms or excludes the row.
- If neither native extraction nor OCR yields a deterministic transaction table/line structure, return
  `PDF_NOT_EXTRACTABLE`; suggest downloading CSV or a better-quality statement.
- Recognize common statement headers such as date, description/details, debit, credit, amount, balance,
  and transaction/reference ID. Header matching must be explicit and tested.
- Do not import balance values as transactions. Balance reconciliation may produce warnings only.
- Preserve page number, detected table/line position, and raw text for each candidate.
- Join wrapped descriptions only when deterministic adjacency rules apply. Ambiguous continuation lines
  become warnings or errors, never silently discarded.
- Parenthesized amounts, trailing minus signs, currency symbols, and thousands separators require tested
  normalization. Conflicting sign signals block commit.
- A generic extractor may support clearly structured tables. Bank-specific adapters can extend the same
  protocol without changing the preview or commit layers.

PDF extraction is best-effort for supported digital and simply scanned layouts, not a claim of universal
bank-PDF or OCR support.

## Validation and error handling

Blocking row errors include missing/invalid date, missing description, invalid amount, ambiguous sign,
unsupported currency, and invalid destination account. Batch-level errors include invalid signature,
oversize file, too many pages, encryption, unavailable extractor, parser crash, and no usable rows.

Valid rows may coexist with invalid rows in preview. The user can exclude bad rows and commit the rest.
Commit itself is atomic: any unexpected database failure rolls back every transaction from that commit.
Parser exceptions must become sanitized batch errors; they must not expose local paths, stack traces, or
raw statement text through the API.

## Security and privacy

- Bind to localhost by default, preserving the current security model.
- Never accept a client-supplied filesystem path for upload.
- Generate temporary names; never use the original filename as a path component.
- Enforce size while streaming, not only after buffering the whole body.
- Validate magic bytes, extension, and media type; extension alone is insufficient.
- Run extraction with time and resource bounds.
- Delete temporary raw files in `finally` paths and during startup cleanup.
- Do not log file contents, full descriptions, account numbers, or extracted rows.
- Redact likely account identifiers from user-facing batch errors.
- No network access is required for extraction or classification fallback.

## UI requirements

The Import route must be a real page, not a toast placeholder. It needs:

- dropzone plus accessible file input and keyboard operation;
- accepted formats, size limit, OCR review warning, quality limitations, and local-processing copy;
- uploading/extracting progress and cancellable pre-commit state;
- summary cards for rows, valid, duplicates, warnings, and errors;
- preview table with date, description, debit/credit, amount, account, provenance, and status;
- filters for all, actionable, errors, warnings, duplicates, and excluded rows;
- row details showing source line/page and original extracted values;
- destination account selection and any required mapping controls;
- disabled commit button with an explicit reason when blocked;
- confirmation step and post-import summary with next actions;
- useful empty, expired, failed, OCR-unavailable, low-confidence, and unsupported-PDF states.

Never label a preview as imported. Use distinct language: uploaded, extracted, ready, committed.

## Observability

Record structured, content-free events for upload accepted/rejected, extraction duration, extractor,
page/row counts, issue-code counts, preview ready, discarded/expired, and commit result. Do not include
filenames, descriptions, raw rows, hashes, or account identifiers in logs.

## Acceptance criteria

1. A browser-uploaded supported CSV reaches preview without a server-local path.
2. A digital PDF fixture yields expected dates, descriptions, signed minor-unit amounts, page
   provenance, and no balance rows.
3. A clean scanned-statement fixture invokes Tesseract only for image-only pages, yields positioned OCR
   candidates, marks them as OCR-derived, and requires review before commit.
4. An encrypted PDF, fake `.pdf`, oversize file, and over-page-limit file each fail with a specific
   sanitized issue code and temporary-file cleanup.
5. User sees all candidate rows and issues before commit; commit is impossible while included rows have
   blocking errors.
6. Excluded invalid rows remain in batch history but do not enter the ledger.
7. Commit inserts all eligible rows atomically; an injected database failure inserts none.
8. Re-upload of the same CSV or PDF reports duplicates and inserts zero duplicate transactions.
9. Legitimate repeated identical rows within one statement remain distinct through occurrence-aware
   fingerprints.
10. Refreshing the browser restores an unexpired preview from its batch ID.
11. Raw uploaded bytes are absent from SQLite and deleted from temporary storage after extraction.
12. Existing CLI CSV imports and their tests continue to pass.
13. API, parser, service, integration, and browser tests cover success, partial validity, rejection,
    expiry, discard, duplicate, and rollback flows.
14. README accurately states CSV/PDF support, browser workflow, GBP limitation, OCR review requirement,
    quality limitations, and privacy behavior.
15. Tests prove native-text pages do not invoke OCR, OCR runs at most once per eligible page, Tesseract
    absence produces an actionable error, and low-confidence financial fields block commit.

## Suggested implementation order

1. Format-neutral contracts and import-batch migration/repositories.
2. Refactor existing CSV logic behind the new extraction/preview boundary; preserve CLI behavior.
3. Multipart preview API, policies, staging cleanup, batch read/discard/expiry.
4. Import UI and CSV preview/commit journey.
5. Native PDF extractor, compatible page renderer, and Tesseract fallback with digital/scanned fixtures
   and failure states.
6. Mapping/exclusion controls, atomic commit, import summary, and duplicate reconciliation.
7. Security, observability, full regression suite, README, and API docs.

## Deferred follow-up

Advanced OCR remains separate work: deskewing, denoising, perspective correction, handwriting,
photographed statements, multi-language auto-detection, and layout-aware ML parsers. Do not silently
route scanned PDFs through an LLM or treat OCR output as authoritative financial data. Basic Tesseract
support in this slice remains deterministic, local, visibly marked, and review-gated.

## Implementation references

- [pdfplumber repository](https://github.com/jsvine/pdfplumber): MIT-licensed native text, coordinate,
  and table extraction.
- [Tesseract repository](https://github.com/tesseract-ocr/tesseract): Apache-2.0 local OCR engine and
  positional TSV/hOCR output.
- [PyMuPDF OCR guidance](https://pymupdf.readthedocs.io/en/latest/recipes-ocr.html): useful page-selective
  OCR design reference; PyMuPDF itself is not the selected dependency.
- [PyMuPDF licensing](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright): AGPL or
  commercial licensing rationale for the exclusion above.
- [Microsoft MarkItDown](https://github.com/microsoft/markitdown) and
  [LangChain Extract](https://github.com/langchain-ai/langchain-extract): evaluated as non-authoritative
  document/LLM extraction references, not ledger ingestion dependencies.
