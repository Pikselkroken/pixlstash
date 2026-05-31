"""Shared route helpers: typed require_*_or_404 lookups.

Each helper performs a session.get() and raises HTTPException(404) if the row
does not exist.  Import these instead of duplicating the inline guard pattern.
"""

from fastapi import HTTPException
from sqlmodel import Session, select

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    Project,
    PictureStack,
)


def require_picture_or_404(session: Session, picture_id: int) -> Picture:
    """Return the Picture row or raise 404."""
    pic = session.get(Picture, picture_id)
    if pic is None:
        raise HTTPException(status_code=404, detail="Picture not found")
    return pic


def require_project_or_404(session: Session, project_id: int) -> Project:
    """Return the Project row or raise 404."""
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def require_character_or_404(session: Session, character_id: int) -> Character:
    """Return the Character row or raise 404."""
    character = session.get(Character, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="Character not found")
    return character


def require_stack_or_404(session: Session, stack_id: int) -> PictureStack:
    """Return the PictureStack row or raise 404."""
    stack = session.get(PictureStack, stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found")
    return stack


def picture_referenced_by_project(
    session: Session,
    picture_id: int,
    project_id: int,
    *,
    exclude_character_id: int | None = None,
    exclude_set_id: int | None = None,
) -> bool:
    """Return True if a character or picture set still assigned to ``project_id``
    references ``picture_id``.

    Used when a character or picture set is moved out of a project to decide
    whether the picture's membership in the old project must be retained
    (another entity still anchors it there) or can be removed.  The entity being
    moved is excluded from the check via ``exclude_character_id`` /
    ``exclude_set_id`` so it does not count as a reason to keep the picture.
    """
    char_query = (
        select(Character.id)
        .join(Face, Face.character_id == Character.id)
        .where(
            Face.picture_id == picture_id,
            Character.project_id == project_id,
        )
    )
    if exclude_character_id is not None:
        char_query = char_query.where(Character.id != exclude_character_id)
    if session.exec(char_query).first() is not None:
        return True

    set_query = (
        select(PictureSet.id)
        .join(PictureSetMember, PictureSetMember.set_id == PictureSet.id)
        .where(
            PictureSetMember.picture_id == picture_id,
            PictureSet.project_id == project_id,
        )
    )
    if exclude_set_id is not None:
        set_query = set_query.where(PictureSet.id != exclude_set_id)
    if session.exec(set_query).first() is not None:
        return True

    return False
