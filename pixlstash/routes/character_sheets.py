"""Character sheets: draw a character for "Create a training set…".

The dialog draws sheets by running a MOSS-Image text-to-image workflow through
``POST /workflows/run`` with the prompt and canvas this module hands it, so a
sheet is an ordinary run: queued behind the GPU, cancellable in Tasks, and
imported like any picture. What is particular to sheets is here:

* ``POST /character-sheets/prompt`` builds the layout prompt and canvas size.
* ``POST /characters/{character_id}/sheets/rank`` scores drawn sheets by their
  own faces, which face extraction reads as for any picture.
* ``POST /characters/{character_id}/sheets/use`` cuts the chosen sheets into
  their six panels and files them on the character, where the training-set
  picker ranks them with the character's other pictures.

The dialog draws with ``hold``, so a sheet is soft-deleted on import and never
the library's: only the panels cut from it are. Every sheet stays in the
Scrapheap, for the owner to restore or for its retention to purge.

The work is in ``services/character_sheet.py``; this module translates.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from pixlstash.event_types import EventType
from pixlstash.services import character_sheet

# A run draws at most a handful of sheets; "Draw more" adds to them.
MAX_SHEETS = 32
_TEXT = 2000


class SheetPromptRequest(BaseModel):
    """What the sheet shows: the character, the build, the outfit and the framing."""

    description: str = Field("", max_length=_TEXT)
    body: str = Field("", max_length=_TEXT)
    top: str = Field("", max_length=_TEXT)
    trousers: str = Field("", max_length=_TEXT)
    shoes: str = Field("", max_length=_TEXT)
    framing: character_sheet.Framing = "mid_thigh"
    subject: character_sheet.Subject = "woman"
    trigger: str = Field("", max_length=200)
    size: character_sheet.Size = "large"


class SheetPrompt(BaseModel):
    """The prompt, negative and canvas a sheet run draws with."""

    prompt: str
    negative: str
    width: int
    height: int


class SheetRankRequest(BaseModel):
    picture_ids: list[int] = Field(min_length=1, max_length=MAX_SHEETS)


class RankedSheet(BaseModel):
    """One drawn sheet, scored by its own faces."""

    picture_id: int
    format: str | None = Field(
        description="The file's format, for loading it full size at "
        "`/pictures/{id}.{format}`."
    )
    faces_read: bool = Field(
        description="Face extraction has run on it; until then nothing is scored."
    )
    faces: int
    agreement: float | None
    agreement_words: str
    closeness: float | None = Field(
        description="Mean similarity to the character's reference faces; null "
        "when the character has none."
    )
    close: bool | None
    broken: bool = Field(
        description="More faces than panels: a panel holds more than one person."
    )
    different_from: list[int] = Field(
        default_factory=list,
        description="The other sheets asked about whose faces look like a "
        "different person: keeping both mixes two people in one set.",
    )


class SheetRanking(BaseModel):
    sheets: list[RankedSheet]
    references: int = Field(description="How many reference faces closeness used.")


class SheetIds(BaseModel):
    picture_ids: list[int] = Field(min_length=1, max_length=MAX_SHEETS)


class UsedSheets(BaseModel):
    """The panels the chosen sheets became: six a sheet, passport photos first."""

    picture_ids: list[int]


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/character-sheets/prompt",
        summary="Build a character sheet prompt",
        description=(
            "The MOSS-Image layout prompt, negative and canvas size for one "
            "character sheet: four passport photos and two body photos of the "
            "character described. Run it with `POST /workflows/run`."
        ),
        response_model=SheetPrompt,
    )
    def sheet_prompt(payload: SheetPromptRequest):
        return SheetPrompt(
            prompt=character_sheet.sheet_prompt(**payload.model_dump()),
            negative=character_sheet.NEGATIVE_PROMPT,
            width=character_sheet.SIZES[payload.size][0],
            height=character_sheet.SIZES[payload.size][1],
        )

    @router.post(
        "/characters/{character_id}/sheets/rank",
        summary="Rank drawn character sheets",
        description=(
            "Scores each sheet by how alike its own faces are and, when the "
            "character has reference faces, by how close they come to them. "
            "A sheet whose faces are not read yet comes back unscored."
        ),
        response_model=SheetRanking,
    )
    def rank_sheets(character_id: int, payload: SheetRankRequest):
        try:
            sheets, references = character_sheet.rank_sheets(
                server.vault, character_id, payload.picture_ids
            )
        except character_sheet.SheetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SheetRanking(sheets=sheets, references=references)

    @router.post(
        "/characters/{character_id}/sheets/use",
        summary="Use character sheets",
        description=(
            "Cuts each sheet into its six panels (four passport photos, then two "
            "body photos), imports each as a picture made from its sheet, and "
            "files them on the character. A held sheet counts. The sheets "
            "themselves are left as they are."
        ),
        response_model=UsedSheets,
    )
    def use_sheets(request: Request, character_id: int, payload: SheetIds):
        try:
            panel_ids = character_sheet.use_sheets(
                server, character_id, payload.picture_ids
            )
        except character_sheet.SheetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except character_sheet.SheetUnreadable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        origin_client_id = getattr(request.state, "origin_client_id", None)
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": panel_ids,
                "source": "ui",
                "origin_client_id": origin_client_id,
                "change_kind": "added",
            },
        )
        server.vault.notify(
            EventType.CHANGED_CHARACTERS, {"origin_client_id": origin_client_id}
        )
        return UsedSheets(picture_ids=panel_ids)

    return router
