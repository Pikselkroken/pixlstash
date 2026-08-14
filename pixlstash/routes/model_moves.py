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

**Relocating a folder PixlStash owns lives here too**, at
``POST /model-folders/{folder_id}/relocate``, despite the path — because a
relocation *is* a move, of every file one folder holds, and it runs the same
plan/execute machinery and the same single job slot. Doing it any other way
would mean a second implementation of the ordering. Two folders qualify and
``managed_model_store.relocatable_identity`` is the one place that says which:
the managed store, and the folder PixlStash downloads its own engines into
(#905, closing #112).

The relocation's own trick is how it keeps "exactly one ``managed`` folder"
true throughout. The new location is registered as an ordinary ``user`` folder
first; every file is moved into it with its ``model_file`` row repointed
individually, so the per-file invariant is untouched; and only when every file
has landed does **one** transaction promote the new row and drop the old one. A
crash at any point leaves exactly one managed row (the old, partly emptied
store) plus a ``user`` folder holding what already moved, with every row naming
a file that exists. Re-running the relocation resumes it.

The download folder adds two steps to that ending, both after the last file has
landed and before the hub is told: its **companion** files are carried across
(they are declared but have no ``model_file`` row, and an engine without its
label set is a broken engine), and the new location is **recorded**, so every
downloader follows the folder instead of re-fetching what was just moved.

Authorization: `LOCAL_OWNER_ONLY` on all three, declared in
``pixlstash/authz/registry.py`` and never inline. See the §16.3 note on the
tier in ``docs/backend_architecture.md``; the reasoning per route is in
``docs/authz-coverage-matrix.md``.
"""

from __future__ import annotations

import os
import shutil
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_models import (
    is_builtin_model_dir,
    set_builtin_model_dir,
)
from pixlstash.services.managed_model_store import relocatable_identity
from pixlstash.services.model_mover import (
    SHELF_IO_LOCK,
    ModelMover,
    MoveOutcome,
    MoveRefused,
)
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path

logger = get_logger(__name__)

# The one in-flight move, machine-wide, plus the last finished one so a client
# that was not watching can still read the outcome. Guarded by ``_job_lock`` —
# the worker thread's writes included, through ``_record_result`` /
# ``_finish_job``, because ``_snapshot`` reads the dict in several steps and a
# write landing between them is a torn snapshot.
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
            "folder does the obvious thing; they come back in `results` with "
            "status `skipped`, so every item you send is accounted for. "
            "Filenames are flattened to the basename; a collision is refused "
            "before anything moves, never overwritten."
        )
    )


class RelocateRequest(BaseModel):
    """Body of ``POST /model-folders/{folder_id}/relocate``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description=(
            "Absolute host path to move the store to. Created if it does not "
            "exist. Symlinks are resolved before the path is checked against "
            "the system-directory blocklist and before it is recorded, so the "
            "store is registered at the location it really lands in — a link "
            "into `/usr` is refused rather than followed. Owner-chosen and "
            "therefore trusted, exactly as a reference folder is; the blocklist "
            "is there to catch the accident, not an attacker."
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
    total: int = Field(
        default=0,
        description=(
            "Items this move will decide — every one you sent, including the "
            "ones already in the destination folder."
        ),
    )
    done: int = Field(default=0, description="Items decided so far.")
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


def _record_result(job: dict, outcome: MoveOutcome) -> None:
    """Append one decided file to the job, under the lock the readers hold.

    The worker thread is the only writer and ``GET`` / ``DELETE`` are the only
    readers, but ``_snapshot`` reads ``job["results"]`` *twice* — once for
    ``done`` and once for the list itself — so an append landing between those
    two reads hands the client a snapshot whose ``done`` does not match its
    ``results``. Sharing ``_job_lock`` is what makes the module docstring's
    locking claim true rather than aspirational.
    """
    with _job_lock:
        job["results"].append(
            {
                "folder_id": outcome.source_folder_id,
                "relpath": outcome.source_relpath,
                "status": outcome.status,
                "detail": outcome.detail,
            }
        )


def _done_count(job: dict) -> int:
    """How many files this job has decided. Same lock as every other reader."""
    with _job_lock:
        return len(job["results"])


def _finish_job(job: dict) -> None:
    """Mark the job finished, under the lock the readers hold.

    ``status`` and ``finished_at`` are read by ``_snapshot`` in the same pass as
    ``results``; setting them off-lock is the same torn read as the append.
    """
    with _job_lock:
        job["finished_at"] = _utcnow()
        job["status"] = STATUS_FINISHED


def _register_or_reuse(hub, path: str) -> int:
    """Register the relocation target as an ordinary ``user`` folder.

    Ordinary on purpose: two ``managed`` rows must never exist, not even for the
    minutes a relocation takes, because the managed row is what "the default
    destination" resolves to. It is promoted in one transaction at the end.

    Reuses a row already at that path — a retry of an interrupted relocation
    lands here, and ``model_folder.path`` is UNIQUE.
    """
    existing = hub.fetchone("SELECT id FROM model_folder WHERE path = ?", (path,))
    if existing is not None:
        return int(existing["id"])
    with hub.transaction() as conn:
        return int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', ?)",
                (path, _utcnow()),
            ).lastrowid
        )


