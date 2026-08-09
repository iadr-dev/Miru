from __future__ import annotations

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from schemas.enums import TLSPolicy


class Settings(BaseSettings):
    """Runtime config from env (backend/.env — see .env.example for the authoritative list).

    Values only — no provider/model/source is ever hardcoded; everything comes from env.
    `.env` is gitignored; never read its values aloud, echo, or log.
    """

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", env_file=".env")

    # ---- Core app config ----
    aitw_env: str = "development"  # development | production
    ingest_on_startup: bool = False  # MUST be false in production (backend/.env.example)
    dev_backfill_years: int = 3
    webhook_base_url: str = "http://localhost:8000"
    # Comma-separated browser origins allowed to call the API with credentials. Empty = no CORS
    # middleware installed (same-origin only). Parsed by `allowed_origins()`.
    cors_allowed_origins: str = ""

    # ---- Scheduler process loop ----
    # The standalone scheduler process wakes on this cadence to fire due jobs and evaluate alerts.
    # A job's own trigger cadence is at least a day apart, so a coarse poll (default one minute) is
    # ample headroom while keeping the leader responsive to just-due work; it never drives how often
    # a job itself fires, only how promptly the loop notices one became due.
    scheduler_poll_interval_seconds: float = 60.0

    # ---- Platform-ingest process loop ----
    # The standalone ingest process wakes on this cadence to notice when a whole-market pull's
    # documented daily fire time has passed. Each job fires at most once per trading day at a fixed
    # time-of-day (a code constant tracking the upstream's publish clock, not a setting); this poll
    # only governs how promptly the loop notices a fire time elapsed, never how often a job fires.
    ingest_poll_interval_seconds: float = 60.0

    # ---- Datastores ----
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    duckdb_path: str = "./data/duckdb"
    parquet_root: str = "./data/parquet"

    # ---- Standalone MCP process (`python -m mcp`) ----
    # A separate remote streamable-HTTP process, never wired into the main API process — mirrors
    # how the scheduler/ingest daemons are already separate `__main__.py` processes. Host/port are
    # container-fixed the same way the API's are (`docker-entrypoint.sh`); replicas/concurrency are
    # an orchestrator concern. `mcp_allowed_origins`/`mcp_allowed_hosts` feed `build_http_app`'s
    # DNS-rebinding protection directly — empty means "use FastMCP's own safe defaults", never
    # "allow everything".
    mcp_host: str = "0.0.0.0"  # noqa: S104 — container-internal bind; the ingress terminates TLS
    mcp_port: int = 8100
    mcp_allowed_origins: str = ""
    mcp_allowed_hosts: str = ""

    def mcp_allowed_origins_list(self) -> list[str] | None:
        origins = [o.strip() for o in self.mcp_allowed_origins.split(",") if o.strip()]
        return origins or None

    def mcp_allowed_hosts_list(self) -> list[str] | None:
        hosts = [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]
        return hosts or None

    # ---- Cross-process event transport ----
    # `memory` is the single-process default (dev, tests): the live SSE fan-out and the running
    # turn live in the same worker, so an in-process bus suffices. `redis` is the multi-worker
    # backend: a turn runs on one worker while a reconnecting client's stream may land on another,
    # so live events cross workers via Redis Pub/Sub keyed by turn, and durable replay reads the
    # persisted `stream_events` log. The selection is config, never hardcoded at the wiring site.
    event_bus_backend: Literal["memory", "redis"] = "memory"
    redis_url: str = "redis://localhost:6379/0"  # never logged raw; may embed auth in prod

    # ---- Rate limiting & quota enforcement ----
    # `memory` is the single-process default (dev, tests): a per-process fixed-window counter,
    # correct where one worker holds. `redis` is the multi-worker backend: the request-rate gate
    # runs as an atomic Redis `INCR`+TTL so two concurrent workers can never both pass a boundary
    # check. The FinMind Sponsor pool reservation is always Redis-backed when a Redis URL is
    # configured (the pool is a single global budget shared across all paid users). Selection is
    # config, never hardcoded at the wiring site.
    rate_limiter_backend: Literal["memory", "redis"] = "memory"
    rate_limit_key_prefix: str = "ratelimit"
    # The FinMind Sponsor token is a hard 6000 req/hr ceiling pooled across ALL paid users; this
    # is the shared global budget the pre-flight reserver decrements before a fan-out spends it.
    finmind_pool_key_prefix: str = "finmind:pool"
    finmind_pool_limit_per_window: int = 6000
    finmind_pool_window_seconds: int = 3600
    # Cost (in Sponsor requests) charged optimistically per reserved fan-out lane; refunded down
    # to the lane's real consumption (or fully, on failure) once the lane finishes.
    finmind_reserve_per_lane: int = 1

    # ---- Live-quote demand-driven poller + cache-invalidation broker ----
    # The intraday quote feed is push, not client-polled: one shared poller per worker fetches a
    # symbol once per tick and fans it out to every subscriber, so N watchers of one symbol cost
    # one upstream call, not N. Cadence hints let a metered/mobile client ask for a coarser
    # interval; when subscribers disagree the poller serves the fastest requested rate so no
    # subscriber is starved. Caps keep the poller bounded observation, never an open-ended scan.
    quote_poll_cadence_normal_seconds: float = 5.0
    quote_poll_cadence_saver_seconds: float = 20.0
    quote_stream_max_symbols_per_connection: int = 50
    quote_stream_max_demanded_symbols: int = 500
    quote_channel_key_prefix: str = "quote"
    quote_demand_key_prefix: str = "demand"
    # The lightweight batch-completion invalidation signal the ingest leader emits so API workers
    # can tell a client to revalidate exactly the affected cache keys the moment fresh derived
    # data lands, instead of waiting out a fixed time boundary. Backend follows `event_bus_backend`
    # (Redis pub/sub cross-worker, or an in-process stream single-process).
    data_updated_key_prefix: str = "data.updated"

    # ---- TLS policy (M6D) ----
    tls_policy: TLSPolicy = TLSPolicy.STRICT

    # ---- LLM providers — set only the ones in use ----
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    cerebras_api_key: str | None = None
    openrouter_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.6"

    # ---- LLM selection + role routing (see llm/registry.py's selection logic) ----
    # Optional overrides of the documented default (Anthropic/claude-sonnet-5) and the
    # per-role model routing table — "provider/model" strings, e.g. "groq/llama-3.3-70b-
    # versatile". None means "use llm/registry.py's documented default for this slot".
    aitw_llm_provider: str | None = None
    aitw_llm_model: str | None = None
    aitw_model_role_classifier: str | None = None
    aitw_model_role_agent: str | None = None
    aitw_model_role_subagent: str | None = None
    aitw_model_role_synthesis: str | None = None

    # ---- Agent runtime: context-window budgeting (agent/context_window.py) ----
    aitw_context_window_tokens: int = 128_000
    aitw_context_reserve_tokens: int = 4_000
    aitw_agent_max_steps: int = 8

    # ---- Orchestrator: per-turn fan-out ceilings ----
    aitw_orch_max_subagents: int = 4  # hard ceiling — never exceeded regardless of classifier
    aitw_orch_max_steps_per_agent: int = 8  # per-lane ReAct-iteration cap
    aitw_orch_max_tool_calls_per_turn: int = 40  # total tool-call ceiling across all lanes
    aitw_orch_max_cost_micros_per_turn: int = 5_000_000  # per-turn cost ceiling (micro-units)
    aitw_orch_max_tokens_per_turn: int = 1_000_000  # per-turn token ceiling

    # ---- Data providers ----
    finmind_api_key: str | None = None
    finmind_base_url: str = "https://api.finmindtrade.com/api/v4"
    brave_search_api_key: str | None = None
    finnhub_key: str | None = None
    yf_tz_cache_dir: str = "./.cache/yf_tz"  # writable, persistent — the default location can
    # be read-only/ephemeral in a container; set explicitly once at adapter init

    # ---- OAuth providers (sign-in) — set only the ones in use ----
    # LINE Login credentials (LINE_CHANNEL_*) are the Login channel, distinct from the
    # Messaging API channel used for notifications; never conflate the two.
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None
    line_channel_id: str | None = None
    line_channel_secret: str | None = None
    line_redirect_uri: str | None = None

    # The web app's origin. `redirect_uri` (above) points the OAuth provider at *this backend's*
    # own /callback route (so the code-exchange leg's redirect_uri matches exactly, as the OAuth
    # spec requires) — after that route establishes the session, it 302s the browser here rather
    # than leaving it on a bare JSON response. No trailing slash.
    frontend_base_url: str = "http://localhost:3000"

    # ---- Auth ----
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 30

    # ---- Secret encryption (BYOK / channel tokens at rest) ----
    # urlsafe-base64 32-byte Fernet key(s). The first is the active (encrypting) key; any
    # extras are decrypt-only, for rotation. None → the secret store refuses to persist (fails
    # loud rather than storing a BYOK key in plaintext). Never logged.
    secret_encryption_keys: str | None = None

    # ---- Payments — Paddle (merchant of record; webhook is the entitlement source of truth) ----
    # `paddle_api_key`/`paddle_webhook_secret` are server-side secrets — never client-exposed,
    # never logged. `paddle_client_token` is the publishable Paddle.js token, safe to hand the
    # frontend. `paddle_environment` selects the sandbox vs production API host. The 8 price IDs
    # are the single {tier x interval x byok} price mapping — no ID is ever hardcoded in code.
    paddle_api_key: str | None = None
    paddle_webhook_secret: str | None = None
    paddle_client_token: str | None = None
    paddle_environment: str = "sandbox"  # sandbox | production
    paddle_price_premium_monthly: str | None = None
    paddle_price_premium_annual: str | None = None
    paddle_price_premium_byok_monthly: str | None = None
    paddle_price_premium_byok_annual: str | None = None
    paddle_price_pro_monthly: str | None = None
    paddle_price_pro_annual: str | None = None
    paddle_price_pro_byok_monthly: str | None = None
    paddle_price_pro_byok_annual: str | None = None

    # ---- Voice (STT/TTS) — provider selected by config, never hardcoded ----
    voice_stt_provider: str = "stub"  # stub | whisper_local | google | azure | deepgram
    voice_tts_provider: str = "stub"  # stub | whisper_local | google | azure
    voice_max_audio_seconds: int = 60

    # ---- Notification delivery channels (scheduler/alerts) ----
    # These are the OUTBOUND notification channels, entirely distinct from the OAuth sign-in
    # credentials above. In particular LINE_MESSAGING_* is the Messaging-API channel used to
    # push notifications and must never be confused with the LINE_CHANNEL_* Login channel:
    # different app, different credential, different purpose. A channel with no credential
    # configured degrades to a no-network stub (real request shape, no live call) so the
    # default test profile never touches an upstream.
    line_messaging_channel_access_token: str | None = None
    line_messaging_channel_secret: str | None = None
    telegram_bot_token: str | None = None
    telegram_webhook_secret: str | None = None
    email_smtp_host: str | None = None
    email_smtp_port: int = 587
    email_smtp_username: str | None = None
    email_smtp_password: str | None = None
    email_from_address: str | None = None
    apns_key_id: str | None = None
    apns_team_id: str | None = None
    apns_bundle_id: str | None = None
    apns_auth_key: str | None = None  # p8 private key contents
    fcm_project_id: str | None = None
    fcm_service_account_json: str | None = None

    def allowed_origins(self) -> list[str]:
        """The parsed CORS origin allow-list. Empty list means no CORS middleware is installed."""
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


def get_settings() -> Settings:
    """Construct Settings fresh from the current environment (no caching — tests override env)."""
    return Settings()
