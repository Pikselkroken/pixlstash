"""Service layer for undoing recent metadata changes using the ChangeLog.

Provides ``undo_last_transaction()`` (ChangeLog-only) and
``undo_to_checkpoint()`` (hybrid: ChangeLog for included tables, snapshot
for excluded tables based on timing heuristics).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from sqlmodel import Session, select, text

from pixlstash.db_models.change_log import ChangeLog
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


@dataclass
class UndoReport:
    """Summary of a completed undo operation.

    Attributes:
        reverted_txn_count: Number of ChangeLog transactions reversed.
        reverted_row_count: Total ChangeLog rows processed.
        errors: Non-fatal error messages.
    """

    reverted_txn_count: int = 0
    reverted_row_count: int = 0
    errors: list[str] = field(default_factory=list)


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
                select(ChangeLog)
                .order_by(ChangeLog.id.desc())
                .limit(1)
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
        with db.write_reason(f"undo txn {txn_id}"):
            db.run_task(
                lambda session: self._apply_undo_entries(session, entries, report),
                priority=0,
            )

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

    def undo_to_checkpoint(self, checkpoint_id: int) -> UndoReport:
        """Undo all changes back to the state at a given checkpoint.

        Strategy:
        - For **included** tables: walk the ChangeLog backward from the
          current head to the checkpoint's ``max_changelog_id`` (read from the
          manifest sidecar) and reverse-apply each transaction in reverse
          chronological order.
        - For **excluded** tables (regenerable: quality, likeness, embeddings,
          etc.): use a timing heuristic per table.  If the most-recent
          excluded-table mutation happened *closer to the checkpoint* than to
          now, the snapshot's data is considered closer to ground truth and a
          full restore is triggered for those tables via
          ``RestoreService.restore_full()``.  Otherwise the current values are
          kept and the WorkPlanner will re-derive them.

        Args:
            checkpoint_id: The ID of the checkpoint to rewind to.

        Returns:
            An ``UndoReport`` summarising the operation.

        Raises:
            ValueError: If the checkpoint is not found or its manifest is
                missing.
        """
        import os

        db = self._vault.db
        vault_root = self._vault.image_root

        cp = self._vault.checkpoint_service.get_checkpoint(checkpoint_id)
        if cp is None:
            raise ValueError(f"Checkpoint id={checkpoint_id} not found.")

        abs_manifest = os.path.join(vault_root, cp.manifest_relative_path)
        try:
            with open(abs_manifest, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except Exception as exc:
            raise ValueError(
                f"Cannot read manifest for checkpoint {checkpoint_id}: {exc}"
            ) from exc

        max_changelog_id: Optional[int] = manifest.get("max_changelog_id")
        checkpoint_dt = cp.created_at
        if checkpoint_dt.tzinfo is None:
            checkpoint_dt = checkpoint_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)

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

        # For excluded tables mutated closer to the checkpoint than to now:
        # trigger a full restore (which resets those columns to NULL so the
        # WorkPlanner regenerates them).
        excluded_tables_needing_restore: list[str] = []
        for table_name, mutation_ts in excluded_latest.items():
            age_from_cp = (mutation_ts - checkpoint_dt).total_seconds()
            age_to_now = (now - mutation_ts).total_seconds()
            if age_from_cp < age_to_now:
                excluded_tables_needing_restore.append(table_name)
                logger.info(
                    "UndoService: excluded table %s: mutation closer to "
                    "checkpoint (%.0fs after cp vs %.0fs before now) — will restore",
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

        report = UndoReport()

        if excluded_tables_needing_restore:
            logger.info(
                "UndoService: delegating full restore for excluded tables: %s",
                excluded_tables_needing_restore,
            )
            try:
                self._vault.restore_service.restore_full(checkpoint_id)
                report.errors.append(
                    f"Full restore triggered for excluded tables: "
                    f"{excluded_tables_needing_restore}"
                )
            except Exception as exc:
                msg = f"Full restore for excluded tables failed: {exc}"
                logger.error("UndoService: %s", msg, exc_info=True)
                report.errors.append(msg)
            return report

        # --- Undo included-table changes via ChangeLog -------------------
        if max_changelog_id is None:
            logger.info(
                "UndoService: checkpoint %d has no max_changelog_id in manifest; "
                "nothing to undo via ChangeLog.",
                checkpoint_id,
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

        with db.write_reason(f"undo to checkpoint {checkpoint_id}"):
            for txn_id, entries in txns:
                entries_sorted = sorted(entries, key=lambda e: e.seq_in_txn, reverse=True)
                db.run_task(
                    lambda session, _entries=entries_sorted: self._apply_undo_entries(
                        session, _entries, report
                    ),
                    priority=0,
                )

        try:
            from pixlstash.event_types import EventType
            self._vault.emit_event(
                EventType.UNDO_APPLIED,
                {
                    "checkpoint_id": checkpoint_id,
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

        Args:
            session: Active writer session.
            entries: ChangeLog rows, already sorted in reverse seq_in_txn
                order.
            report: Accumulates counts and errors in place.
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
                report.reverted_row_count += 1
            except Exception as exc:
                msg = (
                    f"Failed to undo ChangeLog id={entry.id} "
                    f"({entry.op} {entry.table_name} {entry.row_pk_json}): {exc}"
                )
                logger.error("UndoService: %s", msg, exc_info=True)
                report.errors.append(msg)

        session.commit()
        report.reverted_txn_count += 1

    def _reverse_entry(self, session: Session, entry: ChangeLog) -> None:
        """Reverse a single ChangeLog row.

        - INSERT → delete the row.
        - UPDATE → restore ``before_json`` values.
        - DELETE → re-insert from ``before_json``.

        Args:
            session: Active writer session.
            entry: The ChangeLog row to reverse.
        """
        pk: dict = json.loads(entry.row_pk_json) if entry.row_pk_json else {}

        table = session.get_bind().dialect.identifier_preparer.quote(entry.table_name)
        meta = session.get_bind().dialect

        if entry.op == "INSERT":
            self._delete_by_pk(session, entry.table_name, pk)

        elif entry.op == "UPDATE":
            if not entry.before_json:
                logger.warning(
                    "UndoService: no before_json for UPDATE ChangeLog id=%d; skipping",
                    entry.id,
                )
                return
            before: dict = json.loads(entry.before_json)
            self._update_by_pk(session, entry.table_name, pk, before)

        elif entry.op == "DELETE":
            if not entry.before_json:
                logger.warning(
                    "UndoService: no before_json for DELETE ChangeLog id=%d; skipping",
                    entry.id,
                )
                return
            before = json.loads(entry.before_json)
            self._insert_row(session, entry.table_name, before)

    def _delete_by_pk(
        self, session: Session, table_name: str, pk: dict[str, Any]
    ) -> None:
        where_clauses = " AND ".join(f'"{k}" = :{k}' for k in pk)
        session.exec(
            text(f'DELETE FROM "{table_name}" WHERE {where_clauses}'),
            params=pk,
        )

    def _update_by_pk(
        self,
        session: Session,
        table_name: str,
        pk: dict[str, Any],
        values: dict[str, Any],
    ) -> None:
        non_pk = {k: v for k, v in values.items() if k not in pk}
        if not non_pk:
            return
        set_clauses = ", ".join(f'"{k}" = :set_{k}' for k in non_pk)
        where_clauses = " AND ".join(f'"{k}" = :pk_{k}' for k in pk)
        params = {f"set_{k}": v for k, v in non_pk.items()}
        params.update({f"pk_{k}": v for k, v in pk.items()})
        session.exec(
            text(f'UPDATE "{table_name}" SET {set_clauses} WHERE {where_clauses}'),
            params=params,
        )

    def _insert_row(
        self, session: Session, table_name: str, values: dict[str, Any]
    ) -> None:
        cols = ", ".join(f'"{k}"' for k in values)
        placeholders = ", ".join(f":{k}" for k in values)
        session.exec(
            text(
                f'INSERT OR REPLACE INTO "{table_name}" ({cols}) VALUES ({placeholders})'
            ),
            params=values,
        )