def _finish_relocation(
    hub, old_folder_id: int, new_folder_id: int, identity: tuple[str, str, str]
) -> None:
    """Promote the new folder and retire the old one, in one transaction.

    Ordered so that at no instant are there two rows of the relocating folder's
    kind or none: the old row stops being it and the new one starts being it
    inside a single commit. That matters for the managed store, of which exactly
    one must exist, and costs nothing for the built-in download folder.

    The old folder's ``missing`` and ``unreachable`` rows are carried across
    rather than dropped. They are tombstones — a file the shelf once saw and can
    re-link by content — and the store moving is not news about whether those
    files came back.

    Args:
        identity: The ``(kind, owner, movable)`` the folder keeps at its new
            path, from :func:`relocatable_identity`.
    """
    kind, owner, movable = identity
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model_file SET model_folder_id = ? WHERE model_folder_id = ?",
            (new_folder_id, old_folder_id),
        )
        conn.execute(
            "UPDATE model_folder SET kind = ?, owner = ?, movable = ? WHERE id = ?",
            (kind, owner, movable, new_folder_id),
        )
        conn.execute("DELETE FROM model_folder WHERE id = ?", (old_folder_id,))


def _move_leftovers(source: str, destination: str) -> None:
    """Carry the files no ``model_file`` row names across with the folder.

    Only the built-in download folder needs this, and it needs it badly. The
    mover moves *catalogued* copies, and the declaration gives a row to the
    engine file alone: the tagger's label set, its revision sidecar and WD14's
    ``selected_tags.csv`` are declared as **companions** and have no row. Leaving
    them behind would move a tagger to a folder where it does not work, and the
    engine would then download the whole thing again — the exact failure #905 is
    about.

    The managed store deliberately does NOT do this: what the owner left in it is
    theirs and stays put. Here the folder is ours end to end, so everything in it
    is ours to move, including whatever a previous build downloaded.

    Failures are logged per file and never raised: every catalogued copy has
    already moved and been verified by the time this runs, so a companion that
    could not follow is worth a loud line and a re-download, not an exception
    thrown from a finished job.
    """
    for root, _dirs, files in os.walk(source):
        for name in files:
            origin = os.path.join(root, name)
            target = os.path.join(destination, os.path.relpath(origin, source))
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.move(origin, target)
            except OSError as exc:
                logger.error(
                    "Could not move %s to %s while relocating PixlStash's "
                    "download folder: %s. The file stays where it is and will "
                    "be downloaded again at the new location if it is needed.",
                    origin,
                    target,
                    exc,
                )


def _remove_if_empty(path: str) -> None:
    """Tidy the vacated directory, and never let tidying fail a relocation.

    Bottom-up, because a relocation preserves subdirectories: the files have
    moved out of ``runA/`` and ``runB/`` but the empty directories remain, and
    ``os.rmdir`` on the root would refuse while they do. Only *empty*
    directories are removed, so anything the owner left behind keeps the store
    directory alive and is never deleted.
    """
    for root, dirs, _files in os.walk(path, topdown=False):
        for name in dirs:
            child = os.path.join(root, name)
            try:
                os.rmdir(child)
            except OSError as exc:
                logger.debug(
                    "Left the vacated subdirectory %s in place: %s", child, exc
                )
    try:
        os.rmdir(path)
    except OSError as exc:
        logger.info(
            "Left %s in place after the managed store moved out of it: %s. "
            "Anything still in it is not the shelf's to remove.",
            path,
            exc,
        )


