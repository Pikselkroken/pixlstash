"""Tests for the append-only operation log and metadata undo/redo (DAM 1.2).

Covers the three things the design rests on:

1. **Recording** — a metadata mutation appends exactly one operation carrying the
   changed facets, the batch id, and the WS-envelope provenance; a no-op
   mutation appends nothing.
2. **Undo / redo** — undo restores the recorded ``before`` state, redo restores
   ``after``, a bulk action is ONE undoable unit via its batch id, and recording
   a new operation invalidates the redo stack.
3. **The invariants** — the log is append-only (undo mutates only the lifecycle
   markers), the service never reads the origin contextvar, and a locked picture
   set is not walked around by undo.
4. **The scrapheap lifecycle** — a move to the Scrapheap and a restore out of it
   are recorded symmetrically and are reversible in both directions, a bulk move
   is one batch and one Undo, a **permanent** delete is recorded nowhere, and
   undoing a move whose picture has since been purged is refused outright
   (410) rather than half-applied.
"""

import gc
import io
import json
import os
import tempfile

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from pixlstash.db_models import (
    Operation,
    Picture,
    PictureSet,
    PictureSetMember,
    Tag,
    is_tag_sentinel,
)
from pixlstash.server import Server
from pixlstash.services import operation_log_service
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _teardown(temp_dir, server):
    server.vault.close()
    temp_dir.cleanup()
    gc.collect()


_counter = [0]


