"""The library layout: where a picture belongs, whether it still does, and the
engine that acts on the answer (v1.11 Phases 4a and 4b).

The first half is pure functions, so every case is a dict of names and a folder
path. The second half puts files on a disk and a session over them — still no
``Server``, because the engine takes a session and a root and nothing else.
"""

import os
import unicodedata

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    Project,
)
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.db_models.picture_move import PictureMove
from pixlstash.db_models.reference_folder import ReferenceFolder
from pixlstash.services import layout_move_service as engine
from pixlstash.services.operation_log_service import (
    FACET_LOCATION,
    apply_state_in_session,
    capture_state_in_session,
    record_operation_in_session,
)
from pixlstash.utils.library_layout import (
    DEFAULT_LAYOUT,
    Facet,
    Layout,
    folder_name,
    format_layout,
    is_true,
    match_destination,
    parse_layout,
    relocate,
    render,
)

# The library the cases below are judged against: two projects, two people, one
# set. Names, not rows — the model never sees the database.
KNOWN = {
    Facet.PROJECT: ["2024 Shoots", "Client Nordvik"],
    Facet.PERSON: ["Mira", "Aled"],
    Facet.SET: ["mira-lora-v3"],
}


def facets(*, projects=(), people=(), sets=(), tags=()):
    """Build the per-facet names of one picture."""
    return {
        Facet.PROJECT: list(projects),
        Facet.PERSON: list(people),
        Facet.SET: list(sets),
        Facet.TAG: list(tags),
    }


# --- The case that makes the release safe, tested first ---------------------


@pytest.mark.parametrize(
    "folder",
    [
        "",  # a flat library, everything at the root
        "_unsorted",  # a folder of the owner's own
        "Holiday/best",  # two of them
        "Mira and Aled",  # nearly a person's name, but not one
        "Holiday/2024 Shoots",  # a project name, but not where the layout looks
    ],
)
def test_a_path_the_layout_cannot_read_is_never_false(folder):
    """A path that does not parse can never be false, whatever the picture is.

    This is what makes a hand-placed file a permanent override and what means
    an existing flat library needs no migration.
    """
    picture = facets(projects=["Client Nordvik"], people=["Aled"])
    assert is_true(folder, picture, DEFAULT_LAYOUT, KNOWN) is True


def test_an_unreadable_path_stays_true_even_with_nothing_to_file_it_by():
    assert is_true("_unsorted", facets(), DEFAULT_LAYOUT, KNOWN) is True


@pytest.mark.parametrize(
    "folder",
    ["2024 Shoots/../Mira", "./2024 Shoots/Mira", "2024 Shoots/Mira/..", ".."],
)
def test_an_unnormalised_path_is_refused_whole_rather_than_tidied_up(folder):
    """Dropping the ``..`` would fabricate a level the path does not have.

    ``2024 Shoots/../Mira`` is a picture in ``Mira`` and nothing else; reading a
    project level out of it would return false — a move — for a picture that
    never left one.
    """
    picture = facets(projects=["Client Nordvik"], people=["Mira"])
    assert is_true(folder, picture, DEFAULT_LAYOUT, KNOWN) is True
    # The same path without the traversal is read, and is false.
    assert is_true("2024 Shoots/Mira", picture, DEFAULT_LAYOUT, KNOWN) is False


# --- render -----------------------------------------------------------------


def test_render_fills_both_segments():
    picture = facets(projects=["2024 Shoots"], people=["Mira"])
    assert render(picture, DEFAULT_LAYOUT) == "2024 Shoots/Mira"


def test_a_segment_with_nothing_to_fill_it_is_skipped_not_left_empty():
    """No empty folder level: the set picture sits one deep, not two."""
    assert render(facets(sets=["mira-lora-v3"]), DEFAULT_LAYOUT) == "mira-lora-v3"
    assert render(facets(projects=["2024 Shoots"]), DEFAULT_LAYOUT) == "2024 Shoots"


def test_the_first_facet_that_applies_wins_within_a_segment():
    picture = facets(projects=["2024 Shoots"], people=["Mira"], sets=["mira-lora-v3"])
    assert render(picture, DEFAULT_LAYOUT) == "2024 Shoots/Mira"


def test_the_first_value_of_a_facet_wins():
    picture = facets(projects=["2024 Shoots", "Client Nordvik"])
    assert render(picture, DEFAULT_LAYOUT) == "2024 Shoots"


def test_a_picture_with_nothing_to_file_it_by_goes_to_the_unfiled_folder():
    """Never the library root: that is where an unmigrated flat library lives."""
    assert render(facets(), DEFAULT_LAYOUT) == DEFAULT_LAYOUT.unfiled


def test_render_never_escapes_the_library_root():
    """A name is one folder level however many separators the owner typed."""
    picture = facets(projects=["../../etc"], people=["Mira/2024"])
    assert render(picture, DEFAULT_LAYOUT).split("/") == [".._.._etc", "Mira_2024"]


