"""Shared route helpers: typed require_*_or_404 lookups.

Each helper performs a session.get() and raises HTTPException(404) if the row
does not exist.  Import these instead of duplicating the inline guard pattern.
"""

from sqlmodel import Session, select

from pixlstash.db_models import (
    Character,
    CharacterProjectMember,
    Face,
    PictureSet,
    PictureSetMember,
    PictureSetProjectMember,
)


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

    Since issue #125 an entity may belong to several projects, so "still assigned
    to ``project_id``" is read from the ``CharacterProjectMember`` /
    ``PictureSetProjectMember`` join tables rather than the scalar primary-project
    FK. Reading the FK here would drop a picture out of a project that one of its
    entities is still (secondarily) a member of.
    """
    char_query = (
        select(Character.id)
        .join(Face, Face.character_id == Character.id)
        .join(
            CharacterProjectMember,
            CharacterProjectMember.character_id == Character.id,
        )
        .where(
            Face.picture_id == picture_id,
            CharacterProjectMember.project_id == project_id,
        )
    )
    if exclude_character_id is not None:
        char_query = char_query.where(Character.id != exclude_character_id)
    if session.exec(char_query).first() is not None:
        return True

    set_query = (
        select(PictureSet.id)
        .join(PictureSetMember, PictureSetMember.set_id == PictureSet.id)
        .join(
            PictureSetProjectMember,
            PictureSetProjectMember.set_id == PictureSet.id,
        )
        .where(
            PictureSetMember.picture_id == picture_id,
            PictureSetProjectMember.project_id == project_id,
        )
    )
    if exclude_set_id is not None:
        set_query = set_query.where(PictureSet.id != exclude_set_id)
    if session.exec(set_query).first() is not None:
        return True

    return False
