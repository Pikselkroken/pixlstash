from typing import List, Optional, TYPE_CHECKING

from sqlmodel import Field, SQLModel, Relationship

if TYPE_CHECKING:
    from .user_token import UserToken


class User(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    username: Optional[str] = Field(default=None, index=True)
    password_hash: Optional[str] = Field(default=None)

    # User settings (formerly config.json)
    description: Optional[str] = Field(default=None)
    sort: Optional[str] = Field(default=None)
    descending: bool = Field(default=True)
    columns: Optional[int] = Field(default=None)
    show_stars: bool = Field(default=True)
    show_face_bboxes: Optional[bool] = Field(default=None)
    show_hand_bboxes: Optional[bool] = Field(default=None)
    show_format: Optional[bool] = Field(default=None)
    show_resolution: Optional[bool] = Field(default=None)
    show_problem_icon: Optional[bool] = Field(default=None)
    similarity_character: Optional[int] = Field(default=None)
    smart_score_penalized_tags: Optional[str] = Field(default=None)

    tokens: List["UserToken"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )
