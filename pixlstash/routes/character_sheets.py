"""Character sheets: draw a character for "Create a training set…".

The dialog draws sheets by running a MOSS-Image, Krea 2, Qwen-Image or FLUX.2
Klein text-to-image workflow through
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
from pixlstash.services import character_sheet, location_angles

# A run draws at most a handful of sheets; "Draw more" adds to them.
MAX_SHEETS = 32
_TEXT = 2000


class SheetPromptRequest(BaseModel):
    """What the sheet shows: the character, the build, the outfit and the
    framing; or, with another `kind`, the outfit, location or prop."""

    kind: character_sheet.Kind = Field(
        "character",
        description="`character` is a person's six-photo sheet; `outfit`, "
        "`location` and `prop` are four angles of one item, always as prose "
        "from `description` and `size` alone (every other field is ignored "
        "for them).",
    )
    description: str = Field("", max_length=_TEXT)
    body: str = Field("", max_length=_TEXT)
    top: str = Field("", max_length=_TEXT)
    trousers: str = Field("", max_length=_TEXT)
    shoes: str = Field("", max_length=_TEXT)
    background: str = Field(
        "",
        max_length=_TEXT,
        description="What every photo stands in front of; empty is a plain "
        "light-grey studio.",
    )
    framing: character_sheet.Framing = "mid_thigh"
    subject: character_sheet.Subject = "person"
    proportions: character_sheet.Proportions = Field(
        "typical",
        description="The figure: a typical adult's, a little person's, or a "
        "cartoon's, about three heads tall.",
    )
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
        "sheet as one sentence, for a model that takes no layout; `regions` is "
        "the layout JSON with its canvas size and captions that stand alone, "
        "for a sheet workflow made from another text-to-image one "
        "(ComfyUI-PixlStash's Layout Text Encode).",
    )
    photo_look: bool = Field(
        True,
        description="With `layout: prose`, the photos read as a model agency's "
        'unedited digitals rather than renders, and "photorealistic" is '
        "left out. The `moss` and `regions` layouts ignore it.",
    )
    look_added_by_run: bool = Field(
        False,
        description="With `layout: prose`, the run adds the look itself "
        '(`prompt_assist` on `POST /workflows/run`): "photorealistic" is '
        "left out and no look is appended, whatever `photo_look` says. The "
        "`moss` and `regions` layouts ignore it.",
    )
    single: bool = Field(
        False,
        description="`location` only: one wide photograph of the place at "
        f"{character_sheet.LOCATION_VIEW_SIZE[0]}x"
        f"{character_sheet.LOCATION_VIEW_SIZE[1]} instead of a four-view "
        "sheet, the picture More angles films the other views from.",
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
        "the mean similarity to their faces instead of the best match among "
        "the character's reference cluster. An id with no faces counts for "
        "nothing.",
    )


class PanelScore(BaseModel):
    """One panel's face, scored on its own."""

    panel: int = Field(
        description="Which panel: 0-3 the passport photos, 4-5 the body photos, "
        "in the order the sheet is cut."
    )
    box: list[float] = Field(
        description="The face's box, `[left, top, right, bottom]` as fractions "
        "of the sheet's width and height."
    )
    agreement: float | None = Field(
        description="Mean similarity to the other panels' faces; null when "
        "no other panel has one."
    )
    agrees: bool | None = Field(
        description="The agreement clears the bar a sheet's faces must hold to; "
        "null with no agreement."
    )
    closeness: float | None = Field(
        description="Similarity to the reference faces: the best match among "
        "the character's, the mean over `reference_picture_ids`; null when "
        "there are none."
    )
    close: bool | None = Field(
        description="The closeness clears the bar for close to the references; "
        "null with no closeness."
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
        description="Each face's best match among the character's reference "
        "cluster, or its mean similarity to the request's "
        "`reference_picture_ids`, averaged over the faces; null when there "
        "are none."
    )
    close: bool | None
    broken: bool = Field(
        description="More faces than panels: a panel holds more than one person."
    )
    panels: list[PanelScore] = Field(
        default_factory=list,
        description="Each panel's face scored on its own, in panel order; a "
        "panel with no face is left out.",
    )
    different_from: list[int] = Field(
        default_factory=list,
        description="The other sheets asked about whose faces look like a "
        "different person: keeping both mixes two people in one set.",
    )
    cuts_cleanly: bool | None = Field(
        default=None,
        description="Every cut found a clear line between the photos, through "
        "no face, and no panel holds two faces; null only when the sheet's file "
        "cannot be read. Before the faces are read the faces cannot fence the "
        "cut, so it is judged on the lines alone. A sheet that does not cut "
        "cleanly is still cut when used.",
    )
    cells: list[list[float]] = Field(
        default_factory=list,
        description="The six panels using it cuts, in panel order, as "
        "`[left, top, right, bottom]` fractions of the sheet. Drawn from the "
        "moment the sheet exists; the faces, once read, only fence the cut.",
    )


