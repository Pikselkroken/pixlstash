"""Regression test for GitHub issue #585 - "Invalid Images break tasks".

A corrupt/undecodable image used to be retried forever: a ``Missing*Finder``
re-selects any picture whose target column is unset, the task cannot decode the
file, and nothing durably marks it done (``ImageEmbeddingTask`` writes an *empty*
embedding blob that ``fetch_work`` still treats as missing), so the same picture
is picked up on every sweep.

The fix is an in-memory, process-lifetime ``UnprocessableImageRegistry`` (owned by
the database, reachable from both task and finder threads via ``self._db``):

* a decode failure in an image-reading task calls ``mark_unprocessable`` (the
  shared choke point), pinning the picture to the file's current ``(mtime, size)``;
* ``BaseTaskFinder._filter_and_claim`` skips suppressed pictures for every finder,
  and ``count_remaining`` / ``fetch_work`` exclude them so progress can reach 0;
* suppression is keyed on the file signature, so rewriting the file (the "still
  being written" / repaired case) lifts it and the picture is retried.

These tests assert the FIXED behavior. They exercise the real decode-failure path
(``ImageEmbeddingTask._process_preloaded`` with a ``None`` image, which never
touches CLIP) and the real suppression path (``_filter_and_claim`` /
``count_remaining``). ML-free: no CLIP / insightface / tagger inference is run.
"""

import os
from datetime import datetime

from sqlmodel import Session

from pixlstash.db_models import Picture
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.missing_image_embedding_finder import MissingImageEmbeddingFinder
from pixlstash.tasks.missing_thumbnail_finder import MissingThumbnailFinder
from pixlstash.tasks.quality_task import QualityTask
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.vault import Vault

# Bytes that exist on disk as a ``.jpg`` but are not a decodable image. cv2 and
# the PIL fallback in ``load_image_or_video_bgr`` both refuse them.
_CORRUPT_JPEG_BYTES = b"this is not a real JPEG, just some garbage bytes \xff\xd8\x00"


def _write_corrupt_jpeg(path, payload: bytes = _CORRUPT_JPEG_BYTES) -> str:
    with open(path, "wb") as handle:
        handle.write(payload)
    return str(path)


def _seed_picture(vault, file_path: str) -> int:
    """Insert one Picture row (embedding unset) and return its id."""
    now = datetime.now()

    def seed(session: Session):
        picture = Picture(
            file_path=file_path,
            format="jpg",
            width=64,
            height=64,
            deleted=False,
            imported_at=now,
            image_embedding=None,
            aesthetic_score=3.0,
            created_at=now,
        )
        session.add(picture)
        session.commit()
        return picture.id

    return vault.db.run_task(seed)


def _fetch_work_ids(vault, suppressed_ids=None) -> set:
    work = vault.db.run_task(
        lambda session: ImageEmbeddingTask.fetch_work(
            session,
            aesthetic_disabled=True,
            suppressed_ids=suppressed_ids,
        )
    )
    return {pid for pid, _ in work}


def _count_remaining(vault, suppressed_ids=None) -> int:
    return int(
        vault.db.run_task(
            lambda session: ImageEmbeddingTask.count_remaining(
                session,
                aesthetic_disabled=True,
                suppressed_ids=suppressed_ids,
            )
        )
        or 0
    )


def _run_failed_decode(vault, pic_id: int, file_path: str) -> None:
    """Drive the real decode-failure path of ImageEmbeddingTask for one picture.

    A ``None`` preloaded image is exactly what the preload thread produces for a
    corrupt file. This branch writes the empty-blob 'failed' marker and calls the
    shared ``mark_unprocessable`` choke point - without ever touching CLIP.
    """
    task = ImageEmbeddingTask(
        database=vault.db,
        clip_workflow=None,
        batch=[(pic_id, file_path)],
    )
    task._process_preloaded([(pic_id, file_path, None)])


