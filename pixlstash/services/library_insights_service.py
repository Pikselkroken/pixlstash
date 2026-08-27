"""Read-only findings about a library that was organised before PixlStash saw it.

The "About your library" screen (v1.11 Phase 6). Every finding here is computed
from data that already ships - the exact-duplicate key the duplicate queue's
tier 1 groups on, the description sentinel the captioner's finder tests, face
rows and their character assignment, and the app's own definition of an
*unassigned* picture. Nothing in this module writes, queues work, or reads a
pixel.

Two properties the screen depends on and this module owns:

* **Every check answers in both directions.** A check that finds nothing does
  not disappear; it returns ``state="clear"`` with the number that made it
  clear. A screen where every row is a complaint reads as a nag, and the row
  that says "nothing to fix here" is the one that reads as someone who looked.
* **A finding counts what its own button can show.** Each ``action`` names a
  destination that already exists, and the check is defined as the set that
  destination holds - not as the set that would read best. The unnamed-faces
  check intersects with unassigned for exactly this reason, and the overlap
  check scopes the duplicate queue to the pair's common ancestor rather than to
  one of the two folders. A number the owner cannot reach is worse than no
  number: it reads as the feature being broken.
* **A folder means a folder that carries meaning.** ``Picture.file_path`` is an
  absolute path only for pictures indexed in place (reference folders); a
  vault-managed picture in a library with no layout stores a flat ``<uuid>.png``
  that is storage, not organisation. The folder-shaped checks therefore consider
  only paths that have a directory component, and the payload reports
  ``folder_pictures`` / ``folders`` so the screen can say how much of the library
  that was.

  **Since v1.11 Phase 4b a vault-managed picture can have one too.** A library
  whose owner has chosen a layout stores ``<Project>/<Person>/<uuid>.png``, so
  the directory-component test now admits folders PixlStash wrote from the
  layout as well as folders the owner made by hand. That is deliberate and not a
  hole in the rule above: under a layout those folders *are* the organisation -
  they are named after the projects, people and sets these findings are about -
  so a check that skipped them would be blind to exactly the libraries this
  release is for. What it does mean is that a folder-shaped finding in a
  laid-out library may be restating a membership the owner can already see, and
  the wording of those findings is worth a look once both are on a real
  library.
"""

import os
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Optional

from sqlalchemy import func, or_
from sqlmodel import Session, select

from pixlstash.db_models import (
    DESCRIPTION_SENTINEL_ESCAPE_CHAR,
    DESCRIPTION_SENTINEL_LIKE_PATTERN,
    Face,
    Picture,
    Tag,
)
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# A pile smaller than this is not worth a row: every library has a handful of
# loose files and calling that a finding is the nagging this screen avoids.
PILE_MIN_PICTURES = 25

# Two folders are "mostly the same pictures" above this share of the smaller
# one, counted over at least this many pictures. Below either bar the overlap
# is an ordinary handful of copies rather than something the owner did once.
OVERLAP_MIN_SHARE = 0.5
OVERLAP_MIN_PICTURES = 10

# Above this share the "nothing to fix" wording is the honest one.
MOSTLY = 0.95

# How much bigger than the two folders themselves the duplicate queue's scope
# may be before it stops being a narrowing. The queue's folder scope is a
# sub-tree match, so the common ancestor of two folders can be the whole
# library; a scope that holds four times the pictures the finding is about is
# not "these two folders" by any reading.
SCOPE_MAX_WIDENING = 4

# ``Face.face_index`` for the "we looked and there is no face here" sentinel row.
# Not a face; see ``_LibraryFacts``.
REAL_FACE_SENTINEL_INDEX = -1


def _uncaptioned_clause():
    """The captioner's own "still needs a caption" predicate.

    Shared with :class:`~pixlstash.tasks.missing_description_finder.MissingDescriptionFinder`
    so the number on this screen is the number that finder is working through,
    sentinels included. Two different definitions of "uncaptioned" would put two
    different totals in front of the owner.
    """
    return or_(
        Picture.description.is_(None),
        Picture.description.like(
            DESCRIPTION_SENTINEL_LIKE_PATTERN,
            escape=DESCRIPTION_SENTINEL_ESCAPE_CHAR,
        ),
    )


