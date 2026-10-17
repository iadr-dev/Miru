"""The seam through which the authorization server (accounts/, api/) downloads a Client ID Metadata
Document. `sources/` owns the network call and its SSRF fence; callers above `dataaccess/` may not
import `sources/`, so they import this module instead."""

from __future__ import annotations

from config.settings import Settings
from sources.oauth_client_metadata import (
    ClientMetadataFetcher,
    ClientMetadataFetchError,
    FetchFailure,
)


def build_client_metadata_fetcher(settings: Settings) -> ClientMetadataFetcher:
    return ClientMetadataFetcher(
        timeout_seconds=settings.oauth_cimd_timeout_seconds,
        max_bytes=settings.oauth_cimd_max_bytes,
        tls_policy=settings.tls_policy,
    )


__all__ = [
    "ClientMetadataFetchError",
    "ClientMetadataFetcher",
    "FetchFailure",
    "build_client_metadata_fetcher",
]
