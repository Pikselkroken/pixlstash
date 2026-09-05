"""Tests for the append-only operation log and metadata undo/redo (DAM 1.2).

Covers the three things the design rests on:

1. **Recording** - a metadata mutation appends exactly one operation carrying the
   changed facets, the batch id, and the WS-envelope provenance; a no-op
   mutation appends nothing.
2. **Undo / redo** - undo restores the recorded ``before`` state, redo restores
   ``after``, a bulk action is ONE undoable unit via its batch id, and recording
   a new operation invalidates the redo stack.
3. **The invariants** - the log is append-only (undo mutates only the lifecycle
   markers), the service never reads the origin contextvar, and a locked picture
   set is not walked around by undo.
4. **The scrapheap lifecycle** - a move to the Scrapheap and a restore out of it
   are recorded symmetrically and are reversible in both directions, a bulk move
   is one batch and one Undo, a **permanent** delete is recorded nowhere, and
   undoing a move whose picture has since been purged is refused outright
   (410) rather than half-applied.
5. **The tag-review decisions** (§21.2) - confirming and rejecting a tag
   prediction are recorded, and their undo reverses the *whole* decision: the
   Tag row, the prediction status AND the human-label ledger, with the derived
   ``anomaly_tag_uncertainty`` recomputed and the cached smart score dropped.
   A half-undo - the tag back but the rejection still on file - is the failure
   these tests exist to prevent.
"""

import gc
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import delete, select, update

from pixlstash.db_models import (
    Operation,
    Picture,
    PictureSet,
    PictureSetMember,
    PictureStack,
    Tag,
    TagPrediction,
    is_tag_sentinel,
)
from pixlstash.server import Server
from pixlstash.services import operation_log_service
from pixlstash.tasks import TaskType
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


# Backfill finders whose task owns a row or column this module hand-writes.
# They are taken out of the planner for the lifetime of the module server (see
# ``_disable_conflicting_backfill``).
_CONFLICTING_FINDERS = (
    # TagTask runs `delete(Tag).where(Tag.picture_id.in_(...))` before writing
    # its own labels, so it removes a seeded Tag outright and puts real ones in
    # its place; it also rewrites tag_prediction and anomaly_tag_uncertainty and
    # invalidates smart_score. Every `_tags()` assertion here reads that table.
    TaskType.TAGGER,
    # TagPredictionBackfillTask rewrites tag_prediction, which the review
    # decision tests seed by hand and then assert is absent again after an undo.
    TaskType.TAG_PREDICTION_BACKFILL,
    # SmartScoreTask fills any NULL smart_score. Dropping the cached score is
    # part of what an undo must do, so a sweep that refills it turns the
    # `score is None` assertion into a coin flip. It is only reachable at all
    # once the tagger stops being in flight, which detaching TAGGER causes:
    # detach_finders() marks a removed finder exhausted so its dependents are
    # not blocked for ever.
    TaskType.SMART_SCORE,
)


def _disable_conflicting_backfill(server):
    """Take the backfill finders that fight this module's fixtures out of the planner.

    These tests write ``Tag`` and ``TagPrediction`` rows straight into the vault
    and then assert on the exact tag list and the exact prediction/ledger state
    that an undo leaves behind. The tagging sweep owns those same rows: it
    deletes the seeded tag, writes real labels over it, and recomputes the two
    derived score columns beside them.

    The per-test servers this module used to build hid the race. A vault that
    had just come up had nothing to backfill and no model loaded, so the sweep
    was still in a long backoff when the test ended. The shared server is warm
    and its work pool only grows (``reset_operation_log`` never wipes
    ``picture``), so the sweep lands *inside* a test instead: a run of this file
    submits several ``TagTask`` batches, and a batch that lands between a seed
    and its assertion fails it. Two or three failures per run, never the same
    ones.

    Only these finders go, and the planner keeps running. Detaching the whole
    set the way ``tests/test_picture_mutation_scope.py`` does is not an option
    here: that module imports its library once up front, whereas nearly every
    test in this one uploads, and the import endpoint refuses a picture outright
    with ``Cannot import: no TaskType.FACE_EXTRACTION finder is registered with
    the work planner.`` Adding ``FACE_EXTRACTION`` to the tuple above was tried
    and turned 51 of these 63 tests red.

    Returns the names of the finders it removed, so ``reset_operation_log`` can
    re-check before every test that they are still gone.
    """
    for task_type in _CONFLICTING_FINDERS:
        server.vault._planner_work_finders.pop(task_type)
    # detach_finders() edits the planner's finder structures under its own lock,
    # so this is safe against the loop thread that is running right now.
    return server.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)


@pytest.fixture(scope="module")
def _env():
    """One logged-in Server + TestClient shared by every test in this module.

    Building a Server (migrations, vault start-up, route registration) and
    tearing it down again costs well over a second, and minting the login
    credential costs another half-second in deliberately slow password
    hashing. Both used to be paid ~50 times over. They are paid once here
    instead, and per-test isolation comes from ``reset_operation_log`` below.
    """
    temp_dir = tempfile.TemporaryDirectory()
    try:
        os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
        server_config_path = os.path.join(temp_dir.name, "server-config.json")
        with open(server_config_path, "w") as fh:
            fh.write(json.dumps({"port": 8000}))
        server = Server(server_config_path)
        disabled_finders = _disable_conflicting_backfill(server)
        try:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200
            yield client, server, disabled_finders
        finally:
            # The detachment does not need undoing: it edits this server's own
            # planner, and closing the server destroys it.
            server.close()
    finally:
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture
def client(_env):
    return _env[0]


@pytest.fixture
def server(_env):
    return _env[1]


@pytest.fixture(autouse=True)
def reset_operation_log(_env):
    """Start every test from an empty operation log and an empty Scrapheap.

    This is an audit-log suite: assertions read the newest entry, count the
    entries of a type, or require that nothing at all was recorded. Sharing one
    Server would otherwise let an earlier test's entries be read as this
    test's: an assertion that passes for the wrong reason, which is worse than
    one that fails. Truncating ``operation`` (nothing references it by fk,
    and only request-driven code writes it) puts each test back on the clean
    log its assertions assume, including the derived undo/redo state, which is
    read straight off these rows.

    The Scrapheap is the other thing a test can observe globally:
    ``POST /pictures/scrapheap/restore`` with no body restores *every*
    soft-deleted picture, so pictures a previous test left scrapheaped would
    inflate its ``restored_count``. Clearing the flag is the whole reset: the
    endpoint's own query is ``Picture.deleted is True`` and nothing else.

    The third check is that the finders ``_disable_conflicting_backfill``
    removed are still gone, so a later test cannot silently run with the tagging
    sweep rewriting the rows it seeds.

    Note the reset deliberately does not wipe ``picture``: the stale-claim /
    id-reuse hazard that forces other shared-server modules to stop the
    schedulers before truncating is not reachable from here.

    All three integrity checks live here rather than in a "runs last" canary
    test on purpose: the CI gate shards tests individually, so a canary only
    guards its own shard while an autouse fixture runs ahead of every test in
    every shard. They assert the log and the Scrapheap *are* empty rather than
    comparing counts, because a leaked row is exactly what corrupts a count.
    """
    _client, server, disabled_finders = _env

    def _reset(session):
        session.exec(delete(Operation))
        session.exec(
            update(Picture)
            .where(Picture.deleted.is_(True))
            .values(deleted=False, deleted_at=None)
        )
        session.commit()

    server.vault.db.run_task(_reset)

    running = server.vault._work_planner.registered_finder_names()
    assert running.isdisjoint(disabled_finders), (
        "a backfill finder that rewrites this module's Tag / TagPrediction / "
        f"smart_score fixtures is running again: {sorted(running & disabled_finders)}"
    )
    assert _operations(server) == [], (
        "the operation log must be empty at the start of every test; the "
        "truncation above is what makes this module's shared Server safe"
    )
    assert _scrapheaped(server) == [], (
        "the Scrapheap must be empty at the start of every test, or a "
        "whole-Scrapheap restore picks up another test's pictures"
    )
    yield


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
    the user experiences them. It IS part of the recorded before/after state -
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


