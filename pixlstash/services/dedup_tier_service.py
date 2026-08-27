"""Tiered duplicate detection: exact, bucketed near, and embedding.

The v1.9 Dedup -> Stacks design replaces the "Similarity to ..." sort order with
a **Duplicates** destination whose queue is filled by three tiers of increasing
cost and decreasing certainty. This module owns detection, the tier policy, the
cover preselection and the evidence pills; :mod:`pixlstash.services.dedup_verdict_service`
owns what happens when the user decides.

Tier 1 - exact
--------------
``GROUP BY`` on an indexed hash column. **The column is the existing
``picture.pixel_sha``** (``Field(default=None, index=True)`` in
:mod:`pixlstash.db_models.picture`), not a new one. Being honest about what it
is: ``ImageUtils._calculate_sha256_digest`` hashes the whole file only up to
128 KiB and otherwise samples 8 chunks of 8 KiB spread across the file, so it is
a *sampled* content digest, not a full-file SHA-256. Two files can in principle
share a ``pixel_sha`` while differing in an unsampled region.

That is why tier 1 groups on ``(pixel_sha, size_bytes)`` rather than
``pixel_sha`` alone: the sample offsets are derived from the file size, so equal
size plus equal sampled digest is a far stronger claim than the digest alone,
and the extra column costs nothing (the ``pixel_sha`` index already narrows the
group). It is still not a cryptographic identity proof, which is exactly why the
design routes exact matches through a bulk auto-stack **dialog** with a dry-run
count rather than stacking them at import without consent, and why no tier ever
deletes anything.

A new full-file hash column was considered and rejected: it would mean re-reading
every byte of every file in the library on upgrade to buy a guarantee the feature
does not need (the failure mode of a false exact match is two genuinely different
pictures ending up in one *stack*, which is reversible with one keystroke).
``pixel_sha`` is already computed incrementally on every import path, and
:class:`~pixlstash.tasks.missing_pixel_sha_finder.MissingPixelShaFinder` backfills
the rows that predate it.

Tier 2 - bucketed near
----------------------
Perceptual hashes compared **only within candidate buckets**, never library-wide.
The buckets reuse what the library already precomputes:

* ``picture.size_bin_index`` - an indexed ``(width << 32) + height`` column
  maintained by ``LikenessParameterUtils.size_bin_index``. Exact-dimension
  bucket; catches re-saves, re-encodes and burst frames.
* capture minute - ``created_at`` truncated to the minute; catches bursts and
  re-exports that changed dimensions.
* import batch / folder - ``import_source_folder``, and the containing directory
  of ``file_path`` for reference-folder pictures.

``LikenessParameter.PHASH_PREFIX`` was investigated and is deliberately **not**
used as a bucket key. Despite the name it stores the *entire* 64-bit dHash
linearly normalised into ``[0, 1]`` (``int(phash[:16], 16) / (2**64 - 1)``), so
numeric proximity in that slot is dominated by the top bit and says nothing about
Hamming proximity. ``LikenessUtils.PHASH_PREFIX_LEN = 3`` is dead code with no
reader. Within a bucket this module does the real thing: XOR + popcount over the
64-bit dHash, vectorised with numpy.

Each bucket is an independent unit of work, so buckets stream into the queue as
they finish rather than the queue waiting for a full pass.

Tier 3 - embedding
------------------
The existing :class:`~pixlstash.db_models.picture_likeness.PictureLikeness` edge
table, folded into components by the shipped
:mod:`pixlstash.services.dedup_sweep_service` planner. Opt-in, appended to the
same queue. Nothing is recomputed: this tier is a different reading of data the
image-embedding worker already produced.

Policy
------
:class:`TierPolicy` replaces the shipped :class:`~pixlstash.services.dedup_sweep_service.SweepPolicy`
auto/review split *for the queue*. Tier 1 is always on and cannot be switched
off; each looser tier is a separate opt-in that requires the tier above it; the
similarity threshold defaults to 0.90 and **nothing below 0.65 is ever
suggested**. The dry-run planner and its report stay exactly as shipped - they
are the non-destructive foundation this builds on, and ``SweepPolicy`` remains
the parameter object for that surface.
"""

from __future__ import annotations

import base64
import math
import hashlib
import json
from binascii import Error as BinasciiError
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Optional

import numpy as np
from sqlalchemy import case, func
from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureStack
from pixlstash.db_models.dedup import (
    SCAN_PENDING,
    SCAN_RUNNING,
    TIER_EMBEDDING,
    TIER_EXACT,
    TIER_NEAR,
    VERDICT_KEEP_SEPARATE,
    VERDICT_STACKED,
    DedupGroup,
    DedupGroupMember,
    DedupScan,
    DedupVerdict,
)
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.picture_set import PictureSetMember
from pixlstash.db_models.face import Face
from pixlstash.db_models.quality import Quality
from pixlstash.db_models.tag import Tag
from pixlstash.pixl_logging import get_logger
from pixlstash.services import dedup_sweep_service
from pixlstash.services.set_lock_service import (
    StackablePartition,
    build_locked_set_lookup,
    locked_picture_id_subquery,
    locked_sets_freezing_stacks,
    partition_stackable_members,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.sql_chunking import SQLITE_ID_CHUNK as ID_CHUNK

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)


class DedupScanBusyError(RuntimeError):
    """A scope already has an active scan under a different durable policy."""

    def __init__(self, active_scan: dict[str, Any]):
        self.active_scan = active_scan
        super().__init__(
            "A duplicate scan is already active for this scope with a different "
            "tier policy. Wait for it to finish before requesting another scan."
        )


# --- Policy constants -------------------------------------------------------

DEFAULT_THRESHOLD = 0.90
"""The near-duplicate similarity default (design §7 "Threshold")."""

MIN_THRESHOLD = 0.65
"""Hard floor. Below this nothing is suggested at all - a low threshold produces
confident-looking garbage and destroys trust in the sidebar count. Never a silent
clamp: :class:`TierPolicy` raises ``ValueError`` here, and every route carries the
same bound as a pydantic ``ge=``, so over HTTP a low threshold is refused with a
**422** before any handler runs."""

MAX_THRESHOLD = 0.99999

DEFAULT_MIN_GROUP_SIZE = 2
DEFAULT_MAX_GROUP_SIZE = 24
"""Ceiling on a single detected group. A larger transitively-chained blob is
almost never one duplicate cluster; it is split no further but reported with a
lower confidence so it sorts to the bottom of the queue."""

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200

DEFAULT_STACK_MEMBER_PAGE_SIZE = 50
MAX_STACK_MEMBER_PAGE_SIZE = 200
"""Page size and ceiling for ``GET /dedup/stacks/{stack_id}/members``.

The queue row carries a stack's **count and leader only** (§B1 of
``docs/design/mixed-stacks-and-stack-units.md``): inlining every member of every
stack would put a 40-member stack's worth of tiles behind one queue row, which is
exactly the never-render-the-whole-list rule the queue is built on. The expansion
strip fetches the members when the user opens one, through this endpoint, paged
so even a pathological stack cannot return an unbounded list."""

STACK_POSITION_LAST = 999999
"""``stack_position`` substituted for a member whose position was never assigned.

Mirrors the ``COALESCE(stack_position, 999999)`` in
``Picture._get_stack_leader_ids`` so the deck's face in the Duplicates queue is
the same picture the grid shows as the stack's leader. A stack whose positions
have been normalised has its leader at position 0, which is the design's
statement of the rule; this constant is what makes the answer honest for a stack
that has not been normalised yet."""

MAX_BUCKET_MEMBERS = 4000
"""Cap on one tier-2 bucket. The within-bucket comparison is O(k^2) popcounts,
which numpy does in milliseconds at k=4000 (~8M comparisons); beyond that the
bucket is split into shards rather than being dropped, so nothing is silently
skipped. This bounds **CPU**, not memory - see :data:`MAX_PAIRS_PER_BUCKET`."""

MAX_PAIRS_PER_BUCKET = 50_000
"""Cap on the *materialised* near-pairs of one bucket.

``MAX_BUCKET_MEMBERS`` bounds the comparison work; it does not bound the result.
A bucket whose members are mutually near-identical (a burst of near-black frames,
a folder of solid-colour placeholders, one image copied 4000 times) yields
``k*(k-1)/2`` pairs: ~8M tuples, roughly 580 MB, for a component the union-find
only needs a spanning subset of. 50 000 pairs is ~4 MB.

**The cap can lose membership.** Pairs are emitted in increasing member-offset
order, so the cap keeps the nearest-offset edges and drops the wider ones. For a
uniformly near-identical bucket that costs only confidence resolution (the
offset-1 edges alone span the block). For a dense but *non-uniform* block -
~700 mutually matching members exhaust the cap well inside the low offsets - a
member whose only match sits at a wider offset gets no edge at all and is split
off into its own group or drops out of the queue. Hitting the cap logs a warning
naming the bucket, the offset it stopped at and that consequence; the cap is
never silent and the comment is not reassuring about a case it cannot cover.
Mitigation: resolve the dense block and rescan, narrow the bucket, or raise this
constant for the memory it costs."""

MAX_TRACKED_PAIRS = 400_000
"""Cap on the pairs a whole streaming scan keeps in memory across buckets.

A scan holds pairs from every finished bucket so a chain spanning two buckets
becomes one group. That set is what grows without bound over a large library;
capping it at ~32 MB keeps the scan's footprint flat. Reaching it is logged."""

PHASH_BITS = 64
PHASH_HEX_LEN = PHASH_BITS // 4

RAW_FORMATS = frozenset(
    {
        "raw",
        "arw",
        "cr2",
        "cr3",
        "crw",
        "dng",
        "erf",
        "nef",
        "nrw",
        "orf",
        "pef",
        "raf",
        "rw2",
        "sr2",
        "srw",
        "x3f",
    }
)
"""Formats treated as a camera original by the cover formula's RAW bonus."""

COVER_RAW_BONUS = 8.0
COVER_PIXEL_WEIGHT = 4.0
COVER_TAG_WEIGHT = 3.0
COVER_SCORE_WEIGHT = 2.0
"""Weights of the LEGACY ``cover_score`` composite (kept only for the wire field
of the same name, which is deprecated). Cover selection no longer uses it - see
:func:`cover_order_key` for the ranking that does (2026-07-30 rework)."""

COVER_SMART_SCORE_BUCKET = 0.25
"""Bucket width for the smart-score tier of the cover ranking.

Smart score is the dominant signal (owner requirement, 2026-07-30), but a raw
float compare would let a 0.01 scoring-noise edge outrank a 4x resolution
difference. Scores are therefore compared in quarter-star buckets on the [1, 5]
scale: a lead smaller than 0.25 is treated as "same quality" and the decision
falls through to image size. Bucketing (``floor(score / width)``) rather than an
epsilon compare keeps the relation transitive, so it is usable as a sort key."""

COVER_SMART_SCORE_NEUTRAL = 3.0
"""Effective smart score for a candidate whose stored score is unusable.

``Picture.smart_score`` is NULL until the background task computes it (and is
re-NULLed on invalidation); anything non-positive is defensively treated the
same way, covering the repo's ``-1.0`` failed-metric convention should it ever
reach this column. An unknown score must be *neutral*, never *worst*: ranking
it at zero would bury a not-yet-scored original under every scored copy (the
same rule the sweep keeper and the smart-score grid sort follow - neither ranks
an unscored picture at zero). The midpoint of the [1, 5] scale says "average
until proven otherwise": a copy known to be better still wins the tier, a copy
known to be worse still loses it, and unknown vs unknown falls through to
size."""

COVER_SHARPNESS_NEUTRAL = 0.25
"""Effective sharpness for a candidate with no usable sharpness metric.

``Quality.sharpness`` is absent until the quality task runs and is ``-1.0``
when the computation failed (the repo's failed-metric sentinel). Same neutral
principle as :data:`COVER_SMART_SCORE_NEUTRAL`, at the midpoint of the
metric's typical 0-0.5 range (see ``Quality.calculate_quality_score``)."""

"""SQLite bound-variable safety margin for ``IN`` loads."""


class DedupTier(str, Enum):
    """The three detection tiers, ordered strongest evidence first."""

    EXACT = TIER_EXACT
    NEAR = TIER_NEAR
    EMBEDDING = TIER_EMBEDDING


TIER_ORDER: tuple[DedupTier, ...] = (
    DedupTier.EXACT,
    DedupTier.NEAR,
    DedupTier.EMBEDDING,
)

TIER_STRENGTH: dict[str, int] = {
    TIER_EXACT: 3,
    TIER_NEAR: 2,
    TIER_EMBEDDING: 1,
}
"""How strong each tier's evidence is, for the upsert's tier precedence.

Two tiers can find the *same* group: a byte-identical pair is also perceptually
identical, so a near-enabled rescan rediscovers every exact pair. The upsert is
keyed on the signature, so without precedence the later (weaker) tier overwrote
the stronger one - and an ``exact`` pair silently downgraded to ``near``
disappeared from the exact-only default queue *and* from ``POST
/dedup/auto-stack``, which only ever acts on ``exact``.
"""


def tier_strength(tier: Optional[str]) -> int:
    """Rank a stored tier value; an unknown or missing tier ranks lowest."""
    return TIER_STRENGTH.get(str(tier or ""), 0)


class DedupVerdictKind(str, Enum):
    """The two verdicts a decided group can carry.

    The closed vocabulary the decided page is filtered by. There is no deletion
    verdict in 1.9, and there is deliberately no "reopened" member: reopening
    stamps :attr:`~pixlstash.db_models.dedup.DedupVerdict.reopened_at` and
    returns the group to the open queue, so it stops being a decided row at all.
    """

    STACKED = VERDICT_STACKED
    KEEP_SEPARATE = VERDICT_KEEP_SEPARATE


VERDICT_ORDER: tuple[DedupVerdictKind, ...] = (
    DedupVerdictKind.STACKED,
    DedupVerdictKind.KEEP_SEPARATE,
)
"""The verdicts in the order the decided page's filter lists them."""


class ScopeType(str, Enum):
    """Where a scan or a count is scoped to.

    ``GLOBAL`` is the sidebar's vault-wide badge; the rest are the context-menu
    "Find duplicates in ..." entry points.
    """

    GLOBAL = "global"
    PROJECT = "project"
    SET = "set"
    CHARACTER = "character"
    FOLDER = "folder"


_NUMERIC_SCOPE_TYPES = frozenset(
    {ScopeType.PROJECT, ScopeType.SET, ScopeType.CHARACTER}
)
"""Scopes whose ``scope_id`` is a row id and must parse as an integer."""

