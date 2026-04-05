from __future__ import annotations

import os
import uuid
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import Face, Picture, PictureProjectMember, PictureSetMember, PictureStack
from pixlstash.image_plugins.base import ImagePlugin
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.pixl_logging import get_logger
from pixlstash.stacking import get_or_create_stack_for_picture

logger = get_logger(__name__)

_VIDEO_FORMATS = {"MP4", "WEBM", "MOV", "AVI", "MKV"}

# Standard TIFF/EXIF tag id for the Orientation field.
_EXIF_ORIENTATION_TAG = 0x0112


def _get_exif_bbox_transform(
    file_path: str,
    src_w: int,
    src_h: int,
) -> tuple[Any, int, int]:
    """Return ``(transform, after_w, after_h)`` for the EXIF orientation of *file_path*.

    ``transform`` is a callable ``bbox -> bbox`` that maps raw pixel coordinates
    (as stored by the face-extraction worker which uses ``cv2.imread``) to the
    coordinate space produced by ``PIL.ImageOps.exif_transpose``.  Returns
    ``(None, src_w, src_h)`` when no orientation correction is needed.

    ``after_w`` / ``after_h`` are the image dimensions **after** the EXIF
    transform — identical to *src_w* / *src_h* for orientations that do not
    swap axes, and swapped for 90 °/270 ° rotations.
    """
    try:
        with Image.open(file_path) as img:
            # Always read the raw file dimensions directly — don't rely on the
            # DB-stored picture.width/height which may not have been populated yet.
            file_w, file_h = img.size
            exif = img.getexif()
            orientation = int(exif.get(_EXIF_ORIENTATION_TAG, 1)) if exif else 1
    except Exception:
        return None, src_w, src_h

    # Use actual file dimensions for all transform maths.
    src_w, src_h = file_w, file_h

    # Orientation 1 (or missing): no transform needed — but still return the
    # correct file dimensions so callers can use them even when src_w/src_h
    # passed in were zero.
    if orientation == 1:
        return None, src_w, src_h

    # For each orientation we return the bbox transform and the post-transform
    # image dimensions.  Orientations 5-8 swap width and height.
    #
    # Maths: for a continuous bbox [x1, y1, x2, y2] with x1<x2, y1<y2,
    # apply the point transform to all four corners and take axis-aligned bounds.

    if orientation == 2:  # FLIP_LEFT_RIGHT: (x,y) -> (W-x, y) — dims unchanged

        def _t2(b: list[int]) -> list[int]:
            x1, y1, x2, y2 = b
            return [src_w - x2, y1, src_w - x1, y2]

        return _t2, src_w, src_h

    if orientation == 3:  # ROTATE_180: (x,y) -> (W-x, H-y) — dims unchanged

        def _t3(b: list[int]) -> list[int]:
            x1, y1, x2, y2 = b
            return [src_w - x2, src_h - y2, src_w - x1, src_h - y1]

        return _t3, src_w, src_h

    if orientation == 4:  # FLIP_TOP_BOTTOM: (x,y) -> (x, H-y) — dims unchanged

        def _t4(b: list[int]) -> list[int]:
            x1, y1, x2, y2 = b
            return [x1, src_h - y2, x2, src_h - y1]

        return _t4, src_w, src_h

    if orientation == 5:  # TRANSPOSE (flip+90°CCW): (x,y) -> (y, x) — swaps dims

        def _t5(b: list[int]) -> list[int]:
            x1, y1, x2, y2 = b
            return [y1, x1, y2, x2]

        return _t5, src_h, src_w

    if orientation == 6:  # ROTATE_270 (90°CW): (x,y) -> (H-y, x) — swaps dims

        def _t6(b: list[int]) -> list[int]:
            x1, y1, x2, y2 = b
            return [src_h - y2, x1, src_h - y1, x2]

        return _t6, src_h, src_w

    if orientation == 7:  # TRANSVERSE (flip+90°CW): (x,y) -> (H-y, W-x) — swaps dims

        def _t7(b: list[int]) -> list[int]:
            x1, y1, x2, y2 = b
            return [src_h - y2, src_w - x2, src_h - y1, src_w - x1]

        return _t7, src_h, src_w

    if orientation == 8:  # ROTATE_90 (90°CCW): (x,y) -> (y, W-x) — swaps dims

        def _t8(b: list[int]) -> list[int]:
            x1, y1, x2, y2 = b
            return [y1, src_w - x2, y2, src_w - x1]

        return _t8, src_h, src_w

    # Unknown orientation value — leave bboxes as-is.
    return None, src_w, src_h


