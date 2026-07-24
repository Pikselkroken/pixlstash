"""Reproduction for GitHub issue #585 — "Invalid Images break tasks".

When a reference folder contains a corrupt/undecodable image, the task system
retries it forever. The mechanism is generic to every ``Missing*Finder``:

1. A finder selects pictures whose target column is unset. For image embeddings
   that is ``MissingImageEmbeddingFinder``, whose candidate query is
   ``ImageEmbeddingTask.fetch_work`` (``image_embedding IS NULL`` OR a
   zero-length blob).
2. The task tries to open the file. For a corrupt image
   ``ImageUtils.load_image_or_video_bgr(path)`` returns ``None``, so no
   embedding can be produced.
3. There is **no field on ``Picture`` and no logic in any finder** that records a
   picture as unprocessable / failed / skip. The one thing the task does write on
   failure is an *empty* embedding blob (``_build_failure_updates`` →
   ``np.array([], dtype=np.float32).tobytes()``), and ``fetch_work`` explicitly
   treats a zero-length blob as still-missing. So whether the column is left
   ``NULL`` or stamped with the empty "failed" marker, the finder re-selects the
   same picture on the very next sweep — an unbounded retry loop.

These tests assert the CURRENT (buggy) behavior so they pass today and pin the
bug down. The eventual fix must give the pipeline a way to mark an
unprocessable picture as done/failed so it is NOT re-selected; when that lands,
the "re-selected again" assertions below become the ones that flip (the invalid
picture must drop out of ``fetch_work`` after a failed attempt).

ML-free: exercises only the DB candidate query and the on-disk image loader — no
CLIP / insightface / tagger inference is run.
"""

import os
from datetime import datetime

import numpy as np
from sqlmodel import Session

from pixlstash.db_models import Picture
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.vault import Vault

# Bytes that exist on disk as a ``.jpg`` but are not a decodable image. cv2 and
# the PIL fallback in ``load_image_or_video_bgr`` both refuse them.
_CORRUPT_JPEG_BYTES = b"this is not a real JPEG, just some garbage bytes \xff\xd8\x00"

# The exact "failed" marker ImageEmbeddingTask._build_failure_updates writes when
# it cannot produce an embedding: a zero-length float32 blob.
_EMPTY_EMBEDDING_MARKER = np.array([], dtype=np.float32).tobytes()


def _write_corrupt_jpeg(path) -> str:
    with open(path, "wb") as handle:
        handle.write(_CORRUPT_JPEG_BYTES)
    return str(path)


def _seed_picture(vault, file_path: str, image_embedding) -> int:
    """Insert one Picture row and return its id."""
    now = datetime.now()

    def seed(session: Session):
        picture = Picture(
            file_path=file_path,
            format="jpg",
            width=64,
            height=64,
            deleted=False,
            imported_at=now,
            image_embedding=image_embedding,
            aesthetic_score=3.0,
            created_at=now,
        )
        session.add(picture)
        session.commit()
        return picture.id

    return vault.db.run_task(seed)


def _fetch_work_ids(vault) -> set:
    work = vault.db.run_task(
        lambda session: ImageEmbeddingTask.fetch_work(
            session,
            aesthetic_disabled=True,
        )
    )
    return {pid for pid, _ in work}


def test_585_corrupt_image_left_null_is_endlessly_reselected(tmp_path):
    """#585: a corrupt image whose column stays NULL is re-selected forever."""
    corrupt_path = _write_corrupt_jpeg(tmp_path / "corrupt.jpg")

    with Vault(image_root=str(tmp_path)) as vault:
        # Step 1: a Picture pointing at the invalid file, target column unset.
        pic_id = _seed_picture(vault, corrupt_path, image_embedding=None)

        # Step 2: the root cause — the file genuinely fails to open.
        assert os.path.exists(corrupt_path), "file must exist so it is not purged"
        assert ImageUtils.load_image_or_video_bgr(corrupt_path) is None

        # Step 3: the finder's candidate query selects the invalid picture.
        assert pic_id in _fetch_work_ids(vault)

        # Step 4: simulate one full processing attempt that fails. A failed load
        # cannot produce an embedding and NOTHING marks the picture, so the
        # column stays NULL. Running the selection again re-selects the SAME
        # picture. THIS is the reproduction: it will be retried forever.
        #
        # (No DB write happens here precisely because there is no failed/skip
        # mechanism to write — that absence is the bug.)
        assert pic_id in _fetch_work_ids(vault), (
            "#585: corrupt image re-selected after a failed attempt (endless retry)"
        )


def test_585_corrupt_image_failed_marker_is_endlessly_reselected(tmp_path):
    """#585: even the task's own empty-blob 'failed' marker is re-selected.

    ImageEmbeddingTask._build_failure_updates writes a zero-length embedding when
    it cannot decode an image, but fetch_work treats a zero-length blob as
    still-missing (``func.length(image_embedding) == 0``). So the only 'mark
    failed' write the pipeline performs does not stop re-selection either — there
    is no effective skip mechanism anywhere.
    """
    corrupt_path = _write_corrupt_jpeg(tmp_path / "corrupt.jpg")

    with Vault(image_root=str(tmp_path)) as vault:
        pic_id = _seed_picture(vault, corrupt_path, image_embedding=None)

        assert ImageUtils.load_image_or_video_bgr(corrupt_path) is None
        assert pic_id in _fetch_work_ids(vault)

        # Apply the exact failure marking the real task writes on a load failure.
        def mark_failed(session: Session):
            picture = session.get(Picture, pic_id)
            picture.image_embedding = _EMPTY_EMBEDDING_MARKER
            session.commit()

        vault.db.run_task(mark_failed)

        # The empty-blob marker does NOT remove the picture from the work set.
        assert pic_id in _fetch_work_ids(vault), (
            "#585: empty-blob 'failed' marker is still re-selected (endless retry)"
        )
        remaining = vault.db.run_task(
            lambda session: ImageEmbeddingTask.count_remaining(
                session, aesthetic_disabled=True
            )
        )
        assert int(remaining or 0) == 1
