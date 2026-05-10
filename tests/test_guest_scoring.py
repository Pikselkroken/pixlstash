"""Unit tests for the guest scoring feature.

Coverage
--------
1.  GET /pictures/guest-scores — empty response when no cookie present.
2.  POST without a READ token → 403.
3.  POST with bad session_id patterns → 400.
4.  POST with score out of range → 400.
5.  POST with non-integer score value → 400.
6.  POST with more than 500 score entries → 400.
7.  POST as new guest session → 200, score persisted, in-memory tracker updated.
8.  POST as returning session → score upserted, last_active_at refreshed.
9.  GET after accept → scores returned by session cookie.
10. Cookie set when set_cookie=True; NOT set when set_cookie=False.
11. Concurrent session limit: new session refused with 503 when active count at cap.
12. Returning sessions are never blocked by the concurrent limit.
13. FIFO eviction: oldest session deleted when stored cap reached.
14. SCORE sort uses guest scores via COALESCE when guest_session_id present.
15. SCORE sort falls back to picture.score for pictures without a guest score.
16. AuthService.record_guest_activity / count_active_guest_sessions.
"""

import io
import json
import tempfile
import time
from typing import Optional
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from pixlstash.auth import AuthService
from pixlstash.db_models.guest_score import GuestScore
from pixlstash.db_models.guest_session import GuestSession
from pixlstash.server import Server

API = "/api/v1"
_SID = "test-session-id-0001"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_png_bytes(width: int = 16, height: int = 16) -> bytes:
    img = Image.new("RGB", (width, height), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _setup(
    tmp: str,
    *,
    guest_max_stored_sessions: int = 1000,
    guest_max_concurrent_sessions: int = 100,
):
    """Create a Server, log in as owner, return (server, owner_client, read_token)."""
    config_path = f"{tmp}/server-config.json"
    with open(config_path, "w") as fh:
        json.dump(
            {
                "guest_max_stored_sessions": guest_max_stored_sessions,
                "guest_max_concurrent_sessions": guest_max_concurrent_sessions,
            },
            fh,
        )
    server = Server(config_path)
    client = TestClient(server.api, raise_server_exceptions=True)
    r = client.post(f"{API}/login", json={"username": "owner", "password": "pass1234"})
    assert r.status_code == 200, r.text

    r = client.post(
        f"{API}/users/me/token",
        json={"description": "guest token", "scope": "READ"},
    )
    assert r.status_code == 200, r.text
    read_token = r.json()["token"]
    return server, client, read_token


def _import_picture(owner_client) -> int:
    """Upload a tiny PNG and return its picture id."""
    png = _make_png_bytes()
    resp = owner_client.post(
        f"{API}/pictures/import",
        files=[("file", ("test.png", png, "image/png"))],
    )
    assert resp.status_code == 200, resp.text
    # Poll until import task completes
    task_id = resp.json()["task_id"]
    for _ in range(50):
        s = owner_client.get(
            f"{API}/pictures/import/status", params={"task_id": task_id}
        )
        if s.json().get("status") in ("completed", "failed"):
            break
        time.sleep(0.1)
    pics = owner_client.get(f"{API}/pictures").json()
    assert pics, "No pictures after import"
    return pics[0]["id"]


def _read_client(server, read_token, *, cookies: Optional[dict] = None) -> TestClient:
    """Return a TestClient that sends the READ bearer token on every request."""
    c = TestClient(
        server.api,
        raise_server_exceptions=True,
        cookies=cookies or {},
        headers={"Authorization": f"Bearer {read_token}"},
    )
    return c


# ---------------------------------------------------------------------------
# 1. GET with no cookie → empty scores
# ---------------------------------------------------------------------------


def test_get_guest_scores_no_cookie():
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, read_token = _setup(tmp)
        try:
            rc = _read_client(server, read_token)
            r = rc.get(f"{API}/pictures/guest-scores")
            assert r.status_code == 200, r.text
            assert r.json() == {"scores": {}}
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 2. POST without a READ token → 403
# ---------------------------------------------------------------------------


def test_post_requires_read_token():
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, _ = _setup(tmp)
        try:
            # Owner cookie session → not a READ token → should be 403
            r = owner_client.post(
                f"{API}/pictures/guest-scores",
                json={"session_id": _SID, "set_cookie": False, "scores": {}},
            )
            assert r.status_code == 403, r.text
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 3. POST — bad session_id patterns → 400
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_sid",
    [
        "",  # empty
        "a" * 65,  # too long
        "has spaces here",  # spaces not allowed
        "has/slash",  # slash not allowed
        "has@at",  # @ not allowed
    ],
)
def test_post_bad_session_id(bad_sid):
    with tempfile.TemporaryDirectory() as tmp:
        server, _, read_token = _setup(tmp)
        try:
            rc = _read_client(server, read_token)
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={"session_id": bad_sid, "set_cookie": False, "scores": {}},
            )
            assert r.status_code == 400, (
                f"Expected 400 for {bad_sid!r}, got {r.status_code}: {r.text}"
            )
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 4. POST — score out of range → 400
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_score", [-1, 6, 100])
def test_post_score_out_of_range(bad_score):
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, read_token = _setup(tmp)
        try:
            pic_id = _import_picture(owner_client)
            rc = _read_client(server, read_token)
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": _SID,
                    "set_cookie": False,
                    "scores": {str(pic_id): bad_score},
                },
            )
            assert r.status_code == 400, (
                f"Expected 400 for score {bad_score}, got {r.status_code}: {r.text}"
            )
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 5. POST — non-integer score value → 400
# ---------------------------------------------------------------------------


