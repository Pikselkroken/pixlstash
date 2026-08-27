"""Object-scope (BOLA / CWE-639) tests for picture-MUTATION handlers.

Closes finding F1 in ``docs/reviews/feature-slick-grid-updates.md``: the
picture-mutation handlers (tag add/remove/clear, face create/delete, set-project,
apply-scores, run-plugin, character face assign/unassign) must enforce object
scope so a resource-scoped token cannot mutate pictures outside its grant.

**Two enforcement mechanisms after the authz refactor (backend refactor plan
Steps 4-6):**

- **Single-picture handlers (``PICTURE_SCOPED``).** The inline
  ``enforce_picture_scope`` call was removed in Step 5; object authorization now
  lives in the central authz gate. These routes are POST/DELETE/PATCH not in
  ``READ_SAFE_POST_PATHS``, so a real resource-scoped (=READ) share token is
  middleware-blocked (403) before any handler runs - the live guard, exercised
  here with a real scoped token. The gate's per-object membership contract is
  proven in ``tests/test_authz_gate_step4.py``.
- **Batch handlers (``SCOPED_LIST``).** These still filter their own id list
  inline via ``fetch_scope_allowed_picture_ids``; those tests keep the
  monkeypatch technique (``_scope_to``) that exercises the inline filter directly.

Both directions per CLAUDE.md: a scoped token is denied (403) and the owner still
succeeds (200) - over-blocking the owner would be its own regression.
"""

import gc
import json
import os
import tempfile
import time
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, delete

import pixlstash.routes.tags as tags_module
import pixlstash.routes.characters_faces as characters_faces_module
import pixlstash.routes.comfyui as comfyui_module
import pixlstash.routes.pictures._crud as crud_module
import pixlstash.routes.pictures._misc as misc_module
from pixlstash.db_models import Picture, UserToken
from pixlstash.routes.pictures import _helpers as helpers_module
from pixlstash.server import Server
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401
from tests.utils import upload_pictures_and_wait

