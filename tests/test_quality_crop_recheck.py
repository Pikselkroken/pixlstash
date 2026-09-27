"""The quality-crop re-check (#1648): route, finder, task and the apply rule.

A re-check re-runs only the PixlStash tagger's quality crop over pictures that
are already tagged, after the owner changed the crop size. It is additive: it
adds the crop's owned tags a picture is missing and never deletes a Tag row,
because the Tag table cannot say who wrote a row and a manual tag must survive.

Two environments. The unit half runs the finder, the task and the writer on a
bare SQLite engine with a fake database object. The route half shares one
module-scoped ``Server`` with background workers disabled, so no planner runs
the re-check (or anything else) under the assertions.
"""

import json
import os
import tempfile
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image as PILImage
from sqlalchemy import event, true
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, create_engine, delete, select

from pixlstash.db_models import (
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    Tag,
    UserToken,
    make_tag_sentinel,
)
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.server import Server
from pixlstash.task_runner import TaskCancelledError
from pixlstash.tasks.quality_crop_recheck_finder import QualityCropRecheckFinder
from pixlstash.tasks.quality_crop_recheck_task import QualityCropRecheckTask
from pixlstash.tasks.task_type import TaskType
from pixlstash.utils.tag_reset_registry import TagResetRegistry
from pixlstash.work_planner import WorkPlanner
from tests.authz_guard import assert_real_route, no_spa_fallback  # noqa: F401

# The SPA catch-all answers unmatched GETs with 200; see tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")

API = "/api/v1"
RECHECK = f"{API}/taggers/pixlstash_tagger/quality-crop/recheck"
RECHECK_TEMPLATE = RECHECK


# ── the unit environment ────────────────────────────────────────────────────


