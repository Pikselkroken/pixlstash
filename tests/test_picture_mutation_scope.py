"""Object-scope (BOLA / CWE-639) tests for picture-MUTATION handlers.

Closes finding F1 in ``docs/reviews/feature-slick-grid-updates.md``: the
picture-mutation handlers (tag add/remove/clear, face create/delete, set-project,
apply-scores, run-plugin, character face assign/unassign) previously enforced no
object-level scope, so a resource-scoped token could mutate pictures outside its
grant. Each now calls the deny-by-default chokepoint
(``enforce_picture_scope`` for single-picture handlers,
``fetch_scope_allowed_picture_ids`` for batch handlers) before any DB
read/branch/return.

These tests assert both directions per CLAUDE.md:
- a scoped token (simulated by patching the scope helper to allow only one
  picture id, exactly as ``test_batch_character_likeness_scope.py`` does) is
  **denied** (403) when the target picture is outside its grant, including the
  ``face_ids`` and ``picture_ids`` alternate branches of the character handlers;
- an owner / unscoped token (scope helper returns ``None``) still **succeeds**,
  so the guards do not over-block (that would be its own regression).

Patching the scope helper is the same technique the existing batch-scope suite
uses: it exercises the handler's guard directly, independent of how a scoped
token reaches the handler (the middleware only populates ``token_scope`` for
non-ALL tokens — see ``docs/backend_architecture.md`` §16.2).
"""

import gc
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import pixlstash.routes.tags as tags_module
import pixlstash.routes.characters as characters_module
import pixlstash.routes.comfyui as comfyui_module
import pixlstash.routes.pictures._crud as crud_module
import pixlstash.routes.pictures._misc as misc_module
from pixlstash.routes.pictures import _helpers as helpers_module
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


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


@pytest.fixture
def env():
    """A live server with >=2 imported pictures and a reference character."""
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        )
        assert r.status_code == 200, r.text

        files = [("file", (n, d, c)) for n, d, c in _good_picture_files()[:4]]
        assert files, "No test pictures found in pictures/good/"
        st = upload_pictures_and_wait(client, files, timeout_s=30)
        assert st["status"] == "completed", st

        r = client.get(f"{API}/pictures")
        assert r.status_code == 200, r.text
        picture_ids = [p["id"] for p in r.json()]
        assert len(picture_ids) >= 2, "Need at least two pictures for the scope test"

        r = client.post(f"{API}/characters", json={"name": "ScopeChar"})
        assert r.status_code == 200, r.text
        character_id = r.json()["character"]["id"]

        yield server, client, picture_ids, character_id
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


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
    # enforce_picture_scope is imported by name into _crud / tags; the chokepoint
    # itself lives in _helpers. Patch the source too so any direct callers agree.
    monkeypatch.setattr(helpers_module, "enforce_picture_scope", fake_enforce)


# ---------------------------------------------------------------------------
# Single-picture handlers (enforce_picture_scope)
# ---------------------------------------------------------------------------


def test_add_tag_denied_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [tags_module], {in_scope})

    r_out = client.post(f"{API}/pictures/{out_of_scope}/tags", json={"tag": "x"})
    assert r_out.status_code == 403, r_out.text
    r_in = client.post(f"{API}/pictures/{in_scope}/tags", json={"tag": "x"})
    assert r_in.status_code == 200, r_in.text


def test_add_tag_owner_succeeds(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [tags_module], None)
    r = client.post(f"{API}/pictures/{picture_ids[1]}/tags", json={"tag": "y"})
    assert r.status_code == 200, r.text


def test_clear_tags_denied_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [tags_module], {in_scope})
    r_out = client.delete(f"{API}/pictures/{out_of_scope}/tags")
    assert r_out.status_code == 403, r_out.text
    r_in = client.delete(f"{API}/pictures/{in_scope}/tags")
    assert r_in.status_code == 200, r_in.text


def test_remove_tag_everywhere_denied_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [tags_module], {in_scope})
    r_out = client.post(
        f"{API}/pictures/{out_of_scope}/tags/remove_all", json={"tag": "x"}
    )
    assert r_out.status_code == 403, r_out.text


def test_create_face_denied_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [crud_module], {in_scope})
    body = {"bbox": [1, 1, 10, 10], "frame_index": 0}
    r_out = client.post(f"{API}/pictures/{out_of_scope}/face", json=body)
    assert r_out.status_code == 403, r_out.text
    r_in = client.post(f"{API}/pictures/{in_scope}/face", json=body)
    assert r_in.status_code == 200, r_in.text


def test_delete_face_denied_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    # Owner creates a face on the out-of-scope picture so a real face exists.
    _scope_to(monkeypatch, [crud_module], None)
    r = client.post(
        f"{API}/pictures/{out_of_scope}/face",
        json={"bbox": [1, 1, 10, 10], "frame_index": 0},
    )
    assert r.status_code == 200, r.text
    face_index = r.json().get("face_index", 0)

    _scope_to(monkeypatch, [crud_module], {in_scope})
    r_out = client.delete(f"{API}/pictures/{out_of_scope}/face/{face_index}")
    assert r_out.status_code == 403, r_out.text