def _load_input_images(
    server,
    picture_ids: list[int],
) -> list[tuple[Picture, Image.Image, str, str]]:
    def fetch_pictures(session: Session, ids: list[int]):
        return session.exec(select(Picture).where(Picture.id.in_(ids))).all()

    pictures = server.vault.db.run_task(fetch_pictures, picture_ids)
    picture_map = {pic.id: pic for pic in pictures if pic.id is not None}

    loaded: list[tuple[Picture, Image.Image, str, str]] = []
    for picture_id in picture_ids:
        pic = picture_map.get(picture_id)
        if pic is None:
            raise ValueError(f"Picture not found: {picture_id}")
        if not pic.file_path:
            raise ValueError(f"Picture missing file path: {picture_id}")
        resolved_path = ImageUtils.resolve_picture_path(
            server.vault.image_root, pic.file_path
        )
        if not resolved_path or not os.path.isfile(resolved_path):
            raise ValueError(f"Picture file missing: {picture_id}")
        frame = ImageUtils.load_image_or_video(resolved_path)
        if frame is None:
            raise ValueError(f"Could not load image/video data: {picture_id}")
        try:
            frame_image = Image.fromarray(frame).convert("RGB")
        except Exception as exc:
            raise ValueError(
                f"Could not convert image/video data to PIL image: {picture_id}"
            ) from exc
        source_format = str(pic.format or "").strip().upper() or "PNG"
        loaded.append((pic, frame_image, source_format, resolved_path))
    return loaded


def _save_output_images(image: Any, source_format: str) -> tuple[bytes, str]:
    normalized = (source_format or "PNG").upper()

    if (
        isinstance(image, tuple)
        and len(image) == 2
        and isinstance(image[0], (bytes, bytearray))
        and isinstance(image[1], str)
    ):
        ext = image[1] if image[1].startswith(".") else f".{image[1]}"
        return bytes(image[0]), ext

    if normalized in _VIDEO_FORMATS:
        ext = f".{normalized.lower()}"
        if isinstance(image, (bytes, bytearray)):
            return bytes(image), ext
        if not isinstance(image, Image.Image):
            raise ValueError(
                "Plugin output for video sources must be PIL image or encoded bytes"
            )
        normalized = "PNG"

    if isinstance(image, (bytes, bytearray)):
        if normalized in {"JPG", "JPEG"}:
            ext = ".jpg"
        elif normalized == "WEBP":
            ext = ".webp"
        elif normalized == "BMP":
            ext = ".bmp"
        elif normalized in {"TIFF", "TIF"}:
            ext = ".tiff"
        else:
            ext = ".png"
        return bytes(image), ext

    if not isinstance(image, Image.Image):
        raise ValueError("Plugin output must be PIL image or encoded bytes")

    if normalized in {"JPG", "JPEG"}:
        ext = ".jpg"
        save_format = "JPEG"
    elif normalized in {"WEBP"}:
        ext = ".webp"
        save_format = "WEBP"
    elif normalized in {"BMP"}:
        ext = ".bmp"
        save_format = "BMP"
    elif normalized in {"TIFF", "TIF"}:
        ext = ".tiff"
        save_format = "TIFF"
    else:
        ext = ".png"
        save_format = "PNG"

    out = image.convert("RGB")
    buf = BytesIO()
    if save_format == "JPEG":
        out.save(buf, format=save_format, quality=95)
    else:
        out.save(buf, format=save_format)
    return buf.getvalue(), ext


