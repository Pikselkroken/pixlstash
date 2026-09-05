"""HTTP caching helpers for generated, access-controlled image responses.

Generated thumbnails (character face crops, picture-set collages) are served
from a server-side cache file whose *bytes change under a stable URL*: the
sidebar asks for ``/characters/{id}/thumbnail`` and the picture behind it can be
regenerated at any time.

Starlette's ``FileResponse`` sets an ``ETag`` from the file's size and mtime but
does **not** answer a conditional request - the only conditional logic it has is
``If-Range`` for byte ranges (verified against starlette 1.3.1). So an ETag on
its own buys nothing: without a ``Cache-Control`` header the browser falls back
to *heuristic* caching and may reuse a regenerated thumbnail for an unbounded
window with no revalidation, and with ``no-cache`` but no 304 handling every
revalidation would re-send the whole PNG.

:func:`conditional_file_response` closes both halves: it declares
``private, no-cache`` (always revalidate; never store in a shared proxy, because
these images are access-controlled) and answers a matching ``If-None-Match``
with a bodyless 304, so the browser reuses the bytes it already has.
"""

import logging
import os
from email.utils import formatdate

from fastapi import Response
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

# Always revalidate, never store in a shared cache. `private` matters because
# every route using this serves access-controlled images; `no-cache` does not
# mean "do not cache", it means "do not reuse without revalidating", which is
# exactly right for bytes that change under a stable URL. The revalidation is
# cheap: it is a conditional GET answered by a 304 with no body.
REVALIDATE_CACHE_CONTROL = "private, no-cache"


def file_etag(path: str) -> str | None:
    """Return a weak ETag for *path*, or ``None`` if it cannot be stat'ed.

    The ``W/"{size}-{mtime}"`` spelling matches the one already used for
    watermarked originals in ``routes/pictures/_serving.py``, so every
    conditional image response in the app validates the same way.

    Args:
        path: Absolute path to the file being served.

    Returns:
        The weak ETag, or ``None`` when the file could not be stat'ed (the
        caller should then serve unconditionally rather than fail).
    """
    try:
        stat = os.stat(path)
    except OSError as exc:
        logger.warning("Could not stat %s for an ETag: %s", path, exc)
        return None
    return f'W/"{stat.st_size}-{int(stat.st_mtime)}"'


def conditional_file_response(
    request, path: str, media_type: str = "image/png"
) -> Response:
    """Serve *path*, answering a matching ``If-None-Match`` with a 304.

    Args:
        request: The incoming ``Request``; only ``if-none-match`` is read.
        path: Absolute path to the cached file to serve.
        media_type: The response media type.

    Returns:
        A bodyless 304 ``Response`` when the client's ETag still matches,
        otherwise a ``FileResponse`` carrying ``ETag``, ``Last-Modified`` and
        the revalidate-always ``Cache-Control``.
    """
    etag = file_etag(path)
    if etag is None:
        # No stat, so no validator to offer. Serve the bytes and still forbid
        # heuristic caching - a thumbnail that silently goes stale is the bug
        # this helper exists to prevent.
        response = FileResponse(path, media_type=media_type)
        response.headers["Cache-Control"] = REVALIDATE_CACHE_CONTROL
        return response

    if request is not None and request.headers.get("if-none-match") == etag:
        # A 304 must still carry the validator and the caching policy, or the
        # next request has nothing to revalidate against (RFC 9110 §15.4.5).
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": REVALIDATE_CACHE_CONTROL},
        )

    response = FileResponse(path, media_type=media_type)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = REVALIDATE_CACHE_CONTROL
    try:
        response.headers["Last-Modified"] = formatdate(
            os.stat(path).st_mtime, usegmt=True
        )
    except OSError as exc:
        logger.warning("Could not stat %s for Last-Modified: %s", path, exc)
    return response
