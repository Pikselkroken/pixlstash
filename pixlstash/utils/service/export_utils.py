"""ZIP generation and export functionality for pictures and features."""

import json
import logging
import os
import re
import sys
import tempfile
import zipfile

from PIL import Image, PngImagePlugin

from pixlstash.db_models.picture import Picture, PictureSet
from pixlstash.db_models.picture_set import PictureSetMember
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.service.caption_utils import CaptionUtils
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids
from sqlmodel import select


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
    def _write_detection_sidecar(
        zip_file, name_stem, arcname, pic, detections, scale_factor
    ):
        """Write a ``{name_stem}.json`` COCO-subset detection sidecar.

        Boxes are pixel ``xyxy``; when the export is downscaled the box
        coordinates and reported dimensions are scaled to match the exported
        image. Florence detections carry no confidence, so ``score`` defaults
        to ``0.0``.
        """
        width = getattr(pic, "width", None)
        height = getattr(pic, "height", None)
        objects = []
        for det in detections:
            bbox = det.bbox
            if not bbox or len(bbox) != 4:
                continue
            if scale_factor < 1.0:
                bbox = [int(round(v * scale_factor)) for v in bbox]
            objects.append(
                {
                    "label": det.label or "",
                    "bbox": bbox,
                    "score": float(det.score) if det.score is not None else 0.0,
                }
            )
        sidecar = {
            "image": arcname,
            "width": int(round(width * scale_factor)) if width else None,
            "height": int(round(height * scale_factor)) if height else None,
            "schema": "pixlstash.detections/v1",
            "bbox_format": "xyxy_px",
            "objects": objects,
        }
        zip_file.writestr(f"{name_stem}.json", json.dumps(sidecar, indent=2) + "\n")

    @staticmethod
    def _write_ideogram_sidecar(zip_file, name_stem, pic, detections, caption_text):
        """Write an Ideogram-4 structured-JSON caption ``{name_stem}.json``.

        This is the caption file ai-toolkit consumes for Ideogram-4 LoRA
        training (set ``caption_ext: json`` in the dataset config). It follows
        Ideogram-4's documented schema:

        - boxes are **normalized** ``[y_min, x_min, y_max, x_max]`` on a 0-1000
          grid (origin top-left) — resolution-independent, so the export's
          ``resolution`` setting does not affect them;
        - each detection becomes a ``compositional_deconstruction.elements``
          entry of ``type: "obj"`` with its label as ``desc`` (key order
          ``type, bbox, desc`` is significant — the model was trained on a fixed
          key order);
        - the picture's caption (when any) becomes ``high_level_description``;
        - ``style_description`` is omitted (it is optional, and we do not derive
          aesthetics/lighting/medium/palette) rather than emit a partial block.

        See docs/integration_architecture.md §11.1 for the contract.
        """
        width = getattr(pic, "width", None) or 0
        height = getattr(pic, "height", None) or 0

        def _norm(value, size):
            return max(0, min(1000, int(round(value / size * 1000))))

        elements = []
        if width > 0 and height > 0:
            for det in detections:
                bbox = det.bbox
                if not bbox or len(bbox) != 4:
                    continue
                x1, y1, x2, y2 = bbox
                ymin = _norm(y1, height)
                xmin = _norm(x1, width)
                ymax = _norm(y2, height)
                xmax = _norm(x2, width)
                if xmax <= xmin or ymax <= ymin:
                    continue
                # Key order (type, bbox, desc) matters for Ideogram-4.
                elements.append(
                    {
                        "type": "obj",
                        "bbox": [ymin, xmin, ymax, xmax],
                        "desc": det.label or "",
                    }
                )

        caption: dict = {}
        if caption_text:
            caption["high_level_description"] = caption_text
        caption["compositional_deconstruction"] = {
            "background": "",
            "elements": elements,
        }
        zip_file.writestr(f"{name_stem}.json", json.dumps(caption, indent=2) + "\n")

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

        tag_format = background_data.get("tag_format", "spaces")
        tag_format_d = (
            tag_format if tag_format in {"spaces", "underscores"} else "spaces"
        )

        include_character_name = background_data.get("include_character_name", False)
        include_character_name_enabled = (
            bool(include_character_name) and caption_mode_d != "none"
        )

        use_original_file_names = background_data.get("use_original_file_names", False)

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

        # Bounding-box sidecar mode for the picture's stored detections:
        #   "none"         — no sidecar
        #   "coco-json"    — a COCO-subset {stem}.json (pixel xyxy)
        #   "ideogram-json"— an Ideogram-4 structured-JSON caption {stem}.json
        #                    (normalized yxyx 0-1000; use ai-toolkit caption_ext=json)
        # Only meaningful for FULL exports (face/crop exports have no per-image
        # JSON sidecar concept).
        bbox_mode = (
            request.query_params.get("bbox_mode")
            or request.query_params.get("bboxMode")
            or background_data.get("bbox_mode")
            or "none"
        )
        bbox_mode_d = (bbox_mode or "none").lower()
        if bbox_mode_d not in {"none", "coco-json", "ideogram-json"}:
            bbox_mode_d = "none"
        if export_type_d != Picture.ExportType.FULL:
            bbox_mode_d = "none"

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
            "use_original_file_names": use_original_file_names,
            "tag_format_d": tag_format_d,
            "bbox_mode_d": bbox_mode_d,
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
        TEMP_EXPORT_DIR = os.path.join(tempfile.gettempdir(), "pixlstash", "exports")
        try:
            params = ExportUtils._parse_export_params(request, background_data)
            export_type_d = params["export_type_d"]
            caption_mode_d = params["caption_mode_d"]
            include_character_name_enabled = params["include_character_name_enabled"]
            scale_factor = params["scale_factor"]
            only_deleted = params["only_deleted"]
            picture_ids = params["picture_ids"]
            select_fields = params["select_fields"]
            use_original_file_names = params.get("use_original_file_names", False)
            tag_format_d = params.get("tag_format_d", "spaces")
            bbox_mode_d = params.get("bbox_mode_d", "none")
            used_names: dict = {}

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
                from pixlstash.routes.pictures import select_pictures_for_listing

                ordered_ids = select_pictures_for_listing(
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
                        "use_original_file_names",
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

            # Enforce token scope: remove any pictures the token is not
            # authorised to access before packaging the ZIP.
            scope_allowed = fetch_scope_allowed_picture_ids(server, request)
            if scope_allowed is not None and pics:
                pics = [p for p in pics if getattr(p, "id", None) in scope_allowed]

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

            # Pre-fetch detection rows once when a bbox sidecar is requested, so
            # the per-image loop is a dict lookup rather than N queries.
            detections_by_pic: dict = {}
            if bbox_mode_d in ("coco-json", "ideogram-json"):
                from pixlstash.db_models.detection import Detection

                def fetch_detections(session, ids):
                    rows = session.exec(
                        select(Detection).where(Detection.picture_id.in_(ids))
                    ).all()
                    grouped: dict = {}
                    for det in rows:
                        grouped.setdefault(det.picture_id, []).append(det)
                    return grouped

                detections_by_pic = server.vault.db.run_task(
                    fetch_detections, [pic.id for pic in pics]
                )

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
                            orig_name = getattr(pic, "original_file_name", None)
                            if use_original_file_names and orig_name:
                                orig_stem, orig_ext = os.path.splitext(orig_name)
                                file_ext = orig_ext or ext
                                count = used_names.get(orig_stem, 0) + 1
                                used_names[orig_stem] = count
                                name_stem = (
                                    orig_stem if count == 1 else f"{orig_stem}_{count}"
                                )
                                arcname = f"{name_stem}{file_ext}"
                            else:
                                name_stem = f"image_{idx:05d}"
                                arcname = f"{name_stem}{ext}"
                            if scale_factor < 1.0 and not VideoUtils.is_video_file(
                                full_path
                            ):
                                try:
                                    with Image.open(full_path) as img:
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
                                except Exception as exc:
                                    logger.warning(
                                        "Failed to resize %s (%s); falling back to"
                                        " original.",
                                        full_path,
                                        exc,
                                    )
                                    zip_file.write(full_path, arcname=arcname)
                            else:
                                zip_file.write(full_path, arcname=arcname)

                            caption_text = None
                            if caption_mode_d == "description":
                                caption_text = pic.description or ""
                                if not caption_text:
                                    caption_text = CaptionUtils.build_tag_caption(
                                        pic, tag_format_d
                                    )
                            elif caption_mode_d == "tags":
                                caption_text = CaptionUtils.build_tag_caption(
                                    pic, tag_format_d
                                )

                            if include_character_name_enabled:
                                character_names = CaptionUtils.build_character_caption(
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
                                    f"{name_stem}.txt",
                                    f"{caption_text}\n",
                                )

                            if bbox_mode_d == "coco-json":
                                ExportUtils._write_detection_sidecar(
                                    zip_file,
                                    name_stem,
                                    arcname,
                                    pic,
                                    detections_by_pic.get(pic.id, []),
                                    scale_factor,
                                )
                            elif bbox_mode_d == "ideogram-json":
                                ExportUtils._write_ideogram_sidecar(
                                    zip_file,
                                    name_stem,
                                    pic,
                                    detections_by_pic.get(pic.id, []),
                                    caption_text,
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