def test_an_unfiled_folder_that_could_escape_the_root_is_refused():
    """``unfiled`` reaches ``render``'s output verbatim, so it is validated once."""
    for bad in ["../../etc", "/etc/passwd", "Generations/_Inbox", ""]:
        with pytest.raises(ValueError):
            Layout(segments=DEFAULT_LAYOUT.segments, unfiled=bad)


def test_a_bare_string_where_a_list_of_names_belongs_is_refused():
    """``str`` is a ``Sequence[str]``, so this would silently render ``M/``."""
    with pytest.raises(TypeError):
        render({Facet.PROJECT: "Mira"}, DEFAULT_LAYOUT)


# --- is_true: the case table from DECISIONS.md ------------------------------


def test_import_moves_nothing_because_the_path_is_where_the_assignment_came_from():
    picture = facets(projects=["2024 Shoots"], people=["Mira"])
    assert is_true(render(picture, DEFAULT_LAYOUT), picture, DEFAULT_LAYOUT, KNOWN)


def test_adding_a_second_project_leaves_the_folder_true():
    """It is still in the first one, whichever ``render`` would pick today."""
    picture = facets(projects=["Client Nordvik", "2024 Shoots"], people=["Mira"])
    assert is_true("2024 Shoots/Mira", picture, DEFAULT_LAYOUT, KNOWN) is True


def test_removing_the_project_the_folder_is_named_after_makes_it_false():
    picture = facets(projects=["Client Nordvik"], people=["Mira"])
    assert is_true("2024 Shoots/Mira", picture, DEFAULT_LAYOUT, KNOWN) is False


def test_swapping_the_person_the_folder_is_named_after_makes_it_false():
    picture = facets(projects=["2024 Shoots"], people=["Aled"])
    assert is_true("2024 Shoots/Mira", picture, DEFAULT_LAYOUT, KNOWN) is False


def test_a_folder_of_the_owners_own_below_the_layout_is_not_judged():
    """``2024 Shoots / Mira / 2026-08`` is still a Mira picture in that project."""
    picture = facets(projects=["2024 Shoots"], people=["Mira"])
    assert is_true("2024 Shoots/Mira/2026-08", picture, DEFAULT_LAYOUT, KNOWN) is True


def test_a_component_below_the_last_segment_is_not_judged_even_when_known():
    """The layout has run out of segments, so ``Aled`` here is the owner's own."""
    picture = facets(projects=["2024 Shoots"], people=["Mira"])
    assert is_true("2024 Shoots/Mira/Aled", picture, DEFAULT_LAYOUT, KNOWN) is True


def test_a_skipped_segment_stays_skipped_when_the_picture_gains_one():
    """Nothing is re-derived: gaining a project does not move the set picture."""
    picture = facets(projects=["2024 Shoots"], sets=["mira-lora-v3"])
    assert is_true("mira-lora-v3", picture, DEFAULT_LAYOUT, KNOWN) is True


def test_a_deeper_segment_is_judged_even_when_an_earlier_one_was_skipped():
    picture = facets(sets=["mira-lora-v3"], people=["Aled"])
    assert is_true("Mira", picture, DEFAULT_LAYOUT, KNOWN) is False


def test_where_a_component_can_be_read_two_ways_a_true_reading_wins():
    """A name that is both a project and a person errs towards leaving it alone."""
    known = {Facet.PROJECT: ["Mira"], Facet.PERSON: ["Mira"], Facet.SET: []}
    assert is_true("Mira", facets(people=["Mira"]), DEFAULT_LAYOUT, known) is True


def test_a_name_the_library_no_longer_knows_freezes_its_folder():
    """Delete the entity and its name leaves the layout's language."""
    picture = facets(people=["Mira"])
    without_the_project = {**KNOWN, Facet.PROJECT: ["Client Nordvik"]}
    assert is_true("2024 Shoots/Mira", picture, DEFAULT_LAYOUT, KNOWN) is False
    assert is_true("2024 Shoots/Mira", picture, DEFAULT_LAYOUT, without_the_project)


# --- is_true: the unfiled folder --------------------------------------------


def test_the_unfiled_folder_stops_being_true_the_moment_something_files_it():
    unfiled = DEFAULT_LAYOUT.unfiled
    assert is_true(unfiled, facets(), DEFAULT_LAYOUT, KNOWN) is True
    assert is_true(unfiled, facets(people=["Mira"]), DEFAULT_LAYOUT, KNOWN) is False


def test_a_tree_below_a_folder_that_happens_to_be_named_like_the_unfiled_one():
    """The owner's own folders keep the override even under that name."""
    picture = facets(people=["Mira"])
    folder = f"{DEFAULT_LAYOUT.unfiled}/2019/holiday"
    assert is_true(folder, picture, DEFAULT_LAYOUT, KNOWN) is True


# --- folder names -----------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Client Nordvik", "Client Nordvik"),
        ("Mira/2024", "Mira_2024"),
        ("back\\slash", "back_slash"),
        ("colon:name", "colon_name"),
        ("bell\x07name", "bell_name"),
        ("del\x7fname", "del_name"),
        ("trailing dot.", "trailing dot"),
        ("  padded  ", "padded"),
        ("..", "_unnamed"),
        ("///", "___"),
        ("", "_unnamed"),
        ("CON", "_CON"),
        ("com4.raw", "_com4.raw"),
        ("Connor", "Connor"),
    ],
)
def test_folder_name(name, expected):
    assert folder_name(name) == expected