def _finding(
    finding_id: str,
    *,
    state: str,
    title: str,
    evidence: str,
    action: Optional[dict] = None,
) -> dict:
    """One row of the screen.

    Args:
        finding_id: Stable key, so the frontend can pick an icon and a test can
            name a row without matching on prose.
        state: ``"todo"`` (there is something to look at) or ``"clear"`` (the
            check ran and found nothing wrong).
        title: The finding, with its number in it.
        evidence: Why the finding is true - the counts it was read off.
        action: ``{"label", "note", "kind", ...}`` naming the tool that answers
            it, or ``None`` when there is nothing to open.
    """
    return {
        "id": finding_id,
        "state": state,
        "title": title,
        "evidence": evidence,
        "action": action,
    }


def _n(value: int) -> str:
    """Thousands-separated, so a five-digit count is readable in prose."""
    return f"{value:,}"


class _LibraryFacts:
    """Everything the checks read, gathered in one pass over the database.

    Five queries rather than five-per-check: the folder-shaped checks all need
    the same ``id -> folder`` map, and building it twice on a 12k-picture
    library is 28k rows of pointless work.
    """

    def __init__(self, session: Session):
        rows = session.exec(
            select(
                Picture.id,
                Picture.file_path,
                Picture.pixel_sha,
                Picture.size_bytes,
                Picture.stack_id,
            ).where(Picture.deleted.is_(False))
        ).all()

        self.total = len(rows)
        # Every count on this screen is over live pictures. Scrapheap rows are
        # excluded from the picture query above, so the membership tables below
        # - which have no `deleted` column of their own - are intersected with
        # this set rather than trusted.
        self.live_ids: set[int] = {row[0] for row in rows}
        # id -> folder, for the pictures that have one. os.path.dirname on a
        # flat vault name returns "", which is the test for "no folder".
        self.folder_of: dict[int, str] = {}
        # The exact-duplicate identity of tier 1 (dedup_tier_service): the
        # sampled digest AND the size, because the digest alone collides.
        self.content_key: dict[int, str] = {}
        for pic_id, file_path, pixel_sha, size_bytes, _stack_id in rows:
            if file_path:
                folder = os.path.dirname(file_path)
                if folder:
                    self.folder_of[pic_id] = folder
            if pixel_sha:
                self.content_key[pic_id] = f"{pixel_sha}:{size_bytes}"

        self.by_folder: dict[str, list[int]] = defaultdict(list)
        for pic_id, folder in self.folder_of.items():
            self.by_folder[folder].append(pic_id)

        # Which stack each picture is in, so a count can be turned into the
        # number of ROWS the grid will draw. Every grid request the app makes
        # carries ``fields=grid``, which is ``stack_leaders_only``: a stack of
        # eight is one row. Counting pictures put "30 pictures" on a finding
        # whose button opened fifteen rows.
        self.stack_of: dict[int, Optional[int]] = {row[0]: row[4] for row in rows}

        # "Unassigned" in the app's own sense, and taken from the app's own
        # predicate rather than restated here: a named face or a set membership
        # counts as assigned, and either counts for every picture in the same
        # stack. Restating it in Python drifted immediately - the first version
        # added project membership (which the app does not treat as assignment)
        # and dropped the stack arm - so the pile this screen counts was not the
        # pile the view its button opens shows.
        self.unassigned: set[int] = set(
            session.exec(
                select(Picture.id).where(
                    *Picture.build_unassigned_conditions(enforce_stack_assignment=True),
                    Picture.deleted.is_(False),
                )
            ).all()
        )

        # A face row with ``face_index == -1`` is the SENTINEL the extractor
        # writes for a picture where it found NO face
        # (``FaceExtractionTask``), and its ``character_id`` is NULL like any
        # unnamed face. Counting it says "this picture holds a face nobody has
        # named" about a picture holding no face at all - and on a scanned
        # library most pictures carry one, so the finding fired on nearly
        # everything and its button opened an empty grid. Every other reader of
        # this table excludes it (``Face.find``, ``_NO_REAL_FACE_SQL``, and the
        # ``with_face`` predicate the destination itself compiles), so this one
        # must too or the count and the grid are answering different questions.
        self.unnamed_face_pictures: set[int] = (
            set(
                session.exec(
                    select(Face.picture_id).where(
                        Face.character_id.is_(None),
                        Face.face_index != REAL_FACE_SENTINEL_INDEX,
                    )
                ).all()
            )
            & self.live_ids
        )

        self.uncaptioned: int = (
            session.exec(
                select(func.count(Picture.id)).where(
                    Picture.deleted.is_(False), _uncaptioned_clause()
                )
            ).one()
            or 0
        )
        self.captioned_by_folder: Counter = Counter()
        for pic_id in session.exec(
            select(Picture.id).where(Picture.deleted.is_(False), ~_uncaptioned_clause())
        ).all():
            folder = self.folder_of.get(pic_id)
            if folder:
                self.captioned_by_folder[folder] += 1

        self.tagged: set[int] = (
            set(session.exec(select(Tag.picture_id).distinct()).all()) & self.live_ids
        )

    def rows(self, picture_ids) -> int:
        """How many ROWS the grid will draw for these pictures.

        Every grid request the app makes carries ``fields=grid``, which the
        listing route maps to ``stack_leaders_only``: a stack is one row however
        many pictures are in it. A finding that counts pictures therefore states
        a number its own button cannot produce.

        A stack counts once; a picture in no stack counts as itself.
        """
        return len(
            {
                ("stack", stack)
                if (stack := self.stack_of.get(pic_id)) is not None
                # A picture in no stack is its own row. `is not None` and not a
                # truthiness test: a stack id of 0 is falsy and would collapse
                # every unstacked picture into one row.
                else ("picture", pic_id)
                for pic_id in picture_ids
            }
        )

    def label(self, folder: str) -> str:
        """The last component of a folder path - what the owner calls it.

        For the PROSE only. The absolute path still travels in ``action.path``,
        because the tool the button opens needs it, and the client puts it in
        the address bar. What this avoids is an absolute path in the middle of
        a sentence on the release's screenshot: the leaf carries the meaning,
        and the rest is the owner's disk layout.
        """
        return os.path.basename(folder.rstrip("/\\")) or folder