@pytest.fixture
def engine(tmp_path):
    db_engine = create_engine(
        f"sqlite:///{tmp_path / 'recheck.db'}", echo=False, poolclass=NullPool
    )

    @event.listens_for(db_engine, "connect")
    def _foreign_keys(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(db_engine)
    return db_engine


def _picture(session, *, tags=(), pending=True, deleted=False, path=None, **fields):
    pic = Picture(
        file_path=path or f"{uuid.uuid4().hex}.png",
        quality_crop_pending=pending,
        deleted=deleted,
        **fields,
    )
    session.add(pic)
    session.flush()
    for tag in tags:
        session.add(Tag(picture_id=pic.id, tag=tag))
    session.commit()
    return pic.id


def _lock(session, *picture_ids):
    picture_set = PictureSet(name=f"locked-{uuid.uuid4().hex[:6]}", locked=True)
    session.add(picture_set)
    session.flush()
    for pid in picture_ids:
        session.add(PictureSetMember(set_id=picture_set.id, picture_id=pid))
    session.commit()


def _prediction(session, pid, tag, *, source=None, state=None, **fields):
    row = TagPrediction(
        picture_id=pid,
        tag=tag,
        confidence=fields.pop("confidence", 0.9),
        model_version=fields.pop("model_version", "v1"),
        status=fields.pop("status", "REJECTED"),
        label_source=source,
        label_state=state,
        **fields,
    )
    session.add(row)
    session.commit()
    return row.id


def _tags(session, pid) -> set:
    session.expire_all()
    return set(session.exec(select(Tag.tag).where(Tag.picture_id == pid)).all())


def _pending(session, pid) -> bool:
    session.expire_all()
    return session.get(Picture, pid).quality_crop_pending


# ── the apply rule ──────────────────────────────────────────────────────────


def test_a_found_tag_is_added_unless_the_owner_rejected_it(engine):
    """ADD: found and missing is added; a human NEG vetoes; present is left."""
    with Session(engine) as session:
        pid = _picture(session, tags=["woman", "flux chin"])
        _prediction(session, pid, "malformed eyes", source="human", state="NEG")

        result = QualityCropRecheckTask.apply_crop_tags(
            session, {pid: {"blocky", "malformed eyes", "flux chin"}}
        )

        assert _tags(session, pid) == {"woman", "flux chin", "blocky"}, (
            "'blocky' was found and missing, so it is added; 'malformed eyes' "
            "was rejected by the owner and must never be applied"
        )
        assert result["tags_added"] == 1
        assert result["tagged_picture_ids"] == [pid]


@pytest.mark.parametrize(
    "ledger",
    ["none", "model_row", "human_pos", "human_neg"],
)
def test_an_owned_tag_the_crop_no_longer_finds_is_never_removed(engine, ledger):
    """The re-check is additive: no Tag row is deleted, whoever wrote it.

    "blocky" is owned by the crop, present on the picture, and not found by
    this pass - with no ledger row (a manual add), a model prediction (which
    does not prove the model wrote the Tag row), a human POS, or a human NEG
    (the owner said no, yet the row is theirs to remove, not the tagger's).
    """
    with Session(engine) as session:
        pid = _picture(session, tags=["blocky", "malformed teeth", "woman"])
        if ledger == "model_row":
            _prediction(session, pid, "blocky", status="CONFIRMED")
        elif ledger == "human_pos":
            _prediction(session, pid, "blocky", source="human", state="POS")
        elif ledger == "human_neg":
            _prediction(session, pid, "blocky", source="human", state="NEG")

        QualityCropRecheckTask.apply_crop_tags(session, {pid: set()})

        assert _tags(session, pid) == {"blocky", "malformed teeth", "woman"}
        assert _pending(session, pid) is False, "found nothing is still re-checked"


def test_the_flag_clears_for_every_eligible_picture_and_no_other(engine):
    """Cleared: found something, found nothing, no crop. Kept: frozen or pending."""
    with Session(engine) as session:
        found = _picture(session, tags=["woman"])
        nothing = _picture(session, tags=["woman"])
        no_crop = _picture(session, tags=["woman"])
        locked = _picture(session, tags=["woman"])
        retag = _picture(session, tags=[make_tag_sentinel(None)])
        scrapped = _picture(session, tags=["woman"], deleted=True)
        _lock(session, locked)

        result = QualityCropRecheckTask.apply_crop_tags(
            session,
            {
                found: {"blocky"},
                nothing: set(),
                locked: {"blocky"},
                retag: {"blocky"},
                scrapped: {"blocky"},
            },
            [no_crop],
        )

        assert [p for p in (found, nothing, no_crop) if _pending(session, p)] == []
        assert all(_pending(session, p) for p in (locked, retag, scrapped)), (
            "a skipped picture keeps its flag: a locked one is not written at all"
        )
        assert _tags(session, locked) == {"woman"}, "a locked set freezes tags"
        assert _tags(session, retag) == {make_tag_sentinel(None)}, (
            "a pending retag runs the crop itself; nothing lands beside its sentinel"
        )
        assert _tags(session, scrapped) == {"woman"}
        assert sorted(result["cleared_ids"]) == sorted([found, nothing, no_crop])
        assert sorted(result["skipped_ids"]) == sorted([locked, retag, scrapped])


def test_an_added_tag_confirms_its_model_prediction_and_nothing_else(engine):
    """Prediction status follows the applied set; confidences never move."""
    with Session(engine) as session:
        pid = _picture(session, tags=["woman"])
        model_row = _prediction(session, pid, "blocky", confidence=0.61)
        human_row = _prediction(
            session, pid, "flux chin", source="human", state="POS", status="PENDING"
        )
        other_row = _prediction(session, pid, "watermark", status="PENDING")

        QualityCropRecheckTask.apply_crop_tags(session, {pid: {"blocky", "flux chin"}})

        session.expire_all()
        model = session.get(TagPrediction, model_row)
        assert (model.status, model.confidence) == ("CONFIRMED", 0.61)
        assert session.get(TagPrediction, human_row).status == "PENDING", (
            "a human-labelled row's status is the owner's, never flipped"
        )
        assert session.get(TagPrediction, other_row).status == "PENDING", (
            "a row for a tag this pass made no decision about is left alone"
        )


def test_adding_an_anomaly_tag_invalidates_the_cached_smart_score(engine):
    """An applied Tag is an input to the anomaly penalty, so the score goes stale."""
    with Session(engine) as session:
        changed = _picture(session, tags=["woman"], smart_score=0.5)
        unchanged = _picture(session, tags=["woman"], smart_score=0.5)
        _prediction(session, changed, "blocky", confidence=0.9)
        _prediction(session, unchanged, "blocky", confidence=0.9)

        QualityCropRecheckTask.apply_crop_tags(
            session, {changed: {"blocky"}, unchanged: set()}
        )

        session.expire_all()
        assert session.get(Picture, changed).smart_score is None
        assert session.get(Picture, unchanged).smart_score == 0.5


# ── the task ────────────────────────────────────────────────────────────────


class _Db:
    """A vault database stand-in that runs every callable on one session."""

    def __init__(self, session, image_root):
        self.session = session
        self.image_root = image_root
        self.tag_resets = TagResetRegistry()
        self.unprocessable_images = _Registry()

    def run_immediate_read_task(self, fn, *args):
        return fn(self.session, *args)

    def run_task(self, fn, *args, priority=None):
        return fn(self.session, *args)


class _Registry:
    def __init__(self):
        self.unprocessable = []
        self.unreachable = []

    def mark_unprocessable(self, picture_id, file_path, *, reason=""):
        self.unprocessable.append(picture_id)

    def mark_unreachable(self, picture_id, file_path, *, reason=""):
        self.unreachable.append(picture_id)

    def is_suppressed(self, picture_id):
        return picture_id in self.unprocessable + self.unreachable

    def active_suppressed_ids(self):
        return set(self.unprocessable + self.unreachable)


class _Workflow:
    """The crop model, answering a fixed tag list for every crop it is given."""

    def __init__(self, crop_tags, size=512, loaded=True):
        self.crop_tags = list(crop_tags)
        self.size = size
        self.loaded = loaded
        self.crop_calls = []
        self.ready_overrides = []
        self.on_crop = None

    def pixlstash_tagger_image_size_quality_crop(self):
        return self.size

    def ensure_active_plugin_ready(self, engine_override=None):
        self.ready_overrides.append(engine_override)

    def is_quality_crop_model_loaded(self):
        return self.loaded

    def tag_quality_crops(self, items, image_size=None, **_kwargs):
        self.crop_calls.append((sorted(key for key, _ in items), image_size))
        if self.on_crop is not None:
            self.on_crop()
        return {key: list(self.crop_tags) for key, _ in items}


def _png(tmp_path, size=(64, 48)):
    path = tmp_path / f"{uuid.uuid4().hex}.png"
    PILImage.new("RGB", size, "blue").save(path)
    return str(path)


def _task(db, workflow, session, ids):
    pictures = [session.get(Picture, pid) for pid in ids]
    return QualityCropRecheckTask(db, workflow, pictures)


def test_a_face_crop_owns_the_face_tags_and_a_centre_crop_does_not(engine, tmp_path):
    """found = the crop's tags intersected with what that crop type owns."""
    with Session(engine) as session:
        with_face = _picture(session, path=_png(tmp_path), tags=["woman"])
        faceless = _picture(session, path=_png(tmp_path), tags=["woman"])
        face = Face(picture_id=with_face, face_index=0, frame_index=0)
        face.bbox = [10, 10, 30, 30]
        session.add(face)
        session.commit()
        db = _Db(session, str(tmp_path))
        # Every crop "finds" a face tag, a non-face quality tag, and a tag no
        # crop owns at all.
        workflow = _Workflow(["malformed eyes", "blocky", "woman", "dog"], size=320)

        result = _task(db, workflow, session, [with_face, faceless])._run_task()

        assert _tags(session, with_face) == {"woman", "malformed eyes", "blocky"}
        assert _tags(session, faceless) == {"woman", "blocky"}, (
            "a centre crop has no face to judge 'malformed eyes' on"
        )
        assert workflow.crop_calls[0][1] == 320, "the crop runs at the chosen size"
        assert workflow.ready_overrides == ["pixlstash_tagger"], (
            "the crop is always the built-in tagger's, whatever plugin is active"
        )
        assert result["tags_added"] == 3
        assert not _pending(session, with_face) and not _pending(session, faceless)


def test_a_picture_retagged_mid_pass_gets_no_output(engine, tmp_path):
    """#1361: output judged against replaced tags is dropped, flag left for TagTask."""
    with Session(engine) as session:
        retagged = _picture(session, path=_png(tmp_path), tags=["woman"])
        untouched = _picture(session, path=_png(tmp_path), tags=["woman"])
        db = _Db(session, str(tmp_path))
        workflow = _Workflow(["blocky"])
        task = _task(db, workflow, session, [retagged, untouched])

        def _retag():
            session.exec(delete(Tag).where(Tag.picture_id == retagged))
            session.add(Tag(picture_id=retagged, tag="cat"))
            session.commit()
            db.tag_resets.mark_reset([retagged])

        workflow.on_crop = _retag
        task._run_task()

        assert _tags(session, retagged) == {"cat"}
        assert _pending(session, retagged) is True
        assert _tags(session, untouched) == {"woman", "blocky"}
        assert _pending(session, untouched) is False


def test_an_unloadable_file_keeps_its_flag_and_is_held(engine, tmp_path):
    """As TagTask keeps its sentinel: undecodable is marked, missing is held."""
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"this is not a png")
    with Session(engine) as session:
        undecodable = _picture(session, path=str(corrupt), tags=["woman"])
        gone = _picture(session, path=str(tmp_path / "gone" / "x.png"), tags=["woman"])
        good = _picture(session, path=_png(tmp_path), tags=["woman"])
        db = _Db(session, str(tmp_path))

        _task(db, _Workflow(["blocky"]), session, [undecodable, gone, good])._run_task()

        assert _pending(session, undecodable) and _pending(session, gone)
        assert db.unprocessable_images.unprocessable == [undecodable]
        assert db.unprocessable_images.unreachable == [gone]
        assert _pending(session, good) is False


