# Typed accounts, statement adapters, and transfer events

> Review status: approved changes incorporated; plan-only handoff.

Status: ready for phased implementation; implementation not started
Author: handoff brief (design reviewed 2026-08-30)
Branch base: `feat/v02-currency-investments`

---

## 1. Why this exists

PFA currently books credit-card spending as income. Uploading `AMEX/latest.csv`
(35 rows) produces 34 charges classified `credit`/income and the one genuine
credit — `PAYMENT RECEIVED - THANK YOU`, £1,651.71 — classified `debit`/spending.
Exactly inverted. The commit button is enabled with the note "Ready to commit 35
transactions" and no warning.

The root cause is not the parser. It is that **PFA has no concept of which
account a statement belongs to**, so it cannot know what a positive number means.

```
account_id spread in data/pfa.db: [(1, 365)]     # all 365 transactions, one account
```

Every statement — AMEX card, HSBC Visa, HSBC current account — lands in a single
account named "Main account" of type `current`. On a current account a positive
figure is income. On a credit card a positive figure is a charge. Same number,
opposite meaning, and nothing in the model distinguishes them.

This plan fixes the account/ingestion semantic boundary and gives the user a
safe, reversible import workflow. It must deliver three observable outcomes:

1. A known statement cannot be committed to an incompatible or unidentified
   account.
2. Preview makes a sign inversion obvious by showing account, adapter, money-in,
   spending, refund, and repayment totals before commit.
3. A committed import can be undone without manually editing SQLite.

The analytics layer needs a narrow but material correction: `current_cash`
must use account scope plus canonical transaction signs. Treating all transfers
as zero is wrong when money leaves liquid assets to repay a liability.

### Approaches considered

| Approach | Trade-off | Decision |
|---|---|---|
| Patch AMEX signs only | Fast, but the next issuer/product repeats the defect | Reject |
| Account-bound, content-detected adapters first | Smallest reusable slice; immediately makes imports trustworthy | **Do first** |
| Full double-entry ledger rewrite | Strong accounting model, high migration and UX cost | Reject for v0.2 |

Transfer linking remains in scope, but ships after trustworthy imports and debt
metrics. Each slice must be independently usable; the feature is not one large,
all-or-nothing release.

---

## 2. Verified current state

Read these before changing anything. All line numbers verified 2026-08-30.

### What already exists and works

| Thing | Where | Note |
|---|---|---|
| `AccountType` incl. `CREDIT_CARD`, `LOAN` | `src/pfa/domain/accounts.py:4` | Enum complete; **never assigned by any import** |
| `TransactionKind.TRANSFER`, `REFUND` | `src/pfa/domain/transactions.py:4` | Complete |
| `TransferPurpose` | `src/pfa/domain/transactions.py:42` | Only `SAVING`/`INVESTMENT`/`OTHER` |
| Transfers excluded from spending + income | `src/pfa/analytics/service.py:36` | `_SPENDING_KINDS = {EXPENSE, FEE}` — already correct |
| `Dialect` adapter concept | `src/pfa/ingestion/dialects.py` | Has `default_sign`, `credit_markers`, `two_column` |
| `AMEX_CARD.default_sign = "debit_positive"` | `src/pfa/ingestion/dialects.py:38` | Correct value, never reached (see below) |
| Sign re-derivation is idempotent | `src/pfa/ingestion/batches.py:333` | `_apply_amount_sign` re-reads raw text rather than flipping |
| Invariant test home | `tests/unit/test_financial_invariants.py` | Add the new invariants here |

### The specific defects this plan closes

**D1 — Dialect is selected by free-text account name.**
`src/pfa/ingestion/batches.py:117,217` calls `dialect_for_name(account_name)`,
which substring-matches the string the user typed into the Target Account box
(`src/pfa/ingestion/dialects.py:60`). Leave the box at its default and you get
`GENERIC`, whose `default_sign` is `None`, which falls through to `as_written` —
and every card charge becomes income. This is the direct mechanism of the
inversion above. The adapter must be detected from statement content; account
binding then validates type/currency compatibility. Account names must never
select parsing or sign semantics.

**D2 — Account type is hardcoded at commit.**
`src/pfa/ingestion/service.py:227`:
```python
account = self.uow.accounts.get_or_create(
    candidate.account_hint or "Main account", candidate.currency
)
```
`get_or_create(name, currency, account_type="current")` — the third parameter is
never passed (`src/pfa/db/repositories.py:71`). No import path can ever create a
`credit_card` account. There is no account-type selector in the import UI.

**D3 — One `hsbc` dialect serves two incompatible statement shapes.**
`HSBC/Bank/*.pdf` is a current account with three columns
(`Paid out | Paid in | Balance`). `HSBC/*.pdf` is a Visa card with a single
amount column plus a `CR` marker. `dialect_for_name("hsbc")` returns the same
`Dialect` for both. Consequence on the bank statement: the parser takes the last
number on the line — the running balance — as the amount.

```
PDF:    23 Apr 25  DD ADMIRAL INSURANCE   262.11   778.05
stored: 'DD ADMIRAL INSURANCE 262.11'   £778.05   credit/income
```
26 transaction lines in that PDF produced 9 imported rows, 2 of which are
`BALANCEBROUGHTFORWARD`/`BALANCECARRIEDFORWARD` markers imported as income.
Dialects must be keyed on **(institution, product)**, not institution alone.

