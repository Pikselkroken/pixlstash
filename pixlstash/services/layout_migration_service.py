"""Moving an existing library onto its layout (v1.11 Phase 4c).

The one operation in this release that deliberately moves everything. It is
offered when a layout is set or changed and never taken automatically, and it is
**not the move-when-false rule**: under that rule a flat path parses against
nothing, can never be false, and never moves, which is exactly why old libraries
need no migration. This is the owner asking for something else - *make it all
match, now* - so it asks :func:`~pixlstash.utils.library_layout.migrate_destination`
where the layout would put each picture rather than whether its folder has
stopped being true.

Everything that touches a file is Phase 4b's: the same planner refusals, the
same :func:`~pixlstash.services.layout_move_service.apply_moves`, the same
``PictureMove`` journal that stops the reference-folder scan reading our own
writes as owner intent, and the same ``FACET_LOCATION`` undo. What is added here
is the four things a whole-library move needs and a one-picture move does not:

* **a preview**, because consent to move 4,109 files is not consent anybody can
  give from a sentence that does not say 4,109;
* **a collision rule**, because two folders' worth of ``0001.png`` render into
  one path - ``_free_name``, applied visibly and never silently overwriting;
* **cross-volume detection**, because a mount point inside the library is a
  destination ``publish_no_clobber`` cannot reach at all;
* **resumability**, because a run that dies on file 27,000 must be finishable
  rather than restartable.

The last one is why this runs in passes rather than in one transaction. Every
pass is its own operation row and its own commit, all of them stamped with one
server-minted ``batch_id``, and a batch is one undo unit
(``_batch_members_in_session``) - so the owner still gets *one* Ctrl+Z that puts
every file back, and a pass that fails leaves the tree half-moved and wholly
consistent. Re-running finishes it: a picture already where the layout wants it
plans no move at all, so the operation is idempotent by construction and needs
no checkpoint of its own.
"""

import os
import uuid
from collections import Counter
from typing import Optional

from sqlmodel import Session, select

from pixlstash.db_models.picture import Picture
from pixlstash.services.model_mover import same_device
from pixlstash.services.layout_move_service import (
    LayoutRoot,
    OP_LAYOUT_MOVE,
    PlannedMove,
    _prepare_move,
    absolute_path,
    apply_moves,
    drop_unlanded_journal,
    journal_moves,
    move_planned_files,
    layout_roots,
    picture_facets,
    relative_folder,
    record_moves,
    rollback_applied_moves,
    stored_form,
)
from pixlstash.database import DBPriority
from pixlstash.utils.library_layout import format_layout, migrate_destination
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "MIGRATION_BATCH",
    "apply_moves",
    "plan_migration",
    "run_migration_pass",
]

#: How many pictures one pass examines. The same order as
#: ``layout_move_service.BATCH_SIZE`` and for the same reason - the unit of work
#: is a file move on the owner's disk and the whole pass holds the single DB
#: writer thread - but the *undo* is not bounded by it here, because every pass
#: of one migration shares a batch id.
MIGRATION_BATCH: int = 200

#: How many before/after path pairs the preview shows. Enough to recognise the
#: shape of the tree that is about to appear; not a listing of the library.
SAMPLE_SIZE: int = 8


def _library_root(session: Session, image_root: Optional[str]) -> Optional[LayoutRoot]:
    """The library's own picture root, if it has a layout. ``None`` otherwise.

    Reference folders are deliberately not migrated from here. A reference
    folder is a tree the owner arranged and PixlStash indexes in place, and
    ``PATCH /server-config/layout`` - the gesture this hangs off - says nothing
    about one. Migrating a reference folder would need its own consent, on its
    own route, naming that folder.
    """
    return layout_roots(session, image_root).get(None)