# ---------------------------------------------------------------------------
# The checks. One function each, each returning exactly one finding.
# ---------------------------------------------------------------------------


def _no_folders(facts: "_LibraryFacts", finding_id: str) -> Optional[dict]:
    """The folder-shaped checks' shared "there is no folder tree here" answer.

    A library made entirely of pictures copied into the vault has no folder
    names to read, so both folder checks are vacuously clear - and saying
    "none of your 0 folders" instead would be a number nobody can act on. An
    EMPTY library has neither, so it gets its own wording: "all 0 pictures live
    in the vault" is true and reads as a bug.
    """
    if facts.by_folder:
        return None
    if not facts.total:
        return _finding(
            finding_id,
            state="clear",
            title="There is nothing here to read yet",
            evidence=(
                "The library is empty. Add the folder you already organised as a "
                "reference folder and PixlStash will read it where it sits - "
                "nothing moves and nothing is renamed."
            ),
        )
    return _finding(
        finding_id,
        state="clear",
        title="PixlStash is not reading any folder names yet",
        evidence=(
            f"All {_n(facts.total)} pictures live in the vault under names "
            "PixlStash chose, so there is no folder structure of yours to have an "
            "opinion about. Add the folder you already organised as a reference "
            "folder and this screen will read it in place."
        ),
    )


def _check_unsorted_pile(facts: _LibraryFacts) -> dict:
    """The largest folder whose pictures carry no set and nobody's name.

    "Assigned" is `Picture.build_unassigned_conditions`' definition, not one of
    this module's own - see `_LibraryFacts`.
    """
    vacuous = _no_folders(facts, "unsorted_pile")
    if vacuous is not None:
        return vacuous

    piles = [
        (folder, [p for p in ids if p in facts.unassigned])
        for folder, ids in facts.by_folder.items()
    ]
    piles = [(folder, ids) for folder, ids in piles if ids]
    unassigned_total = facts.rows([p for _, ids in piles for p in ids])

    if not piles:
        return _finding(
            "unsorted_pile",
            state="clear",
            title="Every folder you have has something attached to it",
            evidence=(
                f"All {_n(len(facts.by_folder))} folders hold pictures that are in a "
                "set or under someone's name. There is no pile with nothing said "
                "about it."
            ),
        )

    # Ranked and reported in rows, because that is what the button shows.
    piles = [(folder, ids, facts.rows(ids)) for folder, ids in piles]
    folder, ids, pile_rows = max(piles, key=lambda triple: triple[2])
    name = facts.label(folder)
    if pile_rows < PILE_MIN_PICTURES:
        return _finding(
            "unsorted_pile",
            state="clear",
            title="Nothing is sitting in a folder on its own",
            evidence=(
                f"{_n(unassigned_total)} pictures are in no set and under nobody's "
                f"name, and the biggest single group of them is {_n(pile_rows)} in "
                f"{name}. That is loose files, not an unsorted pile."
            ),
        )

    return _finding(
        "unsorted_pile",
        state="todo",
        title=f"{_n(pile_rows)} pictures are in {name} and nowhere else",
        evidence=(
            f"{name} holds {_n(facts.rows(facts.by_folder[folder]))} pictures, and "
            f"{_n(pile_rows)} of them are in no set and under nobody's name. It is "
            "the largest group in your library with no meaning attached."
        ),
        action={
            "label": "Sort them",
            "note": "rapid triage",
            "kind": "unassigned_in_folder",
            "path": folder,
            "folder_label": name,
        },
    )


