"""The Studio's records (#2819): scenes, their takes, and which sets it uses.

A **scene** is a cast in a location, with props. Its cast, props and setting
are small lists the editor writes whole, so they are JSON columns rather than
tables of their own:

* ``cast``: ``[{character_id, outfit_set_id, doing, pose, brings, box}]``,
  ``brings`` being ``text``, ``lora`` or ``refs`` (what of the person goes to
  the engine) and ``box`` ``[x, y, w, h]`` as fractions of the stage picture.
* ``props``: ``[{set_id, state, held_by, box}]``, ``held_by`` a cast member's
  ``character_id`` or null.
* ``setting``: ``{weather, time, happening, light}``, what the location is
  doing in this scene.
* ``runs``: the shoots still drawing, ``[{key, kind, from_picture_id}]``.
  ``GET /studio/scenes/{id}`` files each one that has ended as takes.

Replace a JSON value, never mutate it in place: the column does not see it.

A **take** is a picture a shoot of the scene made: a still, or a clip, which
may have started from a still take (``from_picture_id``). It goes with its
scene and with its picture.

A **studio item** says which picture sets are the Studio's outfits, locations
and props: the sheet dialog (#2817) files each item's panels in a set, and
this row is what makes that set an item. An outfit's wearer is its set's
``parent_character_id``. It goes with its set.

A **studio favourite** is one starred scene, person, outfit, location or prop,
by kind and id. Plain ids, not foreign keys, since the kinds live in different
tables: a reader drops a row whose item is gone.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import JSON, Column, Field, ForeignKey, Integer, SQLModel

SCENE = "scene"
CAST = "cast"
OUTFIT = "outfit"
LOCATION = "location"
PROP = "prop"
ITEM_KINDS = (OUTFIT, LOCATION, PROP)
FAVOURITE_KINDS = (SCENE, CAST, OUTFIT, LOCATION, PROP)
TAKE_KINDS = ("still", "clip")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Scene(SQLModel, table=True):
    """A cast in a location, doing something, with props."""

    __tablename__ = "scene"

    id: int = Field(default=None, primary_key=True)
    name: str = Field(nullable=False)
    project_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("project.id", ondelete="SET NULL"), index=True
        ),
    )
    location_set_id: Optional[int] = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("pictureset.id", ondelete="SET NULL")),
    )
    # ``{shot, view}``: how close (``wide``, ``medium``, ``close``) and which
    # of the location's four views it is taken from (0 Wide, 1 Reverse, 2
    # Left, 3 Right), so one scene can be shot from more than one side.
    camera: dict = Field(default_factory=dict, sa_column=Column(JSON))
    setting: dict = Field(default_factory=dict, sa_column=Column(JSON))
    cast: list = Field(default_factory=list, sa_column=Column(JSON))
    props: list = Field(default_factory=list, sa_column=Column(JSON))
    runs: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_now, nullable=False)
    updated_at: datetime = Field(default_factory=_now, nullable=False)


class SceneTake(SQLModel, table=True):
    """One picture a shoot of a scene made."""

    __tablename__ = "scene_take"

    id: int = Field(default=None, primary_key=True)
    scene_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("scene.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    picture_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    kind: str = Field(default="still", nullable=False)
    from_picture_id: Optional[int] = Field(default=None)
    created_at: datetime = Field(default_factory=_now, nullable=False)


class StudioItem(SQLModel, table=True):
    """A picture set the Studio uses as an outfit, a location or a prop."""

    __tablename__ = "studio_item"

    set_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("pictureset.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    kind: str = Field(nullable=False, index=True)
    created_at: datetime = Field(default_factory=_now, nullable=False)


class StudioFavourite(SQLModel, table=True):
    """One starred Studio item, by kind and id."""

    __tablename__ = "studio_favourite"

    kind: str = Field(primary_key=True)
    item_id: int = Field(primary_key=True)
    created_at: datetime = Field(default_factory=_now, nullable=False)
