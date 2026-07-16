"""Train/eval split assignment — the component-aware, fail-closed leakage guard.

Wave B of the tag-review takeover design
(``docs/reviews/tag-review-tagger-takeover-design.md`` §2). A naive
``hash(picture_id) % 100 < eval_pct`` assignment lets near-duplicate pictures
land on opposite sides of the split, which silently inflates every future
accuracy number a frozen eval slice (a later wave) would compute. This module
instead:

1. Treats a "corroborated near-duplicate" edge between two pictures as either
   (a) dhash Hamming distance <= ``DEFAULT_MAX_TWIN_HAMMING`` **and** CLIP
   cosine similarity >= ``MIN_DISPLAY_TWIN_SIM`` — the exact signal
   :func:`pixlstash.services.tag_scan_service.scan_tag` already uses for its
   twin-detection floor, reused rather than reinvented — **or** (b) a stored
   :class:`~pixlstash.db_models.picture_likeness.PictureLikeness` row >=
   ``MISMATCH_LIKENESS_THRESHOLD`` (the same "near-duplicate" bar
   ``tag_health_service``'s mismatch signal already uses).
2. Unions corroborated edges into connected components (union-find, smallest
   ``picture_id`` in a component becomes its stable ``component_key``).
3. Assigns a whole component to one side by hashing its ``component_key``,
   80/20 TRAIN/EVAL, **stratified within each picture set** so a set's near-dup
   components don't all land on one side by chance (see
   ``_component_stratify_set`` for the primary-set tie-break judgement call).

**Write path (primary guard).** The near-dup graph is discovered
incrementally as new :class:`PictureLikeness` rows are computed
(:mod:`pixlstash.utils.likeness.likeness_utils`'s ``write_results``, the
consumer of :class:`~pixlstash.db_models.picture_likeness.PictureLikenessQueue`).
:func:`check_split_conflicts_for_new_edges` is called from there: whenever a
newly-written, corroborated edge connects two pictures with **different**
existing splits, both are marked ``conflict=True`` and forced to ``NEITHER`` —
never auto-resolved, never auto-moved into ``EVAL``. This is the actual leak
vector (a component can merge *after* both halves already have different
splits) and is the guard that matters most.

**Read path (secondary, defense in depth).**
:func:`has_train_side_conflict` is a small reusable query a future caller
(Wave C's eval-slice freeze — not implemented here) can use to re-validate
that no candidate EVAL item has a corroborated near-dup sibling on the TRAIN
side at freeze time, catching a race between edge discovery and freeze in the
same window.

Judgement calls made here (the design doc left these open; documented per its
own instruction to do so):

* **Candidate-edge universe for bulk assignment.** Re-scanning every pair of
  pictures in the vault for corroboration would be an O(N^2) sweep. Instead,
  ``assign_splits_in_session`` reuses the *already-computed*
  :class:`PictureLikeness` table as the candidate-edge universe (its own gate
  — phash similarity >= 0.45, i.e. Hamming <= 35 bits, and embedding cosine
  >= 0.82 — is strictly looser than the corroboration bar above, so every
  corroborated pair is a candidate, though ``PictureLikeness``'s own windowed
  near-dup search is itself a heuristic, not an exhaustive O(N^2) scan). This
  keeps assignment cheap; completeness of the *primary* guard does not depend
  on it, because the write-path hook fires independently whenever
  ``PictureLikeness`` gains a new corroborated edge, regardless of whether a
  bulk assignment sweep has ever run.
* **Primary/first-encountered set membership** (for stratification): a
  picture's primary set is the lowest ``PictureSet.id`` it belongs to.
  ``PictureSetMember`` carries no membership timestamp, and set ids are
  assigned in creation order, so "lowest id" is the closest available proxy
  for "first encountered". A component spanning members with different
  primary sets is stratified into the lowest of its members' primary sets,
  for the same "lowest id, deterministic" reason.
"""

import hashlib
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Iterable, Optional

