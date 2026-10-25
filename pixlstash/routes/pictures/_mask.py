"""A selection's mask on one picture: the Edit tab's preview of what MASK gets.

``POST /pictures/{id}/mask`` renders a selection (``edit_selection.Selection``)
on the picture and answers a greyscale PNG the size of the picture as
displayed. It is the same renderer a run with ``selection`` uses, so the
preview is the mask the workflow receives.

``PICTURE_SCOPED`` (id_param ``id``) in ``pixlstash/authz/registry.py``; the
authz gate checks scope before the handler runs.
"""

import os

from fastapi import HTTPException, Response

from pixlstash.pixl_logging import get_logger
from pixlstash.services import picture_service
from pixlstash.services.edit_selection import (
    UNREADABLE_PICTURE,
    Selection,
    mask_png,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)


def register_routes(router, server):
    @router.post(
        "/pictures/{id}/mask",
        summary="Render a selection's mask",
        description=(
            "Renders a selection (steps plus refine, geometry as fractions of "
            "the picture) on the picture and answers the mask as a greyscale "
            "PNG the size of the picture as displayed: white is what an edit "
            "changes. The same mask POST /workflows/run hands the workflow "
            "for a run with this selection."
        ),
        response_class=Response,
        responses={200: {"content": {"image/png": {}}}},
    )
    def render_picture_mask(id: int, selection: Selection):
        file_path = picture_service.fetch_picture_file_path(server.vault.db, id)
        path = (
            ImageUtils.resolve_picture_path(server.vault.image_root, file_path)
            if file_path
            else None
        )
        if not path or not os.path.isfile(path):
            raise HTTPException(status_code=404, detail="Picture not found")
        try:
            png = mask_png(selection, path)
        except UNREADABLE_PICTURE as exc:
            logger.warning(
                "Could not render a mask on picture %s (%s): %s", id, path, exc
            )
            raise HTTPException(
                status_code=415, detail="This picture cannot be read as an image."
            ) from exc
        return Response(content=png, media_type="image/png")
