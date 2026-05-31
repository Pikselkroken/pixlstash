"""HTTP-layer auth tests for the snapshot routes.

Every snapshot route is guarded by ``require_unscoped_owner``, which rejects
both READ-scoped tokens *and* ALL-scope tokens narrowed by ``resource_type``.
A regression in that helper (e.g. forgetting one of the two checks) would
otherwise pass every other test in the suite while silently exposing the
whole vault to share tokens — this file is the dedicated regression guard.
"""

import json
import tempfile

import pytest
from fastapi.testclient import TestClient

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


def _make_picture_scoped_all_token(client, picture_id: int):
    """Make an ALL-scope token narrowed to a single picture (share-style)."""
    r = client.post(
        f"{API}/users/me/token",
        json={
            "description": "picture-scoped",
            "scope": "ALL",
            "resource_type": "picture",
            "resource_id": picture_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


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
# ALL-scope but narrowed-by-resource-type tokens must also be rejected
# ---------------------------------------------------------------------------


def test_picture_scoped_all_token_rejected_on_snapshot_routes():
    """``require_unscoped_owner`` must also reject ALL-scope tokens whose
    ``resource_type`` is non-null — those are share-style tokens narrowed to
    a single resource and must not see snapshot data."""
    tmp, server, client = _setup_server_with_owner_session()
    try:
        # Need a picture to scope the token to.
        client.post(f"{API}/snapshots", json={"label": "seed-picture-for-scoping"})

        # If POST /snapshots fails because there are no pictures, that's fine —
        # we only need a token scoped to a (real or synthesized) picture id.
        from pixlstash.db_models import Picture

        def _add(session):
            pic = Picture(file_path="t.jpg", filename="t.jpg")
            session.add(pic)
            session.commit()
            session.refresh(pic)
            return pic.id

        pic_id = server.vault.db.run_task(_add)

        scoped_token = _make_picture_scoped_all_token(client, pic_id)
        r = TestClient(server.api).get(
            f"{API}/snapshots",
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        assert r.status_code == 403, (
            f"Picture-scoped ALL token must be rejected by snapshot routes; "
            f"got {r.status_code}: {r.text}"
        )
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


# ---------------------------------------------------------------------------
# Parametrized: a picture-scoped ALL token is rejected by EVERY read-shaped
# snapshot endpoint, not just GET /snapshots (issue #5).
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
    """``require_unscoped_owner`` must reject a picture-scoped ALL token on
    every read-shaped snapshot route, before any snapshot lookup."""
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
        scoped_token = _make_picture_scoped_all_token(client, pic_id)

        bare = TestClient(server.api)
        r = getattr(bare, method)(
            path, headers={"Authorization": f"Bearer {scoped_token}"}
        )
        assert r.status_code == 403, (
            f"{method.upper()} {path} must reject a picture-scoped ALL token "
            f"with 403; got {r.status_code}: {r.text}"
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
