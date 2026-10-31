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
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import select
from typing import Literal, Optional

from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.library_roots import refuse_path_inside_a_library
from pixlstash.utils.service.share_copies import (
    make_share_copies,
    share_copies_root,
    share_links_root,
    sweep_share_copies,
)
from pixlstash.utils.watermark import get_watermark_bytes
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path
from pixlstash.utils.sql_chunking import chunked


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
    # No `destination`: this route is ANY_TOKEN, whose contract is that it
    # returns no owner data, and the folder export's destination is an
    # absolute host path. Its POST sibling is LOOPBACK_OWNER_ONLY, so the
    # only caller that can ever have a folder task is the one that named the
    # folder in the first place and has nothing to learn from it coming back.
    opened: Optional[bool] = None


class ShareCopiesRequest(BaseModel):
    # No cap: a drag or a copy of a whole view links the originals (#2420).
    picture_ids: list[int] = Field(min_length=1)
    keep_generation_data: bool = False
    # The longer side of each image, at most; None keeps the original size.
    max_long_edge: Optional[int] = Field(default=None, ge=64, le=16384)
    watermark: bool = False
    file_names: Literal["original", "pixlstash"] = "original"
    # The originals themselves, hard-linked under the share names: a drag-out
    # (#2420) starts at once, generation data included. Overrides the rest.
    link_originals: bool = False


class ShareCopyFile(BaseModel):
    picture_id: int
    path: str
    name: str
    size: int
    # "removed", "kept", or "unchanged" for a format nothing is stripped from.
    metadata: str
    resized: bool
    watermarked: bool


class ShareCopySkipped(BaseModel):
    picture_id: int
    reason: str


class ShareCopiesResponse(BaseModel):
    batch: str
    directory: str
    files: list[ShareCopyFile]
    skipped: list[ShareCopySkipped]


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
        keep_generation_data: bool = Query(
            False,
            description=(
                "Leave the ComfyUI workflow, prompt and A1111 parameters in "
                "the exported images. Stripped by default."
            ),
        ),
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
            "keep_generation_data": keep_generation_data,
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
            "empty, writable, existing directory outside every registered "
            "library, this library's reference folders and its import folders. "
            "Local owner, on that machine, only - see POST /pictures/export "
            "for a ZIP you can download from anywhere instead."
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
        keep_generation_data: bool = Query(
            False,
            description=(
                "Leave the ComfyUI workflow, prompt and A1111 parameters in "
                "the exported images. Stripped by default."
            ),
        ),
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
        # A folder inside the library is a folder the library reads back:
        # image_root is the vault's own store and every reference folder is
        # scanned, so the copies this writes return as new pictures - and in a
        # reference folder as duplicates of the very pictures just exported.
        # The empty-destination rule below does not cover it: an empty new
        # subfolder of the library passes that and is the likeliest way in.
        #
        # An import (watch) folder is the same class and the worst member of
        # it: the watcher imports whatever appears there, a re-encoded export
        # (any reduced resolution, any face/hand crop) carries a new pixel_sha
        # so it is not deduplicated, and a folder carrying
        # `delete_after_import` then os.remove()s the file it has just
        # imported - so the export destroys its own output.
        #
        # The roots are the shared list in `utils.library_roots`, not a copy:
        # this route grew the complete one while `POST /import-folders` and the
        # reference-folder routes kept partial ones, which is what #1206 item 1
        # asked to be fixed by having one list.
        vault = request.state.library_lease.vault
        refuse_path_inside_a_library(
            resolved_destination,
            server,
            vault,
            # 400 since #1177; the sibling callers answer 409.
            status_code=400,
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
            "keep_generation_data": keep_generation_data,
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

    @router.post(
        "/pictures/share-copies",
        summary="Make share-ready copies of pictures",
        description=(
            "Writes a copy of each picture into a new staging folder on the "
            "machine running PixlStash and returns the copies' paths, for the "
            "desktop app to hand to a drag, the clipboard or a folder. "
            "Generation data (ComfyUI prompt/workflow, A1111 parameters, "
            "EXIF/XMP) is removed unless keep_generation_data is set; videos "
            "and formats other than JPEG, PNG and WebP are copied unchanged "
            "and say so. Batches are removed after some hours. Local owner, "
            "on that machine, only - GET /pictures/export is the download "
            "for everyone else."
        ),
        response_model=ShareCopiesResponse,
    )
    def make_picture_share_copies(request: Request, payload: ShareCopiesRequest):
        vault = request.state.library_lease.vault
        root = share_copies_root(os.path.dirname(server.server_config_path))
        # The same class the folder export guards: copies written where the
        # library reads come straight back as pictures.
        refuse_path_inside_a_library(os.path.realpath(root), server, vault)
        sweep_share_copies(root)
        # Links go inside the library, on its originals' drive, in a dot-folder
        # no scan reads (share_copies.SHARE_LINKS_DIRNAME).
        link_root = (
            share_links_root(vault.image_root) if payload.link_originals else None
        )
        if link_root:
            sweep_share_copies(link_root)

        ids = list(dict.fromkeys(payload.picture_ids))

        def fetch(session):
            # Chunked: the id list has no cap, and SQLite's has one.
            rows = [
                row
                for chunk in chunked(ids)
                for row in session.exec(
                    select(
                        Picture.id,
                        Picture.file_path,
                        Picture.original_file_name,
                        Picture.created_at,
                    ).where(Picture.id.in_(chunk))
                ).all()
            ]
            return {
                row[0]: {
                    "id": row[0],
                    "file_path": row[1],
                    "original_file_name": row[2],
                    "created_at": row[3],
                }
                for row in rows
            }

        found = vault.db.run_immediate_read_task(fetch)
        watermark_bytes = None
        if payload.watermark:
            user = server.auth.get_user_for_request(request)
            watermark_bytes = get_watermark_bytes(
                getattr(user, "watermark_image", None)
            )
        result = make_share_copies(
            [found[i] for i in ids if i in found],
            vault.image_root,
            root,
            keep_generation_data=payload.keep_generation_data,
            max_long_edge=payload.max_long_edge,
            watermark_bytes=watermark_bytes,
            file_names=payload.file_names,
            link_originals=payload.link_originals,
            link_root=link_root,
        )
        result["skipped"] += [
            {"picture_id": i, "reason": "not_found"} for i in ids if i not in found
        ]
        return result

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
                opened = task.get("opened")
                server.export_tasks.pop(task_id, None)
                return {
                    "status": "completed",
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
