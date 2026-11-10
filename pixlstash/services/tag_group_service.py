"""Tag groups: the library-wide tag-name -> group map, its undo, the default
seeding and the Danbooru tag-file import (issue #2469).

Every public writer function takes a pre-opened session (the ``*_in_session``
rule, docs/backend_architecture.md section 10.1) and runs on the DB writer, so the
change and its operation row commit together. The map is library vocabulary, not
a picture facet, so an undoable change records its own payload
(:func:`operation_log_service.record_payload_operation_in_session`). Each tag's
payload is ``{"group": id|null, "default": bool}``, so undo restores provenance as
well as the group; the post-restore hook also accepts the older bare ``id|null``
value. The route module imports this module, which registers the hook at start-up.

Provenance: ``is_default`` is True for a filing seeding made and False for one the
user made. A user move and an import both write False. Seeding never rewrites an
existing row.

The map answer carries only the filings of tags a picture carries; the rest of the
vocabulary is paged by :func:`list_tags_in_session`. An import's undo payload names
its tags compactly (see :func:`restore_tag_group_moves_in_session`).
"""

import csv
import io
import json
import os
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import delete, desc, func, null, union_all, update
from sqlmodel import Session, select

from pixlstash.db_models import TagGroup, TagGroupMember
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.db_models.operation import TARGET_TAG_GROUP
from pixlstash.db_models.tag import TAG_PENDING_SENTINEL, Tag, is_tag_sentinel
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service
from pixlstash.services.operation_log_service import (
    RESTORE_UNDO,
    record_payload_operation_in_session,
)
from pixlstash.services.tag_group_defaults import (
    ADULT_GROUP,
    CHARACTER,
    COPYRIGHT,
    DEFAULT_GROUP_NAMES,
    franchise_of,
    group_for,
    normalise_tag,
)
from pixlstash.utils.sql_chunking import chunked
from pixlstash.utils.xlsx_reader import XlsxError, iter_first_sheet_rows

logger = get_logger(__name__)

# One op type for every change to the map. An import reuses it rather than
# registering its own: undo runs one hook call per op_type, so a batch that mixes
# a move and an import of the same tag would otherwise restore them out of order.
OP_TYPE_MOVE = "tag_groups.move"
NAME_MAX_LENGTH = 64
MAX_ASSIGN_TAGS = 5000
UNGROUPED = "Ungrouped"

DEFAULT_TAGS_FILE = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "tag_groups"
    / "default_tag_groups.json"
)
IMPORT_MAX_BYTES = 64 * 1024 * 1024
IMPORT_CONFLICT_SAMPLE = 10
# An import may name at most this many categories that are not groups yet; a file
# with more is refused, so one upload cannot create an unbounded number of groups.
MAX_IMPORT_NEW_GROUPS = 50
# SQLite's INTEGER is 64-bit; a larger group id names no group.
_SQLITE_MAX_INT = 2**63 - 1


