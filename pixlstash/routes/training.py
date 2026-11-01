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

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import zipfile
from datetime import datetime, timezone
from typing import Any, Literal, Optional

import psutil
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.background import BackgroundTask

from pixlstash.database import DBPriority
from pixlstash.db_models import Character, PictureSet, User
from pixlstash.hub import dataset_snapshots, training_runs
from pixlstash.hub.db import now_iso
from pixlstash.pixl_logging import get_logger
from pixlstash.resources.arbiter import arbiter
from pixlstash.resources.ledger import ledger
from pixlstash.resources.ledger import ram as read_ram
from pixlstash.services.model_folder_scanner import SOURCE_FOLDER_KIND
from pixlstash.services.picture_set_tree import owning_character_id
from pixlstash.services.training_service import handle_for_run
from pixlstash.training import (
    ai_toolkit_install,
    live_view,
    measurements,
    preset_updates,
    presets,
    sample_prompts,
)
from pixlstash.training.config_render import (
    TUNABLE_KEYS,
    TunableOverrideError,
    normalise_trigger_word,
)
from pixlstash.training.local_ai_toolkit import LocalAiToolkit
from pixlstash.utils.aitoolkit_run import read_output_root, read_run
from pixlstash.utils.known_base_models import training_base_tile
from pixlstash.utils.path_utils import path_is_within, resolve_path_within
from pixlstash.utils.service.export_utils import ExportUtils
from pixlstash.utils.service.user_settings_utils import (
    apply_user_config_patch,
    validate_ai_toolkit_settings,
)

logger = get_logger(__name__)

# The smallest set the effort tables' source reports a working LoRA from
# (ostris/ai-toolkit#550: 6 to 46 pictures); the dialog warns below it.
MIN_PICTURES = 6

# The most pictures a resolve preview takes: far past any real set, and small
# enough that the step arithmetic (float steps per picture) cannot overflow.
MAX_PICTURE_COUNT = 1_000_000
# Lengths follow the recipe names (200) and the model trigger words (500).
MAX_JOB_NAME_LENGTH = 200
MAX_TRIGGER_WORD_LENGTH = 500


def _checked_text(value: str, field: str) -> str:
    """Refuse a newline or other control character in a name or trigger word.

    Raises:
        ValueError: The value holds a control character (C0, DEL or C1, which
            takes in U+0085) or a Unicode line or paragraph separator. Pydantic
            turns it into a 422 naming the field.
    """
    if any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in value):
        raise ValueError(f"{field} cannot contain a newline or control character.")
    return value


# Seconds a checkout's commit is reused for: a pull shows up in the plan's
# measurements this much later at most.
_COMMIT_TTL_S = 30.0

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


# The owner's own text in a job response. The path pattern reads "Cats / Dogs
# (cd)" or a "sunset / beach" prompt as a path, and these carry no host path, so
# they pass through as written.
_OWNER_TEXT_FIELDS = frozenset({"name", "trigger_word", "rows"})


def _redact_job(payload: dict) -> dict:
    """Redact host paths in a job response, except in the owner's own text.

    Args:
        payload: A job response with ``_OWNER_TEXT_FIELDS`` at its top level.

    Returns:
        A copy of *payload* with every other field passed through
        ``_redact_host_paths``.
    """
    return {
        key: value if key in _OWNER_TEXT_FIELDS else _redact_host_paths(value)
        for key, value in payload.items()
    }


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


# Swap traffic between two polls of the live view above this reads as the
# machine paging to disk.
_PAGING_BYTES = 32 * 1024 * 1024
# ...but only while less than this share of RAM is available. With plenty free,
# swap traffic is the OS moving idle pages out (or back in when touched), not
# memory pressure slowing the run.
_PAGING_AVAILABLE_SHARE = 0.10
# The swap counters (bytes in + out) each run's live view saw last, by job id.
_swap_seen: dict[int, int] = {}


def _paging(job_id: int, memory: dict) -> bool:
    """Whether the machine moved more than :data:`_PAGING_BYTES` through swap
    since the last time this run's live view was read, while RAM was tight.

    Args:
        job_id: The run whose live view is being read.
        memory: ``{total_mb, available_mb}`` of this machine's RAM.
    """
    try:
        swap = psutil.swap_memory()
    except Exception as exc:
        logger.debug("Swap counters cannot be read: %s", exc)
        return False
    moved = int(getattr(swap, "sin", 0) or 0) + int(getattr(swap, "sout", 0) or 0)
    last = _swap_seen.get(job_id)
    _swap_seen[job_id] = moved
    total = memory.get("total_mb") or 0
    tight = total > 0 and memory["available_mb"] < total * _PAGING_AVAILABLE_SHARE
    return tight and last is not None and moved - last > _PAGING_BYTES


def _telemetry(job: dict, lease: dict | None, resolution: dict) -> dict:
    """The GPU and RAM figures of a run that is waiting or training.

    The card and the machine's RAM are measured as a whole; the run's own share
    is measured from its process tree (NVML's per-process figure, resident
    memory) where the platform reports one, and is None where it does not.
    """
    gpu = {"total_mb": None, "used_mb": None, "ours_mb": None}
    ram = {"total_mb": None, "used_mb": None, "ours_mb": None}
    paging = False
    if job["status"] in ("queued", "running", "stopping"):
        tree = (
            measurements.process_tree(job.get("pid"))
            if job["status"] in ("running", "stopping")
            else []
        )
        pids = {process.pid for process in tree}
        device_uuid = (lease or {}).get("device") or ledger().pixlstash_device()
        reading = ledger().device(device_uuid)
        if reading is not None and reading.measurable:
            gpu.update(total_mb=reading.total_mb, used_mb=reading.used_mb)
            processes = ledger().processes(device_uuid) if pids else {}
            if processes:
                gpu["ours_mb"] = sum(processes.get(pid, 0) for pid in pids)
        memory = read_ram()
        if memory.get("total_mb"):
            ram.update(
                total_mb=memory["total_mb"],
                used_mb=memory["total_mb"] - memory["available_mb"],
                ours_mb=measurements.rss_mb(tree),
            )
        paging = _paging(job["id"], memory)
    else:
        _swap_seen.pop(job["id"], None)
    return {
        "gpu": gpu,
        "ram": ram,
        "need": {
            "vram_mb": resolution.get("vram_mb"),
            "ram_mb": resolution.get("ram_mb"),
            # Measured on this machine, or the recipe's seed.
            "basis": "measured"
            if resolution.get("evidence") == "measured"
            else "estimated",
        },
        "paging": paging,
    }


def _sha256_of(path: str) -> str | None:
    """The sha256 of a file's bytes, or None when it cannot be read."""
    try:
        with open(path, "rb") as fh:
            return hashlib.file_digest(fh, "sha256").hexdigest()
    except OSError as exc:
        logger.warning("Could not read %s to check its sha256: %s", path, exc)
        return None


