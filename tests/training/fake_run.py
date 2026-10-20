"""Faithful stand-in for ai-toolkit's ``run.py`` for GPU-free tests.

This reproduces, in miniature, exactly the on-disk side effects PixlStash's
``LocalAiToolkit`` adapter depends on (see
``pixlstash/data/ai-toolkit-jobs/SEAM.md`` and ai-toolkit's
``toolkit/logging_aitk.py`` for the real schema):

- Reads the job config passed as ``argv[1]`` and the ``--log`` path.
- Honors ``AITK_JOB_ID`` from the env and the per-job ``Job`` table at
  ``process[0].sqlite_db_path``: sets ``status='running'`` with step/info/speed
  as it advances, polls ``stop`` every step and exits cleanly
  (``status='stopped'``) when set, otherwise finishes ``status='completed'``.
- Writes ``<save_root>/loss_log.db`` in WAL mode with the real
  ``steps`` / ``metric_keys`` / ``metrics`` schema, one row per step, plus a
  decaying ``loss/loss`` metric.
- Writes a ``<name>_<step:09d>.safetensors`` checkpoint every ``save.save_every``
  steps, a final ``<name>.safetensors`` at the end, and one ``samples/`` file.

It runs entirely on CPU and in well under a second: the per-step delay defaults
to ~1ms and is overridable via ``FAKE_RUN_STEP_DELAY`` (seconds).

It does NOT import torch or anything heavy: ``.safetensors`` files are written
as small placeholder byte blobs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse the ai-toolkit-compatible CLI used by the adapter.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Parsed namespace with ``config`` and ``log``.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("-l", "--log", dest="log", default=None)
    parser.add_argument("-r", "--recover", action="store_true")
    parser.add_argument("-n", "--name", dest="name", default=None)
    return parser.parse_args(argv)


def _init_loss_db(loss_db_path: str) -> sqlite3.Connection:
    """Open ``loss_log.db`` in WAL mode and create the real schema.

    Mirrors ai-toolkit's ``UILogger._init_schema`` exactly.

    Args:
        loss_db_path: Destination path for the loss DB.

    Returns:
        An open connection with autocommit (isolation_level=None).
    """
    os.makedirs(os.path.dirname(os.path.abspath(loss_db_path)), exist_ok=True)
    con = sqlite3.connect(loss_db_path, timeout=30.0, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    con.execute("PRAGMA busy_timeout=30000;")
    con.execute(
        "CREATE TABLE IF NOT EXISTS steps ("
        "step INTEGER PRIMARY KEY, wall_time REAL NOT NULL);"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS metric_keys ("
        "key TEXT PRIMARY KEY, first_seen_step INTEGER, last_seen_step INTEGER);"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS metrics ("
        "step INTEGER NOT NULL, key TEXT NOT NULL, "
        "value_real REAL, value_text TEXT, "
        "PRIMARY KEY (step, key), "
        "FOREIGN KEY (step) REFERENCES steps(step) ON DELETE CASCADE);"
    )
    return con


def _log_step(con: sqlite3.Connection, step: int, loss: float) -> None:
    """Append one ``steps`` row and one ``loss/loss`` ``metrics`` row.

    Args:
        con: Open loss-DB connection.
        step: The step index being logged.
        loss: The loss value for this step.
    """
    con.execute(
        "INSERT OR REPLACE INTO steps(step, wall_time) VALUES(?, ?);",
        (step, time.time()),
    )
    con.execute(
        "INSERT OR REPLACE INTO metric_keys(key, first_seen_step, last_seen_step) "
        "VALUES('loss/loss', "
        "COALESCE((SELECT first_seen_step FROM metric_keys WHERE key='loss/loss'), ?), "
        "?);",
        (step, step),
    )
    con.execute(
        "INSERT OR REPLACE INTO metrics(step, key, value_real, value_text) "
        "VALUES(?, 'loss/loss', ?, NULL);",
        (step, loss),
    )


def _update_job(
    job_db_path: str, job_id: str, *, status: str, step: int, info: str, speed: str
) -> None:
    """Update the per-job ``Job`` row (keyed ``WHERE id=?``, like the trainer).

    Args:
        job_db_path: Path to the per-job DB.
        job_id: The job id (``Job.id``).
        status: New status.
        step: Latest step.
        info: Human status string.
        speed: Speed string.
    """
    con = sqlite3.connect(job_db_path, timeout=30.0)
    try:
        con.execute(
            "UPDATE Job SET status=?, step=?, info=?, speed_string=? WHERE id=?",
            (status, step, info, speed, job_id),
        )
        con.commit()
    finally:
        con.close()


def _set_job_status(job_db_path: str, job_id: str, status: str, info: str) -> None:
    """Set just the status + info columns of the ``Job`` row.

    Args:
        job_db_path: Path to the per-job DB.
        job_id: The job id.
        status: New status.
        info: Human status string.
    """
    con = sqlite3.connect(job_db_path, timeout=30.0)
    try:
        con.execute(
            "UPDATE Job SET status=?, info=? WHERE id=?", (status, info, job_id)
        )
        con.commit()
    finally:
        con.close()


def _stop_requested(job_db_path: str, job_id: str) -> bool:
    """Return whether ``Job.stop`` has been set to 1 (cooperative cancel).

    Args:
        job_db_path: Path to the per-job DB.
        job_id: The job id.

    Returns:
        ``True`` if a stop has been requested, else ``False``.
    """
    con = sqlite3.connect(job_db_path, timeout=30.0)
    try:
        row = con.execute("SELECT stop FROM Job WHERE id=?", (job_id,)).fetchone()
        return bool(row and row[0] == 1)
    finally:
        con.close()


def _write_safetensors(path: str) -> None:
    """Write a tiny placeholder ``.safetensors`` file (no torch needed).

    Args:
        path: Destination path.
    """
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as handle:
        # A minimal, recognisable placeholder; the adapter only globs by name.
        handle.write(b"FAKE_SAFETENSORS\x00")


def main(argv: list[str]) -> int:
    """Run the fake trainer loop.

    Args:
        argv: Argument vector excluding the program name.

    Returns:
        Process exit code (0 on clean completion or cooperative stop).
    """
    args = _parse_args(argv)

    with open(args.config, encoding="utf-8") as handle:
        config = json.load(handle)

    process = config["config"]["process"][0]
    name = config["config"]["name"]
    training_folder = process["training_folder"]
    job_db_path = process["sqlite_db_path"]
    total_steps = int(process["train"]["steps"])
    save_every = int(process["save"]["save_every"])

    save_root = os.path.join(training_folder, name)
    os.makedirs(save_root, exist_ok=True)

    job_id = os.environ.get("AITK_JOB_ID")
    step_delay = float(os.environ.get("FAKE_RUN_STEP_DELAY", "0.001"))

    # The adapter passes --log; emulate the trainer touching it so log rotation
    # and HF-token-redaction assertions have a file to inspect.
    if args.log:
        os.makedirs(os.path.dirname(os.path.abspath(args.log)), exist_ok=True)
        with open(args.log, "w", encoding="utf-8") as log_handle:
            # Log only env key *names* we received, never the HF token value,
            # matching the real trainer/adapter discipline under test.
            present_keys = sorted(
                k
                for k in ("AITK_JOB_ID", "CUDA_VISIBLE_DEVICES", "HF_TOKEN")
                if k in os.environ
            )
            log_handle.write(f"fake_run start name={name} env_keys={present_keys}\n")

    loss_con = _init_loss_db(os.path.join(save_root, "loss_log.db"))
    try:
        if job_id:
            _set_job_status(job_db_path, job_id, "running", "Starting")

        for step in range(1, total_steps + 1):
            if job_id and _stop_requested(job_db_path, job_id):
                _set_job_status(job_db_path, job_id, "stopped", "Job stopped")
                if args.log:
                    with open(args.log, "a", encoding="utf-8") as log_handle:
                        log_handle.write(f"stopped at step {step}\n")
                return 0

            # Decaying loss so the curve is monotone-ish and non-trivial.
            loss = 1.0 * math.exp(-step / max(1.0, total_steps / 3.0)) + 0.01
            _log_step(loss_con, step, loss)

            if job_id:
                _update_job(
                    job_db_path,
                    job_id,
                    status="running",
                    step=step,
                    info="Training",
                    speed="2.31 iter/sec",
                )

            if save_every > 0 and step % save_every == 0:
                ckpt = os.path.join(save_root, f"{name}_{step:09d}.safetensors")
                _write_safetensors(ckpt)

            if step_delay > 0:
                time.sleep(step_delay)

        # Final checkpoint + one sample, then mark completed.
        _write_safetensors(os.path.join(save_root, f"{name}.safetensors"))
        samples_dir = os.path.join(save_root, "samples")
        os.makedirs(samples_dir, exist_ok=True)
        sample_path = os.path.join(samples_dir, f"sample_{total_steps:09d}_0.jpg")
        with open(sample_path, "wb") as sample_handle:
            sample_handle.write(b"FAKE_SAMPLE_JPEG")

        if job_id:
            _update_job(
                job_db_path,
                job_id,
                status="completed",
                step=total_steps,
                info="Training completed",
                speed="2.31 iter/sec",
            )
    finally:
        loss_con.close()

    if args.log:
        with open(args.log, "a", encoding="utf-8") as log_handle:
            log_handle.write("fake_run completed\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
