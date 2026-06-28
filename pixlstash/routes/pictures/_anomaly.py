"""Grad-CAM anomaly-region endpoint for the review "zoom to the anomaly" hint.

``GET /pictures/{id}/anomaly_region?tag=<tag>`` runs the PixlStash anomaly
tagger's Grad-CAM localiser for a single picture + anomaly label and returns an
APPROXIMATE bounding box plus a colourised heatmap overlay. Boxes are a UI hint,
not precise detection.

Object scope: this is a per-object data endpoint, so it is in scope state (a) —
it calls the ``enforce_picture_scope`` chokepoint (in ``_helpers.py``)
immediately after parsing the id and before any DB read, image load, branch, or
return, exactly like ``get_picture`` / ``get_picture_metadata`` /
``get_picture_field`` in ``_crud.py``.
"""

import os
import threading
from collections import OrderedDict
from typing import Optional

from fastapi import HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.pixlstash_tagger import UnknownAnomalyLabel
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.caption_utils import sanitise_tag

from ._helpers import enforce_picture_scope

from PIL import Image, UnidentifiedImageError


logger = get_logger(__name__)

# Bounded in-process LRU cache for computed regions, keyed by
# (picture_id, sanitised tag, tagger version). A full Grad-CAM pass per request
# is wasteful; the result is deterministic for a given picture+tag+model, so it
# is cheap to memoise. lru_cache does not fit (the image bytes are not part of
# the key and are not hashable), so a small OrderedDict LRU is used instead.
_ANOMALY_REGION_CACHE_MAX = 512
_anomaly_region_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_anomaly_region_cache_lock = threading.Lock()


def _cache_get(key: tuple) -> Optional[dict]:
    with _anomaly_region_cache_lock:
        value = _anomaly_region_cache.get(key)
        if value is not None:
            _anomaly_region_cache.move_to_end(key)
        return value


def _cache_put(key: tuple, value: dict) -> None:
    with _anomaly_region_cache_lock:
        _anomaly_region_cache[key] = value
        _anomaly_region_cache.move_to_end(key)
        while len(_anomaly_region_cache) > _ANOMALY_REGION_CACHE_MAX:
            _anomaly_region_cache.popitem(last=False)


class AnomalyRegionResponse(BaseModel):
    """Approximate localisation of an anomaly tag within a picture."""

    model_config = ConfigDict(
        extra="allow",
        json_schema_extra={
            "example": {
                "picture_id": 123,
                "tag": "malformed hand",
                "boxes": [[0.28, 0.19, 0.29, 0.31], [0.62, 0.40, 0.20, 0.26]],
                "diffuse": False,
                "heatmap": "data:image/png;base64,iVBORw0KGgo...",
            }
        },
    )

    picture_id: int = Field(..., description="The picture this region belongs to.")
    tag: str = Field(..., description="The anomaly tag that was localised.")
    boxes: list[list[float]] = Field(
        default_factory=list,
        description=(
            "Approximate regions as normalised `[x, y, w, h]` in 0..1, one per "
            "connected hot area (largest first). Empty when the anomaly is "
            "diffuse (no meaningful localised region)."
        ),
    )
    diffuse: bool = Field(
        ...,
        description=(
            "True when the activation is too spread out to localise (a global "
            "or surface defect); `box` and `heatmap` are then null."
        ),
    )
    heatmap: Optional[str] = Field(
        None,
        description=(
            "RGBA PNG overlay as a `data:` URI (warm colour, alpha scales with "
            "intensity) to blend over the image, or null when diffuse."
        ),
    )


