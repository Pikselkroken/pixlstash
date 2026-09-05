"""Commit an accepted folder-structure mapping. v1.11 Phase 3.

``docs/plans/v1.11.0-existing-library.md`` §4 Phase 3; wire contract
``docs/integration_architecture.md`` §22. The read (Phase 2,
``folder_structure_service.py``) only ever proposes; this module is the one
place anything from the mapping screen is written.

**No file is moved, renamed or copied, at any stage, in either commit mode.**

- ``mode="reference"`` (the default): the scanned root is registered as an
  ordinary :class:`~pixlstash.db_models.reference_folder.ReferenceFolder` -
  indexed in place by the existing, already-shipped
  :class:`~pixlstash.tasks.reference_folder_scan_task.ReferenceFolderScanTask`,
  which is the only filesystem-reading step here and writes nothing but the
  vault database and its own thumbnail cache. Right for a folder *external* to
  the library's own storage.
- ``mode="local_import"``: for a root that IS the active library's own
  ``image_root`` (or a folder inside it) - the "Add a library" flow's
  "pictures" verdict, where the folder a fresh vault was just created in
  already held loose files. Those pictures already live where the library
  keeps its own, so they become ordinary MANAGED pictures (relative
  ``file_path``, same shape as any other import) rather than reference-folder
  ones. Routing this case through ``mode="reference"`` instead would either
  bypass or have to reimplement the exact conflict guard
  ``routes.reference_folders._validate_reference_folder_conflicts`` already
  enforces - a reference folder may never equal or contain ``image_root`` -
  which is the proof the two need to stay two commit modes, not one.
  ``local_import_pictures`` does the read this mode's own filesystem step
  (there is no already-shipped scan task for "index my own image_root in
  place"); ``apply_local_mapping`` then reuses the same entity-resolution code
  ``apply_mapping`` uses, via the shared ``_link_pictures`` helper, so the two
  modes cannot answer "who does this folder belong to" differently.

Once a mode's filesystem step has run, every newly-indexed picture is linked
to whichever accepted ancestor folder names it - a database row, never a
filesystem write.

An assignment names a folder, not a picture: two folders that resolve to the
same kind and the same name (a project called ``Mira`` appearing twice in the
tree, or a name the owner matched to an existing entity) become the *same*
row, exactly as `library_layout.folder_name` treats two on-disk spellings that
collapse to one path component as the same folder.
"""

from __future__ import annotations

import concurrent.futures
import io
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from PIL import Image
from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.character import Character
from pixlstash.db_models.face import Face
from pixlstash.db_models.folder_mapping_commit import (
    FolderMappingCommit,
    STATE_DONE,
    STATE_PENDING,
)
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.picture_set import PictureSet, PictureSetMember
from pixlstash.db_models.project import Project
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.db_models.tag import Tag, TAG_PENDING_SENTINEL
from pixlstash.pixl_logging import get_logger
from pixlstash.services.project_membership_service import (
    set_character_projects,
    set_picture_set_projects,
)
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.utils.service.label_ledger import POS, record_human_label
from pixlstash.utils.sql_chunking import chunked
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.library_layout import Facet
from pixlstash.utils.media_files import is_supported_media_file
from pixlstash.utils.path_utils import path_is_within
from pixlstash.utils.reference_folder_validator import (
    validate_reference_folder_accessible,
    validate_reference_folder_path,
)

logger = get_logger(__name__)

#: `local_import`'s own "hash + thumbnail many files" step - mirrors
#: `ReferenceFolderScanTask`'s `_BUILD_CHUNK_SIZE` / `_MAX_BUILD_WORKERS`,
#: the established shape for this exact piece of work, rather than a new one.
_BUILD_CHUNK_SIZE = 128
_MAX_BUILD_WORKERS = 8

#: How long the commit waits for the reference folder's first scan pass
#: before giving up. The release plan measured 28,412 files well inside this;
#: a scan that is still not done past it means something is actually stuck,
#: and the commit should fail rather than hang the screen forever.
INDEX_TIMEOUT_S = 30 * 60.0
_POLL_INTERVAL_S = 0.25

#: The facets a folder can be accepted as, plus "tag" which is a `Facet` value
#: too - every accepted `kind` the mapping screen sends is one of these.
_ACCEPTED_KINDS = frozenset(f.value for f in Facet)


class CommitError(Exception):
    """A refusal the route turns into an HTTP error."""


class CommitStopped(Exception):
    """The owner stopped a running commit; not a failure.

    Carries which stop it was, because the two settle the durable record
    differently: an abort abandons it, "organise later" finishes the indexing
    and records that the mapping was deliberately left undone.
    """

    def __init__(self, state: str):
        super().__init__(state)
        self.state = state


