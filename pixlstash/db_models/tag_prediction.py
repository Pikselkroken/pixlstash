from datetime import datetime
from typing import TYPE_CHECKING, Optional

import sqlalchemy as sa
from sqlalchemy import Column, ForeignKey, Integer, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

if TYPE_CHECKING:
    from .picture import Picture


# ``model_version`` recorded when the producing model cannot say what it is.
# It never compares unequal to itself, so such rows never go stale - which is
# why a plugin should return a real version from ``TaggerPlugin.model_version``.
UNKNOWN_MODEL_VERSION = "unknown"

# Separates the plugin name from its version in a stored ``model_version``.
# Rows written by a tagger plugin are qualified (``joycaption@2024-11``); rows
# from the built-in PixlStash tagger are not (``v43``), and neither is the
# ``manual`` sentinel the human-label ledger writes.  That is what
# :func:`feeds_anomaly_score` tests, and it is why the separator must never
# appear in a bare version string.
PLUGIN_VERSION_SEPARATOR = "@"


def qualify_plugin_model_version(plugin_name: str, version: str | None) -> str:
    """Return the ``model_version`` to store for a tagger plugin's predictions.

    Qualifying with the plugin name keeps two plugins that happen to share a
    version string ("v3") from looking like the same model, which would make
    each one's run delete the other's rows as stale.

    Args:
        plugin_name: Registered plugin name, e.g. ``'joycaption'``.
        version: What the plugin reported, or ``None``/``""`` when it said
            nothing.

    Returns:
        ``'<plugin_name>@<version>'``, with ``unknown`` for an absent version.
    """
    return (
        f"{plugin_name}{PLUGIN_VERSION_SEPARATOR}"
        f"{(version or '').strip() or UNKNOWN_MODEL_VERSION}"
    )


def is_plugin_model_version(model_version: str | None) -> bool:
    """Whether a stored ``model_version`` was written by a tagger plugin.

    Splits prediction rows into two populations that must not overwrite or
    delete each other: the built-in PixlStash tagger's (plus the ledger's
    ``manual`` sentinel), and the tagger plugins'.  A picture holds at most one
    row per tag, so without this split, switching to a plugin would delete the
    built-in tagger's confidences as stale and take the picture's
    ``anomaly_tag_uncertainty`` down with them.
    """
    return PLUGIN_VERSION_SEPARATOR in (model_version or "")


def feeds_anomaly_score(model_version: str | None) -> bool:
    """Whether a prediction row's confidence may feed ``anomaly_tag_uncertainty``.

    Only the built-in PixlStash tagger's confidences may. The anomaly score
    compares a stored confidence against the human's current tag, and raw
    confidences are not comparable across models: another tagger's 0.4 does not
    mean what the PixlStash tagger's 0.4 means, so letting one in would shift
    every affected picture's smart score with no user action and no way to see
    why.

    Unqualified rows pass: the built-in tagger's (``v43``, ``unknown``) and the
    ledger's ``manual`` sentinel, which is every row written before plugins
    could produce predictions at all.
    """
    return not is_plugin_model_version(model_version)


class TagPrediction(SQLModel, table=True):
    """Model confidence score for a single tag on a picture.

    Populated by the background TagPredictionTask from the custom (anomaly)
    tagger's raw sigmoid outputs.  Distinct from the Tag table, which holds
    only user-confirmed ground-truth tags.

    Attributes:
        id: Primary key.
        picture_id: Foreign key to the picture this prediction belongs to.
        tag: The label name.
        confidence: Raw sigmoid probability in [0, 1].
        model_version: Epoch string, e.g. "epoch-43".
        status: "PENDING", "CONFIRMED", or "REJECTED" - review-UI state. NOTE this
            is *not* a reliable human signal: the background TagTask auto-flips it
            from the applied tags. Read the label ledger below for supervision.

    Human-label ledger (the per-(picture,tag) supervision record):
        label_state: "UNKNOWN" | "POS" | "NEG". A real label only when
            ``label_source`` is set; UNKNOWN/None means "nobody reviewed this", which
            training must mask out rather than read as a negative.
        label_source: "human" | "propagated" | "model" | None. POS/NEG is supervision
            only when this is non-null; ``human`` outranks everything and is never
            clobbered by the tagger or a scan (see ``not_human_labeled``).
        labeled_at: When the label was last set by a human/propagation/model.
        label_model_version: Snapshot of the tagger version whose output the human
            was adjudicating at decision time (None for a pure-manual decision with
            no prediction on file). Frozen - the tagger never overwrites it, unlike
            the live ``model_version``.
        label_confidence: Snapshot of the raw confidence the human saw at decision
            time (None if there was no prediction to adjudicate).
        predicted_at: UTC timestamp of when this prediction was written.
    """

    __tablename__ = "tag_prediction"

    id: Optional[int] = Field(default=None, primary_key=True)

    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            index=True,
            nullable=False,
        )
    )

    tag: str = Field(index=True)
    confidence: float
    model_version: str = Field(index=True)
    status: str = Field(default="PENDING", index=True)
    predicted_at: Optional[datetime] = Field(default_factory=datetime.utcnow)

    # --- Human-label ledger (supervision record; see class docstring) ---
    label_state: str = Field(default="UNKNOWN", index=True)  # UNKNOWN | POS | NEG
    label_source: Optional[str] = Field(
        default=None, index=True
    )  # human|propagated|model
    labeled_at: Optional[datetime] = Field(default=None)
    # Snapshot of the prediction the human adjudicated, frozen at decision time.
    label_model_version: Optional[str] = Field(default=None)
    label_confidence: Optional[float] = Field(default=None)

    __table_args__ = (
        UniqueConstraint("picture_id", "tag"),
        # Composite index for the missing-count query: WHERE model_version = ? -> count(distinct picture_id)
        sa.Index(
            "ix_tag_prediction_model_version_picture_id", "model_version", "picture_id"
        ),
    )

    picture: Optional["Picture"] = Relationship(
        back_populates="tag_predictions",
        sa_relationship_kwargs={
            "passive_deletes": True,
            "foreign_keys": "[TagPrediction.picture_id]",
        },
    )
