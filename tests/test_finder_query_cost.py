"""The hot idle finder probes cost O(matching rows), not O(library) (issue #651).

The WorkPlanner sweeps every registered finder on a short interval, so a finder's
candidate query runs continuously even on a fully-processed library where it
matches nothing. Two of them were the dominant background read:

* ``MissingThumbnailFinder._fetch_missing`` selected the FULL ``Picture`` ORM
  entity - including ``image_embedding``, ``text_embedding`` and
  ``likeness_parameters``, three LargeBinary columns - plus every ``Face`` column
  including ``features``, for every candidate row.
* ``SmartScoreTask.find_pictures_missing_smart_score`` did the same, and nothing
  downstream reads anything but ``pic.id``.

and both were served by ``ix_picture_deleted``, i.e. SQLite walked every
non-deleted row to prove there was no work.

Three properties are asserted here, one per failure mode:

1. **Plan** - each probe is served by its partial index, so it touches only rows
   that actually need work. This database never runs ``ANALYZE``: there is no
   ``sqlite_stat1``, so the planner scores a partial index by the table's default
   row estimate and only prefers it when it claims MORE equality terms than the
   index it competes with. That is why both indexes lead with the nullable column
   (``IS NULL`` is an equality term for SQLite) followed by ``deleted``, and why
   an ``(id)``-only partial index would be built, maintained, and never used.
2. **Columns** - the thumbnail probe's emitted SQL names no BLOB column.
3. **Round trip** - the narrowed load still carries everything
   ``ThumbnailGenerationTask`` reads. ``run_immediate_read_task`` closes the
   session, so the task runs against DETACHED pictures and any attribute the
   ``load_only`` forgot raises ``DetachedInstanceError`` at runtime - which
   ``_run_task`` catches per picture and logs, so the only visible symptom would
   be thumbnails silently never being generated. That is the canary below.

The vault here is constructed but never ``start()``ed, so no TaskRunner or
WorkPlanner is running and nothing races the finders. The SQL listener still
filters by substring rather than counting statements: it fires for every thread
and for all of the vault's own bookkeeping, so a raw total would measure
something other than the query under test.
"""

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import event
from sqlalchemy.orm.exc import DetachedInstanceError
from sqlmodel import Session

from pixlstash.db_models import Face, Picture
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.missing_smart_score_finder import MissingSmartScoreFinder
from pixlstash.tasks.missing_thumbnail_finder import MissingThumbnailFinder
from pixlstash.tasks.smart_score_task import SmartScoreTask
from pixlstash.tasks.thumbnail_generation_task import ThumbnailGenerationTask
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.vault import Vault

# Substrings that identify each probe's statement among everything else the
# engine executes. Both are unique to the finder query they name.
_THUMBNAIL_PROBE = "picture.thumbnail_width is null"
_SMART_SCORE_PROBE = "picture.smart_score is null"
_FACE_SELECTIN = "from face where face.picture_id in"

# LargeBinary / blob columns that must never be dragged off disk by a probe.
_BLOB_COLUMNS = (
    "image_embedding",
    "text_embedding",
    "likeness_parameters",
    "features",
)


class _StatementRecorder:
    """Record every statement whose collapsed SQL contains *needle*.

    Deliberately NOT a count of all statements on the engine: the listener fires
    for every thread and the vault runs its own bookkeeping queries, so a raw
    total measures unrelated work (see ``tests/test_picture_sets_query_cost.py``,
    which failed for exactly that reason before it filtered).
    """

    def __init__(self, engine, needle: str):
        self._engine = engine
        self._needle = needle
        self.statements: list[str] = []
        self.parameters: list = []

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        collapsed = " ".join(statement.split()).lower()
        if self._needle in collapsed:
            self.statements.append(collapsed)
            self.parameters.append(parameters)

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info):
        event.remove(self._engine, "before_cursor_execute", self._on_execute)
        return False

    @property
    def only(self) -> str:
        assert self.statements, f"no statement matched {self._needle!r}"
        return self.statements[0]


def _write_image(directory, name: str, size=(400, 300)) -> str:
    """Write one real, decodable PNG under *directory* and return its file name."""
    arr = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    arr[:, :] = (90, 140, 210)
    Image.fromarray(arr, "RGB").save(directory / name)
    return name


