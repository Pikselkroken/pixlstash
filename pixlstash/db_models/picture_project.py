from typing import Optional

from sqlmodel import Column, Field, ForeignKey, Integer, SQLModel


class PictureProjectMember(SQLModel, table=True):
    """Many-to-many membership link between pictures and projects."""

    picture_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
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


class PictureProjectMemberRead(SQLModel):
    """Lightweight schema used in APIs that expose picture-project links."""

    picture_id: Optional[int] = None
    project_id: Optional[int] = None