API = "/api/v1"

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL can make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _good_picture_files():
    pictures_dir = os.path.join(os.path.dirname(__file__), "..", "pictures", "good")
    results = []
    for name in sorted(os.listdir(pictures_dir)):
        path = os.path.join(pictures_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            ct = "image/png" if ext == ".png" else "image/jpeg"
            with open(path, "rb") as fh:
                results.append((name, fh.read(), ct))
    return results


def _tables_to_wipe(server):
    """Vault tables that carry test state and must be emptied between tests.

    Every table in the live vault schema, minus the ones that already hold rows
    on a freshly started server (``library_settings``, ``metadata``,
    ``snapshot``) - those are start-up state, not test state - and minus
    ``picture`` itself, whose rows are the fixture library and are restored
    column-by-column by ``_reset_library`` rather than re-imported.

    Deriving the list from the schema instead of hand-listing models keeps it
    complete: a table added later is wiped without anyone remembering to add it
    here, and a forgotten table is exactly how a shared environment starts
    making assertions pass for the wrong reason. Must be called *before* the
    fixture library is imported, or ``picture`` and ``tag`` look seeded.

    Returned children-first (reverse dependency order), which together with
    restoring ``picture`` before the deletes keeps every foreign key satisfied
    at all times. ``Picture`` points out of the preserved set into the wiped one
    three times - ``stack_id`` -> ``picturestack``, ``project_id`` -> ``project``
    and ``reference_folder_id`` -> ``reference_folder`` - and the restore puts
    all three back to their imported values (NULL, for this fixture) before the
    referenced rows go. Foreign keys really are enforced here
    (``pixlstash/database.py``), so a fixture that imported into a project or a
    reference folder would need that restore, not a ``PRAGMA``: switching
    enforcement off leaves it off on the pooled connection if a delete raises,
    silently weakening every later test.
    """
    engine = server.vault.db._engine
    present = set(sa.inspect(engine).get_table_names())
    with engine.connect() as connection:
        seeded = {
            name
            for name in present
            if connection.execute(
                sa.select(sa.func.count()).select_from(sa.table(name))
            ).scalar()
        }
    seeded.add(Picture.__tablename__)
    return [
        table
        for table in reversed(SQLModel.metadata.sorted_tables)
        if table.name in present and table.name not in seeded
    ]


def _picture_rows(server):
    """Every column of every fixture picture, for restoring between tests.

    Snapshotting the whole row rather than the handful of columns these tests
    are known to write (``deleted``, ``deleted_at``, ``score``, ``stack_id``,
    ``project_id``, …) means a handler that starts writing one more column does
    not quietly leak it into the next test.
    """
    columns = [column.name for column in Picture.__table__.columns]
    return server.vault.db.run_immediate_read_task(
        lambda session: [
            {name: getattr(row, name) for name in columns}
            for row in session.exec(sa.select(Picture)).scalars().all()
        ]
    )


def _quiesce_background_work(server):
    """Take every work finder out of the planner and let the pipeline settle.

    The per-test ``Server`` this module used to build was hiding the import
    pipeline rather than avoiding it: a vault that had just come up had nothing
    to backfill and no models loaded, so the sweeps were still in a long
    backoff when the test ended. A shared server is warm, and the sweeps land
    *inside* the tests instead - where they rewrite the very rows the fixtures
    hand-place. ``TagTask`` runs ``delete(Tag).where(picture_id.in_(...))``
    before writing its own labels, ``FaceExtractionTask`` adds ``Face`` rows
    beside the ones a test just created and indexes by position, and the dedup
    sweep stacks pictures, which changes how many rows a bulk delete touches.

    Nothing in this module needs derived data - every assertion is a status
    code against hand-made objects - so every finder goes, not a curated
    subset. The planner thread itself keeps running (a route that wakes it must
    still find it alive) and so does the task runner, so routes that submit
    work directly are unaffected. ``detach_finders`` edits the planner's finder
    structures under the planner's own lock, so the loop no longer has to be
    stopped around the removal.

    Waiting the pipeline out per test instead was measured slower than the
    per-test servers this replaces (PR #814), which is why it is switched off
    once here rather than polled for.

    Returns the names of the finders it removed, so the per-test fixture can
    re-check before every test that they are still gone.
    """
    planner = server.vault._work_planner
    task_types = list(server.vault._planner_work_finders)
    for task_type in task_types:
        server.vault._planner_work_finders.pop(task_type)
    removed = planner.detach_finders(task_types)

    # Work already queued or running when the finders went is still ours to
    # wait for: it would otherwise write into the first test's freshly reset
    # library.
    runner = server.vault._task_runner
    runner.cancel_pending_tasks()
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        with runner._active_task_lock:
            active = list(runner._active_tasks.values())
        if not active:
            return removed
        time.sleep(0.05)
    raise AssertionError(
        f"import pipeline did not settle within 60s; still running: {active}"
    )


@pytest.fixture(scope="module")
def _shared_env():
    """One Server, one imported library, shared by every test in this module.

    Building a Server and importing four photographs costs ~1.4 s, and tearing
    it down costs another ~2 s that has nothing to do with the Server object:
    ``Vault.stop()`` joins the import pipeline the upload started (face
    detection loads its ONNX models *during* the join), then releases those
    models again, closes the engine and collects. All of it is per-Server work,
    so it is paid once here instead of 30 times, and per-test isolation comes
    from the autouse ``fresh_state`` fixture below.
    """
    temp_dir = tempfile.TemporaryDirectory()
    try:
        config_path = os.path.join(temp_dir.name, "server-config.json")
        with open(config_path, "w") as fh:
            fh.write(json.dumps({"port": 8000}))
        server = Server(config_path)
        try:
            client = TestClient(server.api, raise_server_exceptions=True)
            r = client.post(
                f"{API}/login",
                json={"username": "owner", "password": "example-owner-password"},
            )
            assert r.status_code == 200, r.text

            # Before the import, or the library itself counts as seeded.
            wipe_tables = _tables_to_wipe(server)

            files = [("file", (n, d, c)) for n, d, c in _good_picture_files()[:4]]
            assert files, "No test pictures found in pictures/good/"
            st = upload_pictures_and_wait(client, files, timeout_s=30)
            assert st["status"] == "completed", st

            disabled_finders = _quiesce_background_work(server)

            r = client.get(f"{API}/pictures")
            assert r.status_code == 200, r.text
            picture_ids = [p["id"] for p in r.json()]
            assert len(picture_ids) >= 2, (
                "Need at least two pictures for the scope test"
            )

            yield SimpleNamespace(
                server=server,
                client=client,
                picture_ids=picture_ids,
                wipe_tables=wipe_tables,
                disabled_finders=disabled_finders,
                picture_rows=_picture_rows(server),
            )
        finally:
            server.close()
    finally:
        temp_dir.cleanup()
        gc.collect()


def _reset_library(shared):
    """Put the shared vault back to its just-imported state.

    Restores the pictures first and deletes everything else second - see
    ``_tables_to_wipe`` for why that order keeps the foreign keys satisfied
    without disabling them.
    """
    picture_table = Picture.__table__

    def _reset(session):
        for row in shared.picture_rows:
            session.execute(
                sa.update(picture_table)
                .where(picture_table.c.id == row["id"])
                .values(**row)
            )
        for table in shared.wipe_tables:
            session.exec(delete(table))
        session.commit()

    shared.server.vault.db.run_task(_reset)

    # Tokens live in the hub, not the vault. Flushing through the helper bumps
    # the revocation epoch too, which a bare _token_cache.clear() skips.
    def _wipe_tokens(session):
        session.exec(delete(UserToken))
        session.commit()

    shared.server.hub_engine.run_task(_wipe_tokens)
    shared.server.auth._flush_token_cache()


@pytest.fixture(autouse=True)
def fresh_state(_shared_env):
    """Reset the shared library and re-mint every credential, before each test.

    This is the canary for the shared environment as well as its reset, and it
    is deliberately a per-test fixture rather than a trailing "runs last"
    canary test: the CI gate partitions *individual* tests across shards
    (``--ci-shard``, tests/conftest.py), so a trailing test would land in one
    shard and watch nothing in the others.

    A negative assertion in this module is a 403. Four different accidents
    produce that same 403 without any scope guard being involved, and each one
    is handled here before the test body runs:

    * a credential a previous test revoked, or a session it killed - so the
      owner logs in again and the share token is minted fresh every time;
    * a share token that never worked at all - so it is proved on an in-scope
      read first. If it cannot read, the test does not run;
    * the target picture having been deleted by a previous test - so the
      owner's listing is checked against the exact fixture *ids*, not a count:
      a missing picture and a scope refusal are indistinguishable from a status
      code;
    * a backfill finder rewriting the row under the test - so the finders
      ``_quiesce_background_work`` removed are re-checked by name.
    """
    shared = _shared_env
    _reset_library(shared)

    running = shared.server.vault._work_planner.registered_finder_names()
    assert running.isdisjoint(shared.disabled_finders), (
        "a background finder that rewrites this module's fixture data is "
        f"running again: {sorted(running & shared.disabled_finders)}"
    )

    r = shared.client.post(
        f"{API}/login", json={"username": "owner", "password": "example-owner-password"}
    )
    assert r.status_code == 200, (
        f"owner re-login failed - the shared environment is dirty: {r.text}"
    )

    listed = shared.client.get(f"{API}/pictures")
    assert listed.status_code == 200, listed.text
    assert [p["id"] for p in listed.json()] == shared.picture_ids, (
        "the fixture library is not back to its imported state; a picture a "
        "previous test deleted answers a mutation with 403 exactly like a "
        "scope refusal does"
    )

    r = shared.client.post(f"{API}/characters", json={"name": "ScopeChar"})
    assert r.status_code == 200, r.text
    character_id = r.json()["character"]["id"]

    r = shared.client.post(
        f"{API}/users/me/token",
        json={
            "description": "set share",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": 1,
        },
    )
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    anon = TestClient(shared.server.api, raise_server_exceptions=True)
    probe = anon.get(f"{API}/pictures", headers=_bearer(token))
    assert probe.status_code == 200, (
        f"the freshly minted share token cannot authenticate ({probe.status_code}: "
        f"{probe.text}) - every 403 below would prove nothing. An unknown or "
        "revoked token answers 401 here, not 200."
    )
    assert probe.json() == [], (
        "the share token is scoped to a picture set that holds nothing, so it "
        f"must see no pictures at all; it sees {probe.json()}"
    )

    yield SimpleNamespace(character_id=character_id, token=token, anon=anon)


@pytest.fixture
def env(_shared_env, fresh_state):
    """A live server with >=2 imported pictures and a reference character."""
    return (
        _shared_env.server,
        _shared_env.client,
        _shared_env.picture_ids,
        fresh_state.character_id,
    )


def _scope_to(monkeypatch, modules, allowed_ids):
    """Patch the scope helpers in *modules* to simulate a token scoped to ids.

    ``allowed_ids`` of ``None`` means owner/unscoped (no filtering). A set means
    only those picture ids are in scope; everything else is denied.
    """

    def fake_allowed(server, request):
        return allowed_ids if allowed_ids is None else set(allowed_ids)

    def fake_enforce(server, request, picture_id):
        if allowed_ids is None:
            return
        if int(picture_id) not in set(allowed_ids):
            from fastapi import HTTPException

            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )

    for module in modules:
        if hasattr(module, "fetch_scope_allowed_picture_ids"):
            monkeypatch.setattr(module, "fetch_scope_allowed_picture_ids", fake_allowed)
        if hasattr(module, "enforce_picture_scope"):
            monkeypatch.setattr(module, "enforce_picture_scope", fake_enforce)
    # The batch (SCOPED_LIST) handlers still filter inline via
    # fetch_scope_allowed_picture_ids, so patching it here still exercises them.
    # The single-picture (PICTURE_SCOPED) handlers no longer call
    # enforce_picture_scope inline - that authorization moved to the central gate
    # (Step 5), and those routes are covered by the real-scoped-token tests below.
    if hasattr(helpers_module, "enforce_picture_scope"):
        monkeypatch.setattr(helpers_module, "enforce_picture_scope", fake_enforce)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def scoped(fresh_state):
    """A cookie-less ``anon`` client plus a real resource-scoped READ (share)
    token, for exercising the live object-authorization stack on the
    single-picture mutation routes.

    Every resource-scoped share token is a READ token, and these mutation routes
    are NOT in ``READ_SAFE_POST_PATHS``, so the auth middleware blocks the token
    (403) before any handler runs - the live guard against a share token mutating
    someone else's pictures. The routes are also declared ``PICTURE_SCOPED`` in
    ``pixlstash/authz/registry.py``; the gate's per-object membership contract is
    proven in ``tests/test_authz_gate_step4.py``.

    Both are minted fresh per test by ``fresh_state``, which also proves the
    token authenticates before handing it over - a shared credential is exactly
    what would turn a later test's 403 from "wrong scope" into "no credential".
    """
    return fresh_state.anon, fresh_state.token