def _import_output_images(
    server,
    output_entries: list[tuple[bytes, str]],
) -> tuple[list[int], list[int], list[int]]:
    if not output_entries:
        return [], [], []

    shas = [
        ImageUtils.calculate_hash_from_bytes(image_bytes)
        for image_bytes, _ in output_entries
    ]

    existing = server.vault.db.run_immediate_read_task(
        lambda session: Picture.find(session, pixel_shas=shas, include_unimported=True)
    )
    existing_map = {pic.pixel_sha: pic for pic in existing}

    new_entries = [
        (entry, sha)
        for entry, sha in zip(output_entries, shas)
        if sha not in existing_map
    ]

    new_pictures = []
    for (img_bytes, ext), sha in new_entries:
        picture_uuid = f"{uuid.uuid4()}{ext}"
        new_pictures.append(
            ImageUtils.create_picture_from_bytes(
                image_root_path=server.vault.image_root,
                image_bytes=img_bytes,
                picture_uuid=picture_uuid,
                pixel_sha=sha,
            )
        )

    def persist(session: Session):
        if not new_pictures:
            return []
        session.add_all(new_pictures)
        session.commit()
        for pic in new_pictures:
            session.refresh(pic)
        return new_pictures

    if new_pictures:
        new_pictures = server.vault.db.run_task(persist)

        def mark_imported(session: Session, ids: list[int]):
            now = datetime.utcnow()
            pictures = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
            for pic in pictures:
                if pic.imported_at is None:
                    pic.imported_at = now
                    session.add(pic)
            session.commit()

        server.vault.db.run_task(
            mark_imported,
            [pic.id for pic in new_pictures if pic.id is not None],
        )

    new_ids = [pic.id for pic in new_pictures if pic.id is not None]
    duplicate_ids = [
        pic.id
        for sha in shas
        if (pic := existing_map.get(sha)) is not None and pic.id is not None
    ]

    new_map: dict[str, int] = {}
    for (_entry, sha), pic in zip(new_entries, new_pictures):
        if pic.id is not None:
            new_map[sha] = pic.id

    ordered_output_ids: list[int] = []
    for sha in shas:
        if sha in new_map:
            ordered_output_ids.append(new_map[sha])
            continue
        existing_pic = existing_map.get(sha)
        if existing_pic is not None and existing_pic.id is not None:
            ordered_output_ids.append(existing_pic.id)

    return new_ids, duplicate_ids, ordered_output_ids


def _assign_outputs_to_stack_top(server, stack_id: int, picture_ids: list[int]) -> None:
    if not stack_id or not picture_ids:
        return

    def update_stack(session: Session):
        stack = session.get(PictureStack, stack_id)
        if stack is None:
            return
        pics = session.exec(select(Picture).where(Picture.stack_id == stack_id)).all()
        has_positions = any(pic.stack_position is not None for pic in pics)
        shift = len(picture_ids)
        if has_positions and shift:
            for pic in pics:
                if pic.id in picture_ids:
                    continue
                if pic.stack_position is not None:
                    pic.stack_position += shift
                    session.add(pic)

        for idx, pic_id in enumerate(picture_ids):
            pic = session.get(Picture, pic_id)
            if pic is None:
                continue
            pic.stack_id = stack_id
            pic.stack_position = idx
            session.add(pic)

        stack.updated_at = datetime.utcnow()
        session.add(stack)
        session.commit()

    server.vault.db.run_task(update_stack)