def _seed(vault, tmp_path, count: int, *, with_faces: bool, done: int = 0) -> list[int]:
    """Insert *count* pictures with real files; the first *done* look processed.

    A "done" picture has its thumbnail columns and smart score filled in, so it
    is exactly the row an idle library is full of and neither probe should have
    to look at it.
    """

    def seed(session: Session):
        ids = []
        for index in range(count):
            name = _write_image(tmp_path, f"p{index}.png")
            processed = index < done
            picture = Picture(
                file_path=name,
                format="png",
                width=400,
                height=300,
                deleted=False,
                # A non-NULL embedding is what makes a picture a smart-score
                # candidate at all; the bytes themselves are never read here.
                image_embedding=np.zeros(8, dtype=np.float32).tobytes(),
                thumbnail_width=384 if processed else None,
                thumbnail_height=288 if processed else None,
                smart_score=0.5 if processed else None,
            )
            session.add(picture)
            session.flush()
            if with_faces:
                session.add(
                    Face(
                        picture_id=picture.id,
                        frame_index=0,
                        face_index=0,
                        bbox=[120, 60, 220, 180],
                        features=b"\x00" * 512,
                    )
                )
            ids.append(picture.id)
        session.commit()
        return ids

    return vault.db.run_task(seed)


def _explain(vault, statement: str, parameters) -> list[str]:
    """Return the EXPLAIN QUERY PLAN detail lines for an already-emitted query."""

    def run(session: Session):
        rows = session.connection().exec_driver_sql(
            f"EXPLAIN QUERY PLAN {statement}", parameters
        )
        return [row[-1] for row in rows]

    return vault.db.run_immediate_read_task(run)


# ── query plans ─────────────────────────────────────────────────────────────


def test_thumbnail_probe_is_served_by_its_partial_index(tmp_path):
    """``thumbnail_width IS NULL`` resolves through ``ix_picture_thumbnail_missing``.

    Before the index, this was ``SEARCH picture USING INDEX ix_picture_deleted``:
    every non-deleted row visited, several times a second, to find nothing.
    """
    with Vault(image_root=str(tmp_path)) as vault:
        _seed(vault, tmp_path, count=12, with_faces=False, done=10)
        finder = MissingThumbnailFinder(vault.db)

        with _StatementRecorder(vault.db._engine, _THUMBNAIL_PROBE) as rec:
            finder.find_task()

        plan = _explain(vault, rec.only, rec.parameters[0])
        assert any("ix_picture_thumbnail_missing" in line for line in plan), (
            f"thumbnail probe is not using its partial index; plan was {plan}"
        )
        # The trailing ``id`` column is what keeps ORDER BY free.
        assert not any("TEMP B-TREE" in line.upper() for line in plan), (
            f"ORDER BY picture.id is being sorted rather than read in index "
            f"order; plan was {plan}"
        )


def test_smart_score_probe_is_served_by_its_partial_index(tmp_path):
    """``smart_score IS NULL`` resolves through ``ix_picture_smart_score_missing``.

    NOT through the plain ``ix_picture_smart_score`` that the column's
    ``index=True`` creates. That index can serve ``smart_score IS NULL``, but the
    planner only picks it once ``sqlite_stat1`` exists, and nothing in PixlStash
    runs ``ANALYZE`` - so on a real vault the probe fell back to
    ``ix_picture_deleted`` and walked the whole library.
    """
    with Vault(image_root=str(tmp_path)) as vault:
        _seed(vault, tmp_path, count=12, with_faces=False, done=10)
        finder = MissingSmartScoreFinder(vault)

        with _StatementRecorder(vault.db._engine, _SMART_SCORE_PROBE) as rec:
            finder._db.run_immediate_read_task(finder._fetch_candidates, 128)

        plan = _explain(vault, rec.only, rec.parameters[0])
        assert any("ix_picture_smart_score_missing" in line for line in plan), (
            f"smart-score probe is not using its partial index; plan was {plan}"
        )
        assert not any("TEMP B-TREE" in line.upper() for line in plan), (
            f"ORDER BY picture.id is being sorted rather than read in index "
            f"order; plan was {plan}"
        )


