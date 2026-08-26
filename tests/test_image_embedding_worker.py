import os
import shutil
from datetime import datetime

import numpy as np

from sqlmodel import Session, text

from pixlstash.db_models import Picture
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.vault import Vault

PICTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "pictures")


def test_empty_embedding_blob_is_work_again_after_the_0112_reset(tmp_path):
    """A failed picture is stored as NULL and re-selected by ``fetch_work``.

    The empty-blob marker older releases wrote is no longer matched (the
    ``length()`` arm defeated the probe's partial index); migration 0112 resets
    those rows to NULL once, after which they are ordinary missing work.
    """
    # Copy real images so MissingFilePurgeTask doesn't delete these records
    shutil.copy(os.path.join(PICTURES_DIR, "Bad1.png"), tmp_path / "missing.jpg")
    shutil.copy(os.path.join(PICTURES_DIR, "Bad1.png"), tmp_path / "empty.jpg")
    shutil.copy(os.path.join(PICTURES_DIR, "Bad1.png"), tmp_path / "done.jpg")
    with Vault(image_root=str(tmp_path)) as vault:
        now = datetime.now()

        def seed(session: Session):
            missing = Picture(
                file_path=str(tmp_path / "missing.jpg"),
                format="jpg",
                width=64,
                height=64,
                deleted=False,
                imported_at=now,
                image_embedding=None,
                aesthetic_score=3.0,
                created_at=now,
            )
            empty = Picture(
                file_path=str(tmp_path / "empty.jpg"),
                format="jpg",
                width=64,
                height=64,
                deleted=False,
                imported_at=now,
                image_embedding=np.array([], dtype=np.float32).tobytes(),
                aesthetic_score=3.0,
                created_at=now,
            )
            done = Picture(
                file_path=str(tmp_path / "done.jpg"),
                format="jpg",
                width=64,
                height=64,
                deleted=False,
                imported_at=now,
                image_embedding=np.ones(512, dtype=np.float32).tobytes(),
                aesthetic_score=3.0,
                created_at=now,
            )
            session.add(missing)
            session.add(empty)
            session.add(done)
            session.commit()
            return missing.id, empty.id

        missing_id, empty_id = vault.db.run_task(seed)

        def fetch(session: Session):
            work = ImageEmbeddingTask.fetch_work(session, aesthetic_disabled=True)
            remaining = ImageEmbeddingTask.count_remaining(
                session, aesthetic_disabled=True
            )
            return {pid for pid, _ in work}, int(remaining or 0)

        assert vault.db.run_task(fetch) == ({missing_id}, 1)

        # The task itself now stores NULL for a failure, never an empty blob.
        task = ImageEmbeddingTask(database=vault.db, clip_workflow=None, batch=[])
        assert task._build_failure_updates({empty_id})[0][1] is None

        def reset(session: Session):  # the 0112 one-time NULL-reset
            session.execute(
                text(
                    "UPDATE picture SET image_embedding = NULL "
                    "WHERE image_embedding IS NOT NULL AND length(image_embedding) = 0"
                )
            )
            session.commit()

        vault.db.run_task(reset)
        assert vault.db.run_task(fetch) == ({missing_id, empty_id}, 2)


def test_fetch_work_includes_missing_aesthetic_when_embedding_exists(tmp_path):
    """Pictures with valid embeddings but missing aesthetic score should be selected."""
    # Create actual files so MissingFilePurgeTask doesn't delete these records
    shutil.copy(
        os.path.join(PICTURES_DIR, "Bad1.png"), tmp_path / "needs_aesthetic.jpg"
    )
    shutil.copy(os.path.join(PICTURES_DIR, "Bad1.png"), tmp_path / "complete.jpg")
    with Vault(image_root=str(tmp_path)) as vault:
        now = datetime.now()

        def seed(session: Session):
            needs_aesthetic = Picture(
                file_path=str(tmp_path / "needs_aesthetic.jpg"),
                format="jpg",
                width=64,
                height=64,
                deleted=False,
                imported_at=now,
                image_embedding=np.ones(512, dtype=np.float32).tobytes(),
                aesthetic_score=None,
                created_at=now,
            )
            complete = Picture(
                file_path=str(tmp_path / "complete.jpg"),
                format="jpg",
                width=64,
                height=64,
                deleted=False,
                imported_at=now,
                image_embedding=np.ones(512, dtype=np.float32).tobytes(),
                aesthetic_score=2.5,
                created_at=now,
            )
            session.add(needs_aesthetic)
            session.add(complete)
            session.commit()

        vault.db.run_task(seed)

        work = vault.db.run_task(
            lambda session: ImageEmbeddingTask.fetch_work(
                session,
                aesthetic_disabled=False,
            )
        )
        remaining = int(
            vault.db.run_task(
                lambda session: ImageEmbeddingTask.count_remaining(
                    session,
                    aesthetic_disabled=False,
                )
            )
            or 0
        )

        assert len(work) == 1
        assert remaining == 1