**D4 — `current_cash` will silently break the moment card accounts exist.**
`src/pfa/analytics/service.py:266` excludes only `NON_CASH_ACCOUNT_TYPES =
{INVESTMENT, LOAN}` (`src/pfa/domain/accounts.py:13`). A `credit_card` account's
opening balance would therefore be **added to cash**, and `_cash_delta`
(`src/pfa/analytics/service.py:55`) sums card charges into the cash position even though no cash
moved. There is a second defect: `_cash_delta` returns zero for every transfer,
so a current-account leg that repays a card would not reduce liquid cash. The
fix must filter to liquid asset accounts and sum canonical signed amounts for
all transaction kinds. Merely adding `CREDIT_CARD` to an exclusion set is not
enough.

**D5 — `debt_payments_minor` reads £0.00 the moment payments become transfers.**
`src/pfa/analytics/service.py:99` computes it as
`_spending(row) where category == DEBT_PAYMENT`, and `_spending` returns 0 for
anything that is not `EXPENSE`/`FEE`. Reclassifying card payments as `TRANSFER`
zeroes a live reported number. Classification and replacement debt metrics must
therefore land together in WP7.

### Adjacent defects and scope boundary

Two parser defects move **into** this plan because they invalidate the stated
real-corpus acceptance gate and corrupt categorisation evidence:

- AMEX PDF statement-year inference must read the statement period instead of
  falling back to the system year.
- AMEX/HSBC PDF two-date rows must populate `transaction_date` and
  `posted_date`, removing the posted-date prefix from descriptions.

Measured categorisation changes from 1/24 with the stray prefix to 19/32 once
clean. A plan cannot claim trustworthy corpus re-import while leaving these
known corruptions in place.

Still out of scope; track separately and do not hide them inside this work:

- Frontend month-over-month deltas and 3-month cashflow fetch only the current
  month (`src/pfa/web/app.js:140,218,312`).
- Activity Ledger ignores its month picker and caps at 200 rows
  (`src/pfa/web/app.js:796`).
- `[hidden]` is overridden by `display:flex` on import alert banners.
- Advisor data introspection, grounding, and dynamic tool routing belong to the
  separate General Financial Intelligence slice. This plan must expose stable,
  deterministic account/import metadata for that slice, but does not change the
  advisor or its tool routing.

---

## 3. Target model

### 3.1 Accounts become stable import destinations

`AccountType` and `accounts.account_type` already exist. Do not describe this as
adding typed accounts; the missing work is validation and stable ID-based
binding.

```
Account
├── id                         # import identity; never use display name as a key
├── name                       # user-editable label
├── type: current | savings | credit_card | investment | loan | cash
├── currency
├── institution: str | null   # normalized display/matching hint, not an adapter
├── last4: str | null          # optional masked hint; never store a full account number
├── opening_balance_minor
├── opening_balance_as_of: date | null
└── nature: asset | liability # derived from type, not stored
```

| Type | Nature | Liquid cash? |
|---|---|---|
| current, savings, cash | asset | yes |
| investment | asset | no |
| credit_card, loan | liability | no |

`nature` and `is_liquid_cash` are mappings in `src/pfa/domain/accounts.py`; no override
column. `opening_balance_minor` uses the account's natural statement meaning:
positive means held for an asset and owed for a liability. Net-worth opening
contribution is therefore positive for assets and negative for liabilities.
`opening_balance_as_of` names when that value was true; do not call it merely
`balance_as_of`, which could be mistaken for a live/current balance.

The baseline date is an **end-of-day** balance. Transactions on that date are
already reflected and must not be added again. A statement opening balance at
the start of 1 January is therefore stored as the 31 December end-of-day
baseline. This makes the inclusion rule unambiguous: `baseline_date <
transaction_date <= as_of`.

If `as_of < opening_balance_as_of`, that account's historical balance is
unknown unless an earlier accepted balance snapshot covers the date. A missing
baseline date is also incomplete coverage; migrations must not invent one.

Do not overload the opening value with available balance, credit limit,
statement balance, or current balance. The follow-on reconciliation model is:

```
AccountBalanceSnapshot
├── account_id
├── as_of
├── balance_minor
├── balance_type: ledger | statement | current | available
├── source
└── import_batch_id | null
```

Known adapters should expose opening/closing balance evidence when present. For
an asset account, natural movement equals `signed_minor`; for a liability,
natural movement equals `-signed_minor`. Reconciliation can then verify:

```
opening natural balance + natural movements = closing natural balance
```

This phase adds the dated opening-balance contract and statement reconciliation
work package. It does not implement every balance snapshot type.

An import batch binds either an existing `destination_account_id` or a validated
new-account draft. It never binds a name. If the user chooses a new account, the
account and its transactions are created in the same commit transaction so a
discarded preview leaves no orphan account.

Account names are labels and may repeat. Similar/duplicate names produce a
disambiguation warning, never rejection or identity. Strong duplicate-account
suspicion uses the available tuple `(institution, type, currency, last4)` but
still requires user confirmation; even `last4` is not globally unique. Selection,
imports, and relationships always use account ID.

### 3.2 Canonical sign convention