@dataclass(frozen=True)
class Assignment:
    """One accepted folder from the mapping screen.

    Attributes:
        relative_path: POSIX-separated, relative to the scanned root - the
            same handle the read's folder rows carry. ``""`` addresses the
            root folder itself.
        kind: One of `Facet`'s values (``project``, ``person``, ``set``,
            ``tag``). Rows the owner left as "just a folder" or undecided are
            not sent at all - there is nothing here for them to do.
        match_id: The existing entity to attach to, when the owner accepted a
            `name_match` (or picked one from `candidates` themselves) rather
            than starting a new one. ``None`` means "create one named after
            this folder." Meaningless for ``tag``: a tag is a string on a
            picture, not a row with an id of its own.
    """

    relative_path: str
    kind: str
    match_id: Optional[int] = None

    def as_dict(self) -> dict:
        """The wire form `parse_assignments` reads back, for the durable record."""
        return {
            "relative_path": self.relative_path,
            "kind": self.kind,
            "match_id": self.match_id,
        }


@dataclass
class CommitResult:
    #: ``None`` for a `local_import` commit - there is no reference folder;
    #: the pictures are managed ones, indexed directly under `image_root`.
    reference_folder_id: Optional[int] = None
    pictures_indexed: int = 0
    projects_created: int = 0
    projects_matched: int = 0
    people_created: int = 0
    people_matched: int = 0
    sets_created: int = 0
    sets_matched: int = 0
    tags_created: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def parse_assignments(raw: list) -> list[Assignment]:
    """Validate the wire form of ``assignments`` into `Assignment` rows.

    Raises:
        CommitError: A row is malformed, or names a kind the read never
            proposes and the layout would not accept - "folder" included,
            since a row with nothing to do is simply absent from the list.
    """
    parsed: list[Assignment] = []
    seen: set[str] = set()
    for index, row in enumerate(raw or []):
        if not isinstance(row, dict):
            raise CommitError(f"assignments[{index}] must be an object")
        relative_path = row.get("relative_path")
        kind = row.get("kind")
        if not isinstance(relative_path, str):
            raise CommitError(f"assignments[{index}].relative_path must be a string")
        # Normalised exactly as the read's own `rel_path` is built: POSIX
        # separators, no leading/trailing slash, "" for the root.
        relative_path = relative_path.strip("/")
        if relative_path in seen:
            raise CommitError(f"assignments[{index}] repeats folder {relative_path!r}")
        if kind not in _ACCEPTED_KINDS:
            raise CommitError(
                f"assignments[{index}].kind must be one of "
                f"{sorted(_ACCEPTED_KINDS)}, got {kind!r}"
            )
        match_id = row.get("match_id")
        if match_id is not None:
            try:
                match_id = int(match_id)
            except (TypeError, ValueError):
                raise CommitError(
                    f"assignments[{index}].match_id must be an integer"
                ) from None
        seen.add(relative_path)
        parsed.append(Assignment(relative_path, kind, match_id))
    return parsed


def record_pending_commit(
    server,
    *,
    task_id: str,
    root_path: str,
    mode: str,
    label: Optional[str],
    expected_pictures: int,
    assignments: list[Assignment],
) -> None:
    """Write the accepted mapping down before the commit thread starts.

    Before, deliberately. A record written afterwards would not exist for the
    window this whole mechanism is about - the crash that lands between "the
    owner pressed the button" and "the pictures are organised".
    """

    def write(session: Session) -> None:
        session.add(
            FolderMappingCommit(
                task_id=task_id,
                root_path=root_path,
                mode=mode,
                label=label,
                expected_pictures=expected_pictures,
                assignments=json.dumps([a.as_dict() for a in assignments]),
                stage="registering",
                state=STATE_PENDING,
            )
        )
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def pending_commit(server) -> Optional[dict]:
    """The one unfinished commit for this library, or None.

    Returns the recorded arguments in the shape `_run_commit` takes, with
    ``assignments`` already parsed back into `Assignment` rows.
    """

    def read(session: Session) -> Optional[dict]:
        row = session.exec(
            select(FolderMappingCommit)
            .where(FolderMappingCommit.state == STATE_PENDING)
            .order_by(FolderMappingCommit.id.desc())
        ).first()
        if row is None:
            return None
        return {
            "task_id": row.task_id,
            "root_path": row.root_path,
            "mode": row.mode,
            "label": row.label,
            "expected_pictures": row.expected_pictures,
            "assignments": row.assignments,
            "stage": row.stage,
        }

    record = server.vault.db.run_immediate_read_task(read)
    if record is None:
        return None
    try:
        record["assignments"] = parse_assignments(json.loads(record["assignments"]))
    except (ValueError, CommitError) as exc:
        # Unreadable is not resumable, and a start-up that raises here would
        # be a library that cannot open at all. Say so and leave the row for
        # a person; the pictures already indexed are unaffected.
        logger.error(
            "Cannot resume the folder-mapping commit %s: its recorded "
            "assignments are unreadable (%s). Nothing was changed.",
            record["task_id"],
            exc,
        )
        return None
    return record


