"""Commit an accepted folder-structure mapping. v1.11 Phase 3.

``docs/plans/v1.11.0-existing-library.md`` §4 Phase 3; wire contract
``docs/integration_architecture.md`` §22. The read (Phase 2,
``folder_structure_service.py``) only ever proposes; this module is the one
place anything from the mapping screen is written.

**No file is moved, renamed or copied, at any stage.** The scanned root is
registered as an ordinary :class:`~pixlstash.db_models.reference_folder.ReferenceFolder`
— indexed in place by the existing, already-shipped
:class:`~pixlstash.tasks.reference_folder_scan_task.ReferenceFolderScanTask`,
which is the only filesystem-reading step here and writes nothing but the
vault database and its own thumbnail cache. Once that scan's first pass has
run, every newly-indexed picture is linked to whichever accepted ancestor
folder names it — a database row, never a filesystem write.

An assignment names a folder, not a picture: two folders that resolve to the
same kind and the same name (a project called ``Mira`` appearing twice in the
tree, or a name the owner matched to an existing entity) become the *same*
row, exactly as `library_layout.folder_name` treats two on-disk spellings that
collapse to one path component as the same folder.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.character import Character
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.picture_set import PictureSet, PictureSetMember
from pixlstash.db_models.project import Project
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.db_models.tag import Tag
from pixlstash.pixl_logging import get_logger
from pixlstash.services.project_membership_service import (
    set_character_projects,
    set_picture_set_projects,
)
from pixlstash.utils.library_layout import Facet
from pixlstash.utils.reference_folder_validator import (
    validate_reference_folder_accessible,
    validate_reference_folder_path,
)

logger = get_logger(__name__)

#: How long the commit waits for the reference folder's first scan pass
#: before giving up. The release plan measured 28,412 files well inside this;
#: a scan that is still not done past it means something is actually stuck,
#: and the commit should fail rather than hang the screen forever.
INDEX_TIMEOUT_S = 30 * 60.0
_POLL_INTERVAL_S = 0.25

#: The facets a folder can be accepted as, plus "tag" which is a `Facet` value
#: too — every accepted `kind` the mapping screen sends is one of these.
_ACCEPTED_KINDS = frozenset(f.value for f in Facet)


class CommitError(Exception):
    """A refusal the route turns into an HTTP error."""


@dataclass(frozen=True)
class Assignment:
    """One accepted folder from the mapping screen.

    Attributes:
        relative_path: POSIX-separated, relative to the scanned root — the
            same handle the read's folder rows carry. ``""`` addresses the
            root folder itself.
        kind: One of `Facet`'s values (``project``, ``person``, ``set``,
            ``tag``). Rows the owner left as "just a folder" or undecided are
            not sent at all — there is nothing here for them to do.
        match_id: The existing entity to attach to, when the owner accepted a
            `name_match` (or picked one from `candidates` themselves) rather
            than starting a new one. ``None`` means "create one named after
            this folder." Meaningless for ``tag``: a tag is a string on a
            picture, not a row with an id of its own.
    """

    relative_path: str
    kind: str
    match_id: Optional[int] = None


@dataclass
class CommitResult:
    reference_folder_id: int
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
            proposes and the layout would not accept — "folder" included,
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

    The nearest accepted ancestor of each exclusive kind wins — a folder is
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
                # folder that was already organised once) — either way,
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
            # completed yet — a retry of a commit that crashed after
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


def wait_for_first_scan(
    server,
    reference_folder_id: int,
    *,
    expected_pictures: int,
    on_progress=None,
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


def apply_mapping(
    server,
    reference_folder_id: int,
    assignments: list[Assignment],
    root_path: str,
) -> CommitResult:
    """Create the accepted entities and link every indexed picture to them.

    Runs once the reference folder's first scan has completed, so every
    picture it will ever touch already has a `Picture.file_path`. Nothing here
    reads the filesystem again — folder membership is derived purely from that
    already-recorded path, which is what makes this step a pile of database
    writes and not a second walk.
    """
    by_path = {a.relative_path: a for a in assignments}
    result = CommitResult(reference_folder_id=reference_folder_id)

    def commit(session: Session) -> CommitResult:
        pictures = session.exec(
            select(Picture).where(Picture.reference_folder_id == reference_folder_id)
        ).all()
        result.pictures_indexed = len(pictures)

        # Group by containing folder first: every picture in the same folder
        # resolves to the same (project, person, set, tags), so the ancestor
        # walk runs once per folder rather than once per picture.
        by_folder: dict[str, list[Picture]] = {}
        for pic in pictures:
            folder_abs = os.path.dirname(pic.file_path or "")
            rel = os.path.relpath(folder_abs, root_path).replace(os.sep, "/")
            rel = "" if rel == "." else rel
            by_folder.setdefault(rel, []).append(pic)

        project_cache: dict[tuple[str, str], int] = {}
        character_cache: dict[tuple[str, str], int] = {}
        set_cache: dict[tuple[str, str], int] = {}

        def get_project(assignment: Assignment) -> int:
            name = (
                os.path.basename(assignment.relative_path) or assignment.relative_path
            )
            key = (
                ("id", str(assignment.match_id))
                if assignment.match_id
                else ("name", name)
            )
            if key in project_cache:
                return project_cache[key]
            if assignment.match_id:
                project = session.get(Project, assignment.match_id)
                if project is None:
                    raise CommitError(f"Project {assignment.match_id} not found")
                result.projects_matched += 1
            else:
                project = Project(name=name)
                session.add(project)
                session.flush()
                result.projects_created += 1
            project_cache[key] = project.id
            return project.id

        def get_character(assignment: Assignment, project_id: Optional[int]) -> int:
            name = (
                os.path.basename(assignment.relative_path) or assignment.relative_path
            )
            key = (
                ("id", str(assignment.match_id))
                if assignment.match_id
                else ("name", name)
            )
            if key in character_cache:
                return character_cache[key]
            if assignment.match_id:
                character = session.get(Character, assignment.match_id)
                if character is None:
                    raise CommitError(f"Person {assignment.match_id} not found")
                result.people_matched += 1
            else:
                character = Character(name=name)
                session.add(character)
                session.flush()
                if project_id is not None:
                    set_character_projects(session, character, [project_id])
                result.people_created += 1
            character_cache[key] = character.id
            return character.id

        def get_set(assignment: Assignment, project_id: Optional[int]) -> int:
            name = (
                os.path.basename(assignment.relative_path) or assignment.relative_path
            )
            key = (
                ("id", str(assignment.match_id))
                if assignment.match_id
                else ("name", name)
            )
            if key in set_cache:
                return set_cache[key]
            if assignment.match_id:
                picture_set = session.get(PictureSet, assignment.match_id)
                if picture_set is None:
                    raise CommitError(f"Set {assignment.match_id} not found")
                result.sets_matched += 1
            else:
                picture_set = PictureSet(name=name)
                session.add(picture_set)
                session.flush()
                if project_id is not None:
                    set_picture_set_projects(session, picture_set, [project_id])
                result.sets_created += 1
            set_cache[key] = picture_set.id
            return picture_set.id

        tag_created: set[str] = set()

        for folder_relpath, folder_pictures in by_folder.items():
            project_a, person_a, set_a, tag_as = _resolve_folder(
                folder_relpath, by_path
            )
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
                if set_id is not None:
                    session.add(PictureSetMember(set_id=set_id, picture_id=pic.id))
                for tag_name in tag_names:
                    session.add(Tag(picture_id=pic.id, tag=tag_name))
                session.add(pic)

        session.commit()
        return result

    return server.vault.db.run_task(commit, priority=DBPriority.IMMEDIATE)
