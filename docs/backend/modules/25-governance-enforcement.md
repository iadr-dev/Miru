# 25 — Governance enforcement (where the rules live in code)

How the non-negotiable rules are enforced **in code** - not a restatement of the rules. The rules
themselves are stated in [backend/CLAUDE.md](../../../backend/CLAUDE.md) §4 (governance-as-code) and
§8 (security) and in [AGENTS.md](../../../AGENTS.md); this page maps each one to the file that
enforces it and the tests that pin it. Code: `backend/src/governance/`, `agent/guardrails.py`,
`tools/base.py`, `accounts/entitlements.py`, `accounts/hooks.py`, `research/untrusted.py`,
`schemas/urlsafety.py`, `sources/tls.py`, `config/settings.py`.

| Rule | Enforced in | Pinned by |
|---|---|---|
| No trading output | `governance/textscan.py` (`check_output`, `scan_structured_payload`) | `tests/agent/test_guardrails.py` |
| No whole-market scan | `agent/guardrails.py::check_tool_call` | `tests/agent/test_guardrails.py` |
| Sponsor data masked below Premium | `tools/base.py::dispatch_tool` | `tests/tools/test_dispatch_entitlement.py` |
| Tier limits / feature gates | `accounts/entitlements.py` | `tests/accounts/test_entitlements.py` |
| Caveat on every source result | `schemas/source.py::SourceResult` | `tests/context/test_conversation.py` |
| Untrusted web text is data | `research/untrusted.py`, `schemas/urlsafety.py` | `tests/research/*`, `tests/sources/test_web_fetch_page.py` |
| Secrets never printed | `config/settings.py` (`SecretStr`) | `tests/config/test_secret_settings.py` |

## 1. Non-trading output (`governance/textscan.py`)

`governance/` is a **leaf package**: deliberately absent from `LAYER_ORDER` in
`tests/arch/test_layering.py`, so `analytics` (below `agent`) can scan LLM-generated text without an
upward import. `agent/guardrails.py` re-exports every name unchanged, so older callers still import
from there.

- **Detection** is keyword/regex on *advice-shaped* phrasing, not bare verbs: "you should buy/sell/
  hold", "we/I recommend buying...", "buy/sell rating|recommendation|call", "buy/sell now", "strong
  buy/sell", "price target", "target price", "PT of", position sizing, "allocate N%", "how many
  shares should...", plus 目標價 / 買進建議 / 賣出建議. "Bought/sold" and "buy signal" used to
  describe flows already happened are deliberately allowed.
- **`check_output(text, *, has_market_data=True) -> HookVerdict`** never blocks (`allowed` is always
  true). It rebuilds the text losslessly sentence by sentence (newlines and markdown lists survive):
  an offending sentence becomes the **redaction notice**, adjacent violations collapse to one notice,
  and our own data-vendor names (FinMind, yfinance, Yahoo) are replaced by "the data provider"
  (URLs are left untouched). If `has_market_data` and no caveat marker is present
  (`_CAVEAT_MARKERS`, EN + 中文), the standard caveat is appended. `reason` is one of
  `trading_language_redacted`, `vendor_name_redacted`, `caveat_appended`.
- **Language follows the text.** `_is_chinese` (CJK characters >= Latin words) picks the variant:

  | | EN | ZH |
  |---|---|---|
  | Redaction notice | `REDACTION_NOTICE_EN` "A specific trading recommendation or price target was omitted here." | `REDACTION_NOTICE_ZH` "此處已省略具體的買賣操作建議或價位目標。" (worded to avoid its own trigger terms, so a re-scan is stable) |
  | Caveat | `NON_TRADING_CAVEAT` | "以上僅為資料觀察，不構成買賣建議。" |

- **`scan_structured_payload(value) -> (cleaned, redacted)`** walks dicts/lists; every string value
  with trading language becomes the notice, others get vendor-name stripping. It never appends a
  caveat - the caller stamps one.
- **`scan_sentence` / `split_sentences_streaming`** let the streaming path scan each sentence before
  it is sent as a `message.delta`; the caveat is appended only by the whole-answer pass.

**Where it is applied** (no config path skips any of these):

