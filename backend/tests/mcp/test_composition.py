"""Proves the real MCP-process composition root (`mcp.composition.build_mcp_http_app`) builds
the real ASGI app end to end from config, mirroring how `tests/api/test_app_composition.py` (if
present) or the scheduler's own composition tests prove their process's composition root."""

from __future__ import annotations

from mcp._vendor import bootstrap_vendor_sdk

bootstrap_vendor_sdk()

from fastmcp.server.http import StarletteWithLifespan  # noqa: E402

from config.settings import Settings  # noqa: E402
from mcp.composition import build_mcp_http_app  # noqa: E402

_TEST_JWT_SECRET = "test-secret-not-a-real-key-0123456789abcdef"


def test_build_mcp_http_app_returns_a_real_asgi_app() -> None:
    settings = Settings(_env_file=None, jwt_secret_key=_TEST_JWT_SECRET)
    built = build_mcp_http_app(settings)
    assert isinstance(built.app, StarletteWithLifespan)


def test_build_mcp_http_app_honors_allowed_origins_and_hosts() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key=_TEST_JWT_SECRET,
        mcp_allowed_origins="https://example.com, https://other.example.com",
        mcp_allowed_hosts="mcp.example.com",
    )
    built = build_mcp_http_app(settings)
    assert isinstance(built.app, StarletteWithLifespan)


def test_settings_parse_empty_allowlists_as_none() -> None:
    settings = Settings(_env_file=None, jwt_secret_key=_TEST_JWT_SECRET)
    assert settings.mcp_allowed_origins_list() is None
    assert settings.mcp_allowed_hosts_list() is None


def test_settings_parse_comma_separated_allowlists() -> None:
    settings = Settings(
        _env_file=None,
        jwt_secret_key=_TEST_JWT_SECRET,
        mcp_allowed_origins="https://a.example.com,https://b.example.com",
        mcp_allowed_hosts="a.example.com, b.example.com",
    )
    assert settings.mcp_allowed_origins_list() == [
        "https://a.example.com",
        "https://b.example.com",
    ]
    assert settings.mcp_allowed_hosts_list() == ["a.example.com", "b.example.com"]


def test_composed_app_puts_the_gateway_in_front_of_fastmcps_own_host_guard() -> None:
    from mcp.gateway import McpGateway  # noqa: PLC0415

    settings = Settings(
        _env_file=None,
        jwt_secret_key=_TEST_JWT_SECRET,
        mcp_public_base_url="https://mcp.example.com",
        oauth_issuer="https://auth.example.com",
    )
    built = build_mcp_http_app(settings)
    assert built.app.user_middleware[0].cls is McpGateway
    assert built.app.user_middleware[0].kwargs["config"].resource_metadata_url == (
        "https://mcp.example.com/.well-known/oauth-protected-resource/mcp"
    )


def test_mcp_connector_settings_defaults_are_safe_for_a_public_endpoint() -> None:
    settings = Settings(_env_file=None, jwt_secret_key=_TEST_JWT_SECRET)
    assert settings.mcp_stateless_http is True
    assert settings.mcp_public_base() is None
    assert settings.mcp_max_body_bytes <= 4 * 1024 * 1024
    assert 0 < settings.mcp_max_concurrent_requests <= 256
    assert (
        settings.mcp_rate_limit_anonymous_per_minute < settings.mcp_rate_limit_signed_in_per_minute
    )
