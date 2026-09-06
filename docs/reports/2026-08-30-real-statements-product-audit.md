# Real-Statement Product Audit

**Date:** 2026-08-30  
**Environment:** local app at `http://127.0.0.1:8000/`  
**Database:** disposable SQLite database `data/codex-product-audit-20260830.db`  
**Persona:** financially literate professional trying to find harmful spending habits, improve savings, and optimize cash and assets.

## Executive verdict

**Not ready for the stated user promise.** The HDFC Delimited happy path works well: the original `.txt` was detected at high confidence, all 13 candidates and all 12 balance transitions were correct, explicit INR/current-account confirmation worked, commit worked, duplicate detection worked after binding, and undo removed all 13 transactions.

The surrounding product remains unsafe or ineffective for real multi-bank use. Critical failures include false success/progress UI on a fresh database, generic imports committing without explicit account binding, HSBC statements being misidentified as Amex, GBP-only analytics hiding imported INR activity, and the advisor giving conclusions from zero or missing data. The app cannot yet help this persona understand spending, improve savings, or optimize assets.

**Release recommendation:** keep HDFC support behind an internal/experimental label. Do not advertise broad bank support or personalized financial guidance until all P0 gates below pass against this corpus.

## Privacy handling

- Original statements stayed in `C:\Users\Amit\Downloads\Statements`; none were copied into the repository.
- No statement filename, account number, narration, reference, transaction amount, or balance appears in this report.
- Evidence is limited to anonymous format counts, adapter outcomes, row counts, direction counts, reconciliation counts, and product behavior.
- Uploaded bytes were sent only to the locally running app.

## Test corpus and outcome

| Format group | Files | Result |
|---|---:|---|
| Amex PDF | 4 | Detected as Amex, but parser fidelity diverged materially from matching CSV exports. |
| Amex CSV | 7 | Detected as Amex. One exact duplicate pair present in the source corpus. |
| HDFC Delimited TXT | 1 | Pass: 13 candidates; 10 debits, 3 credits; 12/12 balance transitions; commit and undo pass. |
| HDFC formatted TXT | 1 | Correctly rejected with `UNSUPPORTED_TEXT_LAYOUT` and Delimited guidance. |
| HDFC encrypted PDF | 1 | Incorrectly returned generic `PDF_NOT_EXTRACTABLE`; expected `PDF_PASSWORD_REQUIRED`. |
| HDFC XLS | 1 | Correctly rejected with `UNSUPPORTED_SPREADSHEET_FORMAT` and Delimited guidance. |
| HSBC PDF | 8 | Four detected as HSBC; four falsely detected as Amex. All had incomplete or mismatching extraction/reconciliation. |
| HSBC CSV | 1 | Fell through to generic/headerless import and bypassed explicit account binding. |

## Priority findings