def plan_migration(
    session: Session,
    image_root: Optional[str],
    *,
    after_id: int = 0,
    limit: Optional[int] = None,
    sweep_unfiled: bool = False,
) -> tuple[list, list, int, Optional[int]]:
    """Plan the migration of the library's own root onto its layout.

    Args:
        after_id: Resume cursor - only pictures with a higher id are examined.
            Ascending id is what makes a pass resumable without a checkpoint
            table: the window is a fact about the database, not about the run.
        limit: How many pictures to examine, or ``None`` for the whole library
            (what the preview does).
        sweep_unfiled: Also move the pictures the layout cannot place into its
            unfiled folder. See :func:`migrate_destination`.

    Returns:
        ``(plan, skipped, examined, last_id)``. *skipped* carries Phase 4b's own
        refusal vocabulary; a picture the layout simply leaves alone is not a
        refusal and is not in it.
    """
    root = _library_root(session, image_root)
    if root is None:
        return [], [], 0, None

    query = (
        select(Picture)
        .where(Picture.reference_folder_id.is_(None))
        .where(Picture.deleted.is_(False))
        .where(Picture.id > after_id)
        .order_by(Picture.id)
    )
    if limit is not None:
        query = query.limit(limit)
    pictures = list(session.exec(query).all())
    if not pictures:
        return [], [], 0, None

    facets_by_id = picture_facets(session, [p.id for p in pictures if p.id is not None])

    plan: list = []
    skipped: list = []
    claimed: set = set()
    devices: dict = {}
    last_id: Optional[int] = None
    for picture in pictures:
        if picture.id is None:
            continue
        last_id = int(picture.id)
        source = absolute_path(picture, root)
        if source is None:
            skipped.append((last_id, "path_outside_root"))
            continue
        destination = migrate_destination(
            relative_folder(source, root),
            facets_by_id.get(last_id, {}),
            root.layout,
            sweep_unfiled=sweep_unfiled,
        )
        if destination is None:
            # Already where the layout wants it, or the layout cannot place it
            # and the owner did not ask for those swept up. Neither is a
            # refusal.
            continue
        move, reason = _prepare_move(
            picture, root, source, destination, claimed, uniquify=True
        )
        if move is None:
            skipped.append((last_id, reason))
            continue
        if not _same_volume(move, devices):
            # A mount point or a bind mount inside the library. This is not a
            # slow move, it is not a move at all: ``publish_no_clobber`` claims
            # the destination with ``os.link`` and falls back to ``os.replace``,
            # and both raise EXDEV across a device. Refusing it in the plan is
            # what puts it in ``skipped`` and in the preview's count, instead of
            # letting ``apply_moves`` discover it per file and log it.
            skipped.append((last_id, "destination_other_volume"))
            claimed.discard(move.destination_path)
            continue
        plan.append(move)
    return plan, skipped, len(pictures), last_id


# ---------------------------------------------------------------------------
# The preview
# ---------------------------------------------------------------------------


def _nearest_existing(path: str) -> str:
    """The first ancestor of *path* that exists. A folder the migration has not
    created yet has no device of its own; the one it will get is this one's."""
    probe = path
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return probe
        probe = parent
    return probe


def _same_volume(move: PlannedMove, devices: dict) -> bool:
    """Whether this move can be made at all.

    A mount point or a bind mount inside the library puts the destination on
    another filesystem, and there is no slow path to fall back on:
    ``publish_no_clobber`` claims the name with ``os.link`` and then with
    ``os.replace``, and both raise ``EXDEV`` across a device. So this is not
    "detect the copy and warn about its cost" - it is detect the move that
    cannot happen, and say so before the run rather than one log line per file
    during it. Copying across the boundary would be a new capability with its
    own verification, and is deliberately not in this phase.

    ``same_device`` is ``model_mover``'s, which is where the ``st_dev``
    reasoning (bind mounts, symlinked folders, and the seam a test needs to
    force the other branch on a single-volume machine) already lives.
    """
    source_dir = os.path.dirname(move.source_path)
    destination_dir = _nearest_existing(os.path.dirname(move.destination_path))
    key = (source_dir, destination_dir)
    if key not in devices:
        try:
            devices[key] = same_device(move.source_path, destination_dir)
        except OSError as exc:
            # Neither answer is safe to invent, and the planner's other
            # refusals already cover a source that is not there. Treat it as
            # movable and let ``apply_moves`` report the real error.
            logger.warning(
                "Layout migration: could not compare the filesystems of %s and "
                "%s (%s); planning the move anyway.",
                move.source_path,
                destination_dir,
                exc,
            )
            return True
    return devices[key]