def test_matching_ignores_case_and_unicode_form():
    """Windows and macOS are case-insensitive, and macOS decomposes accents.

    Asserted on a folder that must come out **false**: a decomposed name that
    simply failed to match would read as an unparseable path and pass either
    way, which is no test of the normalisation at all.
    """
    composed = unicodedata.normalize("NFC", "Renée")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    known = {Facet.PERSON: [composed, "Aled"]}
    layout = Layout(segments=((Facet.PERSON,),))
    assert is_true(decomposed.upper(), facets(people=[composed]), layout, known) is True
    assert is_true(decomposed.upper(), facets(people=["Aled"]), layout, known) is False


def test_windows_separators_are_read_as_folder_levels():
    picture = facets(projects=["Client Nordvik"], people=["Mira"])
    assert is_true("2024 Shoots\\Mira", picture, DEFAULT_LAYOUT, KNOWN) is False


# --- the layout itself ------------------------------------------------------


def test_the_new_library_default_is_project_then_person_or_set():
    assert DEFAULT_LAYOUT.segments == ((Facet.PROJECT,), (Facet.PERSON, Facet.SET))
    assert DEFAULT_LAYOUT.unfiled == "_Inbox"


# ===========================================================================
# v1.11 Phase 4b — the move engine
#
# The rule under test is the same sentence, now with files on a disk behind it:
# **a picture moves only when its folder stops being true.** Almost every
# assertion below is therefore a *negative* one — nothing moved — because that
# is what the release promises. The positives are the two the design bundle's
# table calls moves: removing the project a picture's folder is named after,
# and swapping one for another.
#
# Still no ``Server``. The engine takes a session and a root, so a temp folder
# and a file-backed SQLite engine exercise the whole of it, undo included, at a
# fraction of the cost of standing an environment up — and this module's own
# environment already gives the pure half everything it needs.
# ===========================================================================

LAYOUT = DEFAULT_LAYOUT
VOCAB = {
    Facet.PROJECT: ["2024 Shoots", "Client · Nordvik"],
    Facet.PERSON: ["Mira"],
    Facet.SET: ["mira-lora-v3"],
}


# ---------------------------------------------------------------------------
# The rule, as a pure function
# ---------------------------------------------------------------------------


def test_a_still_true_folder_never_moves():
    """The headline negative: a second project is not a reason to move."""
    facets = {
        Facet.PROJECT: ["2024 Shoots", "Client · Nordvik"],
        Facet.PERSON: ["Mira"],
    }
    assert relocate("2024 Shoots/Mira", facets, LAYOUT, VOCAB) is None


def test_removing_the_project_the_folder_names_moves_it():
    facets = {Facet.PROJECT: ["Client · Nordvik"], Facet.PERSON: ["Mira"]}
    assert (
        relocate("2024 Shoots/Mira", facets, LAYOUT, VOCAB) == "Client · Nordvik/Mira"
    )


def test_a_move_carries_the_owners_own_subfolders_across():
    """The artboard's own example: ``2026-08`` is nobody's business but theirs."""
    facets = {Facet.PROJECT: ["Client · Nordvik"], Facet.PERSON: ["Mira"]}
    assert (
        relocate("2024 Shoots/Mira/2026-08", facets, LAYOUT, VOCAB)
        == "Client · Nordvik/Mira/2026-08"
    )


def test_an_off_layout_folder_is_a_permanent_override():
    facets = {Facet.PROJECT: ["Client · Nordvik"]}
    assert relocate("Holiday/2024 Shoots", facets, LAYOUT, VOCAB) is None
    assert relocate("", facets, LAYOUT, VOCAB) is None


def test_the_unfiled_folder_empties_itself_when_something_files_the_picture():
    assert relocate("_Inbox", {}, LAYOUT, VOCAB) is None
    assert relocate("_Inbox", {Facet.PERSON: ["Mira"]}, LAYOUT, VOCAB) == "Mira"


def test_losing_every_assignment_files_the_picture_as_unfiled():
    assert relocate("2024 Shoots", {}, LAYOUT, VOCAB) == "_Inbox"


def test_drift_is_offered_but_never_a_move():
    """Still true where it is, and not where ``render`` would put it."""
    facets = {
        Facet.PROJECT: ["Client · Nordvik", "2024 Shoots"],
        Facet.PERSON: ["Mira"],
    }
    assert relocate("2024 Shoots/Mira/2026-08", facets, LAYOUT, VOCAB) is None
    assert (
        match_destination("2024 Shoots/Mira/2026-08", facets, LAYOUT, VOCAB)
        == "Client · Nordvik/Mira/2026-08"
    )


def test_drift_is_not_offered_on_the_owners_own_folder():
    facets = {Facet.PROJECT: ["2024 Shoots"]}
    assert match_destination("Holiday", facets, LAYOUT, VOCAB) is None


