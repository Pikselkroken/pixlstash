"""HTTP API for ai-toolkit LoRA training jobs and trained-LoRA management.

This router exposes the training feature under ``/api/v1``: enqueueing and
cancelling training jobs, polling their progress, browsing and downloading the
trained LoRA artifacts, and listing the available config templates.

Authorization is declared, not coded here: every endpoint is listed in
``pixlstash/authz/registry.py`` (``ROUTE_POLICIES``) and enforced by the central
``AuthzGate``. Reads and LoRA downloads are ``owner_only`` (share tokens never
reach them). Routes that start or signal a host trainer process, delete host
files or disclose a host path are ``local_owner_only`` (§16.3): training is a
privileged compute and filesystem operation, so a remote owner is refused unless
``allow_remote_host_ops`` is set. Handlers carry no inline owner or scope checks.
"""

import json
import os
import re
import shutil
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from platformdirs import user_data_dir

from pixlstash.db_models.trained_lora import TrainedLora
from pixlstash.db_models.training_job import TrainingJob
from pixlstash.pixl_logging import get_logger
from pixlstash.training.config_render import TunableOverrideError, validate_tunables
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

# Default training template (resolved by name, .json appended) when the caller
# does not specify one. Matches the file shipped in the built-in templates dir.
DEFAULT_TEMPLATE = "zimage-turbo-lora"
# Fallback base model when neither the request nor the template names one.
DEFAULT_BASE_MODEL = "Tongyi-MAI/Z-Image-Turbo"


# ---------------------------------------------------------------------------
# Template directory resolution (mirrors comfyui's workflow dirs)
# ---------------------------------------------------------------------------


def _template_builtin_dir() -> str:
    """Absolute path to the shipped built-in training templates directory."""
    return os.path.abspath(
        os.path.join(
            os.path.dirname(__file__), "..", "data", "ai-toolkit-jobs", "built-in"
        )
    )


def _template_user_dir() -> str:
    """Absolute path to the user's training templates directory."""
    return os.path.join(user_data_dir("pixlstash"), "ai-toolkit-jobs", "user")


def _template_dirs() -> list[tuple[str, str]]:
    """Return ``(source, directory)`` pairs, user dir taking precedence."""
    return [
        ("user", _template_user_dir()),
        ("built-in", _template_builtin_dir()),
    ]


def _normalize_template_name(name: str) -> str:
    """Strip to a bare filename and ensure a ``.json`` suffix.

    Args:
        name: The caller-supplied template name (with or without ``.json``).

    Returns:
        A safe ``basename`` ending in ``.json``, or ``""`` when blank.
    """
    safe = os.path.basename(name or "").strip()
    if not safe:
        return ""
    if not safe.lower().endswith(".json"):
        safe = f"{safe}.json"
    return safe


def _resolve_template_path(name: str) -> tuple[str | None, str | None]:
    """Resolve a template name to an on-disk path, user dir first.

    Path-safety: the normalized name is joined under each template root via
    :func:`resolve_path_within`, so a name containing ``..`` or an absolute
    component can never escape the template directories.

    Args:
        name: The template name.

    Returns:
        ``(absolute_path, source)`` if found, else ``(None, None)``.
    """
    normalized = _normalize_template_name(name)
    if not normalized:
        return None, None
    for source, folder in _template_dirs():
        try:
            path = resolve_path_within(folder, normalized)
        except ValueError:
            return None, None
        if os.path.isfile(path):
            return path, source
    return None, None