def preview_in_session(
    session: Session, image_root: Optional[str], *, sweep_unfiled: bool = False
) -> dict:
    """Count what a migration would do, and move nothing.

    Planned in the same windows the run uses rather than in one pass over the
    library. That is not tidiness: ``picture_facets`` and ``_sizes_for`` pass
    their ids to a raw ``IN``, which Phase 4b never had to think about because
    it only ever handed them a 200-id batch, and a whole-library ``IN`` is the
    SQLite bound-parameter ceiling. Windowing keeps every clause the size the
    engine was written for and keeps the ORM identity map from holding the
    library.

    The windows do mean two pictures colliding across a window boundary are not
    seen as a collision here, only at the run - the claim set is per window, as
    it is in the engine. ``publish_no_clobber`` still refuses the overwrite, and
    the run suffixes it, so the count can be low and the behaviour cannot be
    wrong.

    ``tree`` is the same walk read a second way: the destination folders were
    already being collected to be counted, so per-folder arrivals and
    departures cost nothing beyond keeping them. Only ``have`` is new work, and
    it is one query over one column for the whole library - less than the walk
    it sits beside, which loads every picture row.

    Every path in the answer is in **stored** form - relative to the library
    root - because that is what the screen shows and because the absolute one
    says where the owner keeps their pictures.
    """
    root = _library_root(session, image_root)
    if root is None:
        return {
            "layout": None,
            "picture_count": 0,
            "folder_count": 0,
            "samples": [],
            "collision_count": 0,
            "collisions": [],
            "cross_volume_count": 0,
            "skipped_counts": {},
            "tree": [],
        }

    folders: set = set()
    samples: list = []
    collisions: list = []
    picture_count = 0
    collision_count = 0
    reasons: dict = {}
    # Aggregated off the plan the loop below already builds. The set of
    # destination folders was being counted and thrown away; these two keep the
    # same walk's answer per folder instead.
    arriving: Counter = Counter()
    leaving: Counter = Counter()
    cursor = 0
    while True:
        plan, skipped, examined, last_id = plan_migration(
            session,
            image_root,
            after_id=cursor,
            limit=MIGRATION_BATCH,
            sweep_unfiled=sweep_unfiled,
        )
        for move in plan:
            picture_count += 1
            folders.add(os.path.dirname(move.destination_path))
            arriving[_stored_folder(move.stored_path)] += 1
            leaving[
                _stored_folder(
                    move.old_stored_path or stored_form(move.source_path, root)
                )
            ] += 1
            if len(samples) < SAMPLE_SIZE:
                samples.append(_sample(move, root))
            # A suffixed basename is exactly what a collision is, so it is read
            # back off the plan rather than threaded out of the planner as a
            # third return value nothing else would want.
            if os.path.basename(move.destination_path) != os.path.basename(
                move.source_path
            ):
                collision_count += 1
                if len(collisions) < SAMPLE_SIZE:
                    collisions.append(_sample(move, root))
        for _picture_id, reason in skipped:
            reasons[reason] = reasons.get(reason, 0) + 1
        if examined < MIGRATION_BATCH:
            break
        cursor = last_id if last_id is not None else cursor + 1
    tree = _folder_tree(root, _folders_pictures_are_in(session), arriving, leaving)
    return {
        "layout": format_layout(root.layout),
        "picture_count": picture_count,
        "folder_count": len(folders),
        "samples": samples,
        "collision_count": collision_count,
        "collisions": collisions,
        "cross_volume_count": reasons.get("destination_other_volume", 0),
        "skipped_counts": reasons,
        "tree": tree,
    }


def preview_migration(vault, *, sweep_unfiled: bool = False) -> dict:
    """:func:`preview_in_session` on the vault's own read queue."""
    return vault.db.run_immediate_read_task(
        preview_in_session, vault.image_root, sweep_unfiled=sweep_unfiled
    )


