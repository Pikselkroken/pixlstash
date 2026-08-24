"""The move engine: place a new picture, and move an existing one when it must.

v1.11 Phase 4b (``docs/plans/v1.11.0-existing-library.md`` §4). The layout model
in ``utils/library_layout.py`` decides *where*; this decides *whether*, and then
does it.

**Two jobs, one primitive.** ``render`` says where a picture with no folder yet
belongs, and ``relocate`` says where one with a folder has to go — and returns
``None``, which is the answer almost every time, for every case the rule calls
still true. Adding a second project or a second person changes nothing here:
the folder said "this is a 2024 Shoots picture" and it still is.

**Opt-in by construction.** A root with no ``layout`` is not laid out, and
:func:`layout_roots` simply does not return it. That is why pointing PixlStash
at somebody's curated library moves nothing: there is no layout until the owner
picks one, and even then the paths that produced the assignments are true the
moment they are written.

**Every move is recorded** in ``picture_move`` before anything walks the tree
again, so the reference-folder scan can tell PixlStash's own write from the
owner's. Without that record Phase 5 reads our move back as intent, unfiles the
picture, and the two flip each other forever over real files.

**An emptied folder is kept.** Nothing here calls ``rmdir``. A folder the owner
made is theirs, and one PixlStash made is a place they may already have put
something we cannot see.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlmodel import Session, select

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    Project,
    ReferenceFolder,
    Tag,
)
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.db_models.picture_move import (
    REASON_LAYOUT,
    REASON_RENAME,
    RETENTION_S,
    PictureMove,
)
from pixlstash.database import DBPriority
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_mover import publish_no_clobber
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.library_layout import (
    DEFAULT_LAYOUT,
    Facet,
    format_layout,
    match_destination,
    FacetVocabulary,
    Layout,
    folder_name,
    parse_layout,
    relocate,
    render,
)

logger = get_logger(__name__)

#: The operation type the frontend keys its undo affordance off. Named like its
#: siblings (``pictures.scrapheap.move``, ``pictures.rotate``) because the
#: string is part of the API contract.
OP_LAYOUT_MOVE = "pictures.layout.move"

#: How many pictures one pass of the engine plans and moves. Small because the
#: unit of work is a file move on the owner's disk, and because the count that
#: is reported and the undo that reverses it should describe something a person
#: can still hold in their head.
BATCH_SIZE: int = 200


@dataclass(frozen=True)
class LayoutRoot:
    """A folder tree that has a layout, and the layout it has.

    Attributes:
        path: Absolute path to the root.
        layout: The parsed layout.
        reference_folder_id: The reference folder this is, or ``None`` for the
            library's own picture root. The two differ in exactly one way that
            matters here — a reference picture stores an absolute ``file_path``
            and a library one stores a path relative to the root — and
            :func:`relative_folder` is where that difference lives.
    """

    path: str
    layout: Layout
    reference_folder_id: Optional[int]


@dataclass
class PlannedMove:
    """One file the engine intends to move, resolved down to absolute paths."""

    picture_id: int
    root: LayoutRoot
    source_path: str
    destination_path: str
    #: What goes into ``Picture.file_path``: relative for a library picture,
    #: absolute for a reference-folder one.
    stored_path: str
    old_stored_path: str
    sidecars: list[tuple[str, str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reading the library
# ---------------------------------------------------------------------------


def layout_roots(session: Session, image_root: Optional[str]) -> dict:
    """Return every root that has a layout, keyed by reference-folder id.

    ``None`` keys the library's own picture root. A root whose stored layout
    cannot be parsed is left out and logged rather than guessed at: a layout
    with a segment silently dropped would move files somewhere nobody chose.
    """
    roots: dict = {}

    settings = session.exec(select(LibrarySettings)).first()
    if settings is not None and settings.layout and image_root:
        layout = _parse(settings.layout, settings.layout_unfiled, "this library")
        if layout is not None:
            roots[None] = LayoutRoot(
                path=os.path.abspath(image_root),
                layout=layout,
                reference_folder_id=None,
            )

    for folder in session.exec(
        select(ReferenceFolder).where(ReferenceFolder.layout.is_not(None))
    ).all():
        if folder.id is None or not folder.folder:
            continue
        layout = _parse(folder.layout, folder.layout_unfiled, folder.folder)
        if layout is not None:
            roots[folder.id] = LayoutRoot(
                path=os.path.abspath(folder.folder),
                layout=layout,
                reference_folder_id=folder.id,
            )
    return roots


def _parse(text: str, unfiled: Optional[str], where: str) -> Optional[Layout]:
    """Parse a stored layout, or log why it was ignored and return ``None``."""
    try:
        return parse_layout(text, unfiled or "_Inbox")
    except ValueError as exc:
        logger.error(
            "Layout for %s is not usable (%r, unfiled %r): %s. Nothing will be "
            "placed or moved there until it is corrected.",
            where,
            text,
            unfiled,
            exc,
        )
        return None


def library_vocabulary(session: Session, layouts: Iterable[Layout]) -> FacetVocabulary:
    """Return every entity name in the library, per facet the layouts use.

    Only the facets some layout actually names are loaded. The tag facet is the
    reason: a library has a handful of projects and thousands of distinct tags,
    and reading them all to answer a question no layout asked would be the most
    expensive part of a pass that usually decides nothing.

    **This is the language the folder names are read in**, and it is what
    separates "names a project this picture has left" (false, it moves) from
    "names nothing PixlStash knows about" (unreadable, it never moves). Deleting
    an entity therefore takes its name out of the language and freezes every
    folder named after it, which is the safe direction.
    """
    wanted = {
        facet for layout in layouts for segment in layout.segments for facet in segment
    }
    vocabulary: dict = {}
    if Facet.PROJECT in wanted:
        vocabulary[Facet.PROJECT] = [
            name for name in session.exec(select(Project.name)).all() if name
        ]
    if Facet.SET in wanted:
        vocabulary[Facet.SET] = [
            name for name in session.exec(select(PictureSet.name)).all() if name
        ]
    if Facet.PERSON in wanted:
        vocabulary[Facet.PERSON] = [
            name for name in session.exec(select(Character.name)).all() if name
        ]
    if Facet.TAG in wanted:
        vocabulary[Facet.TAG] = [
            tag for tag in session.exec(select(Tag.tag).distinct()).all() if tag
        ]
    return vocabulary


def picture_facets(session: Session, picture_ids: Iterable[int]) -> dict:
    """Return ``{picture_id: {facet: [name, ...]}}`` for *picture_ids*.

    Names are ordered most-preferred first, and the order is fixed rather than
    chosen: ``Picture.project_id`` (the picture's primary project) leads, then
    every other membership by ascending row id, and sets and people likewise.
    A picture in three projects therefore renders into the same folder today and
    tomorrow — which matters because an unstable choice would make the engine
    move files back and forth for no change at all.
    """
    ids = sorted({int(pid) for pid in picture_ids})
    if not ids:
        return {}
    facets: dict = {
        pid: {Facet.PROJECT: [], Facet.SET: [], Facet.PERSON: [], Facet.TAG: []}
        for pid in ids
    }

    primary: dict = {}
    for picture_id, project_id in session.exec(
        select(Picture.id, Picture.project_id).where(Picture.id.in_(ids))
    ).all():
        if project_id is not None:
            primary[int(picture_id)] = int(project_id)

    for picture_id, name, project_id in session.exec(
        select(PictureProjectMember.picture_id, Project.name, Project.id)
        .join(Project, Project.id == PictureProjectMember.project_id)
        .where(PictureProjectMember.picture_id.in_(ids))
        .order_by(Project.id)
    ).all():
        if name:
            bucket = facets[int(picture_id)][Facet.PROJECT]
            if primary.get(int(picture_id)) == int(project_id):
                bucket.insert(0, name)
            else:
                bucket.append(name)

    for picture_id, name in session.exec(
        select(PictureSetMember.picture_id, PictureSet.name)
        .join(PictureSet, PictureSet.id == PictureSetMember.set_id)
        .where(PictureSetMember.picture_id.in_(ids))
        .order_by(PictureSet.id)
    ).all():
        if name:
            facets[int(picture_id)][Facet.SET].append(name)

    seen_people: dict = {}
    for picture_id, name, character_id in session.exec(
        select(Face.picture_id, Character.name, Character.id)
        .join(Character, Character.id == Face.character_id)
        .where(Face.picture_id.in_(ids))
        .order_by(Character.id)
    ).all():
        if not name:
            continue
        key = (int(picture_id), int(character_id))
        if key in seen_people:
            continue
        seen_people[key] = True
        facets[int(picture_id)][Facet.PERSON].append(name)

    for picture_id, tag in session.exec(
        select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(ids)).order_by(Tag.tag)
    ).all():
        if tag:
            facets[int(picture_id)][Facet.TAG].append(tag)

    return facets


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def absolute_path(picture: Picture, root: LayoutRoot) -> Optional[str]:
    """Return the picture's file as an absolute path under *root*, or ``None``.

    ``None`` when the stored path resolves outside the root. That is not a
    tidiness check: everything below moves a file, and a row whose path escaped
    its root is exactly the row a move must not act on.
    """
    stored = picture.file_path
    if not stored:
        return None
    resolved = os.path.abspath(
        stored if os.path.isabs(stored) else os.path.join(root.path, stored)
    )
    if resolved != root.path and not resolved.startswith(root.path + os.sep):
        return None
    return resolved


def relative_folder(absolute: str, root: LayoutRoot) -> str:
    """Return the folder the picture is in, relative to the root, ``/``-joined."""
    relative = os.path.relpath(os.path.dirname(absolute), root.path)
    if relative in (".", os.curdir):
        return ""
    return relative.replace(os.sep, "/")


def stored_form(absolute: str, root: LayoutRoot) -> str:
    """Return the value ``Picture.file_path`` should hold for *absolute*.

    Absolute for a reference-folder picture, relative for a library one — the
    two conventions the rest of the product already reads (``get_thumbnail_path``
    branches on exactly this).
    """
    if root.reference_folder_id is not None:
        return absolute
    return os.path.relpath(absolute, root.path).replace(os.sep, "/")


def destination_folder(facets, root: LayoutRoot) -> str:
    """Return the folder a *new* picture belongs in, relative to the root."""
    return render(facets, root.layout)


def placement_subfolder(
    session: Session,
    image_root: Optional[str],
    *,
    project_id: Optional[int] = None,
    set_id: Optional[int] = None,
) -> str:
    """Return where a picture being imported belongs, relative to the root.

    Job one of the engine, and the cheap half: a picture that has never had a
    folder cannot have a false one, so this is ``render`` and nothing else.

    ``""`` when the library root has no layout — every library today — and the
    import writes where it always did.

    **Only the assignments that are already true are used.** A drop-to-person
    import carries a *pending* character id: the picture has no faces yet, so
    it is not that person's until face extraction says so, and writing it into
    their folder now would make the folder false the moment anything read it.
    It lands unfiled instead and leaves on its own when the face lands — which
    is exactly what the unfiled folder is drawn as doing.
    """
    root = layout_roots(session, image_root).get(None)
    if root is None:
        return ""
    facets: dict = {Facet.PROJECT: [], Facet.SET: [], Facet.PERSON: [], Facet.TAG: []}
    if project_id is not None:
        project = session.get(Project, int(project_id))
        if project is not None and project.name:
            facets[Facet.PROJECT].append(project.name)
    if set_id is not None:
        picture_set = session.get(PictureSet, int(set_id))
        if picture_set is not None and picture_set.name:
            facets[Facet.SET].append(picture_set.name)
    return destination_folder(facets, root)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def plan_moves(
    session: Session,
    picture_ids: Iterable[int],
    image_root: Optional[str],
) -> tuple[list, list]:
    """Work out which of *picture_ids* have to move, and where to.

    Nothing is touched. This is the "counted before it happens" half: the caller
    reports ``len(plan)`` and can show it before a single file is renamed.

    Returns:
        ``(plan, skipped)`` — the moves to make, and ``(picture_id, reason)``
        for every candidate that was considered and left alone for a reason
        worth naming. A picture whose folder is simply still true is in
        neither: that is the ordinary answer, not an exception.
    """
    ids = sorted({int(pid) for pid in picture_ids})
    if not ids:
        return [], []
    roots = layout_roots(session, image_root)
    if not roots:
        return [], []

    vocabulary = library_vocabulary(session, [root.layout for root in roots.values()])
    facets_by_id = picture_facets(session, ids)
    pictures = session.exec(select(Picture).where(Picture.id.in_(ids))).all()

    plan: list = []
    skipped: list = []
    claimed: set = set()
    for picture in pictures:
        if picture.id is None or picture.deleted:
            continue
        root = roots.get(picture.reference_folder_id)
        if root is None:
            continue
        source = absolute_path(picture, root)
        if source is None:
            skipped.append((picture.id, "path_outside_root"))
            continue
        facets = facets_by_id.get(int(picture.id), {})
        destination = relocate(
            relative_folder(source, root), facets, root.layout, vocabulary
        )
        if destination is None:
            continue
        move, reason = _prepare_move(picture, root, source, destination, claimed)
        if move is None:
            skipped.append((picture.id, reason))
            continue
        plan.append(move)
    return plan, skipped


def _sidecar_plan(
    picture: Picture, source: str, destination: str, root: LayoutRoot
) -> list:
    """Return ``(field, source, destination)`` for each sidecar that travels.

    A tags or description sidecar is *this file's* metadata written beside it,
    so leaving it behind would strand the owner's captions in a folder the
    picture has left and let the next scan read them as somebody else's.

    A layout move changes the folder and never the file name, so the sidecar
    keeps its own name too and only its folder changes. Only a sidecar inside
    the same root is carried: one pointed anywhere else is not this tree's to
    rearrange.
    """
    carried = []
    destination_dir = os.path.dirname(destination)
    for attribute in ("tags_file", "description_file"):
        sidecar = getattr(picture, attribute, None)
        if not sidecar or not os.path.isfile(sidecar):
            continue
        sidecar_abs = os.path.abspath(sidecar)
        if not sidecar_abs.startswith(root.path + os.sep):
            continue
        if os.path.dirname(sidecar_abs) != os.path.dirname(source):
            # Not a sibling of the picture. Leave it where it is rather than
            # relocating a file the move has no claim on.
            continue
        carried.append(
            (
                attribute,
                sidecar_abs,
                os.path.join(destination_dir, os.path.basename(sidecar_abs)),
            )
        )
    return carried


# ---------------------------------------------------------------------------
# Doing it
# ---------------------------------------------------------------------------


def apply_moves(
    session: Session,
    plan: list,
    *,
    image_root: Optional[str],
    reason: str = REASON_LAYOUT,
) -> list:
    """Move the planned files and repoint their rows. Returns the moved ids.

    Ordering per picture: **claim the destination, drop the source name, carry
    the thumbnail and sidecars, then write the row.** The claim is
    ``publish_no_clobber``, the same single-syscall primitive the model shelf
    uses, so a name that appeared since the plan was made is refused rather than
    overwritten — this walks the owner's own library and there is no file here
    it is entitled to destroy.

    A picture that cannot be moved is skipped and logged; the rest of the batch
    still goes. A failure *after* files have moved rolls the files back and
    re-raises, because the caller's transaction is about to disappear and a row
    naming a path where no file is would outlive it.
    """
    moved: list = []
    done: list = []
    try:
        for move in plan:
            if not _move_one_file(move):
                continue
            done.append(move)
            picture = session.get(Picture, move.picture_id)
            if picture is None:
                logger.warning(
                    "Layout move: picture %d vanished before its move to %s could "
                    "be recorded; the file is at the new path and the next scan "
                    "will re-import it.",
                    move.picture_id,
                    move.destination_path,
                )
                continue
            picture.file_path = move.stored_path
            if not _carry_thumbnail(
                image_root, move.source_path, move.destination_path
            ):
                # Nothing to carry, or it could not be carried. Point
                # MissingThumbnailFinder at the picture instead of leaving the
                # row claiming a bitmap that is not at the new name.
                picture.thumbnail_width = None
                picture.thumbnail_height = None
            for attribute, _, destination in move.sidecars:
                setattr(picture, attribute, destination)
            # The debounce stamp is spent either way: the question has been
            # asked and answered.
            picture.layout_check_due_at = None
            session.add(picture)
            session.add(
                PictureMove(
                    picture_id=move.picture_id,
                    old_path=move.old_stored_path or move.source_path,
                    new_path=move.stored_path,
                    reason=reason,
                )
            )
            moved.append(move.picture_id)
    except BaseException:
        for move in reversed(done):
            _undo_one_file(move)
        raise
    return moved


def _move_one_file(move: PlannedMove) -> bool:
    """Move one picture and its sidecars. ``False`` when it was left alone."""
    try:
        os.makedirs(os.path.dirname(move.destination_path), exist_ok=True)
        publish_no_clobber(move.source_path, move.destination_path)
    except OSError as exc:
        logger.error(
            "Layout move: could not move %s to %s (%s); the file is left exactly "
            "where it is and the picture keeps its current folder.",
            move.source_path,
            move.destination_path,
            exc,
        )
        return False
    for _, source, destination in move.sidecars:
        try:
            publish_no_clobber(source, destination)
        except OSError as exc:
            # The picture has already moved. A sidecar left behind is visible
            # and repairable; refusing the whole move now is not, because the
            # image is at the new name.
            logger.warning(
                "Layout move: moved %s but could not carry its sidecar %s to %s "
                "(%s); the sidecar is still at the old path.",
                move.destination_path,
                source,
                destination,
                exc,
            )
    return True


def _undo_one_file(move: PlannedMove) -> None:
    """Put one already-moved file back, best effort, while an error is in flight."""
    for _, source, destination in move.sidecars:
        _restore_file(destination, source)
    _restore_file(move.destination_path, move.source_path)


def _restore_file(current: str, original: str) -> None:
    try:
        if os.path.exists(current):
            os.makedirs(os.path.dirname(original), exist_ok=True)
            publish_no_clobber(current, original)
    except OSError as exc:
        logger.error(
            "Layout move: could not put %s back at %s (%s). The file is at the "
            "new path with no row naming it; the next scan re-imports it.",
            current,
            original,
            exc,
        )


def _carry_thumbnail(image_root: Optional[str], old_path: str, new_path: str) -> bool:
    """Move a moved picture's thumbnail to its new path-derived name.

    Both conventions are handled by ``get_thumbnail_path`` itself: a library
    picture's thumbnail is a sibling file, a reference picture's is a hashed
    name under ``.ref_thumbs``. Either way it is derived from the path, so a
    move that did not carry it would strand a bitmap nothing ever collects —
    the sweep only ever looks where a row's *current* path says.
    """
    old_thumb = ImageUtils.get_thumbnail_path(image_root, old_path)
    new_thumb = ImageUtils.get_thumbnail_path(image_root, new_path)
    if not old_thumb or not new_thumb:
        return False
    if old_thumb == new_thumb:
        return os.path.exists(new_thumb)
    try:
        if not os.path.exists(old_thumb):
            return False
        os.makedirs(os.path.dirname(new_thumb), exist_ok=True)
        os.replace(old_thumb, new_thumb)
        return True
    except OSError as exc:
        logger.warning(
            "Layout move: could not carry the thumbnail %s -> %s (%s); it will be "
            "regenerated and %s may be left behind.",
            old_thumb,
            new_thumb,
            exc,
            old_thumb,
        )
        return False


def describe_drift(
    session: Session, picture_ids: Iterable[int], image_root: Optional[str]
) -> dict:
    """Report, per picture, where the layout would put it if it were asked.

    Backs the **Move to match** offer. A picture whose folder has drifted is
    still filed truthfully; nothing here moves anything, and nothing here calls
    it wrong. ``suggested_folder`` is ``None`` for every picture the offer does
    not apply to — no layout, not in a laid-out root, off-layout, or already
    where ``render`` would put it.
    """
    ids = sorted({int(pid) for pid in picture_ids})
    if not ids:
        return {}
    roots = layout_roots(session, image_root)
    if not roots:
        return {pid: None for pid in ids}
    vocabulary = library_vocabulary(session, [root.layout for root in roots.values()])
    facets_by_id = picture_facets(session, ids)

    report: dict = {pid: None for pid in ids}
    for picture in session.exec(select(Picture).where(Picture.id.in_(ids))).all():
        root = roots.get(picture.reference_folder_id)
        if root is None or picture.id is None:
            continue
        source = absolute_path(picture, root)
        if source is None:
            continue
        current = relative_folder(source, root)
        facets = facets_by_id.get(int(picture.id), {})
        report[int(picture.id)] = {
            "current_folder": current,
            "suggested_folder": match_destination(
                current, facets, root.layout, vocabulary
            ),
            "layout": format_layout(root.layout),
        }
    return report


def plan_match_moves(
    session: Session, picture_ids: Iterable[int], image_root: Optional[str]
) -> tuple[list, list]:
    """Plan the **offered** moves for *picture_ids*, one per drifted picture.

    The same planning as :func:`plan_moves`, against
    :func:`~pixlstash.utils.library_layout.match_destination` instead of the
    truth check — because this is the owner asking, not the rule deciding. Every
    refusal the automatic path makes is made here too: a source outside its
    root, a symlink, a name already taken at the destination.
    """
    ids = sorted({int(pid) for pid in picture_ids})
    if not ids:
        return [], []
    roots = layout_roots(session, image_root)
    if not roots:
        return [], []
    vocabulary = library_vocabulary(session, [root.layout for root in roots.values()])
    facets_by_id = picture_facets(session, ids)

    plan: list = []
    skipped: list = []
    claimed: set = set()
    for picture in session.exec(select(Picture).where(Picture.id.in_(ids))).all():
        if picture.id is None or picture.deleted:
            continue
        root = roots.get(picture.reference_folder_id)
        if root is None:
            skipped.append((picture.id, "no_layout"))
            continue
        source = absolute_path(picture, root)
        if source is None:
            skipped.append((picture.id, "path_outside_root"))
            continue
        destination = match_destination(
            relative_folder(source, root),
            facets_by_id.get(int(picture.id), {}),
            root.layout,
            vocabulary,
        )
        if destination is None:
            skipped.append((picture.id, "already_matches"))
            continue
        move, reason = _prepare_move(picture, root, source, destination, claimed)
        if move is None:
            skipped.append((picture.id, reason))
            continue
        plan.append(move)
    return plan, skipped


def _prepare_move(
    picture: Picture,
    root: LayoutRoot,
    source: str,
    destination: str,
    claimed: set,
) -> tuple:
    """Turn a decided destination folder into a move, or say why there is none.

    Shared by the rule's own moves and the offered ones so the refusals cannot
    drift apart — the offer must be exactly as careful as the automatic path,
    not less.

    Returns:
        ``(PlannedMove, None)`` or ``(None, reason)``.
    """
    if not os.path.isfile(source):
        return None, "source_file_missing"
    if os.path.islink(source):
        # A symlink standing inside the library is not a file the library owns.
        # ``publish_no_clobber`` links the TARGET, so moving one would pull
        # whatever it points at — anywhere on the machine — into the tree under
        # the link's name. Same read-escape-in-a-write-sink shape #1024 closed
        # for the rotate sink, and the same answer: decline the picture and
        # change nothing about it.
        return None, "source_is_symlink"
    destination_path = os.path.join(
        root.path, *destination.split("/"), os.path.basename(source)
    )
    # Two pictures of the same name from two folders can render into one. The
    # claim set catches the pair inside this batch; ``publish_no_clobber``
    # catches everything else, including a file that appeared while the plan was
    # being made. Refused, never uniquified: renaming the owner's file to make
    # room is a bigger liberty than declining to move it.
    if destination_path in claimed or os.path.exists(destination_path):
        return None, "destination_taken"
    claimed.add(destination_path)
    return (
        PlannedMove(
            picture_id=int(picture.id),
            root=root,
            source_path=source,
            destination_path=destination_path,
            stored_path=stored_form(destination_path, root),
            old_stored_path=picture.file_path,
            sidecars=_sidecar_plan(picture, source, destination_path, root),
        ),
        None,
    )


# ---------------------------------------------------------------------------
# What the routes call
# ---------------------------------------------------------------------------


def picture_layout(vault, picture_id: int) -> Optional[dict]:
    """Return one picture's current folder and the layout's offer for it.

    ``None`` when the picture is not in a root that has a layout — including
    when it does not exist, which the caller tells apart for itself.
    """
    report = vault.db.run_immediate_read_task(
        describe_drift, [int(picture_id)], vault.image_root
    )
    return report.get(int(picture_id))


def picture_exists(vault, picture_id: int) -> bool:
    """Whether the picture is in this library at all."""
    return bool(
        vault.db.run_immediate_read_task(
            lambda session: session.get(Picture, int(picture_id)) is not None
        )
    )


def move_to_match(vault, picture_ids: Iterable[int], **operation_context) -> tuple:
    """Take the **Move to match** offer for *picture_ids*.

    The owner asking, not the rule deciding, so it moves pictures whose folder
    is still perfectly true. Everything else is the automatic path's: the same
    planner, the same refusals, one operation-log row for the whole request so
    one undo puts every file back, and no folder is ever deleted for being left
    empty.

    Returns:
        ``(moved_ids, skipped, operation_id)``.
    """
    # Local import: ``operation_log_service`` imports ``restore_location`` from
    # this module for its location applier, so the two cannot see each other at
    # module load. Nothing else in here needs the log.
    from pixlstash.services.operation_log_service import (
        capture_state_in_session,
        record_operation_in_session,
    )

    image_root = vault.image_root
    ids = list(picture_ids)

    def _move(session: Session):
        plan, skipped = plan_match_moves(session, ids, image_root)
        if not plan:
            return [], skipped, None
        # Counted before it happens, in the log as well as in the response.
        logger.info(
            "Move to match: moving %d file(s) at the owner's request.", len(plan)
        )
        targets = [move.picture_id for move in plan]
        before = capture_state_in_session(session, targets)
        moved = apply_moves(session, plan, image_root=image_root)
        after = capture_state_in_session(session, targets)
        operation = record_operation_in_session(
            session,
            op_type=OP_LAYOUT_MOVE,
            before=before,
            after=after,
            summary=_match_summary,
            undoable=True,
            commit=False,
            **operation_context,
        )
        session.commit()
        return moved, skipped, (operation.id if operation is not None else None)

    return vault.db.run_task(_move, priority=DBPriority.IMMEDIATE)


def _match_summary(before_delta: dict, after_delta: dict) -> str:
    """The sentence the undo toast shows for an offered move."""
    count = len(after_delta)
    return (
        "Moved 1 picture to match the layout"
        if count == 1
        else f"Moved {count} pictures to match the layout"
    )


# ---------------------------------------------------------------------------
# Renaming an entity renames its folder
# ---------------------------------------------------------------------------


def rename_entity_folders(
    session: Session,
    facet: Facet,
    old_name: str,
    new_name: str,
    *,
    image_root: Optional[str],
) -> int:
    """Rename the folders named after an entity. **Moves no files.**

    Renaming a project renames one directory and repoints the rows under it. It
    must not move anything, and it is not a nicety that it does not: a project
    with three thousand pictures would otherwise rewrite three thousand paths
    on disk to say the same thing in different words.

    It is also not optional. The layout reads a folder against the library's
    *current* vocabulary, so a folder still carrying the old name names nothing
    PixlStash knows about — unreadable, permanently frozen, and quietly outside
    the layout from then on. The rename is what keeps those pictures inside the
    language.

    Only directories at a depth some segment of the root's layout could put this
    facet at are considered, so a folder of the owner's own that happens to
    share the name is left alone.

    Returns:
        How many directories were renamed.
    """
    old_folder = folder_name(old_name)
    new_folder = folder_name(new_name)
    if old_folder == new_folder:
        return 0

    renamed = 0
    for root in layout_roots(session, image_root).values():
        depths = [
            depth
            for depth, segment in enumerate(root.layout.segments)
            if facet in segment
        ]
        for depth in depths:
            for parent in _directories_at_depth(root.path, depth):
                source = os.path.join(parent, old_folder)
                destination = os.path.join(parent, new_folder)
                if not os.path.isdir(source):
                    continue
                if os.path.exists(destination):
                    logger.warning(
                        "Layout rename: %s already exists, so %s keeps its old "
                        "name. Its pictures read as unfiled against the layout "
                        "until one of the two folders is renamed by hand.",
                        destination,
                        source,
                    )
                    continue
                try:
                    os.rename(source, destination)
                except OSError as exc:
                    logger.error(
                        "Layout rename: could not rename %s to %s (%s); the "
                        "folder keeps its old name.",
                        source,
                        destination,
                        exc,
                    )
                    continue
                renamed += 1
                _repoint_under(session, root, source, destination, image_root)
    return renamed


def _directories_at_depth(root: str, depth: int) -> list:
    """Return the directories that are *depth* levels below *root*.

    ``depth`` 0 is the root itself. Walked level by level rather than with
    ``os.walk`` so a deep library costs the levels the layout actually has,
    which is two.
    """
    level = [root]
    for _ in range(depth):
        children = []
        for parent in level:
            try:
                with os.scandir(parent) as entries:
                    children.extend(
                        entry.path
                        for entry in entries
                        if entry.is_dir(follow_symlinks=False)
                    )
            except OSError as exc:
                logger.warning("Layout rename: cannot list %s (%s)", parent, exc)
        level = children
    return level


def _repoint_under(
    session: Session,
    root: LayoutRoot,
    old_dir: str,
    new_dir: str,
    image_root: Optional[str],
) -> None:
    """Rewrite every ``file_path`` under a renamed directory, and journal it.

    The rows are journalled for the same reason a move is: the scan sees every
    one of these files at a new path and would otherwise read a rename as the
    owner reorganising their library by hand.
    """
    pictures = session.exec(
        select(Picture).where(Picture.reference_folder_id == root.reference_folder_id)
    ).all()
    prefix = old_dir + os.sep
    for picture in pictures:
        source = absolute_path(picture, root)
        if source is None or not source.startswith(prefix):
            continue
        destination = os.path.join(new_dir, os.path.relpath(source, old_dir))
        old_stored = picture.file_path
        picture.file_path = stored_form(destination, root)
        if not _carry_thumbnail(image_root, source, destination):
            picture.thumbnail_width = None
            picture.thumbnail_height = None
        for attribute in ("tags_file", "description_file"):
            sidecar = getattr(picture, attribute, None)
            if sidecar and os.path.abspath(sidecar).startswith(prefix):
                setattr(
                    picture,
                    attribute,
                    os.path.join(
                        new_dir, os.path.relpath(os.path.abspath(sidecar), old_dir)
                    ),
                )
        session.add(picture)
        session.add(
            PictureMove(
                picture_id=picture.id,
                old_path=old_stored or source,
                new_path=picture.file_path,
                reason=REASON_RENAME,
            )
        )


def restore_location(
    session: Session,
    picture_id: int,
    stored_path: Optional[str],
    *,
    image_root: Optional[str],
) -> bool:
    """Put a picture's file back at *stored_path*. The undo half of a move.

    **Absolute, not a delta**, for the reason ``apply_orientation`` is: applying
    the same recorded path twice is a no-op, so an undo is idempotent and a file
    something else has since moved converges instead of drifting.

    It refuses anything it cannot prove is a move *within one root*: no root, a
    path that resolves outside it, a symlink, a destination that is already
    taken. Every refusal leaves the file exactly where it is and says so — this
    walks the owner's own library and there is no file in it that an undo is
    entitled to destroy.

    The move is journalled like any other, because it is one: without the row
    the reference-folder scan reads the undo as the owner moving the file back
    by hand.

    Returns:
        Whether the file was actually moved.
    """
    if not stored_path:
        logger.warning(
            "Layout undo: picture %s has no recorded location, so this restore "
            "cannot move its file.",
            picture_id,
        )
        return False
    picture = session.get(Picture, picture_id)
    if picture is None:
        return False

    root_path = _root_path_for(session, picture, image_root)
    if not root_path:
        logger.error(
            "Layout undo: picture %s has no library root to resolve %r against; "
            "the file is not touched.",
            picture_id,
            stored_path,
        )
        return False
    root = LayoutRoot(
        path=root_path,
        layout=DEFAULT_LAYOUT,
        reference_folder_id=picture.reference_folder_id,
    )

    source = absolute_path(picture, root)
    destination = os.path.abspath(
        stored_path
        if os.path.isabs(stored_path)
        else os.path.join(root_path, stored_path)
    )
    if source is None or not destination.startswith(root_path + os.sep):
        logger.error(
            "Layout undo: picture %s would move between %r and %r, at least one "
            "of which is outside its root %r; the file is not touched.",
            picture_id,
            picture.file_path,
            stored_path,
            root_path,
        )
        return False
    if source == destination:
        return False
    if not os.path.isfile(source) or os.path.islink(source):
        logger.error(
            "Layout undo: picture %s does not have a plain file at %r, so it "
            "cannot be moved back to %r.",
            picture_id,
            source,
            destination,
        )
        return False

    move = PlannedMove(
        picture_id=picture_id,
        root=root,
        source_path=source,
        destination_path=destination,
        stored_path=stored_form(destination, root),
        old_stored_path=picture.file_path,
        sidecars=_sidecar_plan(picture, source, destination, root),
    )
    if not _move_one_file(move):
        return False
    picture.file_path = move.stored_path
    if not _carry_thumbnail(image_root, source, destination):
        picture.thumbnail_width = None
        picture.thumbnail_height = None
    for attribute, _, sidecar_destination in move.sidecars:
        setattr(picture, attribute, sidecar_destination)
    picture.layout_check_due_at = None
    session.add(picture)
    session.add(
        PictureMove(
            picture_id=picture_id,
            old_path=move.old_stored_path or source,
            new_path=move.stored_path,
            reason=REASON_LAYOUT,
        )
    )
    return True


def _root_path_for(
    session: Session, picture: Picture, image_root: Optional[str]
) -> Optional[str]:
    """Return the root a picture's paths are resolved against."""
    if picture.reference_folder_id is None:
        return os.path.abspath(image_root) if image_root else None
    folder = session.get(ReferenceFolder, picture.reference_folder_id)
    if folder is None or not folder.folder:
        return None
    return os.path.abspath(folder.folder)


# ---------------------------------------------------------------------------
# The journal
# ---------------------------------------------------------------------------


def claim_own_moves(session: Session, pairs: Iterable[tuple]) -> set:
    """Return the ``(old_path, new_path)`` pairs PixlStash made itself.

    Called by the reference-folder scan once it has paired a vanished path with
    an arrived one. A claimed pair is marked consumed so a *second*, genuine
    move of the same file between the same two folders is not waved through as
    ours as well.
    """
    wanted = [(str(old), str(new)) for old, new in pairs]
    if not wanted:
        return set()
    olds = {old for old, _ in wanted}
    rows = session.exec(
        select(PictureMove)
        .where(PictureMove.old_path.in_(olds))
        .where(PictureMove.consumed.is_(False))
        .order_by(PictureMove.id)
    ).all()
    by_pair: dict = {}
    for row in rows:
        by_pair.setdefault((row.old_path, row.new_path), []).append(row)

    claimed = set()
    for pair in wanted:
        candidates = by_pair.get(pair)
        if not candidates:
            continue
        row = candidates.pop(0)
        row.consumed = True
        session.add(row)
        claimed.add(pair)
    return claimed


def prune_move_journal(session: Session, older_than: Optional[datetime] = None) -> int:
    """Drop journal rows past the retention window. Returns how many went.

    Retention is what stops an old row excusing a new move: the pair
    ``(old_path, new_path)`` is not unique over time, and a row kept forever
    would let a genuine owner move between the same two folders next month be
    read as PixlStash's own.
    """
    cutoff = older_than or (datetime.utcnow() - timedelta(seconds=RETENTION_S))
    stale = session.exec(select(PictureMove).where(PictureMove.moved_at < cutoff)).all()
    for row in stale:
        session.delete(row)
    return len(stale)
