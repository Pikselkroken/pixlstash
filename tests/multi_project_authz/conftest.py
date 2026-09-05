"""Fixtures for the suites that share one multi-project authz environment.

Two files assert against the same world - the three projects, the shared set and
the shared character, and the scoped tokens the #719/#721 leaks were reproduced
with - so they live in one package and take the environment from this
``conftest.py``.

They used to share it by module-namespace copy instead
(``env = _multi_project.env`` in the borrowing file), which is what a package
conftest exists to replace: pytest resolves a fixture's parameters against the
module it was collected in, so a copied fixture keeps working right up until it
grows a dependency the copy never mentioned - then every test in the borrowing
module errors at setup and nothing about the exporting module changes colour.
Here ``env`` can take whatever it needs and both modules keep resolving it.

Because these fixtures sit in a package conftest rather than in ``tests/``,
``env`` stays ``autouse`` for exactly the two suites in this directory and for
nothing else in the repository.
"""

import gc
import json
import os
import tempfile
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from pixlstash.server import Server
from tests.authz_guard import no_spa_fallback  # noqa: F401
from tests.multi_project_authz.shared_env import (
    API,
    _assert_fixture_shape,
    _bearer,
    _build_fixture_entities,
    _detach_volatile_finders,
    _enforcing,
    _good_picture_files,
    _picture_baseline,
    _reset_domain_state,
    _wait_faces_extracted,
)
from tests.utils import upload_pictures_and_wait, wait_likeness_settled


@pytest.fixture(scope="module")
def _module_env():
    """One Server, one login and one imported library for the whole module.

    Booting a Server (migrations, vault start-up, route registration), importing
    two real pictures through the pipeline and tearing the lot down again is the
    entire cost of this file - the assertions themselves are HTTP calls costing
    milliseconds. It is paid once here; per-test isolation comes from the
    autouse ``env`` fixture below, which rebuilds the domain state and re-mints
    every credential.
    """
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        anon = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, r.text

        files = [("file", (n, d, c)) for n, d, c in _good_picture_files()[:2]]
        assert len(files) >= 2, "need >=2 test pictures"
        # 120 s, not 30: this is the first import after a cold Server boot in
        # this file, and on the shared Windows CI runner that start-up cost
        # (ONNX session init, thumbnailing) blew a 30 s wall-clock bound while
        # the import itself was healthy. Same reasoning as the recorded
        # test_florence skip: a tight wall-clock bound on contended CI hardware
        # is a flake generator, not a signal.
        st = upload_pictures_and_wait(client, files, timeout_s=120)
        assert st["status"] == "completed", st
        pic_ids = [p["id"] for p in client.get(f"{API}/pictures").json()]
        assert len(pic_ids) >= 2
        pic_a, pic_b = pic_ids[0], pic_ids[1]

        # Let the background pipeline finish with the two uploads ONCE, then
        # detach the finders that would keep rewriting what the tests seed. The
        # alternative - waiting for quiescence before each seed - is the slower
        # of the two by a wide margin, and it does not stop a sweep landing
        # halfway through the assertions that follow it.
        _wait_faces_extracted(server, [pic_a, pic_b])
        wait_likeness_settled(server)
        _detach_volatile_finders(server)

        yield SimpleNamespace(
            server=server,
            owner=client,
            anon=anon,
            pic_a=pic_a,
            pic_b=pic_b,
            baseline=_picture_baseline(server, [pic_a, pic_b]),
        )
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture(autouse=True)
def env(_module_env, request):
    """Fresh projects, entities and credentials for every test.

    Autouse rather than opt-in: a test added later that forgets to request
    ``env`` would otherwise inherit whatever the previous one left behind. That
    holds for a test added to *either* suite in this package, and - because a
    package conftest is scoped to its own directory - for nothing outside it.

    ``no_spa_fallback`` is pulled in explicitly below, and that is not
    decoration: pytest sets autouse fixtures up *before* the
    ``usefixtures``-requested ones of the same scope, so making this fixture
    autouse moved its own ~25 HTTP calls - including the bare
    ``status_code == 200`` positive control at the end - out from under the
    anti-vacuity guard, where the SPA catch-all could have satisfied them
    (tests/authz_guard.py). Requesting it here puts them back under it.

    What is reset, and why each one is not optional in a *security* suite where
    almost every assertion is a refusal:

    * **The domain state** (``_reset_domain_state``). A negative assertion here
      reads "403", and a 403 is what you also get when the object is simply
      gone. ``test_deleting_a_project_leaves_the_other_membership_intact``
      deletes P2 outright, ``test_locked_members_listing_both_directions`` locks
      the shared set, and half a dozen tests file extra pictures into projects.
      Every one of those would leave a later test proving nothing.
    * **The credentials.** Every token row is deleted and re-minted per test, so
      a revoked or stale token can never masquerade as a scope refusal. Note
      that the ids themselves recycle - SQLite hands project 1 straight back
      after a whole-table delete - so a surviving token from a previous test
      would still *authenticate*, and against whatever now occupies its id.
      Deleting the rows is therefore the only thing standing between this suite
      and an isolation guarantee resting on rowid reuse. It also keeps token
      verification cheap: it is a bcrypt call per candidate row.
    * **The shape** (``_assert_fixture_shape``), which re-proves by identity
      that the world the assertions describe is actually there.
    """
    request.getfixturevalue("no_spa_fallback")
    m = _module_env
    server, client, anon = m.server, m.owner, m.anon

    _reset_domain_state(server, m.baseline)

    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "example-owner-password"}
    )
    assert r.status_code == 200, (
        f"owner re-login failed - the shared environment is dirty: {r.text}"
    )

    ids = _build_fixture_entities(client, m.pic_a)
    _assert_fixture_shape(client, ids, m.pic_a, m.pic_b)

    def mint(resource_type, resource_id):
        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": f"{resource_type}:{resource_id}",
                "scope": "READ",
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
        )
        assert r.status_code == 200, r.text
        return r.json()["token"]

    tokens = {label: mint("project", pid) for label, pid in ids["projects"].items()}

    # The positive control for every refusal below: each freshly minted token
    # must actually work on an in-scope read *now*. Without this a dead or
    # unminted credential produces exactly the 403 the negative assertions are
    # looking for, and the whole file passes for the wrong reason.
    with _enforcing(server):
        for label, token in tokens.items():
            probe = anon.get(
                f"{API}/projects/{ids['projects'][label]}", headers=_bearer(token)
            )
            assert probe.status_code == 200, (
                f"the fresh {label} token cannot read its own project "
                f"({probe.status_code}: {probe.text[:200]}) - every 403 asserted "
                f"in this test would prove nothing"
            )

    yield {
        "server": server,
        "owner": client,
        "anon": anon,
        "pic_a": m.pic_a,
        "pic_b": m.pic_b,
        "tokens": tokens,
        # Exposed so a test can mint a character- / set-scoped token too: the
        # `project_ids` narrowing (R1) has a different rung for those.
        "mint": mint,
        **ids,
    }
