import os
import shutil
import uuid

from fastapi import (
    BackgroundTasks,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict
from typing import Optional

from pixlstash.pixl_logging import get_logger


logger = get_logger(__name__)


class _PinnedVaultServer:
    """Delegate machine-global state while fixing export work to one vault."""

    def __init__(self, server, vault):
        self._server = server
        self.vault = vault

    def __getattr__(self, name):
        return getattr(self._server, name)


class ExportStartResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str


class ExportStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    total: int
    processed: int
    progress: float
    download_url: Optional[str] = None


def register_routes(router, server):
    def discard_export(task_id: str, task: dict) -> None:
        current = server.export_tasks.get(task_id)
        if current is task:
            server.export_tasks.pop(task_id, None)
        private_dir = task.get("private_dir")
        if not private_dir:
            return
        if not os.path.basename(private_dir).startswith("pixlstash_export_"):
            logger.warning("Refusing to remove unexpected export path %s", private_dir)
            return
        try:
            if os.path.islink(private_dir):
                os.unlink(private_dir)
            elif os.path.isdir(private_dir):
                shutil.rmtree(private_dir)
        except OSError as exc:
            logger.warning("Could not remove completed export %s: %s", private_dir, exc)

    @router.get(
        "/pictures/export",
        summary="Start picture export job",
        description="Queues an asynchronous export task and returns a task id for polling status and downloading the generated archive.",
        response_model=ExportStartResponse,
    )
    def export_pictures_zip(
        request: Request,
        background_tasks: BackgroundTasks,
        query: str = Query(None),
        set_id: int = Query(None),
        threshold: float = Query(0.0),
        caption_mode: str = Query("description"),
        include_character_name: bool = Query(False),
        use_original_file_names: bool = Query(False),
        resolution: str = Query("original"),
        export_type: str = Query("full"),
        tag_format: str = Query("spaces"),
        bbox_mode: str = Query("none"),
    ):
        task_id = str(uuid.uuid4())
        lease = request.state.library_lease
        server.export_tasks[task_id] = {
            "status": "in_progress",
            "file_path": None,
            "total": 0,
            "processed": 0,
            "filename": None,
            "library_uuid": lease.library_uuid,
            "generation": lease.generation,
        }

        from pixlstash.utils.service.export_utils import (
            ExportUtils as PictureServiceUtils,
        )

        # Gather extra params for the export service
        background_data = {
            "query": query,
            "set_id": set_id,
            "threshold": threshold,
            "caption_mode": caption_mode,
            "include_character_name": include_character_name,
            "use_original_file_names": use_original_file_names,
            "resolution": resolution,
            "export_type": export_type,
            "tag_format": tag_format,
            "bbox_mode": bbox_mode,
        }
        background_tasks.add_task(
            PictureServiceUtils.generate_zip,
            _PinnedVaultServer(server, lease.vault),
            request,
            task_id,
            server.export_tasks,
            background_data,
        )
        return JSONResponse({"task_id": task_id})

    @router.get(
        "/pictures/export/status",
        summary="Get export job status",
        description="Returns current progress for an export task id, including completion state and download URL when ready.",
        response_model=ExportStatusResponse,
    )
    def export_status(request: Request, task_id: str):
        task = server.export_tasks.get(task_id)
        lease = request.state.library_lease
        if (
            not task
            or task.get("library_uuid") != lease.library_uuid
            or task.get("generation") != lease.generation
        ):
            raise HTTPException(status_code=404, detail="Task not found")

        total = task.get("total") or 0
        processed = task.get("processed") or 0
        progress = (processed / total * 100.0) if total else 0.0

        if task["status"] == "completed":
            return {
                "status": "completed",
                "download_url": f"/pictures/export/download/{task_id}",
                "total": total,
                "processed": processed,
                "progress": progress,
            }

        return {
            "status": task["status"],
            "total": total,
            "processed": processed,
            "progress": progress,
        }

    @router.get(
        "/pictures/export/download/{task_id}",
        summary="Download completed export",
        description="Downloads the generated export file for a completed task id.",
        response_class=FileResponse,
        responses={200: {"content": {"application/zip": {}}}},
    )
    def download_export(request: Request, task_id: str):
        task = server.export_tasks.get(task_id)
        lease = request.state.library_lease
        if (
            not task
            or task["status"] != "completed"
            or task.get("library_uuid") != lease.library_uuid
            or task.get("generation") != lease.generation
        ):
            raise HTTPException(status_code=404, detail="File not ready")

        filename = task.get("filename") or os.path.basename(task["file_path"])
        return FileResponse(
            task["file_path"],
            filename=filename,
            background=BackgroundTask(discard_export, task_id, task),
        )