def test_a_crop_that_cannot_be_built_is_recorded_as_rechecked(engine, tmp_path):
    """A loaded picture with a broken face row will not improve by retrying."""
    with Session(engine) as session:
        pid = _picture(session, path=_png(tmp_path), tags=["woman"])
        face = Face(picture_id=pid, face_index=0, frame_index=0)
        face.bbox = [1, 2]  # not four numbers
        session.add(face)
        session.commit()
        db = _Db(session, str(tmp_path))

        _task(db, _Workflow(["blocky"]), session, [pid])._run_task()

        assert _tags(session, pid) == {"woman"}
        assert _pending(session, pid) is False


def test_an_unloaded_model_fails_the_task_and_keeps_every_flag(engine, tmp_path):
    """`tag_quality_crops` answers {} unloaded; that must not read as "found nothing"."""
    with Session(engine) as session:
        pid = _picture(session, path=_png(tmp_path), tags=["woman"])
        db = _Db(session, str(tmp_path))
        workflow = _Workflow(["blocky"], loaded=False)

        with pytest.raises(RuntimeError, match="not loaded"):
            _task(db, workflow, session, [pid])._run_task()

        assert workflow.crop_calls == []
        assert _pending(session, pid) is True


def test_a_crop_switched_off_while_queued_leaves_the_flags(engine, tmp_path):
    with Session(engine) as session:
        pid = _picture(session, path=_png(tmp_path), tags=["woman"])
        db = _Db(session, str(tmp_path))
        workflow = _Workflow(["blocky"], size=None)

        _task(db, workflow, session, [pid])._run_task()

        assert workflow.crop_calls == [] and workflow.ready_overrides == []
        assert _pending(session, pid) is True
        assert _tags(session, pid) == {"woman"}


