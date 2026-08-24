"""Both-directions authz coverage for the install-ID endpoints.

Per CLAUDE.md, an authz change asserts both directions: the out-of-scope
principal is blocked AND the in-scope one still works, because over-blocking is
its own regression. The gate ships enforcing, so these run against the shipped
configuration rather than a flipped flag.

The install ID is owner-only rather than any_token on purpose: it is a stable
identifier for the installation, so a share-link holder able to read it could
correlate visits across links.
"""

import contextlib
import json
import os
import tempfile

import pytest
from starlette.testclient import TestClient

from pixlstash.server import Server
from pixlstash.telemetry.install_id import install_id_path, read_install_identity
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"

# The SPA catch-all answers unmatched GETs with 200, which once made a whole
# library BOLA test vacuous. Every positive assertion must reach a real route.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


@contextlib.contextmanager
def _owner_env():
    """Build a real Server and mint the three principals the matrix cares about."""
    tmp = tempfile.TemporaryDirectory()
    cfg = os.path.join(tmp.name, "server-config.json")
    with open(cfg, "w") as fh:
        json.dump({"port": 8000}, fh)
    server = Server(cfg)
    server.__enter__()
    try:
        owner = TestClient(server.api, raise_server_exceptions=True)
        r = owner.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, r.text

        r = owner.post(
            f"{API}/users/me/token",
            json={"description": "global read", "scope": "READ"},
        )
        assert r.status_code == 200, r.text
        unscoped_read = r.json()["token"]

        r = owner.post(
            f"{API}/users/me/token",
            json={
                "description": "set share",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": 1,
            },
        )
        assert r.status_code == 200, r.text
        scoped_read = r.json()["token"]

        # The auth middleware prefers a cookie session over a Bearer token, so a
        # token request on the logged-in owner client would authenticate as the
        # owner and never exercise the token scope. ``anon`` never logs in.
        anon = TestClient(server.api, raise_server_exceptions=True)

        yield {
            "server": server,
            "config_path": cfg,
            "owner": owner,
            "anon": anon,
            "unscoped_read": unscoped_read,
            "scoped_read": scoped_read,
        }
    finally:
        server.__exit__(None, None, None)
        tmp.cleanup()


@pytest.fixture(scope="module")
def env():
    with _owner_env() as e:
        yield e


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Positive direction: the owner still reaches both routes
# ---------------------------------------------------------------------------


def test_owner_can_read_the_install_id(env):
    r = env["owner"].get(f"{API}/telemetry/install-id")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True
    assert body["install_id"]
    assert body["created_date"]


def test_owner_can_recreate_the_install_id(env):
    before = env["owner"].get(f"{API}/telemetry/install-id").json()["install_id"]

    r = env["owner"].post(f"{API}/telemetry/install-id/recreate")

    assert r.status_code == 200, r.text
    after = r.json()["install_id"]
    assert after != before
    assert env["owner"].get(f"{API}/telemetry/install-id").json()["install_id"] == after


def test_the_endpoint_reads_the_same_store_the_server_wrote(env):
    body = env["owner"].get(f"{API}/telemetry/install-id").json()

    on_disk = read_install_identity(env["config_path"])

    assert on_disk is not None
    assert on_disk.install_id == body["install_id"]
    assert os.path.exists(install_id_path(env["config_path"]))


# ---------------------------------------------------------------------------
# Negative direction: no other principal reaches either route
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/telemetry/install-id"])
def test_unauthenticated_read_is_denied(env, path):
    r = env["anon"].get(f"{API}{path}")

    assert r.status_code in (401, 403), r.text


def test_resource_scoped_token_cannot_read_the_install_id(env):
    r = env["anon"].get(
        f"{API}/telemetry/install-id", headers=_bearer(env["scoped_read"])
    )

    # A share-link holder must not be able to read a stable installation
    # identifier and correlate visits across links.
    assert r.status_code == 403, r.text


def test_unscoped_read_token_cannot_read_the_install_id(env):
    r = env["anon"].get(
        f"{API}/telemetry/install-id", headers=_bearer(env["unscoped_read"])
    )

    assert r.status_code == 403, r.text


def test_read_tokens_cannot_recreate_the_install_id(env):
    before = env["owner"].get(f"{API}/telemetry/install-id").json()["install_id"]

    for token in (env["scoped_read"], env["unscoped_read"]):
        r = env["anon"].post(
            f"{API}/telemetry/install-id/recreate", headers=_bearer(token)
        )
        assert r.status_code in (401, 403), r.text

    # And the refusal was real: the stored ID is untouched.
    after = env["owner"].get(f"{API}/telemetry/install-id").json()["install_id"]
    assert after == before


def test_unauthenticated_recreate_is_denied(env):
    r = env["anon"].post(f"{API}/telemetry/install-id/recreate")

    assert r.status_code in (401, 403), r.text
