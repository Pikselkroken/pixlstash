"""Task that scans a reference folder and indexes new image files in place."""

import concurrent.futures
import io
import os
import time
from datetime import datetime, timezone

from PIL import Image
from sqlmodel import Session, delete, select

from pixlstash.database import DBPriority
from pixlstash.db_models.deleted_file_log import DeletedFileLog
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.db_models.tag import Tag, TAG_PENDING_SENTINEL, is_tag_sentinel
from pixlstash.tasks.base_task import BaseTask
from pixlstash.utils.caption_file_utils import (
    DEFAULT_DESCRIPTION_SUFFIX,
    DEFAULT_TAGS_SUFFIX,
    SIDECAR_TYPE_DESCRIPTION,
    SIDECAR_TYPE_TAGS,
    detect_folder_suffixes,
    get_sidecar_mtime,
    read_description_sidecar,
    read_tags_sidecar,
    resolve_typed_sidecar,
    write_sidecar,
    writeback_path,
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

_BUILD_CHUNK_SIZE = 128
_MAX_BUILD_WORKERS = 8


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
        # Sidecar filename suffixes for this folder, loaded at the start of
        # _run_task(); None means "use known conventions / module defaults".
        self._tags_suffix: str | None = None
        self._description_suffix: str | None = None

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

        # Load the folder's sidecar configuration once. The suffixes drive how
        # tags/description sidecars are resolved for new and existing pictures;
        # the sync flags decide whether missing sidecars are exported to disk.
        def fetch_folder_config(session: Session):
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                return None
            return (
                rf.tags_suffix,
                rf.description_suffix,
                bool(rf.sync_tags),
                bool(rf.sync_descriptions),
            )

        config = self._db.run_task(fetch_folder_config, priority=DBPriority.LOW)
        (
            self._tags_suffix,
            self._description_suffix,
            sync_tags,
            sync_descriptions,
        ) = config or (None, None, False, False)

        # When a synced folder has no explicit suffix yet (a migrated folder or a
        # Docker folder added before its mount was reachable), detect the naming
        # convention already on disk and lock it in.  This keeps exports aligned
        # with any existing sidecars instead of creating duplicates under the
        # default names.  Only runs while a suffix is unset and sync is on.
        if (sync_tags or sync_descriptions) and (
            self._tags_suffix is None or self._description_suffix is None
        ):
            detected = detect_folder_suffixes(resolved)
            seed: dict[str, str] = {}
            if self._tags_suffix is None:
                self._tags_suffix = detected["tags_suffix"] or DEFAULT_TAGS_SUFFIX
                seed["tags_suffix"] = self._tags_suffix
            if self._description_suffix is None:
                self._description_suffix = (
                    detected["description_suffix"] or DEFAULT_DESCRIPTION_SUFFIX
                )
                seed["description_suffix"] = self._description_suffix
            if seed:
                self._persist_suffixes(seed)

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
                    )
                ).all()
            )

        existing_pictures: list[Picture] = self._db.run_task(
            fetch_existing, priority=DBPriority.LOW
        )
        existing_by_path: dict[str, Picture] = {
            p.file_path: p for p in existing_pictures if p.file_path
        }

        # Fetch the permanent-deletion ledger.  When a user empties the
        # scrapheap and the reference folder forbids file deletion
        # (allow_delete_file=False), the Picture row is removed but the file
        # stays on disk; a DeletedFileLog row records the path hash so the file
        # is never re-imported.  Match disk paths against the ledger by the same
        # path_sha used by the writer so a still-present file is skipped.
        def fetch_deleted_path_shas(session: Session) -> set[str]:
            rows = session.exec(select(DeletedFileLog.path_sha)).all()
            return {sha for sha in rows if sha}

        deleted_path_shas: set[str] = self._db.run_task(
            fetch_deleted_path_shas, priority=DBPriority.LOW
        )

        # Determine what is new and what has been removed.  A disk path is new
        # only if it is not already indexed and not in the permanent-deletion
        # ledger.
        candidate_new = disk_paths - set(existing_by_path.keys())
        new_paths = {
            p
            for p in candidate_new
            if DeletedFileLog.hash_path(p) not in deleted_path_shas
        }
        removed_paths = set(existing_by_path.keys()) - disk_paths

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
        imported_picture_ids: list[int] = []
        if new_paths:
            pending_paths = sorted(new_paths)
            for i in range(0, len(pending_paths), _BUILD_CHUNK_SIZE):
                chunk_paths = pending_paths[i : i + _BUILD_CHUNK_SIZE]
                chunk_pictures = self._build_picture_chunk(chunk_paths, folder_id)
                if not chunk_pictures:
                    continue
                imported_picture_ids.extend(self._insert_pictures(chunk_pictures))

        if imported_picture_ids:
            logger.info(
                "Reference folder %s: indexed %d new pictures.",
                self._folder_path,
                len(imported_picture_ids),
            )

        # --- Handle sidecar changes (and exports) for existing pictures ---
        # For each picture we reconcile the tags sidecar and the description
        # sidecar independently in both directions:
        #   read  — an external file that appeared or changed is imported (cheap
        #           os.stat() gate so content is only read when mtime differs);
        #   write — when the folder syncs that type and a picture with content
        #           has no sidecar yet, the file is created on disk (export).
        # An empty sidecar is never created.
        tags_by_pic: dict[int, list[str]] = {}
        if sync_tags:
            tags_by_pic = self._fetch_folder_tags(folder_id)

        caption_updates: list[dict] = []
        for file_path, pic in existing_by_path.items():
            if file_path in removed_paths or pic.deleted:
                # Don't touch sidecar data for removed/scrapheap pictures.
                continue
            update: dict = {"pic_id": pic.id}
            self._reconcile_sidecar(
                update,
                file_path,
                SIDECAR_TYPE_TAGS,
                self._tags_suffix,
                stored_path=pic.tags_file,
                stored_mtime=pic.tags_file_mtime,
                sync=sync_tags,
                export_content=", ".join(tags_by_pic.get(pic.id, [])),
            )
            self._reconcile_sidecar(
                update,
                file_path,
                SIDECAR_TYPE_DESCRIPTION,
                self._description_suffix,
                stored_path=pic.description_file,
                stored_mtime=pic.description_file_mtime,
                sync=sync_descriptions,
                export_content=(pic.description or "").strip(),
            )
            if len(update) > 1:
                caption_updates.append(update)

        caption_updated_picture_ids: list[int] = []
        if caption_updates:

            def apply_caption_updates(
                session: Session,
                updates: list[dict],
            ) -> None:
                for u in updates:
                    pic_db = session.get(Picture, u["pic_id"])
                    if pic_db is None:
                        continue
                    if "tags_file" in u:
                        pic_db.tags_file = u["tags_file"]
                        pic_db.tags_file_mtime = u["tags_file_mtime"]
                    if "description_file" in u:
                        pic_db.description_file = u["description_file"]
                        pic_db.description_file_mtime = u["description_file_mtime"]
                    if u.get("new_description") is not None:
                        pic_db.description = u["new_description"]
                    session.add(pic_db)
                    if "new_tags" in u:
                        # Replace tags — an empty list means all tags were removed.
                        session.exec(delete(Tag).where(Tag.picture_id == u["pic_id"]))
                        tags = u["new_tags"]
                        if tags:
                            session.add_all(
                                [Tag(picture_id=u["pic_id"], tag=t) for t in tags]
                            )
                        else:
                            session.add(
                                Tag(picture_id=u["pic_id"], tag=TAG_PENDING_SENTINEL)
                            )
                session.commit()

            self._db.run_task(
                apply_caption_updates, caption_updates, priority=DBPriority.LOW
            )
            caption_updated_picture_ids = [u["pic_id"] for u in caption_updates]
            logger.info(
                "Reference folder %s: reconciled sidecar data for %d existing pictures.",
                self._folder_path,
                len(caption_updates),
            )

        self._set_status(ReferenceFolderStatus.ACTIVE, update_last_scanned=True)
        return {
            "status": "active",
            "folder_id": folder_id,
            "new_count": len(imported_picture_ids),
            "removed_count": len(removed_paths),
            "caption_updated_count": len(caption_updates),
            "caption_updated_picture_ids": caption_updated_picture_ids,
            "imported_picture_ids": imported_picture_ids,
        }

    def _fetch_folder_tags(self, folder_id: int) -> dict[int, list[str]]:
        """Return ``{picture_id: [tag, ...]}`` for this folder's pictures.

        Used only when the folder exports tags, so the export step knows what to
        write into newly-created sidecars.  Sentinel/placeholder tags are
        excluded.  A single join avoids the SQLite bound-variable limit.
        """

        def fetch(session: Session) -> dict[int, list[str]]:
            rows = session.exec(
                select(Tag.picture_id, Tag.tag)
                .join(Picture, Tag.picture_id == Picture.id)
                .where(Picture.reference_folder_id == folder_id)
            ).all()
            out: dict[int, list[str]] = {}
            for pic_id, tag in rows:
                if tag and not is_tag_sentinel(tag):
                    out.setdefault(pic_id, []).append(tag)
            return out

        return self._db.run_task(fetch, priority=DBPriority.LOW)

    def _reconcile_sidecar(
        self,
        update: dict,
        file_path: str,
        sidecar_type: str,
        suffix: str | None,
        *,
        stored_path: str | None,
        stored_mtime: float | None,
        sync: bool,
        export_content: str,
    ) -> None:
        """Reconcile one sidecar type for one picture, mutating *update* in place.

        Read direction: when the file exists and its (path, mtime) differs from
        what was last recorded, queue an import of its content.  Write direction:
        when *sync* is on, the file is missing, and *export_content* is non-empty,
        create the file on disk now and record its new path/mtime.  A vanished
        file only clears the stored reference (the database data is kept).
        """
        is_tags = sidecar_type == SIDECAR_TYPE_TAGS
        path_key = "tags_file" if is_tags else "description_file"
        mtime_key = "tags_file_mtime" if is_tags else "description_file_mtime"

        current_path = resolve_typed_sidecar(file_path, sidecar_type, suffix)
        if current_path is not None:
            current_mtime = get_sidecar_mtime(current_path)
            if current_path != stored_path or current_mtime != stored_mtime:
                update[path_key] = current_path
                update[mtime_key] = current_mtime
                if is_tags:
                    update["new_tags"] = read_tags_sidecar(current_path)
                else:
                    update["new_description"] = read_description_sidecar(current_path)
            return

        # No sidecar on disk. Drop a stale stored reference (keep the DB data).
        if stored_path is not None:
            update[path_key] = None
            update[mtime_key] = None

        # Export: create the file from the database when there is content to write.
        if sync and export_content:
            target = writeback_path(file_path, sidecar_type, suffix, None)
            new_mtime = write_sidecar(target, export_content)
            if new_mtime is not None:
                update[path_key] = target
                update[mtime_key] = new_mtime

    def _build_picture_chunk(
        self,
        file_paths: list[str],
        folder_id: int,
    ) -> list[Picture]:
        def _build(file_path: str) -> Picture | None:
            try:
                pixel_sha = ImageUtils.calculate_hash_from_file_path(file_path)
            except Exception as exc:
                logger.warning(
                    "Reference folder scan: failed to hash %s: %s", file_path, exc
                )
                return None

            try:
                return self._build_picture(file_path, pixel_sha, folder_id)
            except Exception as exc:
                logger.warning(
                    "Reference folder scan: failed to build picture for %s: %s",
                    file_path,
                    exc,
                )
                return None

        if not file_paths:
            return []

        max_workers = min(
            _MAX_BUILD_WORKERS,
            max(1, len(file_paths)),
            max(1, os.cpu_count() or 1),
        )
        if max_workers <= 1:
            return [
                pic for pic in (_build(path) for path in file_paths) if pic is not None
            ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            return [pic for pic in executor.map(_build, file_paths) if pic is not None]

    def _insert_pictures(self, pictures: list[Picture]) -> list[int]:
        def insert_pictures(
            session: Session, pictures_batch: list[Picture]
        ) -> list[int]:
            session.add_all(pictures_batch)
            session.commit()
            for pic in pictures_batch:
                session.refresh(pic)

            sidecar_tags_to_add = []
            sentinel_tags_to_add = []
            imported_ids: list[int] = []
            for pic in pictures_batch:
                if pic.id is not None:
                    imported_ids.append(int(pic.id))
                sidecar_tags = getattr(pic, "_sidecar_tags", None)
                if sidecar_tags and pic.id is not None:
                    for tag_str in sidecar_tags:
                        sidecar_tags_to_add.append(Tag(picture_id=pic.id, tag=tag_str))
                elif pic.id is not None:
                    sentinel_tags_to_add.append(
                        Tag(picture_id=pic.id, tag=TAG_PENDING_SENTINEL)
                    )

            if sidecar_tags_to_add or sentinel_tags_to_add:
                session.add_all(sidecar_tags_to_add + sentinel_tags_to_add)
                session.commit()
            return imported_ids

        return self._db.run_task(insert_pictures, pictures, priority=DBPriority.MEDIUM)

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

        # Detect and read the tags and description sidecars independently, using
        # the folder's configured suffixes (falling back to known conventions).
        tags_path = resolve_typed_sidecar(
            file_path, SIDECAR_TYPE_TAGS, self._tags_suffix
        )
        if tags_path:
            pic.tags_file = tags_path
            pic.tags_file_mtime = get_sidecar_mtime(tags_path)
            sidecar_tags = read_tags_sidecar(tags_path)
            # Tags are stored via the Tag relationship and cannot be set on the
            # unsaved Picture directly; stash them as a transient attribute so
            # the caller can persist them after the Picture is inserted.
            if sidecar_tags:
                pic._sidecar_tags = sidecar_tags  # type: ignore[attr-defined]

        description_path = resolve_typed_sidecar(
            file_path, SIDECAR_TYPE_DESCRIPTION, self._description_suffix
        )
        if description_path:
            pic.description_file = description_path
            pic.description_file_mtime = get_sidecar_mtime(description_path)
            sidecar_description = read_description_sidecar(description_path)
            if sidecar_description and not pic.description:
                pic.description = sidecar_description

        return pic

    def _persist_suffixes(self, suffixes: dict[str, str]) -> None:
        """Store auto-detected sidecar suffixes on the folder (only fills NULLs)."""

        def update(session: Session) -> None:
            rf = session.get(ReferenceFolder, self._folder_id)
            if rf is None:
                return
            if suffixes.get("tags_suffix") and rf.tags_suffix is None:
                rf.tags_suffix = suffixes["tags_suffix"]
            if suffixes.get("description_suffix") and rf.description_suffix is None:
                rf.description_suffix = suffixes["description_suffix"]
            session.add(rf)
            session.commit()

        self._db.run_task(update, priority=DBPriority.LOW)

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