def test_drift_is_not_offered_where_the_folder_is_already_right():
    facets = {Facet.PROJECT: ["2024 Shoots"], Facet.PERSON: ["Mira"]}
    assert match_destination("2024 Shoots/Mira", facets, LAYOUT, VOCAB) is None


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_layout_round_trips_through_its_stored_form():
    assert format_layout(DEFAULT_LAYOUT) == "project/person,set"
    assert parse_layout("project/person,set") == DEFAULT_LAYOUT


def test_no_layout_is_spelled_none_and_empty_string_alike():
    assert parse_layout(None) is None
    assert parse_layout("") is None


def test_an_unknown_facet_is_refused_rather_than_dropped():
    with pytest.raises(ValueError, match="not a layout facet"):
        parse_layout("project/mood")


def test_an_unsafe_unfiled_name_is_refused():
    with pytest.raises(ValueError):
        parse_layout("project", "../escape")


# ---------------------------------------------------------------------------
# The engine, against a real folder tree
# ---------------------------------------------------------------------------


@pytest.fixture
def library(tmp_path):
    """A library root with a layout, one project, one person, and one picture.

    The picture is at ``2024 Shoots/Mira/2026-08/0412.png`` — the design
    bundle's own example, so the tail-preservation assertions are the drawn
    case rather than an invented one.
    """
    root = tmp_path / "Generations"
    engine_ = create_engine(f"sqlite:///{tmp_path / 'vault.db'}")
    SQLModel.metadata.create_all(engine_)
    with Session(engine_) as session:
        session.add(LibrarySettings(layout=format_layout(DEFAULT_LAYOUT)))
        project = Project(name="2024 Shoots")
        other = Project(name="Client · Nordvik")
        person = Character(name="Mira")
        session.add_all([project, other, person])
        session.commit()

        folder = root / "2024 Shoots" / "Mira" / "2026-08"
        folder.mkdir(parents=True)
        (folder / "0412.png").write_bytes(b"pixels")

        picture = Picture(
            file_path="2024 Shoots/Mira/2026-08/0412.png",
            original_file_name="0412.png",
            project_id=project.id,
        )
        session.add(picture)
        session.commit()
        session.add(PictureProjectMember(picture_id=picture.id, project_id=project.id))
        session.add(Face(picture_id=picture.id, character_id=person.id))
        session.commit()
        yield {
            "session": session,
            "root": str(root),
            "picture_id": picture.id,
            "project_id": project.id,
            "other_project_id": other.id,
            "person_id": person.id,
        }


def _swap_project(library):
    """Take the picture out of the project its folder is named after."""
    session = library["session"]
    session.exec(
        select(PictureProjectMember).where(
            PictureProjectMember.picture_id == library["picture_id"]
        )
    ).all()
    for member in session.exec(select(PictureProjectMember)).all():
        session.delete(member)
    session.add(
        PictureProjectMember(
            picture_id=library["picture_id"], project_id=library["other_project_id"]
        )
    )
    picture = session.get(Picture, library["picture_id"])
    picture.project_id = library["other_project_id"]
    session.add(picture)
    session.commit()


def test_adding_a_second_project_plans_nothing(library):
    session = library["session"]
    session.add(
        PictureProjectMember(
            picture_id=library["picture_id"], project_id=library["other_project_id"]
        )
    )
    session.commit()
    plan, skipped = engine.plan_moves(session, [library["picture_id"]], library["root"])
    assert plan == []
    assert skipped == []


def test_a_library_with_no_layout_plans_nothing(library):
    session = library["session"]
    settings = session.exec(select(LibrarySettings)).first()
    settings.layout = None
    session.add(settings)
    session.commit()
    _swap_project(library)
    assert engine.plan_moves(session, [library["picture_id"]], library["root"]) == (
        [],
        [],
    )


def test_swapping_the_project_moves_the_file_and_keeps_the_owners_subfolder(library):
    session, root = library["session"], library["root"]
    _swap_project(library)

    plan, skipped = engine.plan_moves(session, [library["picture_id"]], root)
    assert skipped == []
    assert len(plan) == 1, "counted before it happens"

    moved = engine.apply_moves(session, plan, image_root=root)
    session.commit()
    assert moved == [library["picture_id"]]

    destination = os.path.join(root, "Client · Nordvik", "Mira", "2026-08", "0412.png")
    assert os.path.isfile(destination)
    assert not os.path.exists(
        os.path.join(root, "2024 Shoots", "Mira", "2026-08", "0412.png")
    )
    picture = session.get(Picture, library["picture_id"])
    assert picture.file_path == "Client · Nordvik/Mira/2026-08/0412.png"


def test_the_thumbnail_follows_the_file(library):
    """A library picture's thumbnail is a SIBLING file, not a ``.ref_thumbs``
    entry, and the difference is which path form the mover hands to
    ``get_thumbnail_path``: it branches on absolute-vs-relative. Handing it the
    absolute path would look under ``.ref_thumbs``, find nothing, blank the
    stored dimensions and strand a bitmap nothing ever collects."""
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    session, root = library["session"], library["root"]
    picture = session.get(Picture, library["picture_id"])
    old_thumb = ImageUtils.get_thumbnail_path(root, picture.file_path)
    with open(old_thumb, "wb") as handle:
        handle.write(b"thumbnail")
    picture.thumbnail_width = 320
    picture.thumbnail_height = 200
    session.add(picture)
    session.commit()

    _swap_project(library)
    plan, _ = engine.plan_moves(session, [library["picture_id"]], root)
    engine.apply_moves(session, plan, image_root=root)
    session.commit()

    picture = session.get(Picture, library["picture_id"])
    new_thumb = ImageUtils.get_thumbnail_path(root, picture.file_path)
    assert os.path.isfile(new_thumb), new_thumb
    assert not os.path.exists(old_thumb)
    # Carried, so the stored dimensions are still true and nothing re-renders.
    assert picture.thumbnail_width == 320


def test_an_emptied_folder_is_kept(library):
    session, root = library["session"], library["root"]
    _swap_project(library)
    plan, _ = engine.plan_moves(session, [library["picture_id"]], root)
    engine.apply_moves(session, plan, image_root=root)
    session.commit()
    assert os.path.isdir(os.path.join(root, "2024 Shoots", "Mira", "2026-08"))


def test_every_move_is_journalled(library):
    session, root = library["session"], library["root"]
    _swap_project(library)
    plan, _ = engine.plan_moves(session, [library["picture_id"]], root)
    engine.apply_moves(session, plan, image_root=root)
    session.commit()

    rows = session.exec(select(PictureMove)).all()
    assert len(rows) == 1
    assert rows[0].old_path == "2024 Shoots/Mira/2026-08/0412.png"
    assert rows[0].new_path == "Client · Nordvik/Mira/2026-08/0412.png"
    assert rows[0].consumed is False


def test_the_scan_claims_our_own_move_and_not_the_owners(library):
    session, root = library["session"], library["root"]
    _swap_project(library)
    plan, _ = engine.plan_moves(session, [library["picture_id"]], root)
    engine.apply_moves(session, plan, image_root=root)
    session.commit()

    ours = (
        "2024 Shoots/Mira/2026-08/0412.png",
        "Client · Nordvik/Mira/2026-08/0412.png",
    )
    theirs = ("Holiday/x.png", "Holiday/2025/x.png")
    claimed = engine.claim_own_moves(session, [ours, theirs])
    session.commit()
    assert claimed == {ours}

    # Consumed once and only once: a second, genuine move between the same two
    # folders is the owner's and must not be waved through as ours.
    assert engine.claim_own_moves(session, [ours]) == set()


def test_undo_puts_the_file_back(library):
    session, root = library["session"], library["root"]
    _swap_project(library)
    plan, _ = engine.plan_moves(session, [library["picture_id"]], root)

    before = capture_state_in_session(session, [library["picture_id"]])
    engine.apply_moves(session, plan, image_root=root)
    after = capture_state_in_session(session, [library["picture_id"]])
    operation = record_operation_in_session(
        session,
        op_type=engine.OP_LAYOUT_MOVE,
        before=before,
        after=after,
        commit=False,
    )
    session.commit()
    assert operation is not None

    import json

    recorded = json.loads(operation.before_state)
    assert FACET_LOCATION in recorded[str(library["picture_id"])]

    apply_state_in_session(session, recorded, "undo", image_root=root)
    session.commit()

    assert os.path.isfile(
        os.path.join(root, "2024 Shoots", "Mira", "2026-08", "0412.png")
    )
    assert not os.path.exists(
        os.path.join(root, "Client · Nordvik", "Mira", "2026-08", "0412.png")
    )
    picture = session.get(Picture, library["picture_id"])
    assert picture.file_path == "2024 Shoots/Mira/2026-08/0412.png"


def test_renaming_a_project_renames_the_folder_and_moves_no_files(library):
    session, root = library["session"], library["root"]
    project = session.get(Project, library["project_id"])
    project.name = "2024 Shoots (archive)"
    session.add(project)
    session.commit()

    renamed = engine.rename_entity_folders(
        session,
        Facet.PROJECT,
        "2024 Shoots",
        "2024 Shoots (archive)",
        image_root=root,
    )
    session.commit()
    assert renamed == 1
    assert os.path.isfile(
        os.path.join(root, "2024 Shoots (archive)", "Mira", "2026-08", "0412.png")
    )
    assert not os.path.exists(os.path.join(root, "2024 Shoots"))
    picture = session.get(Picture, library["picture_id"])
    # The picture is in the SAME place under a new name. Nothing about its
    # position in the tree changed, which is the whole point.
    assert picture.file_path == "2024 Shoots (archive)/Mira/2026-08/0412.png"

    # And it is still true there, so the engine has nothing to do afterwards.
    assert engine.plan_moves(session, [library["picture_id"]], root) == ([], [])


def test_a_rename_is_journalled_so_the_scan_does_not_read_it_as_intent(library):
    session, root = library["session"], library["root"]
    engine.rename_entity_folders(
        session, Facet.PROJECT, "2024 Shoots", "Renamed", image_root=root
    )
    session.commit()
    rows = session.exec(select(PictureMove)).all()
    assert [row.reason for row in rows] == ["rename"]