> **PFA's internal amount sign is the economic effect on the account's
> contribution to net worth.**

| Event | Source text | Canonical |
|---|---|---|
| Salary into current account | `+3,305.00` | `+330500` |
| Debit-card purchase | `-50.00` | `-5000` |
| **AMEX charge** | `+50.00` (debit-positive) | `-5000` (liability up, net worth down) |
| **AMEX payment received** | `1,651.71 CR` | `+165171` (liability down) |
| Bank side of that payment | `-1,651.71` | `-165171` |

The invariant that makes this worth having:

```
HSBC   -165171
AMEX   +165171
       ────────
consolidated  0
```

**Implementation note — this does not require a schema migration.**
PFA stores `amount_minor` as a positive magnitude plus a legacy normalized
`flow_direction` of `"debit"`/`"credit"` (`src/pfa/db/models.py:46`,
`src/pfa/ingestion/service.py:249`). For compatibility, the canonical helper is:

```python
signed_minor = amount_minor if flow_direction == "credit" else -amount_minor
```

No account-nature flip is needed in this expression. This mapping describes
PFA's existing normalized money-in/money-out storage only. It is **not** the
source statement's CR/DR marker and is not a claim about accounting debit/credit
semantics on assets or liabilities.

Keep three concepts explicit:

```
source direction     CR / DR / paid in / paid out / source sign
canonical polarity   signed_minor > 0 or < 0
transaction kind     expense / income / refund / transfer / fee
```

Adapters retain source direction in candidate/import provenance and emit
canonical polarity. Downstream code consumes `signed_minor`, not assumptions
about the words `debit` or `credit`. Put a later storage rename on the roadmap:
`flow_direction` → `canonical_direction` (or `money_in`/`money_out`) once the
migration cost is justified.

Do **not** migrate to a stored signed `amount_minor`. That is a breaking change
across `src/pfa/analytics/service.py`, `src/pfa/api/app.py:422`,
`src/pfa/cli/app.py:143` and every
fixture, and it buys nothing the helper does not.

`nature` is for balances and net worth, not for transaction signs. Keep the two
concepts separate.

### 3.3 Statement content selects the adapter; account type validates it

Three distinct pieces of information — do not collapse them:

```
CONTENT DETECTOR  "which export shape is this?"               → amex_uk_csv
ACCOUNT TYPE      "what does this account represent?"         → credit_card
STATEMENT ADAPTER "what do these columns/signs/dates mean?"    → debit_positive
```

The current plan's earlier wording contained a contradiction: it keyed adapters
from a bound account, but the UI asks the user to bind an account only after
detection, and the staged upload is deleted after preview. Resolve it as follows:

- detect a stable `adapter_id` from headers, column geometry, and statement
  markers; never from filename, folder, or account name;
- each adapter declares compatible account types, currency evidence, amount
  convention, date semantics, and extraction strategy;
- persist `adapter_id`, detection confidence, reason codes, detected institution,
  and masked account hint on the import batch;
- account selection validates the detected adapter. It does not choose it;
- an adapter/account mismatch is blocking and explains the conflict.

`credit_card` must not imply debit-positive. Another card export may write
purchases as `-42.50` and payments as `+500.00`. `AmexUkCsvAdapter` can declare
`(compatible_types={CREDIT_CARD}, debit_positive)` while another card declares
signed values. Downstream code receives canonical semantics only.

The `amount_sign` dropdown becomes a **fallback for generic/unknown formats
only**. For a recognized adapter it never appears. A low-confidence or ambiguous
detection cannot silently fall through to `GENERIC`; it must block commit until
the user explicitly chooses generic semantics.

### 3.4 Transaction invariants

| Event | kind | purpose | account | canonical | spending | income |
|---|---|---|---|---|---|---|
| Card purchase | `EXPENSE` | — | credit_card | `< 0` | ✓ | ✗ |
| Card payment, bank leg | `TRANSFER` | `CREDIT_CARD_PAYMENT` | current | `< 0` | ✗ | ✗ |
| Card payment, card leg | `TRANSFER` | `CREDIT_CARD_PAYMENT` | credit_card | `> 0` | ✗ | ✗ |
| Card refund | `REFUND` | — | credit_card | `> 0` | reduces | ✗ |

**A positive/CR row on a credit card is not automatically a payment.**

```
PAYMENT RECEIVED     → TRANSFER / CREDIT_CARD_PAYMENT
PRET REFUND          → REFUND
CASHBACK             → income/rebate semantics
CHARGEBACK CREDIT    → REFUND / adjustment
```

> **CR tells us direction, not kind.** Direction comes from the marker; kind
> comes from description rules plus account context.

`flow_direction` keeps its stored `debit`/`credit` values for compatibility, but
it is documented only as legacy normalized PFA inflow/outflow polarity. It is
not source CR/DR or a universal accounting definition. Canonical calculations
use `signed_minor`; UI copy uses “money out” / “money in” or domain labels.

### 3.5 Import safety and reversibility

Preview is a trust surface, not a row count. It must show:

- detected statement format and confidence/evidence;
- bound account name, type, currency, and masked hint;
- included row count plus money in, spending, refunds, transfers, and repayment
  totals after canonicalisation;
- at least the first five signed rows, including warnings;
- explicit blocking reasons beside the control that resolves them.

