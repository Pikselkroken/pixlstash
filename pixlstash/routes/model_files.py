"""Take one loose model file onto the shelf (shelf plan F6, ``Add file``).

The path for a single adapter or checkpoint that is **not** part of a training
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
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
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
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

SOURCE_FOLDER_KIND = "source"


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

    return router
