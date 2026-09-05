import asyncio
import hashlib
import ipaddress
import os
import re
import secrets
import threading
import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response
from passlib.hash import bcrypt
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_
from sqlmodel import Session, select

from pixlstash.database import DBPriority, VaultDatabase
from pixlstash.db_models import Character, PictureSet, Project, User, UserToken
from pixlstash.server_config_io import persist_server_config
from pixlstash.utils.system_utils import default_max_vram_gb


class LoginRequest(BaseModel):
    username: Optional[str] = Field(
        default=None,
        min_length=1,
        description="Username is required",
    )
    password: Optional[str] = Field(
        default=None,
        min_length=8,
        description="Password must be at least 8 characters long",
    )
    token: Optional[str] = Field(
        default=None,
        description="API token for authentication",
    )

    @field_validator("username", "password", mode="before")
    @classmethod
    def strip_whitespace(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v


# Paths and prefixes that bypass authentication - also used by rate limiting.
AUTH_EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "/login",
        "/docs",
        "/scalar",
        "/openapi.json",
        "/docs/oauth2-redirect",
        "/favicon.ico",
        "/",
        "/version",
        "/check-session",
        "/logout",
        "/Logo.png",
        "/Empty.png",
        "/EmptyTrash.png",
    }
)
AUTH_EXCLUDED_PREFIXES: tuple[str, ...] = (
    "/assets/",
    "/scalar-assets/",
    "/share/",
    "/docs/",
)
AUTH_API_PREFIXES: tuple[str, ...] = ("/api/v1",)
# Credential-bearing paths that must share the full-restore admission barrier.
# Public share links bypass normal API authentication because the credential is
# embedded in the URL, but their token and resource lookups still read the live
# database and therefore cannot cross a whole-file restore cutover.
RESTORE_ADMISSION_PREFIXES: tuple[str, ...] = (*AUTH_API_PREFIXES, "/share/")

# Token scopes that permit a mutating request. The middleware fails **closed**
# against this set: any other scope - including one nobody recognises - is
# treated as read-only, so a scope value is admitted by declaration rather than
# by omission. ``ALL`` is absent on purpose: ``request.state.token_scope`` is
# populated only for non-ALL scopes, so an owner token never reaches the check.
# ``WRITE`` has no mint path (``create_token`` allowlists ``ALL``/``READ``); it
# is named here so the shape the ``*_SCOPED`` route policies exist for keeps its
# write-ness deliberately rather than by the old ``!= "READ"`` accident.
WRITE_ENABLED_SCOPES: frozenset[str] = frozenset({"WRITE"})

# POST paths that are semantically read-only (large request bodies preclude GET).
# These are exempted from the "block non-GET for READ tokens" check.
READ_SAFE_POST_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/pictures/thumbnails",
        # Bulk tag lookup - POST only because it carries an id list; the handler
        # is a pure SELECT and scope-filters its ids (see tags.bulk_fetch_tags).
        "/api/v1/pictures/tags/bulk_fetch",
        "/api/v1/pictures/guest-scores",
        "/api/v1/pictures/guest-scores/session",
        # Membership lookup endpoints use POST for body size but are semantically read-only.
        "/api/v1/picture_sets/membership",
        "/api/v1/projects/membership",
        "/api/v1/characters/membership",
        # Reverse image search - POST only because it accepts a file upload.
        "/api/v1/pictures/likeness-search",
        "/api/v1/pictures/face-search",
        "/api/v1/characters/likeness-search",
    }
)

# GET paths that must not be accessible to READ-scoped tokens.
# Covers sensitive user settings and all folder/filesystem endpoints - READ tokens
# are allowed to access content (pictures, picture_sets, characters, projects)
# but must never expose server filesystem or import-folder configuration.
READ_BLOCKED_GET_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/users/me/config",
        "/api/v1/server-config/watch-folders",
        "/api/v1/server-config/filesystem-roots",
        "/api/v1/filesystem/browse",
        # Walks the server filesystem from a client-supplied path to detect
        # sidecar naming. A folder/filesystem endpoint, so READ tokens (handed
        # out to view a shared gallery) must not reach it.
        "/api/v1/reference-folders/detect-sidecars",
        # Names the tagger-plugin folder on the server's disk, and returns
        # plugin import errors whose text can carry any path the failing plugin
        # reached for. The authz gate declares it LOCAL_OWNER_ONLY; it is here
        # as well because that is the pattern filesystem/browse already sets,
        # and because AUTHZ_GATE_ENFORCING is a documented one-line rollback -
        # without this entry, taking it would hand the folder straight back to
        # every share token.
        "/api/v1/taggers/plugin-diagnostics",
        # The plugin list carries the caller's own tagger_settings, which is
        # user settings exactly like /users/me/config above - a plugin may
        # declare a free-text parameter and the owner may have typed a path
        # into it. Gate policy is OWNER_ONLY; same belt-and-braces reasoning.
        "/api/v1/taggers",
        # Found by the derivation in tests/test_authz_host_capability_16_3.py
        # ::test_every_untemplated_locality_get_is_on_the_read_blocked_belt,
        # not by anyone reading this list: it is LOCAL_OWNER_ONLY at the gate
        # and serves the move queue's source and destination folders, so with
        # the gate rolled back a READ token read host paths straight out of it.
        # Its two templated siblings on the same tier cannot be expressed here
        # at all - this frozenset matches literal paths - and stay the recorded
        # follow-up.
        "/api/v1/model-moves",
        # Walks the server filesystem from a client-supplied path to say what a
        # folder is, so it is the /filesystem/browse class exactly and belongs
        # on the same belt: with AUTHZ_GATE_ENFORCING rolled back, a READ token
        # handed out to view a shared gallery would otherwise probe host layout
        # a folder at a time.
        "/api/v1/libraries/inspect",
        # LOCAL_OWNER_ONLY at the gate: it names the host folder this library
        # publishes its Views tree to. Same belt-and-braces as the entries
        # above - AUTHZ_GATE_ENFORCING is a documented one-line rollback, and
        # without this entry taking it would hand that path to every share
        # token.
        "/api/v1/server-config/views",
        # The folder-structure read's result IS a map of the owner's folder
        # names, tree shape and picture counts (v1.11 Phase 2). It is
        # LOCAL_OWNER_ONLY at the gate; it is here as well for the same
        # rollback reason as filesystem/browse above.
        "/api/v1/folder-structure/read/status",
        # The commit's result carries the same host-path-derived information
        # as the read above (v1.11 Phase 3) - same tier, same rollback
        # reasoning, same belt.
        "/api/v1/folder-structure/commit/status",
        # The library's own folder layout (v1.11 Phase 4b). It names no path,
        # but it describes the shape of the owner's folder tree and it is the
        # read side of the control that decides where their files get written.
        # LOCAL_OWNER_ONLY at the gate; here as well for the same rollback
        # reason as the entries above.
        "/api/v1/server-config/layout",
        # The migration preview (v1.11 Phase 4c) is the same belt one route
        # further: it counts what moving the whole library onto that layout
        # would do, and the sample paths and mount-point findings it returns
        # are more of the owner's folder tree, not less.
        "/api/v1/server-config/layout/migration",
    }
)


def is_auth_excluded_path(path: str) -> bool:
    """Return True when *path* should bypass auth checks.

    Supports both legacy unversioned public paths and versioned API paths
    (e.g. ``/api/v1/login``).
    """
    if path in AUTH_EXCLUDED_PATHS or any(
        path.startswith(prefix) for prefix in AUTH_EXCLUDED_PREFIXES
    ):
        return True

    for api_prefix in AUTH_API_PREFIXES:
        if not path.startswith(api_prefix):
            continue
        stripped = path[len(api_prefix) :] or "/"
        if stripped in AUTH_EXCLUDED_PATHS or any(
            stripped.startswith(prefix) for prefix in AUTH_EXCLUDED_PREFIXES
        ):
            return True

    return False


def get_real_client_ip(request: Request, trusted_proxies: list[str]) -> str:
    """Return the real client IP, walking X-Forwarded-For when the direct connection is from a trusted proxy."""
    direct_ip = request.client.host if request.client else "127.0.0.1"
    if direct_ip not in trusted_proxies:
        return direct_ip
    # Walk X-Forwarded-For right-to-left, skipping trusted proxy IPs.
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if hop not in trusted_proxies:
            return hop
    return direct_ip


# FastAPI's in-process ``TestClient`` presents this non-IP host string. It is the
# ONE unparseable value the locality predicates admit; every other unparseable host
# (e.g. a bogus ``X-Forwarded-For`` hop) fails closed (CSO review, finding 3).
_TESTCLIENT_HOST = "testclient"


def is_local_ip(ip: str) -> bool:
    """Return True if *ip* is a loopback or RFC 1918 private address.

    A non-parseable host fails closed (returns ``False``), except the in-process
    ``TestClient`` sentinel (:data:`_TESTCLIENT_HOST`), which stays local so the
    unit suite is not blocked.
    """
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_loopback or addr.is_private
    except ValueError:
        return ip == _TESTCLIENT_HOST


# Tailscale addresses an owner's own devices out of the CGNAT / shared-address
# range (RFC 6598 ``100.64.0.0/10``) over IPv4 and a ULA prefix
# (``fd7a:115c:a1e0::/48``) over IPv6. Neither is loopback; the IPv4 CGNAT range
# is NOT ``is_private`` either (RFC 6598 is "shared address space", not RFC 1918),
# so a Tailscale-over-IPv4 owner is falsely rejected by ``is_local_ip``. The IPv6
# prefix is a ULA and therefore already ``is_private``, but it is listed here too
# so the scoped predicate stays correct even if ``is_local_ip`` ever changes.
_TAILSCALE_NETS = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


