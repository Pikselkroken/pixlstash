from datetime import datetime, timezone

from sqlmodel import Column, Field, ForeignKey, Integer, SQLModel


class VideoCopy(SQLModel, table=True):
    """The record of a playable MP4 copy of a video (#2807).

    One row per picture, keyed by its id; the file is
    ``.pixlstash-thumbnails/proxies/<picture_id>.mp4``. The copy is fresh only
    while the original's size and mtime still equal the ``source_*`` values
    here, so a rewritten original makes the copy stale. The row also feeds the
    build-time estimate: ``pixel_frames / build_seconds`` is this machine's rate.
    """

    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    copy_bytes: int = Field(nullable=False)
    source_bytes: int = Field(nullable=False)
    source_mtime: float = Field(nullable=False)
    pixel_frames: int = Field(nullable=False)
    build_seconds: float = Field(nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), nullable=False
    )