def test_delete_picture_denied_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [crud_module], {in_scope})
    r_out = client.delete(f"{API}/pictures/{out_of_scope}")
    assert r_out.status_code == 403, r_out.text
    r_in = client.delete(f"{API}/pictures/{in_scope}")
    assert r_in.status_code == 200, r_in.text


def test_bulk_delete_denied_when_any_out_of_scope(env, monkeypatch):
    """Bulk soft-delete enforces scope on EVERY id before any write: a single
    out-of-scope id 403s the whole request and nothing is soft-deleted (no partial
    leak outside the token's grant)."""
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [crud_module], {in_scope})
    r_out = client.request(
        "DELETE",
        f"{API}/pictures",
        json={"picture_ids": [in_scope, out_of_scope]},
    )
    assert r_out.status_code == 403, r_out.text
    # Fail-closed: the in-scope picture in the same request must NOT have been
    # soft-deleted (it is still in the active listing).
    _scope_to(monkeypatch, [crud_module], None)
    r = client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    assert in_scope in {p["id"] for p in r.json()}, "partial soft-delete leaked"


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
    # 1001 ids (need not exist — the cap is checked before any DB access).
    r = client.request(
        "DELETE", f"{API}/pictures", json={"picture_ids": list(range(1, 1002))}
    )
    assert r.status_code == 422, r.text


def test_patch_picture_denied_out_of_scope(env, monkeypatch):
    """PATCH /pictures/{id} mutates score/description/tags — must be scoped.

    Regression for CSO finding S1: this mutator had no object-scope check while
    the coverage matrix claimed it did.
    """
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [crud_module], {in_scope})
    r_out = client.patch(f"{API}/pictures/{out_of_scope}", json={"score": 3})
    assert r_out.status_code == 403, r_out.text
    r_in = client.patch(f"{API}/pictures/{in_scope}", json={"score": 3})
    assert r_in.status_code == 200, r_in.text


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
# Character face handlers — BOTH the picture_ids and face_ids branches
# ---------------------------------------------------------------------------


def test_assign_face_picture_ids_branch_denied(env, monkeypatch):
    server, client, picture_ids, character_id = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [characters_module], {in_scope})
    r = client.post(
        f"{API}/characters/{character_id}/faces",
        json={"picture_ids": [out_of_scope]},
    )
    assert r.status_code == 403, r.text


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

    _scope_to(monkeypatch, [characters_module], {in_scope})
    r = client.post(
        f"{API}/characters/{character_id}/faces",
        json={"face_ids": [out_face_id]},
    )
    assert r.status_code == 403, (
        "face_ids branch must resolve the face to its picture and deny an "
        f"out-of-scope target (alternate-branch BOLA); got {r.status_code}: {r.text}"
    )


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

    _scope_to(monkeypatch, [characters_module], {in_scope})
    r = client.request(
        "DELETE",
        f"{API}/characters/{character_id}/faces",
        json={"face_ids": [out_face_id]},
    )
    assert r.status_code == 403, r.text


def test_assign_face_owner_not_blocked(env, monkeypatch):
    """Owner / unscoped token is not blocked by the face-mutation guard."""
    server, client, picture_ids, character_id = env
    _scope_to(monkeypatch, [characters_module], None)
    r = client.post(
        f"{API}/characters/{character_id}/faces",
        json={"picture_ids": [picture_ids[1]]},
    )
    # 200 regardless of whether a face exists yet (deferred assignment); the
    # point is the scope guard did not 403 an owner request.
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# ComfyUI source-picture reads (CSO finding S2) — i2i uploads source bytes to
# the ComfyUI host, so it must be scoped. Owner direction: the guard passes, so
# the request gets past 403 (then fails downstream because the test env has no
# ComfyUI / workflow — i.e. NOT 403 is the success assertion).
# ---------------------------------------------------------------------------


def test_comfyui_i2i_denied_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [comfyui_module], {in_scope})
    r_out = client.post(
        f"{API}/comfyui/run_i2i",
        json={"workflow_name": "nonexistent", "picture_ids": [out_of_scope]},
    )
    assert r_out.status_code == 403, r_out.text


def test_comfyui_i2i_owner_passes_scope_guard(env, monkeypatch):
    server, client, picture_ids, _ = env
    _scope_to(monkeypatch, [comfyui_module], None)
    r = client.post(
        f"{API}/comfyui/run_i2i",
        json={"workflow_name": "nonexistent", "picture_ids": [picture_ids[1]]},
    )
    # Owner is not scope-blocked; it falls through to the missing-workflow 404.
    assert r.status_code != 403, r.text


def test_comfyui_t2i_source_picture_denied_out_of_scope(env, monkeypatch):
    server, client, picture_ids, _ = env
    in_scope, out_of_scope = picture_ids[0], picture_ids[1]
    _scope_to(monkeypatch, [comfyui_module], {in_scope})
    r_out = client.post(
        f"{API}/comfyui/run_t2i",
        json={"workflow_name": "nonexistent", "source_picture_id": out_of_scope},
    )
    assert r_out.status_code == 403, r_out.text
