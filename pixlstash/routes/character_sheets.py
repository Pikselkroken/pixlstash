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

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from pixlstash.event_types import EventType
from pixlstash.services import character_sheet

# A run draws at most a handful of sheets; "Draw more" adds to them.
MAX_SHEETS = 32
# A draw starts at most this many runs at once.
MAX_RUN_KEYS = 32
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
    reference: bool = Field(
        False,
        description="The run feeds a reference picture: every photo names the "
        "person in it ahead of the description.",
    )
    layout: character_sheet.Layout = Field(
        "moss",
        description="`moss` is MOSS-Image's layout JSON; `prose` is the same "
        "sheet as one sentence, for a model that takes no layout.",
    )


class SheetPrompt(BaseModel):
    """The prompt, negative and canvas a sheet run draws with."""

    prompt: str
    negative: str
    width: int
    height: int


class SheetRankRequest(BaseModel):
    picture_ids: list[int] = Field(min_length=1, max_length=MAX_SHEETS)
    reference_picture_ids: list[int] | None = Field(
        None,
        max_length=4,
        description="The pictures the sheets were drawn from: closeness is "
        "measured against their faces instead of the character's reference "
        "faces. An id with no faces counts for nothing.",
    )


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
        description="Mean similarity to the reference faces (the character's, "
        "or the request's `reference_picture_ids`); null when there are none."
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
    draw_id: int | None = Field(
        None,
        description="The draw these sheets belong to. The panels and the sheets "
        "cut are recorded on it; another character's draw is a 404.",
    )


class UsedSheets(BaseModel):
    """The panels the chosen sheets became: six a sheet, passport photos first."""

    picture_ids: list[int]


def _run_key(key: str) -> str:
    if not key or len(key) > character_sheet.DRAW_RUN_KEY_MAX:
        raise ValueError(
            f"a run key is 1 to {character_sheet.DRAW_RUN_KEY_MAX} characters"
        )
    return key


class DrawRun(BaseModel):
    """One run of a draw. `status` is settled from how the run ended."""

    key: str = Field(description="The run id, or the prompt id it fell back to.")
    status: Literal["queued", "running", "completed", "failed"]
    message: str
    picture_ids: list[int]


class Draw(BaseModel):
    """One draw of character sheets on a character."""

    id: int
    character_id: int
    created_at: str = Field(description="ISO 8601, UTC.")
    settings: dict[str, Any] = Field(
        description="What the dialog drew with. Opaque to the backend."
    )
    drawn_from: int | None = Field(
        description="A picture the draw was drawn from; not a foreign key."
    )
    runs: list[DrawRun]
    kept: list[int] = Field(description="The sheets the owner ticked.")
    used: list[int] = Field(description="The panels filed on the character by use.")
    used_sheets: list[int] = Field(
        description="The sheets cut by use, so the dialog knows which are done."
    )
    seen: bool


class DrawList(BaseModel):
    draws: list[Draw]


class DrawCreate(BaseModel):
    settings: dict[str, Any]
    drawn_from: int | None = None
    run_keys: list[str] = Field(
        min_length=1,
        max_length=MAX_RUN_KEYS,
        description="The runs the draw started, by run id (or prompt id).",
    )

    @field_validator("settings")
    @classmethod
    def _settings_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value)) > character_sheet.DRAW_SETTINGS_MAX:
            raise ValueError(
                f"settings serialise to more than "
                f"{character_sheet.DRAW_SETTINGS_MAX} characters"
            )
        return value

    @field_validator("run_keys")
    @classmethod
    def _keys(cls, value: list[str]) -> list[str]:
        return [_run_key(key) for key in value]