For a recognized card statement, include a plain-language sanity box:

```
American Express ••••1234 · Credit card · GBP
Statement: 01 Aug – 31 Aug

Detected
34 purchases                         £2,835.47
1 card payment                       £1,651.71
0 income · 0 unresolved sign rows

Effect on PFA
Spending                             £2,835.47
Debt repaid                          £1,651.71

✓ No card payments counted as spending
✓ No purchases counted as income
```

Commit is disabled when the account is unbound/inactive, adapter type is
incompatible, currency conflicts, sign semantics remain ambiguous, or included
rows contain errors. No “Ready to commit” state is allowed before these checks.

Every successful commit returns an import receipt and an **Undo import** action.
Undo deletes only transaction IDs recorded by that committed batch plus derived
transfer links/suggestions, in one transaction. It is idempotent and never
deletes the account automatically.

Do not build a dependency graph for v0.2. If an imported transaction has
`updated_at > batch.committed_at` (or later revision metadata), Undo shows how
many imported rows were edited and requires confirmation. Confirmed Undo deletes
those rows and edits. If an event links transactions from two batches, undoing
one batch removes the event/decision and that batch's leg only; the opposite
transaction remains authoritative.

### 3.6 Transfer purposes — add only what this slice uses

Add `CREDIT_CARD_PAYMENT` to the existing `SAVING`, `INVESTMENT`, and `OTHER`
values. Defer `INTERNAL` and `DEBT_PRINCIPAL` until a workflow consumes them.
Adding speculative enum members weakens rather than strengthens the contract.

A card repayment is liability settlement, not spending. Interest and fees that
appear as separate card rows remain expenses. Loan principal allocation is a
different problem because one loan payment can contain both principal and
interest; do not pretend this phase solves it.

### 3.7 Cash and debt metrics

`current_cash` means liquid cash, not net position. Resolve the previous open
question now:

```
liquid account types = CURRENT | SAVINGS | CASH
account_cash(as_of) = opening balance at end-of-day baseline
                    + canonical signed_minor where
                      opening_balance_as_of < transaction_date <= as_of
current_cash(as_of) = sum(account_cash) only when every included account is known
```

If any liquid account lacks a baseline covering `as_of`, the user-facing total
is `unknown/incomplete`, not a confidently partial number. The service result
must carry coverage status and missing account IDs (it may also expose a labeled
known subtotal). Historical cash before the earliest accepted baseline is
unknown. Never sum pre-baseline transactions into a later baseline.

This includes every transaction kind. A transfer between two imported liquid
accounts nets to zero; a card repayment reduces cash on its bank leg; a card
charge never enters the calculation because the card account is not liquid.
Do not use `_cash_delta(kind)` for this metric.

Split the old ambiguous debt number:

| Metric | Exact source | Question answered |
|---|---|---|
| `debt_repayments_minor` | positive canonical card-account legs classified `TRANSFER/CREDIT_CARD_PAYMENT` | How much card liability did I repay? |
| `debt_costs_minor` | `EXPENSE`/`FEE` rows categorized as debt interest/fees | What did debt cost me? |
| `debt_service_minor` | deferred | How much total cash did debt consume? |

Use the card-side repayment transaction as the metric source so a paired event
does not double count both legs. Pairing changes presentation, not totals.
`DEBT_PAYMENT` temporarily means debt costs only; document that name debt and
plan a later enum migration to `DEBT_COST` or explicit interest/fee categories.

### 3.8 Accepted transfer events and reviewable suggestions

Both statement transactions remain authoritative. Linking never merges or
deletes a leg.

```
TransferEvent
├── id, purpose, match_method, created_at
└── TransferLeg(event_id, transaction_id, role)  # 2+; transaction_id unique

TransferMatchDecision
├── stable match_key
├── left_transaction_id, right_transaction_id
├── state: suggested | accepted | dismissed
├── confidence, reason_codes, event_id?
└── created_at, reviewed_at
```

`role` is explicit domain data, never inferred later from sign:

```python
class TransferLegRole(StrEnum):
    SOURCE = "source"
    DESTINATION = "destination"
    FEE = "fee"
```

Every accepted event has exactly one source, exactly one destination, and zero
or more fee legs. v0.2 validates a negative source and positive destination but
does not derive roles from those signs. Manual cross-currency linking requires
the user/service action to assign roles explicitly.

Only accepted/high-confidence auto matches create a `TransferEvent`. Decisions
are persisted so a dismissed pair does not reappear on every import. Matching is
idempotent; rerunning it cannot duplicate an event or suggestion. Manual confirm,
dismiss, link, and unlink operations are explicit API actions and audit their
origin. An unlink records suppression so the same automatic pair is not
immediately recreated.

Cross-currency is a known domain requirement, not a hypothetical one. The event
and leg schema must therefore permit different transaction currencies, unequal
absolute amounts, and an optional fee leg. Do not place same-currency or
equal-amount constraints on `TransferEvent`/`TransferLeg`; those are matcher-v0.2
rules only.

Design the Slice D companion now, but create/populate it when manual
cross-currency linking ships:

```
TransferFx
├── event_id
├── quoted_rate_decimal | null
├── rate_source: derived | statement | user
└── created_at
```

Rate orientation is fixed: **destination currency units per one source currency
unit**. Use `Decimal` major-unit amounts after applying each currency's minor-unit
exponent:

```
SOURCE       -£1,000
DESTINATION  +₹118,400
implied rate = 118.4 INR / GBP
```

The inverse is returned only when explicitly requested. Fee legs do not enter
the implied-rate numerator or denominator.

Source/destination currencies and minor-unit amounts come from event legs and
their `role`; `implied_rate` is derived from those legs. Do not duplicate them in
`TransferFx`, where they could drift from authoritative transactions. A fee with
its own transaction is a `role=fee` leg, not both `fee_minor` and a foreign key.
No automatic GBP↔INR matching is part of v0.2.

The Activity Ledger renders accepted events as one collapsed row expandable to
both original legs. Suggestions remain separate rows with a review prompt; they
must not silently alter presentation or analytics.

### 3.9 Pairing algorithm

A candidate pair requires all baseline checks:

- different active owned accounts and neither transaction already linked;
- one leg on `CREDIT_CARD`, one on `CURRENT`/`SAVINGS`;
- same currency, opposite canonical signs, equal absolute amount;
- dates within ±3 calendar days;
- card leg already classified `CREDIT_CARD_PAYMENT`.

Baseline checks alone are not enough to auto-link. Auto-link also requires a
strong corroborating cue: a stable shared reference, or a bank description that
names the card institution/account hint. A single weak candidate is still a
suggestion; “only one result” is not confidence. Multiple plausible candidates
are suggestions with `ambiguous_amount_date` reason codes.

The real corpus case is expected to auto-link because both the card payment rule
and `AMERICAN EXPRESS` bank descriptor corroborate it:

```
HSBC/Bank/TransactionHistory.csv   02/09/2025  AMERICAN EXPRESS DD           -165171
AMEX/latest.csv                    02/09/2025  PAYMENT RECEIVED - THANK YOU  +165171
```

### 3.10 End-to-end import flow

1. Inspect statement content and detect adapter, institution, currency, date
   period, and masked account hint.
2. Extract raw candidates using that adapter; generic/ambiguous detection stays
   blocked.
3. Suggest an existing account only when deterministic evidence produces one
   compatible match. Otherwise require explicit selection or a new-account draft.
4. Validate adapter type/currency against the binding.
5. Canonicalise and classify.
6. Reconcile opening/movements/closing when the adapter exposes balance evidence.
7. Show the semantic sanity box, reconciliation result, and signed samples.
8. Commit account draft + transactions atomically and return an undo receipt.
9. Run transfer matching idempotently, then surface accepted links and review
   suggestions.

Example selection:

```
● Existing: American Express ••••1234  [Credit card · GBP]
○ Create:   Name […]  Type […]  Currency […]  Institution […]  Last 4 […]
```

Known adapters hide raw sign controls. Generic imports state clearly that the
user is supplying semantics, show the transformed sample, and require explicit
confirmation.

---

## 4. Work packages, in order

Each WP must leave the full gate green (§6).

### Slice A — trustworthy, reversible imports (first value release)

**WP1 — Account/import binding contract and migration.**

- Add derived `nature`/`is_liquid_cash`; add nullable `institution`, `last4`,
  and `opening_balance_as_of` account fields.
- Define `opening_balance_as_of` as an end-of-day baseline and require it for a
  trusted opening balance. Do not guess/backfill dates for legacy accounts.
- Replace batch/account name coupling with `destination_account_id` or a
  validated new-account draft. Existing account selection uses ID.
- Add batch adapter metadata (`adapter_id`, confidence, reason codes, detected
  institution/account hint) plus reconciliation evidence/result. Keep old columns
  only for a deliberate compatibility window; do not maintain two sources of
  truth.
- Reject inactive accounts, type/currency mismatch, and invalid last-four values.
  Duplicate/similar display names warn and require disambiguation but remain
  valid. Strong duplicate suspicion uses institution/type/currency/last4 and
  still requires confirmation.
- Remove the existing database uniqueness constraint on `accounts.name` in the
  migration (SQLite-safe table recreation if required). Deprecate name-based
  `get_or_create`; explicit create and all binding use stable IDs.
- Alembic `0005_account_import_binding` from current head `0004_fx_rates`, with
  upgrade/downgrade and model-schema parity tests. Runtime never migrates.

**WP2 — Canonical financial sign contract.**

- Add and test `signed_minor` before implementing adapters; document canonical
  polarity, natural account balances, and the three-way source/polarity/kind
  separation in `docs/architecture.md`.
- Keep legacy `flow_direction` storage for compatibility, but forbid new code
  from treating accounting debit/credit as its domain definition.
- Require every adapter output to satisfy the invariants in §§3.2–3.4.
- No signed-amount schema migration.

**WP3 — Content-detected adapters and blocking parser fixes.**

- Replace `dialect_for_name` with deterministic content/header detection and a
  stable adapter registry.
- Add distinct AMEX UK card, HSBC UK card, and HSBC UK current-account adapters.
- Add HDFC India delimited support through the reviewed
  [HDFC extension](../../../docs/plans/2026-08-30-hdfc-delimited-import-plan.md);
  it consumes this plan's canonical, binding, and reconciliation contracts rather
  than defining parallel ones.
- Extract HSBC paid-out/paid-in/balance columns using coordinates/table cells;
  never infer the amount from the last flattened number.
