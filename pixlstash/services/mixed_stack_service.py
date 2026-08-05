"""Mixed stacks: cohesion scoring, the ``Keep`` dismissal, split and unstack.

A **mixed stack** is a live stack whose members do not form ONE connected
cluster at the queue's similarity threshold (``docs/design/
mixed-stacks-and-stack-units.md``, D5 and B5). The measurement is deliberately
the *same one tier 2 already makes*: the 64-bit dHash in
``picture.perceptual_hash``, the Hamming distance
:func:`~pixlstash.services.dedup_tier_service._popcount64` computes, the
``max_hamming = int((1 - threshold) * 64)`` cut
:func:`~pixlstash.services.dedup_tier_service.near_pairs_in_bucket` uses, and
the shipped union-find behind
:func:`~pixlstash.services.dedup_tier_service.groups_from_pairs`. A second
notion of "similar" on the same surface would be a bug generator.

Connected components, not the worst pair
----------------------------------------
A legitimate burst chains A~B~C: the ends can be far apart while every step is
tight, so a worst-pair test would condemn exactly the stacks a user most
deliberately made. What matters is whether the members *hang together at all*,
which is a connected-components question. Three numbers fall out of it:

* **component count**: 1 means cohesive, more than 1 means mixed;
* **stranded members**: members in no component with anything else, i.e. the
  ones with no edge at all. These are the "clear stranger" case D5 splits off;
* **weakest edge**: the lowest similarity among the edges that survive at this
  threshold, matching the "confidence is the weakest link" rule the near tier
  already reports per group;
* **each member's closest sibling**: how close that one member gets to anything
  else in the stack, which is the column Compare needs when the question is
  "which of these does not belong" rather than "which copy is better"
  (:class:`MemberEdge`). Two numbers, because the threshold is a question about
  the list and not about the picture: *strongest_edge* is the best edge that
  survives the cut and is therefore absent for a stranded member, and
  *nearest_edge* is the real distance, always. Both are folded out of the same
  edge pass, so they cost no extra query on a page of rows.

Measured on the owner's library at the time this shipped: **26** of the live
stacks are not one cluster at the 0.90 default and **9** at the 0.65 floor,
which is the spectrum the threshold slider drives. Nothing here is bound to a
constant: the same stack is mixed at 0.90 and fine at 0.65, and that is the
point.

The cache
---------
:class:`~pixlstash.db_models.mixed_stack.StackCohesion` caches the
**threshold-independent** half, the near-pair edge list, keyed on the stack's
membership fingerprint. Folding components out of a cached edge list is
microseconds, so a threshold change costs nothing and a membership change
invalidates by construction. Cost is O(sum n^2) over stacks and stacks are
small; the page never walks stacks one at a time
(:func:`cohesion_for_stacks` is batched over every stack a page touches, the
same anti-N+1 rule :func:`~pixlstash.services.dedup_tier_service.load_stack_facts`
follows).
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Iterable, Optional

import numpy as np
from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.db_models.mixed_stack import MixedStackDismissal, StackCohesion
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import dedup_sweep_service, operation_log_service
from pixlstash.services.dedup_tier_service import (
    ID_CHUNK,
    MIN_THRESHOLD,
    PHASH_BITS,
    PHASH_HEX_LEN,
    _pill,
    _popcount64,
    load_stack_facts,
)
from pixlstash.services.set_lock_service import (
    enforce_stack_detach_not_locked,
    locked_sets_freezing_stacks,
)
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.stacking import normalize_stack_positions

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)


# --- Constants --------------------------------------------------------------

MIN_STACK_MEMBERS = 2
"""A stack needs two live members before cohesion means anything."""

MAX_CACHED_HAMMING = int((1.0 - MIN_THRESHOLD) * PHASH_BITS)
"""Widest distance that can ever be an EDGE, so the cached edge list is
threshold-free.

Every API threshold is at or above :data:`~pixlstash.services.dedup_tier_service.MIN_THRESHOLD`
(0.65: the routes carry it as a pydantic ``ge=``, so a lower value is a 422
before any handler runs), and ``max_hamming`` shrinks as the threshold rises.
A pair further apart than this therefore cannot be an edge at *any* admissible
threshold, so dropping it from the cache loses nothing and keeps the stored
edge list small for a stack of near-identical frames.

**It prunes edges only, never the per-member closest sibling.** That claim,
"loses nothing", is true of the question *is this pair an edge* and false of the
question *how close does this member get to anything*, which is what the page
has to answer about a member it is calling a stranger. Those distances are
recorded separately and unconditionally (``nearest`` in :func:`_stack_edges`,
``stackcohesion.nearest_edges``), one row per member rather than per pair, so
the honest number survives at O(n) storage while the edge list stays pruned."""

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200

ACTION_SPLIT = "split"
ACTION_UNSTACK = "unstack"
"""The two outcomes D5 names. ``split`` when a majority cluster survives the
removal of the strangers, ``unstack`` when there is no majority to keep."""

OP_TYPE_SPLIT = "dedup.split_stack"
OP_TYPE_UNSTACK = "dedup.unstack"
"""Operation-log verbs for the two actions. Both mutate stack pointers only, so
the recorded before/after snapshot is the whole inverse and a plain undo
restores the stack (``_apply_stack`` recreates a ``PictureStack`` row the
forward path dissolved, so no post-restore hook is needed)."""


class MixedStackError(Exception):
    """A mixed-stack action could not be applied (unknown or empty stack, ...)."""


# --- Fingerprint ------------------------------------------------------------


def _digest(text: str) -> str:
    """32 lowercase hex characters (128 bits of SHA-256).

    Truncated because these are cache and dismissal keys, not security
    boundaries, and 128 bits is already far beyond collision range for a
    library's worth of stacks.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def membership_fingerprint(picture_ids: Iterable[int]) -> str:
    """Digest a stack's live membership, order-independently.

    **The ``Keep`` dismissal's key**, and only that. D5 is explicit about what
    re-raises a kept stack: *adding a member*. Sorted before hashing so the
    canonical stack order (which a reorder changes without changing *who* is in
    the stack) never retracts a Keep; a member joining or leaving always does.

    Deliberately **not** the cohesion cache's key, see
    :func:`content_fingerprint`.

    Args:
        picture_ids: The stack's live member ids, in any order.

    Returns:
        32 lowercase hex characters. See :func:`_digest`.
    """
    ordered = sorted({int(pid) for pid in picture_ids})
    return _digest(",".join(str(pid) for pid in ordered))


def content_fingerprint(
    picture_ids: Iterable[int], hashes_by_picture: dict[int, int]
) -> str:
    """Digest every input the cached edge list is derived from.

    **The cohesion cache's key**, covering the member ids *and their perceptual
    hashes*. A membership-only key would be wrong here: a hash can move without
    membership moving: the embedding worker filling a ``NULL``, or a
    reference-folder file being replaced under an unchanged picture row and a
    membership-keyed cache would then keep serving edges derived from hashes
    that no longer exist. The visible symptom is the one this feature must never
    produce: a member frozen as "stranded" forever because its hash arrived
    after the cache did.

    Args:
        picture_ids: The stack's live member ids, in any order.
        hashes_by_picture: ``{picture_id: dhash}``; a member absent from this
            map is folded in as ``-`` (no usable hash), which is itself a state
            worth invalidating on.

    Returns:
        32 lowercase hex characters. See :func:`_digest`.
    """
    ordered = sorted({int(pid) for pid in picture_ids})
    return _digest(
        ";".join(f"{pid}:{hashes_by_picture.get(pid, '-')}" for pid in ordered)
    )


# --- Cohesion ---------------------------------------------------------------


@dataclass(frozen=True)
class MemberEdge:
    """How close one member gets to any sibling in its own stack. Two answers.

    The evidence column the Mixed stacks page needs and the duplicate queue does
    not. Compare's other metrics answer *which copy is the better file*; on this
    page the question is *which of these does not belong*, and the honest answer
    is how close each member gets to its nearest sibling.

    **Two fields, two jobs, and they must not be collapsed into one.**
    *strongest_edge* is thresholded: it is the best edge that SURVIVES at the
    row's threshold, so it is ``None`` for a stranded member *by construction*
    and that ``None`` is what the stranded decision is made on. *nearest_edge*
    is unconditional: the real distance to the real closest sibling, whatever it
    is. One decides membership of the list; the other is always the truth. Using
    the thresholded value for both is what made the page print an en dash and
    claim a picture matched nothing, about members whose closest sibling was 89%
    similar.

    Attributes:
        picture_id: The member.
        strongest_edge: Highest similarity among this member's **surviving**
            edges, or ``None`` when it has none. ``None`` covers three different
            facts and the row's other fields say which: the member is in
            ``stranded_picture_ids`` (it has a hash and still matches nothing
            *at this threshold*), it is in ``unhashed_picture_ids`` (it could not
            be compared at all), or the stack has a single member and there is no
            sibling to match. Deliberately **not** a fourth vocabulary for the
            unhashed case: that word already exists on the row and inventing a
            second one is how two surfaces start disagreeing.
        closest_picture_id: The sibling on the other end of that surviving edge,
            so Compare can name it rather than only score it. ``None`` exactly
            when *strongest_edge* is. Ties (two siblings at the same distance)
            resolve to the lower picture id, so the payload is reproducible.
        nearest_edge: Similarity to this member's closest sibling, **never
            thresholded and never pruned at the cache's edge floor**. ``None``
            only when there is genuinely nothing to compare against: this member
            has no usable ``perceptual_hash``, or no other member of the stack
            has one. It is therefore ``>= strongest_edge`` always, and exactly
            equal whenever *strongest_edge* is not ``None`` (the closest pair is
            the first to survive any cut).
        nearest_picture_id: The sibling that distance was measured to, same tie
            rule. ``None`` exactly when *nearest_edge* is.
    """

    picture_id: int
    strongest_edge: Optional[float]
    closest_picture_id: Optional[int]
    nearest_edge: Optional[float] = None
    nearest_picture_id: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        """The wire shape of one member's edge evidence."""
        return {
            "picture_id": self.picture_id,
            "strongest_edge": (
                round(float(self.strongest_edge), 6)
                if self.strongest_edge is not None
                else None
            ),
            "closest_picture_id": self.closest_picture_id,
            "nearest_edge": (
                round(float(self.nearest_edge), 6)
                if self.nearest_edge is not None
                else None
            ),
            "nearest_picture_id": self.nearest_picture_id,
        }


@dataclass(frozen=True)
class CohesionReport:
    """One stack's cohesion at one threshold.

    Attributes:
        stack_id: The stack.
        threshold: The similarity the components were folded at. The report is
            only true for this value: the same stack is mixed at 0.90 and
            cohesive at 0.65.
        member_ids: Every live member, canonical stack order (leader first).
        membership_fingerprint: :func:`membership_fingerprint` of *member_ids*.
        components: Connected components, largest first, each sorted by id.
            A cohesive stack has exactly one.
        stranded_picture_ids: Members with no edge to any sibling, in no
            component with anything else. These are the "clear stranger" case.
        weakest_edge: Lowest similarity among the surviving edges, or ``None``
            when no pair is close enough to be an edge at all (every member
            stranded). The "weakest link" reading the near tier already uses.
        unhashed_picture_ids: Members with no usable ``perceptual_hash``. They
            can carry no edge, so without this they would be indistinguishable
            from a genuinely stranded member; they are reported separately so
            the list can say "not comparable yet" rather than "does not belong".
        member_edges: One :class:`MemberEdge` per member, canonical stack order,
            so the payload is parallel to *member_ids*. Folded out of the same
            edge list the components come from, so it costs no extra query on a
            page of rows.
    """

    stack_id: int
    threshold: float
    member_ids: tuple[int, ...]
    membership_fingerprint: str
    components: tuple[tuple[int, ...], ...]
    stranded_picture_ids: tuple[int, ...]
    weakest_edge: Optional[float]
    unhashed_picture_ids: tuple[int, ...]
    member_edges: tuple[MemberEdge, ...] = ()

    @property
    def member_count(self) -> int:
        """Live members of the stack."""
        return len(self.member_ids)

    @property
    def component_count(self) -> int:
        """Connected clusters. ``1`` is a cohesive stack."""
        return len(self.components)

    @property
    def largest_component_size(self) -> int:
        """Size of the biggest cluster, ``0`` for an empty stack."""
        return len(self.components[0]) if self.components else 0

    @property
    def is_mixed(self) -> bool:
        """True when the members do not form ONE connected cluster."""
        return self.component_count > 1

    @property
    def suggested_action(self) -> str:
        """The outcome the primary button names (D5).

        ``split`` needs two things: somebody to strand, and a **strict majority
        cluster** to keep. Splitting five strangers off a stack of twelve leaves
        a coherent seven and is a split; pulling four strangers out of a stack
        of ten whose biggest cluster is a pair leaves nothing worth calling a
        stack, so that is an ``unstack``. "No majority cluster" is the exact
        wording D5 uses for the unstack case.
        """
        remaining = self.member_count - len(self.stranded_picture_ids)
        if (
            self.stranded_picture_ids
            and self.largest_component_size >= MIN_STACK_MEMBERS
            and self.largest_component_size
            > self.member_count - self.largest_component_size
            and remaining >= MIN_STACK_MEMBERS
        ):
            return ACTION_SPLIT
        return ACTION_UNSTACK

    @property
    def rank_key(self) -> tuple:
        """Sort key: least-held-together first (D5's ranking, exactly).

        Stranded members descending, then component count descending, then
        weakest edge ascending. A stack with no edge at all has no weakest edge;
        it sorts *first* within its tier (``-1.0``) because nothing holds it
        together at all, which is the extreme of "weakest edge ascending" rather
        than an absence of information. ``stack_id`` last so the order is total
        and a page boundary is reproducible.
        """
        return (
            -len(self.stranded_picture_ids),
            -self.component_count,
            self.weakest_edge if self.weakest_edge is not None else -1.0,
            self.stack_id,
        )

    def as_dict(self) -> dict[str, Any]:
        """The wire shape of one row of the Mixed stacks list."""
        return {
            "stack_id": self.stack_id,
            "threshold": round(float(self.threshold), 6),
            "member_count": self.member_count,
            "member_ids": list(self.member_ids),
            "membership_fingerprint": self.membership_fingerprint,
            "component_count": self.component_count,
            "component_sizes": [len(component) for component in self.components],
            "components": [list(component) for component in self.components],
            "largest_component_size": self.largest_component_size,
            "stranded_picture_ids": list(self.stranded_picture_ids),
            "weakest_edge": (
                round(float(self.weakest_edge), 6)
                if self.weakest_edge is not None
                else None
            ),
            "unhashed_picture_ids": list(self.unhashed_picture_ids),
            "member_edges": [edge.as_dict() for edge in self.member_edges],
            "suggested_action": self.suggested_action,
            "why": build_mixed_stack_evidence(self),
        }


def _plural(count: int, singular: str, plural: str) -> str:
    """``"1 picture"`` / ``"3 pictures"``, so no pill ever says "1 pictures"."""
    return f"{count} {singular if count == 1 else plural}"


def _percent(similarity: float) -> int:
    """One similarity as whole percent, the same rounding every pill uses."""
    return int(round(float(similarity) * 100))


def _stranger_pill_text(count: int, percents: list[int]) -> str:
    """``"1 picture is only 89% like the rest"``: the strangers, by number.

    The pill this replaced said *matches nothing else*, which is a false
    statement about a member whose closest sibling is 7 bits away out of 64. It
    matches nothing **at this threshold**, and the number is the fact the user
    actually needs in order to disagree with the cut.

    A range when the strangers differ, because one number would have to be
    either the best or the worst of them and both would misdescribe the others.
    The value quoted is each member's *closest* sibling, so "only 89% like the
    rest" is the strongest thing that can be said for it, never an average
    dressed up as a bound.

    Args:
        count: How many strangers the pill is about; always ``len(percents)``
            unless a caller lost an edge, which is why it is passed rather than
            inferred.
        percents: Their closest-sibling similarities, as whole percent.
    """
    low, high = min(percents), max(percents)
    span = f"{high}%" if low == high else f"{low}-{high}%"
    return (
        f"{_plural(count, 'picture', 'pictures')} "
        f"{'is' if count == 1 else 'are'} only {span} like the rest"
    )


def _component_structure_pill(
    component_count: int,
    component_sizes: Iterable[Any] | None,
    member_count: int | None = None,
) -> dict[str, Any] | None:
    """Describe component structure qualitatively, with exact accessible detail.

    ``component_sizes`` remains the structured wire value. The visible label
    describes the decision-relevant shape without making the user parse a long
    arithmetic expression; ``accessible_text`` retains the exact distribution
    in natural language. Invalid data never earns a majority claim. A valid
    cohesive one-component report omits the structure pill altogether.
    """
    valid_component_count = isinstance(component_count, int) and not isinstance(
        component_count, bool
    )
    if valid_component_count and component_count == 1:
        return None

    if valid_component_count and component_count > 1:
        fallback_text = f"{component_count} groups don't overlap"
    else:
        fallback_text = "Pictures don't all match"
    fallback = {
        "text": fallback_text,
        "against": True,
        "accessible_text": f"{fallback_text}.",
    }
    if not valid_component_count or component_count <= 1 or component_sizes is None:
        return fallback

    sizes = list(component_sizes)
    valid_sizes = all(
        isinstance(size, int) and not isinstance(size, bool) and size > 0
        for size in sizes
    )
    valid_member_count = (
        valid_sizes
        and isinstance(member_count, int)
        and not isinstance(member_count, bool)
        and member_count > 0
        and sum(sizes) == member_count
    )
    if len(sizes) != component_count or not valid_sizes or not valid_member_count:
        return fallback

    member_total = int(member_count)
    frequencies = Counter(sizes)
    ordered = sorted(frequencies.items(), reverse=True)
    singleton_count = frequencies.get(1, 0)
    largest_size = max(sizes)
    if singleton_count == component_count:
        text = "All pictures differ"
    elif singleton_count > member_total / 2:
        text = "Most pictures differ"
    elif largest_size > member_total / 2:
        outlier_count = member_total - largest_size
        if outlier_count == 1:
            text = "1 picture differs from the rest"
        else:
            text = f"{outlier_count} pictures differ from the main group"
    elif component_count == 2:
        text = "2 groups don't match each other"
    else:
        text = "Several groups don't overlap"

    spoken_terms = []
    for size, frequency in ordered:
        if size == 1:
            spoken_terms.append(
                f"{frequency} single-picture {'group' if frequency == 1 else 'groups'}"
            )
        else:
            spoken_terms.append(
                f"{_plural(frequency, 'group', 'groups')} of "
                f"{_plural(size, 'picture', 'pictures')}"
            )
    if len(spoken_terms) == 1:
        spoken_distribution = spoken_terms[0]
    elif len(spoken_terms) == 2:
        spoken_distribution = " and ".join(spoken_terms)
    else:
        spoken_distribution = ", ".join(spoken_terms[:-1]) + f", and {spoken_terms[-1]}"
    return {
        "text": text,
        "against": True,
        "accessible_text": (
            f"{_plural(component_count, 'group', 'groups')}: {spoken_distribution}."
        ),
    }


def build_mixed_stack_evidence(report: "CohesionReport") -> list[dict[str, Any]]:
    """The row-level why-pills for one mixed stack, in the shipped pill shape.

    Deliberately the **same** ``[{text, against}]`` contract
    :func:`~pixlstash.services.dedup_tier_service.build_group_evidence` produces
    and ``WhyPillModel`` serialises, so the queue row's shipped pill component
    renders this list with no second code path. Only the content differs, because
    the question differs: a duplicate group's pills argue about whether these
    pictures are the same picture, and a mixed stack's argue about how little
    holds an existing stack together.

    ``against`` keeps its shipped meaning, *this argues against these pictures
    belonging in one stack*: the structure and the strangers are red, the thread
    that does hold is olive.

    Three things get said, in the order the eye needs them:

    * **the strangers**, members with no edge at this threshold, named by *how
      unlike the rest they actually are* (``1 picture is only 89% like the
      rest``), which is the strong case D5 marks on a tile;
    * **the component structure**, for example ``1 picture differs from the
      rest`` or ``All pictures differ``, the definition of the row and the one
      fact a count of strangers cannot convey (two clusters of three strand
      nobody at all); the exact distribution remains in accessible text;
    * **the weakest surviving edge**, the thinnest thread still holding the
      stack together, the same weakest-link reading a duplicate group's
      ``confidence`` carries, or the flat statement that there is no thread.

    **A stranger is described by its number, never as matching nothing.** The
    two are not the same claim: "matches nothing else" is unfalsifiable and, for
    a member 7 bits from its neighbour at a 6-bit cut, simply untrue. The pill
    quotes :attr:`MemberEdge.nearest_edge`, which is measured regardless of the
    threshold, so the user can see the cut is what made the stranger and move it
    if they disagree.

    **An unhashed member is subtracted from the stranger count and reported on
    its own.** A member with no usable ``perceptual_hash`` can carry no edge, so
    :func:`_fold_components` necessarily lists it as stranded; describing a
    picture nothing has compared yet as unlike anything is the one false
    positive this feature cannot afford. Its pill is not ``against``, because it
    argues for waiting rather than for breaking the stack up. **When fewer than
    two members can be compared at all**, that is the whole story of the row:
    every component is then an artefact of the missing hashes, so the structure
    and weakest-edge pills are suppressed rather than made to describe an
    arithmetic accident.

    Args:
        report: The stack's cohesion at the threshold the row was computed at.

    Returns:
        ``[{"text": str, "against": bool}, ...]``, ready to serialise as
        ``WhyPillModel``. Never empty for a mixed stack, and always empty below
        :data:`MIN_STACK_MEMBERS`.
    """
    pills: list[dict[str, Any]] = []
    if report.member_count < MIN_STACK_MEMBERS:
        # A lone member is in ``stranded_picture_ids`` by construction (it has
        # no edge, because it has no sibling), so any pill about how it compares
        # to the rest would be an accusation made of arithmetic. Cohesion is
        # undefined below two members, so the row says nothing.
        return pills

    unhashed = set(report.unhashed_picture_ids)
    strangers = [pid for pid in report.stranded_picture_ids if pid not in unhashed]

    if report.member_count - len(unhashed) < MIN_STACK_MEMBERS:
        # Fewer than two members carry a usable hash, so no pair in this stack
        # has ever been compared. Every component is a singleton for that reason
        # alone and every member is "stranded" by arithmetic; reporting the
        # structure would dress an absence of data up as a verdict.
        return [_pill("Nothing here can be compared yet")]

    if strangers:
        edges_by_picture = {edge.picture_id: edge for edge in report.member_edges}
        percents = [
            _percent(edges_by_picture[pid].nearest_edge)
            for pid in strangers
            if pid in edges_by_picture
            and edges_by_picture[pid].nearest_edge is not None
        ]
        if percents:
            pills.append(
                _pill(_stranger_pill_text(len(strangers), percents), against=True)
            )
        else:
            # Unreachable from cohesion_for_stacks: two or more members are
            # comparable here (checked above), so every hashed member has a
            # measured closest sibling. A report assembled without member_edges
            # would land here, and the pill must still avoid the "matches
            # nothing" claim it cannot support.
            logger.warning(
                "[mixed-stacks] stack %s has %d stranded member(s) with no "
                "measured closest sibling although %d member(s) are comparable; "
                "the why-pill cannot name a similarity and falls back to the "
                "threshold-relative wording",
                report.stack_id,
                len(strangers),
                report.member_count - len(unhashed),
            )
            pills.append(
                _pill(
                    f"{_plural(len(strangers), 'picture', 'pictures')} "
                    f"{'does' if len(strangers) == 1 else 'do'} not match the rest",
                    against=True,
                )
            )

    structure_pill = _component_structure_pill(
        report.component_count,
        (len(component) for component in report.components),
        report.member_count,
    )
    if structure_pill is not None:
        pills.append(structure_pill)

    if report.weakest_edge is not None:
        pills.append(_pill(f"Weakest match {_percent(report.weakest_edge)}%"))
    else:
        pills.append(_pill("No two pictures match", against=True))

    if unhashed:
        pills.append(
            _pill(f"{_plural(len(unhashed), 'picture', 'pictures')} not comparable yet")
        )

    return pills