def _epoch_iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()


def create_router(server) -> APIRouter:
    """Build the training/LoRA router bound to *server*.

    Args:
        server: The application server (provides ``auth`` and ``vault``).

    Returns:
        The configured :class:`fastapi.APIRouter`.
    """
    router = APIRouter()
    # A check holds a worker thread for up to PROBE + TRIAL timeouts; one at a time.
    check_lock = threading.Lock()
    # Recipes downloaded from pixlstash.dev live beside the hub.
    presets.use_cache_dir(
        os.path.join(os.path.dirname(server.server_config_path), "training-presets")
    )

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
                "Picture-set id to train on. With character_id it must be "
                "that character's reference set; alone, a reference set is "
                "filed under its character."
            ),
        )
        character_id: Optional[int] = Field(
            default=None,
            description=(
                "Character id to train on, or to file a reference-set run "
                "under. Give set_id, character_id or both."
            ),
        )
        name: str = Field(
            ...,
            min_length=1,
            max_length=MAX_JOB_NAME_LENGTH,
            description="Human-readable name for the job.",
        )
        trigger_word: str = Field(
            ...,
            min_length=1,
            max_length=MAX_TRIGGER_WORD_LENGTH,
            description=(
                "Caption trigger word baked into the training dataset. Stripped "
                "of surrounding spaces; the config, the sample prompts and the "
                "captions all use the stripped word."
            ),
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

        @field_validator("name")
        @classmethod
        def _name_is_plain(cls, value: str) -> str:
            return _checked_text(value, "name")

        @field_validator("trigger_word")
        @classmethod
        def _trigger_word_is_plain(cls, value: str) -> str:
            word = normalise_trigger_word(_checked_text(value, "trigger_word"))
            if not word:
                raise ValueError("trigger_word cannot be only spaces.")
            return word

    class ResolveRequest(BaseModel):
        """Body for previewing what a run would use, without queueing it."""

        model_config = ConfigDict(extra="forbid")

        set_id: Optional[int] = Field(
            default=None,
            description=(
                "Picture-set scope, as POST /training/jobs takes it. With neither "
                "scope the previews are written from no captions."
            ),
        )
        character_id: Optional[int] = Field(
            default=None,
            description="Character scope, as POST /training/jobs takes it.",
        )
        trigger_word: Optional[str] = Field(
            default=None,
            max_length=MAX_TRIGGER_WORD_LENGTH,
            description=(
                "The caption trigger word a run would use. The sample prompts "
                "are written with it."
            ),
        )
        preset: str = Field(
            default=presets.DEFAULT_PRESET,
            description="The training recipe's id (GET /training/presets).",
        )
        subject: Literal["person", "style", "thing"] = Field(
            ..., description="What the LoRA learns."
        )
        effort: Literal["gentle", "standard", "strong"] = Field(
            default="standard", description="How hard to train."
        )
        fit: Literal["auto", "save_memory", "best_quality"] = Field(
            default="auto", description="How to fit the run to this GPU."
        )
        picture_count: int = Field(
            ...,
            ge=0,
            le=MAX_PICTURE_COUNT,
            description="Pictures in the dataset (GET /training/dataset).",
        )
        pins: Optional[dict[str, Any]] = Field(
            default=None,
            description="The owner's Advanced values, as POST /training/jobs takes.",
        )

        @field_validator("trigger_word")
        @classmethod
        def _trigger_word_is_plain(cls, value: Optional[str]) -> Optional[str]:
            if value is None:
                return None
            return normalise_trigger_word(_checked_text(value, "trigger_word")) or None

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
        base_tile: Optional[dict] = Field(
            default=None,
            description=(
                "The tile the base model wears, as `mark`, `mark_tab`, "
                "`mark_colors`, `mark_icon`, `mark_letters` and `mark_band` on a "
                "shelf row, plus `label`, its canonical name. Null for a base "
                "model no known one matches."
            ),
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
        error: Optional[str] = Field(
            default=None,
            description="Why the run failed or stopped, when it says; host paths redacted.",
        )
        dataset_snapshot_id: Optional[int] = Field(
            default=None,
            description=(
                "The frozen record of what the run trained on, once its dataset "
                "is built (GET /training/jobs/{job_id}/snapshot)."
            ),
        )
        model_ids: list[int] = Field(
            default_factory=list,
            description=(
                "The shelf models (LoRAs) this run made, a stack cover "
                "first. Listing a set's runs with ?set_id= names the LoRAs "
                "trained from that set."
            ),
        )
        phase: Optional[str] = Field(
            default=None,
            description=(
                "'checking' while a queued run's memory and speed are checked; "
                "otherwise null (the live view names every phase)."
            ),
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
        dataset_snapshot_id: Optional[int] = Field(
            default=None, description="The run's dataset snapshot, once built."
        )
        model_ids: list[int] = Field(
            default_factory=list,
            description="The shelf models this run made, a stack cover first.",
        )

    class SnapshotItemResponse(BaseModel):
        """One picture as the trainer was given it."""

        position: int = Field(..., description="Order in the dataset, from 0.")
        picture_id: Optional[int] = Field(
            default=None,
            description="The picture it came from; may since have been deleted.",
        )
        file_name: str = Field(..., description="The file's name in the dataset.")
        sha256: str = Field(..., description="sha256 of the file's bytes.")
        caption: Optional[str] = Field(
            default=None, description="The caption as written; null if none."
        )

    class SnapshotResponse(BaseModel):
        """What a run trained on, frozen when its dataset was built (#2123)."""

        id: int = Field(..., description="Primary key of the snapshot.")
        job_id: int = Field(..., description="The run it was taken for.")
        set_id: Optional[int] = Field(default=None, description="Source set.")
        character_id: Optional[int] = Field(
            default=None, description="Source character."
        )
        trigger_word: Optional[str] = Field(
            default=None, description="The word every caption leads with."
        )
        caption_mode: Optional[str] = Field(
            default=None, description="How the captions were written."
        )
        image_count: int = Field(..., description="Number of items.")
        created_at: Optional[datetime] = Field(
            default=None, description="UTC time the snapshot was taken."
        )
        items: list[SnapshotItemResponse] = Field(
            default_factory=list, description="The items, in dataset order."
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

    class LiveJobResponse(BaseModel):
        """Everything the Training runs view draws for one run (#2118)."""

        model_config = ConfigDict(extra="allow")

        id: int = Field(..., description="Primary key of the job.")
        status: str = Field(..., description="Lifecycle status.")
        phase: Optional[str] = Field(
            default=None,
            description=(
                "waiting_gpu | checking | loading | caching | training | "
                "previews | saving | importing; null once the run has ended."
            ),
        )
        columns: list[int] = Field(
            default_factory=list,
            description="The checkpoint steps, step 0 first, one column each.",
        )
        report: Optional[str] = Field(
            default=None,
            description=(
                "The trainer's whole text, behind the one-line `error`; host "
                "paths redacted."
            ),
        )
        rows: list[dict] = Field(
            default_factory=list,
            description=(
                "One per sample prompt: {index, prompt, with_trigger, cells}, "
                "each cell {state, filename} in column order."
            ),
        )

    class CoverStepRequest(BaseModel):
        """Body for choosing the step that covers a run."""

        model_config = ConfigDict(extra="forbid")

        step: int = Field(..., ge=0, description="A checkpoint step of the run.")

    class CoverStepResponse(BaseModel):
        """The step chosen, and whether the shelf already changed."""

        job_id: int = Field(..., description="Primary key of the job.")
        cover_step: int = Field(..., description="The step that is the cover.")
        applied: bool = Field(
            ...,
            description=(
                "True when the run is on the shelf and its stack cover and set "
                "LoRA moved now; false when they move at its import."
            ),
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
        clips: bool = Field(
            default=False,
            description=(
                "Whether the recipe trains on the set's video clips as well as "
                "its stills; every other recipe leaves clips out."
            ),
        )

    class PresetUpdateItem(BaseModel):
        """A recipe a check replaced with a newer version from pixlstash.dev."""

        id: str = Field(..., description="The recipe's id.")
        label: str = Field(..., description="The model family it trains.")
        version: int = Field(..., description="The version now in use.")

    class PresetCheckResponse(BaseModel):
        """What a check for newer training recipes did."""

        checked: bool = Field(
            ...,
            description=(
                "False when an automatic check was not due or is not turned on."
            ),
        )
        updated: list[PresetUpdateItem] = Field(
            ..., description="The recipes replaced by a newer version."
        )
        error: Optional[str] = Field(
            None, description="Why the recipe list could not be read, if it could not."
        )

    class SamplePromptItem(BaseModel):
        """One sample prompt and the form clicking its trigger word gives."""

        prompt: str = Field(..., description="The prompt as the run uses it.")
        with_trigger: str = Field(
            ..., description="The prompt with {{TRIGGER_WORD}} in it."
        )
        without_trigger: str = Field(
            ..., description="The same scene without the trigger word."
        )

    class SamplePromptsResponse(BaseModel):
        """The default sample prompts written from the dataset's captions."""

        model_config = ConfigDict(extra="allow")

        subject: str = Field(..., description="person / style / thing.")
        prompts: list[SamplePromptItem] = Field(
            ...,
            description=(
                "Two scenes, each with the trigger word and without it. The "
                "trigger word is the {{TRIGGER_WORD}} placeholder."
            ),
        )
        description: Optional[str] = Field(
            default=None,
            description=(
                "What stands in for the trigger word in a person's or a "
                "thing's prompt ('an elderly man'); None for a style."
            ),
        )
        description_source: Optional[str] = Field(
            default=None,
            description="captions, tags or generic; None for a style.",
        )

    class DatasetCharacter(BaseModel):
        """The character a dataset belongs to, and its two picture choices."""

        id: int = Field(..., description="The character's id.")
        name: str = Field(..., description="The character's name.")
        reference_set_id: Optional[int] = Field(
            default=None, description="Its reference set, if it has one."
        )
        reference_count: int = Field(
            0, description="Pictures a run of the reference set would hold."
        )
        reference_clips: int = Field(
            0, description="Video clips a clip recipe adds to reference_count."
        )
        all_count: int = Field(
            ..., description="Pictures a run of every picture of it would hold."
        )
        all_clips: int = Field(
            0, description="Video clips a clip recipe adds to all_count."
        )
        child_set: Optional[dict] = Field(
            default=None,
            description=(
                "The set asked for when it sits under this character rather than "
                "being its reference set: { id, name, count }."
            ),
        )

    class DatasetResponse(BaseModel):
        """What a run of the set or character would train on, and its checks."""

        model_config = ConfigDict(extra="allow")

        set_id: Optional[int] = Field(
            default=None, description="The set the pictures come from, if any."
        )
        character_id: Optional[int] = Field(
            default=None,
            description=(
                "The character the run is filed under: the one asked for, or "
                "the one whose reference set set_id is."
            ),
        )
        count: int = Field(..., description="Still pictures the dataset would hold.")
        clips: int = Field(
            default=0,
            description=(
                "Video clips a recipe that trains on clips adds to count; "
                "every other recipe leaves them out. The checks below cover "
                "the stills."
            ),
        )
        min_pictures: int = Field(
            ..., description="Below this many pictures the dialog warns."
        )
        untagged: int = Field(
            ...,
            description=(
                "Pictures with no tags; their caption is the trigger word alone."
            ),
        )
        near_duplicates: int = Field(
            ..., description="Pictures with a near copy in the dataset."
        )
        other_people: int = Field(
            ...,
            description="Pictures showing a face named as another character.",
        )
        character: Optional[DatasetCharacter] = None
        parent_set_id: Optional[int] = Field(
            default=None,
            description=(
                "The set this set sits under, if any: with no character, a "
                "concept LoRA's dataset (#2124)."
            ),
        )

    class ResolveResponse(BaseModel):
        """What a run would use on this machine, before it is queued."""

        model_config = ConfigDict(extra="allow")

        resolution: Optional[dict] = Field(
            default=None,
            description="The resolver's result, as POST /training/jobs records it.",
        )
        derived: Optional[dict] = Field(
            default=None,
            description=(
                "Each value the choices give with no pins, as { value, source }: "
                "what a reset returns to."
            ),
        )
        previews: Optional[dict] = Field(
            default=None,
            description=(
                "The Previews question (#2415): while training, smaller or "
                "after training, each priced in time and VRAM peak; null when "
                "only one of them is worth having here."
            ),
        )
        error: Optional[dict] = Field(
            default=None,
            description=(
                "Why the run cannot be resolved: { message } and, when no "
                "memory profile fits, budget / need_mb / free_mb."
            ),
        )
        rules: dict = Field(
            default_factory=dict,
            description="Each listed key's type and range: { kind, lo, hi, count, choices }.",
        )
        steps_clamp: Optional[str] = Field(
            default=None,
            description=(
                "cap or floor when the effort's step range held the "
                "picture-scaled steps (no pins), else null."
            ),
        )
        steps_rule: dict = Field(
            ...,
            description=(
                "The effort's steps_per_picture, min_steps and max_steps, and "
                "fits: { lo, hi }, the picture counts it covers unclamped."
            ),
        )
        room: dict = Field(
            ..., description="The GPU and RAM a run could be given now (MiB)."
        )
        presets: list[dict] = Field(
            default_factory=list,
            description=(
                "Each recipe with a verdict for these choices: fits, streams, "
                "needs_ram, no_gpu or needs_checkout."
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

    def _checkout_version(repo_path: str | None) -> str | None:
        return ai_toolkit_install.read_version(repo_path) if repo_path else None

    def _preset_items(repo_path: str | None) -> list[dict]:
        architectures = presets.checkout_architectures(repo_path)
        version = _checkout_version(repo_path)
        return [
            {
                "id": preset["id"],
                "version": preset["version"],
                "label": preset["label"],
                "base_model": preset["locked"]["model.name_or_path"],
                "requires_arch": preset["requires_arch"],
                "offered": presets.offered(preset, architectures, version),
                "clips": presets.trains_clips(preset),
            }
            for preset in presets.list_presets()
        ]

    def _models_by_run(run_ids: list[int]) -> dict[int, list[int]]:
        """The shelf models each run made, by run id (one query for the lot).

        A stack cover first: position 0 is the row the shelf draws for a
        closed stack, so it is the one "Go to model" opens.
        """
        if not run_ids:
            return {}
        marks = ", ".join("?" for _ in run_ids)
        rows = server.vault.hub.fetchall(
            "SELECT id, training_run_id FROM model "
            f"WHERE training_run_id IN ({marks}) "
            "ORDER BY stack_position IS NULL, stack_position, id",
            tuple(run_ids),
        )
        models: dict[int, list[int]] = {}
        for row in rows:
            models.setdefault(row["training_run_id"], []).append(row["id"])
        return models

    def _checking_uuids() -> set[str]:
        """The runs whose granted lease the worker shows as checking (#2120)."""
        try:
            leases = arbiter().snapshot()["leases"]
        except Exception as exc:
            logger.warning("Could not read the leases for the job list: %s", exc)
            return set()
        return {
            row.get("job_id")
            for row in leases
            if row.get("state") == "granted"
            and (row.get("progress") or {}).get("shown_as") == "checking"
        }

    def _job_to_summary(
        job: dict, model_ids: list[int], checking: set[str] = frozenset()
    ) -> dict:
        return _redact_job(
            {
                "id": job["id"],
                "uuid": job["uuid"],
                "name": job["name"],
                "status": job["status"],
                # A probe runs while the row is still queued.
                "phase": (
                    "checking"
                    if job["status"] == "queued" and job["uuid"] in checking
                    else None
                ),
                "set_id": job["set_id"],
                "character_id": job["character_id"],
                "base_model": job["base_model"],
                "base_tile": training_base_tile(job["base_model"]),
                "trigger_word": job["trigger_word"],
                "step": job["step"],
                "total_steps": job["total_steps"],
                "created_at": job["created_at"],
                "started_at": job["started_at"],
                "completed_at": job["completed_at"],
                "run_name": _run_name(job),
                "error": job["error"],
                "dataset_snapshot_id": job["dataset_snapshot_id"],
                "model_ids": model_ids,
            }
        )

    def _run_name(job: dict) -> str | None:
        """The run's folder name in the shelf's ai-toolkit folder, or None.

        None before the run has a folder there, and for a run from before the
        shelf took runs, whose folder is under its work dir.
        """
        if not job["training_folder"]:
            return None
        handle = handle_for_run(_training_root(), job)
        if handle.training_folder != job["training_folder"]:
            return None
        return os.path.basename(handle.save_root)

    def _subject_for(subject, character_id) -> str:
        """The subject asked for; a character's defaults to a person."""
        if subject is not None:
            return subject
        if character_id is None:
            raise HTTPException(
                status_code=422,
                detail="Say what the LoRA should learn: person, style or thing.",
            )
        return "person"

    def _default_prompts(set_id, character_id, subject, trigger_word) -> dict:
        """The default sample prompts of a run of this scope.

        With no scope (resolve asked without one) they are written from no
        captions, which gives the same number of prompts.
        """
        captions, tags = [], []
        if set_id is not None or character_id is not None:
            captions, tags = ExportUtils.training_captions(
                server, set_id=set_id, character_id=character_id
            )
        return sample_prompts.generate(
            subject, captions, tags, normalise_trigger_word(trigger_word)
        )

    def _sample_prompt_texts(set_id, character_id, subject, trigger_word) -> list[str]:
        """The prompt strings a run stores. Create and resolve both plan from these."""
        return sample_prompts.prompt_texts(
            _default_prompts(set_id, character_id, subject, trigger_word)
        )

    def _cover_state(job: dict, run_dir_info) -> dict:
        """Which steps can cover the run, and which one does.

        On the shelf, the run's own models decide: a stepped model is its step
        and the bare final the run's last step, and the cover is the stack's
        position 0. Before that, a stepped checkpoint still on disk can be
        chosen (the trainer prunes old ones), and so can the last step, which
        is the final file once it is written.
        """
        total = job["total_steps"]
        models = server.vault.hub.fetchall(
            "SELECT training_step, stack_position FROM model WHERE training_run_id = ?",
            (job["id"],),
        )
        if models:
            steps = [
                row["training_step"] if row["training_step"] is not None else total
                for row in models
            ]
            cover = next(
                (
                    step
                    for row, step in zip(models, steps)
                    if row["stack_position"] == 0
                ),
                None,
            )
            return {
                "imported": True,
                "coverable": sorted({step for step in steps if step is not None}),
                "cover_step": cover,
            }
        stepped = set()
        has_final = False
        if run_dir_info is not None:
            stepped = {c.step for c in run_dir_info.checkpoints if c.step is not None}
            has_final = any(c.is_final for c in run_dir_info.checkpoints)
        alive = job["status"] in ("queued", "running", "stopping", "completed")
        if total and (has_final or alive):
            stepped.add(total)
        chosen = job.get("cover_step")
        return {
            "imported": False,
            "coverable": sorted(stepped),
            "cover_step": chosen
            if chosen is not None
            else (total if has_final else None),
        }

    def _run_dir(job: dict):
        """The run's paths, and its folder as read from disk (None before it exists)."""
        handle = handle_for_run(_training_root(), job)
        on_disk = None
        if os.path.isdir(handle.save_root):
            try:
                on_disk = read_run(handle.save_root)
            except OSError as exc:
                logger.warning(
                    "Could not read the run folder of job %s: %s", job["uuid"], exc
                )
        return handle, on_disk

    def _live_payload(job: dict) -> dict:
        hub = server.vault.hub
        config = _parse_json_field(
            job["rendered_config"], "rendered_config", job["uuid"]
        )
        resolution = (
            _parse_json_field(job["resolved_profile"], "resolved_profile", job["uuid"])
            or {}
        )
        settings = live_view.sample_settings(config)
        handle, on_disk = _run_dir(job)
        samples = on_disk.samples if on_disk is not None else []
        live = job["status"] in ("queued", "running", "stopping")
        info = (
            live_view.read_trainer_info(handle.sqlite_db_path, job["uuid"])
            if live and os.path.exists(handle.sqlite_db_path)
            else ""
        )
        step = int(job["step"] or 0)
        total = job["total_steps"]
        phase = live_view.phase_of(job["status"], info, step, job["completed_at"])
        lease = None
        try:
            lease = next(
                (
                    row
                    for row in arbiter().snapshot()["leases"]
                    if row.get("job_id") == job["uuid"]
                ),
                None,
            )
        except Exception as exc:
            logger.warning("Could not read the leases for job %s: %s", job["uuid"], exc)
        # The worker's phase telemetry names what the trainer's own info line
        # cannot: a probe ("checking", while the row is still queued) and the
        # previews drawn after training (while it is completed, not imported).
        shown = ((lease or {}).get("progress") or {}).get("shown_as")
        if (
            lease is not None
            and lease.get("state") == "granted"
            and shown in ("checking", "previews")
            and job["status"] in ("queued", "completed")
            and not job["completed_at"]
        ):
            phase = shown
        previews = live_view.preview_progress(info) if phase == "previews" else None
        # A model the trainer has not loaded before is fetched while it loads,
        # by the probe while checking or by the run itself.
        downloading = False
        if phase in ("checking", "loading"):
            log_path = handle.log_path
            if phase == "checking":
                log_path = LocalAiToolkit.handle_for(
                    os.path.join(handle.work_dir, "probe"),
                    job["uuid"],
                    job["name"],
                    job.get("pid"),
                ).log_path
            downloading = live_view.downloading(log_path)
        deferred = bool(resolution.get("previews_deferred"))

        columns = live_view.checkpoint_columns(
            total, settings["sample_every"], [s.step for s in samples]
        )
        drawing_column = max((c for c in columns if c <= step), default=0)
        by_cell = {}
        for sample in sorted(samples, key=lambda s: s.timestamp):
            by_cell[(sample.step, sample.index)] = sample.filename
        rows = live_view.prompt_rows(settings["prompts"], job["trigger_word"])
        if not rows:
            rows = [
                {"index": index, "prompt": "", "with_trigger": None}
                for index in sorted({s.index for s in samples})
            ]
        for row in rows:
            row["cells"] = [
                {
                    "state": live_view.cell_state(
                        column=column,
                        index=row["index"],
                        has_sample=(column, row["index"]) in by_cell,
                        phase=phase,
                        step=step,
                        drawing_column=drawing_column,
                        previews=previews,
                        previews_deferred=deferred,
                    ),
                    "filename": by_cell.get((column, row["index"])),
                }
                for column in columns
            ]

        folder_id = None
        if job["training_folder"] and handle.training_folder == job["training_folder"]:
            folder = hub.fetchone(
                "SELECT id FROM model_folder WHERE kind = ? AND path = ?",
                (SOURCE_FOLDER_KIND, job["training_folder"]),
            )
            folder_id = folder["id"] if folder is not None else None

        waiting = None
        if lease is not None and lease.get("state") == "waiting":
            reason = lease.get("waiting_reason") or {}
            waiting = {"code": reason.get("code"), "text": reason.get("text")}

        step_times = (
            live_view.read_step_times(handle.loss_db_path)
            if os.path.exists(handle.loss_db_path)
            else []
        )
        pace = live_view.speed(step_times, total)
        if not live:
            pace["remaining_low_s"] = pace["remaining_high_s"] = None

        log = live_view.parse_phase_log(job.get("phase_log"))
        # A run that ended without a completed_at (an error before launch, a
        # queued run cancelled) has no end to its last phase: unknown, rather
        # than a duration that grows on every read.
        until = job["completed_at"] or (
            now_iso() if live or job["status"] == "completed" else None
        )
        unwatched = bool(job.get("finished_unwatched")) and job["status"] == "completed"
        return {
            "id": job["id"],
            "uuid": job["uuid"],
            "name": job["name"],
            "status": job["status"],
            "phase": phase,
            "previews": previews,
            "downloading": downloading,
            "previews_deferred": deferred,
            "step": step,
            "total_steps": total,
            "set_id": job["set_id"],
            "character_id": job["character_id"],
            "base_model": job["base_model"],
            "base_tile": training_base_tile(job["base_model"]),
            "trigger_word": job["trigger_word"],
            "phases": live_view.phase_durations(log, until),
            "columns": columns,
            "rows": rows,
            "run_folder": (
                {"folder_id": folder_id, "run_name": _run_name(job)}
                if folder_id is not None
                else None
            ),
            **_cover_state(job, on_disk),
            "loss_curve": _parse_json_field(
                job["loss_curve"], "loss_curve", job["uuid"]
            )
            or [],
            "dataset_picture_ids": _parse_json_field(
                job["dataset_picture_ids"], "dataset_picture_ids", job["uuid"]
            )
            or [],
            "streamed": (
                {"percent": settings["offload_percent"]}
                if settings["layer_offloading"]
                else None
            ),
            "speed": {
                "seconds_per_step": pace["seconds_per_step"],
                "remaining_low_s": pace["remaining_low_s"],
                "remaining_high_s": pace["remaining_high_s"],
            },
            "telemetry": _telemetry(job, lease, resolution),
            "waiting": waiting,
            "finished_while_closed": (
                {"at": _epoch_iso(pace["last_step_at"]) or job["completed_at"]}
                if unwatched
                else None
            ),
            "error": job["error"],
            "report": job["report"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            "dataset_snapshot_id": job["dataset_snapshot_id"],
        }

    def _scope(set_id, character_id) -> tuple[Optional[int], Optional[int]]:
        """The set the pictures come from and the character the run is filed under.

        A set, a character, or a character with one of its own sets. A
        reference set, or a set under a person (directly or through other
        sets), given alone is filed under that person, so the LoRA shows up
        there.
        """
        if set_id is None and character_id is None:
            raise HTTPException(
                status_code=400,
                detail="Give a set_id, a character_id, or both for a reference set.",
            )
        if set_id is None:
            return None, character_id

        owner = server.vault.db.run_task(owning_character_id, set_id)
        if character_id is not None and owner != character_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "A set_id and a character_id together must name that "
                    "character's reference set or a set under that character."
                ),
            )
        return set_id, owner

    # repo path -> (read at, commit): /resolve runs on every change in the Train
    # dialog, and must not start a git process each time.
    commits: dict[str, tuple[float, str | None]] = {}

    def _commit(repo_path: str) -> str | None:
        read_at, commit = commits.get(repo_path, (None, None))
        if read_at is None or time.monotonic() - read_at > _COMMIT_TTL_S:
            commit = ai_toolkit_install.read_commit(repo_path)
            commits[repo_path] = (time.monotonic(), commit)
        return commit

    def _measured(repo_path: str | None) -> dict:
        """The checkout's commit and the card's name, which measurements are
        tied to (§25.20)."""
        reading = ledger().device(ledger().pixlstash_device())
        return {
            "commit": _commit(repo_path) if repo_path else None,
            "device": reading.name if reading is not None else None,
        }

    def _lookup(preset, context: dict):
        return measurements.make_lookup(server.vault.hub, preset, **context)

    def _verdict(preset, offered: bool, context, **choices) -> str:
        """How *preset* would fit this machine with *choices* and no pins."""
        if not offered:
            return "needs_checkout"
        try:
            resolution = presets.resolve(
                preset, lookup=_lookup(preset, context), **choices
            )
        except presets.NoProfileFits as exc:
            return "needs_ram" if exc.budget == "ram" else "no_gpu"
        values = resolution["values"]
        streams = any(
            values.get(key, {}).get("value")
            for key in ("model.layer_offloading", "model.low_vram")
        )
        return "streams" if streams else "fits"

    def _rule(rule) -> dict:
        return {
            "kind": rule.kind,
            "lo": rule.lo,
            "hi": rule.hi,
            "count": rule.count,
            "choices": sorted(rule.choices),
        }

    # ── Training jobs ─────────────────────────────────────────────────────

    @router.post(
        "/training/jobs",
        summary="Enqueue a training job",
        response_model=CreateJobResponse,
        description=(
            "Creates a queued LoRA training job for a picture set, a character "
            "or a character's reference set (filed under the character). The "
            "background trainer picks it up FIFO. "
            "Owner-only."
        ),
    )
    def create_training_job(payload: CreateJobRequest, request: Request):
        _ensure_secure(request)

        set_id, character_id = _scope(payload.set_id, payload.character_id)
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
        refusal = presets.not_offered_because(
            preset,
            presets.checkout_architectures(repo_path),
            _checkout_version(repo_path),
        )
        if refusal:
            raise HTTPException(status_code=422, detail=refusal)
        subject = _subject_for(payload.subject, character_id)
        try:
            pins = presets.validate_pins(preset, payload.pins)
        except TunableOverrideError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        picture_count = ExportUtils.count_training_pictures(
            server,
            set_id=set_id,
            character_id=character_id,
            clips=presets.trains_clips(preset),
        )
        if picture_count == 0:
            raise HTTPException(
                status_code=422,
                detail="There are no pictures to train on.",
            )
        prompts = None
        if presets.PROMPTS_KEY not in pins:
            prompts = _sample_prompt_texts(
                set_id, character_id, subject, payload.trigger_word
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
                prompts=prompts,
                lookup=_lookup(preset, _measured(repo_path)),
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
                set_id=set_id,
                character_id=character_id,
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

        return _redact_job(
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
        models = _models_by_run([run["id"] for run in runs])
        checking = _checking_uuids()
        return [
            _job_to_summary(run, models.get(run["id"], []), checking) for run in runs
        ]

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
        return _redact_job(
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
                "dataset_snapshot_id": job["dataset_snapshot_id"],
                "model_ids": _models_by_run([job["id"]]).get(job["id"], []),
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
        # A trainer can report a step past its planned total; the fraction stops at 1.
        progress = min(job["step"] / total, 1.0) if total > 0 else 0.0
        return _redact_job(
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

    @router.get(
        "/training/jobs/{job_id}/live",
        summary="Watch a training job",
        response_model=LiveJobResponse,
        description=(
            "Everything the Training runs view draws for one job, read fresh on "
            "every call: its phase, its checkpoint columns and sample prompts "
            "with the preview that fills each cell, the loss curve, the "
            "steps that can cover it and the one that does, seconds per update "
            "and the time left from the trainer's own step log, and the GPU and "
            "RAM figures while it waits or trains. A figure that cannot be "
            "read is null, never 0. Owner-only."
        ),
    )
    def get_training_job_live(job_id: int, request: Request):
        _ensure_secure(request)
        hub, library_uuid = _store()
        job = training_runs.get_run(hub, library_uuid, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        return _redact_job(_live_payload(job))

    @router.put(
        "/training/jobs/{job_id}/cover",
        summary="Choose the step that covers a training job",
        response_model=CoverStepResponse,
        description=(
            "Makes one checkpoint the run's stack cover, so the LoRA attached "
            "to its set or character is that step rather than the last. On a "
            "run already on the shelf the stack and the attachment move now; "
            "before that the step is recorded and applied when the run lands. "
            "Returns 404 for an unknown job and 409 for a step the run kept no "
            "checkpoint at. Owner-only."
        ),
    )
    def set_training_job_cover(
        job_id: int, payload: CoverStepRequest, request: Request
    ):
        _ensure_secure(request)
        hub, library_uuid = _store()
        job = training_runs.get_run(hub, library_uuid, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        _, on_disk = _run_dir(job)
        if payload.step not in _cover_state(job, on_disk)["coverable"]:
            raise HTTPException(
                status_code=409,
                detail=f"No checkpoint of this run is kept at step {payload.step}.",
            )
        # Recorded before the shelf is looked at: an import that reads the row
        # after this applies it, and one that read it earlier has already
        # committed its models, which the call below then finds and moves.
        previous = job.get("cover_step")
        training_runs.update_run(hub, job["uuid"], cover_step=payload.step)
        try:
            applied = server.vault.choose_training_cover(job, payload.step)
        except LookupError as exc:
            training_runs.update_run(hub, job["uuid"], cover_step=previous)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            # The stack cover lives in the hub and the attachment in the vault,
            # so a failure between them cannot be rolled back as one; the
            # recorded step goes back and the failure is logged in full.
            training_runs.update_run(hub, job["uuid"], cover_step=previous)
            logger.exception(
                "Training job id=%s: step %s could not be made the cover.",
                job_id,
                payload.step,
            )
            raise HTTPException(
                status_code=500, detail="Could not make that step the cover."
            ) from exc
        logger.info(
            "Training job id=%s: step %s chosen as the cover (applied=%s).",
            job_id,
            payload.step,
            applied,
        )
        return {"job_id": job_id, "cover_step": payload.step, "applied": applied}

    def _snapshot_of(job_id: int) -> tuple[dict, dict]:
        """One run of the open library and its dataset snapshot, or a 404."""
        hub, library_uuid = _store()
        job = training_runs.get_run(hub, library_uuid, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Training job not found")
        snapshot_id = job["dataset_snapshot_id"]
        snapshot = (
            dataset_snapshots.get_snapshot(hub, library_uuid, snapshot_id)
            if snapshot_id is not None
            else None
        )
        if snapshot is None:
            raise HTTPException(
                status_code=404, detail="This run has no dataset snapshot."
            )
        return job, snapshot

    @router.get(
        "/training/jobs/{job_id}/snapshot",
        summary="What a training job trained on",
        response_model=SnapshotResponse,
        description=(
            "The dataset snapshot taken when the job's dataset was built: one "
            "item per picture the trainer was given, with its file name, the "
            "sha256 of those bytes and its caption as written. It is not "
            "re-read from the library, so later tag edits and deletes do not "
            "change it. Returns 404 for an unknown job and for one whose "
            "dataset is not built yet. Owner-only."
        ),
    )
    def get_training_job_snapshot(job_id: int, request: Request):
        _ensure_secure(request)
        job, snapshot = _snapshot_of(job_id)
        return {
            **{key: value for key, value in snapshot.items() if key != "library_uuid"},
            "job_id": job["id"],
        }

    @router.get(
        "/training/jobs/{job_id}/snapshot/export",
        summary="Download what a training job trained on, as a zip",
        response_class=FileResponse,
        responses={200: {"content": {"application/zip": {}}}},
        description=(
            "A zip of the job's dataset snapshot: each image the trainer was "
            "given with its frozen caption beside it, and snapshot.json listing "
            "every item. An image whose dataset file is gone or no longer "
            "matches its recorded sha256 is left out and marked so in "
            "snapshot.json; its caption is still included. Returns 404 for an "
            "unknown job and for one with no snapshot. Owner-only."
        ),
    )
    def export_training_job_snapshot(job_id: int, request: Request):
        _ensure_secure(request)
        job, snapshot = _snapshot_of(job_id)
        dataset_dir = job.get("dataset_snapshot_path") or ""
        fd, zip_path = tempfile.mkstemp(prefix="pixlstash_snapshot_", suffix=".zip")
        os.close(fd)
        listed = []
        try:
            with zipfile.ZipFile(zip_path, "w") as archive:
                for item in snapshot["items"]:
                    name = item["file_name"]
                    source = os.path.join(dataset_dir, name)
                    included = (
                        bool(dataset_dir)
                        and os.path.basename(name) == name
                        and _sha256_of(source) == item["sha256"]
                    )
                    if included:
                        archive.write(source, arcname=name)
                    if item["caption"] is not None:
                        stem = os.path.splitext(name)[0]
                        archive.writestr(f"{stem}.txt", f"{item['caption']}\n")
                    listed.append({**item, "image_included": included})
                archive.writestr(
                    "snapshot.json",
                    json.dumps(
                        {
                            "run": job["name"],
                            "taken_at": snapshot["created_at"],
                            "set_id": snapshot["set_id"],
                            "character_id": snapshot["character_id"],
                            "trigger_word": snapshot["trigger_word"],
                            "caption_mode": snapshot["caption_mode"],
                            "items": listed,
                        },
                        indent=2,
                    ),
                )
        except Exception as exc:
            try:
                os.remove(zip_path)
            except OSError as remove_exc:
                logger.warning("Could not remove %s: %s", zip_path, remove_exc)
            logger.exception(
                "Training job id=%s: the dataset snapshot %s could not be zipped "
                "from %s.",
                job_id,
                snapshot["id"],
                dataset_dir,
            )
            raise HTTPException(
                status_code=500, detail="Could not build the dataset zip."
            ) from exc
        left_out = sum(1 for item in listed if not item["image_included"])
        if left_out:
            logger.warning(
                "Training job id=%s: %d of %d snapshot images were missing or "
                "changed in %s and were left out of the zip.",
                job_id,
                left_out,
                len(listed),
                dataset_dir,
            )
        stem = re.sub(r"[^\w.-]+", "_", job["name"]).strip("._") or "training"
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"{stem}_dataset.zip",
            background=BackgroundTask(os.remove, zip_path),
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
            # was in a terminal state (completed/stopped/error). Name the state
            # cancel() saw: the one read above may be stale by now.
            latest = training_runs.get_run(hub, library_uuid, job_id)
            if latest is None:
                raise HTTPException(status_code=404, detail="Training job not found")
            raise HTTPException(
                status_code=409,
                detail=f"Job is already in a terminal state ({latest['status']}).",
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
            "model with its own copy. Returns 404 if the job is unknown, and "
            "409 while it is queued, running or stopping (cancel it first), "
            "or still drawing its previews or being imported. "
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
        # Deleting the work dir under a live trainer would orphan the process,
        # which keeps writing into the removed paths. The status test is in the
        # DELETE itself, so the job cannot start between a check and the delete.
        job_uuid = training_runs.delete_run(
            hub, library_uuid, job_id, only_finished=True
        )
        if job_uuid is None:
            current = training_runs.get_run(hub, library_uuid, job_id)
            if current is None:
                raise HTTPException(status_code=404, detail="Training job not found")
            if current["status"] == "completed":
                detail = "Job is still drawing its previews or being imported."
            else:
                detail = f"Job is still {current['status']}; cancel it before deleting."
            raise HTTPException(status_code=409, detail=detail)

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
            "/status does. One check runs at a time: another while it runs is a "
            "409. Owner-only, from this machine."
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
            validate_ai_toolkit_settings(payload.repo_path, python)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not check_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail="An AI-toolkit check is already running. Try again when it ends.",
            )
        try:
            result = ai_toolkit_install.check(payload.repo_path, python)
        finally:
            check_lock.release()
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
            "toolkit/, and the Python must be an executable in its .venv or venv, "
            "or the python3 AI-toolkit itself would use. Both null disconnects the "
            "install. The Python is the trainer's argv[0] and the checkout its "
            "working directory, so only this computer (loopback) may set them."
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

    @router.post(
        "/training/presets/check",
        summary="Check pixlstash.dev for newer training recipes",
        response_model=PresetCheckResponse,
        description=(
            "Fetches the recipe list from pixlstash.dev and keeps each newer "
            "recipe that validates, matches its sha256, needs no newer "
            "PixlStash and changes no model source; anything else is logged "
            "and ignored. `automatic` is the daily check: it does nothing "
            "unless the owner turned automatic checks on, or when the last "
            "check was under a day ago. Owner-only."
        ),
    )
    def check_training_presets(request: Request, automatic: bool = False):
        _ensure_secure(request)
        if automatic:
            user = server.auth.get_user()
            if user is None or not user.training_presets_auto_check:
                return {"checked": False, "updated": [], "error": None}
        return preset_updates.check(automatic=automatic)

    @router.get(
        "/training/sample-prompts",
        summary="Default sample prompts for a training run",
        response_model=SamplePromptsResponse,
        description=(
            "Writes the sample prompts a run of the set or character would "
            "use if none are given: two scenes its captions do not show, each "
            "with the trigger word and without it. The trigger word, when "
            "given, is kept out of the description. Deterministic. Owner-only."
        ),
    )
    def get_training_sample_prompts(
        request: Request,
        set_id: Optional[int] = None,
        character_id: Optional[int] = None,
        subject: Optional[Literal["person", "style", "thing"]] = None,
        trigger_word: Optional[str] = None,
    ):
        _ensure_secure(request)
        set_id, character_id = _scope(set_id, character_id)
        subject = _subject_for(subject, character_id)
        return {
            "subject": subject,
            **_default_prompts(set_id, character_id, subject, trigger_word),
        }

    @router.get(
        "/training/dataset",
        summary="What a training run of a set or character would hold",
        response_model=DatasetResponse,
        description=(
            "Counts the pictures a run of the set or character would train on "
            "and what the Train dialog checks before it starts: pictures "
            "without tags, near copies, and faces named as somebody else. For "
            "a character, or a set that is a character's reference set, it "
            "also gives the character and both picture counts. A set and a "
            "character together mean that character's reference set. "
            "Owner-only."
        ),
    )
    def get_training_dataset(
        request: Request,
        set_id: Optional[int] = None,
        character_id: Optional[int] = None,
    ):
        _ensure_secure(request)
        set_id, character_id = _scope(set_id, character_id)
        facts = ExportUtils.training_facts(
            server, set_id=set_id, character_id=character_id
        )
        scoped_set = (
            server.vault.db.run_task(lambda session: session.get(PictureSet, set_id))
            if set_id is not None
            else None
        )
        character = None
        if character_id is not None:
            row = server.vault.db.run_task(
                lambda session: session.get(Character, character_id)
            )
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"No character {character_id}."
                )
            ref = row.reference_picture_set_id

            def counts(**scope):
                return ExportUtils.training_counts(server, **scope)

            is_child = set_id is not None and set_id != ref
            asked = (facts["count"], facts["clips"])
            reference = (
                asked
                if set_id is not None and not is_child
                else (counts(set_id=ref) if ref is not None else (0, 0))
            )
            every = asked if set_id is None else counts(character_id=character_id)
            character = {
                "id": row.id,
                "name": row.name,
                "reference_set_id": ref,
                "reference_count": reference[0],
                "reference_clips": reference[1],
                "all_count": every[0],
                "all_clips": every[1],
                "child_set": None,
            }
            if is_child:
                character["child_set"] = {
                    "id": set_id,
                    "name": scoped_set.name if scoped_set is not None else "",
                    "count": facts["count"],
                    "clips": facts["clips"],
                }
        return {
            "set_id": set_id,
            "character_id": character_id,
            "min_pictures": MIN_PICTURES,
            "character": character,
            "parent_set_id": scoped_set.parent_set_id if scoped_set else None,
            **facts,
        }

    @router.post(
        "/training/resolve",
        summary="Preview what a training run would use",
        response_model=ResolveResponse,
        description=(
            "Resolves the recipe for the choices, the picture count and the "
            "GPU and RAM a run could be given now, exactly as POST "
            "/training/jobs would, without queueing anything. Also gives the "
            "values the choices give with no pins, the type and range of each "
            "value, and how every recipe would fit. A run that cannot be "
            "resolved answers 200 with `error`. Owner-only."
        ),
    )
    def resolve_training_run(payload: ResolveRequest, request: Request):
        _ensure_secure(request)
        preset = presets.get_preset(payload.preset)
        if preset is None:
            raise HTTPException(
                status_code=404, detail=f"No training recipe '{payload.preset}'."
            )
        row = preset["effort"][payload.effort]
        room = server.vault.training_room()
        rule = {k: row[k] for k in presets.EFFORT_STEP_KEYS}
        # The picture counts the effort's step range covers without clamping.
        rule["fits"] = {
            "lo": math.ceil(row["min_steps"] / row["steps_per_picture"]),
            "hi": math.floor(row["max_steps"] / row["steps_per_picture"]),
        }
        out = {"steps_rule": rule, "room": room}
        if room["vram_mb"] is None:
            out["error"] = {
                "message": (
                    "PixlStash cannot read this GPU's memory, so it cannot "
                    "choose how to fit the run to it."
                )
            }
            return out
        set_id = character_id = None
        if payload.set_id is not None or payload.character_id is not None:
            set_id, character_id = _scope(payload.set_id, payload.character_id)
        # The same prompts POST /training/jobs plans from, so the previews
        # counted here are the ones a run gets.
        prompts = _sample_prompt_texts(
            set_id, character_id, payload.subject, payload.trigger_word
        )
        choices = {
            "subject": payload.subject,
            "effort": payload.effort,
            "fit": payload.fit,
            "picture_count": payload.picture_count,
            "vram_mb": room["vram_mb"],
            "ram_mb": room["ram_mb"],
            "prompts": prompts,
        }
        user = server.auth.get_user()
        repo_path = user.ai_toolkit_repo_path if user is not None else None
        architectures = presets.checkout_architectures(repo_path)
        version = _checkout_version(repo_path)
        context = _measured(repo_path)
        offers = {
            p["id"]: presets.offered(p, architectures, version)
            for p in presets.list_presets()
        }
        out["presets"] = [
            {
                "id": p["id"],
                "label": p["label"],
                "offered": offers[p["id"]],
                "verdict": _verdict(p, offers[p["id"]], context, **choices),
            }
            for p in presets.list_presets()
        ]
        choices["lookup"] = _lookup(preset, context)
        keys = set()
        try:
            derived = presets.resolve(preset, **choices)
            out["derived"] = derived["values"]
            out["steps_clamp"] = derived["steps_clamp"]
            keys |= set(out["derived"])
        except presets.NoProfileFits as exc:
            logger.info("No pin-free %s profile fits: %s", preset["id"], exc)
        try:
            pins = presets.validate_pins(preset, payload.pins)
            out["resolution"] = presets.resolve(preset, pins=pins, **choices)
            keys |= set(out["resolution"]["values"])
        except presets.NoProfileFits as exc:
            out["error"] = {
                "message": str(exc),
                "budget": exc.budget,
                "need_mb": exc.need_mb,
                "free_mb": exc.free_mb,
            }
        except (TunableOverrideError, ValueError) as exc:
            out["error"] = {"message": str(exc)}
        if "resolution" in out:
            # The question is extra: pricing it never spoils a plan that holds.
            try:
                out["previews"] = presets.preview_choice(preset, pins=pins, **choices)
            except (TunableOverrideError, ValueError) as exc:
                logger.warning(
                    "Could not price the Previews question for %s with pins %s: %s",
                    preset["id"],
                    sorted(pins),
                    exc,
                )
        out["rules"] = {
            key: _rule(TUNABLE_KEYS[key]) for key in sorted(keys) if key in TUNABLE_KEYS
        }
        return out

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