- Parse statement year from header/period and split transaction/posted dates.
- Exclude balance-forward markers as non-transaction evidence.
- Generic fallback is explicit and blocking until sign semantics are confirmed.

**WP4 — Statement balance evidence and reconciliation.**

- Have adapters emit opening/closing balance evidence plus dates when available.
- Define one parent `ReconciliationResult` with two independent dimensions:
  `arithmetic_integrity` and `coverage_integrity`.
- Arithmetic integrity verifies natural opening balance + all extracted natural
  movements = closing balance (or adapter-specific adjacent balance chains).
- If evidence can deterministically derive a pre-first-row baseline, expose it as
  a suggestion with provenance; never silently replace an accepted account
  baseline, especially when dates/amounts conflict.
- Coverage integrity verifies every valid source transaction is represented by
  an included candidate or a verified already-ledger duplicate. Excluding a
  genuine non-duplicate row makes coverage incomplete and blocks commit.
- Overall `reconciled` requires arithmetic PASS and coverage PASS. Show
  `reconciled`, `mismatch`, `incomplete`, or `not available` with evidence. A
  known-format mismatch/incomplete coverage blocks; absent balance evidence does
  not pretend to pass.
- Keep snapshot history and additional balance types as follow-on work.

**WP5 — Cash correction.**

- Rewrite `current_cash` per §3.7: liquid account scope plus signed transactions,
  including transfers, but only strictly after each account's end-of-day
  baseline and through `as_of`.
- Return coverage status/missing account IDs; `as_of` before a baseline or an
  absent baseline makes the user-facing total unknown/incomplete rather than
  double-counted or confidently partial.
- Add a separate future `net_position` concept rather than overloading cash.

**WP6 — Import preview, atomic account creation, and undo.**

- API patch contract accepts exactly one of `destination_account_id` or
  `new_account`; revalidate/recanonicalise the preview after a binding change.
- Web UI uses an accessible existing-account picker/new-account form and shows
  adapter evidence, reconciliation status, semantic totals, and sample rows.
- New-account UI confirms baseline amount/date when statement evidence supplies
  or derives them. Existing accounts with no covering baseline show cash coverage
  as incomplete and provide a separate correction action; migration never guesses.
- Commit creates a new account and transactions atomically. Return a receipt.
- Add an idempotent undo endpoint/action scoped to committed transaction IDs and
  derived transfer records. If imported rows changed later, show the affected
  count and require confirmation; do not build a dependency graph.

**Slice A acceptance:** importing the AMEX CSV to a new credit-card account
shows 34 charges with negative canonical polarity and never as income. The
positive payment row is never inferred as income; until Slice B classifies it,
it is visibly unresolved and blocks or requires explicit user classification.
The preview has no known-format sign control, shows reconciliation evidence when
available, and returns a working Undo import action. Choosing a current account
is blocked with a plain-language mismatch. GBP and INR accounts can both be
represented and imported independently; cross-currency transfer linking is
Slice D.

### Slice B — truthful repayment and debt reporting

**WP7 — Card payment classification and debt metrics (one atomic WP).**

- Add only `CREDIT_CARD_PAYMENT`.
- Pass adapter/account context into classification; do not add a global
  description-only `PAYMENT` rule.
- Card-side deterministic path: on a bound credit-card account, an issuer-specific
  payment descriptor such as AMEX `PAYMENT RECEIVED`, with positive canonical
  polarity, becomes `TRANSFER/CREDIT_CARD_PAYMENT`. Refunds, cashback, and
  chargebacks remain distinct.
- Bank-side deterministic path: on current/savings, an exact issuer payment rule
  such as `AMERICAN EXPRESS DD` plus a known owned compatible AMEX account/strong
  institution mapping becomes `TRANSFER/CREDIT_CARD_PAYMENT`. Description text
  alone is never sufficient.
- If the bank side arrives before any corresponding owned card is known, emit a
  `possible_card_repayment` review issue and never silently count it as ordinary
  expenditure. Explicit confirmation may commit it as an unpaired transfer;
  otherwise it remains unresolved. Later card import can pair it without changing
  spending/income totals.
- Cover bank-first and card-first lifecycles. Financial classification and totals
  cannot depend on which statement was imported first.
- Add `debt_repayments_minor` and `debt_costs_minor` with the exact sources in
  §3.7. Update API/CLI/UI labels in the same WP; do not temporarily zero or
  silently redefine `debt_payments_minor`.

**Slice B acceptance:** card purchases drive spending, card payments drive
repayments, interest/fees drive debt cost, and none drive income incorrectly.

### Slice C — explainable transfer linking

**WP8 — Transfer event/suggestion schema and same-currency matcher.**
Alembic `0006_transfer_events`; accepted event/legs plus persisted match
decisions per §§3.8–3.9. Event/leg storage admits unequal amounts and different
currencies; only this matcher enforces same-currency/equal-amount candidates.
Idempotency, uniqueness, dismiss suppression, and high-confidence requirements
are service invariants. Do not create the unused `TransferFx` table yet.

**WP9 — Review API and service actions.**
List suggestions; confirm, dismiss, manually link, and unlink. Validate ownership,
currency, account difference, signs, amount, and existing links. Return stable
reason codes suitable for UI copy.

