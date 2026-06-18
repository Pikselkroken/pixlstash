"""HTTP-layer auth tests for the snapshot routes.

Snapshot routes are owner-only, and two independent layers must hold:

* ``require_unscoped_owner`` (the route dependency) rejects READ-scoped tokens,
  which do reach the route.
* The auth middleware fail-closed-rejects a forged ``ALL``+``resource_type``
  token *before* the route runs. ``create_token`` refuses to mint that shape,
  but a malicious / legacy / snapshot-restored row could still carry it, and it
  would otherwise leave ``token_scope is None`` and read as a full owner — the
  F1/F3 footgun.

A regression in either layer would pass every other test in the suite while
silently exposing the whole vault to share tokens — this file is the dedicated
regression guard.
"""

import json
import secrets
import tempfile
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from passlib.hash import bcrypt
from sqlmodel import Session, select

from pixlstash.db_models import User, UserToken
from pixlstash.server import Server

API = "/api/v1"


def _setup_server_with_owner_session():
    """Create a server with disabled background workers, log in as owner."""
    tmp = tempfile.TemporaryDirectory()
    config_path = f"{tmp.name}/server-config.json"
    with open(config_path, "w") as fh:
        json.dump({"disable_background_workers": True}, fh)
    server = Server(config_path)
    server.__enter__()
    client = TestClient(server.api, raise_server_exceptions=True)
    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
    )
    assert r.status_code == 200, r.text
    return tmp, server, client


