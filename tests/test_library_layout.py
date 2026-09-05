"""The library layout: where a picture belongs, whether it still does, and the
engine that acts on the answer (v1.11 Phases 4a and 4b).

The first half is pure functions, so every case is a dict of names and a folder
path. The second half puts files on a disk and a session over them - still no
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
from pixlstash.db_models.external_move_review import ExternalMoveReview
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.db_models import DeletedFileLog
from pixlstash.db_models.picture_move import PictureMove
from pixlstash.db_models.reference_folder import ReferenceFolder
from pixlstash.routes.library_layout import _MIGRATION_BATCH_ID_RE
from pixlstash.services import layout_migration_service as migration
from pixlstash.services import layout_move_service as engine
from pixlstash.services import move_reconciliation_service as reconciliation
from pixlstash.db_models.operation import Operation
from pixlstash.services.operation_log_service import (
    FACET_LOCATION,
    apply_state_in_session,
    capture_state_in_session,
    record_operation_in_session,
    undo_in_session,
)
from pixlstash.utils.library_layout import (
    DEFAULT_LAYOUT,
    Facet,
    Layout,
    MoveOutcome,
    folder_name,
    format_layout,
    is_true,
    match_destination,
    migrate_destination,
    parse_layout,
    read_named_components,
    reconcile_move,
    relocate,
    render,
)

# The library the cases below are judged against: two projects, two people, one
# set. Names, not rows - the model never sees the database.
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
    project level out of it would return false - a move - for a picture that
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
    assert DEFAULT_LAYOUT.unfiled == "Unassigned"


# ===========================================================================
# v1.11 Phase 4b - the move engine
#
# The rule under test is the same sentence, now with files on a disk behind it:
# **a picture moves only when its folder stops being true.** Almost every
# assertion below is therefore a *negative* one - nothing moved - because that
# is what the release promises. The positives are the two the design bundle's
# table calls moves: removing the project a picture's folder is named after,
# and swapping one for another.
#
# Still no ``Server``. The engine takes a session and a root, so a temp folder
# and a file-backed SQLite engine exercise the whole of it, undo included, at a
# fraction of the cost of standing an environment up - and this module's own
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
    assert relocate("Unassigned", {}, LAYOUT, VOCAB) is None
    assert relocate("Unassigned", {Facet.PERSON: ["Mira"]}, LAYOUT, VOCAB) == "Mira"


def test_losing_every_assignment_files_the_picture_as_unfiled():
    assert relocate("2024 Shoots", {}, LAYOUT, VOCAB) == "Unassigned"


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
# reconcile_move - the mirror, for moves made outside PixlStash (v1.11 Phase 5)
# ---------------------------------------------------------------------------


def test_read_named_components_stops_at_the_first_unknown():
    assert read_named_components(["2024 Shoots", "random"], DEFAULT_LAYOUT, KNOWN) == [
        (Facet.PROJECT, "2024 Shoots")
    ]


def test_read_named_components_is_empty_for_the_owners_own_folder():
    assert read_named_components(["_unsorted"], DEFAULT_LAYOUT, KNOWN) == []


def test_read_named_components_refuses_to_guess_a_folder_name_collision():
    """Two entities that render to the same folder are unreadable, not a coin flip.

    ``folder_name`` is documented many-to-one (``Client: Nordvik`` and
    ``Client_ Nordvik`` both become ``Client_ Nordvik``). Silently picking
    whichever the vocabulary happened to list last would add a picture to a
    different project than the one whose folder it is actually in.
    """
    known = {
        Facet.PROJECT: ["Client: Nordvik", "Client_ Nordvik"],
        Facet.PERSON: [],
        Facet.SET: [],
    }
    assert read_named_components(["Client_ Nordvik"], DEFAULT_LAYOUT, known) == []


def test_a_folder_name_collision_is_off_layout_not_a_guess():
    known = {
        Facet.PROJECT: ["Client: Nordvik", "Client_ Nordvik"],
        Facet.PERSON: [],
        Facet.SET: [],
    }
    picture = facets(projects=["Client: Nordvik"])
    reconciled = reconcile_move(
        "Client: Nordvik", "Client_ Nordvik", picture, DEFAULT_LAYOUT, known
    )
    assert reconciled.outcome == MoveOutcome.OFF_LAYOUT
    assert reconciled.removals == ()
    assert reconciled.additions == ()


def test_a_third_colliding_name_does_not_undo_the_ambiguity_a_second_one_raised():
    """The collision flag is sticky: A, B, C sharing a key must not resolve to C."""
    # ":" and "|" are both in the unsafe-character class, so all three fold to
    # the same folder name "Client_ Nordvik" (space preserved, punctuation
    # replaced).
    known = {
        Facet.PROJECT: ["Client: Nordvik", "Client_ Nordvik", "Client| Nordvik"],
        Facet.PERSON: [],
        Facet.SET: [],
    }
    assert read_named_components(["Client_ Nordvik"], DEFAULT_LAYOUT, known) == []


def test_a_name_appearing_twice_identically_is_not_a_collision():
    known = {
        Facet.PROJECT: ["2024 Shoots", "2024 Shoots"],
        Facet.PERSON: [],
        Facet.SET: [],
    }
    assert read_named_components(["2024 Shoots"], DEFAULT_LAYOUT, known) == [
        (Facet.PROJECT, "2024 Shoots")
    ]


def test_a_project_swap_is_unambiguous_when_the_picture_had_exactly_one():
    picture = facets(projects=["2024 Shoots"])
    reconciled = reconcile_move(
        "2024 Shoots", "Client Nordvik", picture, DEFAULT_LAYOUT, KNOWN
    )
    assert reconciled.outcome == MoveOutcome.UNAMBIGUOUS
    assert reconciled.removals == ((Facet.PROJECT, "2024 Shoots"),)
    assert reconciled.additions == ((Facet.PROJECT, "Client Nordvik"),)


def test_leaving_a_shared_project_folder_is_ambiguous():
    """A folder holds a picture once; a project can share it (DECISIONS.md)."""
    picture = facets(projects=["2024 Shoots", "Client Nordvik"])
    reconciled = reconcile_move(
        "2024 Shoots", "Client Nordvik", picture, DEFAULT_LAYOUT, KNOWN
    )
    assert reconciled.outcome == MoveOutcome.AMBIGUOUS
    assert reconciled.removals == ((Facet.PROJECT, "2024 Shoots"),)
    # Already a member of "Client Nordvik" - nothing new to add.
    assert reconciled.additions == ()


def test_gaining_a_person_the_picture_never_had_is_unambiguous():
    """An addition is never ambiguous: it cannot make any folder untrue."""
    picture = facets(projects=["2024 Shoots"])
    reconciled = reconcile_move(
        "2024 Shoots", "2024 Shoots/Mira", picture, DEFAULT_LAYOUT, KNOWN
    )
    assert reconciled.outcome == MoveOutcome.UNAMBIGUOUS
    assert reconciled.removals == ()
    assert reconciled.additions == ((Facet.PERSON, "Mira"),)


def test_a_folder_naming_nothing_known_is_off_layout_and_touches_nothing():
    picture = facets(projects=["2024 Shoots"])
    reconciled = reconcile_move(
        "2024 Shoots", "_unsorted", picture, DEFAULT_LAYOUT, KNOWN
    )
    assert reconciled.outcome == MoveOutcome.OFF_LAYOUT
    assert reconciled.removals == ()
    assert reconciled.additions == ()


def test_landing_at_the_root_is_off_layout():
    picture = facets(projects=["2024 Shoots"])
    reconciled = reconcile_move("2024 Shoots", "", picture, DEFAULT_LAYOUT, KNOWN)
    assert reconciled.outcome == MoveOutcome.OFF_LAYOUT


def test_only_the_owners_own_subfolder_changing_reconciles_to_nothing():
    picture = facets(projects=["2024 Shoots"], people=["Mira"])
    reconciled = reconcile_move(
        "2024 Shoots/Mira/2026-08",
        "2024 Shoots/Mira/2026-09",
        picture,
        DEFAULT_LAYOUT,
        KNOWN,
    )
    assert reconciled.outcome == MoveOutcome.NONE
    assert reconciled.removals == ()
    assert reconciled.additions == ()


def test_arriving_at_the_unfiled_folder_is_a_deliberate_unambiguous_removal():
    picture = facets(projects=["2024 Shoots"])
    reconciled = reconcile_move(
        "2024 Shoots", DEFAULT_LAYOUT.unfiled, picture, DEFAULT_LAYOUT, KNOWN
    )
    assert reconciled.outcome == MoveOutcome.UNAMBIGUOUS
    assert reconciled.removals == ((Facet.PROJECT, "2024 Shoots"),)
    assert reconciled.additions == ()


def test_a_cross_facet_swap_within_one_segment_reconciles_both_sides():
    """Person and Set share a segment; a folder can name either."""
    picture = facets(sets=["mira-lora-v3"])
    reconciled = reconcile_move("mira-lora-v3", "Mira", picture, DEFAULT_LAYOUT, KNOWN)
    assert reconciled.outcome == MoveOutcome.UNAMBIGUOUS
    assert reconciled.removals == ((Facet.SET, "mira-lora-v3"),)
    assert reconciled.additions == ((Facet.PERSON, "Mira"),)


# ---------------------------------------------------------------------------
# The engine, against a real folder tree
# ---------------------------------------------------------------------------


def _require_symlinks(tmp_path, *, directory: bool):
    """Skip unless this host can actually create the kind of link the case needs.

    The house pattern (`tests/test_views_links.py`): probe rather than test
    `os.name`, so the case still runs on a Windows host with Developer Mode or
    admin rights, and skips with the real `OSError` on one without. Both hazards
    below need a link to *exist* to be reachable at all, so a host that cannot
    make one is not exposed to them - failing there would report a platform
    limitation as a bug in the engine.
    """
    probe = tmp_path / "symlink-probe"
    try:
        os.symlink(
            str(tmp_path if directory else __file__),
            str(probe),
            target_is_directory=directory,
        )
    except OSError as exc:
        kind = "directory" if directory else "file"
        pytest.skip(f"this host cannot create a {kind} symlink: {exc}")
    os.remove(probe)


@pytest.fixture
def library(tmp_path):
    """A library root with a layout, one project, one person, and one picture.

    The picture is at ``2024 Shoots/Mira/2026-08/0412.png`` - the design
    bundle's own example, so the tail-preservation assertions are the drawn
    case rather than an invented one.
    """
    root = tmp_path / "Generations"
    # ``as_posix`` so the URL carries forward slashes on Windows too: a raw
    # ``C:\\...\\vault.db`` inside a ``sqlite:///`` URL is a backslash escape
    # sequence waiting to be read as one.
    engine_ = create_engine(f"sqlite:///{(tmp_path / 'vault.db').as_posix()}")
    SQLModel.metadata.create_all(engine_)
    try:
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
            session.add(
                PictureProjectMember(picture_id=picture.id, project_id=project.id)
            )
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

    finally:
        # Windows will not delete a file another handle still has open, and
        # ``tmp_path`` cleanup is what would hit it. Disposing the pool is also
        # the rule this repo learned the hard way: anything that used to die
        # with a per-test engine leaks once the engine outlives the test body.
        engine_.dispose()


def _swap_project(library):
    """Take the picture out of the project its folder is named after."""
    session = library["session"]
    # Scoped to this picture. An unfiltered delete works today only because the
    # fixture has one membership row, and would quietly remove another
    # picture's the moment the fixture grew - which is how a test starts passing
    # for the wrong reason.
    for member in session.exec(
        select(PictureProjectMember).where(
            PictureProjectMember.picture_id == library["picture_id"]
        )
    ).all():
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
    # The stored form is ``/``-separated on every platform. This one only bites
    # on Windows, which is why the file is on the OS-sensitive list: everything
    # that reads a library picture's path - the thumbnail sibling, the layout's
    # own component split, the grid's URL - assumes forward slashes, and
    # ``os.path.relpath`` hands back backslashes there.
    assert "\\" not in picture.file_path


def test_the_thumbnail_follows_the_file(library):
    """A library picture's thumbnail is keyed by its RELATIVE stored path, and
    the mover has to hand ``get_thumbnail_path`` exactly that form: the name is
    a hash of the string. Handing it the absolute path would look for a name
    nobody wrote, find nothing, blank the stored dimensions and strand a bitmap
    nothing ever collects."""
    from pixlstash.utils.image_processing.image_utils import ImageUtils

    session, root = library["session"], library["root"]
    picture = session.get(Picture, library["picture_id"])
    old_thumb = ImageUtils.get_thumbnail_path(root, picture.file_path)
    os.makedirs(os.path.dirname(old_thumb), exist_ok=True)
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
    assert "\\" not in picture.file_path, "a rename must not native-ise the path"

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
    _require_symlinks(tmp_path, directory=False)
    session, root = library["session"], library["root"]
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not the library's")
    source = os.path.join(root, "2024 Shoots", "Mira", "2026-08", "0412.png")
    os.unlink(source)
    os.symlink(str(outside), source)
    _swap_project(library)

    plan, skipped = engine.plan_moves(session, [library["picture_id"]], root)
    assert plan == []
    assert skipped == [(library["picture_id"], "source_is_symlink")]
    assert outside.read_bytes() == b"not the library's"


def test_a_symlinked_destination_folder_is_refused(library, tmp_path):
    """The source is not the only path a move can escape through.

    The rendered folder names cannot escape lexically - ``folder_name`` strips
    every separator - but a directory that already exists inside the root can be
    a symlink, and ``os.makedirs(exist_ok=True)`` follows one. Without the
    check the file would be written outside the library while the row went on
    naming a path inside it.
    """
    _require_symlinks(tmp_path, directory=True)
    session, root = library["session"], library["root"]
    outside = tmp_path / "another-volume"
    outside.mkdir()
    os.symlink(
        str(outside), os.path.join(root, "Client · Nordvik"), target_is_directory=True
    )
    _swap_project(library)

    plan, skipped = engine.plan_moves(session, [library["picture_id"]], root)
    assert plan == []
    assert skipped == [(library["picture_id"], "destination_outside_root")]
    assert list(outside.iterdir()) == [], "nothing was written outside the library"
    assert os.path.isfile(
        os.path.join(root, "2024 Shoots", "Mira", "2026-08", "0412.png")
    )


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
    assert engine.placement_subfolder(session, library["root"]) == "Unassigned"


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
    assert plan[0].stored_path == "Unassigned/b.png"


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
    ``second >= first`` is true of a marker that never writes at all - it passed
    with the whole re-stamp deleted - and two commits a microsecond apart make
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
    """How an unfiled drop-to-person import leaves ``Unassigned`` on its own."""
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

    Everything after ``apply_moves`` can raise - two state captures, the
    operation row, the flag clear, the commit - and the writer thread then rolls
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
    os.makedirs(os.path.dirname(old_thumb), exist_ok=True)
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
    second move to undo it - two file operations on the owner's disk for a
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
    # engine has nothing to do - which is what makes the assertion at the end
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


# ---------------------------------------------------------------------------
# move_reconciliation_service - classifying and clearing the queue (Phase 5)
# ---------------------------------------------------------------------------


def _reference_folder(library, root="/library/refs", layout=DEFAULT_LAYOUT):
    session = library["session"]
    folder = ReferenceFolder(folder=root, layout=format_layout(layout))
    session.add(folder)
    session.commit()
    return folder.id, root


def _reference_picture(library, folder_id, path, *, project_ids=()):
    session = library["session"]
    project_ids = list(project_ids)
    pic = Picture(
        file_path=path,
        original_file_name=os.path.basename(path),
        reference_folder_id=folder_id,
        project_id=project_ids[0] if project_ids else None,
    )
    session.add(pic)
    session.commit()
    for project_id in project_ids:
        session.add(PictureProjectMember(picture_id=pic.id, project_id=project_id))
    session.commit()
    return pic.id


def test_an_unambiguous_move_is_bucketed_with_its_implied_swap(library):
    session = library["session"]
    folder_id, root = _reference_folder(library)
    old_path = f"{root}/2024 Shoots/a.png"
    new_path = f"{root}/Client · Nordvik/a.png"
    picture_id = _reference_picture(
        library, folder_id, new_path, project_ids=[library["project_id"]]
    )
    reconciliation.record_pending_reviews(session, [(picture_id, old_path, new_path)])
    session.commit()

    summary = reconciliation.pending_summary_in_session(session)
    assert summary["ambiguous"] == []
    assert summary["off_layout"] == []
    assert len(summary["unambiguous"]) == 1
    item = summary["unambiguous"][0]
    assert item["picture_id"] == picture_id
    assert item["removals"] == [{"facet": "project", "name": "2024 Shoots"}]
    assert item["additions"] == [{"facet": "project", "name": "Client · Nordvik"}]

    # Still there: a GET must not consume a row nobody acted on.
    assert session.exec(
        select(ExternalMoveReview).where(ExternalMoveReview.picture_id == picture_id)
    ).first()


def test_leaving_a_shared_project_is_bucketed_ambiguous(library):
    session = library["session"]
    folder_id, root = _reference_folder(library)
    old_path = f"{root}/2024 Shoots/a.png"
    new_path = f"{root}/Client · Nordvik/a.png"
    picture_id = _reference_picture(
        library,
        folder_id,
        new_path,
        project_ids=[library["project_id"], library["other_project_id"]],
    )
    reconciliation.record_pending_reviews(session, [(picture_id, old_path, new_path)])
    session.commit()

    summary = reconciliation.pending_summary_in_session(session)
    assert summary["unambiguous"] == []
    assert len(summary["ambiguous"]) == 1
    item = summary["ambiguous"][0]
    assert item["removals"] == [{"facet": "project", "name": "2024 Shoots"}]
    assert item["current"] == {"project": ["2024 Shoots", "Client · Nordvik"]}


def test_a_move_to_an_unknown_folder_is_bucketed_off_layout(library):
    session = library["session"]
    folder_id, root = _reference_folder(library)
    old_path = f"{root}/2024 Shoots/a.png"
    new_path = f"{root}/_unsorted/a.png"
    picture_id = _reference_picture(
        library, folder_id, new_path, project_ids=[library["project_id"]]
    )
    reconciliation.record_pending_reviews(session, [(picture_id, old_path, new_path)])
    session.commit()

    summary = reconciliation.pending_summary_in_session(session)
    assert summary["unambiguous"] == []
    assert summary["ambiguous"] == []
    assert len(summary["off_layout"]) == 1
    assert summary["off_layout"][0]["removals"] == []
    assert summary["off_layout"][0]["additions"] == []


def test_an_off_layout_row_is_pruned_past_its_retention_window(library):
    """off_layout carries no decision, so it is shown once and then ages out.

    Unlike unambiguous/ambiguous, nothing here waits on the owner - the
    row exists so the screen can say "already followed, nothing to decide"
    at least once, not so it can sit forever as unreachable, unclearable
    state (see docs/backend_architecture.md §27).
    """
    from datetime import datetime, timedelta

    from pixlstash.db_models.picture_move import RETENTION_S

    session = library["session"]
    folder_id, root = _reference_folder(library)
    picture_id = _reference_picture(
        library,
        folder_id,
        f"{root}/_unsorted/a.png",
        project_ids=[library["project_id"]],
    )
    session.add(
        ExternalMoveReview(
            picture_id=picture_id,
            old_path=f"{root}/2024 Shoots/a.png",
            new_path=f"{root}/_unsorted/a.png",
            detected_at=datetime.utcnow() - timedelta(seconds=RETENTION_S * 2),
        )
    )
    session.commit()

    summary = reconciliation.pending_summary_in_session(session)
    assert summary["off_layout"] == []
    assert (
        session.exec(
            select(ExternalMoveReview).where(
                ExternalMoveReview.picture_id == picture_id
            )
        ).first()
        is None
    )


def test_a_move_that_reconciles_to_nothing_is_pruned_from_the_queue(library):
    session = library["session"]
    folder_id, root = _reference_folder(library)
    old_path = f"{root}/2024 Shoots/2026-08/a.png"
    new_path = f"{root}/2024 Shoots/2026-09/a.png"
    picture_id = _reference_picture(
        library, folder_id, new_path, project_ids=[library["project_id"]]
    )
    reconciliation.record_pending_reviews(session, [(picture_id, old_path, new_path)])
    session.commit()

    summary = reconciliation.pending_summary_in_session(session)
    assert summary == {"unambiguous": [], "ambiguous": [], "off_layout": []}
    assert (
        session.exec(
            select(ExternalMoveReview).where(
                ExternalMoveReview.picture_id == picture_id
            )
        ).first()
        is None
    ), "a row implying nothing must not sit in the queue forever"


def test_a_deleted_pictures_row_is_dropped_rather_than_crashing(library):
    session = library["session"]
    folder_id, root = _reference_folder(library)
    reconciliation.record_pending_reviews(
        session, [(999999, f"{root}/2024 Shoots/a.png", f"{root}/_unsorted/a.png")]
    )
    session.commit()

    assert reconciliation.pending_summary_in_session(session) == {
        "unambiguous": [],
        "ambiguous": [],
        "off_layout": [],
    }


# ---------------------------------------------------------------------------
# The appliers - set and person, the two facets pending_summary never exercises
# ---------------------------------------------------------------------------


def test_add_person_prefers_an_unassigned_face_and_never_steals_one(library):
    """An addition is supposed to be safe. Stealing Sara's face is not."""
    from pixlstash.services.move_reconciliation_service import _add_person

    session = library["session"]
    sara = Character(name="Sara (steal-test)")
    mira = Character(name="Mira (steal-test)")
    session.add_all([sara, mira])
    session.commit()

    picture = Picture(file_path="steal-test.png", original_file_name="steal-test.png")
    session.add(picture)
    session.commit()
    sara_face = Face(picture_id=picture.id, character_id=sara.id, face_index=0)
    free_face = Face(picture_id=picture.id, character_id=None, face_index=1)
    session.add_all([sara_face, free_face])
    session.commit()

    changed = _add_person(session, picture, mira.id)
    session.commit()

    assert changed is True
    session.refresh(sara_face)
    session.refresh(free_face)
    assert sara_face.character_id == sara.id, "Sara's face must survive an addition"
    assert free_face.character_id == mira.id