def _fold_components(
    member_ids: tuple[int, ...],
    edges: list[tuple[int, int, int]],
    nearest: list[tuple[int, int, int]],
    threshold: float,
) -> tuple[
    tuple[tuple[int, ...], ...],
    tuple[int, ...],
    Optional[float],
    tuple[MemberEdge, ...],
]:
    """Fold *edges* into connected components over *member_ids*.

    Reuses the shipped union-find (``dedup_sweep_service._LikenessForest``, the
    same one :func:`~pixlstash.services.dedup_tier_service.groups_from_pairs`
    folds near pairs with) rather than growing a second components implementation
    on the same data. The forest only knows nodes an edge named, so members with
    no edge are added back afterwards as their own singleton components; those
    are precisely the stranded members, and losing them would make a stack of
    one cluster plus one stranger look cohesive.

    Args:
        member_ids: Every live member of the stack.
        edges: ``(picture_id_a, picture_id_b, hamming)`` for pairs at or below
            :data:`MAX_CACHED_HAMMING`.
        nearest: ``(picture_id, closest_picture_id, hamming)`` per member with a
            comparable sibling, unthresholded and unpruned. Passed through to
            :attr:`MemberEdge.nearest_edge` untouched: this fold must not filter
            it, because a member's closeness is exactly the fact that survives
            being stranded.
        threshold: Similarity cut; ``max_hamming = int((1 - threshold) * 64)``,
            identical to ``near_pairs_in_bucket``.

    The per-member strongest edge falls out of the same pass: the loop already
    visits every surviving edge, so the evidence column costs one dict update
    per edge and **no extra query**, which is what keeps a page of rows off the
    N+1 path.

    Returns:
        ``(components, stranded_picture_ids, weakest_edge, member_edges)``.
        Components are largest first (ties by lowest member id) and each is
        sorted by id; *member_edges* is parallel to *member_ids*.
    """
    max_hamming = int((1.0 - float(threshold)) * PHASH_BITS)
    live = set(member_ids)
    forest = dedup_sweep_service._LikenessForest()
    weakest: Optional[float] = None
    connected: set[int] = set()
    strongest: dict[int, tuple[float, int]] = {}
    for picture_id_a, picture_id_b, hamming in edges:
        if hamming > max_hamming:
            continue
        if picture_id_a not in live or picture_id_b not in live:
            # A member left the stack between the cache write and this fold.
            # Dropping the edge is correct (it is no longer an edge of THIS
            # stack) and the fingerprint check upstream normally prevents it
            # ever happening, so it is worth a line when it does.
            logger.debug(
                "[mixed-stacks] dropping cached edge %s-%s: no longer both live "
                "members of this stack",
                picture_id_a,
                picture_id_b,
            )
            continue
        similarity = 1.0 - float(hamming) / PHASH_BITS
        forest.add_edge(picture_id_a, picture_id_b, similarity)
        connected.add(picture_id_a)
        connected.add(picture_id_b)
        weakest = similarity if weakest is None else min(weakest, similarity)
        for picture_id, sibling in (
            (picture_id_a, picture_id_b),
            (picture_id_b, picture_id_a),
        ):
            best = strongest.get(picture_id)
            # Ties resolve to the lower sibling id so two equally-close siblings
            # never make the payload flip between requests.
            if best is None or (similarity, -sibling) > (best[0], -best[1]):
                strongest[picture_id] = (similarity, sibling)

    groups = [tuple(members) for members, _low, _high in forest.components(1)]
    stranded = tuple(sorted(pid for pid in live if pid not in connected))
    groups.extend((pid,) for pid in stranded)
    groups.sort(key=lambda component: (-len(component), component[0]))
    closest = {
        int(picture_id): (int(sibling), int(hamming))
        for picture_id, sibling, hamming in nearest
        if int(picture_id) in live and int(sibling) in live
    }
    member_edges = tuple(
        MemberEdge(
            picture_id=picture_id,
            strongest_edge=strongest[picture_id][0]
            if picture_id in strongest
            else None,
            closest_picture_id=strongest[picture_id][1]
            if picture_id in strongest
            else None,
            nearest_edge=(
                1.0 - float(closest[picture_id][1]) / PHASH_BITS
                if picture_id in closest
                else None
            ),
            nearest_picture_id=(
                closest[picture_id][0] if picture_id in closest else None
            ),
        )
        for picture_id in member_ids
    )
    return tuple(groups), stranded, weakest, member_edges


def _absorb_nearest(
    best_distance: np.ndarray,
    best_sibling: np.ndarray,
    slots: np.ndarray,
    distances: np.ndarray,
    siblings: np.ndarray,
) -> None:
    """Keep the closest sibling seen so far for each slot, in place.

    Vectorised so the unconditional pass adds no Python-level loop to the
    O(n^2) comparison. Ties resolve to the **lower sibling id**, the same rule
    the surviving-edge pass uses, so the two numbers on a member agree about
    which sibling they mean and neither flips between requests.

    Args:
        best_distance: Per-slot running minimum, modified in place.
        best_sibling: Per-slot winner, modified in place. Its sentinel must be
            larger than any picture id so the first candidate always wins.
        slots: Row indices this batch of candidates is about.
        distances: Candidate Hamming distance per slot.
        siblings: Candidate sibling picture id per slot.
    """
    better = (distances < best_distance[slots]) | (
        (distances == best_distance[slots]) & (siblings < best_sibling[slots])
    )
    chosen = slots[better]
    best_distance[chosen] = distances[better]
    best_sibling[chosen] = siblings[better]


def _stack_edges(
    hashes_by_picture: dict[int, int], member_ids: tuple[int, ...]
) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    """The stack's near pairs, and every member's closest sibling.

    The comparison is the tier-2 one: XOR the two 64-bit dHashes and popcount
    the result with :func:`~pixlstash.services.dedup_tier_service._popcount64`,
    vectorised over the stack's upper triangle. Stacks are small, so the whole
    O(n^2) is a handful of numpy rows.

    **Two answers out of one pass, because they answer different questions.**
    The edge list is pruned at :data:`MAX_CACHED_HAMMING`, which loses nothing
    when the question is *is this pair an edge*. The per-member closest sibling
    is **not** pruned and not thresholded, because the question there is *how
    close does this member get to anything*, and a member whose nearest sibling
    is 32 bits away has a real answer (50%) that the prune would throw away
    exactly when the page most needs it. It is one row per member beside an
    O(n^2) edge list, so keeping it is O(n) storage and no extra scan.

    Args:
        hashes_by_picture: ``{picture_id: dhash}`` for the members that have a
            usable perceptual hash.
        member_ids: The stack's members, canonical order.

    Returns:
        ``(edges, nearest)``. *edges* is ``(a, b, hamming)`` with ``a < b``,
        sorted, for pairs at or below :data:`MAX_CACHED_HAMMING`. *nearest* is
        ``(picture_id, closest_picture_id, hamming)`` sorted by picture id, one
        entry per member that has a comparable sibling, at any distance. Both
        are empty when fewer than two members can be compared at all.
    """
    usable = [pid for pid in member_ids if pid in hashes_by_picture]
    if len(usable) < 2:
        # Nothing to compare against, which is a different fact from "far from
        # everything" and is reported as such (``unhashed_picture_ids`` and the
        # "nothing here can be compared yet" pill), never as a distance.
        return [], []
    ids = np.array(usable, dtype=np.int64)
    values = np.array([hashes_by_picture[pid] for pid in usable], dtype=np.uint64)
    edges: list[tuple[int, int, int]] = []
    best_distance = np.full(len(usable), PHASH_BITS + 1, dtype=np.int64)
    best_sibling = np.full(len(usable), np.iinfo(np.int64).max, dtype=np.int64)
    for offset in range(1, len(usable)):
        distances = _popcount64(values[:-offset] ^ values[offset:]).astype(np.int64)
        left_slots = np.arange(len(usable) - offset)
        right_slots = left_slots + offset
        _absorb_nearest(
            best_distance, best_sibling, left_slots, distances, ids[right_slots]
        )
        _absorb_nearest(
            best_distance, best_sibling, right_slots, distances, ids[left_slots]
        )
        for index in np.nonzero(distances <= MAX_CACHED_HAMMING)[0]:
            left = int(ids[index])
            right = int(ids[index + offset])
            edges.append((min(left, right), max(left, right), int(distances[index])))
    edges.sort()
    # Every slot saw at least one candidate (there are two or more usable
    # members), so no sentinel can survive to be serialised as a distance.
    nearest = sorted(
        (int(ids[slot]), int(best_sibling[slot]), int(best_distance[slot]))
        for slot in range(len(usable))
    )
    return edges, nearest


