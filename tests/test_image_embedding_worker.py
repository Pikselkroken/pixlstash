import os
import shutil
from datetime import datetime

import numpy as np
import pytest

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


class _RecordingWorkflow:
    """Records what the task hands the workflow; embeds nothing real."""

    def __init__(self):
        self.calls = []
        self.preprocessed = []

    def is_ready(self):
        return True

    def ensure_ready(self):
        return None

    def preprocess_images(self, images):
        tensors = [f"tensor-for-{id(img)}" for img in images]
        self.preprocessed.extend(tensors)
        return tensors

    def encode_images(self, images, tensors=None):
        self.calls.append({"n": len(images), "tensors": tensors})
        return np.ones((len(images), 4), dtype=np.float32)


def test_the_preload_pool_does_the_cpu_work_and_keeps_batch_order(tmp_path):
    """Decode, dhash and CLIP preprocessing happen off the GPU worker.

    On the worker they were 4 s of a 4.2 s batch of 128; the forward pass is
    a tenth of a second. The pool must also keep the batch's order, or a
    hash lands on the wrong picture.
    """
    from PIL import Image as PILImage

    from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
    from pixlstash.vault import Vault

    names = [f"p{i}.png" for i in range(9)]
    for i, name in enumerate(names):
        PILImage.new("RGB", (32, 24), (i * 20, 40, 60)).save(tmp_path / name)
    with Vault(image_root=str(tmp_path)) as vault:
        workflow = _RecordingWorkflow()
        task = ImageEmbeddingTask(
            database=vault.db,
            clip_workflow=workflow,
            batch=[(i + 1, name) for i, name in enumerate(names)],
        )
        task._preload_images_task()
        preloaded = task._preloaded_images

        assert [entry[0] for entry in preloaded] == list(range(1, 10)), "order kept"
        assert all(len(entry) == 5 for entry in preloaded)
        assert all(entry[3] is not None for entry in preloaded), "dhash preloaded"
        # The pool interleaves calls, so the workflow's own record is in call
        # order; what matters is that every picture's tensor is ITS tensor.
        assert [entry[4] for entry in preloaded] == [
            f"tensor-for-{id(entry[2])}" for entry in preloaded
        ], "each picture carries the tensor preprocessed from its own image"
        assert sorted(workflow.preprocessed) == sorted(e[4] for e in preloaded)


def test_the_worker_uses_the_preloaded_tensors_and_hashes(tmp_path, monkeypatch):
    from PIL import Image as PILImage

    from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
    from pixlstash.vault import Vault

    img = PILImage.new("RGB", (32, 24), "blue")
    with Vault(image_root=str(tmp_path)) as vault:
        workflow = _RecordingWorkflow()
        task = ImageEmbeddingTask(database=vault.db, clip_workflow=workflow, batch=[])
        monkeypatch.setattr(
            ImageEmbeddingTask,
            "_compute_dhash",
            staticmethod(
                lambda *_: pytest.fail("the worker re-hashed a preloaded image")
            ),
        )
        monkeypatch.setattr(task, "_save_results", staticmethod(lambda *_: []))
        monkeypatch.setattr(
            ImageEmbeddingTask,
            "_save_results",
            staticmethod(lambda session, updates: []),
        )

        task._process_preloaded(
            [(1, "a.png", img, "aa" * 8, "t1"), (2, "b.png", img, "bb" * 8, "t2")]
        )

        assert workflow.calls == [{"n": 2, "tensors": ["t1", "t2"]}]

        # A batch with any tensor missing is handed over whole, untensored:
        # one forward pass, preprocessed by the service in one go.
        workflow.calls.clear()
        task._process_preloaded(
            [(1, "a.png", img, "aa" * 8, "t1"), (2, "b.png", img, "bb" * 8, None)]
        )
        assert workflow.calls == [{"n": 2, "tensors": None}]
