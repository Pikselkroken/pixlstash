"""Registering the folders the model shelf catalogues, and rescanning one.

A ``model_folder`` is a **hub** row: a folder of LoRAs is a fact about this disk,
and re-registering the same folder in every library would be absurd. So these
routes read and write the hub directly rather than going through the vault.

**Removing a folder is a tombstone, not a deletion.** The folder's ``model_file``
rows go and the ``model`` rows stay, with the display name, triggers, corrected
``file_kind`` and vault-side attachments the owner gave them intact. Re-adding the
folder re-links by content on the next scan. That is precisely what lets folder
removal skip a confirmation prompt (shelf plan §7): nothing a person typed is
destroyed by it. If this ever hard-deletes ``model`` rows, the confirmation comes
back.

**Forgetting takes the machine-wide** ``SHELF_IO_LOCK`` **and 409s if it is
held** (#1017). It is harmless to the models, but it deletes exactly the
``model_file`` rows a running move or import is writing, and a move whose source
row disappeared mid-flight would unlink the source with nothing left registering
the copy it had just made.

**It is a slot, not a general exclusion, and the mover is what actually
guarantees the invariant.** A rescan writes the same rows and is deliberately
*outside* this lock (backend architecture §"MODEL_FOLDER_SCAN"), and a
multi-run import is a sequence of separate lock-taking requests, so a forget can
still land between two of them. What closes the hole in every case is
``ModelMover._repoint`` refusing a commit that does not move exactly one row.
This half stops the *move-versus-forget* interleaving the report was about, and
turns a file failed halfway through a 40-file batch into a clean 4xx before the
batch starts.

**Exactly one ``managed`` folder always exists and cannot be forgotten.** It
is PixlStash's own model storage - created on first run, the default destination
for a drop or an import - so there is no association to dissolve and ``DELETE``
answers 409. ``user`` and ``foreign`` folders may legitimately number zero; that
is a normal state, not an error. See
:mod:`pixlstash.services.managed_model_store`.

**Two of the four columns are derived, not asked for.** ``movable`` and ``owner``
follow from ``kind``, and offering them as inputs would let a caller register a
combination that means nothing (an ``external``-movable ``user`` folder). Only
``user`` and ``source`` are creatable over HTTP: ``managed`` and ``foreign``
describe locations PixlStash registers for itself (tagger artifacts, the
InsightFace root, the HuggingFace cache), and a hand-made row of either kind would
collide with that registration.

**A rescan is a task, not a thread.** ``POST .../rescan`` submits a
:class:`~pixlstash.tasks.model_folder_scan_task.ModelFolderScanTask` to the
shared ``TaskRunner`` and answers 202 with its ``task_id``. That is what gives
the walk per-file progress (``GET /workers/progress`` →
``workers.ModelFolderScanTask``) and a real terminal state (the folder's
``scan_status``), neither of which the daemon thread it replaced could offer -
and it is why the scan no longer outlives the process that started it (#856).

Authorization: the read is ``OWNER_ONLY``; every mutator and the rescan are
``LOCAL_OWNER_ONLY`` with a §16.3 justification, because they take - or walk - a
caller-supplied host path. That is the same tier and the same reason as the
``reference-folders`` block. Declared in ``pixlstash/authz/registry.py``, never
inline.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_models import BUILTIN_OWNER
from pixlstash.services.managed_model_store import (
    MANAGED_KIND,
    deletes_unclaimed_files,
    relocatable_identity,
)
from pixlstash.services.model_mover import SHELF_IO_LOCK
from pixlstash.tasks.base_task import TaskStatus
from pixlstash.tasks.model_folder_scan_task import ModelFolderScanTask
from pixlstash.utils.host_path_utils import is_absolute_host_path, normalize_host_path
from pixlstash.utils.path_utils import path_is_within
from pixlstash.utils.reference_folder_validator import (
    validate_reference_folder_accessible,
    validate_reference_folder_path,
)
from pixlstash.utils.system_utils import describe_storage_device

logger = get_logger(__name__)

# The folder kinds a caller may register. ``managed`` and ``foreign`` are
# PixlStash's own (tagger artifacts, InsightFace, the HuggingFace cache) and are
# registered by the code that owns them.
CREATABLE_KINDS = ("user", "source")

# ``movable`` and ``owner`` follow from ``kind``: a user folder holds files that
# can each be moved individually; a source folder is an ai-toolkit output root,
# taken FROM and never catalogued in place.
_DERIVED_BY_KIND = {
    "user": ("per_item", None),
    "source": ("external", "ai-toolkit"),
}

# The most recent scan submitted for each folder, whatever state it ended in.
# One entry per registered folder, dropped when the folder is forgotten, so it
# cannot grow.
#
# It serves two purposes. It is the "already running" gate: the scanner is
# correct under concurrent runs (its missing sweep is ``seen_at <`` the run's own
# stamp, not ``!=``), so this is not a correctness lock - it stops a double-click
# from reading 438 GB twice. And it is where a *finished* scan's outcome lives,
# because ``TaskRunner`` forgets a task the moment it completes; a caller that
# had only ``last_checked`` to look at could not tell a crash from a slow read.
#
# In memory on purpose: after a restart nothing is scanning and nothing is
# pending, so there is no state worth persisting.
_scans: dict[int, ModelFolderScanTask] = {}
_scans_lock = threading.Lock()

# The two states that mean "this folder is spoken for". PENDING covers the
# window between submission and a worker picking the task up, which the old bare
# thread had no equivalent of - it started running the moment it was created.
_SCAN_IN_FLIGHT = (TaskStatus.PENDING, TaskStatus.RUNNING)


def _scan_state(folder_id: int) -> tuple[Optional[str], Optional[str]]:
    """Return ``(scan_status, scan_error)`` for a folder's most recent scan."""
    with _scans_lock:
        task = _scans.get(folder_id)
    if task is None:
        return None, None
    return task.status.value, task.error


