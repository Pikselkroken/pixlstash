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
    # IQA / signal-degradation tags (added 2026-06-20). Weights are an initial
    # proposal to tune; the tagger detection thresholds come from the post-train gate.
    "compression artifacts": 3,
    "noise": 2,
    "oversaturation": 2,
    "watermark": 4,
}
# Tags that PixlTagger merges into a parent just before training (its tag_remap),
# mirrored here as PixlStash's own editable copy. A picture tagged with a child here
# effectively *has* the parent, so the tag-suggestion scan must not flag the parent as
# "missing" on it. Loosen this (drop entries) as the model improves enough to tell the
# child tags apart on their own. Child → parent; keep in sync with pixltagger's
# pixlstash.json tag_remap (only the non-null, merge-into-a-parent entries).
DEFAULT_TAG_MERGES = {
    "extra digit": "malformed hand",
    "missing digit": "malformed hand",
    "extra toe": "malformed foot",
    "missing toe": "malformed foot",
    "missing limb": "bad anatomy",
    "extra limb": "bad anatomy",
    "swapped hands": "bad anatomy",
    "swapped feet": "bad anatomy",
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


# ---------------------------------------------------------------------------
# Description sentinels
# ---------------------------------------------------------------------------
# Stored in Picture.description to signal "needs (re)describing with a given
# plugin".  The MissingDescriptionFinder matches these rows, extracts the
# engine name, and creates a DescriptionTask with the appropriate override.
# Using a sentinel value instead of NULL prevents the background finder from
# racing with an interactive request and overwriting with the wrong plugin.

DESCRIPTION_SENTINEL_PREFIX = "__description::"

# SQL LIKE pattern — both leading underscores must be escaped.
DESCRIPTION_SENTINEL_LIKE_PATTERN = "\\_\\_description::%"
DESCRIPTION_SENTINEL_ESCAPE_CHAR = "\\"


def make_description_sentinel(engine_name: str | None = None) -> str:
    """Return the sentinel string for the given description engine.

    Args:
        engine_name: Plugin name to embed (e.g. ``'joycaption'``).  ``None``
            means "use whatever the active plugin is".

    Returns:
        ``'__description::'`` or ``'__description::<engine_name>'``.
    """
    if engine_name:
        return f"{DESCRIPTION_SENTINEL_PREFIX}{engine_name}"
    return DESCRIPTION_SENTINEL_PREFIX


def is_description_sentinel(value: str | None) -> bool:
    """Return ``True`` if *value* is a pending-description sentinel."""
    return value is not None and value.startswith(DESCRIPTION_SENTINEL_PREFIX)


def parse_engine_from_description_sentinel(value: str | None) -> str | None:
    """Extract the engine name from a description sentinel, or ``None``.

    Returns ``None`` for the generic ``'__description::'`` sentinel and for
    non-sentinel values.
    """
    if value and value.startswith(DESCRIPTION_SENTINEL_PREFIX):
        engine = value[len(DESCRIPTION_SENTINEL_PREFIX) :]
        return engine or None
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
