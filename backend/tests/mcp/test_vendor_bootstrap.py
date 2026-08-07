"""Proves the `mcp._vendor` bootstrap resolves the name collision between this package (also
named `mcp`) and the PyPI SDK `fastmcp` depends on (also named `mcp`) — without it, `fastmcp`
cannot load under this project's own import setup."""

from __future__ import annotations


def test_bootstrap_lets_fastmcp_fully_initialize() -> None:
    from mcp._vendor import bootstrap_vendor_sdk  # noqa: PLC0415 — ordering under test

    bootstrap_vendor_sdk()

    from fastmcp import FastMCP  # noqa: PLC0415 — must run after the bootstrap call above

    server = FastMCP("bootstrap-test")
    assert server is not None


def test_this_packages_own_mcp_name_is_restored_after_bootstrap() -> None:
    from mcp._vendor import bootstrap_vendor_sdk  # noqa: PLC0415 — ordering under test

    bootstrap_vendor_sdk()

    import mcp  # noqa: PLC0415 — must run after the bootstrap call above

    assert "site-packages" not in mcp.__file__


def test_bootstrap_is_idempotent() -> None:
    from mcp._vendor import bootstrap_vendor_sdk  # noqa: PLC0415 — ordering under test

    bootstrap_vendor_sdk()
    bootstrap_vendor_sdk()  # must not raise or re-do the swap

    import mcp  # noqa: PLC0415 — must run after the bootstrap calls above

    assert "site-packages" not in mcp.__file__