class DrawUpdate(BaseModel):
    """Every field optional. `add_run_keys` appends; `kept` replaces."""

    add_run_keys: list[str] | None = Field(None, max_length=MAX_RUN_KEYS)
    kept: list[int] | None = Field(None, max_length=MAX_SHEETS)
    seen: bool | None = None

    @field_validator("add_run_keys")
    @classmethod
    def _keys(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else [_run_key(key) for key in value]


class DrawDeleted(BaseModel):
    deleted: bool


class DrawCharacter(BaseModel):
    """A character with at least one draw, for the sidebar and the Tasks panel."""

    character_id: int
    draws: int = Field(description="How many draws the character has.")
    newest: Draw
    run_keys: list[str] = Field(
        description="Every run key of every draw of the character."
    )


class DrawCharacters(BaseModel):
    characters: list[DrawCharacter]


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/character-sheets/prompt",
        summary="Build a character sheet prompt",
        description=(
            "The layout prompt, negative and canvas size for one character "
            "sheet: four passport photos and two body photos of the character "
            "described. MOSS-Image's layout JSON, or with `layout: prose` one "
            "sentence for a model drawing from a reference picture. Run it "
            "with `POST /workflows/run`."
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
            "Scores each sheet by how alike its own faces are and by how close "
            "they come to the character's reference faces, or to the faces of "
            "`reference_picture_ids` when the sheets were drawn from pictures. "
            "A sheet whose faces are not read yet comes back unscored."
        ),
        response_model=SheetRanking,
    )
    def rank_sheets(character_id: int, payload: SheetRankRequest):
        try:
            sheets, references = character_sheet.rank_sheets(
                server.vault,
                character_id,
                payload.picture_ids,
                payload.reference_picture_ids,
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
                server, character_id, payload.picture_ids, payload.draw_id
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

    @router.get(
        "/characters/{character_id}/sheets/draws",
        summary="List character sheet draws",
        description=(
            "The character's draws of sheets, newest first. Every run not yet "
            "ended is settled first from how it ended, and a run PixlStash no "
            "longer knows of (it restarted) is marked failed."
        ),
        response_model=DrawList,
    )
    def list_draws(character_id: int):
        draws = character_sheet.list_draws(server.vault, character_id)
        if draws is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return DrawList(draws=draws)

    @router.post(
        "/characters/{character_id}/sheets/draws",
        summary="Record a character sheet draw",
        description=(
            "Stores one draw of sheets on the character: its settings, the "
            "picture it was drawn from, and the runs it started, each queued "
            "until it ends. Returns the draw as settled so far."
        ),
        response_model=Draw,
    )
    def create_draw(character_id: int, payload: DrawCreate):
        draw = character_sheet.create_draw(
            server.vault,
            character_id,
            payload.settings,
            payload.drawn_from,
            payload.run_keys,
        )
        if draw is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return draw

    @router.patch(
        "/characters/{character_id}/sheets/draws/{draw_id}",
        summary="Change a character sheet draw",
        description=(
            "Appends run keys (`add_run_keys`, skipping those already on the "
            "draw), replaces the kept sheets (`kept`), and sets `seen`. Returns "
            "the draw as settled so far."
        ),
        response_model=Draw,
    )
    def update_draw(character_id: int, draw_id: int, payload: DrawUpdate):
        draw = character_sheet.update_draw(
            server.vault,
            character_id,
            draw_id,
            payload.add_run_keys,
            payload.kept,
            payload.seen,
        )
        if draw is None:
            raise HTTPException(status_code=404, detail="Not found.")
        return draw

    @router.delete(
        "/characters/{character_id}/sheets/draws/{draw_id}",
        summary="Delete a character sheet draw",
        description=(
            "Deletes the draw's record only. The sheets it made stay in the "
            "Scrapheap, where retention purges them."
        ),
        response_model=DrawDeleted,
    )
    def delete_draw(character_id: int, draw_id: int):
        if not character_sheet.delete_draw(server.vault, character_id, draw_id):
            raise HTTPException(status_code=404, detail="Not found.")
        return DrawDeleted(deleted=True)

    @router.get(
        "/character-sheets/draws",
        summary="List characters with sheet draws",
        description=(
            "Every character with at least one draw: how many, its newest draw, "
            "and every run key across them. Settles the runs first, as the "
            "per-character list does. Feeds the sidebar row and the Tasks panel's "
            "Open sheets link."
        ),
        response_model=DrawCharacters,
    )
    def list_draw_characters():
        return DrawCharacters(characters=character_sheet.draw_summaries(server.vault))

    return router