# ---------------------------------------------------------------------------
# Single-picture handlers (enforce_picture_scope)
# ---------------------------------------------------------------------------


def test_add_tag_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    target = picture_ids[1]
    r = anon.post(
        f"{API}/pictures/{target}/tags", json={"tag": "x"}, headers=_bearer(tok)
    )
    assert r.status_code == 403, r.text
    r = client.post(f"{API}/pictures/{target}/tags", json={"tag": "x"})
    assert r.status_code == 200, r.text


def test_add_tag_owner_succeeds(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [tags_module], None)
    r = client.post(f"{API}/pictures/{picture_ids[1]}/tags", json={"tag": "y"})
    assert r.status_code == 200, r.text


def test_clear_tags_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    target = picture_ids[1]
    r = anon.delete(f"{API}/pictures/{target}/tags", headers=_bearer(tok))
    assert r.status_code == 403, r.text
    r = client.delete(f"{API}/pictures/{target}/tags")
    assert r.status_code == 200, r.text


def test_remove_tag_everywhere_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    target = picture_ids[1]
    # The READ-token write block runs in middleware, ahead of routing, so a
    # renamed or deleted route answers 403 exactly like a live guarded one.
    # No other assertion in this file proves this route exists.
    assert_real_route(server.api, "POST", f"{API}/pictures/{target}/tags/remove_all")
    r = anon.post(
        f"{API}/pictures/{target}/tags/remove_all",
        json={"tag": "x"},
        headers=_bearer(tok),
    )
    assert r.status_code == 403, r.text


