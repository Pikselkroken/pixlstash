"""HTTP routes for review sessions (the tag-review workflow's first-class noun).

Create runs the near-neighbour scan once into a new review; refresh appends
(never rebuilds); archive/abort close the session without touching suggestion
rows (per-item decisions were written through as they were made). The per-item
actions themselves stay on the ``/tag_suggestions`` routes.

See :mod:`pixlstash.services.review_service` for semantics.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from pixlstash.pixl_logging import get_logger
from pixlstash.services import review_service
from pixlstash.services.review_service import ReviewConflictError, ReviewLockedError
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids

logger = get_logger(__name__)


class CreateReviewRequest(BaseModel):
    """Create a review: one tag + an optional scope, frozen at creation."""

    tag: str
    project_id: Optional[int] = None
    set_id: Optional[int] = None
    character_id: Optional[str] = None
    # Re-surface suspects already decided in earlier reviews (default off -
    # same protection as the old permanent suppression, now a visible choice).
    include_reviewed: bool = False


class ReviewStatsResponse(BaseModel):
    """The review's scan receipt."""

    model_config = ConfigDict(extra="allow")

    scanned: int
    found: int
    prev_reviewed: int
    # Present on create/detail: how many PENDING rows bulk auto-resolve would
    # apply at the default threshold (the "N obvious pairs - auto-resolve?" count).
    auto_resolvable: Optional[int] = None


class ReviewProgressResponse(BaseModel):
    """Row counts for one review: decided / pending / skipped / locked."""

    done: int = 0
    pending: int = 0
    skipped: int = 0
    # Still-undecided rows withheld from the queue because a locked picture set
    # froze their suspect (locking can happen after the scan). They are never
    # served as cards and are excluded from ``pending``, so a review whose
    # pictures were locked mid-session still reaches ``pending == 0`` (complete)
    # instead of appearing stuck. Non-zero means "N suspects are frozen, not
    # lost" - unlocking the set returns them to ``pending``. Defaults to 0 for
    # reviews closed before this count existed.
    locked: int = 0


class ReviewReceiptResponse(BaseModel):
    """Outcome receipt over the review's resolved rows."""

    removed: int = 0
    added: int = 0
    kept: int = 0
    skipped: int = 0


class ReviewResponse(BaseModel):
    """One review session."""

    model_config = ConfigDict(extra="allow")

    id: int
    tag: str
    scope: dict
    status: str
    stats: ReviewStatsResponse
    created_at: Optional[str] = None
    refreshed_at: Optional[str] = None
    progress: Optional[ReviewProgressResponse] = None
    stale: Optional[bool] = None
    # Present on the detail endpoint only.
    receipt: Optional[ReviewReceiptResponse] = None


class ReviewPreviewResponse(BaseModel):
    """Pre-creation coverage counts for the New-review dialog."""

    model_config = ConfigDict(extra="allow")

    in_scope: int
    prev_reviewed: int


class RefreshReviewResponse(BaseModel):
    """Result of an append-only re-scan."""

    model_config = ConfigDict(extra="allow")

    new_count: int
    found: int
    refreshed_at: Optional[str] = None


class DeleteReviewsResponse(BaseModel):
    """How many review sessions a delete removed (1 for a single delete)."""

    deleted: int


class ReviewSuggestionItemResponse(BaseModel):
    """One card in a review's ranked queue."""

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
    review_id: Optional[int] = None
    # "pair" when suspect and twin are versions of one shot (same stack or
    # dhash-near); else "binary". Derived at read time. Forced to "binary"
    # when ``twin_locked`` is true: a frozen twin cannot be written, so the
    # pair-only corners (swap / fix-twin) would only 423.
    kind: str = "binary"
    # Per-side lock state - deliberately NOT a single card-level flag, because
    # the two sides gate different actions. The twin's lock blocks fix-twin and
    # swap; accept and dismiss only ever write the suspect, so they stay valid.
    # ``locked`` is the suspect's: always false today (locked suspects are
    # filtered out of the queue), emitted so a future selection gap shows up as
    # a labelled card rather than an un-actionable one.
    locked: bool = False
    twin_locked: bool = False
    # The locked sets freezing each side, for the "…is in the locked set 'X'"
    # copy: [{"id": int, "name": str}, ...], deduplicated and sorted by set id.
    # Empty when that side is not frozen.
    locked_sets: list[dict] = []
    twin_locked_sets: list[dict] = []
    # Scan-time neighbourhood evidence: [{"picture_id": int, "has": bool}, ...]
    # most-similar first; null for legacy rows scanned before capture existed.
    neighbors: Optional[list[dict]] = None
    picture_ext: Optional[str] = None
    twin_ext: Optional[str] = None
    # Tagger confidence for the suspect / twin (frontend card reads these names).
    confidence: Optional[float] = None
    twin_confidence: Optional[float] = None


