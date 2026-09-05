"""Scrapheap retention policy and the single permanent-destruction path.

This module owns **everything that permanently destroys a soft-deleted picture**.
Both callers go through :func:`purge_scrapheap_pictures`:

1. the manual, consent-gated ``DELETE /api/v1/pictures/scrapheap`` endpoint
   (``include_protected`` chosen by the user), and
2. the scheduled auto-purge (``ScrapheapRetentionPurgeTask``), which *always*
   passes ``include_protected=False``.

There is deliberately **no second destruction path**: the retention timer reuses
the existing skip-protected branch, so a protected reference-folder original
(``ReferenceFolder.allow_delete_file=False``) can only ever be destroyed by an
explicit ``include_protected=true`` request from a human.

Retention policy (settled with the maintainer, do not redesign):

* The retention window governs **unprotected (managed) pictures only**.
* Protected reference-folder originals are **exempt from any timer**
  (``auto_purge_exempt=True``, ``purge_at=None``).
* ``scrapheap_retention_days`` is one of :data:`RETENTION_DAY_CHOICES` or
  ``None`` ("Never" - auto-purge is disabled entirely).
* **Auto-purge is OFF until the user turns it on.** The default is ``None``, and
  the server-config key is written only by an explicit save, so a fresh install
  and an install upgraded from a release without the setting both land on
  "Never". Nothing is ever removed from disk on a timer the user did not choose.
  A stored value that cannot be parsed also resolves to "Never" rather than to a
  window, so an unreadable config can never license a deletion.
* Lowering the window gives EVERY picture a :data:`REDUCTION_GRACE_DAYS`-day
  reprieve measured from the reduction itself, not from the picture's own
  ``deleted_at``. The deadline is
  ``max(deleted_at + retention_days, reduced_at + REDUCTION_GRACE_DAYS)``.
  The floor is what makes the grace real: measuring the grace from
  ``deleted_at`` would only help pictures sitting in the narrow
  ``[retention_days, retention_days + 1)`` band, so a ``Never -> 30`` or
  ``120 -> 30`` change would still destroy a long-lived scrapheap on the very
  next 15-minute sweep - seconds after a dropdown that saves on change with no
  confirmation. With the floor, **no picture can be purged within a day of a
  lowering, regardless of age**, which is the promise the settings copy makes.
* ``Never -> <finite>`` counts as a reduction (Never is an infinite window), so
  it grants the grace too. This is deliberately the safer reading: it is the
  single most destructive transition available in the UI.
* A soft-deleted picture with no ``deleted_at`` is **never** auto-purged
  (fail-closed: no timestamp, no deadline).
* ``deleted_file_log.file_removed=True`` is written BEFORE the file is touched
  (writing it after would leave a window where the picture row is gone with no
  ledger entry - how the reference-folder scan resurrects deleted content), so
  it is a PREDICTION until the removal succeeds. If ``os.remove`` fails, or the
  location is unreachable and we cannot tell, the row is corrected to
  ``file_removed=False`` - "removed from library, file kept" - so restore can
  still resurrect the picture. That is the only True -> False transition in the
  ledger; everywhere else the flag may only be raised. It is bounded to the rows
  THAT purge created or raised (:func:`purge_rows_in_session` returns the set):
  the ledger is keyed by path, not by picture, so an unbounded correction could
  retract an *earlier* purge's genuine deletion of different content that once
  lived at the same path, and restore would then resurrect it bound to the wrong
  file.
* A picture that has LEFT the scrapheap by the time the rows are deleted is
  never destroyed. Selection, planning and deletion run in ONE DB-queue
  submission (:func:`plan_and_purge_in_session`) so no other write can land in
  between, and :func:`purge_rows_in_session` re-checks ``deleted`` in the same
  session that issues the DELETE, so the guarantee survives any future change to
  how the work is scheduled. The skipped ids are logged and reported as
  ``skipped_restored``; their rows, files and ledger entries are untouched.
* The endpoint is gated by a server-side ``confirm_token`` minted by
  ``POST /pictures/scrapheap/delete-preview`` (see
  :class:`ScrapheapDeleteConfirmations`). The type-to-confirm dialog is a client
  control and proves nothing. This is an INTENT control; authorization stays with
  the AuthzGate. The unattended sweep calls :func:`purge_scrapheap_pictures`
  directly and needs no confirmation.
* The deadline is enforced **twice**, mirroring the two-layer protected-original
  defence: once when the finder selects candidates
  (:func:`find_due_retention_picture_ids_in_session`) and again inside
  :func:`build_purge_plan` via a :class:`RetentionGuard`, so a finder bug - or a
  restore/re-delete that resets ``deleted_at`` between planning and the
  LOW-priority task actually running - cannot destroy an in-window picture.
* Pictures frozen by a locked picture-set - directly, or via a live stack
  sibling - are excluded from **every** destruction path, the manual
  ``include_protected=true`` delete-forever included. A locked set is a hard
  whole-set freeze: ``DELETE /pictures/{id}`` refuses with 423 and the bulk
  soft-delete skips, so neither a timer nor the one IRREVERSIBLE path may do what
  the reversible interactive paths forbid. Enforced unconditionally in
  :func:`build_purge_plan` (skip-and-report, never raise, so one frozen member
  cannot fail a batch) and reported as ``skipped_locked``.
* The scrapheap listing applies **both** exemptions through the same helpers the
  sweep uses (:func:`fetch_no_delete_folder_ids` and
  :func:`locked_scrapheap_picture_ids`), so the ``purge_at`` countdown the UI
  renders can never promise a deletion the sweep will not perform.
  ``auto_purge_exempt_reason`` names which exemption applies.
"""

import math
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import and_, delete, or_, update
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Character, DeletedFileLog, Picture, ReferenceFolder
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.path_utils import path_is_within
from pixlstash.utils.service.scope_table import scope_id_subquery

logger = get_logger(__name__)

# The retention windows the UI offers. ``None`` ("Never") is also valid and
# disables auto-purge entirely.
RETENTION_DAY_CHOICES: tuple[int, ...] = (30, 60, 90, 120)

# Default when server-config carries no explicit value: ``None`` - "Never",
# auto-purge OFF. This is a CONSENT default, not a tuning knob. An unattended
# timer that permanently removes files from disk must be something the user
# switched on, so an install that has never been asked (a fresh install, or one
# upgraded from a release that had no such setting) is never on the clock. The
# key is written to server-config ONLY by ``apply_retention_config``, i.e. only
# by an explicit save, so "absent" reliably means "never chosen" and an existing
# explicit choice is preserved across the upgrade.
DEFAULT_RETENTION_DAYS: Optional[int] = None

# Extra days granted to pictures that were already in the scrapheap when the
# retention window was *lowered*, so a reduction never purges anything the same
# day it is applied.
REDUCTION_GRACE_DAYS: int = 1

# Rows per page when scanning past-deadline candidates. Also the floor for the
# over-fetch: protected/locked rows are filtered out AFTER the SQL window, so a
# page sized exactly to the caller's limit could yield fewer due ids than the
# old full-table scan did.
_DUE_SCAN_PAGE: int = 500

# ``auto_purge_exempt_reason`` values. A picture can be frozen by both; the
# reference-folder protection is the stronger, permanent one and wins.
EXEMPT_PROTECTED = "protected"
EXEMPT_LOCKED = "locked"

# Server-config keys.
RETENTION_DAYS_KEY = "scrapheap_retention_days"
RETENTION_REDUCED_AT_KEY = "scrapheap_retention_reduced_at"


@dataclass(frozen=True)
class ScrapheapRow:
    """One soft-deleted picture as needed by the purge / retention maths."""

    id: Optional[int]
    file_path: Optional[str]
    reference_folder_id: Optional[int]
    pixel_sha: Optional[str]
    deleted_at: Optional[datetime]

    def is_protected(self, no_delete_folder_ids: set[int]) -> bool:
        """Whether this row is a reference original whose file must be kept."""
        return (
            self.reference_folder_id is not None
            and self.reference_folder_id in no_delete_folder_ids
        )