def _load_perceptual_hashes(
    session: Session, picture_ids: list[int]
) -> tuple[dict[int, int], set[int]]:
    """Load and parse the 64-bit dHash of *picture_ids*, in chunked batches.

    Returns:
        ``({picture_id: dhash}, unusable_picture_ids)``. A hash that is missing,
        short or unparseable puts the picture in the second set rather than
        silently dropping it: a member that cannot be compared is a different
        fact from a member that does not match, and the list reports it as such.
    """
    hashes: dict[int, int] = {}
    unusable: set[int] = set()
    ordered = sorted({int(pid) for pid in picture_ids})
    for start in range(0, len(ordered), ID_CHUNK):
        chunk = ordered[start : start + ID_CHUNK]
        rows = session.exec(
            select(Picture.id, Picture.perceptual_hash).where(Picture.id.in_(chunk))
        ).all()
        found = set()
        for picture_id, phash in rows:
            found.add(int(picture_id))
            text = str(phash or "")
            if len(text) < PHASH_HEX_LEN:
                unusable.add(int(picture_id))
                continue
            try:
                hashes[int(picture_id)] = int(text[:PHASH_HEX_LEN], 16)
            except ValueError:
                logger.warning(
                    "[mixed-stacks] picture %s has an unparseable perceptual_hash "
                    "%r; it can carry no cohesion edge and is reported as "
                    "not-yet-comparable rather than as a stranded member",
                    picture_id,
                    text,
                )
                unusable.add(int(picture_id))
        for picture_id in chunk:
            if picture_id not in found:
                unusable.add(int(picture_id))
    return hashes, unusable


@dataclass(frozen=True)
class _CachedEdges:
    """One stack's threshold-independent edge set, cached or freshly computed."""

    member_ids: tuple[int, ...]
    membership_fingerprint: str
    content_fingerprint: str
    edges: list[tuple[int, int, int]]
    nearest: list[tuple[int, int, int]]
    unhashed_picture_ids: tuple[int, ...]


def _resolve_stack_inputs(
    session: Session, stack_ids: list[int]
) -> tuple[dict[int, Any], dict[int, int], set[int]]:
    """The two batched reads every cohesion path starts from.

    **Two queries for any number of stacks**, never one per stack: the
    membership read (:func:`~pixlstash.services.dedup_tier_service.load_stack_facts`,
    which also gives the canonical leader-first order) and one chunked read of
    the members' perceptual hashes. The hashes are needed even on a pure cache
    hit, because they are half of :func:`content_fingerprint` and therefore half
    of the staleness question.

    Returns:
        ``(stack_facts, hashes_by_picture, unusable_picture_ids)``.
    """
    facts = load_stack_facts(session, stack_ids)
    member_ids = [
        pid
        for stack_id in stack_ids
        if stack_id in facts
        for pid in facts[stack_id].member_ids
    ]
    hashes, unusable = _load_perceptual_hashes(session, member_ids)
    return facts, hashes, unusable


def _edges_for_stacks(
    session: Session, stack_ids: Iterable[int]
) -> dict[int, _CachedEdges]:
    """Edge sets for every stack in *stack_ids*, reading the cache where valid.

    A cache row is exact however old it is: database triggers delete it in the
    same transaction whenever live membership, deletion state, or a perceptual
    hash changes. Missing rows are recomputed here in one batch. A hit therefore
    avoids both the O(n^2) comparison and all picture/hash reads.

    This function never writes. The cache is owned by
    :class:`~pixlstash.tasks.stack_cohesion_task.StackCohesionTask`, so a read
    path can never turn into a writer behind a GET; a cache miss simply costs
    the (small) recomputation until the finder catches up.
    """
    wanted = sorted({int(sid) for sid in stack_ids})
    if not wanted:
        return {}
    cached: dict[int, StackCohesion] = {}
    for start in range(0, len(wanted), ID_CHUNK):
        chunk = wanted[start : start + ID_CHUNK]
        for row in session.exec(
            select(StackCohesion).where(StackCohesion.stack_id.in_(chunk))
        ).all():
            cached[int(row.stack_id)] = row

    # Database triggers delete a row whenever live membership, deletion state,
    # or a perceptual hash changes. Presence therefore means validity; rereading
    # every member hash to validate a hit would retain the expensive rescan the
    # cache exists to remove.
    missing = [stack_id for stack_id in wanted if stack_id not in cached]
    facts, hashes, unusable = (
        _resolve_stack_inputs(session, missing) if missing else ({}, {}, set())
    )

    result: dict[int, _CachedEdges] = {}
    recomputed = 0
    for stack_id in wanted:
        row = cached.get(stack_id)
        if row is not None:
            members = tuple(int(pid) for pid in json.loads(row.member_ids or "[]"))
            content = str(row.content_fingerprint)
            edges = [
                (int(a), int(b), int(distance))
                for a, b, distance in json.loads(row.edges or "[]")
            ]
            nearest = [
                (int(picture_id), int(sibling), int(distance))
                for picture_id, sibling, distance in json.loads(
                    row.nearest_edges or "[]"
                )
            ]
            unhashed = tuple(
                int(pid) for pid in json.loads(row.unhashed_picture_ids or "[]")
            )
        else:
            stack_facts = facts.get(stack_id)
            if stack_facts is None:
                continue
            members = stack_facts.member_ids
            content = content_fingerprint(members, hashes)
            edges, nearest = _stack_edges(hashes, members)
            unhashed = tuple(pid for pid in members if pid in unusable)
            recomputed += 1
        result[stack_id] = _CachedEdges(
            member_ids=members,
            membership_fingerprint=membership_fingerprint(members),
            content_fingerprint=content,
            edges=edges,
            nearest=nearest,
            unhashed_picture_ids=unhashed,
        )
    if recomputed:
        logger.debug(
            "[mixed-stacks] recomputed cohesion edges for %d stack(s) with no "
            "valid cache row (of %d asked for)",
            recomputed,
            len(wanted),
        )
    return result


def cohesion_for_stacks(
    session: Session, stack_ids: Iterable[int], threshold: float
) -> dict[int, CohesionReport]:
    """Cohesion of every stack in *stack_ids* at *threshold*, in one batch.

    Args:
        session: Pre-opened session.
        stack_ids: The stacks to score.
        threshold: Similarity cut, bound to the queue's own threshold slider,
            never a constant. The same stack is mixed at 0.90 and cohesive at
            0.65, and that spectrum is the feature.

    Returns:
        ``{stack_id: CohesionReport}``. A stack with no live member is absent
        rather than present-and-empty, mirroring
        :func:`~pixlstash.services.dedup_tier_service.load_stack_facts`.
    """
    reports: dict[int, CohesionReport] = {}
    for stack_id, entry in _edges_for_stacks(session, stack_ids).items():
        components, stranded, weakest, member_edges = _fold_components(
            entry.member_ids, entry.edges, entry.nearest, threshold
        )
        reports[stack_id] = CohesionReport(
            stack_id=stack_id,
            threshold=float(threshold),
            member_ids=entry.member_ids,
            membership_fingerprint=entry.membership_fingerprint,
            components=components,
            stranded_picture_ids=stranded,
            weakest_edge=weakest,
            unhashed_picture_ids=entry.unhashed_picture_ids,
            member_edges=member_edges,
        )
    return reports


def live_stack_ids_in_session(session: Session) -> list[int]:
    """Ids of every stack with at least :data:`MIN_STACK_MEMBERS` live members.

    One member is not a stack in any sense cohesion can speak about, and a
    soft-deleted member is not in the stack in any sense the user can see.
    """
    from sqlalchemy import func

    rows = session.exec(
        select(Picture.stack_id)
        .where(Picture.stack_id.is_not(None), Picture.deleted.is_(False))
        .group_by(Picture.stack_id)
        .having(func.count(Picture.id) >= MIN_STACK_MEMBERS)
    ).all()
    return sorted(int(stack_id) for stack_id in rows)


# --- The Keep dismissal -----------------------------------------------------


def _dismissed_fingerprints(
    session: Session, stack_ids: list[int]
) -> dict[int, set[str]]:
    """``{stack_id: {fingerprint, ...}}`` for every ``Keep`` on these stacks."""
    if not stack_ids:
        return {}
    dismissed: dict[int, set[str]] = defaultdict(set)
    for start in range(0, len(stack_ids), ID_CHUNK):
        chunk = stack_ids[start : start + ID_CHUNK]
        for row in session.exec(
            select(
                MixedStackDismissal.stack_id,
                MixedStackDismissal.membership_fingerprint,
            ).where(MixedStackDismissal.stack_id.in_(chunk))
        ).all():
            dismissed[int(row[0])].add(str(row[1]))
    return dismissed


