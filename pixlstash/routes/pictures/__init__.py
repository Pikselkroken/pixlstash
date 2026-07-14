from fastapi import APIRouter

from pixlstash.utils.service.picture_stats import clear_stats_cache  # noqa: F401

from ._helpers import MEDIA_TYPE_BY_FORMAT  # noqa: F401
from ._listing import select_pictures_for_listing  # noqa: F401
from . import (
    _anomaly,
    _crud,
    _export,
    _face_search,
    _import,
    _likeness_search,
    _listing,
    _misc,
    _search,
    _thumbnails,
)


def create_router(server) -> APIRouter:
    """Assemble all picture-related routes into one router."""
    router = APIRouter()
    _misc.register_routes(router, server)
    _thumbnails.register_routes(router, server)
    _export.register_routes(router, server)
    _search.register_routes(router, server)
    _likeness_search.register_routes(router, server)
    _face_search.register_routes(router, server)
    _import.register_routes(router, server)
    # Register before _crud so /pictures/{id}/anomaly_region is matched ahead of
    # the /pictures/{id}/{field} catch-all registered in _crud.
    _anomaly.register_routes(router, server)
    _crud.register_routes(router, server)
    _listing.register_routes(router, server)
    return router


__all__ = ["create_router", "clear_stats_cache", "MEDIA_TYPE_BY_FORMAT"]
