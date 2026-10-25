"""Models downloaded into the watched Downloads folder, moved onto the shelf.

The owner registers one folder of ``kind = 'downloads'`` (``POST /model-folders``)
and :class:`~pixlstash.tasks.downloads_intake_finder.DownloadsIntakeFinder`
watches it. A model file that lands there is **moved** into the default folder
(:func:`~pixlstash.services.managed_model_store.find_default_folder`) and
registered, so it is on the shelf without a rescan.

**A move, unlike ``Add file``.** ``POST /model-files`` copies, because the
source is a path nobody registered and deleting it would be a decision the owner
never made. Registering a folder *as the Downloads folder* is that decision: it
says "take models out of here". The order is still the one that cannot lose a
file - **the destination exists and is registered before the source goes**:

* same drive: :func:`publish_no_clobber` puts the file at the destination name
  (a hard link, or an exclusive reservation, then the old name is dropped), the
  row is written, and a file the scanner cannot read is put back;
* another drive: copy and digest, verify, publish, register, and only then
  unlink the original. A copy that does not verify or register is discarded and
  the original is untouched.

**Top level only, settled, and complete.** Browsers write a download under a
temporary name (``.part``, ``.crdownload``) and Firefox leaves an empty file at
the final name meanwhile, so a file with an in-flight sibling or no bytes is not
a candidate. ``curl -O`` and ``wget`` write straight to the final name, which is
what the finder's settle window is for, and a ``.safetensors`` additionally has
to be as long as its own header says (:func:`safetensors_complete`): a download
that stalled longer than the window is still not moved half-written.
"""

from __future__ import annotations

import json
import os
import struct
from datetime import datetime, timezone
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.managed_model_store import find_default_folder
from pixlstash.services.model_folder_scanner import (
    DOWNLOADS_FOLDER_KIND,
    HEADER_SUFFIX,
    SHELF_MODEL_SUFFIXES,
    STATE_PRESENT,
    TAKEN_FROM_KINDS,
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
    same_device,
    unlink_source,
)
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

# The names a browser or download manager writes under until the download is
# done: Firefox `.part`, Chrome and Edge `.crdownload`, Safari `.download`.
IN_FLIGHT_SUFFIXES = (".part", ".crdownload", ".download", ".partial")

# What happened to one file. ``retry`` means nothing about the file was wrong
# and it goes on a later pass: another shelf write held the slot, the default
# folder is missing or unplugged, a copy failed, or the hub refused the write.
# ``left`` means the file itself is the reason - its name is taken in the
# default folder, or it does not read as a model - so it waits until it changes.
OUTCOME_MOVED = "moved"
OUTCOME_RETRY = "retry"
OUTCOME_LEFT = "left"


def find_downloads_folder(hub: HubDatabase) -> Optional[dict]:
    """Return the registered Downloads folder, or None while none is set."""
    row = hub.fetchone(
        "SELECT id, path FROM model_folder WHERE kind = ? ORDER BY id",
        (DOWNLOADS_FOLDER_KIND,),
    )
    return dict(row) if row is not None else None


def candidates(folder_path: str) -> list[str]:
    """The model files at the top of *folder_path* that look finished.

    A regular file (never a symlink: moving the link would move nothing the
    owner downloaded) of a shelf suffix, with bytes in it and no in-flight
    sibling. Settling and completeness are the caller's next two checks.
    """
    try:
        entries = list(os.scandir(folder_path))
    except OSError as exc:
        logger.warning(
            "Could not list the Downloads folder %s: %s. Nothing is taken from "
            "it until it can be read.",
            folder_path,
            exc,
        )
        return []
    names = {entry.name for entry in entries}
    found = []
    for entry in entries:
        if not entry.name.lower().endswith(SHELF_MODEL_SUFFIXES):
            continue
        if any(entry.name + suffix in names for suffix in IN_FLIGHT_SUFFIXES):
            continue
        try:
            if not entry.is_file(follow_symlinks=False) or entry.stat().st_size == 0:
                continue
        except OSError as exc:
            logger.debug("Cannot stat %s in Downloads, skipping: %s", entry.path, exc)
            continue
        found.append(entry.path)
    return found