def register_routes(router, server):
    @router.get(
        "/pictures/{id}/anomaly_region",
        summary="Locate an anomaly region",
        description=(
            "Runs the PixlStash anomaly tagger's Grad-CAM localiser for the "
            "given anomaly `tag` and returns an APPROXIMATE bounding box plus a "
            "colourised heatmap overlay, powering the review 'zoom to the "
            "anomaly' hint. The box is a UI hint, not precise detection. When "
            "the anomaly is diffuse (a global/surface defect), `box` and "
            "`heatmap` are null and `diffuse` is true. Returns 422 if the tag "
            "is not a label the model knows, 404 if the picture does not exist, "
            "and 503 if the anomaly tagger model is not loaded."
        ),
        response_model=AnomalyRegionResponse,
    )
    def get_picture_anomaly_region(
        request: Request,
        id: str,
        tag: str = Query(
            ...,
            description="Anomaly tag/label to localise (e.g. 'malformed hand').",
        ),
    ):
        try:
            pic_id = int(id)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid picture id") from exc

        # Object-level access check before any DB read, image load, branch, or
        # return — scope state (a). Owner/unscoped sessions (token_scope is None)
        # pass straight through; a scoped token outside this picture's grant gets
        # a 403 here. Mirrors get_picture / get_picture_metadata /
        # get_picture_field and the detections sibling.
        enforce_picture_scope(server, request, pic_id)

        tag_clean = (tag or "").strip()
        if not tag_clean:
            raise HTTPException(
                status_code=422, detail="tag query parameter is required"
            )

        engine = getattr(server.vault, "_engine", None)
        service = getattr(engine, "pixlstash_tagger_service", None) if engine else None
        if service is None:
            raise HTTPException(
                status_code=503, detail="Anomaly tagger is unavailable."
            )
        # The tagger is idle-unloaded between tagging runs, so during review it is
        # usually not resident. Load it on demand (mirrors how a tagging task brings
        # it up) instead of failing — otherwise the review hint would 503 and the
        # frontend would silently show nothing whenever the app is idle.
        if not service.is_loaded():
            ensure = getattr(engine, "ensure_pixlstash_tagger_ready", None)
            if ensure is None or not ensure():
                raise HTTPException(
                    status_code=503,
                    detail="Anomaly tagger model could not be loaded.",
                )

        if service.resolve_label_index(tag_clean) is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown anomaly tag: '{tag_clean}'",
            )

        cache_key = (pic_id, sanitise_tag(tag_clean), int(service.version()))
        cached = _cache_get(cache_key)
        if cached is not None:
            return {"picture_id": pic_id, "tag": tag_clean, **cached}
        logger.debug(
            "anomaly_region cache miss for picture=%s tag=%s version=%s",
            pic_id,
            tag_clean,
            cache_key[2],
        )

        def fetch_file_path(session: Session):
            return session.exec(
                select(Picture.file_path).where(
                    Picture.id == pic_id,
                    Picture.deleted.is_(False),
                )
            ).first()

        rel_path = server.vault.db.run_immediate_read_task(fetch_file_path)
        if rel_path is None:
            raise HTTPException(status_code=404, detail="Picture not found")

        file_path = ImageUtils.resolve_picture_path(server.vault.image_root, rel_path)
        if not file_path or not os.path.isfile(file_path):
            raise HTTPException(
                status_code=404, detail=f"File not found for picture id={pic_id}"
            )

        try:
            with Image.open(file_path) as pil_img:
                result = service.localize_anomaly(pil_img, tag_clean)
        except UnknownAnomalyLabel as exc:
            # Defensive: resolve_label_index already validated above.
            raise HTTPException(
                status_code=422, detail=f"Unknown anomaly tag: '{tag_clean}'"
            ) from exc
        except UnidentifiedImageError as exc:
            # The picture is not a still image PIL can decode (e.g. a video that
            # still carries anomaly tags). There is nothing to localise, so degrade
            # to a diffuse (no-region) result instead of 500 — the UI shows nothing.
            logger.debug(
                "anomaly_region: picture id=%s is not a decodable image: %s",
                pic_id,
                exc,
            )
            result = {"boxes": [], "diffuse": True, "heatmap": None}
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(
                "Anomaly localisation failed for picture id=%s tag=%s: %s",
                pic_id,
                tag_clean,
                exc,
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail="Failed to localise anomaly region"
            ) from exc

        _cache_put(cache_key, result)
        return {"picture_id": pic_id, "tag": tag_clean, **result}