@dataclass
class ScrapheapPurgePlan:
    """What a purge call will destroy, decided before anything is touched."""

    # Picture ids whose rows will be deleted.
    picture_ids: list[int] = field(default_factory=list)
    # ``(picture_id, relative_file_path, was_reference_protected)`` for files to
    # remove from disk.
    removal_targets: list[tuple[Optional[int], str, bool]] = field(default_factory=list)
    # ``deleted_file_log`` rows to write in the same transaction as the delete.
    log_records: list[dict] = field(default_factory=list)
    # Protected originals left completely intact (row + file kept, no ledger row).
    skipped_count: int = 0
    # Ids frozen by a locked picture-set, left completely intact. Applies to
    # EVERY path - a lock outranks even an explicit include_protected=true.
    skipped_locked: list[int] = field(default_factory=list)
    # Pictures the RetentionGuard held back - not yet past their deadline.
    # Always 0 on the manual (unguarded) path.
    retained_count: int = 0


@dataclass
class ScrapheapPurgeOutcome:
    """Result of a purge call."""

    deleted_count: int = 0
    skipped_count: int = 0
    skipped_locked: list[int] = field(default_factory=list)
    retained_count: int = 0
    purged_ids: list[int] = field(default_factory=list)
    # Ids that were planned for destruction but were NOT destroyed: they had
    # left the scrapheap by the time the DELETE ran (restored concurrently, or
    # already purged), or the whole batch was rolled back because the guarded
    # DELETE and the re-check disagreed. Their rows, files and ledger entries
    # are untouched - the caller drops their file removals on the strength of
    # this list.
    skipped_restored: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class RetentionGuard:
    """Independent re-check of the automatic path's preconditions.

    The finder already filters candidates, but a single check is a single point
    of failure for an automatic file-destruction path - the same reasoning that
    put the protected-original check in BOTH the finder query and
    :func:`build_purge_plan`. This guard re-derives the deadline from the row's
    CURRENT ``deleted_at`` at purge time, which also closes a real
    time-of-check/time-of-use window: the purge task runs at ``TaskPriority.LOW``
    and can be queued behind other work, so a picture restored and re-deleted in
    between would otherwise be destroyed on a ``deleted_at`` only seconds old.

    Present only on the automatic path. The manual, consent-gated delete-forever
    passes ``None`` - a human asking for immediate deletion is not subject to a
    retention timer.

    This guard covers the DEADLINE only. The locked-set freeze is enforced
    unconditionally by :func:`build_purge_plan` instead, because it binds on
    every path (manual and automatic) rather than only on the timer.

    Attributes:
        now: The instant the sweep is evaluating against.
        retention_days: Configured window, or None for "Never".
        reduced_at: When the window was last lowered, or None.
    """

    now: datetime
    retention_days: Optional[int]
    reduced_at: Optional[datetime]

    def permits(self, row: "ScrapheapRow") -> tuple[bool, str]:
        """Whether ``row``'s deadline has passed; also the reason if not."""
        purge_at = compute_purge_at(
            row.deleted_at, self.retention_days, self.reduced_at, is_protected=False
        )
        if purge_at is None:
            return False, "has no auto-purge deadline (no deleted_at, or Never)"
        if purge_at > _as_utc(self.now):
            return False, f"still inside its retention window (due {purge_at})"
        return True, ""


# ── Retention maths (pure) ────────────────────────────────────────────────────


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Normalise a possibly-naive datetime to an aware UTC datetime.

    SQLite round-trips ``DateTime`` columns as naive values; the retention maths
    compares them against ``datetime.now(timezone.utc)``, so a naive value is
    interpreted as UTC (which is how every writer in this codebase stores it).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _naive_utc(value: datetime) -> datetime:
    """Drop the tzinfo from an aware UTC datetime for a SQL bind parameter.

    SQLAlchemy's SQLite ``DateTime`` stores naive strings (the offset is dropped
    on write), so every ``picture.deleted_at`` in the DB is naive UTC. Comparing
    a column against an AWARE bind parameter would render a differently-shaped
    literal and silently match nothing.
    """
    aware = _as_utc(value)
    return aware.replace(tzinfo=None)


def retention_rank(days: Optional[int]) -> float:
    """Order retention windows so ``None`` ("Never") sorts as the largest."""
    return math.inf if days is None else float(days)


def is_retention_reduction(
    current_days: Optional[int], new_days: Optional[int]
) -> bool:
    """Whether moving ``current_days`` -> ``new_days`` shortens the window.

    ``None`` ("Never") is treated as an infinite window, so ``Never -> 90`` is a
    reduction while ``30 -> 60`` and ``90 -> Never`` are not.

    Since the default is ``None`` (auto-purge off), **turning auto-empty on for
    the first time is a reduction** and therefore earns the grace floor: nothing
    already in the scrapheap can be destroyed within
    :data:`REDUCTION_GRACE_DAYS` of the switch-on, however old it is. That is
    deliberate - enabling an unattended destruction path is the single most
    consequential change the control offers, so it gets the same reprieve (and
    the same impact confirm) as shortening an existing window.
    """
    return retention_rank(new_days) < retention_rank(current_days)


def reduction_grace_floor(reduced_at: Optional[datetime]) -> Optional[datetime]:
    """Earliest instant ANY picture may be auto-purged after a window lowering.

    Returns ``reduced_at + REDUCTION_GRACE_DAYS``, or ``None`` when the window
    has never been lowered. This is a floor on every deadline, not a per-picture
    extension - see the module docstring for why the distinction is the whole
    safety property.
    """
    reduced_at_utc = _as_utc(reduced_at)
    if reduced_at_utc is None:
        return None
    return reduced_at_utc + timedelta(days=REDUCTION_GRACE_DAYS)


def auto_purge_exemption(is_protected: bool, is_locked: bool) -> Optional[str]:
    """Why a scrapheap picture is exempt from the timer, or ``None``.

    ``"protected"`` (a reference-folder original with ``allow_delete_file=False``)
    outranks ``"locked"``: the protection is permanent and intrinsic to the
    picture, whereas a lock is a state the user can clear. Labelling a merely
    locked picture "Protected" would misdescribe why it is being kept.
    """
    if is_protected:
        return EXEMPT_PROTECTED
    if is_locked:
        return EXEMPT_LOCKED
    return None


def compute_purge_at(
    deleted_at: Optional[datetime],
    retention_days: Optional[int],
    reduced_at: Optional[datetime],
    is_protected: bool,
    is_locked: bool = False,
) -> Optional[datetime]:
    """UTC instant at which a scrapheap picture becomes eligible for auto-purge.

    The deadline is ``max(deleted_at + retention_days, reduced_at + grace)``.
    The second term is a FLOOR measured from the reduction, so lowering the
    window never makes anything purgeable within the grace period no matter how
    old it is - a 400-day-old picture and a 31-day-old one both get the full
    reprieve from a ``120 -> 30`` or ``Never -> 30`` change.

    For a picture soft-deleted *after* the reduction the floor is inert
    (``deleted_at >= reduced_at`` and ``retention_days > grace``), so applying it
    unconditionally costs nothing and removes a branch that could be got wrong.

    Returns ``None`` when the sweep will never auto-purge the picture: it is a
    protected reference original, it is frozen by a locked picture-set, retention
    is "Never", or it carries no ``deleted_at`` stamp. Keeping the locked case
    HERE - rather than only in the finder - is what stops the listing from
    advertising a deadline the sweep will never act on.
    """
    if is_protected or is_locked:
        return None
    if retention_days is None or deleted_at is None:
        return None
    deadline = _as_utc(deleted_at) + timedelta(days=int(retention_days))
    floor = reduction_grace_floor(reduced_at)
    if floor is not None and floor > deadline:
        return floor
    return deadline


# ── Server-config read/write ──────────────────────────────────────────────────


def read_retention_days(server_config: dict) -> Optional[int]:
    """Read ``scrapheap_retention_days`` from a server-config dict.

    An absent key means :data:`DEFAULT_RETENTION_DAYS` - ``None``, "Never",
    auto-purge OFF. Only :func:`apply_retention_config` ever writes the key, so
    "absent" means the user has never been asked and nothing is destroyed on a
    timer; an explicit value (including an explicit ``30``) is a deliberate
    choice and is honoured.

    An unrecognised value also resolves to "Never" and is logged rather than
    silently accepted. That is the fail-safe direction: a config we cannot parse
    must not be read as a licence to delete files.
    """
    if RETENTION_DAYS_KEY not in server_config:
        return DEFAULT_RETENTION_DAYS
    raw = server_config.get(RETENTION_DAYS_KEY)
    if raw is None:
        return None
    try:
        days = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "server-config %s=%r is not an integer; disabling the scrapheap "
            "auto-purge (treating it as Never) until a valid window is saved",
            RETENTION_DAYS_KEY,
            raw,
        )
        return DEFAULT_RETENTION_DAYS
    if days not in RETENTION_DAY_CHOICES:
        logger.warning(
            "server-config %s=%r is not one of %s; disabling the scrapheap "
            "auto-purge (treating it as Never) until a valid window is saved",
            RETENTION_DAYS_KEY,
            raw,
            RETENTION_DAY_CHOICES,
        )
        return DEFAULT_RETENTION_DAYS
    return days


