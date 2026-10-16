"""The authorization server's behaviour: register/resolve clients, take a user's consent, issue and
redeem authorization codes, rotate refresh tokens, revoke grants.

It owns the rules and the storage; `api/routers/oauth.py` only maps HTTP onto it. The caller owns
the session and its transaction (same convention as the other stores), so every protocol failure
is raised as `OAuthProtocolError` AFTER any write that must survive it - a replayed code or
refresh token revokes its grant, and the route turns the error into a response without raising
through the unit-of-work boundary, so that revocation is committed.

Design notes
------------
* Public clients only (`token_endpoint_auth_method = none`): the secret that binds a code to the
  client that asked for it is the PKCE verifier, S256 only.
* Codes and refresh tokens are random, stored as SHA-256 hashes, single use. A code lives 60 s.
* A grant is created when a code is redeemed, not when consent is given, so a consent that is
  never redeemed leaves nothing for the user to revoke.
* Refresh tokens rotate on every use. Presenting one that was already rotated means two parties
  hold it: the whole grant is revoked.
* What a token may read is never decided here. The access token names the user; the tier is read
  from the user row on each MCP request.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, cast
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import structlog
from sqlalchemy import CursorResult, delete, exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.base import Executable

from accounts.mcp_oauth.rules import (
    RedirectUriError,
    is_public_cimd_url,
    is_valid_code_challenge,
    redirect_host,
    redirect_uri_matches,
    resolve_scope,
    same_resource,
    scope_is_subset,
    validate_redirect_uris,
    verify_pkce,
)
from accounts.mcp_tokens import (
    McpTokenError,
    issue_mcp_access_token,
    verify_mcp_access_token,
)
from config.settings import Settings
from dataaccess.oauth_client_metadata import ClientMetadataFetchError
from db.models import (
    OAuthAuthCodeRow,
    OAuthAuthorizationRequestRow,
    OAuthClientRow,
    OAuthGrantRow,
    OAuthRefreshTokenRow,
)

logger = structlog.get_logger(__name__)

MAX_STATE_LENGTH = 2048
PUBLIC_CLIENT_AUTH = "none"  # no client secret: PKCE binds a code to its client
MAX_CLIENT_NAME_LENGTH = 100
_STALE_DCR_DAYS = 30
_KEEP_SPENT_SECONDS = 86_400
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

ConsentFailure = Literal["not_found", "nonce_mismatch"]


class OAuthProtocolError(Exception):
    """An RFC 6749 / 7591 error: `error` is the registered code, `description` human readable."""

    def __init__(self, error: str, description: str, *, status: int = 400) -> None:
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description
        self.status = status


class AuthorizeRejectedError(Exception):
    """An authorize request that cannot proceed. `redirect_uri` is set only when it is safe to
    send the error back to the client (the client and redirect URI were verified); otherwise the
    user is shown the error and nothing is redirected (RFC 6749 section 4.1.2.1)."""

    def __init__(
        self, *, error: str, description: str, redirect_uri: str | None, state: str | None
    ) -> None:
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description
        self.redirect_uri = redirect_uri
        self.state = state


class ConsentError(Exception):
    def __init__(self, failure: ConsentFailure) -> None:
        super().__init__(failure)
        self.failure: ConsentFailure = failure


class ClientMetadataSource(Protocol):
    async def fetch(self, url: str) -> dict[str, object]: ...


@dataclass(frozen=True)
class ClientInfo:
    client_id: str
    name: str
    redirect_uris: tuple[str, ...]
    kind: Literal["dcr", "cimd"]


@dataclass(frozen=True)
class ConsentView:
    request_id: str
    client_name: str
    client_id: str
    client_kind: Literal["dcr", "cimd"]
    redirect_host: str
    scope: str
    resource: str
    nonce: str
    expires_at: datetime


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    expires_in: int
    refresh_token: str
    scope: str


def _now() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes for timezone-aware columns; they are UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def hash_secret(raw: str) -> str:
    """SHA-256 of a high-entropy random value: what the tables store instead of the value."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _rows_changed(result: object) -> int:
    assert isinstance(result, CursorResult)  # noqa: S101 - narrows the type; an UPDATE always is
    return int(result.rowcount)


