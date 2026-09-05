"""The generated shelf library is a real library, with the two odd states in it.

Acceptance for ``scripts/generate_model_shelf_library.py``. Two things have to
hold or the fixture is worse than no fixture:

1. the database is one the product itself created and migrated, so it does not
   drift the moment a migration lands;
2. the two rendering branches the plan calls out are actually present - a set on
   the ``ICON_CARDS`` sentinel with members behind it, and exactly one character
   with no route to a thumbnail.
"""

import importlib.util
import os
import sys

import pytest
from sqlmodel import Session, create_engine, select

from pixlstash.db_models.character import Character
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_set import PictureSet, PictureSetMember

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "generate_model_shelf_library.py",
)


def _load_generator():
    """Load the generator, which lives under scripts/, not in a package."""
    spec = importlib.util.spec_from_file_location(
        "generate_model_shelf_library", _SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_model_shelf_library"] = module
    spec.loader.exec_module(module)
    return module


generator = _load_generator()

# Small enough to stay fast, large enough that the odd rows have company.
CHARACTERS = 40
PICTURES = 24


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    root = tmp_path_factory.mktemp("shelf-library")
    return generator.generate_library(root, characters=CHARACTERS, pictures=PICTURES)


@pytest.fixture(scope="module")
def session(library):
    engine = create_engine(f"sqlite:///{library.db_path}")
    with Session(engine) as open_session:
        yield open_session
    engine.dispose()


def test_the_database_was_created_and_migrated_by_the_product(library, session):
    """It carries an alembic stamp, so it is a vault and not a hand-rolled schema."""
    assert library.db_path.is_file()
    stamped = session.exec(
        select(Picture).limit(1)  # forces the mapper against the real schema
    ).all()
    assert len(stamped) > 0
    version = (
        session.connection()
        .exec_driver_sql("SELECT version_num FROM alembic_version")
        .fetchall()
    )
    assert len(version) == 1


def test_the_library_is_populated_at_the_scale_the_picker_needs(library, session):
    assert len(session.exec(select(Character)).all()) == CHARACTERS + 1
    assert len(session.exec(select(PictureSet)).all()) == library.set_count
    assert library.set_count == 40
    assert len(session.exec(select(Picture)).all()) == PICTURES


class TestTheCardsBranch:
    def test_exactly_one_set_is_on_the_icon_cards_sentinel(self, session):
        icons = [row.set_icon for row in session.exec(select(PictureSet)).all()]
        assert icons.count(generator.ICON_CARDS) == 1

    def test_every_other_set_carries_a_real_mdi_name(self, session):
        for row in session.exec(select(PictureSet)).all():
            if row.set_icon == generator.ICON_CARDS:
                continue
            assert row.set_icon.startswith("mdi-"), row.name
            assert row.set_color.startswith("#"), row.name

    def test_the_cards_set_has_members_to_animate(self, library, session):
        """The sentinel means "show the member thumbnails". With no members it
        shows nothing, which is a different state entirely."""
        members = session.exec(
            select(PictureSetMember).where(
                PictureSetMember.set_id == library.cards_set_id
            )
        ).all()
        assert len(members) >= 4

    def test_those_members_are_images_that_exist_on_disk(self, library, session):
        members = session.exec(
            select(PictureSetMember).where(
                PictureSetMember.set_id == library.cards_set_id
            )
        ).all()
        for member in members:
            picture = session.get(Picture, member.picture_id)
            assert os.path.isfile(os.path.join(library.root, picture.file_path))


class TestTheCharacterWithNoThumbnail:
    def test_it_has_neither_a_face_nor_a_reference_set(self, library, session):
        """Both thumbnail paths in routes/characters.py end at a Face, so this
        is what "no thumbnail" has to mean."""
        character = session.get(Character, library.no_thumbnail_character_id)
        assert character.name == generator.NO_THUMBNAIL_CHARACTER
        assert character.reference_picture_set_id is None
        faces = session.exec(
            select(Face).where(Face.character_id == character.id)
        ).all()
        assert faces == []

    def test_it_is_the_only_one(self, library, session):
        """Over-blocking the fallback is its own regression: every other
        character must still resolve a thumbnail."""
        without = [
            character.id
            for character in session.exec(select(Character)).all()
            if not session.exec(
                select(Face).where(Face.character_id == character.id)
            ).all()
        ]
        assert without == [library.no_thumbnail_character_id]

    def test_the_faces_that_do_exist_crop_inside_their_picture(self, library, session):
        """The endpoint clamps the bbox to the image and 404s if it collapses,
        so a bbox outside the 96x96 frame would fake the missing state."""
        for face in session.exec(select(Face).limit(10)).all():
            picture = session.get(Picture, face.picture_id)
            x1, y1, x2, y2 = face.bbox
            assert 0 <= x1 < x2 <= picture.width
            assert 0 <= y1 < y2 <= picture.height


def test_pictures_must_exceed_the_largest_set_span(tmp_path):
    """Sets span 6 on the cards branch; _make_sets divides by (len(pictures) - span)."""
    with pytest.raises(ValueError, match="pictures must exceed the largest set span"):
        generator.generate_library(tmp_path, pictures=6)
    with pytest.raises(ValueError, match="pictures must exceed the largest set span"):
        generator.generate_library(tmp_path, pictures=5)