def settle_pending_commit(server, task_id: str, state: str) -> None:
    """Mark a recorded commit finished, abandoned or deferred, out of band.

    The success path does NOT come through here: it settles inside the
    assigning transaction (`_settle_in_session`) so that a crash between the
    two can never leave a committed mapping still marked pending, which would
    make the next start-up apply it a second time and duplicate every entity
    it created.
    """

    def write(session: Session) -> None:
        _settle_in_session(session, task_id, state)
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _settle_in_session(session: Session, task_id: str, state: str) -> None:
    """Settle the record on an open session, without committing it."""
    row = session.exec(
        select(FolderMappingCommit).where(FolderMappingCommit.task_id == task_id)
    ).first()
    if row is None:
        return
    row.state = state
    row.updated_at = datetime.now(timezone.utc)
    session.add(row)


def record_commit_stage(server, task_id: str, stage: str) -> None:
    """Note which phase a commit reached, for the record's own readability."""

    def write(session: Session) -> None:
        row = session.exec(
            select(FolderMappingCommit).where(FolderMappingCommit.task_id == task_id)
        ).first()
        if row is None or row.state != STATE_PENDING:
            return
        row.stage = stage
        row.updated_at = datetime.now(timezone.utc)
        session.add(row)
        session.commit()

    server.vault.db.run_task(write, priority=DBPriority.IMMEDIATE)


def _ancestors(relative_path: str) -> list[str]:
    """Nearest-first ancestor chain of *relative_path*, root last as ``""``."""
    if not relative_path:
        return [""]
    parts = relative_path.split("/")
    return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)] + [""]


def _resolve_folder(
    folder_relative_path: str, by_path: dict[str, Assignment]
) -> tuple[
    Optional[Assignment], Optional[Assignment], Optional[Assignment], list[Assignment]
]:
    """Return the (project, person, set) ancestor and every tag ancestor.

    The nearest accepted ancestor of each exclusive kind wins - a folder is
    filed under the *closest* Project or Person or Set above it, mirroring
    ``library_layout``'s first-match-wins segments. Tags are not exclusive:
    every accepted Tag ancestor along the path applies, because a picture can
    carry more than one label at once.
    """
    project = person = set_ = None
    tags: list[Assignment] = []
    for ancestor in _ancestors(folder_relative_path):
        assignment = by_path.get(ancestor)
        if assignment is None:
            continue
        if assignment.kind == Facet.TAG.value:
            tags.append(assignment)
        elif assignment.kind == Facet.PROJECT.value and project is None:
            project = assignment
        elif assignment.kind == Facet.PERSON.value and person is None:
            person = assignment
        elif assignment.kind == Facet.SET.value and set_ is None:
            set_ = assignment
    return project, person, set_, tags


def register_reference_folder(
    server, root_path: str, *, label: Optional[str] = None
) -> ReferenceFolder:
    """Register *root_path* for in-place indexing, or return it if it already is.

    Idempotent by path so a commit resumed after "Cancel and organise later"
    (or a retry of a stalled one) does not fight the row it made last time.
    Mirrors ``routes.reference_folders.create_reference_folder``'s essential
    shape; kept separate rather than sharing that closure because the two
    entry points validate different things upstream (that route re-derives
    accessibility from a caller-supplied path with its own conflict checks
    against every other registered folder; this one starts from a path a
    settled folder-structure read already walked).
    """
    root_path = os.path.normpath(root_path)
    error = validate_reference_folder_path(root_path)
    if error:
        raise CommitError(error)

    def fetch_or_create(session: Session) -> ReferenceFolder:
        existing = session.exec(
            select(ReferenceFolder).where(ReferenceFolder.folder == root_path)
        ).first()
        if existing is not None:
            if existing.last_scanned is not None:
                # A row with a completed scan pass is either an unrelated
                # reference folder the owner already had, or an EARLIER
                # commit of this same path (a fresh read run again over a
                # folder that was already organised once) - either way,
                # reusing it here without re-scanning would silently apply
                # this mapping to whatever pictures happen to be indexed
                # already, not to what the read the owner just accepted
                # actually found. Refuse cleanly rather than under-apply.
                raise CommitError(
                    f"{root_path} is already a reference folder. Remove it "
                    "first, or edit its mapping from the sidebar instead of "
                    "committing this read."
                )
            # last_scanned is None: registered but its first scan has not
            # completed yet - a retry of a commit that crashed after
            # registering but before the scan finished. Safe to keep waiting
            # on the same row rather than erroring, since nothing has been
            # indexed under it that this wait could miss.
            return existing
        access_error = validate_reference_folder_accessible(root_path)
        status = (
            ReferenceFolderStatus.ACTIVE
            if access_error is None
            else ReferenceFolderStatus.MOUNT_ERROR
        )
        rf = ReferenceFolder(
            folder=root_path,
            label=label or os.path.basename(root_path) or root_path,
            status=status,
            pending_reimport=True,
        )
        session.add(rf)
        session.commit()
        session.refresh(rf)
        return rf

    rf = server.vault.db.run_task(fetch_or_create, priority=DBPriority.IMMEDIATE)
    if rf.status == ReferenceFolderStatus.ACTIVE:
        server.vault.watch_reference_folder(rf.id, rf.folder)
    return rf