# ── the finder ──────────────────────────────────────────────────────────────


def test_the_finder_selects_only_pending_live_tagged_unlocked_pictures(engine):
    with Session(engine) as session:
        wanted = _picture(session, tags=["woman"])
        untagged_but_done = _picture(session)
        _picture(session, tags=["woman"], pending=False)
        _picture(session, tags=[make_tag_sentinel(None)])
        _picture(session, tags=["woman"], deleted=True)
        locked = _picture(session, tags=["woman"])
        suppressed = _picture(session, tags=["woman"])
        _lock(session, locked)

        found = QualityCropRecheckFinder.fetch_pending(session, 50, {suppressed})

        assert [p.id for p in found] == [wanted, untagged_but_done]


def test_the_pending_probe_is_served_by_its_partial_index(engine):
    with Session(engine) as session:
        _picture(session, tags=["woman"])
    with engine.connect() as connection:
        compiled = (
            select(Picture.id)
            .where(Picture.quality_crop_pending == true())
            .where(Picture.deleted.is_(False))
            .compile(engine, compile_kwargs={"literal_binds": True})
        )
        plan = connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {compiled}").fetchall()
    assert any("ix_picture_quality_crop_pending" in str(row) for row in plan), plan


def _engine_ns(size=512, active="pixlstash_tagger"):
    workflow = SimpleNamespace(
        pixlstash_tagger_image_size_quality_crop=lambda: size,
        suggested_task_size=lambda: 8,
    )
    return SimpleNamespace(
        tagger_settings={"active_tag_plugin": active}, tagging_workflow=workflow
    )


