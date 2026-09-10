"""The library root's caption-file sync settings.

The same four fields ``PATCH /reference-folders/{folder_id}`` carries for a
folder indexed in place - a toggle and a filename suffix per sidecar type -
for the pictures that live in the library's own root. Turning a type on asks
for a root rescan, which is the pass that reads existing sidecars in and
writes the missing ones out; from then on an edit in PixlStash is written
back through ``caption_utils.sync_picture_sidecar`` as it is for a reference
picture.
"""

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from pixlstash.services.library_settings_service import (
    get_caption_sync,
    set_caption_sync,
)
from pixlstash.utils.caption_file_utils import (
    DEFAULT_DESCRIPTION_SUFFIX,
    DEFAULT_TAGS_SUFFIX,
    is_safe_sidecar_suffix,
    suffixes_collide,
)


class CaptionSyncResponse(BaseModel):
    sync_tags: bool = Field(
        description="Write tag edits to a sidecar beside each picture, and read "
        "a sidecar edited on disk back in on the next root scan."
    )
    sync_descriptions: bool = Field(description="The same for the description.")
    tags_suffix: Optional[str] = Field(
        description="What follows the picture's stem to name its tags file, "
        "e.g. `_tags.txt`. `null` until the owner confirms one or a root scan "
        "detects one; the default is then used for new files."
    )
    description_suffix: Optional[str] = Field(
        description="The same for the description file, e.g. `_caption.txt`."
    )
    default_tags_suffix: str = Field(description="Used when `tags_suffix` is null.")
    default_description_suffix: str = Field(
        description="Used when `description_suffix` is null."
    )


class CaptionSyncPatch(BaseModel):
    sync_tags: Optional[bool] = None
    sync_descriptions: Optional[bool] = None
    tags_suffix: Optional[str] = None
    description_suffix: Optional[str] = None


def _reject_shared_suffix(merged: dict) -> None:
    """One suffix for both kinds is one file for both: see
    ``caption_file_utils.suffixes_collide``, which owns the rule (effective
    values, case-insensitive). Runs inside the writer, on the merged row, so
    two concurrent partial PATCHes cannot each pass against the old row and
    serialise into the shared state.
    """
    if suffixes_collide(merged["tags_suffix"], merged["description_suffix"]):
        raise ValueError(
            "Tags and descriptions cannot share a suffix; they would share one "
            "file and overwrite each other."
        )


def _response(stored: dict) -> CaptionSyncResponse:
    return CaptionSyncResponse(
        **stored,
        default_tags_suffix=DEFAULT_TAGS_SUFFIX,
        default_description_suffix=DEFAULT_DESCRIPTION_SUFFIX,
    )


def _suffix(value: Optional[str]) -> Optional[str]:
    """Normalise a suffix from the request: blank clears it, unsafe is a 400.

    The API trust boundary for ``caption_file_utils.is_safe_sidecar_suffix``,
    the same rule ``PATCH /reference-folders/{folder_id}`` enforces: the
    suffix is appended to a picture path to find the file to read or write.
    """
    value = (value or "").strip() or None
    if value is not None and not is_safe_sidecar_suffix(value):
        raise HTTPException(
            status_code=400,
            detail=(
                "Sidecar suffix may only contain letters, digits, '.', '_' and "
                "'-' (no path separators or '..'), and may not end in a picture "
                "or video extension."
            ),
        )
    return value


def create_router(server) -> APIRouter:
    """The library-level caption sync settings. Included as its own router."""
    router = APIRouter()

    @router.get(
        "/server-config/captions",
        summary="Get the library's caption-file sync settings",
        description=(
            "Whether tag and description edits are kept in sync with caption "
            "files beside the pictures in the library's own root, and the "
            "filename suffix each type uses. Both are off until the owner turns "
            "them on. A reference folder carries its own copy of these fields, "
            "on `PATCH /reference-folders/{folder_id}`."
        ),
        response_model=CaptionSyncResponse,
    )
    def read_caption_sync(request: Request):
        return _response(get_caption_sync(server.vault.db))

    @router.patch(
        "/server-config/captions",
        summary="Set the library's caption-file sync settings",
        description=(
            "A PATCH: a field not sent, or a toggle sent as `null`, keeps its "
            "value. Turning a type on asks "
            "for a root rescan, which reads every existing sidecar of that "
            "type in and writes one beside every picture that has content but "
            "no file yet. An empty file is never created. Turning a type off "
            "leaves every file where it is.\n\n"
            "A suffix must be a bare filename fragment (letters, digits, `.`, "
            "`_`, `-`); anything else is refused with 400. Sending an empty or "
            "`null` "
            "suffix clears it, so the next scan detects the convention on disk "
            "again or the default is used. The two types may not end up with "
            "the same suffix, their own or the default: one file cannot hold "
            "both."
        ),
        response_model=CaptionSyncResponse,
        responses={
            400: {
                "description": (
                    "A suffix is not a bare filename fragment, or both types "
                    "would use the same one."
                )
            }
        },
    )
    def patch_caption_sync(request: Request, body: CaptionSyncPatch = Body(...)):
        current = get_caption_sync(server.vault.db)
        fields: dict = {}
        # A toggle sent as null is "no change", as PATCH /reference-folders
        # treats it; only a real boolean is stored. A suffix sent as null
        # clears it, like an empty string - there is no other way to say
        # "no convention, detect it again".
        if body.sync_tags is not None:
            fields["sync_tags"] = body.sync_tags
        if body.sync_descriptions is not None:
            fields["sync_descriptions"] = body.sync_descriptions
        if "tags_suffix" in body.model_fields_set:
            fields["tags_suffix"] = _suffix(body.tags_suffix)
        if "description_suffix" in body.model_fields_set:
            fields["description_suffix"] = _suffix(body.description_suffix)
        try:
            stored = (
                set_caption_sync(
                    server.vault.db, validate=_reject_shared_suffix, **fields
                )
                if fields
                else current
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # The scan is what reads the existing files in and exports the missing
        # ones, as PATCH /reference-folders does for a folder. A type that just
        # came on is due for one, and so is a type whose suffix changed while
        # it is on: that names a different set of files, none of them read yet.
        due = any(
            stored[toggle]
            and (not current[toggle] or stored[suffix_key] != current[suffix_key])
            for toggle, suffix_key in (
                ("sync_tags", "tags_suffix"),
                ("sync_descriptions", "description_suffix"),
            )
        )
        if due:
            server.vault.rescan_library_root()
        return _response(stored)

    return router
