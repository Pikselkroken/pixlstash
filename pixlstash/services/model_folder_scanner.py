"""Walk the registered model folders and record what is on disk.

The shelf's rows come from here. A registered ``model_folder`` is walked for
``.safetensors`` files, each file's header is read (never its tensors, see
:mod:`pixlstash.utils.adapter_header`), and what comes back becomes one ``model``
row saying what the file *is* plus one ``model_file`` row per copy saying where
it *lives*.

There is no per-kind branch, because there is no per-kind table. ``file_kind``
carries what the header proved — ``adapter``, ``checkpoint`` or ``unknown`` —
and only two things follow from it:

* an **adapter** and an **unknown** are hashed on sight, so ``sha256`` is their
  identity from the first scan;
* a **checkpoint** is registered instantly with ``sha256`` NULL, because it may
  be 24 GB and the shelf must not stall behind it. ``MissingCheckpointHashFinder``
  fills the hash in later, which is what ``hashed_at`` was always for. Until
  then the row is identified by the location it was found at, which is exactly
  what ``model_file`` is.

``unknown`` is stored as ``unknown`` and never promoted: a marker-free file too
small to be a base model is most likely an adapter format we have not met yet,
so it stays visible and correctable on the shelf. A correction the owner makes
is never re-derived away — every column a person can edit is written with
``COALESCE`` on the *stored* value, and ``file_kind`` is not rewritten at all,
because the row is keyed by content and the parser would only ever repeat the
guess the owner just overruled.

**Detection proposes, it never applies.** A file that has vanished flips its
``model_file`` row to ``missing`` and nothing is deleted, so the ``model`` row
keeps the name, triggers and attachments the user gave it and re-linking is
automatic when the file comes back. Anything we could not *look* at is a
different fact and gets a different state: ``unreachable``, meaning "we do not
know". That applies to a whole folder we cannot open and, just as importantly,
to a subdirectory inside one we can — a NAS mounted under a registered folder
must not read as a deletion the moment it drops.

``kind = 'source'`` folders are skipped entirely. They are ai-toolkit output
roots, scanned for importable *runs* by :mod:`pixlstash.utils.aitoolkit_run` and
never catalogued in place — the same distinction the product already makes
between a reference folder and an import folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple, Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.adapter_header import (
    FILE_ADAPTER,
    FILE_CHECKPOINT,
    FILE_UNKNOWN,
    describe_adapter,
)

logger = get_logger(__name__)

# The three states a ``model_file`` row can be in. ``missing`` is a fact ("the
# folder was readable and the file was not in it"); ``unreachable`` is the
# absence of one ("we could not look").
STATE_PRESENT = "present"
STATE_MISSING = "missing"
STATE_UNREACHABLE = "unreachable"

MODEL_SUFFIX = ".safetensors"

# ai-toolkit output roots are taken FROM, never catalogued in place.
SOURCE_FOLDER_KIND = "source"

# Anything the scanner finds on disk was put there by something other than a
# PixlStash-orchestrated training run; ``trained`` is reserved for the latter,
# which also carries a ``training_run_id``.
PROVENANCE_EXTERNAL = "external"

_HASH_CHUNK_BYTES = 1024 * 1024

# Files per write transaction. The hub's multi-process contract is "short
# transactions only", and this also means an interrupted scan keeps the rows it
# already wrote instead of discarding the whole folder.
_WRITE_BATCH = 200

# SQLite LIKE wildcards. Escaped before a relpath prefix is used in a LIKE, or a
# folder named ``sd_xl`` would match ``sdaxl`` and quietly protect the wrong
# subtree from the missing sweep.
_LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


@dataclass
class FolderScanResult:
    """What one folder's scan did, for logs and for the B5 API to report."""

    folder_id: int
    path: str
    state: str
    adapters: int = 0
    checkpoints: int = 0
    unreadable: int = 0
    missing: int = 0
    unreachable: int = 0
    skipped: bool = False