**WP10 — Unified but auditable Activity Ledger presentation.**
Accepted events render once and expand to both statement legs/provenance.
Suggestions remain visibly unlinked until reviewed. Keyboard and screen-reader
behavior follows `PRODUCT.md` accessibility commitments.

**WP11 — Accounting and workflow tests.** §5. Keep tests beside the WP that adds
behavior; WP11 is the final cross-slice invariant pass, not the first time tests
are written.

**WP12 — Privacy-safe real-corpus acceptance.** Run on a disposable migrated DB
against a user-supplied corpus path. Validate AMEX CSV → HSBC bank CSV → HSBC
card PDF → AMEX PDF → HSBC bank PDF. Record only issuer/adapter, hashes, row
counts, semantic totals, match outcomes, and blocking reason codes. Never commit
raw statements, account numbers, or transaction descriptions.

### Follow-on roadmap — designed here, implemented separately

**Slice D — Manual cross-currency transfers.** Link differently denominated
legs such as `-£1,000` and `+₹118,400`; add `TransferFx`, derive the implied
INR/GBP rate from authoritative legs, and support an optional fee leg. Automatic
cross-currency proposals remain later work.

**Slice E — General financial intelligence.** Add deterministic
`DataCatalogService` capabilities: `get_data_coverage`, `list_accounts`,
`get_account_coverage`, `get_import_history`, `get_available_currencies`, and
`get_capabilities`. Route advisor questions through capability-based Data,
Spending, Cashflow, Wealth, Investment, Planning, and System toolsets. This is a
separate AI-engineering plan; the current work only exposes stable deterministic
account/import metadata that it can consume.

**Slice F — Investments.** Add investment accounts, holdings, price snapshots,
NAV providers, FX valuation, and net-worth integration after trusted input and
cross-currency event semantics exist.

### Existing data

All 365 rows in `data/pfa.db` sit on `account_id=1` with no record of which
statement they came from beyond `import_batches`. They are not reliably
back-attributable, and the AMEX PDF subset has incorrect years. Do not silently
rewrite, discard, or trust those rows.

- Schema migration preserves every row and assigns no guessed type.
- Corpus validation uses a new disposable database, never `data/pfa.db`.
- Produce a remediation report grouping committed transaction IDs by import
  batch and identifying rows that cannot be attributed safely.
- Offer an explicit backup + clean re-import path. The user chooses whether to
  keep the old DB read-only, undo attributable batches, or switch to the clean
  DB after comparing counts/totals.
- Verify the backup opens and contains expected counts before any replacement.
  `data/pfa.db.bak` alone is evidence, not a recovery guarantee.

---

## 5. Required tests

Tests stay offline and use sanitized fixtures; the normal suite never reads the
private corpus or calls Ollama.

### Account and adapter contract

```
adapter_detection_does_not_use_filename_or_account_name
adapter_detection_is_stable_after_account_rename
known_adapter_blocks_incompatible_account_type
known_adapter_hides_amount_sign_override
generic_adapter_requires_explicit_sign_confirmation
canonical_sign_is_independent_of_source_sign_convention
existing_account_binding_uses_id_not_name
duplicate_account_names_are_allowed_and_bind_by_id
strong_duplicate_account_suspicion_warns_but_does_not_merge
new_account_and_transactions_commit_atomically
discarded_batch_does_not_create_account
duplicate_name_with_conflicting_type_or_currency_is_rejected
inactive_account_cannot_receive_import
currency_mismatch_blocks_commit
hsbc_current_pdf_uses_paid_out_or_paid_in_not_balance
pdf_statement_period_supplies_year
pdf_second_date_populates_posted_date_and_not_description
balance_forward_rows_are_not_candidates
asset_statement_balance_reconciles_from_canonical_movements
liability_statement_balance_reconciles_from_canonical_movements
known_statement_balance_mismatch_blocks_commit
missing_balance_evidence_reports_not_available_not_passed
reconciliation_reports_arithmetic_and_coverage_independently
excluded_valid_row_makes_reconciliation_coverage_incomplete
verified_ledger_duplicate_satisfies_reconciliation_coverage
```

Canonical adapter equivalence fixture:

```
adapter A source purchase: +42.50 (debit-positive)
adapter B source purchase: -42.50 (signed)
expect both signed_minor == -4250
```

### Preview, commit, and recovery

```
preview_semantic_totals_expose_card_signs
commit_requires_resolved_adapter_and_account
undo_import_removes_only_batch_transactions_and_derived_links
undo_import_does_not_delete_opposite_transfer_leg_from_other_batch
undo_import_is_idempotent
undo_warns_when_later_edits_depend_on_imported_rows
```

For a transfer linked across HSBC batch A and AMEX batch B, undoing batch B
deletes the AMEX transaction and link but preserves the HSBC transaction.

### Accounting invariants

