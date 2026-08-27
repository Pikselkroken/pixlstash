"""Files onto the shelf (shelf plan F6, ``Add file``) and off it again (#933).

``POST /model-files`` is the way in and ``POST /model-files/delete`` is the way
out. They are one module because they are one authority - a file in a registered
folder, written or unlinked - and because the second is the only shelf route
that destroys the owner's own bytes, which is worth reading beside the one that
insists it never touches them.

``POST /model-files`` is the path for a single adapter or checkpoint that is
**not** part of a training
run and does not deserve a whole registered folder of its own: a file downloaded
into ``~/Downloads`` an hour ago. It is copied into the managed store - the
folder PixlStash owns, the ruled default destination for a drop or an import -
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
nobody has registered. What is contained is the *write*, not the read - the
destination is resolved with ``resolve_path_within`` against the registered
destination folder - and the read is bounded by refusing anything that is not a
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
Everything else the shelf lists is refused whole - the engines PixlStash
downloads for itself, the InsightFace packs, the HuggingFace cache shared with
every other tool on the machine - and so is a model with an ``unreachable``
copy, because an unplugged drive is not a deletion and must never be read as
one. The default is the OS trash (``send2trash``), which is the undo;
``permanent=true`` unlinks, and that one has none.

**A copy's training previews go with it.** An imported checkpoint carries a
``<stem>_samples/`` directory beside it (``services/run_importer.py``), and the
delete closes the lifecycle the import opens and a move carries: skipping it
would leave a directory no route lists and no rescan registers, and one that then
refuses the owner's *whole* re-import of that run - with the remedy only
available outside the app. Unlike the file, it is **non-fatal**: the weights are
what was asked for, and previews that will not go are a warning and some occupied
disk rather than a failed deletion.

**Bytes first, rows second, and per model.** Every copy of a model is removed
before its hub rows are, so an interruption leaves a row naming a file that is
not there - which the next scan turns into ``missing`` - rather than a file
nothing on the shelf can see. A model whose unlink fails keeps its rows and is
reported as refused, so one bad file cannot take the rest of the batch with it.
The whole call holds the same machine-wide ``SHELF_IO_LOCK`` slot an add, a move
and an import take, so nothing can be copying into a folder this is emptying.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from send2trash import TrashPermissionError, send2trash

from pixlstash.pixl_logging import get_logger
from pixlstash.services.managed_model_store import MANAGED_KIND, deletes_unclaimed_files
from pixlstash.services.model_folder_scanner import (
    MODEL_SUFFIX,
    STATE_PRESENT,
    STATE_UNREACHABLE,
    ModelFolderScanner,
)
from pixlstash.services.model_mover import (
    PARTIAL_SUFFIX,
    SHELF_IO_LOCK,
    MoveRefused,
    copy_and_digest,
    discard_partial,
    file_digest,
    publish_no_clobber,
    require_space,
    samples_relpath,
)
from pixlstash.services.model_shelf_service import (
    MAX_MODELS_PER_EDIT,
    purge_deleted_models,
)
from pixlstash.utils.adapter_header import FILE_ENGINE
from pixlstash.utils.aitoolkit_run import is_sample_filename
from pixlstash.utils.path_utils import path_is_within, resolve_path_within
from pixlstash.utils.system_utils import TRASH_NAME

logger = get_logger(__name__)

SOURCE_FOLDER_KIND = "source"

# Which folders the shelf may unlink from is `deletes_unclaimed_files`, in
# `managed_model_store` - that is where "which declared root is which" is already
# decided, and by path, because the columns cannot tell PixlStash's download
# folder from the HuggingFace cache. `GET /model-folders` reports the same answer
# as `deletable`, so the client never offers a delete this route would refuse.
# It is the same line `model_mover._plan_one` draws for a move, drawn by `kind`
# rather than by `movable` because the managed store is `root_only` (the FOLDER
# moves as a unit) while the files in it are individually the owner's.

# `STATE_UNREACHABLE` - a copy the scan could not look at, on a drive that is not
# plugged in - is the one state that must never be treated as a deletion: the
# bytes are out there, and dropping the row would leave them orphaned with
# nothing on the shelf naming them. Imported from the scanner that writes them
# rather than re-spelled here, so a rename cannot quietly turn this gate into a
# no-op.


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
            "sits in a folder PixlStash will not unlink from - read `deletable` "
            "on `GET /model-folders` for which those are; the InsightFace packs "
            "and the shared HuggingFace cache are the two on a stock machine, "
            "and its own download folder is NOT one of them: the leftovers "
            "there are yours), "
            "`unreachable_copy` (a copy is on a drive that is not plugged in, "
            "which is not a deletion), `escapes_its_folder` (the row names a "
            "path outside the folder it is registered in, which is a broken "
            "row), `trash_unavailable` (this machine has no trash we can reach; "
            "a permanent delete would still work), `partly_deleted` (some "
            "copies went and one failed, so the rows were kept - the only "
            "refusal that has already destroyed something) or `delete_failed` "
            "(nothing was removed and the server log says why)."
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
    trash_name: str = Field(
        default=TRASH_NAME,
        description=(
            "What THIS machine calls the place the files went - `Trash`, or "
            "`Recycle Bin` on Windows. On the receipt because where the bytes "
            "are is the difference between recoverable and not, and the server "
            "is the machine they are on: a shelf opened from a laptop deletes "
            "files wherever PixlStash is running."
        ),
    )
    refused: list[DeleteRefusal] = Field(
        description=(
            "Ids that were left alone, each with a reason. Reported rather than "
            "raised: a selection is made against a list that may be seconds old, "
            "and failing the whole call because one model moved would be the "
            "wrong answer to good news."
        )
    )


def _contained_path(folder_path: str, relpath: str) -> str:
    """Where one registered copy is, proven to be inside its own folder.

    **The link, never the link's target.** ``resolve_path_within`` returns a
    ``realpath``, and unlinking that would delete the file a symlinked model
    points AT while leaving the link - which is not what a file manager does,
    and which silently guts any other model row naming those bytes. A symlinked
    model is ordinary practice on this shelf (``model_shelf._present_copy``
    contains lexically for exactly that reason), so the containment here is
    lexical on the file itself and ``realpath`` on the DIRECTORY holding it:
    a ``..`` in the row cannot escape, a symlinked *directory* component cannot
    redirect the unlink out of the folder, and the thing removed is still the
    name the shelf catalogues.

    Raises:
        ValueError: when the row names something outside its registered folder.
    """
    lexical = os.path.normpath(os.path.join(folder_path, relpath))
    if not path_is_within(lexical, folder_path):
        raise ValueError(f"{relpath!r} is not inside {folder_path!r}")
    parent = os.path.realpath(os.path.dirname(lexical))
    if not path_is_within(parent, folder_path):
        raise ValueError(
            f"{relpath!r} sits in a directory that resolves outside {folder_path!r}"
        )
    return os.path.join(parent, os.path.basename(lexical))


def _plan_deletions(hub, ids: list[int]) -> tuple[dict[int, list[dict]], list[dict]]:
    """Split the requested ids into copies-to-remove and refusals.

    Every gate is per MODEL and refuses the whole of it: a model with one copy
    in a user folder and another in the HuggingFace cache is not half-deleted,
    because half of it would come straight back on the next scan and the row the
    owner wanted gone would still be there.

    **The reads share one transaction**, which is what
    :func:`~pixlstash.services.model_shelf_service.forget_models` learned to do
    and for the same reason: ``hub.fetchall`` takes and releases the hub lock per
    call, so two of them leave a window in which a background
    ``ModelFolderScanner`` can rewrite the very states being gated on. The
    unlink cannot run inside this block - a 24 GB file would hold the hub's
    write lock for the length of a disk copy - so the window against the FILES
    remains and is closed on the other side instead: the purge drops a ``model``
    row only when no location row for it survives (see
    :func:`~pixlstash.services.model_shelf_service.purge_deleted_models`).

    Args:
        hub: The open hub database.
        ids: ``model.id`` values, already de-duplicated.

    Returns:
        ``(deletable, refused)``. ``deletable`` maps a model id to one entry per
        registered copy - ``{"folder_id", "relpath", "path"}``, where ``path``
        is ``None`` for a ``missing`` copy, which has a row to drop and nothing
        to unlink. ``refused`` carries ``{"id", "reason"}``.
    """
    marks = ", ".join("?" for _ in ids)
    with hub.transaction() as conn:
        kinds = {
            int(row[0]): row[1]
            for row in conn.execute(
                f"SELECT id, file_kind FROM model WHERE id IN ({marks})", tuple(ids)
            ).fetchall()
        }
        copies: dict[int, list[dict]] = {}
        for row in conn.execute(
            "SELECT mf.model_id, mf.model_folder_id, mf.relpath, mf.state, "
            "f.path AS folder_path, f.kind AS folder_kind FROM model_file mf "
            f"JOIN model_folder f ON f.id = mf.model_folder_id "
            f"WHERE mf.model_id IN ({marks})",
            tuple(ids),
        ).fetchall():
            copies.setdefault(int(row["model_id"]), []).append(dict(row))

    deletable: dict[int, list[dict]] = {}
    refused: list[dict] = []
    for model_id in ids:
        rows = copies.get(model_id, [])
        if model_id not in kinds:
            refused.append({"id": model_id, "reason": "no_such_model"})
        elif kinds[model_id] == FILE_ENGINE:
            # Declared again on every start, so deleting one removes a file
            # PixlStash re-downloads the moment something needs it.
            refused.append({"id": model_id, "reason": "is_a_builtin_engine"})
        elif any(
            not deletes_unclaimed_files(row["folder_kind"], row["folder_path"])
            for row in rows
        ):
            refused.append({"id": model_id, "reason": "not_a_user_folder"})
        elif any(row["state"] == STATE_UNREACHABLE for row in rows):
            refused.append({"id": model_id, "reason": "unreachable_copy"})
        else:
            try:
                deletable[model_id] = [
                    {
                        "folder_id": int(row["model_folder_id"]),
                        "relpath": row["relpath"],
                        # The containment site. A relpath that escapes its
                        # folder is a broken row, not a request to unlink
                        # somebody's file outside the shelf.
                        "path": (
                            _contained_path(row["folder_path"], row["relpath"])
                            if row["state"] == STATE_PRESENT
                            else None
                        ),
                    }
                    for row in rows
                ]
            except ValueError as exc:
                logger.error(
                    "Refusing to delete model %s: a registered copy resolves "
                    "outside its folder (%s). The row is wrong; nothing was "
                    "touched.",
                    model_id,
                    exc,
                )
                refused.append({"id": model_id, "reason": "escapes_its_folder"})
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


def _holds_only_samples(directory: str) -> bool:
    """Whether every entry is a preview the trainer wrote, and nothing else.

    **The question the removal turns on, asked of the directory rather than of
    the database.** ``<stem>_samples`` is derived by string manipulation and
    recorded nowhere, so its path alone is a guess about who created it. What
    settles the guess is the contents: ai-toolkit names every preview
    ``<timestamp>__<step>_<index>.<ext>``, so a directory holding only those is
    a directory of previews whoever put it there, and a single file that is not
    one - an owner's favourite render, a note, a subdirectory - means it is
    theirs and the model does not take it.

    Symlinks count as "not a sample" (``follow_symlinks=False``): a link is a
    reference to something outside this directory, and what it points at is not
    this folder's to delete.

    An empty directory passes. There is nothing in it to lose, and leaving
    empties behind is how the re-import refusal gets triggered for no reason.
    """
    try:
        entries = list(os.scandir(directory))
    except OSError as exc:
        logger.warning(
            "Could not read %s to decide whether it holds only previews: %s. "
            "Leaving it in place, which is the answer that cannot destroy "
            "anything.",
            directory,
            exc,
        )
        return False
    return all(
        entry.is_file(follow_symlinks=False) and is_sample_filename(entry.name)
        for entry in entries
    )


def _remove_samples(model_path: str, *, permanent: bool) -> None:
    """Take the file's training previews with it - **if that is all they are**.

    An imported checkpoint's previews sit beside it in ``<stem>_samples/``
    (``services/run_importer.py``), and the lifecycle the import opens and a move
    carries has to close here: a delete that skipped it would leave a directory
    no route lists and no rescan registers, and one that then refuses the owner's
    *entire* re-import of that run, with the remedy only available outside the
    app.

    **What licenses the removal is the directory's contents**, checked by
    :func:`_holds_only_samples`. The model itself is a thing the caller named -
    they selected that row and a ``model_file`` records exactly which file it is
    - but this directory is only ever *inferred* from the model's name, so
    removing it on the strength of the name alone would destroy an owner's own
    folder of renders on a Shift+Delete they meant for a ``.safetensors``. A
    directory of nothing but ``<timestamp>__<step>_<index>`` images is the
    model's previews whoever wrote them; one holding anything else is theirs and
    stays, which is the same answer the importer gives when it refuses to merge
    into a directory that is already there.

    **Non-fatal, unlike the file itself.** The weights are what the caller asked
    to delete and their row is dropped on the strength of that; a previews
    directory that will not go is a warning and some occupied disk, and must not
    turn a completed deletion into a reported failure. It is removed *after* the
    file for the same reason - the file is the thing being deleted.
    """
    directory = samples_relpath(model_path)
    if not os.path.isdir(directory):
        return
    if os.path.islink(directory):
        # ``isdir`` follows the link, so without this the removal is decided by
        # which branch runs: ``rmtree`` happens to refuse a symlinked root and
        # ``send2trash`` happens to move the link and spare its target. Neither
        # is a property either function promises, and a later
        # ``ignore_errors=True`` would silently turn the first into a deletion
        # somewhere else entirely. Refused here, where it is stated and tested.
        logger.warning(
            "Not removing the training previews of %s: %s is a symbolic link, "
            "and what it points at is not this folder's to delete.",
            os.path.basename(model_path),
            directory,
        )
        return
    if not _holds_only_samples(directory):
        logger.info(
            "Left %s in place: it holds something other than this run's "
            "previews, so it is not %s's to remove.",
            directory,
            os.path.basename(model_path),
        )
        return
    try:
        if permanent:
            shutil.rmtree(directory)
        else:
            send2trash(directory)
    except FileNotFoundError:
        logger.debug("No samples directory at %s to remove.", directory)
    except (TrashPermissionError, OSError) as exc:
        logger.warning(
            "Deleted %s but could not remove its training previews at %s: %s. "
            "They are occupying disk and nothing on the shelf names them; "
            "re-importing that run into this folder will be refused until they "
            "are removed by hand.",
            os.path.basename(model_path),
            directory,
            exc,
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

        ``realpath`` first, because everything after it - the suffix, the
        registered-folder check, the copy - has to reason about the file that
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
            "into a folder the shelf catalogues - the managed store unless "
            "another is named - and registers it, so it appears without a "
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
            # *only* here - ``os.path.exists`` is False for it).
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
                # Published rather than replaced, for the same reason the mover
                # publishes: the owner, ComfyUI or a trainer is under no lock of
                # ours, and a check followed by ``os.replace`` still has a gap
                # between them to lose a file in (#1012).
                publish_no_clobber(partial, target)
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
                # registered it either. Our copy is unambiguously ours - the
                # target was proven free above - so it goes rather than sitting
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
            "shelf rows. A model **imported from a training run** also loses "
            "the `<stem>_samples/` directory of previews the import wrote "
            "beside it; a directory beside a model PixlStash did not import is "
            "left alone, because it is not ours to have made. "
            "`permanent=false` (the default) moves the files to "
            f"this machine's {TRASH_NAME.lower()}, which is the undo; "
            "`permanent=true` unlinks them and there is none. Only the folders "
            "whose contents are yours are touched - the ones you registered and "
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
            emptied: dict[int, list[tuple[int, str]]] = {}
            files_removed = 0
            for model_id, copies in deletable.items():
                paths = [copy["path"] for copy in copies if copy["path"]]
                done = 0
                try:
                    for path in paths:
                        _remove(path, permanent=payload.permanent)
                        done += 1
                        # After the file, and never allowed to fail it.
                        _remove_samples(path, permanent=payload.permanent)
                except (TrashPermissionError, OSError) as exc:
                    # `done` files of this model are already gone. Its rows stay
                    # so the shelf keeps naming the copies that did not go, and
                    # the refusal says which of the two happened - "could not be
                    # deleted" over a model that lost half its copies is the one
                    # sentence a reader must not be given.
                    partly = done > 0
                    reason = (
                        "partly_deleted"
                        if partly
                        else (
                            "trash_unavailable"
                            if isinstance(exc, TrashPermissionError)
                            else "delete_failed"
                        )
                    )
                    logger.error(
                        "Could not delete %s (%s). Model %s keeps its rows; %d "
                        "of its %d copies were already removed, and a rescan of "
                        "that folder will mark those missing.",
                        paths[done],
                        exc,
                        model_id,
                        done,
                        len(paths),
                        exc_info=not isinstance(exc, TrashPermissionError),
                    )
                    refused.append({"id": model_id, "reason": reason})
                else:
                    deleted.append(model_id)
                    emptied[model_id] = [
                        (copy["folder_id"], copy["relpath"]) for copy in copies
                    ]
                finally:
                    files_removed += done
            # One transaction for every model that came through, after the last
            # unlink rather than per model. It drops the location rows this call
            # emptied and then the models left with none - so a copy a scan
            # registered while the files were going keeps its model alive rather
            # than being purged out from under a file that is still there.
            purged = purge_deleted_models(server.hub, emptied)
            if len(purged) != len(deleted):
                logger.warning(
                    "Deleted the files of %d model(s) but %d row(s) survived: a "
                    "scan registered a copy while this ran. The shelf will show "
                    "them as missing until it next walks that folder.",
                    len(deleted),
                    len(deleted) - len(purged),
                )
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
