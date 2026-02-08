from sqlmodel import SQLModel, Field, Integer
from sqlalchemy import Column, ForeignKey


class FaceTag(SQLModel, table=True):
    __tablename__ = "face_tag"

    face_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("face.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("tag.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )
