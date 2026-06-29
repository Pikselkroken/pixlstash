import os
import sys
import subprocess
import time
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlmodel import Session

from PIL import Image

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture, User
from pixlstash.pixl_logging import get_logger
from pixlstash.services import config_service
from pixlstash.utils.atomic_write import write_json_atomic
from pixlstash.utils.service.user_settings_utils import (
    apply_user_config_patch,
    serialize_user_config,
)
from pixlstash.utils.watermark import get_default_watermark_bytes

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()
    hw_monitor = config_service.HardwareMonitor()

    def _ensure_secure_when_required(request: Request):
        server.auth.ensure_secure_when_required(request)

    def _open_in_os(path: str) -> bool:
        if not path or not os.path.exists(path):
            return False
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
                return True
            if sys.platform == "darwin":
                subprocess.run(["open", path], check=False)
                return True
            subprocess.run(["xdg-open", path], check=False)
            return True
        except Exception as exc:
            logger.warning("Failed to open path %s: %s", path, exc)
            return False

    class ChangePasswordRequest(BaseModel):
        current_password: Optional[str] = None
        new_password: str = Field(
            ..., min_length=8, description="Password must be at least 8 characters long"
        )

    class CreateTokenRequest(BaseModel):
        description: Optional[str] = None
        scope: str = "ALL"
        resource_type: Optional[str] = None
        resource_id: Optional[int] = None
        expires_at: Optional[datetime] = None
        include_attachments: bool = False
        watermark: bool = True

    # ── Response models ───────────────────────────────────────────────────────
    # All response models use extra="allow" so that no field is ever silently
    # dropped during serialization, while still documenting the known keys.

    class UserConfigResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        description: Optional[str] = None
        sort: Optional[str] = None
        sort_order: Optional[str] = None
        descending: Optional[bool] = None
        columns: Optional[int] = None
        sidebar_thumbnail_size: Optional[int] = None
        sidebar_width: Optional[int] = None
        show_stars: Optional[bool] = None
        show_face_bboxes: Optional[bool] = None
        show_hand_bboxes: Optional[bool] = None
        show_format: Optional[bool] = None
        show_resolution: Optional[bool] = None
        show_problem_icon: Optional[bool] = None
        compact_mode: Optional[bool] = None
        sidebar_docked: Optional[bool] = None
        sidebar_pinned: Optional[bool] = Field(
            default=None,
            description=(
                "When true the sidebar stays pinned open and takes layout "
                "space; when false it auto-hides and slides in on hover."
            ),
        )
        hide_purge_snapshot_warning: Optional[bool] = Field(
            default=None,
            description=(
                "When true, the post-purge warning about deleted pictures still "
                "present in snapshots is suppressed for this user."
            ),
        )
        expand_all_stacks: Optional[bool] = None
        date_format: Optional[str] = None
        theme_mode: Optional[str] = None
        comfyui_url: Optional[str] = None
        public_url: Optional[str] = None
        similarity_character: Optional[int] = None
        stack_strictness: Optional[float] = None
        apply_tag_filter: Optional[bool] = None
        keep_models_in_memory: Optional[bool] = None
        max_vram_gb: Optional[float] = None
        check_for_updates: Optional[bool] = None
        show_keyboard_hint: Optional[bool] = None
        embed_watermark: Optional[bool] = None
        smart_score_penalised_tags: Optional[dict] = None
        hidden_tags: Optional[list] = None
        tagger_settings: Optional[dict] = None

    class PenalisedTagsResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        smart_score_penalised_tags: Optional[dict] = None

    class PatchUserConfigResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str
        updated: bool
        config: UserConfigResponse

    class ChangePasswordResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str

    class MeAuthResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        username: Optional[str] = None
        has_password: bool

    class CreateTokenResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        token: str
        token_id: int
        scope: str
        resource_type: Optional[str] = None
        resource_id: Optional[int] = None
        expires_at: Optional[datetime] = None
        include_attachments: bool
        watermark: bool

    class TokenListItemResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        id: int
        description: Optional[str] = None
        scope: str
        resource_type: Optional[str] = None
        resource_id: Optional[int] = None
        resource_name: Optional[str] = None
        expires_at: Optional[datetime] = None
        created_at: Optional[datetime] = None
        last_used_at: Optional[datetime] = None
        include_attachments: bool
        watermark: bool

    class DeleteTokenResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str
        deleted_id: int

    class PatchTokenResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str
        id: int
        watermark: bool

    class WatermarkUploadResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str

    class SharedResourceIdsResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        resource_type: str
        ids: list[int]

    class BatchSharedPictureIdsResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        shared_ids: list[int]

    class RevokeTokensResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str
        deleted_count: int

    class SessionContextResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        is_owner: bool
        scope: str
        resource_type: Optional[str] = None
        resource_id: Optional[int] = None
        expires_at: Optional[datetime] = None
        include_attachments: Optional[bool] = None

    class WorkersProgressResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str
        workers: dict
        process: dict

    class WatchFoldersResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str
        watch_folders: list[str]

    class FilesystemRootsResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str
        filesystem_roots: list[str]

    class SnapshotConfigResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str
        daily_snapshots: bool

    class OpenServerConfigResponse(BaseModel):
        model_config = ConfigDict(extra="allow")

        status: str

    @router.get(
        "/users/me/config",
        summary="Get current user config",
        response_model=UserConfigResponse,
        description="Returns the authenticated user's UI and behavior configuration payload.",
    )
    def get_me_config(request: Request):
        _ensure_secure_when_required(request)
        user = server.auth.get_user_for_request(request)
        return serialize_user_config(user)

    @router.get(
        "/users/me/penalised-tags",
        summary="Get penalised tags",
        response_model=PenalisedTagsResponse,
        description="Returns the smart-score penalised tags for the authenticated user. Accessible to READ-scoped tokens.",
    )
    def get_me_penalised_tags(request: Request):
        _ensure_secure_when_required(request)
        user = server.auth.get_user_for_request(request)
        config = serialize_user_config(user)
        return {"smart_score_penalised_tags": config["smart_score_penalised_tags"]}

    @router.patch(
        "/users/me/config",
        summary="Update current user config",
        response_model=PatchUserConfigResponse,
        description="Applies a partial config patch for the authenticated user and returns updated settings.",
    )
    async def patch_me_config(request: Request):
        _ensure_secure_when_required(request)
        user_id = server.auth.require_unscoped_owner(request)

        start_time = time.time()
        logger.debug(f"[TIMING] PATCH /users/me/config called at {start_time:.3f}")
        patch_data = await request.json()

        def update_user(session: Session, user_id: int):
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")

            old_penalised_tags = user.smart_score_penalised_tags
            try:
                updated = apply_user_config_patch(user, patch_data)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            if updated:
                session.add(user)
                session.commit()
                session.refresh(user)
            penalised_tags_changed = (
                user.smart_score_penalised_tags != old_penalised_tags
            )
            return user, updated, penalised_tags_changed

        user, updated, penalised_tags_changed = server.vault.db.run_task(
            update_user, user_id, priority=DBPriority.IMMEDIATE
        )
        if penalised_tags_changed:

            def _reset_smart_scores(session: Session) -> None:
                session.exec(update(Picture).values(smart_score=None))
                session.commit()

            server.vault.db.run_task(_reset_smart_scores, priority=DBPriority.LOW)
            server.vault.wake()
        if "keep_models_in_memory" in patch_data:
            server.vault.set_keep_models_in_memory(
                getattr(user, "keep_models_in_memory", True)
            )
        if "max_vram_gb" in patch_data:
            server.vault.set_max_vram_usage_gb(getattr(user, "max_vram_gb", None))
        if "tagger_settings" in patch_data:
            import json as _json

            raw = getattr(user, "tagger_settings", None)
            if raw:
                try:
                    settings = _json.loads(raw)
                    server.vault.set_tagger_settings(settings)
                except (ValueError, TypeError) as exc:
                    logger.warning(
                        "Could not apply tagger_settings from patch: %s", exc
                    )
        elapsed = time.time() - start_time
        logger.debug(
            f"[TIMING] PATCH /users/me/config completed in {elapsed:.3f} seconds"
        )
        return {
            "status": "success",
            "updated": updated,
            "config": serialize_user_config(user),
        }

    @router.post(
        "/users/me/auth",
        summary="Change current user password",
        response_model=ChangePasswordResponse,
        description="Changes the authenticated user's password according to auth policy.",
    )
    def change_me_password(payload: ChangePasswordRequest, request: Request):
        result = server.auth.change_password(request, payload)
        server._user = server.auth.user
        return result

    @router.get(
        "/users/me/auth",
        summary="Get auth state",
        response_model=MeAuthResponse,
        description="Returns authentication and session-related information for the current request.",
    )
    def get_me_auth(request: Request):
        return server.auth.get_auth_info(request)

    @router.post(
        "/users/me/token",
        summary="Create API token",
        response_model=CreateTokenResponse,
        description="Creates a personal access token for the authenticated user.",
    )
    def create_me_token(payload: CreateTokenRequest, request: Request):
        return server.auth.create_token(
            request,
            payload.description,
            scope=payload.scope,
            resource_type=payload.resource_type,
            resource_id=payload.resource_id,
            expires_at=payload.expires_at,
            include_attachments=payload.include_attachments,
            watermark=payload.watermark,
        )

    @router.get(
        "/users/me/token",
        summary="List API tokens",
        response_model=list[TokenListItemResponse],
        description="Lists personal access tokens owned by the authenticated user.",
    )
    def list_me_tokens(request: Request):
        return server.auth.list_tokens(request)

    @router.delete(
        "/users/me/token/{token_id}",
        summary="Delete API token",
        response_model=DeleteTokenResponse,
        description="Deletes one personal access token by id for the authenticated user.",
    )
    def delete_me_token(token_id: int, request: Request):
        return server.auth.delete_token(request, token_id)

    class UpdateTokenRequest(BaseModel):
        watermark: bool

    @router.patch(
        "/users/me/token/{token_id}",
        summary="Update API token",
        response_model=PatchTokenResponse,
        description="Updates mutable fields on a personal access token (currently: watermark).",
    )
    def patch_me_token(token_id: int, payload: UpdateTokenRequest, request: Request):
        return server.auth.update_token(request, token_id, watermark=payload.watermark)

    # ── Watermark image endpoints ─────────────────────────────────────────────

    @router.get(
        "/users/me/watermark",
        summary="Get watermark image",
        response_class=FastAPIResponse,
        responses={200: {"content": {"image/png": {}}}},
        description="Returns the user's watermark as a PNG. Returns the default if no custom watermark is set.",
    )
    def get_me_watermark(request: Request):
        _ensure_secure_when_required(request)
        user = server.auth.get_user_for_request(request)
        img_bytes = getattr(user, "watermark_image", None) if user else None
        if not img_bytes:
            img_bytes = get_default_watermark_bytes()
        if not img_bytes:
            raise HTTPException(status_code=404, detail="No watermark available")
        return FastAPIResponse(content=img_bytes, media_type="image/png")

    @router.post(
        "/users/me/watermark",
        summary="Upload custom watermark",
        response_model=WatermarkUploadResponse,
        description="Uploads a PNG/JPEG/WebP image to use as the user's watermark.",
    )
    async def post_me_watermark(file: UploadFile, request: Request):
        _ensure_secure_when_required(request)
        user_id = server.auth.require_user_id(request)
        if file.content_type not in ("image/png", "image/jpeg", "image/webp"):
            raise HTTPException(
                status_code=400, detail="Only PNG, JPEG, or WebP images are accepted"
            )
        data = await file.read()
        if len(data) > 4 * 1024 * 1024:
            raise HTTPException(
                status_code=400, detail="Watermark image must be under 4 MB"
            )

        # Validate the image with Pillow and transcode to PNG.
        # This rejects spoofed content-type and ensures the GET endpoint
        # can always return a consistent media_type of image/png.
        try:
            from io import BytesIO

            img = Image.open(BytesIO(data))
            img.verify()  # raises on corrupt/invalid data
            # Re-open after verify() (verify() leaves the file in an unusable state)
            img = Image.open(BytesIO(data)).convert("RGBA")
            png_buf = BytesIO()
            img.save(png_buf, format="PNG")
            png_data = png_buf.getvalue()
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail=f"Invalid image data: {exc}"
            ) from exc

        def _save(session: Session, uid: int, img_data: bytes):
            user = session.get(User, uid)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            user.watermark_image = img_data
            session.add(user)
            session.commit()

        server.vault.db.run_task(
            _save, user_id, png_data, priority=DBPriority.IMMEDIATE
        )
        return {"status": "ok"}

    @router.delete(
        "/users/me/watermark",
        summary="Remove custom watermark",
        response_model=WatermarkUploadResponse,
        description="Removes the user's custom watermark; the default will be used for new shares.",
    )
    def delete_me_watermark(request: Request):
        _ensure_secure_when_required(request)
        user_id = server.auth.require_user_id(request)

        def _clear(session: Session, uid: int):
            user = session.get(User, uid)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")
            user.watermark_image = None
            session.add(user)
            session.commit()

        server.vault.db.run_task(_clear, user_id, priority=DBPriority.IMMEDIATE)
        return {"status": "ok"}

    @router.get(
        "/users/me/shared-resource-ids",
        summary="Get shared resource IDs",
        response_model=SharedResourceIdsResponse,
        description=(
            "Returns the IDs of resources of the given type that have at least one "
            "active READ share token. Accepts ?resource_type= (character, picture_set, project, picture)."
        ),
    )
    def get_shared_resource_ids(resource_type: str, request: Request):
        valid = {"character", "picture_set", "project", "picture"}
        if resource_type not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"resource_type must be one of: {', '.join(sorted(valid))}",
            )
        return server.auth.get_shared_resource_ids(request, resource_type)

    class BatchSharedPictureIdsRequest(BaseModel):
        picture_ids: list[int] = Field(default_factory=list)

    @router.post(
        "/users/me/shared-picture-ids/batch",
        summary="Batch check shared picture IDs",
        response_model=BatchSharedPictureIdsResponse,
        description="Given a list of picture IDs, returns which ones have active READ share tokens.",
    )
    def batch_shared_picture_ids(
        payload: BatchSharedPictureIdsRequest, request: Request
    ):
        return server.auth.batch_get_shared_picture_ids(request, payload.picture_ids)

    @router.delete(
        "/users/me/tokens/by-resource",
        summary="Revoke all tokens for a resource",
        response_model=RevokeTokensResponse,
        description="Deletes all READ tokens scoped to a specific resource (by type and id).",
    )
    def revoke_tokens_for_resource(
        resource_type: str, resource_id: int, request: Request
    ):
        return server.auth.revoke_tokens_for_resource(
            request, resource_type, resource_id
        )

    @router.get(
        "/session/context",
        summary="Get session access context",
        response_model=SessionContextResponse,
        description=(
            "Returns the access scope for the current session or token. "
            "Accepts ?token= query parameter so unauthenticated share-link "
            "recipients can discover what the token grants before loading the UI."
        ),
    )
    def get_session_context(request: Request):
        return server.auth.get_session_context(request)

    @router.get(
        "/workers/progress",
        summary="Get worker progress",
        response_model=WorkersProgressResponse,
        description="Returns background worker progress plus process CPU, RAM, and VRAM usage metrics.",
    )
    def get_workers_progress(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)
        return {
            "status": "success",
            "workers": server.vault.get_worker_progress(),
            "process": hw_monitor.get_usage(),
        }

    @router.get(
        "/server-config/watch-folders",
        summary="List watch folders",
        response_model=WatchFoldersResponse,
        description="Returns watch-folder paths from import-folder records in the database.",
    )
    def get_watch_folders(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_unscoped_owner(request)
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403,
                detail="Not available for token-authenticated requests.",
            )
        folders = config_service.get_import_folder_paths(server.vault)
        return {
            "status": "success",
            "watch_folders": folders,
        }

    @router.get(
        "/server-config/filesystem-roots",
        summary="List filesystem browser roots",
        response_model=FilesystemRootsResponse,
        description=(
            "Returns the configured filesystem browser root paths. "
            "When non-empty, the filesystem browser is restricted to these directories. "
            "An empty list means the browser is unrestricted."
        ),
    )
    def get_filesystem_roots(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_unscoped_owner(request)
        if getattr(request.state, "token_scope", None) is not None:
            raise HTTPException(
                status_code=403,
                detail="Not available for token-authenticated requests.",
            )
        roots = [
            r
            for r in (server._server_config.get("filesystem_roots") or [])
            if isinstance(r, str) and r
        ]
        return {"status": "success", "filesystem_roots": roots}

    @router.get(
        "/server-config/snapshots",
        summary="Get snapshot configuration",
        response_model=SnapshotConfigResponse,
        description="Returns server-level snapshot configuration.",
    )
    def get_snapshot_config(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_unscoped_owner(request)
        return {
            "status": "success",
            "daily_snapshots": server._server_config.get("daily_snapshots", True),
        }

    class SnapshotConfigPatch(BaseModel):
        daily_snapshots: bool

    @router.patch(
        "/server-config/snapshots",
        summary="Update snapshot configuration",
        response_model=SnapshotConfigResponse,
        description="Updates snapshot configuration. Changes take effect immediately and are persisted to server-config.json.",
    )
    def patch_snapshot_config(request: Request, body: SnapshotConfigPatch):
        _ensure_secure_when_required(request)
        server.auth.require_unscoped_owner(request)
        server._server_config["daily_snapshots"] = body.daily_snapshots
        server.vault.set_daily_snapshots_enabled(body.daily_snapshots)
        config_path = getattr(server, "_server_config_path", None)
        if config_path:
            write_json_atomic(config_path, server._server_config)
        return {"status": "success", "daily_snapshots": body.daily_snapshots}

    @router.post(
        "/server-config/open",
        summary="Open server config location",
        response_model=OpenServerConfigResponse,
        description="Opens the server config path in the operating system file browser.",
    )
    def open_server_config(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_unscoped_owner(request)
        config_path = getattr(server, "_server_config_path", None)
        opened = _open_in_os(config_path)
        return {"status": "success" if opened else "failed"}

    return router