def test_post_non_integer_score():
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, read_token = _setup(tmp)
        try:
            pic_id = _import_picture(owner_client)
            rc = _read_client(server, read_token)
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": _SID,
                    "set_cookie": False,
                    "scores": {str(pic_id): 3.5},
                },
            )
            assert r.status_code == 400, r.text
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 6. POST — more than 500 entries → 400
# ---------------------------------------------------------------------------


def test_post_too_many_scores():
    with tempfile.TemporaryDirectory() as tmp:
        server, _, read_token = _setup(tmp)
        try:
            rc = _read_client(server, read_token)
            scores = {str(i): 3 for i in range(1, 502)}
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={"session_id": _SID, "set_cookie": False, "scores": scores},
            )
            assert r.status_code == 400, r.text
            assert "500" in r.json()["detail"]
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 7. Successful POST — score persisted, in-memory tracker updated
# ---------------------------------------------------------------------------


def test_post_new_session_persists_score():
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, read_token = _setup(tmp)
        try:
            pic_id = _import_picture(owner_client)
            rc = _read_client(server, read_token)
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": _SID,
                    "set_cookie": False,
                    "scores": {str(pic_id): 4},
                },
            )
            assert r.status_code == 200, r.text
            assert r.json() == {"ok": True}

            # Score persisted in DB
            def check(session):
                return session.exec(
                    select(GuestScore).where(GuestScore.session_id == _SID)
                ).all()

            rows = server.vault.db.run_immediate_read_task(check)
            assert len(rows) == 1
            assert rows[0].picture_id == pic_id
            assert rows[0].score == 4

            # In-memory tracker knows this session is active
            assert server.auth.count_active_guest_sessions() >= 1
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 8. Returning session — score upserted, last_active_at refreshed
# ---------------------------------------------------------------------------


