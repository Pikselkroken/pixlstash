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
from pixlstash.services.comfyui_recipe_service import (
    advertised_model_names,
    fetch_object_info,
)
from pixlstash.services.managed_model_store import MANAGED_KIND, deletes_unclaimed_files
from pixlstash.services.model_folder_scanner import (
    MODEL_SUFFIX,
    STATE_PRESENT,
    STATE_REMOVED,
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


class KeepModelCopy(BaseModel):
    """The one copy of a model to keep, addressed the way the shelf lists it."""

    model_config = ConfigDict(extra="forbid")

    model_id: int = Field(description="Hub `model.id` the copies belong to.")
    folder_id: int = Field(description="`model_folder.id` of the copy to KEEP.")
    relpath: str = Field(
        description="That copy's path relative to its folder, as the shelf reports it."
    )


class MergeCopiesRequest(BaseModel):
    """Body of ``POST /model-files/merge``."""

    model_config = ConfigDict(extra="forbid")

    keep: list[KeepModelCopy] = Field(
        min_length=1,
        max_length=MAX_MODELS_PER_EDIT,
        description=(
            "One entry per model: the copy that stays. Every OTHER copy of that "
            "model that is on the disk is removed. The keeper is named rather "
            "than the copies to remove, so no request can empty a model."
        ),
    )
    permanent: bool = Field(
        default=False,
        description=(
            "False (the default) moves the redundant copies to this machine's "
            "trash, which is the undo. True unlinks them."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Plan it and remove nothing. The same planner and the same refusals "
            "as the real call, which is what lets the client show them before "
            "the owner agrees - `comfyui_reads` in particular, which is the one "
            "warning that has to arrive before the files go."
        ),
    )


class ComfyUIReadsCopy(BaseModel):
    """A copy about to go that a configured ComfyUI says it can load."""

    model_config = ConfigDict(extra="allow")

    model_id: int
    folder_id: int
    relpath: str = Field(description="The copy being removed.")
    keeper_relpath: str = Field(description="The copy being kept.")
    keeper_advertised: bool = Field(
        description=(
            "Whether that ComfyUI also lists the keeper. True and a graph run "
            "**through PixlStash** is substituted onto it at submit; false and "
            "nothing can be substituted, so every graph naming this file breaks "
            "on that install. Either way a graph opened in ComfyUI and queued "
            "there still names the file that went."
        )
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
            "`keeper_not_present` and `keeper_is_that_copy` (`/model-files/merge` "
            "only: the copy named as the keeper is not on the disk, or it is the "
            "same file as one this would remove - a symlink or a hard link), "
            "`no_such_copy`, `not_a_duplicate`, "
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


class MergeCopiesResponse(BaseModel):
    """Body of ``POST /model-files/merge``: what was removed, and what was kept."""

    model_config = ConfigDict(extra="allow")

    merged: list[int] = Field(
        description=(
            "Models now down to one copy, ascending. **The shelf row survives** "
            "with its name, base model, kind, triggers and attachments intact; "
            "so does every removed copy's `model_file` row, at "
            "`state = 'removed'`, which is what keeps a recipe naming that "
            "filename resolving to this model afterwards."
        )
    )
    files_removed: int = Field(description="How many files were unlinked or trashed.")
    permanent: bool = Field(description="What was done, echoed.")
    dry_run: bool = Field(description="Whether anything was actually removed.")
    trash_name: str = Field(
        default=TRASH_NAME,
        description="What THIS machine calls the place the files went.",
    )
    comfyui_reads: list[ComfyUIReadsCopy] = Field(
        default_factory=list,
        description=(
            "Copies a configured ComfyUI advertises, so the owner is told before "
            "the bytes go. Empty when no ComfyUI URL is set or it cannot be "
            "asked - the absence of a warning is not a promise."
        ),
    )
    refused: list[DeleteRefusal] = Field(
        description="Models left alone, each with a reason. Same vocabulary as the delete."
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


def _same_file(one: str, other: str) -> bool:
    """Whether two registered paths are ONE file on the disk.

    Asked before a merge removes anything, because the shelf's own identity
    cannot answer it: two `model_file` rows are one `model` row whenever the
    bytes match, and a symlink or a hard link makes them match *because they are
    the same file*. A symlinked model is ordinary practice here - `_present_copy`
    contains lexically for exactly that reason - so "the same bytes twice" and
    "one file under two names" look identical on the shelf and are opposites on
    the disk: removing one of the second kind destroys the copy being kept and
    leaves a dangling link the shelf still calls `present`.

    ``os.path.samefile`` and not a ``realpath`` comparison, because it compares
    ``st_dev``/``st_ino`` and therefore catches a hard link too, which resolves
    to itself. An unstattable path answers True: that is the reading that
    refuses the merge, and a path we cannot look at is not one to delete on the
    strength of a guess.
    """
    try:
        return os.path.samefile(one, other)
    except OSError as exc:
        logger.warning(
            "Could not tell whether %s and %s are the same file (%s), so the "
            "merge treats them as one and removes neither.",
            one,
            other,
            exc,
        )
        return True


def _plan_merge(hub, keep: list[KeepModelCopy]) -> tuple[dict[int, dict], list[dict]]:
    """Split the requested keepers into copies-to-remove and refusals (#1439).

    **The caller names the copy that stays, never the ones that go.** That is
    what makes "keep one" structural rather than arithmetic: a request cannot
    empty a model, because the surviving copy is the thing being addressed, and
    it is refused unless the shelf says it is really on the disk.

    The gates are per MODEL and refuse the whole of it, as
    :func:`_plan_deletions`' do - but they are asked of the copies being
    **removed**, which is the one place the two differ. Keeping the copy in the
    HuggingFace cache and removing one from a user folder is a perfectly good
    merge; it is the unlink that has to be in a folder whose contents are the
    owner's.

    One transaction for the reads, for the reason :func:`_plan_deletions` gives:
    two ``hub.fetchall`` calls leave a window for a background
    ``ModelFolderScanner`` to rewrite the very states being gated on.

    Args:
        hub: The open hub database.
        keep: One keeper per model, already de-duplicated by ``model_id``.

    Returns:
        ``(plans, refused)``. ``plans`` maps a model id to
        ``{"keeper": relpath, "remove": [{"folder_id", "relpath", "path"}]}``,
        where every entry to remove is a ``present`` copy with a contained path.
        ``refused`` carries ``{"id", "reason"}``.
    """
    ids = [item.model_id for item in keep]
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

    plans: dict[int, dict] = {}
    refused: list[dict] = []
    for item in keep:
        model_id = item.model_id
        rows = copies.get(model_id, [])
        keeper = next(
            (
                row
                for row in rows
                if int(row["model_folder_id"]) == item.folder_id
                and row["relpath"] == item.relpath
            ),
            None,
        )
        # Present copies other than the keeper. `missing` and `not_downloaded`
        # rows are registrations rather than bytes, so there is nothing of theirs
        # to remove and they are left exactly as they are - a merge must not
        # rewrite a fact the scanner established.
        doomed = [
            row for row in rows if row["state"] == STATE_PRESENT and row is not keeper
        ]
        if model_id not in kinds:
            refused.append({"id": model_id, "reason": "no_such_model"})
        elif kinds[model_id] == FILE_ENGINE:
            refused.append({"id": model_id, "reason": "is_a_builtin_engine"})
        elif keeper is None:
            refused.append({"id": model_id, "reason": "no_such_copy"})
        elif keeper["state"] != STATE_PRESENT:
            # The one refusal with no counterpart on the whole-model delete, and
            # the reason this route is safe: removing every other copy on the
            # word of one the shelf cannot find would leave the owner with a
            # redownload.
            refused.append({"id": model_id, "reason": "keeper_not_present"})
        elif any(row["state"] == STATE_UNREACHABLE for row in rows):
            refused.append({"id": model_id, "reason": "unreachable_copy"})
        elif not doomed:
            refused.append({"id": model_id, "reason": "not_a_duplicate"})
        elif any(
            not deletes_unclaimed_files(row["folder_kind"], row["folder_path"])
            for row in doomed
        ):
            refused.append({"id": model_id, "reason": "not_a_user_folder"})
        else:
            try:
                kept_path = _contained_path(keeper["folder_path"], keeper["relpath"])
                removals = [
                    {
                        "model_id": model_id,
                        "folder_id": int(row["model_folder_id"]),
                        "relpath": row["relpath"],
                        "path": _contained_path(row["folder_path"], row["relpath"]),
                    }
                    for row in doomed
                ]
                if any(_same_file(copy["path"], kept_path) for copy in removals):
                    # The keeper and a doomed copy are ONE file on the disk, so
                    # removing that one takes the keeper's bytes with it and
                    # leaves a dangling link the shelf still calls `present`.
                    logger.error(
                        "Refusing to merge the copies of model %s: a copy this "
                        "would remove is the same file on the disk as the one it "
                        "would keep (%s). Nothing was touched.",
                        model_id,
                        kept_path,
                    )
                    refused.append({"id": model_id, "reason": "keeper_is_that_copy"})
                else:
                    plans[model_id] = {"keeper": keeper["relpath"], "remove": removals}
            except ValueError as exc:
                logger.error(
                    "Refusing to merge the copies of model %s: a registered copy "
                    "resolves outside its folder (%s). The row is wrong; nothing "
                    "was touched.",
                    model_id,
                    exc,
                )
                refused.append({"id": model_id, "reason": "escapes_its_folder"})
    return plans, refused


def _mark_removed(hub, copies: list[dict]) -> None:
    """Record that these copies were removed on purpose, keeping their rows.

    **The row is what the delete is for.** ``model_file`` is the record that
    these files were one model, and it is worth more than the row it costs: a
    recipe naming the copy that went still reaches this model through
    ``recipe_asset_index``, ``model_ghost_names`` still does not call that name a
    ghost, and a run through PixlStash is substituted onto the copy that stayed.
    Dropping the rows - which is what :func:`purge_deleted_models` does for the
    whole-model delete, correctly, because there the model is going too - would
    throw all three away for a few hundred bytes.

    ``state`` rather than a new column, because that column already carries
    exactly this kind of fact, and :data:`STATE_REMOVED` rather than ``missing``
    because the scanner will keep finding the file absent and ``missing`` is its
    word for "I looked and it was gone". ``seen_at`` and ``file_mtime`` are left
    alone: when we last saw the file is still true.

    **Scoped by ``model_id`` as well as by the row's primary key.** The unlink
    cannot run inside the planning transaction - a 24 GB file would hold the
    hub's write lock for the length of a disk operation - and the scanner does
    not take ``SHELF_IO_LOCK``, so in that window a scan can re-point this
    ``(folder_id, relpath)`` at a *different* model (``_upsert_model_file``
    writes ``model_id = excluded.model_id`` when the owner has replaced the
    file). Without the extra predicate this would stamp that model's row
    ``removed`` - a row saying the owner deleted a duplicate, over a file that
    was somebody else's only copy. With it the write simply matches nothing and
    the next scan is the authority, which is the same direction
    :func:`~pixlstash.services.model_shelf_service.purge_deleted_models` fails
    in.
    """
    with hub.transaction() as conn:
        for copy in copies:
            conn.execute(
                "UPDATE model_file SET state = ? "
                "WHERE model_folder_id = ? AND relpath = ? AND model_id = ?",
                (STATE_REMOVED, copy["folder_id"], copy["relpath"], copy["model_id"]),
            )


def _comfyui_reads(comfyui_url: Optional[str], plans: dict[int, dict]) -> list[dict]:
    """Which copies about to go a configured ComfyUI says it can load.

    **Asked of ComfyUI, not of the filesystem.** PixlStash holds a URL and no
    path into that install's ``models/`` tree, and its combo lists are the truth
    anyway: they already account for ``extra_model_paths.yaml``, symlinks and
    whatever else put the file within its reach. This is the delete-time half of
    the rule the submit-time swap obeys - verify against what ComfyUI
    advertises, never against what PixlStash believes.

    An unset URL or an unreachable ComfyUI answers "nothing", and the empty list
    is reported as exactly that by the client: the absence of a warning here is
    not a promise that no ComfyUI reads the file.
    """
    if not comfyui_url:
        return []
    try:
        advertised = advertised_model_names(fetch_object_info(comfyui_url))
    except RuntimeError as exc:
        logger.info(
            "Could not ask ComfyUI at %s which models it reads, so the merge "
            "warns about none of them: %s",
            comfyui_url,
            exc,
        )
        return []
    reads: list[dict] = []
    for model_id, plan in plans.items():
        keeper_advertised = _is_advertised(plan["keeper"], advertised)
        for copy in plan["remove"]:
            if not _is_advertised(copy["relpath"], advertised):
                continue
            reads.append(
                {
                    "model_id": model_id,
                    "folder_id": copy["folder_id"],
                    "relpath": copy["relpath"],
                    "keeper_relpath": plan["keeper"],
                    "keeper_advertised": keeper_advertised,
                }
            )
    return reads


def _is_advertised(relpath: str, advertised: set[str]) -> bool:
    """Whether ComfyUI lists this registered copy, by relpath or by basename.

    Both, because a combo entry is relative to one of ComfyUI's own model
    folders and a registered folder's relpath is relative to a root PixlStash
    was given: ``loras/x.safetensors`` here and ``x.safetensors`` there are
    routinely the same file.
    """
    normalized = relpath.replace("\\", "/")
    return normalized in advertised or normalized.rsplit("/", 1)[-1] in advertised


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

    @router.post(
        "/model-files/merge",
        summary="Keep one copy of a model and remove the rest",
        description=(
            "For a model the shelf holds several copies of - the same bytes "
            "under one name or two - keeps the copy named in `keep` and removes "
            "every other copy that is on the disk. `permanent=false` (the "
            "default) moves them to this machine's "
            f"{TRASH_NAME.lower()}; `permanent=true` unlinks them. "
            "`dry_run=true` plans it and removes nothing, which is how a client "
            "shows the refusals and `comfyui_reads` before the owner agrees.\n\n"
            "**The shelf row survives, and so does every removed copy's row**, "
            "at `state = 'removed'`: the record of which files were the same "
            "model outlives the files, so a recipe naming the copy that went "
            "still resolves to this model on screen, and a run through "
            "PixlStash is substituted onto the copy that is left - verified "
            "against what that ComfyUI advertises, never assumed. The stored "
            "workflow is left byte-identical; nothing is rewritten at delete "
            "time. A graph opened in ComfyUI and queued there is the honest "
            "limit, which is what `comfyui_reads` warns about.\n\n"
            "The keeper is what the request names, so no body can empty a "
            "model, and it is refused unless the shelf has it as `present`. "
            "Only the copies being REMOVED must sit in a folder whose contents "
            "are yours, so keeping the one in the shared HuggingFace cache and "
            "removing a user-folder copy is a legitimate merge."
        ),
        tags=["model_shelf"],
        response_model=MergeCopiesResponse,
    )
    def merge_model_copies(request: Request, payload: MergeCopiesRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        # One keeper per model, and two entries for one model is refused rather
        # than picked from: planning it twice would remove its copies and then
        # plan it again against rows that had just gone, and silently keeping the
        # first would make a destructive choice on a confused client's behalf and
        # report nothing.
        keep: dict[int, KeepModelCopy] = {}
        for item in payload.keep:
            if item.model_id in keep:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Model {item.model_id} is named twice with different "
                        "copies to keep. One keeper per model."
                    ),
                )
            keep[item.model_id] = item
        user = server.auth.get_user_for_request(request)
        comfyui_url = (getattr(user, "comfyui_url", None) or "").rstrip("/")

        # The *same* slot a move, an import, an add and the delete take.
        if not SHELF_IO_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A move or an import is already running. Removing copies "
                    "out from under it would leave rows naming files neither of "
                    "us put there."
                ),
            )
        try:
            plans, refused = _plan_merge(server.hub, list(keep.values()))
            # Asked before anything is removed, so a dry run and the real call
            # report the same warning about the same files.
            reads = _comfyui_reads(comfyui_url, plans)
            merged: list[int] = []
            files_removed = 0
            if not payload.dry_run:
                for model_id, plan in plans.items():
                    done = 0
                    paths = [copy["path"] for copy in plan["remove"]]
                    try:
                        for path in paths:
                            _remove(path, permanent=payload.permanent)
                            done += 1
                            # After the file, and never allowed to fail it.
                            _remove_samples(path, permanent=payload.permanent)
                    except (TrashPermissionError, OSError) as exc:
                        reason = (
                            "partly_deleted"
                            if done
                            else (
                                "trash_unavailable"
                                if isinstance(exc, TrashPermissionError)
                                else "delete_failed"
                            )
                        )
                        logger.error(
                            "Could not remove %s (%s). Model %s keeps every copy "
                            "it still has; the %d of its %d redundant copies that "
                            "did go are recorded as removed.",
                            paths[done],
                            exc,
                            model_id,
                            done,
                            len(paths),
                            exc_info=not isinstance(exc, TrashPermissionError),
                        )
                        # The copies that went are recorded even though the model
                        # is refused. They are gone either way, and leaving them
                        # `present` would draw the owner a broken row for a file
                        # they successfully removed - and lose, for exactly those
                        # copies, the record the whole design rests on.
                        _mark_removed(server.hub, plan["remove"][:done])
                        refused.append({"id": model_id, "reason": reason})
                    else:
                        merged.append(model_id)
                        _mark_removed(server.hub, plan["remove"])
                    finally:
                        files_removed += done
        finally:
            SHELF_IO_LOCK.release()

        logger.info(
            "Merged the copies of %d model(s) (%d file(s) %s), %d refused; "
            "%d of the removed copies were advertised by ComfyUI.",
            len(merged),
            files_removed,
            "unlinked" if payload.permanent else "trashed",
            len(refused),
            len(reads),
        )
        return MergeCopiesResponse(
            merged=sorted(merged),
            files_removed=files_removed,
            permanent=payload.permanent,
            dry_run=payload.dry_run,
            comfyui_reads=[ComfyUIReadsCopy(**item) for item in reads],
            refused=[DeleteRefusal(**item) for item in refused],
        )

    return router
