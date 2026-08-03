# ADR 0001 — Provider-agnostic LLM adapter

## Status
Accepted.

## Context
The framework is a Claude Code-style agent, but the reference project's diagram hands off
to ChatGPT, and operators may want local models for cost/privacy. Hardcoding one vendor
would couple the agent runtime to a single API shape.

## Decision
Introduce an `LLMClient` protocol (`backend/src/llm/base.py`) with concrete clients for
Anthropic (default), OpenAI, Gemini, Groq, Cerebras, OpenRouter, and Ollama (local) — the
OpenAI-compatible ones share one base client. Tool-call formats are normalized in
the adapter. Provider and model are selected from config/env only.

## Consequences
- Core runtime depends on the protocol, never a vendor SDK.
- Adding a provider = one new client; no runtime changes.
- Slight normalization overhead for tool-call formats.
- Enables the diagram's ChatGPT handoff and a Claude-native internal loop from one codebase.