#: Top-level folders under a library's own root that hold PixlStash's files,
#: not the owner's pictures: the snapshot tree, and the ``tmp/`` cache the
#: set and character thumbnail routes write into. Every walk of a library's
#: root skips these; the dot-folder rule covers the rest
#: (``.pixlstash-thumbnails``, the older ``.ref_thumbs``, ``.staging``). Found
#: the hard way: an import of a library's
#: own root indexed 24 set and face thumbnails as pictures.
LIBRARY_OWN_FOLDERS = ("snapshots", "tmp")


def library_own_folders(image_root: str) -> set[str]:
    """Realpaths of the folders under *image_root* that no walk may index."""
    return {
        os.path.realpath(os.path.join(image_root, name)) for name in LIBRARY_OWN_FOLDERS
    }


def validate_local_import_root(server, root_path: str) -> None:
    """Refuse a `local_import` commit whose root is not the library's own tree.

    `local_import` turns pictures already on disk into ordinary MANAGED
    pictures - the mirror image of `register_reference_folder`, which
    `routes.reference_folders._validate_reference_folder_conflicts` already
    refuses for exactly this path (a reference folder may never equal or
    contain `image_root`). This is the same rule read the other way: a folder
    outside `image_root` must never be walked by `local_import`, only ever
    registered as a reference folder.

    Raises:
        CommitError: *root_path* is not `image_root` itself or a folder
            inside it.
    """
    image_root = os.path.normpath(getattr(server.vault, "image_root", "") or "")
    root_path = os.path.normpath(root_path)
    if not image_root or not path_is_within(root_path, image_root):
        raise CommitError(
            f"{root_path} is not this library's own folder ({image_root or 'unset'}) "
            "or inside it - local_import only applies to pictures the library "
            "already owns."
        )


def _build_managed_picture(
    abs_path: str, relative_path: str, image_root: str
) -> Picture:
    """Build a managed Picture for a file already sitting under *image_root*.

    Mirrors ``ReferenceFolderScanTask._build_picture`` - same hash, metadata
    and thumbnail steps - with the two differences that matter for a MANAGED
    picture: ``file_path`` is stored RELATIVE (to *image_root*) rather than
    absolute, and no ``reference_folder_id`` is set. No bytes are written or
    moved for the picture itself; only the thumbnail is generated, exactly as
    the reference-folder path does - the source file already lives where it
    is going to stay.
    """
    pixel_sha = ImageUtils.calculate_hash_from_file_path(abs_path)
    with open(abs_path, "rb") as fh:
        image_bytes = fh.read()

    created_at = ImageUtils.extract_created_at_from_metadata(
        image_bytes, fallback_file_path=abs_path
    )

    width = height = None
    img_format = None
    thumbnail_bytes = None
    thumb_cols: dict = {}
    is_video = VideoUtils.is_video_file(abs_path)
    # A video is not PIL's to open; MissingThumbnailFinder renders its frame
    # later. Trying anyway logged a WARNING per clip that read as a failure.
    if not is_video:
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img_format = img.format or "PNG"
                width, height = img.size
                rendered = ImageUtils.render_thumbnail(img)
                if rendered is not None:
                    thumbnail_bytes, bmp_w, bmp_h, crop = rendered
                    thumb_cols = {
                        "thumbnail_width": bmp_w,
                        "thumbnail_height": bmp_h,
                        "square_crop_x": crop["x"],
                        "square_crop_y": crop["y"],
                        "square_crop_side": crop["side"],
                    }
        except Exception as exc:
            # Not fatal: a corrupt image just means no thumbnail yet. Same
            # swallow reference_folder_scan_task makes - the Picture row is
            # still built and MissingThumbnailFinder can retry later.
            logger.warning(
                "Local import: could not decode %s for a thumbnail (%s: %s); "
                "the picture is indexed without one for now.",
                abs_path,
                type(exc).__name__,
                exc,
            )

    if thumbnail_bytes:
        ImageUtils.write_thumbnail_bytes(image_root, relative_path, thumbnail_bytes)

    pic = Picture(
        file_path=relative_path,
        pixel_sha=pixel_sha,
        format=img_format,
        width=width,
        height=height,
        size_bytes=os.path.getsize(abs_path),
        imported_at=datetime.now(timezone.utc),
        original_file_name=os.path.basename(abs_path),
        is_video=is_video,
        **thumb_cols,
    )
    if created_at:
        pic.created_at = created_at
    return pic