| Surface | Call site |
|---|---|
| Agent final answer | `agent/loop.py::_finalize_output` -> `check_output(text)`; recorded in the journal |
| Streamed answer sentences | `agent/loop.py::_stream_step` -> `scan_sentence` |
| Thinking chunks | `agent/loop.py` -> `check_output(..., has_market_data=False)` |
| Rich cards / tool results narrated to the model | `agent/loop.py::_build_card` and the result renderer -> `scan_structured_payload`; card stamped with `non_trading_caveat` |
| Multi-lane synthesis | `orchestrator/synthesis.py` -> `check_output` on the merged text |
| Deep research | `research/render.py` (title and every claim `has_market_data=False`, finished markdown `True`), `research/adapters.py` (market-data results via `scan_structured_payload`) - see [24](24-deep-research.md) |
| Stock summaries | `analytics/summary_generation.py` -> `scan_structured_payload` |
| Voice, scheduler, morning brief | `voice/service.py`, `scheduler/worker.py`, `tools/morning_brief_tools.py` |

Personality, pet and skill text sit below the governance prompt and pass through the same scan
([23](23-agent-personality.md), [21](21-pet-gamification.md), [08](08-skills.md)).

## 2. Tool-call guard (`agent/guardrails.py`)

`check_tool_call(tool_name, arguments)` is the `pre_tool` veto, called first in
`AgentLoop._dispatch_one` (before the tool is even looked up). It returns `allowed=False` when
- an argument **key** contains `scan_all`, `full_market`, `whole_market`, `market_wide`,
  `all_symbols`; or
- a string **value** is `*`, `all`, `all_symbols` or `market`; or
- a **list** value has more than 20 entries (`_MAX_SYMBOLS_PER_CALL`).

A veto becomes an `is_error` tool result `blocked: <reason>`, journaled with
`guardrail_allowed=False`. Tool schemas already take one `(symbol, market)`, so it rarely fires; it
is the enforced seam, not a substitute for narrow schemas. Deep research reuses it for its data
lookups. `guardrails.py` has **no citation check**: web-claim citation is structural in `research/`
([24](24-deep-research.md) §2), and the caveat is enforced by `check_output` above.

## 3. Entitlements (separate from the guard)

Two independent hooks; neither stands in for the other.

- **Data class.** `schemas.enums.DataClass` is `public | sponsor | private`. `private` is one user's
  own account data (watchlists, alerts, schedules, usage, notifications): `dispatch_tool` does not run
  a `private` tool for an anonymous caller (`ctx.user_id is None`) and returns `SignInRequired`
  (a normal result); the tool reads only `ctx.user_id`'s rows (the id never comes from a tool
  argument), it is free at every signed-in tier (`is_entitled` is true), and it is excluded from MCP by
  class (`mcp.app.is_mcp_offered`). Tests: `tests/tools/test_private_data_class.py`,
  `tests/tools/test_account_tools.py`, `tests/accounts/test_account_reader.py`,
  `tests/mcp/test_private_tools_excluded.py`. `tools/base.py::dispatch_tool`
  runs on every tool call: a `sponsor` tool for a tier that fails `is_entitled` (only Premium and Pro
  pass) does **not run**; it returns a `MaskedTeaser` (`locked=True`, `required_tier`, `preview`,
  `tool_name`) - a normal successful result, never an error. The loop builds no card from a teaser
  and journals `masked_teaser`.
- **Row-preview exception.** A `sponsor` tool with `supports_row_preview = True` is run even for an
  unentitled caller; the tool itself nulls the value fields of every row beyond its own free row
  count. Entitled callers always get every row.
- **Tier resolution.** `accounts/hooks.py::EntitlementResolver.tool_context_for(user)` maps the user
  to `ToolContext.tier` via `finmind_class_for` (anonymous -> Free). The API builds the context once
  per request (`api/dependencies.py::tool_context_for`) and `dispatch_tool` re-applies the gate on
  **every tool call**; `ToolContext` is frozen and `tools/` never queries accounts or a database.
- **MCP sign-in does not change this.** An MCP caller signs in through the app's own OAuth
  authorization server, but the access token (`accounts/mcp_tokens.py`) carries only the user, the
  client and the grant - never a tier or entitlement. `mcp/accounts_resolver.py` reads the tier from
  the user row on every MCP call, so a plan change or a lapsed payment applies from the next request
  and a revoked grant stops the token at once (`tests/mcp/test_connector_auth.py`,
  `tests/mcp/test_oauth_end_to_end.py`).
