"""Set, serve and clear a model's icon - the shelf's sixth verb.

Three routes over one content-addressed store (see
:mod:`pixlstash.services.model_icons` for why the storage is shaped this way).

**One upload route serves all three ways of setting an icon.** The ruling names
three - upload a file, pick a library picture, promote one of the model's own
samples - and is explicit that "all three produce the same thing: bytes in the
icon store and a hash in the column". So the client fetches or renders the bytes
and posts them here; there is no second path that resolves a picture id
server-side, which is also what keeps the vault out of a hub table.

**No confirmation on a single-row set or clear.** Both are reconstructable by
doing them again, and the shelf's rule is to confirm only where the prior state
cannot be reconstructed. A **bulk** clear over a selection is not
reconstructable and falls on the same side of that test as the bulk base-model
overwrite, and so does a bulk set over rows that already have a mark; the client
confirms both - the routes themselves stay plain mutations.

**A bulk set is N calls to this route**, one per model, because the store is
content-addressed and the same bytes collapse to one file however many times
they are posted. The client caps and windows that fan-out
(`MAX_MODELS_PER_ICON_SET`, `useModelShelfStore.js`); this route sees only
single-model writes and cannot see the gesture.

Authorization: all three are ``OWNER_ONLY``. The store lives beside the hub and
is written and read by PixlStash alone, so no route here takes, walks or serves
a caller-supplied host path - the §16.3 locality tier its shelf neighbours sit
on would be wrong for the same reason it is wrong for ``GET /adapters``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_icons import (
    IconRefused,
    icon_path,
    media_type_of,
    store_icon,
)

logger = get_logger(__name__)

# Ceiling on one clear. Mirrors the shelf's other bulk verbs rather than
# inventing a number: a selection is what a person made, not a script. The
# client's bulk *set* uses the same figure, enforced there rather than here
# because a set is N single-model calls this route cannot correlate.
MAX_MODELS_PER_CLEAR = 500


class IconSetResponse(BaseModel):
    """Body of ``POST /models/{model_id}/icon``."""

    model_config = ConfigDict(extra="allow")

    model_id: int
    icon_sha256: str = Field(
        description=(
            "The stored icon's content hash. Two models given the same image "
            "get the same hash and share one file on disk."
        )
    )


class ClearIconsRequest(BaseModel):
    """Body of ``POST /models/icons/clear``."""

    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(description="Hub `model.id` values to clear.")


class ClearIconsResponse(BaseModel):
    """Body of ``POST /models/icons/clear``."""

    model_config = ConfigDict(extra="allow")

    cleared: list[int] = Field(
        description=(
            "The models that had an icon and no longer do. A model that already "
            "had none is not listed: nothing changed for it."
        )
    )


def create_router(server) -> APIRouter:
    """Create the model-icon router.

    Args:
        server: The Server instance, for ``hub`` and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    @router.post(
        "/models/{model_id}/icon",
        summary="Set a model's icon",
        description=(
            "Stores the uploaded image in the hub's content-addressed icon "
            "store and points the model at it.\n\n"
            "**The same bytes always produce the same hash**, so forty models "
            "given one logo store one file - which is the normal case for a "
            "base-model mark and the point of addressing by content.\n\n"
            "This is the single write path for all three ways of choosing an "
            "icon: uploading a file, picking a library picture (the client "
            "sends the pixels, so the icon is a copy and cannot break when that "
            "picture is deleted or the library is switched), and promoting one "
            "of the model's own samples."
        ),
        tags=["model_shelf"],
        response_model=IconSetResponse,
    )
    async def set_model_icon(
        model_id: int, request: Request, file: UploadFile = File(...)
    ):
        server.auth.ensure_secure_when_required(request)
        row = server.hub.fetchone("SELECT id FROM model WHERE id = ?", (model_id,))
        if row is None:
            raise HTTPException(status_code=404, detail="No such model.")

        data = await file.read()
        try:
            digest = store_icon(server.hub.path, data)
        except IconRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        with server.hub.transaction() as conn:
            changed = conn.execute(
                "UPDATE model SET icon_sha256 = ? WHERE id = ?", (digest, model_id)
            ).rowcount
        if not changed:
            # Forgotten between the existence check and the write. The bytes are
            # already in the store and are left there: they are content-addressed
            # and shared, so they are not this model's to delete.
            raise HTTPException(status_code=404, detail="No such model.")
        return IconSetResponse(model_id=model_id, icon_sha256=digest)

    @router.get(
        "/model-icons/{sha256}",
        summary="Serve one stored icon",
        description=(
            "Returns the icon's bytes. Addressed by content hash rather than by "
            "model id, so one request serves every model sharing that mark and "
            "the browser caches it once."
        ),
        tags=["model_shelf"],
        response_class=FileResponse,
        responses={200: {"content": {"image/*": {}}}},
    )
    def get_model_icon(sha256: str, request: Request):
        server.auth.ensure_secure_when_required(request)
        try:
            path = icon_path(server.hub.path, sha256)
        except IconRefused as exc:
            # 400 rather than 404: the segment is not a digest at all, which is
            # a malformed request rather than a missing icon.
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            media_type = media_type_of(path)
        except OSError:
            raise HTTPException(status_code=404, detail="No such icon.") from None
        return FileResponse(path, media_type=media_type)

    @router.post(
        "/models/icons/clear",
        summary="Clear the icon on one or more models",
        description=(
            "Points the given models at no icon; the client then draws the "
            "generated mark, so a cleared row is never blank.\n\n"
            "**The stored file is deliberately left in place.** The store is "
            "content-addressed and shared, so another model may name the same "
            "hash - deleting on clear would take a mark forty rows were using. "
            "An unreferenced icon is a few KB.\n\n"
            "Clearing one row needs no confirmation (doing it again restores "
            "it); a bulk clear is not reconstructable and the client confirms "
            "it, on the same test as the bulk base-model overwrite."
        ),
        tags=["model_shelf"],
        response_model=ClearIconsResponse,
    )
    def clear_model_icons(request: Request, payload: ClearIconsRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        ids = list(dict.fromkeys(payload.ids))
        if not ids:
            raise HTTPException(status_code=400, detail="No models named.")
        if len(ids) > MAX_MODELS_PER_CLEAR:
            raise HTTPException(
                status_code=400,
                detail=f"At most {MAX_MODELS_PER_CLEAR} models in one clear.",
            )

        placeholders = ",".join("?" for _ in ids)
        with server.hub.transaction() as conn:
            # The rows that actually HAVE an icon, read and cleared in one
            # transaction. Reported rather than counted, so the receipt can say
            # what changed instead of how many ids were sent - a selection of
            # twenty where three had icons is "3 cleared", not "20".
            cleared = [
                int(row[0])
                for row in conn.execute(
                    f"SELECT id FROM model WHERE id IN ({placeholders}) "
                    f"AND icon_sha256 IS NOT NULL",
                    tuple(ids),
                ).fetchall()
            ]
            if cleared:
                conn.execute(
                    f"UPDATE model SET icon_sha256 = NULL "
                    f"WHERE id IN ({','.join('?' for _ in cleared)})",
                    tuple(cleared),
                )
        logger.info("Cleared the icon on %d model(s).", len(cleared))
        return ClearIconsResponse(cleared=cleared)

    return router
