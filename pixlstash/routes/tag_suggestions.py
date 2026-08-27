"""HTTP routes for the tag-suggestion review queue (dataset refinement).

Exposes the ranked queue of suspected label fixes and the accept/dismiss actions that
close the loop. Accepting writes through to the Tag table and emits a CHANGED_TAGS
event so open clients refresh; dismissing only updates the suggestion's status.

See :mod:`pixlstash.services.tag_suggestion_service` for the writeback semantics.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import tag_scan_service, tag_suggestion_service
from pixlstash.utils.service.caption_utils import sync_picture_sidecar
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids

logger = get_logger(__name__)


class TagSuggestionItemResponse(BaseModel):
    """A single suspected label fix in the review queue.

    **Twin fields are scope-redacted.** For a resource-scoped share token whose
    grant does not cover the twin picture, ``twin_picture_id``, ``twin_sim``,
    ``twin_ext`` and ``twin_tagger_confidence`` are ``null`` and ``reason`` is
    replaced with a generic string - the twin's identity is not the token's to
    learn. An owner/unscoped caller always gets the full pair. Clients already
    handle a twinless row (the confidence-only bootstrap path emits one), so a
    redacted row needs no special casing.
    """

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    picture_id: int
    tag: str
    direction: str
    source: str
    score: float
    reason: Optional[str] = None
    twin_picture_id: Optional[int] = None
    twin_sim: Optional[float] = None
    model_version: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[str] = None
    # File extensions so the client can render full-res /pictures/{id}.{ext} images.
    picture_ext: Optional[str] = None
    twin_ext: Optional[str] = None
    # The tagger's most recent raw confidence for this (picture, tag), if predicted -
    # for the suspect and (so the reviewer can weigh both sides) its twin.
    tagger_confidence: Optional[float] = None
    twin_tagger_confidence: Optional[float] = None


class ReviewSuggestionResponse(BaseModel):
    """Result of accepting or dismissing a suggestion."""

    model_config = ConfigDict(extra="allow")

    status: str
    picture_id: int
    tag: str
    direction: str


class BulkAcceptRequest(BaseModel):
    """Resolve PENDING suggestions where the neighbour vote and tagger agree (both ≥ threshold)."""

    tag: str
    min_combined: float = 0.9
    direction: Optional[str] = None
    dry_run: bool = False
    # Optional review-scope narrowing - the dry-run count and the apply both honour
    # the same filter, matched on the suspect picture only. They AND together.
    project_id: Optional[int] = None
    set_id: Optional[int] = None
    character_id: Optional[str] = None
    # Optional review-session narrowing: only rows with this TagSuggestion.review_id
    # are counted/resolved (the review creation receipt's "auto-resolve?" path).
    review_id: Optional[int] = None


class BulkReopenRequest(BaseModel):
    """Batch-undo: reopen the given suggestion ids.

    With ``review_id`` set and ``ids`` empty, reopens ALL of that review's
    decided rows (SKIPPED rows made no changes and are left as-is).
    """

    ids: list[int] = []
    # When set, ids belonging to a different (or no) review are skipped.
    review_id: Optional[int] = None


class ScanRequest(BaseModel):
    """Run a near-neighbour scan for one tag, rebuilding its pending queue."""

    tag: str
    project: Optional[str] = "PixlTagger"


class ScanResultResponse(BaseModel):
    """Result of a tag scan."""

    model_config = ConfigDict(extra="allow")

    tag: str
    count: int
    added: int = 0
    removed: int = 0
    scanned: int = 0


class BulkResultResponse(BaseModel):
    """Result of a bulk accept/reopen."""

    model_config = ConfigDict(extra="allow")

    count: int
    accepted_ids: list[int] = []
    picture_ids: list[int] = []
    sample: list[dict] = []
    # Picture ids skipped because a locked set freezes their labels (bulk
    # accept/reopen skip locked suspects/twins rather than 423-ing the batch).
    skipped_locked: list[int] = []


def _notify_changed(server, picture_ids, origin_client_id: str | None = None) -> None:
    """Emit a SINGLE CHANGED_TAGS for all distinct ids + sync each sidecar.

    Collapses the previous one-event-per-picture loop into one batched event so a
    bulk accept/reopen does not flood the grid with N refresh frames (issue #500).
    The event carries ``origin_client_id`` so the originating tab recognises its
    own change and reconciles in place instead of raising a pill (issue #499).

    This emits ``notify()`` directly (not via the background 2s coalescer in
    ``vault.py``) on purpose: the coalescer flushes CHANGED_TAGS WITHOUT an origin,
    which would re-introduce the self-pill. Interactive tag edits stay direct, just
    batched.
    """
    distinct_ids: list[int] = []
    seen = set()
    for pid in picture_ids:
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        distinct_ids.append(pid)
    if not distinct_ids:
        return
    server.vault.notify(
        EventType.CHANGED_TAGS,
        {
            "picture_ids": distinct_ids,
            "origin_client_id": origin_client_id,
            "change_kind": "updated",
        },
    )
    for pid in distinct_ids:
        sync_picture_sidecar(server, pid)


def _serialize(s: TagSuggestion) -> dict:
    return {
        "id": s.id,
        "picture_id": s.picture_id,
        "tag": s.tag,
        "direction": s.direction,
        "source": s.source,
        "score": s.score,
        "reason": s.reason,
        "twin_picture_id": s.twin_picture_id,
        "twin_sim": s.twin_sim,
        "model_version": s.model_version,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# Stand-in for a `reason` string that names an out-of-scope twin. The scan writes
# the twin's id and similarity (or dhash distance) straight into `reason` - see
# `tag_scan_service._scan`'s "near-twin {id} (sim …)" / "near-duplicate twin {id}
# (dhash hamming …)" strings - so the free-text field is a twin attribute too, and
# redacting only the structured `twin_*` fields would leave the id in plain sight.
REDACTED_TWIN_REASON = "a near-twin outside this share disagrees with this label"

# Every attribute of the *twin* picture this payload carries. Enumerated (rather
# than pattern-matched on a `twin_` prefix) so that adding a twin-derived field
# without adding it here is a visible omission in review. `reason` is handled
# separately because it is redacted to a replacement string, not to None.
TWIN_DERIVED_FIELDS = (
    "twin_picture_id",
    "twin_sim",
    "twin_ext",
    "twin_tagger_confidence",
)


def _redact_out_of_scope_twin(item: dict, scope_ids: set[int] | None) -> dict:
    """Strip every twin-picture attribute when the twin is outside the token scope.

    The list filter (``_resolve_review_picture_ids``) constrains
    ``TagSuggestion.picture_id`` - the *suspect* - only. A suggestion is a pair,
    and the twin routinely sits outside a share token's grant, so serving the row
    unmodified discloses the id, existence, file type, perceptual similarity and
    model confidence of a picture the token may not see; iterating tags would
    enumerate picture ids across the vault. This is the same reasoning that made
    ``GET /reviews/{id}/suggestions`` owner-only (``routes/reviews.py``), applied
    here rather than left at the module seam.

    Args:
        item: A serialized suggestion dict (mutated in place and returned).
        scope_ids: Picture ids the **token** may see, from
            ``fetch_scope_allowed_picture_ids``. ``None`` (owner/unscoped) is a
            no-op - note this must be the raw token scope, never the
            user-filter intersection, or an owner narrowing the queue by
            project/set would have their own twins redacted.

    Returns:
        The same dict. Rows with no twin, and every row for an unscoped caller,
        are returned untouched.
    """
    if scope_ids is None:
        return item
    twin_id = item.get("twin_picture_id")
    if twin_id is None or int(twin_id) in scope_ids:
        return item
    for field in TWIN_DERIVED_FIELDS:
        item[field] = None
    if item.get("reason"):
        item["reason"] = REDACTED_TWIN_REASON
    return item


def _resolve_review_picture_ids(
    server,
    request: Request,
    project_id: int | None,
    set_id: int | None,
    character_id: str | None,
) -> set[int] | None:
    """Resolve the review scope filters into a final in-scope set of suspect picture ids.

    Two independent narrowings are combined, both keyed on the suspect
    ``TagSuggestion.picture_id``:

    * **Token scope** (``fetch_scope_allowed_picture_ids``): for a scoped/READ
      share token this is the set of pictures the token may see; for an
      owner/unscoped token it is ``None`` (no restriction). This is the
      authorization gate - it must never be widened by the user-supplied filters,
      so it is always intersected in. These review endpoints are reachable by
      scoped READ tokens (GET is not in ``READ_BLOCKED_GET_PATHS``), so without
      this intersection a scoped token would read the whole library's queue.
    * **User filters** (``fetch_tag_review_scope_picture_ids``): the optional
      project / set / character narrowing the reviewer asked for, ``None`` when no
      filter was supplied.

    The result is the intersection of whichever of the two are present:

    * both ``None`` → ``None`` (owner, no filter → unrestricted, as today);
    * only the filter → the filter set (owner narrowing the queue);
    * only the scope → the scope set (scoped token, no filter → its pictures only);
    * both → ``scope & filter`` (a scoped token can only ever narrow further, never
      escape its scope).

    An empty set is a valid, non-error result meaning "no in-scope suspects".
    """
    filter_ids = tag_suggestion_service.resolve_filter_picture_ids(
        server.vault,
        project_id=project_id,
        set_id=set_id,
        character_id=character_id,
    )
    scope_ids = fetch_scope_allowed_picture_ids(server, request)

    if scope_ids is None:
        return filter_ids
    if filter_ids is None:
        return scope_ids
    return scope_ids & filter_ids


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/tag_suggestions",
        summary="List ranked tag-fix suggestions",
        description=(
            "Returns suspected label fixes (the dataset-refinement queue), highest "
            "score first. Filter by ``tag`` (review one tag at a time), ``direction`` "
            "(``add`` / ``remove``), and ``status`` (default ``PENDING``). Optionally "
            "narrow the queue to a ``project_id``, ``set_id``, and/or ``character_id`` "
            "(numeric id, or the literal ``UNASSIGNED``); these AND together and match "
            "the suspect picture only."
        ),
        response_model=list[TagSuggestionItemResponse],
    )
    def list_tag_suggestions(
        request: Request,
        tag: str | None = None,
        direction: str | None = None,
        status: str = "PENDING",
        limit: int = 100,
        offset: int = 0,
        project_id: int | None = None,
        set_id: int | None = None,
        character_id: str | None = None,
    ):
        if direction is not None and direction not in ("add", "remove"):
            raise HTTPException(
                status_code=400, detail="direction must be 'add' or 'remove'"
            )
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        picture_ids = _resolve_review_picture_ids(
            server, request, project_id, set_id, character_id
        )
        suggestions = tag_suggestion_service.list_suggestions(
            server.vault,
            tag=tag,
            direction=direction,
            status=status,
            limit=limit,
            offset=offset,
            picture_ids=picture_ids,
        )
        ids: list[int | None] = []
        for s in suggestions:
            ids.append(s.picture_id)
            ids.append(s.twin_picture_id)
        exts = tag_suggestion_service.get_picture_exts(server.vault, ids)
        pairs: list[tuple[int, str]] = []
        for s in suggestions:
            pairs.append((s.picture_id, s.tag))
            if s.twin_picture_id is not None:
                pairs.append((s.twin_picture_id, s.tag))
        confs = tag_suggestion_service.get_tagger_confidences(server.vault, pairs)
        # The token's own scope, NOT `picture_ids` (which also folds in the
        # caller's project/set/character narrowing) - an owner filtering the queue
        # must still see their twins.
        token_scope_ids = fetch_scope_allowed_picture_ids(server, request)
        out = []
        for s in suggestions:
            item = _serialize(s)
            item["picture_ext"] = exts.get(s.picture_id, "")
            item["twin_ext"] = exts.get(s.twin_picture_id, "")
            item["tagger_confidence"] = confs.get((s.picture_id, s.tag))
            item["twin_tagger_confidence"] = confs.get((s.twin_picture_id, s.tag))
            out.append(_redact_out_of_scope_twin(item, token_scope_ids))
        return out

    @router.post(
        "/tag_suggestions/{suggestion_id}/accept",
        summary="Accept a tag-fix suggestion",
        description=(
            "Applies the fix to the Tag table (remove → deletes the tag, add → creates "
            "it), marks the suggestion ACCEPTED, and emits a CHANGED_TAGS event."
        ),
        response_model=ReviewSuggestionResponse,
    )
    def accept_tag_suggestion(suggestion_id: int, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            result = tag_suggestion_service.accept_suggestion(
                server.vault, suggestion_id
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        except tag_suggestion_service.SuggestionConflictError as exc:
            # The suspect picture was soft-deleted, or a human ledger label now
            # contradicts the suggestion's direction - refuse rather than write a
            # stale/harmful edit (see accept_suggestion's guards).
            raise HTTPException(status_code=409, detail=str(exc))
        pic_id = result["picture_id"]
        server.vault.notify(
            EventType.CHANGED_TAGS,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        sync_picture_sidecar(server, pic_id)
        return {"status": "accepted", **result}

    @router.post(
        "/tag_suggestions/{suggestion_id}/reopen",
        summary="Reopen (undo) a reviewed suggestion",
        description=(
            "Sets the suggestion back to PENDING and reverses any label change it made "
            "(re-adds a removed tag / removes an added tag). Emits a CHANGED_TAGS event."
        ),
        response_model=ReviewSuggestionResponse,
    )
    def reopen_tag_suggestion(suggestion_id: int, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            result = tag_suggestion_service.reopen_suggestion(
                server.vault, suggestion_id
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        # Undo may have changed either the suspect or the twin - refresh both.
        _notify_changed(
            server,
            [result["picture_id"], result.get("twin_picture_id")],
            origin_client_id,
        )
        return {"status": "reopened", **result}

    @router.post(
        "/tag_suggestions/{suggestion_id}/fix-twin",
        summary="Resolve a suggestion in the twin's favour",
        description=(
            "Keeps the suspect's label and flips the near-twin to match it: for a "
            "'remove' suggestion the untagged twin gets the tag (it has the anomaly "
            "too); for an 'add' suggestion the tagged twin loses it. Marks the "
            "suggestion TWIN_FIXED (undoable via reopen)."
        ),
        response_model=ReviewSuggestionResponse,
    )
    def fix_twin_tag_suggestion(suggestion_id: int, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            result = tag_suggestion_service.fix_twin_suggestion(
                server.vault, suggestion_id
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Suggestion has no twin")
        _notify_changed(server, [result.get("twin_picture_id")], origin_client_id)
        return {"status": "twin_fixed", **result}

    @router.post(
        "/tag_suggestions/{suggestion_id}/swap",
        summary="Swap a pair's labels (both were wrong, opposite ways)",
        description=(
            "The tagged image is actually clean and the untagged twin actually has the "
            "tag - untag the former and tag the latter. Marks the suggestion SWAPPED "
            "(undoable via reopen)."
        ),
        response_model=ReviewSuggestionResponse,
    )
    def swap_tag_suggestion(suggestion_id: int, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            result = tag_suggestion_service.swap_suggestion(server.vault, suggestion_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Suggestion has no twin")
        _notify_changed(
            server,
            [result["picture_id"], result.get("twin_picture_id")],
            origin_client_id,
        )
        return {"status": "swapped", **result}

    @router.post(
        "/tag_suggestions/scan",
        summary="Scan a tag for near-neighbour label disagreements",
        description=(
            "Runs the near-neighbour scan for the tag and rebuilds its PENDING "
            "suggestions (reviewed rows are kept). Synchronous - fast on a typical vault."
        ),
        response_model=ScanResultResponse,
    )
    def scan_tag_suggestions(payload: ScanRequest):
        tag = (payload.tag or "").strip()
        if not tag:
            raise HTTPException(status_code=400, detail="tag is required")
        return tag_scan_service.scan_tag(
            server.vault, tag, project=payload.project or None
        )

    @router.post(
        "/tag_suggestions/bulk-accept",
        summary="Resolve all confident suggestions for a tag",
        description=(
            "Accepts every PENDING suggestion for the tag where the model-independent "
            "near-neighbour vote and the tagger agree on the fix and both clear "
            "min_combined, applying each one's fix. Pass dry_run=true to only count "
            "what would be resolved. Optionally narrow to a ``project_id``, ``set_id``, "
            "and/or ``character_id`` (numeric id, or the literal ``UNASSIGNED``); these "
            "AND together, match the suspect picture only, and bind both the dry-run "
            "count and the apply so out-of-scope suggestions are never bulk-resolved."
        ),
        response_model=BulkResultResponse,
    )
    def bulk_accept_tag_suggestions(payload: BulkAcceptRequest, request: Request):
        if payload.direction is not None and payload.direction not in ("add", "remove"):
            raise HTTPException(
                status_code=400, detail="direction must be 'add' or 'remove'"
            )
        min_combined = max(0.0, min(payload.min_combined, 1.0))
        picture_ids = _resolve_review_picture_ids(
            server,
            request,
            payload.project_id,
            payload.set_id,
            payload.character_id,
        )
        result = tag_suggestion_service.bulk_accept(
            server.vault,
            payload.tag,
            min_combined,
            payload.direction,
            payload.dry_run,
            picture_ids=picture_ids,
            review_id=payload.review_id,
        )
        if payload.dry_run and result.get("sample"):
            ids: list[int | None] = []
            for s in result["sample"]:
                ids.append(s["picture_id"])
                ids.append(s.get("twin_picture_id"))
            exts = tag_suggestion_service.get_picture_exts(server.vault, ids)
            # Same twin-disclosure rule as the GET listing. Unreachable by a
            # scoped token today (POST + a READ token is refused by the auth
            # middleware, and "scoped" implies "READ" since ALL+resource_type is
            # refused at mint), but that equivalence is load-bearing and lives two
            # modules away - redact here so this payload is safe on its own terms.
            token_scope_ids = fetch_scope_allowed_picture_ids(server, request)
            for s in result["sample"]:
                s["picture_ext"] = exts.get(s["picture_id"], "")
                s["twin_ext"] = exts.get(s.get("twin_picture_id"), "")
                _redact_out_of_scope_twin(s, token_scope_ids)
        if not payload.dry_run:
            origin_client_id = getattr(request.state, "origin_client_id", None)
            _notify_changed(server, result["picture_ids"], origin_client_id)
        return result

    @router.post(
        "/tag_suggestions/bulk-reopen",
        summary="Batch-undo a bulk accept",
        description="Reopens the given suggestion ids and reverses their label changes.",
        response_model=BulkResultResponse,
    )
    def bulk_reopen_tag_suggestions(payload: BulkReopenRequest, request: Request):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        result = tag_suggestion_service.bulk_reopen(
            server.vault, payload.ids, review_id=payload.review_id
        )
        _notify_changed(server, result["picture_ids"], origin_client_id)
        return result

    @router.post(
        "/tag_suggestions/{suggestion_id}/skip",
        summary="Skip a tag-fix suggestion (no decision)",
        description=(
            "The reviewer cannot decide: marks the suggestion SKIPPED and "
            "removes it from the queue with no decision recorded - the Tag "
            "table and the human-label ledger are untouched (unlike dismiss, "
            "which affirms the current label). Reopen re-pends it."
        ),
        response_model=ReviewSuggestionResponse,
    )
    def skip_tag_suggestion(suggestion_id: int):
        try:
            result = tag_suggestion_service.skip_suggestion(server.vault, suggestion_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        return {"status": "skipped", **result}

    @router.post(
        "/tag_suggestions/{suggestion_id}/dismiss",
        summary="Dismiss a tag-fix suggestion",
        description="Marks the suggestion DISMISSED. Does not modify the Tag table.",
        response_model=ReviewSuggestionResponse,
    )
    def dismiss_tag_suggestion(suggestion_id: int):
        try:
            result = tag_suggestion_service.dismiss_suggestion(
                server.vault, suggestion_id
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        return {"status": "dismissed", **result}

    return router
