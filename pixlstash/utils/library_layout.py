"""The library layout model: where a picture belongs, and whether it still does.

A layout is an ordered list of segments, one folder level each. A segment holds
one or more facets and the first that applies wins; a segment with nothing to
fill it is skipped rather than left as an empty folder, which is what keeps the
tree two deep instead of five. A new library starts on ``DEFAULT_LAYOUT``,
``Project / Person or Set``.

Two pure functions do the work, and the second one is the release:

``render``
    The folder path a picture should have under a layout.

``is_true``
    Whether the folder a picture is *actually* sitting in still describes it.
    **A path that does not parse against the layout can never be false**, so a
    hand-placed file is a permanent override and an existing flat library needs
    no migration: a file at the library root matches no segment, contradicts
    nothing, and stays put.

Truth is membership, not equality with ``render``. The folder ``Mira/`` says
"this is a Mira picture"; it stays true while Mira is one of the picture's
people, whoever ``render`` would pick today. That is what makes adding a second
project or person move nothing.

Neither function touches the database or the filesystem. The caller supplies
the picture's own names per facet, and — for ``is_true`` — the library's whole
vocabulary of names per facet. The vocabulary is what separates *this folder
names a project the picture is no longer in* (false, it moves) from *this
folder names nothing PixlStash knows about* (unparseable, it never moves), and
there is no way to tell those apart without it.

Nothing here moves a file. See v1.11.0 Phase 4b for that.
"""

import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

# Characters that cannot appear in a path component on some supported OS, plus
# the separators, which would otherwise let an entity name invent folder levels.
_ILLEGAL_IN_FOLDER_NAME = set('/\\:*?"<>|') | {chr(code) for code in range(32)}

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

    The vocabulary the design bundle uses throughout: ``person`` is the user-
    facing word for what the database calls a character.
    """

    PROJECT = "project"
    PERSON = "person"
    SET = "set"
    TAG = "tag"


_FACET_LABELS = {
    Facet.PROJECT: "Project",
    Facet.PERSON: "Person",
    Facet.SET: "Set",
    Facet.TAG: "Tag",
}

# A picture's own names per facet, in the order they should be preferred, and
# the library's whole set of names per facet. Both are plain mappings so that
# callers can build them straight out of a query without a wrapper type.
FacetValues = Mapping[Facet, Sequence[str]]
FacetVocabulary = Mapping[Facet, Collection[str]]


@dataclass(frozen=True)
class Layout:
    """An ordered list of folder levels.

    Attributes:
        segments: One tuple of facets per folder level. Within a segment the
            first facet the picture has a value for wins.
        unfiled: The folder a picture with nothing to file it by is written to.
            It has to be a real name rather than the library root, because the
            root is exactly where an unmigrated flat library lives and those
            files must never move.
    """

    segments: tuple[tuple[Facet, ...], ...]
    unfiled: str = "_Inbox"

    def __str__(self) -> str:
        """Render the layout the way the Storage screen shows it."""
        return " / ".join(
            " or ".join(_FACET_LABELS[facet] for facet in segment)
            for segment in self.segments
        )


DEFAULT_LAYOUT = Layout(segments=((Facet.PROJECT,), (Facet.PERSON, Facet.SET)))


def folder_name(name: str) -> str:
    """Return the folder name an entity name is written down as.

    Anything that cannot be a path component is replaced rather than dropped,
    so two different names stay two different folders. In particular the path
    separators go: without this an entity called ``Mira/2024`` would invent a
    folder level and a rendered path would escape the library root.

    Args:
        name: The entity name as the owner typed it.

    Returns:
        A single path component, never empty and never a separator.
    """
    cleaned = "".join(
        "_" if character in _ILLEGAL_IN_FOLDER_NAME else character for character in name
    )
    # Windows silently drops trailing dots and spaces, which would make the
    # written folder a different name from the one matched against later.
    cleaned = cleaned.strip().rstrip(". ")
    if not cleaned or set(cleaned) == {"."}:
        return _EMPTY_FOLDER_NAME
    if cleaned.split(".")[0].lower() in _WINDOWS_RESERVED:
        return f"_{cleaned}"
    return cleaned


def _match_key(name: str) -> str:
    """Return the form two folder names are compared in.

    Case-folded because Windows and macOS are case-insensitive, and NFC-
    normalised because macOS hands back decomposed accents, so an unnormalised
    comparison would find a person's own folder untrue on their own machine.
    """
    return unicodedata.normalize("NFC", name).casefold()


def _segment_value(segment: Iterable[Facet], facets: FacetValues) -> str | None:
    """Return the name that fills a segment, or ``None`` if nothing does."""
    for facet in segment:
        values = facets.get(facet) or ()
        for value in values:
            if value:
                return value
    return None


def render(facets: FacetValues, layout: Layout) -> str:
    """Return the folder path a picture should have, relative to the library root.

    Args:
        facets: The picture's own names per facet, most-preferred first. A
            facet that is missing or empty simply does not fill a segment.
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


def _folder_components(path: str) -> tuple[str, ...]:
    """Return the folder components of a relative picture path, file name dropped."""
    normalised = path.replace("\\", "/")
    components = [part for part in normalised.split("/") if part and part != "."]
    return tuple(components[:-1])


def _segment_keys(
    segment: Iterable[Facet], names: Mapping[Facet, Collection[str]]
) -> set[str]:
    """Return the match keys of every name that could fill a segment."""
    return {
        _match_key(folder_name(name))
        for facet in segment
        for name in names.get(facet) or ()
    }


def is_true(
    path: str,
    facets: FacetValues,
    layout: Layout,
    known_names: FacetVocabulary,
) -> bool:
    """Return whether the folder a picture sits in still describes it.

    A component is read against the layout left to right, skipping segments the
    path does not use — a picture filed by set alone under ``Project / Person or
    Set`` sits one level deep, not two. Components below the last one the layout
    accounts for are the owner's own subfolders and are not judged. Where a
    component could be read as more than one facet, any reading that is still
    true wins: this decides whether a file moves, so it errs towards leaving it
    alone.

    Args:
        path: The picture's path relative to the library root, file name
            included — only its folder components are examined.
        facets: The picture's own names per facet.
        layout: The layout to judge against.
        known_names: Every entity name in the library, per facet. A component
            naming nothing in here is not part of the layout's language, so the
            path does not parse and can never be false. Deleting an entity
            therefore takes its name out of the language and freezes the
            folders named after it; a caller that wants those judged one last
            time passes the name it is deleting in here.

    Returns:
        ``True`` while the folder is still true, including every case where the
        path does not parse against the layout at all. ``False`` only when a
        component names something of the layout's that the picture no longer is.
    """
    components = _folder_components(path)
    if not components:
        # The library root. It matches no segment, so it contradicts nothing —
        # this is why an existing flat library needs no migration.
        return True

    if _match_key(components[0]) == _match_key(layout.unfiled):
        # The unfiled folder is part of the layout's language: it says "nothing
        # files this picture", and stops being true the moment something does.
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