def _propagate_output_picture_sets(
    server,
    source_picture_ids: list[int],
    output_picture_ids: list[int],
) -> None:
    if not source_picture_ids or not output_picture_ids:
        return

    source_to_outputs: dict[int, set[int]] = {}
    for source_id, output_id in zip(source_picture_ids, output_picture_ids):
        if source_id is None or output_id is None:
            continue
        if source_id == output_id:
            continue
        source_to_outputs.setdefault(int(source_id), set()).add(int(output_id))

    if not source_to_outputs:
        return

    source_ids = list(source_to_outputs.keys())
    output_ids = sorted({oid for ids in source_to_outputs.values() for oid in ids})

    def copy_memberships(session: Session):
        source_memberships = session.exec(
            select(PictureSetMember).where(PictureSetMember.picture_id.in_(source_ids))
        ).all()

        source_set_ids: dict[int, set[int]] = {}
        for member in source_memberships:
            source_set_ids.setdefault(int(member.picture_id), set()).add(
                int(member.set_id)
            )

        desired_pairs: set[tuple[int, int]] = set()
        for source_id, out_ids in source_to_outputs.items():
            set_ids = source_set_ids.get(source_id)
            if not set_ids:
                continue
            for out_id in out_ids:
                for set_id in set_ids:
                    desired_pairs.add((set_id, out_id))

        if not desired_pairs:
            return

        desired_set_ids = sorted({set_id for set_id, _ in desired_pairs})
        existing = session.exec(
            select(PictureSetMember).where(
                PictureSetMember.picture_id.in_(output_ids),
                PictureSetMember.set_id.in_(desired_set_ids),
            )
        ).all()
        existing_pairs = {
            (int(member.set_id), int(member.picture_id)) for member in existing
        }

        inserts = [
            PictureSetMember(set_id=set_id, picture_id=picture_id)
            for set_id, picture_id in sorted(desired_pairs - existing_pairs)
        ]
        if inserts:
            session.add_all(inserts)
            session.commit()

    server.vault.db.run_task(copy_memberships)


def _propagate_output_project_memberships(
    server,
    source_picture_ids: list[int],
    output_picture_ids: list[int],
) -> None:
    if not source_picture_ids or not output_picture_ids:
        return

    source_to_outputs: dict[int, set[int]] = {}
    for source_id, output_id in zip(source_picture_ids, output_picture_ids):
        if source_id is None or output_id is None:
            continue
        if source_id == output_id:
            continue
        source_to_outputs.setdefault(int(source_id), set()).add(int(output_id))

    if not source_to_outputs:
        return

    source_ids = list(source_to_outputs.keys())
    output_ids = sorted({oid for ids in source_to_outputs.values() for oid in ids})

    def copy_project_memberships(session: Session):
        source_memberships = session.exec(
            select(PictureProjectMember).where(
                PictureProjectMember.picture_id.in_(source_ids)
            )
        ).all()

        source_project_ids: dict[int, set[int]] = {}
        for member in source_memberships:
            source_project_ids.setdefault(int(member.picture_id), set()).add(
                int(member.project_id)
            )

        desired_pairs: set[tuple[int, int]] = set()
        for source_id, out_ids in source_to_outputs.items():
            project_ids = source_project_ids.get(source_id)
            if not project_ids:
                continue
            for out_id in out_ids:
                for project_id in project_ids:
                    desired_pairs.add((project_id, out_id))

        if not desired_pairs:
            return

        desired_project_ids = sorted({pid for pid, _ in desired_pairs})
        existing = session.exec(
            select(PictureProjectMember).where(
                PictureProjectMember.picture_id.in_(output_ids),
                PictureProjectMember.project_id.in_(desired_project_ids),
            )
        ).all()
        existing_pairs = {
            (int(member.project_id), int(member.picture_id)) for member in existing
        }

        inserts = [
            PictureProjectMember(project_id=project_id, picture_id=picture_id)
            for project_id, picture_id in sorted(desired_pairs - existing_pairs)
        ]
        if inserts:
            session.add_all(inserts)
            session.commit()

    server.vault.db.run_task(copy_project_memberships)


def _set_source_picture_ids_on_new_outputs(
    server,
    source_picture_ids: list[int],
    ordered_output_ids: list[int],
    new_ids: list[int],
) -> None:
    """Set source_picture_id on newly created output pictures.

    Marks each new output picture with the ID of its source so that
    SourceFaceLikenessTask can assign characters via embedding similarity
    once face extraction completes.
    """
    new_id_set = set(new_ids)
    pairs = [
        (out_id, src_id)
        for src_id, out_id in zip(source_picture_ids, ordered_output_ids)
        if out_id in new_id_set and src_id != out_id
    ]
    if not pairs:
        return

    def update(session: Session):
        for out_id, src_id in pairs:
            pic = session.get(Picture, out_id)
            if pic is not None:
                pic.source_picture_id = src_id
                session.add(pic)
        session.commit()

    server.vault.db.run_task(update)


