"""Files onto the shelf (shelf plan F6, ``Add file``) and off it again (#933).

``POST /model-files`` is the way in and ``POST /model-files/delete`` is the way
out. They are one module because they are one authority — a file in a registered
folder, written or unlinked — and because the second is the only shelf route
that destroys the owner's own bytes, which is worth reading beside the one that
insists it never touches them.

``POST /model-files`` is the path for a single adapter or checkpoint that is
**not** part of a training
run and does not deserve a whole registered folder of its own: a file downloaded
into ``~/Downloads`` an hour ago. It is copied into the managed store — the
folder PixlStash owns, the ruled default destination for a drop or an import —
and registered there, so it appears on the shelf without the owner having to
rescan anything.

**It is a copy, never a move.** The source is the owner's own file in the owner's
own directory, which PixlStash did not put there and has no business unlinking;
``delete_after_import`` exists precisely because deleting a source is a decision,
and it is a decision about a *registered* folder rather than about an arbitrary
path. So the ordering here is the move's with the last step removed: **copy →
verify by SHA-256 → register the row and commit.** An interruption leaves either
nothing or an unregistered file in the store, never a row naming a file that is
not there.

**This is the one shelf route that takes a host path**, which the import block
beside it deliberately does not (a run is named, and the server joins the name to
a registered root). It cannot be otherwise: the whole point is a file in a place
nobody has registered. What is contained is the *write*, not the read — the
destination is resolved with ``resolve_path_within`` against the registered
destination folder — and the read is bounded by refusing anything that is not a
regular ``.safetensors`` file. Authorization is therefore ``LOCAL_OWNER_ONLY``
(declared in ``pixlstash/authz/registry.py``): it takes a caller-supplied host
path like ``POST /model-folders`` and writes into a registered folder like
``POST /model-moves``, and it is on that tier for both halves.

**A file already inside a registered folder is refused.** Copying it would put a
second copy of a file the shelf already catalogues into the store, under the same
name, forever; a rescan of the folder it is already in is what the owner wants
and the refusal says so.

``POST /model-files/delete`` is the shelf's destructive verb, and it is
deliberately narrow. It acts only on the folders whose contents are the owner's:
``user``, and the managed store PixlStash keeps for files it was *given*.
Everything else the shelf lists is refused whole — the engines PixlStash
downloads for itself, the InsightFace packs, the HuggingFace cache shared with
every other tool on the machine — and so is a model with an ``unreachable``
copy, because an unplugged drive is not a deletion and must never be read as
one. The default is the OS trash (``send2trash``), which is the undo;
``permanent=true`` unlinks, and that one has none.

**Bytes first, rows second, and per model.** Every copy of a model is removed
before its hub rows are, so an interruption leaves a row naming a file that is
not there — which the next scan turns into ``missing`` — rather than a file
nothing on the shelf can see. A model whose unlink fails keeps its rows and is
reported as refused, so one bad file cannot take the rest of the batch with it.
The whole call holds the same machine-wide ``SHELF_IO_LOCK`` slot an add, a move
and an import take, so nothing can be copying into a folder this is emptying.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from send2trash import TrashPermissionError, send2trash

from pixlstash.pixl_logging import get_logger
from pixlstash.routes.model_shelf import MAX_MODELS_PER_EDIT
from pixlstash.services.managed_model_store import MANAGED_KIND
from pixlstash.services.model_folder_scanner import MODEL_SUFFIX, ModelFolderScanner
from pixlstash.services.model_mover import (
    PARTIAL_SUFFIX,
    SHELF_IO_LOCK,
    MoveRefused,
    copy_and_digest,
    discard_partial,
    file_digest,
    require_space,
)
from pixlstash.services.model_shelf_service import purge_models
from pixlstash.utils.adapter_header import FILE_ENGINE
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.utils.system_utils import TRASH_NAME

logger = get_logger(__name__)

SOURCE_FOLDER_KIND = "source"

# The folder kinds whose contents are the owner's to destroy. `user` is a folder
# they registered; `managed` is the store PixlStash keeps for files it was given,
# which is where `Add file` and an import land, so a shelf that could not delete
# from it could not undo either of them.
#
# Every other kind is `foreign` — PixlStash's own engines, the InsightFace packs,
# the HuggingFace cache — and those are registered so the owner can SEE what they
# cost on disk, not so the shelf can unlink them. The cache in particular is a
# symlink store shared with every other tool on the machine. This is the same
# line `model_mover._plan_one` draws for a move, drawn by `kind` rather than by
# `movable` because the managed store is `root_only` (the FOLDER moves as a unit)
# while the files in it are individually the owner's.
DELETABLE_FOLDER_KINDS = ("user", MANAGED_KIND)

# A copy the scan could not look at, on a drive that is not plugged in. It is the
# one state that must never be treated as a deletion: the bytes are out there,
# and dropping the row would leave them orphaned with nothing on the shelf naming
# them.
STATE_UNREACHABLE = "unreachable"
STATE_PRESENT = "present"


class AddModelFileRequest(BaseModel):
    """Body of ``POST /model-files``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description=(
            "The file on this machine, as an absolute path. It is copied, not "
            "moved: the original stays where it is."
        )
    )
    destination_folder_id: Optional[int] = Field(
        default=None,
        description=(
            "A registered folder the shelf catalogues. Omit for the managed "
            "store, which is the ruled default destination. Never a `source` one."
        ),
    )