import numpy as np
from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureLikeness, PictureSetMember
from pixlstash.db_models.picture_split import PictureSplit, SplitValue
from pixlstash.pixl_logging import get_logger
from pixlstash.services.tag_health_service import MISMATCH_LIKENESS_THRESHOLD
from pixlstash.services.tag_scan_service import (
    DEFAULT_MAX_TWIN_HAMMING,
    MIN_DISPLAY_TWIN_SIM,
)
from pixlstash.utils.near_neighbor import EMBEDDING_BYTES, hamming_distance

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# 80/20 train/eval, resolved by the machine-learning-expert review (design
# doc §2 / §8 item 2). Lean toward more TRAIN: the ratio can't fix per-tag
# sparsity on its own, but a bigger TRAIN pool maximizes what's available for
# human labeling and (a later wave's) disjoint-validation threshold rederivation.
TRAIN_RATIO = 0.8

VALID_SPLITS = {SplitValue.TRAIN.value, SplitValue.EVAL.value, SplitValue.NEITHER.value}


# --------------------------------------------------------------------------- #
# Corroboration primitives
# --------------------------------------------------------------------------- #


def _parse_phash(value: Optional[str]) -> Optional[int]:
    """Parse a stored 16-char hex dhash string to int; None on missing/bad data."""
    if not value:
        return None
    try:
        return int(value, 16)
    except (ValueError, TypeError):
        logger.warning(
            "picture_split_service: unparseable perceptual_hash %r; excluding "
            "from near-duplicate corroboration",
            value,
        )
        return None