class ModelFolderCreateRequest(BaseModel):
    """Body of ``POST /model-folders``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description="Absolute host path to register. Owner-chosen and therefore trusted."
    )
    kind: str = Field(
        default="user",
        description=(
            "`user` for a folder to catalogue in place, `source` for an "
            "ai-toolkit output root that is scanned for importable runs instead."
        ),
    )
    host_path: Optional[str] = Field(
        default=None,
        description="Docker bind source, for the same reason import folders carry one.",
    )
    delete_after_import: bool = Field(
        default=False,
        description="`source` folders only: remove the run's files once imported.",
    )


class ModelFolderUpdateRequest(BaseModel):
    """Body of ``PATCH /model-folders/{folder_id}``.

    Deliberately narrow. Changing ``path`` is a relocation (B7, which must copy,
    verify and only then unlink), and changing ``kind`` changes what the folder
    *is* - neither is a field edit.
    """

    model_config = ConfigDict(extra="forbid")

    host_path: Optional[str] = None
    delete_after_import: Optional[bool] = None


class ModelFolderResponse(BaseModel):
    """One registered folder, with what the shelf holds in it."""

    model_config = ConfigDict(extra="allow")

    id: int
    path: str
    kind: str = Field(description="`user`, `managed`, `foreign` or `source`.")
    owner: Optional[str] = Field(
        default=None,
        description="Which subsystem owns the folder; null for a folder the user chose.",
    )
    movable: str = Field(
        description=(
            "`per_item` (files move one at a time), `root_only` (relocates as a "
            "whole), `external` (taken from, never written into) or `fixed` "
            "(cannot relocate at all; another tool owns where it lives). What "
            "the folder *is*; read `relocatable` for what can be done to it."
        )
    )
    relocatable: bool = Field(
        default=False,
        description=(
            "Whether `POST /model-folders/{id}/relocate` will move this folder "
            "whole. Reported rather than derived by the client, because "
            "`movable=root_only` does not settle it: it is also what the "
            "HuggingFace cache's neighbours say, and the two roots PixlStash "
            "records a location for are told apart from each other by path, not "
            "by any column. Offer Move on exactly the rows that carry this."
        ),
    )
    deletable: bool = Field(
        default=False,
        description=(
            "Whether `POST /model-files/delete` will unlink a file PixlStash "
            "does not claim from this folder: a folder you registered, the managed "
            "store, and the folder PixlStash downloads its engines into, whose "
            "leftovers are yours (#927). False for the HuggingFace cache, a "
            "symlink store shared with every other tool on the machine, and for "
            "the InsightFace packs. Told apart by path for the same reason "
            "`relocatable` is. An **engine** is refused wherever it lives, so "
            "this is the folder half of the answer and `file_kind` is the other."
        ),
    )
    host_path: Optional[str] = None
    delete_after_import: Optional[bool] = None
    last_checked: Optional[str] = Field(
        default=None,
        description="When the scanner last completed a pass. Null if never.",
    )
    created_at: Optional[str] = None
    file_count: int = Field(
        default=0,
        description=(
            "Copies registered under this folder, in any state. Counted in one "
            "grouped query for the whole list, never one per folder."
        ),
    )
    present_bytes: int = Field(
        default=0,
        description=(
            "Bytes registered under this folder whose copies are `present`, in "
            "the same grouped query the capacity meter uses. `missing` and "
            "`unreachable` copies are excluded: one names bytes that are no "
            "longer there and the other bytes we could not look at, and "
            "counting either would report space the drive does not agree is in "
            "use. Zero is therefore a real answer, not an unknown one."
        ),
    )
    scan_status: Optional[str] = Field(
        default=None,
        description=(
            "State of the most recent scan submitted for this folder since the "
            "server started: `pending`, `running`, `completed`, `failed` or "
            "`cancelled`. Null when this server has not been asked to scan the "
            "folder yet - which is not the same as never having scanned it, so "
            "read `last_checked` for that. Poll this rather than watching "
            "`last_checked` advance: a scan that threw never stamps "
            "`last_checked`, so a timestamp cannot tell a crash from a slow read."
        ),
    )
    scan_error: Optional[str] = Field(
        default=None,
        description="Why the most recent scan failed. Null unless `scan_status` is `failed`.",
    )


class ModelFolderListResponse(BaseModel):
    """Body of ``GET /model-folders``."""

    model_config = ConfigDict(extra="allow")

    folders: list[ModelFolderResponse]


class ModelFolderDeviceResponse(BaseModel):
    """One drive the registered folders sit on."""

    model_config = ConfigDict(extra="allow")

    device_id: Optional[str] = Field(
        default=None,
        description=(
            "Opaque key for the filesystem, grouping the folders that share a "
            "drive. Null when the drive could not be measured, and then the "
            "entry covers exactly one folder, because two folders we cannot "
            "stat cannot be shown to be the same drive."
        ),
    )
    mount_point: str = Field(
        description=(
            "Where the filesystem is mounted (`/`, `/mnt/models`, `D:\\`). "
            "Precise, and on Linux long enough to crowd a band header, so it "
            "is the drive band's tooltip rather than its label. Falls back to "
            "the folder's own path when the drive could not be measured."
        )
    )
    label: Optional[str] = Field(
        default=None,
        description=(
            "What the owner called the volume (`Models`, `WinStorage`), read "
            "from `/dev/disk/by-label` on Linux, `GetVolumeInformationW` on "
            "Windows and the `/Volumes` mount name on macOS. Null when the "
            "filesystem carries no label, which a root partition usually does "
            "not; the band then shows `mount_point` instead."
        ),
    )
    total_bytes: Optional[int] = Field(
        default=None, description="Size of the filesystem. Null if unmeasurable."
    )
    free_bytes: Optional[int] = Field(
        default=None,
        description=(
            "Room left on the filesystem, which is the number to read before "
            "moving a 24 GB checkpoint onto it. Null if unmeasurable."
        ),
    )
    kind: Optional[str] = Field(
        default=None,
        description=(
            "What kind of storage this is: `local`, `network`, `removable` or "
            "`ramdisk`. The CONNECTION, not the medium - it says whether the "
            "bytes are on another machine, on a stick, or in memory, which is "
            "what changes how fast a move runs and whether it survives a "
            "reboot. Null where the platform will not say (macOS always, and "
            "any filesystem type we do not recognise), and null is a normal "
            "answer: the drive band then draws its plain disk glyph rather "
            "than claiming the drive is unknown. Deliberately does NOT "
            "distinguish an SSD from a platter - the flags that would are "
            "wrong in a VM, behind LVM or LUKS, and in a USB enclosure, and a "
            "band that mislabels a slow disk as fast is worse than one that "
            "says nothing."
        ),
    )
    shelf_bytes: int = Field(
        default=0,
        description=(
            "Bytes of registered, `present` copies on this drive. A model held "
            "in two folders on one drive counts twice, because it occupies the "
            "drive twice."
        ),
    )
    folder_ids: list[int] = Field(
        default_factory=list,
        description="Registered folders on this drive, in id order.",
    )


class ModelFolderDeviceListResponse(BaseModel):
    """Body of ``GET /model-folders/devices``."""

    model_config = ConfigDict(extra="allow")

    devices: list[ModelFolderDeviceResponse]


class ModelFolderDeleteResponse(BaseModel):
    """Body of ``DELETE /model-folders/{folder_id}``."""

    model_config = ConfigDict(extra="allow")

    status: str
    id: int
    tombstoned_files: int = Field(
        description=(
            "How many ``model_file`` rows were dropped. The models themselves "
            "survive with their names, triggers and attachments, so re-adding "
            "the folder re-links them."
        )
    )


class ModelFolderRescanResponse(BaseModel):
    """Body of ``POST /model-folders/{folder_id}/rescan``."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(
        description="`started`, `already_running`, or `skipped` - for a `source` folder, "
        "which holds runs rather than models, and for a built-in folder, "
        "whose rows are declared rather than scanned."
    )
    id: int
    task_id: Optional[str] = Field(
        default=None,
        description=(
            "The task now queued on the task runner, or the one already running "
            "when `status` is `already_running`. Null for a skipped `source` "
            "folder. Its live progress is in `GET /workers/progress` under "
            "`workers.ModelFolderScanTask`; its outcome is the folder's "
            "`scan_status`."
        ),
    )