def test_add_person_is_a_safe_no_op_when_every_face_already_names_someone(library):
    from pixlstash.services.move_reconciliation_service import _add_person

    session = library["session"]
    sara = Character(name="Sara (no-room)")
    mira = Character(name="Mira (no-room)")
    session.add_all([sara, mira])
    session.commit()

    picture = Picture(file_path="no-room.png", original_file_name="no-room.png")
    session.add(picture)
    session.commit()
    session.add(Face(picture_id=picture.id, character_id=sara.id))
    session.commit()

    assert _add_person(session, picture, mira.id) is False
    faces = session.exec(select(Face).where(Face.picture_id == picture.id)).all()
    assert [f.character_id for f in faces] == [sara.id]


def test_remove_person_clears_only_that_characters_face(library):
    from pixlstash.services.move_reconciliation_service import _remove_person

    session = library["session"]
    sara = Character(name="Sara (remove-test)")
    mira = Character(name="Mira (remove-test)")
    session.add_all([sara, mira])
    session.commit()

    picture = Picture(file_path="remove-test.png", original_file_name="remove-test.png")
    session.add(picture)
    session.commit()
    sara_face = Face(picture_id=picture.id, character_id=sara.id, face_index=0)
    mira_face = Face(picture_id=picture.id, character_id=mira.id, face_index=1)
    session.add_all([sara_face, mira_face])
    session.commit()

    assert _remove_person(session, picture, sara.id) is True
    session.commit()
    session.refresh(sara_face)
    session.refresh(mira_face)
    assert sara_face.character_id is None
    assert mira_face.character_id == mira.id, "removing Sara must not touch Mira"