def test_post_returning_session_upserts():
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, read_token = _setup(tmp)
        try:
            pic_id = _import_picture(owner_client)
            rc = _read_client(server, read_token)

            # First submission
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": _SID,
                    "set_cookie": False,
                    "scores": {str(pic_id): 2},
                },
            )
            assert r.status_code == 200, r.text

            def get_last_active(session):
                return session.get(GuestSession, _SID).last_active_at

            t1 = server.vault.db.run_immediate_read_task(get_last_active)

            # Small sleep to ensure a measurable time difference
            time.sleep(0.05)

            # Second submission — change score
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": _SID,
                    "set_cookie": False,
                    "scores": {str(pic_id): 5},
                },
            )
            assert r.status_code == 200, r.text

            t2 = server.vault.db.run_immediate_read_task(get_last_active)

            # Score updated
            def get_score(session):
                row = session.exec(
                    select(GuestScore).where(
                        GuestScore.session_id == _SID,
                        GuestScore.picture_id == pic_id,
                    )
                ).first()
                return row.score if row else None

            assert server.vault.db.run_immediate_read_task(get_score) == 5

            # last_active_at moved forward
            assert t2 >= t1, "last_active_at should be >= initial value after returning"

            # Only one GuestSession row (no duplicate)
            def count_sessions(session):
                return len(session.exec(select(GuestSession)).all())

            assert server.vault.db.run_immediate_read_task(count_sessions) == 1
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 9. GET after POST (cookie path) — scores returned
# ---------------------------------------------------------------------------


def test_get_returns_scores_after_post():
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, read_token = _setup(tmp)
        try:
            pic_id = _import_picture(owner_client)
            # POST without cookie first so the session is created
            rc_no_cookie = _read_client(server, read_token)
            r = rc_no_cookie.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": _SID,
                    "set_cookie": False,
                    "scores": {str(pic_id): 3},
                },
            )
            assert r.status_code == 200, r.text

            # GET with the session cookie
            rc_with_cookie = _read_client(
                server, read_token, cookies={"guest_session": _SID}
            )
            r = rc_with_cookie.get(f"{API}/pictures/guest-scores")
            assert r.status_code == 200, r.text
            scores = r.json()["scores"]
            assert str(pic_id) in scores
            assert scores[str(pic_id)] == 3
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 10. Cookie behaviour — set_cookie=True sets cookies; False does not
# ---------------------------------------------------------------------------


def test_cookies_set_when_requested():
    with tempfile.TemporaryDirectory() as tmp:
        server, _, read_token = _setup(tmp)
        try:
            rc = _read_client(server, read_token)
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={"session_id": _SID, "set_cookie": True, "scores": {}},
            )
            assert r.status_code == 200, r.text
            assert "guest_session" in r.cookies, "HttpOnly session cookie should be set"
            assert "guest_session_active" in r.cookies, "Sentinel cookie should be set"
            assert r.cookies["guest_session"] == _SID
            assert r.cookies["guest_session_active"] == "1"
        finally:
            server.__exit__(None, None, None)


def test_cookies_not_set_when_declined():
    with tempfile.TemporaryDirectory() as tmp:
        server, _, read_token = _setup(tmp)
        try:
            rc = _read_client(server, read_token)
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={"session_id": _SID, "set_cookie": False, "scores": {}},
            )
            assert r.status_code == 200, r.text
            assert "guest_session" not in r.cookies, (
                "No cookie should be set when declined"
            )
            assert "guest_session_active" not in r.cookies
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 11. Concurrent session limit — new session refused at cap
# ---------------------------------------------------------------------------


def test_concurrent_limit_refuses_new_session():
    with tempfile.TemporaryDirectory() as tmp:
        server, _, read_token = _setup(tmp, guest_max_concurrent_sessions=2)
        try:
            rc = _read_client(server, read_token)

            # Fill up to the cap
            for i in range(2):
                r = rc.post(
                    f"{API}/pictures/guest-scores",
                    json={
                        "session_id": f"session-cap-{i:04d}",
                        "set_cookie": False,
                        "scores": {},
                    },
                )
                assert r.status_code == 200, f"session {i} should be accepted: {r.text}"

            # One more new session should be refused
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": "session-new-over-limit",
                    "set_cookie": False,
                    "scores": {},
                },
            )
            assert r.status_code == 503, f"Expected 503, got {r.status_code}: {r.text}"
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 12. Returning sessions not blocked by concurrent limit
# ---------------------------------------------------------------------------