def test_create_face_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    target = picture_ids[1]
    body = {"bbox": [1, 1, 10, 10], "frame_index": 0}
    r = anon.post(f"{API}/pictures/{target}/face", json=body, headers=_bearer(tok))
    assert r.status_code == 403, r.text
    r = client.post(f"{API}/pictures/{target}/face", json=body)
    assert r.status_code == 200, r.text


def test_delete_face_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    target = picture_ids[1]
    # Owner creates a real face so a deletable index exists.
    r = client.post(
        f"{API}/pictures/{target}/face",
        json={"bbox": [1, 1, 10, 10], "frame_index": 0},
    )
    assert r.status_code == 200, r.text
    face_index = r.json().get("face_index", 0)

    # Middleware answers 403 before routing, so the owner's POST above does not
    # prove this different route still exists.
    assert_real_route(
        server.api, "DELETE", f"{API}/pictures/{target}/face/{face_index}"
    )
    r = anon.delete(f"{API}/pictures/{target}/face/{face_index}", headers=_bearer(tok))
    assert r.status_code == 403, r.text


def test_delete_picture_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    target = picture_ids[1]
    r = anon.delete(f"{API}/pictures/{target}", headers=_bearer(tok))
    assert r.status_code == 403, r.text
    r = client.delete(f"{API}/pictures/{target}")
    assert r.status_code == 200, r.text