```
credit_card_charge_counts_as_spending_not_income
credit_card_payment_counts_as_repayment_not_spending_or_income
card_refund_reduces_spending
card_payment_refund_cashback_and_chargeback_are_not_confused
current_cash_excludes_credit_card_activity
current_cash_includes_signed_bank_leg_of_card_payment
current_cash_uses_only_transactions_after_opening_balance_baseline
current_cash_excludes_transactions_on_end_of_day_baseline
historical_cash_before_known_baseline_is_unknown
missing_cash_baseline_reports_incomplete_coverage
transfer_between_two_liquid_accounts_nets_to_zero_cash
debt_repayment_counts_card_leg_once_after_pairing
debt_cost_contains_interest_and_fees_not_principal
accepted_transfer_event_does_not_change_spending_totals
card_side_payment_classifies_without_bank_leg
bank_side_payment_classifies_only_with_owned_card_evidence
bank_side_payment_without_owned_card_is_unresolved_not_expense
card_payment_totals_are_import_order_independent
```

The paired £1,651.71 regression has four distinct expected effects:

```
HSBC   AMERICAN EXPRESS DD           -165171
AMEX   PAYMENT RECEIVED - THANK YOU  +165171

expect: spending contribution 0
        income contribution   0
        liquid cash change    -165171
        net-worth change      0
        debt repayment        165171 (counted once from card leg)
        one accepted TransferEvent, HSBC → AMEX, £1,651.71
        two transaction records preserved
```

### Matching and review

```
strong_institution_cue_auto_pairs_card_payment
single_weak_same_amount_candidate_is_only_suggested
multiple_same_amount_candidates_are_ambiguous
same_account_or_same_sign_never_pairs
already_linked_transaction_cannot_join_second_event
matcher_rerun_is_idempotent
dismissed_suggestion_does_not_reappear
manual_unlink_suppresses_immediate_relink
payment_pair_preserves_both_transactions_and_provenance
accepted_event_has_one_source_and_one_destination_role
fee_transaction_uses_fee_leg_role
transfer_fx_rate_is_destination_units_per_source_unit
```

The inversion that started this becomes a sanitized fixture guard:

```
amex_csv_charges_are_spending_not_income
    importing a structurally equivalent fixture to a credit_card account yields
    34 expenses and 1 transfer — never 34 income rows
```

Add API integration tests for every new request/response/error contract, Alembic
upgrade/downgrade tests for `0005` and `0006`, and a browser-level test for
keyboard account selection, commit blocking, receipt, and undo.

---

## 6. Verification gate

All four must pass before any WP is called done (matches CI):

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest
```

mypy is `strict` over `src`. Everything runs through `uv`, never bare
`python`/`pytest`. `uv run pfa db migrate` before any CLI/API workflow.

Work on a feature branch, conventional commits (`type(scope): subject`), open a
PR — never commit to `main`.

---

## 7. Decisions already made — do not relitigate

1. Adapter detection comes from statement content, never account/free-text name.
2. Account binding uses stable IDs or an atomic new-account draft. Display names
   may repeat and are never business identity.
3. Account type validates adapter compatibility; it does not determine raw sign.
4. `current_cash` is liquid assets only, includes signed transfer legs, and uses
   transactions strictly after each end-of-day balance baseline. Missing or
   future baselines make the requested period unknown/incomplete. Net position
   is a separate future metric.
5. Both deterministic card-side and evidence-backed bank-side repayments become
   `TRANSFER/CREDIT_CARD_PAYMENT`, not spending. An uncorroborated bank descriptor
   is unresolved, never a broad automatic transfer rule.
6. Repayment and cost-of-debt metrics stay separate and cannot double count
   paired legs.
7. Both transfer legs are preserved. Pairing links, never merges.
8. Canonical sign is economic net-worth effect. `flow_direction` remains a
   legacy normalized storage field, not source/accounting debit-credit truth;
   downstream code uses `signed_minor`. No signed-amount migration.
9. Weak or ambiguous pairs require review; a sole candidate is not proof.
10. Imports are reversible through a batch receipt; existing data is never
    silently rewritten or discarded.
11. Transfer events permit 2+ legs with different currencies and unequal
    amounts. Roles are explicit `source`/`destination`/`fee`; FX rate orientation
    is destination units per source unit. v0.2 matching remains same-currency/
    two-leg; `TransferFx` ships with manual cross-currency linking in Slice D,
    not as an unused table.
12. Opening balances are dated natural account balances. Available/current/
    statement balances remain distinct; known statement balance evidence is
    reconciled rather than reduced to row counts. Reconciled means arithmetic
    integrity PASS and source-row coverage integrity PASS.
13. Advisor introspection/tool routing is Slice E. This work exposes stable
    deterministic metadata but does not expand into AI behavior.

## 8. Product acceptance gate

The slice is usable only when a non-technical user can complete this sequence
without knowing accounting terminology:

1. Upload a recognized AMEX statement.
2. Understand why PFA suggests a credit-card account.
3. Create or select that account and see the sanity box: statement period,
   purchases, payment, income, unresolved rows, PFA spending/repayment effects,
   and reconciliation status before commit.
4. Commit, see a receipt, and undo it safely; edited imported rows produce a
   clear count and confirmation rather than a dependency-graph workflow.
5. Import the matching bank statement and understand whether PFA linked the
   payment automatically or needs confirmation.
6. See spending, income, liquid cash, repayment, and debt-cost totals remain
   consistent before and after linking. If a requested cash period predates a
   known baseline, see “historical balance unavailable/incomplete” rather than a
   fabricated total.

Failure copy names the problem and next action. No raw `debit`/`credit`, adapter
class name, stack trace, or silent fallback is exposed as user guidance.