def test_returning_session_bypasses_concurrent_limit():
    with tempfile.TemporaryDirectory() as tmp:
        server, _, read_token = _setup(tmp, guest_max_concurrent_sessions=1)
        try:
            rc = _read_client(server, read_token)

            # Create the one allowed session
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={"session_id": _SID, "set_cookie": False, "scores": {}},
            )
            assert r.status_code == 200, r.text

            # Returning request from the same session must not be blocked
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={"session_id": _SID, "set_cookie": False, "scores": {}},
            )
            assert r.status_code == 200, (
                f"Returning session should not be blocked at concurrent limit: {r.text}"
            )
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 13. FIFO eviction — oldest session deleted when stored cap reached
# ---------------------------------------------------------------------------


def test_fifo_eviction_removes_oldest():
    with tempfile.TemporaryDirectory() as tmp:
        server, _, read_token = _setup(tmp, guest_max_stored_sessions=3)
        try:
            rc = _read_client(server, read_token)

            # Create 3 sessions to fill the cap
            for i in range(3):
                r = rc.post(
                    f"{API}/pictures/guest-scores",
                    json={
                        "session_id": f"eviction-session-{i:04d}",
                        "set_cookie": False,
                        "scores": {},
                    },
                )
                assert r.status_code == 200, f"setup session {i}: {r.text}"
                time.sleep(0.02)  # ensure distinct created_at ordering

            # Adding a 4th session should evict the oldest
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": "eviction-session-new",
                    "set_cookie": False,
                    "scores": {},
                },
            )
            assert r.status_code == 200, r.text

            def get_session_ids(session):
                return {
                    gs.session_id for gs in session.exec(select(GuestSession)).all()
                }

            ids = server.vault.db.run_immediate_read_task(get_session_ids)
            assert len(ids) == 3, f"Expected 3 sessions after eviction, got {len(ids)}"
            assert "eviction-session-0000" not in ids, (
                "Oldest session should have been evicted"
            )
            assert "eviction-session-new" in ids, "New session should be present"
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 14 & 15. SCORE sort uses COALESCE(guest_score, picture.score)
# ---------------------------------------------------------------------------


def test_score_sort_uses_guest_scores():
    """Pictures should be ordered by guest score (with picture.score as fallback)."""
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, read_token = _setup(tmp)
        try:
            # Upload two pictures
            png = _make_png_bytes()
            resp = owner_client.post(
                f"{API}/pictures/import",
                files=[
                    ("file", ("a.png", png, "image/png")),
                    ("file", ("b.png", png, "image/png")),
                ],
            )
            assert resp.status_code == 200, resp.text
            task_id = resp.json()["task_id"]
            for _ in range(50):
                s = owner_client.get(
                    f"{API}/pictures/import/status", params={"task_id": task_id}
                )
                if s.json().get("status") in ("completed", "failed"):
                    break
                time.sleep(0.1)

            pics = owner_client.get(f"{API}/pictures").json()
            assert len(pics) >= 2
            id_a, id_b = pics[0]["id"], pics[1]["id"]

            # Set owner scores: A=1, B=5
            owner_client.post(
                f"{API}/pictures/apply-scores",
                json={"scores": {str(id_a): 1, str(id_b): 5}, "only_unscored": False},
            )

            # Guest scores: A=5, B=1 (reversed from owner)
            rc = _read_client(server, read_token, cookies={"guest_session": _SID})
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={
                    "session_id": _SID,
                    "set_cookie": False,
                    "scores": {str(id_a): 5, str(id_b): 1},
                },
            )
            assert r.status_code == 200, r.text

            # Fetch sorted by SCORE descending using the guest session cookie
            r = rc.get(
                f"{API}/pictures",
                params={"sort": "SCORE", "descending": "true"},
            )
            assert r.status_code == 200, r.text
            returned_ids = [p["id"] for p in r.json()]

            # A should come first (guest score 5 > guest score 1 for B)
            assert returned_ids.index(id_a) < returned_ids.index(id_b), (
                f"Expected A (guest score 5) before B (guest score 1); got order {returned_ids}"
            )
        finally:
            server.__exit__(None, None, None)