def test_add_and_remove_set_membership(library):
    from pixlstash.db_models import PictureSetMember
    from pixlstash.services.move_reconciliation_service import _add_set, _remove_set

    session = library["session"]
    picture_set = PictureSet(name="Summer (add-remove-test)")
    session.add(picture_set)
    session.commit()

    picture = session.get(Picture, library["picture_id"])
    assert _add_set(session, picture, picture_set.id) is True
    session.commit()
    assert _add_set(session, picture, picture_set.id) is False, (
        "adding twice is a no-op"
    )

    assert _remove_set(session, picture, picture_set.id) is True
    session.commit()
    members = session.exec(
        select(PictureSetMember).where(
            PictureSetMember.picture_id == picture.id,
            PictureSetMember.set_id == picture_set.id,
        )
    ).all()
    assert members == []


# ---------------------------------------------------------------------------
# Phase 4c: moving an existing library onto its layout
#
# The one gesture in this release that deliberately moves everything, and the
# reason it needs its own cases is that it is NOT the move-when-false rule: a
# flat library parses against nothing, is never false, and is exactly what the
# rule leaves alone. What follows is therefore mostly the cases the rule has no
# opinion on at all.
# ---------------------------------------------------------------------------


def test_the_migration_files_a_flat_library_the_rule_would_never_touch():
    # The headline case. ``relocate`` says None here - correctly, under the
    # rule - and the migration says where it goes.
    assert relocate("", facets(projects=["2024 Shoots"]), DEFAULT_LAYOUT, KNOWN) is None
    assert (
        migrate_destination("", facets(projects=["2024 Shoots"]), DEFAULT_LAYOUT)
        == "2024 Shoots"
    )


