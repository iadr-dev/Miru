# ADR 0014 — Web search: Brave + LLM-native, per-turn enablement

## Status
Accepted. Adds a web-search capability across sources ([01](../modules/01-sources.md)),
LLM adapter ([04](../modules/04-llm-adapter.md)), and tools ([03](../modules/03-tools.md)).

## Context
The agent chat has a **web-search toggle**. Two realities: (1) some LLMs offer **native web
search / grounding** (Anthropic `web_search`, OpenAI, Gemini) which is best when available;
(2) we're provider-agnostic and support local Ollama, which has no native search. We need one
capability that works everywhere and stays governed (non-trading, cite sources).

## Decision
- **One `web_search` tool** (+ `fetch_url`) with a uniform result shape, backed by two
  interchangeable engines:
  - **LLM-native** search via the provider (used when `model_capabilities.native_web_search`).
  - **Brave Search API** (`sources/web_search.py`, `BRAVE_API_KEY`) — provider-agnostic
    fallback and the only path for Ollama/no-native providers.
- **Selection:** `AITW_WEBSEARCH = auto|brave|native|off` (`auto` = native if available else Brave).
- **Enablement:** off by default; enabled per turn via `POST /agent/query {web_search:true}`
  (the chat toggle). When off, the tools aren't exposed to the model.
- **Governance:** external/unverified → every web-derived claim is **cited (source URL)**,
  caveated, non-trading; results pass the same guardrails and NL-`activity` narration.
- **Tiers:** basic search available to all (toggle); **`deep_research`** (multi-step
  search+read+synthesize) is Pro ([12](../modules/12-multi-user-tiers.md)).

## Consequences
- Web search works with every provider incl. local; native is used when it's better.
- Adds `WEB` as the 12th source adapter and a `native_web_search` capability flag.
- Citation enforcement is a guardrail concern (claims from web must carry URLs).
- Cost/rate: Brave key usage + native-search provider costs tracked in `usage`; deep_research
  bounded by tier quota + concurrency cap.
