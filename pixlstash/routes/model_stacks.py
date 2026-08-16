"""Collapse loose adapters into stacks: propose one, apply one.

Two routes, and the split between them IS the design. ``GET`` reads the shelf
and returns groups it believes belong together; it writes nothing, so the whole
dry run can be drawn before the owner decides. ``POST`` is the only half that
writes, and it is reached only after they have seen that.

**Detection proposes, it never applies** — the house rule this module is the
third instance of, after folder monitoring and the ai-toolkit run scan.

**Tier 1 only, on purpose.** Files differing solely by a training step are one
run and there is nothing for a person to weigh, so the tier gets one dry run and
one confirmation. Tier 2 (prefix grouping, ``JimmyBuss`` beside ``JimmyBuss2``)
needs per-group adjudication with counter-evidence and is not here yet; its
evidence model is a design question rather than missing code.

Authorization: both routes are ``OWNER_ONLY``, declared in
``pixlstash/authz/registry.py`` and never inline. Neither touches the host
filesystem — detection reads `model` rows the scan already wrote, and applying
writes hub columns — so neither belongs on the §16.3 locality tier that
``model-moves`` and the import block sit on. They surface folder ids, not paths.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.stack_detector import (
    MIN_GROUP_SIZE,
    StackRefused,
    apply_stack,
    propose_stacks,
)

logger = get_logger(__name__)

# Ceiling on one apply. A stack is a training run; runs have tens of steps, not
# thousands, and a caller sending more is confused rather than lucky.
MAX_MEMBERS_PER_STACK = 200


class ProposedMemberResponse(BaseModel):
    """One model a proposal would put into a stack."""

    model_config = ConfigDict(extra="allow")

    model_id: int
    filename: str = Field(description="Basename, which is what the strip reads.")
    step: Optional[int] = Field(
        default=None,
        description=(
            "The training step the filename records, or null for the bare final "
            "file. A group with no stepped member is never proposed."
        ),
    )
    file_size: Optional[int] = None


class StackProposalResponse(BaseModel):
    """One group detection believes is a training run."""

    model_config = ConfigDict(extra="allow")

    tier: str = Field(
        description=(
            "`step_group` — files differing only by a training step. The tier "
            "that needs no judgement, so it is confirmed in one batch."
        )
    )
    key: str = Field(
        description="Stable per-folder identity for the group, for the UI's list."
    )
    name: str = Field(description="The derived name the members share.")
    folder_id: int = Field(
        description=(
            "Groups never span folders: two runs on different disks can share a "
            "name, and collapsing across them would invent a run and put one "
            "stack's members on two drives."
        )
    )
    members: list[ProposedMemberResponse] = Field(
        description="Cover first: the bare final, else the highest step."
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
            "this list; the bare final leads, else the highest step."
        )
    )
    name: Optional[str] = Field(
        default=None,
        description="What to call the stack. Null leaves it unnamed.",
    )


class ApplyStackResponse(BaseModel):
    """Body of ``POST /model-stacks``."""

    model_config = ConfigDict(extra="allow")

    stack_id: int
    member_count: int


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
        summary="Groups of loose adapters that look like one training run",
        description=(
            "The dry run. Returns the groups detection believes belong together "
            "and **writes nothing**, so the whole list can be drawn before the "
            "owner decides about any of it.\n\n"
            "Only adapters with no stack are considered: a run imported from "
            "ai-toolkit is already a stack, and a stack that has been ratified "
            "must never be re-proposed. Grouping is per folder and needs at "
            "least one member carrying a step suffix, or the shared name is a "
            "duplicate rather than a run."
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
            "is a 409 and nothing is written."
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
                    "training run has tens of steps, not thousands."
                ),
            )
        if len(unique_ids) < MIN_GROUP_SIZE:
            raise HTTPException(
                status_code=400, detail="A stack needs at least two models."
            )
        try:
            stack_id = apply_stack(
                server.hub, unique_ids, (payload.name or "").strip() or None
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

    return router