class _FinderDb:
    def __init__(self, pictures):
        self.pictures = pictures
        self.reads = 0
        self.tag_resets = TagResetRegistry()

    def run_immediate_read_task(self, fn, *args):
        self.reads += 1
        return list(self.pictures)


@pytest.mark.parametrize(
    "engine_ns",
    [None, _engine_ns(size=None), _engine_ns(active="")],
    ids=["no-engine", "crop-off", "tagging-off"],
)
def test_the_finder_idles_without_reading_when_it_has_nothing_to_run(engine_ns):
    db = _FinderDb([SimpleNamespace(id=1, file_path="a.png")])
    finder = QualityCropRecheckFinder(db, engine_getter=lambda: engine_ns)

    assert finder.find_task() is None
    assert db.reads == 0, "an idle finder must not even probe"


def test_the_finder_hands_out_a_task_and_backs_off_after_a_failure():
    db = _FinderDb([SimpleNamespace(id=1, file_path="a.png")])
    finder = QualityCropRecheckFinder(db, engine_getter=lambda: _engine_ns())

    task = finder.find_task()
    assert isinstance(task, QualityCropRecheckTask)
    assert task.params["picture_ids"] == [1]

    finder.on_task_complete(task, TaskCancelledError("shutdown"))
    assert finder.find_task() is not None, "a cancel is not a failure"

    finder.on_task_complete(task, RuntimeError("tagger not loaded"))
    assert finder.find_task() is None, "a failed model load must not hot-loop"
    finder._retry_after = 0.0
    assert finder.find_task() is not None


def test_the_finder_is_registered_behind_live_tagging():
    finders = WorkPlanner.work_finders(database=None, engine_getter=lambda: None)
    finder = finders[TaskType.QUALITY_CROP_RECHECK]
    assert isinstance(finder, QualityCropRecheckFinder)
    assert {TaskType.TAGGER, TaskType.FACE_EXTRACTION} <= set(finder.depends_on())


# ── the route, on one shared server ─────────────────────────────────────────