def test_a_taken_destination_is_declined_not_overwritten(library):
    session, root = library["session"], library["root"]
    blocker = os.path.join(root, "Client · Nordvik", "Mira", "2026-08")
    os.makedirs(blocker)
    with open(os.path.join(blocker, "0412.png"), "wb") as handle:
        handle.write(b"somebody else's file")
    _swap_project(library)

    plan, skipped = engine.plan_moves(session, [library["picture_id"]], root)
    assert plan == []
    assert skipped == [(library["picture_id"], "destination_taken")]
    with open(os.path.join(blocker, "0412.png"), "rb") as handle:
        assert handle.read() == b"somebody else's file"


def test_a_symlinked_source_is_refused(library, tmp_path):
    session, root = library["session"], library["root"]
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not the library's")
    source = os.path.join(root, "2024 Shoots", "Mira", "2026-08", "0412.png")
    os.unlink(source)
    os.symlink(outside, source)
    _swap_project(library)

    plan, skipped = engine.plan_moves(session, [library["picture_id"]], root)
    assert plan == []
    assert skipped == [(library["picture_id"], "source_is_symlink")]
    assert outside.read_bytes() == b"not the library's"


def test_a_reference_folder_without_a_layout_is_left_alone(library, tmp_path):
    session = library["session"]
    external = tmp_path / "their-library"
    (external / "2024 Shoots").mkdir(parents=True)
    (external / "2024 Shoots" / "a.png").write_bytes(b"pixels")
    folder = ReferenceFolder(folder=str(external), label="theirs")
    session.add(folder)
    session.commit()
    picture = Picture(
        file_path=str(external / "2024 Shoots" / "a.png"),
        reference_folder_id=folder.id,
    )
    session.add(picture)
    session.commit()

    plan, skipped = engine.plan_moves(session, [picture.id], library["root"])
    assert plan == []
    assert skipped == []


def test_placement_puts_a_new_picture_where_render_says(library):
    session = library["session"]
    picture_set = PictureSet(name="mira-lora-v3")
    session.add(picture_set)
    session.commit()
    assert (
        engine.placement_subfolder(
            session,
            library["root"],
            project_id=library["project_id"],
            set_id=picture_set.id,
        )
        == "2024 Shoots/mira-lora-v3"
    )


def test_placement_is_the_unfiled_folder_when_nothing_files_it(library):
    session = library["session"]
    assert engine.placement_subfolder(session, library["root"]) == "_Inbox"


def test_placement_is_nothing_at_all_without_a_layout(library):
    session = library["session"]
    settings = session.exec(select(LibrarySettings)).first()
    settings.layout = None
    session.add(settings)
    session.commit()
    assert (
        engine.placement_subfolder(
            session, library["root"], project_id=library["project_id"]
        )
        == ""
    )


def test_a_set_member_keeps_the_layout_reading_it(library):
    """A picture filed by set alone sits one level deep, not two."""
    session, root = library["session"], library["root"]
    picture_set = PictureSet(name="mira-lora-v3")
    session.add(picture_set)
    session.commit()
    folder = os.path.join(root, "mira-lora-v3")
    os.makedirs(folder)
    with open(os.path.join(folder, "b.png"), "wb") as handle:
        handle.write(b"pixels")
    picture = Picture(file_path="mira-lora-v3/b.png")
    session.add(picture)
    session.commit()
    session.add(PictureSetMember(set_id=picture_set.id, picture_id=picture.id))
    session.commit()

    assert engine.plan_moves(session, [picture.id], root) == ([], [])

    for member in session.exec(select(PictureSetMember)).all():
        session.delete(member)
    session.commit()
    plan, _ = engine.plan_moves(session, [picture.id], root)
    assert len(plan) == 1
    assert plan[0].stored_path == "_Inbox/b.png"


# ---------------------------------------------------------------------------
# The trigger: what wakes the engine, and what must not
# ---------------------------------------------------------------------------


@pytest.fixture
def stamped(library):
    """*library*'s session with the assignment-change flush hooks attached.

    The hooks are what the writer thread installs on every task session
    (``database._attach_session_hooks``); attaching them here is the same wiring
    without the cost of a ``Server``.
    """
    from sqlalchemy import event as sa_event

    from pixlstash.database import (
        _after_flush_layout_marker,
        _before_flush_layout_tracker,
    )

    session = library["session"]
    sa_event.listen(session, "before_flush", _before_flush_layout_tracker)
    sa_event.listen(session, "after_flush", _after_flush_layout_marker)
    try:
        yield library
    finally:
        sa_event.remove(session, "before_flush", _before_flush_layout_tracker)
        sa_event.remove(session, "after_flush", _after_flush_layout_marker)


def _due(session, picture_id):
    session.expire_all()
    return session.get(Picture, picture_id).layout_check_due_at


def test_a_membership_change_stamps_the_picture_due(stamped):
    session = stamped["session"]
    assert _due(session, stamped["picture_id"]) is None
    session.add(
        PictureProjectMember(
            picture_id=stamped["picture_id"], project_id=stamped["other_project_id"]
        )
    )
    session.commit()
    assert _due(session, stamped["picture_id"]) is not None


