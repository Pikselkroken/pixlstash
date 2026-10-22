"""The VRAM arbiter's controls, and the choice of PixlStash's GPU.

What the owner can do about work waiting for the GPU (start it anyway, cancel
it, free an idle ComfyUI), and which GPU PixlStash itself runs on. The state
these act on is the ``resources`` block of ``GET /workers/progress``; see
``docs/backend_architecture.md`` §7, "VRAM arbitration".
"""

from __future__ import annotations

import os
import sys
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash import gpu_select
from pixlstash.pixl_logging import get_logger
from pixlstash.resources.arbiter import LeaseState, Tenant, arbiter
from pixlstash.resources.comfyui_tenant import probe as comfyui_probe
from pixlstash.server_config_io import persist_server_config
from pixlstash.services.comfyui_service import (
    RUN_CLIENT_ID,
    cancel_held_runs,
)

logger = get_logger(__name__)


class LeaseAnswer(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str = "success"
    lease: dict | None = None


class FreeAnswer(BaseModel):
    status: str = "success"
    freed_mb: int = Field(description="How much ComfyUI's allocation fell, MB.")


class GpuRow(BaseModel):
    uuid: str
    name: str
    vendor: str
    total_mb: int
    free_mb: int
    integrated: bool = False
    backend: str
    backend_installed: bool
    backend_active: bool
    used_by: list[Literal["comfyui", "aitoolkit"]] = Field(default_factory=list)
    pickable: bool = True
    reason: str | None = None


class GpuList(BaseModel):
    gpus: list[GpuRow] = Field(default_factory=list)
    selected: str = Field(description="auto, or the UUID the owner picked.")
    resolved: str | None = Field(
        None, description="The UUID of the GPU this server runs on now."
    )
    auto_reason: str = Field(description="What Auto picks, in one sentence.")
    suggestions: list[str] = Field(default_factory=list)
    restart_required: bool = Field(
        False,
        description="The choice differs from what this server runs on until it restarts.",
    )


class GpuChoice(BaseModel):
    gpu: str = Field(min_length=1, max_length=128, description="auto or a GPU UUID.")


class GpuChosen(BaseModel):
    status: str = "success"
    selected: str
    restart_required: bool = True


def create_router(server) -> APIRouter:
    """Build the resources router.

    Args:
        server: The running :class:`~pixlstash.server.Server`.
    """
    router = APIRouter()

    def _waiting_lease(lease_id: str):
        lease = arbiter().lease(lease_id)
        if lease is None:
            raise HTTPException(status_code=404, detail="No such lease.")
        if lease.state is not LeaseState.WAITING:
            raise HTTPException(
                status_code=409, detail="That work is not waiting for the GPU."
            )
        return lease

    def _row(lease_id: str) -> dict | None:
        snapshot = arbiter().snapshot()
        return next((row for row in snapshot["leases"] if row["id"] == lease_id), None)

    @router.post(
        "/resources/leases/{lease_id}/run-anyway",
        summary="Start waiting GPU work now",
        description=(
            "Starts one lease that is waiting for GPU memory now, whatever it "
            "needs. The other waiting work keeps its order. It can run out of "
            "memory, and so can whatever else is on the GPU."
        ),
        response_model=LeaseAnswer,
    )
    def run_anyway(request: Request, lease_id: str):
        server.auth.ensure_secure_when_required(request)
        _waiting_lease(lease_id)
        arbiter().run_anyway(lease_id)
        return LeaseAnswer(lease=_row(lease_id))

    @router.post(
        "/resources/leases/{lease_id}/cancel",
        summary="Cancel waiting GPU work",
        description=(
            "Withdraws a lease that is waiting for GPU memory, and cancels the "
            "ComfyUI run behind it, which never reaches ComfyUI."
        ),
        response_model=LeaseAnswer,
    )
    def cancel(request: Request, lease_id: str):
        server.auth.ensure_secure_when_required(request)
        lease = _waiting_lease(lease_id)
        if lease.run_id and not cancel_held_runs(lease.run_id):
            # Granted and sent a moment ago: it is a ComfyUI run now, which
            # Stop on its row interrupts. Its lease is not ours to drop.
            raise HTTPException(
                status_code=409, detail="That run has just started on ComfyUI."
            )
        if arbiter().cancel(lease_id) is None:
            raise HTTPException(
                status_code=409, detail="That work has just been given the GPU."
            )
        logger.info("The owner cancelled waiting lease %s (%s)", lease_id, lease.label)
        return LeaseAnswer()

    @router.post(
        "/resources/comfyui/free",
        summary="Free ComfyUI's GPU memory",
        description=(
            "Asks the ComfyUI PixlStash submits to to unload its models now. "
            "Refused with 409 while anything is queued or running on it."
        ),
        response_model=FreeAnswer,
    )
    def free_comfyui(request: Request):
        server.auth.ensure_secure_when_required(request)
        url = (getattr(server._user, "comfyui_url", None) or "").rstrip("/")
        if not url:
            raise HTTPException(status_code=409, detail="No ComfyUI is configured.")
        probe = comfyui_probe(RUN_CLIENT_ID, url)
        if probe.stats(max_age_s=0.0) is None:
            raise HTTPException(status_code=503, detail="ComfyUI is not reachable.")
        freed = probe.free()
        if freed is None:
            raise HTTPException(
                status_code=409,
                detail="ComfyUI is busy; it can be freed once nothing is queued on it.",
            )
        arbiter().kick()
        return FreeAnswer(freed_mb=freed)

    def _torch_cuda() -> bool:
        torch = sys.modules.get("torch")
        try:
            return bool(
                torch is not None
                and torch.cuda.is_available()
                and getattr(torch.version, "hip", None) is None
            )
        except Exception as exc:
            logger.debug("Could not ask torch for CUDA: %s", exc)
            return False

    @router.get(
        "/compute/gpus",
        summary="The GPUs PixlStash can run on",
        description=(
            "Every GPU this machine has, what uses it, and which one "
            "PixlStash runs on. selected is the owner's choice (auto or a "
            "UUID); resolved is the GPU in use now."
        ),
        response_model=GpuList,
    )
    def list_gpus(request: Request):
        server.auth.ensure_secure_when_required(request)
        config = server._server_config
        comfy = arbiter().probe(Tenant.COMFYUI)
        comfy_uuid = comfy.device() if comfy is not None else None
        if comfy_uuid and comfy_uuid != config.get(gpu_select.COMFYUI_GPU):
            # Remembered, so Auto can avoid ComfyUI's card at a boot where
            # ComfyUI is not running yet.
            config[gpu_select.COMFYUI_GPU] = comfy_uuid
            persist_server_config(server.server_config_path, config)
        comfy_uuid = comfy_uuid or config.get(gpu_select.COMFYUI_GPU)
        selected = config.get(gpu_select.SETTING) or gpu_select.AUTO
        gpus = gpu_select.inventory()
        auto_uuid, auto_reason = gpu_select.choose(gpus, gpu_select.AUTO, comfy_uuid)
        running_on = os.environ.get(gpu_select.RESOLVED_ENV)
        if running_on is None and gpus:
            first = (os.environ.get("CUDA_VISIBLE_DEVICES") or "").split(",")[0]
            running_on = first if first.startswith("GPU-") else gpus[0]["uuid"]
        wanted = auto_uuid if selected == gpu_select.AUTO else selected
        cuda = _torch_cuda()
        rows = [
            GpuRow(
                **gpu,
                backend_installed=cuda,
                backend_active=cuda,
                used_by=["comfyui"] if gpu["uuid"] == comfy_uuid else [],
            )
            for gpu in gpus
        ]
        return GpuList(
            gpus=rows,
            selected=selected,
            resolved=running_on,
            auto_reason=auto_reason,
            restart_required=len(gpus) > 1 and wanted != running_on,
        )

    @router.patch(
        "/compute/gpu",
        summary="Choose PixlStash's GPU",
        description=(
            "Stores which GPU PixlStash runs on: auto, or a UUID from "
            "GET /compute/gpus. It takes effect when the server restarts."
        ),
        response_model=GpuChosen,
    )
    def choose_gpu(request: Request, body: GpuChoice):
        server.auth.ensure_secure_when_required(request)
        if body.gpu != gpu_select.AUTO:
            known = {gpu["uuid"] for gpu in gpu_select.inventory()}
            if body.gpu not in known:
                raise HTTPException(status_code=422, detail="No such GPU here.")
        server._server_config[gpu_select.SETTING] = body.gpu
        persist_server_config(server.server_config_path, server._server_config)
        logger.info("PixlStash's GPU set to %s; applies at the next restart", body.gpu)
        return GpuChosen(selected=body.gpu)

    return router
