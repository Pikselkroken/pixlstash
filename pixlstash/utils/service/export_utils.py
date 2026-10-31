"""ZIP generation and export functionality for pictures and features."""

import hashlib
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import unicodedata
import zipfile

from PIL import Image, ImageOps, PngImagePlugin

from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture, PictureSet
from pixlstash.db_models.picture_set import PictureSetMember
from pixlstash.utils.host_open import open_in_file_manager
from pixlstash.utils.image_processing.generation_data import strip_generation_data
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.service.caption_utils import CaptionUtils
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids
from sqlmodel import select


logger = logging.getLogger(__name__)

# Characters that must never survive into a zip member name. A member name is
# interpreted as a *relative path* by the extracting tool, so a stored
# ``original_file_name`` such as "../../.bashrc" would write outside the
# extraction directory on the recipient's machine (zip slip, CWE-22). The name
# is attacker-influenced at upload time, so it is sanitised at archive-build
# time rather than trusted.
#
# Only genuinely dangerous characters are replaced - control characters, the
# Windows-reserved set, and anything that could be read as a separator. Letters
# outside ASCII are kept, so a legitimate name like "café shot.jpg" survives
# export intact instead of being mangled into "caf_ shot.jpg".
_UNSAFE_ARCNAME_CHARS_RE = re.compile(r'[\x00-\x1f\x7f<>:"|?*/\\]')

# The MS-DOS device names Win32 still resolves ahead of the filesystem. A member
# called ``NUL.jpg`` is not a file on Windows: the open succeeds and the bytes go
# to the null device, so the picture leaves the export and never arrives - the
# same silent loss as a name collision, from a picture whose only crime is being
# called ``nul``. The rule, and why the match is shaped the way it is below:
#
# * case-insensitive, so ``nul`` and ``NuL`` are the device too;
# * an extension does not help - ``NUL.jpg`` and ``NUL.tar.gz`` are both the
#   device, because Win32 looks at the name up to the FIRST dot. That is why the
#   rewrite goes on that first segment rather than on the end of the stem:
#   ``NUL.tar`` -> ``NUL_.tar``, since ``NUL.tar_`` would still start ``NUL.``;
# * trailing spaces and dots are stripped by path normalisation before the name
#   is compared, so ``CON `` and ``CON.`` are the device as well. The caller's
#   ``.strip(". ")`` below removes them from the whole candidate; the
#   ``rstrip(" ")`` here covers the space that sits between the name and its
#   extension, as in ``CON .tar``.
#
# The superscript spellings (``COM¹``) are here because Windows folds them onto
# the digits; they are not asserted against the real OS in the gate, unlike the
# ASCII names, so they are defensive rather than measured.
_WINDOWS_DEVICE_NAME_RE = re.compile(
    r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])\Z",
    re.IGNORECASE,
)

# What ai-toolkit's data loader lists (``image_extensions`` in
# ``toolkit/data_loader.py``). Anything else in a dataset folder is skipped
# without a word, so a training dataset converts the rest to PNG (#2290).
TRAINER_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})

# ai-toolkit loads every picture with ``convert("RGB")``, which drops alpha and
# trains the transparent area as whatever colour sits under it (often black).
# A training dataset flattens alpha onto this neutral grey instead.
TRAINER_ALPHA_BACKGROUND = (128, 128, 128)


def _safe_archive_stem(name: str, fallback: str, *, is_extension: bool = False) -> str:
    """Reduce *name* to a single, safe zip member name component.

    Args:
        name: The candidate name, typically a stored ``original_file_name``.
        fallback: Component to use when *name* sanitises to nothing.
        is_extension: The result is an extension rather than the leading
            component. Skips the reserved-device rewrite, which applies only to
            the name up to the first dot: ``report.aux`` is an ordinary file and
            must not be exported as ``report.aux_``.

    Returns:
        A bare filename component with no path separators, no leading dots, no
        drive letter and no MS-DOS device name, safe to place in a zip member
        name and to write as a file on Windows.
    """
    # Take the last component under both separators: a Windows-style name
    # ("..\\..\\evil") keeps no basename on POSIX, so split on both.
    candidate = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    candidate = _UNSAFE_ARCNAME_CHARS_RE.sub("_", candidate).strip(". ")
    candidate = candidate or fallback
    if not is_extension:
        head, dot, tail = candidate.partition(".")
        head = head.rstrip(" ")
        if _WINDOWS_DEVICE_NAME_RE.fullmatch(head):
            # A trailing underscore can never itself be a device name, so the
            # rewrite is stable and one-to-one; a picture genuinely called
            # "NUL_" collides with it and is separated by _unique_export_stem,
            # which every stem goes through next.
            candidate = f"{head}_{dot}{tail}"
    return candidate


