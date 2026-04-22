"""Task that scans a reference folder and indexes new image files in place."""

import io
import os
import time
from datetime import datetime, timezone

from PIL import Image
from sqlmodel import Session, delete, select

from pixlstash.database import DBPriority
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.db_models.tag import Tag, TAG_EMPTY_SENTINEL
from pixlstash.tasks.base_task import BaseTask
from pixlstash.utils.caption_file_utils import (
    find_caption_file,
    get_caption_file_mtime,
    read_caption_file,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils, THUMBNAIL_EXTENSION
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

_SUPPORTED_IMAGE_EXTS: frozenset[str] = frozenset(
    {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".heic",
        ".heif",
        ".avif",
    }
)


def _is_supported_file(file_path: str) -> bool:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _SUPPORTED_IMAGE_EXTS:
        return True
    return VideoUtils.is_video_file(file_path)


class ReferenceFolderScanTask(BaseTask):
    """Task that scans a single reference folder and indexes new image files in place.

    New files found on disk are inserted as Picture rows with their absolute
    paths so that PixlStash serves them directly from their original location.
    Files that have been removed from disk since the last scan have their DB
    records deleted.

    Phase-2 path validation (resolve mapping → blocklist → isdir/access) is
    performed before any filesystem work.  On failure the folder status is set
    to ``mount_error`` and the task exits without touching the picture table.
    """

    def __init__(
        self,
        database,
        folder_id: int,
        folder_path: str,
        resolved_path: str,
        other_resolved_paths: frozenset[str] = frozenset(),
    ):
        super().__init__(
            task_type="ReferenceFolderScanTask",
            params={
                "folder_id": folder_id,
                "folder_path": folder_path,
                "resolved_path": resolved_path,
            },
        )
        self._db = database
        self._folder_id = folder_id
        self._folder_path = folder_path
        self._resolved_path = resolved_path
        self._other_resolved_paths = other_resolved_paths

    def _run_task(self):
        resolved = self._resolved_path
        folder_id = self._folder_id

        if not os.path.isdir(resolved):
            logger.warning(
                "Reference folder %s (resolved: %s) is not a directory — marking mount_error",
                self._folder_path,
                resolved,
            )
            self._set_status(ReferenceFolderStatus.MOUNT_ERROR)
            return {"status": "mount_error", "folder_id": folder_id}

        if not os.access(resolved, os.R_OK | os.X_OK):
            logger.warning(
                "Reference folder %s (resolved: %s) is not readable — marking mount_error",
                self._folder_path,
                resolved,
            )
            self._set_status(ReferenceFolderStatus.MOUNT_ERROR)
            return {"status": "mount_error", "folder_id": folder_id}

        # Collect all supported files currently on disk.
        # Skip PixlStash-generated thumbnail files (e.g. foo_thumb.webp) that
        # may have been written next to source files by an older version — they
        # are not real pictures and would cause infinite re-indexing churn.
        _thumb_suffix = f"_thumb{THUMBNAIL_EXTENSION}"
        other_roots = self._other_resolved_paths
        disk_paths: set[str] = set()
        for root, dirs, files in os.walk(resolved, topdown=True):
            # Prune subdirectories that are roots of other reference folders so
            # their files are only indexed by their own scan task.
            dirs[:] = [d for d in dirs if os.path.join(root, d) not in other_roots]
            for file_name in files:
                if file_name.endswith(_thumb_suffix):
                    continue
                full_path = os.path.join(root, file_name)
                if _is_supported_file(full_path):
                    disk_paths.add(full_path)

        # Fetch all picture paths already indexed for this reference folder,
        # including scrapheap (deleted=True) pictures.  Scrapheap pictures must
        # be present in existing_by_path so their file paths are subtracted from
        # `new_paths`; without them, the scan would re-import the same file every
        # time it ran while the picture sat in the scrapheap.
        def fetch_existing(session: Session) -> list[Picture]:
            return list(
                session.exec(
                    select(Picture).where(
                        Picture.reference_folder_id == folder_id,
                        Picture.import_excluded.is_(False),
                    )
                ).all()
            )

        existing_pictures: list[Picture] = self._db.run_task(
            fetch_existing, priority=DBPriority.LOW
        )
        existing_by_path: dict[str, Picture] = {
            p.file_path: p for p in existing_pictures if p.file_path
        }

        # Fetch sentinel records: pictures the user permanently removed from the
        # scrapheap when allow_delete_file=False.  Their files are still on disk
        # but must not be re-imported.
        def fetch_sentinels(session: Session) -> list[tuple[str, int]]:
            rows = session.exec(
                select(Picture.file_path, Picture.id).where(
                    Picture.reference_folder_id == folder_id,
                    Picture.import_excluded.is_(True),
                )
            ).all()
            return [(row[0], row[1]) for row in rows if row[0] and row[1] is not None]

        sentinel_items: list[tuple[str, int]] = self._db.run_task(
            fetch_sentinels, priority=DBPriority.LOW
        )
        sentinel_paths: set[str] = {path for path, _ in sentinel_items}

        # Determine what is new and what has been removed.
        new_paths = disk_paths - set(existing_by_path.keys()) - sentinel_paths
        removed_paths = set(existing_by_path.keys()) - disk_paths

        # Clean up sentinel records whose source file has since been removed
        # from disk — the sentinel is no longer needed.
        stale_sentinel_ids = [
            pic_id for path, pic_id in sentinel_items if path not in disk_paths
        ]
        if stale_sentinel_ids:

            def delete_stale_sentinels(session: Session, ids: list[int]) -> None:
                for pic_id in ids:
                    pic = session.get(Picture, pic_id)
                    if pic is not None:
                        session.delete(pic)
                session.commit()

            self._db.run_task(
                delete_stale_sentinels, stale_sentinel_ids, priority=DBPriority.LOW
            )
            logger.info(
                "Reference folder %s: cleaned up %d stale sentinel record(s) "
                "(source files removed from disk).",
                self._folder_path,
                len(stale_sentinel_ids),
            )

        # --- Handle removed files ---
        if removed_paths:
            removed_ids = [
                existing_by_path[p].id
                for p in removed_paths
                if existing_by_path[p].id is not None
            ]

            def delete_removed(session: Session, ids: list[int]) -> None:
                for pic_id in ids:
                    pic = session.get(Picture, pic_id)
                    if pic is not None:
                        session.delete(pic)
                session.commit()

            if removed_ids:
                self._db.run_task(delete_removed, removed_ids, priority=DBPriority.LOW)
                logger.info(
                    "Reference folder %s: removed %d stale picture records.",
                    self._folder_path,
                    len(removed_ids),
                )

        # --- Handle new files ---
        new_pictures: list[Picture] = []
        for file_path in new_paths:
            try:
                pixel_sha = ImageUtils.calculate_hash_from_file_path(file_path)
            except Exception as exc:
                logger.warning(
                    "Reference folder scan: failed to hash %s: %s", file_path, exc
                )
                continue

            try:
                pic = self._build_picture(file_path, pixel_sha, folder_id)
                new_pictures.append(pic)
            except Exception as exc:
                logger.warning(
                    "Reference folder scan: failed to build picture for %s: %s",
                    file_path,
                    exc,
                )

        if new_pictures:

            def insert_pictures(
                session: Session, pictures: list[Picture]
            ) -> list[Picture]:
                session.add_all(pictures)
                session.commit()
                for pic in pictures:
                    session.refresh(pic)
                # Persist sidecar tags collected during _build_picture.
                sidecar_tags_to_add = []
                for pic in pictures:
                    sidecar_tags = getattr(pic, "_sidecar_tags", None)
                    if sidecar_tags and pic.id is not None:
                        for tag_str in sidecar_tags:
                            sidecar_tags_to_add.append(
                                Tag(picture_id=pic.id, tag=tag_str)
                            )
                if sidecar_tags_to_add:
                    session.add_all(sidecar_tags_to_add)
                    session.commit()
                return list(pictures)

            self._db.run_task(insert_pictures, new_pictures, priority=DBPriority.LOW)
            logger.info(
                "Reference folder %s: indexed %d new pictures.",
                self._folder_path,
                len(new_pictures),
            )

        # --- Handle caption file changes for existing pictures ---
        # Compare each existing picture's stored caption_file_mtime against the
        # file's current mtime using a cheap os.stat() call so we only read
        # file content when something has actually changed.
        caption_updates: list[tuple] = []
        for file_path, pic in existing_by_path.items():
            if file_path in removed_paths:
                continue
            if pic.deleted:
                # Don't update caption data for scrapheap pictures.
                continue
            current_caption = find_caption_file(file_path)
            current_mtime = (
                get_caption_file_mtime(current_caption) if current_caption else None
            )
            stored_mtime = pic.caption_file_mtime
            stored_caption = pic.caption_file

            # No sidecar now and none before — nothing to do.
            if current_caption is None and stored_caption is None:
                continue

            # Sidecar has appeared, changed path (ext swap), or its mtime
            # differs from what we last recorded.
            changed = current_caption != stored_caption or current_mtime != stored_mtime
            if not changed:
                continue

            if current_caption is None:
                # Sidecar disappeared — clear stored references.
                caption_updates.append((pic.id, None, None, None, []))
                continue

            new_tags, new_description = read_caption_file(current_caption)
            caption_updates.append(
                (pic.id, current_caption, current_mtime, new_description, new_tags)
            )

        caption_updated_picture_ids: list[int] = []
        if caption_updates:

            def apply_caption_updates(
                session: Session,
                updates: list[tuple],
            ) -> None:
                for pic_id, caption_path, mtime, description, tags in updates:
                    pic_db = session.get(Picture, pic_id)
                    if pic_db is None:
                        continue
                    pic_db.caption_file = caption_path
                    pic_db.caption_file_mtime = mtime
                    if description is not None:
                        pic_db.description = description
                    session.add(pic_db)
                    # Always replace tags — even an empty list means all tags were removed.
                    session.exec(delete(Tag).where(Tag.picture_id == pic_id))
                    if tags:
                        session.add_all([Tag(picture_id=pic_id, tag=t) for t in tags])
                    else:
                        session.add(Tag(picture_id=pic_id, tag=TAG_EMPTY_SENTINEL))
                session.commit()

            self._db.run_task(
                apply_caption_updates, caption_updates, priority=DBPriority.LOW
            )
            caption_updated_picture_ids = [pic_id for pic_id, *_ in caption_updates]
            logger.info(
                "Reference folder %s: updated caption data for %d existing pictures.",
                self._folder_path,
                len(caption_updates),
            )

        self._set_status(ReferenceFolderStatus.ACTIVE, update_last_scanned=True)
        return {
            "status": "active",
            "folder_id": folder_id,
            "new_count": len(new_pictures),
            "removed_count": len(removed_paths),
            "caption_updated_count": len(caption_updates),
            "caption_updated_picture_ids": caption_updated_picture_ids,
        }

    def _build_picture(self, file_path: str, pixel_sha: str, folder_id: int) -> Picture:
        """Read image metadata and build a Picture for a reference folder file.

        Args:
            file_path: Absolute path to the source image file.
            pixel_sha: Pre-computed pixel hash of the file.
            folder_id: Primary key of the owning ReferenceFolder.

        Returns:
            An unsaved Picture instance ready for insertion.
        """
        with open(file_path, "rb") as fh:
            image_bytes = fh.read()

        created_at = ImageUtils.extract_created_at_from_metadata(
            image_bytes, fallback_file_path=file_path
        )

        width = height = None
        img_format = None
        thumbnail_bytes = None

        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                img_format = img.format or "PNG"
                width, height = img.size
                thumbnail_bytes = ImageUtils.generate_thumbnail_bytes(img)
        except Exception:
            logger.warning(
                "Failed to process image file %s for reference folder scan.",
                file_path,
            )

        # Write thumbnail into image_root/.ref_thumbs/ so it doesn't land
        # inside the reference folder and get re-indexed on the next scan.
        if thumbnail_bytes:
            ImageUtils.write_thumbnail_bytes(
                self._db.image_root, file_path, thumbnail_bytes
            )

        size_bytes = os.path.getsize(file_path)

        pic = Picture(
            file_path=file_path,
            reference_folder_id=folder_id,
            pixel_sha=pixel_sha,
            format=img_format,
            width=width,
            height=height,
            size_bytes=size_bytes,
            imported_at=datetime.now(timezone.utc),
            original_file_name=os.path.basename(file_path),
        )
        if created_at:
            pic.created_at = created_at

        # Detect and read sidecar caption file (.txt or .caption).
        caption_path = find_caption_file(file_path)
        if caption_path:
            pic.caption_file = caption_path
            pic.caption_file_mtime = get_caption_file_mtime(caption_path)
            sidecar_tags, sidecar_description = read_caption_file(caption_path)
            if sidecar_description and not pic.description:
                pic.description = sidecar_description
            # Tags are stored via the Tag relationship and cannot be set on the
            # unsaved Picture directly; stash them as a transient attribute so
            # the caller can persist them after the Picture is inserted.
            if sidecar_tags:
                pic._sidecar_tags = sidecar_tags  # type: ignore[attr-defined]

        return pic

    def _set_status(
        self,
        status: str,
        *,
        update_last_scanned: bool = False,
    ) -> None:
        def update(session: Session) -> None:
            rf = session.get(ReferenceFolder, self._folder_id)
            if rf is None:
                return
            rf.status = status
            if update_last_scanned:
                rf.last_scanned = time.time()
            session.add(rf)
            session.commit()

        self._db.run_task(update, priority=DBPriority.LOW)
