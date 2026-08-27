"""The journal of moves PixlStash made itself.

**This is the record that keeps the product from arguing with itself.** The
layout engine moves a file; the reference-folder scan walks the same tree a few
minutes later, sees the file at a new path, and - correctly, for a move the
owner made in their file manager - reads it as intent. Without a record saying
*that one was ours*, v1.11 Phase 5 reconciles our own write back into an
assignment change, which makes the folder untrue again, which moves the file
again. The two flip each other forever, and every flip is a real file on a real
disk.

So every move the engine makes writes a row here **before** anything reads the
tree again, and the scan consumes it. The row is deliberately keyed by the two
paths rather than by the picture id: the scan pairs a vanished path with an
arrived one by pixel content and has no picture id in hand until after it has
decided the pairing, and matching on the pair is what makes the answer "this
exact move", not "this picture moved at some point".

Rows are consumed once and pruned after :data:`RETENTION_S`. Keeping them
forever would let a *second*, genuine, owner-made move of the same file between
the same two folders be dismissed as ours.
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel

#: How long an assignment change waits before the layout engine acts on it, in
#: seconds. **This is the debounce**, and it is the whole reason a
#: remove-then-add is one move rather than two: swapping a picture's project in
#: the UI is two requests a fraction of a second apart, and acting on the first
#: would take the file through the unfiled folder on its way to the right one.
#: Each change re-stamps ``Picture.layout_check_due_at``, so the clock restarts
#: rather than accumulating.
#:
#: It lives here rather than beside the engine so ``database.py``'s flush hook -
#: the thing that does the stamping - can reach it without importing the whole
#: service at start-up.
CHECK_DEBOUNCE_S: float = 5.0

#: How long an unconsumed row is kept, in seconds. Comfortably longer than the
#: reference-folder rescan interval (300 s) and than a plausible "the app was
#: closed before the scan ran" gap, and short enough that a stale row cannot
#: quietly excuse a move made a week later.
RETENTION_S: float = 7 * 24 * 60 * 60

#: Why the engine moved the file. Recorded because the two have different
#: consequences for a reader: a folder rename moves every file under a folder
#: and changes no assignment, a truth move moves one file because an assignment
#: already changed.
REASON_LAYOUT = "layout"
REASON_RENAME = "rename"


class PictureMove(SQLModel, table=True):
    """One file move PixlStash performed, waiting to be recognised as its own.

    Attributes:
        id: Primary key.
        picture_id: The picture that moved. A plain column and **not** a foreign
            key, for the reason ``Character.thumbnail_picture_id`` is one:
            pictures are hard-deleted on scrapheap purge, and a real FK would
            abort those deletes for every picture that happens to have moved.
            A dangling id here is harmless - the row is matched by path.
        old_path: Where the file was, exactly as ``Picture.file_path`` held it.
        new_path: Where the engine put it, likewise.
        moved_at: When the move was applied.
        reason: :data:`REASON_LAYOUT` for a picture the rule moved, or
            :data:`REASON_RENAME` for one carried along by a renamed folder.
        consumed: Set once a scan has recognised this move as PixlStash's own.
            A consumed row is kept until it is pruned so the same pair is not
            re-claimed by a later, genuine owner move.
    """

    __tablename__ = "picture_move"

    id: Optional[int] = Field(default=None, primary_key=True)
    picture_id: Optional[int] = Field(default=None, index=True)
    old_path: str = Field(index=True)
    new_path: str = Field(index=True)
    moved_at: datetime = Field(default_factory=datetime.utcnow)
    reason: str = Field(default=REASON_LAYOUT)
    consumed: bool = Field(default=False)