@pytest.fixture(scope="module")
def env():
    """Server + owner client + two READ tokens, shared by the route tests.

    Background workers are off: no planner may run the re-check (or any other
    stage) against the seeded rows while a test reads them back.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        os.makedirs(os.path.join(temp_dir, "images"), exist_ok=True)
        config = os.path.join(temp_dir, "server-config.json")
        with open(config, "w") as fh:
            json.dump({"port": 8000, "disable_background_workers": True}, fh)
        with Server(config) as server:
            owner = TestClient(server.api)
            resp = owner.post(
                f"{API}/login",
                json={"username": "owner", "password": "example-owner-password"},
            )
            assert resp.status_code == 200, resp.text
            yield SimpleNamespace(server=server, owner=owner)


@pytest.fixture
def route_env(env):
    """Wipe the pictures and tokens, re-mint credentials, crop back on at 512."""

    def _wipe(session):
        for model in (TagPrediction, Tag, PictureSetMember, PictureSet, Face, Picture):
            session.exec(delete(model))
        session.commit()

    env.server.vault.db.run_task(_wipe)

    def _wipe_tokens(session):
        session.exec(delete(UserToken))
        session.commit()

    env.server.hub_engine.run_task(_wipe_tokens)
    env.server.auth._flush_token_cache()
    tokens = {}
    for name, body in {
        "owner_all": {"description": "owner"},
        "unscoped_read": {"description": "read", "scope": "READ"},
        "scoped_read": {
            "description": "set share",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": 1,
        },
    }.items():
        resp = env.owner.post(f"{API}/users/me/token", json=body)
        assert resp.status_code == 200, resp.text
        tokens[name] = resp.json()["token"]
    _set_crop(env.server, "512")
    yield SimpleNamespace(
        server=env.server,
        owner=env.owner,
        anon=TestClient(env.server.api),
        tokens=tokens,
    )
    _set_crop(env.server, "512")


def _set_crop(server, value):
    settings = {"plugins": {"pixlstash_tagger": {"params": {"quality_crop": value}}}}
    server.vault.set_tagger_settings(settings)


def _seed(server, build):
    def _run(session):
        return build(session)

    return server.vault.db.run_task(_run)


def _pending_ids(server) -> set:
    return set(
        server.vault.db.run_immediate_read_task(
            lambda s: s.exec(
                select(Picture.id).where(Picture.quality_crop_pending.is_(True))
            ).all()
        )
    )


def test_the_route_refuses_while_the_crop_is_off(route_env):
    assert_real_route(route_env.server.api, "POST", RECHECK, RECHECK_TEMPLATE)
    pid = _seed(route_env.server, lambda s: _picture(s, tags=["woman"], pending=False))
    _set_crop(route_env.server, "off")

    resp = route_env.owner.post(RECHECK)

    assert resp.status_code == 409, resp.text
    assert "quality crop is off" in resp.json()["detail"].lower()
    assert _pending_ids(route_env.server) == set(), f"nothing marked, {pid} included"


def test_the_route_marks_only_tagged_live_unlocked_pictures(route_env):
    def _build(session):
        ids = {
            "tagged": _picture(session, tags=["woman"], pending=False),
            "no_tags": _picture(session, pending=False),
            "retag": _picture(session, tags=[make_tag_sentinel(None)], pending=False),
            "deleted": _picture(session, tags=["woman"], pending=False, deleted=True),
            "locked": _picture(session, tags=["woman"], pending=False),
        }
        _lock(session, ids["locked"])
        return ids

    ids = _seed(route_env.server, _build)

    resp = route_env.owner.post(RECHECK)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"queued": 2}
    assert _pending_ids(route_env.server) == {ids["tagged"], ids["no_tags"]}

    again = route_env.owner.post(RECHECK)
    assert again.status_code == 200 and again.json() == {"queued": 2}, "idempotent"
    assert _pending_ids(route_env.server) == {ids["tagged"], ids["no_tags"]}


def test_only_the_owner_may_request_a_recheck(route_env):
    """OWNER_ONLY, both directions, against a route proven to exist."""
    assert_real_route(route_env.server.api, "POST", RECHECK, RECHECK_TEMPLATE)
    pid = _seed(route_env.server, lambda s: _picture(s, tags=["woman"], pending=False))
    bearer = {
        name: {"Authorization": f"Bearer {token}"}
        for name, token in route_env.tokens.items()
    }

    for name in ("unscoped_read", "scoped_read"):
        resp = route_env.anon.post(RECHECK, headers=bearer[name])
        assert resp.status_code == 403, f"{name}: {resp.status_code} {resp.text}"
        assert _pending_ids(route_env.server) == set(), f"{name} marked pictures"

    # The positive controls, after the negatives so a refusal cannot hide
    # behind a flag an earlier success set.
    resp = route_env.anon.post(RECHECK, headers=bearer["owner_all"])
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"queued": 1}
    assert _pending_ids(route_env.server) == {pid}
    resp = route_env.owner.post(RECHECK)
    assert resp.status_code == 200, resp.text