def test_character_features_rollup_is_served_by_its_partial_index(tmp_path):
    """ "Which characters have an embedded face?" reads only the embedded faces.

    Asserted on the GROUPED shape on purpose, not on a per-character
    ``character_id = ?`` probe. ``ix_face_character_features`` and
    ``ix_face_character_id`` cost the same for an equality lookup when there is no
    ``sqlite_stat1`` (nothing here runs ``ANALYZE``), and SQLite breaks that tie by
    index-creation order - which ``metadata.create_all()`` iterates from a set, so
    it differs between processes. An earlier revision of this test asserted the
    equality shape and failed roughly every other run for exactly that reason.
    The one-pass shape is chosen unconditionally, and is what ``GET /characters``
    asks. See ``Face.__table_args__``.

    Asserted against the statement the endpoint actually issues, compiled from
    ``characters_with_reference_faces_query`` rather than hand-written here. An
    earlier revision hand-wrote a similar query, which passed while the real
    endpoint used the OTHER index: the index looked justified and was not.
    """
    from pixlstash.routes.characters import characters_with_reference_faces_query

    with Vault(image_root=str(tmp_path)) as vault:
        _seed(vault, tmp_path, count=4, with_faces=True)
        statement = characters_with_reference_faces_query().compile(
            vault.db._engine, compile_kwargs={"literal_binds": True}
        )
        plan = _explain(vault, str(statement), ())
        assert any("ix_face_character_features" in line for line in plan), (
            f"character-features rollup is not using its partial index; plan was {plan}"
        )


def test_likeness_stack_leader_sibling_lookup_uses_the_stack_index(tmp_path):
    """The scoped-leader clause's correlated sibling lookup is keyed on stack_id.

    The sibling row is narrowed by ``stack_id`` and by ``deleted``. Written as
    two equality terms, ``ix_picture_stack_id`` and ``ix_picture_deleted`` tie
    (no ``sqlite_stat1``) and creation order decides. When ``ix_picture_deleted``
    won, each face row walked every live picture: one CHARACTER_LIKENESS page
    cost 6.5 s on a 12k-picture library against 0.13 s. The clause now writes
    the deleted test as ``deleted IS NOT 1``, which no index can serve, so the
    only candidates left are the stack_id indexes and the plan cannot flip.

    ``metadata.create_all()`` builds the indexes in set order, so a single
    fresh database is a coin flip and would pass half the time without the
    fix. The two competing indexes are therefore rebuilt in BOTH orders and
    the plan asserted after each.
    """
    from sqlmodel import select

    from pixlstash.scoring.character_likeness import _scoped_stack_leader_clause

    def rebuild(session: Session, *ddl: str):
        for statement in ddl:
            session.connection().exec_driver_sql(statement)
        session.commit()

    deleted_last = (
        "DROP INDEX ix_picture_deleted",
        "CREATE INDEX ix_picture_deleted ON picture (deleted)",
    )
    stack_last = (
        "DROP INDEX ix_picture_stack_id",
        "CREATE INDEX ix_picture_stack_id ON picture (stack_id)",
    )

    with Vault(image_root=str(tmp_path)) as vault:
        _seed(vault, tmp_path, count=4, with_faces=True)
        statement = (
            select(Picture.id)
            .where(Picture.deleted.is_(False))
            .where(_scoped_stack_leader_clause("ALL", None))
            .compile(vault.db._engine, compile_kwargs={"literal_binds": True})
        )
        for order in (deleted_last, stack_last):
            vault.db.run_task(rebuild, *order)
            plan = _explain(vault, str(statement), ())
            sibling_lines = [line for line in plan if "picture_1" in line]
            assert sibling_lines, f"no sibling lookup in plan {plan}"
            assert all("ix_picture_stack_id" in line for line in sibling_lines), (
                f"sibling lookup is not keyed on stack_id; plan was {plan}"
            )


# ── column width ────────────────────────────────────────────────────────────