def read_retention_reduced_at(server_config: dict) -> Optional[datetime]:
    """Read ``scrapheap_retention_reduced_at`` (ISO 8601) from server-config."""
    raw = server_config.get(RETENTION_REDUCED_AT_KEY)
    if not raw:
        return None
    if isinstance(raw, datetime):
        return _as_utc(raw)
    try:
        return _as_utc(datetime.fromisoformat(str(raw)))
    except ValueError:
        logger.warning(
            "server-config %s=%r is not an ISO 8601 timestamp; treating the "
            "retention window as never reduced (no grace day)",
            RETENTION_REDUCED_AT_KEY,
            raw,
        )
        return None


def apply_retention_config(
    server_config: dict, new_days: Optional[int], now: Optional[datetime] = None
) -> tuple[Optional[int], Optional[datetime]]:
    """Write a new retention window into ``server_config`` in place.

    ``scrapheap_retention_reduced_at`` is stamped **only** when the window is
    lowered (see :func:`is_retention_reduction`); a raise, a first explicit set,
    or a no-op save leaves the existing value untouched. Nothing is purged here
    - the timer is the only thing that ever destroys a file.

    Returns:
        The ``(retention_days, reduced_at)`` pair now in effect.
    """
    current_days = read_retention_days(server_config)
    reduced_at = read_retention_reduced_at(server_config)
    if is_retention_reduction(current_days, new_days):
        reduced_at = _as_utc(now or datetime.now(timezone.utc))
        server_config[RETENTION_REDUCED_AT_KEY] = reduced_at.isoformat()
        logger.info(
            "Scrapheap retention lowered %s -> %s days; stamping %s=%s "
            "(pre-existing scrapheap items get +%d grace day)",
            current_days,
            new_days,
            RETENTION_REDUCED_AT_KEY,
            reduced_at.isoformat(),
            REDUCTION_GRACE_DAYS,
        )
    else:
        logger.info(
            "Scrapheap retention set %s -> %s days (not a reduction; %s untouched)",
            current_days,
            new_days,
            RETENTION_REDUCED_AT_KEY,
        )
    server_config[RETENTION_DAYS_KEY] = new_days
    return new_days, reduced_at


# ── Session-scoped DB work ────────────────────────────────────────────────────


def fetch_scrapheap_rows_in_session(
    session: Session, ids: Optional[list[int]]
) -> list[ScrapheapRow]:
    """Return the soft-deleted pictures in ``ids`` (all of them when ``None``)."""
    query = select(
        Picture.id,
        Picture.file_path,
        Picture.reference_folder_id,
        Picture.pixel_sha,
        Picture.deleted_at,
    ).where(Picture.deleted.is_(True))
    if ids is not None:
        scope = scope_id_subquery(session, ids, name="_pixlstash_scrapheap_row_ids")
        query = query.where(Picture.id.in_(scope))
    return [ScrapheapRow(*row) for row in session.exec(query).all()]


def fetch_no_delete_folder_ids_in_session(session: Session) -> set[int]:
    """Ids of reference folders whose original files are protected on disk
    (``allow_delete_file=False``)."""
    result = session.exec(
        select(ReferenceFolder.id).where(
            ReferenceFolder.allow_delete_file.is_(False),
        )
    ).all()
    return {r for r in result if r is not None}


def still_scrapheaped_ids_in_session(
    session: Session, picture_ids: list[int]
) -> set[int]:
    """Which of ``picture_ids`` are, RIGHT NOW, still soft-deleted."""
    ids = [int(pid) for pid in picture_ids if pid is not None]
    scope = scope_id_subquery(session, ids, name="_pixlstash_still_scrapheaped_ids")
    rows = session.exec(
        select(Picture.id).where(Picture.id.in_(scope), Picture.deleted.is_(True))
    ).all()
    return {int(pid) for pid in rows if pid is not None}


def existing_picture_ids_in_session(
    session: Session, picture_ids: list[int]
) -> set[int]:
    """Which of ``picture_ids`` still have a row at all.

    Used immediately after the guarded DELETE, inside the same transaction, to
    learn which ids it ACTUALLY removed. ``rowcount`` gives a total but not an
    identity, and the file removal has to be driven by identity - see
    :func:`purge_rows_in_session`.
    """
    ids = [int(pid) for pid in picture_ids if pid is not None]
    scope = scope_id_subquery(session, ids, name="_pixlstash_existing_picture_ids")
    rows = session.exec(select(Picture.id).where(Picture.id.in_(scope))).all()
    return {int(pid) for pid in rows if pid is not None}


def purge_rows_in_session(
    session: Session, picture_ids: list[int], log_records: list[dict]
) -> tuple[int, set[str], set[int]]:
    """Write the permanent-deletion ledger and delete the picture rows.

    Logged and deleted in the same transaction so the two can never diverge.

    **The ``deleted`` predicate is re-evaluated HERE, in the same session that
    issues the DELETE, and it is the load-bearing safety property of this
    module.** The purge selects its ids while the pictures are scrapheaped and
    then deletes them BY ID; a restore committed in between makes those ids live
    again, and an unqualified ``DELETE ... WHERE id IN (...)`` would hard-delete
    the rows the user just rescued, remove their files from disk, and leave a
    ``file_removed=True`` ledger row so even a snapshot restore drops them
    forever. :func:`plan_and_purge_in_session` already collapses planning and
    deletion into ONE DB-queue submission so nothing can interleave, but that is
    a property of the CALLER and a future refactor can lose it; the re-check is
    the belt that does not depend on scheduling.

    **Exactly what that belt delivers - do not over-read it.** It is authoritative
    for the row, the file and the ledger *together*, and only because all three
    are derived from the same answer:

    * The re-check narrows the batch to ids that are still soft-deleted. Ids that
      have left the scrapheap get no ledger row and are not deleted.
    * **The guarded DELETE, not the re-check, decides what was destroyed.** Its
      own ``deleted`` predicate can still spare a row that the re-check blessed
      moments earlier, so the removed set is read back from the database after
      the DELETE. Deriving the skips from the re-check alone would save the ROW
      but still unlink the FILE and leave the ledger asserting a permanent
      deletion that never happened - a live picture with no original on disk and
      a ``file_removed=True`` path that neither restore nor a re-scan recovers.
    * If the two ever disagree, the WHOLE batch is rolled back: no rows deleted,
      no ledger rows written, no files unlinked, ``deleted_count`` 0, every id
      reported as skipped. An irreversible path fails closed, loudly, rather than
      committing a partial result it cannot describe.

    Returns:
        ``(deleted_count, owned_path_shas, skipped_ids)``. ``deleted_count`` is
        the number of rows the DELETE actually removed. ``owned_path_shas``
        is every ``path_sha`` THIS call created or raised to
        ``file_removed=True``. It is the write-ownership token for
        :func:`mark_files_kept_in_session`: only a row this purge is responsible
        for may later be corrected back to ``False``. Rows that were already
        ``True`` before this call are excluded - see that function for the
        collision this prevents. ``skipped_ids`` are every requested id that was
        NOT destroyed; the caller MUST drop their file removals.
    """
    if not picture_ids:
        return 0, set(), set()
    requested = {int(pid) for pid in picture_ids if pid is not None}
    purgeable = still_scrapheaped_ids_in_session(session, list(requested))
    skipped_ids = requested - purgeable
    if skipped_ids:
        logger.warning(
            "Delete-forever: SKIPPING %d picture id(s) that left the scrapheap "
            "between planning and deletion (restored, or already purged): %s - "
            "their rows, files and ledger entries are untouched. Nothing is "
            "lost; re-run the purge if they are scrapheaped again.",
            len(skipped_ids),
            sorted(skipped_ids),
        )
    if not purgeable:
        return 0, set(), skipped_ids
    now = datetime.now(timezone.utc)
    owned_path_shas: set[str] = set()
    for record in log_records:
        record_id = record.get("picture_id")
        if record_id is not None and int(record_id) not in purgeable:
            # Its picture is no longer being destroyed, so asserting a permanent
            # deletion for its path would be a lie that restore acts on.
            continue
        path_sha = record.get("path_sha")
        if not path_sha:
            continue
        already_logged = session.exec(
            select(DeletedFileLog).where(DeletedFileLog.path_sha == path_sha)
        ).first()
        new_file_removed = record.get("file_removed", True)
        if already_logged is None:
            session.add(
                DeletedFileLog(
                    path_sha=path_sha,
                    pixel_sha=record.get("pixel_sha"),
                    deleted_at=now,
                    file_removed=new_file_removed,
                )
            )
            owned_path_shas.add(path_sha)
        elif new_file_removed and not already_logged.file_removed:
            # A path first logged file_removed=False (protected file kept on
            # disk) is now being genuinely hard-deleted. Upgrade the stale flag
            # to True so the ledger stays truthful rather than leaving a False
            # row that only restore's missing-file net would catch. Only ever
            # raise False -> True; never downgrade a genuine permanent deletion
            # back to "kept".
            already_logged.file_removed = True
            session.add(already_logged)
            owned_path_shas.add(path_sha)
        # else: the row was ALREADY file_removed=True before this call - it
        # records some earlier purge's permanently destroyed content, not ours.
        # Deliberately NOT owned, so a failed removal here can never reach back
        # and downgrade it.
    purgeable_ids = sorted(purgeable)
    purge_scope = scope_id_subquery(
        session, purgeable_ids, name="_pixlstash_purge_picture_ids"
    )
    # ``deleted`` is repeated in the DELETE itself, not just in the SELECT
    # above: belt-and-braces against anything that could commit between the
    # two statements in this same session.
    # Clear any character thumbnail pinned to a picture that is about to stop
    # existing. ``Character.thumbnail_picture_id`` carries no foreign key on
    # purpose (a real one would abort this DELETE), and SQLite reuses rowids, so
    # a pin left behind can silently reattach to whatever picture is imported
    # next into that id.
    session.exec(
        update(Character)
        .where(Character.thumbnail_picture_id.in_(purge_scope))
        .values(thumbnail_picture_id=None)
    )
    session.exec(
        delete(Picture).where(Picture.id.in_(purge_scope), Picture.deleted.is_(True))
    )
    # The DELETE is authoritative, not the re-check above. Read the removed set
    # back BEFORE committing: a row its predicate spared must keep its file and
    # must not be ledgered, which needs identity, not a rowcount.
    removed_ids = purgeable - existing_picture_ids_in_session(session, purgeable_ids)
    spared_by_the_delete = purgeable - removed_ids
    if spared_by_the_delete:
        # Only reachable if something committed between the SELECT and the
        # DELETE in this same session, which the single DB worker thread makes
        # impossible today. Fail the WHOLE batch closed rather than commit a
        # ledger that asserts deletions which did not happen: this is the one
        # irreversible path, and a partial result here is the exact shape of the
        # snapshot-restore data loss this release already had to fix.
        session.rollback()
        logger.error(
            "Delete-forever: ABORTED and rolled back - the guarded DELETE "
            "spared %d of the %d id(s) the re-check had selected (%s), so the "
            "batch could not be described honestly. NOTHING was destroyed: no "
            "rows deleted, no permanent-deletion ledger rows written, no files "
            "removed. Re-run the purge.",
            len(spared_by_the_delete),
            len(purgeable_ids),
            sorted(spared_by_the_delete),
        )
        return 0, set(), requested
    session.commit()
    return len(removed_ids), owned_path_shas, skipped_ids