def test_bulk_delete_scoped_token_blocked(env, scoped):
    """A resource-scoped share token cannot bulk soft-delete: DELETE /pictures is
    not in READ_SAFE, so the middleware 403s the READ token and nothing is deleted
    (fail-closed). The gate additionally declares the route PICTURE_SCOPED with
    ``body_ids`` - the every-id membership contract is proven in
    tests/test_authz_gate_step4.py."""
    server, client, picture_ids, _ = env
    anon, tok = scoped
    a, b = picture_ids[0], picture_ids[1]
    r = anon.request(
        "DELETE",
        f"{API}/pictures",
        json={"picture_ids": [a, b]},
        headers=_bearer(tok),
    )
    assert r.status_code == 403, r.text
    # Fail-closed: neither picture was soft-deleted.
    r = client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    ids = {p["id"] for p in r.json()}
    assert a in ids and b in ids, "a scoped bulk-delete must not delete anything"


def test_bulk_delete_in_scope_succeeds(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope = picture_ids[0]
    _scope_to(monkeypatch, [crud_module], {in_scope})
    r = client.request("DELETE", f"{API}/pictures", json={"picture_ids": [in_scope]})
    assert r.status_code == 200, r.text
    assert r.json()["deleted_count"] == 1, r.text
    # Owner view: the picture left the active listing (really soft-deleted).
    _scope_to(monkeypatch, [crud_module], None)
    r = client.get(f"{API}/pictures")
    assert in_scope not in {p["id"] for p in r.json()}, r.text


def test_bulk_delete_owner_deletes_all(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [crud_module], None)
    targets = picture_ids[:2]
    r = client.request("DELETE", f"{API}/pictures", json={"picture_ids": targets})
    assert r.status_code == 200, r.text
    assert r.json()["deleted_count"] == 2, r.text


def test_bulk_delete_rejects_empty_payload(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [crud_module], None)
    r = client.request("DELETE", f"{API}/pictures", json={"picture_ids": []})
    assert r.status_code == 400, r.text


def test_bulk_delete_rejects_oversized_payload(env, monkeypatch):
    """The id-count cap rejects (422) before any per-id scope read / row fetch, so
    one request can't serialise unbounded work on the DB queue."""
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [crud_module], None)
    # 1001 ids (need not exist - the cap is checked before any DB access).
    r = client.request(
        "DELETE", f"{API}/pictures", json={"picture_ids": list(range(1, 1002))}
    )
    assert r.status_code == 422, r.text


def test_patch_picture_scoped_token_blocked(env, scoped):
    """PATCH /pictures/{id} mutates score/description/tags. A resource-scoped share
    token cannot reach it (PATCH not in READ_SAFE -> middleware 403); the owner can.

    Regression for CSO finding S1: this mutator must not be reachable by a share
    token. Its PICTURE_SCOPED object-scope declaration is proven in
    tests/test_authz_gate_step4.py.
    """
    server, client, picture_ids, _ = env
    anon, tok = scoped
    target = picture_ids[1]
    r = anon.patch(f"{API}/pictures/{target}", json={"score": 3}, headers=_bearer(tok))
    assert r.status_code == 403, r.text
    r = client.patch(f"{API}/pictures/{target}", json={"score": 3})
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Batch handlers (fetch_scope_allowed_picture_ids)
# ---------------------------------------------------------------------------


def test_set_project_denied_when_all_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [crud_module], {in_scope})
    r_out = client.patch(
        f"{API}/pictures/project",
        json={"picture_ids": [out_of_scope], "project_id": None, "mode": "set"},
    )
    assert r_out.status_code == 403, r_out.text
    # An in-scope id still works (no over-block).
    r_in = client.patch(
        f"{API}/pictures/project",
        json={"picture_ids": [in_scope], "project_id": None, "mode": "set"},
    )
    assert r_in.status_code == 200, r_in.text


