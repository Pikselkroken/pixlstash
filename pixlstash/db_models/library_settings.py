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
        settings_fingerprint: An opaque, keyed hash of the owner's
            score-affecting settings as they were when this library was last
            opened. Compared on open to answer "did the penalty weights change
            while this library was closed?", which is the one question a dormant
            library cannot otherwise answer, since a weight change only
            invalidates the *active* library's cached scores.

            **It holds no settings, only a hash, and the key is not here.**
            Penalised tags and hidden tags are personal information: they say
            what someone collects and what they hide. A library folder is
            designed to be copied, moved and handed to other people, and a tag
            vocabulary is small and guessable, so even an unsalted hash would be
            recoverable from it by dictionary attack. The hash is therefore keyed
            by a per-library random salt that lives in the hub and never travels
            with the library.
        views_root: Where this library publishes its PixlStash Views tree, or
            None when views are off. A host path, and a property of the library
            rather than of the person: two libraries publishing their people and
            sets into the same folder would overwrite each other.
        views_kinds: Which kinds are published, as a comma-separated subset of
            ``people,sets,projects``. Empty or None means none of them.
        layout: How this library's own picture root is laid out, in the stored
            form ``utils/library_layout.format_layout`` writes
            (``"project/person,set"``). NULL means the root has no layout, which
            is every existing library: without one nothing is ever placed and
            nothing is ever moved. A property of the library because it
            describes the library's own folder tree.
        layout_unfiled: The folder a picture with nothing to file it by is
            written to under that layout. NULL means the model's default,
            ``Unassigned``. Kept out of ``layout`` on purpose - it is a name the
            owner types, and a free-text name has no business inside a
            separator-bearing format.
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
    settings_fingerprint: Optional[str] = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    views_root: Optional[str] = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    views_kinds: Optional[str] = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    layout: Optional[str] = Field(default=None, sa_column=Column(String, nullable=True))
    layout_unfiled: Optional[str] = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