def build_redirect(redirect_uri: str, **params: str | None) -> str:
    """`redirect_uri` with the given query parameters appended (existing ones kept)."""
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query.extend((k, v) for k, v in params.items() if v is not None)
    return urlunsplit(parts._replace(query=urlencode(query)))


def _clean_name(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = _CONTROL.sub(" ", value).strip()
    return cleaned[:MAX_CLIENT_NAME_LENGTH] or fallback


class AuthorizationServer:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        *,
        metadata_source: ClientMetadataSource | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._metadata_source = metadata_source

    async def _run(self, statement: Executable) -> object:
        """Run an UPDATE/DELETE. `fetch` keeps objects already loaded in the session in step with
        the database without evaluating the WHERE clause in Python (which cannot compare the naive
        datetimes SQLite returns with the aware ones the code uses)."""
        return await self._session.execute(
            statement, execution_options={"synchronize_session": "fetch"}
        )

    # ------------------------------------------------------------------ configuration

    @property
    def issuer(self) -> str:
        issuer = self._settings.oauth_issuer_url()
        if issuer is None:
            raise OAuthProtocolError(
                "server_error", "The authorization server is not configured.", status=503
            )
        return issuer

    @property
    def resource(self) -> str:
        resource = self._settings.mcp_resource_url()
        if resource is None:
            raise OAuthProtocolError(
                "server_error", "No MCP resource is configured for this server.", status=503
            )
        return resource

    # ------------------------------------------------------------------ clients

    async def register_client(
        self, *, client_name: str | None, redirect_uris: list[str]
    ) -> ClientInfo:
        """RFC 7591 dynamic registration of a public client."""
        try:
            checked = validate_redirect_uris(
                redirect_uris, extra_schemes=self._settings.oauth_redirect_schemes()
            )
        except RedirectUriError as exc:
            raise OAuthProtocolError("invalid_redirect_uri", str(exc)) from exc
        now = _now()
        await self._drop_stale_clients(now)
        count = await self._session.scalar(
            select(func.count()).select_from(OAuthClientRow).where(OAuthClientRow.kind == "dcr")
        )
        if (count or 0) >= self._settings.oauth_max_registered_clients:
            raise OAuthProtocolError(
                "temporarily_unavailable", "Client registration is full; try later.", status=503
            )
        row = OAuthClientRow(
            client_id=str(uuid.uuid4()),
            name=_clean_name(client_name, "MCP client"),
            redirect_uris=checked,
            kind="dcr",
            token_endpoint_auth_method=PUBLIC_CLIENT_AUTH,
            created_at=now,
            last_seen_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        logger.info("oauth_client_registered", client_id=row.client_id, kind="dcr")
        return self._info(row)

    async def _drop_stale_clients(self, now: datetime) -> None:
        """Clients (registered or metadata-document) that never got a grant and have not been
        seen for a month - what keeps an unauthenticated caller from growing the table."""
        has_grant = exists().where(OAuthGrantRow.client_id == OAuthClientRow.client_id)
        await self._run(
            delete(OAuthClientRow).where(
                OAuthClientRow.last_seen_at < now - timedelta(days=_STALE_DCR_DAYS),
                ~has_grant,
            )
        )

    async def resolve_client(self, client_id: str) -> ClientInfo:
        if client_id.startswith(("http://", "https://")):
            if not is_public_cimd_url(client_id):
                raise OAuthProtocolError(
                    "invalid_client", "client_id URL is not an acceptable metadata document URL."
                )
            return await self._cimd_client(client_id)
        row = await self._session.get(OAuthClientRow, client_id)
        if row is None or row.kind != "dcr":
            raise OAuthProtocolError("invalid_client", "Unknown client_id.", status=401)
        return self._info(row)

    @staticmethod
    def _info(row: OAuthClientRow) -> ClientInfo:
        kind: Literal["dcr", "cimd"] = "cimd" if row.kind == "cimd" else "dcr"
        return ClientInfo(
            client_id=row.client_id,
            name=row.name,
            redirect_uris=tuple(row.redirect_uris),
            kind=kind,
        )

    async def _cimd_client(self, url: str) -> ClientInfo:
        now = _now()
        row = await self._session.get(OAuthClientRow, url)
        ttl = timedelta(seconds=self._settings.oauth_cimd_cache_seconds)
        if (
            row is not None
            and row.kind == "cimd"
            and row.metadata_fetched_at is not None
            and now - _utc(row.metadata_fetched_at) < ttl
        ):
            return self._info(row)
        if self._metadata_source is None:
            raise OAuthProtocolError("invalid_client", "Client metadata documents are disabled.")
        try:
            document = await self._metadata_source.fetch(url)
        except ClientMetadataFetchError as exc:
            logger.warning("oauth_client_metadata_rejected", reason=exc.reason)
            raise OAuthProtocolError(
                "invalid_client", f"Could not fetch the client metadata document ({exc.reason})."
            ) from exc
        name, uris = self._check_document(url, document)
        if row is None:
            await self._drop_stale_clients(now)
            row = OAuthClientRow(
                client_id=url,
                name=name,
                redirect_uris=uris,
                kind="cimd",
                token_endpoint_auth_method=PUBLIC_CLIENT_AUTH,
                created_at=now,
                last_seen_at=now,
                metadata_fetched_at=now,
            )
            self._session.add(row)
        else:
            row.name, row.redirect_uris, row.metadata_fetched_at = name, uris, now
        await self._session.flush()
        return self._info(row)

    def _check_document(self, url: str, document: dict[str, object]) -> tuple[str, list[str]]:
        def bad(message: str) -> OAuthProtocolError:
            return OAuthProtocolError("invalid_client", f"Client metadata document: {message}.")

        if document.get("client_id") != url:
            raise bad("client_id does not equal the document URL")
        if "client_secret" in document or "client_secret_expires_at" in document:
            raise bad("a public client must not carry a client secret")
        method = document.get("token_endpoint_auth_method", "none")
        if method != "none":
            raise bad("token_endpoint_auth_method must be none")
        grant_types = document.get("grant_types")
        if grant_types is not None and (
            not isinstance(grant_types, list) or "authorization_code" not in grant_types
        ):
            raise bad("grant_types must include authorization_code")
        raw_uris = document.get("redirect_uris")
        if not isinstance(raw_uris, list):
            raise bad("redirect_uris must be a list of strings")
        listed = cast("list[object]", raw_uris)
        strings = [u for u in listed if isinstance(u, str)]
        if len(strings) != len(listed):
            raise bad("redirect_uris must be a list of strings")
        try:
            uris = validate_redirect_uris(
                strings, extra_schemes=self._settings.oauth_redirect_schemes()
            )
        except RedirectUriError as exc:
            raise bad(str(exc)) from exc
        host = urlsplit(url).hostname or "client"
        return _clean_name(document.get("client_name"), host), uris

    # ------------------------------------------------------------------ authorize + consent

    async def begin_authorization(  # noqa: PLR0913 - the authorize request's own parameters
        self,
        *,
        client_id: str,
        redirect_uri: str | None,
        response_type: str | None,
        code_challenge: str | None,
        code_challenge_method: str | None,
        resource: str | None,
        scope: str | None,
        state: str | None,
    ) -> str:
        """Validate an authorize request and store it; returns the request id for the consent
        screen. Raises `AuthorizeRejectedError`, which carries a redirect target only once the
        client and redirect URI are verified."""
        try:
            client = await self.resolve_client(client_id)
        except OAuthProtocolError as exc:
            raise AuthorizeRejectedError(
                error=exc.error, description=exc.description, redirect_uri=None, state=None
            ) from exc
        if redirect_uri is None and len(client.redirect_uris) == 1:
            redirect_uri = client.redirect_uris[0]
        if not redirect_uri or not redirect_uri_matches(client.redirect_uris, redirect_uri):
            raise AuthorizeRejectedError(
                error="invalid_request",
                description="redirect_uri does not match a registered redirect URI.",
                redirect_uri=None,
                state=None,
            )
        target = redirect_uri

        def reject(error: str, description: str) -> AuthorizeRejectedError:
            return AuthorizeRejectedError(
                error=error, description=description, redirect_uri=target, state=state
            )

        if state is not None and len(state) > MAX_STATE_LENGTH:
            raise reject("invalid_request", "state is too long.")
        if response_type != "code":
            raise reject("unsupported_response_type", "Only response_type=code is supported.")
        if code_challenge is None or not is_valid_code_challenge(code_challenge):
            raise reject("invalid_request", "A valid PKCE code_challenge is required.")
        if code_challenge_method != "S256":
            raise reject("invalid_request", "code_challenge_method must be S256.")
        canonical = self.resource
        if resource is not None and not same_resource(resource, canonical):
            raise reject("invalid_target", "resource is not this server's MCP endpoint.")

        now = _now()
        await self._purge_expired(now)
        client_row = await self._session.get(OAuthClientRow, client.client_id)
        if client_row is not None:
            client_row.last_seen_at = now
        request = OAuthAuthorizationRequestRow(
            id=str(uuid.uuid4()),
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            resource=canonical,
            scope=resolve_scope(scope),
            nonce=secrets.token_urlsafe(24),
            created_at=now,
            expires_at=now
            + timedelta(seconds=self._settings.oauth_authorization_request_ttl_seconds),
        )
        self._session.add(request)
        await self._session.flush()
        return request.id

    async def _purge_expired(self, now: datetime) -> None:
        spent_before = now - timedelta(seconds=_KEEP_SPENT_SECONDS)
        await self._run(
            delete(OAuthAuthorizationRequestRow).where(
                OAuthAuthorizationRequestRow.expires_at < now
            )
        )
        await self._run(delete(OAuthAuthCodeRow).where(OAuthAuthCodeRow.expires_at < spent_before))
        await self._run(
            delete(OAuthRefreshTokenRow).where(OAuthRefreshTokenRow.expires_at < spent_before)
        )

    async def _live_request(self, request_id: str) -> OAuthAuthorizationRequestRow:
        row = await self._session.get(OAuthAuthorizationRequestRow, request_id)
        if row is None or _utc(row.expires_at) <= _now():
            raise ConsentError("not_found")
        return row

    async def consent_view(self, request_id: str) -> ConsentView:
        row = await self._live_request(request_id)
        client = await self._session.get(OAuthClientRow, row.client_id)
        if client is None:  # pragma: no cover - the FK cascade removes the request with its client
            raise ConsentError("not_found")
        info = self._info(client)
        return ConsentView(
            request_id=row.id,
            client_name=info.name,
            client_id=info.client_id,
            client_kind=info.kind,
            redirect_host=redirect_host(info.redirect_uris),
            scope=row.scope,
            resource=row.resource,
            nonce=row.nonce,
            expires_at=_utc(row.expires_at),
        )

    async def decide(self, *, request_id: str, user_id: str, approve: bool, nonce: str) -> str:
        """Record the user's decision and return the URL to send the browser to.

        The nonce proves the decision comes from the page that showed this request to the
        signed-in user (cross-site forms and scripts cannot read it). The request is consumed
        either way, so a decision can be taken once."""
        row = await self._live_request(request_id)
        if not hmac.compare_digest(row.nonce.encode(), nonce.encode()):
            raise ConsentError("nonce_mismatch")
        removed = await self._run(
            delete(OAuthAuthorizationRequestRow).where(OAuthAuthorizationRequestRow.id == row.id)
        )
        if _rows_changed(removed) != 1:
            raise ConsentError("not_found")
        issuer = self.issuer
        if not approve:
            logger.info("oauth_consent_denied", client_id=row.client_id)
            return build_redirect(
                row.redirect_uri,
                error="access_denied",
                error_description="The user denied the request.",
                state=row.state,
                iss=issuer,
            )
        raw_code = secrets.token_urlsafe(32)
        now = _now()
        self._session.add(
            OAuthAuthCodeRow(
                code_hash=hash_secret(raw_code),
                user_id=user_id,
                client_id=row.client_id,
                redirect_uri=row.redirect_uri,
                code_challenge=row.code_challenge,
                resource=row.resource,
                scope=row.scope,
                created_at=now,
                expires_at=now + timedelta(seconds=self._settings.oauth_code_ttl_seconds),
            )
        )
        await self._session.flush()
        logger.info("oauth_consent_approved", client_id=row.client_id, user_id=user_id)
        return build_redirect(row.redirect_uri, code=raw_code, state=row.state, iss=issuer)

    # ------------------------------------------------------------------ token endpoint

    async def exchange_code(
        self,
        *,
        client_id: str,
        code: str,
        redirect_uri: str | None,
        code_verifier: str | None,
        resource: str | None,
    ) -> IssuedTokens:
        row = await self._session.get(OAuthAuthCodeRow, hash_secret(code))
        invalid = OAuthProtocolError("invalid_grant", "The authorization code is not valid.")
        if row is None:
            raise invalid
        if row.used_at is not None:
            await self._revoke_grant(row.grant_id)
            logger.warning("oauth_code_replayed", client_id=row.client_id)
            raise invalid
        if (
            _utc(row.expires_at) <= _now()
            or row.client_id != client_id
            or redirect_uri != row.redirect_uri
        ):
            raise invalid
        if resource is not None and not same_resource(resource, row.resource):
            raise OAuthProtocolError("invalid_target", "resource does not match the grant.")
        if not code_verifier or not verify_pkce(
            verifier=code_verifier, challenge=row.code_challenge
        ):
            raise OAuthProtocolError("invalid_grant", "PKCE verification failed.")
        now = _now()
        claimed = await self._run(
            update(OAuthAuthCodeRow)
            .where(OAuthAuthCodeRow.code_hash == row.code_hash, OAuthAuthCodeRow.used_at.is_(None))
            .values(used_at=now)
        )
        if _rows_changed(claimed) != 1:  # a concurrent redemption won: this is a replay
            await self._session.refresh(row)
            await self._revoke_grant(row.grant_id)
            raise invalid
        grant = OAuthGrantRow(
            id=str(uuid.uuid4()),
            user_id=row.user_id,
            client_id=row.client_id,
            scope=row.scope,
            resource=row.resource,
            created_at=now,
            last_used_at=now,
        )
        self._session.add(grant)
        await self._session.flush()
        await self._run(
            update(OAuthAuthCodeRow)
            .where(OAuthAuthCodeRow.code_hash == row.code_hash)
            .values(grant_id=grant.id)
        )
        logger.info("oauth_grant_created", client_id=grant.client_id, user_id=grant.user_id)
        return await self._issue(grant, rotated_from=None)

    async def refresh(
        self,
        *,
        client_id: str,
        refresh_token: str,
        scope: str | None,
        resource: str | None,
    ) -> IssuedTokens:
        row = await self._session.scalar(
            select(OAuthRefreshTokenRow).where(
                OAuthRefreshTokenRow.token_hash == hash_secret(refresh_token)
            )
        )
        invalid = OAuthProtocolError("invalid_grant", "The refresh token is not valid.")
        if row is None:
            raise invalid
        grant = await self._session.get(OAuthGrantRow, row.grant_id)
        if grant is None or grant.revoked_at is not None or grant.client_id != client_id:
            raise invalid
        if row.rotated_at is not None:
            await self._revoke_grant(grant.id)
            logger.warning(
                "oauth_refresh_replayed", client_id=grant.client_id, user_id=grant.user_id
            )
            raise OAuthProtocolError(
                "invalid_grant", "The refresh token was already used; the grant is revoked."
            )
        if _utc(row.expires_at) <= _now():
            raise invalid
        granted = grant.scope
        if scope:
            if not scope_is_subset(scope, grant.scope):
                raise OAuthProtocolError("invalid_scope", "Requested scope exceeds the grant.")
            granted = scope
        if resource is not None and not same_resource(resource, grant.resource):
            raise OAuthProtocolError("invalid_target", "resource does not match the grant.")
        claimed = await self._run(
            update(OAuthRefreshTokenRow)
            .where(OAuthRefreshTokenRow.id == row.id, OAuthRefreshTokenRow.rotated_at.is_(None))
            .values(rotated_at=_now())
        )
        if _rows_changed(claimed) != 1:  # raced another use of the same token: treat as replay
            await self._revoke_grant(grant.id)
            raise invalid
        grant.last_used_at = _now()
        return await self._issue(grant, rotated_from=row.id, scope=granted)

    async def _issue(
        self, grant: OAuthGrantRow, *, rotated_from: str | None, scope: str | None = None
    ) -> IssuedTokens:
        granted = scope or grant.scope
        access, expires_in = issue_mcp_access_token(
            user_id=grant.user_id,
            client_id=grant.client_id,
            grant_id=grant.id,
            scope=granted,
            issuer=self.issuer,
            resource=grant.resource,
            settings=self._settings,
        )
        raw_refresh = secrets.token_urlsafe(48)
        now = _now()
        self._session.add(
            OAuthRefreshTokenRow(
                id=str(uuid.uuid4()),
                user_id=grant.user_id,
                grant_id=grant.id,
                token_hash=hash_secret(raw_refresh),
                rotated_from=rotated_from,
                created_at=now,
                expires_at=now + timedelta(days=self._settings.oauth_refresh_token_days),
            )
        )
        await self._session.flush()
        return IssuedTokens(
            access_token=access, expires_in=expires_in, refresh_token=raw_refresh, scope=granted
        )

    # ------------------------------------------------------------------ revocation + grants

    async def _revoke_grant(self, grant_id: str | None) -> None:
        if grant_id is None:
            return
        await self._run(
            update(OAuthGrantRow)
            .where(OAuthGrantRow.id == grant_id, OAuthGrantRow.revoked_at.is_(None))
            .values(revoked_at=_now())
        )
        await self._run(
            delete(OAuthRefreshTokenRow).where(OAuthRefreshTokenRow.grant_id == grant_id)
        )

    async def revoke_token(self, *, token: str, client_id: str | None) -> None:
        """RFC 7009: revoke the grant a refresh token or an access token belongs to. A token that
        is unknown, or belongs to another client, is ignored - the answer is the same."""
        refresh_row = await self._session.scalar(
            select(OAuthRefreshTokenRow).where(
                OAuthRefreshTokenRow.token_hash == hash_secret(token)
            )
        )
        grant_id: str | None = refresh_row.grant_id if refresh_row is not None else None
        if grant_id is None:
            try:
                claims = verify_mcp_access_token(
                    token, settings=self._settings, issuer=self.issuer, resource=self.resource
                )
            except McpTokenError:
                return
            grant_id = claims.grant_id
        grant = await self._session.get(OAuthGrantRow, grant_id)
        if grant is None or (client_id is not None and grant.client_id != client_id):
            return
        await self._revoke_grant(grant.id)
        logger.info("oauth_grant_revoked", grant_id=grant.id, by="token_revocation")


async def grant_is_active(session: AsyncSession, *, grant_id: str, user_id: str) -> bool:
    """Whether the grant behind an access token still stands (not revoked, same user)."""
    row = await session.scalar(
        select(OAuthGrantRow.id).where(
            OAuthGrantRow.id == grant_id,
            OAuthGrantRow.user_id == user_id,
            OAuthGrantRow.revoked_at.is_(None),
        )
    )
    return row is not None


__all__ = [
    "MAX_CLIENT_NAME_LENGTH",
    "AuthorizationServer",
    "AuthorizeRejectedError",
    "ClientInfo",
    "ClientMetadataSource",
    "ConsentError",
    "ConsentView",
    "IssuedTokens",
    "OAuthProtocolError",
    "build_redirect",
    "grant_is_active",
    "hash_secret",
]
