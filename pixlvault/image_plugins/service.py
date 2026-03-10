from __future__ import annotations

import os
import uuid
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import Image
from sqlmodel import Session, select

from pixlvault.db_models import Picture, PictureSetMember, PictureStack
from pixlvault.image_plugins.base import ImagePlugin
from pixlvault.utils.image_processing.image_utils import ImageUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.stacking import get_or_create_stack_for_picture

logger = get_logger(__name__)

_VIDEO_FORMATS = {"MP4", "WEBM", "MOV", "AVI", "MKV"}


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

    for source_id, out_id in zip(source_picture_ids, ordered_output_ids):
        stack_id = server.vault.db.run_task(get_or_create_stack_for_picture, source_id)
        if stack_id:
            _assign_outputs_to_stack_top(server, stack_id, [out_id])

    return {
        "plugin": plugin.name,
        "picture_ids": picture_ids,
        "created_picture_ids": new_ids,
        "duplicate_picture_ids": duplicate_ids,
        "output_picture_ids": ordered_output_ids,
        "progress": progress_events,
        "errors": errors,
    }