class AddModelFileResponse(BaseModel):
    """Body of ``POST /model-files``."""

    model_config = ConfigDict(extra="allow")

    model_id: int = Field(description="The hub `model.id` the file landed on.")
    filename: str = Field(description="The name it now carries in the folder.")
    folder_id: int = Field(description="The registered folder it was copied into.")
    folder_path: str = Field(description="That folder's path on this machine.")


class DeleteModelsRequest(BaseModel):
    """Body of ``POST /model-files/delete``."""

    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(
        min_length=1,
        max_length=MAX_MODELS_PER_EDIT,
        description="The models to delete, by hub `model.id`. Every copy goes.",
    )
    permanent: bool = Field(
        default=False,
        description=(
            "False (the default) moves the files to this machine's trash, which "
            "is the undo. True unlinks them, and nothing gets them back. The "
            "shelf sends true only for Shift+Delete, the file-manager gesture."
        ),
    )


class DeleteRefusal(BaseModel):
    """One id the delete declined, and why."""

    model_config = ConfigDict(extra="allow")

    id: int
    reason: str = Field(
        description=(
            "`no_such_model` (the id names no row), `is_a_builtin_engine` "
            "(PixlStash downloaded it for itself), `not_a_user_folder` (a copy "
            "sits somewhere PixlStash will not unlink from — its own engine "
            "folders, the InsightFace packs, the shared HuggingFace cache), "
            "`unreachable_copy` (a copy is on a drive that is not plugged in, "
            "which is not a deletion), `trash_unavailable` (this machine has no "
            "trash we can reach; a permanent delete would still work) or "
            "`delete_failed` (the unlink itself failed — the server log says "
            "why)."
        )
    )


class DeleteModelsResponse(BaseModel):
    """Body of ``POST /model-files/delete``: the receipt the shelf shows."""

    model_config = ConfigDict(extra="allow")

    deleted: list[int] = Field(
        description="Ids whose files are gone and whose rows went with them, ascending."
    )
    files_removed: int = Field(
        description="How many files were actually unlinked or trashed."
    )
    permanent: bool = Field(
        description="What was done, echoed: trashed (false) or unlinked (true)."
    )
    refused: list[DeleteRefusal] = Field(
        description=(
            "Ids that were left alone, each with a reason. Reported rather than "
            "raised: a selection is made against a list that may be seconds old, "
            "and failing the whole call because one model moved would be the "
            "wrong answer to good news."
        )
    )


