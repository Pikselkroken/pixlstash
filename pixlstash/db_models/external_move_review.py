"""The queue of file moves PixlStash did not make itself, waiting to be reconciled.

v1.11 Phase 5 (``docs/plans/v1.11.0-existing-library.md`` §4), the mirror of
Phase 4b's move engine: PixlStash moves a file when an assignment change makes
its folder stop being true; when the *owner* moves a file, PixlStash
reconsiders the assignment instead. ``ReferenceFolderScanTask`` writes one row
here per picture it found moved that the move journal (``PictureMove``) did not
claim as PixlStash's own - see ``docs/backend_architecture.md`` §26, "The move
journal, and why it is Phase 4b's job".

Nothing here is applied automatically. A row is the raw fact only; every read
in ``move_reconciliation_service`` classifies it live against the picture's
*current* facets and the root's *current* layout, so a picture whose
memberships changed between the move and the review is judged on what is true
now, not on a snapshot from the moment it moved. A row is deleted once it is
acted on (applied or dismissed) or once a read finds nothing left to reconcile
- there is no status column, the table holds exactly what is still pending.
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class ExternalMoveReview(SQLModel, table=True):
    """One file move made outside PixlStash, not yet reconciled.

    Attributes:
        id: Primary key.
        picture_id: The picture that moved. A plain column, not a foreign key,
            for the same reason ``PictureMove.picture_id`` is one: the picture
            can be hard-deleted (a scrapheap purge) between the move and the
            review, and a dangling row here is simply skipped on read rather
            than aborting that delete.
        old_path: Where the file was, exactly as ``Picture.file_path`` held it
            before the scan followed the move.
        new_path: Where the scan found it, likewise.
        detected_at: When the scan recorded this row.
    """

    __tablename__ = "external_move_review"

    id: Optional[int] = Field(default=None, primary_key=True)
    picture_id: int = Field(index=True)
    old_path: str
    new_path: str
    detected_at: datetime = Field(default_factory=datetime.utcnow)