def _sample(move: PlannedMove, root: LayoutRoot) -> dict:
    return {
        "picture_id": move.picture_id,
        "from": move.old_stored_path or stored_form(move.source_path, root),
        "to": move.stored_path,
    }


def _stored_folder(stored_path: str) -> str:
    """The folder part of a stored path. ``""`` is the library root itself.

    Split on ``/`` and not with :func:`os.path.dirname`, because a stored path
    is always ``/``-joined (:func:`~pixlstash.services.layout_move_service.stored_form`
    writes it that way on every platform) and ``dirname`` on Windows would also
    read a backslash inside a file name as a folder level.
    """
    head, separator, _tail = stored_path.rpartition("/")
    return head if separator else ""


def _folders_pictures_are_in(session: Session) -> Counter:
    """How many pictures sit in each folder today, keyed by stored folder.

    One query for the whole library, one column wide, grouped here rather than
    in SQL: SQLite has no ``dirname``, and the alternative - a folder-shaped
    ``LIKE`` per folder - is the per-folder query this must not become. The
    scope is the same as :func:`plan_migration`'s, or a folder would report a
    ``have`` counting rows the migration never looks at.
    """
    rows = session.exec(
        select(Picture.file_path)
        .where(Picture.reference_folder_id.is_(None))
        .where(Picture.deleted.is_(False))
    ).all()
    return Counter(_stored_folder(path) for path in rows if path)


def _folder_tree(
    root: LayoutRoot,
    have: Counter,
    arriving: Counter,
    leaving: Counter,
) -> list:
    """The library as this layout would draw it, every folder of it.

    A folder is in it when anything about it is non-zero, which is why the
    library root is not: it has no row of its own to show, and a synthetic one
    would draw a level the owner does not have. Uncapped on purpose: an earlier
    version kept the sixty busiest and counted the rest, and on a library filed
    by date that was "...and 299 more folders" over the rows the owner needed
    to check. A few thousand rows is a list; a count of the rows withheld is
    not.

    Sorted by path, so a parent that is in the list always comes immediately
    before its children and the screen can indent by ``depth`` without
    re-sorting. A parent can still be absent - a layout whose first segment
    only ever holds subfolders puts no pictures in it and moves none into it -
    and that is the inclusion rule, not a cap.
    """
    paths = set(have) | set(arriving) | set(leaving)
    paths.discard("")
    return [
        {
            "path": path,
            "name": path.rpartition("/")[2],
            "depth": path.count("/"),
            "have": have[path],
            "arriving": arriving[path],
            "leaving": leaving[path],
            "is_new": not os.path.isdir(os.path.join(root.path, *path.split("/"))),
        }
        for path in sorted(paths)
    ]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def new_batch_id() -> str:
    """A server-minted id grouping every pass of one migration into one undo."""
    return f"srv-layout-migration-{uuid.uuid4().hex[:16]}"


def _summary(before_delta: dict, after_delta: dict) -> str:
    count = len(after_delta)
    return (
        "Moved 1 picture onto the layout"
        if count == 1
        else f"Moved {count} pictures onto the layout"
    )


