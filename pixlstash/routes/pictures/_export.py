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
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path


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
    destination: Optional[str] = None
    opened: Optional[bool] = None


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

    @router.post(
        "/pictures/export/folder",
        summary="Start picture export-to-folder job",
        description=(
            "Queues an asynchronous export task that writes pictures straight "
            "into a folder on the machine running PixlStash, then opens that "
            "folder in the host file manager. The destination must be an "
            "empty, writable, existing directory. Local owner, on that "
            "machine, only - see POST /pictures/export for a ZIP you can "
            "download from anywhere instead."
        ),
        response_model=ExportStartResponse,
    )
    def export_pictures_folder(
        request: Request,
        background_tasks: BackgroundTasks,
        destination: str = Query(...),
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
        if server.running_in_docker():
            raise HTTPException(
                status_code=403,
                detail="Export to folder is not available in Docker mode.",
            )
        # Resolve before validating: checking the blocklist against the raw
        # string first would let a symlink outside it (e.g. one that points at
        # /etc) pass the check and only get caught, incidentally, by the
        # writability test below - the same ordering bug the reference-folder
        # picker avoids in validate_reference_folder_accessible().
        if not os.path.isabs(destination):
            raise HTTPException(status_code=400, detail="Path must be absolute.")
        resolved_destination = os.path.realpath(os.path.normpath(destination))
        validation_error = validate_reference_folder_path(resolved_destination)
        if validation_error:
            raise HTTPException(status_code=400, detail=validation_error)
        if not os.path.isdir(resolved_destination):
            raise HTTPException(status_code=404, detail="Destination folder not found.")
        # Creating/replacing entries in a directory needs its execute (search)
        # bit as well as its write bit on POSIX - W_OK alone can pass here and
        # still fail on every actual write in the background task.
        if not os.access(resolved_destination, os.W_OK | os.X_OK):
            raise HTTPException(
                status_code=403, detail="Destination folder is not writable."
            )
        # A folder export writes plain files, unlike a ZIP: a name that
        # collides with something already in the destination is silently
        # overwritten (shutil.copy2 / open(..., "w") don't refuse). Requiring
        # an empty destination turns that into a refusal up front instead of a
        # data-loss surprise; the picker already offers "New folder" for this.
        if os.listdir(resolved_destination):
            raise HTTPException(
                status_code=409,
                detail="Destination folder is not empty. Choose or create an empty folder.",
            )

        task_id = str(uuid.uuid4())
        lease = request.state.library_lease
        server.export_tasks[task_id] = {
            "status": "in_progress",
            "total": 0,
            "processed": 0,
            "mode": "folder",
            "library_uuid": lease.library_uuid,
            "generation": lease.generation,
        }

        from pixlstash.utils.service.export_utils import (
            ExportUtils as PictureServiceUtils,
        )

        background_data = {
            "destination": resolved_destination,
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
            PictureServiceUtils.generate_folder_export,
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
            if task.get("mode") == "folder":
                # No download step follows a folder export (the files are
                # already on disk and the folder is opened server-side), so
                # this is the one report the task gets - collect it now
                # rather than leaking it in export_tasks forever.
                destination = task.get("destination")
                opened = task.get("opened")
                server.export_tasks.pop(task_id, None)
                return {
                    "status": "completed",
                    "destination": destination,
                    "opened": opened,
                    "total": total,
                    "processed": processed,
                    "progress": progress,
                }
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