class SheetRanking(BaseModel):
    sheets: list[RankedSheet]
    references: int = Field(description="How many reference faces closeness used.")


class SheetIds(BaseModel):
    picture_ids: list[int] = Field(min_length=1, max_length=MAX_SHEETS)
    boxes: dict[int, list[list[float]]] | None = Field(
        None,
        description="Cuts drawn by hand, keyed by sheet picture id: six "
        "`[left, top, right, bottom]` fractions of the sheet, in panel "
        "order (passport photos 1-4, then body photos 5 and 6). A sheet with "
        "no entry is cut automatically.",
    )


class ItemSheetRankRequest(BaseModel):
    kind: character_sheet.ItemKind
    picture_ids: list[int] = Field(min_length=1, max_length=MAX_SHEETS)


class ItemPanelScore(BaseModel):
    """One panel of an item sheet, scored against the other three."""

    panel: int = Field(description="Which panel, 0-3, row by row.")
    box: list[float] = Field(
        description="The panel's cut, `[left, top, right, bottom]` fractions."
    )
    agreement: float = Field(
        description="Mean colour overlap with the other panels, 0..1."
    )
    agrees: bool


class RankedItemSheet(BaseModel):
    """One drawn outfit, location or prop sheet, scored by its panels."""

    picture_id: int
    format: str | None
    readable: bool = Field(
        description="The file could be read; an unreadable one is unscored."
    )
    agreement: float | None
    agreement_words: str
    cuts_cleanly: bool | None
    cells: list[list[float]] = Field(default_factory=list)
    panels: list[ItemPanelScore] = Field(default_factory=list)


class ItemSheetRanking(BaseModel):
    sheets: list[RankedItemSheet]


class ItemSheetIds(BaseModel):
    kind: character_sheet.ItemKind
    picture_ids: list[int] = Field(min_length=1, max_length=MAX_SHEETS)
    boxes: dict[int, list[list[float]]] | None = Field(
        None,
        description="Cuts drawn by hand, keyed by sheet picture id: four "
        "`[left, top, right, bottom]` fractions of the sheet, row by row. A "
        "sheet with no entry is cut automatically.",
    )


class UsedSheets(BaseModel):
    """The panels the chosen sheets became: six a sheet, passport photos first."""

    picture_ids: list[int]


class LocationShot(BaseModel):
    preset: str = Field("closer", pattern="^(closer|opposite|reverse|left|right)$")
    words: str = Field("", max_length=500)


class LocationShotsPromptRequest(BaseModel):
    description: str = Field("", max_length=2000)
    layout: str = Field("", max_length=2000)
    light: str = Field("plain daylight", max_length=200)
    shots: list[LocationShot] = Field(min_length=1, max_length=4)
    last_picture: bool = Field(
        False, description="A second picture is the video's last frame and shot."
    )


class LocationShotsPrompt(BaseModel):
    prompt: str
    seconds: int


