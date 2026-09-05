"""Collapse loose adapters into stacks: propose, apply, fuse, undo, curate.

Five routes. ``GET`` reads the shelf and returns groups it believes belong
together; it writes nothing, so the whole dry run can be drawn before the owner
decides. ``POST`` is the half that writes, and it is reached only after they
have seen that - or from the shelf directly, where it also **fuses**: given
models that are already stacked it absorbs their stacks whole, which is how
stacking two stacks yields one. ``DELETE`` is the undo, and the shelf's first:
it breaks a stack up and leaves every file exactly where it sits on disk.

The last two curate a stack that already exists, and both are the owner
overruling the filename heuristic. ``PATCH .../cover`` chooses which member the
shelf draws for the run, which ``POST`` deliberately will not let a caller do;
``DELETE .../members/{model_id}`` takes one file back out of a run it does not
belong to. Like the unstack, neither touches a byte on disk.

**Detection proposes, it never applies** - the house rule this module is the
third instance of, after folder monitoring and the ai-toolkit run scan.

**A stack is a subject, not a training run.** Groups come back as
``step_group`` (one version, files differing only by a step) or
``version_group`` (``Foxglove`` beside ``Foxglove_v2`` - several runs of one
subject, covered by the newest version). Prefix grouping (``JimmyVehicle``
beside ``JimmyVehicle2``) is still not here: only an explicit ``v<digits>``
token is read as a version, and the ambiguous case needs per-group
adjudication with counter-evidence, which is a design question rather than
missing code.

Authorization: all five routes are ``OWNER_ONLY``, declared in
``pixlstash/authz/registry.py`` and never inline. None touches the host
filesystem - detection reads `model` rows the scan already wrote, and applying,
fusing, unstacking, covering and releasing write hub columns - so none belongs
on the §16.3 locality tier that ``model-moves`` and the import block sit on.
They surface folder ids, not paths.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.stack_detector import (
    MAX_MEMBERS_PER_STACK,
    MIN_GROUP_SIZE,
    StackRefused,
    apply_stack,
    propose_stacks,
    remove_member,
    set_cover,
    unstack,
)

logger = get_logger(__name__)

# The ceiling is imported, not redeclared: `apply_stack` widens the set when
# fusing, so it owns the only count that is authoritative. This route's check
# below is the cheap early one on what the caller actually sent.


class ProposedMemberResponse(BaseModel):
    """One model a proposal would put into a stack."""

    model_config = ConfigDict(extra="allow")

    model_id: int
    filename: str = Field(description="Basename, which is what the strip reads.")
    step: Optional[int] = Field(
        default=None,
        description=(
            "The training step the filename records, or null for the bare final "
            "file of its version."
        ),
    )
    version: Optional[str] = Field(
        default=None,
        description=(
            "The trailing version token the filename carries (`v2`, `V2.1`), "
            "verbatim, or null when it carries none. Compare it by parsing, not "
            "as a string: case and a `.0` are not differences. A group with "
            "neither a stepped member nor two versions is never proposed."
        ),
    )
    file_size: Optional[int] = None


class StackProposalResponse(BaseModel):
    """One group detection believes is a single subject."""

    model_config = ConfigDict(extra="allow")

    tier: str = Field(
        description=(
            "`step_group` - one version, files differing only by a training "
            "step. `version_group` - two or more versions of one subject, so "
            "the group spans training runs."
        )
    )
    key: str = Field(
        description="Stable per-folder identity for the group, for the UI's list."
    )
    name: str = Field(
        description=(
            "The subject the members share, carrying the version when they all "
            "have the same one and dropping it when the group spans versions."
        )
    )
    folder_id: int = Field(
        description=(
            "Groups never span folders: two runs on different disks can share a "
            "name, and collapsing across them would invent a run and put one "
            "stack's members on two drives."
        )
    )
    members: list[ProposedMemberResponse] = Field(
        description=(
            "Cover first: newest version, then its bare final, else its highest step."
        )
    )
    total_size: int = Field(
        description="Sum over the group, which is what a stack shows."
    )


class StackProposalsResponse(BaseModel):
    """Body of ``GET /model-stacks/proposals``."""

    model_config = ConfigDict(extra="allow")

    proposals: list[StackProposalResponse]


class ApplyStackRequest(BaseModel):
    """Body of ``POST /model-stacks``."""

    model_config = ConfigDict(extra="forbid")

    model_ids: list[int] = Field(
        description=(
            "The models to collapse, by hub `model.id`. Order is **recomputed** "
            "server-side, so the caller cannot choose the cover by reordering "
            "this list; the newest version leads, then its bare final, else its "
            "highest step."
        )
    )
    name: Optional[str] = Field(
        default=None,
        description=(
            "What to call the stack. Null leaves it unnamed, except when fusing "
            " - there it inherits the first name among the stacks absorbed."
        ),
    )
    fuse: bool = Field(
        default=False,
        description=(
            "Allow models that are **already stacked**, absorbing their stacks "
            "whole - this is what makes stacking two stacks fuse them. Every "
            "member of every stack named comes along, including ones not listed "
            "in `model_ids`, because a stack is atomic and half of one is not a "
            "stack. Off by default: the proposals flow confirms a dry run over "
            "loose files, and a row stacked in the meantime must be left where "
            "it is rather than torn out."
        ),
    )


class ApplyStackResponse(BaseModel):
    """Body of ``POST /model-stacks``."""

    model_config = ConfigDict(extra="allow")

    stack_id: int
    member_count: int


class UnstackResponse(BaseModel):
    """Body of ``DELETE /model-stacks/{stack_id}``."""

    model_config = ConfigDict(extra="allow")

    released: int = Field(description="How many models are loose on the shelf again.")


class SetCoverRequest(BaseModel):
    """Body of ``PATCH /model-stacks/{stack_id}/cover``."""

    model_config = ConfigDict(extra="forbid")

    model_id: int = Field(
        description=(
            "The member to promote to the cover, by hub `model.id`. It must "
            "already be in this stack. Example: `101`."
        )
    )


class SetCoverResponse(BaseModel):
    """Body of ``PATCH /model-stacks/{stack_id}/cover``."""

    model_config = ConfigDict(extra="allow")

    stack_id: int
    model_ids: list[int] = Field(
        description=(
            "The stack's members in their new order, cover first. Example: "
            "`[101, 102]`."
        )
    )


class RemoveMemberResponse(BaseModel):
    """Body of ``DELETE /model-stacks/{stack_id}/members/{model_id}``."""

    model_config = ConfigDict(extra="allow")

    released: int = Field(
        description=(
            "How many models are loose on the shelf again - one, or both when "
            "the removal left a stack of one and it dissolved."
        )
    )
    dissolved: bool = Field(
        description="Whether the stack itself is gone, because one file is not a run."
    )


def create_router(server) -> APIRouter:
    """Create the adapter-stack router.

    Args:
        server: The Server instance, for ``hub`` and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    @router.get(
        "/model-stacks/proposals",
        summary="Groups of loose adapters that look like one subject",
        description=(
            "The dry run. Returns the groups detection believes belong together "
            "and **writes nothing**, so the whole list can be drawn before the "
            "owner decides about any of it.\n\n"
            "Only adapters with no stack are considered: a run imported from "
            "ai-toolkit is already a stack, and a stack that has been ratified "
            "must never be re-proposed. Grouping is per folder, on the name with "
            "the training step and the version token removed, and needs a "
            "difference those account for - a stepped member, or two versions. "
            "Without either, the shared name is a duplicate rather than a "
            "subject with a history."
        ),
        tags=["model_shelf"],
        response_model=StackProposalsResponse,
    )
    def list_stack_proposals(request: Request):
        server.auth.ensure_secure_when_required(request)
        proposals = propose_stacks(server.hub)
        return StackProposalsResponse(
            proposals=[
                StackProposalResponse(
                    tier=p.tier,
                    key=p.key,
                    name=p.name,
                    folder_id=p.folder_id,
                    total_size=p.total_size,
                    members=[
                        ProposedMemberResponse(
                            model_id=m.model_id,
                            filename=m.filename,
                            step=m.step,
                            version=m.version,
                            file_size=m.file_size,
                        )
                        for m in p.members
                    ],
                )
                for p in proposals
            ]
        )

    @router.post(
        "/model-stacks",
        summary="Collapse models into one stack",
        description=(
            "The applying half. Creates an `adapter_stack` and points every "
            "given model at it, cover first.\n\n"
            "**The gate is re-read inside the write transaction.** A proposal is "
            "a snapshot the owner may have been looking at for a minute, so a "
            "row stacked in the meantime is dropped rather than torn out of the "
            "stack it already has; if fewer than two survive that check the call "
            "is a 409 and nothing is written.\n\n"
            "**`fuse` stacks the stacks.** With it, models that already belong "
            "to a stack are taken, and their stacks are absorbed *whole* and "
            "then removed - so stacking two stacks yields one. Without it (the "
            "default) an already-stacked model is refused, which is what the "
            "proposals flow needs."
        ),
        tags=["model_shelf"],
        response_model=ApplyStackResponse,
    )
    def create_stack(request: Request, payload: ApplyStackRequest = Body(...)):
        server.auth.ensure_secure_when_required(request)
        # Counted on the UNIQUE ids, because `apply_stack` de-dupes: a client
        # that repeated an id would otherwise be told it sent too many models
        # while its actual selection was well under the ceiling.
        unique_ids = list(dict.fromkeys(payload.model_ids))
        if len(unique_ids) > MAX_MEMBERS_PER_STACK:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"A stack takes at most {MAX_MEMBERS_PER_STACK} models; a "
                    "subject has a few versions of tens of steps, not thousands."
                ),
            )
        if len(unique_ids) < MIN_GROUP_SIZE:
            raise HTTPException(
                status_code=400, detail="A stack needs at least two models."
            )
        try:
            stack_id = apply_stack(
                server.hub,
                unique_ids,
                (payload.name or "").strip() or None,
                fuse=payload.fuse,
            )
        except StackRefused as exc:
            # 409 rather than 400: the request was well formed and was refused by
            # the state of the shelf, which is what the receipt has to say.
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        row = server.hub.fetchone(
            "SELECT COUNT(*) AS n FROM model WHERE stack_id = ?", (stack_id,)
        )
        return ApplyStackResponse(
            stack_id=stack_id, member_count=int(row["n"] or 0) if row else 0
        )

    @router.delete(
        "/model-stacks/{stack_id}",
        summary="Break a stack apart",
        description=(
            "The undo. Clears `stack_id` and `stack_position` on every member "
            "and removes the `adapter_stack` row, leaving the files loose on the "
            "shelf as the individual adapters they always were.\n\n"
            "**Nothing on disk is touched** - this writes two hub columns and "
            "deletes one row; no file is moved, renamed or unlinked. Unknown "
            "`stack_id` is a 404 and nothing is written.\n\n"
            "The released models become *loose*, so "
            "`GET /model-stacks/proposals` may offer to regroup them. That is "
            "not a bug: they are still files whose names look like one subject. "
            "Unstacking undoes a grouping; it does not record a refusal."
        ),
        tags=["model_shelf"],
        response_model=UnstackResponse,
    )
    def delete_stack(request: Request, stack_id: int):
        server.auth.ensure_secure_when_required(request)
        try:
            released = unstack(server.hub, stack_id)
        except StackRefused as exc:
            # 404 rather than the 409 the apply uses: this one is "that stack is
            # not there", which is a wrong address rather than a shelf that
            # moved under a well-aimed request.
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return UnstackResponse(released=released)

    @router.patch(
        "/model-stacks/{stack_id}/cover",
        summary="Choose which member covers a stack",
        description=(
            "Moves one member to `stack_position` 0, which is what the shelf "
            "draws for the whole run. The other members keep their relative "
            "order and close the gap behind it, exactly as "
            "`PATCH /stacks/{stack_id}/members/{picture_id}` does for a picture "
            "stack.\n\n"
            "**This is the only way a cover is chosen by hand.** `POST "
            "/model-stacks` recomputes the order from the filenames and ignores "
            "the order it was given, which is right for a heuristic and wrong "
            "once the owner knows the run's best checkpoint is step 1500 rather "
            "than the file the trainer wrote last.\n\n"
            "**The choice sticks.** Nothing recomputes a stack's order after it "
            "is built - detection only ever looks at *loose* adapters, and the "
            "run importer's upsert keeps an existing `stack_position` - so a "
            "chosen cover survives a re-scan and a re-import.\n\n"
            "Nothing on disk is touched. A `model_id` that is not in this stack "
            "is a 404 and nothing is written."
        ),
        tags=["model_shelf"],
        response_model=SetCoverResponse,
    )
    def set_stack_cover(
        request: Request, stack_id: int, payload: SetCoverRequest = Body(...)
    ):
        server.auth.ensure_secure_when_required(request)
        try:
            model_ids = set_cover(server.hub, stack_id, payload.model_id)
        except StackRefused as exc:
            # 404 for both refusals this can raise: "no such stack" and "that
            # model is not in it" are each a wrong address rather than a shelf
            # that moved under a well-aimed request.
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return SetCoverResponse(stack_id=stack_id, model_ids=model_ids)

    @router.delete(
        "/model-stacks/{stack_id}/members/{model_id}",
        summary="Take one model out of a stack",
        description=(
            "The single-member counterpart to breaking the whole stack up. "
            "Clears `stack_id` and `stack_position` on that one model, leaving "
            "it loose on the shelf as the individual adapter it always was, and "
            "renumbers the survivors so the stack keeps a cover - removing the "
            "cover promotes whichever member was behind it.\n\n"
            "**A stack of one is not a stack.** Removing the second-to-last "
            "member dissolves the whole thing: both files go loose and the "
            "`adapter_stack` row is deleted, which the response reports as "
            "`dissolved`.\n\n"
            "**Nothing on disk is touched** - no file is moved, renamed or "
            "unlinked. The released model becomes *loose*, so "
            "`GET /model-stacks/proposals` may offer to regroup it, exactly as "
            "after an unstack. A `model_id` that is not in this stack is a 404 "
            "and nothing is written."
        ),
        tags=["model_shelf"],
        response_model=RemoveMemberResponse,
    )
    def delete_stack_member(request: Request, stack_id: int, model_id: int):
        server.auth.ensure_secure_when_required(request)
        try:
            released, dissolved = remove_member(server.hub, stack_id, model_id)
        except StackRefused as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return RemoveMemberResponse(released=released, dissolved=dissolved)

    return router