def _make_read_token(client):
    r = client.post(
        f"{API}/users/me/token",
        json={"description": "read-only", "scope": "READ"},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _inject_picture_scoped_all_token(server, picture_id: int) -> str:
    """Forge an ``ALL``+``resource_type`` token by writing it straight to the DB.

    This is the malicious case, deliberately. ``create_token`` now *refuses* to
    mint this shape (returns 400 — see ``test_read_token_security`` and
    ``auth.create_token``), so we cannot get one through the API. But the ban at
    the mint is not the only line of defense we care about: a token row carrying
    ``scope="ALL"`` + a ``resource_type`` could still arrive via a hand-crafted
    insert by an attacker with DB access, a token minted before the ban shipped,
    or a snapshot restore of an old row. Such a token is the F1/F3 footgun — the
    middleware only builds ``request.state.token_scope`` for non-``ALL`` scopes,
    so it would leave ``token_scope is None`` and be treated as a *full owner*
    while wearing a "restricted" label.

    So we bypass the API on purpose and forge the row directly, exactly as an
    attacker (or a legacy/snapshot path) would have to, then assert the auth
    middleware rejects it fail-closed before the route ever runs. The row mirrors
    ``create_token``'s construction (``token_hash`` + ``token_prefix``) so
    ``_token_from_value`` matches it on the request. Returns the raw token value.
    """
    token_value = secrets.token_urlsafe(32)

    def _add(session: Session):
        # _token_from_value resolves the principal via get_user() == the first
        # User row, then looks up tokens by that user_id; match it so the forged
        # token is actually found on the request.
        owner = session.exec(select(User)).first()
        assert owner is not None, "owner user must exist for the forge to match"
        session.add(
            UserToken(
                user_id=owner.id,
                token_hash=bcrypt.hash(token_value),
                token_prefix=token_value[:8],
                created_at=datetime.utcnow(),
                description="forged picture-scoped ALL token (test only)",
                scope="ALL",
                resource_type="picture",
                resource_id=picture_id,
            )
        )
        session.commit()

    server.vault.db.run_task(_add)
    return token_value


# ---------------------------------------------------------------------------
# READ-scoped tokens must be rejected by every snapshot route
# ---------------------------------------------------------------------------


class TestReadTokenRejectedOnSnapshotRoutes:
    """A READ-scoped token cannot read or write any snapshot endpoint."""

    def test_list_snapshots_rejects_read_token(self):
        tmp, server, client = _setup_server_with_owner_session()
        try:
            read_token = _make_read_token(client)
            r = TestClient(server.api).get(
                f"{API}/snapshots",
                headers={"Authorization": f"Bearer {read_token}"},
            )
            assert r.status_code == 403, (
                f"READ token must not list snapshots; got {r.status_code}: {r.text}"
            )
        finally:
            server.__exit__(None, None, None)
            tmp.cleanup()

    def test_status_rejects_read_token(self):
        tmp, server, client = _setup_server_with_owner_session()
        try:
            read_token = _make_read_token(client)
            r = TestClient(server.api).get(
                f"{API}/snapshots/status",
                headers={"Authorization": f"Bearer {read_token}"},
            )
            assert r.status_code == 403
        finally:
            server.__exit__(None, None, None)
            tmp.cleanup()

    def test_preview_full_rejects_read_token(self):
        tmp, server, client = _setup_server_with_owner_session()
        try:
            read_token = _make_read_token(client)
            r = TestClient(server.api).get(
                f"{API}/snapshots/1/restore/preview",
                headers={"Authorization": f"Bearer {read_token}"},
            )
            assert r.status_code == 403, (
                "preview_full must reject READ tokens before any snapshot "
                f"lookup; got {r.status_code}: {r.text}"
            )
        finally:
            server.__exit__(None, None, None)
            tmp.cleanup()

    def test_create_snapshot_rejects_read_token(self):
        tmp, server, client = _setup_server_with_owner_session()
        try:
            read_token = _make_read_token(client)
            r = TestClient(server.api).post(
                f"{API}/snapshots",
                json={},
                headers={"Authorization": f"Bearer {read_token}"},
            )
            assert r.status_code == 403
        finally:
            server.__exit__(None, None, None)
            tmp.cleanup()


# ---------------------------------------------------------------------------
# A forged ALL+resource_type token must be rejected fail-closed
# ---------------------------------------------------------------------------


def test_picture_scoped_all_token_rejected_on_snapshot_routes():
    """A forged ``ALL``+``resource_type`` token must be rejected before any
    snapshot route runs.

    ``create_token`` refuses to mint this shape, but a malicious / legacy /
    snapshot-restored row could still carry it (see
    ``_inject_picture_scoped_all_token``). The auth middleware's fail-closed
    ``ALL``+``resource_type`` guard rejects it (403) ahead of the route's
    ``require_unscoped_owner`` — defense in depth against the footgun that would
    otherwise let it read as a full owner."""
    tmp, server, client = _setup_server_with_owner_session()
    try:
        from pixlstash.db_models import Picture

        def _add(session):
            pic = Picture(file_path="t.jpg", filename="t.jpg")
            session.add(pic)
            session.commit()
            session.refresh(pic)
            return pic.id

        pic_id = server.vault.db.run_task(_add)

        forged_token = _inject_picture_scoped_all_token(server, pic_id)
        r = TestClient(server.api).get(
            f"{API}/snapshots",
            headers={"Authorization": f"Bearer {forged_token}"},
        )
        assert r.status_code == 403, (
            f"Forged ALL+resource_type token must be rejected by snapshot routes; "
            f"got {r.status_code}: {r.text}"
        )
        # Prove it was the middleware's malformed-token guard that fired, not the
        # local-IP gate or the owner check — otherwise a regression that lets the
        # footgun through could still pass on an incidental 403.
        assert "misconfigured" in r.text.lower(), (
            f"Expected the ALL+resource_type middleware guard to fire; got: {r.text}"
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Parametrized: a forged ALL+resource_type token is rejected by EVERY
# read-shaped snapshot endpoint, not just GET /snapshots (issue #5).
# ---------------------------------------------------------------------------

# (method, path) for the read-shaped snapshot routes the prior review flagged.
# preview_resource uses a synthetic id; the auth guard must fire before any
# snapshot/resource lookup, so 403 is expected regardless of existence.
_SCOPED_REJECT_ROUTES = [
    ("get", f"{API}/snapshots"),
    ("get", f"{API}/snapshots/status"),
    ("get", f"{API}/snapshots/1/restore/preview"),
    ("get", f"{API}/snapshots/1/restore/picture/1/preview"),
]


@pytest.mark.parametrize(
    "method,path",
    _SCOPED_REJECT_ROUTES,
    ids=[p for _, p in _SCOPED_REJECT_ROUTES],
)
def test_picture_scoped_all_token_rejected_on_every_read_route(method, path):
    """A forged ``ALL``+``resource_type`` token is rejected on every read-shaped
    snapshot route by the middleware's fail-closed guard, before any snapshot
    lookup. See ``_inject_picture_scoped_all_token`` for why it is forged
    directly rather than minted."""
    tmp, server, client = _setup_server_with_owner_session()
    try:
        from pixlstash.db_models import Picture

        def _add(session):
            pic = Picture(file_path="t.jpg", filename="t.jpg")
            session.add(pic)
            session.commit()
            session.refresh(pic)
            return pic.id

        pic_id = server.vault.db.run_task(_add)
        forged_token = _inject_picture_scoped_all_token(server, pic_id)

        bare = TestClient(server.api)
        r = getattr(bare, method)(
            path, headers={"Authorization": f"Bearer {forged_token}"}
        )
        assert r.status_code == 403, (
            f"{method.upper()} {path} must reject a forged ALL+resource_type "
            f"token with 403; got {r.status_code}: {r.text}"
        )
        assert "misconfigured" in r.text.lower(), (
            f"{method.upper()} {path}: expected the ALL+resource_type middleware "
            f"guard to fire; got: {r.text}"
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@pytest.mark.parametrize(
    "method,path",
    _SCOPED_REJECT_ROUTES,
    ids=[p for _, p in _SCOPED_REJECT_ROUTES],
)
def test_read_token_rejected_on_every_read_route(method, path):
    """A plain READ-scoped token is likewise rejected on every read-shaped
    snapshot route."""
    tmp, server, client = _setup_server_with_owner_session()
    try:
        read_token = _make_read_token(client)
        bare = TestClient(server.api)
        r = getattr(bare, method)(
            path, headers={"Authorization": f"Bearer {read_token}"}
        )
        assert r.status_code == 403, (
            f"{method.upper()} {path} must reject a READ token with 403; "
            f"got {r.status_code}: {r.text}"
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Deletion rules: the latest snapshot of each GFS-scheduled kind is locked,
# older ones of the same kind are deletable (issue: "delete daily snapshots").
# ---------------------------------------------------------------------------


def test_any_daily_deletable_including_latest():
    """Any DAILY snapshot can be deleted, including the most recent one — the
    GFS scheduler simply creates a fresh snapshot for the period on its next
    pass, so nothing is locked."""
    tmp, server, client = _setup_server_with_owner_session()
    try:
        svc = server.vault.snapshot_service
        older = svc.create_snapshot("DAILY")
        newer = svc.create_snapshot("DAILY")

        # Older DAILY: deletable.
        r_old = client.delete(f"{API}/snapshots/{older.id}")
        assert r_old.status_code == 204, (
            f"Older DAILY must be deletable; got {r_old.status_code}: {r_old.text}"
        )
        assert svc.get_snapshot(older.id) is None

        # Latest DAILY: now also deletable (no longer locked).
        r_new = client.delete(f"{API}/snapshots/{newer.id}")
        assert r_new.status_code == 204, (
            f"Latest DAILY must be deletable; got {r_new.status_code}: {r_new.text}"
        )
        assert svc.get_snapshot(newer.id) is None
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


def test_manual_and_opportunistic_snapshots_deletable_via_route():
    """MANUAL and OPPORTUNISTIC snapshots are never GFS-locked — both delete
    via the route with 204."""
    tmp, server, client = _setup_server_with_owner_session()
    try:
        svc = server.vault.snapshot_service
        manual = svc.create_snapshot("MANUAL")
        opp = svc.create_snapshot("OPPORTUNISTIC")

        for cp in (manual, opp):
            r = client.delete(f"{API}/snapshots/{cp.id}")
            assert r.status_code == 204, (
                f"{cp.kind} must be deletable; got {r.status_code}: {r.text}"
            )
            assert svc.get_snapshot(cp.id) is None
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()
