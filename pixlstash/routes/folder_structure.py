"""The folder-structure read — v1.11 Phase 2.

Wire contract: ``docs/integration_architecture.md`` §20. The signals themselves
live in ``pixlstash.services.folder_structure_service``; this module is the
task-id-polling shell around one of them (§11's first branch: the owner
triggered it and waits for a result).

**One read at a time.** The mapping screen only ever shows one, the read is the
expensive thing in the release, and two concurrent ones would fight over the same
GPU queue for no gain. A second `POST` while one runs is a 409, not a queue.
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from pixlstash.pixl_logging import get_logger
from pixlstash.services.folder_structure_service import (
    FolderStructureRead,
    load_existing_entities,
)
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path

logger = get_logger(__name__)

#: How long one folder's sampled face batch may wait. Generous enough that the
#: first batch of a read can also pay for loading InsightFace, and short enough
#: that a wedged GPU queue is noticed rather than waited on 20,000 times.
_FACE_BATCH_TIMEOUT_S = 180.0

# Matches any non-empty string with no null bytes or newlines. Applied with
# fullmatch() after realpath so CodeQL recognises the result as a path-injection
# barrier (realpath alone does not break the taint chain in its model). Same
# barrier as pixlstash/routes/filesystem.py.
_SAFE_RESOLVED_PATH_RE = re.compile(r"[^\x00\n]+")


class FolderStructureReadRequest(BaseModel):
    path: str


class FolderStructureReadStartResponse(BaseModel):
    task_id: str


class FolderStructureReadStatusResponse(BaseModel):
    task_id: str
    status: str
    """``queued`` | ``running`` | ``completed`` | ``failed`` | ``cancelled``."""

    stage: str
    """``walking`` | ``faces`` | ``done``.

    There is no ``sidecars`` stage — that signal is counted from the walk's own
    listing. A read with no inference engine never reaches ``faces`` either: it
    goes straight from ``walking`` to ``done``, so the bar stays indeterminate
    throughout.
    """

    processed: int
    total: int
    progress: float
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None


class FolderStructureReadCancelResponse(BaseModel):
    status: str


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _resolve_readable_directory(path: str) -> str:
        """Validate and contain a caller-supplied host path.

        The blocklist runs on the **realpath**, not on the string the caller
        sent. Validating the raw path alone would let ``/home/me/link-to-etc``
        through and hand ``/etc`` — 400-odd directories, walked recursively and
        with every image-extensioned file in them decoded — to a route whose
        whole justification is that it is contained. That is what
        ``validate_reference_folder_accessible`` is for, and it is why this
        route is deliberately *stricter* than ``GET /filesystem/browse``: browse
        lists one level, this walks a subtree and reads out of it.

        **This is the root check only.** The walk re-runs the same blocklist on
        every directory it descends into, because a root-only check is a check on
        one string: ``/`` names no restricted directory and contains all of them.
        """
        if server.running_in_docker():
            raise HTTPException(
                status_code=403,
                detail="The folder-structure read is not available in Docker mode.",
            )
        if not os.path.isabs(path):
            raise HTTPException(status_code=400, detail="Path must be absolute.")

        resolved = os.path.realpath(os.path.normpath(path))
        # Re-run the blocklist on the resolved path: a symlink is exactly how a
        # restricted directory reaches a route that only checked the raw string.
        error = validate_reference_folder_path(resolved)
        if error:
            raise HTTPException(status_code=400, detail=error)

        roots = [
            os.path.realpath(r)
            for r in (server._server_config.get("filesystem_roots") or [])
            if isinstance(r, str) and r
        ]
        if roots and not any(
            resolved == root or resolved.startswith(root + os.sep) for root in roots
        ):
            raise HTTPException(
                status_code=403,
                detail="Path is not within any configured filesystem root.",
            )
        if not os.path.isdir(resolved):
            raise HTTPException(status_code=404, detail="Folder not found.")

        # The CodeQL-recognised path-injection barrier, as in filesystem.py:
        # realpath alone does not break the taint chain in CodeQL's model, and
        # this path reaches os.walk, os.listdir and Image.open.
        matched = _SAFE_RESOLVED_PATH_RE.fullmatch(resolved)
        if not matched:
            raise HTTPException(status_code=400, detail="Invalid path.")
        return matched.group(0)

    def _face_detector():
        """A ``(images) -> per-image faces`` callable, or ``None`` if no engine.

        The face signal runs on the shared GPU queue through the existing
        ``FaceDetectionTask`` rather than opening its own InsightFace session, so
        there is one model in memory rather than two.

        **It does not queue politely.** ``FaceDetectionTask.priority`` is
        ``URGENT`` — "skip ahead of everything" — so every batch of the read
        jumps the queue ahead of background work. Defensible (the owner is
        watching a progress bar) but worth knowing rather than assuming, and it
        is why the read carries a deadline: an URGENT task that cannot finish
        starves the queue it jumped. See ``backend_architecture.md`` §24.
        """
        from pixlstash.tasks.face_detection_task import FaceDetectionTask

        engine = getattr(server.vault, "_engine", None)
        task_runner = getattr(server.vault, "_task_runner", None)
        if engine is None or task_runner is None:
            return None

        def detect(images: list):
            return task_runner.submit_and_wait(
                FaceDetectionTask(engine, images), _FACE_BATCH_TIMEOUT_S
            )

        return detect

    @router.post(
        "/folder-structure/read",
        summary="Start the folder-structure read",
        description=(
            "Reads a folder tree and proposes what each level is (Project, Set, "
            "Person, Tag, or just a folder) from four deterministic local "
            "signals. Writes nothing and moves no files. Returns a task id to "
            "poll; see integration_architecture.md §20."
        ),
        response_model=FolderStructureReadStartResponse,
        tags=["folders"],
    )
    def start_folder_structure_read(
        request: Request, payload: FolderStructureReadRequest
    ):
        root = _resolve_readable_directory(payload.path)

        # Read the entity names before taking the lock: four queries have no
        # business inside the mutex that decides who owns the one read slot.
        entities = load_existing_entities(server.vault.db)

        with server.folder_structure_lock:
            current = server.folder_structure_read
            if current and current["status"] in ("queued", "running"):
                raise HTTPException(
                    status_code=409,
                    detail="A folder-structure read is already running.",
                )

            detect = _face_detector()
            if detect is None:
                logger.warning(
                    "Folder-structure read: no inference engine — the face signal "
                    "will be skipped and no folder will be proposed as a Person."
                )
            task_id = str(uuid.uuid4())
            read = FolderStructureRead(
                root,
                detect_faces=detect,
                existing_entities=entities,
                progress=lambda stage, processed, total: _on_progress(
                    task_id, stage, processed, total
                ),
            )
            server.folder_structure_read = {
                "task_id": task_id,
                "status": "queued",
                "stage": "walking",
                "processed": 0,
                "total": 0,
                "error": None,
                "result": None,
                "read": read,
                "started_epoch_s": time.time(),
            }

        threading.Thread(
            target=_run_read,
            args=(task_id, read),
            daemon=True,
            name="folder-structure-read",
        ).start()
        logger.info("Folder-structure read started: task_id=%s", task_id)
        return {"task_id": task_id}

    def _on_progress(task_id: str, stage: str, processed: int, total: int) -> None:
        state = server.folder_structure_read
        if not state or state["task_id"] != task_id:
            return
        state["stage"] = stage
        state["processed"] = processed
        state["total"] = total

    def _run_read(task_id: str, read: FolderStructureRead) -> None:
        state = server.folder_structure_read
        if state and state["task_id"] == task_id:
            state["status"] = "running"
        try:
            result = read.run()
        except BaseException as exc:  # noqa: BLE001 — the slot must never wedge
            logger.error(
                "Folder-structure read %s failed (%s): %s",
                task_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            state = server.folder_structure_read
            if state and state["task_id"] == task_id:
                state["status"] = "failed"
                state["error"] = f"{type(exc).__name__}: {exc}"
            # Anything that is not an ordinary Exception is still not this
            # thread's to swallow — but the slot is marked failed FIRST, or a
            # KeyboardInterrupt or a MemoryError leaves it "running" forever and
            # every later read is refused with 409. The deadline cannot help
            # here: it lives inside run(), which did not return.
            if not isinstance(exc, Exception):
                raise
            return

        state = server.folder_structure_read
        if not state or state["task_id"] != task_id:
            return
        state["result"] = result
        state["stage"] = "done"
        # A cancelled read keeps whatever it found: the screen can still show it.
        state["status"] = "cancelled" if read.cancelled else "completed"
        logger.info(
            "Folder-structure read %s %s: %d folders, %d pictures, %.1fs",
            task_id,
            state["status"],
            result["folder_count"],
            result["picture_count"],
            time.time() - state["started_epoch_s"],
        )

    @router.get(
        "/folder-structure/read/status",
        summary="Get folder-structure read status",
        description=(
            "Progress for a read started by POST /folder-structure/read. "
            "`result` is null until `status` is `completed` (or `cancelled`, "
            "which keeps the partial read)."
        ),
        response_model=FolderStructureReadStatusResponse,
        tags=["folders"],
    )
    def folder_structure_read_status(request: Request, task_id: str = Query(...)):
        # Snapshot under the lock: a concurrent POST replaces the whole slot, and
        # reading six fields off `server.folder_structure_read` one at a time can
        # otherwise serve an evicted read's body under an id that must now 404.
        with server.folder_structure_lock:
            state = server.folder_structure_read
            if not state or state["task_id"] != task_id:
                raise HTTPException(status_code=404, detail="Task not found")
            state = dict(state)
        # One load each, in this order: the worker writes `result` before it
        # writes `status`, so reading `status` first is what keeps §20's "result
        # is null until the read has settled" true without a lock on every poll.
        status = state["status"]
        stage = state["stage"]
        total = state["total"] or 0
        processed = state["processed"] or 0
        settled = status in ("completed", "failed", "cancelled")
        return {
            "task_id": task_id,
            "status": status,
            "stage": stage,
            "processed": processed,
            "total": total,
            "progress": (processed / total * 100.0) if total else 0.0,
            "error": state["error"],
            "result": state["result"] if settled else None,
        }

    @router.delete(
        "/folder-structure/read",
        summary="Cancel the folder-structure read",
        description=(
            "Asks a running read to stop at its next checkpoint. The partial "
            "result is kept."
        ),
        response_model=FolderStructureReadCancelResponse,
        tags=["folders"],
    )
    def cancel_folder_structure_read(request: Request, task_id: str = Query(...)):
        with server.folder_structure_lock:
            state = server.folder_structure_read
            if not state or state["task_id"] != task_id:
                raise HTTPException(status_code=404, detail="Task not found")
        if state["status"] in ("completed", "failed", "cancelled"):
            # Saying "cancelled" here would be a lie the client cannot check:
            # the read is over and its result stands. Report what it actually is.
            return {"status": state["status"]}
        state["read"].cancel()
        logger.info("Folder-structure read %s cancelled by the owner", task_id)
        # "cancelled" means the cancel was ACCEPTED, not that the read has
        # stopped: it stops at its next folder boundary, so `status` legitimately
        # stays `running` until then and a POST keeps 409-ing meanwhile. §20 says
        # so, because a client reading this as "already stopped" will race it.
        return {"status": "cancelled"}

    return router