def test_585_corrupt_image_is_suppressed_after_failed_decode(tmp_path):
    """#585 (fixed): a corrupt image is NOT re-selected after a failed decode."""
    corrupt_path = _write_corrupt_jpeg(tmp_path / "corrupt.jpg")

    with Vault(image_root=str(tmp_path)) as vault:
        pic_id = _seed_picture(vault, corrupt_path)

        # Root cause is real: the file genuinely fails to open.
        assert os.path.exists(corrupt_path)
        assert ImageUtils.load_image_or_video_bgr(corrupt_path) is None

        # Before the fix runs: the finder's raw query selects it (still missing).
        assert pic_id in _fetch_work_ids(vault)
        assert _count_remaining(vault) == 1

        # Drive one real, failed processing attempt (marks it unprocessable).
        _run_failed_decode(vault, pic_id, corrupt_path)

        registry = vault.db.unprocessable_images
        assert registry.is_suppressed(pic_id)
        suppressed = registry.active_suppressed_ids()
        assert suppressed == {pic_id}

        # The finder now excludes it at the shared claim choke point...
        finder = MissingImageEmbeddingFinder(
            database=vault.db, engine_getter=lambda: None
        )
        rows = vault.db.run_task(
            lambda session: ImageEmbeddingTask.fetch_work(
                session, aesthetic_disabled=True
            )
        )
        selected = finder._filter_and_claim(rows, batch_limit=10)
        assert pic_id not in {getattr(row, "id", None) for row in selected}, (
            "#585: suppressed corrupt image must not be claimed for a new task"
        )

        # ...and both the candidate query and progress count drop it to zero.
        assert _fetch_work_ids(vault, suppressed_ids=suppressed) == set()
        assert _count_remaining(vault, suppressed_ids=suppressed) == 0


def test_585_suppression_lifts_when_file_is_modified(tmp_path):
    """#585 "until modified": rewriting the file makes the picture selectable again."""
    corrupt_path = _write_corrupt_jpeg(tmp_path / "corrupt.jpg")

    with Vault(image_root=str(tmp_path)) as vault:
        pic_id = _seed_picture(vault, corrupt_path)
        _run_failed_decode(vault, pic_id, corrupt_path)

        registry = vault.db.unprocessable_images
        assert registry.active_suppressed_ids() == {pic_id}
        assert _count_remaining(vault, suppressed_ids={pic_id}) == 0

        # The file is modified (e.g. an interrupted write finally completes). A
        # different size guarantees the signature moves even on coarse-mtime
        # filesystems, so suppression must lift regardless of timer resolution.
        _write_corrupt_jpeg(
            corrupt_path, payload=_CORRUPT_JPEG_BYTES + b" now a different length"
        )

        assert not registry.is_suppressed(pic_id)
        suppressed = registry.active_suppressed_ids()
        assert suppressed == set()

        # It is selectable again: the finder claims it and it counts as remaining.
        finder = MissingImageEmbeddingFinder(
            database=vault.db, engine_getter=lambda: None
        )
        rows = vault.db.run_task(
            lambda session: ImageEmbeddingTask.fetch_work(
                session, aesthetic_disabled=True
            )
        )
        selected = finder._filter_and_claim(rows, batch_limit=10)
        assert pic_id in {getattr(row, "id", None) for row in selected}, (
            "#585: a modified file must be retried, not permanently suppressed"
        )
        assert pic_id in _fetch_work_ids(vault, suppressed_ids=suppressed)
        assert _count_remaining(vault, suppressed_ids=suppressed) == 1