def dismiss_stack_in_session(
    session: Session, stack_id: int, actor: Optional[str] = None
) -> dict[str, Any]:
    """Record a ``Keep`` on *stack_id* at its current membership.

    Idempotent: pressing Keep twice on an unchanged stack updates nothing and
    reports ``created: false``. Keyed on the membership fingerprint, so adding a
    member later produces a fingerprint no row matches and the stack is raised
    again: the user approved *these* pictures together, not the stack forever.

    Raises:
        MixedStackError: The stack has no live members.
    """
    stack_id = int(stack_id)
    facts = load_stack_facts(session, [stack_id]).get(stack_id)
    if facts is None:
        raise MixedStackError(f"stack {stack_id} has no live members")
    fingerprint = membership_fingerprint(facts.member_ids)
    existing = session.exec(
        select(MixedStackDismissal).where(
            MixedStackDismissal.stack_id == stack_id,
            MixedStackDismissal.membership_fingerprint == fingerprint,
        )
    ).first()
    created = existing is None
    if created:
        session.add(
            MixedStackDismissal(
                stack_id=stack_id,
                membership_fingerprint=fingerprint,
                member_count=facts.member_count,
                actor=actor,
            )
        )
        session.commit()
    logger.info(
        "[mixed-stacks] kept stack %s at membership %s (%d member(s), created=%s)",
        stack_id,
        fingerprint,
        facts.member_count,
        created,
    )
    return {
        "stack_id": stack_id,
        "dismissed": True,
        "created": created,
        "membership_fingerprint": fingerprint,
        "member_count": facts.member_count,
    }


def undismiss_stack_in_session(session: Session, stack_id: int) -> dict[str, Any]:
    """Drop every ``Keep`` on *stack_id*, whatever membership it was made at.

    The way back from a mis-pressed Keep. Every fingerprint is cleared rather
    than only the current one, because a dismissal made at an older membership
    would otherwise re-hide the stack the moment the user undid an unrelated
    membership change: a Keep the user has explicitly retracted must not come
    back on its own.
    """
    stack_id = int(stack_id)
    rows = session.exec(
        select(MixedStackDismissal).where(MixedStackDismissal.stack_id == stack_id)
    ).all()
    for row in rows:
        session.delete(row)
    if rows:
        session.commit()
    logger.info(
        "[mixed-stacks] cleared %d Keep dismissal(s) on stack %s", len(rows), stack_id
    )
    return {"stack_id": stack_id, "dismissed": False, "removed": len(rows)}


# --- The list ---------------------------------------------------------------


def list_mixed_stacks_in_session(
    session: Session,
    threshold: float,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    include_kept: bool = False,
) -> dict[str, Any]:
    """One page of the Mixed stacks list, ranked least-held-together first.

    Args:
        session: Pre-opened session.
        threshold: The queue's own similarity threshold. Drives the whole list,
            the same stack is mixed at 0.90 and fine at 0.65.
        offset: Rows to skip. Plain offset paging (not the queue's keyset
            cursor): this list is short by construction (tens of rows, not
            thousands) and is not being decided out from under the client the
            way the duplicate queue is.
        limit: Rows per page, clamped to :data:`MAX_PAGE_SIZE`.
        include_kept: Include stacks the user pressed ``Keep`` on, each marked
            ``kept: true``. Off by default: the point of Keep is that the row
            leaves the list.

    Each row carries ``stackable`` / ``blocked_by_sets``, the same pair
    ``GET /dedup/stacks/{stack_id}/members`` reports, rolled up over the whole
    stack: ``stackable`` is false when **any** member is frozen by a locked
    picture set, because a locked set refuses the whole stack rather than the
    member (:func:`~pixlstash.services.set_lock_service.enforce_stack_detach_not_locked`).
    Without it the row's primary button offered an action the server answers
    ``423`` to, with nothing on the row explaining why.

    Returns:
        ``{threshold, total, kept_total, live_stack_count, offset, limit,
        next_offset, stacks: [...]}``.
    """
    offset = max(0, int(offset))
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    stack_ids = live_stack_ids_in_session(session)
    reports = cohesion_for_stacks(session, stack_ids, threshold)
    mixed = [report for report in reports.values() if report.is_mixed]
    dismissed = _dismissed_fingerprints(session, [r.stack_id for r in mixed])

    rows: list[tuple[CohesionReport, bool]] = []
    kept_total = 0
    for report in mixed:
        is_kept = report.membership_fingerprint in dismissed.get(report.stack_id, set())
        if is_kept:
            kept_total += 1
            if not include_kept:
                continue
        rows.append((report, is_kept))
    rows.sort(key=lambda item: item[0].rank_key)

    page = rows[offset : offset + limit]
    facts = load_stack_facts(session, [report.stack_id for report, _ in page])
    # One lookup for the whole page, not one per row (that would be an N+1 over
    # three queries). Keyed by STACK, and computed by the same helper the write
    # guard uses over the same member rows (soft-deleted included), so a row can
    # never promise an action the server then answers 423 to.
    frozen_by_stack = locked_sets_freezing_stacks(
        session, [report.stack_id for report, _ in page]
    )
    stacks: list[dict[str, Any]] = []
    for report, is_kept in page:
        stack_facts = facts.get(report.stack_id)
        payload = report.as_dict()
        payload["kept"] = is_kept
        payload["leader_picture_id"] = (
            stack_facts.leader_picture_id if stack_facts else report.member_ids[0]
        )
        payload["leader_thumbnail_version"] = (
            stack_facts.leader_thumbnail_version if stack_facts else None
        )
        # Whole-stack answer: one frozen member freezes the row, because split
        # and unstack refuse the whole stack rather than skipping the member.
        blocking = frozen_by_stack.get(report.stack_id, [])
        payload["stackable"] = not blocking
        payload["blocked_by_sets"] = [dict(entry) for entry in blocking]
        stacks.append(payload)

    next_offset = offset + limit
    logger.debug(
        "[mixed-stacks] %d of %d live stack(s) are mixed at threshold %.5f "
        "(%d kept); serving rows %d..%d",
        len(mixed),
        len(stack_ids),
        threshold,
        kept_total,
        offset,
        offset + len(stacks),
    )
    return {
        "threshold": round(float(threshold), 6),
        "total": len(rows),
        "kept_total": kept_total,
        "live_stack_count": len(stack_ids),
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset if next_offset < len(rows) else None,
        "stacks": stacks,
    }


# --- The two actions --------------------------------------------------------