def safetensors_complete(path: str) -> bool:
    """Whether a ``.safetensors`` file is as long as its header says it is.

    The format states every tensor's byte range, so the file's full length is
    ``8 + header length + the furthest range end``. A file shorter than that is
    still being written (or was cut off), and moving it would put a model on the
    shelf that cannot load. A file of any other suffix answers True: a ``.gguf``
    states no such total, and the settle window is all it gets.
    """
    if not path.lower().endswith(HEADER_SUFFIX):
        return True
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            prefix = handle.read(8)
            if len(prefix) < 8:
                return False
            (header_len,) = struct.unpack("<Q", prefix)
            if header_len == 0 or 8 + header_len > size:
                return False
            header = json.loads(handle.read(header_len))
    except (OSError, ValueError, UnicodeDecodeError, struct.error) as exc:
        logger.info(
            "%s does not read as a whole safetensors file yet (%s); leaving it "
            "in Downloads.",
            path,
            exc,
        )
        return False
    if not isinstance(header, dict):
        return False
    # The header is the file's own claim, so a malformed one - an offset that
    # is not a whole number, or a negative one that would make any length pass
    # - is judged incomplete rather than trusted or allowed to raise.
    end = 0
    for key, entry in header.items():
        if key == "__metadata__" or not isinstance(entry, dict):
            continue
        offsets = entry.get("data_offsets")
        if not isinstance(offsets, list) or len(offsets) != 2:
            continue
        offset_end = offsets[1]
        if isinstance(offset_end, bool) or not isinstance(offset_end, int):
            offset_end = -1
        if offset_end < 0:
            logger.info(
                "%s has a malformed safetensors header (tensor %r ends at %r); "
                "leaving it in Downloads.",
                path,
                key,
                offsets[1],
            )
            return False
        end = max(end, offset_end)
    return size >= 8 + header_len + end


def take_in(hub: HubDatabase, source_path: str) -> tuple[str, Optional[int]]:
    """Move one downloaded model into the default folder and register it.

    Args:
        hub: The hub holding the shelf tables.
        source_path: A file at the top of the Downloads folder.

    Returns:
        ``(outcome, model_id)``: :data:`OUTCOME_MOVED` with the ``model.id``
        it landed on, :data:`OUTCOME_RETRY` when it stays in Downloads for a
        reason that may pass, or :data:`OUTCOME_LEFT` when the file itself is
        the reason. Every outcome but a move is logged with why.
    """
    destination = find_default_folder(hub)
    if destination is None or destination["kind"] in TAKEN_FROM_KINDS:
        logger.warning(
            "No default model folder to move %s into; it stays in Downloads.",
            source_path,
        )
        return OUTCOME_RETRY, None
    folder_path = destination["path"]
    if not os.path.isdir(folder_path):
        logger.warning(
            "The default model folder %s is not a readable directory right now; "
            "%s stays in Downloads until it is.",
            folder_path,
            source_path,
        )
        return OUTCOME_RETRY, None
    relpath = os.path.basename(source_path)
    try:
        target = resolve_path_within(folder_path, relpath)
    except ValueError as exc:
        logger.error(
            "Refusing to move %s: %r resolves outside the default folder %s (%s).",
            source_path,
            relpath,
            folder_path,
            exc,
        )
        return OUTCOME_LEFT, None

    # The slot a move, an import and Add file take: two writers that each found
    # this destination name free would otherwise race for it. Taken before the
    # name checks, so nothing that holds it can take the name in between.
    if not SHELF_IO_LOCK.acquire(blocking=False):
        return OUTCOME_RETRY, None
    try:
        # `present` only: the scanner keeps a deleted file's row as a
        # `missing` tombstone, and that must not refuse the same model
        # downloaded again. `register_file` takes the row back over.
        if os.path.lexists(target) or hub.fetchone(
            "SELECT 1 FROM model_file WHERE model_folder_id = ? AND relpath = ? "
            "AND state = ?",
            (destination["id"], relpath, STATE_PRESENT),
        ):
            logger.warning(
                "%s is already in the default folder %s; the download %s stays "
                "in Downloads rather than replace it.",
                relpath,
                folder_path,
                source_path,
            )
            return OUTCOME_LEFT, None
        if same_device(source_path, folder_path):
            return _rename_in(hub, destination, source_path, target, relpath)
        return _copy_in(hub, destination, source_path, target, relpath)
    finally:
        SHELF_IO_LOCK.release()