def _decode_unit_embedding(blob) -> Optional[np.ndarray]:
    """Decode a CLIP image_embedding BLOB to a unit-norm float32 vector."""
    if blob is None or len(blob) != EMBEDDING_BYTES:
        return None
    arr = np.frombuffer(blob, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        return None
    return arr / norm


def _fetch_phash_and_embedding(
    session: Session, picture_ids: Iterable[int]
) -> tuple[dict[int, Optional[int]], dict[int, Optional[np.ndarray]]]:
    """Batch-fetch parsed dhash ints and unit embeddings for a set of pictures."""
    ids = list({int(p) for p in picture_ids})
    if not ids:
        return {}, {}
    rows = session.exec(
        select(Picture.id, Picture.perceptual_hash, Picture.image_embedding).where(
            Picture.id.in_(ids)
        )
    ).all()
    phash_by_id: dict[int, Optional[int]] = {}
    emb_by_id: dict[int, Optional[np.ndarray]] = {}
    for pid, phash, emb in rows:
        phash_by_id[int(pid)] = _parse_phash(phash)
        emb_by_id[int(pid)] = _decode_unit_embedding(emb)
    return phash_by_id, emb_by_id


def _is_corroborated(
    pic_a: int,
    pic_b: int,
    likeness: Optional[float],
    phash_by_id: dict[int, Optional[int]],
    emb_by_id: dict[int, Optional[np.ndarray]],
) -> tuple[bool, str]:
    """Whether (pic_a, pic_b) is a corroborated near-duplicate edge, and why.

    Either branch is sufficient (see module docstring): a high-confidence
    stored ``PictureLikeness`` row, or dhash Hamming proximity corroborated by
    CLIP cosine similarity (``tag_scan_service``'s displayed-twin rule).
    """
    if likeness is not None and likeness >= MISMATCH_LIKENESS_THRESHOLD:
        return (
            True,
            f"picture_likeness={likeness:.4f} >= {MISMATCH_LIKENESS_THRESHOLD}",
        )

    hash_a, hash_b = phash_by_id.get(pic_a), phash_by_id.get(pic_b)
    emb_a, emb_b = emb_by_id.get(pic_a), emb_by_id.get(pic_b)
    if hash_a is None or hash_b is None or emb_a is None or emb_b is None:
        return False, ""
    dist = hamming_distance(hash_a, hash_b)
    if dist > DEFAULT_MAX_TWIN_HAMMING:
        return False, ""
    cos_sim = float(np.dot(emb_a, emb_b))
    if cos_sim < MIN_DISPLAY_TWIN_SIM:
        return False, ""
    return True, f"dhash hamming {dist} + clip cosine {cos_sim:.4f}"


# --------------------------------------------------------------------------- #
# Union-find
# --------------------------------------------------------------------------- #


class _UnionFind:
    """Union-find over int picture ids; a component's root is always its min id.

    That invariant is exactly what we want: the root doubles as
    ``component_key`` with no extra bookkeeping.
    """

    def __init__(self) -> None:
        self._parent: dict[int, int] = {}

    def add(self, x: int) -> None:
        self._parent.setdefault(x, x)

    def find(self, x: int) -> int:
        self.add(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if ra < rb:
            self._parent[rb] = ra
        else:
            self._parent[ra] = rb

    def components(self) -> dict[int, set[int]]:
        groups: dict[int, set[int]] = defaultdict(set)
        for x in self._parent:
            groups[self.find(x)].add(x)
        return dict(groups)


def _stable_hash01(key: int) -> float:
    """Deterministic pseudo-random float in [0, 1) for a component_key.

    Used only to order components within a picture-set stratification bucket
    before greedily filling the 80% TRAIN quota. Stable across process
    restarts (unlike Python's salted ``hash()``) so re-running assignment
    never reshuffles components that were already decided.
    """
    digest = hashlib.sha256(str(int(key)).encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(1 << 64)


# --------------------------------------------------------------------------- #
# Write-path guard: incremental conflict detection on new PictureLikeness edges
# --------------------------------------------------------------------------- #


def check_split_conflicts_for_new_edges(
    session: Session, edges: Iterable[tuple[int, int, Optional[float]]]
) -> dict:
    """Fail-closed conflict guard for newly-written PictureLikeness edges.

    Call this immediately after new ``PictureLikeness`` rows are persisted —
    the moment the near-dup graph incrementally grows — and before the
    caller's commit, so the conflict flags land in the same transaction as
    the edges that triggered them. This is the *primary* leakage guard (see
    module docstring): a near-dup component can merge after both halves
    already have different splits, and this is where that merge is caught.

    Pictures with no ``PictureSplit`` row yet are left untouched here —
    assignment happens via ``assign_splits_in_session``, not this hook.

    Args:
        session: Open DB session; caller is responsible for commit.
        edges: ``(picture_id_a, picture_id_b, likeness)`` triples — typically
            the ``PictureLikeness`` rows just written.

    Returns:
        ``{"conflicted": <count of picture rows newly flagged>}``.
    """
    edges = list(edges)
    if not edges:
        return {"conflicted": 0}

    pids = {int(pid) for a, b, _ in edges for pid in (a, b)}
    existing = _fetch_splits(session, pids)

    # Only an edge where BOTH sides already carry a non-conflict split can
    # newly conflict here; an edge touching an unassigned picture has
    # nothing yet to contradict.
    settled_pids = {
        pid for pid, row in existing.items() if row is not None and not row.conflict
    }
    relevant = [
        (int(a), int(b), lk)
        for a, b, lk in edges
        if int(a) in settled_pids and int(b) in settled_pids
    ]
    if not relevant:
        return {"conflicted": 0}

    phash_by_id, emb_by_id = _fetch_phash_and_embedding(session, pids)

    flagged = 0
    for a, b, likeness in relevant:
        row_a, row_b = existing[a], existing[b]
        if row_a.split == row_b.split:
            continue  # already agree — nothing to flag
        corroborated, reason = _is_corroborated(a, b, likeness, phash_by_id, emb_by_id)
        if not corroborated:
            continue
        detail = (
            f"new corroborated edge ({a}, {b}) connects existing splits "
            f"{row_a.split!r} vs {row_b.split!r} [{reason}]"
        )
        for row in (row_a, row_b):
            row.split = SplitValue.NEITHER.value
            row.conflict = True
            row.conflict_detail = detail
            session.add(row)
            flagged += 1
        logger.warning(
            "picture_split: write-path conflict on pictures %s/%s — %s", a, b, detail
        )
    return {"conflicted": flagged}


def _fetch_splits(
    session: Session, picture_ids: Iterable[int]
) -> dict[int, Optional[PictureSplit]]:
    ids = list({int(p) for p in picture_ids})
    if not ids:
        return {}
    rows = session.exec(
        select(PictureSplit).where(PictureSplit.picture_id.in_(ids))
    ).all()
    by_id: dict[int, Optional[PictureSplit]] = {pid: None for pid in ids}
    for row in rows:
        by_id[row.picture_id] = row
    return by_id


# --------------------------------------------------------------------------- #
# Bulk assignment
# --------------------------------------------------------------------------- #


def _fetch_primary_sets(
    session: Session, picture_ids: Iterable[int]
) -> dict[int, Optional[int]]:
    """Each picture's primary (lowest-id) picture-set membership; None if none."""
    ids = list({int(p) for p in picture_ids})
    if not ids:
        return {}
    rows = session.exec(
        select(PictureSetMember.picture_id, func.min(PictureSetMember.set_id))
        .where(PictureSetMember.picture_id.in_(ids))
        .group_by(PictureSetMember.picture_id)
    ).all()
    result: dict[int, Optional[int]] = {pid: None for pid in ids}
    for pid, set_id in rows:
        result[int(pid)] = int(set_id) if set_id is not None else None
    return result


def _component_stratify_set(
    members: set[int], primary_set_by_pid: dict[int, Optional[int]]
) -> Optional[int]:
    """A component's stratification bucket (see module docstring's judgement call)."""
    candidates = [primary_set_by_pid.get(pid) for pid in members]
    candidates = [c for c in candidates if c is not None]
    return min(candidates) if candidates else None


def assign_splits_in_session(session: Session) -> dict:
    """Component-aware (re)assignment for pictures lacking a split.

    On the very first call (no ``PictureSplit`` rows exist yet) every
    non-deleted picture is a target; afterwards only pictures with no
    existing row are. Targets are unioned with their corroborated near-dup
    neighbours (which may already have a split) so a new picture correctly
    joins — or, fail-closed, conflicts with — an existing component instead
    of being assigned independently of it.

    Returns ``{"assigned": int, "conflicted": int}`` — counts of picture rows
    newly written with a definitive split, and newly flagged (or re-flagged)
    as conflicted, respectively.
    """
    existing_rows = {
        row.picture_id: row for row in session.exec(select(PictureSplit)).all()
    }

    all_ids = {
        int(pid)
        for pid in session.exec(select(Picture.id).where(Picture.deleted.is_(False)))
    }
    target_ids = (all_ids - existing_rows.keys()) if existing_rows else set(all_ids)
    if not target_ids:
        return {"assigned": 0, "conflicted": 0}

    edge_rows = session.exec(
        select(
            PictureLikeness.picture_id_a,
            PictureLikeness.picture_id_b,
            PictureLikeness.likeness,
        ).where(
            PictureLikeness.picture_id_a.in_(target_ids)
            | PictureLikeness.picture_id_b.in_(target_ids)
        )
    ).all()

    involved = set(target_ids)
    for a, b, _lk in edge_rows:
        involved.add(int(a))
        involved.add(int(b))

    phash_by_id, emb_by_id = _fetch_phash_and_embedding(session, involved)

    uf = _UnionFind()
    for pid in target_ids:
        uf.add(pid)
    for a, b, likeness in edge_rows:
        a, b = int(a), int(b)
        corroborated, _reason = _is_corroborated(a, b, likeness, phash_by_id, emb_by_id)
        if corroborated:
            uf.union(a, b)

    components = uf.components()
    primary_set_by_pid = _fetch_primary_sets(session, involved)

    assigned = 0
    conflicted = 0
    fresh_components: list[tuple[int, set[int]]] = []
    now = datetime.utcnow()

    for _root, members in components.items():
        component_key = min(members)
        existing_members = {
            pid: existing_rows[pid] for pid in members if pid in existing_rows
        }
        new_members = members - existing_members.keys()
        if not new_members:
            continue  # every member already has a row; nothing to do

        has_existing_conflict = any(row.conflict for row in existing_members.values())
        non_conflict_splits = {
            row.split for row in existing_members.values() if not row.conflict
        }

        if has_existing_conflict or len(non_conflict_splits) > 1:
            detail = (
                f"component {component_key} joins pictures with disagreeing "
                f"pre-existing splits {sorted(non_conflict_splits)!r} "
                "(or an already-conflicted member)"
            )
            for pid in new_members:
                session.add(
                    PictureSplit(
                        picture_id=pid,
                        component_key=component_key,
                        split=SplitValue.NEITHER.value,
                        conflict=True,
                        conflict_detail=detail,
                        assigned_at=now,
                    )
                )
                conflicted += 1
            # Fail-closed: any pre-existing member that disagreed but wasn't
            # yet flagged becomes flagged too, now that the component is
            # known to conflict — never leave one side silently un-flagged.
            for row in existing_members.values():
                if not row.conflict:
                    row.split = SplitValue.NEITHER.value
                    row.conflict = True
                    row.conflict_detail = detail
                    session.add(row)
                    conflicted += 1
            continue

        if len(non_conflict_splits) == 1:
            target_split = next(iter(non_conflict_splits))
            for pid in new_members:
                session.add(
                    PictureSplit(
                        picture_id=pid,
                        component_key=component_key,
                        split=target_split,
                        conflict=False,
                        assigned_at=now,
                    )
                )
                assigned += 1
            continue

        # No pre-existing members at all: a genuinely fresh component,
        # deferred to the stratified batch pass below.
        fresh_components.append((component_key, members))

    buckets: dict[Optional[int], list[tuple[int, set[int]]]] = defaultdict(list)
    for component_key, members in fresh_components:
        stratify_set = _component_stratify_set(members, primary_set_by_pid)
        buckets[stratify_set].append((component_key, members))

    for _stratify_set, comps in buckets.items():
        total = sum(len(members) for _key, members in comps)
        target_train = round(total * TRAIN_RATIO)
        ordered = sorted(comps, key=lambda item: _stable_hash01(item[0]))
        cum = 0
        for component_key, members in ordered:
            split = (
                SplitValue.TRAIN.value if cum < target_train else SplitValue.EVAL.value
            )
            cum += len(members)
            for pid in members:
                session.add(
                    PictureSplit(
                        picture_id=pid,
                        component_key=component_key,
                        split=split,
                        conflict=False,
                        assigned_at=now,
                    )
                )
                assigned += 1

    session.commit()
    return {"assigned": assigned, "conflicted": conflicted}


def assign_splits(vault: "Vault") -> dict:
    """Vault-facing wrapper: runs ``assign_splits_in_session`` on the DB worker."""
    return vault.db.run_task(assign_splits_in_session)


# --------------------------------------------------------------------------- #
# Conflict queue + human resolution
# --------------------------------------------------------------------------- #


def list_conflicts_in_session(
    session: Session, *, limit: int = 100, offset: int = 0
) -> dict:
    """Paginated ``conflict=True`` rows — the conflict queue itself."""
    total = session.exec(
        select(func.count())
        .select_from(PictureSplit)
        .where(PictureSplit.conflict.is_(True))
    ).one()
    total = total[0] if isinstance(total, (tuple, list)) else total
    rows = session.exec(
        select(PictureSplit)
        .where(PictureSplit.conflict.is_(True))
        .order_by(PictureSplit.picture_id)
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "total": int(total or 0),
        "rows": [
            {
                "picture_id": r.picture_id,
                "split": r.split,
                "component_key": r.component_key,
                "assigned_at": r.assigned_at.isoformat() if r.assigned_at else None,
                "conflict_detail": r.conflict_detail,
            }
            for r in rows
        ],
    }


def list_conflicts(vault: "Vault", *, limit: int = 100, offset: int = 0) -> dict:
    """Vault-facing wrapper for :func:`list_conflicts_in_session`."""

    def _fetch(session: Session) -> dict:
        return list_conflicts_in_session(session, limit=limit, offset=offset)

    return vault.db.run_immediate_read_task(_fetch)


def resolve_conflict_in_session(
    session: Session, picture_id: int, target_split: str
) -> dict:
    """Human resolution of a conflicted component.

    Resolving one picture resolves its **entire** corroborated component to
    the given split. Judgement call (the design doc left "a pair or one
    picture" open): a component can have more than two members, so "the
    pair" doesn't generalize; resolving only part of a component would just
    re-trigger the write-path guard the moment the next corroborated edge
    within it is (re)discovered. The component is this table's unit of
    atomicity throughout, so it's also the unit of resolution.

    Raises:
        KeyError: no ``PictureSplit`` row for ``picture_id``.
        ValueError: ``target_split`` is not TRAIN/EVAL/NEITHER.
    """
    if target_split not in VALID_SPLITS:
        raise ValueError(
            f"Invalid split {target_split!r}; expected one of {VALID_SPLITS}"
        )
    row = session.get(PictureSplit, picture_id)
    if row is None:
        raise KeyError(f"No PictureSplit row for picture {picture_id}")

    members = session.exec(
        select(PictureSplit).where(PictureSplit.component_key == row.component_key)
    ).all()
    now = datetime.utcnow()
    resolved_ids = []
    for member in members:
        member.split = target_split
        member.conflict = False
        member.conflict_detail = None
        member.assigned_at = now
        session.add(member)
        resolved_ids.append(member.picture_id)
    session.commit()
    return {"picture_ids": sorted(resolved_ids), "split": target_split}


def resolve_conflict(vault: "Vault", picture_id: int, target_split: str) -> dict:
    """Vault-facing wrapper for :func:`resolve_conflict_in_session`."""

    def _resolve(session: Session) -> dict:
        return resolve_conflict_in_session(session, picture_id, target_split)

    return vault.db.run_task(_resolve)


# --------------------------------------------------------------------------- #
# Read-path guard: defense in depth for a future eval-slice freeze (Wave C)
# --------------------------------------------------------------------------- #


def has_train_side_conflict(session: Session, picture_ids: Iterable[int]) -> set[int]:
    """Which of ``picture_ids`` has a corroborated near-dup on the TRAIN side.

    Secondary, defense-in-depth guard (see module docstring): a future
    eval-slice freeze (Wave C, not implemented here) should call this right
    before freezing to re-validate that no candidate EVAL item's corroborated
    component contains a TRAIN member — catching a race between edge
    discovery and freeze in the same window. Pure query, no graph
    recomputation: it trusts ``component_key`` as already capturing
    corroborated near-dup membership.

    Args:
        session: Open DB session.
        picture_ids: Candidate picture ids to check (typically EVAL-side).

    Returns:
        The subset of ``picture_ids`` whose component (by ``component_key``)
        currently has at least one ``TRAIN``-split member. Pictures with no
        ``PictureSplit`` row are never flagged (nothing to check against).
    """
    ids = list({int(p) for p in picture_ids})
    if not ids:
        return set()
    rows = session.exec(
        select(PictureSplit.picture_id, PictureSplit.component_key).where(
            PictureSplit.picture_id.in_(ids)
        )
    ).all()
    key_by_pid = {int(pid): int(key) for pid, key in rows}
    keys = set(key_by_pid.values())
    if not keys:
        return set()
    train_keys = {
        int(key)
        for key in session.exec(
            select(PictureSplit.component_key).where(
                PictureSplit.component_key.in_(keys),
                PictureSplit.split == SplitValue.TRAIN.value,
            )
        ).all()
    }
    if not train_keys:
        return set()
    return {pid for pid, key in key_by_pid.items() if key in train_keys}
