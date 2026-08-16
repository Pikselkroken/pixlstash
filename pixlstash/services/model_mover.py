"""Move a model's file from one registered folder to another, without losing it.

The whole module exists for one ordering, and the ordering is not negotiable:

    **copy → verify by SHA-256 → repoint the row and commit → then unlink.**

Any other order has a crash window that ends with a ``model_file`` row naming a
path where no file is. Unlink first and a crash between the two leaves the row
pointing at nothing and the bytes gone. Commit first and a crash leaves the row
pointing at a destination that was never written. Only this order has the
property that **every** interruption leaves the file readable at at least one of
the two paths, and every ``model_file`` row still naming a file that exists:

===============================  =========================  ==================
Interrupted…                     On disk                    The row names
===============================  =========================  ==================
after the copy, before commit    both paths                 the **source**
after commit, before the unlink  both paths                 the **destination**
===============================  =========================  ==================

Both residues are a *duplicate*, which the shelf already models — one ``model``
row with two ``model_file`` rows is what a file copied into two registered
folders is, and a **manual** rescan of either folder reconciles it. Neither
residue is a dangling row. That is the acceptance bar (shelf plan §6 item 3),
and ``tests/test_model_move.py`` interrupts both windows to prove it.

**"A rescan fixes it" means the owner presses the button.** ``ModelFolderScanner``
has exactly one caller, ``POST /model-folders/{id}/rescan``; nothing scans a
model folder on start or on a schedule. So every reconciliation claim below is a
claim about a *repair the owner can make*, not about one that happens by itself.
The residues are chosen so that waiting costs nothing — a duplicate is
serviceable, a dangling row is not — but nobody should read them as self-healing.

**Same-drive is a rename and skips all of it**, per the ruling. Nothing is
copied, nothing is verified and no space is needed, because no second copy is
made. Its residue is narrower but not identical: ``os.rename`` is atomic, so a
crash between the rename and the commit leaves the file only at the destination
with the row still naming the source — a ``missing`` row and an unregistered
file, which a manual rescan of either folder repairs by content, because
``model`` is keyed by sha256 and the row keeps its curation. The window is the
microseconds between two syscalls rather than the minutes a 24 GB copy takes,
which is the trade the ruling makes.

**Durability: the hub runs ``PRAGMA synchronous=NORMAL``** (``hub/db.py``), so
"the row is committed" means committed to the WAL without an fsync, and a power
loss — not a process kill, which WAL survives — can lose the last commit. The
consequence here is *milder* than the ordering it sits inside, not worse: the
unlink that follows a lost commit has already removed the source, so what
survives is the bytes at the **destination** with no row naming them. That is an
unregistered file a rescan re-links by content with its curation intact — the
same residue the same-drive rename window already accepts, and never a row
naming a file that is gone. Raising the pragma to ``FULL`` would fsync every
tiny hub write in the product to narrow that one window, which is not the trade.

**One move or import at a time, machine-wide** (:data:`SHELF_IO_LOCK`), and the
destination is re-checked at execution time rather than trusted from the plan.
Both are below, with the reasoning.

**Space is checked before the first byte**, and for the whole batch at once:
:meth:`ModelMover.plan` refuses a job that would not fit rather than filling the
disk and failing on file 1,500 of 1,806.

**Cancel stops the queue and rolls nothing back.** It is checked between files,
never inside one: a half-copied file with a verified digest does not exist, and
abandoning a file mid-copy would leave exactly the partial that the ``.partial``
suffix and the verify step are there to prevent.

Containment (#776): this module both **writes** and **unlinks**, which is
precisely the class §13 "Stored path containment" says to contain. Every
destination is resolved with ``resolve_path_within`` against the *destination*
``model_folder.path`` and every source against its *own* ``model_folder.path``,
so a ``model_file.relpath`` that a faulty scan, a restored hub or a bug put in
the table cannot make this write outside a registered folder or unlink outside
one. Reads are not contained anywhere else in the product and are not contained
here either; what is contained is the ``open(…, "wb")`` and the ``os.unlink``.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_folder_scanner import STATE_PRESENT
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

_COPY_CHUNK_BYTES = 1024 * 1024

# Written next to the destination, in the destination directory, so the
# ``os.replace`` onto the final name is a rename within one filesystem and
# therefore atomic. A crash while this file is being written leaves a `.partial`
# that no ``model_file`` row names and that the scanner ignores (it is not a
# ``.safetensors``), rather than a truncated model at the real name.
PARTIAL_SUFFIX = ".pixlstash-partial"

# The same 10 % headroom the picture import uses. A destination filled to the
# last byte is a destination that cannot be written to again.
_SPACE_HEADROOM = 1.1

STATUS_MOVED = "moved"
STATUS_COPIED = "copied"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

# **One shelf file operation at a time, machine-wide — a move and an import
# included.** They used to hold separate locks, which serialized each against
# itself and *nothing* against the other: a move planned at 12:00 and an import
# started at 12:01 could both decide the same destination filename was free,
# and whichever wrote second won silently. Both are I/O-bound on one disk and
# both space-check the destination up front, so there is nothing to gain from
# overlapping them anyway.
#
# **The loser fails cleanly with 409 and never retries.** Queueing would mean
# the plan the caller was shown — free space, collisions, what is on disk — had
# been validated against a filesystem the other operation was still changing,
# and silently re-planning under the caller is worse than telling them to press
# it again.
SHELF_IO_LOCK = threading.Lock()


class MoveRefused(ValueError):
    """The batch was refused before anything was written.

    Raised only by :meth:`ModelMover.plan`, which runs entirely before the first
    byte, so a refusal means the disk was not touched at all. The route maps it
    to a 4xx; ``status_code`` says which.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class PlannedMove:
    """One file, resolved to absolute paths and checked for containment."""

    model_id: int
    source_folder_id: int
    source_relpath: str
    source_path: str
    destination_relpath: str
    destination_path: str
    sha256: Optional[str]
    """``model.sha256``, or None for a checkpoint nobody has hashed yet."""
    size: int
    same_device: bool
    """True when the move is a rename and no bytes are copied."""