def _token_scope_ids(server, request: Request):
    """Allowed picture ids for a scoped token; None = unrestricted (owner)."""
    return fetch_scope_allowed_picture_ids(server, request)


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/reviews",
        summary="Create a review session for one tag",
        description=(
            "Creates a review (one tag + optional frozen project/set/character "
            "scope), runs the near-neighbour scan once into it, and returns the "
            "receipt: pictures scanned, suspects found, suspects handled in "
            "earlier reviews, and how many the bulk auto-resolve would settle. "
            "``include_reviewed=true`` re-surfaces previously-decided suspects "
            "into this review. 409 when the tag already has an OPEN review."
        ),
        response_model=ReviewResponse,
    )
    def create_review(payload: CreateReviewRequest, request: Request):
        # Reviews are an owner-only, vault-wide curation surface: creating one
        # runs a cross-vault scan and reveals aggregate counts. A resource-scoped
        # share token is READ-only and must not open one (same policy as the
        # /reviews reads).
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        tag = (payload.tag or "").strip()
        if not tag:
            raise HTTPException(status_code=400, detail="tag is required")
        try:
            return review_service.create_review(
                server.vault,
                tag,
                project_id=payload.project_id,
                set_id=payload.set_id,
                character_id=payload.character_id,
                include_reviewed=payload.include_reviewed,
            )
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ReviewLockedError as exc:
            # Backstop: the scoped set is locked, so it can't be a review scope
            # (the UI already greys it out). 423 mirrors the mutation guards.
            raise HTTPException(status_code=423, detail=str(exc))

    @router.get(
        "/reviews",
        summary="List review sessions",
        description=(
            "Returns reviews newest-first with per-review progress "
            "(done/pending suggestion rows) and a ``stale`` flag (the vault "
            "gained pictures or a tagger run after the review's last scan). "
            "Filter with ``status`` = OPEN | ARCHIVED | ABORTED."
        ),
        response_model=list[ReviewResponse],
    )
    def list_reviews(request: Request, status: str | None = None):
        if status and status.upper() not in review_service.VALID_STATUSES:
            raise HTTPException(
                status_code=400, detail="status must be OPEN, ARCHIVED or ABORTED"
            )
        # Reviews are a vault-wide curation surface; scoped share tokens have
        # no business enumerating them (their scope cannot be applied to
        # cross-vault aggregates without leaking counts).
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        return review_service.list_reviews(server.vault, status=status)

    @router.delete(
        "/reviews",
        summary="Bulk-delete review sessions by status (clear all archived)",
        description=(
            "Deletes every review in the given ``status`` and returns the "
            "``deleted`` count. ``status`` is **required** and must be "
            "``ARCHIVED`` - the 'clear all archived' action; there is no "
            "delete-everything default. Suggestion rows are detached "
            "(``review_id`` cleared), never destroyed, so per-item decisions and "
            "the no-resurrection guarantee stand. Owner-only."
        ),
        response_model=DeleteReviewsResponse,
    )
    def clear_reviews(request: Request, status: str):
        # Owner-only surface (see the /reviews reads): a scoped share token is
        # READ-only and must not delete review sessions. Check BEFORE any DB read.
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        # Bulk delete only ever clears ARCHIVED reviews. Anything else (a missing
        # or OPEN/ABORTED status) is refused so this endpoint can never wipe open
        # or aborted sessions.
        if (status or "").upper() != review_service.ARCHIVED:
            raise HTTPException(
                status_code=400, detail="status=ARCHIVED is required for bulk delete"
            )
        deleted = review_service.clear_reviews(server.vault, review_service.ARCHIVED)
        return {"deleted": deleted}

    # NOTE: must be registered before /reviews/{review_id} so the literal
    # "preview" segment is not captured by the int path parameter.
    @router.get(
        "/reviews/preview",
        summary="Preview a review's coverage before creating it",
        description=(
            "Returns how many pictures the given tag+scope would scan "
            "(``in_scope``) and how many of the tag's suspects were already "
            "decided in earlier reviews (``prev_reviewed`` - the count the "
            "'include previously reviewed' toggle re-surfaces)."
        ),
        response_model=ReviewPreviewResponse,
    )
    def preview_review(
        request: Request,
        tag: str,
        project_id: int | None = None,
        set_id: int | None = None,
        character_id: str | None = None,
    ):
        # Owner-only surface (see create_review / the /reviews reads): a scoped
        # share token cannot preview a vault-wide coverage count.
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        tag = (tag or "").strip()
        if not tag:
            raise HTTPException(status_code=400, detail="tag is required")
        try:
            return review_service.preview_review(
                server.vault,
                tag,
                project_id=project_id,
                set_id=set_id,
                character_id=character_id,
            )
        except ReviewLockedError as exc:
            # Backstop: a locked set can't be a review scope (UI greys it out).
            raise HTTPException(status_code=423, detail=str(exc))

    @router.get(
        "/reviews/{review_id}",
        summary="Get one review's detail",
        description=(
            "The review incl. its scan receipt stats (with the live "
            "``auto_resolvable`` count), progress, and staleness."
        ),
        response_model=ReviewResponse,
    )
    def get_review(review_id: int, request: Request):
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        try:
            return review_service.get_review(server.vault, review_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Review not found")

    @router.delete(
        "/reviews/{review_id}",
        summary="Delete one review session",
        description=(
            "Deletes the review (any status). Its suggestion rows are detached "
            "(``review_id`` cleared), not destroyed: a review is an audit receipt "
            "over per-item decisions already written through to the tags/label "
            "ledger, so deleting it never resurrects or alters the underlying "
            "labels. 404 if the review does not exist. Owner-only."
        ),
        response_model=DeleteReviewsResponse,
    )
    def delete_review(review_id: int, request: Request):
        # Owner-only surface (see the /reviews reads): a scoped share token is
        # READ-only and must not delete a review session. Check BEFORE any DB read.
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        try:
            review_service.delete_review(server.vault, review_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Review not found")
        return {"deleted": 1}

    @router.post(
        "/reviews/{review_id}/refresh",
        summary="Re-scan a review append-only",
        description=(
            "Re-runs the near-neighbour scan for the review's tag and frozen "
            "scope. New suspects are appended; the review's decided rows are "
            "never resurrected. Updates ``refreshed_at`` and ``found``."
        ),
        response_model=RefreshReviewResponse,
    )
    def refresh_review(review_id: int, request: Request):
        # Owner-only surface (see create_review / the /reviews reads): a scoped
        # share token is READ-only and must not mutate a review's queue.
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        try:
            return review_service.refresh_review(
                server.vault,
                review_id,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Review not found")
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.post(
        "/reviews/{review_id}/archive",
        summary="Archive a review (completed)",
        description=(
            "Marks the review ARCHIVED. Suggestion rows are untouched - "
            "decisions were written through per item. Idempotent."
        ),
        response_model=ReviewResponse,
    )
    def archive_review(review_id: int, request: Request):
        # Owner-only surface (see the /reviews reads): a scoped share token is
        # READ-only and must not close a review session.
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        return _close(review_id, review_service.ARCHIVED)

    @router.post(
        "/reviews/{review_id}/abort",
        summary="Abort a review (discard the session)",
        description=(
            "Marks the review ABORTED. Already-made decisions stand; pending "
            "rows stay parented to the closed review as its record. Idempotent."
        ),
        response_model=ReviewResponse,
    )
    def abort_review(review_id: int, request: Request):
        # Owner-only surface (see the /reviews reads): a scoped share token is
        # READ-only and must not close a review session.
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        return _close(review_id, review_service.ABORTED)

    def _close(review_id: int, status: str):
        try:
            return review_service.set_review_status(server.vault, review_id, status)
        except KeyError:
            raise HTTPException(status_code=404, detail="Review not found")
        except ReviewConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @router.get(
        "/reviews/{review_id}/suggestions",
        summary="List a review's ranked queue",
        description=(
            "The review's suggestion cards, highest score first: direction, "
            "score, kind (binary / pair), the scan-time neighbourhood evidence, "
            "twin info, and the tagger's confidences. Filter with ``status`` "
            "(default PENDING; empty = all)."
        ),
        response_model=list[ReviewSuggestionItemResponse],
    )
    def list_review_suggestions(
        review_id: int,
        request: Request,
        status: str = "PENDING",
        limit: int = 100,
        offset: int = 0,
    ):
        # Reviews are an owner-only curation surface (like /reviews and
        # /reviews/{id}). A scoped share token must not read this queue: the
        # cards expose twin + up-to-k neighbour picture ids and per-picture
        # tag bits that routinely fall outside the token's share scope.
        if _token_scope_ids(server, request) is not None:
            raise HTTPException(status_code=403, detail="Not available to this token")
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        try:
            return review_service.list_review_suggestions(
                server.vault,
                review_id,
                status=status,
                limit=limit,
                offset=offset,
                picture_ids=None,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Review not found")

    return router
