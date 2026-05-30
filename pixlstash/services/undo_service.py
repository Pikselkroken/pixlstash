"""Service layer for undoing recent metadata changes using the ChangeLog.

Provides ``undo_last_transaction()`` (ChangeLog-only) and
``undo_to_snapshot()`` (hybrid: ChangeLog for included tables, snapshot
for excluded tables based on timing heuristics).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import func, inspect as sa_inspect
from sqlmodel import Session, select

from pixlstash.db_models.change_log import ChangeLog
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# Lazily-populated caches mapping SQLite table names to their ORM model class
# and each model to its {attribute-key: Column} map.  Used by the undo path to
# reverse-apply ChangeLog entries through the ORM (so the change-log / hash
# flush hooks in database.py fire) rather than via raw Core DML.
_MODEL_BY_TABLE: dict[str, type] = {}
_COLUMN_BY_ATTR: dict[type, dict[str, Any]] = {}


def _resolve_model(table_name: str) -> Optional[type]:
    """Return the ORM model class mapped to *table_name*, or None if unmapped."""
    if not _MODEL_BY_TABLE:
        from sqlmodel import SQLModel

        for mapper in SQLModel._sa_registry.mappers:
            local_table = mapper.local_table
            if local_table is not None:
                _MODEL_BY_TABLE[local_table.name] = mapper.class_
    return _MODEL_BY_TABLE.get(table_name)


def _column_by_attr(model: type) -> dict[str, Any]:
    """Return a cached {attribute-key: Column} map for *model*."""
    cached = _COLUMN_BY_ATTR.get(model)
    if cached is None:
        mapper = sa_inspect(model)
        cached = {attr.key: attr.columns[0] for attr in mapper.column_attrs}
        _COLUMN_BY_ATTR[model] = cached
    return cached


def _coerce_serialized_value(column: Any, value: Any) -> tuple[bool, Any]:
    """Coerce a serialized ChangeLog value back to an ORM-assignable value.

    The change-log serializes BLOB columns as ``"sha256:<digest>"`` markers and
    datetimes as ISO strings (see ``_cl_serialize_state`` in database.py).

    Returns:
        ``(should_set, coerced_value)``.  ``should_set`` is False for blob
        markers, whose original bytes cannot be reconstructed — those columns
        are left untouched (regenerable derived data the WorkPlanner refills).
    """
    if isinstance(value, str) and value.startswith("sha256:"):
        return (False, None)
    if isinstance(value, str) and value:
        try:
            if column.type.python_type is datetime:
                return (True, datetime.fromisoformat(value))
        except (NotImplementedError, AttributeError, ValueError):
            # Column type doesn't expose a python_type (NotImplementedError /
            # AttributeError) or the stored string isn't a parseable datetime
            # (ValueError) — fall through and return the raw string value as-is.
            pass
    return (True, value)


@dataclass
class UndoReport:
    """Summary of a completed undo operation.

    Attributes:
        reverted_txn_count: Number of ChangeLog transactions reversed.
        reverted_row_count: Total ChangeLog rows processed.
        errors: Non-fatal error messages.
        escalated_to_full_restore: True when ``undo_to_snapshot`` escalated to a
            whole-database file swap via ``RestoreService.restore_full`` instead
            of a metadata-only undo (because an excluded table was last mutated
            closer to the snapshot than to now).  Callers should treat this as a
            materially larger operation than a metadata revert.
        escalated_tables: The excluded tables whose timing triggered the
            escalation.
    """

    reverted_txn_count: int = 0
    reverted_row_count: int = 0
    errors: list[str] = field(default_factory=list)
    escalated_to_full_restore: bool = False
    escalated_tables: list[str] = field(default_factory=list)


class UndoService:
    """Reverses recent metadata changes recorded in the ChangeLog.

    Attributes:
        _vault: Back-reference to the owning Vault.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the service.

        Args:
            vault: The owning Vault instance.
        """
        self._vault = vault

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def undo_last_transaction(self) -> UndoReport:
        """Reverse the most recent writer-session transaction in the ChangeLog.

        Finds the highest ``txn_id`` (by max ``id``) and reverse-applies every
        entry for that transaction in descending ``seq_in_txn`` order:

        - INSERT → DELETE
        - UPDATE → restore ``before_json`` column values
        - DELETE → INSERT from ``before_json``

        The undo operation itself is wrapped in ``write_reason("undo txn …")``
        so it appears in the ChangeLog.

        Returns:
            An ``UndoReport`` summarising the operation.
        """
        db = self._vault.db

        def _find_last_txn(session):
            row = session.exec(
                select(ChangeLog).order_by(ChangeLog.id.desc()).limit(1)
            ).first()
            return row.txn_id if row else None

        txn_id = db.run_immediate_read_task(_find_last_txn)
        if txn_id is None:
            logger.info("UndoService: ChangeLog is empty, nothing to undo.")
            return UndoReport()

        def _load_txn_entries(session):
            return session.exec(
                select(ChangeLog)
                .where(ChangeLog.txn_id == txn_id)
                .order_by(ChangeLog.seq_in_txn.desc())
            ).all()

        entries = db.run_immediate_read_task(_load_txn_entries)
        if not entries:
            return UndoReport()

        report = UndoReport()

        def _apply(session):
            self._apply_undo_entries(session, entries, report)
            session.commit()

        with db.write_reason(f"undo txn {txn_id}"):
            try:
                db.run_task(_apply, priority=0)
            except Exception as exc:
                # The transaction was rolled back, so nothing was applied.
                logger.error(
                    "UndoService: undo txn %s rolled back: %s",
                    txn_id,
                    exc,
                    exc_info=True,
                )
                report.reverted_row_count = 0
                report.reverted_txn_count = 0
                if not report.errors:
                    report.errors.append(f"Undo rolled back: {exc}")
                return report

        try:
            from pixlstash.event_types import EventType

            self._vault.emit_event(
                EventType.UNDO_APPLIED,
                {
                    "txn_id": txn_id,
                    "reverted_row_count": report.reverted_row_count,
                },
            )
        except Exception as exc:
            logger.warning("UndoService: failed to emit UNDO_APPLIED: %s", exc)

        return report

    def undo_to_snapshot(self, snapshot_id: int) -> UndoReport:
        """Undo all changes back to the state at a given snapshot.

        Strategy:
        - For **included** tables: walk the ChangeLog backward from the
          current head to the snapshot's ``max_changelog_id`` (read from the
          manifest sidecar) and reverse-apply each transaction in reverse
          chronological order.
        - For **excluded** tables (regenerable: quality, likeness, embeddings,
          etc.): use a timing heuristic per table.  If the most-recent
          excluded-table mutation happened *closer to the snapshot* than to
          now, the snapshot's data is considered closer to ground truth and a
          full restore is triggered for those tables via
          ``RestoreService.restore_full()``.  Otherwise the current values are
          kept and the WorkPlanner will re-derive them.

        Args:
            snapshot_id: The ID of the snapshot to rewind to.

        Returns:
            An ``UndoReport`` summarising the operation.

        Raises:
            ValueError: If the snapshot is not found or its manifest is
                missing.
        """
        import os

        db = self._vault.db
        vault_root = self._vault.image_root

        cp = self._vault.snapshot_service.get_snapshot(snapshot_id)
        if cp is None:
            raise ValueError(f"Snapshot id={snapshot_id} not found.")

        abs_manifest = os.path.join(vault_root, cp.manifest_relative_path)
        try:
            with open(abs_manifest, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as exc:
            raise ValueError(
                f"Cannot read manifest for snapshot {snapshot_id}: {exc}"
            ) from exc

        max_changelog_id: Optional[int] = manifest.get("max_changelog_id")
        snapshot_dt = cp.created_at
        if snapshot_dt.tzinfo is None:
            snapshot_dt = snapshot_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

        report = UndoReport()

        # --- Guard: ChangeLog truncated past the target ------------------
        # GFS retention truncates ChangeLog rows with id < oldest_snapshot's
        # max_changelog_id. If the target snapshot's max_changelog_id sits
        # BELOW the current minimum ChangeLog id, the entries needed to undo
        # the (target, current_min) range are gone — a ChangeLog-based undo
        # would silently produce a partial rewind (reverse only the surviving
        # subset and report success). The excluded-table timing heuristic and
        # the per-table mutation scan below also key off
        # ``WHERE ChangeLog.id > max_changelog_id``, so both paths are equally
        # unreliable in this state. Escalate to a file-based restore which
        # rebuilds from the snapshot file directly.
        if max_changelog_id is not None:
            min_cl_id = db.run_immediate_read_task(
                lambda session: session.exec(
                    select(func.min(ChangeLog.id))
                ).one_or_none()
            )
            if min_cl_id is not None and min_cl_id > max_changelog_id + 1:
                logger.warning(
                    "UndoService: ChangeLog truncated past snapshot %d "
                    "(min surviving id %d > target max_changelog_id %d + 1) — "
                    "escalating to file-based restore.",
                    snapshot_id,
                    min_cl_id,
                    max_changelog_id,
                )
                try:
                    self._vault.restore_service.restore_full(snapshot_id)
                    report.escalated_to_full_restore = True
                    report.escalated_tables = ["<changelog-truncated>"]
                except Exception as exc:
                    msg = (
                        f"Full restore (ChangeLog truncated past target) failed: {exc}"
                    )
                    logger.error("UndoService: %s", msg, exc_info=True)
                    report.errors.append(msg)
                return report

        # --- Handle excluded tables via timing heuristic -----------------
        from pixlstash.database import CHANGE_LOG_EXCLUDED_TABLES

        def _load_excluded_mutations(session):
            """Find the most recent ChangeLog entry per excluded table."""
            if max_changelog_id is None:
                return {}
            rows = session.exec(
                select(ChangeLog)
                .where(ChangeLog.id > max_changelog_id)
                .where(ChangeLog.table_name.in_(list(CHANGE_LOG_EXCLUDED_TABLES)))
                .order_by(ChangeLog.created_at.desc())
            ).all()
            # Map table_name → most-recent mutation timestamp
            latest: dict[str, datetime] = {}
            for row in rows:
                if row.table_name not in latest:
                    ts = row.created_at
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    latest[row.table_name] = ts
            return latest

        excluded_latest = db.run_immediate_read_task(_load_excluded_mutations)

        # For excluded tables mutated closer to the snapshot than to now:
        # trigger a full restore (which resets those columns to NULL so the
        # WorkPlanner regenerates them).
        excluded_tables_needing_restore: list[str] = []
        for table_name, mutation_ts in excluded_latest.items():
            age_from_cp = (mutation_ts - snapshot_dt).total_seconds()
            age_to_now = (now - mutation_ts).total_seconds()
            if age_from_cp < age_to_now:
                excluded_tables_needing_restore.append(table_name)
                logger.info(
                    "UndoService: excluded table %s: mutation closer to "
                    "snapshot (%.0fs after cp vs %.0fs before now) — will restore",
                    table_name,
                    age_from_cp,
                    age_to_now,
                )
            else:
                logger.info(
                    "UndoService: excluded table %s: mutation closer to now "
                    "(%.0fs after cp vs %.0fs before now) — keeping current values",
                    table_name,
                    age_from_cp,
                    age_to_now,
                )

        if excluded_tables_needing_restore:
            logger.warning(
                "UndoService: escalating undo_to_snapshot(%d) to a full database "
                "restore because excluded table(s) %s were last mutated closer to "
                "the snapshot than to now.",
                snapshot_id,
                excluded_tables_needing_restore,
            )
            try:
                self._vault.restore_service.restore_full(snapshot_id)
                report.escalated_to_full_restore = True
                report.escalated_tables = excluded_tables_needing_restore
            except Exception as exc:
                msg = f"Full restore for excluded tables failed: {exc}"
                logger.error("UndoService: %s", msg, exc_info=True)
                report.errors.append(msg)
            return report

        # --- Undo included-table changes via ChangeLog -------------------
        if max_changelog_id is None:
            logger.info(
                "UndoService: snapshot %d has no max_changelog_id in manifest; "
                "nothing to undo via ChangeLog.",
                snapshot_id,
            )
            return report

        def _load_entries_to_undo(session):
            from pixlstash.database import CHANGE_LOG_EXCLUDED_TABLES

            return session.exec(
                select(ChangeLog)
                .where(ChangeLog.id > max_changelog_id)
                .where(ChangeLog.table_name.notin_(list(CHANGE_LOG_EXCLUDED_TABLES)))
                .order_by(ChangeLog.id.desc())
            ).all()

        all_entries = db.run_immediate_read_task(_load_entries_to_undo)
        if not all_entries:
            logger.info(
                "UndoService: no ChangeLog entries after id=%d; nothing to undo.",
                max_changelog_id,
            )
            return report

        # Group by txn_id preserving reverse-chronological order.
        txns: list[tuple[str, list[ChangeLog]]] = []
        seen: dict[str, list[ChangeLog]] = {}
        for entry in all_entries:
            if entry.txn_id not in seen:
                seen[entry.txn_id] = []
                txns.append((entry.txn_id, seen[entry.txn_id]))
            seen[entry.txn_id].append(entry)

        # Reverse every transaction in a single writer task so the whole undo
        # is one atomic transaction: either it all commits or (on any failure)
        # the worker rolls it back, leaving the database untouched rather than
        # partially undone.
        def _apply_all(session):
            for _txn_id, entries in txns:
                entries_sorted = sorted(
                    entries, key=lambda e: e.seq_in_txn, reverse=True
                )
                self._apply_undo_entries(session, entries_sorted, report)
            session.commit()

        with db.write_reason(f"undo to snapshot {snapshot_id}"):
            try:
                db.run_task(_apply_all, priority=0)
            except Exception as exc:
                # The transaction was rolled back, so nothing was applied.
                logger.error(
                    "UndoService: undo_to_snapshot(%d) rolled back: %s",
                    snapshot_id,
                    exc,
                    exc_info=True,
                )
                report.reverted_row_count = 0
                report.reverted_txn_count = 0
                if not report.errors:
                    report.errors.append(f"Undo rolled back: {exc}")
                return report

        try:
            from pixlstash.event_types import EventType

            self._vault.emit_event(
                EventType.UNDO_APPLIED,
                {
                    "snapshot_id": snapshot_id,
                    "reverted_txn_count": report.reverted_txn_count,
                    "reverted_row_count": report.reverted_row_count,
                },
            )
        except Exception as exc:
            logger.warning("UndoService: failed to emit UNDO_APPLIED: %s", exc)

        return report

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _apply_undo_entries(
        self, session: Session, entries: list, report: UndoReport
    ) -> None:
        """Reverse-apply a list of ChangeLog entries within *session*.

        Does **not** commit — the caller owns the transaction so that an entire
        undo (one transaction or many) commits atomically.  If any entry fails
        to reverse, the error is recorded in ``report.errors`` and re-raised so
        the caller's transaction is rolled back whole: the undo is all-or-nothing
        rather than leaving the database partially reverted.  (Per-entry
        SAVEPOINTs are not used because pysqlite mishandles nested-transaction
        release, which would let already-applied entries leak past a later
        rollback.)

        Args:
            session: Active writer session.
            entries: ChangeLog rows, already sorted in reverse seq_in_txn
                order.
            report: Accumulates counts in place.

        Raises:
            Exception: Re-raised from the first entry that fails to reverse.
        """
        from pixlstash.database import CHANGE_LOG_EXCLUDED_TABLES

        for entry in entries:
            if entry.table_name in CHANGE_LOG_EXCLUDED_TABLES:
                # Excluded tables: no data payload to reverse.  The full
                # restore or NULL-reset path handles their data separately.
                report.reverted_row_count += 1
                continue
            try:
                self._reverse_entry(session, entry)
            except Exception as exc:
                msg = (
                    f"Failed to undo ChangeLog id={entry.id} "
                    f"({entry.op} {entry.table_name} {entry.row_pk_json}): {exc}"
                )
                logger.error("UndoService: %s", msg, exc_info=True)
                report.errors.append(msg)
                raise
            report.reverted_row_count += 1

        report.reverted_txn_count += 1

    def _reverse_entry(self, session: Session, entry: ChangeLog) -> None:
        """Reverse a single ChangeLog row through the ORM.

        - INSERT → delete the row.
        - UPDATE → restore ``before_json`` values.
        - DELETE → re-insert from ``before_json``.

        Reverse-applying via the ORM (rather than raw Core DML) keeps the
        ``before_flush``/``after_flush`` hooks in database.py firing, so the
        undo is itself recorded in the ChangeLog (making it redoable) and
        ``Picture.metadata_hash`` is recomputed for touched rows.

        Args:
            session: Active writer session.
            entry: The ChangeLog row to reverse.
        """
        pk: dict = json.loads(entry.row_pk_json) if entry.row_pk_json else {}
        model = _resolve_model(entry.table_name)
        if model is None:
            raise RuntimeError(f"No ORM model is mapped to table '{entry.table_name}'")

        if entry.op == "INSERT":
            obj = self._get_by_pk(session, model, pk)
            if obj is not None:
                session.delete(obj)

        elif entry.op == "UPDATE":
            if not entry.before_json:
                logger.warning(
                    "UndoService: no before_json for UPDATE ChangeLog id=%d; skipping",
                    entry.id,
                )
                return
            obj = self._get_by_pk(session, model, pk)
            if obj is None:
                logger.warning(
                    "UndoService: row %s of table '%s' not found for UPDATE undo "
                    "(ChangeLog id=%d); skipping",
                    pk,
                    entry.table_name,
                    entry.id,
                )
                return
            before: dict = json.loads(entry.before_json)
            col_map = _column_by_attr(model)
            for key, value in before.items():
                column = col_map.get(key)
                if column is None:
                    continue
                should_set, coerced = _coerce_serialized_value(column, value)
                if should_set:
                    setattr(obj, key, coerced)

        elif entry.op == "DELETE":
            if not entry.before_json:
                logger.warning(
                    "UndoService: no before_json for DELETE ChangeLog id=%d; skipping",
                    entry.id,
                )
                return
            before = json.loads(entry.before_json)
            col_map = _column_by_attr(model)
            kwargs: dict[str, Any] = {}
            for key, value in before.items():
                column = col_map.get(key)
                if column is None:
                    continue
                should_set, coerced = _coerce_serialized_value(column, value)
                if should_set:
                    kwargs[key] = coerced
            session.merge(model(**kwargs))

    @staticmethod
    def _get_by_pk(session: Session, model: type, pk: dict[str, Any]):
        """Load *model* by its primary key, given a {attr-key: value} dict."""
        if not pk:
            return None
        if len(pk) == 1:
            return session.get(model, next(iter(pk.values())))
        return session.get(model, pk)