def test_the_migration_leaves_a_picture_the_layout_cannot_place():
    # Nothing files it, so ``render`` would answer ``Unassigned``. Sweeping it
    # there is opt-in, not the default.
    assert migrate_destination("", facets(), DEFAULT_LAYOUT) is None
    # ...from wherever it is. A date folder is not a reason to move a picture
    # the layout has no name for.
    assert migrate_destination("2024/2024-08-15", facets(), DEFAULT_LAYOUT) is None


def test_the_sweep_puts_the_unplaceable_in_the_unfiled_folder():
    # Asked for, every picture nothing files lands in one folder, flattened
    # like everything else; one already there stays.
    for folder in ("", "2024/2024-08-15", f"{DEFAULT_LAYOUT.unfiled}/2024"):
        assert (
            migrate_destination(folder, facets(), DEFAULT_LAYOUT, sweep_unfiled=True)
            == DEFAULT_LAYOUT.unfiled
        )
    assert (
        migrate_destination(
            DEFAULT_LAYOUT.unfiled, facets(), DEFAULT_LAYOUT, sweep_unfiled=True
        )
        is None
    )
    # And it changes nothing for a picture the layout can place.
    assert (
        migrate_destination(
            "", facets(projects=["2024 Shoots"]), DEFAULT_LAYOUT, sweep_unfiled=True
        )
        == "2024 Shoots"
    )


def test_the_migration_flattens_a_folder_of_the_owners_own():
    """The migration is the one gesture where a folder of the owner's own is not
    an override. A dated library - what most libraries are before they have a
    layout - would otherwise migrate nothing at all, since none of ``2024/
    2024-08-15`` names anything the layout knows. ``match_destination`` keeps
    refusing the same folders: the rule never sweeps them, the button does."""
    filed = facets(projects=["2024 Shoots"])
    for folder in ("_unsorted", "2024/2024-08-15", "Archive/2019/Weddings"):
        assert migrate_destination(folder, filed, DEFAULT_LAYOUT) == "2024 Shoots"
        assert match_destination(folder, filed, DEFAULT_LAYOUT, KNOWN) is None
    # The automatic rule still leaves them alone.
    assert relocate("2024/2024-08-15", filed, DEFAULT_LAYOUT, KNOWN) is None


def test_the_migration_leaves_a_picture_already_where_the_layout_wants_it():
    assert (
        migrate_destination(
            "2024 Shoots/Mira",
            facets(projects=["2024 Shoots"], people=["Mira"]),
            DEFAULT_LAYOUT,
        )
        is None
    )


def test_the_migration_flattens_the_owners_own_subfolders_too():
    """Unlike ``relocate``, which carries ``2026-08`` across. The migration
    puts every picture exactly where ``render`` says, and a date tail was the
    arrangement the layout replaces; two files of one name meeting is what the
    suffix rule is for."""
    assert (
        migrate_destination(
            "2024 Shoots/Mira/2026-08",
            facets(projects=["Client Nordvik"], people=["Mira"]),
            DEFAULT_LAYOUT,
        )
        == "Client Nordvik/Mira"
    )
    assert (
        relocate(
            "2024 Shoots/Mira/2026-08",
            facets(projects=["Client Nordvik"], people=["Mira"]),
            DEFAULT_LAYOUT,
            KNOWN,
        )
        == "Client Nordvik/Mira/2026-08"
    )
    # Even when the prefix is already right: a subfolder below it is not where
    # the layout puts the picture.
    assert (
        migrate_destination(
            "Client Nordvik/Mira/raw",
            facets(projects=["Client Nordvik"], people=["Mira"]),
            DEFAULT_LAYOUT,
        )
        == "Client Nordvik/Mira"
    )


