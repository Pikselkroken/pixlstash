"""Picture ghosts: what a permanently destroyed picture is allowed to leave behind.

A **ghost is a thumbnail and a prompt, always together** (library plan §5). It is
written at the moment a picture is destroyed, and it lives in the hub because
that is where a workflow outlives the pictures it came from. This module owns the
one decision the store deliberately does not make: **whether a ghost may exist at
all**, which is the user's, expressed as a three-position setting.

======================  ====================================================
``off``                 No ghosts. Nothing that was destroyed can be made
                        again.
``covered`` (default)   Keep a ghost only where a **surviving** picture
                        already carries the same instance hash, so the
                        retained prompt is byte-identical to one the library
                        already holds and the only marginal exposure is a
                        thumbnail of a near-duplicate.
``on``                  Keep every ghost. The full dehydration promise, and
                        the full trade-off.
======================  ====================================================

**The covered-ghost cascade is the load-bearing part, and it is on the default
path rather than in a corner.** A ghost kept under ``covered`` is safe *because*
a covering picture survives. Destroy the last picture carrying that instance hash
and the ghost stops being covered — retroactively, with nothing on screen to say
so. So every purge re-evaluates the ghosts leaning on the instance hashes it just
destroyed, and destroys the ones that have lost their cover.

**Bounded on purpose, twice.** A purge re-evaluates the instance hashes it
touched, not the whole hub — library-wide re-evaluation is Settings › Privacy's
own purge (§F10), which the plan gates heavily precisely because it is
unbounded. And every hub query here is scoped to ONE library: the cover that
justifies a ghost is a picture in that library's vault, only one vault is live
at a time, so a hub-global cascade would destroy ghosts another library still
has cover for.

**One race is narrowed rather than closed, and it is written down.** The vault
and the hub are separate databases with no transaction spanning them, so the
"is this instance still covered" answer can go stale between being read and
being acted on. :func:`apply_purge_to_hub` re-reads it as late as it can and
intersects the two answers, so both directions of the race fail toward retaining
less.

**This is a step inside the one destruction path, never a sibling of it.**
``services/scrapheap_service.py`` owns everything that permanently destroys a
soft-deleted picture; the functions here are called from inside it and from
nowhere else.

**Forgetting reaches every derived copy, and here it does so structurally.** A
prompt lives in ``picture.comfyui_positive_prompt`` *and*, in vector form, in
``picture.text_embedding``. Both are columns of the row the purge deletes, so a
picture whose ghost is refused leaves neither behind. Nothing in the hub holds an
embedding, so destroying a ghost is a row delete with no derived copy to chase.

Model ghosts — the readable filename of a model no longer on the shelf, surviving
inside ``workflow_recipe_asset`` — are the other half of library plan §5 and are
destroyed by :func:`pixlstash.hub.workflows.forget_asset_names`. They are not
reached by a picture purge: a model ghost is created by removing a model from the
shelf and has no relationship to any one picture.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.hub.workflows import (
    PictureGhost,
    destroy_ghosts_for_instances,
    record_picture_ghosts,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.comfyui_utilities import extract_comfy_workflow_info
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.scope_table import scope_id_subquery
from pixlstash.utils.sql_chunking import chunked

logger = get_logger(__name__)

# Server-config key and the three positions it may hold.
GHOST_RETENTION_KEY = "workflow_ghost_retention"

GHOST_RETENTION_OFF = "off"
GHOST_RETENTION_COVERED = "covered"
GHOST_RETENTION_ON = "on"

GHOST_RETENTION_CHOICES: tuple[str, ...] = (
    GHOST_RETENTION_OFF,
    GHOST_RETENTION_COVERED,
    GHOST_RETENTION_ON,
)

# Settled by the owner, 2026-08-23: ``covered``. Near-zero marginal exposure, and
# the commonest reclaim case (collapse a stack to its cover) works without a
# visit to Settings.
DEFAULT_GHOST_RETENTION = GHOST_RETENTION_COVERED


@dataclass(frozen=True)
class GhostCandidate:
    """What a purge reads off a picture BEFORE its row is deleted.

    Everything here has to be captured while the row still exists. ``pixel_sha``
    is the ghost's key, and the file paths are resolved later — the originals are
    still on disk at that point, because the purge removes rows first and files
    second.
    """

    picture_id: int
    pixel_sha: Optional[str]
    instance_hash: Optional[str]
    structural_hash: Optional[str]
    positive_prompt: Optional[str]
    file_path: Optional[str]


@dataclass(frozen=True)
class GhostPurgeContext:
    """Everything the hub step needs, gathered inside the purge's DB submission.

    Two reads that cannot be swapped: ``candidates`` has to be taken **before**
    the ``DELETE`` (the rows carry the prompt and the hashes) and
    ``surviving_instance_hashes`` **after** it (or the pictures being destroyed
    would count as their own cover).
    """

    candidates: list[GhostCandidate]
    surviving_instance_hashes: set[str]


def read_ghost_retention(server_config: dict) -> str:
    """Read ``workflow_ghost_retention`` from a server-config dict.

    An absent key means :data:`DEFAULT_GHOST_RETENTION`.

    **An unrecognised value resolves to ``off``, which is the opposite direction
    from the scrapheap retention window and deliberately so.** There, a config we
    cannot parse must not license a *deletion*; here, it must not license
    *keeping* a thumbnail and a prompt for a picture the user destroyed. In both
    cases the unparseable config falls to the position that holds less of the
    user's data.
    """
    if GHOST_RETENTION_KEY not in server_config:
        return DEFAULT_GHOST_RETENTION
    raw = server_config.get(GHOST_RETENTION_KEY)
    value = str(raw).strip().lower() if raw is not None else ""
    if value in GHOST_RETENTION_CHOICES:
        return value
    logger.warning(
        "server-config %s=%r is not one of %s; keeping NO ghosts (treating it "
        "as %r) until a valid position is saved, because an unreadable config "
        "must not be read as consent to retain prompts and thumbnails for "
        "destroyed pictures",
        GHOST_RETENTION_KEY,
        raw,
        GHOST_RETENTION_CHOICES,
        GHOST_RETENTION_OFF,
    )
    return GHOST_RETENTION_OFF


def collect_ghost_candidates_in_session(
    session: Session, picture_ids: list[int]
) -> list[GhostCandidate]:
    """Read the ghost material for a purge plan, before anything is deleted.

    Called from inside ``plan_and_purge_in_session`` so it runs in the same DB
    submission as the destruction it belongs to. Pictures with no instance hash
    are dropped here rather than later: without one there is nothing for the
    cascade to key on and nothing that could ever be made again, so they can
    leave no trace at all.
    """
    if not picture_ids:
        return []
    # A temp-table scope, not a bound-parameter list: the purge plan can be the
    # ENTIRE scrapheap, and SQLite caps parameters per statement. This is the
    # same helper (and the same id list) that ``still_scrapheaped_ids_in_session``
    # two lines later in the purge already routes through; a raw ``.in_()`` here
    # would abort the whole purge at a few thousand pictures. The table name is
    # distinct because two other scopes are live on this connection inside the
    # same submission.
    scope = scope_id_subquery(
        session, picture_ids, name="_pixlstash_ghost_candidate_ids"
    )
    rows = session.exec(
        select(
            Picture.id,
            Picture.pixel_sha,
            Picture.workflow_instance_hash,
            Picture.workflow_structural_hash,
            Picture.comfyui_positive_prompt,
            Picture.file_path,
        ).where(Picture.id.in_(scope))
    ).all()
    candidates = []
    for row in rows:
        if not row.workflow_instance_hash:
            continue
        candidates.append(
            GhostCandidate(
                picture_id=row.id,
                pixel_sha=row.pixel_sha,
                instance_hash=row.workflow_instance_hash,
                structural_hash=row.workflow_structural_hash,
                positive_prompt=row.comfyui_positive_prompt,
                file_path=row.file_path,
            )
        )
    return candidates


def surviving_instance_hashes_in_session(
    session: Session, instance_hashes: list[str]
) -> set[str]:
    """Which of these instance hashes a SURVIVING picture still carries.

    Must run **after** the purge's ``DELETE`` has committed, so "surviving" means
    what it says.

    A soft-deleted picture does not count. It exists and can be restored, but it
    is sitting in the destruction queue, and reading it as cover would keep a
    ghost alive on the strength of a picture that is itself on its way out. This
    matches the workflow counts, which exclude the scrapheap for the same reason
    (§B3).
    """
    surviving: set[str] = set()
    for batch in chunked(instance_hashes):
        rows = session.exec(
            select(Picture.workflow_instance_hash)
            .where(Picture.workflow_instance_hash.in_(batch))
            .where(Picture.deleted.is_(False))
            .distinct()
        ).all()
        surviving.update(value for value in rows if value)
    return surviving


def _thumbnail_bytes(image_root: str, file_path: Optional[str]) -> Optional[bytes]:
    """The picture's thumbnail, read before the purge removes it from disk.

    ``None`` means the caller writes no ghost at all — see :func:`_prepare_ghosts`.
    """
    thumb_path = ImageUtils.get_thumbnail_path(image_root, file_path)
    if not thumb_path or not os.path.exists(thumb_path):
        return None
    try:
        with open(thumb_path, "rb") as handle:
            return handle.read()
    except OSError as exc:
        logger.warning(
            "Ghost retention: could not read the thumbnail %s for a picture "
            "being destroyed (%s); NO ghost is written for it — a ghost is the "
            "thumbnail and the prompt together, and keeping the prompt alone "
            "would retain the more sensitive half on its own",
            thumb_path,
            exc,
        )
        return None


def _seed(image_root: str, file_path: Optional[str]) -> Optional[int]:
    """The generation seed, re-read from the file the purge is about to delete.

    The seed is not a picture column — the instance hash excludes it on purpose,
    because a generation is an instance *plus* a seed — so the only place it
    exists is the embedded graph. Under ``covered`` it is also the ONLY thing
    that distinguishes the ghost from the surviving picture covering it, which is
    what makes the ghost worth keeping at all.

    Read only for ghosts that are actually being retained, never for the ones
    being refused. ``Image.open`` parses the header and text chunks without
    decoding pixels, so this costs a header read per retained ghost.
    """
    if not file_path:
        return None
    resolved = ImageUtils.resolve_picture_path(image_root, file_path)
    if not resolved:
        return None
    info = extract_comfy_workflow_info(
        ImageUtils.extract_embedded_metadata(resolved) or {}
    )
    if not info:
        return None
    seed = info.get("seed")
    return seed if isinstance(seed, int) else None


def cascade_uncovered_ghosts(
    hub,
    library_uuid: Optional[str],
    retention: str,
    destroyed_instance_hashes: set[str],
    surviving_instance_hashes: set[str],
) -> int:
    """Destroy the ghosts these destroyed pictures were covering. Returns how many.

    **The cascade on its own, for the destruction paths that cannot write a
    ghost.** ``MissingFilePurgeTask`` removes a picture row whose FILE has
    already vanished from disk, so there is no thumbnail to retain and no ghost
    it could ever create — but it can still remove the last picture carrying an
    instance hash, and that un-covers every ghost leaning on it. Leaving that
    path out would let the safe class decay into the unsafe one exactly where
    nobody is looking, which is the failure library plan §5 names.

    ``on`` never cascades — it keeps every ghost unconditionally, which is the
    whole of what that position promises. ``off`` destroys every ghost this
    library holds for a hash it just touched, because at that position no ghost
    is consented to at all; the surviving set is simply not consulted. Both are
    the same rule the scrapheap purge applies, spelled out here so the two
    destruction paths cannot drift apart.
    """
    if hub is None or not library_uuid or retention == GHOST_RETENTION_ON:
        return 0
    if retention == GHOST_RETENTION_OFF:
        doomed = destroyed_instance_hashes
    else:
        doomed = destroyed_instance_hashes - surviving_instance_hashes
    return destroy_ghosts_for_instances(hub, library_uuid, sorted(doomed))


def apply_purge_to_hub(
    hub,
    library_uuid: Optional[str],
    image_root: str,
    retention: str,
    context: GhostPurgeContext,
    destroyed_ids: set[int],
    recheck_surviving=None,
) -> tuple[int, int]:
    """Write the consented ghosts and destroy the ones that have lost their cover.

    Args:
        hub: The :class:`~pixlstash.hub.db.HubDatabase`, or ``None`` for a vault
            opened without one (the CLI tools, most tests) — then there is
            nowhere a ghost could live and nothing to do.
        library_uuid: Which library's ghosts these are. ``None`` for a vault
            opened outside a hub registration; nothing is written or destroyed,
            because a ghost that cannot name its library cannot be cascaded
            correctly later.
        image_root: The vault's image root, for resolving thumbnails and files
            that are still on disk at this point.
        retention: One of :data:`GHOST_RETENTION_CHOICES`.
        context: The before-and-after reads taken inside the purge's own DB
            submission.
        destroyed_ids: The ids the guarded ``DELETE`` actually removed. A picture
            that left the scrapheap mid-purge still exists, so it gets no ghost.
        recheck_surviving: Optional ``set[str] -> set[str]``, re-reading coverage
            immediately before the write. See below.

    Returns:
        ``(written, cascaded)`` — ghosts retained, and ghosts destroyed because
        their last covering picture went.
    """
    if hub is None or not library_uuid:
        return 0, 0
    destroyed = [
        candidate
        for candidate in context.candidates
        if candidate.picture_id in destroyed_ids and candidate.instance_hash
    ]
    if not destroyed:
        return 0, 0
    touched = {candidate.instance_hash for candidate in destroyed}

    if retention == GHOST_RETENTION_ON:
        kept_hashes = touched
    elif retention == GHOST_RETENTION_OFF:
        kept_hashes = set()
    else:
        kept_hashes = touched & context.surviving_instance_hashes

    # Gather first, decide last. Reading the thumbnail and the seed is file I/O
    # and this runs off the DB worker thread, so an unknown amount of wall-clock
    # separates the coverage read inside the purge's submission from the write
    # below — long enough for another writer to delete the very picture that was
    # the cover. So coverage is re-read here, as late as it can be, and the
    # answer is INTERSECTED with the earlier one: a hash that lost its cover in
    # between is dropped and cascaded, and one that gained cover is simply not
    # written this time round (the next purge will see it). Both directions fail
    # toward retaining less, which is the direction this feature exists for.
    #
    # The race cannot be closed outright — the vault and the hub are separate
    # databases with no transaction spanning them — so it is narrowed to the
    # hub write itself and written down rather than claimed away.
    prepared = _prepare_ghosts(library_uuid, image_root, destroyed, kept_hashes)
    if recheck_surviving is not None and retention == GHOST_RETENTION_COVERED:
        kept_hashes &= recheck_surviving(touched)
        prepared = [ghost for ghost in prepared if ghost.instance_hash in kept_hashes]

    # Destroy first. The two sets are disjoint by construction, and keeping the
    # order that way means a future change which makes them overlap fails toward
    # destroying rather than toward retaining.
    cascaded = destroy_ghosts_for_instances(
        hub, library_uuid, sorted(touched - kept_hashes)
    )
    written = record_picture_ghosts(hub, prepared)
    logger.info(
        "Ghost retention (%s): %d picture(s) destroyed with a workflow "
        "instance, %d ghost(s) kept, %d ghost(s) destroyed by the covered "
        "cascade",
        retention,
        len(destroyed),
        written,
        cascaded,
    )
    return written, cascaded


def _prepare_ghosts(
    library_uuid: str,
    image_root: str,
    destroyed: list[GhostCandidate],
    kept_hashes: set[str],
) -> list[PictureGhost]:
    """Build the ghosts for the retained candidates, reading their files.

    **A candidate with no thumbnail on disk gets no ghost at all.** A ghost is
    the thumbnail AND the prompt; keeping the prompt alone would retain the more
    sensitive half on its own, and it would also destroy the argument that makes
    ``covered`` a safe default — that a covered ghost's only marginal exposure
    is a thumbnail of a near-duplicate. Thumbnails are generated lazily, so a
    picture destroyed before its thumbnail existed genuinely reaches here with
    nothing to keep.
    """
    ghosts = []
    for candidate in destroyed:
        if candidate.instance_hash not in kept_hashes:
            continue
        if not candidate.pixel_sha:
            logger.warning(
                "Ghost retention: picture id=%s qualified for a ghost but has "
                "no pixel_sha, which is the only identifier that survives its "
                "row; no ghost is written for it",
                candidate.picture_id,
            )
            continue
        thumbnail = _thumbnail_bytes(image_root, candidate.file_path)
        if not thumbnail:
            logger.info(
                "Ghost retention: picture id=%s qualified for a ghost but has "
                "no thumbnail on disk, so no ghost is written — a ghost is the "
                "thumbnail and the prompt together, never the prompt alone",
                candidate.picture_id,
            )
            continue
        ghosts.append(
            PictureGhost(
                library_uuid=library_uuid,
                pixel_sha=candidate.pixel_sha,
                instance_hash=candidate.instance_hash,
                thumbnail=thumbnail,
                structural_hash=candidate.structural_hash,
                positive_prompt=candidate.positive_prompt,
                seed=_seed(image_root, candidate.file_path),
            )
        )
    return ghosts