class _FileRecord(NamedTuple):
    """One file's scan result, ready to write.

    ``model_id`` is set only when the file was recognised from this folder's own
    row and neither its size nor its mtime moved, so nothing had to be parsed or
    hashed. Every other field is then irrelevant and the write is a single
    ``model_file`` touch.
    """

    relpath: str
    size: int
    mtime_ns: int
    model_id: Optional[int] = None
    digest: Optional[str] = None
    file_kind: str = FILE_UNKNOWN
    kind: Optional[str] = None
    display_name: Optional[str] = None
    filename: Optional[str] = None
    base_model: Optional[str] = None
    trigger_words: Optional[str] = None
    training_step: Optional[int] = None
    param_count: Optional[int] = None


def sha256_file(path: str) -> str:
    """Return the full-file SHA-256 of *path* as lowercase hex.

    Full file, unconditionally: ``model.sha256`` is an interop value (Civitai
    lookup, the public ``{sha256}/file`` path, the ComfyUI node), so a sampled
    digest in that column would break interop silently rather than loudly.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ModelFolderScanner:
    """Reconcile the ``model`` and ``model_file`` tables with what is on disk.

    Holds no state between calls: every method takes the hub connection handed
    to the constructor, does its filesystem work outside any write transaction,
    and writes in short batches.
    """

    def __init__(self, hub: HubDatabase) -> None:
        """Bind the scanner to an open hub.

        Args:
            hub: The hub database. The scanner only ever touches the model-shelf
                tables in it.
        """
        self._hub = hub

    def scan_all(self) -> list[FolderScanResult]:
        """Scan every registered folder that is catalogued in place.

        Returns:
            One :class:`FolderScanResult` per folder, in id order. ``source``
            folders are included with ``skipped=True`` so a caller reporting
            progress does not silently lose them.
        """
        rows = self._hub.fetchall("SELECT id, path, kind FROM model_folder ORDER BY id")
        return [self.scan_folder(row["id"], row["path"], row["kind"]) for row in rows]

    def scan_folder(self, folder_id: int, path: str, kind: str) -> FolderScanResult:
        """Scan one registered folder and reconcile its rows with what is on disk.

        Args:
            folder_id: ``model_folder.id``.
            path: The folder as registered. Owner-chosen and therefore trusted;
                this is a read path only, so nothing here writes into it.
            kind: ``model_folder.kind``. ``source`` folders are skipped.

        Returns:
            A :class:`FolderScanResult` describing what was found.
        """
        if kind == SOURCE_FOLDER_KIND:
            return FolderScanResult(
                folder_id=folder_id, path=path, state=STATE_PRESENT, skipped=True
            )

        if not self._is_readable(path):
            return self._mark_unreachable(folder_id, path)

        scanned_at = _utcnow()
        known = self._known_files(folder_id)
        result = FolderScanResult(folder_id=folder_id, path=path, state=STATE_PRESENT)
        blocked: set[str] = set()

        batch: list[_FileRecord] = []
        for abs_path, relpath in self._walk(path, blocked):
            record = self._describe(abs_path, relpath, known, result)
            if record is not None:
                batch.append(record)
            if len(batch) >= _WRITE_BATCH:
                self._write_batch(folder_id, batch, scanned_at)
                batch = []
        if batch:
            self._write_batch(folder_id, batch, scanned_at)

        # Before the missing sweep, never after: a subtree we could not read is
        # not a subtree whose files were deleted.
        result.unreachable = self._mark_unreachable_subtrees(
            folder_id, blocked, scanned_at
        )
        result.missing = self._mark_missing(folder_id, scanned_at)
        self._touch_folder(folder_id, scanned_at)
        logger.info(
            "Model folder %s (id=%s): %d adapters, %d checkpoints, %d missing, "
            "%d unreachable, %d unreadable.",
            path,
            folder_id,
            result.adapters,
            result.checkpoints,
            result.missing,
            result.unreachable,
            result.unreadable,
        )
        return result

    # -- filesystem -------------------------------------------------------

    @staticmethod
    def _is_readable(path: str) -> bool:
        return bool(path) and os.path.isdir(path) and os.access(path, os.R_OK | os.X_OK)

    @staticmethod
    def _walk(root: str, blocked: set[str]):
        """Yield ``(abs_path, relpath)`` for every model file under *root*.

        Symlinks are not followed (``os.walk`` default), so a folder that links
        back into itself or into another registered folder cannot make the scan
        loop or double-count.

        A directory that cannot be listed is collected into *blocked* rather
        than discarded. ``os.walk`` swallows those errors by default, which
        would make an unplugged mount under a registered folder indistinguishable
        from a deletion — the one thing this module promises never to do.
        """

        def on_error(exc: OSError) -> None:
            failed = exc.filename or root
            logger.warning(
                "Model scan could not list %s: %s. Its files stay unreachable "
                "rather than being reported missing.",
                failed,
                exc,
            )
            blocked.add(os.path.relpath(failed, root))

        for directory, _dirs, files in os.walk(root, onerror=on_error):
            for name in sorted(files):
                if not name.lower().endswith(MODEL_SUFFIX):
                    continue
                abs_path = os.path.join(directory, name)
                yield abs_path, os.path.relpath(abs_path, root)

    def _describe(
        self,
        abs_path: str,
        relpath: str,
        known: dict[str, tuple[int, Optional[int], Optional[int]]],
        result: FolderScanResult,
    ) -> Optional[_FileRecord]:
        """Return the row for one file, hashing it only when it has to be.

        A file already recorded at this relpath whose size *and* mtime both still
        match is taken as unchanged and is not re-hashed: re-reading every byte
        of 1,800 adapters on each sweep would make the scan cost the whole
        folder. Size alone is not enough — a same-size in-place edit would leave
        ``model.sha256`` naming bytes that are no longer on disk, in the column
        Civitai lookup and the public ``{sha256}/file`` route both resolve on.

        The comparison is against *this folder's* row. Trusting another folder's
        row for the same relpath would file one file under another's digest.
        """
        try:
            stat_result = os.stat(abs_path)
        except OSError as exc:
            logger.warning(
                "Model scan could not stat %s: %s. Skipping the file; it will be "
                "reconsidered on the next scan.",
                abs_path,
                exc,
            )
            result.unreadable += 1
            return None
        size = stat_result.st_size
        mtime_ns = stat_result.st_mtime_ns

        previous = known.get(relpath)
        if previous is not None and previous[1:] == (size, mtime_ns):
            result.adapters += 1
            return _FileRecord(
                relpath=relpath, size=size, mtime_ns=mtime_ns, model_id=previous[0]
            )

        info = describe_adapter(abs_path)
        if info is None:
            logger.warning(
                "Model scan could not read a safetensors header from %s; leaving "
                "the file unregistered so it is retried rather than recorded wrong.",
                abs_path,
            )
            result.unreadable += 1
            return None

        record = _FileRecord(
            relpath=relpath,
            size=size,
            mtime_ns=mtime_ns,
            file_kind=info.file_kind,
            # Which adapter algorithm, and only that. NULL for anything that is
            # not an adapter, so the column never carries a guess about a file
            # whose kind was never in question.
            kind=info.kind if info.file_kind == FILE_ADAPTER else None,
            display_name=info.display_name,
            filename=os.path.basename(abs_path),
            base_model=info.base_model,
            trigger_words=json.dumps(info.trigger_words)
            if info.trigger_words
            else None,
            training_step=info.training_step,
            param_count=info.param_count,
        )

        if info.file_kind == FILE_CHECKPOINT:
            # No hash: it may be 24 GB. MissingCheckpointHashFinder supplies it.
            result.checkpoints += 1
            return record

        try:
            digest = sha256_file(abs_path)
        except OSError as exc:
            logger.warning(
                "Model scan could not hash %s (%d bytes): %s. Leaving it "
                "unregistered; a partial hash would be a wrong identity.",
                abs_path,
                size,
                exc,
            )
            result.unreadable += 1
            return None

        result.adapters += 1
        return record._replace(digest=digest)

    # -- hub reads --------------------------------------------------------

    def _known_files(
        self, folder_id: int
    ) -> dict[str, tuple[int, Optional[int], Optional[int]]]:
        """Return ``{relpath: (model_id, file_size, file_mtime)}`` for this folder."""
        rows = self._hub.fetchall(
            "SELECT mf.relpath, mf.model_id, mf.file_mtime, m.file_size "
            "FROM model_file mf JOIN model m ON m.id = mf.model_id "
            "WHERE mf.model_folder_id = ?",
            (folder_id,),
        )
        return {
            row["relpath"]: (row["model_id"], row["file_size"], row["file_mtime"])
            for row in rows
        }

    # -- hub writes -------------------------------------------------------

    def _write_batch(
        self, folder_id: int, batch: list[_FileRecord], scanned_at: str
    ) -> None:
        with self._hub.transaction() as conn:
            for record in batch:
                model_id = record.model_id
                if model_id is None:
                    model_id = self._upsert_model(conn, folder_id, record, scanned_at)
                self._upsert_model_file(conn, folder_id, model_id, record, scanned_at)

    @staticmethod
    def _upsert_model(
        conn: sqlite3.Connection,
        folder_id: int,
        record: _FileRecord,
        scanned_at: str,
    ) -> int:
        """Insert the content row, or refresh it without overwriting curation.

        Every column the user can edit is written with ``COALESCE`` on the
        *stored* value, so a re-scan can fill a blank but can never replace a
        name, a base model or a corrected kind someone typed. ``file_kind`` and
        ``provenance`` are not in the update at all: the row is identified by its
        content, so the parser can only ever repeat the classification the owner
        just overruled. ``file_size`` is a fact about the file and is refreshed.

        Returns:
            ``model.id`` for this file.
        """
        if record.digest is None:
            return ModelFolderScanner._upsert_unhashed(
                conn, folder_id, record, scanned_at
            )
        conn.execute(
            "INSERT INTO model (file_kind, kind, sha256, display_name, filename, "
            "base_model, trigger_words, provenance, training_step, param_count, "
            "file_size, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(sha256) DO UPDATE SET "
            "kind = COALESCE(model.kind, excluded.kind), "
            "display_name = COALESCE(model.display_name, excluded.display_name), "
            "filename = COALESCE(model.filename, excluded.filename), "
            "base_model = COALESCE(model.base_model, excluded.base_model), "
            "trigger_words = COALESCE(model.trigger_words, excluded.trigger_words), "
            "training_step = COALESCE(model.training_step, excluded.training_step), "
            "param_count = COALESCE(model.param_count, excluded.param_count), "
            "file_size = excluded.file_size",
            (
                record.file_kind,
                record.kind,
                record.digest,
                record.display_name,
                record.filename,
                record.base_model,
                record.trigger_words,
                PROVENANCE_EXTERNAL,
                record.training_step,
                record.param_count,
                record.size,
                scanned_at,
            ),
        )
        return int(
            conn.execute(
                "SELECT id FROM model WHERE sha256 = ?", (record.digest,)
            ).fetchone()[0]
        )

    @staticmethod
    def _upsert_unhashed(
        conn: sqlite3.Connection,
        folder_id: int,
        record: _FileRecord,
        scanned_at: str,
    ) -> int:
        """Register a checkpoint in place, with no hash.

        Identified by the location it was found at, because that is all it has
        until something has read all 24 GB of it. Reaching here at all means the
        size or the mtime moved, so the bytes changed and any hash already
        recorded for this path is stale: it is cleared rather than left to
        misidentify the file, which puts the row straight back into
        ``MissingCheckpointHashFinder``'s queue.
        """
        existing = conn.execute(
            "SELECT model_id FROM model_file WHERE model_folder_id = ? AND relpath = ?",
            (folder_id, record.relpath),
        ).fetchone()
        if existing is None:
            return int(
                conn.execute(
                    "INSERT INTO model (file_kind, kind, display_name, filename, "
                    "base_model, trigger_words, provenance, training_step, "
                    "param_count, file_size, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.file_kind,
                        record.kind,
                        record.display_name,
                        record.filename,
                        record.base_model,
                        record.trigger_words,
                        PROVENANCE_EXTERNAL,
                        record.training_step,
                        record.param_count,
                        record.size,
                        scanned_at,
                    ),
                ).lastrowid
            )

        model_id = int(existing["model_id"])
        conn.execute(
            "UPDATE model SET sha256 = NULL, hashed_at = NULL, file_size = ?, "
            "filename = COALESCE(filename, ?), "
            "display_name = COALESCE(display_name, ?), "
            "base_model = COALESCE(base_model, ?), "
            "param_count = COALESCE(param_count, ?) WHERE id = ?",
            (
                record.size,
                record.filename,
                record.display_name,
                record.base_model,
                record.param_count,
                model_id,
            ),
        )
        return model_id

    @staticmethod
    def _upsert_model_file(
        conn: sqlite3.Connection,
        folder_id: int,
        model_id: int,
        record: _FileRecord,
        scanned_at: str,
    ) -> None:
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state, "
            "seen_at, file_mtime) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(model_folder_id, relpath) DO UPDATE SET "
            "model_id = excluded.model_id, state = excluded.state, "
            "seen_at = excluded.seen_at, file_mtime = excluded.file_mtime",
            (
                model_id,
                folder_id,
                record.relpath,
                STATE_PRESENT,
                scanned_at,
                record.mtime_ns,
            ),
        )

    def _mark_unreachable_subtrees(
        self, folder_id: int, blocked: set[str], scanned_at: str
    ) -> int:
        """Flip every row under a directory this scan could not list.

        Stamped with this scan's ``seen_at`` as well, so the missing sweep that
        runs next skips them: they were accounted for, just not read.
        """
        if not blocked:
            return 0
        touched = 0
        with self._hub.transaction() as conn:
            for relative_dir in sorted(blocked):
                if relative_dir in (".", ""):
                    # The root itself became unlistable mid-scan. Nothing under
                    # it was seen, so the whole folder is what we could not read.
                    cursor = conn.execute(
                        "UPDATE model_file SET state = ?, seen_at = ? "
                        "WHERE model_folder_id = ?",
                        (STATE_UNREACHABLE, scanned_at, folder_id),
                    )
                else:
                    prefix = (relative_dir + os.sep).translate(_LIKE_ESCAPE)
                    cursor = conn.execute(
                        "UPDATE model_file SET state = ?, seen_at = ? "
                        "WHERE model_folder_id = ? AND relpath LIKE ? ESCAPE '\\'",
                        (STATE_UNREACHABLE, scanned_at, folder_id, prefix + "%"),
                    )
                touched += int(cursor.rowcount or 0)
        return touched

    def _mark_missing(self, folder_id: int, scanned_at: str) -> int:
        """Flip every row this scan did not touch to ``missing``.

        Compared on ``seen_at`` rather than by listing the relpaths that were
        found: a folder of 1,800 adapters would blow past SQLite's bound-variable
        limit, and every present row was just stamped with this scan's timestamp.

        Strictly **older than** this scan's stamp, not merely different from it.
        With ``!=`` two scans running at once each sweep away the rows the other
        just stamped, and a shelf whose files are all on disk reads as entirely
        missing — measured at 4 of 5 unsynchronised runs flipping every file.
        ``<`` makes a concurrent scan's newer stamp survive, which is correct in
        both directions and across processes: a row is only demoted by the scan
        that walked the folder and did not find it. The residual case, a file
        created after a scan walked past it, is a file that scan genuinely never
        saw, and the next scan restores it.
        """
        with self._hub.transaction() as conn:
            cursor = conn.execute(
                "UPDATE model_file SET state = ? WHERE model_folder_id = ? "
                "AND (seen_at IS NULL OR seen_at < ?) AND state != ?",
                (STATE_MISSING, folder_id, scanned_at, STATE_MISSING),
            )
            return int(cursor.rowcount or 0)

    def _mark_unreachable(self, folder_id: int, path: str) -> FolderScanResult:
        """Record that the folder could not be read, and change nothing else.

        Not ``missing``: an unplugged drive says nothing about whether its files
        still exist, and declaring them gone would make the shelf lie about
        content the user still has.
        """
        logger.warning(
            "Model folder %s (id=%s) is not a readable directory; marking its "
            "files unreachable rather than missing.",
            path,
            folder_id,
        )
        scanned_at = _utcnow()
        with self._hub.transaction() as conn:
            cursor = conn.execute(
                "UPDATE model_file SET state = ? WHERE model_folder_id = ? "
                "AND state != ?",
                (STATE_UNREACHABLE, folder_id, STATE_UNREACHABLE),
            )
        self._touch_folder(folder_id, scanned_at)
        return FolderScanResult(
            folder_id=folder_id,
            path=path,
            state=STATE_UNREACHABLE,
            unreachable=int(cursor.rowcount or 0),
        )

    def _touch_folder(self, folder_id: int, scanned_at: str) -> None:
        with self._hub.transaction() as conn:
            conn.execute(
                "UPDATE model_folder SET last_checked = ? WHERE id = ?",
                (scanned_at, folder_id),
            )