def test_the_unfiled_folder_is_not_a_level_the_migration_nests_under():
    filed = facets(projects=["2024 Shoots"])
    assert migrate_destination(DEFAULT_LAYOUT.unfiled, filed, DEFAULT_LAYOUT) == (
        "2024 Shoots"
    )
    # And at depth: nothing of the old path survives, ``Unassigned`` included, so
    # no permanent ``Unassigned`` is ever left inside a project folder.
    assert (
        migrate_destination(f"{DEFAULT_LAYOUT.unfiled}/2026-08", filed, DEFAULT_LAYOUT)
        == "2024 Shoots"
    )


def test_a_path_the_layout_cannot_read_is_not_migrated_either():
    # Refused whole rather than tidied up, exactly as ``is_true`` refuses it.
    # Rewriting the prefix would silently drop the tail it could not parse.
    assert (
        migrate_destination(
            "2024 Shoots/../Mira", facets(projects=["2024 Shoots"]), DEFAULT_LAYOUT
        )
        is None
    )


def _plant(library, relative_path, *, filed=True):
    """Put one more picture on the disk and in the database. Returns its id."""
    session, root = library["session"], library["root"]
    absolute = os.path.join(root, *relative_path.split("/"))
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    with open(absolute, "wb") as handle:
        handle.write(relative_path.encode())
    picture = Picture(
        file_path=relative_path,
        original_file_name=os.path.basename(relative_path),
        project_id=library["project_id"] if filed else None,
    )
    session.add(picture)
    session.commit()
    if filed:
        session.add(
            PictureProjectMember(
                picture_id=picture.id, project_id=library["project_id"]
            )
        )
        session.add(Face(picture_id=picture.id, character_id=library["person_id"]))
        session.commit()
    return picture.id


def _settled(library):
    """Put the fixture's picture where the migration would put it.

    The fixture keeps it under the owner's own ``2026-08`` folder for the
    rule's tail-carrying tests; the migration flattens that folder, so a test
    counting *other* moves settles it first.
    """
    session, root = library["session"], library["root"]
    old = os.path.join(root, "2024 Shoots", "Mira", "2026-08", "0412.png")
    new = os.path.join(root, "2024 Shoots", "Mira", "0412.png")
    os.replace(old, new)
    picture = session.get(Picture, library["picture_id"])
    picture.file_path = "2024 Shoots/Mira/0412.png"
    session.add(picture)
    session.commit()


def test_the_migration_plans_a_flat_library_and_leaves_the_unfilable(library):
    session, root = library["session"], library["root"]
    _settled(library)
    filed = _plant(library, "0001.png")
    unfilable = _plant(library, "0002.png", filed=False)

    plan, skipped, examined, last_id = migration.plan_migration(session, root)

    assert skipped == []
    assert examined == 3  # the fixture's picture plus these two
    assert last_id == max(filed, unfilable)
    # The fixture's own picture is already where the layout wants it, and the
    # unfilable one stays put, so exactly one move is planned.
    assert [move.picture_id for move in plan] == [filed]
    assert plan[0].stored_path == "2024 Shoots/Mira/0001.png"


def test_a_collision_is_suffixed_and_never_overwrites_the_file_already_there(library):
    session, root = library["session"], library["root"]
    first = _plant(library, "a/0001.png")
    second = _plant(library, "b/0001.png")

    # ``a/`` and ``b/`` are folders of the owner's own, and the migration
    # flattens them, so two files of one name arrive at one folder: the second
    # is suffixed in the plan, not refused.
    plan, skipped, _examined, _last = migration.plan_migration(session, root)
    assert skipped == []
    moved = {move.picture_id: move.stored_path for move in plan}
    assert moved[first] == "2024 Shoots/Mira/0001.png"
    assert moved[second] == "2024 Shoots/Mira/0001-2.png"

    # And a file already sitting at the destination is never the one renamed.
    third = _plant(library, "0001.png")
    fourth_path = os.path.join(root, "2024 Shoots", "Mira", "0001.png")
    os.makedirs(os.path.dirname(fourth_path), exist_ok=True)
    with open(fourth_path, "wb") as handle:
        handle.write(b"somebody else's file")

    plan, _skipped, _examined, _last = migration.plan_migration(session, root)
    moved = {move.picture_id: move.stored_path for move in plan}
    assert moved[first] == "2024 Shoots/Mira/0001-2.png"
    assert moved[second] == "2024 Shoots/Mira/0001-3.png"
    assert moved[third] == "2024 Shoots/Mira/0001-4.png"

    migration.apply_moves(session, plan, image_root=root)
    session.commit()
    # The file that was already sitting there is untouched, which is the whole
    # point of suffixing the file being moved rather than the one in the way.
    with open(fourth_path, "rb") as handle:
        assert handle.read() == b"somebody else's file"
    for name in ("0001-2.png", "0001-3.png", "0001-4.png"):
        assert os.path.isfile(os.path.join(root, "2024 Shoots", "Mira", name))


def test_the_automatic_path_still_refuses_a_taken_destination(library):
    """``uniquify`` is opt-in: the rule's own moves must not start renaming."""
    session, root = library["session"], library["root"]
    _swap_project(library)
    taken = os.path.join(root, "Client · Nordvik", "Mira", "2026-08", "0412.png")
    os.makedirs(os.path.dirname(taken), exist_ok=True)
    with open(taken, "wb") as handle:
        handle.write(b"in the way")

    plan, skipped = engine.plan_moves(session, [library["picture_id"]], root)
    assert plan == []
    assert skipped == [(library["picture_id"], "destination_taken")]


def test_the_migration_is_resumable_by_id_and_idempotent_when_rerun(library):
    session, root = library["session"], library["root"]
    _settled(library)
    first = _plant(library, "0001.png")
    second = _plant(library, "0002.png")

    # One picture per pass, exactly as the route's windows work.
    cursor, moved = 0, []
    for _ in range(4):
        plan, _skipped, examined, last_id = migration.plan_migration(
            session, root, after_id=cursor, limit=1
        )
        moved.extend(migration.apply_moves(session, plan, image_root=root))
        session.commit()
        if examined < 1:
            break
        cursor = last_id
    assert sorted(moved) == sorted([first, second])

    # Re-running finishes nothing because there is nothing left: a picture
    # already where the layout wants it plans no move. That is the resumability
    # story - no checkpoint table, just an idempotent plan.
    plan, skipped, _examined, _last = migration.plan_migration(session, root)
    assert (plan, skipped) == ([], [])


