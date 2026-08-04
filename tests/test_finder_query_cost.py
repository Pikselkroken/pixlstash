"""The hot idle finder probes cost O(matching rows), not O(library) (issue #651).

The WorkPlanner sweeps every registered finder on a short interval, so a finder's
candidate query runs continuously even on a fully-processed library where it
matches nothing. Two of them were the dominant background read:

* ``MissingThumbnailFinder._fetch_missing`` selected the FULL ``Picture`` ORM
  entity — including ``image_embedding``, ``text_embedding`` and
  ``likeness_parameters``, three LargeBinary columns — plus every ``Face`` column
  including ``features``, for every candidate row.
* ``SmartScoreTask.find_pictures_missing_smart_score`` did the same, and nothing
  downstream reads anything but ``pic.id``.

and both were served by ``ix_picture_deleted``, i.e. SQLite walked every
non-deleted row to prove there was no work.

Three properties are asserted here, one per failure mode:

1. **Plan** — each probe is served by its partial index, so it touches only rows
   that actually need work. This database never runs ``ANALYZE``: there is no
   ``sqlite_stat1``, so the planner scores a partial index by the table's default
   row estimate and only prefers it when it claims MORE equality terms than the
   index it competes with. That is why both indexes lead with the nullable column
   (``IS NULL`` is an equality term for SQLite) followed by ``deleted``, and why
   an ``(id)``-only partial index would be built, maintained, and never used.
2. **Columns** — the thumbnail probe's emitted SQL names no BLOB column.
3. **Round trip** — the narrowed load still carries everything
   ``ThumbnailGenerationTask`` reads. ``run_immediate_read_task`` closes the
   session, so the task runs against DETACHED pictures and any attribute the
   ``load_only`` forgot raises ``DetachedInstanceError`` at runtime — which
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
    runs ``ANALYZE`` — so on a real vault the probe fell back to
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
    index-creation order — which ``metadata.create_all()`` iterates from a set, so
    it differs between processes. An earlier revision of this test asserted the
    equality shape and failed roughly every other run for exactly that reason.
    The one-pass shape is chosen unconditionally, and is what ``GET /characters``
    should be asking anyway: one grouped query rather than one probe per
    character. See ``Face.__table_args__``.
    """
    with Vault(image_root=str(tmp_path)) as vault:
        _seed(vault, tmp_path, count=4, with_faces=True)
        plan = _explain(
            vault,
            "SELECT face.character_id, count(*) FROM face "
            "WHERE face.features IS NOT NULL AND face.character_id IS NOT NULL "
            "GROUP BY face.character_id",
            (),
        )
        assert any("ix_face_character_features" in line for line in plan), (
            f"character-features rollup is not using its partial index; plan was {plan}"
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
    fail loudly — it would just stop producing thumbnails forever. Hence the
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