def locked_scrapheap_picture_ids_in_session(session: Session, picture_ids) -> set[int]:
    """Run :func:`locked_picture_ids` - THE lock lookup for the scrapheap.

    Both the auto-purge finder and the scrapheap listing go through here so the
    countdown the UI renders and the decision the sweep makes can never disagree
    about which pictures are frozen (including the live-stack-sibling case that
    ``locked_picture_ids`` resolves).
    """
    return locked_picture_ids(session, picture_ids)


def locked_scrapheap_picture_ids(vault, picture_ids) -> set[int]:
    """Vault wrapper for :func:`locked_scrapheap_picture_ids_in_session`."""
    return vault.db.run_immediate_read_task(
        locked_scrapheap_picture_ids_in_session, picture_ids
    )


def _due_candidate_rows_in_session(
    session: Session,
    cutoff: datetime,
    after: Optional[tuple[datetime, int]],
    limit: int,
) -> list[ScrapheapRow]:
    """Scrapheap rows whose ``deleted_at`` is at or before ``cutoff``.

    The deadline lives in SQL so ``ix_picture_deleted_at`` (added by migration
    0079, and until now used by no query) actually earns its keep, instead of
    loading the whole scrapheap into Python every sweep.

    Ordering by ``(deleted_at, id)`` rather than ``id`` is what makes the index
    usable: ordered by ``id``, SQLite instead walks ``ix_picture_deleted`` over
    EVERY scrapheap row and filters the date. On a 200k-picture library with a
    20k scrapheap of which 500 are due, that is 1.23 ms/page versus 0.08 ms/page
    - and it degrades with scrapheap size rather than with the due count.

    Keyset-paginated on the full ``(deleted_at, id)`` tuple, not on
    ``deleted_at`` alone: a bulk soft-delete stamps one identical ``deleted_at``
    across the whole batch, so paginating on the timestamp alone would skip or
    repeat rows inside such a group.

    ``cutoff`` must be NAIVE UTC: SQLAlchemy's SQLite ``DateTime`` drops the
    offset on write, so every stored ``deleted_at`` is naive and an aware bind
    parameter would compare against a differently-formatted string.
    """
    query = select(
        Picture.id,
        Picture.file_path,
        Picture.reference_folder_id,
        Picture.pixel_sha,
        Picture.deleted_at,
    ).where(
        Picture.deleted.is_(True),
        # A soft-deleted row with no stamp has no deadline and is never
        # auto-purged (fail-closed), so it must not even be a candidate.
        # (SQL already drops NULLs from the <= comparison; stated explicitly so
        # the fail-closed rule is visible in the query itself.)
        Picture.deleted_at.is_not(None),
        Picture.deleted_at <= cutoff,
    )
    if after is not None:
        after_at, after_id = after
        query = query.where(
            or_(
                Picture.deleted_at > after_at,
                and_(Picture.deleted_at == after_at, Picture.id > after_id),
            )
        )
    query = query.order_by(Picture.deleted_at, Picture.id).limit(limit)
    return [ScrapheapRow(*row) for row in session.exec(query).all()]


def _scan_due_rows_in_session(
    session: Session,
    cutoff: datetime,
    limit: Optional[int],
    on_due: Callable[[ScrapheapRow], None],
) -> None:
    """Walk past-deadline rows, skipping protected and locked ones.

    Shared by the sweep's candidate query and the retention-impact count so the
    two cannot disagree about who is eligible. ``limit`` of ``None`` means "walk
    every candidate" (the count); an int stops after that many due rows.

    Pages are over-fetched relative to ``limit`` because protected/locked rows
    are filtered out AFTER the SQL window, so a page of exactly ``limit`` rows
    could return fewer due ids than the previous full-table scan did.
    """
    no_delete_folder_ids = fetch_no_delete_folder_ids_in_session(session)
    page = _DUE_SCAN_PAGE if limit is None else max(int(limit) * 4, _DUE_SCAN_PAGE)
    cursor: Optional[tuple[datetime, int]] = None
    found = 0
    while limit is None or found < limit:
        rows = _due_candidate_rows_in_session(session, cutoff, cursor, page)
        if not rows:
            return
        last = rows[-1]
        cursor = (last.deleted_at, int(last.id))
        locked_ids = locked_scrapheap_picture_ids_in_session(
            session, [r.id for r in rows if r.id is not None]
        )
        for row in rows:
            if row.id is None:
                continue
            if row.is_protected(no_delete_folder_ids):
                continue
            if int(row.id) in locked_ids:
                # A locked picture-set is a hard whole-set freeze: DELETE
                # /pictures/{id} refuses it with 423, so an unattended timer must
                # not silently destroy it either. Unlock the set to let it expire.
                logger.info(
                    "Scrapheap auto-purge: SKIPPING picture id=%s - frozen by a "
                    "locked picture-set; it will not be auto-purged until "
                    "unlocked",
                    row.id,
                )
                continue
            on_due(row)
            found += 1
            if limit is not None and found >= limit:
                return
        if len(rows) < page:
            return


