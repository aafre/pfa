# Personal Finance Agent

A 100% local personal finance intelligence system that runs entirely on your machine.

LocalLedger understands your financial position over time, explains where money is going, detects anomalies and behavioural drift, models future decisions, and enforces deterministic budget tracking without leaking data to cloud APIs.

## Architecture Principles

- **Zero Data Leakage:** Powered by local models via Ollama.
- **Deterministic Compute Isolation:** Math, aggregations, and balance histories are calculated in Python/SQLite. The LLM acts strictly as a decision, routing, and interpretation engine.
- **Type-Safe Agent Loop:** Built with PydanticAI for strict schema enforcement, structured outputs, and self-healing validation retries.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (Fast Python package manager)
- [Ollama](https://ollama.com/) running locally

```bash
ollama pull qwen2.5:3b