def _apply_removal(
    session: Session,
    stack_id: int,
    leaving_ids: list[int],
    op_type: str,
    summary: str,
    batch_id: Optional[str],
    actor: Optional[str],
    source: str,
    origin_client_id: Optional[str],
) -> dict[str, Any]:
    """Move *leaving_ids* out of *stack_id* as ONE undoable operation.

    The shared body of split and unstack: the two differ only in which members
    leave and what the receipt says. The whole reversible state of both is the
    members' ``stack_id`` / ``stack_position``, which
    ``operation_log_service.capture_state_in_session`` snapshots and
    ``apply_state_in_session`` restores (recreating a dissolved ``PictureStack``
    row under its original id), so one before/after pair is the complete inverse
    and no post-restore hook is needed.

    **Dissolves rather than leaving a stack of one.** If fewer than
    :data:`MIN_STACK_MEMBERS` would remain, everything leaves and the stack row
    goes: the same rule ``DELETE /stacks/{stack_id}/members`` already applies,
    and a "stack of 1" is a state the grid has no way to render honestly. The
    response says ``stack_dissolved`` either way rather than letting the client
    infer it.

    **A dissolve takes the soft-deleted members with it.** ``load_stack_facts``
    reports live members only, so a stack of two live pictures and one
    scrapheaped one would otherwise be "dissolved" while the scrapheaped picture
    still pointed at it: ``delete_emptied_stacks`` would find that survivor,
    keep the row, and leave a stack nobody can see and nothing can empty.
    Restoring the scrapheaped picture would then produce a stack of one. They
    are already in the undo snapshot (``include_deleted=True``), so undo puts
    them back.

    **A locked set refuses the whole stack**, before anything is read or written.
    :func:`~pixlstash.services.set_lock_service.enforce_stack_detach_not_locked`
    raises ``423`` if any member of the stack is frozen: a locked set freezes a
    stack's siblings *through* the stack, so detaching them would sever the
    freeze and let a previously-refused delete succeed. Same rule, same helper
    and same status code as ``DELETE /stacks/{stack_id}/members``.
    """
    enforce_stack_detach_not_locked(
        session, stack_id, "remove pictures from a locked stack"
    )
    facts = load_stack_facts(session, [stack_id]).get(stack_id)
    if facts is None:
        raise MixedStackError(f"stack {stack_id} has no live members")
    members = list(facts.member_ids)
    leaving = [pid for pid in members if pid in set(leaving_ids)]
    if not leaving:
        raise MixedStackError(
            f"none of the requested pictures are live members of stack {stack_id}"
        )
    remaining = [pid for pid in members if pid not in set(leaving)]
    if len(remaining) < MIN_STACK_MEMBERS:
        leaving = members
        remaining = []

    batch_id = batch_id or operation_log_service.new_batch_id()
    # Snapshot the stack-expanded set including soft-deleted members: dissolving
    # a stack and renumbering the survivors touches members nobody named, and
    # they must be restorable too (the same expansion the dedup verdict path
    # takes for exactly this reason).
    undo_targets = expand_picture_ids_to_stacks(session, members, include_deleted=True)
    before = operation_log_service.capture_state_in_session(session, undo_targets)

    leaving_set = set(leaving)
    if not remaining:
        leaving_set.update(
            int(pid)
            for pid in session.exec(
                select(Picture.id).where(Picture.stack_id == stack_id)
            ).all()
        )
    for picture in session.exec(
        select(Picture).where(Picture.id.in_(sorted(leaving_set)))
    ).all():
        if picture.stack_id != stack_id:
            continue
        picture.stack_id = None
        picture.stack_position = None
        session.add(picture)
    session.flush()

    if remaining:
        normalize_stack_positions(session, stack_id)
    operation_log_service.delete_emptied_stacks(session, {int(stack_id)})

    after = operation_log_service.capture_state_in_session(session, undo_targets)
    recorded = operation_log_service.record_operation_in_session(
        session,
        op_type=op_type,
        before=before,
        after=after,
        batch_id=batch_id,
        summary=summary,
        actor=actor,
        source=source,
        origin_client_id=origin_client_id,
    )
    if recorded is None:
        # Every path above moves at least one live member off the stack, so the
        # diff cannot be empty. If it somehow is, returning a batch id that
        # points at no operation would hand the client a broken undo handle,
        # so the handle is dropped and the anomaly is loud.
        logger.error(
            "[mixed-stacks] %s on stack %s moved %d member(s) yet produced an "
            "empty operation diff; no operation was recorded and batch %s is "
            "dropped, so this change is NOT undoable",
            op_type,
            stack_id,
            len(leaving),
            batch_id,
        )
        batch_id = None
    session.commit()
    logger.info(
        "[mixed-stacks] %s on stack %s: %d member(s) %s left, %d remain, "
        "dissolved=%s, batch=%s",
        op_type,
        stack_id,
        len(leaving),
        leaving,
        len(remaining),
        not remaining,
        batch_id,
    )
    return {
        "stack_id": stack_id,
        # On dissolve this includes soft-deleted members too: they were detached,
        # and the receipt promises every picture that left the stack.
        "split_picture_ids": sorted(leaving_set),
        "remaining_picture_ids": sorted(remaining),
        "stack_dissolved": not remaining,
        "batch_id": batch_id,
        "event_picture_ids": sorted(undo_targets),
    }