class TagGroupError(Exception):
    """A refused request. The route maps ``status_code`` onto the HTTP response."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _library_tags_stmt():
    """Every distinct tag some picture carries, pending-retag sentinels excluded."""
    return (
        select(Tag.tag)
        .distinct()
        .where(~Tag.tag.startswith(TAG_PENDING_SENTINEL, autoescape=True))
    )


def read_map_in_session(session: Session) -> dict[str, Any]:
    """The map as the browser reads it, not the whole vocabulary.

    The vocabulary can run to hundreds of thousands of names, so only the filings
    of tags a picture carries travel here (``tags``). ``ungrouped_count`` counts
    the library's tags with no filing; ``all_count`` the distinct known tags, filed
    or in the library. ``tag_count`` on each group counts every tag filed there.
    :func:`list_tags_in_session` pages through the rest.
    """
    groups = session.exec(
        select(TagGroup).order_by(TagGroup.position, TagGroup.id)
    ).all()
    tag_counts = dict(
        session.exec(
            select(TagGroupMember.group_id, func.count()).group_by(
                TagGroupMember.group_id
            )
        ).all()
    )
    library_filings = session.exec(
        select(TagGroupMember.tag, TagGroupMember.group_id).where(
            TagGroupMember.tag.in_(_library_tags_stmt())
        )
    ).all()
    library_total = int(
        session.exec(
            select(func.count()).select_from(_library_tags_stmt().subquery())
        ).one()
    )
    ungrouped = library_total - len(library_filings)
    return {
        "groups": [
            {
                "id": g.id,
                "name": g.name,
                "position": g.position,
                "tag_count": int(tag_counts.get(g.id, 0)),
            }
            for g in groups
        ],
        "tags": {tag: group_id for tag, group_id in library_filings},
        "ungrouped_count": ungrouped,
        "all_count": sum(tag_counts.values()) + ungrouped,
    }


def list_tag_groups(db) -> dict[str, Any]:
    """Read the map on the read queue (``GET /tag_groups``)."""
    return db.run_immediate_read_task(read_map_in_session)


def _picture_counts(*, within=None, unfiled_only: bool = False):
    """A subquery of (tag, pictures) for library tags, optionally narrowed.

    *within* limits it to the tags of a select; *unfiled_only* to tags no
    member row names. Pending-retag sentinels never count.
    """
    stmt = select(Tag.tag.label("tag"), func.count().label("pictures")).where(
        ~Tag.tag.startswith(TAG_PENDING_SENTINEL, autoescape=True)
    )
    if within is not None:
        stmt = stmt.where(Tag.tag.in_(within))
    if unfiled_only:
        stmt = stmt.where(~Tag.tag.in_(select(TagGroupMember.tag)))
    return stmt.group_by(Tag.tag).subquery()


def list_tags_in_session(
    session: Session,
    *,
    group: str,
    q: Optional[str],
    sort: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    """One page of tags for the Tags view (``GET /tag_groups/tags``).

    Args:
        session: A read session.
        group: A group id; ``"none"`` for library tags with no filing; ``"all"``
            for every filed tag and every library tag.
        q: Substring to match, folded for ASCII only (SQLite ``LIKE``; tags are
            stored in lower case by :func:`normalise_tag`), or ``None``.
        sort: ``"pictures"`` (most pictures first, then name) or ``"name"``.
        offset: Rows to skip.
        limit: Rows to return.

    Returns:
        ``{"total": matches, "tags": [{"tag", "count", "group_id"}, ...]}``, where
        ``count`` is the pictures in the library carrying the tag (0 when none) and
        ``group_id`` is ``None`` for an unfiled tag.

    Raises:
        TagGroupError: 400 for a malformed group; 404 for an unknown group id.
    """
    group_id: Optional[int] = None
    if group not in ("all", "none"):
        if not group.isascii() or not group.isdigit():
            raise TagGroupError(400, "group must be a group id, none or all")
        group_id = int(group)
        if group_id > _SQLITE_MAX_INT or session.get(TagGroup, group_id) is None:
            raise TagGroupError(404, f"No tag group with id {group}")

    branches = []
    if group != "none":
        if group_id is None:
            counts = _picture_counts()
        else:
            counts = _picture_counts(
                within=select(TagGroupMember.tag).where(
                    TagGroupMember.group_id == group_id
                )
            )
        filed = select(
            TagGroupMember.tag.label("tag"),
            TagGroupMember.group_id.label("group_id"),
            func.coalesce(counts.c.pictures, 0).label("pictures"),
        ).select_from(
            TagGroupMember.__table__.outerjoin(
                counts, counts.c.tag == TagGroupMember.tag
            )
        )
        if group_id is not None:
            filed = filed.where(TagGroupMember.group_id == group_id)
        branches.append(filed)
    if group in ("all", "none"):
        unfiled = _picture_counts(unfiled_only=True)
        branches.append(
            select(
                unfiled.c.tag.label("tag"),
                null().label("group_id"),
                unfiled.c.pictures.label("pictures"),
            )
        )
    source = (
        branches[0].subquery()
        if len(branches) == 1
        else union_all(*branches).subquery()
    )
    matches = select(source.c.tag, source.c.group_id, source.c.pictures).select_from(
        source
    )
    if q:
        matches = matches.where(source.c.tag.contains(q, autoescape=True))
    total = session.exec(select(func.count()).select_from(matches.subquery())).one()
    order = (
        (desc(source.c.pictures), source.c.tag)
        if sort == "pictures"
        else (source.c.tag,)
    )
    page = session.exec(matches.order_by(*order).offset(offset).limit(limit)).all()
    return {
        "total": int(total),
        "tags": [
            {"tag": row.tag, "count": int(row.pictures), "group_id": row.group_id}
            for row in page
        ],
    }


def list_tags(db, **params) -> dict[str, Any]:
    """Page through tags on the read queue; see :func:`list_tags_in_session`."""
    return db.run_immediate_read_task(list_tags_in_session, **params)


def _create_in_session(session: Session, name: str) -> TagGroup:
    cleaned = (name or "").strip()
    if not 1 <= len(cleaned) <= NAME_MAX_LENGTH:
        raise TagGroupError(
            400, f"Group name must be 1 to {NAME_MAX_LENGTH} characters"
        )
    # Compared in Python: SQLite's lower() is ASCII-only, casefold is not.
    wanted = cleaned.casefold()
    for existing in session.exec(select(TagGroup)).all():
        if existing.name.casefold() == wanted:
            raise TagGroupError(409, f"A group named “{existing.name}” already exists")
    highest = session.exec(select(func.max(TagGroup.position))).one()
    group = TagGroup(name=cleaned, position=0 if highest is None else int(highest) + 1)
    session.add(group)
    session.flush()
    return group


def create_group_in_session(session: Session, name: str) -> dict[str, Any]:
    """Create a group last in position and commit (``POST /tag_groups``).

    Returns the whole map plus the new ``group``.
    """
    try:
        group = _create_in_session(session, name)
        session.commit()
        result = read_map_in_session(session)
        result["group"] = {
            "id": group.id,
            "name": group.name,
            "position": group.position,
        }
        return result
    except BaseException:
        session.rollback()
        raise


def reorder_groups_in_session(session: Session, ids: list[int]) -> dict[str, Any]:
    """Set group positions to *ids*, which must be exactly the existing ids."""
    try:
        by_id = {g.id: g for g in session.exec(select(TagGroup)).all()}
        # Sorted lists, not sets: a duplicate id makes the lengths differ.
        if sorted(ids) != sorted(by_id):
            raise TagGroupError(400, "ids must name every existing group exactly once")
        for position, group_id in enumerate(ids):
            by_id[group_id].position = position
            session.add(by_id[group_id])
        session.commit()
        return read_map_in_session(session)
    except BaseException:
        session.rollback()
        raise


def _clean_tags(tags: list[str]) -> list[str]:
    stripped = ((raw or "").strip() for raw in tags or [])
    cleaned = list(dict.fromkeys(name for name in stripped if name))
    for name in cleaned:
        if is_tag_sentinel(name):
            raise TagGroupError(
                400, f"“{name}” is a pending-retag marker, not a tag you can file"
            )
    if len(cleaned) > MAX_ASSIGN_TAGS:
        raise TagGroupError(
            400, f"At most {MAX_ASSIGN_TAGS} tags can be filed in one request"
        )
    return cleaned


def _move_summary(count: int, tag_names: list[str], destination: str) -> str:
    if count == 1:
        return f"Move “{tag_names[0]}” to {destination}"
    return f"Move {count} tags to {destination}"


def _payload(group_id: Optional[int], is_default: bool) -> dict[str, Any]:
    return {"group": group_id, "default": bool(is_default)}


def _decode_payload(value: Any) -> tuple[Optional[int], bool]:
    """A recorded tag state: ``{"group", "default"}``, or the older bare group id."""
    if isinstance(value, dict):
        group_id = value.get("group")
        return (None if group_id is None else int(group_id)), bool(value.get("default"))
    return (None if value is None else int(value)), False


def create_group(db, name: str) -> dict[str, Any]:
    """Create a group on the writer queue; see :func:`create_group_in_session`."""
    return db.run_task(create_group_in_session, name)


def reorder_groups(db, ids: list[int]) -> dict[str, Any]:
    """Reorder on the writer queue; see :func:`reorder_groups_in_session`."""
    return db.run_task(reorder_groups_in_session, ids)


def assign_tags(
    db,
    tags: list[str],
    group_id: Optional[int],
    *,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    """Assign on the writer queue; see :func:`assign_tags_in_session`."""
    return db.run_task(
        assign_tags_in_session,
        tags,
        group_id,
        request_context=request_context,
    )


def assign_tags_in_session(
    session: Session,
    tags: list[str],
    group_id: Optional[int],
    *,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    """File *tags* under *group_id* (``None`` = Ungrouped) and record one undo.

    A tag already in *group_id* is left alone, default or not: moving it there is
    not a move, so it keeps its provenance. A tag that does move becomes a user
    filing (``is_default`` False). Only changed tags are written and recorded; a
    request that changes nothing records no operation.

    Args:
        session: The writer's session; commits the change and its row together.
        tags: Tag names to file. Stripped, de-duplicated and matched exactly.
        group_id: Target group, or ``None`` to make the tags Ungrouped.
        request_context: ``operation_log_service.request_context(request)``.
    """
    try:
        destination = UNGROUPED
        if group_id is not None:
            group = session.get(TagGroup, group_id)
            if group is None:
                raise TagGroupError(404, f"No tag group with id {group_id}")
            destination = group.name
        names = _clean_tags(tags)

        filed = _filings_of(session, names)
        before = {name: filed.get(name, (None, False)) for name in names}
        changed = [name for name in names if before[name][0] != group_id]
        if not changed:
            return read_map_in_session(session)

        _write_members(session, {name: (group_id, False) for name in changed})
        record_payload_operation_in_session(
            session,
            op_type=OP_TYPE_MOVE,
            target_type=TARGET_TAG_GROUP,
            before={"tags": {n: _payload(*before[n]) for n in changed}},
            after={"tags": {n: _payload(group_id, False) for n in changed}},
            summary=_move_summary(len(changed), changed, destination),
            **request_context,
        )
        session.commit()
        return read_map_in_session(session)
    except BaseException:
        session.rollback()
        raise


# ---------------------------------------------------------------------------
# Default groups: seeding
# ---------------------------------------------------------------------------


def load_default_tags(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Read the default filings file.

    Returns:
        ``{"version": int, "groups": [(name, [tag, ...]), ...]}`` in file order,
        or ``None`` when the file is absent (logged): a build without it seeds
        nothing rather than failing to open a library.

    Raises:
        ValueError: The file is not valid JSON or does not have the expected shape.
    """
    target = Path(path) if path is not None else DEFAULT_TAGS_FILE
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            "tag_groups: default tag file %s is missing; no default groups are seeded",
            target,
        )
        return None
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"{target} is not valid JSON: {exc}") from None
    version = raw.get("version") if isinstance(raw, dict) else None
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f"{target} has no integer 'version'")
    groups: list[tuple[str, list[str]]] = []
    entries = raw.get("groups") or []
    if not isinstance(entries, list):
        raise ValueError(f"{target} has a 'groups' that is not a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"{target} has a group entry that is not an object")
        name = str(entry.get("name") or "").strip()
        if not 1 <= len(name) <= NAME_MAX_LENGTH:
            raise ValueError(f"{target} has a group with no usable name")
        raw_tags = entry.get("tags") or []
        if not isinstance(raw_tags, list) or not all(
            isinstance(t, str) for t in raw_tags
        ):
            raise ValueError(
                f"{target} has a group whose 'tags' is not a list of names"
            )
        tags = [normalise_tag(t) for t in raw_tags]
        groups.append((name, [t for t in tags if t]))
    return {"version": version, "groups": groups}


def seed_defaults_in_session(
    session: Session, defaults: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Seed the default groups and filings if this library is behind the file.

    Creates each default group that does not exist yet (case-insensitive name
    match reuses one), then files every default tag that has no member row.
    Seeding runs once per version: version 1 files the defaults once, and a
    later version bump runs it again. An existing row is never rewritten, so a
    user's filing survives a re-seed. A tag the user made Ungrouped has no row,
    though, so a version bump files it again as a default. Remembering that
    choice would take a tombstone row, and none is kept: whoever bumps the
    version decides whether that re-filing is wanted. Not an undoable operation.

    Returns:
        Counts for the log line, or ``None`` when the library is already current.
    """
    settings = session.exec(select(LibrarySettings)).first()
    if settings is None:
        settings = LibrarySettings()
        session.add(settings)
        session.flush()
    seeded = settings.tag_groups_seeded_version
    if seeded is not None and seeded >= defaults["version"]:
        return None
    try:
        groups = session.exec(select(TagGroup)).all()
        by_key = {g.name.casefold(): g for g in groups}
        position = max((g.position for g in groups), default=-1) + 1
        # A file name that matches a default is skipped by the loop's by_key check.
        wanted = [*DEFAULT_GROUP_NAMES, *(name for name, _ in defaults["groups"])]
        created = 0
        for name in wanted:
            if name.casefold() in by_key:
                continue
            group = TagGroup(name=name, position=position)
            position += 1
            session.add(group)
            session.flush()
            by_key[name.casefold()] = group
            created += 1

        filed_already = set(session.exec(select(TagGroupMember.tag)).all())
        rows: list[dict[str, Any]] = []
        for name, tags in defaults["groups"]:
            group_id = by_key[name.casefold()].id
            for tag in tags:
                if tag in filed_already:
                    continue
                filed_already.add(tag)
                rows.append({"tag": tag, "group_id": group_id, "is_default": True})
        if rows:
            session.execute(TagGroupMember.__table__.insert(), rows)
        settings.tag_groups_seeded_version = defaults["version"]
        session.add(settings)
        session.commit()
        return {
            "version": defaults["version"],
            "groups_created": created,
            "tags_filed": len(rows),
        }
    except BaseException:
        session.rollback()
        raise


def seed_default_groups(db) -> Optional[dict[str, Any]]:
    """Seed this library's default groups on the writer; logs one info line when it acts.

    The file is read here, off the writer, and only the filing runs on it. Raises
    on a malformed file or a database error; the caller decides whether a library
    still opens without its defaults (the server logs and carries on).
    """
    defaults = load_default_tags()
    if defaults is None:
        return None
    result = db.run_task(seed_defaults_in_session, defaults)
    if result is not None:
        logger.info(
            "tag_groups: seeded default groups from version %d: %d group(s) created, "
            "%d tag(s) filed",
            result["version"],
            result["groups_created"],
            result["tags_filed"],
        )
    return result


# ---------------------------------------------------------------------------
# Import of a Danbooru tag file
# ---------------------------------------------------------------------------


def parse_tag_file(filename: str, data: bytes) -> list[tuple[str, str, str]]:
    """Read an uploaded .xlsx or .csv into ``(english, category, subcategory)`` rows.

    The ``*_en`` columns are preferred when present and non-empty. Rows with no
    ``english`` are dropped. Runs off the writer.

    Raises:
        TagGroupError: 400 for an unsupported type, an unreadable file, or a file
            with no ``english`` or ``category`` column.
    """
    lower = (filename or "").lower()
    if lower.endswith(".xlsx"):
        try:
            raw_rows = list(iter_first_sheet_rows(data))
        except XlsxError as exc:
            logger.warning("tag_groups: refused import file %r: %s", filename, exc)
            raise TagGroupError(400, f"Could not read the .xlsx file: {exc}") from None
    elif lower.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise TagGroupError(400, "The .csv file is not UTF-8 text") from None
        try:
            raw_rows = list(csv.reader(io.StringIO(text)))
        except csv.Error as exc:
            raise TagGroupError(400, f"Could not read the .csv file: {exc}") from None
    else:
        raise TagGroupError(400, "Upload a .xlsx or .csv tag file")
    # Both formats alike: a blank row (a common Excel artefact above the
    # header, or a styled but empty row) is not a row, so the first row with
    # something in it is the header.
    raw_rows = [row for row in raw_rows if any(str(c).strip() for c in row)]
    if not raw_rows:
        raise TagGroupError(400, "The tag file is empty")

    index: dict[str, int] = {}
    for position, name in enumerate(str(c).strip().lower() for c in raw_rows[0]):
        index.setdefault(name, position)
    missing = [name for name in ("english", "category") if name not in index]
    if missing:
        raise TagGroupError(
            400, f"The tag file has no column named {', '.join(missing)}"
        )

    def cell(row: list, position: Optional[int]) -> str:
        if position is None or position >= len(row):
            return ""
        return str(row[position]).strip()

    english_at = index["english"]
    category_at = index["category"]
    category_en_at = index.get("category_en")
    subcategory_at = index.get("subcategory")
    subcategory_en_at = index.get("subcategory_en")
    rows: list[tuple[str, str, str]] = []
    for raw in raw_rows[1:]:
        english = cell(raw, english_at)
        if not english:
            continue
        category = cell(raw, category_en_at) or cell(raw, category_at)
        subcategory = cell(raw, subcategory_en_at) or cell(raw, subcategory_at)
        rows.append((english, category, subcategory))
    return rows


def _import_summary(count: int, filename: str) -> str:
    base = os.path.basename((filename or "").replace("\\", "/")) or "tag file"
    noun = "tag" if count == 1 else "tags"
    return f"Import {count:,} {noun} from {base[:80]}"


def import_tags_in_session(
    session: Session,
    rows: list[tuple[str, str, str]],
    *,
    filename: str,
    dry_run: bool,
    overwrite_user: bool,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    """File every row of an imported tag table on the writer; one undoable operation.

    Every row with a category is filed, whether or not a picture carries the tag
    yet: the file is the user's vocabulary. An adult row files under the "Adult"
    group, not dropped; only a row with no usable category is skipped and counted
    as ``skipped_uncategorised``. A Character row also files its
    franchise (:func:`franchise_of`) under Copyright, unless a row of the file
    names that tag itself. The file wins over defaults and Ungrouped tags. A tag
    the user filed (``is_default`` False) under a different group is a conflict:
    it is kept unless *overwrite_user* is set. Groups the import needs are
    created, and they stay (empty) after an undo.

    Args:
        session: The writer's session.
        rows: ``(english, category, subcategory)`` from :func:`parse_tag_file`.
        filename: The uploaded name, for the undo summary.
        dry_run: Report the plan and change nothing.
        overwrite_user: Re-file tags the user filed, instead of keeping them.
        request_context: ``operation_log_service.request_context(request)``.

    Returns:
        ``{"plan": {...}}`` for a dry run; otherwise the whole map plus ``plan``.
    """
    try:
        targets: dict[str, str] = {}
        franchises: dict[str, str] = {}
        uncategorised: set[str] = set()
        for english, category, subcategory in rows:
            tag = normalise_tag(english)
            if not tag:
                continue
            group = group_for(tag, category, subcategory, adult_group=ADULT_GROUP)
            if group is None:
                uncategorised.add(tag)
                continue
            # Names are capped to the column's length; a longer category would
            # otherwise be a group no UI can show whole.
            targets.setdefault(tag, group[:NAME_MAX_LENGTH])
            franchise = franchise_of(tag) if group == CHARACTER else None
            if franchise is not None:
                franchises.setdefault(franchise, COPYRIGHT)
        for franchise, group in franchises.items():
            # Same rule as the defaults: a franchise is Copyright even where a row
            # of the file lists it as a character; any other filing stands.
            if targets.get(franchise, CHARACTER) == CHARACTER:
                targets[franchise] = group
        skipped_uncategorised = len(uncategorised.difference(targets))

        groups = session.exec(
            select(TagGroup).order_by(TagGroup.position, TagGroup.id)
        ).all()
        # Keyed by casefold: "Artist" and "artist" are one group, not two.
        by_key = {g.name.casefold(): g for g in groups}
        filings = _filings_of(session, sorted(targets))
        changes: list[tuple[str, str, Optional[tuple[int, bool]]]] = []
        conflicts: list[str] = []
        from_defaults = from_ungrouped = overwritten = 0
        for tag in sorted(targets):
            name = targets[tag]
            before = filings.get(tag)
            target = by_key.get(name.casefold())
            if before is not None and target is not None and before[0] == target.id:
                # Already filed under the file's group: left alone, default or not.
                continue
            if before is None:
                changes.append((tag, name, None))
                from_ungrouped += 1
            elif before[1]:
                changes.append((tag, name, before))
                from_defaults += 1
            else:
                conflicts.append(tag)
                if overwrite_user:
                    changes.append((tag, name, before))
                    overwritten += 1

        # Keyed by casefold, first spelling wins, in order of first use.
        pending: dict[str, str] = {}
        for _, name, _ in changes:
            if name.casefold() not in by_key:
                pending.setdefault(name.casefold(), name)
        groups_created = list(pending.values())
        # Counted over the groups this import would really make: a category
        # whose tags are all kept (the user's own filings, without overwrite)
        # makes none, so it does not count against the cap.
        if len(groups_created) > MAX_IMPORT_NEW_GROUPS:
            raise TagGroupError(
                400,
                f"The file has {len(groups_created)} categories that are not groups "
                f"yet; an import may create at most {MAX_IMPORT_NEW_GROUPS}",
            )
        plan = {
            "rows": len(rows),
            "filed": len(changes),
            "from_defaults": from_defaults,
            "from_ungrouped": from_ungrouped,
            "user_conflicts": len(conflicts),
            "user_conflict_sample": conflicts[:IMPORT_CONFLICT_SAMPLE],
            "user_overwritten": overwritten,
            "groups_created": groups_created,
            "skipped_uncategorised": skipped_uncategorised,
        }
        if dry_run:
            return {"plan": plan}

        if changes:
            position = max((g.position for g in groups), default=-1) + 1
            for name in groups_created:
                group = TagGroup(name=name, position=position)
                position += 1
                session.add(group)
                session.flush()
                by_key[name.casefold()] = group

            # Undo of a big import must not carry every tag. A tag with no row
            # before is listed by name under "unfiled", and the after state is
            # one list per group; only a tag that already had a row keeps its
            # provenance in "tags".
            before_tags: dict[str, Any] = {}
            before_unfiled: list[str] = []
            after_groups: dict[str, list[str]] = {}
            writes: dict[str, tuple[Optional[int], bool]] = {}
            for tag, name, before in changes:
                target = by_key[name.casefold()]
                if before is None:
                    before_unfiled.append(tag)
                else:
                    before_tags[tag] = _payload(*before)
                writes[tag] = (target.id, False)
                after_groups.setdefault(str(target.id), []).append(tag)
            _write_members(session, writes)
            record_payload_operation_in_session(
                session,
                op_type=OP_TYPE_MOVE,
                target_type=TARGET_TAG_GROUP,
                before={"tags": before_tags, "unfiled": before_unfiled},
                after={"groups": after_groups},
                summary=_import_summary(len(changes), filename),
                **request_context,
            )
            session.commit()
        result = read_map_in_session(session)
        result["plan"] = plan
        return result
    except BaseException:
        session.rollback()
        raise


def import_tag_file(
    db,
    *,
    filename: str,
    data: bytes,
    dry_run: bool,
    overwrite_user: bool,
    request_context: dict[str, Any],
) -> dict[str, Any]:
    """Parse off the writer, then file on the writer; see :func:`import_tags_in_session`."""
    rows = parse_tag_file(filename, data)
    return db.run_task(
        import_tags_in_session,
        rows,
        filename=filename,
        dry_run=dry_run,
        overwrite_user=overwrite_user,
        request_context=request_context,
    )


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


def _filings_of(session: Session, tags: list[str]) -> dict[str, tuple[int, bool]]:
    """``{tag: (group_id, is_default)}`` for the member rows among *tags*.

    Reads columns, not ORM objects, so a 200 000-tag import never holds them.
    """
    found: dict[str, tuple[int, bool]] = {}
    for chunk in chunked(tags):
        for tag, group_id, is_default in session.exec(
            select(
                TagGroupMember.tag, TagGroupMember.group_id, TagGroupMember.is_default
            ).where(TagGroupMember.tag.in_(chunk))
        ).all():
            found[tag] = (int(group_id), bool(is_default))
    return found


def _write_members(
    session: Session,
    writes: dict[str, tuple[Optional[int], bool]],
    *,
    operation_id: Optional[int] = None,
) -> None:
    """Set each tag's filing in bulk: ``(group_id, is_default)``, or ``None`` = Ungrouped.

    A group that no longer exists leaves its tags Ungrouped, with one warning. The
    writes go out in chunks: one DELETE, one UPDATE per distinct (group, provenance)
    pair and one executemany INSERT, rather than a statement per tag. Does not commit.
    """
    group_ids = {gid for gid, _ in writes.values() if gid is not None}
    live: set[int] = set()
    for chunk in chunked(sorted(group_ids)):
        live.update(
            session.exec(select(TagGroup.id).where(TagGroup.id.in_(chunk))).all()
        )
    vanished = sorted(group_ids - live)
    if vanished:
        logger.warning(
            "tag_groups: operation %s restores tags into group(s) %s, which no "
            "longer exist; those tags are left Ungrouped",
            operation_id,
            vanished,
        )

    tags = sorted(writes)
    current = _filings_of(session, tags)
    deletes: list[str] = []
    updates: dict[tuple[int, bool], list[str]] = {}
    inserts: list[dict[str, Any]] = []
    for tag in tags:
        group_id, is_default = writes[tag]
        if group_id is not None and group_id not in live:
            group_id = None
        state = current.get(tag)
        if group_id is None:
            if state is not None:
                deletes.append(tag)
        elif state is None:
            inserts.append(
                {"tag": tag, "group_id": int(group_id), "is_default": bool(is_default)}
            )
        elif state != (int(group_id), bool(is_default)):
            updates.setdefault((int(group_id), bool(is_default)), []).append(tag)

    for chunk in chunked(deletes):
        session.execute(delete(TagGroupMember).where(TagGroupMember.tag.in_(chunk)))
    for (group_id, is_default), names in updates.items():
        for chunk in chunked(names):
            session.execute(
                update(TagGroupMember)
                .where(TagGroupMember.tag.in_(chunk))
                .values(group_id=group_id, is_default=is_default)
            )
    if inserts:
        session.execute(TagGroupMember.__table__.insert(), inserts)
    session.flush()


def _payload_writes(payload: dict[str, Any]) -> dict[str, tuple[Optional[int], bool]]:
    """Every tag a recorded state names, as ``{tag: (group_id, is_default)}``.

    Reads the older shapes too: ``tags`` with ``{group, default}`` or a bare group
    id, the import's ``unfiled`` list (no row before), and its ``groups`` lists.
    """
    writes: dict[str, tuple[Optional[int], bool]] = {}
    for tag, value in (payload.get("tags") or {}).items():
        writes[tag] = _decode_payload(value)
    for tag in payload.get("unfiled") or []:
        writes[tag] = (None, False)
    for group_id, tags in (payload.get("groups") or {}).items():
        for tag in tags:
            writes[tag] = (int(group_id), False)
    return writes


def restore_tag_group_moves_in_session(
    session: Session, operations: list, direction: str
) -> None:
    """Post-restore hook for ``tag_groups.move``: write the recorded map back.

    Restores each tag's group and provenance. Undo walks newest-first, so the
    oldest recorded "before" of a tag wins; redo walks oldest-first, so the newest
    "after" wins. Sorting here rather than trusting the caller's order keeps that
    true for any batch. Does not commit.
    """
    undo = direction == RESTORE_UNDO
    ordered = sorted(operations, key=lambda op: op.id or 0, reverse=undo)
    for operation in ordered:
        payload_text = operation.before_state if undo else operation.after_state
        try:
            payload = json.loads(payload_text or "{}")
        except (TypeError, ValueError) as exc:
            logger.error(
                "tag_groups: operation %s has an unreadable %s payload (%s); "
                "its tags are not restored",
                operation.id,
                "before" if undo else "after",
                exc,
            )
            raise
        _write_members(session, _payload_writes(payload), operation_id=operation.id)


operation_log_service.register_post_restore_hook(
    OP_TYPE_MOVE, restore_tag_group_moves_in_session
)
