import json
from typing import List, Optional, TYPE_CHECKING

from sqlmodel import Field, SQLModel, Relationship

from .picture import SortMechanism
from .tag import DEFAULT_SMART_SCORE_PENALIZED_TAGS

if TYPE_CHECKING:
    from .user_token import UserToken


class User(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    username: Optional[str] = Field(default=None, index=True)
    password_hash: Optional[str] = Field(default=None)

    # User settings (persisted in the database)
    description: Optional[str] = Field(default="PixlVault default configuration")
    sort: Optional[str] = Field(default=SortMechanism.Keys.DATE.name)
    descending: bool = Field(default=True)
    columns: Optional[int] = Field(default=4)
    sidebar_thumbnail_size: Optional[int] = Field(default=48)
    show_stars: bool = Field(default=True)
    show_face_bboxes: Optional[bool] = Field(default=False)
    show_hand_bboxes: Optional[bool] = Field(default=False)
    show_format: Optional[bool] = Field(default=True)
    show_resolution: Optional[bool] = Field(default=True)
    show_problem_icon: Optional[bool] = Field(default=True)
    show_stacks: Optional[bool] = Field(default=True)
    date_format: Optional[str] = Field(default="locale")
    theme_mode: Optional[str] = Field(default="light")
    comfyui_url: Optional[str] = Field(default="http://127.0.0.1:8188/")
    similarity_character: Optional[int] = Field(default=None)
    stack_strictness: Optional[float] = Field(default=0.92)
    smart_score_penalised_tags: Optional[str] = Field(
        default_factory=lambda: json.dumps(DEFAULT_SMART_SCORE_PENALIZED_TAGS)
    )
    hidden_tags: Optional[str] = Field(default_factory=lambda: json.dumps([]))
    apply_tag_filter: bool = Field(default=False)
    keep_models_in_memory: bool = Field(default=True)
    max_vram_gb: Optional[float] = Field(default=None)

    tokens: List["UserToken"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )
