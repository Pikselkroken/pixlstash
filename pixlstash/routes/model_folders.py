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

**Exactly one ``managed`` folder always exists and cannot be forgotten.** It
is PixlStash's own model storage — created on first run, the default destination
for a drop or an import — so there is no association to dissolve and ``DELETE``
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

Authorization: the read is ``OWNER_ONLY``; every mutator and the rescan are
``LOCAL_OWNER_ONLY`` with a §16.3 justification, because they take — or walk — a
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
from pixlstash.services.managed_model_store import MANAGED_KIND
from pixlstash.services.model_folder_scanner import ModelFolderScanner
from pixlstash.utils.host_path_utils import is_absolute_host_path, normalize_host_path
from pixlstash.utils.path_utils import path_is_within
from pixlstash.utils.reference_folder_validator import (
    validate_reference_folder_accessible,
    validate_reference_folder_path,
)

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

# Folder ids with a scan in flight. The scanner is correct under concurrent runs
# (its missing sweep is ``seen_at <`` the run's own stamp, not ``!=``), so this
# is not a correctness lock — it stops a double-click from reading 438 GB twice.
_scanning: set[int] = set()
_scanning_lock = threading.Lock()


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
    *is* — neither is a field edit.
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
    movable: str = Field(description="`per_item`, `root_only` or `external`.")
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


class ModelFolderListResponse(BaseModel):
    """Body of ``GET /model-folders``."""

    model_config = ConfigDict(extra="allow")

    folders: list[ModelFolderResponse]


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
        description="`started`, `already_running`, or `skipped` for a source folder."
    )
    id: int


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

    def _to_response(row: dict, file_count: int = 0) -> ModelFolderResponse:
        delete_after_import = row["delete_after_import"]
        return ModelFolderResponse(
            id=int(row["id"]),
            path=row["path"],
            kind=row["kind"],
            owner=row["owner"],
            movable=row["movable"],
            host_path=row["host_path"],
            delete_after_import=(
                None if delete_after_import is None else bool(delete_after_import)
            ),
            last_checked=row["last_checked"],
            created_at=row["created_at"],
            file_count=file_count,
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
        counts = _file_counts()
        rows = server.hub.fetchall(
            "SELECT id, path, kind, owner, movable, host_path, delete_after_import, "
            "last_checked, created_at FROM model_folder ORDER BY id"
        )
        return ModelFolderListResponse(
            folders=[
                _to_response(dict(row), counts.get(int(row["id"]), 0)) for row in rows
            ]
        )

    @router.post(
        "/model-folders",
        summary="Register a model folder",
        description=(
            "Adds a folder for the shelf to catalogue (`kind=user`) or to take "
            "ai-toolkit runs from (`kind=source`). Registering does not scan; "
            "call the rescan route, which reports progress to the server log."
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
        # That check compares strings, so one symlink defeats it: ``~/models ->
        # /etc`` passes, and the scan then walks /etc because os.walk follows the
        # top-level link (followlinks=False only governs links found inside the
        # tree). Resolve, re-run the blocklist on what the link actually points
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
            "associated, so there is nothing to disassociate."
        ),
        tags=["model_shelf"],
        response_model=ModelFolderDeleteResponse,
    )
    def delete_model_folder(folder_id: int, request: Request):
        server.auth.ensure_secure_when_required(request)
        folder = _fetch_folder(folder_id)
        if folder["kind"] == MANAGED_KIND:
            # 409, not 403: the caller is fully authorized and the request is
            # well formed. What refuses it is the state of the target — this row
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
        with server.hub.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM model_file WHERE model_folder_id = ?", (folder_id,)
            )
            tombstoned = int(cursor.rowcount or 0)
            conn.execute("DELETE FROM model_folder WHERE id = ?", (folder_id,))
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
            "Returns immediately; progress goes to the server log, because a "
            "folder of 1,800 adapters is minutes of reading. A `source` folder "
            "is skipped: it is taken from, never catalogued in place."
        ),
        status_code=202,
        tags=["model_shelf"],
        response_model=ModelFolderRescanResponse,
    )
    def rescan_model_folder(folder_id: int, request: Request):
        server.auth.ensure_secure_when_required(request)
        folder = _fetch_folder(folder_id)
        if folder["kind"] == "source":
            return ModelFolderRescanResponse(status="skipped", id=folder_id)

        with _scanning_lock:
            if folder_id in _scanning:
                return ModelFolderRescanResponse(status="already_running", id=folder_id)
            _scanning.add(folder_id)

        def _run():
            try:
                ModelFolderScanner(server.hub).scan_folder(
                    folder_id, folder["path"], folder["kind"]
                )
            except Exception as exc:
                logger.error(
                    "Rescan of model folder %s (id=%s, kind=%s) failed: %s",
                    folder["path"],
                    folder_id,
                    folder["kind"],
                    exc,
                    exc_info=True,
                )
            finally:
                with _scanning_lock:
                    _scanning.discard(folder_id)

        threading.Thread(
            target=_run, daemon=True, name=f"model-folder-rescan-{folder_id}"
        ).start()
        return ModelFolderRescanResponse(status="started", id=folder_id)

    return router
