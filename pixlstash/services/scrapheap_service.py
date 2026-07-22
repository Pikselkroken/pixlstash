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
  ``None`` ("Never" — auto-purge is disabled entirely).
* Lowering the window gives EVERY picture a :data:`REDUCTION_GRACE_DAYS`-day
  reprieve measured from the reduction itself, not from the picture's own
  ``deleted_at``. The deadline is
  ``max(deleted_at + retention_days, reduced_at + REDUCTION_GRACE_DAYS)``.
  The floor is what makes the grace real: measuring the grace from
  ``deleted_at`` would only help pictures sitting in the narrow
  ``[retention_days, retention_days + 1)`` band, so a ``Never -> 30`` or
  ``120 -> 30`` change would still destroy a long-lived scrapheap on the very
  next 15-minute sweep — seconds after a dropdown that saves on change with no
  confirmation. With the floor, **no picture can be purged within a day of a
  lowering, regardless of age**, which is the promise the settings copy makes.
* ``Never -> <finite>`` counts as a reduction (Never is an infinite window), so
  it grants the grace too. This is deliberately the safer reading: it is the
  single most destructive transition available in the UI.
* A soft-deleted picture with no ``deleted_at`` is **never** auto-purged
  (fail-closed: no timestamp, no deadline).
* The deadline is enforced **twice**, mirroring the two-layer protected-original
  defence: once when the finder selects candidates
  (:func:`find_due_retention_picture_ids_in_session`) and again inside
  :func:`build_purge_plan` via a :class:`RetentionGuard`, so a finder bug — or a
  restore/re-delete that resets ``deleted_at`` between planning and the
  LOW-priority task actually running — cannot destroy an in-window picture.
* Pictures frozen by a locked picture-set — directly, or via a live stack
  sibling — are excluded from **every** destruction path, the manual
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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from sqlalchemy import delete
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import DeletedFileLog, Picture, ReferenceFolder
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)

# The retention windows the UI offers. ``None`` ("Never") is also valid and
# disables auto-purge entirely.
RETENTION_DAY_CHOICES: tuple[int, ...] = (30, 60, 90, 120)

# Default window when server-config carries no explicit value.
DEFAULT_RETENTION_DAYS: int = 30

# Extra days granted to pictures that were already in the scrapheap when the
# retention window was *lowered*, so a reduction never purges anything the same
# day it is applied.
REDUCTION_GRACE_DAYS: int = 1

# Max ids per locked-set lookup. SQLITE_LIMIT_VARIABLE_NUMBER is 999 on SQLite
# builds older than 3.32, so an unchunked ``IN (...)`` over a large scrapheap
# would raise — and, because the finder catches and returns no work, would
# SILENTLY disable auto-purge on those builds. Chunking keeps a big scrapheap
# from turning the feature off by accident.
LOCK_QUERY_CHUNK: int = 900

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
    # EVERY path — a lock outranks even an explicit include_protected=true.
    skipped_locked: list[int] = field(default_factory=list)
    # Pictures the RetentionGuard held back — not yet past their deadline.
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


