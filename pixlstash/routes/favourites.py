"""The owner's favourites: models, workflow cards and workflow sets (#1578).

One mechanism for three surfaces. A favourite is a hub ``favourite`` row keyed
by ``(kind, key)``: a model by sha256, a workflow by its card key, a set by the
grid's set key. Nothing here resolves a key to a row: a key that names nothing
today is kept, so a favourite comes back when its model returns. The client
does the ranking (favourites lead inside each group); this module only stores
the choice.

Authorization: both routes are ``OWNER_ONLY``. They read and write the owner's
own hub rows, keyed by identities no share token is ever scoped to.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Ceiling on one write: a selection is what a person made, not a script. The
# shelf's bulk verbs use the same figure.
MAX_FAVOURITES_PER_WRITE = 500

FavouriteKind = Literal["model", "workflow", "set"]

# A sha256 is 64 characters and a set key names a file at most; 512 is room for
# any real key and a bound on what one write may store.
FavouriteKey = Annotated[str, Field(min_length=1, max_length=512)]


class FavouritesResponse(BaseModel):
    """Every favourite on this hub, per kind, each list sorted."""

    model: list[str] = Field(description="Model sha256s.")
    workflow: list[str] = Field(description="Workflow card keys.")
    set: list[str] = Field(description="Workflow set keys (`hand:<id>`, `model:<id>`).")


class FavouritesRequest(BaseModel):
    """Body of ``PUT /favourites``."""

    model_config = ConfigDict(extra="forbid")

    kind: FavouriteKind
    keys: list[FavouriteKey] = Field(
        min_length=1,
        max_length=MAX_FAVOURITES_PER_WRITE,
        description="What to mark or unmark, by the kind's own key.",
    )
    favourite: bool = Field(description="True marks them, false unmarks them.")


def list_favourites(hub) -> FavouritesResponse:
    """Read every favourite, grouped by kind."""
    out: dict[str, list[str]] = {"model": [], "workflow": [], "set": []}
    for row in hub.fetchall("SELECT kind, key FROM favourite ORDER BY kind, key"):
        out[row["kind"]].append(row["key"])
    return FavouritesResponse(**out)


def set_favourites(hub, kind: str, keys: list[str], favourite: bool) -> None:
    """Mark or unmark ``keys`` of one ``kind``. Idempotent both ways."""
    keys = list(dict.fromkeys(keys))
    with hub.transaction() as conn:
        if favourite:
            now = datetime.now(timezone.utc).isoformat()
            conn.executemany(
                "INSERT OR IGNORE INTO favourite (kind, key, created_at) "
                "VALUES (?, ?, ?)",
                [(kind, key, now) for key in keys],
            )
        else:
            conn.executemany(
                "DELETE FROM favourite WHERE kind = ? AND key = ?",
                [(kind, key) for key in keys],
            )
    logger.info(
        "%s %d %s favourite(s).",
        "Marked" if favourite else "Unmarked",
        len(keys),
        kind,
    )


def create_router(server) -> APIRouter:
    """Create the favourites router.

    Args:
        server: The Server instance, for ``hub`` and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    @router.get(
        "/favourites",
        summary="The owner's favourites",
        description=(
            "Every model (by sha256), workflow card (by key) and workflow set "
            "(by set key) the owner has marked as a favourite. Per hub, not per "
            "library. A key may name something no longer on the shelf: the "
            "favourite is kept so it comes back with the model."
        ),
        tags=["favourites"],
        response_model=FavouritesResponse,
    )
    def get_favourites(request: Request):
        server.auth.ensure_secure_when_required(request)
        return list_favourites(server.hub)

    @router.put(
        "/favourites",
        summary="Mark or unmark favourites",
        description=(
            "Marks (`favourite: true`) or unmarks the given keys of one kind. "
            "Idempotent: marking a favourite again or unmarking a key that is "
            "not one changes nothing. Returns every favourite after the call."
        ),
        tags=["favourites"],
        response_model=FavouritesResponse,
    )
    def put_favourites(request: Request, payload: FavouritesRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        set_favourites(server.hub, payload.kind, payload.keys, payload.favourite)
        return list_favourites(server.hub)

    return router