- **Tiers** (`accounts/entitlements.py`, config tables, not scattered constants):

  | Tier | Chats / day | Requests / min | Sponsor data |
  |---|---|---|---|
  | Free | 3 | 60 | masked |
  | Premium | 100 | 300 | yes |
  | Pro | unlimited | 600 | yes |

  `FEATURE_MIN_TIER`: `live_quote_stream`, `ai_summary`, `swot`, `full_score`, `tick_minute_k` ->
  Premium; `deep_research`, `supply_chain` and `concept_themes` -> **Pro** (the last two are previews below Pro, applied
  server-side by `access_for` in the theme tools - [12](12-multi-user-tiers.md)). `feature_allowed` treats an unlisted feature as open. Deep
  research has **no daily cap**, only its own run budget plus the normal rate limit and chat
  reservation ([24](24-deep-research.md) §3). BYOK lifts only the chat limit (`chat_limit_for`). Free
  accounts on the platform key may pick only `FREE_PLATFORM_MODELS`. Tier management and gating at
  the API: [12](12-multi-user-tiers.md); tool contract: [03](03-tools.md).

## 4. Caveats end to end

`SourceResult.currentness_caveat` (`schemas/source.py`) is `Field(min_length=1)`: an adapter cannot
build a result without a staleness statement, and `dataaccess/router.py` builds an explicit
"no healthy source" caveat when nothing answers. Path: adapter -> `SourceResult` ->
`context/conversation.py::_fact_from_result` copies it onto every `ConversationFact` (compaction
never drops it) and `context/source_health.py` carries it -> tool output models declare their own
non-empty `currentness_caveat` (e.g. `tools/analyst_tools.py`) -> the model sees it in the rendered
tool result -> cards are stamped with `non_trading_caveat` -> `check_output` appends the standard
caveat when the prose has none -> the web renders `<Caveat>` (§7). See [01](01-sources.md),
[19](19-data-access-router.md).

## 5. Untrusted external content

Fetched web text is data, never instructions. `research/untrusted.py` wraps pages in `<source>`
blocks with a standing notice, defuses delimiters, and drops pages with instruction-like phrases
(EN + 中文); the `check_output` backstop handles whatever still gets through. URLs must pass
`schemas/urlsafety.canonical_url` (public http/https, no credentials, no internal hosts) before
entering the ledger, and the fetch re-validates DNS and every redirect hop
(`sources/web_search.py::fetch_page`). Outbound HTTPS goes through `sources/tls.py::
build_async_client`: strict verification by default, any other `TLSPolicy` explicit and loudly
logged, ruff `S` blocking `verify=False` elsewhere. Detail: [24](24-deep-research.md) §2,
[01](01-sources.md).

## 6. Secrets

Every credential in `config/settings.py` is `SecretStr` (provider keys, `jwt_secret_key`,
`secret_encryption_keys`, OAuth/LINE/Telegram/Resend secrets), so a printed or logged settings
object is masked; plain values are unwrapped only at the call that needs them.
`tests/config/test_secret_settings.py` fails on a credential-looking setting typed as plain `str`.
BYOK keys are encrypted at rest (`accounts/crypto.py`, `accounts/secrets.py`) and read at call
time; `backend/.env` is gitignored. The journal records tool argument *keys* and field names, never
values. `tests/payments/test_no_secret_logging.py` pins the payments path. See
[16](16-infrastructure.md).

## 7. How it is enforced

- **Architecture tests** (`backend/tests/arch/`, run on every change): `test_layering.py` fails on
  any upward import in `LAYER_ORDER` and on a `sources` import from outside `dataaccess`/`ingest`;
  `test_network_io.py` fails on a network-client import outside `sources/` unless the module is on
  its reviewed allow-list (stale entries fail too); `test_explicit_any.py` fails a `noqa: ANN401`
  without a ` - reason`. `test_compose_env_coverage.py` keeps deployment env in step with settings.
- **`governance` marker** (`pyproject.toml`: "highest-priority guardrail/entitlement suite"; also in
  the pre-commit fast subset). Main modules: `tests/agent/test_guardrails.py`,
  `tests/tools/test_dispatch_entitlement.py`, `tests/accounts/test_entitlements.py`,
  `tests/api/test_governance.py`, `tests/api/test_phase11_governance.py`,
  `tests/context/test_conversation.py`, `tests/research/*`, `tests/personality/test_registry.py`,
  `tests/pets/test_persona.py`, `tests/payments/test_no_secret_logging.py`.
- **Web side** ([web/OVERVIEW.md](../../../web/OVERVIEW.md) §4): 紅漲綠跌 as a token (`--up`/`--down`,
  always paired with ▲/▼, sign and an `aria-label`, never color alone); a required, non-removable
  `<Caveat>` on every analytical or quote surface (`chat/caveat.tsx`); the 馬賽克 teaser for
  `tool.locked` handled as HTTP 200; no trading words in copy; analyst targets always attributed.
