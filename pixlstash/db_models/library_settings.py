"""Settings that belong to a library rather than to the person using it.

One row per vault. The hub owns identity and preferences; this owns the handful
of values that are properties of *this* library and would be meaningless, or
actively wrong, applied to another one.

**The test for what belongs here** (multi-library plan §5): would two libraries
sharing this value feel wrong? Then it lives here. Would two *users* sharing it
feel wrong? Then it lives in the hub. Most view preferences fail the first test
and pass the second, which is why this table is deliberately small.

Decided 2026-08-02, after enumerating the candidates: only
``similarity_character`` moves. Hidden tags, the tag filter and the penalised-tag
weights are the user's own working preferences and stay in the hub even though
they name library vocabulary; the owner is the same person in every library and
wants the same defects penalised.
"""

from typing import Optional

from sqlalchemy import Column, Integer, String
from sqlmodel import Field, SQLModel


class LibrarySettings(SQLModel, table=True):
    """The single settings row for the library this vault is.

    Attributes:
        id: Primary key. There is exactly one row.
        library_uuid: The fingerprint the hub stamps in when it registers this
            library, used to tell "the same library came back" from "a different
            library at the same path" when a detached folder is re-attached. It
            is never referenced by a token and never used to decide access: a
            library folder can arrive from anyone, so a value found here must
            not be able to claim an identity that tokens on this machine already
            carry.
        similarity_character: The character the grid sorts "most like" against.
            A row id **in this vault's** character table, which is exactly why it
            cannot live in the hub: character 7 in one library and character 7 in
            another are different people, so a per-user value silently names the
            wrong person after a switch.
    """

    __tablename__ = "library_settings"

    id: Optional[int] = Field(default=None, primary_key=True)
    library_uuid: Optional[str] = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    similarity_character: Optional[int] = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
