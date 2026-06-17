"""HTTP routes for the tag-suggestion review queue (dataset refinement).

Exposes the ranked queue of suspected label fixes and the accept/dismiss actions that
close the loop. Accepting writes through to the Tag table and emits a CHANGED_TAGS
event so open clients refresh; dismissing only updates the suggestion's status.

See :mod:`pixlstash.services.tag_suggestion_service` for the writeback semantics.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services import tag_suggestion_service
from pixlstash.utils.service.caption_utils import sync_picture_sidecar

logger = get_logger(__name__)


class TagSuggestionItemResponse(BaseModel):
    """A single suspected label fix in the review queue."""

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
    # The tagger's most recent raw confidence for this (picture, tag), if predicted —
    # for the suspect and (so the reviewer can weigh both sides) its twin.
    tagger_confidence: Optional[float] = None
    twin_tagger_confidence: Optional[float] = None


class TagSuggestionSummaryItem(BaseModel):
    """Pending-suggestion counts for one tag."""

    model_config = ConfigDict(extra="allow")

    tag: str
    add: int = 0
    remove: int = 0
    total: int = 0


class ReviewSuggestionResponse(BaseModel):
    """Result of accepting or dismissing a suggestion."""

    model_config = ConfigDict(extra="allow")

    status: str
    picture_id: int
    tag: str
    direction: str


class BulkAcceptRequest(BaseModel):
    """Resolve all confident PENDING suggestions for a tag (blended score ≥ threshold)."""

    tag: str
    min_combined: float = 0.9
    direction: Optional[str] = None
    dry_run: bool = False


class BulkReopenRequest(BaseModel):
    """Batch-undo: reopen the given suggestion ids."""

    ids: list[int] = []


class BulkResultResponse(BaseModel):
    """Result of a bulk accept/reopen."""

    model_config = ConfigDict(extra="allow")

    count: int
    accepted_ids: list[int] = []
    picture_ids: list[int] = []
    sample: list[dict] = []


def _notify_changed(server, picture_ids) -> None:
    """Emit CHANGED_TAGS + sync the sidecar for each distinct, non-null picture id."""
    seen = set()
    for pid in picture_ids:
        if pid is None or pid in seen:
            continue
        seen.add(pid)
        server.vault.notify(
            EventType.CHANGED_TAGS,
            {"picture_ids": [pid], "change_kind": "updated"},
        )
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


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/tag_suggestions",
        summary="List ranked tag-fix suggestions",
        description=(
            "Returns suspected label fixes (the dataset-refinement queue), highest "
            "score first. Filter by ``tag`` (review one tag at a time), ``direction`` "
            "(``add`` / ``remove``), and ``status`` (default ``PENDING``)."
        ),
        response_model=list[TagSuggestionItemResponse],
    )
    def list_tag_suggestions(
        tag: str | None = None,
        direction: str | None = None,
        status: str = "PENDING",
        limit: int = 100,
        offset: int = 0,
    ):
        if direction is not None and direction not in ("add", "remove"):
            raise HTTPException(
                status_code=400, detail="direction must be 'add' or 'remove'"
            )
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        suggestions = tag_suggestion_service.list_suggestions(
            server.vault,
            tag=tag,
            direction=direction,
            status=status,
            limit=limit,
            offset=offset,
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
        out = []
        for s in suggestions:
            item = _serialize(s)
            item["picture_ext"] = exts.get(s.picture_id, "")
            item["twin_ext"] = exts.get(s.twin_picture_id, "")
            item["tagger_confidence"] = confs.get((s.picture_id, s.tag))
            item["twin_tagger_confidence"] = confs.get((s.twin_picture_id, s.tag))
            out.append(item)
        return out

    @router.get(
        "/tag_suggestions/summary",
        summary="Per-tag suggestion counts",
        description=(
            "Returns pending-suggestion counts grouped by tag (with add/remove "
            "breakdown), busiest tag first. Drives the queue's tag picker and progress."
        ),
        response_model=list[TagSuggestionSummaryItem],
    )
    def tag_suggestions_summary(status: str = "PENDING"):
        return tag_suggestion_service.summary_by_tag(server.vault, status=status)

    @router.post(
        "/tag_suggestions/{suggestion_id}/accept",
        summary="Accept a tag-fix suggestion",
        description=(
            "Applies the fix to the Tag table (remove → deletes the tag, add → creates "
            "it), marks the suggestion ACCEPTED, and emits a CHANGED_TAGS event."
        ),
        response_model=ReviewSuggestionResponse,
    )
    def accept_tag_suggestion(suggestion_id: int):
        try:
            result = tag_suggestion_service.accept_suggestion(
                server.vault, suggestion_id
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        pic_id = result["picture_id"]
        server.vault.notify(
            EventType.CHANGED_TAGS,
            {"picture_ids": [pic_id], "change_kind": "updated"},
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
    def reopen_tag_suggestion(suggestion_id: int):
        try:
            result = tag_suggestion_service.reopen_suggestion(
                server.vault, suggestion_id
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        # Undo may have changed either the suspect or the twin — refresh both.
        _notify_changed(
            server, [result["picture_id"], result.get("twin_picture_id")]
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
    def fix_twin_tag_suggestion(suggestion_id: int):
        try:
            result = tag_suggestion_service.fix_twin_suggestion(
                server.vault, suggestion_id
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Suggestion has no twin")
        _notify_changed(server, [result.get("twin_picture_id")])
        return {"status": "twin_fixed", **result}

    @router.post(
        "/tag_suggestions/{suggestion_id}/swap",
        summary="Swap a pair's labels (both were wrong, opposite ways)",
        description=(
            "The tagged image is actually clean and the untagged twin actually has the "
            "tag — untag the former and tag the latter. Marks the suggestion SWAPPED "
            "(undoable via reopen)."
        ),
        response_model=ReviewSuggestionResponse,
    )
    def swap_tag_suggestion(suggestion_id: int):
        try:
            result = tag_suggestion_service.swap_suggestion(
                server.vault, suggestion_id
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        except ValueError:
            raise HTTPException(status_code=400, detail="Suggestion has no twin")
        _notify_changed(
            server, [result["picture_id"], result.get("twin_picture_id")]
        )
        return {"status": "swapped", **result}

    @router.post(
        "/tag_suggestions/bulk-accept",
        summary="Resolve all confident suggestions for a tag",
        description=(
            "Accepts every PENDING suggestion for the tag whose blended (neighbour + "
            "tagger) score is at least min_combined, applying each one's fix. Pass "
            "dry_run=true to only count what would be resolved."
        ),
        response_model=BulkResultResponse,
    )
    def bulk_accept_tag_suggestions(payload: BulkAcceptRequest):
        if payload.direction is not None and payload.direction not in ("add", "remove"):
            raise HTTPException(
                status_code=400, detail="direction must be 'add' or 'remove'"
            )
        min_combined = max(0.0, min(payload.min_combined, 1.0))
        result = tag_suggestion_service.bulk_accept(
            server.vault,
            payload.tag,
            min_combined,
            payload.direction,
            payload.dry_run,
        )
        if payload.dry_run and result.get("sample"):
            ids: list[int | None] = []
            for s in result["sample"]:
                ids.append(s["picture_id"])
                ids.append(s.get("twin_picture_id"))
            exts = tag_suggestion_service.get_picture_exts(server.vault, ids)
            for s in result["sample"]:
                s["picture_ext"] = exts.get(s["picture_id"], "")
                s["twin_ext"] = exts.get(s.get("twin_picture_id"), "")
        if not payload.dry_run:
            _notify_changed(server, result["picture_ids"])
        return result

    @router.post(
        "/tag_suggestions/bulk-reopen",
        summary="Batch-undo a bulk accept",
        description="Reopens the given suggestion ids and reverses their label changes.",
        response_model=BulkResultResponse,
    )
    def bulk_reopen_tag_suggestions(payload: BulkReopenRequest):
        result = tag_suggestion_service.bulk_reopen(server.vault, payload.ids)
        _notify_changed(server, result["picture_ids"])
        return result

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
