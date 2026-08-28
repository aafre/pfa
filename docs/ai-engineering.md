# AI engineering in PFA

PFA keeps probabilistic interpretation behind a small, testable boundary.

## Prompt engineering

The advisor contract lives in `src/pfa/ai/agents/advisor.py`. It tells the model to call tools
before financial claims, avoid arithmetic, separate facts from assumptions, and remain advisory.
It also marks persisted financial text and tool results as untrusted data. The classifier has a
separate short instruction because classification is a narrower task.

## Structured output

`TransactionClassification` in `src/pfa/ai/schemas.py` is the classifier's output contract.
PydanticAI validates the model response. Application validation removes categories from income and
transfer results and removes transfer purposes from non-transfers. Unknown or failed output leaves
the imported row unresolved for review instead of becoming a silent financial fact.

## Tool calling and the agent loop

The advisor exposes functions in `src/pfa/ai/tools/finance.py`. PydanticAI describes their typed
parameters to the model, receives a tool call, validates arguments, executes the function with
`FinanceDependencies`, and appends the result to the model context. Conceptually:

```python
while not_done:
    response = model(context, tools)
    if response.tool_calls:
        execute_tools()
        append_results()
    else:
        return response
```

PFA tools call `AnalyticsService` and `PlanningService`; the model never gets arbitrary SQL.
Money-bearing tool results include integer `*_minor` fields plus deterministic `*_display` GBP
strings. An output validator rejects currency or percentage claims when the run has no tool result.
This is harness enforcement, not reliance on prompt obedience.
The validator proves tool-use presence, not semantic entailment of every sentence. Common numerical
intents bypass narration through deterministic answers; broader grounded-answer eval coverage remains
a stated limitation.

## Context engineering

The model receives system instructions, the current request, and small deterministic tool
results. It does not receive the entire transaction history or database. The database remains
the authoritative source of truth. Explicit goals and merchant corrections are operational data,
not an automatic transcript memory store.

## Harness engineering

The harness is the surrounding reliability boundary: typed tool registry, Pydantic validation,
dependency injection, bounded retries, tool timeouts, local-only model configuration, structured
health reporting, request/time/output limits, and graceful inference failure. Initial tools are
read-only. There is no tool
for money movement, brokerage execution, or arbitrary database access.
CLI/API advisor calls and the lazy classifier preflight Ollama model availability, so a missing
configured model fails within the health-check timeout instead of consuming inference retries.

## Workflow and termination

Known sequences such as CSV import, monthly review evidence, and scenario calculations are
ordinary deterministic workflows. Agentic selection is reserved for natural-language questions.
PydanticAI has configured retries and the application does not create an unbounded custom loop.

## Evals versus tests

Unit and integration tests prove money semantics, persistence, tools, and API behavior without
Ollama. Real-model evals belong under `evals/`; they are slower, model-dependent, and excluded from
normal `pytest`. The checked-in four-case classifier eval is only a compatibility smoke for signed
input and structured output. Its cases are handled by deterministic rules in normal imports, so its
accuracy must not be presented as production classifier quality. A representative residual-case
dataset and grounded-answer eval remain required before broader model-quality claims.

## Deterministic versus probabilistic

Monthly spending, savings rates, category trends, recurring evidence, and projections are
deterministic. The model can decide that a user question needs a category comparison and explain
the returned evidence. It cannot decide that £632.17 was spent by doing arithmetic in prose.
