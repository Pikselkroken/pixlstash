"""Finder for training previews whose faces nobody has looked for yet (#2522)."""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.training_runs import FINISHED_STATUSES
from pixlstash.pixl_logging import get_logger
from pixlstash.services.training_service import handle_for_run
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.training_preview_faces_task import TrainingPreviewFacesTask
from pixlstash.training import preview_likeness
from pixlstash.utils.aitoolkit_run import read_run
from pixlstash.utils.insightface_model_utils import DEFAULT_MODEL_PACK

logger = get_logger(__name__)

# How often the runs' sample folders are listed: a preview round lands every
# few hundred steps, so this only bounds how soon its scores appear.
_CHECK_INTERVAL_S = 5.0


class TrainingPreviewFacesFinder(BaseTaskFinder):
    """Hand out the previews of this library's character runs not yet embedded.

    Only a run filed under a character has a reference to compare with, so
    only those are looked at. A preview is handed out once it has not changed
    for ``SETTLE_SECONDS``, so one the trainer is still writing is left alone.
    A run that has ended with every preview embedded is not listed again until
    a restart.

    Registered in ``vault.py`` with the hub finders: runs are hub rows.
    """

    SETTLE_SECONDS = 2.0
    BATCH_SIZE = 12

    def __init__(
        self,
        hub: HubDatabase,
        library_uuid: str,
        image_root: str,
        engine_getter: Callable,
    ) -> None:
        """Initialise the finder.

        Args:
            hub: The hub database holding ``training_run``.
            library_uuid: The open library; another library's runs name
                characters this vault does not hold.
            image_root: The vault's image root, whose ``training/`` holds the
                runs' work dirs.
            engine_getter: Returns the vault's inference engine, or None
                before it has started.
        """
        super().__init__()
        self._hub = hub
        self._library_uuid = library_uuid
        self._training_root = os.path.join(image_root, "training")
        self._engine_getter = engine_getter
        # Runs that ended with every preview embedded.
        self._settled: set[str] = set()
        # Runs a task is working on; ``on_task_complete`` may run on another thread.
        self._claimed: set[str] = set()
        self._lock = threading.Lock()
        self._seen = 0
        self._pending = 0

    def finder_name(self) -> str:
        return "TrainingPreviewFacesFinder"

    def progress(self) -> tuple[int, int]:
        """``(previews of the runs still watched, how many await their faces)``.

        From the last sweep, so the task panel reads no folder.
        """
        return self._seen, self._pending

    def find_task(self):
        if not self._due(_CHECK_INTERVAL_S):
            return None
        engine = self._engine_getter()
        if engine is None:
            return None
        model_pack = getattr(engine, "insightface_model_pack", DEFAULT_MODEL_PACK)
        runs = self._hub.fetchall(
            "SELECT * FROM training_run WHERE library_uuid = ? "
            "AND character_id IS NOT NULL ORDER BY id DESC",
            (self._library_uuid,),
        )
        now = time.time()
        seen = pending = 0
        task = None
        for run in runs:
            run = dict(run)
            with self._lock:
                if run["uuid"] in self._settled or run["uuid"] in self._claimed:
                    continue
            handle = handle_for_run(self._training_root, run)
            if not os.path.isdir(handle.save_root):
                continue
            try:
                samples = read_run(handle.save_root).samples
            except OSError as exc:
                logger.warning(
                    "Could not list the previews of training run %s: %s",
                    run["uuid"],
                    exc,
                )
                continue
            done = preview_likeness.read_faces(handle.work_dir, model_pack)
            todo = []
            for sample in samples:
                # ponytail: a clip has no likeness read; reading its first
                # frame would give one if motion runs need the chip.
                if sample.is_video or sample.filename in done:
                    continue
                try:
                    settled = now - os.path.getmtime(sample.path) >= self.SETTLE_SECONDS
                except OSError as exc:
                    logger.warning(
                        "Training preview %s went away while it was listed; "
                        "skipped this sweep: %s",
                        sample.path,
                        exc,
                    )
                    continue
                if settled:
                    todo.append((sample.filename, sample.path))
            seen += len(samples)
            pending += len(todo)
            ended = run["status"] in FINISHED_STATUSES and not (
                run["status"] == "completed" and not run["completed_at"]
            )
            if not todo:
                if ended and len(done) >= len(samples):
                    with self._lock:
                        self._settled.add(run["uuid"])
                continue
            if task is None:
                with self._lock:
                    self._claimed.add(run["uuid"])
                task = TrainingPreviewFacesTask(
                    engine, run["uuid"], handle.work_dir, todo[: self.BATCH_SIZE]
                )
        self._seen, self._pending = seen, pending
        return task

    def on_task_complete(self, task, error) -> None:
        run_uuid = (getattr(task, "params", None) or {}).get("run_uuid")
        with self._lock:
            self._claimed.discard(run_uuid)
            if error is not None:
                # Not retried every sweep; a restart tries it again.
                self._settled.add(run_uuid)
        if error is not None:
            logger.warning(
                "Looking for faces in the previews of training run %s failed, so "
                "its likeness waits for a restart: %s",
                run_uuid,
                error,
            )
