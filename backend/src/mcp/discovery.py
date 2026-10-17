"""OAuth 2.0 Protected Resource Metadata (RFC 9728) for the MCP endpoint.

The MCP authorization spec makes the MCP server an OAuth *resource server*: it tells clients
where its authorization server is via this document (and via `WWW-Authenticate` on a 401). The
authorization server is the API service of this same app (`OAUTH_ISSUER`); the document is served
once both that and the public base URL of this server (`MCP_PUBLIC_BASE_URL`, which fixes the
`resource` clients bind tokens to) are configured, and answers 404 otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from accounts.mcp_tokens import SUPPORTED_SCOPES
from config.settings import MCP_RESOURCE_PATH, Settings

WELL_KNOWN_PREFIX = "/.well-known/oauth-protected-resource"


@dataclass(frozen=True)
class ResourceMetadata:
    resource: str  # canonical URI of the MCP endpoint, e.g. https://mcp.example.com/mcp
    authorization_server: str
    metadata_url: str  # RFC 9728 well-known URL for `resource` (path-inserted form)

    def document(self) -> dict[str, object]:
        return {
            "resource": self.resource,
            "authorization_servers": [self.authorization_server],
            "scopes_supported": list(SUPPORTED_SCOPES),
            "bearer_methods_supported": ["header"],
            "resource_name": "Miru MCP",
            "resource_documentation": "https://modelcontextprotocol.io/specification/",
        }


def resource_metadata_from_settings(settings: Settings, mcp_path: str) -> ResourceMetadata | None:
    base = settings.mcp_public_base()
    authorization_server = settings.oauth_issuer_url()
    if not base or not authorization_server:
        return None
    if mcp_path != MCP_RESOURCE_PATH:  # pragma: no cover - the two must stay one value
        raise ValueError("the MCP path and Settings.mcp_resource_url() disagree")
    return ResourceMetadata(
        resource=f"{base}{mcp_path}",
        authorization_server=authorization_server,
        metadata_url=f"{base}{WELL_KNOWN_PREFIX}{mcp_path}",
    )


__all__ = ["WELL_KNOWN_PREFIX", "ResourceMetadata", "resource_metadata_from_settings"]
