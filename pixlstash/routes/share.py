"""Public share endpoint — serves a picture by an embedded share token.

Route: GET /share/{token_slug}

The *token_slug* is the raw token value with the picture's file extension
appended (e.g. ``abc123xyz.jpg``).  This produces an embeddable, camera-roll-
friendly URL that looks like a normal image link.  No session cookie or
``?token=`` query parameter is required — the token IS the authentication.
"""

import logging
import os
from io import BytesIO

from fastapi import APIRouter
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image

from pixlstash.routes.pictures import MEDIA_TYPE_BY_FORMAT
from pixlstash.services import share_service
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.watermark import apply_watermark

logger = logging.getLogger(__name__)


def create_router(server) -> APIRouter:
    """Return an APIRouter with the public picture share endpoint."""

    router = APIRouter()
    _not_found = HTMLResponse(content="404 Not Found", status_code=404)

    @router.get(
        "/share/{token_slug}",
        summary="Serve a shared picture",
        description=(
            "Serves the original picture file for a picture-scoped READ share token. "
            "The token value is embedded in the filename portion of the URL."
        ),
        include_in_schema=False,
        responses={200: {"content": {"image/*": {}}}},
    )
    def serve_shared_picture(token_slug: str):
        # Split off the extension to recover the raw token value.
        name, dot_ext = os.path.splitext(token_slug)
        if not name or not dot_ext:
            return _not_found
        ext = dot_ext.lstrip(".").lower()

        matched_token = share_service.validate_picture_share_token(server.auth, name)
        if matched_token is None:
            return _not_found

        pic_id = matched_token.resource_id
        pic = share_service.get_shared_picture(server.vault, pic_id)
        if not pic:
            return _not_found
        fmt_lower = pic.format.lower() if pic.format else ""

        if fmt_lower != ext:
            return _not_found

        file_path = ImageUtils.resolve_picture_path(
            server.vault.image_root, pic.file_path
        )
        if not file_path or not os.path.isfile(file_path):
            return _not_found

        apply_wm = bool(getattr(matched_token, "watermark", False))

        # Transcode HEIC/HEIF to JPEG — browsers cannot display these natively.
        # For watermarked images, re-encode in the original format so the URL
        # extension and Content-Type remain consistent.
        if fmt_lower in ("heic", "heif") or apply_wm:
            try:
                with Image.open(file_path) as pil_img:
                    if apply_wm:
                        wm_bytes = share_service.get_user_watermark_bytes(
                            server.vault, matched_token.user_id
                        )
                        if wm_bytes:
                            pil_img = apply_watermark(pil_img, wm_bytes)

                    # Determine output format: HEIC/HEIF → JPEG (browser compat);
                    # other formats → preserve original so content-type matches URL.
                    if fmt_lower in ("heic", "heif"):
                        out_fmt = "JPEG"
                        out_mime = "image/jpeg"
                        save_kwargs = {"quality": 92}
                        pil_img = pil_img.convert("RGB")
                    else:
                        out_fmt = pil_img.format or fmt_lower.upper()
                        if out_fmt.upper() in ("JPG", "JPEG"):
                            out_fmt = "JPEG"
                            pil_img = pil_img.convert("RGB")
                            save_kwargs = {"quality": 92}
                        else:
                            save_kwargs = {}
                        out_mime = MEDIA_TYPE_BY_FORMAT.get(
                            fmt_lower, "application/octet-stream"
                        )

                    buf = BytesIO()
                    pil_img.save(buf, format=out_fmt, **save_kwargs)
                    buf.seek(0)
                    return Response(
                        content=buf.read(),
                        media_type=out_mime,
                        headers={"Cache-Control": "no-cache, must-revalidate"},
                    )
            except Exception as exc:
                logger.error(
                    "Failed to process shared picture id=%s: %s",
                    pic_id,
                    exc,
                )
                return HTMLResponse(
                    content="500 Internal Server Error", status_code=500
                )

        from email.utils import formatdate

        media_type = MEDIA_TYPE_BY_FORMAT.get(fmt_lower, "application/octet-stream")
        response = FileResponse(file_path, media_type=media_type)
        try:
            stat = os.stat(file_path)
            etag = f'W/"{stat.st_size}-{int(stat.st_mtime)}"'
            response.headers["ETag"] = etag
            response.headers["Last-Modified"] = formatdate(stat.st_mtime, usegmt=True)
            # Use no-cache so that token revocation takes effect on the next request.
            # The ETag/Last-Modified headers still allow conditional GETs (304) so
            # bandwidth is not wasted for unchanged files.
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        except OSError:
            response.headers["Cache-Control"] = "no-cache"
        return response

    return router