def _plan_deletions(hub, ids: list[int]) -> tuple[dict[int, list[str]], list[dict]]:
    """Split the requested ids into files-to-remove and refusals.

    Every gate is per MODEL and refuses the whole of it: a model with one copy
    in a user folder and another in the HuggingFace cache is not half-deleted,
    because half of it would come straight back on the next scan and the row the
    owner wanted gone would still be there.

    Args:
        hub: The open hub database.
        ids: ``model.id`` values, already de-duplicated.

    Returns:
        ``(deletable, refused)`` — ``deletable`` maps a model id to the resolved
        paths of its ``present`` copies (an empty list when every copy is
        ``missing``, which is a row to drop and nothing to unlink), and
        ``refused`` carries ``{"id", "reason"}``.
    """
    marks = ", ".join("?" for _ in ids)
    kinds = {
        int(row["id"]): row["file_kind"]
        for row in hub.fetchall(
            f"SELECT id, file_kind FROM model WHERE id IN ({marks})", tuple(ids)
        )
    }
    copies: dict[int, list] = {}
    for row in hub.fetchall(
        "SELECT mf.model_id, mf.relpath, mf.state, f.path AS folder_path, "
        "f.kind AS folder_kind FROM model_file mf "
        f"JOIN model_folder f ON f.id = mf.model_folder_id "
        f"WHERE mf.model_id IN ({marks})",
        tuple(ids),
    ):
        copies.setdefault(int(row["model_id"]), []).append(row)

    deletable: dict[int, list[str]] = {}
    refused: list[dict] = []
    for model_id in ids:
        rows = copies.get(model_id, [])
        if model_id not in kinds:
            refused.append({"id": model_id, "reason": "no_such_model"})
        elif kinds[model_id] == FILE_ENGINE:
            # Declared again on every start, so deleting one removes a file
            # PixlStash re-downloads the moment something needs it.
            refused.append({"id": model_id, "reason": "is_a_builtin_engine"})
        elif any(row["folder_kind"] not in DELETABLE_FOLDER_KINDS for row in rows):
            refused.append({"id": model_id, "reason": "not_a_user_folder"})
        elif any(row["state"] == STATE_UNREACHABLE for row in rows):
            refused.append({"id": model_id, "reason": "unreachable_copy"})
        else:
            try:
                deletable[model_id] = [
                    # The containment site. A relpath that escapes its folder is
                    # a broken row, not a request to unlink somebody's file
                    # outside the shelf — and this is the mover's rule, so a
                    # symlinked model is refused here exactly as it is there.
                    resolve_path_within(row["folder_path"], row["relpath"])
                    for row in rows
                    if row["state"] == STATE_PRESENT
                ]
            except ValueError as exc:
                logger.error(
                    "Refusing to delete model %s: a registered copy resolves "
                    "outside its folder (%s). The row is wrong; nothing was "
                    "touched.",
                    model_id,
                    exc,
                )
                refused.append({"id": model_id, "reason": "delete_failed"})
    return deletable, refused


def _remove(path: str, *, permanent: bool) -> None:
    """Trash or unlink one file, treating an already-gone one as done.

    ``FileNotFoundError`` is success, not failure: the shelf is a catalogue of
    what a scan saw, the owner may have deleted the file themselves since, and
    the call asked for the file to not be there.
    """
    try:
        if permanent:
            os.remove(path)
        else:
            send2trash(path)
    except FileNotFoundError:
        logger.warning(
            "%s was already gone when the shelf went to delete it; the row is "
            "dropped anyway, which is what was asked for.",
            path,
        )