def test_one_undo_puts_every_migrated_file_back(library):
    session, root = library["session"], library["root"]
    first = _plant(library, "0001.png")
    second = _plant(library, "sub/0002.png")

    plan, _skipped, _examined, _last = migration.plan_migration(session, root)
    targets = [move.picture_id for move in plan]
    before = capture_state_in_session(session, targets)
    migration.apply_moves(session, plan, image_root=root)
    after = capture_state_in_session(session, targets)
    operation = record_operation_in_session(
        session,
        op_type=engine.OP_LAYOUT_MOVE,
        before=before,
        after=after,
        batch_id=migration.new_batch_id(),
        commit=False,
    )
    session.commit()
    assert operation is not None
    assert os.path.isfile(os.path.join(root, "2024 Shoots", "Mira", "0001.png"))

    import json

    apply_state_in_session(
        session, json.loads(operation.before_state), "undo", image_root=root
    )
    session.commit()
    assert session.get(Picture, first).file_path == "0001.png"
    assert session.get(Picture, second).file_path == "sub/0002.png"
    assert os.path.isfile(os.path.join(root, "0001.png"))
    assert os.path.isfile(os.path.join(root, "sub", "0002.png"))


def test_a_migration_batch_id_is_one_this_route_would_accept_back():
    """Minted here, validated there. The two must not drift apart, or a client
    echoing the id it was given loses the grouping that makes the run one undo."""
    assert _MIGRATION_BATCH_ID_RE.match(migration.new_batch_id())
    assert not _MIGRATION_BATCH_ID_RE.match("cli-anything")


def test_a_destination_on_another_volume_is_refused_rather_than_attempted(
    library, monkeypatch
):
    """A mount point inside the library is not a slow move, it is no move:
    ``publish_no_clobber`` claims the name with ``os.link`` and then
    ``os.replace``, and both raise EXDEV. So it has to leave the plan, or the
    file silently stays put while the pass reports a clean finish.

    Forced through ``same_device``, which exists as its own function for exactly
    this - `tmp_path` puts both directories on one filesystem on every machine
    this suite runs on, so the real branch is unreachable here.
    """
    session, root = library["session"], library["root"]
    _settled(library)
    moving = _plant(library, "0001.png")

    plan, skipped, _examined, _last = migration.plan_migration(session, root)
    assert [move.picture_id for move in plan] == [moving] and skipped == []

    monkeypatch.setattr(migration, "same_device", lambda source, target: False)
    plan, skipped, _examined, _last = migration.plan_migration(session, root)
    assert plan == []
    assert skipped == [(moving, "destination_other_volume")]
    # The claim it took while planning is given back, so a later picture of the
    # same name is not refused for a move that is never going to happen.
    assert not os.path.exists(os.path.join(root, "2024 Shoots", "Mira", "0001.png"))

    preview = migration.preview_in_session(session, root)
    assert preview["picture_count"] == 0
    assert preview["cross_volume_count"] == 1
    assert preview["skipped_counts"] == {"destination_other_volume": 1}


def test_the_device_probe_reads_a_folder_that_does_not_exist_yet(library):
    """The destination folder the migration is about to create has no device of
    its own; the one it will get is its nearest existing ancestor's."""
    root = library["root"]
    assert migration._nearest_existing(
        os.path.join(root, "not", "yet", "created")
    ) == os.path.abspath(root)


def test_the_preview_counts_the_move_without_making_it(library):
    session, root = library["session"], library["root"]
    _settled(library)
    filed = _plant(library, "0001.png")
    _plant(library, "0002.png", filed=False)

    preview = migration.preview_in_session(session, root)

    assert preview["layout"] == format_layout(DEFAULT_LAYOUT)
    # The fixture's picture is already in place and the unfiled one stays put.
    assert preview["picture_count"] == 1
    assert preview["folder_count"] == 1
    assert preview["samples"] == [
        {"picture_id": filed, "from": "0001.png", "to": "2024 Shoots/Mira/0001.png"}
    ]
    assert preview["collision_count"] == 0
    assert preview["cross_volume_count"] == 0
    assert preview["skipped_counts"] == {}
    # Nothing moved: it is a count, and the file is still where it was.
    assert os.path.isfile(os.path.join(root, "0001.png"))


def test_the_sweep_is_previewed_and_run_with_the_same_flag(library):
    session, root = library["session"], library["root"]
    _settled(library)
    loose = _plant(library, "2024/0002.png", filed=False)

    assert migration.preview_in_session(session, root)["picture_count"] == 0
    preview = migration.preview_in_session(session, root, sweep_unfiled=True)
    assert preview["picture_count"] == 1
    assert preview["samples"] == [
        {"picture_id": loose, "from": "2024/0002.png", "to": "Unassigned/0002.png"}
    ]
    assert [row["path"] for row in preview["tree"] if row["is_new"]] == ["Unassigned"]

    result = migration.run_migration_pass(
        _StubVault(session, root), after_id=0, sweep_unfiled=True
    )
    assert result["moved_picture_ids"] == [loose]
    assert session.get(Picture, loose).file_path == "Unassigned/0002.png"
    assert os.path.isfile(os.path.join(root, "Unassigned", "0002.png"))


def test_the_preview_counts_a_collision_and_names_the_suffixed_path(library):
    session, root = library["session"], library["root"]
    _plant(library, "0001.png")
    taken = os.path.join(root, "2024 Shoots", "Mira", "0001.png")
    os.makedirs(os.path.dirname(taken), exist_ok=True)
    with open(taken, "wb") as handle:
        handle.write(b"somebody else's file")

    preview = migration.preview_in_session(session, root)
    assert preview["collision_count"] == 1
    assert preview["collisions"][0]["to"] == "2024 Shoots/Mira/0001-2.png"


def test_the_preview_of_a_library_with_no_layout_is_zero_rather_than_absent(library):
    session, root = library["session"], library["root"]
    settings = session.exec(select(LibrarySettings)).first()
    settings.layout = None
    session.add(settings)
    session.commit()

    preview = migration.preview_in_session(session, root)
    assert preview["layout"] is None
    # Every cost reads as a number, because the screen reads them all
    # unconditionally and a missing key is a rendering bug, not a state.
    assert preview["picture_count"] == 0
    assert preview["cross_volume_count"] == 0
    assert preview["skipped_counts"] == {}
    assert preview["tree"] == []


def _drawable_library(library):
    """A tree with something of every kind in it, for the preview's ``tree``.

    Five folders end up worth a row, and the two that do not are the point as
    much as the five: the library root, which is never a row of its own, and
    ``2024 Shoots``, which holds no picture and receives none because the layout
    only ever puts things one level below it.
    """
    session = library["session"]
    # The fixture's own picture leaves 2024 Shoots/Mira/2026-08 for the other
    # project. The migration flattens, so the owner's own 2026-08 tail is not
    # carried: it lands in Client · Nordvik/Mira.
    _swap_project(library)
    _plant(library, "0001.png")  # root -> 2024 Shoots/Mira
    _plant(library, "0002.png", filed=False)  # nothing files it, so it stays
    # An owner's own folder, flattened too, and named so it sorts ahead of
    # every busier folder: a cap that ordered by path would keep it.
    _plant(library, "00 Loose/0003.png")  # -> 2024 Shoots/Mira

    # A person with no folder on disk yet, so one row comes back is_new.
    nova = Character(name="Nova")
    session.add(nova)
    session.commit()
    with_nova = _plant(library, "0005.png", filed=False)
    picture = session.get(Picture, with_nova)
    picture.project_id = library["project_id"]
    session.add(picture)
    session.add(
        PictureProjectMember(picture_id=with_nova, project_id=library["project_id"])
    )
    session.add(Face(picture_id=with_nova, character_id=nova.id))
    session.commit()


