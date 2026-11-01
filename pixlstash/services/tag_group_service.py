"""Tag groups: the library-wide tag-name -> group map, and its undo (issue #2469).

Every public function takes a pre-opened session (the ``*_in_session`` rule,
docs/backend_architecture.md section 10.1) and runs on the DB writer through the
route, so the change and its operation row commit together. The map is library
vocabulary, not a picture facet, so an undoable move records its own payload
(:func:`operation_log_service.record_payload_operation_in_session`) and a
post-restore hook writes that payload back on undo and redo. The route module
imports this module, which is what registers the hook at start-up.
"""

import json
from typing import Any, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import TagGroup, TagGroupMember
from pixlstash.db_models.operation import TARGET_TAG_GROUP
from pixlstash.db_models.tag import is_tag_sentinel
from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service
from pixlstash.services.operation_log_service import (
    RESTORE_UNDO,
    record_payload_operation_in_session,
)
from pixlstash.utils.sql_chunking import chunked

logger = get_logger(__name__)

OP_TYPE_MOVE = "tag_groups.move"
NAME_MAX_LENGTH = 64
MAX_ASSIGN_TAGS = 5000
UNGROUPED = "Ungrouped"


class TagGroupError(Exception):
    """A refused request. The route maps ``status_code`` onto the HTTP response."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def read_map_in_session(session: Session) -> dict[str, Any]:
    """The whole map: groups in position order and every filed tag name."""
    groups = session.exec(
        select(TagGroup).order_by(TagGroup.position, TagGroup.id)
    ).all()
    members = session.exec(select(TagGroupMember)).all()
    return {
        "groups": [
            {"id": g.id, "name": g.name, "position": g.position} for g in groups
        ],
        "tags": {m.tag: m.group_id for m in members},
    }


def list_tag_groups(db) -> dict[str, Any]:
    """Read the whole map on the read queue (``GET /tag_groups``)."""
    return db.run_immediate_read_task(read_map_in_session)


def _group_dict(group: TagGroup) -> dict[str, Any]:
    return {"id": group.id, "name": group.name, "position": group.position}


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
        result["group"] = _group_dict(group)
        return result
    except BaseException:
        session.rollback()
        raise


def reorder_groups_in_session(session: Session, ids: list[int]) -> dict[str, Any]:
    """Set group positions to *ids*, which must be exactly the existing ids."""
    try:
        groups = session.exec(select(TagGroup)).all()
        existing = {g.id for g in groups}
        if len(ids) != len(set(ids)) or set(ids) != existing:
            raise TagGroupError(400, "ids must name every existing group exactly once")
        by_id = {g.id: g for g in groups}
        for position, group_id in enumerate(ids):
            by_id[group_id].position = position
            session.add(by_id[group_id])
        session.commit()
        return read_map_in_session(session)
    except BaseException:
        session.rollback()
        raise


def _clean_tags(tags: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for raw in tags or []:
        name = (raw or "").strip()
        if name:
            seen.setdefault(name, None)
    cleaned = list(seen)
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

    Only tags whose group actually changes are written and recorded; a request
    that changes nothing records no operation.

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

        # One bounded read per chunk, kept for both the undo record and the
        # write, rather than two lookups per tag on the shared writer.
        members: dict[str, TagGroupMember] = {}
        for chunk in chunked(names):
            for member in session.exec(
                select(TagGroupMember).where(TagGroupMember.tag.in_(chunk))
            ).all():
                members[member.tag] = member
        before: dict[str, Optional[int]] = {
            name: members[name].group_id if name in members else None for name in names
        }
        changed = [name for name in names if before[name] != group_id]
        if not changed:
            return read_map_in_session(session)

        for name in changed:
            member = members.get(name)
            if group_id is None:
                if member is not None:
                    session.delete(member)
            elif member is not None:
                member.group_id = group_id
                session.add(member)
            else:
                session.add(TagGroupMember(tag=name, group_id=group_id))
        session.flush()

        record_payload_operation_in_session(
            session,
            op_type=OP_TYPE_MOVE,
            target_type=TARGET_TAG_GROUP,
            before={"tags": {name: before[name] for name in changed}},
            after={"tags": {name: group_id for name in changed}},
            summary=_move_summary(len(changed), changed, destination),
            actor=request_context.get("actor"),
            source=request_context.get("source", "external"),
            origin_client_id=request_context.get("origin_client_id"),
            batch_id=request_context.get("batch_id"),
        )
        session.commit()
        return read_map_in_session(session)
    except BaseException:
        session.rollback()
        raise


def _write_member(
    session: Session, tag: str, group_id: Optional[int], operation_id: Optional[int]
) -> None:
    """Set one tag's group, or make it Ungrouped. A vanished group is Ungrouped."""
    if group_id is not None and session.get(TagGroup, int(group_id)) is None:
        logger.warning(
            "tag_groups: operation %s restores %r into group %s, which no longer "
            "exists; leaving the tag Ungrouped",
            operation_id,
            tag,
            group_id,
        )
        group_id = None
    member = session.get(TagGroupMember, tag)
    if group_id is None:
        if member is not None:
            session.delete(member)
    elif member is not None:
        member.group_id = int(group_id)
        session.add(member)
    else:
        session.add(TagGroupMember(tag=tag, group_id=int(group_id)))


def restore_tag_group_moves_in_session(
    session: Session, operations: list, direction: str
) -> None:
    """Post-restore hook for ``tag_groups.move``: write the recorded map back.

    Undo walks newest-first, so the oldest recorded "before" of a tag wins; redo
    walks oldest-first, so the newest "after" wins. Sorting here rather than
    trusting the caller's order keeps that true for any batch. Does not commit.
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
        for tag, group_id in (payload.get("tags") or {}).items():
            _write_member(session, tag, group_id, operation.id)


operation_log_service.register_post_restore_hook(
    OP_TYPE_MOVE, restore_tag_group_moves_in_session
)