def split_stranded_in_session(
    session: Session,
    stack_id: int,
    picture_ids: Optional[Iterable[int]] = None,
    threshold: float = MIN_THRESHOLD,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Split the marked member(s) off *stack_id*, as one undoable operation.

    The name is historical: the *default* selection is the stranded set, and
    that is all this did until the Mixed stacks page grew per-member marking.
    An explicit list is now bounded by the stack's live membership rather than
    by cohesion; see the reversal note on the ``picture_ids`` branch below.

    Args:
        session: Pre-opened session; this commits once.
        stack_id: The stack to split.
        picture_ids: The members to split off, which is what the client should
            send: the marks the user made on the row, so the split matches what
            they were looking at rather than a set recomputed behind them. Every
            id must be a **live member of this stack**; a picture belonging to
            another stack or to none, and a soft-deleted member, are both a 400.
            Omit it and the stranded set at *threshold* is used instead, which
            is the row's opening marking.
        threshold: The similarity the stranded set is computed at. Read **only**
            when *picture_ids* is omitted; an explicit list is bounded by the
            stack's live membership, not by cohesion.
        batch_id: Operation-log batch; minted server-side when absent.
        actor / source / origin_client_id: Origin discipline (§21), read from
            the request in the handler and passed down explicitly.

    Raises:
        HTTPException: ``423`` when a locked picture set freezes any member of
            the stack. Checked first, before the stranded set is computed, so a
            frozen stack answers "locked" rather than "nothing to split".
        MixedStackError: The stack has no live members, *picture_ids* is empty,
            *picture_ids* names something that is not a live member of this
            stack, or *picture_ids* was omitted and no member is stranded at
            *threshold*.
    """
    stack_id = int(stack_id)
    # First, before any cohesion work: a locked stack is refused whole, and the
    # answer must be 423 rather than a 400 about the stranded set.
    enforce_stack_detach_not_locked(
        session, stack_id, "split pictures out of a locked stack"
    )
    if picture_ids is None:
        report = cohesion_for_stacks(session, [stack_id], threshold).get(stack_id)
        if report is None:
            raise MixedStackError(f"stack {stack_id} has no live members")
        stranded = list(report.stranded_picture_ids)
        if not stranded:
            raise MixedStackError(
                f"no member of stack {stack_id} is stranded at threshold "
                f"{threshold}; there is nothing to split off"
            )
        targets = stranded
    else:
        # The client sends explicit marks. Membership is the only bound in this
        # branch, so avoid computing cohesion that is discarded while the write
        # transaction is held.
        facts = load_stack_facts(session, [stack_id]).get(stack_id)
        if facts is None:
            raise MixedStackError(f"stack {stack_id} has no live members")
        targets = sorted({int(pid) for pid in picture_ids})
        if not targets:
            raise MixedStackError("picture_ids was empty; nothing to split off")
        # WIDENED 2026-08-02, deliberately reversing security-review finding F7
        # (2026-08-01). F7 bound an explicit list to a SUBSET of the stranded
        # set at *threshold*, on the reasoning that an arbitrary list "would let
        # it break up a cohesive stack this page would never list". That
        # protected the route's NAME, not the user's intent, and the intent has
        # since changed: the Mixed stacks page is being rebuilt so the user
        # marks which members are strangers, starting from the engine's marks
        # and adjusting them. Once the user can mark, their marks ARE the input
        # and "stranded" is only the opening position, so a bound that refuses
        # any mark the engine did not make would refuse the feature. The engine
        # keeps the default (omit ``picture_ids`` and the stranded set is used);
        # it stops being a veto.
        #
        # What replaces it is the real safety property, and it is not nothing:
        # every id must be a LIVE MEMBER OF THIS STACK. A foreign picture is
        # refused, a soft-deleted member is refused (the caller cannot see those
        # and must not move them blind), and the locked-set guard above still
        # refuses the whole stack. F7 itself was rated LOW because "it is not a
        # privilege boundary: the route is OWNER_ONLY and DELETE /stacks/
        # {stack_id}/members gives the same principal an unrestricted remove",
        # so widening grants this principal no capability it lacked; it removes
        # a constraint that only ever cost the user a round trip through a
        # second endpoint.
        live_members = set(facts.member_ids)
        outside = [pid for pid in targets if pid not in live_members]
        if outside:
            # Name the soft-deleted case for what it is rather than reporting a
            # scrapheaped member as "not a member": the client cannot see those
            # rows, so "unknown picture" would send it hunting for a bug that is
            # not there. One extra query, on the refusal path only.
            scrapheaped = {
                int(picture_id)
                for picture_id in session.exec(
                    select(Picture.id).where(
                        Picture.id.in_(outside),
                        Picture.stack_id == stack_id,
                        Picture.deleted.is_(True),
                    )
                ).all()
            }
            foreign = [pid for pid in outside if pid not in scrapheaped]
            reasons = []
            if foreign:
                reasons.append(
                    f"picture(s) {foreign} are not members of stack {stack_id}"
                )
            if scrapheaped:
                reasons.append(
                    f"picture(s) {sorted(scrapheaped)} are in the Scrapheap, so "
                    "they are not live members and are not on the row you "
                    "marked; restore them first if you meant to move them"
                )
            raise MixedStackError(
                "; ".join(reasons)
                + f". Split moves live members of stack {stack_id} and nothing else"
            )
    return _apply_removal(
        session,
        stack_id,
        targets,
        OP_TYPE_SPLIT,
        f"Split {len(targets)} picture(s) out of a stack",
        batch_id,
        actor,
        source,
        origin_client_id,
    )


def unstack_in_session(
    session: Session,
    stack_id: int,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Dissolve *stack_id* entirely, as one undoable operation.

    D5's outcome for a stack with no majority cluster. Every live member becomes
    loose and the ``PictureStack`` row goes; undo restores both.

    Raises:
        HTTPException: ``423`` when a locked picture set freezes any member (via
            :func:`_apply_removal`). Dissolving a stack is the most complete form
            of the detach a locked set forbids: it severs the through-stack
            freeze for every member at once.
        MixedStackError: The stack has no live members.
    """
    stack_id = int(stack_id)
    facts = load_stack_facts(session, [stack_id]).get(stack_id)
    if facts is None:
        raise MixedStackError(f"stack {stack_id} has no live members")
    return _apply_removal(
        session,
        stack_id,
        list(facts.member_ids),
        OP_TYPE_UNSTACK,
        f"Unstacked {facts.member_count} pictures",
        batch_id,
        actor,
        source,
        origin_client_id,
    )


# --- The cohesion cache write path ------------------------------------------


def stale_cohesion_stack_ids_in_session(session: Session, limit: int) -> list[int]:
    """Up to *limit* live stacks whose cached cohesion row is missing or stale.

    The finder's query. Database triggers delete the derived row whenever an
    input changes, including a hash arriving after an unhashed row was cached.
    Presence is therefore validity and absence is the only stale state; the
    finder never needs to reread every member hash merely to validate a hit.
    """
    stack_ids = live_stack_ids_in_session(session)
    if not stack_ids:
        return []
    cached: set[int] = set()
    for start in range(0, len(stack_ids), ID_CHUNK):
        chunk = stack_ids[start : start + ID_CHUNK]
        cached.update(
            int(stack_id)
            for stack_id in session.exec(
                select(StackCohesion.stack_id).where(StackCohesion.stack_id.in_(chunk))
            ).all()
        )
    return [stack_id for stack_id in stack_ids if stack_id not in cached][:limit]


def refresh_cohesion_in_session(
    session: Session, stack_ids: Iterable[int]
) -> list[int]:
    """Recompute and upsert the cached edge set of every stack in *stack_ids*.

    The one writer of :class:`~pixlstash.db_models.mixed_stack.StackCohesion`.
    Batched: one membership read and one hash read cover the whole batch, so a
    finder pass over hundreds of stacks is a constant number of queries.

    Returns:
        The stack ids whose row was written.
    """
    wanted = sorted({int(sid) for sid in stack_ids})
    if not wanted:
        return []
    facts, hashes, unusable = _resolve_stack_inputs(session, wanted)

    existing: dict[int, StackCohesion] = {}
    for start in range(0, len(wanted), ID_CHUNK):
        chunk = wanted[start : start + ID_CHUNK]
        for row in session.exec(
            select(StackCohesion).where(StackCohesion.stack_id.in_(chunk))
        ).all():
            existing[int(row.stack_id)] = row

    written: list[int] = []
    for stack_id in wanted:
        stack_facts = facts.get(stack_id)
        if stack_facts is None:
            # The stack lost its last live member between the finder's query and
            # this write. Its cache row is meaningless now; the cascade FK drops
            # it when the stack row goes, so nothing to do beyond saying so.
            logger.info(
                "[mixed-stacks] stack %s has no live members at cohesion-refresh "
                "time; no cache row written",
                stack_id,
            )
            continue
        members = stack_facts.member_ids
        row = existing.get(stack_id)
        if row is None:
            row = StackCohesion(stack_id=stack_id)
        row.content_fingerprint = content_fingerprint(members, hashes)
        row.member_count = len(members)
        row.member_ids = json.dumps(list(members))
        row.unhashed_picture_ids = json.dumps(
            [pid for pid in members if pid in unusable]
        )
        edges, nearest = _stack_edges(hashes, members)
        row.edges = json.dumps([[a, b, distance] for a, b, distance in edges])
        row.nearest_edges = json.dumps(
            [
                [picture_id, sibling, distance]
                for picture_id, sibling, distance in nearest
            ]
        )
        row.computed_at = datetime.utcnow()
        session.add(row)
        written.append(stack_id)
    session.commit()
    logger.debug(
        "[mixed-stacks] refreshed cohesion cache for %d stack(s)", len(written)
    )
    return written


# --- Vault wrappers ---------------------------------------------------------


def _notify_pictures_changed(
    vault: "Vault",
    picture_ids: list[int],
    origin_client_id: Optional[str],
    source: str,
) -> None:
    """Announce a committed split/unstack on the WS envelope (§15)."""
    if not picture_ids:
        return
    vault.notify(
        EventType.CHANGED_PICTURES,
        {
            "picture_ids": sorted({int(pid) for pid in picture_ids}),
            "origin_client_id": origin_client_id,
            "change_kind": "updated",
            "source": source,
        },
    )


def list_mixed_stacks(
    vault: "Vault",
    threshold: float,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    include_kept: bool = False,
) -> dict[str, Any]:
    """Read-only vault wrapper around :func:`list_mixed_stacks_in_session`."""
    return vault.db.run_immediate_read_task(
        list_mixed_stacks_in_session, threshold, offset, limit, include_kept
    )


def split_stranded(
    vault: "Vault",
    stack_id: int,
    picture_ids: Optional[Iterable[int]] = None,
    threshold: float = MIN_THRESHOLD,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`split_stranded_in_session`."""
    result = vault.db.run_task(
        split_stranded_in_session,
        stack_id,
        list(picture_ids) if picture_ids is not None else None,
        threshold,
        batch_id,
        actor,
        source,
        origin_client_id,
    )
    _notify_pictures_changed(
        vault, result.pop("event_picture_ids", []), origin_client_id, source
    )
    return result


def unstack(
    vault: "Vault",
    stack_id: int,
    batch_id: Optional[str] = None,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`unstack_in_session`."""
    result = vault.db.run_task(
        unstack_in_session,
        stack_id,
        batch_id,
        actor,
        source,
        origin_client_id,
    )
    _notify_pictures_changed(
        vault, result.pop("event_picture_ids", []), origin_client_id, source
    )
    return result


def dismiss_stack(
    vault: "Vault", stack_id: int, actor: Optional[str] = None
) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`dismiss_stack_in_session`."""
    return vault.db.run_task(dismiss_stack_in_session, stack_id, actor)


def undismiss_stack(vault: "Vault", stack_id: int) -> dict[str, Any]:
    """Write-path vault wrapper around :func:`undismiss_stack_in_session`."""
    return vault.db.run_task(undismiss_stack_in_session, stack_id)


__all__ = [
    "ACTION_SPLIT",
    "ACTION_UNSTACK",
    "CohesionReport",
    "DEFAULT_PAGE_SIZE",
    "MAX_CACHED_HAMMING",
    "MAX_PAGE_SIZE",
    "MIN_STACK_MEMBERS",
    "MemberEdge",
    "MixedStackError",
    "OP_TYPE_SPLIT",
    "OP_TYPE_UNSTACK",
    "build_mixed_stack_evidence",
    "cohesion_for_stacks",
    "content_fingerprint",
    "dismiss_stack",
    "dismiss_stack_in_session",
    "list_mixed_stacks",
    "list_mixed_stacks_in_session",
    "live_stack_ids_in_session",
    "membership_fingerprint",
    "refresh_cohesion_in_session",
    "split_stranded",
    "split_stranded_in_session",
    "stale_cohesion_stack_ids_in_session",
    "undismiss_stack",
    "undismiss_stack_in_session",
    "unstack",
    "unstack_in_session",
]