def _scrapheaped(server):
    """The ids currently sitting in the Scrapheap, newest id last."""
    return server.vault.db.run_task(
        lambda session: sorted(
            int(pid)
            for pid in session.exec(
                select(Picture.id).where(Picture.deleted.is_(True))
            ).all()
        )
    )


def _lifecycle(server, picture_id):
    """``(deleted, deleted_at)`` straight off the row, or ``None`` if purged."""

    def _read(session):
        picture = session.get(Picture, picture_id)
        if picture is None:
            return None
        return (bool(picture.deleted), picture.deleted_at)

    return server.vault.db.run_task(_read)


def _stack_state(server, stack_id, picture_ids):
    """``(row_exists, {picture_id: stack_id})`` for the orphan-row assertions."""

    def _read(session):
        row = session.get(PictureStack, stack_id)
        pointers = {
            int(pic.id): pic.stack_id
            for pic in session.exec(
                select(Picture).where(Picture.id.in_(picture_ids))
            ).all()
        }
        return (row is not None, pointers)

    return server.vault.db.run_task(_read)


def _visible(client, picture_id):
    """Whether the picture shows up in the ordinary (non-scrapheap) listing."""
    resp = client.get(f"{API}/pictures", params={"limit": 500})
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    pictures = payload["pictures"] if isinstance(payload, dict) else payload
    return picture_id in {int(pic["id"]) for pic in pictures}


def _seed_prediction(
    server, picture_id, tag, confidence=0.99, status="PENDING", model_version="test-v1"
):
    """Write the tagger prediction row a review decision adjudicates."""

    def _insert(session):
        session.add(
            TagPrediction(
                picture_id=picture_id,
                tag=tag,
                confidence=confidence,
                model_version=model_version,
                status=status,
                predicted_at=datetime.utcnow(),
            )
        )
        session.commit()

    server.vault.db.run_task(_insert)


def _prediction(server, picture_id, tag):
    """The prediction row + its human-label ledger, or ``None`` if there is none.

    This is what the tagger and the training exporter read: ``label_source`` is
    what makes a POS/NEG real supervision, so an undo that leaves it set has not
    undone the decision no matter what the tag list says.
    """

    def _read(session):
        row = session.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == picture_id,
                TagPrediction.tag == tag,
            )
        ).first()
        if row is None:
            return None
        return {
            "model_version": row.model_version,
            "confidence": row.confidence,
            "status": row.status,
            "label_state": row.label_state,
            "label_source": row.label_source,
            "label_model_version": row.label_model_version,
            "label_confidence": row.label_confidence,
        }

    return server.vault.db.run_task(_read)


def _picture_scores(server, picture_id):
    """``(anomaly_tag_uncertainty, smart_score)`` - the two derived values."""

    def _read(session):
        picture = session.get(Picture, picture_id)
        return (picture.anomaly_tag_uncertainty, picture.smart_score)

    return server.vault.db.run_task(_read)


def _set_smart_score(server, picture_id, value):
    def _write(session):
        picture = session.get(Picture, picture_id)
        picture.smart_score = value
        session.add(picture)
        session.commit()

    server.vault.db.run_task(_write)


def _lock_picture(server, picture_id, name="frozen"):
    def _lock(session):
        picture_set = PictureSet(name=name, locked=True)
        session.add(picture_set)
        session.commit()
        session.refresh(picture_set)
        session.add(PictureSetMember(set_id=picture_set.id, picture_id=picture_id))
        session.commit()

    server.vault.db.run_task(_lock)


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
# Pure unit tests - no server needed
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

    A live ``origin_client_id_var`` must not leak into the recorded operation -
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
        "batch_id": None,
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
    assert bare == {
        "actor": None,
        "source": "external",
        "origin_client_id": None,
        "batch_id": None,
    }


def test_request_context_takes_the_gesture_batch_id_from_the_request():
    """One gesture, one batch: the correlation id rides the request, too.

    ``fallback_batch_id`` is what a handler that is a bulk action in its own
    right passes so it stays batched when the caller sent no header; a header
    the middleware accepted wins over it, which is what makes a compound client
    gesture a single undo unit.
    """

    class _State:
        auth_user_id = 7
        origin_client_id = "tab-1"
        operation_batch_id = "cli-gesture-1"

    class _Request:
        state = _State()

    assert operation_log_service.request_context(_Request())["batch_id"] == (
        "cli-gesture-1"
    )
    assert operation_log_service.request_context(
        _Request(), fallback_batch_id="srv-abc"
    )["batch_id"] == ("cli-gesture-1")

    class _BareState:
        pass

    class _BareRequest:
        state = _BareState()

    assert operation_log_service.request_context(_BareRequest())["batch_id"] is None
    assert (
        operation_log_service.request_context(
            _BareRequest(), fallback_batch_id="srv-abc"
        )["batch_id"]
        == "srv-abc"
    )


def test_a_client_batch_id_can_never_impersonate_a_server_minted_one():
    """The namespace guard: ``cli-`` is the client's, ``srv-`` is the server's.

    A caller-supplied correlation id is a grouping hint over its own history
    (§21.2), but it must not be able to attach its requests to a batch the
    server created - so the validator only accepts the ``cli-`` namespace and
    ``new_batch_id`` only mints ``srv-``.
    """
    from pixlstash.utils.request_origin import (
        MAX_OPERATION_BATCH_ID_LENGTH,
        sanitize_operation_batch_id,
    )

    server_minted = operation_log_service.new_batch_id()
    assert server_minted.startswith(operation_log_service.SERVER_BATCH_ID_PREFIX)
    assert sanitize_operation_batch_id(server_minted) is None

    assert sanitize_operation_batch_id("cli-abcd1234") == "cli-abcd1234"
    # Rejected, never raised and never truncated: a bad header must not 500 and
    # a crafted long value must not collide with a legitimate short one.
    for rejected in (
        None,
        "",
        "srv-deadbeef",
        "abcd1234",
        "cli-",
        "cli-a",
        "cli-has spaces",
        "cli-../../etc/passwd",
        "cli-" + "a" * MAX_OPERATION_BATCH_ID_LENGTH,
    ):
        assert sanitize_operation_batch_id(rejected) is None, rejected