| ID | Sev | Finding | Evidence and user impact | Required gate |
|---|---|---|---|---|
| IMP-01 | P0 | Fresh import screen shows fake progress and fake success | Elements carry `hidden` but CSS forces `display:flex`. A new user sees “Extracting candidates” and “Successfully Imported” before selecting a file; keyboard focus reaches fake receipt links. Immediate trust failure. | Hidden elements have no layout, accessibility-tree presence, or tab stops. Add fresh-load browser regression test. |
| IMP-02 | P0 | Generic import bypasses stable account binding | HSBC CSV preview had no explicit destination/new-account binding, then committed 30 rows and created a GBP account with missing institution metadata. This violates the parent binding contract and risks silent cross-account contamination. | Every staged commit supplies exactly one explicit, validated binding. Generic fallback cannot auto-create an account. |
| IMP-03 | P0 | HSBC content can be hijacked by the Amex detector | Four of eight HSBC PDFs selected `amex_uk_pdf` at 0.98 confidence. Detection scans all extracted text and checks broad Amex phrases before HSBC; transaction narrative text can win over statement identity. | Anchor institution evidence to statement header/issuer regions; detect conflicts; reject ambiguity. Verify all eight private samples and redacted fixtures. |
| IMP-04 | P0 | Multi-currency analytics hides real activity | After importing 13 INR transactions, default monthly analytics returned GBP zero values and zero transaction count. INR-scoped API calls returned the real activity. Frontend requests omit currency and money formatting defaults to GBP. Overview therefore showed empty spend while its badge implied 13 items. | Currency/account scope must be explicit and consistent through analytics, categories, UI formatting, budgets, goals, and advisor. No silent GBP fallback. |
| IMP-05 | P0 | Advisor makes unsafe claims from missing data | Advisor reported GBP 0 spending, claimed no transaction history/cash position, and said a purchase was technically affordable based on unused budget while acknowledging missing income, spending, and cash data. Budget headroom is not available cash. | Hard-stop advice when data scope/coverage is incomplete; expose currency and coverage; never infer affordability from a budget alone. Add grounded answer tests. |
| IMP-06 | P0 | Real imports are not practically categorizable | All 13 HDFC transactions arrived uncategorized. Web and API expose no edit workflow. CLI correction is one transaction at a time and creates exact-description rules. Without categories, spending-habit analysis is empty. | Add review queue, single and bulk categorization, rule preview/edit, undo, and immediate analytics refresh. |
| IMP-07 | P0 | Matching Amex PDF and CSV exports disagree | Across four matching periods, valid row counts matched per pair, but normalized comparison found only 49/56, 43/50, 50/65, and 52/63 shared transaction fingerprints. Definitive amount mismatches occurred in three periods and one direction mismatch occurred. PDFs also produced extra invalid candidates. | Cross-format parity fixture must reconcile dates, directions, and minor-unit amounts or block PDF support. |
| IMP-08 | P0 | Assets and net worth are absent | No holdings, valuation, liability, or net-worth endpoints/workflows exist. “Investments” is only an account type/analytic label. The persona cannot optimize assets or see a complete financial position. | Narrow the promise or add holdings, valuations, liabilities, net worth, and data-freshness semantics. |
| IMP-09 | P1 | Encrypted PDF error contract fails | The encrypted HDFC PDF contains encryption markers but returned HTTP 200 with a generic zero-row issue instead of `PDF_PASSWORD_REQUIRED`. Guidance is lost. | Classify encrypted PDFs before generic extraction failure and add this sample shape as a redacted regression fixture. |
| IMP-10 | P1 | Post-commit UI state is stale | Immediately after HDFC commit, API contained 13 transactions while the navigation activity badge remained zero until reload. | Invalidate/reload all derived stores after commit, undo, categorization, budget, and goal mutations. |
| IMP-11 | P1 | Budgets and goals are display-only and currency-blind | No web/API create/edit flow was available; CLI was required. Synthetic budget and goal then rendered with GBP symbols despite the HDFC account being INR. Goal progress remained zero with no usable update path. | Add currency-aware CRUD, contribution/progress semantics, and validation against selected accounts. |
| IMP-12 | P1 | Empty-state copy fabricates insight | Fresh database used narrative language such as spending “movement” and month-over-month change despite having no data. False precision makes the product look unreliable. | Use explicit no-data/partial-data states and state the action needed to unlock analysis. |
| IMP-13 | P1 | Migration metadata has drifted | `alembic check` proposes removal of two transfer-decision indexes. A clean schema is not at migration parity. | Resolve model/migration index ownership; require `alembic check` in CI. |
| IMP-14 | P2 | Mobile layout is technically contained but not mobile-shaped | At 390×844 there was no horizontal overflow, but the full sidebar remained and 11 of 13 interactive targets were below 44 px. | Responsive navigation and minimum target-size pass at phone widths. |
| IMP-15 | P2 | Accessibility polish remains | Lighthouse accessibility 96: 21 contrast failures and one accessible-name mismatch. Hidden fake states create a more severe keyboard issue than the score reflects. | Zero hidden-state focus, WCAG AA contrast, matching visible/accessibility names. |
| IMP-16 | P2 | Transfer capability lacks an obvious user journey | Backend transfer-suggestion endpoints exist, but no visible review surface was found. A single-account corpus cannot validate matching quality. | Add discoverable review/accept/reject UX and test with redacted cross-account transfer fixtures. |
| IMP-17 | P2 | Browser regression coverage is missing | No browser/E2E test suite was found. Unit/API gates stayed green while fresh-load UI and multi-currency journeys were broken. | Cover fresh load, upload, binding, commit, duplicate, categorization, analytics, advisor, and undo in a browser gate. |

## User-journey critique

### 1. “Import all my accounts safely” — fails

HDFC Delimited provides a trustworthy example: high-confidence issuer/format label, explicit account/currency confirmation, exact balance reconciliation, duplicate handling, and guarded undo. The same safety model is not universal. Generic HSBC CSV can commit without binding, HSBC PDFs can be assigned to another bank, and encrypted PDF guidance is wrong.

### 2. “Show where my spending is bad” — fails

The imported HDFC activity is invisible to default GBP analytics and remains uncategorized. Category views cannot answer which habits are costly, recurring, avoidable, or worsening. The UI cannot correct the data. A count of transactions is not an insight.

### 3. “Help me save more” — fails

Budgets and goals require CLI setup, lack coherent currency/account context, and do not connect to categorized behavior. No workflow converts a goal into a contribution plan, identifies realistic cuts, or shows confidence/coverage. The advisor compounds this by confusing budget headroom with affordability.

### 4. “Optimize my cash and assets” — fails

There is no complete balance-sheet model: no holdings, valuation dates, liabilities, debt costs, net worth, asset allocation, or opportunity-cost comparison. Cash analytics alone cannot support asset optimization.

### 5. “Trust this with financial data” — mixed

Local-only operation, explicit HDFC binding, reconciliation, duplicate detection, and undo are strong foundations. False fresh-load success, wrong-bank detection, silent currency fallback, and unsupported advisor conclusions negate that trust.

## Heuristic review

Single-context review; parallel reviewers were not permitted for this run.