def test_apply_scores_denied_when_all_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [crud_module], {in_scope})
    r_out = client.post(
        f"{API}/pictures/apply-scores",
        json={"scores": {str(out_of_scope): 3}, "only_unscored": False},
    )
    assert r_out.status_code == 403, r_out.text
    r_in = client.post(
        f"{API}/pictures/apply-scores",
        json={"scores": {str(in_scope): 3}, "only_unscored": False},
    )
    assert r_in.status_code == 200, r_in.text


def test_apply_scores_owner_sees_all(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [crud_module], None)
    r = client.post(
        f"{API}/pictures/apply-scores",
        json={
            "scores": {str(picture_ids[0]): 2, str(picture_ids[1]): 4},
            "only_unscored": False,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["updated_count"] >= 1, r.text


def test_run_plugin_denied_when_any_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [misc_module], {in_scope})
    # All-or-nothing: any out-of-scope id denies the whole request (captions
    # alignment). A non-existent plugin name would otherwise 404; the scope
    # guard runs first, so we expect 403.
    r = client.post(
        f"{API}/pictures/plugins/nonexistent",
        json={"picture_ids": [in_scope, out_of_scope]},
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Character face handlers - BOTH the picture_ids and face_ids branches
# ---------------------------------------------------------------------------


def test_assign_face_picture_ids_branch_denied(env, monkeypatch):
    server, client, picture_ids, character_id = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [characters_faces_module], {in_scope})
    r = client.post(
        f"{API}/characters/{character_id}/faces",
        json={"picture_ids": [out_of_scope]},
    )
    assert r.status_code == 403, r.text


def test_assign_face_denied_when_a_stack_sibling_is_out_of_scope(env, monkeypatch):
    """Stack-atomic assignment must authorize the whole expanded mutation."""
    server, client, picture_ids, character_id = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [characters_faces_module], None)
    stacked = client.post(
        f"{API}/stacks", json={"picture_ids": [in_scope, out_of_scope]}
    )
    assert stacked.status_code == 200, stacked.text

    _scope_to(monkeypatch, [characters_faces_module], {in_scope})
    response = client.post(
        f"{API}/characters/{character_id}/faces",
        json={"picture_ids": [in_scope]},
    )

    assert response.status_code == 403, response.text


def test_assign_face_face_ids_branch_denied(env, monkeypatch):
    """The face_ids alternate branch must resolve face -> picture and deny."""
    server, client, picture_ids, character_id = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    # Owner creates a real face on the out-of-scope picture, capture its id.
    _scope_to(monkeypatch, [crud_module], None)
    r = client.post(
        f"{API}/pictures/{out_of_scope}/face",
        json={"bbox": [1, 1, 20, 20], "frame_index": 0},
    )
    assert r.status_code == 200, r.text
    out_face_id = r.json()["id"]

    _scope_to(monkeypatch, [characters_faces_module], {in_scope})
    r = client.post(
        f"{API}/characters/{character_id}/faces",
        json={"face_ids": [out_face_id]},
    )
    assert r.status_code == 403, (
        "face_ids branch must resolve the face to its picture and deny an "
        f"out-of-scope target (alternate-branch BOLA); got {r.status_code}: {r.text}"
    )


def test_assign_authoritative_face_checks_actual_face_owner(env, monkeypatch):
    """A forged in-scope picture_id cannot launder an out-of-scope face_id."""
    server, client, picture_ids, character_id = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [crud_module], None)
    created = client.post(
        f"{API}/pictures/{out_of_scope}/face",
        json={"bbox": [1, 1, 20, 20], "frame_index": 0},
    )
    assert created.status_code == 200, created.text
    out_face_id = created.json()["id"]

    _scope_to(monkeypatch, [characters_faces_module], {in_scope})
    response = client.post(
        f"{API}/characters/{character_id}/faces",
        json={"face_assignments": [{"picture_id": in_scope, "face_id": out_face_id}]},
    )

    assert response.status_code == 403, response.text


def test_remove_character_face_ids_branch_denied(env, monkeypatch):
    server, client, picture_ids, character_id = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [crud_module], None)
    r = client.post(
        f"{API}/pictures/{out_of_scope}/face",
        json={"bbox": [1, 1, 20, 20], "frame_index": 0},
    )
    assert r.status_code == 200, r.text
    out_face_id = r.json()["id"]

    _scope_to(monkeypatch, [characters_faces_module], {in_scope})
    r = client.request(
        "DELETE",
        f"{API}/characters/{character_id}/faces",
        json={"face_ids": [out_face_id]},
    )
    assert r.status_code == 403, r.text


