"""HTTP-layer auth tests for the snapshot routes.

Every snapshot route is guarded by ``require_unscoped_owner``, which rejects
both READ-scoped tokens *and* ALL-scope tokens narrowed by ``resource_type``.
A regression in that helper (e.g. forgetting one of the two checks) would
otherwise pass every other test in the suite while silently exposing the
whole vault to share tokens — this file is the dedicated regression guard.
"""

import json
import tempfile

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
