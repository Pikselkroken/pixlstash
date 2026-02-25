from datetime import datetime

import numpy as np

from sqlmodel import Session

from pixlvault.db_models import Picture
from pixlvault.image_embedding_worker import ImageEmbeddingWorker
from pixlvault.vault import Vault


def test_fetch_work_includes_empty_embedding_blob(tmp_path):
    """Empty embedding blobs should be treated as missing and reprocessed."""
    with Vault(image_root=str(tmp_path)) as vault:
        now = datetime.now()

        def seed(session: Session):
            missing = Picture(
                file_path="/tmp/missing.jpg",
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
                file_path="/tmp/empty.jpg",
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
                file_path="/tmp/done.jpg",
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

        vault.db.run_task(seed)

        worker = ImageEmbeddingWorker(vault.db, None, event_callback=None)
        worker._aesthetic_disabled = True

        work = vault.db.run_task(worker._fetch_work)
        remaining = int(vault.db.run_task(worker._count_remaining) or 0)
        work_ids = {pid for pid, _ in work}

        assert len(work_ids) == 2
        assert remaining == 2


def test_fetch_work_includes_missing_aesthetic_when_embedding_exists(tmp_path):
    """Pictures with valid embeddings but missing aesthetic score should be selected."""
    with Vault(image_root=str(tmp_path)) as vault:
        now = datetime.now()

        def seed(session: Session):
            needs_aesthetic = Picture(
                file_path="/tmp/needs_aesthetic.jpg",
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
                file_path="/tmp/complete.jpg",
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

        worker = ImageEmbeddingWorker(vault.db, None, event_callback=None)
        worker._aesthetic_disabled = False

        work = vault.db.run_task(worker._fetch_work)
        remaining = int(vault.db.run_task(worker._count_remaining) or 0)

        assert len(work) == 1
        assert remaining == 1