def find_due_retention_picture_ids_in_session(
    session: Session,
    now: datetime,
    retention_days: Optional[int],
    reduced_at: Optional[datetime],
    limit: int,
) -> list[int]:
    """Ids of UNPROTECTED, UNLOCKED soft-deleted pictures that are past deadline.

    Protected reference originals and locked-set members are filtered out here
    AND (for the lock) re-checked unconditionally in the purge plan; the timer
    must never even select them. Locked pictures are skipped and logged rather
    than raising: this runs in a background sweep, so one frozen member must not
    abort the batch (same convention as ``reference_folder_scan_task.py``).

    The deadline itself is evaluated in SQL. That is a pure performance change,
    equivalent to the previous row-by-row :func:`compute_purge_at` filter:

    * ``compute_purge_at`` is ``max(deleted_at + retention_days, floor)``, so
      while ``now < floor`` NOTHING can be due - hence the early return, which
      also skips the scan entirely during a reduction's grace period.
    * Once ``now >= floor``, the floor term can never be the binding one, so
      ``purge_at <= now`` reduces exactly to
      ``deleted_at <= now - retention_days``.
    * ``purge_at is None`` (for a non-None window) means ``deleted_at is None``,
      which the query excludes.
    """
    if retention_days is None or limit <= 0:
        return []
    now_utc = _as_utc(now)
    floor = reduction_grace_floor(reduced_at)
    if floor is not None and now_utc < floor:
        logger.debug(
            "Scrapheap auto-purge: inside the reduction grace floor (now=%s < "
            "%s); nothing can be due, skipping the scan",
            now_utc,
            floor,
        )
        return []
    cutoff = now_utc - timedelta(days=int(retention_days))
    due: list[int] = []

    def _record(row: ScrapheapRow) -> None:
        logger.info(
            "Scrapheap auto-purge: picture id=%s path=%s is due "
            "(deleted_at=%s cutoff=%s now=%s retention_days=%s reduced_at=%s)",
            row.id,
            row.file_path,
            _as_utc(row.deleted_at),
            cutoff,
            now_utc,
            retention_days,
            _as_utc(reduced_at),
        )
        due.append(int(row.id))

    _scan_due_rows_in_session(session, _naive_utc(cutoff), limit, _record)
    return due


def retention_impact_in_session(
    session: Session,
    now: datetime,
    candidate_days: Optional[int],
    current_days: Optional[int],
) -> dict:
    """How much a retention change would destroy, WITHOUT applying it.

    Pure read: nothing is written, no ``reduced_at`` is stamped, no purge is
    scheduled. It exists so the settings UI can confirm before a lowering rather
    than silently destroying a long-lived scrapheap on a dropdown change.

    ``would_purge_count`` is evaluated at the instant the change would first
    bite - ``now + REDUCTION_GRACE_DAYS``, the grace floor a reduction installs -
    not at ``now``. Evaluating at ``now`` would EXCLUDE pictures that expire
    during the grace day and so understate destruction, which is a consent bug
    in a number whose only job is to inform consent.

    Only a reduction is reported: raising the window, switching to Never, or
    re-saving the same value destroys nothing NEW, so the count is 0 and the UI
    shows no confirmation. Turning auto-purge ON from the Never default IS a
    reduction, so it gets a real count - which is the point: enabling the timer
    is the one change that can expose an entire long-lived scrapheap at once.
    """
    if not is_retention_reduction(current_days, candidate_days):
        return {"would_purge_count": 0, "first_purge_at": None}
    # candidate_days is finite here: None ("Never") ranks as infinite and can
    # never be a reduction.
    now_utc = _as_utc(now)
    first_purge_at = now_utc + timedelta(days=REDUCTION_GRACE_DAYS)
    # max(deleted_at + candidate_days, first_purge_at) <= first_purge_at
    #   <=> deleted_at <= first_purge_at - candidate_days
    cutoff = first_purge_at - timedelta(days=int(candidate_days))
    count = 0

    def _count(_row: ScrapheapRow) -> None:
        nonlocal count
        count += 1

    _scan_due_rows_in_session(session, _naive_utc(cutoff), None, _count)
    return {
        "would_purge_count": count,
        "first_purge_at": first_purge_at.isoformat() if count else None,
    }


# ── Purge planning + file removal ─────────────────────────────────────────────


def build_purge_plan(
    rows: list[ScrapheapRow],
    no_delete_folder_ids: set[int],
    locked_ids: set[int],
    include_protected: bool,
    retention_guard: Optional[RetentionGuard] = None,
) -> ScrapheapPurgePlan:
    """Decide what a purge destroys.

    Three independent reasons to keep a row, checked in this order:

    1. **Locked** - frozen by a locked picture-set (directly or via a live stack
       sibling). This binds on EVERY path, including an explicit
       ``include_protected=true`` delete-forever, and is checked FIRST because it
       is the one blocker no request flag can override. A locked set is a hard
       whole-set freeze: ``DELETE /pictures/{id}`` refuses it with 423 and the
       bulk soft-delete skips it, so the single IRREVERSIBLE path must not be the
       one that ignores it. Skip-and-report, never raise, so one frozen member
       cannot fail a whole batch.
    2. **Retention deadline** - the automatic path's SECOND deadline check,
       recomputed from the row's current ``deleted_at``. The manual
       delete-forever passes ``retention_guard=None``: a human's explicit
       confirmation is not gated on a timer.
    3. **Protected** - a reference-folder original whose folder forbids file
       deletion (``allow_delete_file=False``). ``include_protected`` decides its
       fate: ``False`` -> skip it entirely (row kept, file kept, no ledger row);
       ``True`` -> destroy it like any other. The protection is a ROUTINE
       safeguard that still governs soft-delete and the background scan; only an
       explicit ``include_protected=true`` delete-forever overrides it. The
       retention auto-purge always passes ``False``.

    The deadline is checked before protection, so on the automatic path a row
    that is BOTH protected and still in-window is counted as ``retained_count``
    (deadline) rather than ``skipped_count`` (protected). Both keep the row, and
    the auto path always passes ``include_protected=False``, so this only
    affects which counter reports it.
    """
    plan = ScrapheapPurgePlan()
    for row in rows:
        if row.id is not None and int(row.id) in locked_ids:
            plan.skipped_locked.append(int(row.id))
            logger.info(
                "Delete-forever: SKIPPING picture id=%s - frozen by a locked "
                "picture-set; row and file kept (unlock the set to delete it)",
                row.id,
            )
            continue
        if retention_guard is not None:
            permitted, reason = retention_guard.permits(row)
            if not permitted:
                plan.retained_count += 1
                logger.info(
                    "Scrapheap auto-purge: RETAINING picture id=%s path=%s - %s",
                    row.id,
                    row.file_path,
                    reason,
                )
                continue
        was_reference_protected = row.is_protected(no_delete_folder_ids)
        if was_reference_protected and not include_protected:
            plan.skipped_count += 1
            logger.info(
                "Delete-forever: SKIPPING protected reference original "
                "picture id=%s (include_protected=false); row and file kept",
                row.id,
            )
            continue
        if row.id is not None:
            plan.picture_ids.append(int(row.id))
        if row.file_path:
            # This picture is being purged, so its file is genuinely removed and
            # file_removed is True: restore MUST drop the row and never
            # resurrect it. (A file_removed=False row means "removed from
            # library, file kept" and is only ever produced by routine paths,
            # never here - a skipped protected picture writes NO ledger row.)
            plan.log_records.append(
                {
                    # Carried so purge_rows_in_session can drop the record when
                    # its picture turns out to have left the scrapheap.
                    "picture_id": int(row.id) if row.id is not None else None,
                    "path_sha": DeletedFileLog.hash_path(row.file_path),
                    "pixel_sha": row.pixel_sha,
                    "file_removed": True,
                }
            )
            plan.removal_targets.append(
                (row.id, row.file_path, was_reference_protected)
            )
    return plan