def _unique_export_stem(stem: str, claimed: dict) -> str:
    """Return a member-name stem no earlier member of this export has taken.

    Args:
        stem: The candidate stem, already through :func:`_safe_archive_stem`.
        claimed: Case-folded stem -> highest suffix tried for it. Updated in
            place, so one dict per export run is the whole bookkeeping.

    Returns:
        A stem that is free, ``stem`` itself the first time it is asked for.

    A per-stem counter on its own is not enough, and that is the bug this
    exists for: two pictures named ``photo`` give the second ``photo_2``, which
    silently overwrites a *third* picture actually named ``photo_2`` - a real
    file lost on a folder export, a duplicate member in a ZIP. So every name
    handed out is claimed and the suffix keeps rising until the claim is free.

    The keys are NFC-normalised and case-folded because a folder export writes
    real files, and the filesystems the desktop build ships to answer "same
    path?" more loosely than Python's ``==``. ``Photo.jpg``/``photo.jpg`` are
    one path on Windows NTFS and on default macOS APFS (case-insensitive);
    ``café.jpg`` spelled NFC and NFD is *also* one path on APFS and HFS+,
    which compare normalisation-insensitively - and a name that arrived from a
    Mac is decomposed while the same name typed anywhere else is composed, so
    a library holding both is ordinary. Case-folding alone would hand those two
    the same file name and the second copy would silently replace the first,
    which is the whole bug this function exists for. Reading the previous
    suffix back as the starting point keeps this O(1) per picture rather than
    rescanning the claimed set for every duplicate.
    """

    def _key(value: str) -> str:
        return unicodedata.normalize("NFC", value).casefold()

    key = _key(stem)
    suffix = claimed.get(key, 1)
    candidate = stem
    while _key(candidate) in claimed:
        suffix += 1
        candidate = f"{stem}_{suffix}"
    claimed[key] = suffix
    claimed[_key(candidate)] = suffix
    return candidate


class _FolderSink:
    """Write export members as plain files instead of into a ZIP archive.

    Duck-types the two ``zipfile.ZipFile`` calls the export loop makes -
    ``write(path, arcname=...)`` and ``writestr(arcname, data)`` - so
    ``ExportUtils._write_export_pictures`` runs unchanged whether it is
    packaging a ZIP (:meth:`ExportUtils.generate_zip`) or writing straight
    into a folder (:meth:`ExportUtils.generate_folder_export`). Member names
    are always bare filenames (``_safe_archive_stem``), so no subdirectory
    ever needs creating.
    """

    def __init__(self, root: str):
        self.root = root

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def write(self, full_path: str, arcname: str) -> None:
        shutil.copy2(full_path, os.path.join(self.root, arcname))

    def writestr(self, arcname: str, data) -> None:
        mode = "wb" if isinstance(data, (bytes, bytearray)) else "w"
        kwargs = {} if mode == "wb" else {"encoding": "utf-8"}
        with open(os.path.join(self.root, arcname), mode, **kwargs) as f:
            f.write(data)