@dataclass
class MovePlan:
    """A validated batch, ready to execute. Nothing here has touched the disk."""

    destination_folder_id: int
    destination_path: str
    moves: list[PlannedMove]
    bytes_to_copy: int
    """Total size of the cross-device moves only, samples directories included;
    a rename copies nothing."""
    skipped: list["MoveOutcome"] = field(default_factory=list)
    """Items already in the destination folder: nothing to do, but still
    *reported*. Dropping them silently left a caller unable to reconcile the
    items it asked for against the results it got back."""

    @property
    def total(self) -> int:
        """Every item the caller named, decided or not. What ``total`` means."""
        return len(self.moves) + len(self.skipped)


@dataclass
class MoveOutcome:
    """What happened to one file."""

    source_folder_id: int
    source_relpath: str
    status: str
    detail: Optional[str] = None


@dataclass
class MoveReport:
    """What happened to the batch."""

    outcomes: list[MoveOutcome] = field(default_factory=list)
    cancelled: bool = False

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for outcome in self.outcomes:
            tally[outcome.status] = tally.get(outcome.status, 0) + 1
        return tally


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def same_device(source_path: str, destination_dir: str) -> bool:
    """Whether *source_path* and *destination_dir* sit on one filesystem.

    ``st_dev``, not a path-prefix comparison: a bind mount, a symlinked folder
    and two subdirectories of one mount all look different by path and are the
    same device, and two paths under one root can be different devices when a
    mount sits between them. Getting this wrong in the "same" direction would
    make ``os.rename`` raise ``EXDEV``; getting it wrong in the "different"
    direction only costs a needless copy.

    Its own function so a test can force the copy path on a machine where
    ``tmp_path`` puts both directories on one filesystem, which is every machine
    this suite runs on.
    """
    return os.stat(source_path).st_dev == os.stat(destination_dir).st_dev


def copy_and_digest(source_path: str, destination_path: str) -> str:
    """Copy the file and return the SHA-256 of the bytes that were *read*.

    One pass. Hashing the source in a separate read would double the cost of a
    24 GB checkpoint for no extra assurance: what has to be proved is that the
    bytes which arrived are the bytes that left, and the destination is read back
    separately for exactly that comparison.

    Mode and timestamps are copied too. The scanner's re-hash short circuit
    compares ``st_mtime_ns`` against the stored value, so a move that reset the
    mtime would make the next scan re-read every byte it just moved.
    """
    digest = hashlib.sha256()
    with open(source_path, "rb") as source, open(destination_path, "wb") as destination:
        while chunk := source.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
            destination.write(chunk)
    shutil.copystat(source_path, destination_path)
    return digest.hexdigest()


