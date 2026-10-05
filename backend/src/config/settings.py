from __future__ import annotations

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemas.enums import TLSPolicy


class Settings(BaseSettings):
    """Runtime config from env (backend/.env — see .env.example for the authoritative list).

    Values only — no secrets hardcoded (backend/CLAUDE.md §3 "no hardcoded provider/model/
    source"). `.env` is gitignored; never read its values aloud, echo, or log (root CLAUDE.md).
    """

    model_config = SettingsConfigDict(env_prefix="", extra="ignore", env_file=".env")

    # ---- Core app config ----
    env: str = "development"  # development | production
    ingest_on_startup: bool = False  # MUST be false in production (backend/.env.example)
    dev_backfill_years: int = 3
    webhook_base_url: str = "http://localhost:8000"
    frontend_base_url: str = "http://localhost:3000"

    # CORS (api/composition.py): comma-separated origin list; empty = no CORS middleware.
    cors_allowed_origins: str = ""

    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]

    # ---- Datastores ----
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    duckdb_path: str = "./data/duckdb"
    parquet_root: str = "./data/parquet"

    # ---- TLS policy (M6D, governance.md §5) ----
    tls_policy: TLSPolicy = TLSPolicy.STRICT

    # ---- LLM providers (modules/04) — set only the ones in use ----
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    groq_api_key: str | None = None
    cerebras_api_key: str | None = None
    openrouter_api_key: str | None = None
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.6"

    # ---- LLM routing / agent loop (modules/04, orchestrator) ----
    llm_provider: str | None = None  # optional override; falls back to registry default
    llm_model: str | None = None  # optional override; falls back to registry default
    model_role_classifier: str | None = None
    model_role_agent: str | None = None
    model_role_subagent: str | None = None
    model_role_synthesis: str | None = None
    context_window_tokens: int = 128_000
    context_reserve_tokens: int = 4_000
    agent_max_steps: int = 8
    # Per-call output-token ceiling passed to every LLMClient's `max_tokens`/`max_output_tokens`
    # constructor kwarg. Each provider client's own default (4096) is a conservative fallback for
    # a bare `AnthropicClient(...)`/`OpenAIClient(...)` construction outside this registry, not a
    # tuned production value: with extended/adaptive thinking counting against the same budget
    # (Anthropic), 4096 was observed live truncating a long synthesis (multi-section prose + a
    # per-month table) mid-sentence before any of the caller's actual answer landed.
    llm_max_output_tokens: int = 16_000

    # ---- Orchestrator budgets (per-turn ceilings, orchestrator/ceilings.py) ----
    # `orch_max_subagents` bounds fan-out breadth (how many independent-dimension skill lanes
    # can run in one turn), not per-lane depth (`orch_max_steps_per_agent`, unchanged). Raised
    # from 4 to 6 so a genuinely multi-angle query isn't clamped below what the classifier's own
    # router prompt asks for; the tool-call/cost/token ceilings below are scaled by the same
    # ~1.5x factor so a 6-lane fan-out has proportionally more headroom, not the old 4-lane budget
    # stretched thinner across more lanes.
    orch_max_subagents: int = 6
    orch_max_steps_per_agent: int = 8
    orch_max_tool_calls_per_turn: int = 60
    orch_max_cost_micros_per_turn: int = 7_500_000
    orch_max_tokens_per_turn: int = 1_500_000

    # ---- Data providers ----
    finmind_api_key: str | None = None
    finmind_base_url: str = "https://api.finmindtrade.com/api/v4"
    brave_search_api_key: str | None = None
    finnhub_key: str | None = None
    yf_tz_cache_dir: str = "./.cache/yf_tz"

    # ---- Voice (modules/, api/routers/voice.py) ----
    voice_stt_provider: str = "stub"
    voice_tts_provider: str = "stub"
    voice_max_audio_seconds: int = 60

    # ---- Auth ----
    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 30
    secret_encryption_keys: str | None = None  # comma-separated Fernet keys (accounts/crypto.py)

    # ---- OAuth sign-in (modules/12, adr/0022) ----
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None
    line_channel_id: str | None = None
    line_channel_secret: str | None = None
    line_redirect_uri: str | None = None
    # Log in with Telegram (OpenID Connect). Register the redirect URI under the bot's "Login
    # Widget" in @BotFather; it shows the client id (the bot's numeric id — derived from
    # TELEGRAM_BOT_TOKEN when unset) and the client secret.
    telegram_login_client_id: str | None = None
    telegram_login_client_secret: str | None = None
    telegram_login_redirect_uri: str | None = None

    # ---- Notification delivery channels (modules/14) — distinct from LINE Login above ----
    line_messaging_channel_access_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "line_messaging_channel_access_token", "line_messaging_access_token"
        ),
    )
    # The official account's basic id (e.g. "@123abcde"), used to build the add-friend link.
    line_bot_basic_id: str | None = None
    line_messaging_channel_secret: str | None = None
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None
    telegram_webhook_secret: str | None = None
    # Email goes out through Resend's HTTP API. The from-address must be on a domain verified in
    # Resend (SPF/DKIM), e.g. "Miru <alerts@your-domain.com>".
    resend_api_key: str | None = None
    # Signing secret (`whsec_…`) Resend shows when the webhook is added in its dashboard; without it
    # the bounce/complaint webhook refuses every request.
    resend_webhook_secret: str | None = None
    email_from_address: str | None = None
    # Browser notifications (Web Push, VAPID). The public key is handed to the browser so it can
    # subscribe; the private key signs each push and never leaves the server.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:noreply@miru.app"

    # ---- Paddle / billing (modules/15) — MoR; webhook is entitlement source of truth ----
    paddle_api_key: str | None = None
    paddle_client_token: str | None = None
    paddle_environment: str = "sandbox"  # sandbox | production
    paddle_webhook_secret: str | None = None
    # 8 price IDs = {premium,pro} x {monthly,annual} x {standard,BYOK} — payments/plans.py
    paddle_price_premium_monthly: str | None = None
    paddle_price_premium_annual: str | None = None
    paddle_price_premium_byok_monthly: str | None = None
    paddle_price_premium_byok_annual: str | None = None
    paddle_price_pro_monthly: str | None = None
    paddle_price_pro_annual: str | None = None
    paddle_price_pro_byok_monthly: str | None = None
    paddle_price_pro_byok_annual: str | None = None

    # ---- MCP process (mcp/__main__.py, mcp/composition.py) ----
    mcp_host: str = "0.0.0.0"  # noqa: S104 — container default; bind narrower via env in prod
    mcp_port: int = 8100
    # Comma-separated allow-lists; empty = no allow-list installed (dev-permissive default).
    mcp_allowed_origins: str = ""
    mcp_allowed_hosts: str = ""

    def mcp_allowed_origins_list(self) -> list[str] | None:
        origins = [o.strip() for o in self.mcp_allowed_origins.split(",") if o.strip()]
        return origins or None

    def mcp_allowed_hosts_list(self) -> list[str] | None:
        hosts = [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]
        return hosts or None

    # ---- Redis / rate-limiting / caching / event bus ----
    event_bus_backend: str = "memory"  # memory | redis
    rate_limiter_backend: str = "memory"  # memory | redis
    redis_url: str = "redis://localhost:6379/0"
    rate_limit_key_prefix: str = "ratelimit"
    data_updated_key_prefix: str = "data.updated"
    # "module:callable" the ingest process resolves to build its real `data.updated` publisher.
    # Named rather than imported because `ingest/` sits below `events/` and may not import it;
    # empty disables binding (ingest keeps the no-op). See `ingest.publisher_binding`.
    ingest_data_update_publisher_factory: str = "events.ingest_wiring:build_data_update_publisher"

    # FinMind sponsor-pool quota (usage/finmind_pool.py)
    finmind_pool_key_prefix: str = "finmind:pool"
    finmind_pool_limit_per_window: int = 6000
    finmind_pool_window_seconds: int = 3600
    finmind_reserve_per_lane: int = 1

    # Live-quote streaming (events/live_quote_factory.py)
    quote_poll_cadence_normal_seconds: float = 5.0
    quote_poll_cadence_saver_seconds: float = 20.0
    quote_stream_max_symbols_per_connection: int = 50
    quote_stream_max_demanded_symbols: int = 500
    quote_channel_key_prefix: str = "quote"
    quote_demand_key_prefix: str = "demand"

    # Market-data read-through cache (sources/caching_adapter.py, dataaccess/composition.py)
    market_data_cache_backend: str = "none"  # none | redis
    market_data_cache_key_prefix: str = "mdcache"
    market_data_cache_ttl_reference_seconds: int = 86_400
    market_data_cache_ttl_live_ish_seconds: int = 15
    market_data_cache_ttl_official_eod_seconds: int = 3_600
    market_data_cache_ttl_event_driven_seconds: int = 300
    # A failed/unreachable SourceResult gets this short TTL regardless of timing_class, so a
    # transient upstream blip self-heals on the next request instead of freezing a card empty
    # for hours (the timing_class TTL is only appropriate for a genuinely successful fetch).
    market_data_cache_ttl_failure_seconds: int = 30

    # ---- Scheduler / ingest ----
    scheduler_poll_interval_seconds: float = 60.0
    ingest_poll_interval_seconds: float = 60.0

    # ---- Attachments (api/attachments.py) ----
    max_attachment_size_bytes: int = 10_000_000


def get_settings() -> Settings:
    """Construct Settings fresh from the current environment (no caching — tests override env)."""
    return Settings()