def _copy_face_associations(
    server,
    source_picture_ids: list[int],
    ordered_output_ids: list[int],
    plugin: ImagePlugin | None = None,
    params: dict[str, Any] | None = None,
) -> None:
    """Copy face/character associations from source pictures to their plugin outputs.

    Bounding boxes are transformed using the plugin's ``get_bbox_transform``
    method when available.  Plugins that apply geometric transforms (rotation,
    scaling, …) override that method to return a callable that correctly maps
    each ``[x1, y1, x2, y2]`` bbox from source to output coordinates.  When
    the plugin returns ``None`` the code falls back to a simple proportional
    scale based on the ratio of output to source dimensions.  Sentinel faces
    (``face_index == -1``) and self-to-self mappings are always skipped.
    """
    pairs = [
        (src, out)
        for src, out in zip(source_picture_ids, ordered_output_ids)
        if src is not None and out is not None and src != out
    ]
    if not pairs:
        return

    all_source_ids = list({src for src, _ in pairs})
    all_output_ids = list({out for _, out in pairs})

    def copy_faces(session: Session) -> None:
        # Fetch real (non-sentinel) faces from source pictures.
        source_faces = session.exec(
            select(Face)
            .where(Face.picture_id.in_(all_source_ids))
            .where(Face.face_index != -1)
        ).all()
        if not source_faces:
            return

        # Index source faces by picture_id.
        faces_by_source: dict[int, list[Face]] = {}
        for face in source_faces:
            faces_by_source.setdefault(int(face.picture_id), []).append(face)

        # Fetch source + output pictures to read their dimensions.
        all_ids = list(set(all_source_ids) | set(all_output_ids))
        pictures = session.exec(select(Picture).where(Picture.id.in_(all_ids))).all()
        pic_by_id: dict[int, Picture] = {
            int(p.id): p for p in pictures if p.id is not None
        }

        # Check which (picture_id, frame_index, face_index) combos already exist
        # on the output pictures to avoid UNIQUE constraint violations.
        existing_faces = session.exec(
            select(Face).where(Face.picture_id.in_(all_output_ids))
        ).all()
        existing_keys: set[tuple[int, int, int]] = {
            (int(f.picture_id), int(f.frame_index), int(f.face_index))
            for f in existing_faces
        }

        new_faces: list[Face] = []
        for source_id, output_id in pairs:
            faces = faces_by_source.get(source_id)
            if not faces:
                continue

            source_pic = pic_by_id.get(source_id)
            output_pic = pic_by_id.get(output_id)

            src_w = int(source_pic.width or 0) if source_pic else 0
            src_h = int(source_pic.height or 0) if source_pic else 0
            out_w = int(output_pic.width or 0) if output_pic else 0
            out_h = int(output_pic.height or 0) if output_pic else 0

            # Step 1: account for the EXIF orientation applied by load_image_or_video.
            # Face bboxes are in raw pixel space (cv2.imread ignores EXIF), but the
            # plugin saw — and the output image contains — exif_transpose()'d pixels.
            # We must first map bboxes through the same EXIF transform, then through
            # whatever geometric transform the plugin applies.
            exif_transform = None
            inter_w, inter_h = src_w, src_h  # dimensions after EXIF transform
            if source_pic is not None:
                source_file = ImageUtils.resolve_picture_path(
                    server.vault.image_root, source_pic.file_path
                )
                if source_file:
                    # _get_exif_bbox_transform always reads actual dimensions
                    # from the file, so this also fills in inter_w/inter_h even
                    # when src_w/src_h are 0 (picture.width/height not yet set).
                    exif_transform, inter_w, inter_h = _get_exif_bbox_transform(
                        source_file, src_w, src_h
                    )

            # Step 2: ask the plugin for its geometric bbox transform, using the
            # post-EXIF (intermediate) dimensions as the logical source size.
            bbox_transform = None
            if (
                plugin is not None
                and inter_w > 0
                and inter_h > 0
                and out_w > 0
                and out_h > 0
            ):
                bbox_transform = plugin.get_bbox_transform(
                    params,
                    (inter_w, inter_h),
                    (out_w, out_h),
                )

            # Step 3: fallback — proportional scale when dimensions differ.
            if (
                bbox_transform is None
                and inter_w > 0
                and inter_h > 0
                and out_w > 0
                and out_h > 0
            ):
                if inter_w != out_w or inter_h != out_h:
                    scale_x = out_w / inter_w
                    scale_y = out_h / inter_h

                    def _make_scale_transform(sx: float, sy: float):
                        def _transform(bbox: list[int]) -> list[int]:
                            x1, y1, x2, y2 = bbox
                            return [
                                int(round(x1 * sx)),
                                int(round(y1 * sy)),
                                int(round(x2 * sx)),
                                int(round(y2 * sy)),
                            ]

                        return _transform

                    bbox_transform = _make_scale_transform(scale_x, scale_y)

            for face in faces:
                key = (int(output_id), int(face.frame_index), int(face.face_index))
                if key in existing_keys:
                    continue

                scaled_bbox = None
                if face.bbox and len(face.bbox) == 4:
                    # Apply exif transform first (raw → post-exif space),
                    # then the plugin transform (post-exif → output space).
                    b = face.bbox
                    if exif_transform is not None:
                        b = exif_transform(b)
                    if bbox_transform is not None:
                        b = bbox_transform(b)
                    scaled_bbox = b

                new_face = Face(
                    picture_id=output_id,
                    frame_index=face.frame_index,
                    face_index=face.face_index,
                    character_id=face.character_id,
                    bbox=scaled_bbox,
                    # Copy embedding features so the likeness worker can use them
                    # immediately without re-extracting.
                    features=face.features,
                )
                new_faces.append(new_face)
                existing_keys.add(key)

        if new_faces:
            session.add_all(new_faces)
            session.commit()
            logger.info(
                "Copied %d face association(s) from %d source picture(s) to %d output picture(s).",
                len(new_faces),
                len(all_source_ids),
                len(all_output_ids),
            )

    server.vault.db.run_task(copy_faces)


