# PFA UI/UX Plan

Status: draft with first slice implemented
Date: 2026-08-28

## Product frame

PFA is a local-first, single-user finance dashboard. Deterministic Python/SQLite services own totals, balances, rates, and projections; optional local-model output interprets those facts but never replaces them. The UI must keep evidence and assumptions beside recommendations.

## Visual direction

Use an evidence-led audit desk: a quiet navy navigation shell, warm paper workspace, orange review flags, mint health states, and compact data typography. The recurring visual grammar is a fixed evidence spine: position first, ranked changes second, supporting detail third. Avoid decorative charts, color-only meaning, and generic card grids.

## Information architecture

- Overview: monthly position, cashflow, spending, savings, key changes, and next inspections.
- Activity: searchable transactions with category, account, date, amount, provenance, and review state.
- Plan: budgets, goals, recurring commitments, and progress.
- Scenarios: purchase simulation with baseline, assumptions, and resulting impact.
- Review: anomalies, trends, unresolved classifications, and recommendations.
- Ask: natural-language questions with a visible facts-used panel and model state.
- Import: local CSV validation, duplicate handling, row errors, and post-import summary.

Navigation stays stable. Current month and data freshness stay in the shell. Use inline panels and drawers before modal workflows.

## First viewport

Overview + Monthly Review is the first slice. It proves PFA’s differentiator fastest: a user can see current position, identify the highest-impact change, and trace it to deterministic source data.

1. Trust header: selected month, freshness, local-only state, and data health.
2. Position band: income, spending, net movement, savings rate, with comparison text.
3. Evidence trail: ranked changes with amount, baseline, provenance, and inspect action.
4. Supporting lanes: category bars paired with a screen-reader table; three-month cashflow bars.
5. Review queue: spikes, recurring payments, goals, and unresolved work.
6. Evidence drawer: period, row count, calculation, delta, and interpretation availability.

## Accessibility and responsive behavior

Semantic headings, labeled controls, visible focus, keyboard navigation, 44px-class touch targets, contrast-safe semantic colors, text alternatives for charts, and reduced-motion support are required. Desktop uses a persistent rail and two-column evidence layout. Tablet collapses to a top navigation rail before clipping. Mobile keeps the review trail ahead of secondary analytics, turns metric bands into a two-column sequence, and preserves evidence links beside each recommendation.

## Implementation phases

1. Foundation: vanilla HTML/CSS/JS mounted in FastAPI; shared tokens, shell, API client, loading/error conventions, and demo fallback.
2. Overview + Review: monthly summary, categories, health, review evidence, month switching, and evidence drawer.
3. Activity + Import: transaction filters, CSV preview, duplicates, row errors, and unresolved classification review.
4. Plan: budgets, goals, progress thresholds, empty states, and evidence links.
5. Scenarios + Ask: purchase simulation, assumptions, baseline/delta comparison, model availability, and facts used.
6. Hardening: responsive, keyboard, screen-reader, reduced-motion, offline, empty, loading, error, API contract, and browser smoke passes.

## Success criteria

- A first-time user finds current position and highest-impact change within one viewport.
- Every recommendation traces to deterministic evidence.
- Import problems remain actionable at row level.
- All surfaces share one interaction vocabulary and state language.
- Core workflows remain usable on mobile, by keyboard, with reduced motion, and without Ollama.

## Open decision

Start with Overview + Monthly Review. Activity + Import follows as the next vertical slice because the review surface establishes the product’s trust model before users act on raw ledger data.