LIKE_ESCAPE_CHAR = "\\"
"""Escape character for the folder scope's ``LIKE`` prefix match."""


@dataclass(frozen=True)
class DedupScope:
    """A scan / count scope and the SQL that narrows a picture query to it.

    Attributes:
        scope_type: Which collection kind, or :attr:`ScopeType.GLOBAL`.
        scope_id: The collection's id, or the absolute folder path for
            :attr:`ScopeType.FOLDER`. ``None`` only for ``GLOBAL``.
    """

    scope_type: ScopeType = ScopeType.GLOBAL
    scope_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope_type, ScopeType):
            object.__setattr__(self, "scope_type", ScopeType(str(self.scope_type)))
        if self.scope_type is ScopeType.GLOBAL:
            object.__setattr__(self, "scope_id", None)
            return
        if self.scope_id is None or str(self.scope_id) == "":
            raise ValueError(f"scope_id is required for scope_type={self.scope_type}")
        if self.scope_type in _NUMERIC_SCOPE_TYPES:
            # Validate at construction, not at query time. The id reaches SQL as
            # ``int(...)`` in picture_predicate(); leaving a non-numeric string to
            # blow up there turned a bad request into a 500 on three read routes,
            # and POST /dedup/scan *persisted* the unparseable scope before
            # anything ever tried to parse it. Failing here makes it a 400 at the
            # boundary, before any write.
            try:
                object.__setattr__(self, "scope_id", str(int(str(self.scope_id))))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"scope_id for scope_type={self.scope_type.value} must be an "
                    f"integer id, got {self.scope_id!r}"
                ) from exc
            return
        if self.scope_type is ScopeType.FOLDER:
            # Normalise here, not at query time, and reject what normalises away.
            # The predicate strips trailing separators before building its LIKE
            # prefix, so "/", "\", "///" all became an empty prefix and a LIKE
            # pattern of "%" - a "Find duplicates in this folder" request that
            # silently meant the whole vault, and a persisted dedupscan row whose
            # scope_key claimed otherwise. Normalising at construction also makes
            # "/photos" and "/photos/" the same scope key instead of two scans.
            prefix = str(self.scope_id).rstrip("/\\")
            if not prefix:
                raise ValueError(
                    "scope_id for scope_type=folder must name a folder; "
                    f"{self.scope_id!r} normalises to an empty prefix, which "
                    "would match the whole vault"
                )
            object.__setattr__(self, "scope_id", prefix)

    @property
    def key(self) -> str:
        """Canonical ``DedupScan.scope_key`` for this scope."""
        if self.scope_type is ScopeType.GLOBAL:
            return "global"
        return f"{self.scope_type.value}:{self.scope_id}"

    def picture_predicate(self):
        """Return the SQLAlchemy predicate restricting ``Picture`` to this scope.

        ``None`` for the global scope, so a caller can skip the ``WHERE`` entirely
        rather than emitting a tautology.
        """
        if self.scope_type is ScopeType.GLOBAL:
            return None
        if self.scope_type is ScopeType.PROJECT:
            return Picture.id.in_(
                select(PictureProjectMember.picture_id).where(
                    PictureProjectMember.project_id == int(self.scope_id)
                )
            )
        if self.scope_type is ScopeType.SET:
            return Picture.id.in_(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.set_id == int(self.scope_id)
                )
            )
        if self.scope_type is ScopeType.CHARACTER:
            return Picture.id.in_(
                select(Face.picture_id).where(Face.character_id == int(self.scope_id))
            )
        # FOLDER: a picture is in the folder when it was imported from it or its
        # file lives under it. Both are prefix matches on an indexed column.
        #
        # The LIKE pattern escapes ``%`` / ``_`` / the escape character itself:
        # unescaped, a scope_id of "%" would silently mean "everywhere", so a
        # "Find duplicates in this folder" entry could match far more than the
        # folder it named. The literal path is still compared exactly on
        # import_source_folder. ``scope_id`` was already stripped of trailing
        # separators and rejected if that left it empty (__post_init__).
        prefix = str(self.scope_id)
        pattern = (
            prefix.replace(LIKE_ESCAPE_CHAR, LIKE_ESCAPE_CHAR * 2)
            .replace("%", f"{LIKE_ESCAPE_CHAR}%")
            .replace("_", f"{LIKE_ESCAPE_CHAR}_")
        )
        return (Picture.import_source_folder == prefix) | (
            Picture.file_path.like(f"{pattern}%", escape=LIKE_ESCAPE_CHAR)
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope_type": self.scope_type.value,
            "scope_id": self.scope_id,
            "key": self.key,
        }


@dataclass(frozen=True)
class TierPolicy:
    """Which tiers feed the queue, and how similar is similar enough.

    This is the design's tier gating, and it replaces ``SweepPolicy``'s
    auto/review split as the queue's policy surface. Validated in
    ``__post_init__``: an invalid combination raises :class:`ValueError` (the
    route turns that into a 400) rather than being silently corrected, because a
    silently-retuned duplicate scan is exactly the surprise the feature cannot
    afford.

    Attributes:
        near_enabled: Tier 2. Opt-in.
        embedding_enabled: Tier 3. Opt-in, and requires ``near_enabled`` - the
            design's "enabling one requires the tier above it", so a user cannot
            land on "same scene" suggestions without having deliberately walked
            down to them.
        threshold: Minimum similarity for a near / embedding group to be
            suggested at all. Defaults to :data:`DEFAULT_THRESHOLD`; may never go
            below :data:`MIN_THRESHOLD`.
        min_group_size: Smallest group that counts.
        max_group_size: Groups larger than this keep their members but are
            flagged in their evidence and pushed down the queue.

    Tier 1 (exact) has no flag. It is always included and cannot be switched off.
    """

    near_enabled: bool = False
    embedding_enabled: bool = False
    threshold: float = DEFAULT_THRESHOLD
    min_group_size: int = DEFAULT_MIN_GROUP_SIZE
    max_group_size: int = DEFAULT_MAX_GROUP_SIZE

    def __post_init__(self) -> None:
        if not isinstance(self.threshold, (int, float)) or not (
            MIN_THRESHOLD <= float(self.threshold) <= MAX_THRESHOLD
        ):
            raise ValueError(
                f"threshold must be between {MIN_THRESHOLD} and {MAX_THRESHOLD}, "
                f"got {self.threshold!r}. Below {MIN_THRESHOLD} nothing is "
                "suggested at all."
            )
        if self.embedding_enabled and not self.near_enabled:
            raise ValueError(
                "embedding_enabled requires near_enabled: each looser tier "
                "requires the tier above it"
            )
        if int(self.min_group_size) < 2:
            raise ValueError(
                f"min_group_size must be at least 2, got {self.min_group_size!r}"
            )
        if int(self.max_group_size) < int(self.min_group_size):
            raise ValueError(
                "max_group_size must be >= min_group_size "
                f"({self.max_group_size} < {self.min_group_size})"
            )

    @property
    def tiers(self) -> tuple[DedupTier, ...]:
        """The enabled tiers, strongest first. Always starts with EXACT."""
        enabled = [DedupTier.EXACT]
        if self.near_enabled:
            enabled.append(DedupTier.NEAR)
        if self.embedding_enabled:
            enabled.append(DedupTier.EMBEDDING)
        return tuple(enabled)

    def includes(self, tier: DedupTier) -> bool:
        """Whether *tier* is switched on."""
        return tier in self.tiers

    def as_dict(self) -> dict[str, Any]:
        return {
            "near_enabled": bool(self.near_enabled),
            "embedding_enabled": bool(self.embedding_enabled),
            "threshold": float(self.threshold),
            "min_group_size": int(self.min_group_size),
            "max_group_size": int(self.max_group_size),
        }