def _upload(client):
    """Upload a fresh, content-distinct in-memory PNG and return its id."""
    _counter[0] += 1
    n = _counter[0]
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return upload_pictures_and_wait(
        client, [("file", (f"op{n}.png", buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _tags(server, picture_id):
    """The picture's user-visible tags.

    The pending-retag sentinel (``__tag``) is machine bookkeeping written by the
    importer, not a user tag; it is filtered out here so the assertions read as
    the user experiences them. It IS part of the recorded before/after state —
    see the round-trip assertion in the recording test.
    """
    return sorted(
        server.vault.db.run_task(
            lambda session: [
                row.tag
                for row in session.exec(
                    select(Tag).where(Tag.picture_id == picture_id)
                ).all()
                if not is_tag_sentinel(row.tag)
            ]
        )
    )


def _operations(server, **filters):
    return operation_log_service.list_operations(server.vault, limit=100, **filters)


def _lifecycle(server, picture_id):
    """``(deleted, deleted_at)`` straight off the row, or ``None`` if purged."""

    def _read(session):
        picture = session.get(Picture, picture_id)
        if picture is None:
            return None
        return (bool(picture.deleted), picture.deleted_at)

    return server.vault.db.run_task(_read)


def _visible(client, picture_id):
    """Whether the picture shows up in the ordinary (non-scrapheap) listing."""
    resp = client.get(f"{API}/pictures", params={"limit": 500})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    pictures = payload["pictures"] if isinstance(payload, dict) else payload
    return picture_id in {int(pic["id"]) for pic in pictures}


def _purge_forever(client, ids):
    """Permanently destroy scrapheap rows through the real preview->confirm flow."""
    preview = client.post(f"{API}/pictures/scrapheap/delete-preview", json={"ids": ids})
    assert preview.status_code == 200, preview.text
    token = preview.json()["confirm_token"]
    resp = client.request(
        "DELETE",
        f"{API}/pictures/scrapheap",
        json={"picture_ids": ids, "include_protected": True, "confirm_token": token},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Pure unit tests — no server needed
# ---------------------------------------------------------------------------


def test_diff_states_keeps_only_changed_facets():
    before = {"1": {"tags": ["a"], "score": 3, "description": "x"}}
    after = {"1": {"tags": ["a", "b"], "score": 3, "description": "x"}}
    before_delta, after_delta = operation_log_service.diff_states(before, after)
    assert before_delta == {"1": {"tags": ["a"]}}
    assert after_delta == {"1": {"tags": ["a", "b"]}}


def test_diff_states_drops_unchanged_pictures_entirely():
    state = {"1": {"tags": ["a"]}, "2": {"tags": []}}
    before_delta, after_delta = operation_log_service.diff_states(state, state)
    assert before_delta == {}
    assert after_delta == {}


def test_request_context_reads_the_request_never_a_contextvar():
    """The §15 rule at the op-log's entry point: provenance comes off the request.

    A live ``origin_client_id_var`` must not leak into the recorded operation —
    the recorder runs on the DB worker thread where that contextvar is dead, so
    reading it anywhere downstream is the silent-misattribution bug.
    """
    from pixlstash.utils.request_origin import origin_client_id_var

    class _State:
        auth_user_id = 7
        origin_client_id = "tab-from-request"

    class _Request:
        state = _State()

    token = origin_client_id_var.set("contextvar-tab-should-be-ignored")
    try:
        context = operation_log_service.request_context(_Request())
    finally:
        origin_client_id_var.reset(token)

    assert context == {
        "actor": "7",
        "source": "ui",
        "origin_client_id": "tab-from-request",
    }

    # No X-Client-Id on the request -> the envelope's own defaults, still not
    # the (live) contextvar.
    class _BareState:
        pass

    class _BareRequest:
        state = _BareState()

    token = origin_client_id_var.set("contextvar-tab-should-be-ignored")
    try:
        bare = operation_log_service.request_context(_BareRequest())
    finally:
        origin_client_id_var.reset(token)
    assert bare == {"actor": None, "source": "external", "origin_client_id": None}


def test_service_module_never_reads_the_origin_contextvar():
    """Structural guard: a future edit cannot reintroduce a contextvar read.

    The same failure shape ``test_source_origin_read_from_data_only`` pins for
    the broadcaster, pinned here for the operation log — both run off the
    request's task, so both must take origin from data passed to them.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(operation_log_service))
    referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "origin_client_id_var" not in referenced, (
        "operation_log_service reads the origin contextvar. It runs on the DB "
        "worker thread where that contextvar is dead — origin must be passed in "
        "explicitly (docs/backend_architecture.md §15)."
    )
    imported = {
        alias_module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias_module in ([node.module] if node.module else [])
    }
    assert not any("request_origin" in module for module in imported)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


def test_tag_add_records_one_undoable_operation_with_provenance():
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        resp = client.post(
            f"{API}/pictures/{picture_id}/tags",
            json={"tag": "sunset"},
            headers={"X-Client-Id": "tab-1"},
        )
        assert resp.status_code == 200, resp.text

        operations = _operations(server, op_type="pictures.tags.add")
        assert len(operations) == 1
        operation = operations[0]
        assert operation["target_ids"] == [picture_id]
        assert operation["target_count"] == 1
        assert operation["undoable"] is True
        assert operation["status"] == "applied"
        # WS-envelope provenance, carried from the request header.
        assert operation["origin_client_id"] == "tab-1"
        assert operation["source"] == "ui"

        # The recorded state is the RAW tag list, sentinel included: adding the
        # first real tag also consumes the importer's pending-retag sentinel, and
        # undo must put that back or the picture silently leaves the retag queue.
        detail = client.get(f"{API}/operations/{operation['id']}").json()
        assert detail["before"] == {str(picture_id): {"tags": ["__tag"]}}
        assert detail["after"] == {str(picture_id): {"tags": ["sunset"]}}
    finally:
        _teardown(temp_dir, server)


def test_no_op_mutation_records_nothing():
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
        before = len(_operations(server))
        # Adding the same tag again changes nothing.
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
        assert len(_operations(server)) == before
    finally:
        _teardown(temp_dir, server)


def test_bulk_rating_is_one_operation_over_many_targets():
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(3)]
        resp = client.post(
            f"{API}/pictures/apply-scores",
            json={"scores": {str(pid): 4 for pid in ids}, "only_unscored": False},
        )
        assert resp.status_code == 200, resp.text

        operations = _operations(server, op_type="pictures.score")
        assert len(operations) == 1
        assert operations[0]["target_ids"] == sorted(ids)
        assert operations[0]["target_count"] == 3
    finally:
        _teardown(temp_dir, server)


def test_set_membership_is_recorded_and_undone():
    """The membership facet, end to end through the real endpoints."""
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        created = client.post(f"{API}/picture_sets", json={"name": "trip"})
        assert created.status_code == 200, created.text
        set_id = created.json()["picture_set"]["id"]

        resp = client.post(f"{API}/picture_sets/{set_id}/members/{picture_id}")
        assert resp.status_code == 200, resp.text

        def members(session):
            return sorted(
                int(row.picture_id)
                for row in session.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == set_id)
                ).all()
            )

        assert server.vault.db.run_task(members) == [picture_id]

        operations = _operations(server, op_type="picture_sets.members.add")
        assert len(operations) == 1
        assert operations[0]["target_ids"] == [picture_id]

        assert client.post(f"{API}/operations/undo").status_code == 200
        assert server.vault.db.run_task(members) == []
        assert client.post(f"{API}/operations/redo").status_code == 200
        assert server.vault.db.run_task(members) == [picture_id]
    finally:
        _teardown(temp_dir, server)


def test_stacking_is_recorded_and_undone():
    """The stack facet: undo unstacks, redo re-stacks the same pictures."""
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(2)]
        resp = client.post(f"{API}/stacks", json={"picture_ids": ids})
        assert resp.status_code == 200, resp.text

        def stack_ids(session):
            return sorted(
                (int(row.id), row.stack_id)
                for row in session.exec(
                    select(Picture).where(Picture.id.in_(ids))
                ).all()
            )

        stacked = server.vault.db.run_task(stack_ids)
        assert all(stack_id is not None for _pid, stack_id in stacked)

        operations = _operations(server, op_type="stacks.create")
        assert len(operations) == 1
        assert operations[0]["target_ids"] == sorted(ids)

        assert client.post(f"{API}/operations/undo").status_code == 200
        assert all(
            stack_id is None for _pid, stack_id in server.vault.db.run_task(stack_ids)
        )

        # Redo re-points the pictures at the same stack row.
        assert client.post(f"{API}/operations/redo").status_code == 200
        assert server.vault.db.run_task(stack_ids) == stacked
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


def test_undo_restores_before_state_and_redo_restores_after():
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
        assert _tags(server, picture_id) == ["sunset"]

        state = client.get(f"{API}/operations/undo-state").json()
        assert state["can_undo"] is True
        assert state["can_redo"] is False

        undo = client.post(f"{API}/operations/undo")
        assert undo.status_code == 200, undo.text
        assert undo.json()["picture_ids"] == [picture_id]
        assert _tags(server, picture_id) == []

        state = client.get(f"{API}/operations/undo-state").json()
        assert state["can_undo"] is False
        assert state["can_redo"] is True

        redo = client.post(f"{API}/operations/redo")
        assert redo.status_code == 200, redo.text
        assert _tags(server, picture_id) == ["sunset"]
    finally:
        _teardown(temp_dir, server)


def test_undo_of_bulk_rating_reverts_every_target():
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(3)]
        client.post(
            f"{API}/pictures/apply-scores",
            json={"scores": {str(pid): 4 for pid in ids}, "only_unscored": False},
        )

        def scores(session):
            return sorted(
                (int(row.id), row.score)
                for row in session.exec(
                    select(Picture).where(Picture.id.in_(ids))
                ).all()
            )

        assert [score for _pid, score in server.vault.db.run_task(scores)] == [4, 4, 4]

        assert client.post(f"{API}/operations/undo").status_code == 200
        assert [score for _pid, score in server.vault.db.run_task(scores)] == [
            None,
            None,
            None,
        ]
    finally:
        _teardown(temp_dir, server)


def test_undo_is_last_in_first_out():
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "one"})
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "two"})
        assert _tags(server, picture_id) == ["one", "two"]

        client.post(f"{API}/operations/undo")
        assert _tags(server, picture_id) == ["one"]
        client.post(f"{API}/operations/undo")
        assert _tags(server, picture_id) == []
    finally:
        _teardown(temp_dir, server)


def test_recording_a_new_operation_invalidates_the_redo_stack():
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "one"})
        client.post(f"{API}/operations/undo")
        assert client.get(f"{API}/operations/undo-state").json()["can_redo"] is True

        # A new change moves the history on; the undone operation can no longer
        # be replayed onto it.
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "two"})
        assert client.get(f"{API}/operations/undo-state").json()["can_redo"] is False
        assert client.post(f"{API}/operations/redo").status_code == 409

        superseded = _operations(server, status="superseded")
        assert len(superseded) == 1
        # The row survives — this is an audit log, not a stack that pops.
        assert superseded[0]["op_type"] == "pictures.tags.add"
    finally:
        _teardown(temp_dir, server)


def test_undo_with_nothing_to_undo_is_409():
    temp_dir, client, server = _setup()
    try:
        assert client.post(f"{API}/operations/undo").status_code == 409
        assert client.post(f"{API}/operations/redo").status_code == 409
    finally:
        _teardown(temp_dir, server)


def test_batch_undo_reverts_the_whole_bulk_action_in_one_call():
    """One bulk action = one batch id = one Undo, exactly as the sweep needs.

    Recorded through the service (the sweep's own service does not exist yet),
    then reverted through the public batch endpoint.
    """
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(2)]
        batch_id = operation_log_service.new_batch_id()

        def _tag_one(session, picture_id, tag):
            session.add(Tag(picture_id=picture_id, tag=tag))
            session.commit()

        for picture_id in ids:
            operation_log_service.run_recorded_metadata_task(
                server.vault,
                _tag_one,
                picture_id,
                "swept",
                op_type="test.sweep",
                picture_ids=[picture_id],
                batch_id=batch_id,
                summary="Swept",
                actor="1",
                source="ui",
                origin_client_id="tab-sweep",
            )

        assert all(_tags(server, pid) == ["swept"] for pid in ids)
        members = _operations(server, batch_id=batch_id)
        assert len(members) == 2

        resp = client.post(f"{API}/operations/batches/{batch_id}/undo")
        assert resp.status_code == 200, resp.text
        assert sorted(resp.json()["picture_ids"]) == sorted(ids)
        assert all(_tags(server, pid) == [] for pid in ids)
        assert all(
            op["status"] == "undone" for op in _operations(server, batch_id=batch_id)
        )

        # A second call has nothing left to revert.
        assert (
            client.post(f"{API}/operations/batches/{batch_id}/undo").status_code == 409
        )
    finally:
        _teardown(temp_dir, server)


def test_undoing_one_member_of_a_batch_reverts_the_whole_batch():
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(2)]
        batch_id = operation_log_service.new_batch_id()

        def _tag_one(session, picture_id, tag):
            session.add(Tag(picture_id=picture_id, tag=tag))
            session.commit()

        for picture_id in ids:
            operation_log_service.run_recorded_metadata_task(
                server.vault,
                _tag_one,
                picture_id,
                "swept",
                op_type="test.sweep",
                picture_ids=[picture_id],
                batch_id=batch_id,
            )

        newest = _operations(server, batch_id=batch_id)[0]
        resp = client.post(f"{API}/operations/{newest['id']}/undo")
        assert resp.status_code == 200, resp.text
        # Both members reverted — a partially-undone bulk action cannot exist.
        assert len(resp.json()["operations"]) == 2
        assert all(_tags(server, pid) == [] for pid in ids)
    finally:
        _teardown(temp_dir, server)


def test_log_is_append_only_across_undo_and_redo():
    """Undo/redo move the lifecycle marker only; no row is rewritten or removed."""
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})

        def snapshot(session):
            return [
                (
                    row.id,
                    row.op_type,
                    row.target_ids,
                    row.before_state,
                    row.after_state,
                    row.created_at,
                )
                for row in session.exec(select(Operation).order_by(Operation.id)).all()
            ]

        recorded = server.vault.db.run_task(snapshot)
        assert len(recorded) == 1

        client.post(f"{API}/operations/undo")
        client.post(f"{API}/operations/redo")

        assert server.vault.db.run_task(snapshot) == recorded
    finally:
        _teardown(temp_dir, server)


def test_undo_refuses_to_write_a_picture_frozen_by_a_locked_set():
    """A locked set is a hard freeze; undo must not be the way around it."""
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})

        def lock(session):
            picture_set = PictureSet(name="frozen", locked=True)
            session.add(picture_set)
            session.commit()
            session.refresh(picture_set)
            session.add(PictureSetMember(set_id=picture_set.id, picture_id=picture_id))
            session.commit()

        server.vault.db.run_task(lock)

        resp = client.post(f"{API}/operations/undo")
        assert resp.status_code == 423, resp.text
        assert _tags(server, picture_id) == ["sunset"]
        # The operation stays applied — a refused undo must not half-commit.
        assert _operations(server)[0]["status"] == "applied"
    finally:
        _teardown(temp_dir, server)


# ---------------------------------------------------------------------------
# Scrapheap lifecycle (soft delete / restore)
# ---------------------------------------------------------------------------


def test_scrapheap_move_is_recorded_and_undo_brings_the_picture_back():
    """The core promise: a move to the Scrapheap is reversible from the log."""
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        assert _visible(client, picture_id) is True

        resp = client.delete(
            f"{API}/pictures/{picture_id}", headers={"X-Client-Id": "tab-1"}
        )
        assert resp.status_code == 200, resp.text
        deleted, deleted_at = _lifecycle(server, picture_id)
        assert deleted is True
        assert deleted_at is not None
        assert _visible(client, picture_id) is False

        operations = _operations(server, op_type="pictures.scrapheap.move")
        assert len(operations) == 1
        operation = operations[0]
        assert operation["target_ids"] == [picture_id]
        assert operation["undoable"] is True
        assert operation["summary"] == "Moved 1 picture to the Scrapheap"
        assert operation["origin_client_id"] == "tab-1"

        # The recorded facet is the soft-delete state itself, retention stamp
        # included, so undo restores the deadline rather than inventing one.
        detail = client.get(f"{API}/operations/{operation['id']}").json()
        assert detail["before"][str(picture_id)]["deleted"] == {
            "deleted": False,
            "deleted_at": None,
        }
        assert detail["after"][str(picture_id)]["deleted"]["deleted"] is True
        assert detail["after"][str(picture_id)]["deleted"]["deleted_at"] is not None

        undo = client.post(f"{API}/operations/undo")
        assert undo.status_code == 200, undo.text
        assert undo.json()["restored_picture_ids"] == [picture_id]
        assert undo.json()["scrapheaped_picture_ids"] == []
        assert _lifecycle(server, picture_id) == (False, None)
        assert _visible(client, picture_id) is True

        # Redo puts it back in the Scrapheap, with the SAME retention stamp.
        redo = client.post(f"{API}/operations/redo")
        assert redo.status_code == 200, redo.text
        assert redo.json()["scrapheaped_picture_ids"] == [picture_id]
        assert redo.json()["restored_picture_ids"] == []
        assert _lifecycle(server, picture_id) == (True, deleted_at)
        assert _visible(client, picture_id) is False
    finally:
        _teardown(temp_dir, server)


def test_bulk_scrapheap_move_is_one_batch_and_one_undo():
    """Bulk = one batch id = one Undo, matching the log's grouping rule."""
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(3)]
        resp = client.request("DELETE", f"{API}/pictures", json={"picture_ids": ids})
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted_count"] == 3

        operations = _operations(server, op_type="pictures.scrapheap.move")
        assert len(operations) == 1
        operation = operations[0]
        assert operation["target_ids"] == sorted(ids)
        assert operation["target_count"] == 3
        assert operation["batch_id"]
        assert operation["summary"] == "Moved 3 pictures to the Scrapheap"
        assert all(_lifecycle(server, pid)[0] is True for pid in ids)

        # The batch endpoint reverts the whole move in one call.
        undo = client.post(f"{API}/operations/batches/{operation['batch_id']}/undo")
        assert undo.status_code == 200, undo.text
        assert sorted(undo.json()["restored_picture_ids"]) == sorted(ids)
        assert all(_lifecycle(server, pid) == (False, None) for pid in ids)

        redo = client.post(f"{API}/operations/redo")
        assert redo.status_code == 200, redo.text
        assert all(_lifecycle(server, pid)[0] is True for pid in ids)
    finally:
        _teardown(temp_dir, server)


def test_restore_from_the_scrapheap_is_recorded_symmetrically():
    """Undoing a restore puts the pictures back — the history stays coherent."""
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(2)]
        client.request("DELETE", f"{API}/pictures", json={"picture_ids": ids})
        stamps = {pid: _lifecycle(server, pid)[1] for pid in ids}
        assert all(stamp is not None for stamp in stamps.values())

        resp = client.post(f"{API}/pictures/scrapheap/restore")
        assert resp.status_code == 200, resp.text
        assert resp.json()["restored_count"] == 2
        assert all(_lifecycle(server, pid) == (False, None) for pid in ids)

        operations = _operations(server, op_type="pictures.scrapheap.restore")
        assert len(operations) == 1
        restore_op = operations[0]
        assert restore_op["target_ids"] == sorted(ids)
        assert restore_op["batch_id"]
        assert restore_op["summary"] == "Restored 2 pictures from the Scrapheap"

        # Undoing the restore is a re-scrapheap, stamp and all.
        undo = client.post(f"{API}/operations/undo")
        assert undo.status_code == 200, undo.text
        assert sorted(undo.json()["scrapheaped_picture_ids"]) == sorted(ids)
        for pid in ids:
            assert _lifecycle(server, pid) == (True, stamps[pid])

        # And redoing it restores them again.
        assert client.post(f"{API}/operations/redo").status_code == 200
        assert all(_lifecycle(server, pid) == (False, None) for pid in ids)
    finally:
        _teardown(temp_dir, server)


def test_restore_of_an_id_that_is_not_scrapheaped_records_nothing():
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        before = len(_operations(server))
        resp = client.post(
            f"{API}/pictures/scrapheap/restore", json={"picture_ids": [picture_id]}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["restored_count"] == 0
        assert len(_operations(server)) == before
    finally:
        _teardown(temp_dir, server)


def test_undoing_a_move_whose_picture_was_purged_refuses_and_changes_nothing():
    """The fail-closed edge case: a purge is permanent, so the undo is refused.

    Same contract as the locked-set guard — the WHOLE request is refused with a
    specific error, nothing is written, and the operation stays ``applied``
    rather than being marked undone over a change that did not happen.
    """
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        assert client.delete(f"{API}/pictures/{picture_id}").status_code == 200
        assert _operations(server, op_type="pictures.scrapheap.move")

        purged = _purge_forever(client, [picture_id])
        assert purged["deleted_count"] == 1
        assert _lifecycle(server, picture_id) is None

        resp = client.post(f"{API}/operations/undo")
        assert resp.status_code == 410, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "pictures_purged"
        assert detail["picture_ids"] == [picture_id]
        assert "permanently deleted" in detail["message"]

        # Refused, not half-applied.
        move = _operations(server, op_type="pictures.scrapheap.move")[0]
        assert move["status"] == "applied"
        assert move["undone_at"] is None
    finally:
        _teardown(temp_dir, server)


def test_a_partially_purged_bulk_move_refuses_the_whole_undo():
    """No silent partial success: one purged target refuses the entire batch."""
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(3)]
        client.request("DELETE", f"{API}/pictures", json={"picture_ids": ids})
        _purge_forever(client, [ids[1]])
        assert _lifecycle(server, ids[1]) is None

        resp = client.post(f"{API}/operations/undo")
        assert resp.status_code == 410, resp.text
        assert resp.json()["detail"]["picture_ids"] == [ids[1]]

        # The survivors are untouched — the refusal rolled the whole thing back.
        assert _lifecycle(server, ids[0])[0] is True
        assert _lifecycle(server, ids[2])[0] is True
        assert (
            _operations(server, op_type="pictures.scrapheap.move")[0]["status"]
            == "applied"
        )
    finally:
        _teardown(temp_dir, server)


def test_permanent_deletes_record_no_operation():
    """Purge / Empty Scrapheap are NOT undoable and must leave no log row."""
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(2)]
        client.request("DELETE", f"{API}/pictures", json={"picture_ids": ids})
        before = {op["id"] for op in _operations(server)}

        # Named selection, then "Empty Scrapheap" (no ids at all).
        _purge_forever(client, [ids[0]])
        preview = client.post(f"{API}/pictures/scrapheap/delete-preview", json=None)
        assert preview.status_code == 200, preview.text
        emptied = client.request(
            "DELETE",
            f"{API}/pictures/scrapheap",
            json={
                "include_protected": True,
                "confirm_token": preview.json()["confirm_token"],
            },
        )
        assert emptied.status_code == 200, emptied.text

        assert {op["id"] for op in _operations(server)} == before
        assert _operations(server, op_type="pictures.scrapheap.purge") == []
    finally:
        _teardown(temp_dir, server)


def test_scrapheap_move_of_a_stack_member_snapshots_the_whole_stack():
    """normalize_stack_positions renumbers siblings; undo must put them back."""
    temp_dir, client, server = _setup()
    try:
        ids = [_upload(client) for _ in range(3)]
        stacked = client.post(f"{API}/stacks", json={"picture_ids": ids})
        assert stacked.status_code == 200, stacked.text

        def positions(session):
            return {
                int(row.id): row.stack_position
                for row in session.exec(
                    select(Picture).where(Picture.id.in_(ids))
                ).all()
            }

        before = server.vault.db.run_task(positions)
        leader = next(pid for pid, pos in before.items() if pos == 0)

        assert client.delete(f"{API}/pictures/{leader}").status_code == 200
        after = server.vault.db.run_task(positions)
        assert after != before, "deleting the leader should promote a sibling"

        operation = _operations(server, op_type="pictures.scrapheap.move")[0]
        # Every renumbered sibling is in the snapshot, not just the deleted one.
        assert set(operation["target_ids"]) >= {
            pid for pid in ids if before[pid] != after[pid]
        }

        assert client.post(f"{API}/operations/undo").status_code == 200
        assert server.vault.db.run_task(positions) == before
        assert _lifecycle(server, leader) == (False, None)
    finally:
        _teardown(temp_dir, server)


def test_scrapheap_move_of_a_locked_picture_is_refused_and_records_nothing():
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)

        def lock(session):
            picture_set = PictureSet(name="frozen", locked=True)
            session.add(picture_set)
            session.commit()
            session.refresh(picture_set)
            session.add(PictureSetMember(set_id=picture_set.id, picture_id=picture_id))
            session.commit()

        server.vault.db.run_task(lock)

        assert client.delete(f"{API}/pictures/{picture_id}").status_code == 423
        assert _lifecycle(server, picture_id) == (False, None)
        assert _operations(server, op_type="pictures.scrapheap.move") == []
    finally:
        _teardown(temp_dir, server)


def test_scrapheap_summaries_count_the_recorded_change_not_the_request():
    """A skipped (locked / already-deleted) picture must not inflate the toast."""
    move = operation_log_service.scrapheap_move_summary
    restore = operation_log_service.scrapheap_restore_summary
    facet = operation_log_service.FACET_DELETED

    after = {
        "1": {facet: {"deleted": True, "deleted_at": "2026-07-29T00:00:00"}},
        "2": {facet: {"deleted": True, "deleted_at": "2026-07-29T00:00:00"}},
        # A stack sibling that was only renumbered is not a move.
        "3": {"stack": {"id": 1, "name": None, "position": 0}},
    }
    assert move({}, after) == "Moved 2 pictures to the Scrapheap"
    assert restore({}, after) is None

    restored = {"9": {facet: {"deleted": False, "deleted_at": None}}}
    assert restore({}, restored) == "Restored 1 picture from the Scrapheap"
    assert move({}, restored) is None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_list_filters_and_rejects_a_bad_status():
    temp_dir, client, server = _setup()
    try:
        picture_id = _upload(client)
        client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})

        assert (
            client.get(f"{API}/operations", params={"status": "bogus"}).status_code
            == 400
        )

        applied = client.get(f"{API}/operations", params={"status": "applied"}).json()
        assert len(applied) == 1
        # The list omits the (potentially huge) before/after payloads.
        assert "before" not in applied[0]

        assert client.get(f"{API}/operations", params={"op_type": "nope"}).json() == []
    finally:
        _teardown(temp_dir, server)


