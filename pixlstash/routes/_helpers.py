"""Shared route helpers: typed require_*_or_404 lookups.

Each helper performs a session.get() and raises HTTPException(404) if the row
does not exist.  Import these instead of duplicating the inline guard pattern.
"""

from fastapi import HTTPException
from sqlmodel import Session

from pixlstash.db_models import Character, Picture, Project, PictureStack


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
