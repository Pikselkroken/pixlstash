"""Move model files between registered folders: start one, watch it, cancel it.

Three routes over one job, because a move is not a request-shaped operation. A
folder of 1,806 adapters is 438 GB, so the copying runs on a thread and the
client watches; the *validation* is not deferred, though. ``ModelMover.plan``
runs inside the POST and refuses the whole batch before the first byte if the
destination is unusable, an item names no row, a path escapes its folder, or the
copy would not fit — so a mistake is an immediate 4xx rather than a job that dies
on file 1,500 having already moved 1,499, which nothing can undo.

**One move at a time**, machine-wide. Two concurrent moves would race for the
same free space that both of them checked before either started, and a move is
I/O-bound on one disk regardless. A second POST while one runs is a 409.

**Cancel stops the queue and rolls nothing back.** The files already moved stay
moved. That is the ruling, and it is the only answer that does not need its own
crash-window argument for the undo.

Authorization: `LOCAL_OWNER_ONLY` on all three, declared in
``pixlstash/authz/registry.py`` and never inline. See the §16.3 note on the
tier in ``docs/backend_architecture.md``; the reasoning per route is in
``docs/authz-coverage-matrix.md``.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_mover import ModelMover, MoveOutcome, MoveRefused

logger = get_logger(__name__)

# The one in-flight move, machine-wide, plus the last finished one so a client
# that was not watching can still read the outcome. Guarded by ``_job_lock``;
# the worker thread only ever mutates the dict it was handed.
_job: Optional[dict] = None
_job_lock = threading.Lock()

STATUS_RUNNING = "running"
STATUS_FINISHED = "finished"


class MoveItem(BaseModel):
    """One registered copy to move, named by its ``model_file`` primary key."""

    model_config = ConfigDict(extra="forbid")

    folder_id: int = Field(
        description=(
            "`model_folder.id` the copy currently lives under. Comes straight "
            "from a shelf row's `locations[].folder_id`."
        )
    )
    relpath: str = Field(
        description=(
            "The copy's path relative to that folder, i.e. "
            "`locations[].relpath`. Together with `folder_id` this is the "
            "`model_file` primary key, so it names one copy and never a model "
            "that happens to have several."
        )
    )


class MoveRequest(BaseModel):
    """Body of ``POST /model-moves``."""

    model_config = ConfigDict(extra="forbid")

    destination_folder_id: int = Field(
        description=(
            "A registered folder the shelf catalogues. A `source` folder is "
            "refused: it is an ai-toolkit output root, taken from, never "
            "written into."
        )
    )
    items: list[MoveItem] = Field(
        description=(
            "The copies to move. Files already in the destination folder are "
            "skipped rather than refused, so a mixed selection dropped onto a "
            "folder does the obvious thing. Filenames are flattened to the "
            "basename; a collision is refused before anything moves, never "
            "overwritten."
        )
    )


class MoveItemResult(BaseModel):
    """What happened to one copy."""

    model_config = ConfigDict(extra="allow")

    folder_id: int = Field(description="The folder the copy started in.")
    relpath: str = Field(description="The copy's original path in that folder.")
    status: str = Field(
        description=(
            "`moved`, `copied`, `skipped`, `failed`, or `cancelled` for the "
            "queue behind a cancel. A `failed` item left its original untouched."
        )
    )
    detail: Optional[str] = Field(
        default=None, description="Why, when the status is `failed`."
    )


class MoveStatusResponse(BaseModel):
    """Body of ``GET`` / ``POST`` / ``DELETE`` on ``/model-moves``."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(
        description="`running`, `finished`, or `idle` when none has ever run."
    )
    destination_folder_id: Optional[int] = None
    total: int = Field(default=0, description="Files this move will touch.")
    done: int = Field(default=0, description="Files decided so far.")
    bytes_to_copy: int = Field(
        default=0,
        description=(
            "Total bytes that will actually be copied. Zero when every file is "
            "on the destination's own filesystem and the move is a rename."
        ),
    )
    cancel_requested: bool = Field(
        default=False,
        description=(
            "A cancel stops the queue between files. It never rolls back what "
            "has already moved."
        ),
    )
    results: list[MoveItemResult] = Field(default_factory=list)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot(job: Optional[dict]) -> MoveStatusResponse:
    if job is None:
        return MoveStatusResponse(status="idle")
    return MoveStatusResponse(
        status=job["status"],
        destination_folder_id=job["destination_folder_id"],
        total=job["total"],
        done=len(job["results"]),
        bytes_to_copy=job["bytes_to_copy"],
        cancel_requested=job["cancel"].is_set(),
        results=[MoveItemResult(**result) for result in job["results"]],
        started_at=job["started_at"],
        finished_at=job["finished_at"],
    )


def create_router(server) -> APIRouter:
    """Create the model-move router.

    Args:
        server: The Server instance, for ``hub`` (the shelf tables) and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    @router.post(
        "/model-moves",
        summary="Move model files into another registered folder",
        description=(
            "Validates the whole batch first — destination, every item, path "
            "containment and free space — and refuses it before writing a byte "
            "if anything is wrong. Then copies on a thread and returns 202. Per "
            "file the order is copy, verify by SHA-256, repoint the row and "
            "commit, and only then unlink, so an interruption leaves a "
            "duplicate and never a row naming a file that is gone. A move "
            "within one filesystem is a rename and copies nothing."
        ),
        status_code=202,
        tags=["model_shelf"],
        response_model=MoveStatusResponse,
    )
    def start_move(request: Request, payload: MoveRequest = Body(...)):
        global _job
        server.auth.ensure_secure_when_required(request)

        mover = ModelMover(server.hub)
        try:
            plan = mover.plan(
                [(item.folder_id, item.relpath) for item in payload.items],
                payload.destination_folder_id,
            )
        except MoveRefused as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        with _job_lock:
            if _job is not None and _job["status"] == STATUS_RUNNING:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "A move is already running. Two at once would race for "
                        "the free space each of them checked."
                    ),
                )
            job = {
                "status": STATUS_RUNNING,
                "destination_folder_id": plan.destination_folder_id,
                "total": len(plan.moves),
                "bytes_to_copy": plan.bytes_to_copy,
                "results": [],
                "cancel": threading.Event(),
                "started_at": _utcnow(),
                "finished_at": None,
            }
            _job = job

        def _record(outcome: MoveOutcome) -> None:
            job["results"].append(
                {
                    "folder_id": outcome.source_folder_id,
                    "relpath": outcome.source_relpath,
                    "status": outcome.status,
                    "detail": outcome.detail,
                }
            )

        def _run() -> None:
            try:
                report = mover.execute(
                    plan,
                    should_cancel=job["cancel"].is_set,
                    on_progress=_record,
                )
                # The cancelled tail is decided in one go rather than reported
                # file by file, so append whatever `on_progress` did not see.
                for outcome in report.outcomes[len(job["results"]) :]:
                    _record(outcome)
            except Exception as exc:
                logger.error(
                    "Move into folder %s failed after %d of %d file(s): %s",
                    plan.destination_folder_id,
                    len(job["results"]),
                    len(plan.moves),
                    exc,
                    exc_info=True,
                )
            finally:
                job["finished_at"] = _utcnow()
                job["status"] = STATUS_FINISHED

        threading.Thread(target=_run, daemon=True, name="model-move").start()
        return _snapshot(job)

    @router.get(
        "/model-moves",
        summary="How the current or last model move is going",
        description=(
            "The in-flight move, or the last finished one so a client that was "
            "not watching can still read the outcome. `idle` when none has run."
        ),
        tags=["model_shelf"],
        response_model=MoveStatusResponse,
    )
    def get_move(request: Request):
        server.auth.ensure_secure_when_required(request)
        with _job_lock:
            return _snapshot(_job)

    @router.delete(
        "/model-moves",
        summary="Cancel the running model move",
        description=(
            "Stops the queue between files. It does **not** roll back what has "
            "already moved: those files are where the shelf says they are. The "
            "file being copied when the cancel arrives is finished first, "
            "because abandoning it mid-copy is the partial the verify step "
            "exists to prevent."
        ),
        tags=["model_shelf"],
        response_model=MoveStatusResponse,
    )
    def cancel_move(request: Request):
        server.auth.ensure_secure_when_required(request)
        with _job_lock:
            if _job is None or _job["status"] != STATUS_RUNNING:
                raise HTTPException(status_code=409, detail="No move is running.")
            _job["cancel"].set()
            logger.info(
                "Cancel requested for the move into folder %s after %d of %d "
                "file(s). Nothing already moved is rolled back.",
                _job["destination_folder_id"],
                len(_job["results"]),
                _job["total"],
            )
            return _snapshot(_job)

    return router