def apply_plugin_to_pictures(
    server,
    plugin: ImagePlugin,
    picture_ids: list[int],
    parameters: dict[str, Any] | None,
    captions: list[str] | None = None,
    progress_reporter=None,
    error_reporter=None,
) -> dict[str, Any]:
    loaded = _load_input_images(server, picture_ids)

    progress_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    params = parameters or {}

    # Build per-image captions: use caller-supplied list when provided,
    # otherwise fall back to each picture's stored description (or "").
    if captions and len(captions) == len(loaded):
        resolved_captions: list[str] = [str(c or "") for c in captions]
    else:
        resolved_captions = [str(pic.description or "") for pic, *_ in loaded]

    def progress_cb(payload):
        progress_events.append(payload)
        if progress_reporter is not None:
            progress_reporter(payload)

    def error_cb(payload):
        errors.append(payload)
        if error_reporter is not None:
            error_reporter(payload)

    outputs: list[Any] = [None] * len(loaded)
    image_indices: list[int] = []
    image_inputs: list[Image.Image] = []

    for idx, (_pic, pil_image, source_format, source_path) in enumerate(loaded):
        if source_format in _VIDEO_FORMATS and plugin.supports_videos:
            if type(plugin).run_video is not ImagePlugin.run_video:
                outputs[idx] = plugin.run_video(
                    source_path,
                    parameters=params,
                    progress_callback=progress_cb,
                    error_callback=error_cb,
                )
            else:
                image_indices.append(idx)
                image_inputs.append(pil_image)
        else:
            image_indices.append(idx)
            image_inputs.append(pil_image)

    if image_inputs:
        input_captions = [resolved_captions[i] for i in image_indices]
        image_outputs = plugin.run(
            image_inputs,
            parameters=params,
            progress_callback=progress_cb,
            error_callback=error_cb,
            captions=input_captions,
        )
        if len(image_outputs) != len(image_inputs):
            raise ValueError(
                f"Plugin '{plugin.name}' returned {len(image_outputs)} images for {len(image_inputs)} inputs"
            )
        for out_idx, loaded_idx in enumerate(image_indices):
            outputs[loaded_idx] = image_outputs[out_idx]

    if any(output is None for output in outputs):
        raise ValueError(
            f"Plugin '{plugin.name}' did not return outputs for all inputs"
        )

    output_entries: list[tuple[bytes, str]] = []
    source_picture_ids: list[int] = []
    for idx, output in enumerate(outputs):
        pic, _, source_format, _source_path = loaded[idx]
        output_bytes, ext = _save_output_images(output, source_format)
        output_entries.append((output_bytes, ext))
        source_picture_ids.append(pic.id)

    new_ids, duplicate_ids, ordered_output_ids = _import_output_images(
        server, output_entries
    )

    _propagate_output_picture_sets(server, source_picture_ids, ordered_output_ids)
    _propagate_output_project_memberships(server, source_picture_ids, ordered_output_ids)
    _set_source_picture_ids_on_new_outputs(
        server, source_picture_ids, ordered_output_ids, new_ids
    )
    _copy_face_associations(
        server, source_picture_ids, ordered_output_ids, plugin=plugin, params=params
    )

    # Ensure each source picture has a stack (creating one if needed) and collect
    # the stack_id for each source.  Deduplicating so get_or_create_stack is only
    # called once per unique source id — multiple inputs from the same stack are
    # handled by the grouping below.
    unique_source_ids = list(dict.fromkeys(source_picture_ids))
    stack_by_source: dict[int, int | None] = {}
    for source_id in unique_source_ids:
        stack_id = server.vault.db.run_task(get_or_create_stack_for_picture, source_id)
        stack_by_source[source_id] = stack_id

    # Read each source's current stack_position so outputs can be placed at the
    # top in the same relative order as their sources.  Stack positions may have
    # changed after the get_or_create calls above (e.g., new stack was created),
    # so we read them fresh here.
    def _read_source_positions(session: Session, src_ids: list[int]) -> dict[int, int]:
        pics = session.exec(select(Picture).where(Picture.id.in_(src_ids))).all()
        return {
            int(p.id): (
                int(p.stack_position) if p.stack_position is not None else 999999
            )
            for p in pics
            if p.id is not None
        }

    pos_by_source: dict[int, int] = server.vault.db.run_immediate_read_task(
        _read_source_positions, unique_source_ids
    )

    # Group outputs by stack, preserving source-position order as the primary
    # sort key and input order as a stable tiebreaker.  This guarantees that
    # the output derived from the original stack leader ends up at position 0
    # even when multiple members of the same stack were selected and filtered.
    outputs_by_stack: dict[int, list[tuple[int, int, int]]] = {}
    for input_order, (source_id, out_id) in enumerate(
        zip(source_picture_ids, ordered_output_ids)
    ):
        sid = stack_by_source.get(source_id)
        if not sid:
            continue
        src_pos = pos_by_source.get(source_id, input_order)
        outputs_by_stack.setdefault(sid, []).append((src_pos, input_order, out_id))

    for sid, items in outputs_by_stack.items():
        items.sort()
        _assign_outputs_to_stack_top(server, sid, [out_id for _, _, out_id in items])

    return {
        "plugin": plugin.name,
        "picture_ids": picture_ids,
        "created_picture_ids": new_ids,
        "duplicate_picture_ids": duplicate_ids,
        "output_picture_ids": ordered_output_ids,
        "progress": progress_events,
        "errors": errors,
    }
