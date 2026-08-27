"""Many-to-many membership links between projects and the entities they scope.

Issue #125 makes a character or a picture set reachable from **several** projects
at once. The change is deliberately *additive*: the scalar
``Character.project_id`` / ``PictureSet.project_id`` foreign keys stay exactly as
they were and keep pointing at the entity's **primary** project (the lowest member
project id, or ``NULL`` when the entity belongs to no project), while these two
join tables carry the full membership set.

**The contract is "write both, read the join".** Every write path goes through
:mod:`pixlstash.services.project_membership_service`
(``set_character_projects`` / ``set_picture_set_projects``), which updates the join
rows *and* re-derives the scalar pointer in one place. Every *read* that asks
"is this entity in project P?" must use the predicates below rather than comparing
the scalar column, because the scalar only ever names one of possibly many
projects. Comparing the FK is now a bug: it silently hides an entity's secondary
projects, and - in the authorization helpers - under-grants (or, once the FK is
eventually dropped post-1.12, breaks outright).

The FK removal is explicitly out of scope here; a later cleanup release retires it
once no reader depends on it.
"""

from sqlalchemy import exists
from sqlmodel import Column, Field, ForeignKey, Integer, SQLModel, select

from .character import Character
from .picture_set import PictureSet

__all__ = [
    "CharacterProjectMember",
    "PictureSetProjectMember",
    "character_in_project",
    "character_in_no_project",
    "picture_set_in_project",
    "picture_set_in_no_project",
]


class CharacterProjectMember(SQLModel, table=True):
    """Many-to-many membership link between characters and projects.

    Attributes:
        character_id: FK to ``character.id`` (cascade-deletes with the character).
        project_id: FK to ``project.id`` (cascade-deletes with the project).
    """

    character_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("character.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )
    project_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("project.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )


class PictureSetProjectMember(SQLModel, table=True):
    """Many-to-many membership link between picture sets and projects.

    Attributes:
        set_id: FK to ``pictureset.id`` (cascade-deletes with the set).
        project_id: FK to ``project.id`` (cascade-deletes with the project).
    """

    set_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("pictureset.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )
    project_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("project.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        ),
    )


def character_in_project(project_id: int):
    """SQL predicate: the correlated ``Character`` row belongs to ``project_id``.

    Use in any query whose FROM already contains ``Character`` (directly or via a
    join). Replaces the pre-#125 ``Character.project_id == project_id``, which now
    only matches the character's *primary* project.

    Args:
        project_id: The project the character must be a member of.

    Returns:
        A correlated ``EXISTS`` boolean expression.
    """
    return exists(
        select(CharacterProjectMember.character_id).where(
            CharacterProjectMember.character_id == Character.id,
            CharacterProjectMember.project_id == project_id,
        )
    )


def character_in_no_project():
    """SQL predicate: the correlated ``Character`` row belongs to no project.

    Replaces ``Character.project_id.is_(None)``.

    Returns:
        A correlated ``NOT EXISTS`` boolean expression.
    """
    return ~exists(
        select(CharacterProjectMember.character_id).where(
            CharacterProjectMember.character_id == Character.id
        )
    )


def picture_set_in_project(project_id: int):
    """SQL predicate: the correlated ``PictureSet`` row belongs to ``project_id``.

    Args:
        project_id: The project the picture set must be a member of.

    Returns:
        A correlated ``EXISTS`` boolean expression.
    """
    return exists(
        select(PictureSetProjectMember.set_id).where(
            PictureSetProjectMember.set_id == PictureSet.id,
            PictureSetProjectMember.project_id == project_id,
        )
    )


def picture_set_in_no_project():
    """SQL predicate: the correlated ``PictureSet`` row belongs to no project.

    Returns:
        A correlated ``NOT EXISTS`` boolean expression.
    """
    return ~exists(
        select(PictureSetProjectMember.set_id).where(
            PictureSetProjectMember.set_id == PictureSet.id
        )
    )