def test_thumbnail_probe_reads_no_blob_columns(tmp_path):
    """Neither the picture SELECT nor the face selectin names a blob column."""
    with Vault(image_root=str(tmp_path)) as vault:
        _seed(vault, tmp_path, count=4, with_faces=True)
        finder = MissingThumbnailFinder(vault.db)

        with (
            _StatementRecorder(vault.db._engine, _THUMBNAIL_PROBE) as pictures,
            _StatementRecorder(vault.db._engine, _FACE_SELECTIN) as faces,
        ):
            finder.find_task()

        for column in _BLOB_COLUMNS:
            assert column not in pictures.only, (
                f"the thumbnail probe is reading {column!r} off disk on every "
                f"planning sweep: {pictures.only}"
            )
        assert faces.statements, "the faces selectin did not run"
        for column in _BLOB_COLUMNS:
            assert column not in faces.only, (
                f"the thumbnail probe's face selectin is reading {column!r}: "
                f"{faces.only}"
            )
        # Positive control: the columns the task actually needs ARE there.
        assert "picture.file_path" in pictures.only
        assert "face.bbox" in faces.only
        assert "face.picture_id" in faces.only


def test_smart_score_probe_reads_only_the_id(tmp_path):
    """The smart-score probe selects ``picture.id`` and nothing else.

    ``SmartScoreTask`` re-fetches every scorer input by id inside its own read
    transaction, so loading them here was pure waste.
    """
    with Vault(image_root=str(tmp_path)) as vault:
        _seed(vault, tmp_path, count=4, with_faces=False)

        with _StatementRecorder(vault.db._engine, _SMART_SCORE_PROBE) as rec:
            vault.db.run_immediate_read_task(
                SmartScoreTask.find_pictures_missing_smart_score, 128
            )

        selected = rec.only.split(" from picture")[0]
        assert selected == "select picture.id", (
            f"the smart-score probe is selecting more than the id: {selected}"
        )


# ── the load_only canary ────────────────────────────────────────────────────


def test_finder_to_thumbnail_task_round_trip_on_detached_pictures(tmp_path):
    """End to end: narrowed rows still generate thumbnails, with faces.

    ``_run_task`` catches per-picture exceptions and logs them, so a
    ``DetachedInstanceError`` from a column the ``load_only`` forgot would not
    fail loudly - it would just stop producing thumbnails forever. Hence the
    explicit attribute reads before the run, and the file-level assertions after.
    """
    with Vault(image_root=str(tmp_path)) as vault:
        picture_ids = _seed(vault, tmp_path, count=3, with_faces=True)

        finder = MissingThumbnailFinder(vault.db)
        task = finder.find_task()
        assert isinstance(task, ThumbnailGenerationTask)
        assert sorted(task.params["picture_ids"]) == sorted(picture_ids)

        # The session is already closed. Every attribute the task reads must be
        # readable here; each of these would raise DetachedInstanceError if the
        # load_only had dropped it.
        for picture in task._pictures:
            assert picture.id in picture_ids
            assert picture.file_path
            assert len(picture.faces) == 1
            assert picture.faces[0].bbox == [120, 60, 220, 180]
            assert picture.faces[0].picture_id == picture.id

        # Positive control: the load really is narrowed. If this stops raising,
        # the probe has gone back to reading blobs and the assertions above stop
        # meaning anything.
        with pytest.raises(DetachedInstanceError):
            _ = task._pictures[0].image_embedding

        result = task.run()
        assert result["changed_count"] == len(picture_ids), (
            "thumbnails were not generated for every candidate; a swallowed "
            "DetachedInstanceError looks exactly like this"
        )

        def read_back(session: Session):
            return {
                pic.id: (
                    pic.thumbnail_width,
                    pic.thumbnail_height,
                    pic.square_crop_side,
                )
                for pic in session.exec(
                    Picture.__table__.select().where(
                        Picture.__table__.c.id.in_(picture_ids)
                    )
                ).all()
            }

        stored = vault.db.run_immediate_read_task(read_back)
        for picture_id in picture_ids:
            width, height, side = stored[picture_id]
            assert width and height and side, (
                f"picture {picture_id} has no thumbnail columns: {stored[picture_id]}"
            )
        # And the bitmaps are on disk.
        for index in range(len(picture_ids)):
            thumb = ImageUtils.get_thumbnail_path(vault.db.image_root, f"p{index}.png")
            assert thumb and Image.open(thumb).size == (width, height)


# ── the face finder's candidate window ──────────────────────────────────────


class _StubEngine:
    """Only what `MissingFaceExtractionFinder` reads off the engine."""

    def __init__(self, keep_models_in_memory: bool = False):
        self.keep_models_in_memory = keep_models_in_memory