| Nielsen heuristic | Score / 4 | Assessment |
|---|---:|---|
| Visibility of system status | 0 | Fabricated progress/success, stale post-commit counts. |
| Match with the real world | 1 | Currency and affordability semantics conflict with real financial reasoning. |
| User control and freedom | 2 | Undo is strong; correction and setup controls are absent. |
| Consistency and standards | 1 | HDFC binding safeguards do not apply consistently to generic imports. |
| Error prevention | 1 | HDFC guards well; generic commit and detector ambiguity are dangerous. |
| Recognition rather than recall | 2 | Preview metadata helps, but essential account/currency context disappears downstream. |
| Flexibility and efficiency | 1 | No bulk categorization or professional review workflow. |
| Aesthetic and minimalist design | 3 | Visually authored and restrained; false content damages clarity. |
| Error recovery | 2 | Undo/format guidance are useful; encrypted-PDF and parser failures lack actionable recovery. |
| Help and documentation | 1 | Delimited guidance is good; coverage, currency, and advice limitations are not explained. |
| **Total** | **14 / 40** | Attractive shell; low functional trust. |

**Design-specificity verdict:** the visual system feels intentionally designed rather than generic. Runtime state correctness, financial semantics, and actionable workflows—not visual styling—are the primary blockers.

## Recommended delivery order

WSJF is directional: `(user value + time criticality + risk reduction) / job size`, each numerator dimension scored 1–10.

| Order | Work item | WSJF | Why now |
|---:|---|---:|---|
| 1 | Fix hidden/fake fresh-load states and add browser test | 27.0 | XS fix; immediate trust and keyboard impact. |
| 2 | Enforce explicit stable binding for every adapter/fallback | 10.0 | Small-to-medium fix preventing account contamination. |
| 3 | Make currency/account context end-to-end | 6.0 | Unlocks truthful analytics, budgets, goals, and advisor inputs. |
| 4 | Add advisor evidence/coverage safety gates | 5.8 | Prevents financially unsafe conclusions. |
| 5 | Redesign institution detection and ambiguity handling | 5.8 | Prevents wrong-bank parsing and downstream corruption. |
| 6 | Deliver web categorization and bulk review | 4.6 | Converts imported rows into usable spending insight. |
| 7 | Establish Amex/HSBC corpus parity gates | 4.5 | Required before those banks can be called supported. |
| 8 | Add currency-aware budget/goal workflows | 3.8 | Enables saving plan after categorization is reliable. |
| 9 | Define assets/net-worth product slice or narrow claims | 3.4 | Aligns product promise with actual capability. |

Safety sequencing overrides pure WSJF: account binding and wrong-bank detection must block release even if their estimated ratio is lower than the tiny UI fix.

## Verification gates run

| Gate | Result |
|---|---|
| Focused CSV/import API baseline | 35 passed |
| Full pytest | 138 passed |
| Ruff lint | Passed |
| Ruff format check | Passed; 111 files already formatted |
| mypy | Passed; 59 source files |
| Alembic migration parity | **Failed**; two transfer-decision indexes appear removable |
| Lighthouse desktop | Accessibility 96, Best Practices 100, SEO 90, Agentic 100 |
| Performance trace | LCP 226 ms, CLS 0, TTFB 3 ms |
| Mobile containment | No horizontal overflow at 390×844; target sizing/navigation fail |
| Console | No persistent app errors/warnings in final pass; initial favicon 404 only |

The green Python gates do not cover real-corpus adapter conflicts, browser state, multi-currency journeys, or advice grounding. Those need dedicated acceptance gates.

## Disposable test state

- HDFC commit and guarded undo completed successfully; final transaction count is zero.
- Two accounts remain: the explicitly created HDFC account and the generic-import account created by the binding bypass.
- One synthetic budget and one synthetic savings goal remain for UI/backend inspection.
- Preview/import-batch audit records remain in the disposable database.
- Local server intentionally remains running for follow-up inspection.

## Relevant implementation hotspots

- `src/pfa/web/index.html` — upload progress and batch success elements.
- `src/pfa/web/styles.css` — display rules overriding hidden state.
- `src/pfa/web/app.js` — GBP defaults, currency-less analytics requests, stale count fallback, budget/goal rendering.
- `src/pfa/ingestion/dialects.py` — whole-document adapter detection and Amex-first precedence.
- `src/pfa/api/routes/imports.py` and import service — commit binding enforcement and PDF error mapping.

## Acceptance bar before “supported” or “advisor-ready” claims

1. All 24 statement files produce the expected adapter or precise unsupported guidance; no cross-bank false positive.
2. Every commit has one explicit compatible account binding; missing institution/currency/type blocks commit.
3. Matching exports reconcile direction, minor-unit amount, dates, and candidate counts.
4. Imported INR activity appears consistently across overview, categories, cash, budgets, goals, and advisor.
5. A user can categorize/review transactions in the web app and see analytics refresh immediately.
6. Advisor answers expose scope and coverage, cite the underlying aggregate, and abstain when essential data is absent.
7. Fresh, loading, success, empty, duplicate, error, and undo states pass keyboard and browser tests.
8. Alembic parity and all automated gates pass on a newly migrated disposable database.

