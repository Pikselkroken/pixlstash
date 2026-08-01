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
  already reports per group.

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
from collections import defaultdict
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
"""Widest distance that can ever be an edge, so the cache is threshold-free.

Every API threshold is at or above :data:`~pixlstash.services.dedup_tier_service.MIN_THRESHOLD`
(0.65: the routes carry it as a pydantic ``ge=``, so a lower value is a 422
before any handler runs), and ``max_hamming`` shrinks as the threshold rises.
A pair further apart than this therefore cannot be an edge at *any* admissible
threshold, so dropping it from the cache loses nothing and keeps the stored
edge list small for a stack of near-identical frames."""

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
    """

    stack_id: int
    threshold: float
    member_ids: tuple[int, ...]
    membership_fingerprint: str
    components: tuple[tuple[int, ...], ...]
    stranded_picture_ids: tuple[int, ...]
    weakest_edge: Optional[float]
    unhashed_picture_ids: tuple[int, ...]

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
            "suggested_action": self.suggested_action,
        }


def _fold_components(
    member_ids: tuple[int, ...],
    edges: list[tuple[int, int, int]],
    threshold: float,
) -> tuple[tuple[tuple[int, ...], ...], tuple[int, ...], Optional[float]]:
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
        threshold: Similarity cut; ``max_hamming = int((1 - threshold) * 64)``,
            identical to ``near_pairs_in_bucket``.

    Returns:
        ``(components, stranded_picture_ids, weakest_edge)``. Components are
        largest first (ties by lowest member id) and each is sorted by id.
    """
    max_hamming = int((1.0 - float(threshold)) * PHASH_BITS)
    live = set(member_ids)
    forest = dedup_sweep_service._LikenessForest()
    weakest: Optional[float] = None
    connected: set[int] = set()
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

    groups = [tuple(members) for members, _low, _high in forest.components(1)]
    stranded = tuple(sorted(pid for pid in live if pid not in connected))
    groups.extend((pid,) for pid in stranded)
    groups.sort(key=lambda component: (-len(component), component[0]))
    return tuple(groups), stranded, weakest