def plan_and_purge_in_session(
    session: Session,
    ids: Optional[list[int]],
    include_protected: bool,
    retention_guard: Optional["RetentionGuard"],
) -> tuple[ScrapheapPurgePlan, int, set[str], set[int]]:
    """Select, plan and destroy in ONE DB-queue submission.

    The purge used to run as four separate submissions - fetch the scrapheap
    rows, fetch the protected folder ids, look the locks up, then delete. Writes
    are serialised on a single DB worker thread, so any write submitted between
    those steps ran BETWEEN them: a ``POST /pictures/scrapheap/restore`` landing
    in that window made the selected ids live again and the final delete-by-id
    destroyed them (rows, files and a ``file_removed=True`` ledger row). The
    locked-set lookup was worse still - it ran on the CALLER's thread via
    ``run_immediate_read_task``, so a set locked afterwards was not seen at all.

    Running the whole decision inside one task closes the window: nothing else
    can write while this runs. It is the structural half of the fix.
    :func:`purge_rows_in_session` re-checks ``deleted`` at the point of deletion
    anyway - the half that does not depend on this scheduling - and derives the
    row, the file and the ledger from what the guarded DELETE actually removed,
    rolling the batch back if the two ever disagree. Read that docstring for the
    precise limits of the guarantee before relying on it.

    Returns:
        ``(plan, deleted_count, owned_path_shas, skipped_restored_ids)``.
    """
    rows = fetch_scrapheap_rows_in_session(session, ids)
    if not rows:
        return ScrapheapPurgePlan(), 0, set(), set()
    no_delete_folder_ids = fetch_no_delete_folder_ids_in_session(session)
    # Unconditional: a locked picture-set freezes its members against EVERY
    # destruction path, manual delete-forever included. Looked up through the one
    # shared helper so this can never disagree with the sweep or the listing.
    locked_ids = locked_scrapheap_picture_ids_in_session(
        session, [row.id for row in rows if row.id is not None]
    )
    plan = build_purge_plan(
        rows, no_delete_folder_ids, locked_ids, include_protected, retention_guard
    )
    deleted_count, owned_path_shas, skipped_restored = purge_rows_in_session(
        session, plan.picture_ids, plan.log_records
    )
    return plan, deleted_count, owned_path_shas, skipped_restored


def classify_delete_preview(
    rows: list[ScrapheapRow],
    no_delete_folder_ids: set[int],
    locked_ids: set[int],
) -> dict:
    """Partition a delete-forever selection into three DISJOINT buckets.

    The confirm dialog has to state exactly what each button will destroy, and
    **no count may overstate destruction**. So the buckets are keyed on which
    action destroys the row, not on which properties it happens to have:

    * ``locked_count``   - frozen by a locked picture-set, whether or not it is
      ALSO protected. Destroyed by neither button.
    * ``protected_count`` - protected and NOT locked. Destroyed only by
      "Delete all" (``include_protected=true``).
    * ``unprotected_count`` - neither. Destroyed by both buttons.

    They are disjoint and sum to ``total_count``, so "Delete unprotected only
    (``unprotected_count``)" and "Delete all - incl. ``protected_count``
    protected" are each literally true.

    **Locked is classified FIRST here, which is deliberately the opposite of
    ``auto_purge_exempt_reason`` (where protected wins).** The two answer
    different questions. The badge answers "why is this being kept?" and leads
    with the permanent, intrinsic reason. The preview answers "what will this
    button destroy?" and must lead with the BINDING blocker - for a
    locked+protected row under ``include_protected=true``, protection is
    overridden but the lock still holds, so counting it as protected would tell
    the user "Delete all" destroys it when it does not.

    ``protected`` lists the locked-free protected originals with their resolved
    on-disk paths (the files genuinely at risk from "Delete all"); ``locked``
    lists the frozen ids so the dialog can name them.
    """
    protected_items: list[dict] = []
    locked_items: list[int] = []
    unprotected_count = 0
    for row in rows:
        if row.id is not None and int(row.id) in locked_ids:
            locked_items.append(int(row.id))
        elif row.is_protected(no_delete_folder_ids):
            protected_items.append({"id": row.id, "file_path": row.file_path or ""})
        else:
            unprotected_count += 1
    return {
        "total_count": len(rows),
        "protected_count": len(protected_items),
        "locked_count": len(locked_items),
        "unprotected_count": unprotected_count,
        "protected": protected_items,
        "locked": sorted(locked_items),
    }


# ── Server-side delete-forever confirmation ──────────────────────────────────
#
# The type-to-confirm dialog is a CLIENT control and proves nothing to the
# server: ``DELETE /pictures/scrapheap`` with an empty body used to destroy the
# entire scrapheap and its files with no server-side intent check at all. There
# is no CSRF token anywhere, and CORS admits ANY localhost/LAN-IP *port* with
# credentials, so a page served on another local port can drive the owner's own
# session straight into the one irreversible endpoint in the product.
#
# Alternatives considered:
#
# * **Echo the preview's ``total_count``** (the CSO's suggestion). Rejected as
#   the primary control: it is a small integer, stable, and enumerable - a
#   caller that cannot read the preview response can still just try 1, 2, 3…
#   It also makes ordinary concurrent activity (another tab scrapheaping a
#   picture) a spurious failure.
# * **Require a custom header.** Rejected: a DELETE with a JSON body already
#   triggers a preflight, and ``allow_headers=["*"]`` means the preflight passes
#   for every origin the regex admits. It costs a round trip and buys nothing.
# * **A single-use, TTL-bounded random token minted by the preview endpoint and
#   bound to the exact selection** - chosen. It cannot be guessed, so a caller
#   that cannot read a preview response cannot construct one; it cannot be
#   replayed, so one leaked value destroys at most one selection; and it is
#   bound to the selection, so a token minted for one picture cannot be spent
#   emptying the whole heap.
#
# This is an INTENT control, not an authorization control. Authorization for
# these routes is owned by the AuthzGate (``OWNER_ONLY`` in
# ``pixlstash/authz/registry.py``) and is unchanged.

# How long a preview's confirmation stays spendable. Long enough to read a
# dialog listing every protected original, short enough that a token left in a
# closed tab is not a standing capability.
CONFIRM_TOKEN_TTL_SECONDS: int = 300

# Cap on outstanding confirmations, so repeated previews cannot grow the map
# without bound. The oldest is evicted first; it is only ever a fresh preview
# away from being reissued.
CONFIRM_TOKEN_MAX_OUTSTANDING: int = 64

# Why a confirmation was refused. ``MISSING`` is a malformed request (400);
# the rest mean the preview is no longer spendable (409 - re-run the preview).
CONFIRM_MISSING = "missing"
CONFIRM_UNKNOWN = "unknown"
CONFIRM_MISMATCH = "mismatch"


def selection_fingerprint(ids: Optional[list[int]]) -> str:
    """Stable identity of a delete-forever selection.

    ``None`` (the whole scrapheap) is its OWN fingerprint, deliberately distinct
    from any explicit id list: a confirmation minted for "these three pictures"
    must never be spendable on "everything".
    """
    if ids is None:
        return "ALL"
    return ",".join(str(int(pid)) for pid in sorted({int(pid) for pid in ids}))


@dataclass(frozen=True)
class DeleteConfirmation:
    """One outstanding, unspent confirmation.

    Attributes:
        fingerprint: The selection the preview was computed over.
        total_count: What the preview reported, for the audit log.
        expires_at: ``time.monotonic()`` deadline.
    """

    fingerprint: str
    total_count: int
    expires_at: float
    library_uuid: Optional[str] = None
    generation: Optional[int] = None


