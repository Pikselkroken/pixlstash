"""Generate a realistic library for the model shelf's assignment surfaces.

The shelf's fourth verb is *assign*, and its picker is built against the
character and set lists. Three characters make any picker look fine, so this
writes the library the plan asks for instead: ~300 characters and ~40 sets, in a
real migrated vault the server can open, with the two rendering branches that
only show up at scale.

* **A set on the default ``ICON_CARDS`` branch.** ``set_icon = "cards"`` is the
  sentinel that means "animate the member thumbnails" rather than "draw this
  MDI glyph", so it is the one set appearance that needs real member pictures
  behind it to render at all.
* **A character with no thumbnail.** Both thumbnail paths in
  ``routes/characters.py`` end at a :class:`Face` with a bbox, so a character
  with no faces and no reference set is a 404 and the UI has to have a
  fallback. Every other character here has one, because over-blocking the
  fallback is its own bug.

The pictures are 96x96 JPEGs. They are real images at real paths, so the face
crop the thumbnail endpoint performs succeeds; they are simply small.

Run it::

    python scripts/generate_model_shelf_library.py /tmp/shelf-library
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image
from sqlmodel import Session, create_engine

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pixlstash.database import VaultDatabase  # noqa: E402
from pixlstash.db_models.character import Character  # noqa: E402
from pixlstash.db_models.face import Face  # noqa: E402
from pixlstash.db_models.picture import Picture  # noqa: E402
from pixlstash.db_models.picture_set import (  # noqa: E402
    PictureSet,
    PictureSetMember,
)

ICON_CARDS = "cards"
"""The sentinel from ``frontend/src/utils/setAppearance.js``: not an MDI name."""

# A short slice of the palettes in `routes/picture_sets.py::_SET_ICONS` and
# `_SET_COLORS`, which are nested inside a function there and so cannot be
# imported. Only the shape matters for a fixture: real MDI names, real hexes.
_SET_ICONS = (
    "mdi-star mdi-heart mdi-camera mdi-music mdi-run mdi-bike mdi-hiking "
    "mdi-pine-tree mdi-flower mdi-beach mdi-airplane mdi-city-variant"
).split()
_SET_COLORS = (
    "#e53935 #00acc1 #f4511e #039be5 #ff7043 #546e7a #fb8c00 #1e88e5 "
    "#fdd835 #3949ab #c0ca33 #9c27b0"
).split()

# 30 x 10 = 300 distinct character names.
_GIVEN = (
    "Ada Bea Cleo Dina Esme Farah Gilda Hana Ines Juno Kira Lena Mira Nadia "
    "Orla Petra Quinn Rosa Sena Tova Uma Vera Wren Xenia Yara Zola Anika "
    "Bella Coral Delia"
).split()
_FAMILY = ("Ash Brand Cole Dane Frost Hale Ivory Lark Mercer North").split()

_SET_NAMES = (
    "Studio Portraits|Golden Hour|Backstage|Rain Series|Rooftop|Analogue Grain|"
    "Cold Light|Market Day|Long Exposure|Winter Coats|Neon Signs|Harbour|"
    "Studio B|Editorial|Street Cast|Overcast|Blue Hour|Costume Tests|"
    "Location Scout|Reflections|Silhouettes|Close Crops|Full Length|Motion|"
    "Monochrome|High Key|Low Key|Window Light|Practical Light|Mixed Sources|"
    "Second Unit|Reshoots|Contact Sheet|Selects|Archive 2024|Archive 2025|"
    "Client Approved|Unreleased|Test Roll|Card Stack Demo"
).split("|")

NO_THUMBNAIL_CHARACTER = "Unphotographed Ash"
"""The one character deliberately left without a face or a reference set."""

CARDS_SET_NAME = "Card Stack Demo"
"""The one set left on the ``ICON_CARDS`` branch."""


@dataclass
class LibraryFixture:
    """Where the generated library is, and which rows carry the odd states."""

    root: Path
    db_path: Path
    character_count: int
    set_count: int
    picture_count: int
    cards_set_id: int
    no_thumbnail_character_id: int


def _character_names(count: int) -> list[str]:
    """Deterministic ``Given Family`` names, unique up to 300."""
    return [
        f"{_GIVEN[index % len(_GIVEN)]} {_FAMILY[(index // len(_GIVEN)) % len(_FAMILY)]}"
        for index in range(count)
    ]


def _write_pictures(root: Path, count: int) -> list[Picture]:
    """Write *count* small JPEGs and return unsaved rows describing them.

    Real files at real paths: the character-thumbnail endpoint opens the file
    and crops the face bbox out of it, so a zero-byte placeholder would 404 the
    very state this fixture exists to populate.
    """
    images = root / "images"
    images.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20261011)
    imported = datetime.now(timezone.utc) - timedelta(days=count)

    rows: list[Picture] = []
    for index in range(count):
        name = f"shoot_{index:04d}.jpg"
        path = images / name
        tint = (index * 13) % 256
        Image.new("RGB", (96, 96), (tint, (tint * 5) % 256, 220 - tint // 3)).save(
            path, "JPEG", quality=60
        )
        rows.append(
            Picture(
                file_path=os.path.join("images", name),
                format="JPEG",
                width=96,
                height=96,
                size_bytes=path.stat().st_size,
                score=rng.randint(1, 5),
                imported_at=imported + timedelta(days=index),
                thumbnail_width=96,
                thumbnail_height=96,
                square_crop_x=0,
                square_crop_y=0,
                square_crop_side=96,
            )
        )
    return rows


def _make_sets(session: Session, pictures: list[Picture]) -> int:
    """Create the sets and their memberships; return the ICON_CARDS set's id."""
    cards_set_id = 0
    for index, name in enumerate(_SET_NAMES):
        on_cards_branch = name == CARDS_SET_NAME
        picture_set = PictureSet(
            name=name,
            description=f"{name}, {index + 1} of {len(_SET_NAMES)}.",
            # The cards sentinel carries no colour: the member thumbnails are
            # the appearance, so a colour would only fight them.
            set_icon=ICON_CARDS
            if on_cards_branch
            else _SET_ICONS[index % len(_SET_ICONS)],
            set_color=None
            if on_cards_branch
            else _SET_COLORS[index % len(_SET_COLORS)],
        )
        session.add(picture_set)
        session.flush()
        if on_cards_branch:
            cards_set_id = picture_set.id
        # Six members on the cards branch so the animated stack has a stack;
        # four elsewhere, which is enough for a member count to be non-trivial.
        span = 6 if on_cards_branch else 4
        start = (index * 3) % (len(pictures) - span)
        for picture in pictures[start : start + span]:
            session.add(PictureSetMember(set_id=picture_set.id, picture_id=picture.id))
    return cards_set_id


