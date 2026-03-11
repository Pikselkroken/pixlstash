import os
from datetime import datetime
from typing import Optional, Tuple

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

    next_position = None
    rows = session.exec(
        select(Picture.stack_position).where(
            Picture.stack_id == stack_id,
            Picture.stack_position.is_not(None),
        )
    ).all()
    existing_positions = [row for row in rows if row is not None]
    if existing_positions:
        next_position = max(existing_positions) + 1

    pic.stack_id = stack_id
    if next_position is not None:
        pic.stack_position = next_position
    stack.updated_at = datetime.utcnow()
    session.add(pic)
    session.add(stack)
    session.commit()
    return True