def _register(
    hub: HubDatabase,
    destination: dict,
    target: str,
    relpath: str,
    sha256: Optional[str] = None,
) -> tuple[str, Optional[int]]:
    """Register the file now at *target*; never raises.

    ``None`` from the scanner means the file does not read as a model, which is
    about the file (``left``); an exception is the hub refusing the write - a
    locked database - which is about this moment (``retry``).
    """
    try:
        model_id = ModelFolderScanner(hub).register_file(
            destination["id"], target, relpath, sha256=sha256
        )
    except Exception as exc:  # noqa: BLE001 - the caller undoes the move whatever it was
        logger.error(
            "Registering %s in folder %s failed: %s. The move is undone and it "
            "will be tried again.",
            target,
            destination["id"],
            exc,
            exc_info=True,
        )
        return OUTCOME_RETRY, None
    if model_id is None:
        return OUTCOME_LEFT, None
    _mark_arrived(hub, model_id)
    return OUTCOME_MOVED, model_id


def _mark_arrived(hub: HubDatabase, model_id: int) -> None:
    """Stamp ``model.arrived_at``; never undoes the move it follows.

    Only the New workflow set picker's "new ready to make" line reads it, so a
    failed write costs that hint for this file and nothing else.
    """
    try:
        with hub.transaction() as conn:
            conn.execute(
                "UPDATE model SET arrived_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), model_id),
            )
    except Exception as exc:  # noqa: BLE001 - the move stands whatever this was
        logger.warning(
            "Model %d is on the shelf, but its arrival time could not be "
            "recorded (%s); the picker will not count it as new.",
            model_id,
            exc,
        )


def _signature(path: str) -> Optional[tuple[int, int]]:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _rename_in(
    hub: HubDatabase, destination: dict, source_path: str, target: str, relpath: str
) -> tuple[str, Optional[int]]:
    try:
        publish_no_clobber(source_path, target)
    except OSError as exc:
        logger.error(
            "Could not move %s to %s: %s. It stays in Downloads.",
            source_path,
            target,
            exc,
            exc_info=True,
        )
        return OUTCOME_RETRY, None
    outcome, model_id = _register(hub, destination, target, relpath)
    if outcome == OUTCOME_MOVED:
        return outcome, model_id
    # Put it back where the owner downloaded it rather than leave a file in the
    # default folder that no row lists.
    try:
        publish_no_clobber(target, source_path)
    except OSError as exc:
        logger.error(
            "%s could not be registered and could not be put back at %s (%s). "
            "It is in the default folder %s, unlisted.",
            target,
            source_path,
            exc,
            destination["path"],
            exc_info=True,
        )
    return outcome, None


def _copy_in(
    hub: HubDatabase, destination: dict, source_path: str, target: str, relpath: str
) -> tuple[str, Optional[int]]:
    partial = target + PARTIAL_SUFFIX
    before = _signature(source_path)
    try:
        require_space(destination["path"], os.path.getsize(source_path))
        written = copy_and_digest(source_path, partial)
        if file_digest(partial) != written:
            raise OSError(f"The copy of {source_path} did not verify.")
        publish_no_clobber(partial, target)
    except (OSError, MoveRefused) as exc:
        discard_partial(partial)
        logger.error(
            "Copying %s into %s failed: %s. The download is untouched.",
            source_path,
            target,
            exc,
            exc_info=True,
        )
        return OUTCOME_RETRY, None
    outcome, model_id = _register(hub, destination, target, relpath, sha256=written)
    if outcome != OUTCOME_MOVED:
        # Our own copy, proven free above: it goes, and the download stays.
        discard_partial(target)
        return outcome, None
    # The copy is of the bytes read; a download that grew while they were read
    # (a resumed `curl -C`) has more than that, so it is kept, not unlinked.
    if _signature(source_path) != before:
        logger.warning(
            "%s changed while it was being copied to %s. The copy is on the "
            "shelf and the download is kept; remove whichever you do not want.",
            source_path,
            target,
        )
        return outcome, model_id
    try:
        unlink_source(source_path)
    except OSError as exc:
        logger.warning(
            "%s is on the shelf as %s, but the download could not be removed "
            "(%s). Both copies are on disk.",
            source_path,
            target,
            exc,
        )
    return outcome, model_id