def _duplicate_scope(facts: "_LibraryFacts", left: str, right: str) -> Optional[str]:
    """The folder to scope the duplicate queue to for a cross-folder overlap.

    **Not either of the two folders.** Tier 1 is
    ``GROUP BY pixel_sha, size_bytes HAVING count(*) > 1`` with the scope
    predicate applied *inside* the aggregate
    (:func:`~pixlstash.services.dedup_tier_service.find_exact_groups_in_session`),
    so a scope holding one copy of each shared file sees count 1 and finds
    nothing. Scoping to `left` - which this did first - sent the owner to an
    empty queue for a finding that had just told them 90 of 100 pictures were
    duplicated, on exactly the un-swept library the finding is designed for.

    The queue's folder scope is a **sub-tree** prefix match, so the common
    ancestor holds both copies and the group forms.

    Returns ``None`` when there is no ancestor worth naming, and the caller
    opens the queue unscoped and says so. Three ways that happens:

    * **No common path at all** - different Windows drives, or one relative and
      one absolute.
    * **A filesystem root.** ``/`` is a whole-vault scan wearing a folder's
      name.
    * **An ancestor that narrows nothing.** Two unrelated trees under one home
      directory (``~/Pictures`` and ``~/Downloads``) are *siblings*, structurally
      identical to ``library/selects`` and ``library/final``, so no rule about
      path shape can tell them apart - the first version of this tried, and
      admitted ``/home/<user>`` behind a scope pill reading the login name.

      What does tell them apart is measurable: how much of the library the
      ancestor's sub-tree actually holds. Above
      :data:`SCOPE_MAX_WIDENING` times the two folders' own size it is not a
      narrowing, it is the whole library wearing a folder's name, and the
      unscoped queue is the honest thing to open.

    Relative paths are refused outright: the queue's folder predicate is
    ``file_path LIKE '<prefix>%'`` against absolute stored paths, so a relative
    ancestor matches nothing and the queue would be silently empty.
    """
    if not (os.path.isabs(left) and os.path.isabs(right)):
        return None
    try:
        common = os.path.commonpath([left, right])
    except ValueError:
        # Different Windows drives.
        return None
    # A filesystem root is its own dirname. Anything else is a real folder.
    if os.path.dirname(common) == common:
        return None

    pair = len(facts.by_folder[left]) + len(facts.by_folder[right])
    # Measured with the QUEUE's own predicate, which is `file_path LIKE
    # '<prefix>%'` with NO separator (`DedupScope.picture_predicate`). Counting
    # a separator-bounded sub-tree instead is tidier and wrong: `/lib-archive`
    # is outside `/lib` by that arithmetic and inside the scope the owner is
    # handed, so a prefix-sibling folder walked straight through the ceiling.
    under = sum(
        len(ids) for folder, ids in facts.by_folder.items() if folder.startswith(common)
    )
    return common if pair and under <= pair * SCOPE_MAX_WIDENING else None