def _make_characters(
    session: Session, pictures: list[Picture], count: int, cards_set_id: int
) -> int:
    """Create the characters; return the id of the one with no thumbnail."""
    no_thumbnail_id = 0
    for index, name in enumerate(_character_names(count)):
        character = Character(
            name=name,
            description=None if index % 7 else f"Cast member {index + 1}.",
            # A minority carry a reference set, which is the thumbnail path
            # that gets preferred over the loose-face one.
            reference_picture_set_id=cards_set_id if index % 25 == 0 else None,
        )
        session.add(character)
        session.flush()
        picture = pictures[index % len(pictures)]
        session.add(
            Face(
                picture_id=picture.id,
                character_id=character.id,
                face_index=index // len(pictures),
                bbox=[24, 24, 72, 72],
                model_pack="buffalo_l",
            )
        )

    # The odd one out, added last so it cannot collide with a face above.
    blank = Character(
        name=NO_THUMBNAIL_CHARACTER,
        description="Cast but never shot; the shelf must fall back, not 500.",
    )
    session.add(blank)
    session.flush()
    no_thumbnail_id = blank.id
    return no_thumbnail_id


def generate_library(
    root: Path, characters: int = 300, pictures: int = 120
) -> LibraryFixture:
    """Build a real, openable library under *root*.

    Args:
        root: Destination directory. Created if absent.
        characters: How many characters to create, plus one without a
            thumbnail. 300 is what the plan asks for.
        pictures: How many images to write. Sets and faces draw from these.

    Returns:
        A :class:`LibraryFixture` naming the database and the two odd rows.
    """
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "vault.db"

    # Let the product create and migrate its own database rather than
    # reproducing the schema here: a fixture built from a hand-written CREATE
    # TABLE stops being a fixture the moment a migration lands.
    VaultDatabase(str(db_path)).close()

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with Session(engine) as session:
            rows = _write_pictures(root, pictures)
            session.add_all(rows)
            session.flush()
            cards_set_id = _make_sets(session, rows)
            no_thumbnail_id = _make_characters(session, rows, characters, cards_set_id)
            session.commit()
    finally:
        engine.dispose()

    return LibraryFixture(
        root=root,
        db_path=db_path,
        character_count=characters + 1,
        set_count=len(_SET_NAMES),
        picture_count=pictures,
        cards_set_id=cards_set_id,
        no_thumbnail_character_id=no_thumbnail_id,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("root", type=Path, help="Directory to generate into.")
    parser.add_argument("--characters", type=int, default=300)
    parser.add_argument("--pictures", type=int, default=120)
    args = parser.parse_args(argv)

    library = generate_library(
        args.root, characters=args.characters, pictures=args.pictures
    )
    print(f"library:       {library.db_path}")
    print(f"  characters:  {library.character_count}")
    print(f"  sets:        {library.set_count}")
    print(f"  pictures:    {library.picture_count}")
    print(f"  cards set:   id {library.cards_set_id} ({CARDS_SET_NAME})")
    print(
        f"  no thumb:    id {library.no_thumbnail_character_id} "
        f"({NO_THUMBNAIL_CHARACTER})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