_STRIPPABLE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class _StripGenerationDataSink:
    """Wrap an export sink so every image member leaves without generation data.

    The export's "Keep the workflow" off (the Share panel and Export row in
    Settings › Privacy). Wrapping the sink rather than each write site covers
    every branch of :meth:`ExportUtils._write_export_pictures` - the original
    file, a resized re-encode, a trainer PNG, a face crop - in one place. A
    format :func:`strip_generation_data` does not handle (videos, GIF, TIFF)
    goes through unchanged, as it does for share copies.
    """

    def __init__(self, sink):
        self._sink = sink

    @staticmethod
    def _strippable(arcname: str) -> bool:
        return os.path.splitext(arcname)[1].lower() in _STRIPPABLE_EXTS

    @staticmethod
    def _strip(data: bytes, arcname: str, source: str):
        """Stripped bytes, ``None`` for an unhandled format, ``False`` if broken."""
        try:
            return strip_generation_data(data)
        except ValueError as exc:
            # Fail closed: a file whose structure cannot be walked cannot be
            # promised clean, so it is left out rather than sent as it is, and
            # one broken picture does not fail the rest of the export.
            logger.warning(
                "Left %s (%s) out of the export: could not strip its "
                "generation data: %s",
                arcname,
                source,
                exc,
            )
            return False

    def write(self, full_path: str, arcname: str) -> None:
        if not self._strippable(arcname):
            self._sink.write(full_path, arcname=arcname)
            return
        # ponytail: reads the whole image into memory; fine for pictures, a
        # streaming chunk walker if huge originals ever show up here.
        with open(full_path, "rb") as fh:
            stripped = self._strip(fh.read(), arcname, full_path)
        if stripped is False:
            return
        if stripped is None:
            self._sink.write(full_path, arcname=arcname)
            return
        self._sink.writestr(arcname, stripped)

    def writestr(self, arcname: str, data) -> None:
        if isinstance(data, (bytes, bytearray)) and self._strippable(arcname):
            stripped = self._strip(bytes(data), arcname, "re-encoded")
            if stripped is False:
                return
            if stripped is not None:
                data = stripped
        self._sink.writestr(arcname, data)


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
            if getattr(feature, "frame_index", 0) != 0:
                # Measured on a later frame of an animated GIF; img is frame 0.
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
    def _needs_trainer_conversion(full_path, ext) -> bool:
        """Whether ai-toolkit would skip this still or train its alpha wrong.

        Args:
            full_path: The picture's source file.
            ext: Its extension, as the export loop read it.

        Returns:
            True for a format outside :data:`TRAINER_IMAGE_EXTS`, a picture
            with transparency, 16-bit grey (the trainer's ``convert("RGB")``
            clips it to white), or one that cannot be read; False for one the
            trainer reads as it is, and for a video, which the trainer cuts
            frames from itself.
        """
        if VideoUtils.is_video_file(full_path):
            return False
        if ext.lower() not in TRAINER_IMAGE_EXTS:
            return True
        try:
            with Image.open(full_path) as img:
                return (
                    img.mode in {"RGBA", "LA", "PA"}
                    or img.mode.startswith("I;16")
                    or "transparency" in img.info
                )
        except Exception as exc:
            # A still the trainer cannot decode either: the conversion fails
            # too, and leaves it out of the dataset rather than miscounted.
            logger.warning(
                "Could not read %s to check for transparency (%s).", full_path, exc
            )
            return True

    @staticmethod
    def _write_trainer_png(full_path, arcname, sink, scale) -> bool:
        """Write *full_path* as an RGB PNG ai-toolkit trains on as it looks.

        Orientation is baked into the pixels (the EXIF that carried it is not
        written), the colour profile is kept, 16-bit grey is scaled to 8-bit
        and alpha is flattened onto :data:`TRAINER_ALPHA_BACKGROUND`.
        An animated GIF gives its first frame.

        Args:
            full_path: The picture's source file.
            arcname: The member name to write, ending ``.png``.
            sink: The dataset's :class:`_FolderSink`.
            scale: The dataset's resolution factor (1.0 for the original).

        Returns:
            True when the PNG was written; False when the source could not be
            decoded, so the picture is left out of the dataset.
        """
        try:
            with Image.open(full_path) as img:
                icc_profile = img.info.get("icc_profile")
                img = ImageOps.exif_transpose(img)
                if img.mode.startswith("I;16"):
                    # 16-bit grey: convert("RGB") clips every value above 255
                    # to white, so scale it down first. Plain "I" is left
                    # alone: a 32-bit file can hold 0-255 already.
                    img = img.convert("I").point(lambda v: v / 256).convert("L")
                if img.mode in {"RGBA", "LA", "PA"} or "transparency" in img.info:
                    rgba = img.convert("RGBA")
                    img = Image.new("RGB", rgba.size, TRAINER_ALPHA_BACKGROUND)
                    img.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    img = img.convert("RGB")
                ExportUtils._write_image_to_zip(
                    img,
                    arcname,
                    sink,
                    ext=".png",
                    scale=scale,
                    save_kwargs={"icc_profile": icc_profile} if icc_profile else None,
                )
            return True
        except Exception as exc:
            logger.warning(
                "Could not convert %s to %s for the training dataset (%s); "
                "leaving the picture out.",
                full_path,
                arcname,
                exc,
            )
            return False

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
          grid (origin top-left) - resolution-independent, so the export's
          ``resolution`` setting does not affect them;
        - each detection becomes a ``compositional_deconstruction.elements``
          entry of ``type: "obj"`` with its label as ``desc`` (key order
          ``type, bbox, desc`` is significant - the model was trained on a fixed
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
        # Strip unless asked to keep (#2417): the export is a way out like any
        # other share.
        keep_generation_data = bool(background_data.get("keep_generation_data", False))

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
        #   "none"         - no sidecar
        #   "coco-json"    - a COCO-subset {stem}.json (pixel xyxy)
        #   "ideogram-json" - an Ideogram-4 structured-JSON caption {stem}.json
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
            "keep_generation_data": keep_generation_data,
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
    def _fetch_character_picture_ids(server, character_id: int, only_deleted: bool):
        """Resolve a character id to its picture ids via the Face table.

        Every Face row whose ``character_id`` matches contributes its
        ``picture_id``; the distinct set is then filtered to the requested
        lifecycle (deleted vs. live).  Used by the training dataset builder,
        which selects by character; the ZIP/folder routes never pass a
        ``character_id`` here (they filter by it through the list path).
        """

        def _query(session, char_id):
            face_pic_ids = {
                int(r) if not isinstance(r, tuple) else int(r[0])
                for r in session.exec(
                    select(Face.picture_id).where(Face.character_id == char_id)
                ).all()
            }
            if not face_pic_ids:
                return []
            rows = session.exec(
                select(Picture.id).where(
                    Picture.id.in_(face_pic_ids),
                    Picture.deleted.is_(bool(only_deleted)),
                )
            ).all()
            return [int(r) if not isinstance(r, tuple) else int(r[0]) for r in rows]

        return server.vault.db.run_task(_query, int(character_id))

    @staticmethod
    def _gather_export_pictures(server, request, task_id, background_data, params):
        """Resolve the pictures an export task should package.

        Shared by :meth:`generate_zip` and :meth:`generate_folder_export`: the
        same id/set/query/list-filter resolution, token-scope enforcement and
        stack deduplication apply whether the pictures end up in a ZIP or
        written straight into a folder.

        Returns:
            The pictures to export, scope-filtered and stack-deduplicated.
            Empty when nothing matched or the caller's token cannot see any of
            it.
        """
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
                    select(PictureSetMember).where(PictureSetMember.set_id == set_id)
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
        elif background_data.get("character_id") is not None:
            character_id = background_data["character_id"]
            logger.debug("Exporting pictures for character %s", character_id)
            char_pic_ids = ExportUtils._fetch_character_picture_ids(
                server, character_id, only_deleted
            )
            if char_pic_ids:
                pics = server.vault.db.run_task(
                    Picture.find,
                    id=char_pic_ids,
                    select_fields=select_fields,
                    include_deleted=only_deleted,
                )
        elif query:
            logger.debug("Exporting pictures using search query: {}".format(query))

            words = re.findall(r"\b\w+\b", query.lower())
            query_full = "A photo of " + query
            # Encoded on this thread, never inside the database task - see
            # ``Picture.semantic_search`` for why an encode must not hold the
            # DB writer.
            query_embedding = server.vault.generate_text_embedding(query_full)

            def find_by_text(session):
                return [
                    r[0]
                    for r in Picture.semantic_search(
                        session,
                        query_full,
                        words,
                        query_embedding=query_embedding,
                        offset=0,
                        limit=sys.maxsize,
                        threshold=threshold,
                        select_fields=select_fields,
                        only_deleted=only_deleted,
                    )
                ]

            pics = server.vault.db.run_task(find_by_text)
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
                    "destination",
                    "tag_format",
                    "bbox_mode",
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
        # authorised to access before packaging the export.
        # Trusted server-side callers (the training dataset builder) pass
        # request=None and carry no token, so there is no scope to apply.
        if request is not None:
            scope_allowed = fetch_scope_allowed_picture_ids(server, request)
            if scope_allowed is not None and pics:
                pics = [p for p in pics if getattr(p, "id", None) in scope_allowed]

        logger.debug(
            f"Export task {task_id}: {len(pics)} pictures matched before stack "
            "deduplication."
        )

        pics = ExportUtils._deduplicate_stacks(pics)
        logger.debug(
            f"Export task {task_id}: {len(pics)} pictures after stack deduplication."
        )

        return pics

    @staticmethod
    def _prepare_feature_maps_and_total(server, pics, params):
        """Pre-fetch per-picture feature/detection rows and the progress total.

        Shared by :meth:`generate_zip` and :meth:`generate_folder_export`: both
        need the same face/detection lookups and the same "how many items will
        this task report progress for" count before the write loop starts.
        """
        export_type_d = params["export_type_d"]
        bbox_mode_d = params["bbox_mode_d"]

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
                    if getattr(face, "frame_index", 0) != 0:
                        continue
                    total_items += 1

        return feature_faces_by_pic, face_tags_by_face, detections_by_pic, total_items

    @staticmethod
    def _write_export_pictures(
        sink,
        server,
        task_id,
        export_tasks,
        pics,
        params,
        feature_faces_by_pic,
        face_tags_by_face,
        detections_by_pic,
    ):
        """Write every picture (and its sidecars) into *sink*.

        *sink* is anything with a ``zipfile.ZipFile``-shaped ``write(path,
        arcname=...)``/``writestr(arcname, data)`` pair - a real
        ``zipfile.ZipFile`` for :meth:`generate_zip`, or :class:`_FolderSink`
        for :meth:`generate_folder_export`. Neither this loop nor the member
        sidecar helpers it calls care which.

        ``params["keep_generation_data"]`` false strips every image member; a
        caller that never sets it (the training dataset) keeps the files as
        they are.
        """
        if not params.get("keep_generation_data", True):
            sink = _StripGenerationDataSink(sink)
        export_type_d = params["export_type_d"]
        caption_mode_d = params["caption_mode_d"]
        include_character_name_enabled = params["include_character_name_enabled"]
        scale_factor = params["scale_factor"]
        use_original_file_names = params.get("use_original_file_names", False)
        tag_format_d = params.get("tag_format_d", "spaces")
        bbox_mode_d = params.get("bbox_mode_d", "none")
        # Case-folded stem -> highest suffix tried; see _unique_export_stem.
        used_names: dict = {}

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
                        # The stored name is attacker-influenced; keep
                        # only a safe bare component so the member name
                        # cannot escape the extraction directory.
                        orig_stem = _safe_archive_stem(orig_stem, f"image_{idx:05d}")
                        file_ext = _safe_archive_stem(
                            orig_ext or ext,
                            ext.lstrip(".") or "bin",
                            is_extension=True,
                        )
                        file_ext = f".{file_ext.lstrip('.')}"
                        name_stem = _unique_export_stem(orig_stem, used_names)
                        arcname = f"{name_stem}{file_ext}"
                    else:
                        # Claimed from the same set as the original names: one
                        # export mixes the two whenever a picture has no
                        # original_file_name, so a picture literally called
                        # "image_00003" must not land on index 3's fallback.
                        name_stem = _unique_export_stem(f"image_{idx:05d}", used_names)
                        arcname = f"{name_stem}{ext}"
                    if params.get("trainer_images") and (
                        ExportUtils._needs_trainer_conversion(full_path, ext)
                    ):
                        arcname = f"{name_stem}.png"
                        if not ExportUtils._write_trainer_png(
                            full_path, arcname, sink, scale_factor
                        ):
                            continue
                    elif scale_factor < 1.0 and not VideoUtils.is_video_file(full_path):
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
                                                # Skip a non-UTF-8 PNG text
                                                # chunk; dropping an
                                                # undecodable key IS correct.
                                                continue
                                    save_kwargs["pnginfo"] = pnginfo
                                ExportUtils._write_image_to_zip(
                                    img,
                                    arcname,
                                    sink,
                                    ext=ext,
                                    scale=scale_factor,
                                    save_kwargs=save_kwargs,
                                )
                        except Exception as exc:
                            logger.warning(
                                "Failed to resize %s (%s); falling back to original.",
                                full_path,
                                exc,
                            )
                            sink.write(full_path, arcname=arcname)
                    else:
                        sink.write(full_path, arcname=arcname)

                    caption_text = None
                    if caption_mode_d == "description":
                        caption_text = pic.description or ""
                        if not caption_text:
                            caption_text = CaptionUtils.build_tag_caption(
                                pic, tag_format_d
                            )
                    elif caption_mode_d == "tags":
                        caption_text = CaptionUtils.build_tag_caption(pic, tag_format_d)

                    if include_character_name_enabled:
                        character_names = CaptionUtils.build_character_caption(pic)
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

                    # Training datasets lead every caption with the LoRA trigger
                    # word; exports never set it.
                    trigger = params.get("trigger_word")
                    if trigger and caption_mode_d != "none":
                        caption_text = (
                            f"{trigger}, {caption_text}" if caption_text else trigger
                        )

                    if caption_mode_d != "none" and caption_text is not None:
                        sink.writestr(
                            f"{name_stem}.txt",
                            f"{caption_text}\n",
                        )

                    if bbox_mode_d == "coco-json":
                        ExportUtils._write_detection_sidecar(
                            sink,
                            name_stem,
                            arcname,
                            pic,
                            detections_by_pic.get(pic.id, []),
                            scale_factor,
                        )
                    elif bbox_mode_d == "ideogram-json":
                        ExportUtils._write_ideogram_sidecar(
                            sink,
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
                            export_faces = export_type_d == Picture.ExportType.FACE

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
                                    sink,
                                    scale=scale_factor,
                                )
                                export_tasks[task_id]["processed"] += len(faces)
                    except Exception as exc:
                        logger.warning(
                            "Failed to export features for %s (%s).",
                            full_path,
                            exc,
                        )

    @staticmethod
    def _training_pictures(server, set_id, character_id, params, clips=False):
        """The pictures a training dataset of the set or character holds.

        The same skips the writer applies, so a manifest's ids and count
        describe exactly what gets written. Video clips are left out unless
        *clips*: only a recipe that trains on clips is given them.
        """
        pics = ExportUtils._gather_export_pictures(
            server,
            None,
            None,
            {"set_id": set_id, "character_id": character_id},
            params,
        )
        kept = []
        for pic in pics:
            if not getattr(pic, "file_path", None):
                continue
            path = ImageUtils.resolve_picture_path(
                server.vault.image_root, pic.file_path
            )
            if os.path.exists(path) and (clips or not VideoUtils.is_video_file(path)):
                kept.append(pic)
        return kept

    @staticmethod
    def count_training_pictures(
        server, *, set_id=None, character_id=None, clips=False
    ) -> int:
        """How many pictures :meth:`build_training_dataset` would write."""
        params = {
            "only_deleted": False,
            "picture_ids": [],
            "select_fields": Picture.metadata_fields(),
        }
        return len(
            ExportUtils._training_pictures(
                server, set_id, character_id, params, clips=clips
            )
        )

    @staticmethod
    def training_counts(server, *, set_id=None, character_id=None) -> tuple[int, int]:
        """The stills and the video clips a training dataset could hold.

        Returns:
            ``(stills, clips)``: what every recipe trains on, and what a recipe
            that trains on clips adds to it.
        """
        params = {
            "only_deleted": False,
            "picture_ids": [],
            "select_fields": Picture.metadata_fields(),
        }
        pics = ExportUtils._training_pictures(
            server, set_id, character_id, params, clips=True
        )
        clips = sum(1 for pic in pics if VideoUtils.is_video_file(pic.file_path))
        return len(pics) - clips, clips

    @staticmethod
    def training_captions(
        server, *, set_id=None, character_id=None
    ) -> tuple[list[str], list[list[str]]]:
        """The captions and tags of the pictures a training dataset holds.

        Returns:
            ``(captions, tags)``, one entry per picture in id order; a picture
            with no caption has ``""``.
        """
        # ponytail: reads every picture's row and stats its file on each call,
        # like count_training_pictures; cache per set if large sets make the
        # Train dialog slow to open.
        pics = ExportUtils._training_rows(server, set_id, character_id)
        return ExportUtils._captions_and_tags(pics)

    @staticmethod
    def _training_rows(server, set_id, character_id, clips=False) -> list:
        """The dataset's pictures with their tags, in id order."""
        params = {
            "only_deleted": False,
            "picture_ids": [],
            "select_fields": Picture.metadata_fields() | {"tags"},
        }
        return sorted(
            ExportUtils._training_pictures(
                server, set_id, character_id, params, clips=clips
            ),
            key=lambda pic: pic.id,
        )

    @staticmethod
    def _captions_and_tags(pics) -> tuple[list[str], list[list[str]]]:
        captions = [pic.description or "" for pic in pics]
        tags = [CaptionUtils.build_tag_caption(pic).split(", ") for pic in pics]
        return captions, [[t for t in row if t] for row in tags]

    @staticmethod
    def training_facts(server, *, set_id=None, character_id=None) -> dict:
        """What the Train dialog checks before a run of the set or character.

        Returns:
            ``{"count", "clips", "untagged", "near_duplicates",
            "other_people"}``: the picture count (stills), the video clips a
            recipe that trains on clips adds to them, how many pictures have
            no tags (the dataset's captions are the tags, so those train on
            the trigger word alone), how many have a near copy in the dataset,
            and how many show a face named as somebody other than
            *character_id* (0 without one).
        """
        # Local import to break a cycle: dedup_tier_service imports
        # set_lock_service → stack_membership → pixlstash.utils.service, whose
        # __init__ imports this module.
        from pixlstash.services.dedup_tier_service import (
            DEFAULT_THRESHOLD,
            NearBucket,
            near_pairs_in_bucket,
        )

        rows = ExportUtils._training_rows(server, set_id, character_id, clips=True)
        pics = [pic for pic in rows if not VideoUtils.is_video_file(pic.file_path)]
        _, tags = ExportUtils._captions_and_tags(pics)
        ids = [pic.id for pic in pics]

        def near_and_others(session):
            pairs = near_pairs_in_bucket(
                session,
                NearBucket("training", "dataset", tuple(ids)),
                DEFAULT_THRESHOLD,
            )
            near = {pid for pair in pairs for pid in pair[:2]}
            others = 0
            if character_id is not None and ids:
                others = len(
                    set(
                        session.exec(
                            select(Face.picture_id).where(
                                Face.picture_id.in_(ids),
                                Face.character_id.is_not(None),
                                Face.character_id != int(character_id),
                            )
                        ).all()
                    )
                )
            return len(near), others

        near, others = server.vault.db.run_task(near_and_others)
        return {
            "count": len(ids),
            "clips": len(rows) - len(pics),
            "untagged": sum(1 for row in tags if not row),
            "near_duplicates": near,
            "other_people": others,
        }

    @staticmethod
    def build_training_dataset(
        server,
        *,
        dest_dir,
        set_id=None,
        character_id=None,
        trigger_word=None,
        caption_mode="tags",
        resolution="original",
        tag_format="spaces",
        clips=False,
    ):
        """Build an ai-toolkit training dataset snapshot into ``dest_dir``.

        Runs the same selection, scope-free source resolution and member
        writer as the folder export (:meth:`generate_folder_export`), so the
        files match a FULL export made with the same settings: one
        ``image_NNNNN.<ext>`` plus ``image_NNNNN.txt`` caption per picture.
        Pictures whose source file is missing are skipped, and so are videos
        unless ``clips``: those are copied as they are, and ai-toolkit cuts
        its frames from them. A still ai-toolkit would skip (anything outside
        :data:`TRAINER_IMAGE_EXTS`) or whose alpha it would train as black is
        written as ``image_NNNNN.png`` instead, alpha flattened onto
        :data:`TRAINER_ALPHA_BACKGROUND`; one that cannot be decoded is left
        out, and the manifest counts only what was written. The rest are
        copied as they are, or resized at a reduced ``resolution``.

        Captions default to the picture's tags.  When ``trigger_word`` is
        given it leads every caption as its first comma-separated token.

        The caller owns ``dest_dir``'s lifecycle (cleanup); this creates it if
        absent and returns a manifest whose ``picture_ids`` feed the
        training run reproducibility record (the hub ``training_run`` row).

        Args:
            server: The server instance (provides ``vault.db`` / ``image_root``).
            dest_dir: Target directory for the image + caption pairs.
            set_id: Picture-set id to build the dataset from.
            character_id: Character id to build the dataset from (resolved via
                the Face table).  One of ``set_id`` / ``character_id`` is required.
            trigger_word: Optional LoRA trigger word, prepended to each caption.
            caption_mode: ``"tags"`` (default), ``"description"``, or ``"none"``.
            resolution: ``"original"`` (default), ``"half"``, or ``"quarter"``.
            tag_format: ``"spaces"`` (default) or ``"underscores"``.
            clips: Whether the set's video clips go in beside its stills (a
                recipe that trains on clips,
                :func:`pixlstash.training.presets.trains_clips`).

        Returns:
            Manifest dict: ``{"dest_dir", "image_count", "picture_ids",
            "caption_mode", "trigger_word"}``.
        """
        if set_id is None and character_id is None:
            raise ValueError("build_training_dataset needs a set_id or a character_id")

        caption_mode_d = (caption_mode or "tags").lower()
        if caption_mode_d not in {"none", "description", "tags"}:
            caption_mode_d = "tags"

        tag_format_d = (
            tag_format if tag_format in {"spaces", "underscores"} else "spaces"
        )

        resolution_d = (resolution or "original").lower()
        scale_map = {"original": 1.0, "half": 0.5, "quarter": 0.25}
        scale_factor = scale_map.get(resolution_d, 1.0)

        trigger = trigger_word.strip() if trigger_word else None

        select_fields = Picture.metadata_fields()
        if caption_mode_d != "none":
            select_fields = select_fields | {"tags"}

        params = {
            "export_type_d": Picture.ExportType.FULL,
            "caption_mode_d": caption_mode_d,
            "include_character_name_enabled": False,
            "scale_factor": scale_factor,
            "only_deleted": False,
            "picture_ids": [],
            "select_fields": select_fields,
            "use_original_file_names": False,
            "tag_format_d": tag_format_d,
            "bbox_mode_d": "none",
            "trigger_word": trigger,
            "trainer_images": True,
        }

        pics = ExportUtils._training_pictures(
            server, set_id, character_id, params, clips=clips
        )

        feature_faces_by_pic, face_tags_by_face, detections_by_pic, _ = (
            ExportUtils._prepare_feature_maps_and_total(server, pics, params)
        )

        os.makedirs(dest_dir, exist_ok=True)
        progress = {None: {"processed": 0}}
        with _FolderSink(dest_dir) as sink:
            ExportUtils._write_export_pictures(
                sink,
                server,
                None,
                progress,
                pics,
                params,
                feature_faces_by_pic,
                face_tags_by_face,
                detections_by_pic,
            )

        # Counted from the files written, so a picture the writer could not
        # convert is in neither the count nor the snapshot (#2290).
        items = ExportUtils._training_dataset_items(dest_dir, pics)
        manifest = {
            "dest_dir": dest_dir,
            "image_count": len(items),
            "picture_ids": [item["picture_id"] for item in items],
            "caption_mode": caption_mode_d,
            "trigger_word": trigger,
            "items": items,
        }
        logger.info(
            "Built training dataset in %s: %d images, caption_mode=%s, trigger=%r",
            dest_dir,
            len(items),
            caption_mode_d,
            trigger,
        )
        return manifest

    @staticmethod
    def _training_dataset_items(dest_dir: str, pics) -> list[dict]:
        """What the trainer was given, read back from the files just written.

        One item per picture that has a file in *dest_dir*, in dataset order:
        its ``file_name``, the ``sha256`` of those bytes and the ``caption``
        file's text (None when none was written). A picture skipped because
        its source was missing has no file and no item. Training datasets are
        never named from the originals, so picture *n* is ``image_<n>``.

        Args:
            dest_dir: The dataset directory the writer filled.
            pics: The pictures in the order the writer numbered them.

        Returns:
            The items the dataset snapshot records (#2123).
        """
        image_by_stem = {}
        for name in os.listdir(dest_dir):
            stem, ext = os.path.splitext(name)
            if ext.lower() != ".txt":
                image_by_stem[stem] = name
        items = []
        for idx, pic in enumerate(pics, start=1):
            stem = f"image_{idx:05d}"
            file_name = image_by_stem.get(stem)
            if file_name is None:
                continue
            with open(os.path.join(dest_dir, file_name), "rb") as fh:
                sha256 = hashlib.file_digest(fh, "sha256").hexdigest()
            caption = None
            caption_path = os.path.join(dest_dir, f"{stem}.txt")
            if os.path.isfile(caption_path):
                with open(caption_path, encoding="utf-8") as fh:
                    caption = fh.read().removesuffix("\n")
            items.append(
                {
                    "picture_id": pic.id,
                    "file_name": file_name,
                    "sha256": sha256,
                    "caption": caption,
                }
            )
        return items

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
        temp_export_dir = None
        try:
            params = ExportUtils._parse_export_params(request, background_data)

            pics = ExportUtils._gather_export_pictures(
                server, request, task_id, background_data, params
            )
            if not pics:
                export_tasks[task_id]["status"] = "failed"
                return

            set_id = background_data.get("set_id")
            query = background_data.get("query")

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

            temp_export_dir = tempfile.mkdtemp(prefix=f"pixlstash_export_{task_id}_")
            os.chmod(temp_export_dir, 0o700)
            zip_path = os.path.join(temp_export_dir, f"export_{task_id}.zip")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(zip_path, flags, 0o600)
            os.close(fd)

            (
                feature_faces_by_pic,
                face_tags_by_face,
                detections_by_pic,
                total_items,
            ) = ExportUtils._prepare_feature_maps_and_total(server, pics, params)

            export_tasks[task_id]["total"] = total_items
            export_tasks[task_id]["processed"] = 0

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                ExportUtils._write_export_pictures(
                    zip_file,
                    server,
                    task_id,
                    export_tasks,
                    pics,
                    params,
                    feature_faces_by_pic,
                    face_tags_by_face,
                    detections_by_pic,
                )

            zip_size = os.path.getsize(zip_path)
            logger.debug(
                f"Export task {task_id}: ZIP file created with size {zip_size} bytes."
            )

            export_tasks[task_id]["status"] = "completed"
            export_tasks[task_id]["file_path"] = zip_path
            export_tasks[task_id]["private_dir"] = temp_export_dir
        except Exception as exc:
            export_tasks[task_id]["status"] = "failed"
            if temp_export_dir is not None:
                shutil.rmtree(temp_export_dir, ignore_errors=True)
            logger.error(f"Export task {task_id} failed: {exc}")

    @staticmethod
    def generate_folder_export(server, request, task_id, export_tasks, background_data):
        """
        Write pictures straight into a folder on the local machine (#291).

        The folder counterpart to :meth:`generate_zip`: a local owner already
        has the destination mounted, so packaging into a ZIP and downloading it
        back onto the same disk is pure overhead. Opens the destination in the
        host file manager once every picture is written, the same courtesy
        ``pixlstash/utils/host_open.py`` gives every other host-capability
        route.

        Args:
            server: The server instance.
            request: The FastAPI request.
            task_id: The export task ID.
            export_tasks: The export_tasks dict (for progress/status).
            background_data: Same as :meth:`generate_zip`, plus ``destination``
                - an existing, writable directory on the server's own disk.
        """
        try:
            destination = background_data.get("destination")
            if not destination or not os.path.isdir(destination):
                export_tasks[task_id]["status"] = "failed"
                logger.error(
                    "Export task %s: destination %r is not a directory.",
                    task_id,
                    destination,
                )
                return

            params = ExportUtils._parse_export_params(request, background_data)

            pics = ExportUtils._gather_export_pictures(
                server, request, task_id, background_data, params
            )
            if not pics:
                export_tasks[task_id]["status"] = "failed"
                return

            (
                feature_faces_by_pic,
                face_tags_by_face,
                detections_by_pic,
                total_items,
            ) = ExportUtils._prepare_feature_maps_and_total(server, pics, params)

            export_tasks[task_id]["total"] = total_items
            export_tasks[task_id]["processed"] = 0

            with _FolderSink(destination) as sink:
                ExportUtils._write_export_pictures(
                    sink,
                    server,
                    task_id,
                    export_tasks,
                    pics,
                    params,
                    feature_faces_by_pic,
                    face_tags_by_face,
                    detections_by_pic,
                )

            logger.debug(
                f"Export task {task_id}: {len(pics)} pictures written to {destination}."
            )

            # Named rather than swallowed (mirrors models/{id}/open-location):
            # the export itself already succeeded, so this cannot fail the
            # task, but a host with no desktop session left the pictures
            # sitting there with nothing telling the caller to go look.
            opened = open_in_file_manager(destination)
            if not opened:
                logger.warning(
                    "Export task %s: wrote to %s but could not open it in the "
                    "host file manager.",
                    task_id,
                    destination,
                )

            export_tasks[task_id]["status"] = "completed"
            export_tasks[task_id]["opened"] = opened
        except Exception as exc:
            export_tasks[task_id]["status"] = "failed"
            logger.error(f"Export task {task_id} failed: {exc}")
