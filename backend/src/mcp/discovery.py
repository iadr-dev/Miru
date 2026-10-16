"""OAuth 2.0 Protected Resource Metadata (RFC 9728) for the MCP endpoint.

The MCP authorization spec makes the MCP server an OAuth *resource server*: it tells clients
where its authorization server is via this document (and via `WWW-Authenticate` on a 401). Miru
does not ship an authorization server, so the document is served only when both the public base
URL and an authorization-server URL are configured; otherwise the paths answer 404 and the
server is authless / Bearer-token only.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.settings import Settings

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
            "bearer_methods_supported": ["header"],
            "resource_name": "Miru MCP",
            "resource_documentation": "https://modelcontextprotocol.io/specification/",
        }


def resource_metadata_from_settings(settings: Settings, mcp_path: str) -> ResourceMetadata | None:
    base = settings.mcp_public_base()
    authorization_server = settings.mcp_authorization_server_url.strip().rstrip("/")
    if not base or not authorization_server:
        return None
    return ResourceMetadata(
        resource=f"{base}{mcp_path}",
        authorization_server=authorization_server,
        metadata_url=f"{base}{WELL_KNOWN_PREFIX}{mcp_path}",
    )


__all__ = ["WELL_KNOWN_PREFIX", "ResourceMetadata", "resource_metadata_from_settings"]