def test_score_sort_fallback_to_picture_score():
    """Pictures without a guest score should fall back to picture.score."""
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, read_token = _setup(tmp)
        try:
            png = _make_png_bytes()
            resp = owner_client.post(
                f"{API}/pictures/import",
                files=[
                    ("file", ("x.png", png, "image/png")),
                    ("file", ("y.png", png, "image/png")),
                ],
            )
            assert resp.status_code == 200, resp.text
            task_id = resp.json()["task_id"]
            for _ in range(50):
                s = owner_client.get(
                    f"{API}/pictures/import/status", params={"task_id": task_id}
                )
                if s.json().get("status") in ("completed", "failed"):
                    break
                time.sleep(0.1)

            pics = owner_client.get(f"{API}/pictures").json()
            assert len(pics) >= 2
            id_x, id_y = pics[0]["id"], pics[1]["id"]

            # Owner scores: X=1, Y=5 — no guest scores set
            owner_client.post(
                f"{API}/pictures/apply-scores",
                json={"scores": {str(id_x): 1, str(id_y): 5}, "only_unscored": False},
            )

            # Guest has no scores → fallback to picture.score
            rc = _read_client(server, read_token, cookies={"guest_session": _SID})
            # Create an empty session so guest_session_id is valid in DB
            r = rc.post(
                f"{API}/pictures/guest-scores",
                json={"session_id": _SID, "set_cookie": False, "scores": {}},
            )
            assert r.status_code == 200, r.text

            r = rc.get(
                f"{API}/pictures",
                params={"sort": "SCORE", "descending": "true"},
            )
            assert r.status_code == 200, r.text
            returned_ids = [p["id"] for p in r.json()]

            # Y (picture.score=5) should come before X (picture.score=1)
            assert returned_ids.index(id_y) < returned_ids.index(id_x), (
                f"Expected Y (score 5) before X (score 1) via fallback; got {returned_ids}"
            )
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 16. AuthService in-memory guest session tracking
# ---------------------------------------------------------------------------


class TestAuthServiceGuestTracking:
    """Unit tests for the in-memory guest session tracker on AuthService."""

    def _make_auth(self):
        """Return a minimal AuthService instance (no real DB needed)."""
        db_mock = MagicMock()
        return AuthService(
            db=db_mock,
            server_config={},
            server_config_path="/dev/null",
            logger=MagicMock(),
        )

    def test_initial_count_is_zero(self):
        auth = self._make_auth()
        assert auth.count_active_guest_sessions() == 0

    def test_record_activity_increments_count(self):
        auth = self._make_auth()
        auth.record_guest_activity("sid-1")
        assert auth.count_active_guest_sessions() == 1

    def test_multiple_sessions_counted(self):
        auth = self._make_auth()
        auth.record_guest_activity("sid-1")
        auth.record_guest_activity("sid-2")
        auth.record_guest_activity("sid-3")
        assert auth.count_active_guest_sessions() == 3

    def test_same_session_recorded_twice_counts_once(self):
        auth = self._make_auth()
        auth.record_guest_activity("sid-1")
        auth.record_guest_activity("sid-1")
        assert auth.count_active_guest_sessions() == 1

    def test_expired_session_not_counted(self):
        auth = self._make_auth()
        # Manually inject a stale timestamp
        stale_ts = time.monotonic() - auth._GUEST_SESSION_ACTIVE_TTL - 1
        with auth._guest_sessions_lock:
            auth._guest_sessions["stale-sid"] = stale_ts
        assert auth.count_active_guest_sessions() == 0

    def test_mix_of_active_and_expired(self):
        auth = self._make_auth()
        stale_ts = time.monotonic() - auth._GUEST_SESSION_ACTIVE_TTL - 1
        with auth._guest_sessions_lock:
            auth._guest_sessions["stale"] = stale_ts
        auth.record_guest_activity("fresh")
        assert auth.count_active_guest_sessions() == 1
