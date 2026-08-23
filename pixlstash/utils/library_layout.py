"""The library layout model: where a picture belongs, and whether it still does.

A layout is an ordered list of segments, one folder level each. A segment holds
one or more facets and the first that applies wins; a segment with nothing to
fill it is skipped rather than left as an empty folder. A new library starts on
``DEFAULT_LAYOUT``, ``Project`` then ``Person or Set``.

``render`` gives the folder a picture should be in. ``is_true`` says whether the
folder it *is* in still describes it, and that one is the release: **a path that
does not parse against the layout can never be false**, so a hand-placed file is
a permanent override and an existing flat library needs no migration.

Truth is membership, not equality with ``render``. The folder ``Mira/`` says
"this is a Mira picture" and stays true while Mira is one of the picture's
people, whoever ``render`` would pick today. That is what makes adding a second
project or person move nothing.

Neither function touches the database or the filesystem. The caller passes the
picture's own names per facet and, for ``is_true``, the library's whole
vocabulary of names per facet. The vocabulary is the only thing that separates
*this folder names a project the picture is no longer in* (false, it moves) from
*this folder names nothing PixlStash knows about* (unparseable, it never moves).

Nothing here moves a file. See v1.11.0 Phase 4b for that. The rule and its case
table live in ``design/1.11-existing-library/DECISIONS.md``.
"""

import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

# The character class from ``utils/service/export_utils.py``, which solves the
# same problem for zip member names. Not that function itself: it takes the last
# component of a name, so ``Mira/2024`` becomes ``2024``, and here two entities
# that differ only above a separator must not become the same folder.
_UNSAFE_FOLDER_CHARS_RE = re.compile(r'[\x00-\x1f\x7f<>:"|?*/\\]')

# Names Windows refuses whatever the extension, so a project called ``CON`` has
# to be written down as something else or its folder cannot exist.
_WINDOWS_RESERVED = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{digit}" for digit in range(1, 10)]
    + [f"lpt{digit}" for digit in range(1, 10)]
)

# What a name collapses to when sanitising leaves nothing of it at all.
_EMPTY_FOLDER_NAME = "_unnamed"


class Facet(str, Enum):
    """A kind of thing a folder level can be named after.

    The vocabulary the design bundle uses throughout: ``person`` is the
    user-facing word for what the database calls a character.
    """

    PROJECT = "project"
    PERSON = "person"
    SET = "set"
    TAG = "tag"


# A picture's own names per facet, in the order they should be preferred, and
# the library's whole set of names per facet. Both are plain mappings so that
# callers can build them straight out of a query without a wrapper type.
FacetValues = Mapping[Facet, Sequence[str]]
FacetVocabulary = Mapping[Facet, Collection[str]]


def folder_name(name: str) -> str:
    """Return the folder name an entity name is written down as.

    Every character that cannot appear in a path component becomes ``_``. That
    is a many-to-one map — ``A/B``, ``A:B`` and ``A_B`` all become ``A_B`` — so
    two entities whose names differ only in punctuation share a folder, and a
    picture in either of them reads as true there. That is the same collision
    the filesystem would force anyway, and it errs towards not moving files.

    What it must not do is let a name invent a folder level: without the
    separator replacement an entity called ``Mira/2024`` would render two
    folders deep and one called ``../..`` would escape the library root.

    Args:
        name: The entity name as the owner typed it.

    Returns:
        A single path component, never empty and never a separator.
    """
    # Windows silently drops trailing dots and spaces, which would make the
    # written folder a different name from the one matched against later.
    cleaned = _UNSAFE_FOLDER_CHARS_RE.sub("_", name).strip().rstrip(". ")
    if not cleaned:
        return _EMPTY_FOLDER_NAME
    if cleaned.split(".")[0].lower() in _WINDOWS_RESERVED:
        return f"_{cleaned}"
    return cleaned


@dataclass(frozen=True)
class Layout:
    """An ordered list of folder levels.

    Attributes:
        segments: One tuple of facets per folder level. Within a segment the
            first facet the picture has a value for wins. A facet repeated
            across segments is expressible and meaningless — that is still open
            in ``DECISIONS.md`` and is deliberately not resolved here.
        unfiled: The single folder a picture with nothing to file it by is
            written to. It has to be a real name rather than the library root,
            because the root is exactly where an unmigrated flat library lives
            and those files must never move.

    Raises:
        ValueError: If ``unfiled`` is not already a safe single path component.
            It reaches ``render``'s output verbatim, so it is the one field that
            could otherwise escape the library root.
    """

    segments: tuple[tuple[Facet, ...], ...]
    unfiled: str = "_Inbox"

    def __post_init__(self) -> None:
        if self.unfiled != folder_name(self.unfiled):
            raise ValueError(
                f"unfiled must be a single safe path component, "
                f"got {self.unfiled!r} (try {folder_name(self.unfiled)!r})"
            )


DEFAULT_LAYOUT = Layout(segments=((Facet.PROJECT,), (Facet.PERSON, Facet.SET)))


