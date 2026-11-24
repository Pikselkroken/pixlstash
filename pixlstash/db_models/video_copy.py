"""A playable copy of a video the browser refuses (#2807).

One row per copy, keyed by its picture: the file is
``<library root>/.pixlstash-thumbnails/video-copies/<picture id>.mp4``
(``ImageUtils.video_copy_path``). The original's size and modified time when
the copy was made say whether it is still fresh; a row whose file is gone or
whose original changed counts as no copy. ``pixel_frames / seconds`` of the
latest row is how fast this machine makes copies, which the next estimate
divides by. The row goes with its picture (``ON DELETE CASCADE``).
"""

from datetime import datetime

from sqlmodel import Column, Field, ForeignKey, Integer, SQLModel, UTCDateTime


class VideoCopy(SQLModel, table=True):
    """One picture's playable copy and what it cost to make."""

    __tablename__ = "video_copy"

    picture_id: int = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    bytes: int = Field(nullable=False)
    source_bytes: int = Field(nullable=False)
    source_mtime: float = Field(nullable=False)
    pixel_frames: int = Field(nullable=False)
    seconds: float = Field(nullable=False)
    made_at: datetime = Field(
        sa_column=Column("made_at", UTCDateTime(), nullable=False, index=True)
    )
