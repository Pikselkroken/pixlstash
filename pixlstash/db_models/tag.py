from sqlalchemy import Column, ForeignKey, UniqueConstraint
from sqlmodel import SQLModel, Field, Integer, Relationship

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .picture import Picture


DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT = 3
DEFAULT_SMART_SCORE_PENALIZED_TAGS = {
    "incorrect reflection": DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    "bad anatomy": 5,
    "malformed hand": 4,
    "malformed teeth": 4,
    "missing nipples": 5,
    "malformed nipples": 4,
    "waxy skin": 2,
    "flux chin": 1,
    "silicone breasts": 0,
    "malformed foot": 4,
    "pixelated": 2,
}
TAG_EMPTY_SENTINEL = (
    ""  # deprecated: use TAG_PENDING_SENTINEL; kept for migration compat
)
TAG_PENDING_SENTINEL = "__tag"
TAG_ENGINE_SENTINEL_PREFIX = "__tag:"

# SQL LIKE pattern matching any pending-retag sentinel value.
# Both underscores must be escaped because '_' is a wildcard in SQL LIKE.
# Use together with escape='\\' in SQLAlchemy .like() calls.
TAG_SENTINEL_LIKE_PATTERN = r"\_\_tag%"
TAG_SENTINEL_ESCAPE_CHAR = "\\"


def make_tag_sentinel(engine_name: str | None = None) -> str:
    """Return the sentinel tag value for the given engine.

    Args:
        engine_name: Optional engine/plugin name.  If ``None``, returns the
            generic pending-retag sentinel ``'__tag'``.

    Returns:
        ``'__tag'`` or ``'__tag:<engine_name>'``.
    """
    if engine_name:
        return f"{TAG_ENGINE_SENTINEL_PREFIX}{engine_name}"
    return TAG_PENDING_SENTINEL


def is_tag_sentinel(tag_value: str | None) -> bool:
    """Return ``True`` if *tag_value* is any pending-retag sentinel value."""
    return tag_value is not None and tag_value.startswith("__tag")


def parse_tag_engine_from_sentinel(tag_value: str | None) -> str | None:
    """Extract the engine name from an engine-specific sentinel, or ``None``.

    Returns ``None`` for the generic ``'__tag'`` sentinel and for non-sentinel
    values.
    """
    if tag_value and tag_value.startswith(TAG_ENGINE_SENTINEL_PREFIX):
        return tag_value[len(TAG_ENGINE_SENTINEL_PREFIX) :]
    return None


class Tag(SQLModel, table=True):
    """
    SQLModel for the picture_tags table.
    """

    id: int = Field(default=None, primary_key=True)

    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )
    tag: str = Field(index=True)

    __table_args__ = (UniqueConstraint("picture_id", "tag"),)

    picture: Optional["Picture"] = Relationship(
        back_populates="tags",
        sa_relationship_kwargs={
            "passive_deletes": True,
            "foreign_keys": "[Tag.picture_id]",
        },
    )