def create_router(server) -> APIRouter:
    """Create the model-move router.

    Args:
        server: The Server instance, for ``hub`` (the shelf tables) and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    def _launch(mover: ModelMover, plan, on_finished=None) -> dict:
        """Put one planned batch on the single move thread and return its job.

        Shared by the plain move and by a relocation, because a relocation *is* a
        move — of every file one folder holds. One slot machine-wide, and it is
        ``model_mover.SHELF_IO_LOCK``, the *same* slot an ai-toolkit import
        takes: two file operations at once race for the free space each of them
        checked and for the destination filenames each of them found free. The
        loser is a 409 and never queues — see the lock's own note.

        Args:
            mover: The mover bound to this server's hub.
            plan: A validated :class:`MovePlan`.
            on_finished: Called with the :class:`MoveReport` when every file has
                been decided, on the worker thread, only when the batch ran to
                completion. A relocation uses it to flip the folder rows.
        """
        global _job
        if not SHELF_IO_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A move or an import is already running. Two at once would "
                    "race for the free space and the filenames each of them "
                    "checked before starting."
                ),
            )
        with _job_lock:
            job = {
                "status": STATUS_RUNNING,
                "destination_folder_id": plan.destination_folder_id,
                "total": plan.total,
                "bytes_to_copy": plan.bytes_to_copy,
                "results": [],
                "cancel": threading.Event(),
                "started_at": _utcnow(),
                "finished_at": None,
            }
            _job = job

        def _record(outcome: MoveOutcome) -> None:
            _record_result(job, outcome)

        def _run() -> None:
            try:
                report = mover.execute(
                    plan,
                    should_cancel=job["cancel"].is_set,
                    on_progress=_record,
                )
                # The cancelled tail is decided in one go rather than reported
                # file by file, so append whatever `on_progress` did not see.
                for outcome in report.outcomes[_done_count(job) :]:
                    _record(outcome)
                if on_finished is not None:
                    on_finished(report)
            except Exception as exc:
                logger.error(
                    "Move into folder %s failed after %d of %d file(s): %s",
                    plan.destination_folder_id,
                    _done_count(job),
                    len(plan.moves),
                    exc,
                    exc_info=True,
                )
            finally:
                _finish_job(job)
                # Released last, so a POST that wins the lock never observes a
                # job still marked running.
                SHELF_IO_LOCK.release()

        try:
            threading.Thread(target=_run, daemon=True, name="model-move").start()
        except BaseException:
            # The worker's ``finally`` is the only other release, so a thread
            # that never started would strand the lock and refuse every later
            # move and import for the life of the process.
            SHELF_IO_LOCK.release()
            _finish_job(job)
            logger.error(
                "Could not start the move worker for folder %s; the job slot has "
                "been released.",
                plan.destination_folder_id,
                exc_info=True,
            )
            raise
        return job

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
        server.auth.ensure_secure_when_required(request)

        mover = ModelMover(server.hub)
        try:
            plan = mover.plan(
                [(item.folder_id, item.relpath) for item in payload.items],
                payload.destination_folder_id,
            )
        except MoveRefused as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        return _snapshot(_launch(mover, plan))

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

    @router.post(
        "/model-folders/{folder_id}/relocate",
        summary="Move a folder PixlStash owns to another location",
        description=(
            "Moves every file the folder holds to a new host path and points the "
            "folder at it. This is a model move like any other — copy, verify by "
            "SHA-256, repoint the row and commit, then unlink, per file — so an "
            "interruption leaves duplicates rather than rows naming files that "
            "are gone, and a move within one filesystem is a rename. "
            "Two folders can be relocated: the managed store, and the folder "
            "PixlStash downloads its own engines into, whose new location is "
            "recorded so every downloader follows it. A folder you registered "
            "is one you moved yourself; register it again at its new path."
        ),
        status_code=202,
        tags=["model_shelf"],
        response_model=MoveStatusResponse,
    )
    def relocate_model_folder(
        folder_id: int, request: Request, payload: RelocateRequest = Body(...)
    ):
        server.auth.ensure_secure_when_required(request)
        folder = server.hub.fetchone(
            "SELECT id, path, kind FROM model_folder WHERE id = ?", (folder_id,)
        )
        if folder is None:
            raise HTTPException(status_code=404, detail="Model folder not found.")
        identity = relocatable_identity(folder)
        if identity is None:
            # 409 for the same reason the managed store's DELETE is 409: the
            # caller is authorized and the request is well formed, and what
            # refuses it is what the target row is.
            raise HTTPException(
                status_code=409,
                detail=(
                    "This folder cannot be relocated. Only the managed store "
                    "and PixlStash's own download folder can be; a folder you "
                    "registered is one you moved yourself, so register it again "
                    "at its new path."
                ),
            )
        # Read before anything moves: `relocatable_identity` recognises the
        # download folder by comparing it with `builtin_model_dir()`, and this
        # route is about to change what that returns.
        is_download_folder = is_builtin_model_dir(folder["path"])

        # **Canonicalized before the blocklist runs, and the canonical form is
        # what gets registered.** Two reviews disagreed about this. The security
        # sign-off filed the lexical check under "explicitly not a finding" on
        # the owner-trust ruling — an owner who may name any destination
        # directly gains nothing by naming one through a symlink — and that is
        # correct *as a statement about boundaries*. It is not what this check
        # is. There is no non-owner principal to keep out, so the blocklist is
        # not a boundary at all: it is the guard that stops the owner relocating
        # their model store onto ``/usr`` by accident, and the accident it has
        # to catch is precisely the one the owner cannot see by reading the path
        # they typed. ``/mnt/models`` may be a symlink to ``/usr/share``; the
        # owner named the former and this route would create directories in,
        # move every file of the store into, and ``rmdir`` around the latter. A
        # lexical check walks straight past that, which makes it false
        # assurance, and false assurance is worse than no check.
        #
        # It also matches what the reference folders this route is modelled on
        # already do: ``validate_reference_folder_accessible`` realpaths before
        # validating. The precedent the sign-off cited points this way.
        #
        # ``payload.path`` is validated first because ``realpath`` makes a
        # relative path absolute against the server's cwd, which would turn the
        # "must be absolute" refusal into an accidental acceptance.
        destination_path = os.path.realpath(payload.path)
        error = validate_reference_folder_path(
            payload.path
        ) or validate_reference_folder_path(destination_path)
        if error:
            raise HTTPException(status_code=400, detail=error)
        if os.path.realpath(folder["path"]) == destination_path:
            raise HTTPException(status_code=400, detail="The store is already there.")
        try:
            os.makedirs(destination_path, exist_ok=True)
        except OSError as exc:
            logger.error(
                "Could not create %s for the managed store relocation: %s",
                destination_path,
                exc,
            )
            raise HTTPException(
                status_code=409, detail=f"Could not create {destination_path}: {exc}"
            ) from exc

        destination_id = _register_or_reuse(server.hub, destination_path)
        relpaths = [
            row["relpath"]
            for row in server.hub.fetchall(
                "SELECT relpath FROM model_file WHERE model_folder_id = ? "
                "AND state = ? ORDER BY relpath",
                (folder_id, "present"),
            )
        ]

        mover = ModelMover(server.hub)
        try:
            # ``flatten=False``: the store is ``movable='root_only'`` — it moves
            # as a unit — so its tree has to arrive as a tree. Flattening would
            # make ``runA/model.safetensors`` and ``runB/model.safetensors``
            # collide, refusing the relocation permanently with advice ("move
            # them separately") naming a verb the shelf does not have, and with
            # only one such file it would silently drop the subdirectory.
            # ``relocating=True``: the store is ``root_only``, which refuses a
            # per-item move out of it, and a relocation is the one case where
            # that is not what is happening — the folder is going with the
            # files. The route above has already refused every folder whose
            # ``kind`` is not ``managed``, so this cannot reach the engines,
            # the InsightFace packs or the HuggingFace cache.
            plan = mover.plan(
                [(folder_id, relpath) for relpath in relpaths],
                destination_id,
                flatten=False,
                relocating=True,
            )
        except MoveRefused as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        def _promote(report) -> None:
            failed = [o for o in report.outcomes if o.status != "moved"]
            if failed or report.cancelled:
                logger.warning(
                    "Relocation of %s to %s stopped with %d file(s) not moved. "
                    "The folder stays where it is and the moved files are "
                    "catalogued under the new folder; re-run to finish.",
                    folder["path"],
                    destination_path,
                    len(failed),
                )
                return
            if is_download_folder:
                # Companions first, then the pointer, then the hub. Each step is
                # only correct once the one before it has happened: an engine
                # without its label set is broken, and a pointer written before
                # the files arrived would send the next download to an empty
                # folder.
                _move_leftovers(folder["path"], destination_path)
                try:
                    set_builtin_model_dir(destination_path)
                except OSError as exc:
                    # The files ARE at the new path, so the hub is still told the
                    # truth. What is lost is the memory of it: the next start
                    # resolves the default location again, declares it, and
                    # downloads there. Loud, because the fix is to write the
                    # pointer by hand or re-run the relocation.
                    logger.error(
                        "Moved PixlStash's downloads to %s but could not record "
                        "the new location: %s. The shelf will show them there, "
                        "but the next start will resolve the default folder "
                        "again and download into it. What failed is the write "
                        "of the pointer file named in that error — it lives in "
                        "the platform user data directory, NOT under %s — so "
                        "make that file writable and re-run the relocation.",
                        destination_path,
                        exc,
                        destination_path,
                    )
            _finish_relocation(server.hub, folder_id, destination_id, identity)
            _remove_if_empty(folder["path"])
            logger.info(
                "Model folder relocated from %s to %s (%d file(s)).",
                folder["path"],
                destination_path,
                len(plan.moves),
            )

        return _snapshot(_launch(mover, plan, on_finished=_promote))

    return router
