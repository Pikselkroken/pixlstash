"""On-demand near-neighbour tag scan - find one tag's suspects and append them.

The in-app equivalent of ``scripts/near_neighbor_label_disagreement.py``: it reuses the
shared :func:`pixlstash.utils.near_neighbor.knn_disagreement_with_neighbors` kernel so
the CLI and the UI can't drift, and is merge-aware via :data:`DEFAULT_TAG_MERGES`.

The write path is **diff-insert / refresh-in-place, never delete-and-rebuild**:
a scan only inserts suspects that don't already have a row for (tag, source),
refreshes the stored evidence on rows that do, and never deletes or resurrects
rows. When a ``review_id`` is given the scan writes into that review session
(see :class:`pixlstash.db_models.review.Review`):

* new suspects are inserted with ``review_id`` and their neighbourhood evidence
  captured into ``TagSuggestion.neighbors``;
* still-**undecided** rows (``PENDING`` *or* ``SKIPPED`` - a skip records no
  decision) from the legacy queue or a closed review are adopted into the
  review with fresh evidence and counted as ``new`` (a re-parented ``SKIPPED``
  row is re-pended so it re-appears in the queue); they were never decided, so
  this resurrects nothing;
* rows already **decided** in an earlier review are skipped and counted as
  ``prev_reviewed`` - unless ``include_reviewed=True``, which re-parents them
  into the new review with ``status`` back to ``PENDING`` (the row is kept, so
  UNIQUE(picture_id, tag, source) and the audit trail both survive; the
  overwritten decision is snapshotted into ``prior_*`` so undo can restore it);
* rows already belonging to *this* review are never touched - a refresh cannot
  resurrect the review's own decided rows nor re-pend its own skips.

Without a ``review_id`` (the legacy ``POST /tag_suggestions/scan`` path) a
re-scan refreshes the evidence on existing ``PENDING`` rows **in place** - it is
deliberately a diff/refresh, **not** a delete-and-rebuild purge, so the row's
identity and history survive a re-scan.

Suppression of previously-reviewed suspects is therefore **per-review** (the
explicit ``include_reviewed`` toggle), not the old permanent ``reviewed_pids``
skip. Runs synchronously - fast enough for an interactive click on a typical
vault.

Suggestion *kind* ("pair" for true versions of one shot vs "binary") is derived
at read time (see :func:`pixlstash.services.review_service.derive_kind`) from
the pictures' ``stack_id`` and dhash - not stored - so legacy rows and
re-parented rows get it uniformly.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING

import numpy as np
from sqlalchemy import and_
from sqlmodel import Session, select

from pixlstash.db_models import (
    Picture,
    Project,
    Tag,
)
from pixlstash.db_models.tag import DEFAULT_TAG_MERGES
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_id_subquery
from pixlstash.services.tag_health_service import EST_MISSING_MIN_CONF
from pixlstash.utils.service.scope_table import scope_id_subquery
from pixlstash.utils.near_neighbor import (
    EMBEDDING_BYTES,
    EMBEDDING_DIM,
    dedupe_by_pair,
    hamming_distance,
    knn_disagreement_with_neighbors,
    nearest_opposite_by_hamming,
)

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

SOURCE = "near_neighbor"

# Default dhash Hamming threshold for both the displayed-twin override at scan
# time and the read-time "pair" kind derivation (same shot, altered copy).
DEFAULT_MAX_TWIN_HAMMING = 8

# Floor on the displayed twin's actual CLIP cosine similarity for the dhash
# override below to take effect. dhash proximity alone is not reliable enough:
# 64-bit hash collisions between visually unrelated pictures happen, and
# without this floor the UI could label a ~55%-similar pair "versions of the
# same shot", which reads as contradictory nonsense to the user.
# Tuning: 0.9 proved too strict - genuinely near-identical altered copies
# (recolour, watermark, heavy filter) can land in the 0.8–0.9 CLIP band, while
# the collisions this floor exists to kill sit far lower (~0.5–0.6).
MIN_DISPLAY_TWIN_SIM = 0.85

# --- Fix 1: near-zero-ground-truth fallback -------------------------------
#
# Below this many concept-positive Tag rows (within the scan's scope), the kNN
# vote is statistically vacuous, not just weak: knn_disagreement_with_neighbors
# votes each picture's k nearest neighbours against `has_concept`, so if that
# mask has ZERO True entries, pos_frac is identically 0.0 for every picture
# regardless of true prevalence - add_threshold can then mathematically never
# be met. Confirmed empirically against the real vault: a brand-new tag
# ("compression artifacts", 0 Tag rows, 36,318 embeddings) produced pos_frac
# == 0.0 vault-wide.
#
# The floor is 1 (i.e. the fallback fires only at exactly zero ground truth),
# not the investigation's suggested "<5" - deliberately more conservative.
# Zero is the only count that is *provably* vacuous for every picture
# unconditionally; at n_ground_truth >= 1 the vote is no longer guaranteed
# meaningless (a picture whose neighbourhood happens to include that one
# positive gets a real, non-zero vote), so a same-scope 1-in-N tag is exactly
# as valid a case for the vote as it always was. A fixed floor above 1 also
# doesn't generalise: scan_tag is used both vault-wide (thousands of
# pictures, where "few" ground truth really is thin) and scoped to a small
# review picture set (picture_ids) or project, where 1-4 tagged pictures out
# of a small pool is completely normal and the vote is still well-founded -
# an absolute floor can't tell those apart, and this repo's own test suite
# exercises exactly that small-scope, few-ground-truth case. Raise this only
# alongside evidence that a specific higher floor doesn't break legitimate
# small-scope scans.
MIN_GROUND_TRUTH_FOR_VOTE = 1

# --- Fix 2: base-rate-relative default thresholds -------------------------
#
# The fixed 0.55/0.45 pair implicitly assumes a ~50% base rate. For a tag
# whose real prevalence sits far from 50% (this vault's minority defect/
# quality tags run 5-25%), E[pos_frac] tracks the tag's own base rate for any
# picture uncorrelated with neighbour structure, so a threshold centered on
# 50% instead of the tag's own p skews eligibility hard toward whichever
# direction is on the base rate's side of 0.5 - confirmed via a permutation
# experiment (real embeddings, shuffled labels to isolate the pure base-rate
# effect): up to 58x remove:add skew at a 20% base rate with zero real
# signal, vs. effectively unreachable add-eligibility at low base rates
# (an unmet 0.55 floor is exactly the "compression artifacts" mechanism above
# once any ground truth exists at all). Centering the thresholds on the tag's
# own p = has_concept.sum() / len(ids) removes that artifact.
#
# margin=0.15 was chosen empirically, verified against the real vault
# (36,318 embeddings) with a permutation experiment (shuffled labels, same
# embeddings, isolates the pure base-rate effect): across base rates 5%-48%
# it brings the remove:add ratio from up to 58x / undefined down to 0.2-1.3x.
# Real tags with strong genuine embedding separation (this vault's
# "malformed hand", "waxy skin") still land off of 1.0 after this fix - a
# single symmetric margin around p cannot fully cancel a real-signal-driven
# population-size asymmetry between the (minority) tagged and (majority)
# untagged groups; see the PR/session notes for the full measurement. The
# fix's proven, unconditional win is eliminating the *unbounded* pure-base-
# rate skew and the near-zero-base-rate "0.55 can never be met" failure mode.
#
# CORRECTED after a confirmed majority-tag regression: the naive symmetric
# `p ± margin` shifts *every* tag uniformly by its own base rate, regardless
# of whether the legacy fixed defaults were already serving that tag well.
# The permutation experiment above that justified margin=0.15 only measured
# minority base rates (5%-48%) - it never validated the formula for p > 0.5.
# Reproduced against the vault.db e2e fixture (111 pictures) for "man"
# (p=68/111=0.6126): the legacy 0.55/0.45 pair finds 1 add suspect; the
# uncapped symmetric formula computes add_threshold=p+0.15=0.7626 - *stricter*
# than the legacy 0.55 - and finds 0. This was never the population the fix
# targeted (see the "minority defect/quality tags" framing above); a majority
# tag being shifted stricter than the legacy default is a pure regression,
# not a base-rate correction.
#
# The fix is a one-directional cap: each threshold is bounded by its own
# legacy fixed default in whichever direction is *stricter* for its role, so
# the shift can only make a threshold at least as reachable as the legacy
# behaviour, never less. Note the two thresholds are stricter in opposite
# directions - add_threshold is stricter the *higher* it is (harder to add),
# remove_threshold is stricter the *lower* it is (harder to remove) - so the
# cap is `min()` for add against the legacy add ceiling (0.55), and `min()`
# for remove against the legacy remove ceiling (0.45) applied to the raw
# `p - margin` value (which itself is floored, not capped, at the absolute
# clamp below): the base-rate shift may only push add_threshold *down* from
# 0.55 or remove_threshold *down* from 0.45, matching the direction that
# relaxes eligibility toward the tag's own p and never past the legacy value.
#
# This preserves the minority-tag win intact: for p < 0.60 the caps never
# engage (`p + margin < 0.55` and `p - margin < 0.45` hold automatically), so
# minority tags see exactly the pre-existing centered thresholds - including
# the deliberate remove-threshold *tightening* below 0.45 that produced the
# measured skew reduction (that tightening is not a "regression" needing a
# floor at 0.45; it is Fix 2's actual contribution and a `max(0.45, ...)`
# floor would silently erase it for essentially every minority tag). For
# p >= 0.60 both caps engage and clamp the pair back to exactly the legacy
# 0.55/0.45 defaults, matching "man" and - by the same mechanism - preventing
# an unverified, symmetric-but-opposite regression at the other extreme (an
# uncapped remove_threshold at p=0.95 would be 0.80, a huge and untested
# expansion of remove-eligibility for majority tags; the cap keeps it at the
# legacy 0.45).
BASE_RATE_THRESHOLD_MARGIN = 0.15
_BASE_RATE_CLAMP_LOW = 0.05
_BASE_RATE_CLAMP_HIGH = 0.95

# Legacy fixed-threshold pair (see CLI default in
# scripts/near_neighbor_label_disagreement.py) - the ceiling each base-rate-
# relative default is capped against so the shift can only relax eligibility
# relative to old behaviour, never tighten it. See the "CORRECTED" note above.
_LEGACY_ADD_THRESHOLD = 0.55
_LEGACY_REMOVE_THRESHOLD = 0.45


def scan_tag(
    vault: "Vault",
    tag: str,
    *,
    project: str | None = "PixlTagger",
    picture_ids: set[int] | None = None,
    k: int = 12,
    add_threshold: float | None = None,
    remove_threshold: float | None = None,
    min_twin_sim: float = 0.85,
    max_twin_hamming: int = DEFAULT_MAX_TWIN_HAMMING,
    min_display_twin_sim: float = MIN_DISPLAY_TWIN_SIM,
    review_id: int | None = None,
    include_reviewed: bool = False,
) -> dict:
    """Scan one tag for near-neighbour label disagreements and append its suspects.

    Args:
        vault: Application vault, used for DB task dispatch.
        tag: The tag to scan, e.g. ``"malformed hand"``.
        project: Scope to this project name (default ``"PixlTagger"``); ``None`` = whole
            vault. Unknown names fall back to the whole vault. Ignored when
            ``picture_ids`` is provided (the review path resolves scope itself).
        picture_ids: Optional explicit scope - only these picture ids are scanned.
            An empty set scans nothing. ``None`` = no explicit scope (use ``project``).
        k: neighbours per image for the kNN vote.
        add_threshold, remove_threshold: explicit override knobs. ``None`` (the
            default) computes both relative to the tag's own base rate
            ``p = has_concept.sum() / len(ids)`` - ``p + BASE_RATE_THRESHOLD_MARGIN``
            / ``p - BASE_RATE_THRESHOLD_MARGIN``, clamped to
            ``[_BASE_RATE_CLAMP_LOW, _BASE_RATE_CLAMP_HIGH]`` and then each capped
            at its own legacy default (``_LEGACY_ADD_THRESHOLD`` /
            ``_LEGACY_REMOVE_THRESHOLD``, the old fixed 0.55/0.45 pair) so the
            base-rate shift can only relax eligibility relative to the legacy
            pair, never tighten it (see the module-level constants' comments
            for why, including the majority-tag regression this capping
            fixes). Passing an explicit float bypasses the base-rate
            computation entirely, for callers (tests, future CLI wiring) that
            want the old fixed-threshold behaviour.
        min_twin_sim: scan knob (CLI default 0.85) gating eligibility on the CLIP
            twin's similarity; unaffected by the perceptual-hash twin override
            below. Not applied to the near-zero-ground-truth confidence fallback
            (see ``MIN_GROUND_TRUTH_FOR_VOTE``) - that path has no neighbour vote
            to corroborate against, so it demands direct model confidence instead
            of neighbour corroboration.
        max_twin_hamming: max 64-bit dhash Hamming distance for the *displayed* twin
            override. When an eligible suspect has an opposite-labelled perceptual
            near-duplicate within this many bits (~<=8 ≈ near-identical), that
            near-duplicate is shown as the twin instead of the CLIP-nearest one. This
            changes only which comparison is displayed, never which pictures are flagged.
        min_display_twin_sim: floor on the candidate override twin's actual CLIP cosine
            similarity to the suspect. dhash proximity alone is a noisy signal - a
            close Hamming distance can still be a hash collision between unrelated
            pictures - so the override is only applied when it's also corroborated by
            embedding similarity. Below this floor, the CLIP-nearest twin (and its
            similarity) from ``min_twin_sim`` above is kept instead.
        review_id: When set, write the suspects into this review session (see the
            module docstring for the diff-insert / re-parent semantics).
        include_reviewed: Only meaningful with ``review_id``: re-parent suspects
            already decided in earlier reviews into this one (status back to PENDING).

    Returns:
        ``{"tag", "count", "added", "removed", "scanned", "new", "prev_reviewed"}``
        where ``count``/``added``/``removed`` describe the suspects the scan
        *detected*, ``new`` is how many rows this call actually added to the
        queue/review (inserted + adopted + re-included), and ``prev_reviewed``
        is how many detected suspects were already decided in earlier reviews.
    """
    # Child tags that PixlTagger merges into this one count as "has the tag" for voting
    # and the "missing" direction (but not for "remove" - see has_literal vs has_concept).
    equiv = {tag} | {
        child for child, parent in DEFAULT_TAG_MERGES.items() if parent == tag
    }

    def _load(session: Session):
        pid = None
        if project and picture_ids is None:
            pid = session.exec(
                select(Project.id).where(Project.name == project)
            ).first()
        q = select(Picture.id, Picture.image_embedding, Picture.perceptual_hash).where(
            Picture.image_embedding.is_not(None), Picture.deleted.is_(False)
        )
        if picture_ids is not None:
            # Filter via a temp-table subquery instead of one bound parameter
            # per id: a large explicit scope (tens of thousands of pictures)
            # would otherwise exceed SQLite's bound-parameter ceiling and raise
            # OperationalError. Result-identical to ``.in_(picture_ids)``.
            q = q.where(Picture.id.in_(scope_id_subquery(session, picture_ids)))
        elif pid is not None:
            q = q.where(Picture.project_id == pid)
        emb_rows = session.exec(q).all()
        literal = set(session.exec(select(Tag.picture_id).where(Tag.tag == tag)).all())
        concept = set(
            session.exec(select(Tag.picture_id).where(Tag.tag.in_(sorted(equiv)))).all()
        )
        # Pictures frozen by a locked set are excluded from being SUSPECTS (the
        # editable item) below - but stay in the pool so they can still serve as
        # twins/neighbour guides (which write nothing). See the suspect loops.
        #
        # Uses the set_lock_service predicate rather than a local
        # PictureSetMember join: membership is stack-atomic, so a picture that
        # merely *shares a stack* with a locked-set member is frozen by the
        # write guards too. The local join missed that arm and selected such a
        # picture as a suspect, producing a card whose every action 423s.
        locked = {
            int(locked_id)
            for locked_id in session.exec(locked_picture_id_subquery()).all()
        }
        return emb_rows, literal, concept, locked

    def _load_confidence_fallback(session: Session) -> list[tuple[int, float]]:
        """Fix 1's bootstrap candidates: literal-tag ``TagPrediction`` rows at or
        above ``EST_MISSING_MIN_CONF``, pinned to the current (non-``manual``)
        model version, for *un-reviewed* pictures with no existing literal ``Tag``
        row (``label_state == "UNKNOWN"`` - no human POS/NEG on the ledger).

        Mirrors ``tag_health_service.compute_tag_health_rows``'s ``est_missing``
        query one-for-one (same threshold constant, same model-version pin, same
        outer-join-on-Tag shape, same ``label_state == "UNKNOWN"`` un-reviewed
        filter) so this fallback and the board's "estimated missing" count can
        never silently diverge. The un-reviewed filter is what stops the cold-start
        path re-proposing a tag a human already REJECTED via the tag-prediction
        reject endpoint: that reject writes a ledger NEG (keeping the tagger's high
        confidence) but no ``TagSuggestion`` row, so ``_write``'s suggestion-level
        dedup can't see it - only this ledger filter can. (A human ACCEPT adds a
        ``Tag`` row, so those are already dropped by ``Tag.picture_id IS NULL``; an
        un-reviewed confident prediction is ``label_state == "UNKNOWN"`` and is
        still proposed.) Deliberately literal (not ``equiv``-merged like the vote
        path's ``has_concept``) to match that query exactly rather than inventing
        new merge semantics here.
        """
        pid = None
        if project and picture_ids is None:
            pid = session.exec(
                select(Project.id).where(Project.name == project)
            ).first()
        current_version = session.exec(
            select(TagPrediction.model_version)
            .where(TagPrediction.model_version != "manual")
            .order_by(TagPrediction.predicted_at.desc())
            .limit(1)
        ).first()
        if current_version is None:
            return []
        q = (
            select(TagPrediction.picture_id, TagPrediction.confidence)
            .join(Picture, Picture.id == TagPrediction.picture_id)
            .outerjoin(
                Tag,
                and_(
                    Tag.picture_id == TagPrediction.picture_id,
                    Tag.tag == TagPrediction.tag,
                ),
            )
            .where(
                Picture.deleted.is_(False),
                TagPrediction.tag == tag,
                TagPrediction.confidence >= EST_MISSING_MIN_CONF,
                TagPrediction.model_version == current_version,
                Tag.picture_id.is_(None),
                # Un-reviewed only - mirrors est_missing so a human-REJECTED tag
                # (ledger NEG, no Tag row) is never re-proposed as a cold-start add.
                TagPrediction.label_state == "UNKNOWN",
            )
        )
        if picture_ids is not None:
            # Temp-table subquery, not one bound parameter per id - a large
            # scope would otherwise exceed SQLite's bound-parameter ceiling.
            # Result-identical to ``.in_(picture_ids)``.
            q = q.where(
                TagPrediction.picture_id.in_(scope_id_subquery(session, picture_ids))
            )
        elif pid is not None:
            q = q.where(Picture.project_id == pid)
        return session.exec(q).all()

    emb_rows, literal, concept, locked_ids = vault.db.run_immediate_read_task(_load)

    ids: list[int] = []
    blobs: list[bytes] = []
    phash_values: list[int] = []
    phash_valid: list[bool] = []
    for pic_id, blob, phash in emb_rows:
        if blob is None or len(blob) != EMBEDDING_BYTES:
            continue
        ids.append(pic_id)
        blobs.append(blob)
        # dhash is stored as a 16-char lowercase hex string (8x8 = 64 bits). Parse to an
        # int; mark missing/malformed values invalid rather than raising.
        value = 0
        valid = False
        if phash:
            try:
                value = int(phash, 16)
                valid = True
            except (ValueError, TypeError):
                logger.warning(
                    "scan_tag: unparseable perceptual_hash %r for picture %s; "
                    "excluding from near-duplicate twin selection",
                    phash,
                    pic_id,
                )
        phash_values.append(value)
        phash_valid.append(valid)

    empty = {
        "tag": tag,
        "count": 0,
        "added": 0,
        "removed": 0,
        "scanned": len(ids),
        "new": 0,
        "prev_reviewed": 0,
    }
    if len(ids) < 2:
        return empty

    # uint64 so the full 64-bit dhash range round-trips for the XOR/popcount Hamming.
    phash_ints = np.array(phash_values, dtype=np.uint64)
    valid_mask = np.array(phash_valid, dtype=bool)

    emb = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(
        len(ids), EMBEDDING_DIM
    )
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    emb = (emb / norms).astype(np.float32)

    has_literal = np.array([pid in literal for pid in ids], dtype=bool)
    has_concept = np.array([pid in concept for pid in ids], dtype=bool)
    n_ground_truth = int(has_concept.sum())

    suspects: list[dict] = []
    if n_ground_truth < MIN_GROUND_TRUTH_FOR_VOTE:
        # Fix 1: the kNN vote below would be statistically vacuous with this
        # little ground truth - has_concept is all/near-all False, so pos_frac
        # is ~0.0 for every picture and add_threshold could never be met (the
        # confirmed "compression artifacts" bug). Bootstrap from direct model
        # confidence instead (mirrors tag_health_service's est_missing query;
        # see _load_confidence_fallback). Deliberately bypasses min_twin_sim:
        # there is no neighbour vote here to corroborate against, so demanding
        # direct confidence evidence is the honest substitute for "no ground
        # truth to vote against", not a gate this signal was ever meant to
        # clear.
        logger.info(
            "scan_tag(%r): only %d ground-truth positive(s) (< floor %d); using "
            "the confidence-based fallback instead of the kNN vote",
            tag,
            n_ground_truth,
            MIN_GROUND_TRUTH_FOR_VOTE,
        )
        fallback_rows = vault.db.run_immediate_read_task(_load_confidence_fallback)
        for pic_id, confidence in fallback_rows:
            # A picture frozen by a locked set is never surfaced as a suspect.
            if int(pic_id) in locked_ids:
                continue
            conf = float(confidence)
            suspects.append(
                {
                    "picture_id": int(pic_id),
                    "direction": "add",
                    "score": round(conf, 4),
                    "twin_picture_id": None,
                    "twin_sim": None,
                    # None, not the confidence: pos_frac is the kNN neighbour
                    # vote fraction, a different quantity, and this fallback runs
                    # precisely because there is no vote to compute one from.
                    # Kept present so both paths emit structurally identical dicts.
                    "pos_frac": None,
                    "reason": (
                        f"model is confident ({conf:.0%}) but this tag has no "
                        "confirmed examples yet"
                    ),
                    "neighbors": [],
                }
            )
    else:
        # Fix 2: default thresholds relative to the tag's own base rate rather
        # than the fixed 0.55/0.45 pair (see BASE_RATE_THRESHOLD_MARGIN above)
        # - only when the caller didn't explicitly override. Each is also
        # capped at its own legacy default (min() for both - see the
        # "CORRECTED" comment above for why add and remove need the same
        # min() shape despite being stricter in opposite directions) so the
        # base-rate shift can only relax eligibility relative to the legacy
        # 0.55/0.45 pair, never tighten it.
        p = n_ground_truth / len(ids)
        p_clamped = min(_BASE_RATE_CLAMP_HIGH, max(_BASE_RATE_CLAMP_LOW, p))
        effective_add_threshold = (
            add_threshold
            if add_threshold is not None
            else min(
                _LEGACY_ADD_THRESHOLD,
                min(_BASE_RATE_CLAMP_HIGH, p_clamped + BASE_RATE_THRESHOLD_MARGIN),
            )
        )
        effective_remove_threshold = (
            remove_threshold
            if remove_threshold is not None
            else min(
                _LEGACY_REMOVE_THRESHOLD,
                max(_BASE_RATE_CLAMP_LOW, p_clamped - BASE_RATE_THRESHOLD_MARGIN),
            )
        )

        pos_frac, twin_idx, twin_sim, neighbor_idx = knn_disagreement_with_neighbors(
            emb, has_concept, k
        )

        for i in range(len(ids)):
            # A picture frozen by a locked set is never surfaced as a suspect, but
            # it remains in `ids`/`emb` above so it can still be a twin/neighbour.
            if int(ids[i]) in locked_ids:
                continue
            # ADD eligibility uses the merged concept; REMOVE uses the literal tag.
            if not has_concept[i] and pos_frac[i] >= effective_add_threshold:
                direction, score = "add", float(pos_frac[i])
            elif has_literal[i] and pos_frac[i] <= effective_remove_threshold:
                direction, score = "remove", float(1.0 - pos_frac[i])
            else:
                continue
            if twin_sim[i] < min_twin_sim:
                continue
            # Eligibility above is unchanged. Below, only the *displayed* twin may
            # switch: if this suspect has an opposite-labelled perceptual
            # near-duplicate (an altered copy of itself), show that as the twin
            # instead of the CLIP-nearest one.
            ti = int(twin_idx[i])
            display_twin_id = int(ids[ti]) if ti >= 0 else None
            display_twin_sim = round(float(twin_sim[i]), 4)
            reason = (
                f"near-twin {display_twin_id} (sim {display_twin_sim:.3f}) disagrees; "
                f"{float(pos_frac[i]):.0%} of nearest neighbours have the tag"
            )

            j = nearest_opposite_by_hamming(
                phash_ints, valid_mask, has_concept, i, max_twin_hamming, twin_sim
            )
            if j >= 0 and j != ti:
                # Recompute similarity for the candidate override twin so the gate
                # below (and the stored value, if it's applied) describes this
                # specific pair, not the (possibly discarded) CLIP-nearest twin.
                candidate_sim = round(float(emb[i] @ emb[j]), 4)
                if candidate_sim >= min_display_twin_sim:
                    d = hamming_distance(int(phash_values[i]), int(phash_values[j]))
                    display_twin_id = int(ids[j])
                    display_twin_sim = candidate_sim
                    reason = (
                        f"near-duplicate twin {display_twin_id} (dhash hamming {d}); "
                        f"{float(pos_frac[i]):.0%} of nearest neighbours have the tag"
                    )

            # The neighbourhood evidence the vote used, most-similar first, with
            # each neighbour's merged-concept "has the tag" flag - frozen at scan
            # time.
            neighbors = [
                {"picture_id": int(ids[m]), "has": bool(has_concept[m])}
                for m in neighbor_idx[i]
                if m >= 0
            ]

            suspects.append(
                {
                    "picture_id": int(ids[i]),
                    "direction": direction,
                    "score": round(score, 4),
                    "twin_picture_id": display_twin_id,
                    "twin_sim": display_twin_sim,
                    "pos_frac": round(float(pos_frac[i]), 4),
                    "reason": reason,
                    "neighbors": neighbors,
                }
            )

    # A mutually-disagreeing pair yields both a remove and an add suspect that are the
    # same review - keep one per pair so the queue doesn't show it twice.
    suspects = dedupe_by_pair(suspects)

    def _write(session: Session) -> dict:
        # Diff-insert against ALL existing rows for (tag, source): the unique
        # constraint is on (picture_id, tag, source), so a suspect with any prior
        # row is updated-or-skipped, never re-inserted. Nothing is ever deleted.
        existing = {
            row.picture_id: row
            for row in session.exec(
                select(TagSuggestion).where(
                    TagSuggestion.tag == tag, TagSuggestion.source == SOURCE
                )
            ).all()
        }
        now = datetime.utcnow()
        new_count = 0
        prev_reviewed = 0

        def _refresh_scan_fields(row: TagSuggestion, r: dict) -> None:
            row.direction = r["direction"]
            row.score = r["score"]
            row.reason = r["reason"]
            row.twin_picture_id = r["twin_picture_id"]
            row.twin_sim = r["twin_sim"]
            row.neighbors = json.dumps(r["neighbors"])

        for r in suspects:
            row = existing.get(r["picture_id"])
            if row is None:
                session.add(
                    TagSuggestion(
                        picture_id=r["picture_id"],
                        tag=tag,
                        direction=r["direction"],
                        source=SOURCE,
                        score=r["score"],
                        reason=r["reason"],
                        twin_picture_id=r["twin_picture_id"],
                        twin_sim=r["twin_sim"],
                        status="PENDING",
                        created_at=now,
                        review_id=review_id,
                        neighbors=json.dumps(r["neighbors"]),
                    )
                )
                new_count += 1
                continue
            if review_id is not None and row.review_id == review_id:
                # Already part of this review - pending, skipped, or decided.
                # Never touch it: a refresh must not resurrect this review's own
                # decisions, nor re-pend a row it deliberately skipped.
                continue
            if row.status in ("PENDING", "SKIPPED"):
                # Undecided (PENDING or SKIPPED - no decision was ever made) row
                # from the legacy global queue or a closed/legacy review. SKIPPED
                # adopts exactly like PENDING and is NOT prev_reviewed; only
                # genuinely decided rows are (see the branch below).
                if review_id is not None:
                    # Adopt it into this review with fresh scan evidence,
                    # re-pending a SKIPPED row so it re-appears in the queue.
                    # Nobody decided it, so this resurrects nothing.
                    row.review_id = review_id
                    row.status = "PENDING"
                    row.reviewed_at = None
                    _refresh_scan_fields(row, r)
                    new_count += 1
                elif row.status == "PENDING":
                    # Legacy scan (review_id=None): no review to adopt into, so
                    # refresh the stale evidence in place on the existing PENDING
                    # row. DELIBERATE no-purge decision - this is what replaces
                    # the old delete-and-rebuild "rebuild" path: a re-scan
                    # UPDATES direction/score/reason/twin/neighbours and never
                    # deletes or recreates the row, so UNIQUE(picture_id, tag,
                    # source) and the audit trail both survive. Nothing is ever
                    # deleted here. (SKIPPED legacy rows have no review to adopt
                    # into and are left untouched.)
                    _refresh_scan_fields(row, r)
                continue
            # Genuinely DECIDED in an earlier review (or the legacy queue):
            # ACCEPTED / DISMISSED / SWAPPED / TWIN_FIXED. Only these are
            # prev_reviewed.
            prev_reviewed += 1
            if include_reviewed and review_id is not None:
                # Explicit re-surfacing: re-parent the decided row into this
                # review and reopen it. The row (and its history in the ledger)
                # is kept - UNIQUE(picture_id, tag, source) stays intact. Capture
                # the decision being overwritten (its review_id/status/
                # reviewed_at) into prior_* FIRST, so undo can restore it -
                # re-exposing the original decision for a normal reversal
                # instead of silently erasing it.
                row.prior_review_id = row.review_id
                row.prior_status = row.status
                row.prior_reviewed_at = row.reviewed_at
                row.review_id = review_id
                row.status = "PENDING"
                row.reviewed_at = None
                _refresh_scan_fields(row, r)
                new_count += 1
        session.commit()
        return {"new": new_count, "prev_reviewed": prev_reviewed}

    write_stats = vault.db.run_task(_write)

    added = sum(1 for r in suspects if r["direction"] == "add")
    removed = sum(1 for r in suspects if r["direction"] == "remove")
    return {
        "tag": tag,
        "count": len(suspects),
        "added": added,
        "removed": removed,
        "scanned": len(ids),
        "new": write_stats["new"],
        "prev_reviewed": write_stats["prev_reviewed"],
    }
