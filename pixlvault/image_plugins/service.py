from __future__ import annotations

import os
import uuid
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import Image
from sqlmodel import Session, select

from pixlvault.db_models import Picture, PictureStack
from pixlvault.image_plugins.base import ImagePlugin
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.stacking import get_or_create_stack_for_picture

logger = get_logger(__name__)


def _load_input_images(
    server,
    picture_ids: list[int],
) -> list[tuple[Picture, Image.Image, str]]:
    def fetch_pictures(session: Session, ids: list[int]):
        return session.exec(select(Picture).where(Picture.id.in_(ids))).all()

    pictures = server.vault.db.run_task(fetch_pictures, picture_ids)
    picture_map = {pic.id: pic for pic in pictures if pic.id is not None}

    loaded: list[tuple[Picture, Image.Image, str]] = []
    for picture_id in picture_ids:
        pic = picture_map.get(picture_id)
        if pic is None:
            raise ValueError(f"Picture not found: {picture_id}")
        if not pic.file_path:
            raise ValueError(f"Picture missing file path: {picture_id}")
        resolved_path = PictureUtils.resolve_picture_path(
            server.vault.image_root, pic.file_path
        )
        if not resolved_path or not os.path.isfile(resolved_path):
            raise ValueError(f"Picture file missing: {picture_id}")
        if str(pic.format or "").upper() in {"MP4", "MOV", "WEBM", "AVI", "MKV"}:
            raise ValueError(f"Picture format not supported by plugins: {picture_id}")
        with open(resolved_path, "rb") as handle:
            source_bytes = handle.read()
        with Image.open(BytesIO(source_bytes)) as img:
            loaded.append((pic, img.convert("RGB"), img.format or "PNG"))
    return loaded


def _serialize_image_bytes(image: Image.Image, source_format: str) -> tuple[bytes, str]:
    normalized = (source_format or "PNG").upper()
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
        PictureUtils.calculate_hash_from_bytes(image_bytes)
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
            PictureUtils.create_picture_from_bytes(
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


def apply_plugin_to_pictures(
    server,
    plugin: ImagePlugin,
    picture_ids: list[int],
    parameters: dict[str, Any] | None,
) -> dict[str, Any]:
    loaded = _load_input_images(server, picture_ids)
    input_images = [entry[1] for entry in loaded]

    progress_events: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    outputs = plugin.run(
        input_images,
        parameters=parameters or {},
        progress_callback=lambda payload: progress_events.append(payload),
        error_callback=lambda payload: errors.append(payload),
    )

    if len(outputs) != len(loaded):
        raise ValueError(
            f"Plugin '{plugin.name}' returned {len(outputs)} images for {len(loaded)} inputs"
        )

    output_entries: list[tuple[bytes, str]] = []
    source_picture_ids: list[int] = []
    for idx, output in enumerate(outputs):
        pic, _, source_format = loaded[idx]
        output_bytes, ext = _serialize_image_bytes(output, source_format)
        output_entries.append((output_bytes, ext))
        source_picture_ids.append(pic.id)

    new_ids, duplicate_ids, ordered_output_ids = _import_output_images(
        server, output_entries
    )

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