def local_import_pictures(
    server,
    root_path: str,
    *,
    expected_pictures: int,
    on_progress=None,
    should_stop=None,
) -> list[int]:
    """Import every supported file under *root_path* as a managed Picture.

    The `local_import` counterpart to `register_reference_folder` +
    `ReferenceFolderScanTask`'s scan pass: there is no already-shipped task
    for "index my own `image_root` in place", so this module does that one
    filesystem read itself.

    Idempotent by `file_path`, the same spirit as `register_reference_folder`
    checking for a row that already exists before creating one: a file
    already indexed (an overlapping earlier `local_import`, or an ordinary
    import that landed here independently) is reused by id, never
    reimported as a second row.

    Args:
        expected_pictures: The read's own count, shown as the progress total.
        on_progress: ``(processed, total) -> None``, called as files are
            resolved - both the already-indexed ones (counted immediately)
            and the newly-built ones (counted as each chunk commits).
        should_stop: ``() -> str | None``, checked at each chunk boundary. A
            returned state aborts the walk by raising `CommitStopped`.
            Checked *between* chunks and never inside one, so a stop can never
            tear a half-written chunk: every picture already inserted is
            complete and stays indexed.

    Returns:
        Every matching file's Picture id, existing and newly-created alike.

    Raises:
        CommitStopped: The owner stopped it. Whatever was indexed remains.
    """
    image_root = os.path.normpath(server.vault.image_root)

    # Prune dot-folders exactly as the Phase 2 read does (`folder_structure_
    # service.py`): a vault's own caches - `.pixlstash-thumbnails/`, the
    # older `.ref_thumbs/` - sit *inside* image_root, and root_path is
    # commonly image_root itself. Without this prune, local_import would
    # re-import every thumbnail in there as if it were a picture of its own
    # (`.webp` is a supported extension).
    own = library_own_folders(image_root)
    file_paths: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [
            name
            for name in dirnames
            if not name.startswith(".")
            and os.path.realpath(os.path.join(dirpath, name)) not in own
        ]
        for name in filenames:
            if not name.startswith(".") and is_supported_media_file(name):
                file_paths.append(os.path.join(dirpath, name))
    rel_by_abs = {
        path: os.path.relpath(path, image_root).replace(os.sep, "/")
        for path in file_paths
    }
    total = expected_pictures or len(file_paths)

    def load_existing(session: Session) -> dict[str, int]:
        rows = session.exec(
            select(Picture.file_path, Picture.id).where(
                Picture.file_path.in_(rel_by_abs.values())
            )
        ).all()
        return {rel: pid for rel, pid in rows}

    existing_by_rel = server.vault.db.run_immediate_read_task(load_existing)

    picture_ids: list[int] = list(existing_by_rel.values())
    to_build = [path for path in file_paths if rel_by_abs[path] not in existing_by_rel]
    processed = len(picture_ids)
    if on_progress is not None:
        on_progress(processed, total)

    def _build(abs_path: str) -> Optional[Picture]:
        try:
            return _build_managed_picture(abs_path, rel_by_abs[abs_path], image_root)
        except Exception as exc:
            logger.warning(
                "Local import: failed to build picture for %s: %s", abs_path, exc
            )
            return None

    pass_started = time.monotonic()
    build_s = 0.0
    insert_s = 0.0
    for start in range(0, len(to_build), _BUILD_CHUNK_SIZE):
        stopped = should_stop() if should_stop is not None else None
        if stopped:
            logger.info(
                "Local import stopped by the owner (%s) after %d of %d picture(s); "
                "what is indexed stays indexed",
                stopped,
                processed,
                total,
            )
            raise CommitStopped(stopped)
        chunk = to_build[start : start + _BUILD_CHUNK_SIZE]
        max_workers = min(_MAX_BUILD_WORKERS, max(1, len(chunk)))
        chunk_started = time.monotonic()
        if max_workers <= 1:
            built = [pic for pic in (_build(path) for path in chunk) if pic is not None]
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
                built = [pic for pic in ex.map(_build, chunk) if pic is not None]
        build_s += time.monotonic() - chunk_started

        def insert(session: Session, built=built) -> list[int]:
            session.add_all(built)
            session.commit()
            for pic in built:
                session.refresh(pic)
            session.add_all(
                Tag(picture_id=pic.id, tag=TAG_PENDING_SENTINEL) for pic in built
            )
            session.commit()
            return [pic.id for pic in built]

        insert_started = time.monotonic()
        picture_ids.extend(
            server.vault.db.run_task(insert, priority=DBPriority.IMMEDIATE)
        )
        insert_s += time.monotonic() - insert_started
        # The rows are visible to every finder now, but the WorkPlanner only
        # sweeps on a wake or when its backoff (up to MAX_INTERVAL_S) expires,
        # and an idle library has it parked at the maximum. Poke it per chunk
        # so faces, quality and the rest start on the first 128 pictures
        # rather than on the commit's end-of-run notify.
        server.vault.wake()
        processed += len(chunk)
        if on_progress is not None:
            on_progress(processed, total)

    # The split that says where an import's time went: `build_s` is decode +
    # thumbnail + hash on the build pool (CPU and disk, contended by the
    # workers' preload pools), `insert_s` is the wait for the single DB writer
    # (contended by the workers' own write transactions). Same spirit as the
    # planner's [PIPELINE_PASS] line, and meant to be read next to it.
    wall_s = time.monotonic() - pass_started
    built_count = len(to_build)
    logger.info(
        "[IMPORT_PASS] pictures=%d reused=%d wall_s=%.1f img_per_s=%.1f "
        "build_s=%.1f insert_s=%.1f",
        built_count,
        len(file_paths) - built_count,
        wall_s,
        built_count / wall_s if wall_s > 0 else 0.0,
        build_s,
        insert_s,
    )

    return picture_ids