def test_the_face_finder_can_fill_every_slot_it_says_it_has(tmp_path):
    """`max_inflight_tasks() == 3` was a promise one batch of candidates broke.

    A picture keeps matching ``~faces.any()`` until its task finishes, so with a
    candidate window of exactly one batch the second sweep re-read the same
    hundred rows, found all of them claimed, and returned None - which the
    planner answers with a backoff that grows 1.8x a time. Two idle slots and a
    lengthening sleep, on a library with thousands of pictures left to do.
    """
    from pixlstash.tasks.missing_face_extraction_finder import (
        FACE_EXTRACTION_BATCH_LIMIT,
        MissingFaceExtractionFinder,
    )

    with Vault(image_root=str(tmp_path)) as vault:
        _seed(
            vault,
            tmp_path,
            count=FACE_EXTRACTION_BATCH_LIMIT + 5,
            with_faces=False,
        )
        finder = MissingFaceExtractionFinder(vault.db, lambda: _StubEngine())

        first = finder.find_task()
        assert first is not None
        assert len(first.params["picture_ids"]) == FACE_EXTRACTION_BATCH_LIMIT

        # Nothing has completed, so every id in `first` is still claimed AND
        # still faceless. The next sweep must reach past them.
        second = finder.find_task()
        assert second is not None, (
            "the finder starved itself: a full batch in flight left it "
            "returning None while 5 pictures still had no faces"
        )
        assert len(second.params["picture_ids"]) == 5
        assert not set(first.params["picture_ids"]) & set(second.params["picture_ids"])


def test_undecodable_pictures_cannot_wedge_the_face_finder(tmp_path):
    """A window's worth of suppressed rows used to be a permanent stall.

    They are filtered *after* the query by ``_filter_and_claim``, so a run of
    them long enough to fill the candidate window handed back a list that
    claimed nothing - every sweep, forever, with real work sitting behind them.
    """
    from pixlstash.tasks.missing_face_extraction_finder import (
        FACE_EXTRACTION_BATCH_LIMIT,
        MissingFaceExtractionFinder,
    )

    with Vault(image_root=str(tmp_path)) as vault:
        ids = _seed(
            vault,
            tmp_path,
            count=FACE_EXTRACTION_BATCH_LIMIT + 3,
            with_faces=False,
        )
        for picture_id in ids[:FACE_EXTRACTION_BATCH_LIMIT]:
            vault.db.unprocessable_images.mark_unprocessable(
                picture_id,
                str(tmp_path / f"p{picture_id}.png"),
                reason="test: undecodable",
            )
        finder = MissingFaceExtractionFinder(vault.db, lambda: _StubEngine())

        task = finder.find_task()
        assert task is not None, "the three good pictures behind the bad ones"
        assert set(task.params["picture_ids"]) == set(ids[FACE_EXTRACTION_BATCH_LIMIT:])


def test_keeping_models_in_memory_survives_the_finder_running_dry(tmp_path):
    """The setting exists to stop exactly this reload, so it has to win here."""
    from pixlstash.tasks.face_extraction_task import FaceExtractionTask
    from pixlstash.tasks.missing_face_extraction_finder import (
        MissingFaceExtractionFinder,
    )

    released = []
    with Vault(image_root=str(tmp_path)) as vault:
        finder = MissingFaceExtractionFinder(
            vault.db, lambda: _StubEngine(keep_models_in_memory=True)
        )
        original = FaceExtractionTask.release_detection_models
        FaceExtractionTask.release_detection_models = classmethod(
            lambda cls: released.append(True)
        )
        try:
            finder.on_all_tasks_complete()
            assert released == [], "the owner asked for the models to stay resident"

            dropping = MissingFaceExtractionFinder(
                vault.db, lambda: _StubEngine(keep_models_in_memory=False)
            )
            dropping.on_all_tasks_complete()
            assert released == [True], "and to be dropped when they did not"
        finally:
            FaceExtractionTask.release_detection_models = original


