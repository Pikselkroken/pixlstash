"""The folder-structure read - v1.11 Phase 2.

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
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from pixlstash.db_models.folder_mapping_commit import (
    STATE_ABANDONED,
    STATE_DEFERRED,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import folder_structure_commit_service as commit_service
from pixlstash.services.folder_structure_service import (
    FolderStructureRead,
    load_existing_entities,
)
from pixlstash.utils.path_utils import resolve_path_within
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
    match_existing: bool = True
    """``False`` skips the ``name_match`` signal. The "Add a library" flow reads
    the chosen folder BEFORE the library it will become exists, and matching
    against the *active* library's entities there would propose the old
    library's People and Sets and hand out their ids as ``match``."""


class FolderStructureReadStartResponse(BaseModel):
    task_id: str


class FolderStructureReadStatusResponse(BaseModel):
    task_id: str
    status: str
    """``queued`` | ``running`` | ``completed`` | ``failed`` | ``cancelled``."""

    stage: str
    """``walking`` | ``faces`` | ``done``.

    There is no ``sidecars`` stage - that signal is counted from the walk's own
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


class FolderStructureAssignmentPayload(BaseModel):
    """One accepted folder from the mapping screen. See §22 for the shape."""

    relative_path: str
    kind: str
    match_id: Optional[int] = None


class FolderStructureCommitRequest(BaseModel):
    task_id: Optional[str] = None
    """The read to commit, when this server is the one that performed it."""

    read_result: Optional[dict[str, Any]] = None
    """A read's own result, for a caller that already holds one.

    **A read lives in one server process's memory, and processes end.** The
    desktop's first run reads the library folder while the GPU runtime
    downloads and then restarts the backend onto that runtime, so by the time
    the owner answers the mapping questions the task that produced the answer
    is gone and ``task_id`` can only be "Task not found". The result is the
    thing that matters, so a caller may hand it back instead.

    Exactly one of ``task_id`` and ``read_result`` is required. A supplied
    result reserves nothing (there is no server-side read to mark committed),
    so it carries no protection against being committed twice - the caller owns
    that, exactly as it owns the result.
    """

    assignments: list[FolderStructureAssignmentPayload] = []
    label: Optional[str] = None
    mode: Literal["reference", "local_import"] = "reference"
    """``reference`` (default): register the scanned root as an ordinary
    reference folder, indexed in place - right for a folder external to the
    library's own storage.

    ``local_import``: the scanned root IS the active library's own
    `image_root` (or a folder inside it) - the "Add a library" flow's
    "pictures" verdict, where the folder a fresh vault was just created in
    already held loose files. Those pictures become ordinary MANAGED pictures
    (relative `file_path`) instead of reference-folder ones. The commit fails
    (`status: "failed"`, polled via `GET .../commit/status`) if the root is
    not actually inside `image_root`. See integration_architecture.md §22.
    """


class FolderStructureCommitStartResponse(BaseModel):
    task_id: str


class FolderStructureCommitStopResponse(BaseModel):
    status: str
    """``abandoned`` or ``deferred`` - the stop was accepted. As with the
    read's cancel, accepted is not the same as stopped: the commit unwinds at
    its next chunk boundary, so `status` legitimately still reports `running`
    for a moment afterwards."""


class FolderStructureCommitStatusResponse(BaseModel):
    task_id: str
    status: str
    """``queued`` | ``running`` | ``completed`` | ``failed`` | ``abandoned`` |
    ``deferred``. The last two are the owner's own two ways of stopping a
    running commit (DELETE below): `abandoned` gives up on it, `deferred` is
    "organise later" - indexing stands, the mapping was not applied. Neither
    unwinds what is already indexed, and in reference mode the folder's scan
    runs to completion on its own regardless of what this screen does next."""

    stage: str
    """``registering`` | ``indexing`` | ``assigning`` | ``done``."""

    processed: int
    total: int
    progress: float
    error: Optional[str] = None
    result: Optional[dict[str, Any]] = None


def _within(root: str, resolved: str) -> bool:
    """Whether *resolved* is contained in *root*, strictly.

    ``resolve_path_within`` rather than a ``startswith`` on two realpaths: it is
    the shared helper, it realpaths both sides, and it compares with
    ``os.path.commonpath`` instead of a string prefix. #1024 moved the codebase
    onto it for exactly this class of check; a second hand-rolled containment
    here would be the thing that review is trying to stop.
    """
    try:
        resolve_path_within(root, resolved)
    except ValueError:
        return False
    return True


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _resolve_readable_directory(path: str) -> str:
        """Validate and contain a caller-supplied host path.

        The blocklist runs on the **realpath**, not on the string the caller
        sent. Validating the raw path alone would let ``/home/me/link-to-etc``
        through and hand ``/etc`` - 400-odd directories, walked recursively and
        with every image-extensioned file in them decoded - to a route whose
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
            r
            for r in (server._server_config.get("filesystem_roots") or [])
            if isinstance(r, str) and r
        ]
        if roots and not any(_within(root, resolved) for root in roots):
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
        ``URGENT`` - "skip ahead of everything" - so every batch of the read
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
        if payload.match_existing:
            entities = load_existing_entities(server.vault.db)
        else:
            entities = []
            logger.debug(
                "Folder-structure read of %s: name matching is off, the active "
                "library's entities are not consulted",
                root,
            )

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
                    "Folder-structure read: no inference engine - the face signal "
                    "will be skipped and no folder will be proposed as a Person."
                )
            task_id = str(uuid.uuid4())
            read = FolderStructureRead(
                root,
                exclude=commit_service.library_own_folders(server.vault.image_root),
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
        except BaseException as exc:  # noqa: BLE001 - the slot must never wedge
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
            # thread's to swallow - but the slot is marked failed FIRST, or a
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

    def _commit_progress(task_id: str, stage: str, processed: int, total: int) -> None:
        # Locked, unlike the read's own `_on_progress`/`_run_read` above: those
        # rely on a deliberate write ORDER (`result` before `status`) so a
        # torn read can only ever show "not ready yet", never a wrong ready
        # value. A commit writes real database rows behind these fields, so
        # this takes the small, cheap step further and makes every multi-field
        # update atomic with respect to the status endpoint's snapshot,
        # instead of relying on getting every write order right by hand.
        with server.folder_structure_commit_lock:
            state = server.folder_structure_commit
            if not state or state["task_id"] != task_id:
                return
            state["stage"] = stage
            state["processed"] = processed
            state["total"] = total

    def _stop_requested(task_id: str):
        """The owner's stop, or None. Read under the lock the writers use."""
        with server.folder_structure_commit_lock:
            state = server.folder_structure_commit
            if not state or state["task_id"] != task_id:
                return None
            return state.get("stop")

    def _run_commit(
        task_id: str,
        root_path: str,
        expected_pictures: int,
        assignments: list,
        label: Optional[str],
        mode: str,
    ) -> None:
        """Hold a library read lease for the whole commit, then run it.

        Without the lease ``LibraryGenerationCoordinator.begin_switch`` sees
        zero readers and lets a switch through while this thread is still
        working - and every step of it re-reads ``server.vault``. A commit
        against library A that survived a switch would create A's projects,
        people, sets and tags **inside B**, link B's rows to them, and write A's
        durable record into B, so B would resume A's commit at the next
        start-up. The window is ``INDEX_TIMEOUT_S``: thirty minutes.

        Holding the lease makes a switch wait and then fail rather than
        proceed. That is the intended answer - the owner is told the library is
        busy, and "Organise later" stops the commit if they would rather switch
        than wait.
        """
        lease = server.library_coordinator.acquire_read()
        if lease is None:
            logger.error(
                "Folder-structure commit %s cannot start: the library is "
                "switching or unavailable. The pending record is kept, so the "
                "next start-up tries again.",
                task_id,
            )
            with server.folder_structure_commit_lock:
                state = server.folder_structure_commit
                if state and state["task_id"] == task_id:
                    state["error"] = "Library is switching or unavailable."
                    state["status"] = "failed"
            return
        try:
            _run_commit_holding_the_library(
                task_id, root_path, expected_pictures, assignments, label, mode
            )
        finally:
            server.library_coordinator.release_read(lease)

    def _run_commit_holding_the_library(
        task_id: str,
        root_path: str,
        expected_pictures: int,
        assignments: list,
        label: Optional[str],
        mode: str,
    ) -> None:
        with server.folder_structure_commit_lock:
            state = server.folder_structure_commit
            if state and state["task_id"] == task_id:
                state["status"] = "running"
        should_stop = lambda: _stop_requested(task_id)  # noqa: E731
        try:
            if mode == "local_import":
                # No reference folder to register - the "registering" stage
                # is simply skipped, straight into "indexing".
                commit_service.validate_local_import_root(server, root_path)
                commit_service.record_commit_stage(server, task_id, "indexing")
                picture_ids = commit_service.local_import_pictures(
                    server,
                    root_path,
                    expected_pictures=expected_pictures,
                    on_progress=lambda processed, total: _commit_progress(
                        task_id, "indexing", processed, total
                    ),
                    should_stop=should_stop,
                )
                _commit_progress(
                    task_id, "assigning", expected_pictures, expected_pictures
                )
                # Last look before the one irreversible step. "Organise later"
                # pressed during indexing means exactly this: keep every
                # picture that was indexed, apply none of the mapping.
                stopped = should_stop()
                if stopped:
                    raise commit_service.CommitStopped(stopped)
                commit_service.record_commit_stage(server, task_id, "assigning")
                result = commit_service.apply_local_mapping(
                    server, picture_ids, assignments, root_path, task_id=task_id
                )
            else:
                rf = commit_service.register_reference_folder(
                    server, root_path, label=label
                )
                _commit_progress(task_id, "indexing", 0, expected_pictures)
                commit_service.record_commit_stage(server, task_id, "indexing")
                commit_service.wait_for_first_scan(
                    server,
                    rf.id,
                    expected_pictures=expected_pictures,
                    on_progress=lambda processed, total: _commit_progress(
                        task_id, "indexing", processed, total
                    ),
                    should_stop=should_stop,
                )
                _commit_progress(
                    task_id, "assigning", expected_pictures, expected_pictures
                )
                stopped = should_stop()
                if stopped:
                    raise commit_service.CommitStopped(stopped)
                commit_service.record_commit_stage(server, task_id, "assigning")
                result = commit_service.apply_mapping(
                    server, rf.id, assignments, root_path, task_id=task_id
                )
        except commit_service.CommitStopped as exc:
            # Not a failure. The durable record is settled here rather than in
            # a transaction, because there is no transaction left to settle it
            # in: the assigning step is exactly what did not run.
            commit_service.settle_pending_commit(server, task_id, exc.state)
            logger.info(
                "Folder-structure commit %s stopped by the owner (%s); "
                "everything already indexed stays",
                task_id,
                exc.state,
            )
            with server.folder_structure_commit_lock:
                state = server.folder_structure_commit
                if state and state["task_id"] == task_id:
                    state["stage"] = "done"
                    state["status"] = exc.state
            return
        except commit_service.CommitError as exc:
            # The durable record is deliberately LEFT PENDING. A failure here is
            # usually transient - a scan that outran its timeout, a folder that
            # was not mounted yet - and losing the intent is the thing this
            # record exists to prevent, so the next start-up tries once more.
            # A commit that keeps failing is stopped by the owner, not by us
            # guessing which failures are permanent.
            logger.error("Folder-structure commit %s failed: %s", task_id, exc)
            with server.folder_structure_commit_lock:
                state = server.folder_structure_commit
                if state and state["task_id"] == task_id:
                    state["error"] = str(exc)
                    state["status"] = "failed"
            return
        except BaseException as exc:  # noqa: BLE001 - the slot must never wedge
            logger.error(
                "Folder-structure commit %s failed (%s): %s",
                task_id,
                type(exc).__name__,
                exc,
                exc_info=True,
            )
            with server.folder_structure_commit_lock:
                state = server.folder_structure_commit
                if state and state["task_id"] == task_id:
                    state["error"] = f"{type(exc).__name__}: {exc}"
                    state["status"] = "failed"
            if not isinstance(exc, Exception):
                raise
            return

        with server.folder_structure_commit_lock:
            state = server.folder_structure_commit
            if not state or state["task_id"] != task_id:
                return
            state["result"] = result.as_dict()
            state["stage"] = "done"
            state["status"] = "completed"
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {"source": "folder-structure-commit", "change_kind": "updated"},
        )
        logger.info(
            "Folder-structure commit %s completed: %d picture(s) indexed",
            task_id,
            result.pictures_indexed,
        )

    def _resume_interrupted_commit() -> None:
        """Finish a commit the last run of this process did not.

        Called once, as the router is built at start-up. The two phases are
        both safe to re-enter: indexing is idempotent by ``file_path``, and
        assigning is a single transaction that settles its own record inside
        itself, so a commit that got as far as writing entities has already
        marked itself done and is not pending here at all.

        The task id is kept, not minted, so a client that still remembers the
        one it was polling reattaches to the resumed run rather than being
        told 404 about work that is very much still happening.
        """
        if getattr(server.vault, "disable_background_workers", False):
            # A read-only deployment does no background work at all; finishing
            # somebody else's import would be the one exception, which is not
            # a promise that flag should quietly break.
            return
        try:
            record = commit_service.pending_commit(server)
        except Exception:
            # A start-up that raises here is a library that will not open, over
            # work that is by definition already incomplete.
            logger.exception("Could not read the pending folder-mapping commit")
            return
        if record is None:
            return

        task_id = record["task_id"]
        with server.folder_structure_commit_lock:
            server.folder_structure_commit = {
                "task_id": task_id,
                "status": "queued",
                "stage": record["stage"],
                "processed": 0,
                "total": record["expected_pictures"],
                "error": None,
                "result": None,
                "stop": None,
            }
        logger.info(
            "Resuming the folder-mapping commit %s interrupted at stage %r "
            "(%s, %d picture(s) expected under %s)",
            task_id,
            record["stage"],
            record["mode"],
            record["expected_pictures"],
            record["root_path"],
        )
        threading.Thread(
            target=_run_commit,
            args=(
                task_id,
                record["root_path"],
                record["expected_pictures"],
                record["assignments"],
                record["label"],
                record["mode"],
            ),
            daemon=True,
            name="folder-structure-commit-resume",
        ).start()

    @router.post(
        "/folder-structure/commit",
        summary="Commit an accepted folder-structure mapping",
        description=(
            "mode=reference (default): registers the read's root folder for "
            "in-place indexing. mode=local_import: imports the pictures "
            "already under the ACTIVE library's own image_root as ordinary "
            "managed pictures instead - for the 'Add a library' flow's "
            "'pictures' verdict, refused unless the root is image_root itself "
            "or inside it. Either way no file is moved, renamed or copied; "
            "then the accepted projects, people, sets and tags are created "
            "and every picture found is linked to them. Returns a task id to "
            "poll; see integration_architecture.md §22."
        ),
        response_model=FolderStructureCommitStartResponse,
        tags=["folders"],
    )
    def start_folder_structure_commit(
        request: Request, payload: FolderStructureCommitRequest
    ):
        if bool(payload.task_id) == bool(payload.read_result):
            raise HTTPException(
                status_code=400,
                detail="Send either task_id or read_result, not both and not neither.",
            )

        def _check_read_settled():
            """Validate the named read exists and is settled; return its result.

            Does not mark it committed. Called here, before `assignments` is
            even parsed, so a 400 on malformed input never burns the read's
            one commit - the actual commit-reservation re-checks (and this
            time sets) `committed` again, right before the commit starts.

            A caller-supplied result skips the lookup: there is no read in this
            process to find. It is validated for the two fields the commit
            actually reads, so a malformed body fails here with a 400 rather
            than inside the task with a stack trace.
            """
            if payload.read_result is not None:
                supplied = payload.read_result
                if not isinstance(supplied.get("root"), dict) or not isinstance(
                    supplied["root"].get("path"), str
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="read_result is missing its root path.",
                    )
                return supplied
            with server.folder_structure_lock:
                read_state = server.folder_structure_read
                if not read_state or read_state["task_id"] != payload.task_id:
                    raise HTTPException(status_code=404, detail="Task not found")
                if read_state["status"] not in ("completed", "cancelled"):
                    raise HTTPException(
                        status_code=409,
                        detail="The folder-structure read has not finished yet.",
                    )
                if read_state.get("committed"):
                    raise HTTPException(
                        status_code=409,
                        detail="This read has already been committed.",
                    )
                return read_state["result"]

        result = _check_read_settled()
        if not result:
            raise HTTPException(
                status_code=409, detail="The read found nothing to map."
            )
        root_path = result["root"]["path"]
        expected_pictures = result["picture_count"]

        # The library's own storage is indexed in place as MANAGED pictures
        # and is never a reference folder. A reference-mode commit against
        # image_root registered a whole library as one reference folder once
        # (absolute paths, `reference_folder_id` on every row, and "remove"
        # on that folder would have hard-deleted all 12k pictures), so refuse
        # it here, where every client meets it, rather than in the wizard.
        if payload.mode == "reference":
            image_root = os.path.realpath(server.vault.image_root)
            resolved = os.path.realpath(root_path)
            if resolved == image_root or _within(image_root, resolved):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "That folder is the library's own storage, which is "
                        "indexed in place and is never a reference folder. "
                        'Use mode=local_import ("Add a library").'
                    ),
                )

        try:
            assignments = commit_service.parse_assignments(
                [a.model_dump() for a in payload.assignments]
            )
        except commit_service.CommitError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # A read commits once. Re-checked (not just re-validated) here, inside
        # the lock, immediately before the commit actually starts: two
        # requests racing this same task_id cannot both pass, because the
        # loser sees `committed` already true. Marked the instant a commit
        # STARTS, not once it finishes - a commit already running or already
        # done must refuse a second one against the same read, or
        # `apply_mapping` runs twice over the same pictures and creates
        # duplicate projects, people, sets, tags and memberships. See
        # backend_architecture.md §25 and integration_architecture.md §22
        # ("one-shot"). Reserving the (unrelated, single, global) commit slot
        # BEFORE marking this read committed: if a different read's commit is
        # already running there, this request must not spend this read's one
        # commit on a 409 it never got to act on.
        with server.folder_structure_commit_lock:
            current = server.folder_structure_commit
            if current and current["status"] in ("queued", "running"):
                raise HTTPException(
                    status_code=409,
                    detail="A folder-structure commit is already running.",
                )

            # Only a read this process performed can be reserved. A supplied
            # result has no slot to mark, which is stated on the field: the
            # caller that kept the result owns the once-only-ness of it.
            if payload.task_id:
                with server.folder_structure_lock:
                    read_state = server.folder_structure_read
                    if not read_state or read_state["task_id"] != payload.task_id:
                        raise HTTPException(status_code=404, detail="Task not found")
                    if read_state.get("committed"):
                        raise HTTPException(
                            status_code=409,
                            detail="This read has already been committed.",
                        )
                    read_state["committed"] = True

            task_id = str(uuid.uuid4())
            server.folder_structure_commit = {
                "task_id": task_id,
                "status": "queued",
                "stage": "registering",
                "processed": 0,
                "total": expected_pictures,
                "error": None,
                "result": None,
                "stop": None,
            }

        # Written before the thread starts, so the window this record exists
        # for - the crash between "the owner pressed the button" and "the
        # pictures are organised" - is covered from its first instant.
        commit_service.record_pending_commit(
            server,
            task_id=task_id,
            root_path=root_path,
            mode=payload.mode,
            label=payload.label,
            expected_pictures=expected_pictures,
            assignments=assignments,
        )

        threading.Thread(
            target=_run_commit,
            args=(
                task_id,
                root_path,
                expected_pictures,
                assignments,
                payload.label,
                payload.mode,
            ),
            daemon=True,
            name="folder-structure-commit",
        ).start()
        logger.info("Folder-structure commit started: task_id=%s", task_id)
        return {"task_id": task_id}

    @router.get(
        "/folder-structure/commit/status",
        summary="Get folder-structure commit status",
        description=(
            "Progress for a commit started by POST /folder-structure/commit. "
            "`result` is null until `status` is `completed`."
        ),
        response_model=FolderStructureCommitStatusResponse,
        tags=["folders"],
    )
    def folder_structure_commit_status(request: Request, task_id: str = Query(...)):
        with server.folder_structure_commit_lock:
            state = server.folder_structure_commit
            if not state or state["task_id"] != task_id:
                raise HTTPException(status_code=404, detail="Task not found")
            state = dict(state)
        total = state["total"] or 0
        processed = state["processed"] or 0
        return {
            "task_id": task_id,
            "status": state["status"],
            "stage": state["stage"],
            "processed": processed,
            "total": total,
            "progress": (processed / total * 100.0) if total else 0.0,
            "error": state["error"],
            "result": state["result"],
        }

    @router.delete(
        "/folder-structure/commit",
        summary="Stop a running folder-structure commit",
        description=(
            "`stop=abort` gives up on the commit; `stop=defer` is 'organise "
            "later' - the indexing already done stands and the mapping is not "
            "applied. Neither un-indexes anything: no file is touched either "
            "way, and every picture already indexed stays indexed. The commit "
            "unwinds at its next chunk boundary, so a following status poll "
            "can still report `running` briefly."
        ),
        response_model=FolderStructureCommitStopResponse,
        tags=["folders"],
    )
    def stop_folder_structure_commit(
        request: Request,
        task_id: str = Query(...),
        stop: Literal["abort", "defer"] = Query("abort"),
    ):
        wanted = STATE_ABANDONED if stop == "abort" else STATE_DEFERRED
        with server.folder_structure_commit_lock:
            state = server.folder_structure_commit
            if not state or state["task_id"] != task_id:
                raise HTTPException(status_code=404, detail="Task not found")
            if state["status"] not in ("queued", "running"):
                # Same honesty as the read's cancel: reporting the stop as
                # accepted would be a claim the client cannot check, and this
                # commit is already over.
                return {"status": state["status"]}
            state["stop"] = wanted
        logger.info(
            "Folder-structure commit %s asked to stop (%s) by the owner",
            task_id,
            wanted,
        )
        return {"status": wanted}

    # Handed to the Server rather than called here: this factory runs from
    # `_setup_routes`, which is EARLIER in `Server.__init__` than the commit
    # slot and its lock are created, so calling it now reads attributes that
    # do not exist yet. The Server calls it once those are up.
    server.resume_folder_mapping_commit = _resume_interrupted_commit

    return router