def test_a_rating_change_stamps_nothing(stamped):
    """The rule is about the FOLDER, not about anything changing."""
    session = stamped["session"]
    picture = session.get(Picture, stamped["picture_id"])
    picture.score = 5
    session.add(picture)
    session.commit()
    assert _due(session, stamped["picture_id"]) is None


def test_a_second_change_pushes_the_check_out_again(stamped):
    """The debounce IS the re-stamp: remove-then-add settles into one move.

    Asserted by planting a sentinel rather than by comparing two clock readings.
    ``second >= first`` is true of a marker that never writes at all — it passed
    with the whole re-stamp deleted — and two commits a microsecond apart make
    the strict ``>`` a flake waiting to happen. Overwriting a stamp that is
    already set is the behaviour, so that is what is checked.
    """
    session = stamped["session"]
    session.add(
        PictureProjectMember(
            picture_id=stamped["picture_id"], project_id=stamped["other_project_id"]
        )
    )
    session.commit()
    assert _due(session, stamped["picture_id"]) is not None

    sentinel = 1.0
    picture = session.get(Picture, stamped["picture_id"])
    picture.layout_check_due_at = sentinel
    session.add(picture)
    session.commit()

    for member in session.exec(select(PictureProjectMember)).all():
        session.delete(member)
    session.commit()
    second = _due(session, stamped["picture_id"])
    assert second is not None and second != sentinel, (
        "the second change must re-stamp, not leave the first stamp standing"
    )


def test_nothing_is_stamped_in_a_library_with_no_layout(stamped):
    session = stamped["session"]
    settings = session.exec(select(LibrarySettings)).first()
    settings.layout = None
    session.add(settings)
    session.commit()
    session.info.pop("_library_has_layout", None)

    session.add(
        PictureProjectMember(
            picture_id=stamped["picture_id"], project_id=stamped["other_project_id"]
        )
    )
    session.commit()
    assert _due(session, stamped["picture_id"]) is None


def test_a_person_landing_on_a_picture_stamps_it(stamped):
    """How an unfiled drop-to-person import leaves ``_Inbox`` on its own."""
    session = stamped["session"]
    other = Character(name="Someone Else")
    session.add(other)
    session.commit()
    session.add(
        Face(picture_id=stamped["picture_id"], character_id=other.id, face_index=1)
    )
    session.commit()
    assert _due(session, stamped["picture_id"]) is not None


def test_the_task_finds_only_what_is_due(stamped):
    from pixlstash.tasks.layout_move_task import LayoutMoveTask

    session = stamped["session"]
    session.add(
        PictureProjectMember(
            picture_id=stamped["picture_id"], project_id=stamped["other_project_id"]
        )
    )
    session.commit()
    due_at = _due(session, stamped["picture_id"])

    assert LayoutMoveTask.find_due_pictures(session, 10, due_at - 1) == []
    found = LayoutMoveTask.find_due_pictures(session, 10, due_at + 1)
    assert [picture.id for picture in found] == [stamped["picture_id"]]


# ---------------------------------------------------------------------------
# The paths that can lose a file
# ---------------------------------------------------------------------------


def test_a_failure_after_the_moves_puts_every_file_back(library):
    """The rollback has to cover the caller's whole transaction, not the loop.

    Everything after ``apply_moves`` can raise — two state captures, the
    operation row, the flag clear, the commit — and the writer thread then rolls
    the session back. A row left naming a path with no file at it is not
    cosmetic: ``MissingFilePurgeFinder`` deletes it within the hour and the
    picture's tags, sets and score go with it.
    """
    session, root = library["session"], library["root"]
    _swap_project(library)
    plan, _ = engine.plan_moves(session, [library["picture_id"]], root)
    assert plan

    applied: list = []
    engine.apply_moves(session, plan, image_root=root, applied=applied)
    assert applied, "the move reached the disk"
    assert os.path.isfile(
        os.path.join(root, "Client · Nordvik", "Mira", "2026-08", "0412.png")
    )

    engine.rollback_applied_moves(applied, root)
    session.rollback()

    assert os.path.isfile(
        os.path.join(root, "2024 Shoots", "Mira", "2026-08", "0412.png")
    )
    assert not os.path.exists(
        os.path.join(root, "Client · Nordvik", "Mira", "2026-08", "0412.png")
    )
    assert session.get(Picture, library["picture_id"]).file_path == (
        "2024 Shoots/Mira/2026-08/0412.png"
    )


def test_the_rollback_brings_the_thumbnail_back_too(library):
    """A bitmap left at the new name is stranded: nothing sweeps by anything but
    a row's *current* path, and the row still claims a thumbnail so
    ``MissingThumbnailFinder`` will not render a fresh one either."""
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    session, root = library["session"], library["root"]
    picture = session.get(Picture, library["picture_id"])
    old_thumb = ImageUtils.get_thumbnail_path(root, picture.file_path)
    with open(old_thumb, "wb") as handle:
        handle.write(b"thumbnail")

    _swap_project(library)
    plan, _ = engine.plan_moves(session, [library["picture_id"]], root)
    applied: list = []
    engine.apply_moves(session, plan, image_root=root, applied=applied)
    engine.rollback_applied_moves(applied, root)
    session.rollback()

    assert os.path.isfile(old_thumb)