class LocationShotsRequest(BaseModel):
    video_picture_id: int
    shots: int = Field(ge=1, le=4, description="New shots, the picture's own left out.")
    ends_on_picture: bool = Field(
        False, description="The last shot is a second picture, not a new view."
    )


class PictureThingsRequest(BaseModel):
    picture_id: int


class PictureThings(BaseModel):
    things: list[str]


class LocationRestorePair(BaseModel):
    frame_id: int
    restored_id: int


class LocationRestoreCheckRequest(BaseModel):
    original_id: int
    pairs: list[LocationRestorePair] = Field(min_length=1, max_length=8)


class LocationRestoreCheck(BaseModel):
    frame_id: int
    restored_id: int
    ok: bool
    reason: str
    frame_match: float
    original_match: float


class LocationRestoreChecks(BaseModel):
    views: list[LocationRestoreCheck]


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
        if payload.single:
            if payload.kind != "location":
                raise HTTPException(
                    status_code=422,
                    detail="Only a location is drawn as a single wide picture.",
                )
            width, height = character_sheet.LOCATION_VIEW_SIZE
            return SheetPrompt(
                prompt=character_sheet.location_view_prompt(payload.description),
                negative=character_sheet.NEGATIVE_PROMPT,
                width=width,
                height=height,
            )
        width, height = character_sheet.item_canvas(payload.kind, payload.size)
        fields = payload.model_dump()
        fields.pop("single")
        kind = fields.pop("kind")
        if kind == "character":
            prompt = character_sheet.sheet_prompt(**fields)
        else:
            prompt = character_sheet.item_sheet_prompt(
                kind=kind, description=payload.description
            )
        return SheetPrompt(
            prompt=prompt,
            negative=character_sheet.NEGATIVE_PROMPT,
            width=width,
            height=height,
        )

    @router.post(
        "/characters/{character_id}/sheets/rank",
        summary="Rank drawn character sheets",
        description=(
            "Scores each sheet by how alike its own faces are and by how close "
            "they come to the character's reference cluster (each face's best "
            "match in it), or to the faces of `reference_picture_ids` when the "
            "sheets are compared with the pictures they were drawn from. "
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
            "files them on the character. A sheet in `boxes` is cut at the six "
            "boxes given for it, exactly; the others are cut automatically. A "
            "held sheet counts. The sheets themselves are left as they are. "
            "422 when a box is not inside the sheet, is inverted or too small, "
            "or a sheet's boxes are not six, or name a picture not in "
            "`picture_ids`."
        ),
        response_model=UsedSheets,
    )
    def use_sheets(request: Request, character_id: int, payload: SheetIds):
        try:
            panel_ids = character_sheet.use_sheets(
                server, character_id, payload.picture_ids, payload.boxes
            )
        except character_sheet.SheetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except character_sheet.SheetBoxesInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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

    @router.post(
        "/item-sheets/rank",
        summary="Rank drawn outfit, location or prop sheets",
        description=(
            "Scores each sheet by how its four panels agree in colour, each "
            "panel against the other three, and returns where it cuts. Item "
            "sheets have no faces, so nothing is waited for."
        ),
        response_model=ItemSheetRanking,
    )
    def rank_item_sheets(payload: ItemSheetRankRequest):
        try:
            sheets = character_sheet.rank_item_sheets(
                server.vault, payload.kind, payload.picture_ids
            )
        except character_sheet.SheetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ItemSheetRanking(sheets=sheets)

    @router.post(
        "/item-sheets/use",
        summary="Use outfit, location or prop sheets",
        description=(
            "Cuts each sheet into its four panels (an outfit's side by side, "
            "a location's or prop's two by two, row by row) and imports each "
            "as a picture made from its sheet, filed on nobody. A sheet in "
            "`boxes` is cut at the four boxes given for it. 422 for a bad box "
            "as with character sheets."
        ),
        response_model=UsedSheets,
    )
    def use_item_sheets(request: Request, payload: ItemSheetIds):
        try:
            panel_ids = character_sheet.use_item_sheets(
                server, payload.kind, payload.picture_ids, payload.boxes
            )
        except character_sheet.SheetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except character_sheet.SheetBoxesInvalid as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except character_sheet.SheetUnreadable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": panel_ids,
                "source": "ui",
                "origin_client_id": getattr(request.state, "origin_client_id", None),
                "change_kind": "added",
            },
        )
        return UsedSheets(picture_ids=panel_ids)

    @router.post(
        "/item-sheets/shots/prompt",
        summary="Write a location's shots prompt",
        description=(
            "The prompt for *MiniMax H3 Location Shots*: the picture as shot 1, "
            "then one static shot per row (`closer`, `opposite`, `reverse`, "
            "`left`, `right`, with words for what it shows), joined by hard "
            "cuts. `seconds` is the video's length, two a shot."
        ),
        response_model=LocationShotsPrompt,
    )
    def location_shots_prompt(payload: LocationShotsPromptRequest):
        shots = [shot.model_dump() for shot in payload.shots]
        return LocationShotsPrompt(
            prompt=location_angles.location_shots_prompt(
                payload.description,
                payload.layout,
                shots,
                payload.light,
                last_picture=payload.last_picture,
            ),
            seconds=location_angles.shots_seconds(len(shots), payload.last_picture),
        )

    @router.post(
        "/item-sheets/shots",
        summary="Take a location's new views out of its shots video",
        description=(
            "Finds the hard cuts in the video and imports the sharpest frame "
            "of each new shot as a picture made from it, in shot order. The "
            "video model may film fewer or more shots than `shots`; every one "
            "it made is returned. 409 when the video has no cut (no new view) "
            "or cannot be read; 404 for a picture that is not there."
        ),
        response_model=UsedSheets,
    )
    def location_shots(request: Request, payload: LocationShotsRequest):
        try:
            frame_ids = location_angles.frames_of_video(
                server,
                payload.video_picture_id,
                payload.shots,
                ends_on_picture=payload.ends_on_picture,
            )
        except character_sheet.SheetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (character_sheet.SheetUnreadable, location_angles.ShotsNotFound) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        server.vault.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": frame_ids,
                "source": "ui",
                "origin_client_id": getattr(request.state, "origin_client_id", None),
                "change_kind": "added",
            },
        )
        return UsedSheets(picture_ids=frame_ids)

    @router.post(
        "/item-sheets/shots/things",
        summary="The things in a location's picture, for its new views",
        description=(
            "Florence-2's region captions of the picture, as phrases a new "
            'view can show ("the brick fireplace on the left"), biggest '
            f"first, at most {location_angles.THINGS_MAX}. A picture in the "
            "Scrapheap counts. 404 for a picture that is not there; 409 when "
            "it cannot be read or the describer is not running."
        ),
        response_model=PictureThings,
    )
    def location_picture_things(payload: PictureThingsRequest):
        try:
            things = location_angles.things_in_picture(server, payload.picture_id)
        except character_sheet.SheetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except character_sheet.SheetUnreadable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return PictureThings(things=things)

    @router.post(
        "/item-sheets/shots/check",
        summary="Check a location's restored views",
        description=(
            "Whether each restored view is still the frame it was restored "
            "from (grey correlation at 128 px of at least "
            f"{location_angles.RESTORE_KEEPS}) and has not turned back into "
            "the original picture. 404 for a picture that is not there."
        ),
        response_model=LocationRestoreChecks,
    )
    def location_shots_check(payload: LocationRestoreCheckRequest):
        try:
            views = location_angles.check_restored(
                server,
                payload.original_id,
                [(p.frame_id, p.restored_id) for p in payload.pairs],
            )
        except character_sheet.SheetNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except character_sheet.SheetUnreadable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return LocationRestoreChecks(views=views)

    return router
