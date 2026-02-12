import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlmodel import Session

from pixlvault.database import DBPriority
from pixlvault.db_models import User
from pixlvault.pixl_logging import get_logger
from pixlvault.utils import apply_user_config_patch, serialize_user_config

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _ensure_secure_when_required(request: Request):
        server.auth.ensure_secure_when_required(request)

    class ChangePasswordRequest(BaseModel):
        current_password: Optional[str] = None
        new_password: str = Field(
            ..., min_length=8, description="Password must be at least 8 characters long"
        )

    class CreateTokenRequest(BaseModel):
        description: Optional[str] = None

    @router.get("/users/me/config")
    async def get_me_config(request: Request):
        _ensure_secure_when_required(request)
        user = server.auth.get_user_for_request(request)
        return serialize_user_config(user)

    @router.patch("/users/me/config")
    async def patch_me_config(request: Request):
        _ensure_secure_when_required(request)
        user_id = server.auth.require_user_id(request)

        start_time = time.time()
        logger.debug(f"[TIMING] PATCH /users/me/config called at {start_time:.3f}")
        patch_data = await request.json()

        def update_user(session: Session, user_id: int):
            user = session.get(User, user_id)
            if user is None:
                raise HTTPException(status_code=404, detail="User not found")

            try:
                updated = apply_user_config_patch(user, patch_data)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            if updated:
                session.add(user)
                session.commit()
                session.refresh(user)
            return user, updated

        user, updated = server.vault.db.run_task(
            update_user, user_id, priority=DBPriority.IMMEDIATE
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

    @router.post("/users/me/auth")
    async def change_me_password(payload: ChangePasswordRequest, request: Request):
        result = server.auth.change_password(request, payload)
        server._user = server.auth.user
        return result

    @router.get("/users/me/auth")
    async def get_me_auth(request: Request):
        return server.auth.get_auth_info(request)

    @router.post("/users/me/token")
    async def create_me_token(payload: CreateTokenRequest, request: Request):
        return server.auth.create_token(request, payload.description)

    @router.get("/users/me/token")
    async def list_me_tokens(request: Request):
        return server.auth.list_tokens(request)

    @router.delete("/users/me/token/{token_id}")
    async def delete_me_token(token_id: int, request: Request):
        return server.auth.delete_token(request, token_id)

    @router.get("/workers/progress")
    async def get_workers_progress(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)
        return {"status": "success", "workers": server.vault.get_worker_progress()}

    return router
