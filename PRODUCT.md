# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

delegated: plain HTML/CSS/vanilla JavaScript served by the existing FastAPI app; no frontend framework added for a local-first dashboard

## Users

One person reviewing their own private financial activity on a local machine.

## Product Purpose

PFA helps a person understand and improve their financial life over time through evidence-backed
analysis, deterministic calculations, scenarios, and concise explanations.

## Positioning

Financial facts come from deterministic Python/SQLite services. The local model interprets those
facts but is never the source of totals, balances, rates, or projections.

## Operating Context

The user imports synthetic or personal bank CSVs, reviews monthly changes, checks budgets and goals,
asks natural-language questions, and simulates decisions. The application runs locally with SQLite
and optional Ollama.

## Capabilities and Constraints

Transactions, accounts, budgets, goals, imports, analytics, scenarios, recurring-payment evidence,
anomaly/trend signals, a CLI, and a FastAPI API are implemented. Initial behavior is read-only and
advisory. No cloud APIs, money movement, brokerage execution, or hosted telemetry.

## Brand Commitments

The product name is PFA (Personal Finance Agent). Voice is concise, plain-language, evidence-backed,
transparent about assumptions, and respectful of the user's decision authority.

## Evidence on Hand

`data/demo_transactions.csv` is synthetic demonstration data covering June–August 2026. No real
financial claims, testimonials, or commercial proof should be fabricated.

## Product Principles

- Deterministic calculations outrank model guesses.
- Local privacy is the default.
- Show evidence and assumptions beside recommendations.
- Keep the user in control; initial features are read-only.
- Prefer small, explainable workflows over speculative infrastructure.

## Accessibility & Inclusion

The dashboard must support keyboard navigation, visible focus, semantic HTML, responsive layouts,
adequate contrast, reduced-motion preferences, and text alternatives for status indicators.

## Assumptions

The UI surface, visual direction, and frontend implementation path are inferred because no visual
brief or question mechanism was available in this session. Revisit these if the user supplies a
different dashboard scope, brand direction, or frontend constraint.