def wait_for_first_scan(
    server,
    reference_folder_id: int,
    *,
    expected_pictures: int,
    on_progress=None,
    should_stop=None,
    timeout_s: float = INDEX_TIMEOUT_S,
) -> None:
    """Block until the reference folder's first scan pass has completed.

    Args:
        expected_pictures: The read's own count, shown as the progress total
            while the scan is still running.
        on_progress: ``(processed, total) -> None``, called as pictures land.

    Raises:
        CommitError: The scan did not finish inside *timeout_s*, or the
            reference folder failed to mount.
        CommitStopped: The owner stopped waiting. Only the *wait* stops - the
            scan is an ordinary background task and carries on to the end,
            which is exactly what "organise later" is asking for.
    """

    def read_state(session: Session):
        rf = session.get(ReferenceFolder, reference_folder_id)
        if rf is None:
            return None, 0
        count = session.exec(
            select(Picture.id).where(Picture.reference_folder_id == reference_folder_id)
        ).all()
        return rf, len(count)

    deadline = time.monotonic() + timeout_s
    while True:
        stopped = should_stop() if should_stop is not None else None
        if stopped:
            logger.info(
                "Waiting on the first scan of reference folder %d stopped by the "
                "owner (%s); the scan itself continues",
                reference_folder_id,
                stopped,
            )
            raise CommitStopped(stopped)
        rf, indexed = server.vault.db.run_immediate_read_task(read_state)
        if rf is None:
            raise CommitError("The reference folder disappeared mid-scan.")
        if rf.status == ReferenceFolderStatus.MOUNT_ERROR:
            raise CommitError(f"{rf.folder} could not be mounted for scanning.")
        if on_progress is not None:
            on_progress(indexed, max(expected_pictures, indexed))
        if rf.last_scanned is not None:
            return
        if time.monotonic() >= deadline:
            raise CommitError(
                f"The initial scan of {rf.folder} did not finish within "
                f"{int(timeout_s)}s."
            )
        time.sleep(_POLL_INTERVAL_S)