def run_migration_pass(
    vault,
    *,
    after_id: int = 0,
    limit: int = MIGRATION_BATCH,
    sweep_unfiled: bool = False,
    should_stop=None,
    **operation_context,
) -> dict:
    """Move one window of the library onto its layout.

    One pass, one operation row - and the caller repeats until ``done``,
    carrying ``next_after_id`` and the same ``batch_id`` each time. A pass that
    raises rolls its own files back and leaves every earlier pass standing,
    which is the half-moved-but-consistent tree Phase 4c asks for.

    **Three phases, and the middle one is not on the writer thread.** The plan
    and its journal commit first, then the renames run here, then the rows are
    repointed in a second short transaction. A whole-library migration moves
    real files by the thousand; doing that inside the serialised writer's open
    transaction meant a shutdown or a library switch could dispose the engine
    part way through a batch, and the best-effort rollback that followed is the
    one path that can lose a picture's metadata for good.
    """
    from pixlstash.services.operation_log_service import (
        capture_state_in_session,
        record_operation_in_session,
    )

    image_root = vault.image_root

    def _plan(session: Session):
        plan, skipped, examined, last_id = plan_migration(
            session,
            image_root,
            after_id=after_id,
            limit=limit,
            sweep_unfiled=sweep_unfiled,
        )
        if plan:
            logger.info(
                "Layout migration: moving %d file(s) onto the layout (pass after "
                "id %d, %d examined).",
                len(plan),
                after_id,
                examined,
            )
            # Committed before a single file moves, so a crash during the
            # renames leaves a record the purge sweep can repair from.
            journal_moves(session, plan)
            session.commit()
        return plan, skipped, examined, last_id

    plan, skipped, examined, last_id = vault.db.run_task(
        _plan, priority=DBPriority.IMMEDIATE
    )
    next_after_id = last_id if last_id is not None else after_id
    result = {
        "moved_picture_ids": [],
        "examined": examined,
        "skipped": [
            {"picture_id": picture_id, "reason": reason}
            for picture_id, reason in skipped
        ],
        "next_after_id": next_after_id,
        "done": examined < limit,
        "operation_id": None,
    }
    if not plan:
        return result

    targets = [move.picture_id for move in plan]

    def _record(session: Session, landed: list):
        before = capture_state_in_session(session, targets)
        moved = record_moves(session, landed, image_root=image_root)
        drop_unlanded_journal(session, plan, landed)
        after = capture_state_in_session(session, targets)
        operation = None
        if moved:
            operation = record_operation_in_session(
                session,
                op_type=OP_LAYOUT_MOVE,
                before=before,
                after=after,
                summary=_summary,
                undoable=True,
                commit=False,
                **operation_context,
            )
        session.commit()
        return moved, (operation.id if operation is not None else None)

    applied: list = []
    try:
        landed = move_planned_files(plan, applied=applied, should_stop=should_stop)
        moved, operation_id = vault.db.run_task(
            lambda session: _record(session, landed), priority=DBPriority.IMMEDIATE
        )
    except BaseException:
        # Same reasoning as ``move_to_match``: a row naming a path with no file
        # at it is purged within the hour, and the picture's metadata with it,
        # so the rollback covers the whole pass rather than the move loop. The
        # intent rows go with the files, or they would excuse a later genuine
        # owner move between the same two paths.
        rollback_applied_moves(applied, image_root)
        _abandon_journal(vault, plan)
        raise
    # A picture the plan named and the move loop did not move - a name that
    # appeared at the destination since the plan, a file locked on Windows, a
    # folder gone read-only. ``_move_one_file`` logs and carries on, which is
    # what makes a failing run finishable, but a caller that is about to stop
    # looping has to be told: without this the picture is in neither list and
    # the pass reports a clean finish over a file it never touched.
    left_behind = [pid for pid in targets if pid not in set(moved)]
    result["skipped"].extend(
        {"picture_id": picture_id, "reason": "move_failed"}
        for picture_id in left_behind
    )
    if left_behind:
        logger.warning(
            "Layout migration: %d of %d planned file(s) could not be moved "
            "and keep the folder they are in; see the errors above.",
            len(left_behind),
            len(targets),
        )
    result["moved_picture_ids"] = moved
    result["operation_id"] = operation_id
    return result


def _abandon_journal(vault, plan: list) -> None:
    """Drop the intent rows after a failed pass put the files back. Best effort.

    Runs while an error is already on its way to the caller, so a failure here
    is logged rather than raised - it would replace the real one.
    """
    try:
        vault.db.run_task(
            lambda session: (
                drop_unlanded_journal(session, plan, []),
                session.commit(),
            ),
            priority=DBPriority.IMMEDIATE,
        )
    except Exception as exc:
        logger.warning(
            "Layout migration: could not drop the move journal for %d abandoned "
            "move(s): %s. A later owner move between the same paths may be "
            "mistaken for ours until the row expires.",
            len(plan),
            exc,
        )