def test_renaming_a_person_leaves_a_same_named_sets_folder_alone(library):
    """Under the default layout a person and a set both sit one level down, so a
    folder name alone cannot say which of them wrote it.

    Renaming the person must not claim the set's folder: doing so drags the
    set's rows to a name that is not theirs and leaves the engine planning a
    second move to undo it — two file operations on the owner's disk for a
    change to an entity nobody touched.
    """
    session, root = library["session"], library["root"]
    picture_set = PictureSet(name="Summer")
    person = Character(name="Summer")
    session.add_all([picture_set, person])
    session.commit()

    folder = os.path.join(root, "2024 Shoots", "Summer")
    os.makedirs(folder)
    with open(os.path.join(folder, "s.png"), "wb") as handle:
        handle.write(b"pixels")
    # In the project AND the set, so `2024 Shoots/Summer/` is true of it and the
    # engine has nothing to do — which is what makes the assertion at the end
    # about the rename rather than about the rule.
    member = Picture(
        file_path="2024 Shoots/Summer/s.png", project_id=library["project_id"]
    )
    session.add(member)
    session.commit()
    session.add(PictureSetMember(set_id=picture_set.id, picture_id=member.id))
    session.add(
        PictureProjectMember(picture_id=member.id, project_id=library["project_id"])
    )
    session.commit()

    person.name = "Summer B"
    session.add(person)
    session.commit()
    renamed = engine.rename_entity_folders(
        session, Facet.PERSON, "Summer", "Summer B", image_root=root
    )

    assert renamed == 0
    assert os.path.isdir(folder)
    assert session.get(Picture, member.id).file_path == "2024 Shoots/Summer/s.png"
    # And nothing is queued to move, which is the failure the rename would cause.
    assert engine.plan_moves(session, [member.id], root) == ([], [])


def test_move_to_match_takes_the_offer_and_records_one_undo(library):
    session, root = library["session"], library["root"]
    session.add(
        PictureProjectMember(
            picture_id=library["picture_id"], project_id=library["other_project_id"]
        )
    )
    picture = session.get(Picture, library["picture_id"])
    picture.project_id = library["other_project_id"]
    session.add(picture)
    session.commit()

    report = engine.describe_drift(session, [library["picture_id"]], root)
    entry = report[library["picture_id"]]
    assert entry["current_folder"] == "2024 Shoots/Mira/2026-08"
    assert entry["suggested_folder"] == "Client · Nordvik/Mira/2026-08"

    plan, skipped = engine.plan_match_moves(session, [library["picture_id"]], root)
    assert len(plan) == 1 and skipped == []
    engine.apply_moves(session, plan, image_root=root)
    session.commit()
    assert os.path.isfile(
        os.path.join(root, "Client · Nordvik", "Mira", "2026-08", "0412.png")
    )


def test_move_to_match_skips_a_picture_that_already_matches(library):
    session, root = library["session"], library["root"]
    plan, skipped = engine.plan_match_moves(session, [library["picture_id"]], root)
    assert plan == []
    assert skipped == [(library["picture_id"], "already_matches")]


def test_restore_location_refuses_a_path_outside_the_root(library, tmp_path):
    session, root = library["session"], library["root"]
    outside = tmp_path / "elsewhere" / "0412.png"
    assert (
        engine.restore_location(
            session, library["picture_id"], str(outside), image_root=root
        )
        is False
    )
    assert session.get(Picture, library["picture_id"]).file_path == (
        "2024 Shoots/Mira/2026-08/0412.png"
    )
    assert not outside.exists()


def test_restore_location_refuses_a_recorded_none(library):
    session, root = library["session"], library["root"]
    assert (
        engine.restore_location(session, library["picture_id"], None, image_root=root)
        is False
    )


def test_restore_location_is_idempotent(library):
    """Applying the recorded path twice is a no-op, which is what makes undo
    converge on a file something else has since moved rather than drift."""
    session, root = library["session"], library["root"]
    assert (
        engine.restore_location(
            session,
            library["picture_id"],
            "2024 Shoots/Mira/2026-08/0412.png",
            image_root=root,
        )
        is False
    )


def test_the_journal_is_pruned_past_its_retention_window(library):
    from datetime import datetime, timedelta

    from pixlstash.db_models.picture_move import RETENTION_S

    session = library["session"]
    stale = PictureMove(
        picture_id=library["picture_id"],
        old_path="a.png",
        new_path="b.png",
        moved_at=datetime.utcnow() - timedelta(seconds=RETENTION_S * 2),
    )
    fresh = PictureMove(
        picture_id=library["picture_id"], old_path="c.png", new_path="d.png"
    )
    session.add_all([stale, fresh])
    session.commit()

    assert engine.prune_move_journal(session) == 1
    session.commit()
    assert [row.old_path for row in session.exec(select(PictureMove)).all()] == [
        "c.png"
    ]