def _link_pictures(
    session: Session,
    pictures: list[Picture],
    assignments: list[Assignment],
    root_path: str,
    image_root: str,
    result: CommitResult,
) -> None:
    """Create the accepted entities and link *pictures* to them.

    Shared by `apply_mapping` (a reference folder's indexed set, absolute
    `file_path`) and `apply_local_mapping` (`local_import_pictures`'s managed
    pictures, `file_path` relative to *image_root*) - the one place the
    Facet-handling (create-or-match project/person/set, nearest-ancestor-wins,
    every Tag ancestor) is written, so the two commit modes cannot answer "who
    does this folder belong to" differently.
    `ImageUtils.resolve_picture_path` normalises either `file_path` shape to
    an absolute path before the folder grouping below, which is the only
    place the two callers' pictures actually differ.
    """
    by_path = {a.relative_path: a for a in assignments}

    # `apply_local_mapping` is handed pictures that may already have been
    # indexed before the wizard ran, so some can sit in a locked set. Skip
    # those rather than failing the whole commit - the rest of the library
    # still gets filed. (`apply_mapping`'s pictures are new, so it is a no-op
    # there.)
    frozen = locked_picture_ids(session, [p.id for p in pictures if p.id is not None])
    if frozen:
        logger.info(
            "Folder-mapping commit skipping %d picture(s) frozen by a locked set",
            len(frozen),
        )
        pictures = [p for p in pictures if p.id not in frozen]

    # Group by containing folder first: every picture in the same folder
    # resolves to the same (project, person, set, tags), so the ancestor
    # walk runs once per folder rather than once per picture.
    by_folder: dict[str, list[Picture]] = {}
    for pic in pictures:
        absolute = ImageUtils.resolve_picture_path(image_root, pic.file_path) or ""
        folder_abs = os.path.dirname(absolute)
        rel = os.path.relpath(folder_abs, root_path).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        by_folder.setdefault(rel, []).append(pic)

    def _free_name(model, name: str, project_id: Optional[int] = None) -> str:
        """A name no existing row already holds, in the scope its index covers.

        ``Project.name`` is unique case-insensitively across the library;
        ``Character`` and ``PictureSet`` are unique per ``(project_id,
        lower(name))``. Creating a second row with a taken name raises
        ``IntegrityError``, which is neither ``CommitError`` nor
        ``CommitStopped`` - so the route leaves the durable record *pending*,
        every start-up resumes it, re-walks and re-hashes the whole root, and
        fails the same way for ever.

        The owner asked for a new one, so the answer is a new one under a free
        name rather than silently reusing the row they declined to match. The
        suffix is the shape migrations 0010-0012 already use for exactly this
        collision.
        """
        taken = select(model).where(func.lower(model.name) == name.lower())
        if project_id is not None and model is not Project:
            taken = taken.where(model.project_id == project_id)
        if session.exec(taken).first() is None:
            return name
        counter = 2
        while True:
            candidate = f"{name} ({counter})"
            probe = select(model).where(func.lower(model.name) == candidate.lower())
            if project_id is not None and model is not Project:
                probe = probe.where(model.project_id == project_id)
            if session.exec(probe).first() is None:
                logger.info(
                    "Folder commit: %s %r already exists, creating %r instead.",
                    model.__name__,
                    name,
                    candidate,
                )
                return candidate
            counter += 1

    project_cache: dict[tuple[str, str], int] = {}
    character_cache: dict[tuple[str, str], int] = {}
    set_cache: dict[tuple[str, str], int] = {}

    def get_project(assignment: Assignment) -> int:
        name = os.path.basename(assignment.relative_path) or assignment.relative_path
        key = (
            ("id", str(assignment.match_id)) if assignment.match_id else ("name", name)
        )
        if key in project_cache:
            return project_cache[key]
        if assignment.match_id:
            project = session.get(Project, assignment.match_id)
            if project is None:
                raise CommitError(f"Project {assignment.match_id} not found")
            result.projects_matched += 1
        else:
            project = Project(name=_free_name(Project, name))
            session.add(project)
            session.flush()
            result.projects_created += 1
        project_cache[key] = project.id
        return project.id

    def get_character(assignment: Assignment, project_id: Optional[int]) -> int:
        name = os.path.basename(assignment.relative_path) or assignment.relative_path
        key = (
            ("id", str(assignment.match_id)) if assignment.match_id else ("name", name)
        )
        if key in character_cache:
            return character_cache[key]
        if assignment.match_id:
            character = session.get(Character, assignment.match_id)
            if character is None:
                raise CommitError(f"Person {assignment.match_id} not found")
            result.people_matched += 1
        else:
            character = Character(name=_free_name(Character, name, project_id))
            session.add(character)
            session.flush()
            if project_id is not None:
                set_character_projects(session, character, [project_id])
            result.people_created += 1
        character_cache[key] = character.id
        return character.id

    def get_set(assignment: Assignment, project_id: Optional[int]) -> int:
        name = os.path.basename(assignment.relative_path) or assignment.relative_path
        key = (
            ("id", str(assignment.match_id)) if assignment.match_id else ("name", name)
        )
        if key in set_cache:
            return set_cache[key]
        if assignment.match_id:
            picture_set = session.get(PictureSet, assignment.match_id)
            if picture_set is None:
                raise CommitError(f"Set {assignment.match_id} not found")
            result.sets_matched += 1
        else:
            picture_set = PictureSet(name=_free_name(PictureSet, name, project_id))
            session.add(picture_set)
            session.flush()
            if project_id is not None:
                set_picture_set_projects(session, picture_set, [project_id])
            result.sets_created += 1
        set_cache[key] = picture_set.id
        return picture_set.id

    # A second commit over a folder that already carries its assignments would
    # re-insert rows whose keys are already taken - PictureProjectMember and
    # PictureSetMember have composite primary keys and Tag a
    # (picture_id, tag) unique constraint - and that IntegrityError wedges the
    # durable record `pending` exactly as a taken name does: every start-up
    # resumes it, re-walks the whole root, and fails identically. Load what is
    # already there once and skip it.
    linked_ids = [pic.id for pic in pictures if pic.id is not None]
    existing_projects: set[tuple[int, int]] = set()
    existing_sets: set[tuple[int, int]] = set()
    existing_tags: set[tuple[int, str]] = set()
    for chunk in chunked(linked_ids):
        batch = list(chunk)
        existing_projects.update(
            (row.picture_id, row.project_id)
            for row in session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id.in_(batch)
                )
            ).all()
        )
        existing_sets.update(
            (row.set_id, row.picture_id)
            for row in session.exec(
                select(PictureSetMember).where(PictureSetMember.picture_id.in_(batch))
            ).all()
        )
        existing_tags.update(
            (row.picture_id, row.tag)
            for row in session.exec(select(Tag).where(Tag.picture_id.in_(batch))).all()
        )

    tag_created: set[str] = set()

    for folder_relpath, folder_pictures in by_folder.items():
        project_a, person_a, set_a, tag_as = _resolve_folder(folder_relpath, by_path)
        project_id = get_project(project_a) if project_a else None
        character_id = get_character(person_a, project_id) if person_a else None
        set_id = get_set(set_a, project_id) if set_a else None
        tag_names = []
        for tag_a in tag_as:
            tag_name = os.path.basename(tag_a.relative_path) or tag_a.relative_path
            tag_names.append(tag_name)
            if tag_name not in tag_created:
                tag_created.add(tag_name)
                result.tags_created += 1

        for pic in folder_pictures:
            if project_id is not None:
                pic.project_id = project_id
                if (pic.id, project_id) not in existing_projects:
                    existing_projects.add((pic.id, project_id))
                    session.add(
                        PictureProjectMember(picture_id=pic.id, project_id=project_id)
                    )
            if character_id is not None:
                # Deferred, exactly as the character-assignment endpoint
                # defers when face extraction has not run yet for a
                # picture: FaceExtractionTask clears this and assigns the
                # best face once it has. A folder-derived person is not a
                # detection, so there is no face row to attach to yet.
                pic.pending_character_id = character_id
            if set_id is not None and (set_id, pic.id) not in existing_sets:
                existing_sets.add((set_id, pic.id))
                session.add(PictureSetMember(set_id=set_id, picture_id=pic.id))
            for tag_name in tag_names:
                # The owner accepted this folder as a tag, so it is a human POS
                # and belongs in the label ledger. Without it the tag does not
                # survive: these pictures still carry TAG_PENDING_SENTINEL, and
                # when TagTask reaches one it deletes every Tag row and rewrites
                # the picture from `model_tags | human_POS - human_NEG`. The
                # general upsert, not `record_human_label_if_relevant`: a folder
                # tag is usually outside the tagger's anomaly vocabulary, which
                # is exactly the case that variant declines to record.
                record_human_label(session, pic.id, tag_name, POS)
                if (pic.id, tag_name) in existing_tags:
                    continue
                existing_tags.add((pic.id, tag_name))
                session.add(Tag(picture_id=pic.id, tag=tag_name))
            session.add(pic)


