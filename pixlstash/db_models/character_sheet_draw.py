"""One draw of character sheets: the state "Create a training set…" keeps.

A draw is one run of the dialog's "Draw" on a character: the settings it was
drawn with, the runs it started, the sheets those runs made, the sheets the
owner kept, the panels filed from them, and whether the owner has seen it.
The dialog's state lives here so closing or reloading the page loses none of
it. ``settings`` is opaque to the backend. The JSON columns are text, read
tolerantly by ``services/character_sheet.py``.

``drawn_from`` names a picture but is not a foreign key: a picture is hard
deleted by the Scrapheap's purge, and a draw outlives it.
"""

from datetime import datetime, timezone

import sqlalchemy as sa
from sqlmodel import Column, Field, ForeignKey, Integer, SQLModel


class CharacterSheetDraw(SQLModel, table=True):
    """One draw of character sheets on one character."""

    __tablename__ = "character_sheet_draw"

    id: int | None = Field(default=None, primary_key=True)
    character_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("character.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    settings: str = Field(default="{}", sa_column=Column(sa.Text(), nullable=False))
    drawn_from: int | None = Field(
        default=None, sa_column=Column(Integer(), nullable=True)
    )
    runs: str = Field(default="[]", sa_column=Column(sa.Text(), nullable=False))
    kept: str = Field(default="[]", sa_column=Column(sa.Text(), nullable=False))
    used: str = Field(default="[]", sa_column=Column(sa.Text(), nullable=False))
    used_sheets: str = Field(
        default="[]", sa_column=Column(sa.Text(), nullable=False, server_default="[]")
    )
    seen: bool = Field(
        default=False,
        sa_column=Column(sa.Boolean(), nullable=False, server_default=sa.false()),
    )