def test_585_filter_and_claim_never_reads_file_path(tmp_path):
    """#585 regression: ``_filter_and_claim`` must not touch ``picture.file_path``.

    The CI failure was a ``DetachedInstanceError``: some finders' candidate
    queries return detached ``Picture`` objects whose ``file_path`` is deferred,
    and the suppression check used to read it while claiming, crashing the
    WorkPlanner thread. Suppression now looks the picture up by id against the
    registry's own stored path, so ``file_path`` is never accessed here.
    """

    class _FilePathExplodes:
        """Stands in for a detached Picture whose file_path would lazy-load."""

        def __init__(self, pid: int):
            self.id = pid

        @property
        def file_path(self):
            raise AssertionError(
                "_filter_and_claim must not access picture.file_path (#585)"
            )

    with Vault(image_root=str(tmp_path)) as vault:
        finder = MissingImageEmbeddingFinder(
            database=vault.db, engine_getter=lambda: None
        )
        candidates = [_FilePathExplodes(1), _FilePathExplodes(2)]
        selected = finder._filter_and_claim(candidates, batch_limit=10)
        assert [c.id for c in selected] == [1, 2]


def test_585_thumbnail_task_suppresses_corrupt_image(tmp_path):
    """#585 follow-up: ThumbnailGenerationTask must mark undecodable sources.

    Reported against v1.8.0: the original fix marked decode failures only in
    ImageEmbeddingTask, so a corrupt picture whose thumbnail columns were NULL
    (e.g. after the v1.8.0 thumbnail-regeneration reset) was re-selected by
    MissingThumbnailFinder on every sweep forever.
    """
    corrupt_path = _write_corrupt_jpeg(tmp_path / "corrupt.jpg")

    with Vault(image_root=str(tmp_path)) as vault:
        pic_id = _seed_picture(vault, corrupt_path)

        # The finder selects it (thumbnail_width is NULL) and builds a task,
        # and the "Upgrading thumbnails" progress count sees it as remaining.
        finder = MissingThumbnailFinder(vault.db)
        task = finder.find_task()
        assert task is not None
        assert task.params["picture_ids"] == [pic_id]
        assert vault.db.run_task(vault._count_missing_thumbnails) == 1

        # The task fails to decode the source: no columns are written...
        result = task._run_task()
        assert result == {"changed_count": 0}

        # ...but the picture is now suppressed, so after the claim is released
        # the finder no longer re-selects it - the old endless-retry loop -
        # and the progress count reaches 0 so the bar can complete.
        assert vault.db.unprocessable_images.is_suppressed(pic_id)
        finder.on_task_complete(task, None)
        assert finder.find_task() is None
        assert vault.db.run_task(vault._count_missing_thumbnails) == 0


def test_585_quality_metadata_backfill_marks_instead_of_raising(tmp_path):
    """#585 follow-up: an undecodable file must not abort quality backfill.

    ``_backfill_missing_picture_metadata`` used to raise on the first corrupt
    picture, killing the whole batch (good pictures included) and leaving every
    column unset, so the finder re-selected the same batch on every sweep.
    """
    from PIL import Image as PILImage

    corrupt_path = _write_corrupt_jpeg(tmp_path / "corrupt.jpg")
    valid_path = str(tmp_path / "valid.jpg")
    PILImage.new("RGB", (8, 8), "red").save(valid_path)

    class _MetadatalessPic:
        """Stands in for a Picture whose format/width/height are unset."""

        def __init__(self, pid: int, file_path: str):
            self.id = pid
            self.file_path = file_path
            self.format = None
            self.width = None
            self.height = None

    corrupt_pic = _MetadatalessPic(1, corrupt_path)
    valid_pic = _MetadatalessPic(2, valid_path)

    with Vault(image_root=str(tmp_path)) as vault:
        task = QualityTask(database=vault.db, pictures=[corrupt_pic, valid_pic])

        # Corrupt picture first: the loop must survive it and reach the valid one.
        task._backfill_missing_picture_metadata([corrupt_pic, valid_pic])

        assert vault.db.unprocessable_images.is_suppressed(corrupt_pic.id)
        assert not vault.db.unprocessable_images.is_suppressed(valid_pic.id)
        assert (valid_pic.width, valid_pic.height) == (8, 8)
        assert valid_pic.format == "JPG"
