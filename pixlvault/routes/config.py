import json
import os
import subprocess
import sys
import time
from typing import Optional

try:
    import psutil
except Exception:  # pragma: no cover - optional dependency
    psutil = None

try:
    import pynvml
except Exception:  # pragma: no cover - optional dependency
    pynvml = None

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

    def _load_watch_folders():
        config_path = getattr(server, "_server_config_path", None)
        if not config_path or not os.path.exists(config_path):
            return config_path, []
        try:
            with open(config_path, "r") as handle:
                config = json.load(handle)
            raw_folders = config.get("watch_folders", []) or []
        except Exception as exc:
            logger.warning("Failed to read watch_folders: %s", exc)
            return config_path, []

        folders = []
        for entry in raw_folders:
            if isinstance(entry, str):
                folder = entry
            elif isinstance(entry, dict):
                folder = entry.get("folder")
            else:
                folder = None
            if folder:
                folders.append(folder)
        return config_path, folders

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

    def _get_process_usage():
        payload = {
            "cpu_percent": None,
            "ram_used_gb": None,
            "ram_total_gb": None,
            "ram_percent": None,
            "vram_used_gb": None,
            "vram_total_gb": None,
            "vram_percent": None,
        }

        if psutil:
            try:
                process = psutil.Process(os.getpid())
                payload["cpu_percent"] = process.cpu_percent(interval=None)
                memory = process.memory_info()
                payload["ram_used_gb"] = round(memory.rss / (1024**3), 2)
                payload["ram_percent"] = process.memory_percent()
            except Exception as exc:
                logger.warning("Failed to read CPU/RAM usage: %s", exc)

        if pynvml:
            try:
                pynvml.nvmlInit()
                pid = os.getpid()
                used_bytes = 0
                total_bytes = 0
                device_count = pynvml.nvmlDeviceGetCount()
                for index in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                    processes = []
                    try:
                        processes = pynvml.nvmlDeviceGetComputeRunningProcesses(
                            handle
                        )
                    except Exception:
                        processes = []
                    try:
                        processes += pynvml.nvmlDeviceGetGraphicsRunningProcesses(
                            handle
                        )
                    except Exception:
                        pass
                    for entry in processes:
                        if entry.pid != pid:
                            continue
                        used_gpu = getattr(entry, "usedGpuMemory", None)
                        if used_gpu is None:
                            continue
                        if used_gpu == getattr(pynvml, "NVML_VALUE_NOT_AVAILABLE", -1):
                            continue
                        used_bytes += used_gpu
                        try:
                            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                            total_bytes += mem_info.total
                        except Exception:
                            pass
                if total_bytes > 0 and used_bytes >= 0:
                    payload["vram_total_gb"] = round(total_bytes / (1024**3), 2)
                    payload["vram_used_gb"] = round(used_bytes / (1024**3), 2)
                    payload["vram_percent"] = round(used_bytes / total_bytes * 100, 1)
            except Exception as exc:
                logger.warning("Failed to read VRAM usage: %s", exc)
            finally:
                try:
                    pynvml.nvmlShutdown()
                except Exception:
                    pass

        return payload

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
        if "keep_models_in_memory" in patch_data:
            server.vault.set_keep_models_in_memory(
                getattr(user, "keep_models_in_memory", True)
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
        return {
            "status": "success",
            "workers": server.vault.get_worker_progress(),
            "process": _get_process_usage(),
        }

    @router.get("/server-config/watch-folders")
    async def get_watch_folders(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)
        config_path, folders = _load_watch_folders()
        return {
            "status": "success",
            "config_path": config_path,
            "watch_folders": folders,
        }

    @router.post("/server-config/open")
    async def open_server_config(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)
        config_path = getattr(server, "_server_config_path", None)
        opened = _open_in_os(config_path)
        return {"status": "success" if opened else "failed"}

    return router
