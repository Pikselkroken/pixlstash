"""HTTP API for ai-toolkit LoRA training jobs.

This router exposes the training feature under ``/api/v1``: enqueueing and
cancelling training jobs, polling their progress, and listing the training
recipes (:mod:`pixlstash.training.presets`). A finished run is not served from here: it lands on the model
shelf, which is where a trained LoRA is listed, downloaded and deleted.

Authorization is declared, not coded here: every endpoint is listed in
``pixlstash/authz/registry.py`` (``ROUTE_POLICIES``) and enforced by the central
``AuthzGate``. Reads are ``owner_only`` (share tokens never reach them). Routes
that start or signal a host trainer process are ``local_owner_only`` (§16.3):
training is a privileged compute and filesystem operation, so a remote owner is
refused unless ``allow_remote_host_ops`` is set. Handlers carry no inline owner
or scope checks.
"""

import json
import os
import re
import shutil
from datetime import datetime
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.database import DBPriority
from pixlstash.db_models import User
from pixlstash.hub import training_runs
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_folder_scanner import SOURCE_FOLDER_KIND
from pixlstash.training import ai_toolkit_install, presets
from pixlstash.training.config_render import TunableOverrideError
from pixlstash.utils.aitoolkit_run import read_output_root
from pixlstash.utils.path_utils import path_is_within, resolve_path_within
from pixlstash.utils.service.export_utils import ExportUtils
from pixlstash.utils.service.user_settings_utils import (
    apply_user_config_patch,
    validate_ai_toolkit_settings,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# JSON parsing helpers for progress fields persisted as text columns
# ---------------------------------------------------------------------------


def _parse_json_field(raw: str | None, field_name: str, job_uuid: str | None = None):
    """Parse a JSON text column, logging (not raising) on malformed content.

    Args:
        raw: The stored JSON string, or ``None``.
        field_name: Column name (for the log message).
        job_uuid: Owning job uuid (for the log message).

    Returns:
        The parsed value, or ``None`` when *raw* is empty or unparseable.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("Could not parse %s for job %s: %s", field_name, job_uuid, exc)
        return None


# An absolute host path, as it appears inside a string: a ``file://`` URI, or a
# POSIX or Windows path. A path runs to the end of the line, a quote, a ``<>|``
# or a closing bracket, so a name with spaces (``/Users/Alice Smith/...``) is
# redacted whole. Text after a path on the same line is redacted with it: over-
# redaction is the safe direction. The lookbehind keeps URLs, ``1/2`` and
# ``and/or`` out of the path alternative.
_HOST_PATH_RE = re.compile(
    r"file://[^\r\n'\"<>|)\]}]*"
    r"|(?<![\w./:])(?:[A-Za-z]:[\\/]|/)[^\r\n'\"<>|)\]}]+"
)


def _redact_host_paths(value):
    """Replace absolute host paths in a response value with a placeholder.

    Job responses go to the owner's browser and to any tool they run, so the
    host's install and library locations must not travel with them. Relative
    paths pass through unchanged.

    Args:
        value: A JSON-serialisable value (dict, list, str or scalar).

    Returns:
        A copy of *value* with each absolute path replaced by ``<host path>``.
    """
    if isinstance(value, dict):
        return {key: _redact_host_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_host_paths(item) for item in value]
    if isinstance(value, str):
        return _HOST_PATH_RE.sub("<host path>", value)
    return value


def _latest_loss(loss_curve) -> float | None:
    """Return the loss value of the last point in a loss curve, if any."""
    if not isinstance(loss_curve, list) or not loss_curve:
        return None
    last = loss_curve[-1]
    if isinstance(last, dict) and "loss" in last:
        try:
            return float(last["loss"])
        except (TypeError, ValueError):
            return None
    return None


def create_router(server) -> APIRouter:
    """Build the training/LoRA router bound to *server*.

    Args:
        server: The application server (provides ``auth`` and ``vault``).

    Returns:
        The configured :class:`fastapi.APIRouter`.
    """
    router = APIRouter()

    def _ensure_secure(request: Request) -> None:
        server.auth.ensure_secure_when_required(request)

    def _image_root() -> str:
        return server.vault.image_root

    def _store():
        """The hub and library the training runs are read from and written to.

        Every run is scoped to the library open now: another library's runs are
        neither listed nor addressable, so their vault ids mean nothing here.
        """
        hub = server.vault.hub
        library_uuid = server.vault.library_uuid
        if hub is None or not library_uuid:
            raise HTTPException(status_code=503, detail="No library is open.")
        return hub, library_uuid

    def _training_root() -> str:
        return os.path.join(_image_root(), "training")

    def _shelf_source_folders() -> list[str]:
        """Paths of the shelf's registered ``source`` folders, oldest first."""
        hub = server.vault.hub
        if hub is None:
            return []
        rows = hub.fetchall(
            "SELECT path FROM model_folder WHERE kind = ? ORDER BY id",
            (SOURCE_FOLDER_KIND,),
        )
        return [row["path"] for row in rows]

    def _count_runs(root: str) -> int:
        """How many ai-toolkit run folders *root* holds; 0 if it is absent."""
        if not os.path.isdir(root):
            return 0
        try:
            return len(read_output_root(root))
        except OSError as exc:
            logger.warning("Could not count ai-toolkit runs in %s: %s", root, exc)
            return 0

    # ── Request models ────────────────────────────────────────────────────

    class CreateJobRequest(BaseModel):
        """Body for enqueueing a new training job."""

        model_config = ConfigDict(extra="forbid")

        set_id: Optional[int] = Field(
            default=None,
            description=(
                "Picture-set id to train on. Mutually exclusive with "
                "character_id; provide exactly one."
            ),
        )
        character_id: Optional[int] = Field(
            default=None,
            description=(
                "Character id to train on. Mutually exclusive with set_id; "
                "provide exactly one."
            ),
        )
        name: str = Field(
            ..., min_length=1, description="Human-readable name for the job."
        )
        trigger_word: str = Field(
            ...,
            min_length=1,
            description="Caption trigger word baked into the training dataset.",
        )
        preset: str = Field(
            default=presets.DEFAULT_PRESET,
            description="The training recipe's id (GET /training/presets).",
        )
        subject: Optional[Literal["person", "style", "thing"]] = Field(
            default=None,
            description=(
                "What the LoRA learns. Defaults to 'person' for a character, "
                "and must be given for a set."
            ),
        )
        effort: Literal["gentle", "standard", "strong"] = Field(
            default="standard", description="How hard to train."
        )
        fit: Literal["auto", "save_memory", "best_quality"] = Field(
            default="auto", description="How to fit the run to this GPU."
        )
        pins: Optional[dict[str, Any]] = Field(
            default=None,
            description=(
                "The owner's Advanced values, keyed by tunable setting "
                "(e.g. 'train.steps'). Each wins over the derived value; a "
                "locked recipe key is refused with 422 naming it."
            ),
        )

    # ── Response models ───────────────────────────────────────────────────

    class CreateJobResponse(BaseModel):
        """The job created by ``POST /training/jobs``."""

        model_config = ConfigDict(extra="allow")

        id: int = Field(..., description="Primary key of the created job.")
        uuid: str = Field(..., description="Stable external identifier of the job.")
        name: str = Field(..., description="Human-readable job name.")
        status: str = Field(..., description="Lifecycle status (queued on create).")
        set_id: Optional[int] = Field(
            default=None, description="Picture-set scope, if set-scoped."
        )
        character_id: Optional[int] = Field(
            default=None, description="Character scope, if character-scoped."
        )
        base_model: Optional[str] = Field(
            default=None, description="Resolved base checkpoint for the run."
        )
        trigger_word: Optional[str] = Field(
            default=None, description="Caption trigger word for the run."
        )
        resolved_profile: Optional[dict] = Field(
            default=None,
            description=(
                "The resolver's result: the recipe, the choices, the memory "
                "profile and every value with where it came from."
            ),
        )
        created_at: Optional[datetime] = Field(
            default=None, description="UTC creation timestamp."
        )

    class JobSummaryResponse(BaseModel):
        """One row in the job listing (summary fields + status)."""

        model_config = ConfigDict(extra="allow")

        id: int = Field(..., description="Primary key of the job.")
        uuid: str = Field(..., description="Stable external identifier.")
        name: str = Field(..., description="Human-readable job name.")
        status: str = Field(
            ...,
            description=("queued | running | stopping | stopped | completed | error."),
        )
        set_id: Optional[int] = Field(
            default=None, description="Picture-set scope, if set-scoped."
        )
        character_id: Optional[int] = Field(
            default=None, description="Character scope, if character-scoped."
        )
        base_model: Optional[str] = Field(
            default=None, description="Base checkpoint for the run."
        )
        trigger_word: Optional[str] = Field(
            default=None, description="Caption trigger word for the run."
        )
        step: int = Field(0, description="Latest reported training step.")
        total_steps: Optional[int] = Field(
            default=None, description="Planned total number of training steps."
        )
        created_at: Optional[datetime] = Field(
            default=None, description="UTC creation timestamp."
        )
        started_at: Optional[datetime] = Field(
            default=None, description="UTC timestamp when the run started."
        )
        completed_at: Optional[datetime] = Field(
            default=None, description="UTC timestamp when the run finished."
        )

    class JobDetailResponse(BaseModel):
        """Full detail for a single job."""

        model_config = ConfigDict(extra="allow")

        id: int = Field(..., description="Primary key of the job.")
        uuid: str = Field(..., description="Stable external identifier.")
        name: str = Field(..., description="Human-readable job name.")
        status: str = Field(..., description="Lifecycle status.")
        set_id: Optional[int] = Field(
            default=None, description="Picture-set scope, if set-scoped."
        )
        character_id: Optional[int] = Field(
            default=None, description="Character scope, if character-scoped."
        )
        base_model: Optional[str] = Field(
            default=None, description="Base checkpoint for the run."
        )
        trigger_word: Optional[str] = Field(
            default=None, description="Caption trigger word for the run."
        )
        step: int = Field(0, description="Latest reported training step.")
        total_steps: Optional[int] = Field(
            default=None, description="Planned total number of training steps."
        )
        loss_curve: Optional[list] = Field(
            default=None,
            description="Parsed list of {step, loss} points, if recorded.",
        )
        rendered_config: Optional[dict] = Field(
            default=None,
            description="Full ai-toolkit config used for the run (parsed JSON).",
        )
        report: Optional[str] = Field(
            default=None, description="Final human-readable run report."
        )
        sample_image_paths: Optional[list] = Field(
            default=None,
            description="Parsed list of sample image paths produced during the run.",
        )
        dataset_picture_ids: Optional[list] = Field(
            default=None,
            description="Parsed list of picture ids frozen into the dataset.",
        )
        ai_toolkit_commit: Optional[str] = Field(
            default=None,
            description="ai-toolkit git commit the run was launched with.",
        )
        error: Optional[str] = Field(
            default=None, description="Error message when status is 'error'."
        )
        created_at: Optional[datetime] = Field(
            default=None, description="UTC creation timestamp."
        )
        started_at: Optional[datetime] = Field(
            default=None, description="UTC timestamp when the run started."
        )
        completed_at: Optional[datetime] = Field(
            default=None, description="UTC timestamp when the run finished."
        )

    class JobStatusResponse(BaseModel):
        """Lightweight status poll for a job (pairs with WS PLUGIN_PROGRESS)."""

        model_config = ConfigDict(extra="allow")

        id: int = Field(..., description="Primary key of the job.")
        uuid: str = Field(..., description="Stable external identifier.")
        status: str = Field(..., description="Lifecycle status.")
        step: int = Field(0, description="Latest reported training step.")
        total_steps: Optional[int] = Field(
            default=None, description="Planned total number of training steps."
        )
        latest_loss: Optional[float] = Field(
            default=None,
            description="Loss value of the most recent loss-curve point, if any.",
        )
        progress: float = Field(
            0.0,
            description="Fraction in [0, 1]: step / total_steps (0 when unknown).",
        )
        info: Optional[str] = Field(
            default=None,
            description="Short status/info string (e.g. the final report).",
        )

    class CancelJobResponse(BaseModel):
        """Result of cancelling a job."""

        model_config = ConfigDict(extra="allow")

        status: str = Field(..., description="'cancelled' on success.")
        job_id: int = Field(..., description="Primary key of the cancelled job.")

    class DeleteJobResponse(BaseModel):
        """Result of deleting a job and its artifacts."""

        model_config = ConfigDict(extra="allow")

        status: str = Field(..., description="'deleted' on success.")
        job_id: int = Field(..., description="Primary key of the deleted job.")

    class PresetItemResponse(BaseModel):
        """One training recipe and whether the saved checkout can run it."""

        model_config = ConfigDict(extra="allow")

        id: str = Field(..., description="The recipe's id.")
        version: int = Field(..., description="The recipe's version.")
        label: str = Field(..., description="The model family it trains.")
        base_model: str = Field(..., description="The checkpoint it trains on.")
        requires_arch: str = Field(
            ..., description="The model code the ai-toolkit checkout must ship."
        )
        offered: bool = Field(
            ...,
            description=(
                "Whether the saved checkout ships that model code; a recipe "
                "that is not offered cannot be queued."
            ),
        )

    class AiToolkitDiscoverResponse(BaseModel):
        """Where an ai-toolkit checkout was found, and where PixlStash looked."""

        model_config = ConfigDict(extra="allow")

        found: bool = Field(..., description="Whether a checkout was found.")
        repo_path: Optional[str] = Field(
            default=None, description="The checkout directory, when found."
        )
        python: Optional[str] = Field(
            default=None, description="The interpreter AI-toolkit would use."
        )
        version: Optional[str] = Field(
            default=None, description="AI-toolkit's version from version.py."
        )
        searched: list[str] = Field(
            default_factory=list, description="Every directory looked in, in order."
        )

    class AiToolkitGpu(BaseModel):
        """One CUDA device the checkout's torch can see."""

        name: str = Field(..., description="The device name torch reports.")
        total_mb: int = Field(..., description="Total device memory in MiB.")

    class AiToolkitDatasetsFolder(BaseModel):
        """The folder PixlStash writes datasets into."""

        path: str = Field(..., description="The checkout's datasets folder.")
        writable: bool = Field(..., description="Whether PixlStash can write to it.")

    class AiToolkitRunsFolder(BaseModel):
        """The folder training runs land in."""

        path: str = Field(
            ...,
            description=(
                "The shelf's ai-toolkit folder when one is registered, else the "
                "checkout's output folder."
            ),
        )
        run_count: int = Field(..., description="Run folders it holds.")
        on_shelf: bool = Field(
            ..., description="Whether the folder is registered on the model shelf."
        )

    class AiToolkitCheckResponse(BaseModel):
        """Whether the checkout can train, and the folders it would use."""

        model_config = ConfigDict(extra="allow")

        ok: bool = Field(..., description="Torch has CUDA and run.py --help ran.")
        python: Optional[str] = Field(
            default=None, description="The interpreter that was checked."
        )
        version: Optional[str] = Field(default=None, description="AI-toolkit version.")
        torch: Optional[str] = Field(default=None, description="Torch version.")
        cuda: bool = Field(..., description="Whether torch sees a CUDA GPU.")
        gpus: list[AiToolkitGpu] = Field(
            default_factory=list, description="The CUDA devices torch sees."
        )
        trial_ok: bool = Field(..., description="Whether run.py --help exited 0.")
        detail: Optional[str] = Field(
            default=None, description="One plain sentence on what failed."
        )
        datasets: Optional[AiToolkitDatasetsFolder] = None
        runs: Optional[AiToolkitRunsFolder] = None
        presets: list[PresetItemResponse] = Field(
            default_factory=list,
            description="The recipes and whether this checkout can run each.",
        )

    class AiToolkitStatusResponse(BaseModel):
        """The configured ai-toolkit install and its folders."""

        model_config = ConfigDict(extra="allow")

        configured: bool = Field(
            ..., description="Whether a checkout and its Python are saved."
        )
        repo_path: Optional[str] = Field(default=None, description="The checkout.")
        python: Optional[str] = Field(default=None, description="Its interpreter.")
        version: Optional[str] = Field(default=None, description="AI-toolkit version.")
        datasets: Optional[AiToolkitDatasetsFolder] = None
        runs: Optional[AiToolkitRunsFolder] = None

    class AiToolkitConfigResponse(BaseModel):
        """The ai-toolkit install as saved."""

        repo_path: Optional[str] = Field(default=None, description="The checkout.")
        python: Optional[str] = Field(default=None, description="Its interpreter.")

    # ── Serialisation helpers ─────────────────────────────────────────────

    def _preset_items(repo_path: str | None) -> list[dict]:
        architectures = presets.checkout_architectures(repo_path)
        return [
            {
                "id": preset["id"],
                "version": preset["version"],
                "label": preset["label"],
                "base_model": preset["locked"]["model.name_or_path"],
                "requires_arch": preset["requires_arch"],
                "offered": presets.offered(preset, architectures),
            }
            for preset in presets.list_presets()
        ]

    def _job_to_summary(job: dict) -> dict:
        return _redact_host_paths(
            {
                "id": job["id"],
                "uuid": job["uuid"],
                "name": job["name"],
                "status": job["status"],
                "set_id": job["set_id"],
                "character_id": job["character_id"],
                "base_model": job["base_model"],
                "trigger_word": job["trigger_word"],
                "step": job["step"],
                "total_steps": job["total_steps"],
                "created_at": job["created_at"],
                "started_at": job["started_at"],
                "completed_at": job["completed_at"],
            }
        )

    # ── Training jobs ─────────────────────────────────────────────────────

    @router.post(
        "/training/jobs",
        summary="Enqueue a training job",
        response_model=CreateJobResponse,
        description=(
            "Creates a queued LoRA training job for exactly one of a picture "
            "set or a character. The background trainer picks it up FIFO. "
            "Owner-only."
        ),
    )
    def create_training_job(payload: CreateJobRequest, request: Request):
        _ensure_secure(request)

        if (payload.set_id is None) == (payload.character_id is None):
            raise HTTPException(
                status_code=400,
                detail="Exactly one of set_id / character_id must be provided.",
            )
        preset = presets.get_preset(payload.preset)
        if preset is None:
            raise HTTPException(
                status_code=404, detail=f"No training recipe '{payload.preset}'."
            )
        user = server.auth.get_user()
        repo_path = user.ai_toolkit_repo_path if user is not None else None
        if not repo_path:
            raise HTTPException(
                status_code=422,
                detail=(
                    "AI-toolkit is not set up. Connect it in Settings › "
                    "AI-toolkit before training."
                ),
            )
        if not presets.offered(preset, presets.checkout_architectures(repo_path)):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"This ai-toolkit checkout has no {preset['requires_arch']} "
                    f"model code, so it cannot train {preset['label']}. Update "
                    "the checkout."
                ),
            )
        subject = payload.subject
        if subject is None:
            if payload.character_id is None:
                raise HTTPException(
                    status_code=422,
                    detail="Say what the LoRA should learn: person, style or thing.",
                )
            subject = "person"
        try:
            pins = presets.validate_pins(preset, payload.pins)
        except TunableOverrideError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        picture_count = ExportUtils.count_training_pictures(
            server, set_id=payload.set_id, character_id=payload.character_id
        )
        if picture_count == 0:
            raise HTTPException(
                status_code=422,
                detail="There are no pictures to train on.",
            )
        room = server.vault.training_room()
        if room["ram_mb"] is None:
            logger.warning(
                "RAM could not be read; resolving %s for job %r without a RAM "
                "check, so a profile that streams from RAM may not fit.",
                preset["id"],
                payload.name,
            )
        if room["vram_mb"] is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "PixlStash cannot read this GPU's memory, so it cannot "
                    "choose how to fit the run to it."
                ),
            )
        try:
            resolution = presets.resolve(
                preset,
                subject=subject,
                effort=payload.effort,
                fit=payload.fit,
                picture_count=picture_count,
                vram_mb=room["vram_mb"],
                ram_mb=room["ram_mb"],
                pins=pins,
            )
        except presets.NoProfileFits as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "budget": exc.budget,
                    "need_mb": exc.need_mb,
                    "free_mb": exc.free_mb,
                },
            ) from exc
        except (TunableOverrideError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            job = server.vault.training_service.enqueue(
                set_id=payload.set_id,
                character_id=payload.character_id,
                name=payload.name,
                base_model=preset["locked"]["model.name_or_path"],
                trigger_word=payload.trigger_word,
                template_path=presets.template_path(preset),
                config_overrides=presets.to_patch(presets.resolved_values(resolution)),
                locked=presets.to_patch(preset["locked"]),
                resolution=resolution,
                pins=pins,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return _redact_host_paths(
            {
                "id": job["id"],
                "uuid": job["uuid"],
                "name": job["name"],
                "status": job["status"],
                "set_id": job["set_id"],
                "character_id": job["character_id"],
                "base_model": job["base_model"],
                "trigger_word": job["trigger_word"],
                "resolved_profile": resolution,
                "created_at": job["created_at"],
            }
        )

    @router.get(
        "/training/jobs",
        summary="List training jobs",
        response_model=list[JobSummaryResponse],
        description=(
            "Lists training jobs newest-first, optionally filtered by set_id or "
            "character_id. Owner-only."
        ),
    )
    def list_training_jobs(
        request: Request,
        set_id: Optional[int] = None,
        character_id: Optional[int] = None,
    ):
        _ensure_secure(request)
        hub, library_uuid = _store()
        runs = training_runs.list_runs(
            hub, library_uuid, set_id=set_id, character_id=character_id
        )
        return [_job_to_summary(run) for run in runs]

    @router.get(
        "/training/jobs/{job_id}",
        summary="Get training job detail",
        response_model=JobDetailResponse,
        description=(
            "Returns the full detail of a single job, including the parsed loss "
            "curve, rendered config, report, sample paths, error, and "
            "timestamps. Owner-only."
        ),
    )
    def get_training_job(job_id: int, request: Request):
        _ensure_secure(request)
        hub, library_uuid = _store()
        job = training_runs.get_run(hub, library_uuid, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        return _redact_host_paths(
            {
                "id": job["id"],
                "uuid": job["uuid"],
                "name": job["name"],
                "status": job["status"],
                "set_id": job["set_id"],
                "character_id": job["character_id"],
                "base_model": job["base_model"],
                "trigger_word": job["trigger_word"],
                "step": job["step"],
                "total_steps": job["total_steps"],
                "loss_curve": _parse_json_field(
                    job["loss_curve"], "loss_curve", job["uuid"]
                ),
                "rendered_config": _parse_json_field(
                    job["rendered_config"], "rendered_config", job["uuid"]
                ),
                "report": job["report"],
                "sample_image_paths": _parse_json_field(
                    job["sample_image_paths"], "sample_image_paths", job["uuid"]
                ),
                "dataset_picture_ids": _parse_json_field(
                    job["dataset_picture_ids"], "dataset_picture_ids", job["uuid"]
                ),
                "ai_toolkit_commit": job["ai_toolkit_commit"],
                "preset": job["preset"],
                "resolved_profile": _parse_json_field(
                    job["resolved_profile"], "resolved_profile", job["uuid"]
                ),
                "advanced_pins": _parse_json_field(
                    job["advanced_pins"], "advanced_pins", job["uuid"]
                ),
                "error": job["error"],
                "created_at": job["created_at"],
                "started_at": job["started_at"],
                "completed_at": job["completed_at"],
            }
        )

    @router.get(
        "/training/jobs/{job_id}/status",
        summary="Poll training job status",
        response_model=JobStatusResponse,
        description=(
            "Lightweight status poll for a job: status, step, total_steps, "
            "latest loss, and a progress fraction. Pairs with the WS "
            "PLUGIN_PROGRESS events the service emits. Owner-only."
        ),
    )
    def get_training_job_status(job_id: int, request: Request):
        _ensure_secure(request)
        hub, library_uuid = _store()
        job = training_runs.get_run(hub, library_uuid, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        loss_curve = _parse_json_field(job["loss_curve"], "loss_curve", job["uuid"])
        total = job["total_steps"] or 0
        progress = (job["step"] / total) if total > 0 else 0.0
        return _redact_host_paths(
            {
                "id": job["id"],
                "uuid": job["uuid"],
                "status": job["status"],
                "step": job["step"],
                "total_steps": job["total_steps"],
                "latest_loss": _latest_loss(loss_curve),
                "progress": progress,
                "info": job["report"] or job["error"],
            }
        )

    @router.post(
        "/training/jobs/{job_id}/cancel",
        summary="Cancel a training job",
        response_model=CancelJobResponse,
        description=(
            "Cancels a queued or running job. Returns 404 if the job is "
            "unknown and 409 if it is already in a terminal state. Owner-only."
        ),
    )
    def cancel_training_job(job_id: int, request: Request):
        _ensure_secure(request)

        # First distinguish "unknown job" (404) from "already terminal" (409):
        # training_service.cancel returns False for both, so check existence and
        # terminal state directly before acting.
        hub, library_uuid = _store()
        job = training_runs.get_run(hub, library_uuid, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        current_status = job["status"]

        cancelled = server.vault.training_service.cancel(job_id)
        if not cancelled:
            # Existence was already confirmed, so a False return means the job
            # was in a terminal state (completed/stopped/error).
            raise HTTPException(
                status_code=409,
                detail=f"Job is already in a terminal state ({current_status}).",
            )
        logger.info("Cancelled training job id=%s (was %s).", job_id, current_status)
        return {"status": "cancelled", "job_id": job_id}

    @router.delete(
        "/training/jobs/{job_id}",
        summary="Delete a training job, its work directory and its dataset",
        response_model=DeleteJobResponse,
        description=(
            "Deletes the job row, its work directory and the dataset snapshot "
            "PixlStash wrote for it. The run folder in the model shelf's "
            "ai-toolkit folder is left where it is, and so is anything else "
            "there: the run is listed by the shelf and its LoRA is a shelf "
            "model with its own copy. Returns 404 if the job is unknown. "
            "Owner-only."
        ),
    )
    def delete_training_job(job_id: int, request: Request):
        _ensure_secure(request)

        training_root = _training_root()
        hub, library_uuid = _store()

        job = training_runs.get_run(hub, library_uuid, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        job_uuid = training_runs.delete_run(hub, library_uuid, job_id)
        if job_uuid is None:
            raise HTTPException(status_code=404, detail="Training job not found")

        # Remove the job's work dir (validated under training/).
        _safe_remove_dir(training_root, job_uuid)
        _remove_job_dataset(job_uuid, job.get("dataset_snapshot_path"))

        logger.info("Deleted training job id=%s (uuid=%s).", job_id, job_uuid)
        return {"status": "deleted", "job_id": job_id}

    def _remove_job_dataset(job_uuid: str, dataset_path: str | None) -> None:
        """Remove the dataset snapshot PixlStash wrote for one job, and nothing else.

        The snapshot is ``<checkout>/datasets/pixlstash-<uuid>``. Anything else
        recorded on the row is left alone, and so is anything inside a shelf
        source folder.
        """
        if not dataset_path:
            return
        normalized = os.path.normpath(dataset_path)
        name = f"pixlstash-{job_uuid}"
        parent = os.path.dirname(normalized)
        if (
            os.path.basename(normalized) != name
            or os.path.basename(parent) != "datasets"
        ):
            logger.warning(
                "Not removing the dataset of job %s: unexpected path %s",
                job_uuid,
                normalized,
            )
            return
        for folder in _shelf_source_folders():
            if path_is_within(normalized, folder):
                logger.warning(
                    "Not removing the dataset of job %s: it lies in a shelf folder",
                    job_uuid,
                )
                return
        _safe_remove_dir(parent, name)

    # ── ai-toolkit install (Settings › AI-toolkit) ─────────────────────────

    class AiToolkitCheckRequest(BaseModel):
        """Body for checking one ai-toolkit install: its checkout and its Python."""

        model_config = ConfigDict(extra="forbid")

        repo_path: str = Field(
            ..., description="The ai-toolkit checkout directory (absolute)."
        )
        python: Optional[str] = Field(
            default=None,
            description=(
                "The interpreter that runs the trainer (absolute). Omitted or "
                "null: found in the checkout the way AI-toolkit finds it."
            ),
        )

    def _ai_toolkit_folders(repo: str) -> dict:
        """The dataset and run folders of one checkout, as /status reports them.

        Shared by the status route (the saved checkout) and the check route (the
        checkout being checked), so the Folders step and the status card agree.
        """
        datasets = os.path.join(repo, "datasets")
        # The folder is created on the first job; until then the checkout must
        # be writable, since that is where it is created.
        writable_target = datasets if os.path.isdir(datasets) else repo
        shelf = _shelf_source_folders()
        runs_path = shelf[0] if shelf else os.path.join(repo, "output")
        return {
            "datasets": {
                "path": datasets,
                "writable": os.access(writable_target, os.W_OK),
            },
            "runs": {
                "path": runs_path,
                "run_count": _count_runs(runs_path),
                "on_shelf": bool(shelf),
            },
        }

    @router.get(
        "/training/ai-toolkit/discover",
        response_model=AiToolkitDiscoverResponse,
        summary="Find an ai-toolkit checkout on this computer",
        description=(
            "Looks for an ai-toolkit checkout in the parent of each shelf "
            "ai-toolkit folder, the saved path, then the usual places under the "
            "home directory. Reports the path, the interpreter AI-toolkit would "
            "use and the version, and every directory it looked in. Owner-only, "
            "from this machine."
        ),
    )
    def discover_ai_toolkit(request: Request):
        _ensure_secure(request)
        user = server.auth.get_user()
        return ai_toolkit_install.discover(
            shelf_source_folders=_shelf_source_folders(),
            saved_repo_path=user.ai_toolkit_repo_path if user is not None else None,
        )

    @router.post(
        "/training/ai-toolkit/check",
        response_model=AiToolkitCheckResponse,
        summary="Check that an ai-toolkit install can train",
        description=(
            "Runs the checkout's own Python to see whether torch imports with a "
            "CUDA GPU, and runs `run.py --help`. Both are host processes, started "
            "with an argument list and a timeout. With no `python`, the checkout's "
            "own interpreter is found first. The response names the interpreter "
            "checked and gives the dataset and run folders for this checkout, as "
            "/status does. Owner-only, from this machine."
        ),
    )
    def check_ai_toolkit(payload: AiToolkitCheckRequest, request: Request):
        _ensure_secure(request)
        if not payload.repo_path:
            raise HTTPException(status_code=422, detail="repo_path is required.")
        try:
            validate_ai_toolkit_settings(payload.repo_path, None)
            python = payload.python or ai_toolkit_install.find_python(payload.repo_path)
            if not python:
                raise ValueError(
                    "No Python found for this checkout: it has no .venv or venv, "
                    "and python3 is not on the PATH."
                )
            validate_ai_toolkit_settings(None, python)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = ai_toolkit_install.check(payload.repo_path, python)
        result["python"] = python
        result.update(_ai_toolkit_folders(payload.repo_path))
        result["presets"] = _preset_items(payload.repo_path)
        return result

    @router.get(
        "/training/ai-toolkit/status",
        response_model=AiToolkitStatusResponse,
        summary="The configured ai-toolkit install and its folders",
        description=(
            "Whether an ai-toolkit install is configured, its paths and version, "
            "the dataset folder and whether PixlStash can write to it, and the "
            "folder runs land in (the shelf's ai-toolkit folder when one is "
            "registered) with its run count. Owner-only, from this machine."
        ),
    )
    def ai_toolkit_status(request: Request):
        _ensure_secure(request)
        user = server.auth.get_user()
        repo = user.ai_toolkit_repo_path if user is not None else None
        python = user.ai_toolkit_python if user is not None else None
        configured = bool(repo and python)
        if not configured:
            return {
                "configured": False,
                "repo_path": repo or None,
                "python": python or None,
                "version": None,
                "datasets": None,
                "runs": None,
            }

        return {
            "configured": True,
            "repo_path": repo,
            "python": python,
            "version": ai_toolkit_install.read_version(repo),
            **_ai_toolkit_folders(repo),
        }

    class AiToolkitConfigRequest(BaseModel):
        """Body for saving the ai-toolkit install: its checkout and its Python.

        Both keys are required. Both null disconnects the install.
        """

        model_config = ConfigDict(extra="forbid")

        repo_path: Optional[str] = Field(
            ..., description="The ai-toolkit checkout directory (absolute), or null."
        )
        python: Optional[str] = Field(
            ...,
            description=(
                "The interpreter that runs the trainer (absolute), or null. It "
                "must be an executable file."
            ),
        )

    @router.put(
        "/training/ai-toolkit/config",
        response_model=AiToolkitConfigResponse,
        summary="Save or disconnect the ai-toolkit install",
        description=(
            "Stores the ai-toolkit checkout and the Python that runs its trainer. "
            "Each is checked on this host first: the checkout must hold run.py and "
            "the Python must be an executable file. Both null disconnects the "
            "install. The Python is the trainer's argv[0] and the checkout its "
            "working directory, so this is a host operation: a remote owner is "
            "refused unless allow_remote_host_ops is set."
        ),
    )
    def put_ai_toolkit_config(payload: AiToolkitConfigRequest, request: Request):
        _ensure_secure(request)
        repo_path = (payload.repo_path or "").strip() or None
        python = (payload.python or "").strip() or None
        try:
            validate_ai_toolkit_settings(repo_path, python)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        user_id = server.auth.require_user_id(request)

        def save(session, user_id: int):
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            apply_user_config_patch(
                user,
                {"ai_toolkit_repo_path": repo_path, "ai_toolkit_python": python},
            )
            session.add(user)
            session.commit()
            session.refresh(user)
            return user

        user = server.hub_engine.run_task(save, user_id, priority=DBPriority.IMMEDIATE)
        # Keep the process-local owner cache coherent with the hub row, as the
        # config PATCH does.
        server.auth.user = user
        server._user = user
        return {
            "repo_path": user.ai_toolkit_repo_path,
            "python": user.ai_toolkit_python,
        }

    # ── Recipes ───────────────────────────────────────────────────────────

    @router.get(
        "/training/presets",
        summary="List training recipes",
        response_model=list[PresetItemResponse],
        description=(
            "Lists the shipped training recipes, each with the model it trains "
            "and whether the saved ai-toolkit checkout ships the model code it "
            "needs. Owner-only."
        ),
    )
    def list_training_presets(request: Request):
        _ensure_secure(request)
        user = server.auth.get_user()
        return _preset_items(user.ai_toolkit_repo_path if user is not None else None)

    return router


# ---------------------------------------------------------------------------
# Module-level filesystem-removal helpers (path-validated; never raise)
# ---------------------------------------------------------------------------


def _safe_remove_dir(base_dir: str, dir_name: str) -> None:
    """Recursively remove *dir_name* under *base_dir*, validated to stay within it.

    Args:
        base_dir: The permitted root (training/).
        dir_name: The directory name to remove (e.g. a job uuid or a
            ``{uuid}_samples`` dir).
    """
    if not dir_name:
        return
    try:
        safe_abs = resolve_path_within(base_dir, dir_name)
    except ValueError as exc:
        logger.warning(
            "Refusing to remove directory outside %s: %r (%s)",
            base_dir,
            dir_name,
            exc,
        )
        return
    if not os.path.isdir(safe_abs):
        logger.debug("Directory already gone, nothing to remove: %s", safe_abs)
        return
    try:
        shutil.rmtree(safe_abs)
        logger.info("Removed directory: %s", safe_abs)
    except OSError as exc:
        logger.warning("Failed to remove directory %s: %s", safe_abs, exc)