def test_get_unknown_operation_is_404():
    temp_dir, client, server = _setup()
    try:
        assert client.get(f"{API}/operations/999999").status_code == 404
        assert client.post(f"{API}/operations/999999/undo").status_code == 409
    finally:
        _teardown(temp_dir, server)


def test_every_operations_route_is_declared_owner_only():
    """Both-direction authz record: the gate, not a handler, guards these.

    Arithmetic completeness — every mounted /operations route has a declaration
    and every declaration is OWNER_ONLY. The positive direction (the owner
    reaches them) is exercised by every other test in this module.
    """
    from pixlstash.authz.policy import AccessPolicy
    from pixlstash.authz.registry import ROUTE_POLICIES

    declared = {
        (method, path): policy
        for (method, path), policy in ROUTE_POLICIES.items()
        if path.startswith("/api/v1/operations")
    }
    assert set(declared) == {
        ("GET", "/api/v1/operations"),
        ("GET", "/api/v1/operations/undo-state"),
        ("GET", "/api/v1/operations/{operation_id}"),
        ("POST", "/api/v1/operations/undo"),
        ("POST", "/api/v1/operations/redo"),
        ("POST", "/api/v1/operations/{operation_id}/undo"),
        ("POST", "/api/v1/operations/batches/{batch_id}/undo"),
    }
    assert all(policy.policy is AccessPolicy.OWNER_ONLY for policy in declared.values())


def test_operations_routes_have_no_inline_authz_check():
    """§16.1: the gate owns object authorization; handlers carry none."""
    import inspect

    from pixlstash.routes import operations as operations_routes

    source = inspect.getsource(operations_routes)
    for forbidden in (
        "enforce_picture_scope",
        "require_unscoped_owner",
        "fetch_scope_allowed_picture_ids",
        "token_scope",
    ):
        assert forbidden not in source, (
            f"{forbidden} is an inline authz check; the AuthzGate owns "
            "authorization for these routes (docs/backend_architecture.md §16.1)"
        )
