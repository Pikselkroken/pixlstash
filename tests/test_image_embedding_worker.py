import os
import shutil
from datetime import datetime, timezone

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
        now = datetime.now(timezone.utc)

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
        now = datetime.now(timezone.utc)

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

    def reduce_for_preprocess(self, images):
        return images

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


@pytest.mark.parametrize("clip_loaded", [False, True])
def test_the_embedding_preload_keeps_only_what_clip_reads(tmp_path, clip_loaded):
    """No decoded original survives the preload (#2748), and the tensor CLIP
    is given is bit-identical to the one the original produces, whether or
    not CLIP was loaded when the preload ran (the first batches after start-up).

    Holding the originals beside their tensors kept 6.6 GB for a batch of
    64 large photos, three batches preloading at once. An image CLIP would
    enlarge is held as it is, never upscaled.
    """
    from types import SimpleNamespace

    import open_clip
    import torch
    from PIL import Image as PILImage

    from pixlstash.inference.workflows.clip_embedding import ClipEmbeddingWorkflow
    from pixlstash.tagger_plugins.clip_service import (
        CLIP_MODEL_NAME,
        CLIP_MODEL_WEIGHTS,
        ClipService,
        _unloaded_first_resize,
    )

    # The preprocessing a real load gets, without its weights.
    _model, _, preprocess = open_clip.create_model_and_transforms(
        CLIP_MODEL_NAME, pretrained=CLIP_MODEL_WEIGHTS, load_weights=False
    )
    assert repr(_unloaded_first_resize()) == repr(preprocess.transforms[0])

    service = ClipService(device="cpu")
    if clip_loaded:
        service._model = service._tokenizer = object()
        service._preprocess = preprocess
    workflow = ClipEmbeddingWorkflow(SimpleNamespace(clip_service=service))

    rng = np.random.default_rng(2748)
    # Landscape, portrait, one smaller than CLIP's input, two thin strips:
    # the last two would be enlarged by the resize, and are held as they are.
    sizes = [(2400, 1600), (333, 1001), (100, 50), (1201, 77), (2000, 20)]
    names = [f"p{i}.png" for i in range(len(sizes))]
    for (w, h), name in zip(sizes, names):
        array = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        PILImage.fromarray(array).save(tmp_path / name)
    with Vault(image_root=str(tmp_path)) as vault:
        task = ImageEmbeddingTask(
            database=vault.db,
            clip_workflow=workflow,
            batch=[(i + 1, name) for i, name in enumerate(names)],
        )
        task._preload_images_task()
        preloaded = task._preloaded_images

    assert [entry[0] for entry in preloaded] == [1, 2, 3, 4, 5]
    for (_pid, name, held, dhash, tensor), size in zip(preloaded, sizes):
        original = PILImage.open(tmp_path / name).convert("RGB")
        expected = size if min(size) < 224 else None
        if expected is None:
            assert min(held.size) == 224, f"{name} {size} held at {held.size}"
        else:
            assert held.size == expected, f"{name} {size} upscaled to {held.size}"
        assert torch.equal(preprocess(held), preprocess(original)), name
        assert dhash == ImageEmbeddingTask._compute_dhash(original), name
        if clip_loaded:
            assert torch.equal(tensor, preprocess(original)), name
        else:
            assert tensor is None


def test_an_animated_gif_preloads_three_frames_and_a_still_gif_one(tmp_path):
    """An animated GIF is averaged over the frames a video is (#1487)."""
    from PIL import Image as PILImage

    frames = [PILImage.new("RGB", (32, 24), c) for c in ("red", "green", "blue")]
    frames[0].save(
        tmp_path / "anim.gif", save_all=True, append_images=frames[1:], duration=100
    )
    frames[0].save(tmp_path / "still.gif")
    with Vault(image_root=str(tmp_path)) as vault:
        task = ImageEmbeddingTask(
            database=vault.db,
            clip_workflow=_RecordingWorkflow(),
            batch=[(1, "anim.gif"), (2, "still.gif")],
        )
        task._preload_images_task()

        assert [entry[0] for entry in task._preloaded_images] == [1, 1, 1, 2]


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