def apply_mapping(
    server,
    reference_folder_id: int,
    assignments: list[Assignment],
    root_path: str,
    task_id: Optional[str] = None,
) -> CommitResult:
    """Create the accepted entities and link every indexed picture to them.

    Runs once the reference folder's first scan has completed, so every
    picture it will ever touch already has a `Picture.file_path`. Nothing here
    reads the filesystem again - folder membership is derived purely from that
    already-recorded path, which is what makes this step a pile of database
    writes and not a second walk.

    Args:
        task_id: The durable record to settle, in this same transaction. That
            is what makes the whole commit exactly-once: nothing here creates
            an entity twice, because a crash either rolls the entities back
            *and* leaves the record pending, or commits both together.
    """
    image_root = os.path.normpath(server.vault.image_root)

    linked_ids: list[int] = []

    def commit(session: Session) -> CommitResult:
        pictures = session.exec(
            select(Picture).where(Picture.reference_folder_id == reference_folder_id)
        ).all()
        result = CommitResult(
            reference_folder_id=reference_folder_id, pictures_indexed=len(pictures)
        )
        _link_pictures(session, pictures, assignments, root_path, image_root, result)
        linked_ids.extend(int(pic.id) for pic in pictures)
        if task_id:
            _settle_in_session(session, task_id, STATE_DONE)
        session.commit()
        return result

    result = server.vault.db.run_task(commit, priority=DBPriority.IMMEDIATE)
    resolve_pending_people_with_faces(server, linked_ids)
    return result


def apply_local_mapping(
    server,
    picture_ids: list[int],
    assignments: list[Assignment],
    root_path: str,
    task_id: Optional[str] = None,
) -> CommitResult:
    """The `local_import` counterpart to `apply_mapping`.

    Links the pictures `local_import_pictures` just imported (or found
    already indexed) to the accepted projects, people, sets and tags - same
    entity-resolution code as `apply_mapping` via `_link_pictures`, sourced
    from an explicit id list rather than a `reference_folder_id` foreign key,
    since a managed picture carries no such column.
    """
    image_root = os.path.normpath(server.vault.image_root)

    def commit(session: Session) -> CommitResult:
        pictures = (
            list(session.exec(select(Picture).where(Picture.id.in_(picture_ids))).all())
            if picture_ids
            else []
        )
        result = CommitResult(pictures_indexed=len(pictures))
        _link_pictures(session, pictures, assignments, root_path, image_root, result)
        if task_id:
            _settle_in_session(session, task_id, STATE_DONE)
        session.commit()
        return result

    result = server.vault.db.run_task(commit, priority=DBPriority.IMMEDIATE)
    resolve_pending_people_with_faces(server, picture_ids)
    return result


def resolve_pending_people_with_faces(server, picture_ids: list[int]) -> int:
    """Attach the folder-derived person to pictures whose faces already exist.

    `_link_pictures` defers a person assignment into
    `Picture.pending_character_id`, which `FaceExtractionTask`'s completion
    hook resolves - but only for the pictures it just extracted. Since the
    workers start while the import is still indexing, extraction routinely
    finishes BEFORE the mapping runs, and a picture in that order was never
    revisited: pending set, faces present, nobody to join them. This runs the
    same resolver now for exactly those pictures. Pictures whose extraction
    has not run are left alone on purpose: the resolver treats "no face rows"
    as "extraction found nothing" and would discard the pending id.

    Returns:
        How many pictures were handed to the resolver.
    """
    if not picture_ids:
        return 0

    def fetch(session: Session) -> list[int]:
        found: list[int] = []
        # Chunked: SQLite bounds the number of bound parameters per statement.
        for start in range(0, len(picture_ids), 500):
            chunk = picture_ids[start : start + 500]
            extracted = select(Face.id).where(Face.picture_id == Picture.id).exists()
            found.extend(
                session.exec(
                    select(Picture.id).where(
                        Picture.id.in_(chunk),
                        Picture.pending_character_id.is_not(None),
                        extracted,
                    )
                ).all()
            )
        return found

    ready = server.vault.db.run_task(fetch)
    if ready:
        logger.info(
            "Folder mapping: %d picture(s) had faces before their person was "
            "assigned; assigning now",
            len(ready),
        )
        server.vault._process_pending_character_assignments(ready)
    return len(ready)