def _match_key(name: str) -> str:
    """Return the form two folder names are compared in.

    Case-folded because Windows and macOS are case-insensitive, and NFC-
    normalised because macOS hands back decomposed accents, so an unnormalised
    comparison would find a person's own folder untrue on their own machine.

    ``render`` writes the name as the owner typed it and only the comparison
    folds, so on a case-sensitive filesystem two entities differing only in case
    get two folders and each reads as true in both. Again: the same collision
    the other two platforms force, in the direction that does not move files.
    """
    return unicodedata.normalize("NFC", name).casefold()


def _names_of(facet: Facet, names: Mapping[Facet, Collection[str]]) -> Collection[str]:
    """Return one facet's names, refusing a bare string handed in for a list."""
    values = names.get(facet) or ()
    if isinstance(values, str):
        raise TypeError(
            f"{facet} must map to a sequence of names, not the string "
            f"{values!r} — a str would be read one character per name"
        )
    return values


def _segment_value(segment: Iterable[Facet], facets: FacetValues) -> str | None:
    """Return the name that fills a segment, or ``None`` if nothing does."""
    for facet in segment:
        for value in _names_of(facet, facets):
            if value:
                return value
    return None


def _segment_keys(
    segment: Iterable[Facet], names: Mapping[Facet, Collection[str]]
) -> set[str]:
    """Return the match keys of every name that could fill a segment."""
    return {
        _match_key(folder_name(name))
        for facet in segment
        for name in _names_of(facet, names)
        if name
    }


def render(facets: FacetValues, layout: Layout) -> str:
    """Return the folder a picture should be in, relative to the library root.

    Args:
        facets: The picture's own names per facet, most-preferred first. A facet
            that is missing or empty simply does not fill a segment.
        layout: The layout to place it under.

    Returns:
        A ``/``-separated relative folder path, never absolute and never empty:
        a picture that fills no segment at all gets ``layout.unfiled``.
        Components never contain ``/`` themselves, so splitting is safe.
    """
    parts = []
    for segment in layout.segments:
        value = _segment_value(segment, facets)
        if value is not None:
            parts.append(folder_name(value))
    return "/".join(parts) if parts else layout.unfiled


def _components(folder: str) -> tuple[str, ...]:
    """Split a relative folder path into components, either separator."""
    return tuple(
        part
        for part in folder.replace("\\", "/").split("/")
        if part and part not in (".", "..")
    )


def is_true(
    folder: str,
    facets: FacetValues,
    layout: Layout,
    known_names: FacetVocabulary,
) -> bool:
    """Return whether the folder a picture sits in still describes it.

    Components are read against the layout left to right, skipping segments the
    path does not use — a picture filed by set alone under ``Project`` then
    ``Person or Set`` sits one level deep, not two. Reading **stops** at the
    first component the layout's vocabulary cannot read: everything from there
    down is the owner's own, so ``2024 Shoots/Mira/2026-08`` is judged on its
    first two components and ``Holiday/2024 Shoots`` on none of them. Where a
    component could be read as more than one facet, any reading that is still
    true wins: this decides whether a file moves, so it errs towards leaving it
    alone.

    Args:
        folder: The folder the picture is in, relative to the library root, and
            **not** including the file name — a caller holding a relative file
            path passes ``os.path.dirname`` of it. Guessing which trailing
            component was a file name would silently flip the answer for a path
            written with a trailing separator.
        facets: The picture's own names per facet.
        layout: The layout to judge against.
        known_names: Every entity name in the library, per facet. A component
            naming nothing in here is not part of the layout's language, so the
            path does not parse and can never be false. Deleting an entity
            therefore takes its name out of the language and freezes the folders
            named after it.

    Returns:
        ``True`` while the folder is still true, including every case where the
        path does not parse against the layout at all. ``False`` only when a
        component names something of the layout's that the picture no longer is.
    """
    components = _components(folder)
    if not components:
        # The library root. It matches no segment, so it contradicts nothing —
        # this is why an existing flat library needs no migration.
        return True

    if len(components) == 1 and _match_key(components[0]) == _match_key(layout.unfiled):
        # The unfiled folder is part of the layout's language: it says "nothing
        # files this picture", and stops being true the moment something does.
        # Only at this exact depth. Anything nested below a folder of that name
        # is a tree of the owner's own that happens to share the name, and the
        # override has to survive that.
        return render(facets, layout) == layout.unfiled

    vocab = [_segment_keys(segment, known_names) for segment in layout.segments]
    mine = [_segment_keys(segment, facets) for segment in layout.segments]

    next_segment = 0
    for component in components:
        key = _match_key(component)
        parses = False
        for index in range(next_segment, len(layout.segments)):
            if key not in vocab[index]:
                continue
            parses = True
            if key in mine[index]:
                # Still true here. Consume this segment and move on.
                next_segment = index + 1
                break
        else:
            if parses:
                # It names something of the layout's, in every reading, and the
                # picture is none of them any more. The folder has stopped
                # being true.
                return False
            # Nothing the layout knows about: the owner's own folder, and the
            # rest of the path below it is theirs too.
            break

    return True
