import json
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import Column, LargeBinary
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
    description: Optional[str] = Field(default="PixlStash default configuration")
    sort: Optional[str] = Field(default=SortMechanism.Keys.DATE.name)
    descending: bool = Field(default=True)
    columns: Optional[int] = Field(default=4)
    sidebar_thumbnail_size: Optional[int] = Field(default=48)
    # Grid thumbnail shape: "square" (force-crop to a square cell) or
    # "justified" (aspect-ratio-preserving thumbnails for a justified grid).
    thumbnail_mode: Optional[str] = Field(default="square")
    # Unified grid thumbnail size index (0..6, larger index = fewer/larger
    # thumbnails). Default 3 = Medium.
    thumbnail_size_level: Optional[int] = Field(default=3)
    sidebar_width: Optional[int] = Field(default=240)
    show_stars: bool = Field(default=True)
    show_face_bboxes: Optional[bool] = Field(default=False)
    show_hand_bboxes: Optional[bool] = Field(default=False)
    show_format: Optional[bool] = Field(default=True)
    show_resolution: Optional[bool] = Field(default=True)
    show_problem_icon: Optional[bool] = Field(default=True)
    show_stacks: Optional[bool] = Field(default=True)
    compact_mode: Optional[bool] = Field(default=True)
    # Sidebar width preference: False = full sidebar, True = narrow icon dock.
    sidebar_docked: Optional[bool] = Field(default=False)
    # Sidebar visibility preference: True = pinned open (takes layout space),
    # False = auto-hide (slides in as an overlay on hover). Set in Settings.
    sidebar_pinned: Optional[bool] = Field(default=True)
    # Dismissal of the "deleted pictures still in snapshots" warning shown after
    # a permanent purge. True = the user opted out of seeing it again.
    hide_purge_snapshot_warning: Optional[bool] = Field(default=False)
    date_format: Optional[str] = Field(default="locale")
    # Dark by default. Only a NEW row takes it: every existing user already
    # carries the theme they chose (or the light one they were given), and a
    # column default never rewrites a stored value.
    theme_mode: Optional[str] = Field(default="dark")
    comfyui_url: Optional[str] = Field(default=None)
    public_url: Optional[str] = Field(default=None)
    similarity_character: Optional[int] = Field(default=None)
    stack_strictness: Optional[float] = Field(default=0.92)
    smart_score_penalised_tags: Optional[str] = Field(
        default_factory=lambda: json.dumps(DEFAULT_SMART_SCORE_PENALIZED_TAGS)
    )
    hidden_tags: Optional[str] = Field(default_factory=lambda: json.dumps([]))
    apply_tag_filter: bool = Field(default=False)
    keep_models_in_memory: bool = Field(default=True)
    max_vram_gb: Optional[float] = Field(default=2.0)
    tagger_settings: Optional[str] = Field(default=None)
    check_for_updates: Optional[bool] = Field(default=None)
    # Opt-in telemetry consent. Every category is off by default, on every
    # install and every deployment type; nothing is transmitted until the user
    # ticks a box. ``telemetry_consent_prompted`` records that the question has
    # been put to the user, so it is asked exactly once and never re-raised.
    # Declining is a decision, not an unanswered prompt. Existing rows read NULL
    # for all five and fall back to these defaults, so an upgrade stays off.
    telemetry_send_install_id: bool = Field(default=False)
    telemetry_send_feature_usage: bool = Field(default=False)
    telemetry_send_error_reports: bool = Field(default=False)
    telemetry_send_hardware_profile: bool = Field(default=False)
    telemetry_consent_prompted: bool = Field(default=False)
    show_keyboard_hint: bool = Field(default=True)
    embed_watermark: bool = Field(default=True)
    watermark_image: Optional[bytes] = Field(
        default=None,
        sa_column=Column(LargeBinary, nullable=True),
    )

    tokens: List["UserToken"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "passive_deletes": True,
        },
    )
