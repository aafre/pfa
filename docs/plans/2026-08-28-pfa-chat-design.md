# PFA Ask / Chat Design Brief

Status: confirmed direction; ready for implementation
Date: 2026-08-28

## Job and audience

The Ask surface is for one person reviewing their own finances and wanting a fast, plain-language explanation of a month, transaction, budget, or goal. They arrive curious or uncertain, not looking for an open-ended chatbot. The surface operates as an investigation desk: ask a question, understand the answer, inspect the evidence, and decide what to look at next.

## Outcome and proof

Primary outcome: the user gets a useful answer to a monthly “why did this change?” question and can verify it without trusting model confidence. Success means the answer is understandable, the period and scope are explicit, deterministic facts are separated from interpretation, and source transactions/calculations are one interaction away.

PFA-specific truth remains visible: Python/SQLite services own totals, balances, rates, and projections. The local model may explain those facts but cannot invent or replace them. When Ollama is unavailable, deterministic answers remain usable and the UI names the degraded state.

## Selected direction

Build a dedicated Ask page with a contextual entry point from Overview. Use the existing evidence-led audit-desk world: navy shell, warm paper workspace, orange review flags, mint health states, compact data typography, and a fixed facts-used spine. Chat is the focal interaction, but evidence is the product signature.

Visitor sequence:

1. Choose or confirm the period context.
2. Pick a suggested investigation or write a question.
3. Submit and see an explicit answering state.
4. Read the answer with a deterministic/model provenance label.
5. Expand “Facts used” to inspect source rows, calculations, assumptions, and next links.
6. Continue the conversation or open the linked Activity/Review surface.

Signature interaction: an answer’s facts-used rail expands inline beside the response on desktop and below it on mobile, preserving the question/answer context while exposing evidence without a modal interruption.

## Scope and boundaries

First build is a production-ready single conversation, not a multi-user assistant or autonomous financial advisor. Include prompt suggestions, current-month context, conversation state for the active browser session, `/chat` integration, facts-used evidence, loading, deterministic fallback, model-unavailable error, empty, and long-answer states. Keep Overview content and the existing navigation language intact. Do not add financial claims, cloud telemetry, money movement, automatic recommendations, or persistent server-side chat history in this slice.

## States and realistic ranges

- Empty: 3–5 suggested prompts tied to the selected month.
- Typical: 3–8 turns, answers under 500 words, 3–8 evidence rows.
- Long: answers over 500 words wrap cleanly and keep evidence accessible.
- Loading: composer remains understandable; response region announces progress.
- Deterministic: label answer as calculated from PFA data.
- Model unavailable: explain that local model is unavailable and suggest deterministic questions.
- Invalid/blank input: inline recovery message; preserve typed text.
- Offline/API failure: keep previous conversation visible and provide retry.

## Interaction and layout

Desktop: persistent navigation rail, page heading with month context, wide conversation column, and an evidence column that can pin the current answer’s facts. Mobile: top navigation, full-width composer, stacked answers, evidence expansion below each answer, and touch targets at least 44px. Composer supports keyboard submit, visible focus, disabled/loading state, and a clear action label. Never rely on color alone for provenance or confidence.

Answers use readable measure, strong question/answer distinction, restrained metadata, and explicit links such as “Open 12 source transactions.” Render model text safely; do not inject raw HTML. Keep the current month attached to requests and show when a question is interpreted against another period.

## Constraints and implementation consequences

The existing API returns `{answer}` from `POST /chat`; the UI can initially derive a compact facts-used panel from the selected period’s existing analytics/review endpoints. A follow-up API contract should expose provenance, period, facts used, assumptions, and linked transaction/category identifiers as structured fields. Add no framework dependency; use the existing vanilla HTML/CSS/JS surface and shared tokens/components.

Acceptance criteria:

- A user can ask a monthly question and receive a visible answer or actionable error.
- The current period is explicit before and after submission.
- Every answer shows whether it is deterministic or model-assisted.
- Facts used expand without losing the answer and link back to evidence.
- Keyboard, mobile, reduced-motion, empty, loading, and model-unavailable paths work.
- The surface feels native to the existing PFA audit-desk visual system.