def create_router(server) -> APIRouter:
    """Create the loose-file router.

    Args:
        server: The Server instance, for ``hub`` (the shelf tables) and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    def _source_file(raw_path: str) -> str:
        """Resolve and vet the file the caller named.

        ``realpath`` first, because everything after it — the suffix, the
        registered-folder check, the copy — has to reason about the file that
        will actually be read rather than about a symlink standing in for it.
        """
        resolved = os.path.realpath(os.path.normpath(raw_path))
        if not os.path.isabs(resolved) or not os.path.isfile(resolved):
            raise HTTPException(
                status_code=404, detail=f"No file at {raw_path!r} on this machine."
            )
        if not resolved.lower().endswith(MODEL_SUFFIX):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"The shelf catalogues {MODEL_SUFFIX} files. "
                    f"{os.path.basename(resolved)} is not one."
                ),
            )
        for row in server.hub.fetchall("SELECT id, path, kind FROM model_folder"):
            folder = os.path.normpath(row["path"])
            if resolved == folder or resolved.startswith(folder + os.sep):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"That file is already inside {row['path']}, a folder "
                        "PixlStash knows about. Rescan that folder instead of "
                        "copying the file a second time."
                    ),
                )
        return resolved

    def _destination_folder(folder_id: Optional[int]) -> dict:
        if folder_id is None:
            row = server.hub.fetchone(
                "SELECT id, path, kind FROM model_folder WHERE kind = ? ORDER BY id",
                (MANAGED_KIND,),
            )
            if row is None:
                # First-run creation failed, i.e. the store's directory could not
                # be made. Naming that beats a bare 404 on a folder the caller
                # never chose.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The managed model store is not registered, so there is "
                        "no default destination. Add a model folder and name it."
                    ),
                )
        else:
            row = server.hub.fetchone(
                "SELECT id, path, kind FROM model_folder WHERE id = ?", (folder_id,)
            )
            if row is None:
                raise HTTPException(
                    status_code=404, detail="No such destination folder."
                )
            if row["kind"] == SOURCE_FOLDER_KIND:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "A source folder is where runs are taken from, never a "
                        "place to put a file."
                    ),
                )
        if not os.path.isdir(row["path"]):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{row['path']} is not a readable directory right now, so "
                    "nothing was added."
                ),
            )
        return dict(row)

    @router.post(
        "/model-files",
        summary="Add one model file to the shelf",
        description=(
            "Copies a single `.safetensors` file from anywhere on this machine "
            "into a folder the shelf catalogues — the managed store unless "
            "another is named — and registers it, so it appears without a "
            "rescan. The order is copy, verify by SHA-256, then register and "
            "commit; **the original is never removed**. A file that already sits "
            "inside a registered folder is refused: a rescan of that folder is "
            "what puts it on the shelf."
        ),
        tags=["model_shelf"],
        response_model=AddModelFileResponse,
    )
    def add_model_file(request: Request, payload: AddModelFileRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        source = _source_file(payload.path)
        folder = _destination_folder(payload.destination_folder_id)

        relpath = os.path.basename(source)
        try:
            # The write is contained even though the name is a basename: a
            # symlink standing at the destination filename resolves out of the
            # folder, and this is what refuses it (a dangling one is refused
            # *only* here — ``os.path.exists`` is False for it).
            target = resolve_path_within(folder["path"], relpath)
        except ValueError as exc:
            logger.error(
                "Refusing to add %s to folder %s: %r resolves outside %s (%s).",
                source,
                folder["id"],
                relpath,
                folder["path"],
                exc,
            )
            raise HTTPException(
                status_code=400,
                detail=f"{relpath!r} would be written outside the destination folder.",
            ) from exc
        if os.path.lexists(target):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{relpath} already exists in {folder['path']}. Nothing was added."
                ),
            )
        if server.hub.fetchone(
            "SELECT 1 FROM model_file WHERE model_folder_id = ? AND relpath = ?",
            (folder["id"], relpath),
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{relpath} is already registered in that folder. Rescan it first."
                ),
            )

        # The *same* slot a move and an import take: two writers that each found
        # one destination filename free would otherwise race for it.
        if not SHELF_IO_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A move or an import is already running. Two at once would "
                    "race for the free space and the filenames each of them "
                    "checked before starting."
                ),
            )
        try:
            try:
                require_space(folder["path"], os.path.getsize(source))
            except MoveRefused as exc:
                raise HTTPException(
                    status_code=exc.status_code, detail=str(exc)
                ) from exc

            partial = target + PARTIAL_SUFFIX
            try:
                written = copy_and_digest(source, partial)
                if file_digest(partial) != written:
                    raise OSError(
                        f"The copy of {source} did not verify; it was discarded "
                        "and the original is untouched."
                    )
                # Re-checked after the copy for the same reason the mover
                # re-checks: ``os.replace`` overwrites in silence, and the owner,
                # ComfyUI or a trainer is under no lock of ours.
                if os.path.lexists(target):
                    raise OSError(
                        f"{relpath} appeared in the destination folder while the "
                        "copy was running; the copy was discarded rather than "
                        "written over it."
                    )
                os.replace(partial, target)
            except OSError as exc:
                discard_partial(partial)
                logger.error(
                    "Adding %s to %s failed: %s. The original file is untouched.",
                    source,
                    target,
                    exc,
                    exc_info=True,
                )
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            # The digest goes with it: `copy_and_digest` hashed these bytes on
            # the way in and `file_digest` proved the copy matches, so the
            # scanner reading the whole file a third time would only add to the
            # wait before the row appears.
            model_id = ModelFolderScanner(server.hub).register_file(
                folder["id"], target, relpath, sha256=written
            )
            if model_id is None:
                # The header would not parse, so the scanner would not have
                # registered it either. Our copy is unambiguously ours — the
                # target was proven free above — so it goes rather than sitting
                # in the store as a file the shelf never lists.
                discard_partial(target)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{relpath} could not be read as a model file, so nothing "
                        "was added. The server log says why."
                    ),
                )
        finally:
            SHELF_IO_LOCK.release()

        logger.info(
            "Added %s to model folder %s (id=%s) as model %s.",
            source,
            folder["path"],
            folder["id"],
            model_id,
        )
        return AddModelFileResponse(
            model_id=model_id,
            filename=relpath,
            folder_id=int(folder["id"]),
            folder_path=folder["path"],
        )

    @router.post(
        "/model-files/delete",
        summary="Delete models from disk",
        description=(
            "Removes every registered copy of the named models and then their "
            "shelf rows. `permanent=false` (the default) moves the files to "
            f"this machine's {TRASH_NAME.lower()}, which is the undo; "
            "`permanent=true` unlinks them and there is none. Only the folders "
            "whose contents are yours are touched — the ones you registered and "
            "the store PixlStash keeps for files it was given. A model with a "
            "copy anywhere else, a copy on a drive that is not plugged in, or "
            "one of PixlStash's own engines is refused with a reason rather "
            "than half-deleted."
        ),
        tags=["model_shelf"],
        response_model=DeleteModelsResponse,
    )
    def delete_model_files(request: Request, payload: DeleteModelsRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        # Order preserved so the receipt reads in the order asked; duplicates
        # dropped so one id cannot be planned, deleted and then planned again.
        ids = list(dict.fromkeys(payload.ids))

        # The *same* slot a move, an import and an add take. A move copying a
        # file into the folder this is emptying, or out of it, would otherwise
        # race the unlink for the row it is repointing.
        if not SHELF_IO_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A move or an import is already running. Deleting files out "
                    "from under it would leave rows naming files neither of us "
                    "put there."
                ),
            )
        try:
            deletable, refused = _plan_deletions(server.hub, ids)
            deleted: list[int] = []
            files_removed = 0
            for model_id, paths in deletable.items():
                done = 0
                try:
                    for path in paths:
                        _remove(path, permanent=payload.permanent)
                        done += 1
                except TrashPermissionError as exc:
                    logger.error(
                        "There is no trash this server can reach for %s (%s), so "
                        "model %s was left alone.",
                        paths[done],
                        exc,
                        model_id,
                    )
                    refused.append({"id": model_id, "reason": "trash_unavailable"})
                except OSError as exc:
                    logger.error(
                        "Could not delete %s (%s), so model %s keeps its rows. "
                        "%d of its %d copies had already gone; a rescan of that "
                        "folder will mark them missing.",
                        paths[done],
                        exc,
                        model_id,
                        done,
                        len(paths),
                        exc_info=True,
                    )
                    refused.append({"id": model_id, "reason": "delete_failed"})
                else:
                    deleted.append(model_id)
                finally:
                    files_removed += done
            # One transaction for every model that came through, after the last
            # unlink rather than per model: the rows are only ever dropped for
            # files this call has already removed, so batching them cannot widen
            # any window that matters.
            purge_models(server.hub, deleted)
        finally:
            SHELF_IO_LOCK.release()

        logger.info(
            "Deleted %d model(s) from the shelf (%d file(s) %s), %d refused.",
            len(deleted),
            files_removed,
            "unlinked" if payload.permanent else "trashed",
            len(refused),
        )
        return DeleteModelsResponse(
            deleted=sorted(deleted),
            files_removed=files_removed,
            permanent=payload.permanent,
            refused=[DeleteRefusal(**item) for item in refused],
        )

    return router
