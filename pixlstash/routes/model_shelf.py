"""The model shelf's API: adapters, checkpoints, what uses them, and the five verbs.

Two route blocks, one table. ``/adapters`` and ``/checkpoints`` converge on the
same ``model`` query filtered by ``file_kind``; the blocks stay separate only
because their **addressing** differs. An adapter always carries a sha256 (the
hub's ``CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL)`` makes that an
invariant, not a hope), so it is addressable as ``/adapters/{sha256}``. A
checkpoint may be 24 GB and registers instantly with ``sha256`` NULL, so until
``MissingCheckpointHashFinder`` has read it there is no hash to address it by —
hence a list route and no by-hash detail route on that side. Every row carries
its hub ``model.id`` for that reason: it is AUTOINCREMENT, never recycled, and it
is the only identifier an unhashed checkpoint has.

**``unknown`` is never rendered as a checkpoint.** ``file_kind='unknown'`` is a
first-class stored value (a marker-free file too small to be a base model is most
likely an adapter format we have not met yet), and it appears in neither list by
default. It surfaces on the *adapters* block under an explicit
``?file_kind=unknown``, because an unknown is hashed on sight by the scanner and
is therefore hash-addressable exactly as an adapter is. ``/checkpoints`` never
returns one.

**A null ``base_model`` is a bulk state, not an edge case.** Measured against 91
real adapters, 37 % carry no title, no base model and no trigger word at all. So
``base_model`` is serialised as ``null`` rather than coerced to a string, is never
a reason to drop a row from the list, and is selectable through the filter as the
explicit sentinel ``base_model=UNASSIGNED``.

The queries themselves live in
:mod:`pixlstash.services.model_shelf_service`, including the hub/vault seam the
``character_id`` / ``set_id`` filter has to cross. Locations and attachments each
arrive in a single whole-page query, and B7's sorting went into that same one
SELECT: ``member_count`` / ``total_size`` / ``newest_member_at`` for a stack and
the newest ``file_mtime`` across a model's present copies are joined in, never
looked up per row.

**The five verbs sit on two routes, not five.** Assign is
``PUT /adapters/{sha256}/attachments`` and writes the vault. Rename, Set base
model and Set kind write one curated hub column each and differ in nothing else,
so they share ``PATCH /models``, which writes only the fields the body actually
carries. Forget is ``POST /models/forget`` and is the one that destroys
curation, so it is the one with a confirmation in front of it — and it is gated
on the row's *state* (no ``present`` and no ``unreachable`` copy), never on how
many rows were selected. There is **no undo** and no operation-log half for any
of this: ruled 2026-08-09, reaffirmed 2026-08-10.

All four id-taking verbs address rows by hub ``model.id`` rather than by hash,
because a 24 GB checkpoint is listable long before it is hashed and would
otherwise be the one row on the shelf that cannot be corrected.

Authorization is declared, never inline: every route here is ``OWNER_ONLY`` in
``pixlstash/authz/registry.py`` and takes the **default** library pin
(``library_independent`` is left at ``False``, so the pin runs before the policy
branch). See ``docs/backend_architecture.md`` §16.1.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.db_models.adapter_attachment import ENTITY_CHARACTER, ENTITY_SET
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_shelf_service import (
    CURATABLE_FIELDS,
    DEFAULT_DIRECTION,
    DEFAULT_SORT,
    ENTITY_TYPES,
    FILE_KINDS,
    UnknownAttachmentEntityError,
    attached_hashes,
    fetch_attachments,
    fetch_locations,
    fetch_model_by_hash,
    fetch_models,
    forget_models,
    replace_attachments,
    update_models,
)
from pixlstash.utils.adapter_header import FILE_ADAPTER, FILE_CHECKPOINT, FILE_UNKNOWN

logger = get_logger(__name__)

# What ``GET /adapters`` will serve. ``checkpoint`` is deliberately absent: it has
# its own block because it is not addressable by hash, and folding it in here
# would be the "unknown renders as checkpoint" defect in the other direction.
ADAPTER_BLOCK_FILE_KINDS = (FILE_ADAPTER, FILE_UNKNOWN)

# Constrained here rather than validated in the handler, so an unknown key is a
# 422 from FastAPI and never reaches the SQL builder. The values are the five
# ruled sort keys; keep them in step with ``model_shelf_service.SORT_KEYS``,
# which a test pins.
SortKey = Literal["added_at", "file_mtime", "name", "size", "base_model"]
SortDirection = Literal["asc", "desc"]


class ModelLocation(BaseModel):
    """Where one copy of a model sits, and whether it was there at the last scan."""

    model_config = ConfigDict(extra="allow")

    folder_id: int = Field(description="``model_folder.id`` this copy lives under.")
    folder_path: str = Field(description="The registered folder, as registered.")
    relpath: str = Field(description="Path of this copy relative to the folder.")
    state: str = Field(
        description=(
            "``present``, ``missing`` or ``unreachable``. ``missing`` is a fact "
            "(the folder was readable and the file was not in it); "
            "``unreachable`` is the absence of one (we could not look), and only "
            "``missing`` is something a forget/cleanup action may act on."
        )
    )
    file_mtime: Optional[int] = Field(
        default=None,
        description="``st_mtime_ns`` of this copy at the last scan; null if never seen.",
    )


class ModelAttachment(BaseModel):
    """One character or set in **this library** that uses the model."""

    model_config = ConfigDict(extra="allow")

    entity_type: str = Field(description="``character`` or ``set``.")
    entity_id: int = Field(
        description="Row id in this library's vault. Meaningless in another library."
    )


class ModelAttachmentRequest(ModelAttachment):
    """One attachment as a *client* may send it.

    The response model above allows extra keys, so a future field does not
    break an old client reading it. A request is the other direction: an
    unrecognised key there is a typo or a client the server does not
    understand, and accepting it silently is how a misspelled ``entity_id``
    becomes a no-op the user reads as success.
    """

    model_config = ConfigDict(extra="forbid")


# Ceiling on one PUT's attachment list. Every element costs a ``session.get``
# inside a single ``DBPriority.IMMEDIATE`` vault transaction, which is the
# queue every other write is waiting behind, so an unbounded list is a stall
# any authenticated caller can trigger. 200 is far above the real shape of the
# data (one adapter used by a handful of characters or sets) and far below a
# list long enough to hold the write path.
MAX_ATTACHMENTS_PER_MODEL = 200

# Ceiling on one verb's selection. A shelf of 1,806 rows can be selected whole,
# so this is set above the largest real selection rather than below it: the cost
# is one hub UPDATE or DELETE over an id list, not a lookup per id, and 5,000
# ids is still a single short transaction. It exists so a malformed client
# cannot post an unbounded list into the SQL builder, not to ration the verb.
MAX_MODELS_PER_EDIT = 5000


class ModelResponse(BaseModel):
    """One row of the shelf: what the file is, where its copies are, who uses it."""

    model_config = ConfigDict(extra="allow")

    id: int = Field(
        description=(
            "Hub ``model.id``. AUTOINCREMENT, so it is never reissued to a "
            "different file. This is the only identifier an unhashed checkpoint "
            "has, which is why it is on every row and not only on the ones a "
            "sha256 could address."
        )
    )
    sha256: Optional[str] = Field(
        default=None,
        description=(
            "Full-file SHA-256, the interop identity (Civitai lookup, the ComfyUI "
            "node). Never null for an adapter or an unknown; null for a checkpoint "
            "MissingCheckpointHashFinder has not read yet."
        ),
    )
    file_kind: str = Field(
        description="``adapter``, ``checkpoint`` or ``unknown``. Owner-correctable."
    )
    kind: Optional[str] = Field(
        default=None,
        description="Adapter algorithm (``lora``, ``lokr``, …). Null for a checkpoint.",
    )
    display_name: Optional[str] = None
    filename: Optional[str] = None
    base_model: Optional[str] = Field(
        default=None,
        description=(
            "What the trainer said this was trained against, verbatim. Null for "
            "the large minority of real adapters that record nothing — shown as "
            "'Not set', never dropped, and selectable with base_model=UNASSIGNED."
        ),
    )
    trigger_words: Optional[str] = None
    provenance: str = Field(
        description="``external`` for anything found on disk; ``trained`` for a run we ran."
    )
    training_run_id: Optional[int] = None
    training_step: Optional[int] = None
    param_count: Optional[int] = None
    file_size: Optional[int] = None
    hashed_at: Optional[str] = None
    stack_id: Optional[int] = None
    stack_position: Optional[int] = None
    run_key: Optional[str] = None
    added_at: Optional[str] = Field(
        default=None, description="``model.created_at`` — when the shelf first saw it."
    )
    newest_file_mtime: Optional[int] = Field(
        default=None,
        description=(
            "Newest ``st_mtime_ns`` across this model's **present** copies, which "
            "is what the `file_modified` sort orders on. Null when no copy is "
            "present, so the row sorts last in either direction."
        ),
    )
    member_count: Optional[int] = Field(
        default=None,
        description=(
            "How many models share this row's ``stack_id``. Null for a model "
            "that stands alone. Computed in the list query, not per row."
        ),
    )
    total_size: Optional[int] = Field(
        default=None,
        description=(
            "Sum of ``file_size`` over every member of this row's stack. This is "
            "what the shelf displays and what `size` sorts on for a stacked row, "
            "because the cover alone understates a six-step run by about six "
            "times, in the column the shelf exists to answer. Null when the row "
            "is not stacked; its own ``file_size`` is then both."
        ),
    )
    newest_member_at: Optional[str] = Field(
        default=None,
        description=(
            "Newest ``created_at`` across the stack's members: a stack's date is "
            "its newest member's, never its cover's. Null when not stacked."
        ),
    )
    locations: list[ModelLocation] = Field(
        default_factory=list,
        description="Every registered copy. Empty means every copy was forgotten.",
    )
    attachments: list[ModelAttachment] = Field(
        default_factory=list,
        description=(
            "Characters and sets in the active library that use this model. "
            "Returned on the list as well as on the detail so the shelf never "
            "has to fetch them one row at a time."
        ),
    )


class AttachmentsResponse(BaseModel):
    """Body of ``PUT /adapters/{sha256}/attachments``."""

    model_config = ConfigDict(extra="allow")

    sha256: str
    attachments: list[ModelAttachment]


class ModelEditRequest(BaseModel):
    """Body of ``PATCH /models``: the verbs that write a curated column.

    Every field is optional and **only the fields actually sent are written**,
    which is what lets one route carry Rename, Set base model and Set kind
    without each of them blanking the other two. A field sent explicitly as
    ``null`` IS written: clearing a wrong base model back to "not set" is a
    correction the owner is entitled to make.
    """

    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(
        min_length=1,
        max_length=MAX_MODELS_PER_EDIT,
        description=(
            "The models to write, by hub `model.id`. Ids rather than hashes "
            "because a 24 GB checkpoint is listable before it has been hashed "
            "and would otherwise be the one row that cannot be corrected. At "
            f"most {MAX_MODELS_PER_EDIT} per call."
        ),
    )
    display_name: Optional[str] = Field(
        default=None,
        description=(
            "The name to show. **One id only**: a name is a fact about one "
            "file, and a bulk rename would give every selected row the same "
            "one. Null clears it, which puts the row back in the `Needs a "
            "name` queue and lets the shelf show a name derived from the "
            "filename instead."
        ),
    )
    base_model: Optional[str] = Field(
        default=None,
        description=(
            "Free text, folded for the shelf's sort and filters by "
            "`known_base_models.fold` rather than constrained here. Null "
            "clears it. Overwriting this in bulk is one of the shelf's two "
            "confirmations, because the values it replaces cannot be "
            "reconstructed."
        ),
    )
    kind: Optional[str] = Field(
        default=None,
        description=(
            "The adapter algorithm (`lora`, `lokr`, `loha`, `dora`, `oft`, …). "
            "Free text for the same reason `base_model` is: a trainer we have "
            "not met yet must be recordable rather than rejected."
        ),
    )
    file_kind: Optional[str] = Field(
        default=None,
        description=(
            "What the file IS: `adapter`, `checkpoint` or `unknown`. This is "
            "the correction `unknown` exists for. Never null, and never "
            "re-derived away by a later scan."
        ),
    )


class ModelEditResponse(BaseModel):
    """Body of ``PATCH /models``."""

    model_config = ConfigDict(extra="allow")

    updated: list[int] = Field(
        description="The ids that existed and were written, ascending."
    )
    fields: list[str] = Field(
        description="Which curated columns this call wrote, in request order."
    )


class ModelForgetRequest(BaseModel):
    """Body of ``POST /models/forget``."""

    model_config = ConfigDict(extra="forbid")

    ids: list[int] = Field(
        min_length=1,
        max_length=MAX_MODELS_PER_EDIT,
        description="The models to forget, by hub `model.id`.",
    )


class ForgetRefusal(BaseModel):
    """One id the forget declined, and why."""

    model_config = ConfigDict(extra="allow")

    id: int
    reason: str = Field(
        description=(
            "`no_such_model` (the id names no row) or `still_has_a_copy` (a "
            "location is `present` or `unreachable`)."
        )
    )


class ModelForgetResponse(BaseModel):
    """Body of ``POST /models/forget``: the receipt the shelf shows."""

    model_config = ConfigDict(extra="allow")

    forgotten: list[int] = Field(description="Ids whose rows are gone, ascending.")
    refused: list[ForgetRefusal] = Field(
        description=(
            "Ids that were left alone, each with a reason. Reported rather "
            "than raised: a selection is made against a list that may be "
            "seconds old, and failing the whole call because one file came "
            "back would be the wrong answer to good news."
        )
    )


class AdapterListResponse(BaseModel):
    """Body of ``GET /adapters``."""

    model_config = ConfigDict(extra="allow")

    adapters: list[ModelResponse]


class CheckpointListResponse(BaseModel):
    """Body of ``GET /checkpoints``."""

    model_config = ConfigDict(extra="allow")

    checkpoints: list[ModelResponse]


def _to_response(
    row: dict,
    locations: dict[int, list[dict]],
    attachments: dict[str, list[dict]],
) -> ModelResponse:
    return ModelResponse(
        id=int(row["id"]),
        sha256=row["sha256"],
        file_kind=row["file_kind"],
        kind=row["kind"],
        display_name=row["display_name"],
        filename=row["filename"],
        base_model=row["base_model"],
        trigger_words=row["trigger_words"],
        provenance=row["provenance"],
        training_run_id=row["training_run_id"],
        training_step=row["training_step"],
        param_count=row["param_count"],
        file_size=row["file_size"],
        hashed_at=row["hashed_at"],
        stack_id=row["stack_id"],
        stack_position=row["stack_position"],
        run_key=row["run_key"],
        added_at=row["created_at"],
        newest_file_mtime=row["newest_file_mtime"],
        member_count=row["member_count"],
        total_size=row["total_size"],
        newest_member_at=row["newest_member_at"],
        locations=[ModelLocation(**loc) for loc in locations.get(int(row["id"]), [])],
        attachments=[
            ModelAttachment(**att) for att in attachments.get(row["sha256"] or "", [])
        ],
    )


def create_router(server) -> APIRouter:
    """Create the adapter/checkpoint read router.

    Args:
        server: The Server instance, for ``hub`` (the model tables) and ``vault``
            (the attachments).

    Returns:
        The configured router.
    """
    router = APIRouter()

    def _build_list(
        file_kinds: tuple[str, ...],
        *,
        base_model: Optional[str],
        kind: Optional[str],
        q: Optional[str],
        character_id: Optional[int],
        set_id: Optional[int],
        sort: str = DEFAULT_SORT,
        direction: str = DEFAULT_DIRECTION,
    ) -> list[ModelResponse]:
        """The shared body of both list routes: three queries, whatever the size."""
        if character_id is not None and set_id is not None:
            raise HTTPException(
                status_code=400, detail="Give character_id or set_id, not both."
            )

        rows = fetch_models(
            server.hub,
            file_kinds,
            base_model=base_model,
            kind=kind,
            q=q,
            sort=sort,
            direction=direction,
        )

        if character_id is not None or set_id is not None:
            entity_type = ENTITY_CHARACTER if character_id is not None else ENTITY_SET
            entity_id = character_id if character_id is not None else set_id
            allowed = attached_hashes(server.vault, entity_type, int(entity_id))
            # Intersected in Python rather than pushed into the hub query as an
            # IN list: the two tables live in different SQLite files, and a list
            # of attachment hashes would also meet the bound-parameter limit on
            # a well-used character.
            rows = [row for row in rows if row["sha256"] in allowed]

        if not rows:
            return []
        # Hoisted deliberately: both are whole-page lookups, so calling them
        # inside the comprehension would be the N+1 this route exists to avoid.
        locations = fetch_locations(server.hub)
        attachments = fetch_attachments(server.vault)
        return [_to_response(row, locations, attachments) for row in rows]

    @router.get(
        "/adapters",
        summary="List adapters on the shelf",
        description=(
            "Every adapter registered on this machine, with each copy's location "
            "and the characters/sets in the active library that use it. "
            "`file_kind=unknown` returns the unclassified files instead — they "
            "are hashed and addressable exactly as adapters are, and they are "
            "never folded into /checkpoints. `base_model=UNASSIGNED` selects the "
            "rows that record no base model."
        ),
        tags=["model_shelf"],
        response_model=AdapterListResponse,
    )
    def list_adapters(
        request: Request,
        file_kind: str = Query(
            FILE_ADAPTER,
            description="`adapter` (default) or `unknown`. Checkpoints have their own route.",
        ),
        base_model: Optional[str] = Query(
            None,
            description=(
                "Exact match on the recorded base model, or `UNASSIGNED` for the "
                "rows that record none. Omit for all."
            ),
        ),
        kind: Optional[str] = Query(
            None, description="Adapter algorithm, e.g. `lora` or `lokr`."
        ),
        character_id: Optional[int] = Query(
            None, description="Only adapters attached to this character."
        ),
        set_id: Optional[int] = Query(
            None, description="Only adapters attached to this picture set."
        ),
        q: Optional[str] = Query(
            None,
            description="Substring of the display name, filename or trigger words.",
        ),
        sort: SortKey = Query(
            DEFAULT_SORT,
            description=(
                "`added_at` (default), `file_mtime`, `name`, `size` or "
                "`base_model`. A stacked row sorts by its stack's total size and "
                "its newest member's date, which is what it displays. Rows with "
                "no value for the key sort last in both directions."
            ),
        ),
        direction: SortDirection = Query(
            DEFAULT_DIRECTION,
            description="`desc` (default, newest/largest first) or `asc`.",
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        if file_kind not in ADAPTER_BLOCK_FILE_KINDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"file_kind must be one of {list(ADAPTER_BLOCK_FILE_KINDS)}; "
                    "checkpoints are served by GET /checkpoints."
                ),
            )
        return AdapterListResponse(
            adapters=_build_list(
                (file_kind,),
                base_model=base_model,
                kind=kind,
                q=q,
                character_id=character_id,
                set_id=set_id,
                sort=sort,
                direction=direction,
            )
        )

    @router.get(
        "/adapters/{sha256}",
        summary="One adapter by hash",
        description=(
            "Resolves on the interop identity, so it answers for an unclassified "
            "file too. A checkpoint is deliberately not reachable here: it may "
            "have no hash yet, so its block addresses rows by id instead."
        ),
        tags=["model_shelf"],
        response_model=ModelResponse,
    )
    def get_adapter(sha256: str, request: Request):
        server.auth.ensure_secure_when_required(request)
        row = fetch_model_by_hash(server.hub, sha256)
        if row is None:
            raise HTTPException(status_code=404, detail="No such adapter.")
        if row["file_kind"] == FILE_CHECKPOINT:
            raise HTTPException(
                status_code=404,
                detail="That hash is a checkpoint; see GET /checkpoints.",
            )
        return _to_response(
            row,
            fetch_locations(server.hub, int(row["id"])),
            fetch_attachments(server.vault, sha256=sha256),
        )

    @router.get(
        "/checkpoints",
        summary="List checkpoints on the shelf",
        description=(
            "The same query as /adapters, filtered to `file_kind='checkpoint'`. "
            "`sha256` is null until MissingCheckpointHashFinder has read the "
            "file, so `id` is the identifier to hold on to. Unclassified files "
            "are never returned here."
        ),
        tags=["model_shelf"],
        response_model=CheckpointListResponse,
    )
    def list_checkpoints(
        request: Request,
        base_model: Optional[str] = Query(
            None, description="Exact match, or `UNASSIGNED` for the rows with none."
        ),
        q: Optional[str] = Query(
            None, description="Substring of the display name or filename."
        ),
        sort: SortKey = Query(DEFAULT_SORT, description="Same five keys as /adapters."),
        direction: SortDirection = Query(
            DEFAULT_DIRECTION, description="`desc` (default) or `asc`."
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        return CheckpointListResponse(
            checkpoints=_build_list(
                (FILE_CHECKPOINT,),
                base_model=base_model,
                kind=None,
                q=q,
                character_id=None,
                set_id=None,
                sort=sort,
                direction=direction,
            )
        )

    @router.put(
        "/adapters/{sha256}/attachments",
        summary="Set which characters and sets use an adapter",
        description=(
            "Replaces the adapter's whole attachment set with the one given, in "
            "one transaction. This is the assignment path an external import "
            "lands on: it addresses the adapter by its interop hash, so it works "
            "for a file that arrived from anywhere. Every entity id is checked "
            "against this library before anything is written."
        ),
        tags=["model_shelf"],
        response_model=AttachmentsResponse,
    )
    def put_adapter_attachments(
        sha256: str,
        request: Request,
        attachments: list[ModelAttachmentRequest] = Body(
            ...,
            max_length=MAX_ATTACHMENTS_PER_MODEL,
            description=(
                "The complete set of characters and sets that use this adapter. "
                "An empty list detaches it from everything. At most "
                f"{MAX_ATTACHMENTS_PER_MODEL} entries: each one is a lookup "
                "inside the immediate write transaction, so a longer list "
                "would stall every other write behind it."
            ),
        ),
    ):
        server.auth.ensure_secure_when_required(request)
        row = fetch_model_by_hash(server.hub, sha256)
        if row is None:
            raise HTTPException(status_code=404, detail="No such adapter.")
        if row["file_kind"] == FILE_CHECKPOINT:
            # Attachment means "this character uses this LoRA". A base model is
            # not something a character uses in that sense, and the table is
            # keyed by a hash a checkpoint may not even have yet.
            raise HTTPException(
                status_code=400, detail="A checkpoint cannot be attached to an entity."
            )

        unknown_types = {
            att.entity_type
            for att in attachments
            if att.entity_type not in ENTITY_TYPES
        }
        if unknown_types:
            raise HTTPException(
                status_code=400,
                detail=f"entity_type must be one of {list(ENTITY_TYPES)}.",
            )

        try:
            stored = replace_attachments(
                server.vault,
                sha256,
                [(att.entity_type, att.entity_id) for att in attachments],
            )
        except UnknownAttachmentEntityError as exc:
            # 404 rather than 400: the id names nothing in this library, which is
            # the same answer every other by-id route gives.
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        logger.info(
            "Adapter %s attachments set to %d entity/entities.", sha256, len(stored)
        )
        return AttachmentsResponse(
            sha256=sha256,
            attachments=[ModelAttachment(**att) for att in stored],
        )

    @router.patch(
        "/models",
        summary="Correct what the shelf records about one or more models",
        description=(
            "Three of the shelf's five verbs on one route, because all three "
            "write a curated column and differ only in which one: **Rename** "
            "(`display_name`, one id), **Set base model** (`base_model`) and "
            "**Set kind** (`kind`, `file_kind`). Only the fields present in "
            "the body are written, so setting a base model across a selection "
            "cannot blank the names in it.\n\n"
            "Every column here is upserted with `COALESCE` by the folder "
            "scanner, so a correction made through this route survives every "
            "later scan rather than being re-derived away."
        ),
        tags=["model_shelf"],
        response_model=ModelEditResponse,
    )
    def edit_models(
        request: Request,
        payload: ModelEditRequest = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        sent = [
            field for field in CURATABLE_FIELDS if field in payload.model_fields_set
        ]
        if not sent:
            raise HTTPException(
                status_code=400,
                detail=f"Name at least one of {list(CURATABLE_FIELDS)} to write.",
            )
        changes = {field: getattr(payload, field) for field in sent}

        if "display_name" in changes and len(payload.ids) > 1:
            # A name is a fact about one file. Applying one across a selection
            # would give every row the same name, which is not a rename anyone
            # asked for and cannot be undone.
            raise HTTPException(
                status_code=400,
                detail="display_name can only be set on one model at a time.",
            )
        if (
            changes.get("file_kind") is not None
            and changes["file_kind"] not in FILE_KINDS
        ):
            raise HTTPException(
                status_code=400, detail=f"file_kind must be one of {list(FILE_KINDS)}."
            )
        if "file_kind" in changes and changes["file_kind"] is None:
            # Every file is *something*, and `unknown` is how the shelf says so.
            # A null here would leave a row that no list block matches.
            raise HTTPException(
                status_code=400,
                detail="file_kind cannot be cleared; use 'unknown'.",
            )

        _refuse_impossible_adapters(payload.ids, changes)

        updated = update_models(server.hub, payload.ids, changes)
        logger.info(
            "Shelf edit wrote %s on %d model(s).", ", ".join(sent), len(updated)
        )
        return ModelEditResponse(updated=updated, fields=sent)

    def _refuse_impossible_adapters(ids: list[int], changes: dict) -> None:
        """Refuse a correction the hub's own CHECK constraints would reject.

        ``model`` carries ``CHECK (file_kind <> 'adapter' OR sha256 IS NOT
        NULL)`` and the same for ``kind``. Left to SQLite a violation surfaces
        as a 500 naming ``CHECK constraint failed``, which tells the owner
        nothing about the file they picked.

        Decided on the **post-write** state rather than on the body, because
        three different bodies reach the same violation and only one of them
        mentions ``file_kind`` at all:

        * ``{"file_kind": "adapter"}`` on an unhashed or algorithm-less row;
        * ``{"kind": null}`` on a row that is *already* an adapter, which names
          no ``file_kind`` and so never looked like this guard's business;
        * ``{"kind": null, "file_kind": "adapter"}``, which passed a guard that
          read the *stored* kind instead of the one about to be written.

        Taking the effective value of each column collapses all three into one
        condition.
        """
        if "file_kind" not in changes and "kind" not in changes:
            # Nothing else a caller may write can reach either constraint.
            return
        placeholders = ", ".join("?" for _ in ids)
        blocked = []
        for row in server.hub.fetchall(
            f"SELECT id, filename, sha256, kind, file_kind FROM model "
            f"WHERE id IN ({placeholders})",
            tuple(ids),
        ):
            file_kind = changes.get("file_kind", row["file_kind"])
            if file_kind != FILE_ADAPTER:
                continue
            if row["sha256"] is None:
                blocked.append((row["filename"], "has not been hashed yet"))
            elif changes.get("kind", row["kind"]) is None:
                blocked.append(
                    (
                        row["filename"],
                        "would be left with no algorithm recorded; name one "
                        "with `kind`",
                    )
                )
        if not blocked:
            return
        filename, reason = blocked[0]
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(blocked)} file(s) cannot be an adapter: {filename!r} {reason}."
            ),
        )

    @router.post(
        "/models/forget",
        summary="Forget models whose files are gone",
        description=(
            "The fifth verb, and the only shelf operation that destroys "
            "curation: the row goes and takes its name, base model, kind and "
            "trigger words with it. Forgetting a folder only tombstones, which "
            "is why that needs no prompt and this one does.\n\n"
            "**Gated on the row's state, never on how many were selected.** A "
            "model is forgettable only when no copy of it is `present` or "
            "`unreachable` — the second is the "
            "we-could-not-look state, and acting on it would let one call wipe "
            "the curation for a drive that is merely unplugged. Ids that fail "
            "the gate come back under `refused` with a reason rather than "
            "failing the call, because a selection is made against a list that "
            "may be seconds old."
        ),
        tags=["model_shelf"],
        response_model=ModelForgetResponse,
    )
    def forget_shelf_models(
        request: Request,
        payload: ModelForgetRequest = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        forgotten, refused = forget_models(server.hub, payload.ids)
        logger.info(
            "Shelf forgot %d model(s); %d refused.", len(forgotten), len(refused)
        )
        return ModelForgetResponse(
            forgotten=forgotten,
            refused=[ForgetRefusal(**item) for item in refused],
        )

    return router
