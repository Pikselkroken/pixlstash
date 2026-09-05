from __future__ import annotations

import os
import uuid
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import ExifTags, Image, PngImagePlugin
from sqlmodel import Session, select

from pixlstash.db_models import (
    Picture,
    PictureProjectMember,
    PictureSetMember,
    PictureStack,
    Tag,
    TAG_PENDING_SENTINEL,
)
from pixlstash.image_plugins.base import ImagePlugin
from pixlstash.services.set_lock_service import drop_locked_set_ids
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.pixl_logging import get_logger
from pixlstash.services.layout_move_service import resolve_placement
from pixlstash.stacking import (
    get_or_create_stack_for_picture,
    normalize_stack_positions,
)

logger = get_logger(__name__)

_VIDEO_FORMATS = {"MP4", "WEBM", "MOV", "AVI", "MKV"}

# Standard TIFF/EXIF tag id for the Orientation field.
_EXIF_ORIENTATION_TAG = 0x0112

# EXIF fields that describe the *source's* pixel geometry. A plugin is allowed
# to change geometry (scaling upscales, rotate swaps the axes), so carrying
# these onto the output would publish a measurement that is simply wrong.
_EXIF_GEOMETRY_TAGS = (0x0100, 0x0101)  # ImageWidth, ImageLength (IFD0)
_EXIF_SUBIFD_GEOMETRY_TAGS = (0xA002, 0xA003)  # PixelXDimension, PixelYDimension


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


def _source_png_text(source_path: str | None) -> PngImagePlugin.PngInfo | None:
    """Rebuild the source PNG's text chunks so provenance follows the output.

    ``metadata["png"]["workflow"]`` / ``["prompt"]`` - the ComfyUI graph this
    product recovers in :mod:`pixlstash.utils.comfyui_utilities` - live in those
    chunks and nowhere else, so a plugin run that dropped them destroyed them.
    Returns ``None`` when the source has no text chunks; never fabricates.
    """
    if not source_path:
        return None
    try:
        with Image.open(source_path) as src:
            text = dict(getattr(src, "text", None) or {})
    except Exception as exc:
        logger.warning(
            "Could not read PNG text chunks from %s; plugin output will carry no "
            "embedded metadata: %s",
            source_path,
            exc,
        )
        return None
    if not text:
        return None
    info = PngImagePlugin.PngInfo()
    for key, value in text.items():
        info.add_text(str(key), str(value))
    return info


def _source_exif_bytes(source_path: str | None) -> bytes | None:
    """Return the source's EXIF minus orientation and geometry, or ``None``.

    Orientation must go: :meth:`ImageUtils.load_image_or_video` runs
    ``ImageOps.exif_transpose`` on load, so the pixels a plugin hands back are
    already upright and re-stamping the source's orientation would rotate the
    output a second time on display.
    """
    if not source_path:
        return None
    try:
        with Image.open(source_path) as src:
            exif = src.getexif()
            if not exif:
                return None
            for tag in (_EXIF_ORIENTATION_TAG, *_EXIF_GEOMETRY_TAGS):
                exif.pop(tag, None)
            sub_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            for tag in _EXIF_SUBIFD_GEOMETRY_TAGS:
                sub_ifd.pop(tag, None)
            return exif.tobytes()
    except Exception as exc:
        logger.warning(
            "Could not read EXIF from %s; plugin output will carry no embedded "
            "metadata: %s",
            source_path,
            exc,
        )
        return None


def _save_output_images(
    image: Any, source_format: str, source_path: str | None = None
) -> tuple[bytes, str]:
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
        # A video frame saved as PNG has no still-image metadata to inherit.
        source_path = None

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
    save_kwargs: dict[str, Any] = {}
    if save_format == "PNG":
        pnginfo = _source_png_text(source_path)
        if pnginfo is not None:
            save_kwargs["pnginfo"] = pnginfo
    elif save_format in {"JPEG", "WEBP"}:
        exif_bytes = _source_exif_bytes(source_path)
        if exif_bytes:
            save_kwargs["exif"] = exif_bytes
    if save_format == "JPEG":
        save_kwargs["quality"] = 95
    out.save(buf, format=save_format, **save_kwargs)
    return buf.getvalue(), ext


def _unique_edit_filename(output_dir: str, stem: str, ext: str) -> str:
    """Return ``{stem}_edit{n}{ext}`` with the first n >= 1 that is unused in output_dir."""
    n = 1
    while True:
        candidate = f"{stem}_edit{n}{ext}"
        if not os.path.exists(os.path.join(output_dir, candidate)):
            return candidate
        n += 1