class ScrapheapDeleteConfirmations:
    """Mint and redeem single-use delete-forever confirmations.

    One instance per server. Thread-safe: previews and deletes are served from
    the FastAPI thread pool, so mint and redeem can genuinely race, and
    "redeemed exactly once" is the property that makes a token single-use.

    Deliberately in-memory: a confirmation is a few-minutes-long proof that a
    human just read a destructive preview, so losing them on restart is correct
    behaviour, not a limitation.

    A confirmation is bound to the SELECTION, not to ``include_protected``: one
    preview drives both dialog buttons, and it already reports exactly what each
    one destroys.
    """

    def __init__(
        self,
        ttl_seconds: int = CONFIRM_TOKEN_TTL_SECONDS,
        max_outstanding: int = CONFIRM_TOKEN_MAX_OUTSTANDING,
    ) -> None:
        """Initialise an empty confirmation store."""
        self._ttl_seconds = ttl_seconds
        self._max_outstanding = max_outstanding
        self._lock = threading.Lock()
        self._outstanding: dict[str, DeleteConfirmation] = {}

    def issue(
        self,
        ids: Optional[list[int]],
        total_count: int,
        *,
        library_uuid: Optional[str] = None,
        generation: Optional[int] = None,
    ) -> str:
        """Mint a confirmation for ``ids`` and return the opaque token."""
        token = secrets.token_urlsafe(32)
        record = DeleteConfirmation(
            fingerprint=selection_fingerprint(ids),
            total_count=int(total_count),
            expires_at=time.monotonic() + self._ttl_seconds,
            library_uuid=library_uuid,
            generation=generation,
        )
        with self._lock:
            self._prune_locked()
            while len(self._outstanding) >= self._max_outstanding:
                oldest = min(
                    self._outstanding,
                    key=lambda key: self._outstanding[key].expires_at,
                )
                del self._outstanding[oldest]
                logger.info(
                    "Delete-forever: evicted the oldest unspent confirmation "
                    "(more than %d outstanding); that preview must be re-run "
                    "before it can be confirmed",
                    self._max_outstanding,
                )
            self._outstanding[token] = record
        return token

    def redeem(
        self,
        token: Optional[str],
        ids: Optional[list[int]],
        *,
        library_uuid: Optional[str] = None,
        generation: Optional[int] = None,
    ) -> tuple[bool, str]:
        """Spend ``token`` for the selection ``ids``.

        Returns:
            ``(True, "")`` when the confirmation was valid and is now spent, or
            ``(False, reason)`` - one of :data:`CONFIRM_MISSING`,
            :data:`CONFIRM_UNKNOWN` (absent, already spent, or expired) or
            :data:`CONFIRM_MISMATCH` (minted for a different selection). The
            token is consumed on a fingerprint mismatch too: a confirmation the
            user did not mean to spend this way is not spendable at all.
        """
        if not token or not isinstance(token, str):
            logger.warning(
                "Delete-forever: REFUSED - no confirm_token. This endpoint "
                "permanently destroys pictures and their files, so it requires "
                "a confirmation minted by POST /pictures/scrapheap/delete-preview."
            )
            return False, CONFIRM_MISSING
        wanted = selection_fingerprint(ids)
        with self._lock:
            self._prune_locked()
            record = self._outstanding.pop(token, None)
        if record is None:
            logger.warning(
                "Delete-forever: REFUSED - the confirmation is unknown, already "
                "spent, or older than %ds. Nothing was destroyed; re-run the "
                "delete preview.",
                self._ttl_seconds,
            )
            return False, CONFIRM_UNKNOWN
        if (
            record.fingerprint != wanted
            or record.library_uuid != library_uuid
            or record.generation != generation
        ):
            logger.warning(
                "Delete-forever: REFUSED - the confirmation was minted for a "
                "different selection (preview covered %s picture(s); this "
                "request targets a different set). Nothing was destroyed; the "
                "confirmation has been discarded.",
                record.total_count,
            )
            return False, CONFIRM_MISMATCH
        logger.info(
            "Delete-forever: confirmation accepted for a selection previewed as "
            "%d picture(s)",
            record.total_count,
        )
        return True, ""

    def _prune_locked(self) -> None:
        """Drop expired confirmations. Caller must hold ``self._lock``."""
        now = time.monotonic()
        expired = [
            token
            for token, record in self._outstanding.items()
            if record.expires_at <= now
        ]
        for token in expired:
            del self._outstanding[token]
        if expired:
            logger.debug(
                "Delete-forever: dropped %d expired confirmation(s)", len(expired)
            )


def file_location_is_unreachable(file_path: str) -> bool:
    """Whether a path's absence is unexplained rather than genuine.

    ``os.path.isfile`` is False both when a file was really deleted and when the
    volume holding it is not currently mounted (an unplugged reference folder, a
    dropped network share). Treating the second case as "genuinely gone" is what
    lets the ledger assert ``file_removed=True`` for a file that still exists.
    The parent directory tells them apart: if the directory is missing too, the
    location is unreachable and we must not claim anything about the file.
    """
    parent = os.path.dirname(file_path) or os.curdir
    return not os.path.isdir(parent)


def remove_picture_files(
    image_root: str,
    targets: list[tuple[Optional[int], str, bool]],
    reference_roots: tuple[str, ...] = (),
) -> list[str]:
    """Delete the on-disk originals (and thumbnails) for a purged selection.

    ``Picture.file_path`` is a database value, and this is the one unattended
    ``os.remove`` that follows it wherever it points. A path outside the
    library's legitimate roots is therefore skipped rather than deleted
    (#776): a wrong absolute path written by an import or a bug cannot reach
    a delete. Skipping is the safe direction, because an undeleted file is
    reported as unconfirmed and the ledger is corrected so restore can still
    resurrect the row.

    Args:
        image_root: The vault's image root. Relative paths resolve under it.
        targets: ``(picture id, stored path, was_reference_protected)``.
        reference_roots: Configured reference-folder roots, which pictures may
            legitimately live under even though they are outside *image_root*.
            Pass :meth:`Vault.reference_folder_roots`. Defaulting to empty
            means only *image_root* is honoured.

    Returns:
        ``path_sha`` of every target whose file is NOT confirmed gone - the
        removal raised, the path was refused, or the location is unreachable
        so we cannot tell. The caller must correct those ledger rows to
        ``file_removed=False``; see :func:`mark_files_kept_in_session`.
    """
    unconfirmed: list[str] = []
    allowed_roots = (image_root, *reference_roots)

    def _unconfirmed(rel_path: str) -> None:
        unconfirmed.append(DeletedFileLog.hash_path(rel_path))

    for pic_id, rel_path, was_reference_protected in targets:
        file_path = ImageUtils.resolve_picture_path(image_root, rel_path)
        if file_path and not any(
            path_is_within(file_path, root) for root in allowed_roots if root
        ):
            logger.error(
                "Delete-forever: refusing to remove the file for picture "
                "id=%s because its stored path %s resolves to %s, which is "
                "outside the library roots %s; neither it nor its thumbnail "
                "is touched and the ledger will be corrected to "
                "file_removed=False",
                pic_id,
                rel_path,
                file_path,
                allowed_roots,
            )
            _unconfirmed(rel_path)
            continue
        if file_path and os.path.isfile(file_path):
            logger.info(
                "Delete-forever: destroying file for picture id=%s "
                "path=%s reference_protected=%s op=os.remove",
                pic_id,
                file_path,
                was_reference_protected,
            )
            try:
                os.remove(file_path)
                logger.info(
                    "Delete-forever: removed file for picture id=%s "
                    "path=%s reference_protected=%s",
                    pic_id,
                    file_path,
                    was_reference_protected,
                )
            except Exception as e:
                logger.error(
                    "Delete-forever: failed to remove file for picture "
                    "id=%s path=%s reference_protected=%s: %s - the "
                    "permanent-deletion ledger will be corrected to "
                    "file_removed=False so restore can still resurrect it",
                    pic_id,
                    file_path,
                    was_reference_protected,
                    e,
                    exc_info=True,
                )
                _unconfirmed(rel_path)
        elif file_path and file_location_is_unreachable(file_path):
            # Absent because the location is gone, not because the file is.
            logger.error(
                "Delete-forever: cannot reach the location of picture id=%s "
                "path=%s (parent directory missing - unmounted reference "
                "folder or network vault?) reference_protected=%s; the file may "
                "still exist, so the ledger will be corrected to "
                "file_removed=False rather than claiming it is gone",
                pic_id,
                file_path,
                was_reference_protected,
            )
            _unconfirmed(rel_path)
        else:
            logger.warning(
                "Delete-forever: no on-disk file to remove for picture "
                "id=%s rel_path=%s (resolved=%s) reference_protected=%s",
                pic_id,
                rel_path,
                file_path,
                was_reference_protected,
            )
        ImageUtils.remove_thumbnail(image_root, rel_path)
    return unconfirmed


