"""Original / watermarked picture file serving.

``GET /pictures/{id}.{ext}`` streams the original media file for a picture, with
on-the-fly HEIC->JPEG transcoding for browser compatibility and optional
watermark compositing (disk-cached) for watermark-scoped tokens.

Object scope: this is a per-object data endpoint declared ``PICTURE_SCOPED``
(id_param ``id``) in ``pixlstash/authz/registry.py``; the centralised authz gate
runs ``enforce_picture_scope`` before the handler body, so no inline scope check
lives here.
"""

import os
import re
from email.utils import formatdate
from io import BytesIO

from fastapi import HTTPException, Request, Response
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from PIL.PngImagePlugin import PngInfo

from pixlstash.db_models import Picture
from pixlstash.db_models.user import User
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.watermark import apply_watermark, get_watermark_bytes

from ._helpers import MEDIA_TYPE_BY_FORMAT


logger = get_logger(__name__)


# Formats whose EXIF orientation the BROWSER applies for us, so the raw bytes can
# be streamed through untouched. Everything else has to be transposed server-side
# before it is served - see the measurement in `get_picture` below, and the
# matching table in `utils/image_processing/orientation.py`.
BROWSER_ORIENTED_FORMATS = {"jpg", "jpeg"}


def register_routes(router, server):
    @router.get(
        "/pictures/{id}.{ext}",
        summary="Get original picture file",
        description="Streams the original media file for a picture id when the requested extension matches the stored format.",
        response_class=FileResponse,
        responses={200: {"content": {"image/*": {}}}},
    )
    def get_picture(request: Request, id: str, ext: str):
        if not isinstance(id, str):
            logger.error(f"Invalid id type: {type(id)} value: {id}")
            raise HTTPException(status_code=400, detail="Invalid picture id type")

        if not ext or not isinstance(ext, str):
            logger.error(f"Invalid extension type: {type(ext)} value: {ext}")
            raise HTTPException(status_code=400, detail="Invalid picture extension")
        id = int(id)

        pics = server.vault.db.run_immediate_read_task(
            lambda session: Picture.find(session, id=id, include_deleted=True)
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]

        file_path = ImageUtils.resolve_picture_path(
            server.vault.image_root, pic.file_path
        )
        if not file_path or not os.path.isfile(file_path):
            logger.error(
                f"File path missing or does not exist for picture id={pic.id}, file_path={pic.file_path}"
            )
            raise HTTPException(
                status_code=404, detail=f"File not found for picture id={pic.id}"
            )
        if pic.format.lower() != ext.lower():
            logger.error(
                f"Requested extension '{ext}' does not match picture format '{pic.format}' for id={pic.id}"
            )
            raise HTTPException(
                status_code=400,
                detail="Requested extension does not match picture format",
            )

        fmt_lower = pic.format.lower()

        # Determine whether the active token requires a watermark.
        _token_scope = getattr(request.state, "token_scope", None)
        apply_wm = bool(_token_scope and getattr(_token_scope, "watermark", False))

        def _get_user_watermark_bytes() -> bytes | None:
            user_id = getattr(request.state, "auth_user_id", None)
            if not user_id:
                return None
            # The watermark is the owner's, not a library's, so it is in the hub.
            user = server.hub_engine.run_immediate_read_task(
                lambda session: session.get(User, user_id)
            )
            return get_watermark_bytes(
                getattr(user, "watermark_image", None) if user else None
            )

        # Browsers (Chrome, Firefox) cannot display HEIC/HEIF natively.
        # Transcode to JPEG on-the-fly so the overlay image loads correctly.
        # Watermark compositing also requires PIL, so we share this branch.
        #
        # Disk cache for watermarked images: stored as {stem}_watermarked.{ext}
        # next to the original. Valid while the cached file is at least as new as
        # the source. Served directly via FileResponse so the browser can also
        # cache it by ETag.
        is_heic = fmt_lower in ("heic", "heif")

        # Does this response have to arrive already turned?
        #
        # An in-place rotate writes the file's EXIF orientation tag and leaves
        # the pixels alone, which is correct only where the renderer applies the
        # tag. The backend always does (`exif_transpose` on every decode, which
        # is what the thumbnail is built from); the browser applies it for JPEG
        # and ONLY for JPEG. Measured 2026-08-18 against a real library file:
        #
        #     Chromium 148.0.7778.96 / Firefox 150.0.2
        #       40x20 JPEG, orientation 6  ->  naturalSize 20x40   (honoured)
        #       40x20 PNG,  orientation 6  ->  naturalSize 40x20   (ignored)
        #
        # A PNG's `eXIf` chunk is ignored by both engines exactly as WebP's is,
        # so a rotated PNG showed a turned thumbnail beside an unturned full
        # view. Turning it here is what lets the rotate stay instant, lossless
        # and undoable for the 5-in-6 of a ComfyUI library that is PNG.
        #
        # Re-encoding drops the EXIF block wholesale, so anything on the
        # HEIC-transcode or watermark path has to be turned here too - including
        # JPEG, which would otherwise lose the tag on its way through PIL.
        orientation = int(getattr(pic, "orientation", None) or 1)
        reencoding = is_heic or apply_wm
        needs_transpose = orientation != 1 and (
            reencoding or fmt_lower not in BROWSER_ORIENTED_FORMATS
        )

        # Reference-folder pictures are the user's own files in the user's own
        # folders; this library does not write beside them. They render per
        # request.
        cacheable = not (pic.file_path and os.path.isabs(pic.file_path))
        if not is_heic and cacheable and (apply_wm or needs_transpose):
            file_stem, file_ext = os.path.splitext(file_path)
            suffix = "_watermarked" if apply_wm else "_oriented"
            wm_cache_path = f"{file_stem}{suffix}{file_ext}"
            if os.path.isfile(wm_cache_path):
                try:
                    if os.path.getmtime(wm_cache_path) >= os.path.getmtime(file_path):
                        media_type = MEDIA_TYPE_BY_FORMAT.get(
                            fmt_lower, "application/octet-stream"
                        )
                        stat = os.stat(wm_cache_path)
                        etag = f'W/"{stat.st_size}-{int(stat.st_mtime)}"'
                        if request.headers.get("if-none-match") == etag:
                            return Response(status_code=304)
                        resp = FileResponse(wm_cache_path, media_type=media_type)
                        resp.headers["ETag"] = etag
                        # Revalidate like every other branch of this route. The
                        # ETag makes the repeat cost a 304, while a max-age here
                        # would be the one media response that can serve stale
                        # pixels after an in-place edit: the client cannot know
                        # a picture was rewritten, so it must be allowed to ask.
                        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
                        return resp
                except OSError as exc:
                    logger.warning(
                        "Failed to access derived-render cache for id=%s: %s",
                        pic.id,
                        exc,
                    )
        else:
            wm_cache_path = None

        if is_heic or apply_wm or needs_transpose:
            try:
                with Image.open(file_path) as pil_img:
                    # ComfyUI provenance rides in the PNG's text chunks
                    # (`workflow` / `prompt`), and a PIL re-save drops every one
                    # of them. This response is what "Save image as" hands the
                    # user, so they are carried across explicitly - the stored
                    # file is untouched either way, but a downloaded copy that
                    # silently lost its graph would be a worse bug than the one
                    # this branch exists to fix.
                    pil_img.load()
                    png_text = dict(getattr(pil_img, "text", {}) or {})
                    # Turned FIRST, so anything composited below lands the right
                    # way up. `exif_transpose` also drops the orientation tag it
                    # has just applied, so the result cannot be turned twice.
                    if needs_transpose:
                        pil_img = ImageOps.exif_transpose(pil_img)
                    if apply_wm:
                        wm_bytes = _get_user_watermark_bytes()
                        if wm_bytes:
                            pil_img = apply_watermark(pil_img, wm_bytes)
                    # HEIC/HEIF → JPEG (browser compat);
                    # other formats preserve original so content-type matches URL.
                    if is_heic:
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
                            if out_fmt.upper() == "PNG" and png_text:
                                png_info = PngInfo()
                                for text_key, text_value in png_text.items():
                                    png_info.add_text(text_key, text_value)
                                save_kwargs["pnginfo"] = png_info
                        out_mime = MEDIA_TYPE_BY_FORMAT.get(
                            fmt_lower, "application/octet-stream"
                        )
                    buf = BytesIO()
                    pil_img.save(buf, format=out_fmt, **save_kwargs)
                    buf.seek(0)
                    encoded_bytes = buf.read()
            except Exception as exc:
                logger.error(
                    "Failed to process picture id=%s: %s",
                    pic.id,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail="Failed to process image",
                )

            # Persist the derived render to disk so future requests are free.
            # Its validity rule is mtime against the source, so the next rotate
            # (which rewrites the original) invalidates it by itself.
            if wm_cache_path is not None:
                try:
                    with open(wm_cache_path, "wb") as _f:
                        _f.write(encoded_bytes)
                except OSError as exc:
                    logger.warning(
                        "Could not write derived-render cache for id=%s: %s",
                        pic.id,
                        exc,
                    )

            response = Response(
                content=encoded_bytes,
                media_type=out_mime,
            )
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

        media_type = MEDIA_TYPE_BY_FORMAT.get(fmt_lower)
        response = FileResponse(file_path, media_type=media_type)
        try:
            stat = os.stat(file_path)
            etag = f'W/"{stat.st_size}-{int(stat.st_mtime)}"'
            response.headers["ETag"] = etag
            response.headers["Last-Modified"] = formatdate(stat.st_mtime, usegmt=True)
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        except OSError:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        if pic.original_file_name:
            # Suggest the original filename when using "Save image as" in the browser.
            # Using 'inline' keeps the image rendering in-page while still providing
            # the filename hint - no URL change needed.
            safe_name = pic.original_file_name.replace('"', "")
            response.headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
        origin = request.headers.get("origin")
        if origin and (
            origin in server.allow_origins
            or (
                server.allow_origin_regex
                and re.match(server.allow_origin_regex, origin)
            )
        ):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response