def test_service_module_never_reads_the_origin_contextvar():
    """Structural guard: a future edit cannot reintroduce a contextvar read.

    The same failure shape ``test_source_origin_read_from_data_only`` pins for
    the broadcaster, pinned here for the operation log - both run off the
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
        "worker thread where that contextvar is dead - origin must be passed in "
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


def test_tag_add_records_one_undoable_operation_with_provenance(client, server):
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


def test_no_op_mutation_records_nothing(client, server):
    picture_id = _upload(client)
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
    before = len(_operations(server))
    # Adding the same tag again changes nothing.
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
    assert len(_operations(server)) == before


@pytest.mark.parametrize("failure_stage", ["capture", "record", "serialize"])
def test_recording_failure_rolls_back_the_tag_mutation(
    monkeypatch, failure_stage, client, server
):
    """The receipt is part of the write: no receipt means no domain mutation."""
    picture_id = _upload(client)
    before_operations = len(_operations(server))

    def add_tag(session, pid):
        session.add(Tag(picture_id=pid, tag="atomic"))
        session.flush()

    if failure_stage == "capture":
        real_capture = operation_log_service.capture_state_in_session
        calls = 0

        def fail_second_capture(session, picture_ids):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("after capture failed")
            return real_capture(session, picture_ids)

        monkeypatch.setattr(
            operation_log_service,
            "capture_state_in_session",
            fail_second_capture,
        )
    elif failure_stage == "record":
        monkeypatch.setattr(
            operation_log_service,
            "record_operation_in_session",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("record failed")
            ),
        )
    else:
        monkeypatch.setattr(
            operation_log_service,
            "serialize",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("serialize failed")
            ),
        )

    with pytest.raises(RuntimeError):
        operation_log_service.run_recorded_metadata_task(
            server.vault,
            add_tag,
            picture_id,
            op_type="test.atomic-tag",
            picture_ids=[picture_id],
        )

    assert _tags(server, picture_id) == []
    assert len(_operations(server)) == before_operations


def test_recorded_callback_cannot_commit_independently(client, server):
    """Fail closed if a future callback reintroduces a premature commit."""
    picture_id = _upload(client)

    def bad_callback(session, pid):
        session.add(Tag(picture_id=pid, tag="escaped"))
        session.commit()

    with pytest.raises(RuntimeError, match="attempted to commit independently"):
        operation_log_service.run_recorded_metadata_task(
            server.vault,
            bad_callback,
            picture_id,
            op_type="test.bad-commit",
            picture_ids=[picture_id],
        )

    assert _tags(server, picture_id) == []
    assert _operations(server, op_type="test.bad-commit") == []


def test_an_empty_diff_is_recorded_only_when_targets_are_declared(client, server):
    """The empty-diff escape hatch for operations with no picture facet.

    Default behaviour is unchanged - an empty diff records nothing, so a no-op
    endpoint cannot consume a Ctrl+Z. Passing ``empty_diff_target_ids`` records
    the row anyway, with empty payloads and those targets: the path the dedup
    keep-separate verdict uses, whose whole restore is its post-restore hook.
    """
    picture_id = _upload(client)

    def record(session, target_ids):
        # Serialized in-session: the row would be detached once the DB
        # worker closes the session.
        operation = operation_log_service.record_operation_in_session(
            session,
            op_type="test.no_facet",
            before={},
            after={},
            summary="No facet changed",
            empty_diff_target_ids=target_ids,
        )
        return (
            operation_log_service.serialize(operation, include_state=True)
            if operation is not None
            else None
        )

    assert server.vault.db.run_task(record, None) is None
    operation = server.vault.db.run_task(record, [picture_id])
    assert operation is not None
    assert operation["target_ids"] == [picture_id]
    assert operation["target_count"] == 1
    assert operation["before"] == {}
    assert operation["after"] == {}
    assert operation["undoable"] is True
    assert operation["summary"] == "No facet changed"
    # Undo finds it, writes no picture facet, and still reports (and
    # announces) the declared targets - their domain state is what the
    # operation's post-restore hook changes.
    undone = client.post(f"{API}/operations/undo", json={})
    assert undone.status_code == 200, undone.text
    assert undone.json()["picture_ids"] == [picture_id]
    assert [op["op_type"] for op in undone.json()["operations"]] == ["test.no_facet"]


def test_bulk_rating_is_one_operation_over_many_targets(client, server):
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


def test_set_membership_is_recorded_and_undone(client, server):
    """The membership facet, end to end through the real endpoints."""
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


def test_record_failure_rolls_back_set_membership(monkeypatch, client, server):
    """Membership joins cannot commit without their operation receipt."""
    picture_id = _upload(client)
    created = client.post(f"{API}/picture_sets", json={"name": "atomic-set"})
    set_id = created.json()["picture_set"]["id"]
    monkeypatch.setattr(
        operation_log_service,
        "record_operation_in_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("membership receipt failed")
        ),
    )

    with pytest.raises(RuntimeError, match="membership receipt failed"):
        client.post(f"{API}/picture_sets/{set_id}/members/{picture_id}")

    members = server.vault.db.run_task(
        lambda session: list(
            session.exec(
                select(PictureSetMember).where(
                    PictureSetMember.set_id == set_id,
                    PictureSetMember.picture_id == picture_id,
                )
            ).all()
        )
    )
    assert members == []
    assert _operations(server, op_type="picture_sets.members.add") == []


def test_stacking_is_recorded_and_undone(client, server):
    """The stack facet: undo unstacks, redo re-stacks the same pictures."""
    ids = [_upload(client) for _ in range(2)]
    resp = client.post(f"{API}/stacks", json={"picture_ids": ids})
    assert resp.status_code == 200, resp.text

    def stack_ids(session):
        return sorted(
            (int(row.id), row.stack_id)
            for row in session.exec(select(Picture).where(Picture.id.in_(ids))).all()
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


def test_undo_of_stack_creation_deletes_the_emptied_stack_row(client, server):
    """Issue #643 (CSO finding C3): undoing a stack creation must not leave an
    orphaned empty PictureStack row behind - the mirror of undo-of-dissolve
    recreating the row."""
    ids = [_upload(client) for _ in range(2)]
    resp = client.post(f"{API}/stacks", json={"picture_ids": ids})
    assert resp.status_code == 200, resp.text
    stack_id = int(resp.json()["id"])

    row_exists, pointers = _stack_state(server, stack_id, ids)
    assert row_exists
    assert all(pointer == stack_id for pointer in pointers.values())

    assert client.post(f"{API}/operations/undo").status_code == 200

    row_exists, pointers = _stack_state(server, stack_id, ids)
    assert all(pointer is None for pointer in pointers.values())
    assert not row_exists, "undo left an orphaned empty PictureStack row"


def test_undo_keeps_a_stack_an_outside_picture_still_points_at(client, server):
    """A stack with members the restored operations never touched survives the
    cleanup: over-deletion would break a live pointer, which is worse than the
    row leak the cleanup fixes."""
    ids = [_upload(client) for _ in range(2)]
    resp = client.post(f"{API}/stacks", json={"picture_ids": ids})
    assert resp.status_code == 200, resp.text
    stack_id = int(resp.json()["id"])

    # A third picture joins the stack via the (unrecorded) add-members
    # endpoint, so it is outside the operation about to be undone.
    outsider = _upload(client)
    resp = client.post(
        f"{API}/stacks/{stack_id}/members", json={"picture_ids": [outsider]}
    )
    assert resp.status_code == 200, resp.text

    assert client.post(f"{API}/operations/undo").status_code == 200

    row_exists, pointers = _stack_state(server, stack_id, ids + [outsider])
    assert all(pointers[pid] is None for pid in ids)
    assert pointers[outsider] == stack_id
    assert row_exists, "cleanup deleted a stack that still had a member"


def test_undo_of_a_dissolve_recreates_the_row_and_redo_deletes_it_again(client, server):
    """The pre-existing dissolve-undo behaviour (row recreation) still holds,
    and its redo now removes the recreated row again - matching the forward
    dissolve, which deletes the row itself."""
    ids = [_upload(client) for _ in range(2)]
    resp = client.post(f"{API}/stacks", json={"picture_ids": ids})
    assert resp.status_code == 200, resp.text
    stack_id = int(resp.json()["id"])

    # Removing one member leaves <= 1 behind: the dissolve branch unstacks
    # the survivor too and deletes the stack row.
    resp = client.request(
        "DELETE",
        f"{API}/stacks/{stack_id}/members",
        json={"picture_ids": [ids[0]]},
    )
    assert resp.status_code == 200, resp.text
    row_exists, pointers = _stack_state(server, stack_id, ids)
    assert not row_exists
    assert all(pointer is None for pointer in pointers.values())

    # Undo recreates the row under its original id and restores both members.
    assert client.post(f"{API}/operations/undo").status_code == 200
    row_exists, pointers = _stack_state(server, stack_id, ids)
    assert row_exists, "undo of a dissolve no longer recreates the stack row"
    assert all(pointer == stack_id for pointer in pointers.values())

    # Redo replays the dissolve: members off, and the emptied row goes too.
    assert client.post(f"{API}/operations/redo").status_code == 200
    row_exists, pointers = _stack_state(server, stack_id, ids)
    assert all(pointer is None for pointer in pointers.values())
    assert not row_exists, "redo of a dissolve left the recreated row behind"


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


def test_undo_restores_before_state_and_redo_restores_after(client, server):
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


def test_undo_of_bulk_rating_reverts_every_target(client, server):
    ids = [_upload(client) for _ in range(3)]
    client.post(
        f"{API}/pictures/apply-scores",
        json={"scores": {str(pid): 4 for pid in ids}, "only_unscored": False},
    )

    def scores(session):
        return sorted(
            (int(row.id), row.score)
            for row in session.exec(select(Picture).where(Picture.id.in_(ids))).all()
        )

    assert [score for _pid, score in server.vault.db.run_task(scores)] == [4, 4, 4]

    assert client.post(f"{API}/operations/undo").status_code == 200
    assert [score for _pid, score in server.vault.db.run_task(scores)] == [
        None,
        None,
        None,
    ]


def test_undo_is_last_in_first_out(client, server):
    picture_id = _upload(client)
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "one"})
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "two"})
    assert _tags(server, picture_id) == ["one", "two"]

    client.post(f"{API}/operations/undo")
    assert _tags(server, picture_id) == ["one"]
    client.post(f"{API}/operations/undo")
    assert _tags(server, picture_id) == []


def test_named_undo_rejects_a_stale_operation_without_writes(client, server):
    picture_id = _upload(client)
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "one"})
    older = _operations(server)[0]
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "two"})

    response = client.post(f"{API}/operations/{older['id']}/undo")

    assert response.status_code == 409, response.text
    assert "must be undone first" in response.json()["detail"]
    assert _tags(server, picture_id) == ["one", "two"]
    assert [op["status"] for op in _operations(server)[:2]] == [
        "applied",
        "applied",
    ]


def test_batch_undo_rejects_an_interleaved_newer_operation_without_writes(
    client, server
):
    """A latest batch id is still stale when another unit landed inside it."""
    first, second, unrelated = [_upload(client) for _ in range(3)]
    batch_id = operation_log_service.new_batch_id()

    def add_tag(session, picture_id, tag):
        session.add(Tag(picture_id=picture_id, tag=tag))
        session.flush()

    operation_log_service.run_recorded_metadata_task(
        server.vault,
        add_tag,
        first,
        "batched",
        op_type="test.batch",
        picture_ids=[first],
        batch_id=batch_id,
    )
    operation_log_service.run_recorded_metadata_task(
        server.vault,
        add_tag,
        unrelated,
        "newer",
        op_type="test.unrelated",
        picture_ids=[unrelated],
    )
    operation_log_service.run_recorded_metadata_task(
        server.vault,
        add_tag,
        second,
        "batched",
        op_type="test.batch",
        picture_ids=[second],
        batch_id=batch_id,
    )

    response = client.post(f"{API}/operations/batches/{batch_id}/undo")

    assert response.status_code == 409, response.text
    assert client.post(f"{API}/operations/undo").status_code == 409
    assert _tags(server, first) == ["batched"]
    assert _tags(server, second) == ["batched"]
    assert _tags(server, unrelated) == ["newer"]
    assert all(op["status"] == "applied" for op in _operations(server)[:3])


@pytest.mark.parametrize("address", ["operation", "batch"])
def test_concurrent_explicit_undo_has_one_winner_and_one_409(address, client, server):
    first_client = client
    second_client = TestClient(server.api)
    try:
        login = second_client.post(
            "/login", json={"username": "testuser", "password": "testpassword"}
        )
        assert login.status_code == 200
        picture_id = _upload(first_client)
        batch_id = operation_log_service.new_batch_id()

        def add_tag(session, pid):
            session.add(Tag(picture_id=pid, tag="once"))
            session.flush()

        _result, operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            add_tag,
            picture_id,
            op_type="test.concurrent",
            picture_ids=[picture_id],
            batch_id=batch_id,
        )
        path = (
            f"{API}/operations/{operation['id']}/undo"
            if address == "operation"
            else f"{API}/operations/batches/{batch_id}/undo"
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(lambda c: c.post(path), [first_client, second_client])
            )

        assert sorted(response.status_code for response in responses) == [200, 409]
        assert _tags(server, picture_id) == []
        rows = _operations(server, batch_id=batch_id)
        assert [row["status"] for row in rows] == ["undone"]
    finally:
        second_client.close()


def test_recording_a_new_operation_invalidates_the_redo_stack(client, server):
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
    # The row survives - this is an audit log, not a stack that pops.
    assert superseded[0]["op_type"] == "pictures.tags.add"


def test_undo_with_nothing_to_undo_is_409(client, server):
    assert client.post(f"{API}/operations/undo").status_code == 409
    assert client.post(f"{API}/operations/redo").status_code == 409


def test_batch_undo_reverts_the_whole_bulk_action_in_one_call(client, server):
    """One bulk action = one batch id = one Undo, exactly as the sweep needs.

    Recorded through the service (the sweep's own service does not exist yet),
    then reverted through the public batch endpoint.
    """
    ids = [_upload(client) for _ in range(2)]
    batch_id = operation_log_service.new_batch_id()

    def _tag_one(session, picture_id, tag):
        session.add(Tag(picture_id=picture_id, tag=tag))
        session.flush()

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
    assert client.post(f"{API}/operations/batches/{batch_id}/undo").status_code == 409


def test_undoing_one_member_of_a_batch_reverts_the_whole_batch(client, server):
    ids = [_upload(client) for _ in range(2)]
    batch_id = operation_log_service.new_batch_id()

    def _tag_one(session, picture_id, tag):
        session.add(Tag(picture_id=picture_id, tag=tag))
        session.flush()

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
    # Both members reverted - a partially-undone bulk action cannot exist.
    assert len(resp.json()["operations"]) == 2
    assert all(_tags(server, pid) == [] for pid in ids)


def test_log_is_append_only_across_undo_and_redo(client, server):
    """Undo/redo move the lifecycle marker only; no row is rewritten or removed."""
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


def test_undo_refuses_to_write_a_picture_frozen_by_a_locked_set(client, server):
    """A locked set is a hard freeze; undo must not be the way around it."""
    picture_id = _upload(client)
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
    _lock_picture(server, picture_id)

    resp = client.post(f"{API}/operations/undo")
    assert resp.status_code == 423, resp.text
    assert _tags(server, picture_id) == ["sunset"]
    # The operation stays applied - a refused undo must not half-commit.
    assert _operations(server)[0]["status"] == "applied"


# ---------------------------------------------------------------------------
# Scrapheap lifecycle (soft delete / restore)
# ---------------------------------------------------------------------------


def test_scrapheap_move_is_recorded_and_undo_brings_the_picture_back(client, server):
    """The core promise: a move to the Scrapheap is reversible from the log."""
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


def test_scrapheap_lifecycle_announces_restored_not_added(client, server):
    """End to end: the WS envelope calls a scrapheap comeback ``restored``.

    Both the op-log undo path and the explicit restore endpoint put a card back,
    and both used to say ``added`` - which the SPA reads as "new to the vault"
    and answers with the sidebar's NEW marker on the counts that grew. That is a
    lie for a picture that has been in the library all along, so the comeback
    gets its own kind. Genuine imports keep ``added``; a re-scrapheap on redo
    keeps ``removed``.
    """
    picture_id = _upload(client)
    emitted: list[dict] = []
    real_notify = server.vault.notify

    def _capture(event_type, data=None):
        if isinstance(data, dict) and "change_kind" in data:
            emitted.append({"event": event_type, **data})
        return real_notify(event_type, data)

    server.vault.notify = _capture

    assert (
        client.delete(
            f"{API}/pictures/{picture_id}", headers={"X-Client-Id": "tab-1"}
        ).status_code
        == 200
    )
    # The move itself: the card goes away.
    assert emitted[-1]["change_kind"] == "removed"
    assert emitted[-1]["picture_ids"] == [picture_id]

    emitted.clear()
    assert client.post(f"{API}/operations/undo").status_code == 200
    undo_kinds = {
        kind["change_kind"]
        for kind in emitted
        if picture_id in (kind.get("picture_ids") or [])
    }
    assert undo_kinds == {"restored"}, emitted
    # Origin travels in ``data`` (§15) - the undo path runs on the DB worker
    # thread where the contextvar is dead.
    assert all("origin_client_id" in kind for kind in emitted)

    emitted.clear()
    assert client.post(f"{API}/operations/redo").status_code == 200
    redo_kinds = {
        kind["change_kind"]
        for kind in emitted
        if picture_id in (kind.get("picture_ids") or [])
    }
    assert redo_kinds == {"removed"}, emitted

    # …and the explicit endpoint agrees with the undo path.
    emitted.clear()
    assert (
        client.post(
            f"{API}/pictures/scrapheap/restore", json={"picture_ids": [picture_id]}
        ).status_code
        == 200
    )
    endpoint_kinds = {
        kind["change_kind"]
        for kind in emitted
        if picture_id in (kind.get("picture_ids") or [])
    }
    assert "restored" in endpoint_kinds, emitted
    assert "added" not in endpoint_kinds, emitted


def test_bulk_scrapheap_move_is_one_batch_and_one_undo(client, server):
    """Bulk = one batch id = one Undo, matching the log's grouping rule."""
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


def test_restore_from_the_scrapheap_is_recorded_symmetrically(client, server):
    """Undoing a restore puts the pictures back - the history stays coherent."""
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


def test_restore_of_an_id_that_is_not_scrapheaped_records_nothing(client, server):
    picture_id = _upload(client)
    before = len(_operations(server))
    resp = client.post(
        f"{API}/pictures/scrapheap/restore", json={"picture_ids": [picture_id]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["restored_count"] == 0
    assert len(_operations(server)) == before


def test_undoing_a_move_whose_picture_was_purged_refuses_and_changes_nothing(
    client, server
):
    """The fail-closed edge case: a purge is permanent, so the undo is refused.

    Same contract as the locked-set guard - the WHOLE request is refused with a
    specific error, nothing is written, and the operation stays ``applied``
    rather than being marked undone over a change that did not happen.
    """
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


def test_a_partially_purged_bulk_move_refuses_the_whole_undo(client, server):
    """No silent partial success: one purged target refuses the entire batch."""
    ids = [_upload(client) for _ in range(3)]
    client.request("DELETE", f"{API}/pictures", json={"picture_ids": ids})
    _purge_forever(client, [ids[1]])
    assert _lifecycle(server, ids[1]) is None

    resp = client.post(f"{API}/operations/undo")
    assert resp.status_code == 410, resp.text
    assert resp.json()["detail"]["picture_ids"] == [ids[1]]

    # The survivors are untouched - the refusal rolled the whole thing back.
    assert _lifecycle(server, ids[0])[0] is True
    assert _lifecycle(server, ids[2])[0] is True
    assert (
        _operations(server, op_type="pictures.scrapheap.move")[0]["status"] == "applied"
    )


def test_permanent_deletes_record_no_operation(client, server):
    """Purge / Empty Scrapheap are NOT undoable and must leave no log row."""
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


def test_scrapheap_move_of_a_stack_member_snapshots_the_whole_stack(client, server):
    """normalize_stack_positions renumbers siblings; undo must put them back."""
    ids = [_upload(client) for _ in range(3)]
    stacked = client.post(f"{API}/stacks", json={"picture_ids": ids})
    assert stacked.status_code == 200, stacked.text

    def positions(session):
        return {
            int(row.id): row.stack_position
            for row in session.exec(select(Picture).where(Picture.id.in_(ids))).all()
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


def test_scrapheap_move_of_a_locked_picture_is_refused_and_records_nothing(
    client, server
):
    picture_id = _upload(client)
    _lock_picture(server, picture_id)

    assert client.delete(f"{API}/pictures/{picture_id}").status_code == 423
    assert _lifecycle(server, picture_id) == (False, None)
    assert _operations(server, op_type="pictures.scrapheap.move") == []


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
# Tag-review decisions: confirm / reject (§21.2)
# ---------------------------------------------------------------------------

# An anomaly-vocabulary tag, so the decision moves the scorer's inputs too.
ANOMALY_TAG = "watermark"


def test_reject_is_recorded_and_undo_clears_the_human_negative(client, server):
    """The reported gap: a reject raised no receipt and could not be undone.

    Undo must leave the ledger as if the reject never happened - status back to
    PENDING and ``label_source`` back to null. A row still carrying a human NEG
    is one the tagger and the training exporter would go on treating as refused,
    which is the half-undo this facet exists to prevent.
    """
    picture_id = _upload(client)
    _seed_prediction(server, picture_id, ANOMALY_TAG, confidence=0.99)

    resp = client.post(
        f"{API}/pictures/{picture_id}/tag_predictions/{ANOMALY_TAG}/reject",
        headers={"X-Client-Id": "tab-1"},
    )
    assert resp.status_code == 200, resp.text

    operations = _operations(server, op_type="pictures.tags.reject")
    assert len(operations) == 1
    operation = operations[0]
    assert operation["target_ids"] == [picture_id]
    assert operation["undoable"] is True
    assert operation["status"] == "applied"
    assert operation["summary"] == f"Removed tag '{ANOMALY_TAG}'"
    assert operation["origin_client_id"] == "tab-1"
    assert operation["source"] == "ui"

    rejected = _prediction(server, picture_id, ANOMALY_TAG)
    assert rejected["status"] == "REJECTED"
    assert rejected["label_state"] == "NEG"
    assert rejected["label_source"] == "human"
    # The tagger version/confidence the reviewer overruled is snapshotted.
    assert rejected["label_model_version"] == "test-v1"
    assert rejected["label_confidence"] == 0.99

    # The recorded facet is the prediction/ledger state, not an inverse.
    detail = client.get(f"{API}/operations/{operation['id']}").json()
    assert set(detail["before"][str(picture_id)]) == {"tag_predictions"}
    assert (
        detail["before"][str(picture_id)]["tag_predictions"][ANOMALY_TAG][
            "label_source"
        ]
        is None
    )

    assert client.post(f"{API}/operations/undo").status_code == 200
    reopened = _prediction(server, picture_id, ANOMALY_TAG)
    assert reopened["status"] == "PENDING"
    assert reopened["label_state"] == "UNKNOWN"
    assert reopened["label_source"] is None
    assert reopened["label_model_version"] is None
    assert reopened["label_confidence"] is None
    # The tagger's own live fields are never rolled back by an undo.
    assert reopened["model_version"] == "test-v1"
    assert reopened["confidence"] == 0.99

    assert client.post(f"{API}/operations/redo").status_code == 200
    assert _prediction(server, picture_id, ANOMALY_TAG)["label_state"] == "NEG"


def test_reject_of_a_hand_added_tag_round_trips_the_synthetic_manual_row(
    client, server
):
    """A tag the tagger never predicted has its NEG parked on an invented row.

    That row is the only prediction row a user action can create, so it is also
    the only one an undo may delete - and a redo has to be able to rebuild it.
    """
    picture_id = _upload(client)
    assert (
        client.post(
            f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"}
        ).status_code
        == 200
    )
    # A content tag outside the tagger's label space: no prediction on file.
    assert _prediction(server, picture_id, "sunset") is None

    resp = client.post(f"{API}/pictures/{picture_id}/tag_predictions/sunset/reject")
    assert resp.status_code == 200, resp.text
    synthetic = _prediction(server, picture_id, "sunset")
    assert synthetic["model_version"] == "manual"
    assert synthetic["status"] == "REJECTED"
    assert synthetic["label_state"] == "NEG"

    assert client.post(f"{API}/operations/undo").status_code == 200
    assert _prediction(server, picture_id, "sunset") is None

    assert client.post(f"{API}/operations/redo").status_code == 200
    rebuilt = _prediction(server, picture_id, "sunset")
    assert rebuilt["model_version"] == "manual"
    assert rebuilt["status"] == "REJECTED"
    assert rebuilt["label_state"] == "NEG"


def test_confirm_is_recorded_and_undo_removes_the_tag_row_it_created(client, server):
    """Confirm promotes a prediction to a Tag; undo takes both back."""
    picture_id = _upload(client)
    _seed_prediction(server, picture_id, ANOMALY_TAG, confidence=0.99)
    assert _tags(server, picture_id) == []

    resp = client.post(
        f"{API}/pictures/{picture_id}/tag_predictions/{ANOMALY_TAG}/confirm"
    )
    assert resp.status_code == 200, resp.text
    assert _tags(server, picture_id) == [ANOMALY_TAG]

    operations = _operations(server, op_type="pictures.tags.confirm")
    assert len(operations) == 1
    assert operations[0]["summary"] == f"Confirmed tag '{ANOMALY_TAG}'"
    assert operations[0]["target_ids"] == [picture_id]
    confirmed = _prediction(server, picture_id, ANOMALY_TAG)
    assert confirmed["status"] == "CONFIRMED"
    assert confirmed["label_state"] == "POS"

    # Both facets moved, so both are recorded and both come back.
    detail = client.get(f"{API}/operations/{operations[0]['id']}").json()
    assert set(detail["before"][str(picture_id)]) == {"tags", "tag_predictions"}

    assert client.post(f"{API}/operations/undo").status_code == 200
    assert _tags(server, picture_id) == []
    reopened = _prediction(server, picture_id, ANOMALY_TAG)
    assert reopened["status"] == "PENDING"
    assert reopened["label_state"] == "UNKNOWN"
    assert reopened["label_source"] is None

    assert client.post(f"{API}/operations/redo").status_code == 200
    assert _tags(server, picture_id) == [ANOMALY_TAG]
    assert _prediction(server, picture_id, ANOMALY_TAG)["label_state"] == "POS"


def test_undo_recomputes_the_derived_uncertainty_and_drops_the_cached_score(
    client, server
):
    """Derived data is re-derived on restore, never snapshotted.

    ``anomaly_tag_uncertainty`` is a function of the labels, and ``smart_score``
    is a cache of a function of them. Restoring a snapshot of either would let
    the pair drift out of step with the rows the undo just wrote.
    """
    picture_id = _upload(client)
    _seed_prediction(server, picture_id, ANOMALY_TAG, confidence=0.99)

    assert (
        client.post(
            f"{API}/pictures/{picture_id}/tag_predictions/{ANOMALY_TAG}/confirm"
        ).status_code
        == 200
    )
    uncertainty, _score = _picture_scores(server, picture_id)
    # Tag applied: the model was 0.99 sure, so the human/model gap is 0.01.
    assert uncertainty == pytest.approx(0.01)

    # Pretend the background scorer has since filled the cache back in.
    _set_smart_score(server, picture_id, 0.5)

    assert client.post(f"{API}/operations/undo").status_code == 200
    uncertainty, score = _picture_scores(server, picture_id)
    # Tag gone again: the gap is the model's raw confidence, recomputed.
    assert uncertainty == pytest.approx(0.99)
    # And the cached score the confirm had moved is dropped for recompute.
    assert score is None


def test_removing_a_tag_chip_end_to_end_is_fully_undoable(client, server):
    """The overlay gesture the bug was reported against, start to finish.

    Removing a chip is two requests - ``tags/remove_all`` then the reject that
    makes the removal durable supervision. Each records its own operation (no
    batch id, exactly like the other single-picture tag ops), and walking both
    back leaves the tag applied with **no** rejection on file.

    This is the *unbatched* path - no ``X-Operation-Batch-Id``, as an external
    caller would issue it. The in-app gesture stamps both requests with one id
    and takes a single Ctrl+Z; see
    ``test_removing_a_tag_chip_with_a_gesture_batch_id_is_one_undo_step``.
    """
    picture_id = _upload(client)
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
    assert _tags(server, picture_id) == ["sunset"]

    assert (
        client.post(
            f"{API}/pictures/{picture_id}/tags/remove_all", json={"tag": "sunset"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{API}/pictures/{picture_id}/tag_predictions/sunset/reject"
        ).status_code
        == 200
    )
    assert _tags(server, picture_id) == []
    assert _prediction(server, picture_id, "sunset")["label_state"] == "NEG"

    recorded = _operations(server)
    assert [op["op_type"] for op in recorded] == [
        "pictures.tags.reject",
        "pictures.tags.remove_all",
        "pictures.tags.add",
    ]
    assert all(op["batch_id"] is None for op in recorded)

    # Undo is last-in-first-out: the reject, then the removal.
    assert client.post(f"{API}/operations/undo").status_code == 200
    assert _prediction(server, picture_id, "sunset") is None
    assert client.post(f"{API}/operations/undo").status_code == 200
    assert _tags(server, picture_id) == ["sunset"]
    assert _prediction(server, picture_id, "sunset") is None


def test_removing_a_tag_chip_with_a_gesture_batch_id_is_one_undo_step(client, server):
    """The fix: one user gesture, one history step, one Ctrl+Z.

    The overlay stamps both requests of the chip delete with the same
    ``X-Operation-Batch-Id``. They still record two operations - the log stays a
    faithful record of what happened - but they share a batch, and undoing the
    newest reverts the whole batch, so a single Ctrl+Z brings the tag back AND
    clears the human NEG the reject wrote.
    """
    picture_id = _upload(client)
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
    assert _tags(server, picture_id) == ["sunset"]

    headers = {"X-Operation-Batch-Id": "cli-gesture-abc123"}
    assert (
        client.post(
            f"{API}/pictures/{picture_id}/tags/remove_all",
            json={"tag": "sunset"},
            headers=headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"{API}/pictures/{picture_id}/tag_predictions/sunset/reject",
            headers=headers,
        ).status_code
        == 200
    )
    assert _tags(server, picture_id) == []
    assert _prediction(server, picture_id, "sunset")["label_state"] == "NEG"

    gesture = _operations(server, batch_id="cli-gesture-abc123")
    assert [op["op_type"] for op in gesture] == [
        "pictures.tags.reject",
        "pictures.tags.remove_all",
    ]
    # The `pictures.tags.add` that seeded the tag is a different gesture and
    # must stay out of the batch.
    assert [op["batch_id"] for op in _operations(server)] == [
        "cli-gesture-abc123",
        "cli-gesture-abc123",
        None,
    ]

    # ONE Ctrl+Z: the tag is back and the ledger with it.
    assert client.post(f"{API}/operations/undo").status_code == 200
    assert _tags(server, picture_id) == ["sunset"]
    assert _prediction(server, picture_id, "sunset") is None
    assert all(
        op["status"] == "undone"
        for op in _operations(server, batch_id="cli-gesture-abc123")
    )


def test_the_batch_undo_endpoint_reverts_a_client_gesture_in_one_call(client, server):
    """``POST /operations/batches/{batch_id}/undo`` over a client-supplied id.

    Same unit as the implicit undo above, reached the other way - the receipt's
    Undo button knows the batch id it just created and reverts by it.
    """
    picture_id = _upload(client)
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})
    headers = {"X-Operation-Batch-Id": "cli-gesture-xyz789"}
    client.post(
        f"{API}/pictures/{picture_id}/tags/remove_all",
        json={"tag": "sunset"},
        headers=headers,
    )
    client.post(
        f"{API}/pictures/{picture_id}/tag_predictions/sunset/reject",
        headers=headers,
    )

    reverted = client.post(f"{API}/operations/batches/cli-gesture-xyz789/undo")
    assert reverted.status_code == 200, reverted.text
    assert len(reverted.json()["operations"]) == 2
    assert _tags(server, picture_id) == ["sunset"]
    assert _prediction(server, picture_id, "sunset") is None


def test_a_malformed_gesture_batch_header_is_ignored_never_a_500(client, server):
    """A header is attacker-controllable: bad values degrade, they do not fail.

    Each rejected value records an unbatched operation - exactly the behaviour
    every caller had before the header existed.
    """
    picture_id = _upload(client)
    for i, bad in enumerate(
        (
            "srv-" + "0" * 32,  # cannot impersonate a server-minted batch
            "no-namespace",
            "cli-" + "a" * 200,  # oversized
            "cli-has spaces",
            "cli-abcd\n",  # G1: a trailing LF must not pass (fullmatch, not match)
        )
    ):
        resp = client.post(
            f"{API}/pictures/{picture_id}/tags",
            json={"tag": f"t{i}"},
            headers={"X-Operation-Batch-Id": bad},
        )
        assert resp.status_code == 200, resp.text

    recorded = _operations(server, op_type="pictures.tags.add")
    assert len(recorded) == 5
    assert all(op["batch_id"] is None for op in recorded), recorded


def test_a_bulk_confirm_gesture_is_one_batch_over_many_pictures(client, server):
    """The tag-panel fan-out: N confirms, one gesture id, one undo unit.

    This is what gives the receipt its ``+N`` count and makes "confirm on all"
    reversible in one press instead of N.
    """
    picture_ids = [_upload(client) for _ in range(3)]
    for picture_id in picture_ids:
        _seed_prediction(server, picture_id, "sunset", confidence=0.99)

    headers = {"X-Operation-Batch-Id": "cli-confirm-all-1"}
    for picture_id in picture_ids:
        assert (
            client.post(
                f"{API}/pictures/{picture_id}/tag_predictions/sunset/confirm",
                headers=headers,
            ).status_code
            == 200
        )
    assert all(_tags(server, pid) == ["sunset"] for pid in picture_ids)

    batch = _operations(server, batch_id="cli-confirm-all-1")
    assert len(batch) == 3

    assert client.post(f"{API}/operations/undo").status_code == 200
    assert all(_tags(server, pid) == [] for pid in picture_ids)
    assert all(
        _prediction(server, pid, "sunset")["label_source"] is None
        for pid in picture_ids
    )


def test_a_reject_on_a_locked_picture_is_refused_and_records_nothing(client, server):
    """The 423 fires inside the recorded task, before anything is written."""
    picture_id = _upload(client)
    _seed_prediction(server, picture_id, ANOMALY_TAG, confidence=0.99)
    _lock_picture(server, picture_id)

    rejected = client.post(
        f"{API}/pictures/{picture_id}/tag_predictions/{ANOMALY_TAG}/reject"
    )
    assert rejected.status_code == 423, rejected.text
    confirmed = client.post(
        f"{API}/pictures/{picture_id}/tag_predictions/{ANOMALY_TAG}/confirm"
    )
    assert confirmed.status_code == 423, confirmed.text

    assert _operations(server, op_type="pictures.tags.reject") == []
    assert _operations(server, op_type="pictures.tags.confirm") == []
    # The decision itself did not land either.
    assert _prediction(server, picture_id, ANOMALY_TAG)["status"] == "PENDING"
    assert _tags(server, picture_id) == []


def test_a_reject_of_an_unpredicted_tag_on_a_confirmed_one_does_not_batch(
    client, server
):
    """Two decisions are two independent steps, as for every other tag op."""
    picture_id = _upload(client)
    _seed_prediction(server, picture_id, ANOMALY_TAG, confidence=0.99)
    _seed_prediction(server, picture_id, "noise", confidence=0.98)

    for tag in (ANOMALY_TAG, "noise"):
        assert (
            client.post(
                f"{API}/pictures/{picture_id}/tag_predictions/{tag}/reject"
            ).status_code
            == 200
        )

    operations = _operations(server, op_type="pictures.tags.reject")
    assert len(operations) == 2
    assert all(op["batch_id"] is None for op in operations)

    # One Undo reverts one decision, not both.
    assert client.post(f"{API}/operations/undo").status_code == 200
    assert _prediction(server, picture_id, "noise")["label_source"] is None
    assert _prediction(server, picture_id, ANOMALY_TAG)["label_source"] == "human"


def test_undo_of_a_reject_leaves_a_later_tagger_prediction_alone(client, server):
    """Only the synthetic row a decision invents may be deleted by its undo.

    A prediction the tagger wrote *after* the operation was recorded is model
    output nobody asked to revert; silently deleting it would make undo a data
    loss path.
    """
    picture_id = _upload(client)
    _seed_prediction(server, picture_id, ANOMALY_TAG, confidence=0.99)
    assert (
        client.post(
            f"{API}/pictures/{picture_id}/tag_predictions/{ANOMALY_TAG}/reject"
        ).status_code
        == 200
    )
    # The tagger runs in the background and scores another label.
    _seed_prediction(server, picture_id, "noise", confidence=0.42)

    assert client.post(f"{API}/operations/undo").status_code == 200
    survivor = _prediction(server, picture_id, "noise")
    assert survivor is not None
    assert survivor["confidence"] == 0.42


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def test_list_filters_and_rejects_a_bad_status(client, server):
    picture_id = _upload(client)
    client.post(f"{API}/pictures/{picture_id}/tags", json={"tag": "sunset"})

    assert (
        client.get(f"{API}/operations", params={"status": "bogus"}).status_code == 400
    )

    applied = client.get(f"{API}/operations", params={"status": "applied"}).json()
    assert len(applied) == 1
    # The list omits the (potentially huge) before/after payloads.
    assert "before" not in applied[0]

    assert client.get(f"{API}/operations", params={"op_type": "nope"}).json() == []


def test_get_unknown_operation_is_404(client, server):
    assert client.get(f"{API}/operations/999999").status_code == 404
    assert client.post(f"{API}/operations/999999/undo").status_code == 409


def test_every_operations_route_is_declared_owner_only():
    """Both-direction authz record: the gate, not a handler, guards these.

    Arithmetic completeness - every mounted /operations route has a declaration
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


# ── post-restore hooks ────────────────────────────────────────────────────────


def _register_recording_hook(op_type):
    """Register a hook that records its calls, and return the call list.

    The registry is module-global, so the hook is removed again by the caller's
    ``finally`` to keep tests independent.
    """
    calls = []

    def _hook(session, operations, direction):
        calls.append((direction, sorted(int(op.id) for op in operations)))

    operation_log_service.register_post_restore_hook(op_type, _hook)
    return calls


def test_a_post_restore_hook_runs_once_per_restore_with_its_whole_batch(client, server):
    """The generic seam a feature uses to reopen what an operation also decided.

    An operation can change state the recorded picture facets do not cover (the
    v1.9 duplicate verdict is the first). Without a hook, undo restored the
    pictures and left that decision standing. The contract asserted here is what
    the feature relies on: called once per restore, with **every** operation of
    its own op_type, with the direction, and never for a foreign op_type.
    """
    try:
        calls = _register_recording_hook("test.hooked")
        other = _register_recording_hook("test.unhooked")
        ids = [_upload(client) for _ in range(2)]
        batch_id = operation_log_service.new_batch_id()

        def _tag_one(session, picture_id, tag):
            session.add(Tag(picture_id=picture_id, tag=tag))
            session.flush()

        for picture_id in ids:
            operation_log_service.run_recorded_metadata_task(
                server.vault,
                _tag_one,
                picture_id,
                "hooked",
                op_type="test.hooked",
                picture_ids=[picture_id],
                batch_id=batch_id,
                summary="Hooked",
            )
        recorded = sorted(op["id"] for op in _operations(server, batch_id=batch_id))
        assert len(recorded) == 2
        assert calls == [], "recording must not fire a restore hook"

        assert client.post(f"{API}/operations/undo").status_code == 200
        assert calls == [("undo", recorded)], calls

        assert client.post(f"{API}/operations/redo").status_code == 200
        assert calls == [("undo", recorded), ("redo", recorded)], calls

        # A hook registered for a different op_type never saw any of it.
        assert other == []
    finally:
        operation_log_service._POST_RESTORE_HOOKS.pop("test.hooked", None)
        operation_log_service._POST_RESTORE_HOOKS.pop("test.unhooked", None)


def test_a_failing_post_restore_hook_aborts_the_whole_undo(client, server):
    """The hook runs inside the restore's transaction, so it fails closed.

    A hook that could fail *after* the state was committed would leave the
    pictures restored and the feature's own state stale - exactly the split the
    hook exists to prevent.
    """
    try:

        def _boom(session, operations, direction):
            raise RuntimeError("hook refused")

        operation_log_service.register_post_restore_hook("test.failing", _boom)
        picture_id = _upload(client)

        def _tag_one(session, pid, tag):
            session.add(Tag(picture_id=pid, tag=tag))
            session.flush()

        operation_log_service.run_recorded_metadata_task(
            server.vault,
            _tag_one,
            picture_id,
            "kept",
            op_type="test.failing",
            picture_ids=[picture_id],
            summary="Failing",
        )
        assert _tags(server, picture_id) == ["kept"]

        # The TestClient re-raises an unhandled server exception rather than
        # turning it into a 500, so the refusal surfaces here.
        with pytest.raises(RuntimeError, match="hook refused"):
            client.post(f"{API}/operations/undo")
        # Nothing was written: the tag is still there and the operation is still
        # applied, so the user can retry rather than being left half-undone.
        assert _tags(server, picture_id) == ["kept"]
        assert [op["status"] for op in _operations(server, op_type="test.failing")] == [
            "applied"
        ]
    finally:
        operation_log_service._POST_RESTORE_HOOKS.pop("test.failing", None)


def test_a_server_minted_batch_id_is_namespaced():
    """M1: an un-namespaced id is indistinguishable from a client-supplied one."""
    assert operation_log_service.new_batch_id().startswith(
        operation_log_service.SERVER_BATCH_ID_PREFIX
    )
    assert operation_log_service.new_batch_id() != operation_log_service.new_batch_id()