def test_the_preview_draws_the_tree_the_layout_would_make(library):
    session, root = library["session"], library["root"]
    _drawable_library(library)

    tree = migration.preview_in_session(session, root)["tree"]

    assert tree == [
        {
            "path": "00 Loose",
            "name": "00 Loose",
            "depth": 0,
            "have": 1,
            "arriving": 0,
            "leaving": 1,
            "is_new": False,
        },
        {
            "path": "2024 Shoots/Mira",
            "name": "Mira",
            "depth": 1,
            "have": 0,
            "arriving": 2,
            "leaving": 0,
            "is_new": False,
        },
        {
            "path": "2024 Shoots/Mira/2026-08",
            "name": "2026-08",
            "depth": 2,
            "have": 1,
            "arriving": 0,
            "leaving": 1,
            "is_new": False,
        },
        {
            "path": "2024 Shoots/Nova",
            "name": "Nova",
            "depth": 1,
            "have": 0,
            "arriving": 1,
            "leaving": 0,
            "is_new": True,
        },
        {
            "path": "Client · Nordvik/Mira",
            "name": "Mira",
            "depth": 1,
            "have": 0,
            "arriving": 1,
            "leaving": 0,
            "is_new": True,
        },
    ]


def test_the_tree_has_no_row_for_the_library_root(library):
    """Three pictures sit at the root and two of them leave it, and none of that
    is a row: the root is what every path is relative to, so a row for it would
    draw a level the owner does not have."""
    session, root = library["session"], library["root"]
    _drawable_library(library)

    tree = migration.preview_in_session(session, root)["tree"]
    assert all(entry["path"] for entry in tree)
    assert [entry["path"] for entry in tree] == sorted(
        entry["path"] for entry in tree
    ), "path order is what lets the screen indent on depth without re-sorting"


def test_the_tree_is_never_capped(library):
    """A tree of sixty rows and "...and 299 more folders" was the version before
    this one, and the 299 were the date folders the owner wanted to check."""
    session, root = library["session"], library["root"]
    _drawable_library(library)
    for n in range(80):
        _plant(library, f"2024/2024-08-{n:02d}/{n:04d}.png")

    preview = migration.preview_in_session(session, root)
    assert (
        len([row for row in preview["tree"] if row["path"].startswith("2024/")]) == 80
    )
    assert "tree_truncated" not in preview


def test_the_tree_of_an_empty_library_is_empty_rather_than_a_root_row(library):
    session, root = library["session"], library["root"]
    for picture in session.exec(select(Picture)).all():
        session.delete(picture)
    session.commit()

    preview = migration.preview_in_session(session, root)
    assert preview["tree"] == []


def test_a_suffixed_picture_carries_its_sidecar_under_the_same_suffix(library):
    """A sidecar pairs with its picture by stem, so the collision suffix has to
    reach it too - otherwise it lands beside a picture it no longer names, on
    top of the sidecar of whatever file was already sitting there."""
    session, root = library["session"], library["root"]
    moving = _plant(library, "0001.png")
    with open(os.path.join(root, "0001.txt"), "w") as handle:
        handle.write("a caption")
    session.get(Picture, moving).tags_file = os.path.join(root, "0001.txt")
    session.commit()

    # Somebody else's 0001.png is already where the layout would put ours, with
    # its own sidecar beside it.
    taken = os.path.join(root, "2024 Shoots", "Mira")
    os.makedirs(taken, exist_ok=True)
    for name, body in (("0001.png", b"theirs"), ("0001.txt", b"their caption")):
        with open(os.path.join(taken, name), "wb") as handle:
            handle.write(body)

    plan, _skipped, _examined, _last = migration.plan_migration(session, root)
    migration.apply_moves(session, plan, image_root=root)
    session.commit()

    assert session.get(Picture, moving).tags_file == os.path.join(taken, "0001-2.txt")
    assert os.path.isfile(os.path.join(taken, "0001-2.txt"))
    with open(os.path.join(taken, "0001.txt"), "rb") as handle:
        assert handle.read() == b"their caption"


def test_a_move_that_keeps_the_file_name_keeps_the_sidecar_name(library):
    """The 4b path is unchanged by the suffix logic above: same name, same
    sidecar name, only the folder moves."""
    session, root = library["session"], library["root"]
    folder = os.path.join(root, "2024 Shoots", "Mira", "2026-08")
    with open(os.path.join(folder, "0412.txt"), "w") as handle:
        handle.write("a caption")
    session.get(Picture, library["picture_id"]).tags_file = os.path.join(
        folder, "0412.txt"
    )
    session.commit()
    _swap_project(library)

    plan, _skipped = engine.plan_moves(session, [library["picture_id"]], root)
    engine.apply_moves(session, plan, image_root=root)
    session.commit()

    moved_to = os.path.join(root, "Client · Nordvik", "Mira", "2026-08", "0412.txt")
    assert session.get(Picture, library["picture_id"]).tags_file == moved_to
    assert os.path.isfile(moved_to)


class _StubVault:
    """Just enough vault for ``run_migration_pass``: a root and a writer.

    ``run_task`` runs the closure on this session rather than queueing it,
    which is the only reason this file can exercise the pass at all without a
    ``Server``. What is under test is the pass's own arithmetic — the cursor,
    ``done``, the batch grouping and what lands in ``skipped`` — none of which
    is about the queue.
    """

    def __init__(self, session, image_root):
        self.image_root = image_root
        self.db = self
        self._session = session

    def run_task(self, fn, *args, priority=None):
        return fn(self._session, *args)

    def run_immediate_read_task(self, fn, *args):
        return fn(self._session, *args)


def test_the_run_reports_a_cursor_and_only_says_done_at_the_end(library):
    """The mutation this covers: hardcoding ``done`` to True moved 200 pictures
    and told the client the library was migrated. Nothing else in the suite
    calls ``run_migration_pass`` on a library with pictures in it."""
    session, root = library["session"], library["root"]
    _settled(library)
    vault = _StubVault(session, root)
    planted = [_plant(library, f"{n:04d}.png") for n in range(1, 4)]

    # The window covers the fixture's own picture too, so the first pass sees
    # it and one of the planted ones.
    first = migration.run_migration_pass(vault, after_id=0, limit=2)
    assert first["done"] is False, "there are pictures behind the window"
    assert first["examined"] == 2
    assert first["next_after_id"] == planted[0]
    assert first["operation_id"] is not None

    moved = list(first["moved_picture_ids"])
    cursor, passes = first["next_after_id"], 1
    while True:
        result = migration.run_migration_pass(vault, after_id=cursor, limit=2)
        moved.extend(result["moved_picture_ids"])
        passes += 1
        assert result["next_after_id"] >= cursor, "the cursor must never go back"
        if result["done"]:
            break
        cursor = result["next_after_id"]
    assert passes > 1, "done must not be true on the first window"
    assert sorted(moved) == sorted(planted)
    for picture_id in planted:
        assert session.get(Picture, picture_id).file_path.startswith(
            "2024 Shoots/Mira/"
        )