@dataclass(frozen=True)
class RetentionGuard:
    """Independent re-check of the automatic path's preconditions.

    The finder already filters candidates, but a single check is a single point
    of failure for an automatic file-destruction path — the same reasoning that
    put the protected-original check in BOTH the finder query and
    :func:`build_purge_plan`. This guard re-derives the deadline from the row's
    CURRENT ``deleted_at`` at purge time, which also closes a real
    time-of-check/time-of-use window: the purge task runs at ``TaskPriority.LOW``
    and can be queued behind other work, so a picture restored and re-deleted in
    between would otherwise be destroyed on a ``deleted_at`` only seconds old.

    Present only on the automatic path. The manual, consent-gated delete-forever
    passes ``None`` — a human asking for immediate deletion is not subject to a
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


def retention_rank(days: Optional[int]) -> float:
    """Order retention windows so ``None`` ("Never") sorts as the largest."""
    return math.inf if days is None else float(days)


def is_retention_reduction(
    current_days: Optional[int], new_days: Optional[int]
) -> bool:
    """Whether moving ``current_days`` -> ``new_days`` shortens the window.

    ``None`` ("Never") is treated as an infinite window, so ``Never -> 90`` is a
    reduction while ``30 -> 60`` and ``90 -> Never`` are not. Because the default
    window (:data:`DEFAULT_RETENTION_DAYS`) is also the shortest choice, a
    *first* explicit set can never be a reduction — which is exactly the
    "untouched on raise or first-set" rule.
    """
    return retention_rank(new_days) < retention_rank(current_days)


def reduction_grace_floor(reduced_at: Optional[datetime]) -> Optional[datetime]:
    """Earliest instant ANY picture may be auto-purged after a window lowering.

    Returns ``reduced_at + REDUCTION_GRACE_DAYS``, or ``None`` when the window
    has never been lowered. This is a floor on every deadline, not a per-picture
    extension — see the module docstring for why the distinction is the whole
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
    old it is — a 400-day-old picture and a 31-day-old one both get the full
    reprieve from a ``120 -> 30`` or ``Never -> 30`` change.

    For a picture soft-deleted *after* the reduction the floor is inert
    (``deleted_at >= reduced_at`` and ``retention_days > grace``), so applying it
    unconditionally costs nothing and removes a branch that could be got wrong.

    Returns ``None`` when the sweep will never auto-purge the picture: it is a
    protected reference original, it is frozen by a locked picture-set, retention
    is "Never", or it carries no ``deleted_at`` stamp. Keeping the locked case
    HERE — rather than only in the finder — is what stops the listing from
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

    An absent key means the default window; an explicit ``null`` means "Never".
    An unrecognised value falls back to the default and is logged rather than
    silently accepted — a typo must not silently change how long files survive.
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
            "server-config %s=%r is not an integer; falling back to the default "
            "%s-day scrapheap retention window",
            RETENTION_DAYS_KEY,
            raw,
            DEFAULT_RETENTION_DAYS,
        )
        return DEFAULT_RETENTION_DAYS
    if days not in RETENTION_DAY_CHOICES:
        logger.warning(
            "server-config %s=%r is not one of %s; falling back to the default "
            "%s-day scrapheap retention window",
            RETENTION_DAYS_KEY,
            raw,
            RETENTION_DAY_CHOICES,
            DEFAULT_RETENTION_DAYS,
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
    — the timer is the only thing that ever destroys a file.

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
        query = query.where(Picture.id.in_(ids))
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


def purge_rows_in_session(
    session: Session, picture_ids: list[int], log_records: list[dict]
) -> int:
    """Write the permanent-deletion ledger and delete the picture rows.

    Logged and deleted in the same transaction so the two can never diverge.
    """
    if not picture_ids:
        return 0
    now = datetime.now(timezone.utc)
    for record in log_records:
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
        elif new_file_removed and not already_logged.file_removed:
            # A path first logged file_removed=False (protected file kept on
            # disk) is now being genuinely hard-deleted. Upgrade the stale flag
            # to True so the ledger stays truthful rather than leaving a False
            # row that only restore's missing-file net would catch. Only ever
            # raise False -> True; never downgrade a genuine permanent deletion
            # back to "kept".
            already_logged.file_removed = True
            session.add(already_logged)
    session.exec(delete(Picture).where(Picture.id.in_(picture_ids)))
    session.commit()
    return len(picture_ids)


def locked_scrapheap_picture_ids_in_session(session: Session, picture_ids) -> set[int]:
    """Chunked :func:`locked_picture_ids` — THE lock lookup for the scrapheap.

    Both the auto-purge finder and the scrapheap listing go through here so the
    countdown the UI renders and the decision the sweep makes can never disagree
    about which pictures are frozen (including the live-stack-sibling case that
    ``locked_picture_ids`` resolves).
    """
    ids = [int(pid) for pid in picture_ids if pid is not None]
    locked: set[int] = set()
    for start in range(0, len(ids), LOCK_QUERY_CHUNK):
        locked |= locked_picture_ids(session, ids[start : start + LOCK_QUERY_CHUNK])
    return locked


def locked_scrapheap_picture_ids(vault, picture_ids) -> set[int]:
    """Vault wrapper for :func:`locked_scrapheap_picture_ids_in_session`."""
    return vault.db.run_immediate_read_task(
        locked_scrapheap_picture_ids_in_session, picture_ids
    )


def find_due_retention_picture_ids_in_session(
    session: Session,
    now: datetime,
    retention_days: Optional[int],
    reduced_at: Optional[datetime],
    limit: int,
) -> list[int]:
    """Ids of UNPROTECTED, UNLOCKED soft-deleted pictures that are past deadline.

    Protected reference originals and locked-set members are filtered out here
    AND re-checked by the :class:`RetentionGuard` in the purge plan; the timer
    must never even select them.

    Locked pictures are skipped and logged rather than raising: this runs in a
    background sweep, so one frozen member must not abort the batch (same
    convention as ``reference_folder_scan_task.py``'s locked-picture handling).
    """
    if retention_days is None or limit <= 0:
        return []
    no_delete_folder_ids = fetch_no_delete_folder_ids_in_session(session)
    rows = fetch_scrapheap_rows_in_session(session, None)
    locked_ids = locked_scrapheap_picture_ids_in_session(
        session, [r.id for r in rows if r.id is not None]
    )
    now_utc = _as_utc(now)
    due: list[int] = []
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
                "Scrapheap auto-purge: SKIPPING picture id=%s — frozen by a "
                "locked picture-set; it will not be auto-purged until unlocked",
                row.id,
            )
            continue
        purge_at = compute_purge_at(
            row.deleted_at, retention_days, reduced_at, is_protected=False
        )
        if purge_at is None or purge_at > now_utc:
            continue
        logger.info(
            "Scrapheap auto-purge: picture id=%s path=%s is due "
            "(deleted_at=%s deadline=%s now=%s retention_days=%s reduced_at=%s)",
            row.id,
            row.file_path,
            _as_utc(row.deleted_at),
            purge_at,
            now_utc,
            retention_days,
            _as_utc(reduced_at),
        )
        due.append(int(row.id))
        if len(due) >= limit:
            break
    return due


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

    1. **Locked** — frozen by a locked picture-set (directly or via a live stack
       sibling). This binds on EVERY path, including an explicit
       ``include_protected=true`` delete-forever, and is checked FIRST because it
       is the one blocker no request flag can override. A locked set is a hard
       whole-set freeze: ``DELETE /pictures/{id}`` refuses it with 423 and the
       bulk soft-delete skips it, so the single IRREVERSIBLE path must not be the
       one that ignores it. Skip-and-report, never raise, so one frozen member
       cannot fail a whole batch.
    2. **Retention deadline** — the automatic path's SECOND deadline check,
       recomputed from the row's current ``deleted_at``. The manual
       delete-forever passes ``retention_guard=None``: a human's explicit
       confirmation is not gated on a timer.
    3. **Protected** — a reference-folder original whose folder forbids file
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
                "Delete-forever: SKIPPING picture id=%s — frozen by a locked "
                "picture-set; row and file kept (unlock the set to delete it)",
                row.id,
            )
            continue
        if retention_guard is not None:
            permitted, reason = retention_guard.permits(row)
            if not permitted:
                plan.retained_count += 1
                logger.info(
                    "Scrapheap auto-purge: RETAINING picture id=%s path=%s — %s",
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
            # never here — a skipped protected picture writes NO ledger row.)
            plan.log_records.append(
                {
                    "path_sha": DeletedFileLog.hash_path(row.file_path),
                    "pixel_sha": row.pixel_sha,
                    "file_removed": True,
                }
            )
            plan.removal_targets.append(
                (row.id, row.file_path, was_reference_protected)
            )
    return plan


def classify_delete_preview(
    rows: list[ScrapheapRow],
    no_delete_folder_ids: set[int],
    locked_ids: set[int],
) -> dict:
    """Partition a delete-forever selection into three DISJOINT buckets.

    The confirm dialog has to state exactly what each button will destroy, and
    **no count may overstate destruction**. So the buckets are keyed on which
    action destroys the row, not on which properties it happens to have:

    * ``locked_count``   — frozen by a locked picture-set, whether or not it is
      ALSO protected. Destroyed by neither button.
    * ``protected_count``— protected and NOT locked. Destroyed only by
      "Delete all" (``include_protected=true``).
    * ``unprotected_count`` — neither. Destroyed by both buttons.

    They are disjoint and sum to ``total_count``, so "Delete unprotected only
    (``unprotected_count``)" and "Delete all — incl. ``protected_count``
    protected" are each literally true.

    **Locked is classified FIRST here, which is deliberately the opposite of
    ``auto_purge_exempt_reason`` (where protected wins).** The two answer
    different questions. The badge answers "why is this being kept?" and leads
    with the permanent, intrinsic reason. The preview answers "what will this
    button destroy?" and must lead with the BINDING blocker — for a
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


def remove_picture_files(
    image_root: str,
    targets: list[tuple[Optional[int], str, bool]],
) -> None:
    """Delete the on-disk originals (and thumbnails) for a purged selection."""
    for pic_id, rel_path, was_reference_protected in targets:
        file_path = ImageUtils.resolve_picture_path(image_root, rel_path)
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
                    "id=%s path=%s reference_protected=%s: %s",
                    pic_id,
                    file_path,
                    was_reference_protected,
                    e,
                    exc_info=True,
                )
        else:
            logger.warning(
                "Delete-forever: no on-disk file to remove for picture "
                "id=%s rel_path=%s (resolved=%s) reference_protected=%s",
                pic_id,
                rel_path,
                file_path,
                was_reference_protected,
            )
        thumb_path = ImageUtils.get_thumbnail_path(image_root, rel_path)
        if thumb_path and os.path.isfile(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception as e:
                logger.warning(
                    "Delete-forever: failed to delete thumbnail %s for "
                    "picture id=%s: %s",
                    thumb_path,
                    pic_id,
                    e,
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
            — row kept, file untouched, no ledger row. When ``True`` they are
            destroyed too; only an explicit human confirmation sets this.
        schedule_file_removal: Optional deferral hook called as
            ``schedule_file_removal(remove_picture_files, image_root, targets)``
            — the HTTP handler passes ``BackgroundTasks.add_task`` so files are
            removed after the response is sent. ``None`` removes them inline
            (the background task path, which is already off the event loop).
        retention_guard: The automatic path's independent re-check of the
            retention DEADLINE, evaluated against each row's CURRENT
            ``deleted_at``. Supplied by the auto-purge task; ``None`` on the
            manual, consent-gated path. (The locked-set freeze is NOT part of
            this — it binds on every path and is enforced unconditionally below.)

    Returns:
        A :class:`ScrapheapPurgeOutcome`.
    """
    rows = fetch_scrapheap_rows(vault, ids)
    if not rows:
        return ScrapheapPurgeOutcome()

    no_delete_folder_ids = fetch_no_delete_folder_ids(vault)
    # Unconditional: a locked picture-set freezes its members against EVERY
    # destruction path, manual delete-forever included. Looked up through the one
    # shared helper so this can never disagree with the sweep or the listing.
    locked_ids = locked_scrapheap_picture_ids(
        vault, [row.id for row in rows if row.id is not None]
    )
    plan = build_purge_plan(
        rows, no_delete_folder_ids, locked_ids, include_protected, retention_guard
    )

    # Rows + ledger first, files second: a crash between the two leaves orphaned
    # files that MissingFilePurgeFinder/the reference scan already handle, while
    # the reverse order would leave rows pointing at destroyed files.
    deleted_count = vault.db.run_task(
        purge_rows_in_session,
        plan.picture_ids,
        plan.log_records,
        priority=DBPriority.IMMEDIATE,
    )
    if schedule_file_removal is not None:
        schedule_file_removal(
            remove_picture_files, vault.image_root, plan.removal_targets
        )
    else:
        remove_picture_files(vault.image_root, plan.removal_targets)
    logger.info(
        "Delete-forever: purged %d, skipped %d protected, skipped %d locked, "
        "retained %d (include_protected=%s, guarded=%s)",
        deleted_count,
        plan.skipped_count,
        len(plan.skipped_locked),
        plan.retained_count,
        include_protected,
        retention_guard is not None,
    )
    return ScrapheapPurgeOutcome(
        deleted_count=deleted_count,
        skipped_count=plan.skipped_count,
        skipped_locked=sorted(plan.skipped_locked),
        retained_count=plan.retained_count,
        purged_ids=list(plan.picture_ids),
    )
