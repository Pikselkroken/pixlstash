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

from pixlstash.database import DBPriority
from pixlstash.db_models import User
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.config_utils import (
    apply_user_config_patch,
    serialize_user_config,
)

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()
    process_usage_handle = None
    process_usage_pid = None
    last_cpu_sample_at = None
    last_cpu_seconds = None

    if psutil:
        try:
            process_usage_handle = psutil.Process(os.getpid())
            process_usage_pid = process_usage_handle.pid
        except Exception as exc:
            logger.warning("Failed to initialize process usage handle: %s", exc)
            process_usage_handle = None
            process_usage_pid = None

    def _get_total_process_cpu_seconds(process):
        cpu_times = process.cpu_times()
        total_seconds = float(cpu_times.user + cpu_times.system)
        try:
            for child in process.children(recursive=True):
                child_times = child.cpu_times()
                total_seconds += float(child_times.user + child_times.system)
        except Exception:
            pass
        return total_seconds

    def _set_vram_payload(payload: dict, used_bytes: int, total_bytes: int) -> bool:
        try:
            total = int(total_bytes or 0)
            if total <= 0:
                return False
            used = max(0, int(used_bytes or 0))
            payload["vram_total_gb"] = round(total / (1024**3), 2)
            payload["vram_used_gb"] = round(used / (1024**3), 2)
            payload["vram_percent"] = round((used / total) * 100.0, 1)
            return True
        except Exception:
            return False

    def _collect_vram_from_torch(payload: dict) -> bool:
        try:
            import torch

            if not torch.cuda.is_available():
                return False
            total_bytes = 0
            used_bytes = 0
            device_count = int(torch.cuda.device_count() or 0)
            if device_count <= 0:
                return False
            for index in range(device_count):
                props = torch.cuda.get_device_properties(index)
                total_bytes += int(getattr(props, "total_memory", 0) or 0)
                try:
                    used_bytes += int(torch.cuda.memory_reserved(index) or 0)
                except Exception:
                    pass
            return _set_vram_payload(payload, used_bytes, total_bytes)
        except Exception:
            return False

    def _parse_nvidia_smi_values(command: list[str]) -> list[int]:
        output = subprocess.check_output(command, stderr=subprocess.DEVNULL, text=True)
        values = []
        for line in output.splitlines():
            value = line.strip().split(",", 1)[0].strip()
            if not value or value.upper() == "N/A":
                continue
            try:
                values.append(int(float(value)))
            except Exception:
                continue
        return values

    def _collect_vram_from_nvidia_smi(payload: dict) -> bool:
        try:
            totals_mib = _parse_nvidia_smi_values(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.total",
                    "--format=csv,noheader,nounits",
                ]
            )
            if not totals_mib:
                return False
            total_bytes = sum(totals_mib) * 1024 * 1024

            pid = os.getpid()
            used_mib = 0
            try:
                process_lines = subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-compute-apps=pid,used_gpu_memory",
                        "--format=csv,noheader,nounits",
                    ],
                    stderr=subprocess.DEVNULL,
                    text=True,
                )
                for line in process_lines.splitlines():
                    parts = [part.strip() for part in line.split(",")]
                    if len(parts) < 2:
                        continue
                    try:
                        entry_pid = int(parts[0])
                    except Exception:
                        continue
                    if entry_pid != pid:
                        continue
                    value = parts[1]
                    if not value or value.upper() == "N/A":
                        continue
                    try:
                        used_mib += int(float(value))
                    except Exception:
                        continue
            except Exception:
                used_mib = 0

            used_bytes = used_mib * 1024 * 1024
            return _set_vram_payload(payload, used_bytes, total_bytes)
        except Exception:
            return False

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
        nonlocal process_usage_handle
        nonlocal process_usage_pid
        nonlocal last_cpu_sample_at
        nonlocal last_cpu_seconds
        payload = {
            "cpu_percent": None,
            "cpu_percent_all_cores": None,
            "cpu_percent_one_core": None,
            "ram_used_gb": None,
            "ram_total_gb": None,
            "ram_percent": None,
            "vram_used_gb": None,
            "vram_total_gb": None,
            "vram_percent": None,
        }

        if psutil:
            try:
                current_pid = os.getpid()
                process = process_usage_handle
                if (
                    process is None
                    or process_usage_pid != current_pid
                    or not process.is_running()
                ):
                    process = psutil.Process(current_pid)
                    process_usage_handle = process
                    process_usage_pid = process.pid
                    last_cpu_sample_at = None
                    last_cpu_seconds = None

                now = time.monotonic()
                cpu_seconds = _get_total_process_cpu_seconds(process)
                if (
                    last_cpu_sample_at is not None
                    and last_cpu_seconds is not None
                    and now > last_cpu_sample_at
                ):
                    elapsed = now - last_cpu_sample_at
                    used_cpu = max(0.0, cpu_seconds - last_cpu_seconds)
                    cpu_count = psutil.cpu_count() or 1
                    cpu_percent_one_core = max(
                        0.0,
                        (used_cpu / elapsed) * 100.0,
                    )
                    cpu_percent_all_cores = max(
                        0.0,
                        min(100.0, cpu_percent_one_core / cpu_count),
                    )
                    payload["cpu_percent_one_core"] = cpu_percent_one_core
                    payload["cpu_percent_all_cores"] = cpu_percent_all_cores
                    payload["cpu_percent"] = cpu_percent_all_cores
                else:
                    payload["cpu_percent"] = 0.0
                    payload["cpu_percent_all_cores"] = 0.0
                    payload["cpu_percent_one_core"] = 0.0

                last_cpu_sample_at = now
                last_cpu_seconds = cpu_seconds

                memory = process.memory_info()
                payload["ram_used_gb"] = round(memory.rss / (1024**3), 2)
                payload["ram_percent"] = process.memory_percent()
            except Exception as exc:
                logger.warning("Failed to read CPU/RAM usage: %s", exc)

        vram_collected = False
        if pynvml:
            try:
                pynvml.nvmlInit()
                pid = os.getpid()
                used_bytes = 0
                total_bytes = 0
                device_count = pynvml.nvmlDeviceGetCount()
                for index in range(device_count):
                    handle = pynvml.nvmlDeviceGetHandleByIndex(index)
                    try:
                        mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                        total_bytes += int(getattr(mem_info, "total", 0) or 0)
                    except Exception:
                        pass
                    processes = []
                    try:
                        processes = pynvml.nvmlDeviceGetComputeRunningProcesses(handle)
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
                vram_collected = _set_vram_payload(payload, used_bytes, total_bytes)
            except Exception as exc:
                logger.warning("Failed to read VRAM usage: %s", exc)
            finally:
                try:
                    pynvml.nvmlShutdown()
                except Exception:
                    pass

        if not vram_collected:
            vram_collected = _collect_vram_from_torch(payload)

        if not vram_collected:
            _collect_vram_from_nvidia_smi(payload)

        return payload

    class ChangePasswordRequest(BaseModel):
        current_password: Optional[str] = None
        new_password: str = Field(
            ..., min_length=8, description="Password must be at least 8 characters long"
        )

    class CreateTokenRequest(BaseModel):
        description: Optional[str] = None

    @router.get(
        "/users/me/config",
        summary="Get current user config",
        description="Returns the authenticated user's UI and behavior configuration payload.",
    )
    def get_me_config(request: Request):
        _ensure_secure_when_required(request)
        user = server.auth.get_user_for_request(request)
        return serialize_user_config(user)

    @router.patch(
        "/users/me/config",
        summary="Update current user config",
        description="Applies a partial config patch for the authenticated user and returns updated settings.",
    )
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
        if "max_vram_gb" in patch_data:
            server.vault.set_max_vram_usage_gb(getattr(user, "max_vram_gb", None))
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
        description="Changes the authenticated user's password according to auth policy.",
    )
    def change_me_password(payload: ChangePasswordRequest, request: Request):
        result = server.auth.change_password(request, payload)
        server._user = server.auth.user
        return result

    @router.get(
        "/users/me/auth",
        summary="Get auth state",
        description="Returns authentication and session-related information for the current request.",
    )
    def get_me_auth(request: Request):
        return server.auth.get_auth_info(request)

    @router.post(
        "/users/me/token",
        summary="Create API token",
        description="Creates a personal access token for the authenticated user.",
    )
    def create_me_token(payload: CreateTokenRequest, request: Request):
        return server.auth.create_token(request, payload.description)

    @router.get(
        "/users/me/token",
        summary="List API tokens",
        description="Lists personal access tokens owned by the authenticated user.",
    )
    def list_me_tokens(request: Request):
        return server.auth.list_tokens(request)

    @router.delete(
        "/users/me/token/{token_id}",
        summary="Delete API token",
        description="Deletes one personal access token by id for the authenticated user.",
    )
    def delete_me_token(token_id: int, request: Request):
        return server.auth.delete_token(request, token_id)

    @router.get(
        "/workers/progress",
        summary="Get worker progress",
        description="Returns background worker progress plus process CPU, RAM, and VRAM usage metrics.",
    )
    def get_workers_progress(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)
        return {
            "status": "success",
            "workers": server.vault.get_worker_progress(),
            "process": _get_process_usage(),
        }

    @router.get(
        "/server-config/watch-folders",
        summary="List watch folders",
        description="Returns watch-folder paths from server configuration.",
    )
    def get_watch_folders(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)
        config_path, folders = _load_watch_folders()
        return {
            "status": "success",
            "config_path": config_path,
            "watch_folders": folders,
        }

    @router.post(
        "/server-config/open",
        summary="Open server config location",
        description="Opens the server config path in the operating system file browser.",
    )
    def open_server_config(request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)
        config_path = getattr(server, "_server_config_path", None)
        opened = _open_in_os(config_path)
        return {"status": "success" if opened else "failed"}

    return router