def _check_overlapping_folders(facts: _LibraryFacts) -> dict:
    """Two folders holding mostly the same pictures, byte for byte.

    Grouped on the tier-1 key rather than on a scan's results, so the finding
    is true on a library nobody has swept yet - and it is the same identity the
    duplicate queue would find, so the button opens onto the same pictures.
    """
    vacuous = _no_folders(facts, "overlapping_folders")
    if vacuous is not None:
        return vacuous

    folders_by_key: dict[str, set[str]] = defaultdict(set)
    for pic_id, folder in facts.folder_of.items():
        key = facts.content_key.get(pic_id)
        if key:
            folders_by_key[key].add(folder)

    # Pairwise, and bounded rather than quadratic in the library: a key's
    # folder set can hold at most one folder per picture carrying that content,
    # so the total work is sum(k^2) <= max(k) * sum(k) <= folders * pictures.
    # On the 12k-picture, 200-folder library this release is sized for that is
    # at most ~2.4M set operations, and in practice k is 1 or 2 for almost
    # every key.
    # ponytail: exact pairs, in Python. If a library ever makes this the slow
    # part, the shape to reach for is a per-folder signature and a threshold
    # join in SQL, not a cache.
    shared: Counter = Counter()
    for folders in folders_by_key.values():
        ordered = sorted(folders)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                shared[(left, right)] += 1

    best = None
    for (left, right), count in shared.items():
        # Measured against the SMALLER of the two, so "one folder is a copy of
        # part of another" is found rather than diluted by the big one's size.
        # That is also why the title below names the smaller folder first: at
        # 100% the two are not "the same pictures", the small one is inside the
        # big one, and saying they are the same would be a claim about the big
        # one that is not true.
        small, big = sorted((left, right), key=lambda f: len(facts.by_folder[f]))
        smaller = len(facts.by_folder[small])
        if smaller == 0 or count < OVERLAP_MIN_PICTURES:
            continue
        share = count / smaller
        if share < OVERLAP_MIN_SHARE:
            continue
        if best is None or share > best[0]:
            best = (share, small, big, count, smaller)

    if best is None:
        return _finding(
            "overlapping_folders",
            state="clear",
            title="No two of your folders are copies of each other",
            evidence=(
                f"Every pair among {_n(len(facts.by_folder))} folders was compared on "
                "the same identity the duplicate queue uses, and none of them holds "
                "most of another one's pictures."
            ),
        )

    share, small, big, count, smaller = best
    small_name, big_name = facts.label(small), facts.label(big)
    scope = _duplicate_scope(facts, small, big)
    action = (
        {
            "label": "Compare them",
            "note": f"duplicates under {facts.label(scope)}",
            "kind": "duplicates_in_folder",
            "path": scope,
            "folder_label": facts.label(scope),
        }
        if scope
        else {
            "label": "Compare them",
            "note": "duplicate queue",
            "kind": "duplicates",
        }
    )
    return _finding(
        "overlapping_folders",
        state="todo",
        title=(
            f"Every picture in {small_name} is also in {big_name}"
            if count >= smaller
            else (f"{round(share * 100)}% of {small_name} is also in {big_name}")
        ),
        evidence=(
            f"{_n(count)} of the {_n(smaller)} pictures in {small_name} are the same "
            f"file in {big_name}, byte for byte. Most likely a copy you made once and "
            "both folders then grew. Stacking them keeps both paths working - nothing "
            "is deleted and nothing moves."
        ),
        action=action,
    )


def _check_uncaptioned(facts: _LibraryFacts) -> dict:
    """How much of the library can be searched by description."""
    captioned = facts.total - facts.uncaptioned
    if facts.total == 0 or captioned >= facts.total * MOSTLY:
        return _finding(
            "uncaptioned",
            state="clear",
            title="Your library is described",
            evidence=(
                f"{_n(captioned)} of {_n(facts.total)} pictures carry a caption, so "
                "searching by description reaches almost all of them."
            ),
        )

    where = ""
    if facts.captioned_by_folder:
        top_folder, top_count = facts.captioned_by_folder.most_common(1)[0]
        if captioned and top_count >= captioned * MOSTLY:
            where = (
                f" The {_n(captioned)} that do are almost all under "
                f"{facts.label(top_folder)}."
            )
    return _finding(
        "uncaptioned",
        state="todo",
        title=f"{_n(facts.uncaptioned)} pictures have no caption",
        evidence=(
            "A picture with no caption cannot be found by describing it, only by "
            f"where it sits and what is tagged on it.{where} PixlStash captions in "
            "the background once a captioner is switched on, and never overwrites "
            "one you wrote."
        ),
        action={
            "label": "Choose a captioner",
            "note": "Settings ▸ Models",
            "kind": "settings",
            "tab": "behaviour",
        },
    )


