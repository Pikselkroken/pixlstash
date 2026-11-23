"""A second training plugin, for tests: proves a trainer plugs in through the seam.

:class:`FakeTrainer` implements only what
:class:`~pixlstash.training.backend.TrainerBackend` requires: the six lifecycle
methods and ``handle_for``. Every other hook is left at its default, which is
the claim being tested: a trainer that knows nothing of ai-toolkit (no ``Job``
table, no ``loss_log.db``, no memory probe, no after-training previews) runs a
job through the same routes and lands its model on the shelf.

It does not use ``fake_run.py``, which speaks ai-toolkit's protocol. Its
"trainer" is a short Python subprocess that writes the output-folder contract
(:mod:`pixlstash.utils.training_run_folder`) straight into the run's
``save_root``, then a one-word status file that :meth:`FakeTrainer.progress`
reads.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import psutil

from pixlstash.training.backend import (
    TrainerBackend,
    TrainingHandle,
    TrainingProgress,
    TrainingSpec,
)

#: The recipe :func:`write_recipe` writes, and the template it renders over.
RECIPE_ID = "fake-recipe"
TEMPLATE_NAME = "fake-template"

# The trainer: writes config.yaml, a step-2 checkpoint and the bare final, unless
# a stop file appears first; the status file says how it ended.
_TRAINER = r"""
import json, os, struct, sys, time
save_root, stem, status_path, stop_path, seconds = sys.argv[1:6]
def write_status(word):
    with open(status_path + ".tmp", "w") as fh:
        fh.write(word)
    os.replace(status_path + ".tmp", status_path)
def checkpoint(path, step):
    header = json.dumps(
        {"__metadata__": {"training_info": json.dumps({"step": step})}}
    ).encode()
    with open(path, "wb") as fh:
        fh.write(struct.pack("<Q", len(header)) + header)
write_status("running")
os.makedirs(save_root, exist_ok=True)
with open(os.path.join(save_root, "config.yaml"), "w") as fh:
    fh.write("job: fake\n")
deadline = time.monotonic() + float(seconds)
while time.monotonic() < deadline:
    if os.path.exists(stop_path):
        write_status("stopped")
        sys.exit(0)
    time.sleep(0.05)
checkpoint(os.path.join(save_root, stem + "_000000002.safetensors"), 2)
checkpoint(os.path.join(save_root, stem + ".safetensors"), 2)
write_status("completed")
"""


class FakeTrainer(TrainerBackend):
    """A trainer that is not ai-toolkit, implementing only the required members.

    Attributes:
        run_seconds: How long a run trains before it saves; a test raises it
            for a run it means to cancel.
    """

    name = "fake-trainer"
    display_name = "Fake trainer"
    run_seconds = 0.3

    def __init__(self) -> None:
        self._processes: dict[int, subprocess.Popen] = {}

    @staticmethod
    def handle_for(
        work_dir: str,
        job_id: str,
        job_name: str,
        pid: int | None = None,
        training_folder: str | None = None,
    ) -> TrainingHandle:
        work_dir = os.path.abspath(work_dir)
        training_folder = os.path.abspath(
            training_folder or os.path.join(work_dir, "output")
        )
        save_root = os.path.join(training_folder, f"fake_{job_id[:8]}")
        return TrainingHandle(
            job_id=job_id,
            work_dir=work_dir,
            training_folder=training_folder,
            save_root=save_root,
            sqlite_db_path=os.path.join(work_dir, "status"),
            loss_db_path=os.path.join(work_dir, "no-loss-log"),
            config_path=os.path.join(work_dir, "config.json"),
            log_path=os.path.join(work_dir, "log.txt"),
            pid=pid,
        )

    def prepare(self, spec: TrainingSpec) -> TrainingHandle:
        handle = self.handle_for(
            spec.work_dir, spec.job_id, spec.job_name, None, spec.training_folder
        )
        os.makedirs(handle.work_dir, exist_ok=True)
        shutil.copyfile(spec.template_path, handle.config_path)
        _write(handle.sqlite_db_path, "queued")
        return handle

    def submit(self, handle: TrainingHandle, spec: TrainingSpec) -> None:
        stem = os.path.basename(handle.save_root)
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _TRAINER,
                handle.save_root,
                stem,
                handle.sqlite_db_path,
                _stop_path(handle),
                str(self.run_seconds),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes[process.pid] = process
        handle.pid = process.pid
        _write(handle.sqlite_db_path, "running")

    def progress(self, handle: TrainingHandle) -> TrainingProgress:
        status = _read(handle.sqlite_db_path) or "queued"
        stem = os.path.basename(handle.save_root)
        stepped = os.path.join(handle.save_root, f"{stem}_000000002.safetensors")
        final = os.path.join(handle.save_root, f"{stem}.safetensors")
        done = status == "completed" and os.path.isfile(final)
        return TrainingProgress(
            status=status,
            info=status,
            step=2 if done else 0,
            total_steps=2,
            speed="",
            checkpoints=[stepped] if os.path.isfile(stepped) else [],
            final_checkpoint=final if done else None,
        )

    def cancel(self, handle: TrainingHandle) -> None:
        _write(_stop_path(handle), "stop")
        if handle.pid is None:
            return
        process = self._processes.get(handle.pid)
        if process is not None:
            process.wait(timeout=10)
            return
        try:
            psutil.Process(handle.pid).wait(timeout=10)
        except psutil.NoSuchProcess:
            return  # already gone

    def reconcile(self, handle: TrainingHandle) -> TrainingProgress:
        progress = self.progress(handle)
        if progress.status in ("queued", "running"):
            progress.status = "error"
            progress.info = "Interrupted"
        return progress

    def is_alive(self, handle: TrainingHandle) -> bool:
        if handle.pid is None:
            return False
        process = self._processes.get(handle.pid)
        if process is not None:
            return process.poll() is None
        try:
            return psutil.Process(handle.pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False


def _stop_path(handle: TrainingHandle) -> str:
    return os.path.join(handle.work_dir, "stop")


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return None


def write_recipe(presets_dir: str, template_dir: str, shipped: dict) -> None:
    """Write the fake trainer's recipe and template into the two folders.

    The recipe is a copy of *shipped* (a shipped ai-toolkit recipe, so every
    schema field holds) renamed, pointed at the fake template, and naming the
    fake trainer. Its seed profile is what a run resolves to: the fake cannot
    probe memory.
    """
    os.makedirs(presets_dir, exist_ok=True)
    os.makedirs(template_dir, exist_ok=True)
    recipe = {
        key: value
        for key, value in json.loads(json.dumps(shipped)).items()
        if key != "trainer"
    }
    recipe.update(
        id=RECIPE_ID,
        label="Fake recipe",
        template=TEMPLATE_NAME,
        trainer=FakeTrainer.name,
    )
    with open(os.path.join(presets_dir, f"{RECIPE_ID}.json"), "w") as fh:
        json.dump(recipe, fh)
    with open(os.path.join(template_dir, f"{TEMPLATE_NAME}.json"), "w") as fh:
        json.dump({"config": {"process": [{"train": {"steps": 2}}]}}, fh)


__all__ = ["FakeTrainer", "RECIPE_ID", "write_recipe"]
