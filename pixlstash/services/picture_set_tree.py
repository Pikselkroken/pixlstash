"""Child sets: a picture set may sit under another set or under a person (#2124).

The parent is a home, not a constraint: a child's pictures need not be in its
parent, a parent's count is its own pictures, and deleting a parent lifts its
children to the top level rather than deleting them. Nesting has no depth limit;
the only shape refused is a set becoming its own ancestor.
"""

from typing import Optional

from fastapi import HTTPException
from sqlmodel import Session, select, update

from pixlstash.db_models import Character, PictureSet


def validate_parent(
    session: Session,
    set_id: Optional[int],
    parent_set_id: Optional[int],
    parent_character_id: Optional[int],
) -> None:
    """Raise unless the parent pair names at most one existing, acyclic parent.

    Args:
        session: The open session.
        set_id: The set being placed, or None for a set not created yet.
        parent_set_id: The proposed parent set, or None.
        parent_character_id: The proposed parent person, or None.

    Raises:
        HTTPException: 400 for both parents or a cycle, 404 for a missing one.
    """
    for value in (parent_set_id, parent_character_id):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            raise HTTPException(
                status_code=400, detail=f"A parent id must be a number, not {value!r}."
            )
    if parent_set_id is not None and parent_character_id is not None:
        raise HTTPException(
            status_code=400,
            detail="A set sits under one parent: a set or a person, not both.",
        )
    if parent_character_id is not None:
        if session.get(Character, parent_character_id) is None:
            raise HTTPException(
                status_code=404, detail=f"No person {parent_character_id}."
            )
        return
    seen: set[int] = set()
    current = parent_set_id
    while current is not None:
        if set_id is not None and current == set_id:
            raise HTTPException(
                status_code=400,
                detail="A set cannot sit inside itself or one of its own child sets.",
            )
        if current in seen:
            # A cycle already stored is not this request's to diagnose; stop.
            break
        seen.add(current)
        row = session.get(PictureSet, current)
        if row is None:
            raise HTTPException(status_code=404, detail=f"No set {current}.")
        current = row.parent_set_id


def lift_children(
    session: Session,
    *,
    set_id: Optional[int] = None,
    character_id: Optional[int] = None,
) -> None:
    """Move the child sets of a set or person about to be deleted to the top.

    The caller commits. SQLite reuses ids, so a child left pointing at a deleted
    parent would turn up under whatever is created next with that id.
    """
    if set_id is not None:
        session.exec(
            update(PictureSet)
            .where(PictureSet.parent_set_id == set_id)
            .values(parent_set_id=None)
        )
    if character_id is not None:
        session.exec(
            update(PictureSet)
            .where(PictureSet.parent_character_id == character_id)
            .values(parent_character_id=None)
        )


def owning_character_id(session: Session, set_id: int) -> Optional[int]:
    """The person a set is of: the nearest person at or above it.

    A step names its person either as the set's reference owner or through
    ``parent_character_id``, so a set under a person, under a person's
    reference set, or under a set under either, is a character LoRA's dataset;
    a chain that ends at an ordinary top-level set is a concept's.
    """
    seen: set[int] = set()
    current: Optional[int] = set_id
    while current is not None and current not in seen:
        seen.add(current)
        owner = session.exec(
            select(Character.id).where(Character.reference_picture_set_id == current)
        ).first()
        if owner is not None:
            return owner
        row = session.get(PictureSet, current)
        if row is None:
            return None
        if row.parent_character_id is not None:
            return row.parent_character_id
        current = row.parent_set_id
    return None
