import os
import sys
import subprocess
import time
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from PIL import Image

from pixlstash.database import DBPriority
from pixlstash.db_models import User
from pixlstash.db_models.tag import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services import (
    config_service,
    library_settings_service,
    scrapheap_service,
    views_service,
)
from pixlstash.telemetry import mark_install_established
from pixlstash.server_config_io import persist_server_config
from pixlstash.utils.quality.smart_score_utils import smart_score_penalised_tags
from pixlstash.utils.service.smart_score_invalidation import (
    changed_penalised_tags,
    invalidate_all_anomaly_scores,
    apply_pending_invalidations,
    record_pending_invalidation,
)
from pixlstash.utils.service.user_settings_utils import (
    apply_user_config_patch,
    serialize_user_config,
)
from pixlstash.utils.watermark import get_default_watermark_bytes

logger = get_logger(__name__)


def _resolved_penalised_tags(raw) -> dict:
    """Resolve a stored ``smart_score_penalised_tags`` value to ``{tag: weight}``.

    Uses the same parser the scorer does, so the config-change diff is taken over the
    weights that actually reach the penalty rather than over the raw JSON text.
    """
    return smart_score_penalised_tags(
        raw,
        DEFAULT_SMART_SCORE_PENALIZED_TAGS,
        default_weight=DEFAULT_SMART_SCORE_PENALIZED_TAG_WEIGHT,
    )


def create_router(server) -> APIRouter:
    router = APIRouter()
    hw_monitor = config_service.HardwareMonitor()

    def _config_payload(user) -> dict:
        """Serialise the user's config, with the library-owned settings merged in.

        The client sees one flat config object exactly as before. Behind it,
        ``similarity_character`` comes from the *active library* rather than from
        the user row: it is a character id in that vault, so the same number in
        another library is a different person (see
        pixlstash/db_models/library_settings.py).
        """
        payload = serialize_user_config(user)
        payload["similarity_character"] = (
            library_settings_service.get_similarity_character(server.vault.db)
        )
        return payload

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
        thumbnail_size_level: Optional[int] = Field(
            default=None,
            description=(
                "Unified grid thumbnail size index (0..6, larger index means "
                "fewer/larger thumbnails). Default 3 = Medium."
            ),
        )
        sidebar_thumbnail_size: Optional[int] = None
        sidebar_width: Optional[int] = None
        thumbnail_mode: Optional[str] = Field(
            default=None,
            description=(
                "Grid thumbnail shape preference: 'square' renders a square cell "
                "cropped to the stored face-weighted rectangle; 'justified' lays "
                "out the full aspect-ratio-preserving thumbnail. This is a "
                "DISPLAY-ONLY preference - the frontend applies it instantly from "
                "the single stored bitmap; the backend never regenerates "
                "thumbnails when it changes."
            ),
        )
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
        telemetry_send_install_id: Optional[bool] = Field(
            default=None,
            description=(
                "When true, the anonymous install ID is sent with update "
                "checks, which is what makes day-7/day-30 cohort retention "
                "computable. Off by default on every install."
            ),
        )
        telemetry_send_feature_usage: Optional[bool] = Field(
            default=None,
            description=(
                "When true, which features were used and whether they "
                "succeeded is sent. Never includes search queries themselves, "
                "only their shape. Off by default on every install."
            ),
        )
        telemetry_send_error_reports: Optional[bool] = Field(
            default=None,
            description=(
                "When true, error and crash reports are sent. Off by default "
                "on every install."
            ),
        )
        telemetry_send_hardware_profile: Optional[bool] = Field(
            default=None,
            description=(
                "When true, a coarse environment profile is sent: OS, GPU "
                "vendor, RAM bucket, install type, library-size bucket. Never "
                "file paths or the library location. Off by default on every "
                "install."
            ),
        )
        telemetry_consent_prompted: Optional[bool] = Field(
            default=None,
            description=(
                "True once the telemetry question has been put to the user. "
                "Set by the consent dialog so the question is asked exactly "
                "once and never re-raised. Declining is a recorded decision, "
                "not an unanswered prompt."
            ),
        )
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

    class ScrapheapRetentionConfigResponse(BaseModel):
        """The scrapheap auto-purge retention window currently in effect."""

        model_config = ConfigDict(
            extra="allow",
            json_schema_extra={
                "example": {
                    "status": "success",
                    "scrapheap_retention_days": 30,
                    "scrapheap_retention_reduced_at": "2026-07-22T09:15:00+00:00",
                    "scrapheap_retention_choices": [30, 60, 90, 120],
                    "scrapheap_retention_grace_days": 1,
                }
            },
        )

        status: str
        scrapheap_retention_days: Optional[int] = Field(
            default=None,
            description=(
                "Days an UNPROTECTED (managed) picture stays in the scrapheap "
                "before it is permanently purged. `null` means Never - "
                "auto-purge is disabled entirely, and that is the **default**: "
                "a server on which nobody has saved a window reports `null` and "
                "never deletes anything on a timer. Protected reference-folder "
                "originals (allow_delete_file=false) are exempt from this "
                "timer at any value and are only ever destroyed by the manual, "
                "consent-gated delete-forever."
            ),
            examples=[30],
        )
        scrapheap_retention_reduced_at: Optional[str] = Field(
            default=None,
            description=(
                "ISO 8601 UTC instant at which the window was last LOWERED, or "
                "null if it never was. A picture soft-deleted before this "
                "instant gets `scrapheap_retention_grace_days` extra day(s), so "
                "shortening the window is never retroactively instantaneous. "
                "Turning auto-purge ON (Never -> a finite window) counts as a "
                "lowering and is stamped too. Untouched when the window is "
                "raised, switched to Never, or re-saved unchanged."
            ),
            examples=["2026-07-22T09:15:00+00:00"],
        )
        scrapheap_retention_choices: list[int] = Field(
            default=[],
            description=(
                "The day values this server accepts, in ascending order. Any "
                "other integer is rejected with 422; `null` (Never) is always "
                "accepted and is not listed here."
            ),
            examples=[[30, 60, 90, 120]],
        )
        scrapheap_retention_grace_days: int = Field(
            default=0,
            description=(
                "Extra days granted to pictures that were already in the "
                "scrapheap when the window was last lowered."
            ),
            examples=[1],
        )

    class ScrapheapRetentionImpactResponse(BaseModel):
        """What lowering the retention window to a candidate value would destroy."""

        model_config = ConfigDict(
            extra="allow",
            json_schema_extra={
                "example": {
                    "would_purge_count": 412,
                    "first_purge_at": "2026-07-23T09:15:00+00:00",
                }
            },
        )

        would_purge_count: int = Field(
            description=(
                "How many scrapheap pictures the auto-purge would permanently "
                "destroy under the candidate window, counted at the moment the "
                "change would first bite (see `first_purge_at`) so it can never "
                "understate the consequence. EXCLUDES protected reference-folder "
                "originals and locked-set members, which the sweep never "
                "destroys. `0` when the candidate window is not a REDUCTION "
                "(raising it, setting it for the first time, or re-saving the "
                "same value destroys nothing new) - show no confirmation then."
            ),
            examples=[412],
        )
        first_purge_at: Optional[str] = Field(
            default=None,
            description=(
                "ISO 8601 UTC instant at which those deletions would begin, i.e. "
                "when the reduction's grace floor elapses if the change were "
                "applied now. Nothing is destroyed before this. `null` when "
                "`would_purge_count` is 0."
            ),
            examples=["2026-07-23T09:15:00+00:00"],
        )

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
        return _config_payload(user)

    @router.get(
        "/users/me/penalised-tags",
        summary="Get penalised tags",
        response_model=PenalisedTagsResponse,
        description="Returns the smart-score penalised tags for the authenticated user. Accessible to READ-scoped tokens.",
    )
    def get_me_penalised_tags(request: Request):
        _ensure_secure_when_required(request)
        user = server.auth.get_user_for_request(request)
        config = _config_payload(user)
        return {"smart_score_penalised_tags": config["smart_score_penalised_tags"]}

    @router.patch(
        "/users/me/config",
        summary="Update current user config",
        response_model=PatchUserConfigResponse,
        description="Applies a partial config patch for the authenticated user and returns updated settings.",
    )
    async def patch_me_config(request: Request):
        _ensure_secure_when_required(request)
        # The authz gate enforces OWNER_ONLY on this route before the handler runs;
        # require_user_id here only fetches the (owner) user id the update needs.
        user_id = server.auth.require_user_id(request)

        start_time = time.time()
        logger.debug(f"[TIMING] PATCH /users/me/config called at {start_time:.3f}")
        patch_data = await request.json()

        def preview_patch(session: Session, user_id: int):
            """Validate the patch and work out which tag weights would change.

            Runs against a detached copy and commits nothing, so the diff is
            known *before* anything is written. That ordering is what lets the
            invalidation be recorded durably first; see the comment on the write
            below.
            """
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")

            old_penalised_tags = _resolved_penalised_tags(
                user.smart_score_penalised_tags
            )
            probe = User(**user.model_dump())
            try:
                would_update = apply_user_config_patch(probe, patch_data)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if not would_update:
                return set()
            # Diff the *resolved* weight tables, not the raw JSON strings: a
            # reordered or reformatted payload with identical weights must not
            # trigger a re-score.
            return changed_penalised_tags(
                old_penalised_tags,
                _resolved_penalised_tags(probe.smart_score_penalised_tags),
            )

        def update_user(session: Session, user_id: int):
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")

            consent_was_prompted = bool(
                getattr(user, "telemetry_consent_prompted", False)
            )
            install_id_was_enabled = bool(
                getattr(user, "telemetry_send_install_id", False)
            )
            try:
                updated = apply_user_config_patch(user, patch_data)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            consent_is_prompted = bool(
                getattr(user, "telemetry_consent_prompted", False)
            )
            install_id_is_enabled = bool(
                getattr(user, "telemetry_send_install_id", False)
            )
            # A fresh identity is a valid new-install cohort member only when
            # telemetry is accepted as part of the first consent decision. If
            # that decision declines it, a later opt-in's first ping describes
            # an established install. The second condition retries the
            # demotion on that later opt-in if the original file write failed.
            exclude_from_new_cohort = (
                not consent_was_prompted
                and consent_is_prompted
                and not install_id_is_enabled
            ) or (
                consent_was_prompted
                and not install_id_was_enabled
                and install_id_is_enabled
            )
            if exclude_from_new_cohort:
                # Do this before commit. Once the opt-in is visible to the
                # periodic sender, the on-disk identity must already carry the
                # established classification.
                mark_install_established(server.server_config_path)
            if updated:
                session.add(user)
                session.commit()
                session.refresh(user)
            return user, updated

        changed_tags = server.hub_engine.run_immediate_read_task(preview_patch, user_id)

        # ``similarity_character`` belongs to the active library, not to the
        # user: it is a row id in that vault's character table, so the same
        # number in another library is a different person (see
        # pixlstash/db_models/library_settings.py). Split it out before the hub
        # write so the hub never becomes a second, stale home for it. The config
        # API stays one flat object; only the storage is split.
        if "similarity_character" in patch_data:
            raw_character = patch_data.pop("similarity_character")
            if raw_character in ("", None, "null"):
                character_id = None
            elif isinstance(raw_character, str) and raw_character.isdigit():
                character_id = int(raw_character)
            else:
                character_id = raw_character
            library_settings_service.set_similarity_character(
                server.vault.db, character_id
            )

        # Record the invalidation in the vault BEFORE committing the setting to
        # the hub. The two live in different databases and SQLite has no
        # transaction spanning both, so one of them has to go first, and this
        # order is the one that fails safe. Crash after the record and before the
        # setting: the record is applied against unchanged weights and the scores
        # recompute to the values they already had, costing work and nothing
        # else. The other order would leave the setting saved with no record that
        # a recompute is owed, and a stale smart score is a plausible number, so
        # nothing would ever notice.
        if changed_tags:
            server.vault.db.run_task(
                lambda session: (
                    record_pending_invalidation(session, changed_tags),
                    session.commit(),
                ),
                priority=DBPriority.IMMEDIATE,
            )

        # The hub owns the user row. The five library-scoped fields (§5) still
        # ride along here rather than in the vault's library_settings row; see
        # the note in pixlstash/hub/schema.py.
        user, updated = server.hub_engine.run_task(
            update_user, user_id, priority=DBPriority.IMMEDIATE
        )
        # Keep the process-local owner cache coherent with its new hub home.
        # Background work (notably smart scoring) has no request from which to
        # reload these settings.
        server.auth.user = user
        server._user = user

        if changed_tags:
            # Apply the recorded invalidation now, so the user sees the effect
            # immediately. Consuming the record and NULLing the scores share one
            # vault transaction, so that half cannot tear.
            #
            # A failure here is not the end of the story: the record is already
            # durable, and PendingScoreInvalidationFinder drains it on its next
            # sweep. Logged rather than raised for exactly that reason, because
            # the setting is saved and the repair is already scheduled.
            try:
                server.vault.db.run_task(
                    apply_pending_invalidations, priority=DBPriority.IMMEDIATE
                )
            except Exception:
                logger.exception(
                    "Penalised tags changed (%s) and the invalidation is "
                    "recorded, but applying it now failed. Scores for pictures "
                    "carrying those tags stay stale until the pending-"
                    "invalidation finder drains the record.",
                    ", ".join(sorted(changed_tags)),
                )

        if changed_tags:
            # The NULL-reset already committed atomically above; just nudge the scheduler
            # so MissingSmartScoreFinder promptly re-scores the cleared rows. wake() is a
            # scheduler poke, not a DB write, so it need not be inside the transaction.
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
                    # The threshold offset moves both the anomaly apply gate and the
                    # penalty's u = (p - t)/(1 - t) normalisation, so every cached score
                    # with an anomaly component goes stale when it changes. Capture the
                    # resolved offset either side of the apply and invalidate only on a
                    # real move (an identical save must not re-score).
                    old_offset = server.vault.get_pixlstash_tagger_threshold_offset()
                    server.vault.set_tagger_settings(settings)
                    new_offset = server.vault.get_pixlstash_tagger_threshold_offset()
                    if new_offset != old_offset:

                        def _reset_anomaly_scores(session: Session) -> None:
                            invalidate_all_anomaly_scores(
                                session, context="tagger threshold offset config"
                            )
                            session.commit()

                        server.vault.db.run_task(
                            _reset_anomaly_scores, priority=DBPriority.LOW
                        )
                        server.vault.wake()
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
            "config": _config_payload(user),
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

        server.hub_engine.run_task(
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

        server.hub_engine.run_task(_clear, user_id, priority=DBPriority.IMMEDIATE)
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
        server._server_config["daily_snapshots"] = body.daily_snapshots
        server.vault.set_daily_snapshots_enabled(body.daily_snapshots)
        config_path = getattr(server, "_server_config_path", None)
        if config_path:
            persist_server_config(config_path, server._server_config)
        return {"status": "success", "daily_snapshots": body.daily_snapshots}

    def _scrapheap_retention_payload() -> dict:
        """Build the scrapheap-retention response from server-config."""
        reduced_at = scrapheap_service.read_retention_reduced_at(server._server_config)
        return {
            "status": "success",
            "scrapheap_retention_days": scrapheap_service.read_retention_days(
                server._server_config
            ),
            "scrapheap_retention_reduced_at": (
                reduced_at.isoformat() if reduced_at else None
            ),
            "scrapheap_retention_choices": list(
                scrapheap_service.RETENTION_DAY_CHOICES
            ),
            "scrapheap_retention_grace_days": scrapheap_service.REDUCTION_GRACE_DAYS,
        }

    @router.get(
        "/server-config/scrapheap-retention",
        summary="Get scrapheap retention configuration",
        response_model=ScrapheapRetentionConfigResponse,
        description=(
            "Returns the scrapheap auto-purge retention window. An UNPROTECTED "
            "(managed) picture left in the scrapheap longer than "
            "`scrapheap_retention_days` is permanently deleted by a background "
            "task; `null` means Never (auto-purge disabled), which is the "
            "default until someone saves a window. Protected reference-folder "
            "originals are exempt from the timer entirely."
        ),
    )
    def get_scrapheap_retention_config(request: Request):
        _ensure_secure_when_required(request)
        return _scrapheap_retention_payload()

    class ScrapheapRetentionConfigPatch(BaseModel):
        model_config = ConfigDict(
            json_schema_extra={"example": {"scrapheap_retention_days": 60}}
        )

        scrapheap_retention_days: Optional[int] = Field(
            default=None,
            description=(
                "New retention window in days - one of 30, 60, 90, 120 - or "
                "`null` for Never (disables auto-purge). Any other value is a "
                "422. Saving NEVER purges anything: the change takes effect on "
                "the next scheduled sweep."
            ),
            examples=[60],
        )

    @router.patch(
        "/server-config/scrapheap-retention",
        summary="Update scrapheap retention configuration",
        response_model=ScrapheapRetentionConfigResponse,
        description=(
            "Sets the scrapheap auto-purge window (one of 30/60/90/120 days, or "
            "null for Never) and persists it to server-config.json. **This is "
            "the only thing that ever enables the auto-purge**: until it is "
            "called with a finite window the server stays on the Never default "
            "and the timer deletes nothing. Saving does NOT purge anything "
            "synchronously - the background retention task is the only thing "
            "that ever deletes, and it never touches protected reference-folder "
            "originals. Lowering the window, INCLUDING turning it on "
            "(Never -> 30), stamps `scrapheap_retention_reduced_at`, which puts "
            "a floor of one grace day under EVERY picture's deadline - so even "
            "a 400-day-old scrapheap item survives at least a day after the "
            "change. Raising it, switching to Never, or saving the same value "
            "leaves that stamp untouched."
        ),
        responses={
            422: {
                "description": "scrapheap_retention_days is not 30/60/90/120 or null."
            }
        },
    )
    def patch_scrapheap_retention_config(
        request: Request, body: ScrapheapRetentionConfigPatch
    ):
        _ensure_secure_when_required(request)
        new_days = body.scrapheap_retention_days
        if (
            new_days is not None
            and int(new_days) not in scrapheap_service.RETENTION_DAY_CHOICES
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "scrapheap_retention_days must be one of "
                    f"{list(scrapheap_service.RETENTION_DAY_CHOICES)} or null "
                    "(Never)."
                ),
            )
        # Config write only. No purge is triggered here, by design: an automatic
        # file-destruction path must never fire inside a settings save.
        effective_days, reduced_at = scrapheap_service.apply_retention_config(
            server._server_config, new_days
        )
        server.vault.set_scrapheap_retention(effective_days, reduced_at)
        config_path = getattr(server, "_server_config_path", None)
        if config_path:
            persist_server_config(config_path, server._server_config)
        return _scrapheap_retention_payload()

    @router.get(
        "/server-config/scrapheap-retention/impact",
        summary="Preview the impact of a scrapheap retention change",
        response_model=ScrapheapRetentionImpactResponse,
        description=(
            "Reports what LOWERING the scrapheap retention window to `days` "
            "would permanently destroy, so the UI can confirm before applying a "
            "reduction instead of silently wiping a long-lived scrapheap on a "
            "dropdown change.\n\n"
            "**Pure read.** It applies nothing, stamps no "
            "`scrapheap_retention_reduced_at`, and schedules no purge; call "
            "`PATCH /server-config/scrapheap-retention` to actually apply the "
            "value.\n\n"
            "`would_purge_count` is computed with the same helpers the sweep "
            "itself uses, so it cannot drift from reality, and excludes the "
            "pictures the sweep never touches (protected reference-folder "
            "originals and locked-set members). It is evaluated at "
            "`first_purge_at` - the instant the reduction's grace floor "
            "elapses - rather than at now, so pictures that expire during the "
            "grace day are counted rather than omitted.\n\n"
            "Returns `{would_purge_count: 0, first_purge_at: null}` when `days` "
            "is not lower than the current window."
        ),
        responses={
            422: {"description": "days is not one of 30/60/90/120."},
        },
    )
    def get_scrapheap_retention_impact(
        request: Request,
        days: int = Query(
            ...,
            description=(
                "Candidate retention window in days - one of 30, 60, 90, 120. "
                "Any other value is a 422. ('Never' is never a reduction, so "
                "there is nothing to preview for it.)"
            ),
            examples=[30],
        ),
    ):
        _ensure_secure_when_required(request)
        if int(days) not in scrapheap_service.RETENTION_DAY_CHOICES:
            raise HTTPException(
                status_code=422,
                detail=(
                    "days must be one of "
                    f"{list(scrapheap_service.RETENTION_DAY_CHOICES)}."
                ),
            )
        return scrapheap_service.retention_impact(
            server.vault,
            datetime.now(timezone.utc),
            int(days),
            scrapheap_service.read_retention_days(server._server_config),
        )

    class ViewsConfigPatch(BaseModel):
        model_config = ConfigDict(
            json_schema_extra={
                "example": {
                    "views_root": "/home/me/Pictures/_PixlStash Views",
                    "kinds": ["people", "sets"],
                }
            }
        )

        views_root: Optional[str] = Field(
            default=None,
            description=(
                "Absolute host path the views tree is published to, or `null` "
                "to turn views off and remove the tree PixlStash published."
            ),
        )
        kinds: list[str] = Field(
            default_factory=list,
            description=(
                "Which kinds to publish: any subset of `people`, `sets`, "
                "`projects`. An empty list publishes an empty tree."
            ),
        )

    def _views_payload(report: Optional[views_service.PublishReport] = None) -> dict:
        root, kinds = library_settings_service.get_views_config(server.vault.db)
        payload = {
            "status": "success",
            "views_root": root,
            "kinds": kinds,
            "available_kinds": list(views_service.KIND_FOLDERS),
        }
        if report is not None:
            payload["last_publish"] = report.as_dict()
        return payload

    @router.get(
        "/server-config/views",
        summary="Get the PixlStash Views configuration",
        description=(
            "Returns where this library publishes its Views tree - sets, people "
            "and projects as folders of LINKS to the real files - and which "
            "kinds it publishes. `views_root` is `null` when views are off, "
            "which is the default: nothing is written until a folder is named."
        ),
    )
    def get_views_config(request: Request):
        _ensure_secure_when_required(request)
        return _views_payload()

    @router.patch(
        "/server-config/views",
        summary="Set the PixlStash Views configuration and rebuild the tree",
        description=(
            "Records the views folder and the published kinds, then rebuilds "
            "the tree. Sending the current values is how the UI's *Rebuild now* "
            "works: the rebuild is a full re-derive and costs a fraction of a "
            "second even for a large library, so there is no incremental path "
            "and no separate verb.\n\n"
            "**Nothing is copied and no original moves.** Each file in the tree "
            "is a link; deleting the whole folder loses no picture. Views are "
            "*additional* to the folders the owner already keeps.\n\n"
            "A folder that cannot hold the tree is refused with 400 and the "
            "reason, rather than half-written: inside the library (backups "
            "refuse a symlinked payload), inside a reference folder (the scan "
            "would index every link as a second copy), in a cloud-sync folder "
            "(the client uploads the file the link points at), or on a "
            "filesystem with no links at all - exFAT and FAT have neither, and "
            "on Windows symbolic links need administrator rights or Developer "
            "Mode. Sending `views_root: null` removes the published tree and "
            "leaves the folder itself alone."
        ),
        responses={
            400: {"description": "The views folder cannot hold the tree."},
            403: {
                "description": (
                    "The views folder is outside every configured "
                    "`filesystem_roots` entry."
                )
            },
        },
    )
    def patch_views_config(request: Request, body: ViewsConfigPatch):
        _ensure_secure_when_required(request)
        kinds = [kind for kind in views_service.KIND_FOLDERS if kind in set(body.kinds)]
        previous_root, _ = library_settings_service.get_views_config(server.vault.db)

        # `null` turns views off; ANY other value goes through the location
        # checks. An empty or whitespace string is not "off" - treating it as
        # off made a malformed body remove the published tree without a single
        # check having run, which is the one shape of this route that can
        # destroy a tree by accident.
        if body.views_root is None:
            if previous_root:
                views_service.remove(previous_root)
            library_settings_service.set_views_config(server.vault.db, None, [])
            return _views_payload()

        # An operator who confined the folder picker to a set of roots did not
        # mean "except for the route that creates a tree of links". Honoured
        # here rather than in `check_views_root`, which is a pure path rule and
        # has no server config to read.
        roots = [
            os.path.realpath(root)
            for root in (server._server_config.get("filesystem_roots") or [])
            if isinstance(root, str) and root
        ]
        # Only a path the sink would itself accept is compared. `~` is NOT
        # expanded and a relative value is skipped rather than resolved:
        # `os.path.realpath("")` is the server's working directory, which could
        # sit inside a configured root and pass a check it was never meant to.
        # Both shapes are refused downstream by `check_views_root`, which is the
        # one place that decides what a views root may be.
        candidate = body.views_root.strip()
        if roots and os.path.isabs(candidate):
            resolved_views_root = os.path.realpath(candidate)
            if not any(
                resolved_views_root == root
                or resolved_views_root.startswith(root + os.sep)
                for root in roots
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Path is not within any configured filesystem root.",
                )

        if previous_root and views_service.roots_overlap(
            previous_root, body.views_root
        ):
            # Publishing under the previous root would write the whole tree and
            # then have the removal below walk that same root and delete it
            # again, while the response still reported the links it had made.
            # Refused before anything is written, because afterwards there is
            # nothing left to refuse.
            raise HTTPException(
                status_code=400,
                detail=(
                    "The views folder cannot be inside the one it replaces, or "
                    "contain it. Removing the old tree would delete the new "
                    "one. Pick a folder outside "
                    f"{previous_root}, or turn views off first."
                ),
            )

        collected = server.vault.db.run_immediate_read_task(
            lambda session: views_service.collect_in_session(session, kinds)
        )
        # The hub knows every registered library; this vault knows only itself.
        # Since v1.11 the owner can register several from Settings, and a views
        # tree inside a dormant one breaks that library's backups exactly as it
        # would this one's.
        other_library_roots = [
            library.path
            for library in server.library_registry.list_libraries()
            if library.path
        ]
        try:
            report = views_service.publish(
                server.vault,
                body.views_root,
                kinds,
                collected,
                other_library_roots,
            )
        except views_service.ViewsError as exc:
            # The settings are left untouched, so a refused folder never becomes
            # the recorded one and the next rebuild does not retry it.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if previous_root and not views_service.same_root(
            previous_root, body.views_root
        ):
            views_service.remove(previous_root)
        library_settings_service.set_views_config(
            server.vault.db, body.views_root, kinds
        )
        return _views_payload(report)

    @router.post(
        "/server-config/open",
        summary="Open server config location",
        response_model=OpenServerConfigResponse,
        description="Opens the server config path in the operating system file browser.",
    )
    def open_server_config(request: Request):
        _ensure_secure_when_required(request)
        config_path = getattr(server, "_server_config_path", None)
        opened = _open_in_os(config_path)
        return {"status": "success" if opened else "failed"}

    return router