def test_a_tag_drain_leaves_wd14_to_the_idle_sweep(tmp_path):
    """The drain used to tear the WD14 session down; with per-row dependencies
    a finder drains many times per pass, so the session now outlives the drain
    and only the idle sweep frees it - and only when models are not kept."""
    from pixlstash.tasks.missing_tag_finder import MissingTagFinder

    unloads = []

    class _Engine:
        def __init__(self, keep: bool):
            self.keep_models_in_memory = keep

        def unload_tagger_session(self):
            unloads.append("drain")

        def aggressive_unload(self):
            unloads.append("sweep")

    with Vault(image_root=str(tmp_path)) as vault:
        for keep in (True, False):
            MissingTagFinder(vault.db, lambda: _Engine(keep)).on_all_tasks_complete()
        assert unloads == [], "a drain must not reload the model on the next batch"

        original_engine = vault._engine
        vault._engine = _Engine(True)
        vault._keep_models_in_memory = True
        vault._last_aggressive_unload_at = 0.0
        try:
            vault._maybe_aggressive_unload({})
            assert unloads == [], "the owner asked for the models to stay resident"

            vault._keep_models_in_memory = False
            vault._maybe_aggressive_unload({})
            assert unloads == ["sweep"], "and the idle sweep frees them when idle"
        finally:
            vault._engine = original_engine


# ── per-row stage dependencies (throughput plan §7 step 4) ──────────────────


def _add_rows(vault, rows) -> None:
    """Insert already-built ORM rows (Face / Tag / Quality) and commit."""

    def add(session: Session):
        session.add_all(rows)
        session.commit()

    vault.db.run_task(add)


def test_image_embedding_needs_nothing_upstream(tmp_path):
    """CLIP reads only the file: a picture with no faces and tags still pending
    is embedded now, not after those stages drain (the old ``depends_on``).
    """
    from pixlstash.db_models import Tag
    from pixlstash.db_models.tag import make_tag_sentinel
    from pixlstash.tasks.missing_image_embedding_finder import (
        MissingImageEmbeddingFinder,
    )

    class _Clip:
        def suggested_batch_size(self):
            return 4

    class _Engine:
        clip_embedding_workflow = _Clip()

    with Vault(image_root=str(tmp_path)) as vault:

        def seed(session: Session):
            picture = Picture(
                file_path=_write_image(tmp_path, "p0.png"),
                format="png",
                width=400,
                height=300,
                deleted=False,
            )
            session.add(picture)
            session.flush()
            session.add(Tag(picture_id=picture.id, tag=make_tag_sentinel()))
            session.commit()
            return picture.id

        picture_id = vault.db.run_task(seed)
        finder = MissingImageEmbeddingFinder(vault.db, lambda: _Engine())
        assert finder.depends_on() == []

        task = finder.find_task()
        assert task is not None, "an untagged, faceless picture is CLIP work"
        assert task.params["picture_ids"] == [picture_id]


_IMAGE_EMBEDDING_PROBE = "where picture.image_embedding is null"


def _image_embedding_plan(vault, aesthetic_disabled: bool) -> list[str]:
    with _StatementRecorder(vault.db._engine, _IMAGE_EMBEDDING_PROBE) as rec:
        vault.db.run_immediate_read_task(
            lambda session: ImageEmbeddingTask.fetch_work(
                session, aesthetic_disabled=aesthetic_disabled, limit=8
            )
        )
    for column in _BLOB_COLUMNS:
        assert f"picture.{column}," not in rec.only, rec.only
    return _explain(vault, rec.only, rec.parameters[0])


def test_image_embedding_probe_is_served_by_its_partial_indexes(tmp_path):
    """Both OR arms resolve through their own partial index (MULTI-INDEX OR).

    Before 0112 the ``length(image_embedding) = 0`` arm forced ``SCAN picture``
    on every planner sweep.
    """
    with Vault(image_root=str(tmp_path)) as vault:
        _seed(vault, tmp_path, count=3, with_faces=False, done=2)

        plan = _image_embedding_plan(vault, aesthetic_disabled=False)
        assert any("ix_picture_image_embedding_missing" in line for line in plan), plan
        assert any("ix_picture_aesthetic_score_missing" in line for line in plan), plan
        assert not any("SCAN picture" in line for line in plan), plan

        plan = _image_embedding_plan(vault, aesthetic_disabled=True)
        assert any("ix_picture_image_embedding_missing" in line for line in plan), plan
        assert not any("aesthetic_score" in line for line in plan), plan
        assert not any("SCAN picture" in line for line in plan), plan