@dataclass
class CandidateMember:
    """One picture in a detected group, with everything cover + evidence need.

    Loaded once per group by :func:`load_candidates`; the queue page never reads
    a picture row a second time.
    """

    id: int
    file_path: Optional[str] = None
    format: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    size_bytes: Optional[int] = None
    score: Optional[int] = None
    created_at: Optional[datetime] = None
    imported_at: Optional[datetime] = None
    stack_id: Optional[int] = None
    reference_folder_id: Optional[int] = None
    pixel_sha: Optional[str] = None
    perceptual_hash: Optional[str] = None
    thumbnail_width: Optional[int] = None
    thumbnail_height: Optional[int] = None
    orientation: Optional[int] = None
    tag_count: int = 0
    smart_score: Optional[float] = None
    sharpness: Optional[float] = None

    @property
    def thumbnail_version(self) -> str:
        """The ``?v=`` version the queue's thumbnail URLs must carry.

        Same value and same semantics as the batch-thumbnail endpoint's - both
        call :meth:`ImageUtils.thumbnail_cache_version`. Without it a thumbnail
        regenerated mid-triage would keep painting the stale cached bitmap in the
        queue, because the queue's URL would never change.
        """
        return ImageUtils.thumbnail_cache_version(
            self.thumbnail_width, self.thumbnail_height, self.orientation
        )

    @property
    def pixels(self) -> int:
        """Total pixel count; 0 when the dimensions were never recorded."""
        return int(self.width or 0) * int(self.height or 0)

    @property
    def megapixels(self) -> float:
        return round(self.pixels / 1_000_000.0, 2)

    @property
    def is_raw(self) -> bool:
        """Whether this is a camera original, by format or by file extension."""
        fmt = (self.format or "").strip().lower().lstrip(".")
        if fmt in RAW_FORMATS:
            return True
        path = self.file_path or ""
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return suffix in RAW_FORMATS

    @property
    def aspect_ratio(self) -> Optional[float]:
        if not self.width or not self.height:
            return None
        return round(float(self.width) / float(self.height), 4)

    @property
    def content_key(self) -> str:
        """The member's contribution to the group signature.

        ``pixel_sha:size_bytes`` when the hash exists. **The size is a co-key
        here for the same reason it is one in tier-1 detection** (§22.1):
        ``pixel_sha`` is a *sampled* digest above 128 KiB, so the digest alone
        does not identify a file. Detection has always grouped on
        ``(pixel_sha, size_bytes)``; identity omitting the size made
        :func:`group_signature` non-injective over groups, which meant two
        distinct exact groups differing only in size collapsed onto one
        signature - the upsert dropped one from the queue, a ``keep_separate``
        silenced both file sets, and a stack verdict's write target depended on
        scan order rather than on what the user saw.

        A picture whose hash has not been computed yet falls back to ``id:<n>``,
        which is stable but *not* stable across a re-import of the same file - so
        a verdict made on a group containing such a member will be re-asked after
        a re-import. That is the honest behaviour: pretending two un-hashed rows
        are the same file would make the verdict memory lie.
        ``MissingPixelShaFinder`` closes the gap in the background.
        """
        if self.pixel_sha:
            return f"{self.pixel_sha}:{self.size_bytes}"
        return f"id:{self.id}"

    @property
    def known_smart_score(self) -> Optional[float]:
        """The stored smart score, when it is actually usable.

        ``None`` covers both "never computed / invalidated" (a NULL column -
        ``MissingSmartScoreFinder`` will fill it) and any non-positive value
        (defence against the repo's ``-1.0`` failed-metric sentinel; the real
        scale starts at 1). The ranking treats an unknown score as *neutral*
        via :data:`COVER_SMART_SCORE_NEUTRAL`, never as zero.
        """
        if self.smart_score is None:
            return None
        value = float(self.smart_score)
        return value if value > 0.0 else None

    @property
    def smart_score_bucket(self) -> int:
        """The quarter-star bucket the cover ranking compares smart scores in."""
        value = self.known_smart_score
        if value is None:
            value = COVER_SMART_SCORE_NEUTRAL
        return math.floor(value / COVER_SMART_SCORE_BUCKET)

    @property
    def known_sharpness(self) -> Optional[float]:
        """The stored sharpness metric, when usable.

        ``None`` covers a missing ``Quality`` row / NULL column and the
        ``-1.0`` failed-metric sentinel the quality task writes for a picture
        it could not decode.
        """
        if self.sharpness is None:
            return None
        value = float(self.sharpness)
        return value if value >= 0.0 else None

    @property
    def effective_sharpness(self) -> float:
        """Sharpness as the ranking compares it: neutral when unknown."""
        value = self.known_sharpness
        return value if value is not None else COVER_SHARPNESS_NEUTRAL

    @property
    def cover_score(self) -> float:
        """DEPRECATED legacy composite: ``px*4 + tags*3 + userScore*2 + RAW``.

        No longer the selection rule (see :func:`cover_order_key`, 2026-07-30).
        Kept only because the wire field of the same name shipped; scheduled for
        removal once the frontend reads ``smart_score`` + the why-pills instead.
        """
        return (
            self.megapixels * COVER_PIXEL_WEIGHT
            + float(self.tag_count) * COVER_TAG_WEIGHT
            + float(self.score or 0) * COVER_SCORE_WEIGHT
            + (COVER_RAW_BONUS if self.is_raw else 0.0)
        )

    def as_dict(self, *, why: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        """Serialise for the queue / compare API.

        ``file_path`` is included **only for reference-folder pictures** (design
        §7 "Paths only where they matter"): there the user manages the files and
        needs to know which copy is which, while for a managed-library picture
        the path is an implementation detail.
        """
        return {
            "picture_id": self.id,
            "width": self.width,
            "height": self.height,
            "megapixels": self.megapixels,
            "size_bytes": self.size_bytes,
            "format": self.format,
            "is_raw": self.is_raw,
            "score": self.score,
            "tag_count": self.tag_count,
            "created_at": self.created_at,
            "imported_at": self.imported_at,
            "stack_id": self.stack_id,
            "reference_folder_id": self.reference_folder_id,
            "file_path": (
                self.file_path if self.reference_folder_id is not None else None
            ),
            "thumbnail_version": self.thumbnail_version,
            # The ranking signals, null-safe for display: null means "not
            # computed yet or failed", and the client shows a dash rather than
            # a fake zero (or the meaningless -1.0 sentinel).
            "smart_score": (
                round(self.known_smart_score, 3)
                if self.known_smart_score is not None
                else None
            ),
            "sharpness": (
                round(self.known_sharpness, 3)
                if self.known_sharpness is not None
                else None
            ),
            # DEPRECATED: the legacy composite, no longer the selection rule.
            "cover_score": round(self.cover_score, 4),
            "why": why if why is not None else [],
        }


@dataclass
class DetectedGroup:
    """A group produced by one of the tiers, before it is persisted.

    Attributes:
        tier: Which tier found it.
        confidence: 1.0 for exact; the weakest pairwise similarity otherwise.
        members: Every member, in cover-preselection order (cover first).
        cover_picture_id: The cover preselection.
        evidence: The group-level why-pills.
        signature: The verdict-memory key.
    """

    tier: DedupTier
    confidence: float
    members: list[CandidateMember]
    cover_picture_id: int
    evidence: list[dict[str, Any]] = field(default_factory=list)
    signature: str = ""

    @property
    def picture_ids(self) -> list[int]:
        return [member.id for member in self.members]


# --- Signature --------------------------------------------------------------


def group_signature(content_keys: Iterable[str]) -> str:
    """Stable identity for a *set of files*, independent of ids and order.

    The design keys verdict memory on "sorted member content hashes"; this is
    that, hashed down to a fixed-width string so it indexes cheaply. Sorting
    first is what makes the signature survive a rescan finding the members in a
    different order, and hashing content rather than ids is what makes it survive
    a re-import that assigns new picture ids.

    Args:
        content_keys: Per-member content keys (see
            :attr:`CandidateMember.content_key`).

    Returns:
        A 64-character lowercase hex digest.
    """
    joined = "\x1f".join(sorted(str(key) for key in content_keys))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --- Cover selection --------------------------------------------------------


def cover_order_key(member: CandidateMember) -> tuple:
    """Sort key implementing the cover ranking (reworked 2026-07-30).

    Lexicographic tiers, strongest first - a lower tier can never outvote a
    higher one, which is what "prioritise smart score" means and what the old
    weighted sum (``px*4 + tags*3 + score*2 + RAW``) could not express: there a
    40 MP blurry scan outscored a sharp 12 MP original on pixels alone.

    1. **Smart score**, in quarter-star buckets
       (:data:`COVER_SMART_SCORE_BUCKET`): the library's one composite quality
       opinion (CLIP anchors, aesthetics, sharpness, resolution, detail,
       anomaly penalty - see ``pixlstash/scoring/smart_score.py``). Unknown or
       failed scores rank *neutral* (:data:`COVER_SMART_SCORE_NEUTRAL`), never
       zero. Bucketing keeps scoring noise from outranking real differences.
    2. **Image size** as raw pixel count. Pixels, not bytes: bytes measure
       compression, pixels measure the information you would lose by keeping
       the smaller copy. Exact duplicates share dimensions, so this tier ties
       exactly there and the decision moves on.
    3. **Sharpness** (``Quality.sharpness``, the objective per-picture metric;
       unknown/failed ranks neutral). Sharpness is already *inside* smart
       score, but at equal smart bucket and equal pixels it is the best
       remaining objective discriminator between two renditions of the same
       shot.
    4. **Human stars** (``Picture.score``). Deliberately below the quality
       tiers here, although the canonical stack order
       (``routes/stacks.py::_stack_order_key`` / the sweep keeper) puts it
       first: duplicates of one shot rarely carry different stars, and the
       post-stack metadata union lifts every member to ``max(score)`` anyway,
       so inside a duplicate group stars barely discriminate - and the owner's
       requirement is smart score first.
    5. **Tag count** (richer metadata), then the **RAW** camera-original
       bonus, then **file size in bytes** (at equal pixels the heavier file is
       the less-compressed one).
    6. Ties break to the **oldest capture time** (the original, not a later
       re-export - the one deliberate inversion of the stack order's
       recency-first rule, because a duplicate group wants its origin), then
       to the lowest id so the choice is deterministic for rows with no
       timestamp.
    """
    created = member.created_at
    created_ts = created.timestamp() if isinstance(created, datetime) else float("inf")
    return (
        -member.smart_score_bucket,
        -member.pixels,
        -member.effective_sharpness,
        -int(member.score or 0),
        -int(member.tag_count),
        0 if member.is_raw else 1,
        -int(member.size_bytes or 0),
        created_ts,
        int(member.id),
    )


def select_cover(members: list[CandidateMember]) -> int:
    """Return the preselected cover's picture id.

    Never silent: the caller surfaces this as a *preselection* the user overrides
    with 1-9, together with the per-candidate evidence that explains it.
    """
    if not members:
        raise ValueError("select_cover requires at least one member")
    return min(members, key=cover_order_key).id


# --- Evidence ---------------------------------------------------------------


def _pill(text: str, against: bool = False) -> dict[str, Any]:
    """One why-pill: matching evidence (olive check) or evidence against (red x)."""
    return {"text": text, "against": bool(against)}


_LARGE_GROUP_EVIDENCE_PREFIX = "Unusually large group ("


def _refresh_group_size_evidence(
    evidence: list[Any], member_count: int, max_group_size: int
) -> list[Any]:
    """Make the size warning describe the live members in the payload.

    Evidence is stored when the scan finds a group, while queue candidates are
    filtered live when the page is read. A member moved to the Scrapheap can
    therefore shrink a row without rewriting its stored evidence. Remove the
    scan-time size warning and add it back only when the live row still exceeds
    the policy limit, using the same count the response reports.
    """
    refreshed = []
    for pill in evidence:
        if isinstance(pill, dict):
            text = pill.get("text", pill.get("label", ""))
        else:
            text = pill
        if str(text).startswith(_LARGE_GROUP_EVIDENCE_PREFIX):
            continue
        refreshed.append(pill)
    if member_count > max_group_size:
        refreshed.append(
            _pill(
                f"Unusually large group ({member_count} pictures)",
                against=True,
            )
        )
    return refreshed


def _humanise_gap(seconds: float) -> str:
    if seconds < 2:
        return f"{seconds:.1f}s apart"
    if seconds < 120:
        return f"{int(round(seconds))}s apart"
    if seconds < 7200:
        return f"{int(round(seconds / 60))} min apart"
    if seconds < 172800:
        return f"{int(round(seconds / 3600))} hours apart"
    return f"{int(round(seconds / 86400))} days apart"


def build_group_evidence(
    tier: DedupTier, confidence: float, members: list[CandidateMember]
) -> list[dict[str, Any]]:
    """The group-level why-pills, both directions.

    The design's rule is that signals cut both ways: matching evidence is an
    olive check, anything arguing *against* a stack is a red x, and a group
    carrying red pills is exactly the one that needs Compare. So this deliberately
    reports resolution / aspect-ratio / format mismatches alongside the match.

    Nothing here is a conclusion - the client renders reasons and the user
    decides.
    """
    pills: list[dict[str, Any]] = []
    if tier is DedupTier.EXACT:
        pills.append(_pill("Identical file hash"))
    # Near-group confidence is already rendered as the group's ``% similar``
    # badge. Repeating that exact value as a ``% visual match`` pill adds no
    # evidence; exact groups keep the hash pill because it names the mechanism,
    # not merely the confidence score.

    dimensions = {(m.width, m.height) for m in members if m.width and m.height}
    if len(dimensions) == 1:
        pills.append(_pill("Same dimensions"))
    elif len(dimensions) > 1:
        pills.append(_pill("Different resolution", against=True))

    ratios = {m.aspect_ratio for m in members if m.aspect_ratio is not None}
    if len(ratios) > 1 and max(ratios) - min(ratios) > 0.01:
        pills.append(_pill("Different aspect ratio", against=True))

    formats = {(m.format or "").lower() for m in members if m.format}
    if len(formats) > 1:
        pills.append(_pill("Different file format", against=True))

    captures = sorted(m.created_at for m in members if m.created_at is not None)
    if len(captures) >= 2:
        span = (captures[-1] - captures[0]).total_seconds()
        if span <= 1.0:
            pills.append(_pill("Same capture second"))
        elif span <= 5.0:
            pills.append(_pill(f"Burst - {_humanise_gap(span)}"))
        else:
            pills.append(_pill(f"Captured {_humanise_gap(span)}"))

    folders = {_parent_folder(m.file_path) for m in members if m.file_path}
    folders.discard(None)
    if len(folders) == 1 and len(members) > 1:
        pills.append(_pill("Same folder"))

    imports = sorted(m.imported_at for m in members if m.imported_at is not None)
    if len(imports) >= 2:
        span = (imports[-1] - imports[0]).total_seconds()
        if span <= 600:
            pills.append(_pill(f"Imported {_humanise_gap(span)}"))

    return pills


def build_candidate_evidence(
    member: CandidateMember, members: list[CandidateMember], cover_id: int
) -> list[dict[str, Any]]:
    """Per-candidate why-pills, so the client renders reasons, not conclusions.

    These are the signals :func:`cover_order_key` actually ranks on, stated per
    candidate in ranking-priority order - smart score first, then resolution,
    then sharpness, then the lower-order signals - so a user can see *why* the
    preselection landed where it did and disagree with it. A signal nobody in
    the group carries (no smart score computed yet, no sharpness) produces no
    pill: the serialized ``smart_score`` / ``sharpness`` fields are null there
    and the client shows a dash, which is more honest than a pill about a
    number that does not exist.
    """
    pills: list[dict[str, Any]] = []

    # Tier 1: smart score, compared in the same quarter-star buckets the
    # ranking uses, so two effectively-tied candidates both read as best
    # instead of one carrying a "lower" pill over scoring noise.
    known = [m for m in members if m.known_smart_score is not None]
    if known and member.known_smart_score is not None:
        best_bucket = max(m.smart_score_bucket for m in known)
        best_display = max(m.known_smart_score for m in known)
        if member.smart_score_bucket == best_bucket:
            pills.append(_pill(f"Best smart score ({member.known_smart_score:.1f})"))
        else:
            pills.append(
                _pill(
                    f"Lower smart score ({member.known_smart_score:.1f} vs "
                    f"{best_display:.1f})",
                    against=True,
                )
            )

    # Tier 2: image size.
    best_pixels = max((m.pixels for m in members), default=0)
    if member.pixels and member.pixels == best_pixels:
        pills.append(_pill("Highest resolution"))
    elif member.pixels and best_pixels:
        shortfall = 100 - int(round(100 * member.pixels / best_pixels))
        pills.append(_pill(f"{shortfall}% fewer pixels than the best", against=True))

    # Tier 3: sharpness, positive-only - a third-order "softer" red pill on
    # every non-sharpest member would be noise, not evidence.
    with_sharpness = [m for m in members if m.known_sharpness is not None]
    if with_sharpness and member.known_sharpness is not None:
        best_sharpness = max(m.known_sharpness for m in with_sharpness)
        if member.known_sharpness == best_sharpness:
            pills.append(_pill("Sharpest copy"))

    # Lower-order signals, in ranking order.
    best_score = max((int(m.score or 0) for m in members), default=0)
    if best_score and int(member.score or 0) == best_score:
        pills.append(_pill(f"Highest score ({best_score})"))

    best_tags = max((m.tag_count for m in members), default=0)
    if best_tags and member.tag_count == best_tags:
        pills.append(_pill(f"Most metadata ({member.tag_count} tags)"))
    elif best_tags and member.tag_count < best_tags:
        pills.append(
            _pill(
                f"Fewer tags than the best ({member.tag_count} of {best_tags})",
                against=True,
            )
        )

    if member.is_raw:
        pills.append(_pill("Camera original (RAW)"))

    if member.id == cover_id:
        pills.append(_pill("Preselected as cover"))
    return pills


def _parent_folder(file_path: Optional[str]) -> Optional[str]:
    if not file_path:
        return None
    normalised = file_path.replace("\\", "/")
    if "/" not in normalised:
        return None
    return normalised.rsplit("/", 1)[0]


# --- Candidate loading ------------------------------------------------------


def load_candidates(
    session: Session, picture_ids: Iterable[int]
) -> dict[int, CandidateMember]:
    """Load the cover / evidence columns for *picture_ids*, plus their tag counts.

    One query per id chunk for the picture columns and one for the tag counts -
    never a per-picture round trip, because the queue loads a whole page of
    groups at once.
    """
    ordered = sorted({int(pid) for pid in picture_ids})
    if not ordered:
        return {}
    members: dict[int, CandidateMember] = {}
    for start in range(0, len(ordered), ID_CHUNK):
        chunk = ordered[start : start + ID_CHUNK]
        rows = session.exec(
            select(
                Picture.id,
                Picture.file_path,
                Picture.format,
                Picture.width,
                Picture.height,
                Picture.size_bytes,
                Picture.score,
                Picture.created_at,
                Picture.imported_at,
                Picture.stack_id,
                Picture.reference_folder_id,
                Picture.pixel_sha,
                Picture.perceptual_hash,
                Picture.thumbnail_width,
                Picture.thumbnail_height,
                Picture.orientation,
                Picture.smart_score,
            ).where(Picture.id.in_(chunk), Picture.deleted.is_(False))
        ).all()
        for row in rows:
            members[int(row[0])] = CandidateMember(
                id=int(row[0]),
                file_path=row[1],
                format=row[2],
                width=row[3],
                height=row[4],
                size_bytes=row[5],
                score=row[6],
                created_at=row[7],
                imported_at=row[8],
                stack_id=row[9],
                reference_folder_id=row[10],
                pixel_sha=row[11],
                perceptual_hash=row[12],
                thumbnail_width=row[13],
                thumbnail_height=row[14],
                orientation=row[15],
                smart_score=row[16],
            )
        tag_rows = session.exec(
            select(Tag.picture_id, func.count(Tag.id))
            .where(Tag.picture_id.in_(chunk))
            .group_by(Tag.picture_id)
        ).all()
        for picture_id, count in tag_rows:
            member = members.get(int(picture_id))
            if member is not None:
                member.tag_count = int(count or 0)
        # Sharpness for the ranking's third tier. Its own indexed lookup, like
        # the tag counts - Quality is one row per picture (or absent until the
        # quality task has run; -1.0 marks a failed computation, and both read
        # as "unknown" through CandidateMember.known_sharpness).
        quality_rows = session.exec(
            select(Quality.picture_id, Quality.sharpness).where(
                Quality.picture_id.in_(chunk)
            )
        ).all()
        for picture_id, sharpness in quality_rows:
            member = members.get(int(picture_id))
            if member is not None:
                member.sharpness = sharpness
    return members


# --- Stack units ------------------------------------------------------------


@dataclass(frozen=True)
class StackFacts:
    """The truth about one existing stack, independent of any duplicate group.

    The Duplicates queue renders an existing stack as a single **deck** whose
    depth is the stack's real member count, not the number of its members that
    happen to be in the group (design D2). On a real 17k-picture library 36 of
    116 stack-touching groups name only ONE member of a stack, so a client that
    counted the group's own members would draw a 4-deep stack as a single
    picture and then silently move all four.

    Attributes:
        stack_id: The stack.
        member_ids: Every live member, in the canonical stack order, leader
            first. Same ranking as ``Picture._get_stack_leader_ids`` and the
            grid's ``compareStackOrder``.
        leader_thumbnail_version: The leader's thumbnail cache-buster, so the
            deck's face can be rendered from the queue payload alone.
    """

    stack_id: int
    member_ids: tuple[int, ...]
    leader_thumbnail_version: str

    @property
    def member_count(self) -> int:
        """The stack's REAL live member count, not the count within a group."""
        return len(self.member_ids)

    @property
    def leader_picture_id(self) -> int:
        """The member at ``stack_position`` 0: the deck's face."""
        return self.member_ids[0]


def _stack_member_order_key(
    picture_id: int,
    stack_position: Optional[int],
    score: Optional[int],
    created_at: Optional[datetime],
) -> tuple:
    """Canonical stack order: the leader sorts first.

    Deliberately identical to the window function in
    ``Picture._get_stack_leader_ids``: ``COALESCE(stack_position, 999999) ASC,
    COALESCE(score, 0) DESC, created_at DESC, id ASC`` and to the frontend's
    ``compareStackOrder``. A deck whose face disagreed with the grid's leader
    would be the same show-one-mean-another mismatch the deck exists to remove.

    ``created_at DESC`` puts NULLs last (SQLite's ordering under DESC), which is
    why an absent capture time maps to ``-inf`` before the sign flip.
    """
    created_ts = (
        created_at.timestamp() if isinstance(created_at, datetime) else float("-inf")
    )
    return (
        int(stack_position) if stack_position is not None else STACK_POSITION_LAST,
        -int(score or 0),
        -created_ts,
        int(picture_id),
    )


def load_stack_facts(
    session: Session, stack_ids: Iterable[int]
) -> dict[int, StackFacts]:
    """Load the real membership of every stack in *stack_ids*, in one batch.

    **Batched for the whole queue page, never per group.** The page already
    resolves its candidates and its locked-set lookup once for every group it is
    about to serve; resolving stacks per group instead would make the deck
    rollup an N+1 on the one query the queue page is measured by.

    Args:
        session: Pre-opened session.
        stack_ids: The distinct non-null ``stack_id`` values a page touches.

    Returns:
        ``{stack_id: StackFacts}``. A stack with no live member is absent rather
        than present-and-empty, so a caller cannot read a leader off it.
    """
    ordered = sorted({int(sid) for sid in stack_ids if sid is not None})
    if not ordered:
        return {}
    rows_by_stack: dict[int, list[tuple]] = defaultdict(list)
    thumbnails: dict[int, tuple[Optional[int], Optional[int]]] = {}
    for start in range(0, len(ordered), ID_CHUNK):
        chunk = ordered[start : start + ID_CHUNK]
        rows = session.exec(
            select(
                Picture.id,
                Picture.stack_id,
                Picture.stack_position,
                Picture.score,
                Picture.created_at,
                Picture.thumbnail_width,
                Picture.thumbnail_height,
                Picture.orientation,
            ).where(Picture.stack_id.in_(chunk), Picture.deleted.is_(False))
        ).all()
        for (
            picture_id,
            stack_id,
            stack_position,
            score,
            created_at,
            thumbnail_width,
            thumbnail_height,
            orientation,
        ) in rows:
            rows_by_stack[int(stack_id)].append(
                _stack_member_order_key(picture_id, stack_position, score, created_at)
            )
            thumbnails[int(picture_id)] = (
                thumbnail_width,
                thumbnail_height,
                orientation,
            )
    facts: dict[int, StackFacts] = {}
    for stack_id, keys in rows_by_stack.items():
        member_ids = tuple(int(key[-1]) for key in sorted(keys))
        width, height, orientation = thumbnails.get(member_ids[0], (None, None, None))
        facts[stack_id] = StackFacts(
            stack_id=stack_id,
            member_ids=member_ids,
            leader_thumbnail_version=ImageUtils.thumbnail_cache_version(
                width, height, orientation
            ),
        )
    return facts


def build_group_stacks(
    members: list[CandidateMember],
    partition: StackablePartition,
    stack_facts: dict[int, StackFacts],
) -> dict[str, dict[str, Any]]:
    """The per-group ``stacks`` block: one entry per existing stack it touches.

    Keyed by stack id **as a string**, because that is what the key becomes on
    the wire; the ``stack_id`` field inside each entry is the integer.

    ``stackable`` and ``blocked_by_sets`` are the **unit-level rollup** of the
    per-candidate values the caller already computed: a deck is unstackable if
    ANY of its members is, because a stack cannot be partially stacked; it
    moves as a unit or not at all. Nothing about locks is re-derived here.
    ``partition_stackable_members`` is already lock-correct across a whole stack
    (``_locked_sets_by_picture`` expands its input to whole stacks and
    ``locked_sets_for_pictures`` rolls each frozen picture's sets back onto every
    input id), so a locked sibling *outside* this group has already marked the
    members inside it.

    Args:
        members: The group's live candidates.
        partition: The group's stackable/blocked split, over the same members.
        stack_facts: :func:`load_stack_facts` for at least every stack these
            members belong to.

    Returns:
        ``{"<stack id>": {stack_id, member_count, leader_picture_id,
        leader_thumbnail_version, matched_picture_ids, stackable,
        blocked_by_sets}}``. Empty when no member of the group is stacked.
    """
    stackable_ids = set(partition.stackable)
    by_stack: dict[int, list[CandidateMember]] = defaultdict(list)
    for member in members:
        if member.stack_id is not None:
            by_stack[int(member.stack_id)].append(member)

    block: dict[str, dict[str, Any]] = {}
    for stack_id in sorted(by_stack):
        matched = sorted(member.id for member in by_stack[stack_id])
        facts = stack_facts.get(stack_id)
        if facts is None:
            # Every member here is a live picture carrying this stack_id, so the
            # batched load must have found the stack. Reaching this means the
            # caller batched too narrowly (or the stack was emptied between the
            # two reads); report the group's own members rather than inventing a
            # count, and say so loudly - a silently under-reported depth would
            # make the deck claim a verdict moves fewer pictures than it does.
            logger.warning(
                "[dedup-queue] stack %s has no loaded facts; reporting the "
                "group's own %d member(s) as its depth. The deck's member_count "
                "and leader are therefore the group's, not the stack's; batch "
                "load_stack_facts over every stack the page touches.",
                stack_id,
                len(matched),
            )
            leader = min(by_stack[stack_id], key=lambda m: m.id)
            member_count = len(matched)
            leader_picture_id = leader.id
            leader_thumbnail_version = leader.thumbnail_version
        else:
            member_count = facts.member_count
            leader_picture_id = facts.leader_picture_id
            leader_thumbnail_version = facts.leader_thumbnail_version

        blocked: dict[int, str] = {}
        for member in by_stack[stack_id]:
            for entry in partition.sets_for(member.id):
                blocked[int(entry["id"])] = entry["name"]
        block[str(stack_id)] = {
            "stack_id": stack_id,
            "member_count": member_count,
            "leader_picture_id": leader_picture_id,
            "leader_thumbnail_version": leader_thumbnail_version,
            "matched_picture_ids": matched,
            "stackable": all(
                member.id in stackable_ids for member in by_stack[stack_id]
            ),
            "blocked_by_sets": [
                {"id": set_id, "name": name} for set_id, name in sorted(blocked.items())
            ],
        }
    return block


def assemble_group(
    tier: DedupTier, confidence: float, members: list[CandidateMember]
) -> DetectedGroup:
    """Turn raw members into a :class:`DetectedGroup` with cover and evidence."""
    ordered = sorted(members, key=cover_order_key)
    cover_id = ordered[0].id
    return DetectedGroup(
        tier=tier,
        confidence=round(float(confidence), 6),
        members=ordered,
        cover_picture_id=cover_id,
        evidence=build_group_evidence(tier, confidence, ordered),
        signature=group_signature(m.content_key for m in ordered),
    )


# --- Tier 1: exact ----------------------------------------------------------


def find_exact_groups_in_session(
    session: Session, scope: Optional[DedupScope] = None
) -> list[DetectedGroup]:
    """Tier 1. ``GROUP BY pixel_sha, size_bytes HAVING count(*) > 1``.

    Two indexed-column queries: one aggregate to find the duplicated hashes, one
    to pull the member ids for exactly those hashes. No image is decoded, no
    model runs, and the whole tier is milliseconds on a library-sized table
    because ``picture.pixel_sha`` is indexed.

    See the module docstring for why ``size_bytes`` is a co-key.
    """
    scope = scope or DedupScope()
    predicate = scope.picture_predicate()

    duplicated = (
        select(Picture.pixel_sha, Picture.size_bytes)
        .where(
            Picture.pixel_sha.is_not(None),
            Picture.deleted.is_(False),
        )
        .group_by(Picture.pixel_sha, Picture.size_bytes)
        .having(func.count(Picture.id) > 1)
    )
    if predicate is not None:
        duplicated = duplicated.where(predicate)
    keys = [(row[0], row[1]) for row in session.exec(duplicated).all()]
    if not keys:
        return []

    shas = sorted({key[0] for key in keys})
    wanted = set(keys)
    by_key: dict[tuple[Optional[str], Optional[int]], list[int]] = defaultdict(list)
    for start in range(0, len(shas), ID_CHUNK):
        chunk = shas[start : start + ID_CHUNK]
        member_query = select(Picture.id, Picture.pixel_sha, Picture.size_bytes).where(
            Picture.pixel_sha.in_(chunk), Picture.deleted.is_(False)
        )
        if predicate is not None:
            member_query = member_query.where(predicate)
        for picture_id, sha, size_bytes in session.exec(member_query).all():
            key = (sha, size_bytes)
            if key in wanted:
                by_key[key].append(int(picture_id))

    member_ids = [ids for ids in by_key.values() if len(ids) > 1]
    candidates = load_candidates(session, [pid for ids in member_ids for pid in ids])
    groups: list[DetectedGroup] = []
    for ids in member_ids:
        members = [candidates[pid] for pid in ids if pid in candidates]
        if len(members) < 2:
            continue
        groups.append(assemble_group(DedupTier.EXACT, 1.0, members))
    logger.info(
        "[dedup-tier1] scope=%s produced %d exact group(s) from %d hash key(s)",
        scope.key,
        len(groups),
        len(keys),
    )
    return groups


# --- Tier 2: bucketed near --------------------------------------------------


@dataclass(frozen=True)
class NearBucket:
    """One candidate bucket: a bucket key and the picture ids inside it.

    A bucket is a unit of work. It is scanned on its own, its groups become
    visible in the queue as soon as it finishes, and its cost is bounded by
    :data:`MAX_BUCKET_MEMBERS`.
    """

    kind: str
    key: str
    picture_ids: tuple[int, ...]
    oversized: bool = False
    source_member_count: int = 0


def _bucket_rows(session: Session, scope: DedupScope) -> list[tuple]:
    """Load the bucketing columns for every picture that has a perceptual hash."""
    query = select(
        Picture.id,
        Picture.size_bin_index,
        Picture.created_at,
        Picture.import_source_folder,
        Picture.file_path,
    ).where(
        Picture.deleted.is_(False),
        Picture.perceptual_hash.is_not(None),
    )
    predicate = scope.picture_predicate()
    if predicate is not None:
        query = query.where(predicate)
    return list(session.exec(query).all())


def build_near_buckets(
    session: Session, scope: Optional[DedupScope] = None
) -> list[NearBucket]:
    """Group the scope's pictures into tier-2 candidate buckets.

    Four bucket kinds, all cheap and all reusing precomputed columns:

    * ``size_bin`` - the indexed ``picture.size_bin_index`` (exact width/height).
    * ``capture_minute`` - ``created_at`` truncated to the minute.
    * ``import_folder`` - the ``import_source_folder`` batch.
    * ``folder`` - the containing directory of ``file_path``.

    A picture appears in several buckets; that is the point. Buckets larger than
    :data:`MAX_BUCKET_MEMBERS` are split on the leading hex digits of the picture
    id ordering rather than dropped, so no candidate is silently skipped.

    Singleton buckets are discarded here rather than in the scan, so the scan's
    "N of M buckets" progress describes real work.
    """
    scope = scope or DedupScope()
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for picture_id, size_bin, created_at, import_folder, file_path in _bucket_rows(
        session, scope
    ):
        picture_id = int(picture_id)
        if size_bin is not None:
            grouped[("size_bin", str(size_bin))].append(picture_id)
        if isinstance(created_at, datetime):
            grouped[("capture_minute", created_at.strftime("%Y-%m-%dT%H:%M"))].append(
                picture_id
            )
        if import_folder:
            grouped[("import_folder", str(import_folder))].append(picture_id)
        folder = _parent_folder(file_path)
        if folder:
            grouped[("folder", folder)].append(picture_id)

    buckets: list[NearBucket] = []
    for (kind, key), ids in grouped.items():
        if len(ids) < 2:
            continue
        ordered = sorted(set(ids))
        if len(ordered) <= MAX_BUCKET_MEMBERS:
            buckets.append(NearBucket(kind=kind, key=key, picture_ids=tuple(ordered)))
            continue
        # Oversized bucket: split into bounded contiguous shards, but overlap the
        # preceding boundary member. Without that overlap a 4,001-member bucket
        # produced a 4,000-member shard plus a discarded singleton, so the last
        # member was not compared at all. The overlap cannot recover every
        # cross-shard pair, so the scan is reported as partial by its task.
        logger.warning(
            "[dedup-tier2] bucket %s=%s has %d members; splitting into overlapping "
            "shards of %d and reporting the scan partial because cross-shard "
            "comparisons are incomplete",
            kind,
            key,
            len(ordered),
            MAX_BUCKET_MEMBERS,
        )
        stride = MAX_BUCKET_MEMBERS - 1
        for shard_index, start in enumerate(range(0, len(ordered), stride)):
            shard = ordered[start : start + MAX_BUCKET_MEMBERS]
            if len(shard) < 2:
                continue
            buckets.append(
                NearBucket(
                    kind=kind,
                    key=f"{key}#{shard_index}",
                    picture_ids=tuple(shard),
                    oversized=True,
                    source_member_count=len(ordered),
                )
            )
    buckets.sort(key=lambda bucket: (bucket.kind, bucket.key))
    logger.info(
        "[dedup-tier2] scope=%s produced %d candidate bucket(s)",
        scope.key,
        len(buckets),
    )
    return buckets


def _popcount64(values: np.ndarray) -> np.ndarray:
    """Vectorised 64-bit population count (SWAR), matching ``likeness_utils``."""
    counts = values - ((values >> np.uint64(1)) & np.uint64(0x5555555555555555))
    counts = (counts & np.uint64(0x3333333333333333)) + (
        (counts >> np.uint64(2)) & np.uint64(0x3333333333333333)
    )
    counts = (counts + (counts >> np.uint64(4))) & np.uint64(0x0F0F0F0F0F0F0F0F)
    return (counts * np.uint64(0x0101010101010101)) >> np.uint64(56)


def near_pairs_in_bucket(
    session: Session,
    bucket: NearBucket,
    threshold: float,
    status: Optional[dict[str, Any]] = None,
) -> list[tuple[int, int, float]]:
    """Compare perceptual hashes **within one bucket** and return the near pairs.

    ``similarity = 1 - hamming(dhash_a, dhash_b) / 64`` over the 64-bit dHash
    stored in ``picture.perceptual_hash``. The comparison is a numpy XOR plus a
    SWAR popcount over the bucket's upper triangle, so a 4000-member bucket is
    ~8M popcounts - milliseconds - and the library-wide O(n^2) never happens.

    Returns:
        ``(picture_id_a, picture_id_b, similarity)`` with ``a < b``, similarity
        at or above *threshold*.
    """
    ids = list(bucket.picture_ids)
    if len(ids) < 2:
        return []
    values: list[int] = []
    kept: list[int] = []
    for start in range(0, len(ids), ID_CHUNK):
        chunk = ids[start : start + ID_CHUNK]
        rows = session.exec(
            select(Picture.id, Picture.perceptual_hash).where(
                Picture.id.in_(chunk),
                Picture.deleted.is_(False),
                Picture.perceptual_hash.is_not(None),
            )
        ).all()
        for picture_id, phash in rows:
            text = str(phash or "")
            if len(text) < PHASH_HEX_LEN:
                logger.warning(
                    "[dedup-tier2] picture %s has a %d-char perceptual_hash %r "
                    "(expected %d); excluded from bucket %s=%s",
                    picture_id,
                    len(text),
                    text,
                    PHASH_HEX_LEN,
                    bucket.kind,
                    bucket.key,
                )
                continue
            try:
                values.append(int(text[:PHASH_HEX_LEN], 16))
            except ValueError:
                logger.warning(
                    "[dedup-tier2] picture %s has an unparseable perceptual_hash "
                    "%r; excluded from bucket %s=%s",
                    picture_id,
                    text,
                    bucket.kind,
                    bucket.key,
                )
                continue
            kept.append(int(picture_id))
    if len(kept) < 2:
        return []

    hashes = np.array(values, dtype=np.uint64)
    id_array = np.array(kept, dtype=np.int64)
    max_hamming = int((1.0 - float(threshold)) * PHASH_BITS)
    pairs: list[tuple[int, int, float]] = []
    truncated_at_offset: Optional[int] = None
    for offset in range(1, len(kept)):
        distances = _popcount64(hashes[:-offset] ^ hashes[offset:])
        hits = np.nonzero(distances <= max_hamming)[0]
        if hits.size == 0:
            continue
        for index in hits:
            if len(pairs) >= MAX_PAIRS_PER_BUCKET:
                truncated_at_offset = offset
                break
            left = int(id_array[index])
            right = int(id_array[index + offset])
            similarity = 1.0 - float(distances[index]) / PHASH_BITS
            pairs.append((min(left, right), max(left, right), round(similarity, 6)))
        if truncated_at_offset is not None:
            break
    if truncated_at_offset is not None:
        # Never silent (CLAUDE.md's no-silent-caps rule), and never dishonest.
        # Pairs are emitted in increasing member-offset order, so the cap keeps
        # the *nearest-offset* edges and drops every edge at a wider offset. In a
        # bucket where members are mutually near-identical that is harmless: the
        # offset-1 edges alone form a spanning path (k-1 <= MAX_PAIRS_PER_BUCKET
        # for any bucket, since MAX_BUCKET_MEMBERS is far smaller), so every
        # member still lands in one component and only confidence *resolution*
        # suffers.
        #
        # It is NOT harmless in a dense but non-uniform block. ~700 mutually
        # matching members exhaust the cap inside the low offsets; a member whose
        # only match sits at a wider offset then never gets an edge at all, so it
        # is split into a separate group or drops out of the queue entirely. The
        # cap can therefore lose membership, and this warning says so. The
        # mitigations are to resolve the dense block and rescan (the survivors
        # then fit under the cap), to narrow the bucket, or to raise
        # MAX_PAIRS_PER_BUCKET for the memory it costs.
        logger.warning(
            "[dedup-tier2] bucket %s=%s hit the %d-pair cap at member offset %d "
            "of %d with %d members: no edge at a wider offset was emitted, so a "
            "member whose only match is further away may be split into its own "
            "group or missing from the queue, and reported confidences are "
            "measured over a subset of the edges. Resolve this block and rescan, "
            "narrow the bucket, or raise MAX_PAIRS_PER_BUCKET.",
            bucket.kind,
            bucket.key,
            MAX_PAIRS_PER_BUCKET,
            truncated_at_offset,
            len(kept) - 1,
            len(kept),
        )
    if status is not None:
        status.update(
            {
                "truncated": truncated_at_offset is not None,
                "truncated_at_offset": truncated_at_offset,
                "member_count": len(kept),
                "pair_count": len(pairs),
            }
        )
    return pairs


def groups_from_pairs(
    session: Session,
    pairs: list[tuple[int, int, float]],
    policy: TierPolicy,
    tier: DedupTier = DedupTier.NEAR,
) -> list[DetectedGroup]:
    """Fold near pairs into connected components and assemble them.

    Reuses :class:`~pixlstash.services.dedup_sweep_service._LikenessForest` - the
    shipped union-find that already accumulates per-component min/max similarity,
    so the group's confidence is its **weakest link**, not its strongest.
    """
    if not pairs:
        return []
    forest = dedup_sweep_service._LikenessForest()
    for picture_id_a, picture_id_b, similarity in pairs:
        forest.add_edge(picture_id_a, picture_id_b, similarity)
    components = forest.components(policy.min_group_size)
    return groups_from_components(session, components, policy, tier)


def groups_from_components(
    session: Session,
    components: Iterable[tuple[list[int], float, float]],
    policy: TierPolicy,
    tier: DedupTier = DedupTier.NEAR,
) -> list[DetectedGroup]:
    """Assemble already-folded, plain-Python components into detected groups."""
    components = list(components)
    candidates = load_candidates(
        session, [pid for member_ids, _, _ in components for pid in member_ids]
    )
    groups: list[DetectedGroup] = []
    for member_ids, similarity_min, _similarity_max in components:
        members = [candidates[pid] for pid in member_ids if pid in candidates]
        if len(members) < policy.min_group_size:
            continue
        confidence = float(similarity_min)
        if confidence < policy.threshold:
            continue
        group = assemble_group(tier, confidence, members)
        if len(members) > policy.max_group_size:
            group.evidence.append(
                _pill(f"Unusually large group ({len(members)} pictures)", against=True)
            )
        groups.append(group)
    return groups


def find_near_groups_in_session(
    session: Session,
    policy: TierPolicy,
    scope: Optional[DedupScope] = None,
    buckets: Optional[list[NearBucket]] = None,
) -> list[DetectedGroup]:
    """Tier 2 end to end: build buckets, compare inside them, assemble groups.

    Callers that want the streaming behaviour (groups visible as each bucket
    finishes) drive :func:`build_near_buckets` and :func:`near_pairs_in_bucket`
    themselves from the task system; this convenience wrapper runs the whole
    tier in one call and is what the tests and the synchronous scoped scan use.
    """
    scope = scope or DedupScope()
    buckets = buckets if buckets is not None else build_near_buckets(session, scope)
    pairs: dict[tuple[int, int], float] = {}
    for bucket in buckets:
        for picture_id_a, picture_id_b, similarity in near_pairs_in_bucket(
            session, bucket, policy.threshold
        ):
            key = (picture_id_a, picture_id_b)
            # The same pair can surface in several buckets; keep the strongest
            # observation, which is the same number either way (the hashes do
            # not change between buckets) but makes the merge order-independent.
            if similarity > pairs.get(key, 0.0):
                pairs[key] = similarity
    edges = [(a, b, similarity) for (a, b), similarity in pairs.items()]
    return groups_from_pairs(session, edges, policy, DedupTier.NEAR)


# --- Tier 3: embedding ------------------------------------------------------


def find_embedding_groups_in_session(
    session: Session, policy: TierPolicy, scope: Optional[DedupScope] = None
) -> list[DetectedGroup]:
    """Tier 3. Fold the existing likeness edge table into groups.

    Nothing is recomputed: this reads the ``PictureLikeness`` rows the image
    embedding worker already produced, through the shipped keyset-paginated
    edge stream, so the tier costs one table scan and no GPU time. Opt-in
    because that table is only complete once the embedding worker has caught up.
    """
    scope = scope or DedupScope()
    in_scope: Optional[set[int]] = None
    predicate = scope.picture_predicate()
    if predicate is not None:
        in_scope = {
            int(row)
            for row in session.exec(
                select(Picture.id).where(Picture.deleted.is_(False), predicate)
            ).all()
        }
        if not in_scope:
            return []

    pairs: list[tuple[int, int, float]] = []
    for (
        picture_id_a,
        picture_id_b,
        likeness,
    ) in dedup_sweep_service.stream_likeness_edges(session, policy.threshold):
        if in_scope is not None and (
            picture_id_a not in in_scope or picture_id_b not in in_scope
        ):
            continue
        pairs.append((picture_id_a, picture_id_b, likeness))
    return groups_from_pairs(session, pairs, policy, DedupTier.EMBEDDING)


# --- Persistence ------------------------------------------------------------


def verdict_signatures_in_session(
    session: Session, signatures: Iterable[str]
) -> set[str]:
    """Return the subset of *signatures* that already carry a live verdict.

    A verdict with ``reopened_at`` set is not live: reopening returns the group
    to the queue, which is exactly what "permanent until the user reopens it"
    means.
    """
    wanted = sorted({str(signature) for signature in signatures})
    if not wanted:
        return set()
    found: set[str] = set()
    for start in range(0, len(wanted), ID_CHUNK):
        chunk = wanted[start : start + ID_CHUNK]
        rows = session.exec(
            select(DedupVerdict.signature).where(
                DedupVerdict.signature.in_(chunk),
                DedupVerdict.reopened_at.is_(None),
            )
        ).all()
        found.update(str(row) for row in rows)
    return found


def persist_groups_in_session(
    session: Session,
    groups: list[DetectedGroup],
    scan_id: Optional[int] = None,
) -> int:
    """Upsert detected groups on their signature; return how many are unresolved.

    Upserting rather than inserting is what makes a rescan idempotent: the same
    files produce the same signature, so the row is refreshed in place and the
    queue does not grow duplicates of its own. A group whose signature already
    carries a live verdict is stored ``resolved=True`` and never re-asked.
    """
    if not groups:
        return 0
    resolved_signatures = verdict_signatures_in_session(
        session, (group.signature for group in groups)
    )
    unresolved = 0
    for group in groups:
        existing = session.exec(
            select(DedupGroup).where(DedupGroup.signature == group.signature)
        ).first()
        resolved = group.signature in resolved_signatures
        if existing is None:
            row = DedupGroup(
                signature=group.signature,
                tier=group.tier.value,
                confidence=group.confidence,
                member_count=len(group.members),
                cover_picture_id=group.cover_picture_id,
                evidence=json.dumps(group.evidence),
                resolved=resolved,
                scan_id=scan_id,
            )
            session.add(row)
            session.flush()
        else:
            row = existing
            # Tier precedence: a stronger tier is never downgraded by a weaker
            # one rediscovering the same signature. Exact pairs are perceptually
            # identical too, so a near-enabled rescan finds every one of them
            # again; overwriting unconditionally moved them out of the
            # exact-only default view and out of auto-stack's eligibility.
            # Tier, confidence and evidence describe the *same* finding, so they
            # move together - mixing a stored exact tier with a near
            # confidence would misreport both.
            if tier_strength(group.tier.value) >= tier_strength(row.tier):
                row.tier = group.tier.value
                row.confidence = group.confidence
                row.evidence = json.dumps(group.evidence)
                row.cover_picture_id = group.cover_picture_id
            else:
                logger.debug(
                    "[dedup] keeping tier %s for signature %s; %s rediscovered "
                    "the same group with weaker evidence",
                    row.tier,
                    group.signature,
                    group.tier.value,
                )
            # Membership is pinned by the signature (it hashes the sorted member
            # content keys), so it is refreshed either way: a re-import can give
            # the same content new picture ids.
            row.member_count = len(group.members)
            row.resolved = resolved
            row.scan_id = scan_id
            session.add(row)
            session.exec(
                DedupGroupMember.__table__.delete().where(
                    DedupGroupMember.__table__.c.group_id == row.id
                )
            )
        for position, member in enumerate(group.members):
            session.add(
                DedupGroupMember(
                    group_id=int(row.id), picture_id=member.id, position=position
                )
            )
        if not resolved:
            unresolved += 1
    session.commit()
    return unresolved


def prune_stale_groups_in_session(session: Session) -> int:
    """Drop groups whose members no longer exist or no longer number two.

    Called after any verdict and at the start of a rescan. Without it a group
    whose members were soft-deleted would keep inflating the sidebar badge, and
    the badge is the whole reason the verdict memory exists.
    """
    live_counts = dict(
        session.exec(
            select(DedupGroupMember.group_id, func.count(DedupGroupMember.picture_id))
            .join(Picture, Picture.id == DedupGroupMember.picture_id)
            .where(Picture.deleted.is_(False))
            .group_by(DedupGroupMember.group_id)
        ).all()
    )
    removed = 0
    for row in session.exec(select(DedupGroup)).all():
        if live_counts.get(int(row.id), 0) >= 2:
            continue
        session.delete(row)
        removed += 1
    if removed:
        session.commit()
        logger.info("[dedup] pruned %d stale group(s)", removed)
    return removed


def retire_obsolete_scan_groups_in_session(
    session: Session,
    scan_id: int,
    signatures_by_tier: dict[str, set[str]],
    complete_tiers: Iterable[str],
) -> int:
    """Retire evidence absent from one successfully completed scan generation.

    ``DedupScan`` rows are reused, so a schema generation column would be
    redundant: the running task carries this generation's final signatures and
    reconciles only at successful finalisation. ``DedupGroup.scan_id`` limits
    deletion to evidence last produced by this same scope; a global or sibling
    scoped scan that subsequently refreshed a row owns it and cannot be deleted
    here. Failed/cancelled tasks never call this function, and partial tiers are
    omitted from ``complete_tiers`` so their prior complete evidence survives.

    Deletions are left uncommitted so the caller can commit them atomically with
    the terminal scan status.
    """
    completed = {str(tier) for tier in complete_tiers}
    if not completed:
        return 0
    current = {
        tier: {str(signature) for signature in signatures_by_tier.get(tier, set())}
        for tier in completed
    }
    rows = session.exec(
        select(DedupGroup).where(
            DedupGroup.scan_id == int(scan_id),
            DedupGroup.tier.in_(sorted(completed)),
        )
    ).all()
    removed = 0
    for row in rows:
        if row.signature in current.get(row.tier, set()):
            continue
        session.delete(row)
        removed += 1
    if removed:
        logger.info(
            "[dedup-scan] retiring %d obsolete group(s) from scan %s across "
            "complete tiers %s",
            removed,
            scan_id,
            sorted(completed),
        )
    return removed


# --- Queue reads ------------------------------------------------------------


def _tier_filter(policy: TierPolicy):
    return DedupGroup.tier.in_([tier.value for tier in policy.tiers])


CURSOR_VERSION = "1"
"""Version tag inside the opaque queue cursor, so its encoding can change."""


class DedupCursorError(ValueError):
    """A queue cursor could not be decoded (truncated, edited or foreign)."""


def encode_queue_cursor(confidence: float, group_id: int) -> str:
    """Encode the keyset position of the last delivered row.

    The queue's order is ``(confidence DESC, id ASC)``, so the position after a
    row is exactly that pair. The wire form is base64url (unpadded) over
    ``"1|<confidence>|<group id>"``, with the confidence written at 17
    significant digits so a float round-trips exactly and the ``=`` tie-break
    branch of the keyset predicate is reliable. It is opaque by intent - clients
    must pass it back verbatim, never construct or interpret one.

    Args:
        confidence: The last delivered group's confidence.
        group_id: The last delivered group's row id.

    Returns:
        The cursor to hand back as ``next_cursor``.
    """
    raw = f"{CURSOR_VERSION}|{float(confidence):.17g}|{int(group_id)}"
    return base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii").rstrip("=")


def decode_queue_cursor(cursor: str) -> tuple[float, int]:
    """Decode a cursor produced by :func:`encode_queue_cursor`.

    Raises:
        DedupCursorError: The cursor is not a cursor this server minted. A bad
            cursor is a 400, never a silent restart from the top - silently
            paging from offset 0 would hand the client the same page forever.
    """
    text = str(cursor or "")
    try:
        padded = text + "=" * (-len(text) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        version, confidence, group_id = raw.split("|")
        if version != CURSOR_VERSION:
            raise ValueError(f"unsupported cursor version {version!r}")
        parsed = float(confidence)
        if not math.isfinite(parsed):
            # float() happily parses "inf"/"nan"; a non-finite confidence makes
            # the keyset predicate match everything, which is exactly the
            # silent-restart this function's contract refuses (CSO R6).
            raise ValueError(f"non-finite cursor confidence {confidence!r}")
        return parsed, int(group_id)
    except (ValueError, UnicodeDecodeError, BinasciiError) as exc:
        raise DedupCursorError(f"malformed queue cursor {cursor!r}: {exc}") from exc


def _keyset_predicate(cursor: str):
    """The ``WHERE`` that resumes ``(confidence DESC, id ASC)`` after *cursor*.

    The tie-break half is load-bearing: several groups routinely share a
    confidence (every exact group is 1.0), so ``confidence < c`` alone would skip
    the rest of the tied run and ``confidence <= c`` would repeat it forever.
    """
    confidence, group_id = decode_queue_cursor(cursor)
    return (DedupGroup.confidence < confidence) | (
        (DedupGroup.confidence == confidence) & (DedupGroup.id > group_id)
    )


DECIDED_CURSOR_KIND = "d"
"""Kind marker inside a decided-page cursor.

The decided page orders by its latest relevant activity, not confidence, so its
cursor encodes a different keyset. The marker makes the two cursor families mutually
unreadable: a queue cursor replayed onto the decided page (or vice versa) is a
400, never a silently wrong resume position.
"""


def encode_decided_cursor(activity_at: Optional[datetime], group_id: int) -> str:
    """Encode the keyset position of the last delivered *decided* row.

    The decided page's order is ``(activity_at DESC, id DESC)`` with the
    verdict-less tail last (see :func:`page_queue_in_session`), so the position
    after a row is that pair. ``activity_at`` is written as an exact ISO string
    (microseconds included) so the ``=`` tie-break branch round-trips reliably;
    an empty timestamp field marks a row from the verdict-less tail. Opaque by
    intent, like the queue cursor.
    """
    stamp = activity_at.isoformat() if activity_at is not None else ""
    raw = f"{CURSOR_VERSION}|{DECIDED_CURSOR_KIND}|{stamp}|{int(group_id)}"
    return base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii").rstrip("=")


def decode_decided_cursor(cursor: str) -> tuple[Optional[datetime], int]:
    """Decode a cursor produced by :func:`encode_decided_cursor`.

    Raises:
        DedupCursorError: Not a decided-page cursor this server minted (a queue
            cursor included). Same contract as :func:`decode_queue_cursor`: a
            bad cursor is a 400, never a silent restart from the top.
    """
    text = str(cursor or "")
    try:
        padded = text + "=" * (-len(text) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("ascii")
        version, kind, stamp, group_id = raw.split("|")
        if version != CURSOR_VERSION:
            raise ValueError(f"unsupported cursor version {version!r}")
        if kind != DECIDED_CURSOR_KIND:
            raise ValueError(f"not a decided-page cursor (kind {kind!r})")
        decided_at = datetime.fromisoformat(stamp) if stamp else None
        return decided_at, int(group_id)
    except (ValueError, UnicodeDecodeError, BinasciiError) as exc:
        raise DedupCursorError(f"malformed decided cursor {cursor!r}: {exc}") from exc


def _decided_activity_at():
    """Timestamp used to order a row on the Decided page.

    A stacked verdict follows its live stack's ``updated_at`` so later changes
    to that stack bring its Compare Group back to the top. Keep-separate rows
    have no stack and continue to use the decision stamp. ``COALESCE`` also
    falls back safely if an old stacked verdict references a missing stack.
    """
    return func.coalesce(PictureStack.updated_at, DedupVerdict.decided_at)


def _decided_keyset_predicate(cursor: str):
    """The ``WHERE`` that resumes ``(activity_at DESC, id DESC, NULL tail)``.

    Mirrors :func:`_keyset_predicate` for the decided ordering. A cursor from
    inside the timestamped run resumes at older stamps, the id tie-break within
    an equal stamp, and always admits the verdict-less ``NULL`` tail (which
    sorts after every real stamp); a cursor from inside that tail resumes on id
    alone.
    """
    activity_at, group_id = decode_decided_cursor(cursor)
    activity = _decided_activity_at()
    if activity_at is None:
        return activity.is_(None) & (DedupGroup.id < group_id)
    return (
        (activity < activity_at)
        | ((activity == activity_at) & (DedupGroup.id < group_id))
        | (activity.is_(None))
    )


def _unfrozen(value):
    """*value* for a member no locked set freezes, ``NULL`` for a frozen one.

    Wrapped in ``COUNT`` / ``COUNT(DISTINCT …)``, which skip NULLs, this counts
    only the members that could actually take part in a stack.
    :func:`~pixlstash.services.set_lock_service.locked_picture_id_subquery` is the
    single SQL definition of "frozen" shared with the write guards, so the read
    filters cannot drift away from what a verdict will actually do.
    """
    return case((Picture.id.notin_(locked_picture_id_subquery()), value), else_=None)


def live_groups_filter():
    """Only groups that still POSE a decision.

    Three conditions, one HAVING clause over the live (non-scrapheaped) members:

    * **Two or more of them.** A soft-delete thins its groups the moment it
      lands, but :func:`prune_stale_groups_in_session` only runs on the next
      verdict or scan; without this the badge counts a group with one picture.
    * **Spanning two or more stack units** (``COALESCE(stack_id, -id)`` - every
      unstacked picture is its own unit, stacked pictures share one). Members
      already stacked TOGETHER are a decision the user has made, whether or
      not it was made through this queue: the grid's own stack actions never
      touch ``dedupgroup``, so an exact pair the user stacked by hand stayed
      "unresolved" here and was re-offered forever. Re-offering the answered
      is how the count stops being trusted. A group where a stack would still
      FOLD something in (two stacks, or a stack plus a loner) keeps counting.
    * **Two or more stack units among the members a locked set does NOT
      freeze.** A frozen picture can join neither the stack nor the metadata
      union, so a group left with fewer than two stackable members poses no
      stackable decision at all and is withheld (owner call, 2026-07-30).

    The third condition strictly implies the first two; all three are kept
    because they state three different rules and a future edit to one should not
    silently drop another.

    **The lock rule has to be SQL, not a post-filter.** The queue is paged, so
    dropping rows after the ``LIMIT`` would shrink pages and desynchronise the
    cursor, and the badge would disagree with the list. Expressing it here is
    what makes ``count_unresolved_in_session``, ``count_by_tier_in_session`` and
    ``page_queue_in_session`` apply one identical rule: they all go through this
    filter. :func:`~pixlstash.services.set_lock_service.locked_picture_id_subquery`
    is the shared definition of "frozen", so the read filter and the write guards
    cannot drift.
    """
    stack_unit = func.coalesce(Picture.stack_id, 0 - Picture.id)
    return DedupGroup.id.in_(
        select(DedupGroupMember.group_id)
        .join(Picture, Picture.id == DedupGroupMember.picture_id)
        .where(Picture.deleted.is_(False))
        .group_by(DedupGroupMember.group_id)
        .having(func.count(DedupGroupMember.picture_id) >= 2)
        .having(func.count(func.distinct(stack_unit)) >= 2)
        .having(func.count(func.distinct(_unfrozen(stack_unit))) >= 2)
    )


def _scope_groups_filter(scope: DedupScope):
    """Narrow a ``dedupgroup`` query to the groups touching a scope's pictures.

    ``None`` when the scope is the whole vault, so the caller adds no clause at
    all rather than an always-true one.
    """
    predicate = scope.picture_predicate()
    if predicate is None:
        return None
    return DedupGroup.id.in_(
        select(DedupGroupMember.group_id)
        .join(Picture, Picture.id == DedupGroupMember.picture_id)
        .where(Picture.deleted.is_(False), predicate)
    )


def _live_verdict_join_clause():
    """The join that attaches a decided group's LIVE verdict row.

    ``reopened_at IS NULL`` is what makes it live: a reopened verdict is history,
    and joining it would resurrect a decision the user already cleared.
    ``signature`` is unique on ``dedupverdict``, so this cannot fan a group out
    into several rows.
    """
    return (DedupVerdict.signature == DedupGroup.signature) & (
        DedupVerdict.reopened_at.is_(None)
    )


def count_decided_by_verdict_in_session(
    session: Session,
    scope: Optional[DedupScope] = None,
) -> dict[str, int]:
    """Decided group count per verdict, for the decided page's filter.

    Deliberately ignores the verdict filter itself (and the tier gate, which the
    decided page ignores wholesale), so the menu can show what turning a verdict
    back on would add before the user turns it on - the same contract as
    :func:`count_by_tier_in_session`.

    The counts can sum to less than the decided page's ``total``: a resolved
    group whose live verdict row is missing is a stale edge state that still
    lists (the unfiltered page keeps it, so its "clear decision" way back
    survives) but belongs to no verdict.
    """
    scope = scope or DedupScope()
    query = (
        select(DedupVerdict.verdict, func.count(func.distinct(DedupGroup.id)))
        .select_from(DedupGroup)
        .join(DedupVerdict, _live_verdict_join_clause())
        .where(DedupGroup.resolved.is_(True))
    )
    scope_filter = _scope_groups_filter(scope)
    if scope_filter is not None:
        query = query.where(scope_filter)
    counts = {verdict.value: 0 for verdict in VERDICT_ORDER}
    for verdict, count in session.exec(query.group_by(DedupVerdict.verdict)).all():
        counts[str(verdict)] = int(count)
    return counts


def count_unresolved_in_session(
    session: Session,
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
) -> int:
    """The sidebar badge / context-menu count: unresolved groups in scope.

    Groups, not pictures: the to-do count is the number of decisions left to
    make, which is what the queue actually asks the user for.
    """
    policy = policy or TierPolicy()
    scope = scope or DedupScope()
    query = select(func.count(func.distinct(DedupGroup.id))).where(
        DedupGroup.resolved.is_(False),
        _tier_filter(policy),
        DedupGroup.confidence >= policy.threshold,
        live_groups_filter(),
    )
    predicate = scope.picture_predicate()
    if predicate is not None:
        query = query.where(
            DedupGroup.id.in_(
                select(DedupGroupMember.group_id)
                .join(Picture, Picture.id == DedupGroupMember.picture_id)
                .where(Picture.deleted.is_(False), predicate)
            )
        )
    return int(session.exec(query).one())


def count_by_tier_in_session(
    session: Session,
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
) -> dict[str, int]:
    """Unresolved group count per tier, including the tiers that are switched off.

    The design gives each tier its own live count so the user can see what
    turning a tier on would add before turning it on. That means this ignores
    the policy's tier gating on purpose and reports every tier; only the
    threshold applies, and exact is always counted in full.
    """
    policy = policy or TierPolicy()
    scope = scope or DedupScope()
    query = select(DedupGroup.tier, func.count(DedupGroup.id)).where(
        DedupGroup.resolved.is_(False), live_groups_filter()
    )
    predicate = scope.picture_predicate()
    if predicate is not None:
        query = query.where(
            DedupGroup.id.in_(
                select(DedupGroupMember.group_id)
                .join(Picture, Picture.id == DedupGroupMember.picture_id)
                .where(Picture.deleted.is_(False), predicate)
            )
        )
    # An exact match is always shown regardless of where the threshold sits, so
    # the threshold is applied to the looser tiers only.
    query = query.where(
        (DedupGroup.tier == TIER_EXACT) | (DedupGroup.confidence >= policy.threshold)
    )
    counts = {tier.value: 0 for tier in DedupTier}
    for tier, count in session.exec(query.group_by(DedupGroup.tier)).all():
        counts[str(tier)] = int(count)
    return counts


def page_queue_in_session(
    session: Session,
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Optional[str] = None,
    decided: bool = False,
    verdicts: Optional[Iterable[str]] = None,
) -> tuple[list[dict[str, Any]], int, Optional[str]]:
    """One page of the queue, confidence descending. Never loads the whole list.

    The **decided** page (``decided=True``) instead orders by recent activity
    (id descending on ties): a stacked verdict uses its stack's ``updated_at``;
    other verdicts use ``decided_at``. This makes Compare Group's decided
    sequence follow recently changed stacks while leaving each group's member
    order alone. Its cursor is a separate family encoding that keyset; the two
    cursor kinds reject each other.

    Exactly ``limit`` group rows are read, then one candidate load for that
    page's members. 10 groups and 10,000 cost the same per page, which is the
    design's virtual-queue requirement expressed on the server side.

    **Page with the cursor, not the offset.** The queue is a live list: deciding
    a verdict on a delivered row removes it from ``resolved=False``, and a tier-2
    scan commits new groups after every bucket. Both shift every later row's
    offset, so ``offset=limit`` on the second request skips exactly as many
    groups as the first page's decisions removed - a deterministic, silent skip
    reproduced with a single verdict between two pages. The keyset cursor encodes
    *where the last row was in the ordering* rather than *how many rows to
    discard*, so a row that never moved is never skipped.

    Args:
        session: Pre-opened session.
        policy: Tier gating and threshold; server defaults when omitted.
        scope: Scope to page within; the whole vault when omitted.
        offset: Deprecated rows-to-skip paging. Ignored when *cursor* is given -
            the route rejects the combination before it reaches here.
        limit: Page size, clamped to :data:`MAX_PAGE_SIZE`.
        cursor: Opaque keyset position from a previous page's ``next_cursor``.
        decided: ``False`` (default) pages the open queue; ``True`` pages the
            decided listing, most recently changed first.
        verdicts: **Decided page only.** Restrict the listing to these live
            verdicts (:class:`DedupVerdictKind` values). ``None`` or empty is
            every verdict, which is the whole decided page. Ignored when
            *decided* is false - the open queue's rows carry no verdict by
            definition, and the route rejects the combination before it gets
            here.

    Returns:
        ``(groups, total, next_cursor)``. *total* is the complete count in
        scope **under the same filter as the page**, so the client can size its
        scrollbar without a second request. *next_cursor* is ``None`` once the
        page is not full, which is end-of-found.

        Every group is reported over its **live** members only: a scrapheaped
        picture is absent from ``candidates``, from ``member_count`` and from
        the ``stacks`` depths, and an open-queue group left with fewer than two
        of them is not reported at all.

    Raises:
        DedupCursorError: *cursor* is not a cursor this server minted.
    """
    policy = policy or TierPolicy()
    scope = scope or DedupScope()
    limit = max(1, min(int(limit), MAX_PAGE_SIZE))
    offset = max(0, int(offset))
    # Only the decided page carries verdicts, so a filter is meaningless on the
    # open queue and is dropped here rather than silently emptying it.
    wanted = sorted({str(v) for v in (verdicts or [])}) if decided else []
    # Every verdict selected is no filter at all: the unfiltered page also keeps
    # the verdict-less tail, which an IN clause would drop.
    if len(wanted) >= len(VERDICT_ORDER):
        wanted = []

    # The DECIDED page deliberately ignores the tier gate and the threshold:
    # a decision was made under whatever policy was live at the time, and a
    # later policy change must not hide it from the "clear my decision" list.
    query = select(DedupGroup).where(DedupGroup.resolved.is_(decided))
    if decided:
        # The decided page is "review what I decided", so it orders by the
        # stack's latest change when the verdict created a stack, and otherwise
        # by the decision stamp. The live verdict is outer joined for the ORDER
        # BY: a resolved group whose verdict row is missing or reopened (a stale
        # edge state) sorts into a deterministic tail (SQLite puts NULLs last
        # under DESC) rather than being hidden.
        # ``signature`` is unique on dedupverdict, so the join cannot fan out.
        query = query.join(DedupVerdict, _live_verdict_join_clause(), isouter=True)
        query = query.join(
            PictureStack,
            PictureStack.id == DedupVerdict.stack_id,
            isouter=True,
        )
        if wanted:
            # The IN clause turns the outer join inner for this page: a row with
            # no live verdict matches no verdict the user asked for. It is still
            # reachable with the filter off, which is where the way back lives.
            query = query.where(DedupVerdict.verdict.in_(wanted))
    else:
        # The open queue lists only groups that still pose a decision - same
        # live-membership rule as the counts, so the badge and the list agree.
        # The decided page keeps thinned groups: the verdict already happened,
        # and hiding it would hide the "clear decision" way back.
        query = query.where(
            _tier_filter(policy),
            DedupGroup.confidence >= policy.threshold,
            live_groups_filter(),
        )
    predicate = scope.picture_predicate()
    if predicate is not None:
        query = query.where(
            DedupGroup.id.in_(
                select(DedupGroupMember.group_id)
                .join(Picture, Picture.id == DedupGroupMember.picture_id)
                .where(Picture.deleted.is_(False), predicate)
            )
        )
    if cursor:
        query = query.where(
            _decided_keyset_predicate(cursor) if decided else _keyset_predicate(cursor)
        )
    if decided:
        # Most recent activity first; id DESC keeps a same-instant run (a bulk
        # auto-stack) deterministic so cursor/offset seams cannot skip or repeat
        # a row.
        query = query.order_by(
            _decided_activity_at().desc(),
            DedupGroup.id.desc(),
        ).limit(limit)
    else:
        query = query.order_by(
            DedupGroup.confidence.desc(),
            DedupGroup.id.asc(),
        ).limit(limit)
    if not cursor:
        query = query.offset(offset)
    rows = session.exec(query).all()
    if decided:
        count_query = select(func.count(func.distinct(DedupGroup.id))).select_from(
            DedupGroup
        )
        # The total must be counted under the SAME filter as the page, or the
        # scrollbar is sized for rows the client will never be served.
        if wanted:
            count_query = count_query.join(
                DedupVerdict, _live_verdict_join_clause()
            ).where(DedupVerdict.verdict.in_(wanted))
        count_query = count_query.where(DedupGroup.resolved.is_(True))
        if predicate is not None:
            count_query = count_query.where(
                DedupGroup.id.in_(
                    select(DedupGroupMember.group_id)
                    .join(Picture, Picture.id == DedupGroupMember.picture_id)
                    .where(Picture.deleted.is_(False), predicate)
                )
            )
        total = int(session.exec(count_query).one() or 0)
    else:
        total = count_unresolved_in_session(session, policy, scope)
    if not rows:
        return [], total, None

    # A decided row says WHICH verdict resolved it, so the client can render
    # "Stacked" and "Kept separate" differently and offer the right way back.
    verdict_by_signature: dict[str, DedupVerdict] = {}
    activity_by_signature: dict[str, Optional[datetime]] = {}
    if decided:
        verdict_rows = session.exec(
            select(DedupVerdict, PictureStack.updated_at)
            .join(
                PictureStack,
                PictureStack.id == DedupVerdict.stack_id,
                isouter=True,
            )
            .where(
                DedupVerdict.signature.in_([row.signature for row in rows]),
                DedupVerdict.reopened_at.is_(None),
            )
        ).all()
        for verdict, stack_updated_at in verdict_rows:
            verdict_by_signature[verdict.signature] = verdict
            activity_by_signature[verdict.signature] = (
                stack_updated_at or verdict.decided_at
            )

    # A short page is end-of-found; a full page may or may not be, and handing
    # back a cursor that yields one empty page is cheaper than the extra COUNT
    # that would be needed to know for certain. Each ordering mints its own
    # cursor family; the decided one encodes the last row's activity stamp.
    if len(rows) != limit:
        next_cursor = None
    elif decided:
        next_cursor = encode_decided_cursor(
            activity_by_signature.get(rows[-1].signature),
            int(rows[-1].id),
        )
    else:
        next_cursor = encode_queue_cursor(
            float(rows[-1].confidence or 0.0), int(rows[-1].id)
        )

    group_ids = [int(row.id) for row in rows]
    member_rows = session.exec(
        select(
            DedupGroupMember.group_id,
            DedupGroupMember.picture_id,
            DedupGroupMember.position,
        )
        .where(DedupGroupMember.group_id.in_(group_ids))
        .order_by(DedupGroupMember.group_id, DedupGroupMember.position)
    ).all()
    ids_by_group: dict[int, list[int]] = defaultdict(list)
    for group_id, picture_id, _position in member_rows:
        ids_by_group[int(group_id)].append(int(picture_id))
    all_member_ids = [pid for ids in ids_by_group.values() for pid in ids]
    candidates = load_candidates(session, all_member_ids)
    # Resolved once for the whole page rather than once per group: the lookup is
    # stack-expanded and costs three queries, which would be three per group. It
    # carries its own coverage, so a group whose members are not all in the pool
    # raises rather than being reported as unfrozen.
    lock_lookup = build_locked_set_lookup(session, all_member_ids)
    # The stack truth behind every deck on this page, resolved in one batch for
    # the same reason as the lock lookup: the queue renders an existing stack as
    # one unit whose depth is the STACK's live member count, which routinely
    # exceeds the members of it that are in the group (design D2 / B1). Per group
    # this would be a query per stack per row.
    stack_facts = load_stack_facts(
        session,
        {
            member.stack_id
            for member in candidates.values()
            if member.stack_id is not None
        },
    )

    payload: list[dict[str, Any]] = []
    for row in rows:
        members = [
            candidates[pid]
            for pid in ids_by_group.get(int(row.id), [])
            if pid in candidates
        ]
        if len(members) < 2:
            # Fewer than two LIVE members: the group no longer poses a decision.
            # ``live_groups_filter`` already excludes it from the open queue in
            # SQL, so reaching this on the open queue means a member was
            # scrapheaped between the group read and the candidate load. The row
            # is dropped rather than served thin: a group of one renders as a
            # lone picture with an empty slot beside it, and its verdict buttons
            # offer a stack the server would refuse (owner report, 2026-08-01).
            # ``prune_stale_groups_in_session`` removes the row itself on the
            # next verdict or scan; until then every read filters it out.
            #
            # The DECIDED page keeps it. The verdict already happened, and the
            # "clear this decision" way back has to survive its members going to
            # the Scrapheap: hiding it would strand the decision with no way
            # to reopen it.
            logger.info(
                "[dedup-queue] group %s has %d live member(s); %s",
                row.signature,
                len(members),
                "kept on the decided page" if decided else "dropped from the queue",
            )
            if not decided:
                continue
        cover_id = (
            int(row.cover_picture_id)
            if row.cover_picture_id is not None
            else (members[0].id if members else None)
        )
        # Nothing is filtered out of the listing: a locked-set member is still a
        # real member of the group, and hiding it would make the row disagree
        # with the scan and quietly withdraw the Keep-separate decision the user
        # can still make. It is marked instead, and the cover moves onto the side
        # that can actually be stacked so the client's default is a legal one.
        partition = partition_stackable_members(
            session, [member.id for member in members], lookup=lock_lookup
        )
        stackable_ids = set(partition.stackable)
        if cover_id not in stackable_ids and len(stackable_ids) >= 2:
            cover_id = select_cover(
                [member for member in members if member.id in stackable_ids]
            )
        verdict = verdict_by_signature.get(row.signature)
        payload.append(
            {
                "signature": row.signature,
                "tier": row.tier,
                "confidence": float(row.confidence or 0.0),
                # The LIVE member count, not the stored one. ``dedupgroup``
                # remembers how many members the scan found, and a scrapheaped
                # member is still counted there until the next prune so the
                # stored number described a group the payload does not contain
                # and made the row claim a picture that is in the Scrapheap.
                "member_count": len(members),
                "cover_picture_id": cover_id,
                "why": _refresh_group_size_evidence(
                    json.loads(row.evidence) if row.evidence else [],
                    len(members),
                    policy.max_group_size,
                ),
                "created_at": row.created_at,
                "verdict": verdict.verdict if verdict else None,
                "decided_at": verdict.decided_at if verdict else None,
                "stacks": build_group_stacks(members, partition, stack_facts),
                "candidates": [
                    {
                        **member.as_dict(
                            why=build_candidate_evidence(member, members, cover_id)
                        ),
                        "stackable": member.id in stackable_ids,
                        "blocked_by_sets": partition.sets_for(member.id),
                    }
                    for member in members
                ],
            }
        )
    return payload, total, next_cursor


def stack_members_in_session(
    session: Session,
    stack_id: int,
    offset: int = 0,
    limit: int = DEFAULT_STACK_MEMBER_PAGE_SIZE,
) -> Optional[dict[str, Any]]:
    """One existing stack's members, paged: the deck expansion's own read.

    The lazy half of the design's B1 contract. The queue row ships a deck's
    **count and leader** eagerly, because those are what it draws; the members
    are fetched only when the user opens the expansion strip, and only a page of
    them, so a 40-member stack cannot put 40 tiles behind a queue row that has
    room for none.

    Members come back in the canonical stack order (leader first), with exactly
    the fields a queue candidate carries, so the strip reuses the row's tile
    unchanged.

    The payload's own ``stackable`` / ``blocked_by_sets`` are the **unit-level**
    answer and come from
    :func:`~pixlstash.services.set_lock_service.locked_sets_freezing_stacks`, so
    they are computed from the same member rows (soft-deleted included) as the
    Mixed stacks row and as the guard the three detach routes raise ``423``
    from. Each member's own ``stackable`` / ``blocked_by_sets`` stay the
    per-picture answer, which is a different question with a legitimately
    different answer: a scrapheaped locked-set member freezes its stack against
    being broken up without freezing its live siblings' label data.

    Args:
        session: Pre-opened session.
        stack_id: The stack to expand.
        offset: Members to skip. Plain offset paging is correct here (unlike the
            queue's, §22.7): a stack's membership is not a live list being
            decided out from under the client, and a member added mid-scroll is
            a change the user made.
        limit: Page size, clamped to :data:`MAX_STACK_MEMBER_PAGE_SIZE`.

    Returns:
        The expansion payload, or ``None`` when the stack has no live member at
        all (deleted, dissolved, or never existed): which the route turns into
        a 404 rather than an empty stack that appears to exist.
    """
    stack_id = int(stack_id)
    limit = max(1, min(int(limit), MAX_STACK_MEMBER_PAGE_SIZE))
    offset = max(0, int(offset))
    facts = load_stack_facts(session, [stack_id]).get(stack_id)
    if facts is None:
        return None

    page_ids = list(facts.member_ids[offset : offset + limit])
    candidates = load_candidates(session, page_ids)
    # The unit-level rollup is taken over the WHOLE stack, never over the page:
    # a deck's stackability is a fact about the stack, and reading it off page 1
    # would report a different answer on page 2.
    #
    # It comes from ``locked_sets_freezing_stacks``, the same helper the Mixed
    # stacks row uses and the same member rows ``enforce_stack_detach_not_locked``
    # refuses on, INCLUDING the soft-deleted ones. Rolling it up from
    # ``facts.member_ids`` (live only) instead made this endpoint the odd one
    # out: a stack whose only locked-set member is scrapheaped was reported
    # ``stackable: true`` here while the row said false and the writes answered
    # 423. A read that promises an action the server refuses is worse than no
    # prediction at all, so all three now read the same rows.
    unit_blocking = locked_sets_freezing_stacks(session, [stack_id]).get(stack_id, [])
    # Per-MEMBER values stay the per-picture answer, which is a different
    # question and legitimately a different answer: a scrapheaped locked-set
    # member projects no freeze onto its live siblings, so every member on this
    # page can be ``stackable: true`` under a ``stackable: false`` unit. The
    # frozen row is simply not on the page, because it is in the Scrapheap.
    lookup = build_locked_set_lookup(session, facts.member_ids)

    members: list[dict[str, Any]] = []
    for position, picture_id in enumerate(page_ids, start=offset):
        member = candidates.get(picture_id)
        if member is None:
            # The picture was soft-deleted between the two reads. Skipping it is
            # correct (it is no longer a member), but never silent: the page is
            # then shorter than `limit` without being the end of the stack.
            logger.warning(
                "[dedup-stack] picture %s vanished from stack %s between the "
                "membership read and the candidate load; it is omitted from "
                "this page, which is therefore shorter than the requested %d",
                picture_id,
                stack_id,
                limit,
            )
            continue
        sets = lookup.sets_for(picture_id)
        members.append(
            {
                **member.as_dict(),
                "position": position,
                "is_leader": picture_id == facts.leader_picture_id,
                "stackable": not sets,
                "blocked_by_sets": sets,
            }
        )
    next_offset = offset + limit
    return {
        "stack_id": stack_id,
        "member_count": facts.member_count,
        "leader_picture_id": facts.leader_picture_id,
        "leader_thumbnail_version": facts.leader_thumbnail_version,
        "stackable": not unit_blocking,
        "blocked_by_sets": [dict(entry) for entry in unit_blocking],
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset if next_offset < facts.member_count else None,
        "members": members,
    }


def scope_counts_in_session(
    session: Session,
    scopes: list[DedupScope],
    policy: Optional[TierPolicy] = None,
) -> list[dict[str, Any]]:
    """Unresolved counts for several scopes in one request.

    The context menus need a count per project / set / character / folder; asking
    for them one at a time would be a request per menu item.
    """
    policy = policy or TierPolicy()
    return [
        {
            **scope.as_dict(),
            "unresolved_groups": count_unresolved_in_session(session, policy, scope),
        }
        for scope in scopes
    ]


# --- Scan requests and progress ---------------------------------------------


def request_scan_in_session(
    session: Session,
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
) -> dict[str, Any]:
    """Queue a scan for *scope* and return its progress row immediately.

    The route returns as soon as this row exists, which is what makes the
    context-menu "Find duplicates in ..." entry feel instant: the hashes are
    already cached (``pixel_sha`` and ``perceptual_hash`` are computed on import),
    so the scan only has to read and compare them, and the queue can be opened
    while it does.

    One row per scope key, reused across rescans, so a scope has exactly one
    place to read progress from.
    """
    policy = policy or TierPolicy()
    scope = scope or DedupScope()
    row = session.exec(
        select(DedupScan).where(DedupScan.scope_key == scope.key)
    ).first()
    requested_tiers = [tier.value for tier in policy.tiers]
    if row is not None and row.status in (SCAN_PENDING, SCAN_RUNNING):
        active = scan_progress(row)
        # Scan rows durably define policy as scope + enabled tiers + threshold.
        # min/max group size predate request persistence and are intentionally
        # outside active-request equivalence until the schema stores them.
        if active["tiers"] == requested_tiers and math.isclose(
            float(active["threshold"]),
            float(policy.threshold),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            logger.info(
                "[dedup-scan] coalesced equivalent active request for "
                "scope=%s scan_id=%s",
                scope.key,
                row.id,
            )
            return active
        logger.info(
            "[dedup-scan] refused policy change for active scope=%s scan_id=%s "
            "active_tiers=%s active_threshold=%.4f requested_tiers=%s "
            "requested_threshold=%.4f",
            scope.key,
            row.id,
            active["tiers"],
            active["threshold"],
            requested_tiers,
            policy.threshold,
        )
        raise DedupScanBusyError(active)
    now = datetime.utcnow()
    if row is None:
        row = DedupScan(scope_key=scope.key)
    row.scope_type = scope.scope_type.value
    row.scope_id = scope.scope_id
    row.tiers = json.dumps(requested_tiers)
    row.threshold = float(policy.threshold)
    row.status = SCAN_PENDING
    row.error = None
    row.started_at = now
    row.updated_at = now
    row.finished_at = None
    session.add(row)
    session.commit()
    session.refresh(row)
    logger.info(
        "[dedup-scan] requested scan for scope=%s tiers=%s threshold=%.4f",
        scope.key,
        row.tiers,
        policy.threshold,
    )
    return scan_progress(row)


def scan_progress(row: Optional[DedupScan]) -> dict[str, Any]:
    """Serialise a scan row for the "scanned N of M" banner.

    ``None`` (no scan has ever run for this scope) is reported as an idle scan
    rather than as an error: the queue is still perfectly usable, it just shows
    whatever an earlier global scan found.
    """
    if row is None:
        return {
            "status": "idle",
            "scanned_pictures": 0,
            "total_pictures": 0,
            "scanned_buckets": 0,
            "total_buckets": 0,
            "groups_found": 0,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
    return {
        "scan_id": row.id,
        "scope_key": row.scope_key,
        "status": row.status,
        "tiers": json.loads(row.tiers or "[]"),
        "threshold": float(row.threshold or DEFAULT_THRESHOLD),
        "scanned_pictures": int(row.scanned_pictures or 0),
        "total_pictures": int(row.total_pictures or 0),
        "scanned_buckets": int(row.scanned_buckets or 0),
        "total_buckets": int(row.total_buckets or 0),
        "groups_found": int(row.groups_found or 0),
        "started_at": row.started_at,
        "updated_at": row.updated_at,
        "finished_at": row.finished_at,
        "error": row.error,
    }


def scan_progress_in_session(
    session: Session, scope: Optional[DedupScope] = None
) -> dict[str, Any]:
    """Current scan progress for *scope*."""
    scope = scope or DedupScope()
    row = session.exec(
        select(DedupScan).where(DedupScan.scope_key == scope.key)
    ).first()
    return scan_progress(row)


def run_scan_now_in_session(
    session: Session,
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
) -> dict[str, Any]:
    """Run every enabled tier synchronously and persist the groups.

    The background path is :class:`~pixlstash.tasks.dedup_scan_task.DedupScanTask`;
    this is the same work without the task system, for tests and for a caller
    that genuinely wants to block (a small scope where the round trip is cheaper
    than polling).
    """
    policy = policy or TierPolicy()
    scope = scope or DedupScope()
    prune_stale_groups_in_session(session)
    found = 0
    for tier in iter_tiers(policy):
        if tier is DedupTier.EXACT:
            groups = find_exact_groups_in_session(session, scope)
        elif tier is DedupTier.NEAR:
            groups = find_near_groups_in_session(session, policy, scope)
        else:
            groups = find_embedding_groups_in_session(session, policy, scope)
        found += persist_groups_in_session(session, groups)
    return {
        "scope": scope.as_dict(),
        "policy": policy.as_dict(),
        "unresolved_groups": found,
    }


# --- Vault wrappers ---------------------------------------------------------


def count_unresolved(
    vault: "Vault",
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
) -> int:
    """Read-only vault wrapper around :func:`count_unresolved_in_session`."""
    return vault.db.run_immediate_read_task(count_unresolved_in_session, policy, scope)


def page_queue(
    vault: "Vault",
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Optional[str] = None,
) -> tuple[list[dict[str, Any]], int, Optional[str]]:
    """Read-only vault wrapper around :func:`page_queue_in_session`."""
    return vault.db.run_immediate_read_task(
        page_queue_in_session, policy, scope, offset, limit, cursor
    )


def stack_members(
    vault: "Vault",
    stack_id: int,
    offset: int = 0,
    limit: int = DEFAULT_STACK_MEMBER_PAGE_SIZE,
) -> Optional[dict[str, Any]]:
    """Read-only vault wrapper around :func:`stack_members_in_session`."""
    return vault.db.run_immediate_read_task(
        stack_members_in_session, stack_id, offset, limit
    )


def _queue_response_in_session(
    session: Session,
    policy: TierPolicy,
    scope: DedupScope,
    offset: int,
    limit: int,
    cursor: Optional[str] = None,
    decided: bool = False,
    verdicts: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """The whole ``GET /dedup/groups`` payload, on one session.

    Assembled here rather than in the route so the page and the scan progress it
    is captioned with come from the same read, and so the route never touches
    ``vault.db`` (§10.1).
    """
    wanted = list(verdicts or [])
    groups, total, next_cursor = page_queue_in_session(
        session, policy, scope, offset, limit, cursor, decided, wanted
    )
    return {
        "groups": groups,
        "total": total,
        "offset": offset,
        "limit": min(int(limit), MAX_PAGE_SIZE),
        "cursor": cursor,
        "next_cursor": next_cursor,
        "policy": policy.as_dict(),
        "scope": scope.as_dict(),
        # The decided page's own filter counts, read from the same session as
        # the page so the menu's rows and the list can never disagree. Empty on
        # the open queue, whose rows carry no verdict.
        "by_verdict": (
            count_decided_by_verdict_in_session(session, scope) if decided else {}
        ),
        "verdicts": sorted({str(v) for v in wanted}) if decided else [],
        "scan": scan_progress_in_session(session, scope),
    }


def queue_response(
    vault: "Vault",
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    cursor: Optional[str] = None,
    decided: bool = False,
    verdicts: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Read-only vault wrapper producing the full queue-page response."""
    return vault.db.run_immediate_read_task(
        _queue_response_in_session,
        policy or TierPolicy(),
        scope or DedupScope(),
        offset,
        limit,
        cursor,
        decided,
        list(verdicts or []),
    )


def _counts_response_in_session(
    session: Session, policy: TierPolicy, scopes: list[DedupScope]
) -> dict[str, Any]:
    """The whole ``POST /dedup/counts`` payload, on one session."""
    return {
        "unresolved_groups": count_unresolved_in_session(session, policy),
        "by_tier": count_by_tier_in_session(session, policy),
        "scopes": scope_counts_in_session(session, scopes, policy),
        "policy": policy.as_dict(),
        "scan": scan_progress_in_session(session),
    }


def counts_response(
    vault: "Vault",
    policy: Optional[TierPolicy] = None,
    scopes: Optional[list[DedupScope]] = None,
) -> dict[str, Any]:
    """Read-only vault wrapper producing the live-counts response."""
    return vault.db.run_immediate_read_task(
        _counts_response_in_session, policy or TierPolicy(), list(scopes or [])
    )


def request_scan(
    vault: "Vault",
    policy: Optional[TierPolicy] = None,
    scope: Optional[DedupScope] = None,
) -> dict[str, Any]:
    """Queue a scan and wake the work planner so it starts now.

    Write-path vault wrapper: the row is the request, and the planner turns it
    into a :class:`~pixlstash.tasks.dedup_scan_task.DedupScanTask`.
    """
    progress = vault.db.run_task(
        request_scan_in_session,
        policy or TierPolicy(),
        scope or DedupScope(),
    )
    vault.wake()
    return progress


def iter_tiers(policy: TierPolicy) -> Iterator[DedupTier]:
    """Yield the enabled tiers strongest first (exact, near, embedding)."""
    for tier in TIER_ORDER:
        if policy.includes(tier):
            yield tier


__all__ = [
    "COVER_PIXEL_WEIGHT",
    "COVER_RAW_BONUS",
    "COVER_SCORE_WEIGHT",
    "COVER_TAG_WEIGHT",
    "CURSOR_VERSION",
    "DEFAULT_MAX_GROUP_SIZE",
    "DEFAULT_MIN_GROUP_SIZE",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_STACK_MEMBER_PAGE_SIZE",
    "DEFAULT_THRESHOLD",
    "MAX_BUCKET_MEMBERS",
    "MAX_PAGE_SIZE",
    "MAX_STACK_MEMBER_PAGE_SIZE",
    "MAX_THRESHOLD",
    "MIN_THRESHOLD",
    "RAW_FORMATS",
    "STACK_POSITION_LAST",
    "TIER_ORDER",
    "TIER_STRENGTH",
    "VERDICT_KEEP_SEPARATE",
    "VERDICT_ORDER",
    "VERDICT_STACKED",
    "CandidateMember",
    "DedupCursorError",
    "DedupScanBusyError",
    "DedupScope",
    "DedupTier",
    "DedupVerdictKind",
    "DetectedGroup",
    "NearBucket",
    "ScopeType",
    "StackFacts",
    "TierPolicy",
    "assemble_group",
    "build_candidate_evidence",
    "build_group_evidence",
    "build_group_stacks",
    "build_near_buckets",
    "count_by_tier_in_session",
    "count_decided_by_verdict_in_session",
    "count_unresolved",
    "count_unresolved_in_session",
    "counts_response",
    "cover_order_key",
    "decode_queue_cursor",
    "encode_queue_cursor",
    "find_embedding_groups_in_session",
    "find_exact_groups_in_session",
    "find_near_groups_in_session",
    "group_signature",
    "groups_from_pairs",
    "iter_tiers",
    "load_candidates",
    "load_stack_facts",
    "near_pairs_in_bucket",
    "page_queue",
    "page_queue_in_session",
    "persist_groups_in_session",
    "prune_stale_groups_in_session",
    "queue_response",
    "request_scan",
    "request_scan_in_session",
    "retire_obsolete_scan_groups_in_session",
    "run_scan_now_in_session",
    "scan_progress",
    "scan_progress_in_session",
    "scope_counts_in_session",
    "select_cover",
    "stack_members",
    "stack_members_in_session",
    "tier_strength",
    "verdict_signatures_in_session",
]
