"""The layout model: where a picture belongs and whether it still does.

Pure functions, so there is no ``Server`` and no vault here — every case is a
dict of names and a string path.
"""

import unicodedata

import pytest

from pixlstash.utils.library_layout import (
    DEFAULT_LAYOUT,
    Facet,
    Layout,
    folder_name,
    is_true,
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
    "path",
    [
        "0412.png",  # a flat library, everything at the root
        "_unsorted/0412.png",  # a folder of the owner's own
        "Holiday/best/0412.png",  # two of them
        "Mira and Aled/0412.png",  # nearly a person's name, but not one
    ],
)
def test_a_path_the_layout_cannot_read_is_never_false(path):
    """A path that does not parse can never be false, whatever the picture is.

    This is what makes a hand-placed file a permanent override and what means
    an existing flat library needs no migration.
    """
    picture = facets(projects=["Client Nordvik"], people=["Aled"])
    assert is_true(path, picture, DEFAULT_LAYOUT, KNOWN) is True


def test_an_unreadable_path_stays_true_even_with_nothing_to_file_it_by():
    picture = facets()
    assert is_true("_unsorted/0412.png", picture, DEFAULT_LAYOUT, KNOWN) is True


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
    rendered = render(picture, DEFAULT_LAYOUT)
    assert rendered.split("/") == [".._.._etc", "Mira_2024"]


# --- is_true: the case table from DECISIONS.md ------------------------------


def test_import_moves_nothing_because_the_path_is_where_the_assignment_came_from():
    picture = facets(projects=["2024 Shoots"], people=["Mira"])
    path = render(picture, DEFAULT_LAYOUT) + "/0412.png"
    assert is_true(path, picture, DEFAULT_LAYOUT, KNOWN) is True


def test_adding_a_second_project_leaves_the_folder_true():
    """It is still in the first one, whichever ``render`` would pick today."""
    picture = facets(projects=["Client Nordvik", "2024 Shoots"], people=["Mira"])
    assert is_true("2024 Shoots/Mira/0412.png", picture, DEFAULT_LAYOUT, KNOWN) is True


def test_removing_the_project_the_folder_is_named_after_makes_it_false():
    picture = facets(projects=["Client Nordvik"], people=["Mira"])
    assert is_true("2024 Shoots/Mira/0412.png", picture, DEFAULT_LAYOUT, KNOWN) is False


def test_swapping_the_person_the_folder_is_named_after_makes_it_false():
    picture = facets(projects=["2024 Shoots"], people=["Aled"])
    assert is_true("2024 Shoots/Mira/0412.png", picture, DEFAULT_LAYOUT, KNOWN) is False


def test_a_folder_of_the_owners_own_below_the_layout_is_not_judged():
    """``2024 Shoots / Mira / 2026-08`` is still a Mira picture in that project."""
    picture = facets(projects=["2024 Shoots"], people=["Mira"])
    path = "2024 Shoots/Mira/2026-08/0412.png"
    assert is_true(path, picture, DEFAULT_LAYOUT, KNOWN) is True


def test_a_skipped_segment_stays_skipped_when_the_picture_gains_one():
    """Nothing is re-derived: gaining a project does not move the set picture."""
    picture = facets(projects=["2024 Shoots"], sets=["mira-lora-v3"])
    assert is_true("mira-lora-v3/0412.png", picture, DEFAULT_LAYOUT, KNOWN) is True


def test_a_deeper_segment_is_judged_even_when_an_earlier_one_was_skipped():
    picture = facets(sets=["mira-lora-v3"], people=["Aled"])
    assert is_true("Mira/0412.png", picture, DEFAULT_LAYOUT, KNOWN) is False


def test_where_a_component_can_be_read_two_ways_a_true_reading_wins():
    """A name that is both a project and a person errs towards leaving it alone."""
    known = {Facet.PROJECT: ["Mira"], Facet.PERSON: ["Mira"], Facet.SET: []}
    picture = facets(people=["Mira"])
    assert is_true("Mira/0412.png", picture, DEFAULT_LAYOUT, known) is True


def test_a_name_the_library_no_longer_knows_freezes_its_folder():
    """Delete the entity and its name leaves the layout's language."""
    picture = facets(people=["Mira"])
    without_the_project = {**KNOWN, Facet.PROJECT: ["Client Nordvik"]}
    path = "2024 Shoots/Mira/0412.png"
    assert is_true(path, picture, DEFAULT_LAYOUT, without_the_project) is True
    assert is_true(path, picture, DEFAULT_LAYOUT, KNOWN) is False


# --- is_true: the unfiled folder --------------------------------------------


def test_the_unfiled_folder_stops_being_true_the_moment_something_files_it():
    empty = facets()
    filed = facets(people=["Mira"])
    path = f"{DEFAULT_LAYOUT.unfiled}/0412.png"
    assert is_true(path, empty, DEFAULT_LAYOUT, KNOWN) is True
    assert is_true(path, filed, DEFAULT_LAYOUT, KNOWN) is False


# --- folder names -----------------------------------------------------------


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Client Nordvik", "Client Nordvik"),
        ("Mira/2024", "Mira_2024"),
        ("back\\slash", "back_slash"),
        ("colon:name", "colon_name"),
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
    """Windows and macOS are case-insensitive, and macOS decomposes accents."""
    composed = unicodedata.normalize("NFC", "Ren\u00e9e")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    known = {Facet.PERSON: [composed]}
    picture = facets(people=[composed])
    layout = Layout(segments=((Facet.PERSON,),))
    assert is_true(f"{decomposed.upper()}/0412.png", picture, layout, known) is True


def test_windows_separators_in_the_path_are_read_as_folder_levels():
    picture = facets(projects=["Client Nordvik"], people=["Mira"])
    path = "2024 Shoots\\Mira\\0412.png"
    assert is_true(path, picture, DEFAULT_LAYOUT, KNOWN) is False


# --- the layout itself ------------------------------------------------------


def test_the_new_library_default_is_project_then_person_or_set():
    assert str(DEFAULT_LAYOUT) == "Project / Person or Set"
    assert DEFAULT_LAYOUT.segments == ((Facet.PROJECT,), (Facet.PERSON, Facet.SET))
