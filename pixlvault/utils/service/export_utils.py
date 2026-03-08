"""ZIP generation and export functionality for pictures and features."""

import logging
import os
import re
import sys
import tempfile
import zipfile

from PIL import Image, PngImagePlugin

from pixlvault.db_models.picture import Picture, PictureSet
from pixlvault.db_models.picture_set import PictureSetMember
from pixlvault.utils.image_processing.image_utils import ImageUtils
from pixlvault.utils.image_processing.video_utils import VideoUtils
from pixlvault.utils.service.caption_utils import CaptionUtils
from sqlmodel import select

from pixlvault.routes.pictures import (
    _select_pictures_for_listing,
)

logger = logging.getLogger(__name__)


class ExportUtils:
    """Utility methods for ZIP-based picture export."""

    @staticmethod
    def _export_features_to_zip(
        img, base_name, features, tags_by_feature, feature_type, zip_file, scale=1.0
    ):
        """Export face/hand crops and tags to a zip file."""
        for feature in features:
            index = getattr(feature, f"{feature_type}_index", 0)
            if index < 0 or not feature.bbox:
                continue
            bbox = feature.bbox
            crop = img.crop(bbox)
            if scale < 1.0:
                crop = crop.resize(
                    (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
                    resample=Image.LANCZOS,
                )
            arcname = f"{base_name}_{feature_type}_{(index + 1):03d}.png"
            ExportUtils._write_image_to_zip(
                crop, arcname, zip_file, ext=".png", scale=1.0
            )
            tags = tags_by_feature.get(feature.id, [])
            if tags:
                zip_file.writestr(
                    f"{base_name}_{feature_type}_{(index + 1):03d}.txt",
                    ", ".join(tags) + "\n",
                )

    @staticmethod
    def _write_image_to_zip(
        img, arcname, zip_file, ext=None, scale=1.0, save_kwargs=None
    ):
        """Resize and write an image to a zip file, preserving metadata if possible."""
        from io import BytesIO

        if scale < 1.0:
            new_width = max(1, int(img.width * scale))
            new_height = max(1, int(img.height * scale))
            img = img.resize((new_width, new_height), resample=Image.LANCZOS)
        buffer = BytesIO()
        fmt = ext.lstrip(".").upper() if ext else (img.format or "PNG")
        if fmt == "JPG":
            fmt = "JPEG"
        if save_kwargs is None:
            save_kwargs = {}
        img.save(buffer, format=fmt, **save_kwargs)
        zip_file.writestr(arcname, buffer.getvalue())

    @staticmethod
    def _parse_export_params(request, background_data):
        """
        Parse and normalise export parameters from request and background_data.

        Returns a dict with all normalised parameters.
        """
        export_type_value = (
            request.query_params.get("export_type")
            or request.query_params.get("exportType")
            or background_data.get("export_type")
        )
        export_type_d = Picture.ExportType.from_string(export_type_value)

        caption_mode = background_data.get("caption_mode", "description")
        caption_mode_d = (caption_mode or "description").lower()
        if caption_mode_d not in {"none", "description", "tags"}:
            caption_mode_d = "description"

        include_character_name = background_data.get("include_character_name", False)
        include_character_name_enabled = (
            bool(include_character_name) and caption_mode_d != "none"
        )

        if export_type_d != Picture.ExportType.FULL:
            caption_mode_d = "tags"
            include_character_name_enabled = False

        resolution = background_data.get("resolution", "original")
        resolution_d = (resolution or "original").lower()
        if resolution_d not in {"original", "half", "quarter"}:
            resolution_d = "original"
        scale_map = {
            "original": 1.0,
            "half": 0.5,
            "quarter": 0.25,
        }
        scale_factor = scale_map.get(resolution_d, 1.0)

        only_deleted = request.query_params.get("character_id") == "SCRAPHEAP"
        picture_ids = request.query_params.getlist("id")

        select_fields = Picture.metadata_fields()
        if export_type_d == Picture.ExportType.FULL:
            if caption_mode_d != "none":
                select_fields = select_fields | {"tags"}
            if include_character_name_enabled:
                select_fields = select_fields | {"characters"}

        return {
            "export_type_d": export_type_d,
            "caption_mode_d": caption_mode_d,
            "include_character_name_enabled": include_character_name_enabled,
            "scale_factor": scale_factor,
            "only_deleted": only_deleted,
            "picture_ids": picture_ids,
            "select_fields": select_fields,
        }

    @staticmethod
    def _deduplicate_stacks(pics: list) -> list:
        """Keep only the stack leader from each stack, drop the rest.

        The leader is the newest picture by ``created_at`` (ties broken by
        highest ``id``), matching the frontend's ``sortStackMembers`` logic.
        Pictures not in any stack are passed through unchanged.
        """
        by_stack: dict = {}
        result = []
        for pic in pics:
            stack_id = getattr(pic, "stack_id", None)
            if stack_id is None:
                result.append(pic)
            else:
                by_stack.setdefault(stack_id, []).append(pic)

        for stack_id, members in by_stack.items():
            leader = max(
                members,
                key=lambda p: (
                    getattr(p, "created_at", None) or "",
                    getattr(p, "id", 0) or 0,
                ),
            )
            result.append(leader)

        return result

    @staticmethod
    def generate_zip(server, request, task_id, export_tasks, background_data):
        """
        Generate a ZIP file for picture export.

        Args:
            server: The server instance.
            request: The FastAPI request.
            task_id: The export task ID.
            export_tasks: The export_tasks dict (for progress/status).
            background_data: Dict of extra params (query, set_id, threshold,
                caption_mode, include_character_name, resolution, export_type).
        """
        TEMP_EXPORT_DIR = os.path.join(tempfile.gettempdir(), "pixlvault", "exports")
        try:
            params = ExportUtils._parse_export_params(request, background_data)
            export_type_d = params["export_type_d"]
            caption_mode_d = params["caption_mode_d"]
            include_character_name_enabled = params["include_character_name_enabled"]
            scale_factor = params["scale_factor"]
            only_deleted = params["only_deleted"]
            picture_ids = params["picture_ids"]
            select_fields = params["select_fields"]

            pics = []
            set_id = background_data.get("set_id")
            query = background_data.get("query")
            threshold = background_data.get("threshold", 0.0)

            if picture_ids:
                pics = server.vault.db.run_task(
                    Picture.find,
                    id=picture_ids,
                    select_fields=select_fields,
                    include_deleted=only_deleted,
                )
            elif set_id is not None:
                logger.debug("Exporting pictures set {} ".format(set_id))

                def fetch_members(session, set_id):
                    members = session.exec(
                        select(PictureSetMember).where(
                            PictureSetMember.set_id == set_id
                        )
                    ).all()
                    picture_ids = [m.picture_id for m in members]
                    if not picture_ids:
                        return []
                    return Picture.find(
                        session,
                        id=picture_ids,
                        select_fields=select_fields,
                    )

                pics = server.vault.db.run_task(fetch_members, set_id)
            elif query:
                logger.debug("Exporting pictures using search query: {}".format(query))

                def find_by_text(session, query):
                    words = re.findall(r"\b\w+\b", query.lower())
                    query_full = "A photo of " + query
                    return [
                        r[0]
                        for r in Picture.semantic_search(
                            session,
                            query_full,
                            words,
                            text_to_embedding=server.vault.generate_text_embedding,
                            offset=0,
                            limit=sys.maxsize,
                            threshold=threshold,
                            select_fields=select_fields,
                            only_deleted=only_deleted,
                        )
                    ]

                pics = server.vault.db.run_task(find_by_text, query)
            else:
                logger.debug("Exporting pictures using list filters")
                ordered_ids = _select_pictures_for_listing(
                    server=server,
                    request=request,
                    sort=None,
                    descending=True,
                    offset=0,
                    limit=sys.maxsize,
                    metadata_fields=select_fields,
                    return_ids_only=True,
                    exclude_query_params={
                        "query",
                        "set_id",
                        "threshold",
                        "caption_mode",
                        "include_character_name",
                        "export_type",
                        "resolution",
                    },
                )
                if ordered_ids:
                    pics = server.vault.db.run_task(
                        Picture.find,
                        id=ordered_ids,
                        select_fields=select_fields,
                        include_deleted=only_deleted,
                    )
                    pic_map = {pic.id: pic for pic in pics}
                    pics = [pic_map.get(pid) for pid in ordered_ids if pid in pic_map]

            logger.debug(
                f"Export task {task_id}: {len(pics)} pictures to be added to the ZIP."
            )

            pics = ExportUtils._deduplicate_stacks(pics)
            logger.debug(
                f"Export task {task_id}: {len(pics)} pictures after stack deduplication."
            )

            if not pics:
                export_tasks[task_id]["status"] = "failed"
                return

            filename_parts = []
            if set_id is not None:

                def get_set(session, set_id):
                    return session.get(PictureSet, set_id)

                picture_set = server.vault.db.run_task(get_set, set_id)
                if picture_set:
                    filename_parts.append(picture_set.name.replace(" ", "_"))
            if query:
                filename_parts.append(f"search_{query[:20]}")

            filename = "_".join(filename_parts) if filename_parts else "pictures"
            filename = f"{filename}_{len(pics)}_images.zip"
            export_tasks[task_id]["filename"] = filename

            os.makedirs(TEMP_EXPORT_DIR, exist_ok=True)
            zip_path = os.path.join(TEMP_EXPORT_DIR, f"export_{task_id}.zip")
            feature_faces_by_pic = {}
            face_tags_by_face = {}

            if export_type_d != Picture.ExportType.FULL:
                (
                    feature_faces_by_pic,
                    _,
                    face_tags_by_face,
                    _,
                ) = server.vault.db.run_task(
                    Picture.fetch_features,
                    [pic.id for pic in pics],
                )

            if export_type_d == Picture.ExportType.FULL:
                total_items = len(pics)
            else:
                total_items = 0
                for pic in pics:
                    if not getattr(pic, "file_path", None) or not os.path.exists(
                        ImageUtils.resolve_picture_path(
                            server.vault.image_root, pic.file_path
                        )
                    ):
                        continue
                    full_path = ImageUtils.resolve_picture_path(
                        server.vault.image_root, pic.file_path
                    )
                    if VideoUtils.is_video_file(full_path):
                        continue
                    faces = feature_faces_by_pic.get(pic.id, [])
                    for face in faces:
                        if getattr(face, "face_index", 0) < 0:
                            continue
                        if not face.bbox:
                            continue
                        total_items += 1

            export_tasks[task_id]["total"] = total_items
            export_tasks[task_id]["processed"] = 0

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                for idx, pic in enumerate(pics, start=1):
                    if (
                        hasattr(pic, "file_path")
                        and pic.file_path
                        and os.path.exists(
                            ImageUtils.resolve_picture_path(
                                server.vault.image_root, pic.file_path
                            )
                        )
                    ):
                        full_path = ImageUtils.resolve_picture_path(
                            server.vault.image_root, pic.file_path
                        )
                        ext = os.path.splitext(full_path)[1]
                        if export_type_d == Picture.ExportType.FULL:
                            arcname = f"image_{idx:05d}{ext}"
                            try:
                                with Image.open(full_path) as img:
                                    if (
                                        scale_factor < 1.0
                                        and not VideoUtils.is_video_file(full_path)
                                    ):
                                        save_kwargs = {}
                                        exif_bytes = img.info.get("exif")
                                        if exif_bytes:
                                            save_kwargs["exif"] = exif_bytes
                                        icc_profile = img.info.get("icc_profile")
                                        if icc_profile:
                                            save_kwargs["icc_profile"] = icc_profile
                                        if (
                                            img.format or ext.lstrip(".").upper()
                                        ).upper() == "PNG":
                                            pnginfo = PngImagePlugin.PngInfo()
                                            for key, value in (img.info or {}).items():
                                                if key in {"exif", "icc_profile"}:
                                                    continue
                                                if isinstance(value, str):
                                                    pnginfo.add_text(key, value)
                                                elif isinstance(value, bytes):
                                                    try:
                                                        pnginfo.add_text(
                                                            key,
                                                            value.decode("utf-8"),
                                                        )
                                                    except Exception:
                                                        continue
                                            save_kwargs["pnginfo"] = pnginfo
                                        ExportUtils._write_image_to_zip(
                                            img,
                                            arcname,
                                            zip_file,
                                            ext=ext,
                                            scale=scale_factor,
                                            save_kwargs=save_kwargs,
                                        )
                                    else:
                                        zip_file.write(full_path, arcname=arcname)
                            except Exception as exc:
                                logger.warning(
                                    "Failed to resize %s (%s); falling back to"
                                    " original.",
                                    full_path,
                                    exc,
                                )
                                zip_file.write(full_path, arcname=arcname)

                            caption_text = None
                            if caption_mode_d == "description":
                                caption_text = pic.description or ""
                                if not caption_text:
                                    caption_text = CaptionUtils._build_tag_caption(pic)
                            elif caption_mode_d == "tags":
                                caption_text = CaptionUtils._build_tag_caption(pic)

                            if include_character_name_enabled:
                                character_names = CaptionUtils._build_character_caption(
                                    pic
                                )
                                if character_names:
                                    if caption_mode_d == "tags":
                                        caption_text = (
                                            ", ".join([character_names, caption_text])
                                            if caption_text
                                            else character_names
                                        )
                                    elif caption_mode_d == "description":
                                        caption_text = (
                                            f"{character_names}: {caption_text}"
                                            if caption_text
                                            else character_names
                                        )

                            if caption_mode_d != "none" and caption_text is not None:
                                zip_file.writestr(
                                    f"image_{idx:05d}.txt",
                                    f"{caption_text}\n",
                                )
                            export_tasks[task_id]["processed"] += 1
                        else:
                            if VideoUtils.is_video_file(full_path):
                                continue
                            try:
                                with Image.open(full_path) as img:
                                    base_name = f"image_{idx:05d}"
                                    export_faces = (
                                        export_type_d == Picture.ExportType.FACE
                                    )

                                    if export_faces:
                                        faces = feature_faces_by_pic.get(pic.id, [])
                                        for face in faces:
                                            if face.bbox:
                                                face.bbox = ImageUtils.clamp_bbox(
                                                    face.bbox, img.width, img.height
                                                )
                                        ExportUtils._export_features_to_zip(
                                            img,
                                            base_name,
                                            faces,
                                            face_tags_by_face,
                                            "face",
                                            zip_file,
                                            scale=scale_factor,
                                        )
                                        export_tasks[task_id]["processed"] += len(faces)
                            except Exception as exc:
                                logger.warning(
                                    "Failed to export features for %s (%s).",
                                    full_path,
                                    exc,
                                )

            zip_size = os.path.getsize(zip_path)
            logger.debug(
                f"Export task {task_id}: ZIP file created with size {zip_size} bytes."
            )

            export_tasks[task_id]["status"] = "completed"
            export_tasks[task_id]["file_path"] = zip_path
        except Exception as exc:
            export_tasks[task_id]["status"] = "failed"
            logger.error(f"Export task {task_id} failed: {exc}")