def is_tailscale_ip(ip: str) -> bool:
    """Return True if *ip* is in a Tailscale CGNAT (IPv4) or ULA (IPv6) range.

    Non-parseable strings (e.g. ``"testclient"``) are NOT Tailscale - the caller's
    ``is_local_ip`` already treats those as local, so returning False here avoids a
    second, redundant "everything unparsable is local" widening.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in _TAILSCALE_NETS)


def is_local_or_tailscale_ip(ip: str) -> bool:
    """Return True for a loopback / RFC1918 / Tailscale address.

    Scoped locality predicate for the §16.3 host-capability gate ONLY
    (:mod:`pixlstash.authz.gate`). It deliberately does **not** replace the shared
    :func:`is_local_ip`, which also backs ``_require_local_for_write``, the
    middleware ALL-token remote block, and the HTTPS-skip carve-out - widening
    those to Tailscale is an unrelated remote-login risk decision the §16.3 debate
    refused to couple. This predicate widens only the host-ops locality check so a
    Tailscale-over-IPv4 owner is no longer falsely denied.
    """
    return is_local_ip(ip) or is_tailscale_ip(ip)


def is_loopback_ip(ip: str) -> bool:
    """Return True only if *ip* is a loopback address (127.0.0.0/8, ::1).

    Unlike :func:`is_local_ip`, RFC 1918 private LAN addresses are **not**
    treated as loopback. This is the correct gate for high-privilege paths that
    must never be reachable from another host on the LAN (the seeded desktop
    owner session and first-owner registration): the real desktop window always
    talks to the backend over 127.0.0.1, so pinning to loopback loses nothing
    while closing the LAN to those paths.

    A non-parseable host fails closed (returns ``False``), except the in-process
    ``TestClient`` sentinel (:data:`_TESTCLIENT_HOST`), which stays loopback so the
    unit suite is not blocked.
    """
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return ip == _TESTCLIENT_HOST


def is_unscoped_owner_token(token: UserToken) -> bool:
    """Return True when *token* grants full, unrestricted owner access.

    An owner credential is ``ALL`` scope with **no** resource restriction. A
    ``READ`` token and any resource-restricted token are narrower and must not
    be treated as the owner.

    This is the single spelling of that rule. It matches what
    :meth:`AuthService.require_unscoped_owner` derives from ``request.state``
    and what the request middleware applies to a matched token, so the
    WebSocket handshake and the login endpoint cannot drift from it.
    """
    return token.scope == "ALL" and token.resource_type is None


def is_token_expired(token: UserToken, now: Optional[datetime] = None) -> bool:
    """Return True when *token* has passed its ``expires_at`` timestamp.

    A token with no ``expires_at`` never expires. *now* defaults to the current
    UTC time; ``expires_at`` is stored naive-UTC.

    The comparison is inclusive: a token whose ``expires_at`` is exactly *now*
    has expired. The two checks this replaced disagreed on that boundary, and
    the stricter reading is the one that matches the field's meaning.
    """
    if token.expires_at is None:
        return False
    return token.expires_at <= (now if now is not None else datetime.utcnow())


@dataclass
class TokenScope:
    """Scope restriction carried on request.state for token-authenticated requests."""

    scope: str
    resource_type: Optional[str]
    resource_id: Optional[int]
    expires_at: Optional[datetime]
    include_attachments: bool = False
    watermark: bool = False


@dataclass(frozen=True)
class WebSocketAuth:
    """Result of authenticating a WebSocket handshake.

    Attributes:
        user_id: The authenticated owner user id.
        is_owner: True for a cookie session or an ALL-scope token with no
            resource restriction; False for a resource-scoped / READ token,
            which authenticates the connection but is not entitled to the
            owner-level global event stream.
    """

    user_id: int
    is_owner: bool
    library_uuid: Optional[str] = None


class AuthService:
    def __init__(
        self, db: VaultDatabase, server_config: dict, server_config_path: str, logger
    ):
        self._db = db
        self._server_config = server_config
        self._server_config_path = server_config_path
        self._logger = logger
        # Returns the uuid of the library a newly minted token belongs to.
        # A callable rather than a value because the active library changes at
        # runtime: a token must be stamped with the library that is active when
        # it is minted, not the one that was active when the server booted.
        # Set by ``Server``; left None for a Vault built without one (tests, the
        # CLI tools), where minting is not exercised.
        self.library_uuid_provider = None
        # The *active library's* database, as distinct from ``self._db``, which
        # is the hub. This service is not purely identity: guest sessions are
        # per-library state and stay in the vault (plan §9), so the guest-session
        # cookie lookup has to read from there. Re-pointed when the active
        # library changes; falls back to ``self._db`` when unset, which is the
        # single-database arrangement tests and CLI tools still use.
        self.vault_db = None
        self.active_session_ids: dict[str, int] = {}
        # Which token minted which session, in both directions, so a session can
        # be ended in O(1) when its token is removed (see _register_session /
        # _drop_sessions_for_tokens). Sessions from a password login or the
        # seeded desktop session have no token and appear in neither map.
        # Guarded by _session_lock. That lock and _token_cache_lock are always
        # taken separately, one after the other, never nested, so there is no
        # lock ordering to get wrong.
        #
        # Keyed on ``UserToken.public_id``, never on the integer primary key.
        # A session outlives the request that created it, and the integer id is
        # a plain SQLite rowid alias: delete the token and the next one created
        # is handed the same id, at which point these maps would name a token
        # they were never built from. Revoking the right token would then not
        # end its session, and revoking an unrelated one would end the wrong
        # session - fail-open. ``public_id`` is random and never reissued, so a
        # stale key resolves to the same token or to nothing (issue #666).
        self._sessions_by_token_public_id: dict[str, set[str]] = {}
        self._token_public_id_by_session: dict[str, str] = {}
        # Token-derived cookie sessions keep the token's library pin. Password
        # and ordinary browser/desktop sessions are absent and follow switches.
        self._library_uuid_by_session: dict[str, str] = {}
        self._session_lock = threading.Lock()
        # The pre-authenticated Electron desktop owner session, if seeded (see
        # seed_desktop_session). It grants full owner access and is therefore
        # pinned to local connections only: the desktop window reaches the
        # backend over loopback, so this session must never authenticate a
        # request arriving on the optional external listener.
        self._desktop_session_token: Optional[str] = None
        self.user: Optional[User] = None
        self.password_hash: Optional[str] = None
        self.username: Optional[str] = None
        self._failed_login_attempts: int = 0
        self._login_lockout_until: float = 0.0
        # Cache of recently-verified tokens: digest(token_value) → (UserToken, expiry_monotonic)
        # Avoids a bcrypt.verify() call on every authenticated request.
        self._token_cache: dict[str, tuple[UserToken, float]] = {}
        self._TOKEN_CACHE_TTL = 300.0  # seconds
        self._token_cache_lock = threading.Lock()
        # Revocation generation counter, bumped by every _flush_token_cache().
        # A lookup that started before a revocation must not be allowed to
        # install its (now stale) result into the cache afterwards - it samples
        # this before reading and re-checks it before writing, see
        # _token_from_value. Guarded by _token_cache_lock.
        self._token_cache_epoch: int = 0
        # A full restore replaces the database file underneath this service.
        # From immediately before that cutover until the restored database has
        # been cleaned and every in-memory credential cache has been rebuilt,
        # no request may authenticate against either side of the swap.  The
        # restore path closes this gate before submitting the swap control task
        # and only reopens it after reset_after_restore() succeeds.
        self._restore_admission_condition = threading.Condition()
        self._restore_auth_gate_closed = False
        self._restore_admission_serial = 0
        self._active_restore_http_leases: set[int] = set()
        self._active_restore_websockets: dict[int, tuple] = {}
        self._RESTORE_DRAIN_TIMEOUT_SECONDS = 30.0
        # In-memory guest session tracking: session_id → last_active_at (monotonic seconds).
        # Entries expire after _GUEST_SESSION_INACTIVE_TTL (30 days) and are pruned lazily
        # in record_guest_activity().  When the cache reaches _guest_max_tracked_sessions
        # the oldest entry is evicted, provided it is at least _GUEST_SESSION_EVICT_MIN_AGE
        # old (4 hours), so a truly-active burst of sessions is never silently dropped.
        self._guest_sessions: dict[str, float] = {}
        self._guest_sessions_lock = threading.Lock()
        self._GUEST_SESSION_ACTIVE_TTL = 3600.0  # 1 hour - "currently active"
        self._GUEST_SESSION_INACTIVE_TTL = 30 * 86400.0  # 30 days - hard expiry
        self._GUEST_SESSION_EVICT_MIN_AGE = (
            4 * 3600.0
        )  # 4 hours - min age to evict under cap
        self._guest_max_tracked_sessions: int = int(
            self._server_config.get("guest_max_stored_sessions", 1000)
        )

    def _next_restore_admission_id(self) -> int:
        self._restore_admission_serial += 1
        return self._restore_admission_serial

    def _acquire_restore_http_lease(self) -> Optional[int]:
        """Atomically admit one API request, or reject it during a restore."""
        with self._restore_admission_condition:
            if self._restore_auth_gate_closed:
                return None
            lease = self._next_restore_admission_id()
            self._active_restore_http_leases.add(lease)
            return lease

    def _release_restore_http_lease(self, lease: Optional[int]) -> None:
        if lease is None:
            return
        with self._restore_admission_condition:
            self._active_restore_http_leases.discard(lease)
            self._restore_admission_condition.notify_all()

    def register_authenticated_websocket(self, websocket) -> Optional[int]:
        """Register an authenticated WebSocket for its entire handler lifetime.

        Authentication and registration are deliberately separate: token
        verification may touch the database and must not hold the admission
        condition. Registration is the final, atomic admission check before
        ``accept()``; a restore that closed the barrier in the meantime wins.
        """
        loop = asyncio.get_running_loop()
        task = asyncio.current_task()
        with self._restore_admission_condition:
            if self._restore_auth_gate_closed:
                return None
            lease = self._next_restore_admission_id()
            self._active_restore_websockets[lease] = (websocket, loop, task)
            return lease

    def unregister_authenticated_websocket(self, lease: Optional[int]) -> None:
        if lease is None:
            return
        with self._restore_admission_condition:
            self._active_restore_websockets.pop(lease, None)
            self._restore_admission_condition.notify_all()

    @staticmethod
    async def _terminate_authenticated_websocket(websocket, handler_task) -> None:
        """Close one established socket, then cancel its owning handler."""
        try:
            await websocket.close(code=1012, reason="Database restore in progress")
        finally:
            current = asyncio.current_task()
            if (
                handler_task is not None
                and handler_task is not current
                and not handler_task.done()
            ):
                handler_task.cancel()

    def close_auth_for_restore(
        self, restore_request_lease: Optional[int] = None
    ) -> None:
        """Close admissions and drain authenticated traffic before DB cutover.

        The restore endpoint itself entered through the HTTP middleware, so its
        lease must be removed from the drain set after admissions close. Its
        middleware ``finally`` releases the same id again harmlessly. Every
        other API request remains counted through ``call_next`` and every
        authenticated WebSocket remains counted through handler teardown.

        A timeout raises while leaving admissions closed. Proceeding to a
        whole-file swap without proving that the old authenticated world has
        drained would be fail-open.
        """
        deadline = time.monotonic() + self._RESTORE_DRAIN_TIMEOUT_SECONDS
        with self._restore_admission_condition:
            self._restore_auth_gate_closed = True
            if restore_request_lease is not None:
                self._active_restore_http_leases.discard(restore_request_lease)
            sockets = list(self._active_restore_websockets.items())
            self._restore_admission_condition.notify_all()

        for lease, (websocket, loop, handler_task) in sockets:
            if loop.is_closed() or not loop.is_running():
                self.unregister_authenticated_websocket(lease)
                continue
            termination = self._terminate_authenticated_websocket(
                websocket, handler_task
            )
            try:
                asyncio.run_coroutine_threadsafe(termination, loop)
            except Exception as exc:
                termination.close()
                self._logger.error(
                    "Could not schedule WebSocket termination for restore: %s",
                    exc,
                )

        with self._restore_admission_condition:
            while self._active_restore_http_leases or self._active_restore_websockets:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    http_count = len(self._active_restore_http_leases)
                    websocket_count = len(self._active_restore_websockets)
                    self._logger.critical(
                        "Restore admission drain timed out with %d HTTP request(s) "
                        "and %d WebSocket(s) still active; authentication remains "
                        "closed.",
                        http_count,
                        websocket_count,
                    )
                    raise RuntimeError(
                        "Timed out draining authenticated traffic before database "
                        "restore; authentication remains disabled."
                    )
                self._restore_admission_condition.wait(timeout=remaining)

    def reopen_auth_after_restore(self) -> None:
        """Re-enable authentication after a fully successful restore."""
        with self._restore_admission_condition:
            self._restore_auth_gate_closed = False
            self._restore_admission_condition.notify_all()

    def is_auth_closed_for_restore(self) -> bool:
        """Return whether authentication is fail-closed for a database restore."""
        with self._restore_admission_condition:
            return self._restore_auth_gate_closed

    @staticmethod
    def _restore_unavailable_response() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Authentication is temporarily unavailable during restore."
            },
            headers={"Retry-After": "5"},
        )

    def record_guest_activity(self, session_id: str) -> None:
        """Record or refresh the in-memory last-active timestamp for a guest session.

        Also performs two bounded maintenance operations while the lock is held:
        1. Prune all entries that have been inactive for more than 30 days.
        2. If the cache still exceeds the configured cap after pruning, evict the
           single oldest entry provided it is at least 4 hours old.
        """
        now = time.monotonic()
        expire_before = now - self._GUEST_SESSION_INACTIVE_TTL
        evict_before = now - self._GUEST_SESSION_EVICT_MIN_AGE
        with self._guest_sessions_lock:
            # 1. Prune expired entries (inactive for > 30 days).
            expired = [
                sid for sid, ts in self._guest_sessions.items() if ts < expire_before
            ]
            for sid in expired:
                del self._guest_sessions[sid]

            # 2. Cap enforcement: if still over the limit, evict the oldest entry
            #    that is at least 4 hours old so we never silently drop a hot session.
            if len(self._guest_sessions) >= self._guest_max_tracked_sessions:
                oldest_sid = min(
                    self._guest_sessions, key=self._guest_sessions.__getitem__
                )
                if self._guest_sessions[oldest_sid] < evict_before:
                    del self._guest_sessions[oldest_sid]

            self._guest_sessions[session_id] = now

    def count_active_guest_sessions(self) -> int:
        """Return the number of guest sessions with activity in the last hour.

        Expired entries (inactive > 30 days) are pruned while the lock is held
        so the dict stays bounded between calls to record_guest_activity().
        """
        now = time.monotonic()
        active_cutoff = now - self._GUEST_SESSION_ACTIVE_TTL
        expire_before = now - self._GUEST_SESSION_INACTIVE_TTL
        with self._guest_sessions_lock:
            expired = [
                sid for sid, ts in self._guest_sessions.items() if ts < expire_before
            ]
            for sid in expired:
                del self._guest_sessions[sid]
            return sum(1 for ts in self._guest_sessions.values() if ts >= active_cutoff)

    def clear_guest_session_tracking(self) -> None:
        """Drop availability-only guest activity when the active library changes."""
        with self._guest_sessions_lock:
            self._guest_sessions = {}

    def _register_session(
        self,
        session_id: str,
        user_id: int,
        token_public_id: Optional[str] = None,
        token_library_uuid: Optional[str] = None,
    ) -> None:
        """Record an authenticated session and the token that created it.

        Passing *token_public_id* links the session to that token so
        :meth:`_drop_sessions_for_tokens` can end it if the token is later
        removed, keeping a session's lifetime within its credential's. A
        password login and the seeded desktop session pass ``None`` and are
        unaffected by token removal.

        The link is made on ``UserToken.public_id`` rather than on the integer
        primary key, because the key has to stay meaningful for as long as the
        session does and a deleted row's integer id is handed to the next token
        created (issue #666).
        """
        with self._session_lock:
            self.active_session_ids[session_id] = user_id
            if token_public_id is not None:
                self._sessions_by_token_public_id.setdefault(
                    token_public_id, set()
                ).add(session_id)
                self._token_public_id_by_session[session_id] = token_public_id
                if token_library_uuid is not None:
                    self._library_uuid_by_session[session_id] = token_library_uuid

    def _forget_session(self, session_id: str) -> None:
        """Forget a single session and its token link (used by logout)."""
        with self._session_lock:
            self.active_session_ids.pop(session_id, None)
            token_public_id = self._token_public_id_by_session.pop(session_id, None)
            self._library_uuid_by_session.pop(session_id, None)
            if token_public_id is not None:
                sessions = self._sessions_by_token_public_id.get(token_public_id)
                if sessions is not None:
                    sessions.discard(session_id)
                    if not sessions:
                        del self._sessions_by_token_public_id[token_public_id]

    def _drop_sessions_for_tokens(self, token_public_ids: Iterable[str]) -> int:
        """End every session created from one of *token_public_ids*.

        Sessions created by any other credential are left untouched. Returns
        the number of sessions ended.
        """
        dropped = 0
        with self._session_lock:
            for token_public_id in token_public_ids:
                for session_id in self._sessions_by_token_public_id.pop(
                    token_public_id, set()
                ):
                    self._token_public_id_by_session.pop(session_id, None)
                    self._library_uuid_by_session.pop(session_id, None)
                    if self.active_session_ids.pop(session_id, None) is not None:
                        dropped += 1
        return dropped

    def _confirm_session_token_still_exists(
        self, session_id: str, token_public_id: str
    ) -> None:
        """Undo a just-registered session if its token was removed mid-login.

        A session stays within the lifetime of the token that created it.
        Verifying a token takes a bcrypt call per candidate row plus a database
        round trip, so a removal can land between the read that matched the
        token and :meth:`_register_session`, which is before this session
        exists for that removal's sweep to find.

        Re-reading the row *after* registering settles that ordering rather
        than narrowing it. ``_session_lock`` totally orders this registration
        against the removal's :meth:`_drop_sessions_for_tokens` sweep, so there
        are exactly two cases:

        * If the session is registered before the sweep, the sweep finds and
          ends it.
        * Otherwise the sweep ran first. The sweep runs only after
          ``run_task(remove_token)`` has returned, so the delete had already
          committed before the registration, and therefore before this re-read
          starts - so this re-read cannot see the row and ends the session
          here.

        There is no third ordering, so exactly one of the two always fires.

        Note what the second case does **not** depend on: it needs only
        "a read that starts after a commit observes it", which holds for the
        writer queue and equally for a WAL read on the read path (§16.4). It is
        therefore safe if this read is ever moved off ``run_task``. What it does
        depend on is the sweep running *after* the delete has committed, and on
        registration and sweep sharing ``_session_lock``. Do not reorder either.

        The re-read matches on ``public_id``, so "the same token" means the same
        token and not merely the same rowid: an integer id can be reissued to a
        replacement token between the match and this read, which would let a
        removed credential's session survive by answering to its successor.
        """
        still_exists = self._db.run_task(
            lambda session, pid=token_public_id: (
                session.exec(
                    select(UserToken).where(UserToken.public_id == pid)
                ).first()
                is not None
            ),
            priority=DBPriority.IMMEDIATE,
        )
        if still_exists:
            return
        self._forget_session(session_id)
        self._logger.warning(
            "Discarded a session for token %s: the token was removed while "
            "the sign-in was in progress.",
            token_public_id,
        )
        raise HTTPException(status_code=401, detail="Invalid token")

    def _clear_all_sessions(self) -> None:
        """End every active session, including the seeded desktop session.

        Used by the credential-changing paths (password change, password
        removal), where no existing session should survive.
        """
        with self._session_lock:
            self.active_session_ids = {}
            self._sessions_by_token_public_id = {}
            self._token_public_id_by_session = {}
            self._library_uuid_by_session = {}

    def reset_after_restore(self) -> None:
        """Drop every piece of in-memory authentication state after a restore.

        A full restore replaces the whole database file. Everything this
        service holds in memory was derived from the *previous* file and now
        describes rows that either no longer exist or belong to someone else:

        * the token cache would keep authenticating verified tokens for the
          rest of its TTL, including tokens absent from the restored database;
        * ``active_session_ids`` and the session maps would keep authenticating
          sessions established before the swap, against an owner account the
          restore may have replaced;
        * the in-memory guest-session counters would keep describing
          ``guest_session`` rows the swap rolled back;
        * ``user`` / ``username`` / ``password_hash`` are a cache of the owner
          row, and a restore can roll the account back to different
          credentials, so they are re-read from the restored database.

        Requiring a fresh sign-in is the correct outcome, not a cost: restore
        is owner-only, and the identities the surviving state names have moved.

        The desktop shell's pre-authenticated session is re-seeded afterwards.
        It is not a stored credential - the shell mints it per launch and the
        server only registers it - so re-registering it against the restored
        owner is the honest equivalent of the sign-in every other client has to
        redo, and it does not leave the local window stranded until a restart.

        **This is not made redundant by ``UserToken.public_id``.** A restored
        snapshot brings back its *own* ``public_id`` values, so a public id this
        process still remembers can be absent from the restored database, or -
        for a snapshot taken from this same vault - present and pointing at a
        token row whose other columns have since changed. Never-reused ids stop
        an id from silently naming a *different* token; they cannot make
        in-memory state that outlived a whole-file swap correct. Both halves of
        issue #666 are needed.

        Clearing itself touches only in-memory dictionaries. The two database
        reads that follow it (``ensure_user`` and, when the desktop shell is
        running, ``seed_desktop_session``) go through the ordinary writer queue,
        which is why this must be called from the restore path only *after*
        ``run_control_task(_do_swap)`` has returned - the swap has released
        ``exclusive_engine_access()`` by then and the engine has been
        re-created. Calling it from inside the swap would deadlock the request
        path (see ``services/restore/full_restore.py``).
        """
        self._flush_token_cache()
        self._clear_all_sessions()
        with self._guest_sessions_lock:
            self._guest_sessions = {}
        self._desktop_session_token = None
        self._logger.info(
            "Cleared the token cache and every session after a database "
            "restore; clients must sign in again."
        )
        self.ensure_user()
        self.seed_desktop_session()

    def ensure_secure_when_required(self, request: Request):
        if not self._server_config.get("require_ssl", False):
            return
        if request.url.scheme == "https":
            return
        # require_ssl governs the *external* surface only. The Electron desktop
        # window always reaches the backend over a plain-HTTP loopback connection
        # by design (require_ssl drives the separate external listener, not this
        # one), and a standalone HTTPS-only server never serves plaintext at all.
        # So a local/loopback client must not be forced onto HTTPS - only a
        # genuinely remote plaintext request is rejected.
        if is_local_ip(self._get_real_client_ip(request)):
            return
        raise HTTPException(
            status_code=403,
            detail="HTTPS is required for this operation.",
        )

    def real_client_ip(self, request: Request) -> str:
        """Return the request's real client IP (walking trusted-proxy hops).

        Public entry point for the authorization gate's ``LOCAL_OWNER_ONLY``
        locality check (:mod:`pixlstash.authz.gate`), which is a legitimate second
        caller and must not reach into a private method. Internal auth callers use
        the private alias below.
        """
        trusted = self._server_config.get("trusted_proxies", [])
        return get_real_client_ip(request, trusted)

    @property
    def allow_remote_host_ops(self) -> bool:
        """Whether a remote authenticated owner may reach the §16.3
        ``LOCAL_OWNER_ONLY`` host-capability routes (default ``False``).

        Dedicated flag for the host-ops locality gate. It is deliberately NOT
        ``require_local_for_write`` (the §16.3 debate refused to couple
        remote-login and remote-host-ops risk): enabling remote logins must not
        implicitly hand a remote caller the server's host-filesystem authority.
        It never loosens the ``LOOPBACK_OWNER_ONLY`` red-line routes.
        """
        return bool(self._server_config.get("allow_remote_host_ops", False))

    def _get_real_client_ip(self, request: Request) -> str:
        return self.real_client_ip(request)

    def _get_real_client_ip_ws(self, websocket) -> str:
        """Return the real client IP for a WebSocket handshake.

        Mirrors :meth:`_get_real_client_ip` for the WS object, which exposes the
        direct peer as ``websocket.client.host`` and forwarded hops in the
        handshake headers. Falls back to loopback when the client is unknown.
        """
        direct_ip = (
            websocket.client.host if getattr(websocket, "client", None) else "127.0.0.1"
        )
        trusted = self._server_config.get("trusted_proxies", [])
        if direct_ip not in trusted:
            return direct_ip
        forwarded_for = websocket.headers.get("X-Forwarded-For", "")
        hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
        for hop in reversed(hops):
            if hop not in trusted:
                return hop
        return direct_ip

    def _require_local_for_write(self, http_request: Optional[Request]) -> None:
        """Raise 403 if require_local_for_write is enabled and the client is not on the local network."""
        if not self._server_config.get("require_local_for_write", True):
            return
        if http_request is None:
            return  # programmatic call (e.g. tests) - treat as local
        client_ip = self._get_real_client_ip(http_request)
        if not is_local_ip(client_ip):
            raise HTTPException(
                status_code=403,
                detail="Full access is restricted to local network connections. Use a share token for remote access.",
            )

    def _require_loopback_for_registration(
        self, http_request: Optional[Request]
    ) -> None:
        """Raise 403 if a first-owner registration is attempted from a non-loopback host.

        Claiming the (empty) owner account by setting its username/password the
        first time must only ever happen from the local desktop window, i.e. over
        loopback. ``is_local_ip`` is deliberately **not** used here: it treats the
        whole RFC 1918 LAN as "local", which would let any co-network device
        register the owner account before the legitimate user does and take over
        the entire library. Pin to loopback so the LAN cannot claim the account.
        """
        if http_request is None:
            return  # programmatic call (e.g. tests) - treat as loopback
        client_ip = self._get_real_client_ip(http_request)
        if not is_loopback_ip(client_ip):
            self._logger.warning(
                "Rejected first-owner registration from non-loopback IP %s.",
                client_ip,
            )
            if os.environ.get("PIXLSTASH_IN_DOCKER", "") == "1":
                # Inside a container the host's traffic arrives as the bridge
                # gateway IP, never loopback, so the in-browser first-run setup
                # can never pass this guard. The guard itself must stay exactly
                # this strict (under Docker's userland proxy a LAN attacker is
                # indistinguishable from the operator by IP); instead, tell the
                # operator the supported provisioning path.
                detail = (
                    "Initial setup cannot be completed through a Docker "
                    "network. Set the PIXLSTASH_INITIAL_USERNAME and "
                    "PIXLSTASH_INITIAL_PASSWORD environment variables on the "
                    "container and restart it to provision the owner account "
                    "(then unset them). Alternatively, log in from inside the "
                    "container over loopback using 'docker exec'."
                )
            else:
                detail = (
                    "Initial setup must be completed from the device running PixlStash."
                )
            raise HTTPException(status_code=403, detail=detail)

    def _validate_bcrypt_password_length(self, password: Optional[str]):
        if password is None:
            return
        try:
            byte_length = len(password.encode("utf-8"))
        except Exception:
            byte_length = len(password)
        if byte_length > 72:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Password cannot be longer than 72 bytes. "
                    "Truncate or shorten the password and try again."
                ),
            )

    def get_user(self) -> Optional[User]:
        """Return the (single) owner user row, or None if the account row is absent.

        Runs on the **read** path (``run_immediate_read_task``), not the
        serialised writer queue. ``DBPriority.IMMEDIATE`` only reordered the
        queue; the writer still finished the in-flight task's session first, so
        every authenticated request paid the tail of whatever background batch
        was committing (issue #651). See ``docs/backend_architecture.md`` §16.
        """
        return self._db.run_immediate_read_task(
            lambda session: session.exec(select(User)).first()
        )

    def ensure_user(self) -> User:
        def ensure_user(session: Session):
            user = session.exec(select(User)).first()
            if user:
                if getattr(user, "max_vram_gb", None) is None:
                    user.max_vram_gb = default_max_vram_gb()
                    session.add(user)
                    session.commit()
                    session.refresh(user)
                return user

            user = User(
                max_vram_gb=default_max_vram_gb(),
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self._db.run_task(ensure_user, priority=DBPriority.IMMEDIATE)
        self.user = user
        self.password_hash = user.password_hash if user else None
        self.username = user.username if user else None
        return user

    # Env var the Electron desktop shell uses to hand the server a
    # pre-authenticated owner session token (see seed_desktop_session).
    DESKTOP_SESSION_ENV = "PIXLSTASH_DESKTOP_SESSION"
    # Env vars a headless install (Docker, a remote server) uses to provision
    # the initial owner credentials at startup (see claim_owner_from_env).
    # These constants hold the *names* of the env vars, not any credential;
    # they are deliberately not named *_USERNAME_/_PASSWORD_ so CodeQL's
    # clear-text-logging heuristic doesn't misread logging them as leaking a
    # secret (it classifies any *password*/*username*-named symbol as a
    # sensitive source regardless of value).
    INITIAL_OWNER_LOGIN_ENV = "PIXLSTASH_INITIAL_USERNAME"
    INITIAL_OWNER_AUTH_ENV = "PIXLSTASH_INITIAL_PASSWORD"
    # Minimum accepted length for an env-provisioned password, matching
    # LoginRequest's ``min_length=8`` floor: a shorter env password would claim
    # an account whose credentials the login endpoint itself rejects (422),
    # locking the operator out.
    INITIAL_PASSWORD_MIN_LEN = 8
    # Minimum accepted length of the desktop session token. The shell ships a
    # 32-byte token rendered as 64 hex chars; 32 is the documented floor of that
    # contract (see seed_desktop_session and ServerProcess.ts).
    DESKTOP_SESSION_MIN_LEN = 32

    def seed_desktop_session(self) -> Optional[str]:
        """Register a pre-authenticated owner session for the local desktop app.

        When PixlStash runs inside the Electron desktop shell, the shell
        generates a high-entropy token once per launch, passes it to the server
        via the ``PIXLSTASH_DESKTOP_SESSION`` env var, and injects the same value
        as the ``session_id`` cookie in its own ``BrowserWindow``.  Seeding the
        token into :attr:`active_session_ids` here means the local window opens
        straight into the library with no login/registration prompt.

        This affects only the loopback owner: remote browsers and API clients
        present a *different* cookie/token and continue to authenticate via the
        normal password login (:meth:`login`) or share tokens.  Returns the
        seeded token, or ``None`` when no (valid) token was supplied.
        """
        token = os.environ.get(self.DESKTOP_SESSION_ENV, "").strip()
        if not token:
            return None
        # Guard against a weak/guessable token leaking owner access. The shell
        # (electron/src/backend/ServerProcess.ts) supplies a 32-byte random token
        # as 64 hex chars, so the documented contract is 32+ chars. Pin the floor
        # to that contract (DESKTOP_SESSION_MIN_LEN) rather than an arbitrary 16,
        # so a regression in the shell's generator to a shorter token is rejected
        # instead of accepted as a full-owner credential.
        if len(token) < self.DESKTOP_SESSION_MIN_LEN:
            self._logger.warning(
                "Ignoring %s: token too short (got %d chars, expected >=%d).",
                self.DESKTOP_SESSION_ENV,
                len(token),
                self.DESKTOP_SESSION_MIN_LEN,
            )
            return None
        user = self.ensure_user()
        if not user or user.id is None:
            self._logger.error(
                "Could not seed desktop session: failed to ensure an owner user."
            )
            return None
        self._register_session(token, user.id)
        self._desktop_session_token = token
        self._logger.info(
            "Seeded a pre-authenticated desktop session for the local owner."
        )
        return token

    def claim_owner_from_env(self) -> bool:
        """Claim the unclaimed owner account from env-provided credentials.

        Headless installs (Docker in particular) can never satisfy the
        loopback-only first-owner registration gate
        (:meth:`_require_loopback_for_registration`): inside a container, host
        traffic arrives as the bridge-gateway IP, so the in-browser first-run
        setup is unreachable by design. The IP guard must stay that strict -
        under Docker's userland proxy a LAN attacker is indistinguishable from
        the operator by IP - so instead the operator provisions the initial
        credentials via the ``PIXLSTASH_INITIAL_USERNAME`` /
        ``PIXLSTASH_INITIAL_PASSWORD`` env vars. This startup chokepoint claims
        the account with them before the server accepts requests, removing the
        racing window entirely.

        Hard rules:

        - An **already-claimed account is never modified** - stale env vars on
          a later restart must not become a takeover vector; they are ignored
          with an INFO log.
        - Exactly one of the two vars set is a configuration error: warn
          loudly, claim nothing.
        - The password passes the same bcrypt 72-byte validation as every
          other claim path, plus the login endpoint's 8-character floor.

        Returns:
            True when the account was claimed from the env vars.
        """
        username = os.environ.get(self.INITIAL_OWNER_LOGIN_ENV, "").strip()
        # Strip to mirror LoginRequest's whitespace-stripping validator -
        # otherwise an env password with stray whitespace could never log in.
        password = os.environ.get(self.INITIAL_OWNER_AUTH_ENV, "").strip()
        if not username and not password:
            return False
        if bool(username) != bool(password):
            self._logger.warning(
                "Ignoring initial owner credentials: only %s is set. Both %s "
                "and %s must be set (non-empty) to provision the owner "
                "account; nothing was claimed.",
                self.INITIAL_OWNER_LOGIN_ENV
                if username
                else self.INITIAL_OWNER_AUTH_ENV,
                self.INITIAL_OWNER_LOGIN_ENV,
                self.INITIAL_OWNER_AUTH_ENV,
            )
            return False

        user = self.ensure_user()
        if user.username or user.password_hash:
            self._logger.info(
                "Ignoring %s/%s: the owner account is already claimed. These "
                "variables only provision an unclaimed account on first "
                "startup and should be unset now.",
                self.INITIAL_OWNER_LOGIN_ENV,
                self.INITIAL_OWNER_AUTH_ENV,
            )
            return False

        try:
            self._validate_bcrypt_password_length(password)
        except HTTPException as exc:
            self._logger.error(
                "Refusing to claim the owner account from %s: %s Nothing was "
                "claimed; fix the password and restart.",
                self.INITIAL_OWNER_AUTH_ENV,
                exc.detail,
            )
            return False
        if len(password) < self.INITIAL_PASSWORD_MIN_LEN:
            self._logger.error(
                "Refusing to claim the owner account from %s: the password "
                "must be at least %d characters (the login endpoint enforces "
                "this floor, so a shorter password could never log in). "
                "Nothing was claimed; fix the password and restart.",
                self.INITIAL_OWNER_AUTH_ENV,
                self.INITIAL_PASSWORD_MIN_LEN,
            )
            return False

        hashed_password = bcrypt.hash(password)

        def set_credentials(session: Session):
            db_user = session.exec(select(User)).first()
            if db_user is None:
                db_user = User(max_vram_gb=default_max_vram_gb())
            if db_user.username or db_user.password_hash:
                # Re-checked inside the transaction so a claim that raced this
                # one (e.g. a concurrent loopback login) is never overwritten.
                return db_user
            db_user.username = username
            db_user.password_hash = hashed_password
            session.add(db_user)
            session.commit()
            session.refresh(db_user)
            return db_user

        user = self._db.run_task(set_credentials, priority=DBPriority.IMMEDIATE)
        if user.password_hash != hashed_password:
            self._logger.warning(
                "Did not claim the owner account from %s/%s: the account was "
                "claimed by another path while startup provisioning ran. The "
                "existing credentials are untouched.",
                self.INITIAL_OWNER_LOGIN_ENV,
                self.INITIAL_OWNER_AUTH_ENV,
            )
            return False
        self.user = user
        self.username = user.username
        self.password_hash = user.password_hash
        self._logger.info(
            "Claimed the owner account as %r from %s/%s. Initial setup is "
            "complete - unset these environment variables now; they are no "
            "longer needed, and leaving them set exposes the initial password "
            "in the container/process environment.",
            username,
            self.INITIAL_OWNER_LOGIN_ENV,
            self.INITIAL_OWNER_AUTH_ENV,
        )
        return True

    def set_password_hash(self, hashed_password: str):
        def update_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                user = User(max_vram_gb=default_max_vram_gb())
            user.password_hash = hashed_password
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self._db.run_task(update_user, priority=DBPriority.IMMEDIATE)
        self.password_hash = user.password_hash
        self.user = user
        return user

    def set_username(self, username: str):
        def update_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                user = User(max_vram_gb=default_max_vram_gb())
            user.username = username
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self._db.run_task(update_user, priority=DBPriority.IMMEDIATE)
        self.username = user.username
        self.user = user
        return user

    def remove_password_hash(self):
        self._logger.info("Removing stored password hash from user database.")

        def clear_user(session: Session):
            user = session.exec(select(User)).first()
            if user is None:
                return None
            user.password_hash = None
            user.username = None
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = self._db.run_task(clear_user, priority=DBPriority.IMMEDIATE)
        self.user = user
        self.password_hash = None
        self.username = None
        self._clear_all_sessions()
        removed_any = False
        if "PASSWORD_HASH" in self._server_config:
            del self._server_config["PASSWORD_HASH"]
            removed_any = True
        if "USERNAME" in self._server_config:
            del self._server_config["USERNAME"]
            removed_any = True
        # Persist whenever either key was removed - previously the write was
        # nested under the USERNAME branch, so removing only PASSWORD_HASH left
        # the stale hash on disk.
        if removed_any:
            persist_server_config(self._server_config_path, self._server_config)
        return user

    def token_from_value(self, token_value: str) -> Optional[UserToken]:
        """Public wrapper for validating a raw token value.

        Looks up and returns the matching UserToken, or None if the value is
        invalid or expired.  Callers outside this module (e.g. share endpoints)
        should use this method rather than the private ``_token_from_value``.

        Args:
            token_value: The raw token string to validate.

        Returns:
            Matching UserToken or None.
        """
        return self._token_from_value(token_value)

    def _token_from_value(self, token_value: str) -> Optional[UserToken]:
        """Validate a raw token value using prefix-indexed lookup; return the
        matching UserToken or None.  Legacy tokens without a token_prefix are
        checked with full iteration as a backward-compatible fallback.

        Results are cached for _TOKEN_CACHE_TTL seconds to avoid a bcrypt call
        on every request (bcrypt is intentionally slow ~200 ms).

        Revocation safety: the DB candidate fetch is the authority and runs on
        the read path (issue #651), so it always observes an already-committed
        revocation - every removal path commits *before* calling
        :meth:`_flush_token_cache`, and under WAL a read that starts after that
        commit sees it. The one ordering that would slip through is a lookup
        that read the row *before* the delete committed and installed it in the
        cache *after* the flush, which the cache fast path would then serve for
        the rest of the TTL without consulting the database. The epoch is
        sampled before the read and re-checked before the write to rule that
        out.
        """
        if not token_value:
            return None

        # Fast path: check in-memory cache first.
        digest = hashlib.sha256(token_value.encode()).hexdigest()
        now_mono = time.monotonic()
        with self._token_cache_lock:
            # Sampled before the database read below, so any removal that
            # lands from here on moves the epoch and blocks the write.
            epoch_at_start = self._token_cache_epoch
            cached = self._token_cache.get(digest)
            if cached is not None:
                token_obj, expires_mono = cached
                if now_mono < expires_mono:
                    # Validate token hasn't been expired server-side either.
                    if not is_token_expired(token_obj):
                        return token_obj
                # Cache entry stale - remove and fall through to verification.
                self._token_cache.pop(digest, None)

        user = self.get_user()
        if user is None:
            return None

        prefix = token_value[:8]

        def fetch_candidates(session: Session, user_id: int, prefix: str):
            return session.exec(
                select(UserToken).where(
                    UserToken.user_id == user_id,
                    or_(
                        UserToken.token_prefix == prefix,
                        UserToken.token_prefix.is_(None),
                    ),
                )
            ).all()

        # Read path, not the writer queue - see get_user (issue #651).
        tokens = self._db.run_immediate_read_task(fetch_candidates, user.id, prefix)
        now = datetime.utcnow()
        for token in tokens:
            if is_token_expired(token, now):
                continue
            if bcrypt.verify(token_value, token.token_hash):
                self._record_token_last_used(token.id)
                # Populate cache so subsequent requests skip bcrypt - but only
                # if no revocation landed while we were reading/verifying. A
                # token this lookup saw as live may have been deleted in the
                # meantime; caching it then would keep a revoked token working
                # for up to _TOKEN_CACHE_TTL, defeating the synchronous flush
                # that delete_token/update_token/revoke_tokens_for_resource do.
                with self._token_cache_lock:
                    if self._token_cache_epoch == epoch_at_start:
                        self._token_cache[digest] = (
                            token,
                            now_mono + self._TOKEN_CACHE_TTL,
                        )
                        # Evict entries beyond a reasonable cap to bound memory use.
                        if len(self._token_cache) > 1000:
                            self._token_cache.pop(next(iter(self._token_cache)))
                    else:
                        self._logger.info(
                            "Not caching token id=%s: a token revocation raced "
                            "this lookup. The next request re-reads the database.",
                            token.id,
                        )
                return token
        return None

    def _flush_token_cache(self) -> None:
        """Drop every cached token verification and invalidate in-flight lookups.

        The single invalidation chokepoint. Every path that removes or changes
        a token (``delete_token``, ``update_token``,
        ``revoke_tokens_for_resource``) calls this *after* its change has
        committed, so the next request re-reads the database and sees it.

        Bumping ``_token_cache_epoch`` under the same lock is what makes that
        sound: a lookup already in flight sampled the epoch before its read and
        will refuse to install its result now that the epoch has moved.
        Clearing without bumping would leave that lookup free to write the row
        it read moments ago straight back into the cache, keeping a revoked
        token authenticating for the full cache TTL.
        """
        with self._token_cache_lock:
            self._token_cache.clear()
            self._token_cache_epoch += 1

    def _record_token_last_used(self, token_id: int) -> None:
        """Queue a token's ``last_used_at`` refresh off the request's critical path.

        ``last_used_at`` is a display-only hygiene signal (shown by
        :meth:`list_tokens` and the Settings account panel); **nothing in the
        authentication or authorization path reads it**, and it carries neither
        revocation nor expiry state - those live in the row's existence and in
        ``expires_at``, both of which are re-read from the database on every
        cache miss. So it is deliberately fire-and-forget: the request no longer
        waits for the serialised writer queue to reach it (issue #651). The cost
        is bounded freshness on the timestamp, and a failed write is logged
        rather than swallowed.

        Args:
            token_id: Primary key of the ``UserToken`` that just authenticated.
        """

        def update_last_used(session: Session, tid: int):
            db_token = session.get(UserToken, tid)
            if db_token is not None:
                db_token.last_used_at = datetime.utcnow()
                session.add(db_token)
                session.commit()

        future = self._db.submit_task(
            update_last_used, token_id, priority=DBPriority.LOW
        )
        future.add_done_callback(
            lambda done: self._log_last_used_result(token_id, done)
        )

    def _log_last_used_result(self, token_id: int, future) -> None:
        """Log a failed background ``last_used_at`` refresh (never raises).

        Runs as a ``Future`` done-callback on the DB writer thread, so it must
        not propagate: an exception here would be swallowed by the executor with
        no trace, which is exactly what this callback exists to prevent.
        """
        try:
            exc = future.exception()
        except Exception as callback_exc:
            self._logger.warning(
                "Could not determine the outcome of the last_used_at refresh "
                "for token id=%s: %s. Authentication is unaffected; the "
                "timestamp shown in Settings may be stale.",
                token_id,
                callback_exc,
            )
            return
        if exc is not None:
            self._logger.warning(
                "Background last_used_at refresh failed for token id=%s: %s. "
                "Authentication is unaffected (last_used_at is display-only), "
                "but the timestamp shown in Settings may be stale.",
                token_id,
                exc,
            )

    def _user_id_from_bearer(self, request: Request) -> Optional[int]:
        """Validate a Bearer token from the Authorization header and return the user id."""
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token_value = auth_header[len("Bearer ") :]
        matched = self._token_from_value(token_value)
        if matched is not None:
            user = self.get_user()
            return user.id if user else None
        return None

    def _token_from_query_param(self, request: Request) -> Optional[UserToken]:
        """Validate a ?token= query parameter.  Only READ-scoped tokens are
        accepted this way - ALL-scoped tokens must not be placed in URLs."""
        token_value = request.query_params.get("token")
        if not token_value:
            return None
        matched = self._token_from_value(token_value)
        if matched is None:
            return None
        if matched.scope != "READ":
            return None
        return matched

    def get_user_id(self, request: Request) -> Optional[int]:
        user_id = getattr(request.state, "auth_user_id", None)
        if user_id is not None:
            return user_id
        session_id = request.cookies.get("session_id")
        if session_id:
            return self.active_session_ids.get(session_id)
        return self._user_id_from_bearer(request)

    def require_user_id(
        self, request: Request, detail: str = "Not authenticated"
    ) -> int:
        user_id = getattr(request.state, "auth_user_id", None)
        if user_id is not None:
            return user_id
        session_id = request.cookies.get("session_id")
        if session_id:
            user_id = self.active_session_ids.get(session_id)
            if user_id is not None:
                return user_id
        user_id = self._user_id_from_bearer(request)
        if user_id is not None:
            return user_id
        raise HTTPException(status_code=401, detail=detail)

    def require_unscoped_owner(
        self,
        request: Request,
        detail: str = "Owner-level (full, unscoped) access required",
    ) -> int:
        """Require a fully-unscoped owner: cookie session or ALL-scope token
        with no resource_type restriction.

        Rejects READ-scoped tokens *and* ALL-scope tokens that are restricted
        to a specific resource. Use this for system-level operations (e.g.
        snapshots, restore) where any narrowing of access would expose data
        outside the token's intended scope.
        """
        user_id = self.require_user_id(request)
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(status_code=403, detail=detail)
        matched_token = getattr(request.state, "matched_token", None)
        if matched_token is not None and matched_token.resource_type is not None:
            raise HTTPException(status_code=403, detail=detail)
        return user_id

    # ------------------------------------------------------------------
    # WebSocket authentication
    #
    # The HTTP auth middleware only runs for the ``http`` ASGI scope, so
    # WebSocket routes are NOT covered by it and must authenticate themselves
    # *before* calling ``websocket.accept()``. These helpers mirror the HTTP
    # rules and additionally guard against cross-site WebSocket hijacking
    # (CSWSH), which the auth check alone cannot stop because the browser
    # auto-attaches the victim's session cookie to a cross-site handshake.
    # ------------------------------------------------------------------

    def authenticate_websocket(self, websocket) -> "Optional[WebSocketAuth]":
        """Authenticate a WebSocket handshake. Call BEFORE ``accept()``.

        Mirrors the HTTP authentication paths: a cookie session is a full
        owner; a ``?token=`` query param is honoured only for READ scope
        (ALL-scope tokens must never appear in URLs); a ``Bearer`` header is
        honoured for any scope.

        Args:
            websocket: The incoming Starlette/FastAPI ``WebSocket``.

        Returns:
            A ``WebSocketAuth(user_id, is_owner)`` - ``is_owner`` is True for a
            cookie session or an ALL-scope token with no resource restriction -
            or ``None`` if the connection is unauthenticated.
        """
        # WebSocket handshakes bypass HTTP middleware. Keep them behind the
        # same restore gate so a pre-swap cookie or cached bearer token cannot
        # authenticate in the queue gap after the database has been replaced.
        if self.is_auth_closed_for_restore():
            return None

        # Cookie session - full owner (the browser SPA sends this on the
        # handshake, same-origin).
        session_id = websocket.cookies.get("session_id")
        if session_id:
            user_id = self.active_session_ids.get(session_id)
            # Mirror the HTTP path's fail-closed backstop: the seeded desktop
            # owner session is loopback-only and must never authenticate a
            # WebSocket arriving on the optional external listener. Without this,
            # a non-browser LAN client presenting the desktop session token as a
            # session_id cookie would be authenticated as full owner on /ws/*.
            if (
                user_id is not None
                and session_id == self._desktop_session_token
                and not is_loopback_ip(self._get_real_client_ip_ws(websocket))
            ):
                self._logger.warning(
                    "Rejected desktop owner WebSocket session from non-loopback IP %s.",
                    self._get_real_client_ip_ws(websocket),
                )
                user_id = None
            if user_id is not None:
                library_uuid = self._library_uuid_by_session.get(session_id)
                if library_uuid not in (None, self.active_library_uuid()):
                    return None
                return WebSocketAuth(
                    user_id=user_id, is_owner=True, library_uuid=library_uuid
                )

        matched: Optional[UserToken] = None
        # Bearer token (non-browser clients can set this header).
        auth_header = websocket.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            matched = self._token_from_value(auth_header[len("Bearer ") :])
        # ?token= query param - READ scope only, mirroring _token_from_query_param.
        if matched is None:
            token_value = websocket.query_params.get("token")
            if token_value:
                candidate = self._token_from_value(token_value)
                if candidate is not None and candidate.scope == "READ":
                    matched = candidate

        if matched is not None:
            if getattr(matched, "library_uuid", None) != self.active_library_uuid():
                return None
            if (
                is_unscoped_owner_token(matched)
                and self._server_config.get("require_local_for_write", True)
                and not is_local_ip(self._get_real_client_ip_ws(websocket))
            ):
                self._logger.warning(
                    "Rejected owner-token WebSocket from non-local IP %s.",
                    self._get_real_client_ip_ws(websocket),
                )
                return None
            user = self.get_user()
            if user is not None:
                return WebSocketAuth(
                    user_id=user.id,
                    is_owner=is_unscoped_owner_token(matched),
                    library_uuid=getattr(matched, "library_uuid", None),
                )
        return None

    def is_websocket_origin_allowed(
        self, websocket, allow_origins, allow_origin_regex
    ) -> bool:
        """Reject cross-site WebSocket handshakes (CSWSH).

        Browsers always send an ``Origin`` on a WS handshake, so a present
        Origin that is neither same-origin nor in the configured CORS allow
        list is a cross-site attempt and is refused. A missing Origin
        (non-browser client) is allowed through to the auth check, which still
        gates access.

        Args:
            websocket: The incoming ``WebSocket``.
            allow_origins: List of explicitly-allowed origins (CORS policy).
            allow_origin_regex: Optional regex of allowed origins.

        Returns:
            True if the handshake's Origin is acceptable.
        """
        origin = websocket.headers.get("origin")
        if not origin:
            return True
        # Same-origin: the Origin's host:port equals the Host the handshake
        # targeted (covers the normal SPA case regardless of CORS config).
        host = websocket.headers.get("host")
        if host and urlparse(origin).netloc == host:
            return True
        if origin in (allow_origins or []):
            return True
        if allow_origin_regex and re.match(allow_origin_regex, origin):
            return True
        return False

    def get_user_for_request(self, request: Request) -> User:
        user_id = self.require_user_id(request)
        # Read path, not the writer queue - see get_user (issue #651).
        user = self._db.run_immediate_read_task(
            lambda session: session.get(User, user_id)
        )
        if user is None:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    def change_password(self, request: Request, payload) -> dict:
        self.ensure_secure_when_required(request)
        user = self.get_user_for_request(request)

        self._validate_bcrypt_password_length(payload.current_password)
        self._validate_bcrypt_password_length(payload.new_password)

        if user.password_hash:
            if not payload.current_password:
                raise HTTPException(
                    status_code=400,
                    detail="Current password is required",
                )
            if not bcrypt.verify(payload.current_password, user.password_hash):
                raise HTTPException(status_code=401, detail="Invalid password")
        else:
            # The account has no password set yet (the auto-logged-in desktop
            # owner before it is claimed). Without this guard, the current-
            # password check above is skipped entirely, so anyone holding a
            # session for the unclaimed account could set its password. Today
            # only the loopback desktop window can reach an authenticated
            # session for it, but that is an *indirect* defence (the session
            # backstop); make it explicit and fail-closed here. Setting the
            # first password on an unclaimed account is a claim, so gate it to
            # loopback exactly like first-owner registration
            # (_require_loopback_for_registration), keeping the two claim paths
            # consistent.
            self._require_loopback_for_registration(request)

        hashed_password = bcrypt.hash(payload.new_password)

        def update_user(session: Session, user_id: int):
            db_user = session.get(User, user_id)
            if db_user is None:
                self._logger.debug(
                    "User %s not found in DB when updating",
                    user_id,
                )
                raise HTTPException(
                    status_code=404, detail="User not found when updating"
                )
            db_user.password_hash = hashed_password
            session.add(db_user)
            session.commit()
            session.refresh(db_user)
            return db_user

        updated_user = self._db.run_task(
            update_user, user.id, priority=DBPriority.IMMEDIATE
        )
        self.user = updated_user
        self.password_hash = updated_user.password_hash
        self.username = updated_user.username
        self._clear_all_sessions()
        return {"status": "success"}

    def get_auth_info(self, request: Request) -> dict:
        self.ensure_secure_when_required(request)
        user = self.get_user_for_request(request)
        return {
            "username": user.username,
            "has_password": bool(user.password_hash),
        }

    def active_library_uuid(self) -> Optional[str]:
        """Return the library a token minted right now belongs to.

        Every token is stamped with exactly one library (multi-library plan §4):
        an unpinned token would change what it grants the moment the owner
        switched, so a share link would start serving different pictures and an
        automation would write into the wrong place. The hub column is NOT NULL,
        so a missing provider surfaces as a write error rather than as a token
        that silently follows the active library.
        """
        if self.library_uuid_provider is None:
            return None
        return self.library_uuid_provider()

    def create_token(
        self,
        request: Request,
        description: Optional[str],
        scope: str = "ALL",
        resource_type: Optional[str] = None,
        resource_id: Optional[int] = None,
        expires_at: Optional[datetime] = None,
        include_attachments: bool = False,
        watermark: bool = False,
    ):
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        # Only the owner (full session or ALL-scope bearer) may create tokens
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403, detail="Scoped tokens cannot create new tokens"
            )

        if scope not in ("ALL", "READ"):
            raise HTTPException(status_code=400, detail="scope must be 'ALL' or 'READ'")
        # A resource-scoped token must be READ. An ALL-scope token carrying a
        # resource_type is the F1/F3 footgun: the auth middleware only builds
        # ``request.state.token_scope`` for non-ALL scopes, so such a token would
        # (a) bypass every object-scope guard (``enforce_picture_scope`` /
        # ``fetch_scope_allowed_picture_ids`` read ``token_scope``) and (b) pass
        # the "only the owner may create tokens" check above - i.e. it is a full
        # owner token wearing a "restricted" label. Forbid minting it at the
        # source. See docs/reviews/feature-slick-grid-updates.md (F3).
        if scope == "ALL" and resource_type is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A resource-scoped token must use READ scope; an ALL-scope "
                    "token cannot be restricted to a resource."
                ),
            )
        if resource_type is not None and resource_type not in (
            "picture_set",
            "character",
            "project",
            "picture",
        ):
            raise HTTPException(
                status_code=400,
                detail="resource_type must be 'picture_set', 'character', 'project', or 'picture'",
            )
        if resource_type is not None and resource_id is None:
            raise HTTPException(
                status_code=400,
                detail="resource_id is required when resource_type is set",
            )
        if resource_id is not None and resource_type is None:
            raise HTTPException(
                status_code=400,
                detail="resource_type is required when resource_id is set",
            )
        if include_attachments and resource_type != "project":
            raise HTTPException(
                status_code=400,
                detail="include_attachments is only valid for project tokens",
            )

        # A date-only value (e.g. "2026-05-05") is parsed as midnight 00:00:00,
        # which would expire the token at the very start of that day.  Normalize
        # it to end-of-day so the token remains valid throughout the named day.
        if (
            expires_at is not None
            and expires_at.hour == 0
            and expires_at.minute == 0
            and expires_at.second == 0
        ):
            expires_at = expires_at.replace(hour=23, minute=59, second=59)

        token_value = secrets.token_urlsafe(32)
        token_hash = bcrypt.hash(token_value)
        token_prefix = token_value[:8]

        def _create_token(
            session: Session,
            user_id: int,
            token_hash: str,
            token_prefix: str,
            desc: Optional[str],
            scope: str,
            resource_type: Optional[str],
            resource_id: Optional[int],
            expires_at: Optional[datetime],
            include_attachments: bool,
            watermark: bool,
        ):
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            token = UserToken(
                user_id=user_id,
                library_uuid=self.active_library_uuid(),
                token_hash=token_hash,
                token_prefix=token_prefix,
                created_at=datetime.utcnow(),
                description=desc,
                scope=scope,
                resource_type=resource_type,
                resource_id=resource_id,
                expires_at=expires_at,
                include_attachments=include_attachments,
                watermark=watermark,
            )
            session.add(token)
            session.commit()
            session.refresh(token)
            return token

        token = self._db.run_task(
            _create_token,
            user_id,
            token_hash,
            token_prefix,
            description,
            scope,
            resource_type,
            resource_id,
            expires_at,
            include_attachments,
            watermark,
            priority=DBPriority.IMMEDIATE,
        )

        return {
            "token": token_value,
            "token_id": token.id,
            "scope": token.scope,
            "resource_type": token.resource_type,
            "resource_id": token.resource_id,
            "expires_at": token.expires_at,
            "include_attachments": token.include_attachments,
            "watermark": token.watermark,
        }

    def list_tokens(self, request: Request):
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403, detail="Scoped tokens cannot list tokens"
            )

        def fetch_tokens(session: Session, user_id: int):
            tokens = session.exec(
                select(UserToken)
                .where(UserToken.user_id == user_id)
                .order_by(UserToken.created_at.desc())
            ).all()
            result = []
            for token in tokens:
                resource_name = None
                if token.resource_type == "character" and token.resource_id is not None:
                    obj = session.get(Character, token.resource_id)
                    resource_name = obj.name if obj else None
                elif (
                    token.resource_type == "picture_set"
                    and token.resource_id is not None
                ):
                    obj = session.get(PictureSet, token.resource_id)
                    resource_name = obj.name if obj else None
                elif token.resource_type == "project" and token.resource_id is not None:
                    obj = session.get(Project, token.resource_id)
                    resource_name = obj.name if obj else None
                result.append(
                    {
                        "id": token.id,
                        "description": token.description,
                        "scope": token.scope,
                        "resource_type": token.resource_type,
                        "resource_id": token.resource_id,
                        "resource_name": resource_name,
                        "expires_at": token.expires_at,
                        "created_at": token.created_at,
                        "last_used_at": token.last_used_at,
                        "include_attachments": token.include_attachments,
                        "watermark": token.watermark,
                    }
                )
            return result

        return self._db.run_task(fetch_tokens, user_id, priority=DBPriority.IMMEDIATE)

    def delete_token(self, request: Request, token_id: int):
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        def remove_token(session: Session, user_id: int, token_id: int):
            token = session.get(UserToken, token_id)
            if token is None or token.user_id != user_id:
                raise HTTPException(status_code=404, detail="Token not found")
            # Read the public id out before the delete: it is the key the
            # session maps are built on, and after the commit the row is gone.
            public_id = token.public_id
            session.delete(token)
            session.commit()
            return public_id

        removed_public_id = self._db.run_task(
            remove_token, user_id, token_id, priority=DBPriority.IMMEDIATE
        )
        # End any session this token created, so access does not outlive the
        # credential. Taken before (never inside) the token-cache lock, so the
        # two locks are only ever held one at a time.
        dropped = (
            self._drop_sessions_for_tokens((removed_public_id,))
            if removed_public_id is not None
            else 0
        )
        if dropped:
            self._logger.info(
                "Ended %d session(s) created by removed token %s.",
                dropped,
                removed_public_id,
            )
        # Clear the token cache. The cache is keyed on a digest of the raw
        # token *value*, and nothing maps a token row back to that digest -
        # ``public_id`` does not supply it either, so precise eviction would
        # need a second index maintained at insert time (see §16.5). Flushing
        # everything is coarse but correct. The delete has already committed
        # above (run_task is synchronous), so every subsequent lookup re-reads
        # the database and 401s.
        self._flush_token_cache()
        return {"status": "success", "deleted_id": token_id}

    def update_token(self, request: Request, token_id: int, watermark: bool):
        """Update mutable fields on an existing token (currently: watermark)."""
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403, detail="Scoped tokens cannot modify tokens"
            )

        def _update(session: Session, user_id: int, token_id: int, watermark: bool):
            token = session.get(UserToken, token_id)
            if token is None or token.user_id != user_id:
                raise HTTPException(status_code=404, detail="Token not found")
            token.watermark = watermark
            session.add(token)
            session.commit()
            session.refresh(token)
            return token

        token = self._db.run_task(
            _update, user_id, token_id, watermark, priority=DBPriority.IMMEDIATE
        )
        # Flush the token cache so the updated watermark setting takes effect immediately.
        self._flush_token_cache()
        return {"status": "success", "id": token.id, "watermark": token.watermark}

    def revoke_tokens_for_resource(
        self,
        request: Request,
        resource_type: str,
        resource_id: int,
    ):
        """Delete all tokens scoped to a specific resource owned by the user."""
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        # Only the owner (full session or ALL-scope bearer) may delete tokens.
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403, detail="Scoped tokens cannot revoke tokens"
            )

        def _revoke(
            session: Session, user_id: int, rt: str, rid: int
        ) -> tuple[int, list[str]]:
            tokens = session.exec(
                select(UserToken).where(
                    UserToken.user_id == user_id,
                    UserToken.resource_type == rt,
                    UserToken.resource_id == rid,
                )
            ).all()
            # Public ids, collected before the delete - they key the session
            # maps, and the rows are gone once this commits.
            public_ids = [t.public_id for t in tokens if t.public_id is not None]
            for t in tokens:
                session.delete(t)
            session.commit()
            return len(tokens), public_ids

        deleted_count, deleted_public_ids = self._db.run_task(
            _revoke, user_id, resource_type, resource_id, priority=DBPriority.IMMEDIATE
        )
        # End any session these tokens created (see delete_token). Sessions from
        # other credentials are untouched. Taken outside the token-cache lock.
        dropped = self._drop_sessions_for_tokens(deleted_public_ids)
        if dropped:
            self._logger.info(
                "Ended %d session(s) created by tokens revoked for %s %s.",
                dropped,
                resource_type,
                resource_id,
            )
        self._flush_token_cache()
        return {"status": "success", "deleted_count": deleted_count}

    def get_shared_resource_ids(self, request: Request, resource_type: str):
        """Return the set of resource_ids for which the user has active READ tokens."""
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(status_code=403, detail="Not allowed for scoped tokens")

        def _fetch(session: Session, user_id: int, rt: str) -> list[int]:
            now = datetime.utcnow()
            tokens = session.exec(
                select(UserToken).where(
                    UserToken.user_id == user_id,
                    UserToken.resource_type == rt,
                    UserToken.scope == "READ",
                )
            ).all()
            return [
                t.resource_id
                for t in tokens
                if t.resource_id is not None
                and (t.expires_at is None or t.expires_at > now)
            ]

        ids = self._db.run_task(
            _fetch, user_id, resource_type, priority=DBPriority.IMMEDIATE
        )
        return {"resource_type": resource_type, "ids": ids}

    def batch_get_shared_picture_ids(self, request: Request, picture_ids: list[int]):
        """Given a list of picture_ids, return which ones have active READ tokens."""
        self.ensure_secure_when_required(request)
        user_id = self.require_user_id(request)

        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(status_code=403, detail="Not allowed for scoped tokens")

        if not picture_ids:
            return {"shared_ids": []}

        def _fetch(session: Session, user_id: int, ids: list[int]) -> list[int]:
            now = datetime.utcnow()
            id_set = set(ids)
            tokens = session.exec(
                select(UserToken).where(
                    UserToken.user_id == user_id,
                    UserToken.resource_type == "picture",
                    UserToken.scope == "READ",
                    UserToken.resource_id.in_(list(id_set)),
                )
            ).all()
            return [
                t.resource_id
                for t in tokens
                if t.resource_id is not None
                and (t.expires_at is None or t.expires_at > now)
            ]

        shared = self._db.run_task(
            _fetch, user_id, picture_ids, priority=DBPriority.IMMEDIATE
        )
        return {"shared_ids": shared}

    def check_session(self, request: Request) -> JSONResponse:
        session_id = request.cookies.get("session_id")
        if session_id and session_id in self.active_session_ids:
            return JSONResponse(content={"status": "success"})
        raise HTTPException(status_code=401, detail="Invalid session")

    def login(self, request, http_request: Optional[Request] = None) -> Response:
        self._require_local_for_write(http_request)
        remaining = self._login_lockout_until - time.monotonic()
        if remaining > 0:
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts. Try again later.",
                headers={"Retry-After": str(int(remaining) + 1)},
            )
        try:
            response = self._do_login(request, http_request)
        except HTTPException as exc:
            if exc.status_code == 401:
                self._failed_login_attempts += 1
                if self._failed_login_attempts >= 5:
                    self._login_lockout_until = time.monotonic() + 60
                    self._logger.warning(
                        "5 failed login attempts - locked out for 60s."
                    )
                else:
                    self._logger.warning(
                        "Login failure #%d.", self._failed_login_attempts
                    )
            raise
        if self._failed_login_attempts:
            self._logger.info(
                "Login succeeded after %d failure(s). Resetting lockout.",
                self._failed_login_attempts,
            )
        self._failed_login_attempts = 0
        self._login_lockout_until = 0.0
        return response

    def _do_login(self, request, http_request: Optional[Request] = None) -> Response:
        if not request.token and self._server_config.get(
            "disable_password_auth", False
        ):
            raise HTTPException(
                status_code=403,
                detail="Password authentication is disabled on this server.",
            )

        session_token_public_id: Optional[str] = None
        session_library_uuid: Optional[str] = None
        if request.token:
            user = self.get_user()
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            def fetch_tokens(session: Session, user_id: int):
                return session.exec(
                    select(UserToken).where(UserToken.user_id == user_id)
                ).all()

            tokens = self._db.run_task(
                fetch_tokens, user.id, priority=DBPriority.IMMEDIATE
            )
            matched_token = None
            for token in tokens:
                if bcrypt.verify(request.token, token.token_hash):
                    matched_token = token
                    break
            # Signing in requires a credential with full, unscoped owner
            # authority: an unexpired ALL-scope token with no resource
            # restriction. This is the rule require_unscoped_owner and the
            # request middleware apply (is_unscoped_owner_token /
            # is_token_expired). A narrower or expired token is refused with
            # the same status and body as an unrecognised one, so the response
            # does not tell the two apart.
            if (
                matched_token is None
                or is_token_expired(matched_token)
                or not is_unscoped_owner_token(matched_token)
            ):
                if matched_token is not None:
                    self._logger.warning(
                        "Refused a session for token id %s: logging in requires "
                        "an unexpired, unrestricted owner token (scope=%s, "
                        "resource_type=%s, expires_at=%s).",
                        matched_token.id,
                        matched_token.scope,
                        matched_token.resource_type,
                        matched_token.expires_at,
                    )
                raise HTTPException(status_code=401, detail="Invalid token")
            if matched_token.public_id is None:
                # Every row is given a public_id by the model's default factory
                # and every pre-existing row was backfilled by migration 0090,
                # so this is only reachable on a database that never ran it. The
                # session-to-token link is what keeps a session's lifetime
                # inside its credential's, and it cannot be made on an integer
                # id that is reissued after deletion - so refuse the sign-in
                # rather than issue a session that revocation cannot reach.
                self._logger.error(
                    "Refused a session for token id %s: the token has no "
                    "public_id, so its session could not be revoked with it. "
                    "The database is behind migration 0090.",
                    matched_token.id,
                )
                raise HTTPException(status_code=401, detail="Invalid token")
            session_token_public_id = matched_token.public_id
            session_library_uuid = matched_token.library_uuid
            if session_library_uuid != self.active_library_uuid():
                raise HTTPException(
                    status_code=403,
                    detail="This token belongs to a library that is not currently active.",
                )

            def update_token_last_used(session: Session, token_id: int):
                db_token = session.get(UserToken, token_id)
                if db_token is None:
                    return None
                db_token.last_used_at = datetime.utcnow()
                session.add(db_token)
                session.commit()
                return db_token

            self._db.run_task(
                update_token_last_used,
                matched_token.id,
                priority=DBPriority.IMMEDIATE,
            )

            response = JSONResponse(content={"message": "Login successful."})
        else:
            if not request.username or not request.password:
                raise HTTPException(
                    status_code=400,
                    detail="Username and password are required",
                )

            user = self.get_user() or self.ensure_user()
            if not user.username or not user.password_hash:
                # First-owner registration: claiming the empty owner account by
                # setting its credentials. This must be loopback-only so a LAN
                # device cannot claim the account before the real owner does.
                self._require_loopback_for_registration(http_request)
                self._validate_bcrypt_password_length(request.password)
                hashed_password = bcrypt.hash(request.password)

                def set_credentials(session: Session):
                    db_user = session.exec(select(User)).first()
                    if db_user is None:
                        db_user = User(max_vram_gb=default_max_vram_gb())
                    # Re-check unclaimed-ness INSIDE the transaction, mirroring
                    # claim_owner_from_env: two concurrent loopback claims must
                    # not overwrite each other - first commit wins, the loser
                    # falls through to normal credential verification.
                    if db_user.username or db_user.password_hash:
                        return db_user
                    db_user.username = request.username
                    db_user.password_hash = hashed_password
                    session.add(db_user)
                    session.commit()
                    session.refresh(db_user)
                    return db_user

                user = self._db.run_task(set_credentials, priority=DBPriority.IMMEDIATE)
                self.user = user
                self.username = user.username
                self.password_hash = user.password_hash
                if user.password_hash != hashed_password:
                    # Lost the claim race: another loopback client committed
                    # first. Do not issue a session for this request; the
                    # caller must log in against the winning credentials.
                    self._logger.warning(
                        "First-owner claim raced; keeping the first claim."
                    )
                    raise HTTPException(
                        status_code=401, detail="Invalid username or password"
                    )
                response = JSONResponse(
                    content={"message": "Username and password set successfully."}
                )
            else:
                if request.username != user.username:
                    raise HTTPException(status_code=401, detail="Invalid username")
                self._validate_bcrypt_password_length(request.password)
                if not bcrypt.verify(request.password, user.password_hash):
                    raise HTTPException(status_code=401, detail="Invalid password")
                response = JSONResponse(content={"message": "Login successful."})

        session_id = str(uuid.uuid4())
        if not user or user.id is None:
            raise HTTPException(status_code=500, detail="User not found")
        self._register_session(
            session_id,
            user.id,
            token_public_id=session_token_public_id,
            token_library_uuid=session_library_uuid,
        )
        if session_token_public_id is not None:
            self._confirm_session_token_still_exists(
                session_id, session_token_public_id
            )

        cookie_samesite = self._server_config.get("cookie_samesite", "Lax")
        cookie_secure = self._server_config.get("cookie_secure", False)
        if cookie_samesite == "None" and not cookie_secure:
            self._logger.warning(
                "cookie_samesite=None requires cookie_secure=True for cross-site cookies to work in browsers."
            )
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            samesite=cookie_samesite,
            secure=bool(cookie_secure),
        )
        return response

    def get_session_context(self, request: Request) -> dict:
        """Return the access scope for the current request.

        Used by the frontend to determine what the current token/session allows.
        """
        token_scope = getattr(request.state, "token_scope", None)
        user_id = getattr(request.state, "auth_user_id", None) or self.get_user_id(
            request
        )
        if token_scope is None:
            return {
                "is_owner": user_id is not None,
                "scope": "ALL",
                "resource_type": None,
                "resource_id": None,
                "expires_at": None,
            }
        return {
            "is_owner": False,
            "scope": token_scope.scope,
            "resource_type": token_scope.resource_type,
            "resource_id": token_scope.resource_id,
            "expires_at": token_scope.expires_at,
            "include_attachments": token_scope.include_attachments,
        }

    def check_registration(self) -> JSONResponse:
        user = self.get_user()
        if not user or not user.username or not user.password_hash:
            return JSONResponse(content={"needs_registration": True})
        return JSONResponse(content={"needs_registration": False})

    def logout(self, response: Response, request: Request):
        session_id = request.cookies.get("session_id")
        if session_id:
            self._forget_session(session_id)
        response.delete_cookie("session_id", path="/")
        return {"message": "Logged out successfully."}

    async def auth_middleware(
        self, request: Request, call_next, allow_origins, allow_origin_regex
    ):
        """Admit credential-bearing traffic and hold its lease through response.

        Admission happens before credential lookup, including for public auth
        endpoints such as login/check-session and ``/share/{token_slug}``.
        Otherwise a request could begin token or owner/resource-row verification
        just before the restore closes the gate, miss the boolean check, and
        enter its handler after the database swap.
        """
        requires_restore_admission = any(
            request.url.path.startswith(prefix) for prefix in RESTORE_ADMISSION_PREFIXES
        )
        lease = (
            self._acquire_restore_http_lease() if requires_restore_admission else None
        )
        if requires_restore_admission and lease is None:
            return self._restore_unavailable_response()
        if lease is not None:
            request.state.restore_admission_lease = lease
        try:
            return await self._auth_middleware_admitted(
                request, call_next, allow_origins, allow_origin_regex
            )
        finally:
            self._release_restore_http_lease(lease)

    async def _auth_middleware_admitted(
        self, request: Request, call_next, allow_origins, allow_origin_regex
    ):
        """Run the existing authentication policy under an admission lease."""

        if request.method == "OPTIONS":
            return await call_next(request)

        if not is_auth_excluded_path(request.url.path):
            # Only enforce authentication for API routes.  Non-API paths (e.g.
            # SPA routes like /character/5) are served as static HTML so the
            # frontend can load and handle auth itself - the login screen is
            # shown and, after a successful login, Vue router restores the
            # original URL automatically.
            if not any(
                request.url.path.startswith(prefix) for prefix in AUTH_API_PREFIXES
            ):
                return await call_next(request)

            session_id = request.cookies.get("session_id")
            user_id = self.active_session_ids.get(session_id) if session_id else None

            # The seeded Electron desktop owner session is loopback-only: the
            # window talks to the backend over 127.0.0.1, so this high-privilege
            # session must never grant access on the optional external listener
            # (even though the cookie is scoped to the loopback origin and so is
            # not normally sent there - this is the fail-closed backstop). Pin to
            # loopback, not the broad LAN: the external listener is reached over
            # the LAN, so an is_local_ip check would leave the whole LAN open.
            # Drop it for non-loopback clients and let normal token auth take over.
            if (
                user_id is not None
                and session_id == self._desktop_session_token
                and not is_loopback_ip(self._get_real_client_ip(request))
            ):
                self._logger.warning(
                    "Rejected desktop owner session from non-local IP %s.",
                    self._get_real_client_ip(request),
                )
                user_id = None

            if user_id is not None:
                # Cookie session - full owner access, no scope restriction
                request.state.auth_user_id = user_id
                session_library_uuid = self._library_uuid_by_session.get(session_id)
                if session_library_uuid is not None:
                    request.state.session_library_uuid = session_library_uuid
                    lease = getattr(request.state, "library_lease", None)
                    if lease is not None and session_library_uuid != lease.library_uuid:
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Token belongs to a different library"},
                        )
            else:
                # Try Bearer token, then fall back to ?token= query param
                matched_token: Optional[UserToken] = None
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token_value = auth_header[len("Bearer ") :]
                    matched_token = self._token_from_value(token_value)
                if matched_token is None:
                    matched_token = self._token_from_query_param(request)

                if matched_token is not None:
                    lease = getattr(request.state, "library_lease", None)
                    if (
                        lease is not None
                        and matched_token.library_uuid != lease.library_uuid
                    ):
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Token belongs to a different library"},
                        )
                    user = self.get_user()
                    user_id = user.id if user else None
                    if user_id:
                        # Block ALL-scope tokens from non-local IPs when require_local_for_write is enabled.
                        if matched_token.scope == "ALL" and self._server_config.get(
                            "require_local_for_write", True
                        ):
                            client_ip = self._get_real_client_ip(request)
                            if not is_local_ip(client_ip):
                                return JSONResponse(
                                    status_code=403,
                                    content={
                                        "detail": "Full access is restricted to local network connections. Use a share token for remote access."
                                    },
                                )
                        request.state.auth_user_id = user_id
                        # Stash the matched token so route-level helpers
                        # (e.g. require_unscoped_owner) can introspect its
                        # resource_type - token_scope is only populated for
                        # non-ALL scopes, so an ALL+resource_type token would
                        # otherwise look indistinguishable from a cookie session.
                        request.state.matched_token = matched_token
                        # Defensive (F3 residual): create_token now refuses to
                        # mint an ALL+resource_type token, but a legacy or
                        # snapshot-restored one would have token_scope is None
                        # and thus bypass every object-scope guard while looking
                        # like an owner. Reject it fail-closed - the shape is
                        # invalid and unreachable through any supported flow.
                        if (
                            matched_token.scope == "ALL"
                            and matched_token.resource_type is not None
                        ):
                            return JSONResponse(
                                status_code=403,
                                content={
                                    "detail": (
                                        "Misconfigured token: an ALL-scope token "
                                        "cannot be restricted to a resource."
                                    )
                                },
                            )
                        if matched_token.scope != "ALL":
                            request.state.token_scope = TokenScope(
                                scope=matched_token.scope,
                                resource_type=matched_token.resource_type,
                                resource_id=matched_token.resource_id,
                                expires_at=matched_token.expires_at,
                                include_attachments=matched_token.include_attachments,
                                watermark=bool(
                                    getattr(matched_token, "watermark", False)
                                ),
                            )
                            request.state.token_id = matched_token.id
                            # Guest sessions and guest scores live in the vault
                            # and name their token by public id, because the
                            # token itself is in the hub (see GuestSession).
                            request.state.token_public_id = matched_token.public_id
                            # Resolve the guest session cookie for READ-scoped tokens.
                            # The cookie value is a server-generated cookie_token, NOT
                            # the client-supplied session_id.  We look up the DB row by
                            # cookie_token to get the real session_id; this ensures no
                            # user-supplied value is ever trusted directly from the cookie.
                            raw_gs = request.cookies.get("guest_session", "")
                            if (
                                lease is not None
                                and raw_gs
                                and re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", raw_gs)
                            ):
                                from pixlstash.db_models.guest_session import (
                                    GuestSession,
                                )

                                def _lookup_by_token(
                                    session: Session, tok: str = raw_gs
                                ):
                                    return session.exec(
                                        select(GuestSession).where(
                                            GuestSession.cookie_token == tok
                                        )
                                    ).first()

                                # Read path, not the writer queue (issue #651).
                                # Unlike token auth this lookup is never cached:
                                # every request re-reads GuestSession by
                                # cookie_token, so a deleted/rotated row stops
                                # resolving on the very next request with no
                                # invalidation step to get wrong. The guest
                                # session's own tracking (record_guest_activity,
                                # the 30-day expiry and the eviction cap) is
                                # in-memory and untouched by this change.
                                # Reads the vault, not the hub: a guest session
                                # belongs to one library.
                                gs = lease.db.run_immediate_read_task(_lookup_by_token)
                                if gs is not None:
                                    request.state.guest_session_id = gs.session_id
                                    self.record_guest_activity(gs.session_id)
                                    self._logger.info(
                                        "[guest-scores] Resolved guest_session cookie for %s → session_id=%r",
                                        request.url.path,
                                        gs.session_id,
                                    )
                                else:
                                    request.state.guest_session_id = None
                                    self._logger.info(
                                        "[guest-scores] No session found for guest_session cookie at %s",
                                        request.url.path,
                                    )
                            else:
                                request.state.guest_session_id = None
                                self._logger.info(
                                    "[guest-scores] No valid guest_session cookie for %s (raw=%r, all_cookies=%r)",
                                    request.url.path,
                                    raw_gs,
                                    list(request.cookies.keys()),
                                )

                if user_id is None:
                    self._logger.error(
                        "Invalid session_id. It has expired and the client needs to log in again. When trying to access %s",
                        request.url.path,
                    )
                    origin = request.headers.get("origin")
                    headers = {
                        "Access-Control-Allow-Credentials": "true",
                    }
                    if origin and (
                        origin in allow_origins
                        or (allow_origin_regex and re.match(allow_origin_regex, origin))
                    ):
                        headers["Access-Control-Allow-Origin"] = origin

                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Not authenticated"},
                        headers=headers,
                    )

            # Block write operations for every token that is not explicitly
            # write-enabled. Fail closed: an unrecognised scope is read-only.
            # Paths in READ_SAFE_POST_PATHS use POST for body size but are semantically read-only.
            token_scope = getattr(request.state, "token_scope", None)
            if token_scope is not None:
                if token_scope.scope != "READ":
                    # ``READ`` is the only scope ``create_token`` will mint that
                    # reaches here, so anything else is a misconfigured row or a
                    # forgery. Logged **before** the write-enabled test, not
                    # inside its else-branch: a forged ``WRITE`` is the one such
                    # row that actually writes, and it would otherwise be the
                    # only one to pass through silently.
                    self._logger.warning(
                        "Token %s carries an unmintable scope %r (write-enabled: "
                        "%s) for %s %s",
                        getattr(request.state, "token_public_id", None),
                        token_scope.scope,
                        token_scope.scope in WRITE_ENABLED_SCOPES,
                        request.method,
                        request.url.path,
                    )
                if token_scope.scope not in WRITE_ENABLED_SCOPES:
                    if (
                        request.method not in ("GET", "HEAD", "OPTIONS")
                        and request.url.path not in READ_SAFE_POST_PATHS
                    ):
                        return JSONResponse(
                            status_code=403,
                            content={"detail": "Token is read-only"},
                        )
                # Filesystem and user-settings reads are barred to every scoped
                # token, write-enabled or not: the restriction is about what a
                # narrow credential may *see*, not about what it may change.
                if (
                    request.method == "GET"
                    and request.url.path in READ_BLOCKED_GET_PATHS
                ):
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Token is read-only"},
                    )

        return await call_next(request)