def _base_model_from_template(template_path: str) -> str | None:
    """Read the base model name out of a rendered/golden template, if present.

    Reads ``config.process[0].model.name_or_path``; returns ``None`` on any
    structural mismatch or read error (the caller falls back to a default).

    Args:
        template_path: Absolute path to the template JSON.

    Returns:
        The base-model name, or ``None``.
    """
    try:
        with open(template_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload["config"]["process"][0]["model"]["name_or_path"]
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        logger.warning(
            "Could not read base model from template %s: %s", template_path, exc
        )
        return None


def _template_description(template_path: str) -> str | None:
    """Build a short, secret-free description for a template listing entry.

    Combines the base model and planned training steps when readable. Returns
    ``None`` if neither can be derived.

    Args:
        template_path: Absolute path to the template JSON.

    Returns:
        A short human-readable description, or ``None``.
    """
    try:
        with open(template_path, encoding="utf-8") as fh:
            payload = json.load(fh)
        process = payload["config"]["process"][0]
        base_model = process.get("model", {}).get("name_or_path")
        steps = process.get("train", {}).get("steps")
        parts = []
        if base_model:
            parts.append(f"Base model {base_model}")
        if steps:
            parts.append(f"{int(steps)} steps")
        return ", ".join(parts) or None
    except (OSError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        logger.warning(
            "Could not derive description for template %s: %s", template_path, exc
        )
        return None


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


# An absolute POSIX or Windows path, as it appears inside a string. The lookbehind
# keeps URLs, ``1/2`` and ``and/or`` out of it.
_HOST_PATH_RE = re.compile(r"(?<![\w./:])(?:[A-Za-z]:[\\/]|/)[^\s'\"<>|,;()\[\]{}]+")


def _redact_host_paths(value):
    """Replace absolute host paths in a response value with a placeholder.

    Job responses go to the owner's browser and to any tool they run, so the
    host's install and library locations must not travel with them. Relative
    vault paths (``loras/...``) pass through unchanged.

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

    def _loras_dir() -> str:
        return os.path.join(_image_root(), "loras")

    def _training_root() -> str:
        return os.path.join(_image_root(), "training")

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
        base_model: Optional[str] = Field(
            default=None,
            description=(
                "Base checkpoint to train the LoRA against. Defaults to the "
                "base model named in the chosen template."
            ),
        )
        trigger_word: str = Field(
            ...,
            min_length=1,
            description="Caption trigger word baked into the training dataset.",
        )
        template: Optional[str] = Field(
            default=None,
            description=(
                "Template name (with or without .json) resolved from the user "
                f"then built-in template dirs. Defaults to '{DEFAULT_TEMPLATE}'."
            ),
        )
        config_overrides: Optional[dict] = Field(
            default=None,
            description=(
                "Optional deep-merge patch applied over the template config "
                "for this run."
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
        deleted_lora_ids: list[int] = Field(
            default_factory=list,
            description="Primary keys of TrainedLora rows removed with the job.",
        )

    class LoraSummaryResponse(BaseModel):
        """One row in the trained-LoRA listing."""

        model_config = ConfigDict(extra="allow")

        id: int = Field(..., description="Primary key of the LoRA.")
        uuid: str = Field(..., description="Stable external identifier.")
        name: str = Field(..., description="Human-readable LoRA name.")
        job_id: Optional[int] = Field(
            default=None, description="Primary key of the producing training job."
        )
        set_id: Optional[int] = Field(
            default=None, description="Picture-set scope, denormalised from the job."
        )
        character_id: Optional[int] = Field(
            default=None, description="Character scope, denormalised from the job."
        )
        step: Optional[int] = Field(
            default=None, description="Training step the artifact was saved at."
        )
        file_size_bytes: Optional[int] = Field(
            default=None, description="Size of the .safetensors artifact on disk."
        )
        created_at: Optional[datetime] = Field(
            default=None, description="UTC creation timestamp."
        )

    class LoraDetailResponse(BaseModel):
        """Full detail for a single trained LoRA, with reproducibility links."""

        model_config = ConfigDict(extra="allow")

        id: int = Field(..., description="Primary key of the LoRA.")
        uuid: str = Field(..., description="Stable external identifier.")
        name: str = Field(..., description="Human-readable LoRA name.")
        job_id: Optional[int] = Field(
            default=None, description="Primary key of the producing training job."
        )
        set_id: Optional[int] = Field(
            default=None, description="Picture-set scope, denormalised from the job."
        )
        character_id: Optional[int] = Field(
            default=None, description="Character scope, denormalised from the job."
        )
        step: Optional[int] = Field(
            default=None, description="Training step the artifact was saved at."
        )
        file_path: Optional[str] = Field(
            default=None,
            description="Path relative to the vault image-root (loras/{uuid}.safetensors).",
        )
        file_size_bytes: Optional[int] = Field(
            default=None, description="Size of the artifact on disk."
        )
        file_sha: Optional[str] = Field(
            default=None, description="SHA-256 content hash of the artifact."
        )
        created_at: Optional[datetime] = Field(
            default=None, description="UTC creation timestamp."
        )
        job_base_model: Optional[str] = Field(
            default=None,
            description="Base checkpoint of the producing job (reproducibility).",
        )
        job_trigger_word: Optional[str] = Field(
            default=None,
            description="Trigger word of the producing job (reproducibility).",
        )
        job_ai_toolkit_commit: Optional[str] = Field(
            default=None,
            description="ai-toolkit commit of the producing job (reproducibility).",
        )

    class LoraFilePathResponse(BaseModel):
        """Resolved absolute local path of a LoRA, for same-machine loaders."""

        model_config = ConfigDict(extra="allow")

        id: int = Field(..., description="Primary key of the LoRA.")
        uuid: str = Field(..., description="Stable external identifier.")
        file_path: str = Field(
            ...,
            description=(
                "Absolute local filesystem path to the .safetensors file, "
                "validated to be within the vault image-root. Owner-only."
            ),
        )

    class DeleteLoraResponse(BaseModel):
        """Result of deleting a trained LoRA."""

        model_config = ConfigDict(extra="allow")

        status: str = Field(..., description="'deleted' on success.")
        lora_id: int = Field(..., description="Primary key of the deleted LoRA.")

    class TemplateItemResponse(BaseModel):
        """One available training template (no secrets)."""

        model_config = ConfigDict(extra="allow")

        name: str = Field(..., description="Template name including the .json suffix.")
        display_name: str = Field(
            ..., description="Template name without the .json suffix."
        )
        source: str = Field(
            ..., description="Where the template was found: 'user' or 'built-in'."
        )
        base_model: Optional[str] = Field(
            default=None, description="Base model named in the template, if readable."
        )
        description: Optional[str] = Field(
            default=None, description="Short, secret-free summary of the template."
        )

    # ── Serialisation helpers ─────────────────────────────────────────────

    def _job_to_summary(job: TrainingJob) -> dict:
        return _redact_host_paths(
            {
                "id": job.id,
                "uuid": job.uuid,
                "name": job.name,
                "status": job.status,
                "set_id": job.set_id,
                "character_id": job.character_id,
                "base_model": job.base_model,
                "trigger_word": job.trigger_word,
                "step": job.step,
                "total_steps": job.total_steps,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
            }
        )

    def _lora_to_summary(lora: TrainedLora) -> dict:
        return {
            "id": lora.id,
            "uuid": lora.uuid,
            "name": lora.name,
            "job_id": lora.job_id,
            "set_id": lora.set_id,
            "character_id": lora.character_id,
            "step": lora.step,
            "file_size_bytes": lora.file_size_bytes,
            "created_at": lora.created_at,
        }

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

        try:
            validate_tunables(payload.config_overrides)
        except TunableOverrideError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        template_name = payload.template or DEFAULT_TEMPLATE
        template_path, _source = _resolve_template_path(template_name)
        if not template_path:
            raise HTTPException(
                status_code=404, detail=f"Template not found: {template_name}"
            )

        base_model = (
            payload.base_model
            or _base_model_from_template(template_path)
            or DEFAULT_BASE_MODEL
        )

        try:
            job = server.vault.training_service.enqueue(
                set_id=payload.set_id,
                character_id=payload.character_id,
                name=payload.name,
                base_model=base_model,
                trigger_word=payload.trigger_word,
                template_path=template_path,
                config_overrides=payload.config_overrides,
            )
        except ValueError as exc:
            # Raised when not exactly one of set_id/character_id was provided.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return _redact_host_paths(
            {
                "id": job.id,
                "uuid": job.uuid,
                "name": job.name,
                "status": job.status,
                "set_id": job.set_id,
                "character_id": job.character_id,
                "base_model": job.base_model,
                "trigger_word": job.trigger_word,
                "created_at": job.created_at,
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

        def _query(session: Session):
            stmt = select(TrainingJob)
            if set_id is not None:
                stmt = stmt.where(TrainingJob.set_id == set_id)
            if character_id is not None:
                stmt = stmt.where(TrainingJob.character_id == character_id)
            stmt = stmt.order_by(TrainingJob.created_at.desc(), TrainingJob.id.desc())
            return [_job_to_summary(job) for job in session.exec(stmt).all()]

        return server.vault.db.run_immediate_read_task(_query)

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

        def _query(session: Session):
            job = session.get(TrainingJob, job_id)
            if job is None:
                return None
            return _redact_host_paths(
                {
                    "id": job.id,
                    "uuid": job.uuid,
                    "name": job.name,
                    "status": job.status,
                    "set_id": job.set_id,
                    "character_id": job.character_id,
                    "base_model": job.base_model,
                    "trigger_word": job.trigger_word,
                    "step": job.step,
                    "total_steps": job.total_steps,
                    "loss_curve": _parse_json_field(
                        job.loss_curve, "loss_curve", job.uuid
                    ),
                    "rendered_config": _parse_json_field(
                        job.rendered_config, "rendered_config", job.uuid
                    ),
                    "report": job.report,
                    "sample_image_paths": _parse_json_field(
                        job.sample_image_paths, "sample_image_paths", job.uuid
                    ),
                    "dataset_picture_ids": _parse_json_field(
                        job.dataset_picture_ids, "dataset_picture_ids", job.uuid
                    ),
                    "ai_toolkit_commit": job.ai_toolkit_commit,
                    "error": job.error,
                    "created_at": job.created_at,
                    "started_at": job.started_at,
                    "completed_at": job.completed_at,
                }
            )

        detail = server.vault.db.run_immediate_read_task(_query)
        if detail is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        return detail

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

        def _query(session: Session):
            job = session.get(TrainingJob, job_id)
            if job is None:
                return None
            loss_curve = _parse_json_field(job.loss_curve, "loss_curve", job.uuid)
            total = job.total_steps or 0
            progress = (job.step / total) if total > 0 else 0.0
            return _redact_host_paths(
                {
                    "id": job.id,
                    "uuid": job.uuid,
                    "status": job.status,
                    "step": job.step,
                    "total_steps": job.total_steps,
                    "latest_loss": _latest_loss(loss_curve),
                    "progress": progress,
                    "info": job.report or job.error,
                }
            )

        status = server.vault.db.run_immediate_read_task(_query)
        if status is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        return status

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
        def _load_status(session: Session):
            job = session.get(TrainingJob, job_id)
            if job is None:
                return None
            return job.status

        current_status = server.vault.db.run_immediate_read_task(_load_status)
        if current_status is None:
            raise HTTPException(status_code=404, detail="Training job not found")

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
        summary="Delete a training job and its artifacts",
        response_model=DeleteJobResponse,
        description=(
            "Deletes the job row, every TrainedLora it produced (rows + files + "
            "sample dirs), and the job's on-disk work directory. Returns 404 if "
            "the job is unknown. Owner-only."
        ),
    )
    def delete_training_job(job_id: int, request: Request):
        _ensure_secure(request)

        loras_dir = _loras_dir()
        training_root = _training_root()

        def _delete(session: Session):
            job = session.get(TrainingJob, job_id)
            if job is None:
                return None
            job_uuid = job.uuid
            loras = session.exec(
                select(TrainedLora).where(TrainedLora.job_id == job_id)
            ).all()
            # Collect file paths to remove AFTER the DB rows are gone, so a file
            # I/O error never leaves a dangling row pointing at a deleted file.
            lora_file_paths: list[str] = []
            lora_sample_dirs: list[str] = []
            deleted_lora_ids: list[int] = []
            for lora in loras:
                deleted_lora_ids.append(lora.id)
                if lora.file_path:
                    lora_file_paths.append(lora.file_path)
                    lora_sample_dirs.append(f"{lora.uuid}_samples")
                session.delete(lora)
            session.delete(job)
            session.commit()
            return job_uuid, deleted_lora_ids, lora_file_paths, lora_sample_dirs

        result = server.vault.db.run_task(_delete)
        if result is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        job_uuid, deleted_lora_ids, lora_file_paths, lora_sample_dirs = result

        # Remove LoRA artifacts (path-validated to stay within loras/).
        for rel_path in lora_file_paths:
            _safe_remove_file(loras_dir, _image_root(), rel_path)
        for sample_dir_name in lora_sample_dirs:
            _safe_remove_dir(loras_dir, sample_dir_name)
        # Remove the job's work dir (validated under training/).
        _safe_remove_dir(training_root, job_uuid)

        logger.info(
            "Deleted training job id=%s (uuid=%s); removed %d LoRA(s): %s",
            job_id,
            job_uuid,
            len(deleted_lora_ids),
            deleted_lora_ids,
        )
        return {
            "status": "deleted",
            "job_id": job_id,
            "deleted_lora_ids": deleted_lora_ids,
        }

    # ── Trained LoRAs ─────────────────────────────────────────────────────

    @router.get(
        "/loras",
        summary="List trained LoRAs",
        response_model=list[LoraSummaryResponse],
        description=(
            "Lists trained LoRAs newest-first, optionally filtered by set_id or "
            "character_id. Owner-only."
        ),
    )
    def list_loras(
        request: Request,
        set_id: Optional[int] = None,
        character_id: Optional[int] = None,
    ):
        _ensure_secure(request)

        def _query(session: Session):
            stmt = select(TrainedLora)
            if set_id is not None:
                stmt = stmt.where(TrainedLora.set_id == set_id)
            if character_id is not None:
                stmt = stmt.where(TrainedLora.character_id == character_id)
            stmt = stmt.order_by(TrainedLora.created_at.desc(), TrainedLora.id.desc())
            return [_lora_to_summary(lora) for lora in session.exec(stmt).all()]

        return server.vault.db.run_immediate_read_task(_query)

    @router.get(
        "/loras/{lora_id}",
        summary="Get trained LoRA detail",
        response_model=LoraDetailResponse,
        description=(
            "Returns full detail for a LoRA, including a link back to its "
            "producing job's reproducibility metadata (base model, trigger "
            "word, ai-toolkit commit). Owner-only."
        ),
    )
    def get_lora(lora_id: int, request: Request):
        _ensure_secure(request)

        def _query(session: Session):
            lora = session.get(TrainedLora, lora_id)
            if lora is None:
                return None
            job = (
                session.get(TrainingJob, lora.job_id)
                if lora.job_id is not None
                else None
            )
            return {
                "id": lora.id,
                "uuid": lora.uuid,
                "name": lora.name,
                "job_id": lora.job_id,
                "set_id": lora.set_id,
                "character_id": lora.character_id,
                "step": lora.step,
                "file_path": lora.file_path,
                "file_size_bytes": lora.file_size_bytes,
                "file_sha": lora.file_sha,
                "created_at": lora.created_at,
                "job_base_model": job.base_model if job is not None else None,
                "job_trigger_word": job.trigger_word if job is not None else None,
                "job_ai_toolkit_commit": (
                    job.ai_toolkit_commit if job is not None else None
                ),
            }

        detail = server.vault.db.run_immediate_read_task(_query)
        if detail is None:
            raise HTTPException(status_code=404, detail="Trained LoRA not found")
        return detail

    @router.get(
        "/loras/{lora_id}/download",
        summary="Download a trained LoRA",
        response_class=FileResponse,
        responses={200: {"content": {"application/octet-stream": {}}}},
        description=(
            "Streams the .safetensors artifact as an attachment. The stored "
            "relative path is resolved and validated to stay within the vault "
            "image-root before any bytes are served. Owner-only."
        ),
    )
    def download_lora(lora_id: int, request: Request):
        _ensure_secure(request)

        abs_path, lora = _resolve_lora_file_or_404(lora_id)
        filename = f"{lora['name']}.safetensors"
        return FileResponse(
            abs_path,
            media_type="application/octet-stream",
            filename=filename,
        )

    @router.get(
        "/loras/{lora_id}/file-path",
        summary="Get a LoRA's absolute local path",
        response_model=LoraFilePathResponse,
        description=(
            "Returns the resolved absolute local filesystem path of the LoRA "
            "for same-machine ComfyUI loader nodes. The path is validated to be "
            "within the vault image-root. Owner-only (this is the highest-risk "
            "endpoint: it discloses a local server path)."
        ),
    )
    def get_lora_file_path(lora_id: int, request: Request):
        _ensure_secure(request)

        abs_path, lora = _resolve_lora_file_or_404(lora_id)
        return {"id": lora["id"], "uuid": lora["uuid"], "file_path": abs_path}

    @router.delete(
        "/loras/{lora_id}",
        summary="Delete a trained LoRA",
        response_model=DeleteLoraResponse,
        description=(
            "Deletes the LoRA row, its .safetensors file, and its sample "
            "directory. Returns 404 if the LoRA is unknown. Owner-only."
        ),
    )
    def delete_lora(lora_id: int, request: Request):
        _ensure_secure(request)

        loras_dir = _loras_dir()
        image_root = _image_root()

        def _delete(session: Session):
            lora = session.get(TrainedLora, lora_id)
            if lora is None:
                return None
            file_path = lora.file_path
            sample_dir_name = f"{lora.uuid}_samples"
            session.delete(lora)
            session.commit()
            return file_path, sample_dir_name

        result = server.vault.db.run_task(_delete)
        if result is None:
            raise HTTPException(status_code=404, detail="Trained LoRA not found")
        file_path, sample_dir_name = result

        if file_path:
            _safe_remove_file(loras_dir, image_root, file_path)
        _safe_remove_dir(loras_dir, sample_dir_name)

        logger.info("Deleted trained LoRA id=%s (file=%s).", lora_id, file_path)
        return {"status": "deleted", "lora_id": lora_id}

    # ── Templates ─────────────────────────────────────────────────────────

    @router.get(
        "/training/templates",
        summary="List training templates",
        response_model=list[TemplateItemResponse],
        description=(
            "Lists available training config templates from the user and "
            "built-in directories, each with its name, base model, and a short "
            "description. No secrets are returned. Owner-only."
        ),
    )
    def list_training_templates(request: Request):
        _ensure_secure(request)

        templates: list[dict] = []
        seen: set[str] = set()
        for source, folder in _template_dirs():
            if not os.path.isdir(folder):
                continue
            for entry in sorted(os.listdir(folder)):
                if not entry.lower().endswith(".json"):
                    continue
                if entry in seen:
                    continue
                seen.add(entry)
                path = os.path.join(folder, entry)
                templates.append(
                    {
                        "name": entry,
                        "display_name": os.path.splitext(entry)[0],
                        "source": source,
                        "base_model": _base_model_from_template(path),
                        "description": _template_description(path),
                    }
                )
        templates.sort(key=lambda item: item.get("name", ""))
        return templates

    # ── Shared file-resolution + removal helpers (closure over server) ─────

    def _resolve_lora_file_or_404(lora_id: int) -> tuple[str, dict]:
        """Load a LoRA, resolve its file to an in-vault absolute path, or 404.

        Args:
            lora_id: Primary key of the LoRA.

        Returns:
            ``(absolute_path, {id, uuid, name, file_path})``.

        Raises:
            HTTPException: 404 if the row, the stored path, or the file is
                missing, or if the resolved path would escape the image-root.
        """

        def _load(session: Session):
            lora = session.get(TrainedLora, lora_id)
            if lora is None:
                return None
            return {
                "id": lora.id,
                "uuid": lora.uuid,
                "name": lora.name,
                "file_path": lora.file_path,
            }

        lora = server.vault.db.run_immediate_read_task(_load)
        if lora is None:
            raise HTTPException(status_code=404, detail="Trained LoRA not found")
        if not lora["file_path"]:
            raise HTTPException(
                status_code=404, detail="Trained LoRA has no file on record"
            )

        image_root = _image_root()
        resolved = ImageUtils.resolve_picture_path(image_root, lora["file_path"])
        if not resolved:
            raise HTTPException(
                status_code=404, detail="LoRA file path could not be resolved"
            )
        # Assert the resolved path stays within image_root. resolve_path_within
        # raises ValueError on any escape (.. components, symlinks, abs paths
        # outside the root); we relativise first so an absolute stored path is
        # still re-validated against the root.
        try:
            rel = os.path.relpath(resolved, image_root)
            safe_abs = resolve_path_within(image_root, rel)
        except ValueError as exc:
            logger.warning(
                "LoRA id=%s file_path escapes image_root (%s): %s",
                lora_id,
                lora["file_path"],
                exc,
            )
            raise HTTPException(
                status_code=404, detail="LoRA file is outside the vault"
            ) from exc
        if not os.path.isfile(safe_abs):
            raise HTTPException(status_code=404, detail="LoRA file is missing")
        return safe_abs, lora

    return router


# ---------------------------------------------------------------------------
# Module-level filesystem-removal helpers (path-validated; never raise)
# ---------------------------------------------------------------------------


def _safe_remove_file(base_dir: str, image_root: str, rel_or_abs_path: str) -> None:
    """Remove a LoRA file, validated to be within *base_dir*.

    Args:
        base_dir: The loras directory the file must live under.
        image_root: The vault image-root, used to resolve a relative path.
        rel_or_abs_path: The stored ``file_path`` (relative to image_root, or
            absolute).
    """
    resolved = ImageUtils.resolve_picture_path(image_root, rel_or_abs_path)
    if not resolved:
        logger.warning(
            "Could not resolve LoRA file path for removal: %r", rel_or_abs_path
        )
        return
    try:
        # Re-validate under loras/ specifically: deletions must never reach
        # outside the artifact directory even if a row was tampered with.
        rel = os.path.relpath(resolved, base_dir)
        safe_abs = resolve_path_within(base_dir, rel)
    except ValueError as exc:
        logger.warning(
            "Refusing to remove LoRA file outside %s: %r (%s)",
            base_dir,
            rel_or_abs_path,
            exc,
        )
        return
    if not os.path.isfile(safe_abs):
        logger.debug("LoRA file already gone, nothing to remove: %s", safe_abs)
        return
    try:
        os.remove(safe_abs)
        logger.info("Removed LoRA file: %s", safe_abs)
    except OSError as exc:
        logger.warning("Failed to remove LoRA file %s: %s", safe_abs, exc)


def _safe_remove_dir(base_dir: str, dir_name: str) -> None:
    """Recursively remove *dir_name* under *base_dir*, validated to stay within it.

    Args:
        base_dir: The permitted root (loras/ or training/).
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