def mark_files_kept_in_session(
    session: Session, path_shas: list[str], owned_path_shas: set[str]
) -> int:
    """Correct ledger rows to ``file_removed=False`` after a failed removal.

    The ledger row is written BEFORE the file is touched, and deliberately so:
    writing it afterwards would leave a window in which the picture row is gone
    with no ledger entry, which is exactly how the reference-folder scan
    resurrects deleted content. The cost is that ``file_removed=True`` is a
    PREDICTION until the removal succeeds. This is the correction when it does
    not: ``False`` is the accurate state - "removed from the library, file kept
    on disk" - and it lets restore resurrect the picture instead of dropping it
    forever on the strength of a deletion that never happened.

    This is the ONLY True -> False transition in the ledger; everywhere else the
    flag may only be raised. Two things make it safe:

    1. It is conditioned on having OBSERVED that the file was not destroyed.
    2. ``owned_path_shas`` - the rows this same purge created or raised (see
       :func:`purge_rows_in_session`) - bounds it. The ledger is keyed by PATH,
       not by picture identity, so without this a purge could reach back and
       rewrite a row describing somebody else's already-destroyed content:

           purge A destroys content C1 at path P   -> ledger(P) = (True, C1)
           different content C2 is later written at P and indexed as picture B
           purge B is denied by os.remove          -> unconfirmed
           ...and would downgrade A's row to False, so restoring a snapshot
           containing A resurrects it bound to C2's file.

       That row was already ``True`` on entry, so this call does not own it and
       leaves it alone. Today no API route can build that collision (reference
       folders reject overlapping roots with 409, the routine scan skips
       ledgered paths, explicit re-import clears the row) - the intersection
       makes it impossible by construction rather than by the accident of those
       surrounding guards.
    """
    if not path_shas:
        return 0
    corrected = 0
    for path_sha in path_shas:
        if path_sha not in owned_path_shas:
            # Not written by this purge: it records an earlier permanent
            # deletion of some other content that happened to share this path.
            logger.warning(
                "Delete-forever: NOT downgrading permanent-deletion ledger row "
                "%s - it predates this purge, so it describes content this call "
                "did not destroy and must keep asserting file_removed=True",
                path_sha,
            )
            continue
        row = session.exec(
            select(DeletedFileLog).where(DeletedFileLog.path_sha == path_sha)
        ).first()
        if row is None or not row.file_removed:
            continue
        row.file_removed = False
        session.add(row)
        corrected += 1
    session.commit()
    if corrected:
        logger.error(
            "Delete-forever: CORRECTED %d permanent-deletion ledger row(s) to "
            "file_removed=False - their files were not confirmed destroyed, so "
            "the ledger must not claim they are gone (restore may resurrect "
            "them).",
            corrected,
        )
    return corrected


def remove_picture_files_and_reconcile_ledger(
    vault, targets, owned_path_shas: set[str]
) -> None:
    """Remove the files, then correct the ledger for anything not confirmed gone.

    This pairing is the unit that must always run together - never schedule
    :func:`remove_picture_files` on its own, or a failed removal will leave the
    ledger permanently asserting a deletion that did not happen.

    ``owned_path_shas`` comes from the :func:`purge_rows_in_session` call that
    wrote those rows, and bounds the correction to them.
    """
    unconfirmed = remove_picture_files(
        vault.image_root, targets, vault.reference_folder_roots()
    )
    if unconfirmed:
        vault.db.run_task(
            mark_files_kept_in_session,
            unconfirmed,
            owned_path_shas,
            priority=DBPriority.IMMEDIATE,
        )


# ── Vault wrappers (the thin bridge to the DB work-queue) ─────────────────────


def fetch_scrapheap_rows(vault, ids: Optional[list[int]]) -> list[ScrapheapRow]:
    """Vault wrapper for :func:`fetch_scrapheap_rows_in_session`."""
    return vault.db.run_task(
        fetch_scrapheap_rows_in_session, ids, priority=DBPriority.IMMEDIATE
    )


def fetch_no_delete_folder_ids(vault) -> set[int]:
    """Vault wrapper for :func:`fetch_no_delete_folder_ids_in_session`."""
    return vault.db.run_task(
        fetch_no_delete_folder_ids_in_session, priority=DBPriority.IMMEDIATE
    )


def find_due_retention_picture_ids(
    vault,
    now: datetime,
    retention_days: Optional[int],
    reduced_at: Optional[datetime],
    limit: int,
) -> list[int]:
    """Vault wrapper for :func:`find_due_retention_picture_ids_in_session`."""
    return vault.db.run_immediate_read_task(
        find_due_retention_picture_ids_in_session,
        now,
        retention_days,
        reduced_at,
        limit,
    )


def retention_impact(
    vault, now: datetime, candidate_days: Optional[int], current_days: Optional[int]
) -> dict:
    """Vault wrapper for :func:`retention_impact_in_session`. Read-only."""
    return vault.db.run_immediate_read_task(
        retention_impact_in_session, now, candidate_days, current_days
    )


def purge_scrapheap_pictures(
    vault,
    ids: Optional[list[int]],
    include_protected: bool,
    schedule_file_removal: Optional[Callable[..., None]] = None,
    retention_guard: Optional[RetentionGuard] = None,
) -> ScrapheapPurgeOutcome:
    """Permanently destroy a scrapheap selection. THE destruction path.

    Args:
        vault: The owning Vault (DB work-queue + ``image_root``).
        ids: Picture ids to purge, or ``None`` for the entire scrapheap.
        include_protected: When ``False`` (always, for the retention timer),
            protected reference originals in the selection are skipped entirely
            - row kept, file untouched, no ledger row. When ``True`` they are
            destroyed too; only an explicit human confirmation sets this.
        schedule_file_removal: Optional deferral hook, called as
            ``schedule_file_removal(remove_picture_files_and_reconcile_ledger,
            vault, targets, owned_path_shas)`` - the HTTP handler passes
            ``BackgroundTasks.add_task`` so files are removed after the response
            is sent. ``None`` removes them inline (the background-task path,
            which is already off the event loop). Either way it is the
            removal+reconcile pair, never the bare removal.
        retention_guard: The automatic path's independent re-check of the
            retention DEADLINE, evaluated against each row's CURRENT
            ``deleted_at``. Supplied by the auto-purge task; ``None`` on the
            manual, consent-gated path. (The locked-set freeze is NOT part of
            this - it binds on every path and is enforced unconditionally below.)

    Returns:
        A :class:`ScrapheapPurgeOutcome`.
    """
    # Selection, planning, the ledger write and the DELETE all run inside ONE
    # DB-queue submission (see plan_and_purge_in_session): writes are serialised
    # on a single worker thread, so splitting them let a concurrent restore land
    # in the gap and turned the delete-by-id into a hard delete of live rows.
    plan, deleted_count, owned_path_shas, skipped_restored = vault.db.run_task(
        plan_and_purge_in_session,
        ids,
        include_protected,
        retention_guard,
        priority=DBPriority.IMMEDIATE,
    )
    # Rows + ledger first, files second: a crash between the two leaves orphaned
    # files that MissingFilePurgeFinder/the reference scan already handle, while
    # the reverse order would leave rows pointing at destroyed files.
    #
    # A picture that left the scrapheap mid-purge was NOT deleted and got no
    # ledger row, so its file must not be removed either - drop its target.
    #
    # Fail CLOSED on an unidentifiable target. A target with no picture id
    # cannot be matched against ``skipped_restored``, so it cannot be shown to
    # have been destroyed - and on the rollback path nothing was. Admitting it
    # would unlink a file for a row that still exists, which is the F1 shape
    # again. Unreachable today (a persisted Picture always has a PK), but this
    # is the one irreversible path and "unknown" must not mean "delete it".
    removal_targets = []
    for target in plan.removal_targets:
        if target[0] is None:
            logger.error(
                "Delete-forever: NOT removing the file for a purge target with "
                "no picture id (rel_path=%s) - it cannot be matched against the "
                "ids that were actually destroyed, so the removal is refused "
                "rather than guessed. The row (if any) and the file are kept.",
                target[1],
            )
            continue
        if int(target[0]) in skipped_restored:
            continue
        removal_targets.append(target)
    purged_ids = [pid for pid in plan.picture_ids if pid not in skipped_restored]
    # Always the removal+reconcile pair, never the bare removal: the ledger rows
    # were committed above and assert file_removed=True, which stays a PREDICTION
    # until the files are actually gone. ``owned_path_shas`` scopes any later
    # correction to the rows THIS call wrote.
    if schedule_file_removal is not None:
        schedule_file_removal(
            remove_picture_files_and_reconcile_ledger,
            vault,
            removal_targets,
            owned_path_shas,
        )
    else:
        remove_picture_files_and_reconcile_ledger(
            vault, removal_targets, owned_path_shas
        )
    logger.info(
        "Delete-forever: purged %d, skipped %d protected, skipped %d locked, "
        "skipped %d that left the scrapheap, retained %d "
        "(include_protected=%s, guarded=%s)",
        deleted_count,
        plan.skipped_count,
        len(plan.skipped_locked),
        len(skipped_restored),
        plan.retained_count,
        include_protected,
        retention_guard is not None,
    )
    return ScrapheapPurgeOutcome(
        deleted_count=deleted_count,
        skipped_count=plan.skipped_count,
        skipped_locked=sorted(plan.skipped_locked),
        retained_count=plan.retained_count,
        purged_ids=purged_ids,
        skipped_restored=sorted(skipped_restored),
    )
