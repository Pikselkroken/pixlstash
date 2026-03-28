from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlmodel import Column, Field, ForeignKey, Integer, Relationship, SQLModel

from .picture_project import PictureProjectMember

if TYPE_CHECKING:
    from pixlstash.db_models.character import Character
    from pixlstash.db_models.picture import Picture
    from pixlstash.db_models.picture_set import PictureSet


class Project(SQLModel, table=True):
    """Database model for the project table.

    A project is a named container that scopes characters and picture sets.
    Pictures, stacks and tags remain global.

    Attributes:
        id: Primary key.
        name: Human-readable project name.
        description: Optional markdown/text description.
        cover_image_path: Optional path to a vault picture used as cover art.
        extra_metadata: JSON blob for arbitrary future metadata.
        created_at: UTC timestamp set on insert.
    """

    id: int = Field(default=None, primary_key=True)
    name: str = Field(index=True, nullable=False, unique=True)
    description: Optional[str] = Field(default=None)
    cover_image_path: Optional[str] = Field(default=None)
    extra_metadata: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    # Relationships
    attachments: List["ProjectAttachment"] = Relationship(
        back_populates="project",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    characters: List["Character"] = Relationship(back_populates="project")
    picture_sets: List["PictureSet"] = Relationship(back_populates="project")
    pictures: List["Picture"] = Relationship(
        back_populates="projects", link_model=PictureProjectMember
    )


class ProjectAttachment(SQLModel, table=True):
    """Database model for the projectattachment table.

    Stores files copied into the vault and associated with a project.

    Attributes:
        id: Primary key.
        project_id: FK to the owning project (cascade-deletes with the project).
        original_filename: The name the file had when the user dragged it in.
        stored_path: Relative path inside image_root where the copy lives.
        mime_type: MIME type detected at upload time.
        file_size: Size in bytes of the stored copy.
        url: Optional URL for link-type attachments (no file stored when set).
        created_at: UTC timestamp set on insert.
    """

    id: int = Field(default=None, primary_key=True)
    project_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("project.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    original_filename: str = Field(nullable=False)
    stored_path: str = Field(nullable=False)
    mime_type: Optional[str] = Field(default=None)
    file_size: int = Field(nullable=False)
    url: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    # Relationships
    project: Optional["Project"] = Relationship(back_populates="attachments")