def test_assign_face_owner_not_blocked(env, monkeypatch):
    """Owner / unscoped token is not blocked by the face-mutation guard."""
    server, client, picture_ids, character_id = env
    _scope_to(monkeypatch, [characters_faces_module], None)
    r = client.post(
        f"{API}/characters/{character_id}/faces",
        json={"picture_ids": [picture_ids[1]]},
    )
    # 200 regardless of whether a face exists yet (deferred assignment); the
    # point is the scope guard did not 403 an owner request.
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# ComfyUI source-picture reads (CSO finding S2) - i2i uploads source bytes to
# the ComfyUI host, so it must be scoped. Owner direction: the guard passes, so
# the request gets past 403 (then fails downstream because the test env has no
# ComfyUI / workflow - i.e. NOT 403 is the success assertion).
# ---------------------------------------------------------------------------


def test_comfyui_i2i_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    # The owner counterpart asserts 404, which a deleted route also returns, so
    # neither test would notice this route disappearing. Middleware 403s ahead
    # of routing, so this one would not notice either.
    assert_real_route(server.api, "POST", f"{API}/comfyui/run_i2i")
    r = anon.post(
        f"{API}/comfyui/run_i2i",
        json={"workflow_name": "nonexistent", "picture_ids": [picture_ids[1]]},
        headers=_bearer(tok),
    )
    assert r.status_code == 403, r.text


def test_comfyui_i2i_owner_passes_scope_guard(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [comfyui_module], None)
    r = client.post(
        f"{API}/comfyui/run_i2i",
        json={"workflow_name": "nonexistent", "picture_ids": [picture_ids[1]]},
    )
    # Owner is not scope-blocked; it falls through to the missing-workflow 404.
    # Assert that exact status rather than a bare ``!= 403``: the loose form is
    # also satisfied by a 404 from a renamed route or a 500, so it would keep
    # passing after the handler it is meant to reach stopped existing.
    assert r.status_code == 404, r.text


def test_comfyui_t2i_source_picture_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    # This route has no owner counterpart anywhere in the file, so nothing else
    # would notice it being renamed away; middleware 403s before routing.
    assert_real_route(server.api, "POST", f"{API}/comfyui/run_t2i")
    r = anon.post(
        f"{API}/comfyui/run_t2i",
        json={"workflow_name": "nonexistent", "source_picture_id": picture_ids[1]},
        headers=_bearer(tok),
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Remix recipe routes (v1.9). Both read and replay the graph embedded in one
# picture's FILE, so both are per-object reads of that picture's contents and
# both must be scoped. Tested in both directions: over-blocking the owner is
# its own regression.
# ---------------------------------------------------------------------------


def test_comfyui_recipe_read_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    r = anon.get(
        f"{API}/comfyui/pictures/{picture_ids[1]}/recipe", headers=_bearer(tok)
    )
    assert r.status_code == 403, r.text


def test_comfyui_recipe_read_owner_succeeds(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [comfyui_module], None)
    r = client.get(f"{API}/comfyui/pictures/{picture_ids[1]}/recipe")
    # The test fixtures are ordinary photos with no embedded graph, so the
    # honest answer is a 200 saying so - NOT an error.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is False
    assert body["reason"] == "no_prompt_chunk"


def test_comfyui_run_recipe_scoped_token_blocked(env, scoped):
    server, client, picture_ids, _ = env
    anon, tok = scoped
    r = anon.post(
        f"{API}/comfyui/run_recipe",
        json={"picture_id": picture_ids[1]},
        headers=_bearer(tok),
    )
    assert r.status_code == 403, r.text


def test_comfyui_run_recipe_owner_passes_scope_guard(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [comfyui_module], None)
    r = client.post(f"{API}/comfyui/run_recipe", json={"picture_id": picture_ids[1]})
    # Owner is not scope-blocked; it reaches the handler and is refused for the
    # real reason - the picture carries no executable graph. Asserting the exact
    # status and detail rather than a bare `!= 403` keeps this from passing
    # after the handler it targets stops existing.
    assert r.status_code == 400, r.text
    assert "no executable workflow embedded" in r.json()["detail"]