def test_every_pass_of_one_migration_is_one_undo(library):
    """The acceptance criterion, and it needs *two* passes to mean anything: one
    `batch_id` over both, and undoing either reverts them together."""
    session, root = library["session"], library["root"]
    vault = _StubVault(session, root)
    planted = [_plant(library, f"{n:04d}.png") for n in range(1, 4)]
    batch_id = migration.new_batch_id()

    cursor, operations = 0, []
    while True:
        result = migration.run_migration_pass(
            vault, after_id=cursor, limit=2, batch_id=batch_id, source="ui"
        )
        if result["operation_id"] is not None:
            operations.append(result["operation_id"])
        if result["done"]:
            break
        cursor = result["next_after_id"]

    assert len(operations) == 2, "the point is that this is more than one row"
    rows = session.exec(select(Operation).where(Operation.id.in_(operations))).all()
    assert {row.batch_id for row in rows} == {batch_id}

    # Undoing the *later* one reverts the whole batch, which is what makes the
    # run a single Ctrl+Z rather than one per pass.
    undo_in_session(session, operations[-1], image_root=root)
    for picture_id in planted:
        picture = session.get(Picture, picture_id)
        assert picture.file_path == f"{planted.index(picture_id) + 1:04d}.png"
        assert os.path.isfile(os.path.join(root, picture.file_path))


def test_a_planned_file_that_could_not_be_moved_is_reported_not_dropped(
    library, monkeypatch
):
    """Without this the picture is in neither `moved_picture_ids` nor `skipped`,
    the pass still says `done`, and the only trace is a log line — a clean
    finish reported over a file that never moved."""
    session, root = library["session"], library["root"]
    _settled(library)
    vault = _StubVault(session, root)
    stubborn = _plant(library, "0001.png")
    monkeypatch.setattr(migration, "move_planned_files", lambda *a, **k: [])

    result = migration.run_migration_pass(vault, after_id=0, limit=10)
    assert result["moved_picture_ids"] == []
    assert result["skipped"] == [{"picture_id": stubborn, "reason": "move_failed"}]


# ---------------------------------------------------------------------------
# The crash window: a file that moved and a row that did not follow
# ---------------------------------------------------------------------------


def _purge(library, picture_ids):
    """Run one MissingFilePurgeTask over the named pictures."""
    from pixlstash.tasks.missing_file_purge_task import MissingFilePurgeTask

    session, root = library["session"], library["root"]
    pictures = [session.get(Picture, pid) for pid in picture_ids]
    task = MissingFilePurgeTask(_StubVault(session, root), pictures)
    result = task._run_task()
    session.expire_all()
    return result


def test_a_move_that_crashed_before_the_row_was_written_is_repaired_not_purged(
    library,
):
    """The blocking case. The engine journals the move, renames the file, and
    the process dies before the row is repointed. The row names a path with no
    file at it, which is exactly what the purge sweep deletes pictures over,
    and the DeletedFileLog it writes carries file_removed=True, which restore
    reads as *never resurrect*. The journal is what makes it a repair."""
    session, root = library["session"], library["root"]
    picture_id = _plant(library, "0001.png")

    # The rename landed; the transaction that would have recorded it did not.
    landed = os.path.join(root, "2024 Shoots", "Mira", "0001.png")
    os.makedirs(os.path.dirname(landed), exist_ok=True)
    os.replace(os.path.join(root, "0001.png"), landed)
    session.add(
        PictureMove(
            picture_id=picture_id,
            old_path="0001.png",
            new_path="2024 Shoots/Mira/0001.png",
        )
    )
    session.commit()

    result = _purge(library, [picture_id])

    assert result["purged"] == 0
    assert result["repaired"] == 1
    picture = session.get(Picture, picture_id)
    assert picture is not None, "the picture must survive a crashed move"
    assert picture.file_path == "2024 Shoots/Mira/0001.png"
    assert session.exec(select(DeletedFileLog)).all() == []


def test_an_undo_that_crashed_before_the_row_was_written_is_repaired_not_purged(
    library,
):
    """The same window, entered from the other end. An undo puts the file back
    at the path the move took it from, so the journal row that names the pair is
    read backwards: the row's `old_path` is where the file now is."""
    session, root = library["session"], library["root"]
    picture_id = _plant(library, "2024 Shoots/Mira/0002.png")

    # The undo's rename landed; its transaction rolled back.
    os.replace(
        os.path.join(root, "2024 Shoots", "Mira", "0002.png"),
        os.path.join(root, "0002.png"),
    )
    session.add(
        PictureMove(
            picture_id=picture_id,
            old_path="0002.png",
            new_path="2024 Shoots/Mira/0002.png",
        )
    )
    session.commit()

    result = _purge(library, [picture_id])

    assert result["purged"] == 0
    assert result["repaired"] == 1
    assert session.get(Picture, picture_id).file_path == "0002.png"


def test_a_file_the_owner_really_deleted_is_still_purged(library):
    """The guard must not become a blanket exemption: with no journal row
    naming the path, a missing file is a deletion and is recorded as one."""
    session, root = library["session"], library["root"]
    picture_id = _plant(library, "0003.png")
    os.remove(os.path.join(root, "0003.png"))

    result = _purge(library, [picture_id])

    assert result["purged"] == 1
    assert result["repaired"] == 0
    assert session.get(Picture, picture_id) is None
    logged = session.exec(select(DeletedFileLog)).all()
    assert [row.file_removed for row in logged] == [True]


def test_another_pictures_journal_row_at_the_same_path_is_not_repair_evidence(
    library,
):
    """Journal rows outlive their move by RETENTION_S, and a path can be reused
    in that window: the engine moved picture A out of `0005.png`, a later
    import put picture B at `0005.png`, and the owner then deleted B. Matched
    on path alone, A's row would repoint B at A's file. B is a deletion."""
    session, root = library["session"], library["root"]
    earlier = _plant(library, "2024 Shoots/Mira/0005.png")
    later = _plant(library, "0005.png")
    os.remove(os.path.join(root, "0005.png"))
    session.add(
        PictureMove(
            picture_id=earlier,
            old_path="0005.png",
            new_path="2024 Shoots/Mira/0005.png",
            consumed=True,
        )
    )
    session.commit()

    result = _purge(library, [later])

    assert result["purged"] == 1
    assert result["repaired"] == 0
    assert session.get(Picture, later) is None
    assert session.get(Picture, earlier).file_path == "2024 Shoots/Mira/0005.png"


def test_a_move_still_in_flight_is_deferred_rather_than_purged(library):
    """A journal row whose other end holds no file either. Something is mid
    flight, or the move failed after the intent was committed; neither is a
    deletion, and the row expires on its own if it never completes."""
    session, root = library["session"], library["root"]
    picture_id = _plant(library, "0004.png")
    os.remove(os.path.join(root, "0004.png"))
    session.add(
        PictureMove(
            picture_id=picture_id,
            old_path="0004.png",
            new_path="2024 Shoots/Mira/0004.png",
        )
    )
    session.commit()

    result = _purge(library, [picture_id])

    assert result["purged"] == 0
    assert result["deferred"] == 1
    assert session.get(Picture, picture_id) is not None
    assert session.exec(select(DeletedFileLog)).all() == []