def _import_output_images(
    server,
    output_entries: list[tuple[bytes, str]],
    output_dirs: list[str | None] | None = None,
    reference_folder_ids: list[int | None] | None = None,
    source_file_names: list[str | None] | None = None,
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
        (orig_idx, entry, sha)
        for orig_idx, (entry, sha) in enumerate(zip(output_entries, shas))
        if sha not in existing_map
    ]

    new_pictures = []
    for orig_idx, (img_bytes, ext), sha in new_entries:
        out_dir = output_dirs[orig_idx] if output_dirs else None
        ref_id = reference_folder_ids[orig_idx] if reference_folder_ids else None
        source_stem = source_file_names[orig_idx] if source_file_names else None
        if out_dir and source_stem:
            picture_uuid = _unique_edit_filename(out_dir, source_stem, ext)
        else:
            picture_uuid = f"{uuid.uuid4()}{ext}"
        new_pictures.append(
            ImageUtils.create_picture_from_bytes(
                image_root_path=server.vault.image_root,
                image_bytes=img_bytes,
                picture_uuid=picture_uuid,
                pixel_sha=sha,
                output_dir=out_dir,
                reference_folder_id=ref_id,
                # Placement on write (v1.11 Phase 4b). `None` whenever
                # ``out_dir`` is set - an edit written beside its original in a
                # reference folder is already where the owner put it. Otherwise
                # the unfiled folder, and the memberships this service copies
                # from the source picture file it one debounce later.
                subfolder=resolve_placement(server.vault.db, out_dir),
            )
        )

    def persist(session: Session):
        if not new_pictures:
            return []
        session.add_all(new_pictures)
        session.flush()
        for pic in new_pictures:
            session.add(Tag(tag=TAG_PENDING_SENTINEL, picture_id=pic.id))
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
    for (_orig_idx, _entry, sha), pic in zip(new_entries, new_pictures):
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

        # Guarantee a contiguous 0-based ordering (and a position-0 leader for
        # the grid) regardless of any pre-existing NULL/gapped positions.
        normalize_stack_positions(session, stack_id)

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

        # A locked set's membership cannot change. An upscale/edit run is a
        # propagation path, not an explicit set edit, so locked target sets are
        # dropped (and logged) while the unlocked ones still propagate - failing
        # the run would discard outputs the user did ask for.
        allowed_set_ids = set(
            drop_locked_set_ids(
                session,
                {set_id for set_id, _ in desired_pairs},
                "copy plugin outputs into the source picture's sets",
                picture_ids=output_ids,
            )
        )
        desired_pairs = {
            (set_id, out_id)
            for set_id, out_id in desired_pairs
            if set_id in allowed_set_ids
        }
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


def apply_plugin_to_pictures(
    server,
    plugin: ImagePlugin,
    picture_ids: list[int],
    parameters: dict[str, Any] | None,
    captions: list[str] | None = None,
    progress_reporter=None,
    error_reporter=None,
    stack: bool = True,
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
    output_dirs: list[str | None] = []
    reference_folder_ids: list[int | None] = []
    source_file_names: list[str | None] = []
    source_picture_ids: list[int] = []
    for idx, output in enumerate(outputs):
        pic, _, source_format, source_path = loaded[idx]
        # The in-memory PIL image came from ``Image.fromarray`` and so has an
        # empty ``.info``; embedded metadata has to be re-read from the file.
        output_bytes, ext = _save_output_images(output, source_format, source_path)
        output_entries.append((output_bytes, ext))
        source_picture_ids.append(pic.id)
        if (
            pic.reference_folder_id is not None
            and pic.file_path
            and os.path.isabs(pic.file_path)
        ):
            output_dirs.append(os.path.dirname(pic.file_path))
            reference_folder_ids.append(pic.reference_folder_id)
            raw = pic.original_file_name or os.path.basename(pic.file_path)
            source_file_names.append(os.path.splitext(raw)[0] if raw else None)
        else:
            output_dirs.append(None)
            reference_folder_ids.append(None)
            source_file_names.append(None)

    new_ids, duplicate_ids, ordered_output_ids = _import_output_images(
        server,
        output_entries,
        output_dirs=output_dirs,
        reference_folder_ids=reference_folder_ids,
        source_file_names=source_file_names,
    )

    _propagate_output_picture_sets(server, source_picture_ids, ordered_output_ids)
    _propagate_output_project_memberships(
        server, source_picture_ids, ordered_output_ids
    )
    # Setting source_picture_id above is the WHOLE face story for an output.
    # `MissingFaceExtractionFinder` detects the output's real faces, then
    # `MissingSourceFaceLikenessCharacterFinder` sees a picture with both a
    # source_picture_id and an extracted embedding and runs
    # `SourceFaceLikenessTask`, which inherits a character from the source only
    # where the two faces actually match at >= 0.7 and then clears the marker.
    #
    # Copying the source's face rows here as a shortcut is what this used to do,
    # and it was wrong twice over: a bbox is pixel coordinates, so on an output
    # of a different size the source's numbers describe a different region (on a
    # much larger canvas they collapse into the top-left corner and capture
    # nothing), and copying `features` asserts the output contains that person
    # without ever looking at the output's pixels. Real detection plus a
    # similarity gate answers both questions properly, and it costs one
    # extraction pass the finder was going to be able to do anyway.
    _set_source_picture_ids_on_new_outputs(
        server, source_picture_ids, ordered_output_ids, new_ids
    )

    # Physical stacking is optional. Associations above (set/project/source/face)
    # always run; only the stack placement below is gated. When stack is False,
    # outputs are imported and associated but never placed in a stack.
    if stack:
        # Ensure each source picture has a stack (creating one if needed) and
        # collect the stack_id for each source.  Deduplicating so
        # get_or_create_stack is only called once per unique source id - multiple
        # inputs from the same stack are handled by the grouping below.
        unique_source_ids = list(dict.fromkeys(source_picture_ids))
        stack_by_source: dict[int, int | None] = {}
        for source_id in unique_source_ids:
            stack_id = server.vault.db.run_task(
                get_or_create_stack_for_picture, source_id
            )
            stack_by_source[source_id] = stack_id

        # Read each source's current stack_position so outputs can be placed at
        # the top in the same relative order as their sources.  Stack positions
        # may have changed after the get_or_create calls above (e.g., new stack
        # was created), so we read them fresh here.
        def _read_source_positions(
            session: Session, src_ids: list[int]
        ) -> dict[int, int]:
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
            _assign_outputs_to_stack_top(
                server, sid, [out_id for _, _, out_id in items]
            )

    return {
        "plugin": plugin.name,
        "picture_ids": picture_ids,
        "created_picture_ids": new_ids,
        "duplicate_picture_ids": duplicate_ids,
        "output_picture_ids": ordered_output_ids,
        "progress": progress_events,
        "errors": errors,
    }
