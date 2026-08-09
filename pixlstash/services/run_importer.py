"""Bring an ai-toolkit run onto the shelf, one checkpoint at a time.

A ``kind='source'`` folder is an ai-toolkit output root: the scanner never
catalogues it, because a run's steps are working output rather than a library of
models. Importing is how a run's files stop being working output — they are
brought into a folder the shelf *does* catalogue, and become ``model`` rows with
a stack over them so the whole run reads as one shelf row with an expandable step
strip.

**The move invariant applies unchanged**, because an import is a move with one
extra step: copy → verify by SHA-256 → register the row and commit → then unlink.
The helpers are :mod:`pixlstash.services.model_mover`'s, not copies of them, so
there is one implementation of the ordering and one place to get it wrong. The
only difference is which row is written: a move *repoints* an existing
``model_file``, an import *creates* the ``model`` and the ``model_file`` together,
because the source was never catalogued and so has no row to repoint.

``delete_after_import`` on the source folder decides whether the last step runs.
Off (the default) the run's files stay where the trainer left them and the shelf
holds a second copy; on, the run folder is emptied of the files that were taken.
Either way the unlink is last, so an interruption leaves a duplicate.

**Provenance is ``trained``, not ``external``.** Every other row on the shelf was
found on disk by the scanner and says so; a run PixlStash imported from a trainer
it can read is the one case where the shelf knows where the file came from.

An interrupted import can leave an ``adapter_stack`` row with no members. It is
inert — nothing reads a stack except through a ``model.stack_id`` pointing at it —
so it is left rather than cleaned up by a rollback the rest of this module
deliberately does not have.

The stack mirrors ``PictureStack`` exactly, so nothing new is invented for
presentation: ``stack_position`` 0 is the cover and is the **final** checkpoint
when the run has a bare no-step file, or the highest step when it does not.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_folder_scanner import STATE_PRESENT
from pixlstash.services.model_mover import (
    PARTIAL_SUFFIX,
    STATUS_CANCELLED,
    STATUS_FAILED,
    MoveRefused,
    copy_and_digest,
    discard_partial,
    file_digest,
    require_space,
    unlink_source,
)
from pixlstash.utils.adapter_header import FILE_ADAPTER, describe_adapter
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.utils.aitoolkit_run import Checkpoint, read_run

logger = get_logger(__name__)

# What an imported file's ``model.provenance`` says. The one value the scanner
# never writes: it only ever finds files somebody else put there.
PROVENANCE_TRAINED = "trained"

STATUS_IMPORTED = "imported"


@dataclass
class ImportOutcome:
    """What happened to one checkpoint of the run."""

    filename: str
    step: Optional[int]
    status: str
    model_id: Optional[int] = None
    detail: Optional[str] = None


@dataclass
class ImportReport:
    """What happened to the run."""

    run_name: str
    stack_id: Optional[int] = None
    outcomes: list[ImportOutcome] = field(default_factory=list)
    cancelled: bool = False


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cover_first(checkpoints: list[Checkpoint]) -> list[Checkpoint]:
    """Order a run's files so ``stack_position`` 0 is the right cover.

    The bare no-step file is what the trainer wrote last and is the one a user
    means by "the LoRA", so it leads. Without one — the run the fixtures call
    *the unconfirmed cover* — the highest step is the best available answer, and
    the rest follow newest first so expanding the strip reads backwards in time.
    """
    finals = [c for c in checkpoints if c.is_final]
    stepped = sorted(
        (c for c in checkpoints if not c.is_final),
        key=lambda c: c.step or 0,
        reverse=True,
    )
    return finals + stepped


class RunImporter:
    """Copy a run's checkpoints into a catalogued folder and register them."""

    def __init__(self, hub: HubDatabase) -> None:
        """Bind the importer to an open hub.

        Args:
            hub: The hub database. Only the model-shelf tables are touched.
        """
        self._hub = hub

    def import_run(
        self,
        run_dir: str,
        destination_folder_id: int,
        *,
        steps: Optional[list[Optional[int]]] = None,
        delete_source: bool = False,
        should_cancel: Optional[Callable[[], bool]] = None,
        on_progress: Optional[Callable[[ImportOutcome], None]] = None,
    ) -> ImportReport:
        """Import the selected checkpoints of one run.

        Args:
            run_dir: The run folder, i.e. a child of a registered ``source``
                folder. Contained against that folder by the caller.
            destination_folder_id: A registered folder the shelf catalogues.
            steps: Which checkpoints to take, by step, with ``None`` meaning the
                bare final. Omit for every checkpoint in the run.
            delete_source: Whether to unlink each file after its row is durably
                committed. Comes from the source folder's
                ``delete_after_import``; the unlink is always last.
            should_cancel: Consulted between files. Nothing is rolled back.
            on_progress: Called with each :class:`ImportOutcome`.

        Returns:
            An :class:`ImportReport`, with the stack the run landed in.

        Raises:
            MoveRefused: The destination is unusable, the run holds nothing to
                import, a filename collides, or the copy would not fit. Nothing
                has been written when this is raised.
        """
        destination = self._destination(destination_folder_id)
        run = read_run(run_dir)
        wanted = self._select(run.checkpoints, steps)
        if not wanted:
            raise MoveRefused(
                f"{run.name} has no checkpoint to import"
                + (f" at step(s) {steps}." if steps else "."),
                status_code=404,
            )

        targets = self._resolve_targets(wanted, destination, destination_folder_id)
        require_space(destination, sum(os.path.getsize(c.path) for c in wanted))

        report = ImportReport(run_name=run.name)
        report.stack_id = self._create_stack(run.name)
        for position, checkpoint in enumerate(_cover_first(wanted)):
            if should_cancel is not None and should_cancel():
                report.cancelled = True
                report.outcomes.append(
                    ImportOutcome(
                        checkpoint.filename, checkpoint.step, STATUS_CANCELLED
                    )
                )
                continue
            outcome = self._import_one(
                checkpoint,
                targets[checkpoint.path],
                run=run,
                stack_id=report.stack_id,
                position=position,
                destination_folder_id=destination_folder_id,
                delete_source=delete_source,
            )
            report.outcomes.append(outcome)
            if on_progress is not None:
                on_progress(outcome)

        logger.info(
            "Imported %s into folder %s: %d file(s), stack %s%s.",
            run.name,
            destination_folder_id,
            sum(1 for o in report.outcomes if o.status == STATUS_IMPORTED),
            report.stack_id,
            " (cancelled)" if report.cancelled else "",
        )
        return report

    # -- planning ---------------------------------------------------------

    def _destination(self, destination_folder_id: int) -> str:
        row = self._hub.fetchone(
            "SELECT path, kind FROM model_folder WHERE id = ?",
            (destination_folder_id,),
        )
        if row is None:
            raise MoveRefused("No such destination folder.", status_code=404)
        if row["kind"] == "source":
            raise MoveRefused(
                "A source folder is where runs are taken from, never a place to "
                "import them into."
            )
        if not os.path.isdir(row["path"]):
            raise MoveRefused(
                f"Destination folder {row['path']} is not a readable directory "
                "right now, so nothing was imported.",
                status_code=409,
            )
        return row["path"]

    @staticmethod
    def _select(
        checkpoints: list[Checkpoint], steps: Optional[list[Optional[int]]]
    ) -> list[Checkpoint]:
        if steps is None:
            return list(checkpoints)
        wanted = set(steps)
        return [c for c in checkpoints if c.step in wanted]

    def _resolve_targets(
        self,
        checkpoints: list[Checkpoint],
        destination: str,
        destination_folder_id: int,
    ) -> dict[str, str]:
        """Contain and collision-check every destination before the first byte.

        Same rule as the mover: refuse the batch rather than import half a run
        and stop, because there is no undo for shelf operations.
        """
        targets: dict[str, str] = {}
        for checkpoint in checkpoints:
            relpath = os.path.basename(checkpoint.filename)
            try:
                # Containment (#776) on the write path. ``basename`` above has
                # already flattened the name, and ``aitoolkit_run`` only ever
                # produces ``scandir`` entry names, so today nothing can reach
                # this check — it is a sanitizer, not the live guard. It stays
                # because it becomes load-bearing the moment a second producer
                # supplies a relpath, and because ``os.path.join`` silently
                # discards the base for an absolute component.
                target = resolve_path_within(destination, relpath)
            except ValueError as exc:
                raise MoveRefused(
                    f"{relpath!r} would be written outside the destination folder."
                ) from exc
            if target in targets.values():
                raise MoveRefused(
                    f"Two files in {os.path.dirname(checkpoint.path)} would both "
                    f"land on {relpath!r}."
                )
            if os.path.exists(target):
                raise MoveRefused(
                    f"{relpath} already exists in the destination folder. "
                    "Nothing was imported."
                )
            if self._hub.fetchone(
                "SELECT 1 FROM model_file WHERE model_folder_id = ? AND relpath = ?",
                (destination_folder_id, relpath),
            ):
                raise MoveRefused(
                    f"{relpath} is already registered in the destination folder. "
                    "Rescan it first."
                )
            targets[checkpoint.path] = target
        return targets

    def _create_stack(self, name: str) -> int:
        now = _utcnow()
        with self._hub.transaction() as conn:
            return int(
                conn.execute(
                    "INSERT INTO adapter_stack (name, created_at, updated_at) "
                    "VALUES (?, ?, ?)",
                    (name, now, now),
                ).lastrowid
            )

    # -- one file ---------------------------------------------------------

    def _import_one(
        self,
        checkpoint: Checkpoint,
        target: str,
        *,
        run,
        stack_id: int,
        position: int,
        destination_folder_id: int,
        delete_source: bool,
    ) -> ImportOutcome:
        """copy → verify → register and commit → then unlink. No other order."""
        partial = target + PARTIAL_SUFFIX
        try:
            written = copy_and_digest(checkpoint.path, partial)
            if file_digest(partial) != written:
                raise OSError(
                    f"Copy of {checkpoint.path} did not verify; the copy was "
                    "discarded and the original is untouched."
                )
            os.replace(partial, target)
        except OSError as exc:
            discard_partial(partial)
            logger.error(
                "Importing %s into %s failed: %s. The run's file is untouched.",
                checkpoint.path,
                target,
                exc,
                exc_info=True,
            )
            return ImportOutcome(
                checkpoint.filename, checkpoint.step, STATUS_FAILED, detail=str(exc)
            )

        model_id = self._register(
            target,
            written,
            checkpoint=checkpoint,
            run=run,
            stack_id=stack_id,
            position=position,
            destination_folder_id=destination_folder_id,
        )
        if delete_source:
            unlink_source(checkpoint.path)
        return ImportOutcome(
            checkpoint.filename, checkpoint.step, STATUS_IMPORTED, model_id=model_id
        )

    def _register(
        self,
        target: str,
        digest: str,
        *,
        checkpoint: Checkpoint,
        run,
        stack_id: int,
        position: int,
        destination_folder_id: int,
    ) -> int:
        """Write the content row and the location row in one transaction.

        The header is parsed from the *destination* copy, which is the one the
        row will name. ``ON CONFLICT(sha256)`` because a run imported twice, or a
        file already on the shelf from somewhere else, is one model with two
        locations — the shelf's whole content/location split. On that path the
        curation already on the row is kept and only the run facts are filled in.

        The run's own config supplies what the header often does not: 37 % of
        real adapters record no base model at all, and ai-toolkit's
        ``config.yaml`` names it exactly.
        """
        info = describe_adapter(target)
        size = os.path.getsize(target)
        mtime = os.stat(target).st_mtime_ns
        now = _utcnow()
        base_model = (info.base_model if info else None) or run.base_model
        triggers = (info.trigger_words if info else None) or run.trigger_words
        with self._hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model (file_kind, kind, sha256, display_name, "
                "filename, base_model, trigger_words, provenance, training_step, "
                "param_count, file_size, stack_id, stack_position, run_key, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(sha256) DO UPDATE SET "
                "display_name = COALESCE(model.display_name, excluded.display_name), "
                "base_model = COALESCE(model.base_model, excluded.base_model), "
                "trigger_words = COALESCE(model.trigger_words, excluded.trigger_words), "
                "training_step = COALESCE(model.training_step, excluded.training_step), "
                "stack_id = COALESCE(model.stack_id, excluded.stack_id), "
                "stack_position = COALESCE(model.stack_position, excluded.stack_position), "
                "run_key = COALESCE(model.run_key, excluded.run_key), "
                "file_size = excluded.file_size",
                (
                    # An adapter, always: a training run's output is what it
                    # trained, and the CHECK constraints demand a kind with it.
                    FILE_ADAPTER,
                    (info.kind if info else None) or "unknown",
                    digest,
                    run.name,
                    os.path.basename(target),
                    base_model,
                    json.dumps(triggers) if triggers else None,
                    PROVENANCE_TRAINED,
                    checkpoint.step,
                    info.param_count if info else None,
                    size,
                    stack_id,
                    position,
                    run.name,
                    now,
                ),
            )
            model_id = int(
                conn.execute(
                    "SELECT id FROM model WHERE sha256 = ?", (digest,)
                ).fetchone()[0]
            )
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                "state, seen_at, file_mtime) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(model_folder_id, relpath) DO UPDATE SET "
                "model_id = excluded.model_id, state = excluded.state, "
                "seen_at = excluded.seen_at, file_mtime = excluded.file_mtime",
                (
                    model_id,
                    destination_folder_id,
                    os.path.basename(target),
                    STATE_PRESENT,
                    now,
                    mtime,
                ),
            )
        return model_id