def _normalize_optional_host_path(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    host_path = str(value).strip()
    if not host_path:
        return None
    normalized = normalize_host_path(host_path)
    if not is_absolute_host_path(normalized):
        raise HTTPException(
            status_code=400, detail="Host path must be an absolute path."
        )
    return normalized


def create_router(server) -> APIRouter:
    """Create the model-folder router.

    Args:
        server: The Server instance, for ``hub`` (the folder rows) and ``auth``.

    Returns:
        The configured router.
    """
    router = APIRouter()

    def _fetch_folder(folder_id: int) -> dict:
        row = server.hub.fetchone(
            "SELECT id, path, kind, owner, movable, host_path, delete_after_import, "
            "last_checked, created_at FROM model_folder WHERE id = ?",
            (folder_id,),
        )
        if row is None:
            raise HTTPException(status_code=404, detail="Model folder not found.")
        return dict(row)

    def _validate_folder_conflicts(path: str) -> None:
        """Refuse a path that overlaps the vault or an already-registered folder.

        Containment, not just equality, for the same reason
        ``_validate_reference_folder_conflicts`` checks it: two roots over the
        same files register a ``model_file`` row per root, which double-counts
        every file in ``file_count`` and in a model's locations, and gives the
        scanner N walks of the same bytes. ``/`` is refused here rather than by a
        rule of its own, since it contains the vault by construction.

        Args:
            path: The resolved candidate path.

        Raises:
            HTTPException: 409 if the path overlaps something already registered.
        """
        image_root = getattr(server.vault, "image_root", "") or ""
        if path_is_within(path, image_root) or path_is_within(image_root, path):
            raise HTTPException(
                status_code=409,
                detail="Path overlaps the PixlStash data folder.",
            )
        for row in server.hub.fetchall("SELECT path FROM model_folder"):
            other = str(row["path"])
            if path_is_within(path, other):
                raise HTTPException(
                    status_code=409,
                    detail=f"Path is inside a registered model folder: {other}",
                )
            if path_is_within(other, path):
                raise HTTPException(
                    status_code=409,
                    detail=f"A registered model folder is inside this path: {other}",
                )

    def _file_counts() -> dict[int, int]:
        """Copies per folder, in one grouped query rather than one per folder."""
        rows = server.hub.fetchall(
            "SELECT model_folder_id, COUNT(*) AS n FROM model_file "
            "GROUP BY model_folder_id"
        )
        return {int(row["model_folder_id"]): int(row["n"]) for row in rows}

    def _to_response(
        row: dict,
        file_count: int = 0,
        present_bytes: int = 0,
        scan: Optional[tuple[Optional[str], Optional[str]]] = None,
    ) -> ModelFolderResponse:
        delete_after_import = row["delete_after_import"]
        scan_status, scan_error = (
            scan if scan is not None else _scan_state(int(row["id"]))
        )
        return ModelFolderResponse(
            id=int(row["id"]),
            path=row["path"],
            kind=row["kind"],
            owner=row["owner"],
            movable=row["movable"],
            relocatable=relocatable_identity(row) is not None,
            deletable=deletes_unclaimed_files(row["kind"], row["path"]),
            host_path=row["host_path"],
            delete_after_import=(
                None if delete_after_import is None else bool(delete_after_import)
            ),
            last_checked=row["last_checked"],
            created_at=row["created_at"],
            file_count=file_count,
            present_bytes=present_bytes,
            scan_status=scan_status,
            scan_error=scan_error,
        )

    @router.get(
        "/model-folders",
        summary="List registered model folders",
        description=(
            "Every folder the shelf catalogues or takes runs from, with how many "
            "copies are registered under each."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderListResponse,
    )
    def list_model_folders(request: Request):
        server.auth.ensure_secure_when_required(request)
        rows = server.hub.fetchall(
            "SELECT id, path, kind, owner, movable, host_path, delete_after_import, "
            "last_checked, created_at FROM model_folder ORDER BY id"
        )
        # Scan state before the aggregates, never after. A scan's task flips to
        # completed only once it has written its rows, so reading the counts
        # first lets a scan that finishes in between be reported as completed
        # with the count from before it wrote - an empty folder that just
        # succeeded. This way round the stale pairing is "still running" with a
        # full count, which the next poll corrects.
        scans = {int(row["id"]): _scan_state(int(row["id"])) for row in rows}
        counts = _file_counts()
        present_bytes = _present_bytes_by_folder()
        return ModelFolderListResponse(
            folders=[
                _to_response(
                    dict(row),
                    counts.get(int(row["id"]), 0),
                    present_bytes.get(int(row["id"]), 0),
                    scans[int(row["id"])],
                )
                for row in rows
            ]
        )

    def _present_bytes_by_folder() -> dict[int, int]:
        """Registered `present` bytes per folder, in one grouped query.

        ``present`` only. A ``missing`` row names bytes that are no longer
        there, and an ``unreachable`` one names bytes we could not look at;
        counting either into a capacity meter would report space that the drive
        does not agree is in use.
        """
        rows = server.hub.fetchall(
            "SELECT mf.model_folder_id AS folder_id, "
            "SUM(COALESCE(m.file_size, 0)) AS total "
            "FROM model_file mf JOIN model m ON m.id = mf.model_id "
            "WHERE mf.state = 'present' GROUP BY mf.model_folder_id"
        )
        return {int(row["folder_id"]): int(row["total"] or 0) for row in rows}

    @router.get(
        "/model-folders/devices",
        summary="Capacity of the drives the model folders sit on",
        description=(
            "One entry per drive, with the folders on it, how full it is and "
            "how much of that the shelf accounts for. Folders are grouped by "
            "the filesystem they sit on rather than by path, so two folders on "
            "one drive share one meter and a bind mount does not read as a "
            "second drive.\n\n"
            "Separate from `GET /model-folders` on purpose: this route stats "
            "the filesystem, so an offline network mount can make it slow, "
            "while the folder list answers from the database and stays fast. A "
            "drive that cannot be measured is still returned, with null "
            "capacity, so its folders keep a band to sit in."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderDeviceListResponse,
    )
    def list_model_folder_devices(request: Request):
        server.auth.ensure_secure_when_required(request)
        shelf_bytes = _present_bytes_by_folder()
        rows = server.hub.fetchall("SELECT id, path FROM model_folder ORDER BY id")
        # Key on the measured device, never on the path: a bind mount and a
        # symlinked folder look like different drives by path and are one, and
        # two subdirectories of one root can be different drives when a mount
        # sits between them. Same reasoning as `model_mover.same_device`.
        by_device: dict[str, ModelFolderDeviceResponse] = {}
        devices: list[ModelFolderDeviceResponse] = []
        for row in rows:
            folder_id = int(row["id"])
            path = str(row["path"])
            device = describe_storage_device(path)
            if device is None:
                devices.append(
                    ModelFolderDeviceResponse(
                        mount_point=path,
                        shelf_bytes=shelf_bytes.get(folder_id, 0),
                        folder_ids=[folder_id],
                    )
                )
                continue
            existing = by_device.get(device.device_id)
            if existing is None:
                existing = ModelFolderDeviceResponse(
                    device_id=device.device_id,
                    mount_point=device.mount_point,
                    label=device.label,
                    total_bytes=device.total_bytes,
                    free_bytes=device.free_bytes,
                    kind=device.kind,
                    shelf_bytes=0,
                    folder_ids=[],
                )
                by_device[device.device_id] = existing
                devices.append(existing)
            existing.folder_ids.append(folder_id)
            existing.shelf_bytes += shelf_bytes.get(folder_id, 0)
        return ModelFolderDeviceListResponse(devices=devices)

    @router.post(
        "/model-folders",
        summary="Register a model folder",
        description=(
            "Adds a folder for the shelf to catalogue (`kind=user`) or to take "
            "ai-toolkit runs from (`kind=source`). Registering does not scan; "
            "call the rescan route, which queues the walk as a background task."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderResponse,
    )
    def create_model_folder(
        request: Request,
        payload: ModelFolderCreateRequest = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        if payload.kind not in CREATABLE_KINDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"kind must be one of {list(CREATABLE_KINDS)}; managed and "
                    "foreign folders are registered by PixlStash itself."
                ),
            )
        path = os.path.normpath(payload.path)
        # Lexical first, because it is the only check that can still see a
        # relative path: realpath below would silently make one absolute against
        # the server's cwd.
        error = validate_reference_folder_path(path)
        if error:
            raise HTTPException(status_code=400, detail=error)
        # That check compares strings, so one symlink defeats it:
        # ``/home/u/models-link -> /etc`` passes, and the scan then walks /etc
        # because os.walk follows the top-level link (followlinks=False only
        # governs links found inside the tree). The example is spelled absolute
        # because the lexical check above has already refused anything else, and
        # nothing here expands ``~``. Resolve, re-run the blocklist on what the
        # link actually points
        # at, and store the resolved path so the row names the directory that
        # gets walked. This is the second half of the check
        # ``create_reference_folder`` runs and this route was missing.
        path = os.path.realpath(path)
        error = validate_reference_folder_accessible(path)
        if error:
            raise HTTPException(status_code=400, detail=error)
        host_path = _normalize_optional_host_path(payload.host_path)
        if server.running_in_docker() and host_path is None:
            raise HTTPException(
                status_code=400, detail="Host path is required in Docker mode."
            )

        movable, owner = _DERIVED_BY_KIND[payload.kind]
        now = datetime.now(timezone.utc).isoformat()
        existing = server.hub.fetchone(
            "SELECT id FROM model_folder WHERE path = ?", (path,)
        )
        if existing is not None:
            raise HTTPException(
                status_code=409, detail="This folder is already registered."
            )
        _validate_folder_conflicts(path)
        with server.hub.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO model_folder (path, kind, owner, movable, host_path, "
                "delete_after_import, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    path,
                    payload.kind,
                    owner,
                    movable,
                    host_path,
                    int(payload.delete_after_import),
                    now,
                ),
            )
            folder_id = int(cursor.lastrowid)
        logger.info("Model folder registered: %s (kind=%s)", path, payload.kind)
        return _to_response(_fetch_folder(folder_id))

    @router.patch(
        "/model-folders/{folder_id}",
        summary="Update a registered model folder",
        description=(
            "Changes the Docker bind source or the source-folder import "
            "behaviour. The path itself is not editable: moving a folder's "
            "contents is a copy-verify-repoint operation, not a field edit."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderResponse,
    )
    def update_model_folder(
        folder_id: int,
        request: Request,
        payload: ModelFolderUpdateRequest = Body(...),
    ):
        server.auth.ensure_secure_when_required(request)
        _fetch_folder(folder_id)

        assignments: list[str] = []
        params: list = []
        if "host_path" in payload.model_fields_set:
            assignments.append("host_path = ?")
            params.append(_normalize_optional_host_path(payload.host_path))
        if payload.delete_after_import is not None:
            assignments.append("delete_after_import = ?")
            params.append(int(payload.delete_after_import))
        if assignments:
            params.append(folder_id)
            with server.hub.transaction() as conn:
                conn.execute(
                    f"UPDATE model_folder SET {', '.join(assignments)} WHERE id = ?",
                    tuple(params),
                )
        return _to_response(_fetch_folder(folder_id))

    @router.delete(
        "/model-folders/{folder_id}",
        summary="Forget a registered model folder",
        description=(
            "Drops the folder and its location rows. The models themselves "
            "survive with their names, triggers, corrected kinds and "
            "attachments, so re-adding the folder re-links them by content. "
            "Nothing on disk is touched. The managed store cannot be forgotten: "
            "it is PixlStash's own storage rather than a folder the owner "
            "associated, so there is nothing to disassociate. Answers 409 while "
            "a move or an import is running: those write the very location rows "
            "this deletes."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderDeleteResponse,
    )
    def delete_model_folder(folder_id: int, request: Request):
        server.auth.ensure_secure_when_required(request)
        folder = _fetch_folder(folder_id)
        if folder["owner"] == BUILTIN_OWNER:
            # Same 409 reasoning as the managed store one line down: the caller
            # is authorized and the request is well formed, and what refuses it
            # is that this folder is PixlStash's own. Forgetting it would only
            # make the next start-up declare it again.
            raise HTTPException(
                status_code=409,
                detail=(
                    "This folder holds the engines PixlStash downloaded for "
                    "itself. It is registered so you can see what is on your "
                    "disk, and re-registers itself on the next start."
                ),
            )
        if folder["kind"] == MANAGED_KIND:
            # 409, not 403: the caller is fully authorized and the request is
            # well formed. What refuses it is the state of the target - this row
            # is PixlStash's own storage, and exactly one of it always exists.
            # A 403 would say "you may not", which is wrong and would send an
            # operator hunting through the authz tiers for a permission that
            # does not exist.
            raise HTTPException(
                status_code=409,
                detail=(
                    "The managed model store cannot be forgotten: it is where "
                    "PixlStash keeps models it was given, not a folder you "
                    "associated. Move it instead."
                ),
            )
        # The same machine-wide slot a move and an import take, and for the
        # reason at ``SHELF_IO_LOCK``'s own note: this deletes the very
        # ``model_file`` rows a running move is about to repoint. Forgetting a
        # source folder mid-move used to leave the move's UPDATE matching
        # nothing, the source unlinked anyway and the destination bytes
        # registered nowhere (#1017). The mover now refuses a repoint that does
        # not move exactly one row - that is the guarantee - and this is what
        # turns it into a clean 4xx before the batch starts rather than a file
        # failed halfway through forty.
        if not SHELF_IO_LOCK.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail=(
                    "A move or an import is running. Forgetting a folder now "
                    "would delete the location rows it is writing. Wait for it "
                    "to finish and try again."
                ),
            )
        try:
            with server.hub.transaction() as conn:
                cursor = conn.execute(
                    "DELETE FROM model_file WHERE model_folder_id = ?", (folder_id,)
                )
                tombstoned = int(cursor.rowcount or 0)
                conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))
            # Drop the remembered scan with the folder. SQLite reuses rowids, so
            # a folder registered later could otherwise inherit this one's
            # outcome.
            with _scans_lock:
                _scans.pop(folder_id, None)
        finally:
            SHELF_IO_LOCK.release()
        logger.info(
            "Model folder %s (id=%s) forgotten; %d location row(s) tombstoned, "
            "model rows and their curation kept.",
            folder["path"],
            folder_id,
            tombstoned,
        )
        return ModelFolderDeleteResponse(
            status="success", id=folder_id, tombstoned_files=tombstoned
        )

    @router.post(
        "/model-folders/{folder_id}/rescan",
        summary="Rescan a registered model folder",
        description=(
            "Walks the folder and reconciles the shelf with what is on disk. "
            "Returns immediately with the id of the task now queued on the task "
            "runner, because a folder of 1,800 adapters is minutes of reading. "
            "Watch it two ways: live file progress in `GET /workers/progress` "
            "under `workers.ModelFolderScanTask`, and the outcome in this "
            "folder's `scan_status` from `GET /model-folders`. A `source` folder "
            "is skipped: it is taken from, never catalogued in place."
        ),
        status_code=202,
        tags=["model_shelf"],
        response_model=ModelFolderRescanResponse,
    )
    def rescan_model_folder(folder_id: int, request: Request):
        server.auth.ensure_secure_when_required(request)
        folder = _fetch_folder(folder_id)
        if folder["kind"] == "source" or folder["owner"] == BUILTIN_OWNER:
            # A source folder holds runs to import, not models to catalogue. A
            # built-in folder is DECLARED, and pointing the scanner at it would
            # be actively wrong: it yields only `.safetensors` and sweeps what it
            # did not see to `missing`, so it would mark the ONNX tagger and both
            # `.pth` scorers missing on every pass.
            return ModelFolderRescanResponse(status="skipped", id=folder_id)

        with _scans_lock:
            running = _scans.get(folder_id)
            if running is not None and running.status in _SCAN_IN_FLIGHT:
                return ModelFolderRescanResponse(
                    status="already_running", id=folder_id, task_id=running.id
                )
            task = ModelFolderScanTask(
                server.hub, folder_id, folder["path"], folder["kind"]
            )
            # Claimed before submission, so the gate covers the queued window
            # too. A submission that fails releases it below.
            _scans[folder_id] = task

        try:
            task_id = server.vault.submit_task(task)
        except RuntimeError as exc:
            logger.error(
                "Could not queue a rescan of model folder %s (id=%s): the task "
                "runner refused the submission: %s",
                folder["path"],
                folder_id,
                exc,
            )
            task_id = None
        if task_id is None:
            with _scans_lock:
                if _scans.get(folder_id) is task:
                    del _scans[folder_id]
            raise HTTPException(
                status_code=503,
                detail="The task runner is not available, so the scan cannot be queued.",
            )
        logger.info(
            "Rescan of model folder %s (id=%s, kind=%s) queued as task %s.",
            folder["path"],
            folder_id,
            folder["kind"],
            task_id,
        )
        return ModelFolderRescanResponse(
            status="started", id=folder_id, task_id=task_id
        )

    return router
