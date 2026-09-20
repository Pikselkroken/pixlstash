"""Vault-side reads over the workflow keys ``picture`` carries.

The rows these hashes name live in the hub and are content-addressed, so nothing
here joins across the database boundary: a hash the attached hub has never heard
of is a workflow this machine does not have, which the library view reports as
unknown rather than treating as an error.

**Soft-deleted pictures are excluded, and that is the point of the module
existing rather than the query being inlined at each call site.** A workflow
whose every picture sits in the Scrapheap must read as "none kept"; counting the
scrapheap in would make it read as live.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import and_, case, func, nullslast, or_
from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.services.saved_recipe_service import counts_by_workflow_key
from pixlstash.stacking import get_or_create_stack_for_picture


@dataclass(frozen=True)
class WorkflowActivity:
    """What a vault knows about one workflow key: how much, and how recently."""

    pictures: int
    last_used: Optional[datetime]


@dataclass(frozen=True)
class ScanProgress:
    """How far the ComfyUI extraction pass has read.

    The Workflows view has to say which of three states an empty list is in --
    not looked yet, looking, or looked and there is genuinely nothing -- and it
    cannot tell them apart from the list alone. ``scanned`` counts pictures
    carrying a ``workflow_hash_version``, which the extraction task writes for
    every picture it reads whether or not that picture held a workflow.
    """

    pictures: int
    scanned: int


# When a workflow was last used. ``created_at`` is the picture's own date and is
# nullable -- a PNG that carried no date has none -- so a bare ``max`` over it
# reads as "never" for a workflow whose every picture came in undated, which is
# a real state in the owner's libraries and not the one the column means. Falling
# back to ``imported_at`` answers with the best date the vault actually has.
_USED_AT = func.coalesce(Picture.created_at, Picture.imported_at)


def _activity(session: Session, column) -> dict[str, WorkflowActivity]:
    """Group kept pictures by one workflow hash column.

    Returns:
        ``{hash: WorkflowActivity}``, with keys whose pictures are all
        soft-deleted absent entirely rather than present with a zero.
    """
    rows = session.exec(
        select(column, func.count(Picture.id), func.max(_USED_AT))
        .where(column.is_not(None))
        .where(Picture.deleted.is_(False))
        .group_by(column)
    ).all()
    return {
        key: WorkflowActivity(pictures=count, last_used=last_used)
        for key, count, last_used in rows
    }


def topology_activity(session: Session) -> dict[str, WorkflowActivity]:
    """What each topology accounts for, **vault-wide**.

    Served by ``ix_picture_workflow_topology_hash``.

    **These counts are unscoped and must not be returned to a scoped token as
    they stand.** They read every non-deleted picture in the vault, so a route
    exposing them to a picture-, set- or project-scoped token would disclose the
    size of the whole library -- the deny-by-default rule in
    ``docs/backend_architecture.md`` §16 exists because that class of omission
    has recurred here. ``GET /workflows`` is declared ``OWNER_ONLY`` for exactly
    this reason. A caller that needs a scoped answer adds the narrowing
    parameter then, against a real policy.
    """
    return _activity(session, Picture.workflow_topology_hash)


def recipe_activity(
    session: Session, structural_hashes: list[str]
) -> dict[str, WorkflowActivity]:
    """The same figures per recipe, for the variants under one topology.

    Narrowed by hash rather than grouped vault-wide: the caller already knows
    which recipes it is expanding, and a topology holding 159 of them is still
    one ``IN`` over an indexed column.
    """
    if not structural_hashes:
        return {}
    column = Picture.workflow_structural_hash
    rows = session.exec(
        select(column, func.count(Picture.id), func.max(_USED_AT))
        .where(column.in_(structural_hashes))
        .where(Picture.deleted.is_(False))
        .group_by(column)
    ).all()
    return {
        key: WorkflowActivity(pictures=count, last_used=last_used)
        for key, count, last_used in rows
    }


def recipe_picture_counts(session: Session) -> dict[str, int]:
    """How many kept pictures each recipe made, **vault-wide**.

    Unscoped, like :func:`topology_activity`, and served only to owner routes
    for the same reason.
    """
    return {
        key: activity.pictures
        for key, activity in _activity(
            session, Picture.workflow_structural_hash
        ).items()
    }


def scan_progress(session: Session) -> ScanProgress:
    """How many kept pictures exist, and how many have been read for a workflow."""
    pictures, scanned = session.exec(
        select(
            func.count(Picture.id),
            # A picture with keys has been read, even while migration 0118's
            # backfill has cleared its marker to record how it was made.
            func.count(
                case(
                    (
                        or_(
                            Picture.workflow_hash_version.is_not(None),
                            Picture.workflow_instance_hash.is_not(None),
                        ),
                        1,
                    )
                )
            ),
        ).where(Picture.deleted.is_(False))
    ).one()
    return ScanProgress(pictures=pictures or 0, scanned=scanned or 0)


# ---------------------------------------------------------------------------
# The vault-level entry points the routes call.
#
# The session-level functions above stay public because they are the ones whose
# contract is worth reading (and testing) on its own; these are the thin layer
# that owns the ``vault.db`` call, so no route file has to. That split is what
# ``tests/test_architecture_guardrails.py::test_no_new_direct_db_calls_from_routes``
# asks for, and it is why the list's two reads share one session rather than
# taking two.
# ---------------------------------------------------------------------------


def read_recipe_activity(
    vault, structural_hashes: list[str]
) -> dict[str, WorkflowActivity]:
    """The expansion's vault side: figures for one topology's recipes."""
    return vault.db.run_immediate_read_task(recipe_activity, structural_hashes)


def read_variant_picture_counts(vault) -> dict[str, int]:
    """``{structural_hash: kept pictures}``, vault-wide.

    What a slot-mark flip decides its merge winner on (v1.12 B4): the flip is
    a hub write, the counts belong to whichever vault is attached, and the two
    are joined by the hub on the variants it is re-keying.
    """
    return vault.db.run_immediate_read_task(recipe_picture_counts)


# ---------------------------------------------------------------------------
# The card grid's vault side (v1.12 B3).
#
# Everything below is grouped by ``workflow_structural_hash`` - the VARIANT -
# and never by card, because the vault does not know what a card is: the
# variant-to-card map lives in the hub and the two are joined in memory
# (``services/workflow_card_service``). That is what keeps this at two queries
# for the whole grid, and what lets a card gain a variant without the vault
# learning anything new.
# ---------------------------------------------------------------------------

# Rated means a star the owner actually put there. ``score`` is NULL for a
# picture nobody has rated and 0 for one explicitly cleared, and neither is a
# rating: counting either would drag every card's Bayesian mean towards zero in
# proportion to how much of the library is simply unrated.
_IS_RATED = and_(Picture.score.is_not(None), Picture.score > 0)


@dataclass(frozen=True)
class VariantActivity:
    """What a vault knows about one variant, as the card rank is made of it.

    ``score_total`` is Σ of the ratings that exist, over ``rated`` pictures, so
    the two divide into a mean the prior can be mixed into. ``pictures`` counts
    every kept picture whether rated or not, because that is the card's size
    and the rank's tie-break.
    """

    pictures: int
    rated: int
    score_total: int
    last_used: Optional[datetime]


@dataclass(frozen=True)
class CoverCandidate:
    """One picture in the running to be a card's cover, with its sort keys.

    The keys travel with the row because the top three of a CARD are picked in
    memory out of the top three of each of its variants, and that second pick
    has to order by exactly what the window ordered by -- including how each
    key treats NULL, which is the part that is easy to get subtly wrong.

    The three bitmap fields are here for the cache-buster in the thumbnail URL
    (``ImageUtils.thumbnail_cache_version``), so the covers can be served as
    URLs without a second read per card.

    The three ``square_crop_*`` fields are the stored face-weighted rectangle
    within that bitmap (``FaceUtils.square_crop_rect``), so a client can crop a
    cover around the face rather than to a blind top anchor (#1465). They are
    NULL while a picture is still being processed, which is the client's cue to
    fall back to the shipped ``object-fit: cover`` framing.
    """

    structural_hash: str
    picture_id: int
    score: Optional[int]
    smart_score: Optional[float]
    used_at: Optional[datetime]
    thumbnail_width: Optional[int] = None
    thumbnail_height: Optional[int] = None
    orientation: Optional[int] = None
    square_crop_x: Optional[int] = None
    square_crop_y: Optional[int] = None
    square_crop_side: Optional[int] = None


def variant_activity(session: Session) -> dict[str, VariantActivity]:
    """One ``GROUP BY workflow_structural_hash`` over every kept picture.

    Served by ``ix_picture_workflow_structural_hash``. **Unscoped, like
    :func:`topology_activity`**, and served only to owner routes for the same
    reason: it reads every non-deleted picture in the vault, so a scoped token
    holding the result would learn the size of the whole library one workflow
    at a time.
    """
    column = Picture.workflow_structural_hash
    rows = session.exec(
        select(
            column,
            func.count(Picture.id),
            func.sum(case((_IS_RATED, 1), else_=0)),
            func.sum(case((_IS_RATED, Picture.score), else_=0)),
            func.max(_USED_AT),
        )
        .where(column.is_not(None))
        .where(Picture.deleted.is_(False))
        .group_by(column)
    ).all()
    return {
        key: VariantActivity(
            pictures=pictures,
            rated=int(rated or 0),
            score_total=int(score_total or 0),
            last_used=last_used,
        )
        for key, pictures, rated, score_total, last_used in rows
    }


def variant_cover_candidates(
    session: Session, per_variant: int
) -> list[CoverCandidate]:
    """The best few kept pictures of every variant, from one window pass.

    ``ROW_NUMBER() OVER (PARTITION BY workflow_structural_hash ...)`` rather
    than a query per card: the top *n* of a card is always a subset of the
    union of the top *n* of its variants, so one pass over the picture table
    answers the whole grid and the per-card pick is a sort of a handful of rows.
    """
    ordering = (
        nullslast(Picture.score.desc()),
        nullslast(Picture.smart_score.desc()),
        nullslast(_USED_AT.desc()),
        Picture.id.desc(),
    )
    ranked = (
        select(
            Picture.workflow_structural_hash.label("structural_hash"),
            Picture.id.label("picture_id"),
            Picture.score.label("score"),
            Picture.smart_score.label("smart_score"),
            _USED_AT.label("used_at"),
            Picture.thumbnail_width.label("thumbnail_width"),
            Picture.thumbnail_height.label("thumbnail_height"),
            Picture.orientation.label("orientation"),
            Picture.square_crop_x.label("square_crop_x"),
            Picture.square_crop_y.label("square_crop_y"),
            Picture.square_crop_side.label("square_crop_side"),
            func.row_number()
            .over(partition_by=Picture.workflow_structural_hash, order_by=ordering)
            .label("rank"),
        )
        .where(Picture.workflow_structural_hash.is_not(None))
        .where(Picture.deleted.is_(False))
        .subquery()
    )
    rows = session.exec(
        select(
            ranked.c.structural_hash,
            ranked.c.picture_id,
            ranked.c.score,
            ranked.c.smart_score,
            ranked.c.used_at,
            ranked.c.thumbnail_width,
            ranked.c.thumbnail_height,
            ranked.c.orientation,
            ranked.c.square_crop_x,
            ranked.c.square_crop_y,
            ranked.c.square_crop_side,
        ).where(ranked.c.rank <= per_variant)
    ).all()
    return [CoverCandidate(*row) for row in rows]


def variant_picture_ids(
    session: Session, structural_hashes: list[str], limit: int
) -> list[int]:
    """The newest kept pictures of one card, newest first.

    Narrowed by the card's variants rather than grouped vault-wide, for the
    reason :func:`recipe_activity` is: the caller already knows which variants
    it is opening.
    """
    if not structural_hashes:
        return []
    return list(
        session.exec(
            select(Picture.id)
            .where(Picture.workflow_structural_hash.in_(structural_hashes))
            .where(Picture.deleted.is_(False))
            .order_by(nullslast(_USED_AT.desc()), Picture.id.desc())
            .limit(limit)
        ).all()
    )


def cover_pictures_by_pixel_sha(
    session: Session, pixel_shas: list[str]
) -> dict[str, CoverCandidate]:
    """Resolve the owner's chosen covers, which are stored by content.

    A ``pixel_sha`` with no kept picture behind it is simply absent: the cover
    was destroyed or binned, and the card falls back to its computed one rather
    than showing a hole. The rows come back as :class:`CoverCandidate` with no
    ``structural_hash`` -- a chosen cover belongs to the card, not to one of its
    variants -- so the caller can build its URL the same way as any other.
    """
    if not pixel_shas:
        return {}
    rows = session.exec(
        select(
            Picture.pixel_sha,
            Picture.id,
            Picture.score,
            Picture.smart_score,
            _USED_AT,
            Picture.thumbnail_width,
            Picture.thumbnail_height,
            Picture.orientation,
            Picture.square_crop_x,
            Picture.square_crop_y,
            Picture.square_crop_side,
        )
        .where(Picture.pixel_sha.in_(pixel_shas))
        .where(Picture.deleted.is_(False))
        .order_by(Picture.id)
    ).all()
    return {row[0]: CoverCandidate("", *row[1:]) for row in rows}


def instance_hashes_for_variants(
    session: Session,
    structural_hashes: list[str],
    minimum_score: Optional[int],
    limit: int,
) -> list[str]:
    """The most recent distinct instance hashes of a card's kept pictures.

    **Capped, and the cap is the point of the argument existing.** An instance
    keys on every parameter value, the seed included, so a card the owner has
    run forty thousand times has forty thousand of these -- and the caller
    reads and parses one stored graph per hash. The mode of a parameter over
    the newest few hundred runs is the same answer as the mode over all of
    them, for a bounded read.

    Args:
        structural_hashes: The card's variants.
        minimum_score: Keep only pictures rated at least this, or ``None`` for
            every kept picture. The defaults read is "the best pictures, and
            failing that all of them", which is this function twice.
        limit: How many distinct hashes to take, newest first.
    """
    if not structural_hashes:
        return []
    query = (
        select(Picture.workflow_instance_hash, func.max(_USED_AT).label("used_at"))
        .where(Picture.workflow_structural_hash.in_(structural_hashes))
        .where(Picture.workflow_instance_hash.is_not(None))
        .where(Picture.deleted.is_(False))
        .group_by(Picture.workflow_instance_hash)
        .order_by(nullslast(func.max(_USED_AT).desc()))
        .limit(limit)
    )
    if minimum_score is not None:
        query = query.where(Picture.score.is_not(None)).where(
            Picture.score >= minimum_score
        )
    return [instance_hash for instance_hash, _ in session.exec(query).all()]


def read_card_grid(
    vault, cover_depth: int
) -> tuple[dict[str, VariantActivity], list[CoverCandidate], dict[str, int]]:
    """The grid's whole vault side in one session.

    Three statements, one task: the per-variant counts, the cover candidates
    and how many saved recipes each card holds. They share a session because
    the round trip is the expensive part, not the third `GROUP BY`.
    """

    def _read(session: Session):
        return (
            variant_activity(session),
            variant_cover_candidates(session, cover_depth),
            counts_by_workflow_key(session),
        )

    return vault.db.run_immediate_read_task(_read)


def read_chosen_covers(vault, pixel_shas: list[str]) -> dict[str, CoverCandidate]:
    """The pictures the owner picked as covers, by content."""
    return vault.db.run_immediate_read_task(cover_pictures_by_pixel_sha, pixel_shas)


def read_card_picture_ids(vault, structural_hashes: list[str], limit: int) -> list[int]:
    """The card's newest kept pictures."""
    return vault.db.run_immediate_read_task(
        variant_picture_ids, structural_hashes, limit
    )


def read_instance_hashes(
    vault, structural_hashes: list[str], minimum_score: Optional[int], limit: int
) -> list[str]:
    """The instances a card's pictures ran, optionally only its best."""
    return vault.db.run_immediate_read_task(
        instance_hashes_for_variants, structural_hashes, minimum_score, limit
    )


def best_picture_ids(
    session: Session, structural_hashes: list[str], limit: int
) -> list[int]:
    """The card's best kept pictures, best first.

    The same order :func:`variant_cover_candidates` ranks by - rating, then the
    smart score, then how recently it was used - narrowed to one card's
    variants rather than run as a window pass over the whole table. "Best" is
    what the run route wants: the picture whose embedded graph is most worth
    re-running is the one the owner liked, not the one that happens to be
    newest.
    """
    if not structural_hashes:
        return []
    return list(
        session.exec(
            select(Picture.id)
            .where(Picture.workflow_structural_hash.in_(structural_hashes))
            .where(Picture.deleted.is_(False))
            .order_by(
                nullslast(Picture.score.desc()),
                nullslast(Picture.smart_score.desc()),
                nullslast(_USED_AT.desc()),
                Picture.id.desc(),
            )
            .limit(limit)
        ).all()
    )


def read_best_picture_ids(vault, structural_hashes: list[str], limit: int) -> list[int]:
    """The card's best kept pictures, in its own read task."""
    return vault.db.run_immediate_read_task(best_picture_ids, structural_hashes, limit)


def kept_pixel_shas(session: Session, pixel_shas: list[str]) -> set[str]:
    """Which of these contents a kept picture still holds.

    The read behind ``fixed_input_deleted``: a card's fixed picture input names
    its picture by ``pixel_sha`` rather than by id, so the question "is it still
    here" is a content lookup and not a row fetch.
    """
    if not pixel_shas:
        return set()
    # `set(...)` and NOT a `for (sha,) in ...` unpack: a one-column `select()`
    # is a `SelectOfScalar`, so `exec().all()` hands back the values themselves
    # and unpacking one raises. Every other single-column read in this module
    # reads it this way.
    return set(
        session.exec(
            select(Picture.pixel_sha)
            .where(Picture.pixel_sha.in_(pixel_shas))
            .where(Picture.deleted.is_(False))
        ).all()
    )


def read_kept_pixel_shas(vault, pixel_shas: list[str]) -> set[str]:
    """Which of these contents a kept picture still holds, in its own task."""
    return vault.db.run_immediate_read_task(kept_pixel_shas, pixel_shas)


def stack_for_picture(vault, picture_id: int) -> Optional[int]:
    """The stack this picture is in, created if it has none.

    A write, and here rather than in ``pixlstash/stacking.py`` so a route never
    reaches for ``vault.db`` itself (the guardrail in
    ``tests/test_architecture_guardrails.py``).
    """
    return vault.db.run_task(get_or_create_stack_for_picture, picture_id)