_TAG_PROBE = "picture.id in (select tag.picture_id"


def test_tag_probe_is_served_by_the_tag_index(tmp_path):
    """The pending-sentinel range drives the probe through ``ix_tag_tag``.

    The old ``tags.any(tag LIKE '\\_\\_tag%' ESCAPE '\\')`` could not use an index
    (SQLite's LIKE optimisation is off with ESCAPE), so it was ``SCAN picture``.
    """
    from pixlstash.db_models import Tag
    from pixlstash.db_models.tag import make_tag_sentinel
    from pixlstash.tasks.missing_tag_finder import MissingTagFinder

    with Vault(image_root=str(tmp_path)) as vault:
        ids = _seed(vault, tmp_path, count=3, with_faces=True)
        _add_rows(vault, [Tag(picture_id=ids[0], tag=make_tag_sentinel())])

        with _StatementRecorder(vault.db._engine, _TAG_PROBE) as rec:
            vault.db.run_immediate_read_task(
                lambda s: MissingTagFinder._fetch_missing_tags(s, 64)
            )

        plan = _explain(vault, rec.only, rec.parameters[0])
        assert any("ix_tag_tag" in line for line in plan), plan
        # The outer loop is a rowid lookup fed by the tag index, not a scan.
        assert plan[0].startswith("SEARCH picture USING INTEGER PRIMARY KEY"), plan
        assert not any("TEMP B-TREE" in line.upper() for line in plan), plan


def test_tagging_starts_per_picture_as_soon_as_its_faces_are_known(tmp_path):
    """A picture whose face row exists is tagged while its neighbour, still in
    the face stage, is not - and a no-face sentinel row counts as known.
    """
    from pixlstash.db_models import Tag
    from pixlstash.db_models.tag import make_tag_sentinel
    from pixlstash.tasks.missing_tag_finder import MissingTagFinder

    with Vault(image_root=str(tmp_path)) as vault:
        with_face, no_face_sentinel, faces_pending = _seed(
            vault, tmp_path, count=3, with_faces=False
        )
        _add_rows(
            vault,
            [
                Face(picture_id=with_face, face_index=0, bbox=[1, 1, 9, 9]),
                Face(picture_id=no_face_sentinel, face_index=-1),
                Tag(picture_id=with_face, tag=make_tag_sentinel()),
                Tag(picture_id=no_face_sentinel, tag=make_tag_sentinel("wd14")),
                Tag(picture_id=faces_pending, tag=make_tag_sentinel()),
            ],
        )

        selected = {
            p.id
            for p in vault.db.run_immediate_read_task(
                lambda s: MissingTagFinder._fetch_missing_tags(s, 64)
            )
        }
        assert with_face in selected
        assert no_face_sentinel in selected, "a no-face sentinel is 'faces known'"
        assert faces_pending not in selected, "its face stage has not run yet"


_TEXT_PROBE = "picture.text_embedding is null"


class _TextEngine:
    def __init__(self, **tagger_settings):
        self.tagger_settings = tagger_settings or None


def _text_candidates(vault, engine) -> set[int]:
    from pixlstash.tasks.missing_text_embedding_finder import (
        MissingTextEmbeddingFinder,
    )

    finder = MissingTextEmbeddingFinder(vault.db, lambda: engine)
    rows = vault.db.run_immediate_read_task(finder._fetch_candidates, 64)
    return {p.id for p in rows}


def _seed_text_stage(vault, tmp_path):
    """Four pictures, none embedded: described / tags pending / undescribed /
    description still a sentinel. Returns their ids in that order."""
    from pixlstash.db_models import Tag
    from pixlstash.db_models.tag import make_description_sentinel, make_tag_sentinel

    ids = _seed(vault, tmp_path, count=4, with_faces=False)

    def describe(session: Session):
        for pid, text_ in zip(
            ids, ("a cat", "a dog", None, make_description_sentinel())
        ):
            session.get(Picture, pid).description = text_
        session.add(Tag(picture_id=ids[1], tag=make_tag_sentinel()))
        session.commit()

    vault.db.run_task(describe)
    return ids


