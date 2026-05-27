import os
from datetime import datetime
from typing import Optional, Tuple

from sqlalchemy import case
from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureStack

STACK_TAG_PREFIX = "stack_"
SOURCE_TAG_PREFIX = "src_"
STACK_TAG_SEPARATOR = "__"


def build_stack_filename_prefix(base_prefix: str, stack_id: int, source_id: int) -> str:
    parts = []
    if base_prefix:
        parts.append(base_prefix)
    parts.append(f"{STACK_TAG_PREFIX}{stack_id}")
    parts.append(f"{SOURCE_TAG_PREFIX}{source_id}")
    return STACK_TAG_SEPARATOR.join(parts)


def parse_stack_tags_from_filename(
    filename: str,
) -> Tuple[Optional[int], Optional[int]]:
    base_name = os.path.basename(filename or "")
    stem = os.path.splitext(base_name)[0]
    if not stem:
        return None, None

    stack_id = None
    source_id = None
    for part in stem.split(STACK_TAG_SEPARATOR):
        if part.startswith(STACK_TAG_PREFIX):
            value = part[len(STACK_TAG_PREFIX) :]
            if value.isdigit():
                stack_id = int(value)
        elif part.startswith(SOURCE_TAG_PREFIX):
            value = part[len(SOURCE_TAG_PREFIX) :]
            if value.isdigit():
                source_id = int(value)

    return stack_id, source_id


def normalize_stack_positions(session: Session, stack_id: int) -> None:
    """Renumber a stack's members to contiguous, 0-based ``stack_position``.

    Enforces the invariant that every non-empty stack has a *non-deleted* member
    at ``stack_position == 0``. The grid's fast SQL leader filter
    (``deleted = 0 AND (stack_id IS NULL OR stack_position = 0)``) depends on
    this — a stack whose position-0 member is missing or soft-deleted would
    silently vanish from the grid even when it still has visible members.

    Ordering (and therefore which member becomes the position-0 leader):

    1. non-deleted members rank before soft-deleted ones, so deleting the leader
       promotes the next live member to position 0;
    2. within each group, members with an explicit position rank first
       (ascending), then NULL-position members;
    3. ties broken by ``id``.

    Does not commit; the caller is responsible for committing.

    Args:
        session: Active DB session.
        stack_id: The stack to renumber. A ``None`` value is a no-op.
    """
    if stack_id is None:
        return
    pics = session.exec(
        select(Picture)
        .where(Picture.stack_id == stack_id)
        .order_by(
            Picture.deleted,
            case((Picture.stack_position.is_(None), 1), else_=0),
            Picture.stack_position,
            Picture.id,
        )
    ).all()
    for idx, pic in enumerate(pics):
        if pic.stack_position != idx:
            pic.stack_position = idx
            session.add(pic)


def get_or_create_stack_for_picture(
    session: Session, picture_id: int, name: Optional[str] = None
) -> Optional[int]:
    if picture_id is None:
        return None

    pic = session.get(Picture, picture_id)
    if pic is None:
        return None

    if pic.stack_id is not None:
        return pic.stack_id

    stack = PictureStack(name=name)
    session.add(stack)
    session.commit()
    session.refresh(stack)

    pic.stack_id = stack.id
    pic.stack_position = 0
    session.add(pic)
    session.commit()

    return stack.id


def assign_picture_to_stack(session: Session, picture_id: int, stack_id: int) -> bool:
    if picture_id is None or stack_id is None:
        return False

    pic = session.get(Picture, picture_id)
    if pic is None:
        return False

    stack = session.get(PictureStack, stack_id)
    if stack is None:
        return False

    if pic.stack_id == stack_id:
        return True

    # Append to the end of the stack (NULL sorts last), then renumber so the
    # stack keeps a contiguous 0-based ordering and therefore always has a
    # position-0 leader for the grid's SQL filter.
    pic.stack_id = stack_id
    pic.stack_position = None
    session.add(pic)
    normalize_stack_positions(session, stack_id)
    stack.updated_at = datetime.utcnow()
    session.add(stack)
    session.commit()
    return True