def file_digest(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_COPY_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def unlink_source(source_path: str) -> None:
    """The last step of a move or an import, and only ever the last step.

    Its own function so the crash-window test has a seam to interrupt exactly
    here — between a durable commit and the removal that commit authorises —
    without patching ``os.unlink`` for the whole process.
    """
    os.unlink(source_path)


def discard_partial(partial: str) -> None:
    """Remove a copy that never verified. Never raises: the caller is already
    reporting a failure and a cleanup error must not replace it."""
    try:
        os.unlink(partial)
    except FileNotFoundError:
        # The copy never got as far as creating it. Nothing to clean up.
        logger.debug("No partial copy at %s to discard.", partial)
    except OSError as exc:
        logger.warning(
            "Could not remove the partial copy %s: %s. It is inert — no row "
            "names it and the scanner ignores it — but it is occupying disk.",
            partial,
            exc,
        )


def discard_partial_tree(partial: str) -> None:
    """Remove a directory copy that never completed. Never raises.

    The directory counterpart of :func:`discard_partial`, and quiet for the same
    reason: the caller is already reporting a failure and a cleanup error must
    not replace it. Whatever is left behind is inert — it carries the
    ``.pixlstash-partial`` suffix, so no row names it and nothing loads it — but
    it is occupying disk, which is why the failure is logged rather than passed.
    """

    try:
        shutil.rmtree(partial)
    except FileNotFoundError:
        # The copy never got as far as creating it. Nothing to clean up.
        logger.debug("No partial copy at %s to discard.", partial)
    except OSError as exc:
        logger.warning(
            "Could not remove the partial copy %s: %s. It is inert — no row "
            "names it and nothing loads it — but it is occupying disk.",
            partial,
            exc,
        )


def move_directory(source_path: str, destination_path: str) -> None:
    """Move a whole directory so that a *complete* copy survives any interruption.

    The pack-shaped counterpart of this module's per-file ordering, for the one
    root whose registered rows are directories rather than files: an InsightFace
    pack is ``scrfd_10g_bnkps.onnx`` plus ``glintr100.onnx`` and the shelf
    catalogues the pack, not the files. The ordering is the same shape and has
    the same property — **copy → rename into place → then remove the source** —
    so every interruption leaves the pack loadable at the source, at the
    destination, or (harmlessly) at both. The intermediate copy carries
    :data:`PARTIAL_SUFFIX`, so a crash mid-copy can never leave a
    half-populated directory under the name ``FaceAnalysis`` loads, which would
    be a face pipeline that starts and then fails on a missing model.

    **No SHA-256 verify, unlike a model file.** These rows have no ``sha256`` to
    compare against — the packs are declared from a directory listing, never
    hashed — so there is nothing to verify against, and ``copytree`` raises on
    the I/O errors a digest would be catching. Re-running the relocation is the
    repair, and the packs are re-downloadable besides.

    Args:
        source_path: The directory to move. Removed once the copy has landed.
        destination_path: Where it goes. Must not exist; its parent must.

    Raises:
        OSError: The copy or the removal failed. Nothing is left under
            *destination_path* itself when it does.
    """
    if same_device(source_path, os.path.dirname(destination_path)):
        # A rename, exactly as the file path does it: atomic, nothing copied,
        # no space needed.
        os.rename(source_path, destination_path)
        return

    partial = destination_path + PARTIAL_SUFFIX
    try:
        shutil.copytree(source_path, partial)
        os.rename(partial, destination_path)
    except BaseException:
        discard_partial_tree(partial)
        raise
    shutil.rmtree(source_path)


# A trained checkpoint's previews sit beside it, in a directory named from its
# own stem: ``JimmyBuss_0001500.safetensors`` -> ``JimmyBuss_0001500_samples/``.
# On disk in the folder rather than in a hub store, so a person opening that
# folder sees what each file looked like, and so nothing has to be migrated when
# they move the file somewhere PixlStash is not looking.
SAMPLES_DIR_SUFFIX = "_samples"


def samples_relpath(model_relpath: str) -> str:
    """Where one model file's training previews sit, derived from its own name.

    Takes a relpath or a full path and answers in kind, keeping any directory
    part: a whole-folder relocation moves ``runA/model.safetensors``, and its
    previews are ``runA/model_samples/``, not a directory at the root.
    """
    return os.path.splitext(model_relpath)[0] + SAMPLES_DIR_SUFFIX


def samples_size(model_path: str) -> int:
    """Bytes of a model file's samples directory, or 0 when it has none.

    Counted into the space check because a run measured 1.9 GB of which
    ``samples/`` was 15 MB: small against the weights, and not nothing when the
    destination is nearly full.
    """
    directory = samples_relpath(model_path)
    if not os.path.isdir(directory):
        return 0
    total = 0
    for root, _dirs, files in os.walk(directory):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError as exc:
                logger.warning(
                    "Could not size %s while counting the samples of %s: %s. "
                    "The space check is short by that file.",
                    os.path.join(root, name),
                    model_path,
                    exc,
                )
    return total


def carry_samples(
    source_path: str, destination_path: str, *, delete_source: bool = True
) -> Optional[str]:
    """Take a model file's previews with it, or say why they did not go.

    **Non-fatal by construction, and that is the ruling**: losing a preview must
    not cost the weights. The caller has already committed the row that names
    the file at its new home, so a failure here is reported in the outcome's
    ``detail`` and the file stays moved.

    Ordered by the caller — after the row commits and before the source is
    unlinked — so ``delete_after_import`` can never outrun the copy.

    Args:
        source_path: The model file at its old location. Its samples directory
            is derived from it and may not exist, which is the common case.
        destination_path: The model file at its new location, already contained
            against the destination folder. The samples directory is derived
            from it and must not exist.
        delete_source: True moves the directory, matching a move; False copies
            it, matching a copy-in, where the original file stays too.

    Returns:
        None when there was nothing to carry or it was carried, or a short
        message for the outcome's ``detail`` when it was not.
    """
    source_dir = samples_relpath(source_path)
    if not os.path.isdir(source_dir):
        return None
    destination_dir = samples_relpath(destination_path)
    if os.path.lexists(destination_dir):
        # Refused rather than merged or replaced, for the reason the importer
        # refuses the same name: there is no undo for shelf operations. It is
        # also what makes the two platforms agree — ``os.rename`` over an
        # **empty** existing directory silently replaces it on POSIX and raises
        # on Windows, so without this check the owner's empty directory is
        # destroyed on four CI shards' worth of Linux and preserved on the other
        # four. Checking first makes it preserved everywhere.
        logger.warning(
            "Not carrying the samples of %s: %s is already there. The model "
            "moved; its previews stayed behind rather than being written over.",
            os.path.basename(source_path),
            destination_dir,
        )
        return (
            f"Samples were not carried: {os.path.basename(destination_dir)} "
            "already exists at the destination."
        )
    try:
        if delete_source:
            move_directory(source_dir, destination_dir)
        else:
            partial = destination_dir + PARTIAL_SUFFIX
            try:
                shutil.copytree(source_dir, partial)
                os.rename(partial, destination_dir)
            except BaseException:
                discard_partial_tree(partial)
                raise
    except OSError as exc:
        logger.error(
            "Could not carry the samples of %s from %s to %s: %s. The model "
            "itself is registered at its new location; only the previews stayed "
            "behind.",
            os.path.basename(source_path),
            source_dir,
            destination_dir,
            exc,
            exc_info=True,
        )
        return f"Samples were not carried from {os.path.basename(source_dir)}: {exc}"
    return None


def require_space(destination_path: str, bytes_to_copy: int) -> None:
    """Check free space **before the first byte**, for the whole batch.

    Per file would fill the disk and fail on file 1,500 of 1,806, having
    already moved 1,499 — and there is no undo.
    """
    if bytes_to_copy <= 0:
        return
    try:
        free = shutil.disk_usage(destination_path).free
    except OSError as exc:
        logger.error(
            "Could not read free space on %s: %s. Refusing the move rather "
            "than starting a copy that may not fit.",
            destination_path,
            exc,
        )
        raise MoveRefused(
            f"Could not read free space on {destination_path}.",
            status_code=507,
        ) from exc
    required = int(bytes_to_copy * _SPACE_HEADROOM)
    if required > free:
        raise MoveRefused(
            f"Not enough space in {destination_path}: the move needs "
            f"{required / 1024**3:.2f} GB including 10 % headroom and "
            f"{free / 1024**3:.2f} GB is free. Nothing was moved.",
            status_code=507,
        )


class ModelMover:
    """Relocate ``model_file`` rows and the files they name, in that order.

    Two phases on purpose. :meth:`plan` resolves, contains and space-checks the
    whole batch and writes nothing, so a refusal is an immediate error to the
    caller rather than a background job that dies on file 1,500. :meth:`execute`
    is the part that takes minutes and belongs on a thread.
    """

    def __init__(self, hub: HubDatabase) -> None:
        """Bind the mover to an open hub.

        Args:
            hub: The hub database. Only the model-shelf tables are touched.
        """
        self._hub = hub

    # -- planning ---------------------------------------------------------

    def plan(
        self,
        items: list[tuple[int, str]],
        destination_folder_id: int,
        *,
        flatten: bool = True,
        relocating: bool = False,
    ) -> MovePlan:
        """Resolve and check a batch without writing anything.

        Args:
            items: ``(model_folder.id, model_file.relpath)`` pairs — the
                ``model_file`` primary key, which is what the list response
                already gives the client for every copy of every row.
            destination_folder_id: Where they go. Must be registered, and must
                be a folder that is catalogued in place: a ``source`` folder is
                an ai-toolkit output root, taken *from*, never written into.
            flatten: True — the default, and what dropping files onto a folder
                means — lands every file at its basename. False keeps each
                file's ``relpath`` verbatim, subdirectories and all, which is
                what **relocating a whole folder** needs: a store holding
                ``runA/model.safetensors`` and ``runB/model.safetensors`` is not
                two files fighting over one name, it is a tree that has to
                arrive as a tree. Flattening it would refuse the relocation
                outright (a collision that no "move them separately" can
                resolve, because there is no such verb) or, with one such file,
                silently drop the subdirectory.
            relocating: True only for a whole-folder relocation, which is the
                one legitimate way files leave a ``root_only`` folder — the
                folder is going with them. It is a separate flag from
                ``flatten`` on purpose: ``flatten`` is about the shape of the
                destination path, this is about authority, and welding the two
                together would mean any future caller wanting a tree copy
                silently inherited the right to empty the HuggingFace cache.
                ``POST /model-folders/{id}/relocate`` is the only caller, and
                ``relocatable_identity`` has already refused every folder but
                the two that relocate — the managed store and PixlStash's own
                download folder — before it gets here.

        Returns:
            The validated :class:`MovePlan`.

        Raises:
            MoveRefused: The destination is unusable, an item names no row, a
                path would escape its registered folder, or the copy would not
                fit. Nothing has been written when this is raised.
        """
        destination = self._folder(destination_folder_id)
        if destination is None:
            raise MoveRefused("No such destination folder.", status_code=404)
        if destination["kind"] == "source":
            raise MoveRefused(
                "A source folder is an ai-toolkit output root: it is taken from, "
                "never written into. Pick a folder the shelf catalogues.",
            )
        destination_path = destination["path"]
        if not os.path.isdir(destination_path):
            raise MoveRefused(
                f"Destination folder {destination_path} is not a readable "
                "directory right now, so nothing was moved.",
                status_code=409,
            )

        moves: list[PlannedMove] = []
        skipped: list[MoveOutcome] = []
        claimed: set[str] = set()
        for folder_id, relpath in items:
            move = self._plan_one(
                folder_id,
                relpath,
                destination_folder_id,
                destination_path,
                flatten=flatten,
                relocating=relocating,
            )
            if move is None:
                skipped.append(
                    MoveOutcome(
                        folder_id,
                        relpath,
                        STATUS_SKIPPED,
                        "Already in the destination folder.",
                    )
                )
                continue
            if move.destination_relpath in claimed:
                raise MoveRefused(
                    f"Two files in this batch would both land on "
                    f"{move.destination_relpath!r}. Nothing was moved; move them "
                    "separately or rename one first."
                )
            claimed.add(move.destination_relpath)
            moves.append(move)

        # The samples directory travels with the file, so its bytes are part of
        # what has to fit. Same-device moves are renames and copy nothing, here
        # as for the file itself.
        bytes_to_copy = sum(
            move.size + samples_size(move.source_path)
            for move in moves
            if not move.same_device
        )
        require_space(destination_path, bytes_to_copy)
        logger.info(
            "Planned a move of %d file(s) into %s: %d byte(s) to copy, %d "
            "rename(s), %d already there.",
            len(moves),
            destination_path,
            bytes_to_copy,
            sum(1 for move in moves if move.same_device),
            len(skipped),
        )
        return MovePlan(
            destination_folder_id=destination_folder_id,
            destination_path=destination_path,
            moves=moves,
            bytes_to_copy=bytes_to_copy,
            skipped=skipped,
        )

    def _plan_one(
        self,
        folder_id: int,
        relpath: str,
        destination_folder_id: int,
        destination_path: str,
        *,
        flatten: bool,
        relocating: bool = False,
    ) -> Optional[PlannedMove]:
        row = self._hub.fetchone(
            "SELECT mf.model_id, mf.state, m.sha256, m.file_size, f.path AS "
            "folder_path, f.movable AS folder_movable FROM model_file mf "
            "JOIN model m ON m.id = mf.model_id "
            "JOIN model_folder f ON f.id = mf.model_folder_id "
            "WHERE mf.model_folder_id = ? AND mf.relpath = ?",
            (folder_id, relpath),
        )
        if row is None:
            raise MoveRefused(
                f"No registered copy at {relpath!r} in folder {folder_id}.",
                status_code=404,
            )
        # **Two values, and the pair is the point.** `root_only` says the folder
        # relocates as a whole — PixlStash's own downloads and the InsightFace
        # packs. `fixed` says it cannot relocate at all, because another tool
        # owns where it lives — the HuggingFace cache. Neither permits a
        # per-item move out, so keying on one would leave the other open, which
        # is exactly how a rename of this vocabulary could silently drop the
        # protection. Refused here, at the single point every move funnels
        # through, rather than by each caller remembering to ask.
        #
        # The cache is why this is a containment site and not a tidiness rule.
        # It is not a folder of files: it is `blobs/` under content hashes with
        # `snapshots/` symlinking names onto them, it is shared with every other
        # HF tool on the machine, and a row's relpath there is a whole repo
        # DIRECTORY. Moving one does not relocate a model, it breaks
        # HuggingFace's bookkeeping for ComfyUI and everything else too. Its
        # real control is `HF_HOME`, read at import: a restart and a
        # re-download, not a move — which is the distinction `fixed` exists to
        # record.
        if row["folder_movable"] in ("root_only", "fixed") and not relocating:
            raise MoveRefused(
                f"{relpath!r} is inside a folder whose files are not moved one "
                "at a time, so nothing was moved. PixlStash's own model folders, "
                "the InsightFace packs and the HuggingFace cache are listed so "
                "you can see what they cost on disk, not to be rearranged: the "
                "cache in particular is a symlink store shared with your other "
                "tools, and moving a file out of it corrupts it for all of them "
                "rather than relocating anything.",
                status_code=409,
            )
        if folder_id == destination_folder_id:
            # Not an error: the shelf lets a user drop a mixed selection onto a
            # folder, and the files already in it are simply nothing to do. The
            # caller still hears about them — ``plan`` turns this ``None`` into
            # a ``skipped`` outcome — because an item that vanishes between the
            # request and the results is one the client cannot account for.
            return None

        try:
            source_path = resolve_path_within(row["folder_path"], relpath)
        except ValueError as exc:
            # The unlink is what makes this a containment site. A relpath that
            # escapes its folder is a broken row, not a request to delete
            # somebody's file outside the shelf.
            logger.error(
                "Refusing to move %r out of registered folder %s: it resolves "
                "outside it (%s). The row is wrong; nothing was touched.",
                relpath,
                row["folder_path"],
                exc,
            )
            raise MoveRefused(
                f"{relpath!r} resolves outside its registered folder."
            ) from exc

        # Flattened to the basename when a *selection* is dropped onto a folder:
        # that means "put it in that folder", not "recreate three levels of
        # somebody else's tree in it". A collision is refused below, never
        # silently overwritten. A whole-folder relocation passes
        # ``flatten=False`` and keeps the tree, because there the tree *is* the
        # thing being moved.
        #
        # The containment that follows is **reachable and load-bearing in both
        # modes**, not a sanitizer. ``basename`` neutralises ``..`` but not a
        # *symlink* standing at the destination filename: ``resolve_path_within``
        # calls ``realpath``, so a dangling ``dest/alice.safetensors ->
        # /elsewhere/alice.safetensors`` is refused here — and only here, since
        # ``os.path.exists`` is False for a dangling link and the collision check
        # below would wave it through, straight into an ``os.replace`` that
        # writes outside the registered folder. Asserted at both ends in
        # ``tests/test_model_move.py``.
        destination_relpath = os.path.basename(relpath) if flatten else relpath
        try:
            resolved_destination = resolve_path_within(
                destination_path, destination_relpath
            )
        except ValueError as exc:
            logger.error(
                "Refusing to write %r into %s: it resolves outside the "
                "destination folder (%s).",
                destination_relpath,
                destination_path,
                exc,
            )
            raise MoveRefused(
                f"{destination_relpath!r} would be written outside the "
                "destination folder."
            ) from exc

        if not os.path.exists(source_path):
            raise MoveRefused(
                f"{relpath!r} is registered but is not on disk (state "
                f"{row['state']!r}), so there is nothing to move.",
                status_code=409,
            )
        self._check_destination_free(
            resolved_destination, destination_folder_id, destination_relpath
        )

        return PlannedMove(
            model_id=int(row["model_id"]),
            source_folder_id=folder_id,
            source_relpath=relpath,
            source_path=source_path,
            destination_relpath=destination_relpath,
            destination_path=resolved_destination,
            sha256=row["sha256"],
            size=int(os.path.getsize(source_path)),
            same_device=same_device(source_path, destination_path),
        )

    def _check_destination_free(
        self,
        destination: str,
        destination_folder_id: int,
        destination_relpath: str,
    ) -> None:
        """Refuse rather than overwrite a file the caller did not name.

        A move that clobbers is a move that destroys data the shelf never
        offered to touch, and there is no undo for shelf operations.

        Run **twice**: once in :meth:`plan`, so a doomed batch is a 4xx to the
        caller rather than a background job, and again in :meth:`_move_one`
        immediately before the write, because the plan ran in the POST and the
        write runs minutes later on the worker thread. ``SHELF_IO_LOCK`` keeps
        the other shelf operation out of that gap; the owner, ComfyUI or a
        trainer is not under any lock of ours, and both ``os.replace`` and
        ``os.rename`` overwrite in silence.
        """
        if os.path.exists(destination):
            raise MoveRefused(
                f"{destination_relpath} already exists in the destination "
                "folder. Nothing was moved."
            )
        existing = self._hub.fetchone(
            "SELECT model_id FROM model_file WHERE model_folder_id = ? AND relpath = ?",
            (destination_folder_id, destination_relpath),
        )
        if existing is not None:
            # A row with no file: stale. Repointing onto its key would violate
            # UNIQUE(model_folder_id, relpath) at commit time, and when the row
            # belongs to a different model it would also delete that model's
            # only known location. Let a rescan clear it instead.
            raise MoveRefused(
                f"{destination_relpath} is registered to model "
                f"{existing['model_id']} in the destination folder. Rescan that "
                "folder first."
            )

    # -- execution --------------------------------------------------------

    def execute(
        self,
        plan: MovePlan,
        *,
        delete_source: bool = True,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_progress: Optional[Callable[[MoveOutcome], None]] = None,
    ) -> MoveReport:
        """Carry out a planned batch, one file at a time.

        Args:
            plan: The result of :meth:`plan`.
            delete_source: False leaves the original in place, which makes this
                a register-a-second-copy rather than a move. No route passes it
                today — the ai-toolkit import writes its own rows, because it
                creates the ``model`` as well as the ``model_file``.
            should_cancel: Consulted **between** files. Cancelling stops the
                queue and rolls nothing back: the files already moved are moved,
                which is the ruling, and is also the only answer that does not
                need a second crash-window argument for the rollback.
            on_progress: Called with each :class:`MoveOutcome` as it is decided.

        Returns:
            A :class:`MoveReport` with one outcome per planned file.
        """
        report = MoveReport()
        # The already-there items lead, because they were decided in ``plan``
        # and are the only outcomes a cancel can never touch. Reported rather
        # than dropped: a caller has to be able to match what it asked for
        # against what came back.
        for outcome in plan.skipped:
            report.outcomes.append(outcome)
            if on_progress is not None:
                on_progress(outcome)
        for index, move in enumerate(plan.moves):
            if should_cancel is not None and should_cancel():
                report.cancelled = True
                # Indexed off ``plan.moves``, not off ``len(report.outcomes)``,
                # which also counts the skipped items.
                for remaining in plan.moves[index:]:
                    report.outcomes.append(
                        MoveOutcome(
                            remaining.source_folder_id,
                            remaining.source_relpath,
                            STATUS_CANCELLED,
                        )
                    )
                break
            outcome = self._move_one(move, plan, delete_source=delete_source)
            report.outcomes.append(outcome)
            if on_progress is not None:
                on_progress(outcome)
        logger.info(
            "Move into %s finished: %s%s.",
            plan.destination_path,
            report.counts(),
            " (cancelled)" if report.cancelled else "",
        )
        return report

    def _move_one(
        self, move: PlannedMove, plan: MovePlan, *, delete_source: bool
    ) -> MoveOutcome:
        try:
            self._check_destination_free(
                move.destination_path,
                plan.destination_folder_id,
                move.destination_relpath,
            )
        except MoveRefused as exc:
            logger.warning(
                "Refusing to move %s -> %s: the destination stopped being free "
                "between planning and now (%s). The source is untouched.",
                move.source_path,
                move.destination_path,
                exc,
            )
            return MoveOutcome(
                move.source_folder_id, move.source_relpath, STATUS_FAILED, str(exc)
            )
        try:
            # ``flatten=False`` moves a tree, so the destination subdirectory
            # may not exist yet. A no-op for the flattened case.
            os.makedirs(os.path.dirname(move.destination_path), exist_ok=True)
            if move.same_device and delete_source:
                return self._rename(move, plan)
            return self._copy_verify_repoint_unlink(
                move, plan, delete_source=delete_source
            )
        except (OSError, sqlite3.IntegrityError) as exc:
            logger.error(
                "Moving %s -> %s failed: %s. The source is untouched.",
                move.source_path,
                move.destination_path,
                exc,
                exc_info=True,
            )
            return MoveOutcome(
                move.source_folder_id, move.source_relpath, STATUS_FAILED, str(exc)
            )

    def _rename(self, move: PlannedMove, plan: MovePlan) -> MoveOutcome:
        """Same filesystem: one atomic syscall, then repoint.

        No copy, no verify and no space check, because no second copy is made —
        the ruling. The residue of a crash between the two steps is a file at the
        destination that no row names plus a row that will read ``missing``, and
        a manual rescan of either folder re-links it by content with its
        curation intact. That is narrower than the copy path's guarantee, and it
        is bought with a window measured in syscalls rather than in minutes of
        I/O.
        """
        os.rename(move.source_path, move.destination_path)
        try:
            self._repoint(move, plan)
        except sqlite3.IntegrityError:
            # Somebody registered this destination key between the check above
            # and this commit — a rescan, which is deliberately not under
            # SHELF_IO_LOCK. Put the file back: the row still names the source,
            # and leaving it renamed away is the dangling row this whole module
            # exists to prevent. The racing row is left for the rescan that owns
            # it; correcting somebody else's bookkeeping from inside a failed
            # move is how a tombstone gets deleted by accident.
            logger.error(
                "The destination key (folder %s, %r) was registered between the "
                "check and the commit; renaming %s back and failing this file.",
                plan.destination_folder_id,
                move.destination_relpath,
                move.destination_path,
                exc_info=True,
            )
            os.rename(move.destination_path, move.source_path)
            raise
        # After the row is committed, exactly as the copy path does it: a
        # failure here costs previews, never the file. Nothing is unlinked on
        # this path — the rename *was* the move — so this is simply last.
        detail = carry_samples(move.source_path, move.destination_path)
        return MoveOutcome(
            move.source_folder_id, move.source_relpath, STATUS_MOVED, detail
        )

    def _copy_verify_repoint_unlink(
        self, move: PlannedMove, plan: MovePlan, *, delete_source: bool
    ) -> MoveOutcome:
        """The invariant, in the only order that has no dangling-row window."""
        partial = move.destination_path + PARTIAL_SUFFIX
        try:
            written = copy_and_digest(move.source_path, partial)
            # Read the destination back. The source digest above proves what left;
            # only re-reading proves what arrived, which is the entire point of
            # verifying a copy rather than trusting the write.
            arrived = file_digest(partial)
            if arrived != written:
                raise OSError(
                    f"Copy of {move.source_path} verified as {arrived} but "
                    f"{written} was read from the source; the destination copy "
                    "was discarded and the original is untouched."
                )
            if move.sha256 is not None and written != move.sha256:
                # The row's hash no longer names the bytes on disk. Moving would
                # carry the wrong identity to the new path, where the Civitai
                # lookup and the public {sha256}/file route both resolve on it.
                raise OSError(
                    f"{move.source_relpath} hashes as {written} but is "
                    f"registered as {move.sha256}; rescan its folder before "
                    "moving it."
                )
            os.replace(partial, move.destination_path)
        except OSError:
            discard_partial(partial)
            raise

        # Durable before the unlink, and *only* then the unlink.
        try:
            self._repoint(move, plan, delete_source=delete_source)
        except sqlite3.IntegrityError:
            # See ``_rename``. Here the undo is simply to drop the copy we just
            # made: the source has not been touched and its row still names it.
            logger.error(
                "The destination key (folder %s, %r) was registered between the "
                "check and the commit; discarding the copy at %s and failing "
                "this file.",
                plan.destination_folder_id,
                move.destination_relpath,
                move.destination_path,
                exc_info=True,
            )
            discard_partial(move.destination_path)
            raise
        # The samples sit inside the existing window rather than widening it:
        # after the durable commit, before the unlink the commit authorises. A
        # crash here still leaves the file at both paths with the row naming the
        # destination, which is what the table at the top of this module says.
        detail = carry_samples(
            move.source_path, move.destination_path, delete_source=delete_source
        )
        if not delete_source:
            return MoveOutcome(
                move.source_folder_id, move.source_relpath, STATUS_COPIED, detail
            )
        unlink_source(move.source_path)
        return MoveOutcome(
            move.source_folder_id, move.source_relpath, STATUS_MOVED, detail
        )

    def _repoint(
        self, move: PlannedMove, plan: MovePlan, *, delete_source: bool = True
    ) -> None:
        """Point the location row at the destination, in one transaction.

        A move repoints the existing row; a copy-in (``delete_source=False``)
        inserts a second one, because both copies then exist and the shelf's
        whole model is that one content row can have several locations.
        """
        now = _utcnow()
        mtime = os.stat(move.destination_path).st_mtime_ns
        with self._hub.transaction() as conn:
            if delete_source:
                conn.execute(
                    "UPDATE model_file SET model_folder_id = ?, relpath = ?, "
                    "state = ?, seen_at = ?, file_mtime = ? "
                    "WHERE model_folder_id = ? AND relpath = ?",
                    (
                        plan.destination_folder_id,
                        move.destination_relpath,
                        STATE_PRESENT,
                        now,
                        mtime,
                        move.source_folder_id,
                        move.source_relpath,
                    ),
                )
            else:
                # No ``ON CONFLICT``: the destination key was checked free
                # twice, so a conflict here means a racing writer took it, and
                # ``DO UPDATE`` would repoint *their* row at this file. The
                # UNIQUE raises and ``_copy_verify_repoint_unlink`` discards the
                # copy it just made — the same fail-closed answer the repointing
                # branch above gets for free.
                conn.execute(
                    "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                    "state, seen_at, file_mtime) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        move.model_id,
                        plan.destination_folder_id,
                        move.destination_relpath,
                        STATE_PRESENT,
                        now,
                        mtime,
                    ),
                )

    def _folder(self, folder_id: int) -> Optional[dict]:
        row = self._hub.fetchone(
            "SELECT id, path, kind, movable FROM model_folder WHERE id = ?",
            (folder_id,),
        )
        return None if row is None else dict(row)