def _stack_edges(
    hashes_by_picture: dict[int, int], member_ids: tuple[int, ...]
) -> list[tuple[int, int, int]]:
    """Every within-stack pair at or below :data:`MAX_CACHED_HAMMING`.

    The comparison is the tier-2 one: XOR the two 64-bit dHashes and popcount
    the result with :func:`~pixlstash.services.dedup_tier_service._popcount64`,
    vectorised over the stack's upper triangle. Stacks are small, so the whole
    O(n^2) is a handful of numpy rows.

    Args:
        hashes_by_picture: ``{picture_id: dhash}`` for the members that have a
            usable perceptual hash.
        member_ids: The stack's members, canonical order.

    Returns:
        ``(a, b, hamming)`` with ``a < b``, sorted, distance ascending last.
    """
    usable = [pid for pid in member_ids if pid in hashes_by_picture]
    if len(usable) < 2:
        return []
    ids = np.array(usable, dtype=np.int64)
    values = np.array([hashes_by_picture[pid] for pid in usable], dtype=np.uint64)
    edges: list[tuple[int, int, int]] = []
    for offset in range(1, len(usable)):
        distances = _popcount64(values[:-offset] ^ values[offset:])
        for index in np.nonzero(distances <= MAX_CACHED_HAMMING)[0]:
            left = int(ids[index])
            right = int(ids[index + offset])
            edges.append((min(left, right), max(left, right), int(distances[index])))
    edges.sort()
    return edges


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

    A stack whose cached :func:`content_fingerprint` still matches is exact
    however old the row is, because every input it was derived from is
    unchanged; anything else is recomputed here, in the same batch. What the
    cache buys is therefore the O(n^2) comparison, not the reads, the reads
    happen either way, because the fingerprint is the only honest staleness
    test and it needs the hashes.

    This function never writes. The cache is owned by
    :class:`~pixlstash.tasks.stack_cohesion_task.StackCohesionTask`, so a read
    path can never turn into a writer behind a GET; a cache miss simply costs
    the (small) recomputation until the finder catches up.
    """
    wanted = sorted({int(sid) for sid in stack_ids})
    if not wanted:
        return {}
    facts, hashes, unusable = _resolve_stack_inputs(session, wanted)

    cached: dict[int, StackCohesion] = {}
    for start in range(0, len(wanted), ID_CHUNK):
        chunk = wanted[start : start + ID_CHUNK]
        for row in session.exec(
            select(StackCohesion).where(StackCohesion.stack_id.in_(chunk))
        ).all():
            cached[int(row.stack_id)] = row

    result: dict[int, _CachedEdges] = {}
    recomputed = 0
    for stack_id in wanted:
        stack_facts = facts.get(stack_id)
        if stack_facts is None:
            continue
        members = stack_facts.member_ids
        content = content_fingerprint(members, hashes)
        row = cached.get(stack_id)
        if row is not None and row.content_fingerprint == content:
            edges = [
                (int(a), int(b), int(distance))
                for a, b, distance in json.loads(row.edges or "[]")
            ]
            unhashed = tuple(
                int(pid) for pid in json.loads(row.unhashed_picture_ids or "[]")
            )
        else:
            edges = _stack_edges(hashes, members)
            unhashed = tuple(pid for pid in members if pid in unusable)
            recomputed += 1
        result[stack_id] = _CachedEdges(
            member_ids=members,
            membership_fingerprint=membership_fingerprint(members),
            content_fingerprint=content,
            edges=edges,
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
        components, stranded, weakest = _fold_components(
            entry.member_ids, entry.edges, threshold
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
        )
    return reports


def live_stack_ids_in_session(session: Session) -> list[int]:
    """Ids of every stack with at least :data:`MIN_STACK_MEMBERS` live members.

    One member is not a stack in any sense cohesion can speak about, and a
    soft-deleted member is not in the stack in any sense the user can see.
    """
    rows = session.exec(
        select(Picture.stack_id).where(
            Picture.stack_id.is_not(None), Picture.deleted.is_(False)
        )
    ).all()
    counts: dict[int, int] = defaultdict(int)
    for stack_id in rows:
        counts[int(stack_id)] += 1
    return sorted(
        stack_id for stack_id, count in counts.items() if count >= MIN_STACK_MEMBERS
    )


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
        "split_picture_ids": sorted(leaving),
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
    """Split the stranded member(s) off *stack_id*, as one undoable operation.

    Args:
        session: Pre-opened session; this commits once.
        stack_id: The stack to split.
        picture_ids: The members to split off. Omit to use the stranded set the
            server computes at *threshold*, the same set the list showed. An
            explicit list is what the client should send, so the split matches
            the row the user was looking at rather than a set recomputed behind
            them; it must be a **subset of the stranded set** at *threshold*,
            because this route splits stranded members off a mixed stack and
            nothing else. ``DELETE /stacks/{stack_id}/members`` is the general
            remove-from-stack primitive.
        threshold: The similarity the stranded set is computed at. Read on every
            request: when *picture_ids* is omitted it selects the targets, and
            when it is supplied it bounds them.
        batch_id: Operation-log batch; minted server-side when absent.
        actor / source / origin_client_id: Origin discipline (§21), read from
            the request in the handler and passed down explicitly.

    Raises:
        HTTPException: ``423`` when a locked picture set freezes any member of
            the stack. Checked first, before the stranded set is computed, so a
            frozen stack answers "locked" rather than "nothing to split".
        MixedStackError: The stack has no live members, the request names none
            of them, no member is stranded at *threshold*, or *picture_ids*
            names a member that is not stranded.
    """
    stack_id = int(stack_id)
    # First, before any cohesion work: a locked stack is refused whole, and the
    # answer must be 423 rather than a 400 about the stranded set.
    enforce_stack_detach_not_locked(
        session, stack_id, "split pictures out of a locked stack"
    )
    report = cohesion_for_stacks(session, [stack_id], threshold).get(stack_id)
    if report is None:
        raise MixedStackError(f"stack {stack_id} has no live members")
    stranded = list(report.stranded_picture_ids)
    if picture_ids is None:
        if not stranded:
            raise MixedStackError(
                f"no member of stack {stack_id} is stranded at threshold "
                f"{threshold}; there is nothing to split off"
            )
        targets = stranded
    else:
        targets = sorted({int(pid) for pid in picture_ids})
        if not targets:
            raise MixedStackError("picture_ids was empty; nothing to split off")
        # This route splits STRANDED members off a MIXED stack, which is what it
        # is named, documented and listed for. Taking an arbitrary id list made
        # it an unconstrained remove-from-stack primitive that would happily
        # break up a perfectly cohesive stack the Mixed stacks page would never
        # show. Constraining the explicit list to the stranded set keeps the
        # reason it exists (act on exactly the ids the row displayed, never a
        # set recomputed behind the user's back) while removing the ability to
        # act on anything the row did not display. A named id that is no longer
        # stranded means the stack moved under the client, and the honest answer
        # is to refuse and let it re-read the row.
        #
        # The bound is deliberately THRESHOLD-RELATIVE, and that is not a hole
        # left open by accident. A caller passing a high threshold widens the
        # stranded set (``max_hamming`` shrinks), so it can name more members.
        # That is the same widening ``GET /dedup/mixed-stacks?threshold=…``
        # would show it: at whatever threshold the caller passes, this splits
        # exactly what that list reports as stranded, which is the contract D5
        # asks for ("bind to the threshold, never a constant"). Tightening it to
        # some fixed threshold would make the route disagree with its own list.
        # The route is OWNER_ONLY and ``DELETE /stacks/{stack_id}/members``
        # already gives the same principal an unrestricted remove, so nothing
        # here is a privilege boundary; what the bound buys is that the route
        # does what its name and its page say it does.
        not_stranded = [pid for pid in targets if pid not in set(stranded)]
        if not_stranded:
            raise MixedStackError(
                f"picture(s) {not_stranded} are not stranded in stack "
                f"{stack_id} at threshold {threshold}; this route splits off "
                "stranded members only, so re-read the row and send the ids it "
                "reports in stranded_picture_ids (use DELETE /stacks/"
                f"{stack_id}/members to remove an arbitrary member)"
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

    The finder's query. Staleness is decided on the :func:`content_fingerprint`,
    not on a timestamp: a row whose fingerprint still matches is exact however
    old it is, and one whose fingerprint has moved is wrong however fresh it is.
    Because that fingerprint covers the members' perceptual hashes as well as
    their ids, a hash arriving after the cache did (the embedding worker filling
    a ``NULL``) re-queues the stack instead of freezing its members as stranded.
    """
    stack_ids = live_stack_ids_in_session(session)
    if not stack_ids:
        return []
    facts, hashes, _unusable = _resolve_stack_inputs(session, stack_ids)
    cached: dict[int, str] = {}
    for start in range(0, len(stack_ids), ID_CHUNK):
        chunk = stack_ids[start : start + ID_CHUNK]
        for row in session.exec(
            select(StackCohesion.stack_id, StackCohesion.content_fingerprint).where(
                StackCohesion.stack_id.in_(chunk)
            )
        ).all():
            cached[int(row[0])] = str(row[1])
    stale: list[int] = []
    for stack_id in stack_ids:
        stack_facts = facts.get(stack_id)
        if stack_facts is None:
            continue
        if cached.get(stack_id) != content_fingerprint(stack_facts.member_ids, hashes):
            stale.append(stack_id)
            if len(stale) >= limit:
                break
    return stale


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
        row.edges = json.dumps(
            [[a, b, distance] for a, b, distance in _stack_edges(hashes, members)]
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
    "MixedStackError",
    "OP_TYPE_SPLIT",
    "OP_TYPE_UNSTACK",
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