def _check_unnamed_faces(facts: _LibraryFacts) -> dict:
    """Pictures where PixlStash found a person and the owner has said nothing.

    The **intersection** of "holds a face" and unassigned, not just the first
    half, because the second half is what the destination is. `/character/
    UNASSIGNED?face=with_face` is exactly this set: unassigned means no face on
    it is named, and `with_face` means there is one. Counting every picture
    holding an unnamed face would have included ones already in a set, which
    that view excludes - a number the button could not show.
    """
    unnamed = facts.unnamed_face_pictures & facts.unassigned
    if not unnamed:
        return _finding(
            "unnamed_faces",
            state="clear",
            title="Every face PixlStash has found is somewhere you can reach it",
            evidence=(
                "No picture holds a face with no name on it while also being in no "
                "set, so asking for a person reaches all of them."
            ),
        )

    folders = {
        facts.folder_of[pic_id] for pic_id in unnamed if pic_id in facts.folder_of
    }
    spread = ""
    if len(folders) > 1:
        by_size = Counter(
            facts.folder_of[pic_id] for pic_id in unnamed if pic_id in facts.folder_of
        )
        top_folder, _ = by_size.most_common(1)[0]
        spread = (
            f" They are spread over {_n(len(folders))} folders, the largest being "
            f"{facts.label(top_folder)} - and a folder name is no way to find someone "
            f"who also turns up in the other {_n(len(folders) - 1)}."
        )
    return _finding(
        "unnamed_faces",
        state="todo",
        title=f"{_n(facts.rows(unnamed))} pictures hold a face nobody has named",
        evidence=(
            "PixlStash found the face and can match it against every other face in "
            "the library; what it has not got is who it is, and these pictures are "
            f"in no set either, so there is nothing else to find them by.{spread} "
            "Naming one makes every picture of that person reachable at once."
        ),
        action={
            "label": "Name the faces",
            "note": "people review",
            "kind": "unassigned_with_face",
        },
    )


def _check_untagged(facts: _LibraryFacts) -> dict:
    """How much of the library can be searched by what is in the picture."""
    tagged = len(facts.tagged)
    untagged = max(0, facts.total - tagged)
    if facts.total == 0 or tagged >= facts.total * MOSTLY:
        return _finding(
            "untagged",
            state="clear",
            title="Your library is tagged",
            evidence=(
                f"{_n(tagged)} of {_n(facts.total)} pictures carry at least one tag, "
                "so filtering by what is in the picture reaches almost all of them."
            ),
        )
    return _finding(
        "untagged",
        state="todo",
        title=f"{_n(untagged)} pictures carry no tag at all",
        evidence=(
            f"{_n(tagged)} of {_n(facts.total)} have been through a tagger. The rest "
            "can only be found by where they sit, which is the one thing your folder "
            "names already tell you."
        ),
        action={
            "label": "Choose a tagger",
            "note": "Settings ▸ Models",
            "kind": "settings",
            "tab": "behaviour",
        },
    )


_CHECKS = (
    _check_unsorted_pile,
    _check_overlapping_folders,
    _check_uncaptioned,
    _check_unnamed_faces,
    _check_untagged,
)


def build_insights_in_session(session: Session) -> dict:
    """Read the library and return every finding, in both directions.

    The logic half of the pair (§10.1): everything happens on the caller's
    session, so a test can hand it one and never stand up a worker queue.

    Args:
        session: An open read session. Nothing here writes.

    Returns:
        ``{"total_pictures", "folder_pictures", "folders", "findings"}``.
        ``findings`` is ordered todo-first, and within that in check order, so
        the screen leads with what there is to look at without ever dropping
        the rows that came back clear.
    """
    facts = _LibraryFacts(session)

    findings = [check(facts) for check in _CHECKS]
    findings.sort(key=lambda f: 0 if f["state"] == "todo" else 1)

    logger.info(
        "Library insights: %d pictures, %d folders, %d of %d findings to look at.",
        facts.total,
        len(facts.by_folder),
        sum(1 for f in findings if f["state"] == "todo"),
        len(findings),
    )
    return {
        "total_pictures": facts.total,
        "folder_pictures": len(facts.folder_of),
        "folders": len(facts.by_folder),
        "findings": findings,
    }


def build_insights(vault: "Vault") -> dict:
    """The vault wrapper (§10.1): put the read on the DB worker and return it.

    ``run_immediate_read_task`` rather than a queued one because the screen is
    a page load: the owner is looking at a spinner while this runs.

    **This is a full pass over the library, not a handful of indexed lookups** -
    every live picture's id, path, digest, size and stack, plus four more
    whole-table selects - and it holds the engine read lock for its duration.
    That is affordable at the scale this release is sized for (a 12k-picture
    library measures well under a second) and it is why the screen has one
    button that repeats it rather than a poll. If a library ever makes this the
    slow part, the shape to reach for is a cached row per check invalidated by
    the same signals ``tag_health`` uses, not a bigger query.
    """
    return vault.db.run_immediate_read_task(build_insights_in_session)
