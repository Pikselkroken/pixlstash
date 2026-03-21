"""Tests for the RateLimitMiddleware.

A minimal FastAPI app is used so tests run fast without a real Server instance.
``_LIMIT`` is patched to a small value to keep the tests short.
"""

import tempfile
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pixlstash.utils.rate_limiter as rl_module
from pixlstash.auth import AUTH_EXCLUDED_PATHS
from pixlstash.utils.rate_limiter import RateLimitMiddleware


def _make_app() -> FastAPI:
    """Return a minimal app with only the rate-limit middleware registered."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    # One public (unauthenticated) endpoint that is in AUTH_EXCLUDED_PATHS.
    @app.get("/login")
    def fake_login():
        return {"ok": True}

    # One authenticated endpoint that is NOT in AUTH_EXCLUDED_PATHS.
    @app.get("/protected")
    def fake_protected():
        return {"ok": True}

    return app


@pytest.fixture()
def client():
    with patch.object(rl_module, "_LIMIT", 3):
        with TestClient(_make_app(), raise_server_exceptions=True) as c:
            yield c


def test_public_path_is_rate_limited(client):
    """Requests up to the limit succeed; the next one returns 429."""
    for _ in range(3):
        r = client.get("/login")
        assert r.status_code == 200

    r = client.get("/login")
    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_authenticated_path_is_not_rate_limited(client):
    """Authenticated paths bypass the limiter entirely."""
    assert "/protected" not in AUTH_EXCLUDED_PATHS

    for _ in range(20):
        r = client.get("/protected")
        assert r.status_code == 200


def test_window_expiry_resets_limit():
    """After the window elapses, the counter resets and requests go through again."""
    import time

    with patch.object(rl_module, "_LIMIT", 2), patch.object(rl_module, "_WINDOW", 1):
        app = _make_app()
        with TestClient(app) as c:
            # Exhaust the limit.
            assert c.get("/login").status_code == 200
            assert c.get("/login").status_code == 200
            assert c.get("/login").status_code == 429

            # Wait for the 1-second window to expire.
            time.sleep(1.1)

            # Counter should have reset — request succeeds again.
            assert c.get("/login").status_code == 200
