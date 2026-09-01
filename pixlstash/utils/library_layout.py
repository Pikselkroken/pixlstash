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

Nothing here moves a file. :func:`relocate` says where a file would go and
``services/layout_move_service.py`` is what takes it there. The rule and its
case table live in ``design/1.11-existing-library/DECISIONS.md``.
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
    is a many-to-one map - ``A/B``, ``A:B`` and ``A_B`` all become ``A_B`` - so
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
            across segments is expressible and meaningless - that is still open
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
    unfiled: str = "Unassigned"

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


def folder_match_key(name: str) -> str:
    """The key two entity names are the same folder under.

    ``folder_name`` then the case/NFC fold, in that order - the same pair
    :func:`is_true` compares a path component with, exposed because deciding
    *whether a rename may claim a directory* has to ask exactly the question the
    truth check will ask of the result. Two entities with the same key share a
    folder on every filesystem this runs on.
    """
    return _match_key(folder_name(name))


def _names_of(facet: Facet, names: Mapping[Facet, Collection[str]]) -> Collection[str]:
    """Return one facet's names, refusing a bare string handed in for a list."""
    values = names.get(facet) or ()
    if isinstance(values, str):
        raise TypeError(
            f"{facet} must map to a sequence of names, not the string "
            f"{values!r} - a str would be read one character per name"
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
    """Split a relative folder path into components, either separator.

    A path carrying ``.`` or ``..`` is refused whole rather than tidied up.
    Dropping the ``..`` from ``2024 Shoots/../Mira`` would fabricate a project
    level the path does not have and could return ``False`` - a move - for a
    picture that is only ever in ``Mira``. An unnormalised path is a caller
    mistake, and the safe reading of one is the same as any path the layout
    cannot read: no components, and never false.
    """
    parts = folder.replace("\\", "/").split("/")
    if any(part in (".", "..") for part in parts):
        return ()
    return tuple(part for part in parts if part)


def is_true(
    folder: str,
    facets: FacetValues,
    layout: Layout,
    known_names: FacetVocabulary,
) -> bool:
    """Return whether the folder a picture sits in still describes it.

    Components are read against the layout left to right, skipping segments the
    path does not use - a picture filed by set alone under ``Project`` then
    ``Person or Set`` sits one level deep, not two. Reading **stops** at the
    first component the layout's vocabulary cannot read: everything from there
    down is the owner's own, so ``2024 Shoots/Mira/2026-08`` is judged on its
    first two components and ``Holiday/2024 Shoots`` on none of them. Where a
    component could be read as more than one facet, any reading that is still
    true wins: this decides whether a file moves, so it errs towards leaving it
    alone.

    Args:
        folder: The folder the picture is in, relative to the library root, and
            **not** including the file name - a caller holding a relative file
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
        # The library root, or a path this cannot read at all. It matches no
        # segment, so it contradicts nothing - this is why an existing flat
        # library needs no migration.
        return True

    if len(components) == 1 and _match_key(components[0]) == _match_key(layout.unfiled):
        # The unfiled folder is part of the layout's language: it says "nothing
        # files this picture", and stops being true the moment something does.
        # Only at this exact depth. Anything nested below a folder of that name
        # is a tree of the owner's own that happens to share the name, and the
        # override has to survive that.
        return render(facets, layout) == layout.unfiled

    still_true, _ = _walk(components, facets, layout, known_names)
    return still_true


def _walk(
    components: Sequence[str],
    facets: FacetValues,
    layout: Layout,
    known_names: FacetVocabulary,
) -> tuple[bool, int]:
    """Read *components* against the layout and report what it made of them.

    The one reading both :func:`is_true` and :func:`relocate` share, so the
    folder a picture is moved out of and the judgement that it had to move can
    never disagree about where the layout's part of the path ends.

    Returns:
        ``(still_true, owned)`` - whether every component the layout could read
        still describes the picture, and **how many leading components the
        layout owns**. Owning one is not the same as being right about it: a
        component that names a project the picture has left is the layout's to
        rewrite, so it counts, and it is also what makes *still_true* ``False``.
        Everything from ``owned`` on is the owner's own and travels with the
        picture unchanged.
    """
    vocab = [_segment_keys(segment, known_names) for segment in layout.segments]
    mine = [_segment_keys(segment, facets) for segment in layout.segments]

    still_true = True
    owned = 0
    next_segment = 0
    for component in components:
        key = _match_key(component)
        parses_at: int | None = None
        mine_at: int | None = None
        for index in range(next_segment, len(layout.segments)):
            if key not in vocab[index]:
                continue
            if parses_at is None:
                parses_at = index
            if key in mine[index]:
                mine_at = index
                break
        if mine_at is not None:
            # Still true here. Consume this segment and move on.
            next_segment = mine_at + 1
            owned += 1
            continue
        if parses_at is not None:
            # It names something of the layout's, in every reading, and the
            # picture is none of them any more. The folder has stopped being
            # true. The walk carries on rather than returning, because the
            # segments below this one may still be the layout's and a move has
            # to rewrite the whole prefix, not the one component that failed.
            still_true = False
            next_segment = parses_at + 1
            owned += 1
            continue
        # Nothing the layout knows about: the owner's own folder, and the rest
        # of the path below it is theirs too.
        break

    return still_true, owned


def relocate(
    folder: str,
    facets: FacetValues,
    layout: Layout,
    known_names: FacetVocabulary,
) -> str | None:
    """Return the folder a picture has to move to, or ``None`` to leave it alone.

    The move engine's whole decision in one call: ``None`` for every case
    :func:`is_true` calls true, and otherwise the destination. Arguments are
    :func:`is_true`'s, and so is the reading.

    **The owner's own folders below the layout are carried across, not
    flattened.** A picture in ``2024 Shoots/Mira/2026-08`` whose project changes
    goes to ``Client · Nordvik/Mira/2026-08``: the layout owns the first two
    components and rewrites them, and ``2026-08`` is nobody's business but the
    owner's. Flattening it would collapse a date tree into one folder and make
    two files of the same name collide, which is a curated library's structure
    being destroyed by a rule that promised to preserve it.

    Returns:
        A ``/``-separated relative folder path, or ``None`` when the picture
        must not move - which includes the case where the destination is where
        the picture already is.
    """
    components = _components(folder)
    if not components:
        return None

    if len(components) == 1 and _match_key(components[0]) == _match_key(layout.unfiled):
        destination = render(facets, layout)
        return None if destination == layout.unfiled else destination

    still_true, owned = _walk(components, facets, layout, known_names)
    if still_true:
        return None
    destination = "/".join((render(facets, layout), *components[owned:]))
    return None if _components(destination) == components else destination


def match_destination(
    folder: str,
    facets: FacetValues,
    layout: Layout,
    known_names: FacetVocabulary,
) -> str | None:
    """Where ``render`` would put a picture that is allowed to stay put.

    The **drift** the design bundle draws, and it is deliberately not a move:
    a picture filed under ``2024 Shoots`` that has become mostly a Nordvik job
    is still a 2024 Shoots picture, so the folder never stopped being true and
    the rule leaves it alone. The tree is not wrong; it is not always what the
    owner would have picked. This is what "Move to match" offers, and it is
    offered, never taken.

    ``None`` when there is nothing to offer, which is three cases and each is
    the answer rather than a gap:

    * The folder has stopped being true - that is :func:`relocate`'s move, and
      it needs no offering.
    * The layout owns none of the path. A folder of the owner's own contradicts
      nothing and is a permanent override; offering to pull it into the layout
      would be offering to undo the override.
    * ``render`` already agrees with where the picture is.

    The tail below the layout travels, exactly as it does for a move.
    """
    components = _components(folder)
    if not components:
        return None
    still_true, owned = _walk(components, facets, layout, known_names)
    if not still_true or owned == 0:
        return None
    destination = "/".join((render(facets, layout), *components[owned:]))
    return None if _components(destination) == components else destination


def migrate_destination(
    folder: str, facets: FacetValues, layout: Layout, *, sweep_unfiled: bool = False
) -> str | None:
    """Where the whole-library migration puts a picture, or ``None`` to leave it.

    v1.11 Phase 4c, and **deliberately not the move-when-false rule.** Under
    that rule a flat library parses against nothing, can never be false, and
    never moves - which is exactly why old libraries need no migration. This is
    the owner asking for something else: *make it all match, now.* So it does
    not read the folder at all; it asks where the layout would put the picture,
    and answers that.

    **Every picture lands exactly where ``render`` says, with nothing of its old
    path kept.** A library arranged by year and date, which is what most
    libraries are before they have a layout, goes to ``Nordvik/Mira``, not to
    ``Nordvik/Mira/2024/2024-08-15``. The date tree was the arrangement the
    layout replaces, so carrying it across would keep the thing the owner
    pressed the button to be rid of; and a folder of the owner's own is not an
    override here, because the whole point of the gesture is that nothing is.
    Two files of one name from two old folders therefore meet, which is what
    the migration's suffix rule is for (decided once, 2026-09-01; the rule's
    own moves still carry the tail and still refuse a taken name).

    Args:
        sweep_unfiled: Also move the pictures nothing files into
            ``layout.unfiled``. Off, they stay wherever they are; on, the
            owner has asked for one folder of everything the layout could not
            place, which is the difference between a tidy tree and a tree
            with a few thousand loose files still in the old date folders.

    Returns:
        A ``/``-separated relative folder, or ``None`` when the picture stays
        where it is - which is three cases:

        * **The layout cannot place it**, and *sweep_unfiled* is off. Nothing
          files it, so ``render`` would answer the unfiled folder, and
          sweeping it there was not asked for.
        * It is already where the layout would put it.
        * The folder is a path the layout cannot read at all - one carrying
          ``.`` or ``..`` - which is refused whole here as it is everywhere
          else in this module.
    """
    placed = render(facets, layout)
    if placed == layout.unfiled and not sweep_unfiled:
        return None
    components = _components(folder)
    if folder and not components:
        # ``.``/``..`` - refused whole rather than tidied up, exactly as
        # :func:`is_true` refuses it.
        return None
    return None if _components(placed) == components else placed


# ---------------------------------------------------------------------------
# Reconciliation - the mirror, for moves made outside PixlStash (v1.11 Phase 5)
# ---------------------------------------------------------------------------


class MoveOutcome(str, Enum):
    """What an owner-made move implies about a picture's assignments.

    The three the release plan names, plus the ordinary case a real library
    mostly produces:

    * ``UNAMBIGUOUS`` - apply the removals and additions below.
    * ``AMBIGUOUS`` - at least one removal cannot be told apart from a refile:
      the picture has more than one of that facet, so leaving one folder does
      not say which. Listed, changed only when asked.
    * ``OFF_LAYOUT`` - the new folder names nothing the layout's vocabulary
      knows. The path was already followed; no assignment is touched, and it
      is never moved back.
    * ``NONE`` - nothing to reconcile at all, most commonly a subfolder of the
      owner's own changing below an unchanged layout prefix.
    """

    UNAMBIGUOUS = "unambiguous"
    AMBIGUOUS = "ambiguous"
    OFF_LAYOUT = "off_layout"
    NONE = "none"


@dataclass(frozen=True)
class ReconciledMove:
    """The reconciliation of one owner-made move.

    Attributes:
        outcome: See :class:`MoveOutcome`.
        removals: Facet/name pairs the picture should leave. Non-empty only for
            ``UNAMBIGUOUS`` and ``AMBIGUOUS``.
        additions: Facet/name pairs the picture should gain. An addition is
            never ambiguous by itself - it cannot make any folder untrue - so
            an ``AMBIGUOUS`` outcome is decided entirely by its removals; this
            function never holds an addition back because a removal next to
            it needs a human. What a *caller* does with that when the outcome
            is ``AMBIGUOUS`` is the caller's policy, not this function's: this
            struct states the reconciliation, not an action. The two calling
            services this repository has read it against apply BOTH tuples
            together when a caller resolves the row (an explicit "apply this
            id" on a picture is asking for what is true right now, ambiguity
            included) and NEITHER when a caller dismisses it (a dismissal
            changes nothing at all, by construction).
    """

    outcome: MoveOutcome
    removals: tuple[tuple[Facet, str], ...] = ()
    additions: tuple[tuple[Facet, str], ...] = ()


def read_named_components(
    components: Sequence[str], layout: Layout, known_names: FacetVocabulary
) -> list[tuple[Facet, str]]:
    """Return what the leading, layout-readable *components* name.

    The read :func:`is_true` and :func:`relocate` do not need, because they
    only ask whether a picture's OWN names still explain its folder.
    :func:`reconcile_move` asks the opposite question - what does this path
    say, regardless of who currently holds it - which is what a move made
    outside PixlStash has to be read against.

    Segments are consumed left to right and, within a segment, a facet's
    listed order wins on a name two facets could both claim - the same
    convention :func:`render` already uses to resolve it going forward, so a
    folder means the same entity read either direction. Stops at the first
    component that names nothing in *known_names*: everything from there is
    the owner's own and this function has nothing to say about it.

    Two distinct names that render to the same folder (``folder_name`` is
    documented many-to-one: ``Client: Nordvik`` and ``Client_ Nordvik`` both
    become ``Client_ Nordvik``) are read as **unreadable at that key**, not as
    one of the two arbitrarily. ``is_true`` accepts this same collision going
    forward because it only asks about membership, never about which specific
    entity a component names - but this function's caller has to name one, so
    picking whichever the vocabulary query happened to return last would
    silently add the picture to a different entity than the one on disk.
    Unreadable is the safe reading: it stops the walk here, same as a name
    nothing in the vocabulary claims at all.

    Returns:
        One ``(facet, canonical name)`` pair per component consumed, in
        layout order - the canonical name from *known_names*, not the raw
        component text, so a folder-name-safe rendering of the entity (e.g.
        punctuation replaced by ``_``) still resolves to the real name.
    """
    _AMBIGUOUS = object()
    name_by_key: dict[Facet, dict[str, str]] = {}
    for facet in Facet:
        keyed: dict[str, object] = {}
        for name in known_names.get(facet) or ():
            key = _match_key(folder_name(name))
            existing = keyed.get(key)
            if existing is None or existing == name:
                keyed[key] = name
            else:
                # A second (or third, ...) distinct name landed on this key.
                # Sticky once marked: a third colliding name must not undo the
                # ambiguity a second one already raised.
                keyed[key] = _AMBIGUOUS
        name_by_key[facet] = {
            key: name for key, name in keyed.items() if name is not _AMBIGUOUS
        }
    result: list[tuple[Facet, str]] = []
    next_segment = 0
    for component in components:
        key = _match_key(component)
        found: tuple[int, Facet, str] | None = None
        for index in range(next_segment, len(layout.segments)):
            for facet in layout.segments[index]:
                name = name_by_key[facet].get(key)
                if name is not None:
                    found = (index, facet, name)
                    break
            if found is not None:
                break
        if found is None:
            break
        index, facet, name = found
        result.append((facet, name))
        next_segment = index + 1
    return result


def reconcile_move(
    old_folder: str,
    new_folder: str,
    facets: FacetValues,
    layout: Layout,
    known_names: FacetVocabulary,
) -> ReconciledMove:
    """Return what a picture's move from *old_folder* to *new_folder* implies.

    The mirror of :func:`relocate`: that function moves a file when an
    assignment change makes its folder untrue; this reads a file the owner
    already moved and says whether an assignment should change to match. Same
    arguments, same reading of a path - a component the layout cannot read is
    the owner's own and never contradicts anything, in either direction.

    Args:
        old_folder: Where the picture's file was, relative to the root, not
            including the file name.
        new_folder: Where it is now, likewise.
        facets: The picture's own, CURRENT names per facet - not a snapshot
            from when it moved. Reconciliation asks whether today's
            assignments explain today's path, so a picture that gained or
            lost a membership since the move is judged on what is true now.
        layout: The root's layout.
        known_names: Every entity name the library currently has, per facet -
            also read live, for the same reason.

    Returns:
        The :class:`ReconciledMove`.
    """
    new_components = _components(new_folder)
    if not new_components:
        # The root itself, or a path this cannot split. Matches is_true's own
        # reading of the same shape: it contradicts nothing, so nothing here
        # is touched at all - not even a removal on the old side.
        return ReconciledMove(MoveOutcome.OFF_LAYOUT)

    is_unfiled_arrival = len(new_components) == 1 and _match_key(
        new_components[0]
    ) == _match_key(layout.unfiled)
    if is_unfiled_arrival:
        new_read: list[tuple[Facet, str]] = []
    else:
        new_read = read_named_components(new_components, layout, known_names)
        if not new_read:
            # Names nothing the layout's vocabulary knows. The path was
            # already followed by the scan; nothing here is an override to
            # correct, so nothing is touched.
            return ReconciledMove(MoveOutcome.OFF_LAYOUT)

    old_read = read_named_components(_components(old_folder), layout, known_names)

    old_set = set(old_read)
    new_set = set(new_read)

    removals: list[tuple[Facet, str]] = []
    for facet, name in sorted(old_set - new_set):
        member_keys = {_match_key(n) for n in _names_of(facet, facets) if n}
        if _match_key(name) in member_keys:
            # Currently a membership, and the picture just left its folder:
            # this is the "leaving a folder" half of the mirror.
            removals.append((facet, name))

    additions: list[tuple[Facet, str]] = []
    for facet, name in sorted(new_set - old_set):
        member_keys = {_match_key(n) for n in _names_of(facet, facets) if n}
        if _match_key(name) not in member_keys:
            additions.append((facet, name))

    if not removals and not additions:
        return ReconciledMove(MoveOutcome.NONE)

    # Ambiguous exactly when a removal cannot be told apart from a refile: the
    # picture has more than one of that facet, so leaving one folder does not
    # say which it left. An addition never carries this - gaining a facet
    # value cannot make any existing folder untrue, so it is always safe.
    ambiguous = any(len(_names_of(facet, facets)) > 1 for facet, _ in removals)
    outcome = MoveOutcome.AMBIGUOUS if ambiguous else MoveOutcome.UNAMBIGUOUS
    return ReconciledMove(outcome, tuple(removals), tuple(additions))


# ---------------------------------------------------------------------------
# Serialisation - one column, readable in a database browser
# ---------------------------------------------------------------------------

_SEGMENT_SEPARATOR = "/"
_FACET_SEPARATOR = ","


def format_layout(layout: Layout) -> str:
    """Return the stored form of a layout: ``"project/person,set"``.

    Segments are separated by ``/`` and a segment's alternatives by ``,``, which
    is what the design bundle draws (``Project / Person or Set``) with the words
    the :class:`Facet` values already use. ``unfiled`` is deliberately NOT in
    here: it is a folder name the owner can type, and folding a free-text name
    into a separator-bearing format is how it eventually contains a separator.
    """
    return _SEGMENT_SEPARATOR.join(
        _FACET_SEPARATOR.join(facet.value for facet in segment)
        for segment in layout.segments
    )


def parse_layout(text: str | None, unfiled: str = "Unassigned") -> Layout | None:
    """Return the layout *text* describes, or ``None`` for "this root has none".

    ``None`` and the empty string both mean no layout, which is the default and
    the only state in which nothing is ever moved.

    Raises:
        ValueError: The text names something that is not a facet, or *unfiled*
            is not a safe single path component. Refused rather than silently
            dropped: a layout with a segment quietly missing would move files
            somewhere nobody asked for.
    """
    if not text:
        return None
    segments = []
    for raw_segment in text.split(_SEGMENT_SEPARATOR):
        facets = []
        for raw_facet in raw_segment.split(_FACET_SEPARATOR):
            name = raw_facet.strip().lower()
            if not name:
                continue
            try:
                facets.append(Facet(name))
            except ValueError:
                raise ValueError(
                    f"{raw_facet.strip()!r} is not a layout facet; expected one "
                    f"of {', '.join(facet.value for facet in Facet)}"
                ) from None
        if facets:
            segments.append(tuple(facets))
    if not segments:
        return None
    return Layout(segments=tuple(segments), unfiled=unfiled)
