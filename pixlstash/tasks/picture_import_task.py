import os
import shutil
import threading
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.character import Character
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.picture_set import PictureSet, PictureSetMember
from pixlstash.db_models.tag import Tag, TAG_PENDING_SENTINEL
from pixlstash.pixl_logging import get_logger
from pixlstash.services import import_dedup_service
from pixlstash.services.layout_move_service import resolve_placement
from pixlstash.services.project_membership_service import (
    character_project_ids,
    picture_set_project_ids,
    reconcile_entity_projects_change,
)
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils


logger = get_logger(__name__)


def _sidecar_stem(name: str) -> str:
    """Basename stem for sidecar matching.

    Mirrors ``routes.pictures._helpers._normalise_sidecar_stem`` verbatim;
    duplicated as a one-liner to avoid a routes→tasks import cycle.
    """
    return os.path.splitext(os.path.basename(name or ""))[0].strip().lower()


class PictureImportTask(BaseTask):
    """Finish an async import server-side, in the safe (background) window.

    The client first streams uploaded files into a per-session staging directory
    during the *unsafe* window (the browser tab must stay open). Once staging is
    complete the route hands off to this task, which runs on the shared
    ``TaskRunner`` so the import completes even after the tab closes (the *safe*
    window). Each staged file is hashed, de-duplicated by ``pixel_sha``, ingested
    into the vault, and inserted as a ``Picture`` row (with a pending-tag
    sentinel so the finders pick it up for downstream processing). The staging
    directory is removed on completion.

    **Every staged file lands in exactly one of five disjoint buckets**,
    imported, duplicate (live content already in the vault, including a repeat
    inside this batch), **scrapheaped** (content matches a soft-deleted picture:
    not imported again, offered for restore), failed, cancelled and they sum
    to the staged total. A scrapheaped match used to be reported as an ordinary
    duplicate, which told the user nothing about why their file did not appear.
    See :mod:`pixlstash.services.import_dedup_service`.

    Live progress is exposed via ``_total_count`` / ``_processed_count`` and read
    by ``Vault._build_worker_progress_snapshot`` so the import surfaces as a task
    row in the frontend task manager, mirroring the detection / watch-folder
    workers. Completion is announced by ``Vault._on_task_completed`` (a
    ``CHANGED_PICTURES`` + ``PICTURE_IMPORTED`` broadcast).

    Attributes:
        _db: The application ``VaultDatabase``.
        _staged_files: List of ``{"file_path", "original_file_name"}`` staged
            entries to ingest.
        _project_id: Optional project the imported pictures are added to.
        _staging_id: The staging-session id (carried into the result/logs).
        _staging_dir: Absolute path of the staging directory to remove when done.
        _origin_client_id: The originating tab's client id (for origin-stamped
            WebSocket events), or ``None`` for an external caller.
    """

    def __init__(
        self,
        database,
        staged_files: list[dict],
        *,
        project_id: Optional[int] = None,
        set_id: Optional[int] = None,
        character_id: Optional[int] = None,
        sidecar_tags_by_stem: Optional[dict[str, list[str]]] = None,
        staging_id: str,
        staging_dir: Optional[str] = None,
        origin_client_id: Optional[str] = None,
    ):
        """Initialise the task.

        Args:
            database: The application ``VaultDatabase`` instance.
            staged_files: Sequence of ``{"file_path", "original_file_name"}``
                dicts for each staged media file on disk.
            project_id: Optional project id to add every imported picture to.
            set_id: Optional picture-set id every imported picture is added to
                (a drop-to-set target).
            character_id: Optional character id every imported picture is
                associated with (a drop-to-character target; deferred via
                ``Picture.pending_character_id`` until face extraction runs).
            sidecar_tags_by_stem: Optional map of ``basename stem -> [tags]``
                parsed from ``.txt`` caption sidecars, applied to the matching
                imported picture (mirrors the one-shot import).
            staging_id: The staging-session id this task finishes.
            staging_dir: Absolute path of the staging directory to remove on
                completion (best-effort cleanup).
            origin_client_id: Originating tab's client id for origin-stamped
                events, or ``None`` for an external client.
        """
        staged_files = staged_files or []
        super().__init__(
            task_type="PictureImportTask",
            params={
                "staging_id": staging_id,
                "candidate_count": len(staged_files),
                "project_id": project_id,
                "set_id": set_id,
                "character_id": character_id,
                "origin_client_id": origin_client_id,
            },
        )
        self._db = database
        self._staged_files = staged_files
        self._project_id = project_id
        self._set_id = set_id
        self._character_id = character_id
        self._sidecar_tags_by_stem = sidecar_tags_by_stem or {}
        self._staging_id = staging_id
        self._staging_dir = staging_dir
        self._origin_client_id = origin_client_id
        # Live progress, read by Vault.get_worker_progress for the task manager.
        self._total_count = len(staged_files)
        self._processed_count = 0
        self._stop_event = threading.Event()

    @property
    def priority(self) -> TaskPriority:
        # User-initiated, latency-sensitive ingest - ahead of the finder-driven
        # background lanes, mirroring DetectionTask.
        return TaskPriority.HIGH

    def on_cancel(self) -> None:
        self._stop_event.set()

    def _run_task(self):
        new_pictures: list[Picture] = []
        duplicate_count = 0
        scrapheaped_count = 0
        failed_count = 0
        cancelled_count = 0
        # Distinct scrapheaped pictures this import matched. Per PICTURE (the
        # restore offer), while ``scrapheaped_count`` is per FILE (the bucket
        # arithmetic): several staged copies of one content name one id once.
        scrapheaped_picture_ids: list[int] = []
        seen_scrapheaped_ids: set[int] = set()
        # Content hashes already accepted this batch. The DB dedupe below only
        # catches files already committed; the batch is committed after the
        # loop, so two byte-identical staged files would both pass the DB check.
        # Track them here so an intra-batch duplicate is counted, not imported.
        seen_fingerprints: set[tuple[int, str]] = set()

        # Placement on write (v1.11 Phase 4b): where the library's layout says
        # a picture with these assignments belongs. Resolved once for the whole
        # batch, because every picture in it gets the same project and set, and
        # empty for a library with no layout - which is every library until its
        # owner chooses one.
        subfolder = resolve_placement(
            self._db, project_id=self._project_id, set_id=self._set_id
        )
        if subfolder:
            logger.info(
                "PictureImportTask: the library layout places this import in "
                "%s/ (staging_id=%s).",
                subfolder,
                self._staging_id,
            )

        for index, entry in enumerate(self._staged_files):
            if self._stop_event.is_set():
                # Count the untouched tail explicitly. The buckets have to sum
                # to the staged total on a cancelled run too, and deriving that
                # remainder by subtracting the other buckets would report a
                # number nobody measured.
                cancelled_count = len(self._staged_files[index:])
                logger.info(
                    "PictureImportTask %s cancelled; stopping early "
                    "(staging_id=%s, processed=%d/%d, %d staged file(s) never "
                    "reached)",
                    self.id,
                    self._staging_id,
                    self._processed_count,
                    self._total_count,
                    cancelled_count,
                )
                break

            file_path = entry.get("file_path")
            if not file_path:
                # A staged entry with no path can never be ingested; it belongs
                # in the failed bucket, not in no bucket at all.
                logger.warning(
                    "PictureImportTask: staged entry %d has no file_path "
                    "(staging_id=%s); counting it as failed.",
                    index,
                    self._staging_id,
                )
                failed_count += 1
                self._processed_count += 1
                continue

            try:
                pixel_sha = ImageUtils.calculate_hash_from_file_path(file_path)
                size_bytes = os.path.getsize(file_path)
                full_sha = ImageUtils.calculate_full_hash_from_file_path(file_path)
            except Exception as exc:
                logger.warning(
                    "PictureImportTask: failed to hash staged file %s "
                    "(staging_id=%s): %s",
                    file_path,
                    self._staging_id,
                    exc,
                )
                failed_count += 1
                self._processed_count += 1
                continue

            content_key = (pixel_sha, size_bytes)
            fingerprint = (pixel_sha, size_bytes, full_sha)

            def find_candidates(session: Session, key):
                # Deliberately matches soft-deleted rows too: a scrapheaped
                # picture used to be invisible to import dedup on the one-shot
                # path, which re-imported its file as a second row. Here the
                # match was already found (this query never had a ``deleted``
                # filter) but was reported as an ordinary duplicate, which hid
                # from the user that their file is sitting in the Scrapheap.
                # The service classifies it instead of silently collapsing it.
                return import_dedup_service.load_match_candidates_in_session(
                    session, [key]
                )

            # Submitted on the writer queue, not the immediate read path, so a
            # picture inserted by an earlier batch of this same import is
            # already visible.
            candidates = self._db.run_task(find_candidates, content_key)
            match = import_dedup_service.confirmed_match(
                candidates, fingerprint, self._db.image_root
            )
            if match is not None and match.deleted:
                logger.info(
                    "PictureImportTask: staged file %s matches scrapheaped "
                    "picture %d (sha %s); not importing a second copy, "
                    "offering a restore instead (staging_id=%s)",
                    file_path,
                    match.id,
                    pixel_sha,
                    self._staging_id,
                )
                scrapheaped_count += 1
                if match.id not in seen_scrapheaped_ids:
                    seen_scrapheaped_ids.add(match.id)
                    scrapheaped_picture_ids.append(match.id)
                self._processed_count += 1
                continue
            intra_batch_key = (size_bytes, full_sha)
            if match is not None or intra_batch_key in seen_fingerprints:
                logger.debug(
                    "PictureImportTask: duplicate sha %s already imported; "
                    "skipping staged file %s",
                    pixel_sha,
                    file_path,
                )
                duplicate_count += 1
                self._processed_count += 1
                continue

            try:
                original_file_name = entry.get(
                    "original_file_name"
                ) or os.path.basename(file_path)
                pic = ImageUtils.create_picture_from_file(
                    image_root_path=self._db.image_root,
                    source_file_path=file_path,
                    pixel_sha=pixel_sha,
                    subfolder=subfolder,
                )
                pic.imported_at = datetime.utcnow()
                if original_file_name:
                    pic.original_file_name = original_file_name
                new_pictures.append(pic)
                seen_fingerprints.add(intra_batch_key)
            except Exception as exc:
                logger.warning(
                    "PictureImportTask: failed to ingest staged file %s "
                    "(staging_id=%s): %s",
                    file_path,
                    self._staging_id,
                    exc,
                )
                failed_count += 1
            finally:
                self._processed_count += 1

        imported_ids: list[int] = []
        if new_pictures:
            sidecar_tags_by_stem = self._sidecar_tags_by_stem

            def insert_pictures(session: Session, pictures: list[Picture]):
                session.add_all(pictures)
                session.flush()
                for pic in pictures:
                    # Apply sidecar caption tags when the picture matches one by
                    # basename stem; otherwise seed the pending sentinel so the
                    # tagger picks it up (mirrors WatchFolderImportTask).
                    tags = sidecar_tags_by_stem.get(
                        _sidecar_stem(pic.original_file_name or "")
                    )
                    if tags:
                        for tag_str in tags:
                            session.add(Tag(picture_id=pic.id, tag=tag_str))
                    else:
                        session.add(Tag(picture_id=pic.id, tag=TAG_PENDING_SENTINEL))
                session.commit()
                for pic in pictures:
                    session.refresh(pic)
                return [pic.id for pic in pictures if pic.id is not None]

            imported_ids = self._db.run_task(
                insert_pictures,
                new_pictures,
                priority=DBPriority.IMMEDIATE,
            )

            if imported_ids:
                self._apply_project(imported_ids)
                self._apply_set(imported_ids)
                self._apply_character(imported_ids)

        self._cleanup_staging_dir()

        logger.info(
            "PictureImportTask completed: staging_id=%s imported=%d duplicates=%d "
            "scrapheaped=%d (%d picture(s)) failed=%d cancelled=%d of %d staged",
            self._staging_id,
            len(imported_ids),
            duplicate_count,
            scrapheaped_count,
            len(scrapheaped_picture_ids),
            failed_count,
            cancelled_count,
            self._total_count,
        )
        # The five buckets are disjoint and describe every staged file. Each is
        # counted where it happens; none is derived by subtracting the others
        # from the total. If they ever disagree with the staged total the
        # summary the user reads is wrong, so say so instead of shipping it
        # quietly.
        bucket_total = (
            len(imported_ids)
            + duplicate_count
            + scrapheaped_count
            + failed_count
            + cancelled_count
        )
        if bucket_total != self._total_count:
            logger.error(
                "PictureImportTask %s bucket arithmetic is inconsistent "
                "(staging_id=%s): imported=%d + duplicate=%d + scrapheaped=%d + "
                "failed=%d + cancelled=%d = %d, but %d file(s) were staged. "
                "(%d picture(s) were built for insert.)",
                self.id,
                self._staging_id,
                len(imported_ids),
                duplicate_count,
                scrapheaped_count,
                failed_count,
                cancelled_count,
                bucket_total,
                self._total_count,
                len(new_pictures),
            )
        return {
            "staging_id": self._staging_id,
            "imported_picture_ids": imported_ids,
            "imported_count": len(imported_ids),
            "duplicate_count": duplicate_count,
            "scrapheaped_count": scrapheaped_count,
            "scrapheaped_picture_ids": scrapheaped_picture_ids,
            "failed_count": failed_count,
            "cancelled_count": cancelled_count,
            "candidate_count": self._total_count,
            "origin_client_id": self._origin_client_id,
        }

    def _apply_project(self, imported_ids: list[int]) -> None:
        """Add imported pictures to the drop target project (mirrors the
        one-shot import's ``apply_import_context``)."""
        if self._project_id is None:
            return

        def apply_project(session: Session, ids: list[int], project_id_value: int):
            for pic in session.exec(select(Picture).where(Picture.id.in_(ids))).all():
                member = session.exec(
                    select(PictureProjectMember).where(
                        PictureProjectMember.picture_id == pic.id,
                        PictureProjectMember.project_id == project_id_value,
                    )
                ).first()
                if member is None:
                    session.add(
                        PictureProjectMember(
                            picture_id=pic.id, project_id=project_id_value
                        )
                    )
                pic.project_id = project_id_value
                session.add(pic)
            session.commit()

        self._db.run_task(
            apply_project,
            imported_ids,
            self._project_id,
            priority=DBPriority.IMMEDIATE,
        )

    def _apply_set(self, imported_ids: list[int]) -> None:
        """Add imported pictures to the drop target set (mirrors
        ``picture_sets.add_picture_to_set``: ``PictureSetMember`` + project
        reconciliation when the set belongs to a project)."""
        if self._set_id is None:
            return

        def apply_set(session: Session, ids: list[int], set_id_value: int):
            picture_set = session.get(PictureSet, set_id_value)
            if picture_set is None:
                # Validated at commit; a race that deletes it here is logged, not
                # silently ignored, and does not fail the (already-done) import.
                logger.error(
                    "PictureImportTask: set %s vanished before association "
                    "(staging_id=%s)",
                    set_id_value,
                    self._staging_id,
                )
                return
            member_ids: list[int] = []
            for pic in session.exec(select(Picture).where(Picture.id.in_(ids))).all():
                exists = session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.set_id == set_id_value,
                        PictureSetMember.picture_id == pic.id,
                    )
                ).first()
                if exists is None:
                    session.add(
                        PictureSetMember(set_id=set_id_value, picture_id=pic.id)
                    )
                member_ids.append(int(pic.id))
            # Issue #125: the set may belong to several projects, so the imported
            # pictures join *all* of them, not just the primary FK.
            reconcile_entity_projects_change(
                session,
                picture_ids=member_ids,
                ensure_project_ids=picture_set_project_ids(session, set_id_value),
                remove_project_ids=[],
            )
            session.commit()

        self._db.run_task(
            apply_set,
            imported_ids,
            self._set_id,
            priority=DBPriority.IMMEDIATE,
        )

    def _apply_character(self, imported_ids: list[int]) -> None:
        """Associate imported pictures with the drop target character.

        Fresh imports have no faces yet, so this uses the same deferral the
        ``POST /characters/{id}/faces`` handler uses for face-less pictures:
        set ``Picture.pending_character_id`` (consumed by
        ``Vault._process_pending_character_assignments`` once face extraction
        runs), plus project reconciliation when the character has a project."""
        if self._character_id is None:
            return

        def apply_character(session: Session, ids: list[int], character_id_value: int):
            character = session.get(Character, character_id_value)
            if character is None:
                logger.error(
                    "PictureImportTask: character %s vanished before association "
                    "(staging_id=%s)",
                    character_id_value,
                    self._staging_id,
                )
                return
            pending_ids: list[int] = []
            for pic in session.exec(select(Picture).where(Picture.id.in_(ids))).all():
                pic.pending_character_id = character_id_value
                session.add(pic)
                pending_ids.append(int(pic.id))
            # Issue #125: the character may belong to several projects, so the
            # imported pictures join *all* of them, not just the primary FK.
            reconcile_entity_projects_change(
                session,
                picture_ids=pending_ids,
                ensure_project_ids=character_project_ids(session, character_id_value),
                remove_project_ids=[],
            )
            session.commit()

        self._db.run_task(
            apply_character,
            imported_ids,
            self._character_id,
            priority=DBPriority.IMMEDIATE,
        )

    def _cleanup_staging_dir(self) -> None:
        """Remove the staging directory once the import has finished."""
        if not self._staging_dir:
            return
        if not os.path.isdir(self._staging_dir):
            return
        try:
            shutil.rmtree(self._staging_dir)
            logger.debug(
                "PictureImportTask: removed staging dir %s (staging_id=%s)",
                self._staging_dir,
                self._staging_id,
            )
        except Exception as exc:
            logger.warning(
                "PictureImportTask: failed to remove staging dir %s "
                "(staging_id=%s): %s",
                self._staging_dir,
                self._staging_id,
                exc,
            )
