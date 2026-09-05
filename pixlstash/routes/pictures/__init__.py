from fastapi import APIRouter

from pixlstash.utils.service.picture_stats import clear_stats_cache  # noqa: F401

from ._faces import FaceListResponse  # noqa: F401
from ._helpers import MEDIA_TYPE_BY_FORMAT  # noqa: F401
from ._listing import select_pictures_for_listing  # noqa: F401
from pixlstash.routes.library_layout import (
    register_picture_routes as register_layout_picture_routes,
)
from . import (
    _anomaly,
    _character_likeness,
    _crud,
    _export,
    _faces,
    _face_search,
    _import,
    _likeness_search,
    _listing,
    _misc,
    _search,
    _serving,
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
    _serving.register_routes(router, server)
    _faces.register_routes(router, server)
    # Register the specific /pictures/{id}/... GET routes (anomaly_region,
    # character_likeness) before _crud so they are matched ahead of the
    # /pictures/{id}/{field} catch-all registered in _crud.
    _anomaly.register_routes(router, server)
    _character_likeness.register_routes(router, server)
    # Same reason, and the reason it lives outside this package: the v1.11
    # layout routes are one feature with the library-level settings beside them
    # (routes/library_layout.py), but GET /pictures/{id}/layout is the
    # {id}/{field} shape and would be answered by the catch-all if it were
    # registered after _crud.
    register_layout_picture_routes(router, server)
    _crud.register_routes(router, server)
    _listing.register_routes(router, server)
    return router


__all__ = [
    "create_router",
    "clear_stats_cache",
    "MEDIA_TYPE_BY_FORMAT",
    # Re-exported so routes/characters.py can share the face-list wire shape
    # without reaching into the private _faces module (#721).
    "FaceListResponse",
]
