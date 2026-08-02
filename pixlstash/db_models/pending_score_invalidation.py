"""A durable record that some smart scores are owed a recompute.

Changing the weight of a penalised tag makes the cached ``picture.smart_score``
of every picture carrying that tag stale. The repair is to NULL those scores, at
which point :class:`~pixlstash.tasks.missing_smart_score_finder.MissingSmartScoreFinder`
recomputes them. NULL is the *only* signal that a recompute is owed, so if it is
never written, nothing anywhere knows.

That used to be safe: the setting and the pictures shared one database, so the
setting write and the NULLing committed together or not at all. Since the
hub/vault split the setting lives in the hub and the pictures in the vault, and
SQLite has no transaction spanning two databases. Without something durable in
between, a crash after the setting commits and before the NULLing runs leaves
scores computed under the old weights, with nothing left to notice: a stale
score is a plausible number, so nothing errors and the grid simply sorts wrong,
indefinitely.

This table is that something. A row here means "these tags changed weight and
the affected scores have not been invalidated yet". It is written to the vault
*before* the setting is committed to the hub, so the two possible crash points
both fail safe:

* crash after the row, before the setting: the row is consumed, scores are
  recomputed under the *unchanged* weights, and come out identical. Wasted work,
  correct data.
* crash after the setting: the row is already durable and is consumed on the
  next sweep.

Consuming the row and NULLing the scores happen in one vault transaction, so
that half cannot tear.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String
from sqlmodel import Field, SQLModel


class PendingScoreInvalidation(SQLModel, table=True):
    """One recorded, not-yet-applied smart-score invalidation.

    Attributes:
        id: Auto-incrementing primary key.
        tags: JSON array of lowercase tag names whose weight changed.
        created_at: When the invalidation was recorded, for diagnostics and so a
            stuck row is visible rather than merely present.
        attempts: How many times applying it has been tried. Incremented on
            failure so a row that cannot be applied is loud in the logs instead
            of retried forever in silence.
    """

    __tablename__ = "pending_score_invalidation"

    id: Optional[int] = Field(default=None, primary_key=True)
    tags: str = Field(sa_column=Column(String, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime, nullable=False),
        default_factory=datetime.utcnow,
    )
    attempts: int = Field(sa_column=Column(Integer, nullable=False, default=0))