def test_text_probe_is_served_by_its_partial_index(tmp_path):
    with Vault(image_root=str(tmp_path)) as vault:
        _seed_text_stage(vault, tmp_path)
        engine = _TextEngine(active_tag_plugin="wd14", active_description_plugin="x")

        with _StatementRecorder(vault.db._engine, _TEXT_PROBE) as rec:
            _text_candidates(vault, engine)

        plan = _explain(vault, rec.only, rec.parameters[0])
        assert any("ix_picture_text_embedding_missing" in line for line in plan), plan
        assert not any("TEMP B-TREE" in line.upper() for line in plan), plan
        for column in ("image_embedding", "likeness_parameters", "features"):
            assert column not in rec.only, rec.only


def test_text_embedding_waits_per_picture_for_tags_and_description(tmp_path):
    with Vault(image_root=str(tmp_path)) as vault:
        described, tags_pending, undescribed, sentinel = _seed_text_stage(
            vault, tmp_path
        )
        both_on = _TextEngine(active_tag_plugin="wd14", active_description_plugin="x")
        assert _text_candidates(vault, both_on) == {described}

        # A stage that is off never delivers, so it is not waited for.
        tags_off = _TextEngine(active_tag_plugin=None, active_description_plugin="x")
        assert _text_candidates(vault, tags_off) == {described, tags_pending}
        descriptions_off = _TextEngine(active_tag_plugin="wd14")
        assert _text_candidates(vault, descriptions_off) == {
            described,
            undescribed,
            sentinel,
        }
        # No tagger_settings at all: tagger off, Florence-2 captioning on.
        assert _text_candidates(vault, _TextEngine()) == {described, tags_pending}


_LIKENESS_PROBE = "picture.size_bin_index is null"


def _likeness_candidates(vault) -> set[int]:
    from pixlstash.tasks.likeness_parameters_task import LikenessParametersTask
    from pixlstash.utils.likeness.likeness_parameter_utils import (
        LikenessParameterUtils,
    )

    rows = vault.db.run_immediate_read_task(
        LikenessParameterUtils.find_next_work, LikenessParametersTask.SCAN_LIMIT
    )
    return {pid for pid, _w, _h in (rows or [])}


def test_likeness_parameter_probe_is_served_by_the_size_bin_index(tmp_path):
    """``size_bin_index IS NULL`` walks ``ix_picture_size_bin_index`` in rowid
    order, so no partial index and no sort are needed; the per-picture quality
    check is an ``ix_quality_picture_id`` lookup."""
    from pixlstash.db_models import Quality

    with Vault(image_root=str(tmp_path)) as vault:
        ids = _seed(vault, tmp_path, count=3, with_faces=False)
        _add_rows(vault, [Quality(picture_id=ids[0], sharpness=0.4)])

        with _StatementRecorder(vault.db._engine, _LIKENESS_PROBE) as rec:
            _likeness_candidates(vault)

        plan = _explain(vault, rec.only, rec.parameters[0])
        assert plan[0].startswith(
            "SEARCH picture USING INDEX ix_picture_size_bin_index"
        ), plan
        assert any("ix_quality_picture_id" in line for line in plan), plan
        assert not any("TEMP B-TREE" in line.upper() for line in plan), plan


def test_likeness_parameters_wait_per_picture_for_quality(tmp_path):
    """A picture with its quality row is offered while its neighbours -- no
    Quality row, or a row not yet filled in -- are not (no stage-wide gate)."""
    from pixlstash.db_models import Quality
    from pixlstash.tasks.missing_likeness_parameters_finder import (
        MissingLikenessParametersFinder,
    )

    with Vault(image_root=str(tmp_path)) as vault:
        done, no_row, unfilled = _seed(vault, tmp_path, count=3, with_faces=False)
        _add_rows(
            vault,
            [
                Quality(picture_id=done, sharpness=0.4, brightness=0.5),
                Quality(picture_id=unfilled, sharpness=None),
            ],
        )
        assert _likeness_candidates(vault) == {done}

        finder = MissingLikenessParametersFinder(vault.db)
        assert finder.depends_on() == []
        task = finder.find_task()
        assert task is not None and task.params["picture_ids"] == [done], (
            "the finder must not wait for the other two pictures' quality"
        )
        assert no_row not in _likeness_candidates(vault)
