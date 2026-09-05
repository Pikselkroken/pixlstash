"""Per-resource and batch restore: the upsert paths.

Restores a single resource (picture / picture_set / character) or a batch of
them from a snapshot: resolves the affected pictures, honours the
permanent-deletion ledger, collects rows plus referenced parents from the
snapshot, and upserts them into the live database inside one writer
transaction (restoring missing parents first when the caller confirms).
"""

import os
import shutil
from datetime import datetime, timezone

from sqlmodel import Session, select
from sqlalchemy import (
    delete as sa_delete,
    inspect as sa_inspect,
    select as sa_select,
)

from pixlstash.db_models import (
    Character,
    CharacterProjectMember,
    Face,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    PictureSetProjectMember,
    Project,
    Tag,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.path_utils import resolve_path_within

from ._models import (
    MissingDependenciesError,
    RestoreInProgressError,
    RestoreReport,
    _SUPPORTED_RESOURCE_TYPES,
)
from .schema_upgrade import snapshot_engine

logger = get_logger(__name__)

# Transport key for an entity's project membership on a candidate-parent dict.
# Not a column: it is popped before the row is turned back into a model, and it
# exists only so the join rows travel with the parent from the snapshot session
# to the live one (issue #125).
_PROJECT_IDS_KEY = "__project_ids"


class ResourceRestoreMixin:
    """Per-resource and batch restore behaviour.

    Mixed into :class:`~pixlstash.services.restore.RestoreService`.
    """

    def restore_resource(
        self,
        snapshot_id: int,
        resource_type: str,
        resource_id: int,
        confirm_restore_dependencies: bool = False,
    ) -> RestoreReport:
        """Restore a single resource from a snapshot snapshot.

        Supported *resource_type* values:
        - ``'picture'``  - restores the Picture row plus Face, Tag,
          PictureSetMember, and PictureProjectMember dependents.
        - ``'picture_set'`` - restores the PictureSet row and all member
          pictures (recursive picture restore).
        - ``'character'`` - restores the Character row only. **Does not
          re-attach faces**: if a character was deleted live, the cascading
          ``Face.character_id = NULL`` is not reversed by this path. Use the
          full restore for a faithful character revert.

        ``'project'`` is **not** supported in this release - use the full
        restore. See ``_SUPPORTED_RESOURCE_TYPES`` for the reasoning.

        Args:
            snapshot_id: ID of the snapshot to restore from.
            resource_type: One of the strings in ``_SUPPORTED_RESOURCE_TYPES``.
            resource_id: Primary key of the resource to restore.
            confirm_restore_dependencies: If the snapshot rows reference
                parents (Character / PictureSet / Project) that have been
                deleted from live since the snapshot, ``False`` (the
                default) raises ``MissingDependenciesError`` without
                writing anything; ``True`` re-inserts the missing parents
                from the snapshot before upserting the requested
                resource.

        Returns:
            A ``RestoreReport`` summarising the operation.

        Raises:
            ValueError: If the snapshot is not found or ``resource_type`` is
                unsupported.
            RestoreInProgressError: If another restore is already running.
            MissingDependenciesError: If parents are missing in live and
                ``confirm_restore_dependencies`` is False.
        """
        if not self._restore_lock.acquire(blocking=False):
            raise RestoreInProgressError(
                "Another restore operation is already in progress; "
                "see GET /snapshots/status."
            )
        try:
            vault_root = self._vault.image_root
            cp = self._get_snapshot_or_raise(snapshot_id)
            abs_snapshot = resolve_path_within(vault_root, cp.relative_path)
            if not os.path.exists(abs_snapshot):
                raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

            if resource_type not in _SUPPORTED_RESOURCE_TYPES:
                raise ValueError(
                    f"Unsupported resource_type '{resource_type}'. "
                    f"Supported: {', '.join(_SUPPORTED_RESOURCE_TYPES)}. "
                    "Use the full restore for project-level recovery."
                )

            started_payload = {
                "snapshot_id": snapshot_id,
                "resource_type": resource_type,
                "resource_id": resource_id,
            }
            self._emit_lifecycle(EventType.RESTORE_STARTED, started_payload)
            try:
                upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
                if upgraded_snapshot is None:
                    raise RuntimeError("Schema upgrade failed; aborting restore.")

                try:
                    report = self._restore_resource_from_snapshot(
                        upgraded_snapshot,
                        snapshot_id,
                        resource_type,
                        resource_id,
                        vault_root,
                        confirm_restore_dependencies=confirm_restore_dependencies,
                    )
                finally:
                    # Remove the whole mkdtemp dir, not just the file, so the
                    # empty scratch directory isn't leaked.
                    shutil.rmtree(
                        os.path.dirname(upgraded_snapshot), ignore_errors=True
                    )

                self._emit_lifecycle(
                    EventType.RESTORE_COMPLETED,
                    {
                        **started_payload,
                        "missing_files_count": report.missing_files_count,
                        "upserted_count": report.upserted_count,
                    },
                )
                return report
            except Exception as exc:
                self._emit_lifecycle(
                    EventType.RESTORE_FAILED,
                    {**started_payload, "error": str(exc)},
                )
                raise
        finally:
            self._restore_lock.release()

    def restore_batch(
        self,
        snapshot_id: int,
        resources: list[dict],
        confirm_restore_dependencies: bool = False,
    ) -> RestoreReport:
        """Restore a batch of resources from a snapshot.

        Args:
            snapshot_id: ID of the snapshot.
            resources: List of ``{"type": str, "id": int}`` dicts.
            confirm_restore_dependencies: If any item in the batch
                references parents (Character / PictureSet / Project)
                that have been deleted from live since the snapshot,
                ``False`` (the default) raises ``MissingDependenciesError``
                with the *union* of missing parents across the whole
                batch - no items are restored. ``True`` re-inserts the
                missing parents from the snapshot first and then runs
                the batch.

        Returns:
            A ``RestoreReport`` with aggregate counts.

        Raises:
            ValueError: If the snapshot/snapshot is not found.
            RestoreInProgressError: If another restore is already running.
            MissingDependenciesError: If any batch item has parents
                missing in live and ``confirm_restore_dependencies`` is
                False. ``missing`` carries the union across the batch.
        """
        if not self._restore_lock.acquire(blocking=False):
            raise RestoreInProgressError(
                "Another restore operation is already in progress; "
                "see GET /snapshots/status."
            )
        try:
            vault_root = self._vault.image_root
            cp = self._get_snapshot_or_raise(snapshot_id)
            abs_snapshot = resolve_path_within(vault_root, cp.relative_path)
            if not os.path.exists(abs_snapshot):
                raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

            if not resources:
                return RestoreReport(
                    snapshot_id=snapshot_id,
                    resource_type="batch",
                )

            started_payload = {"snapshot_id": snapshot_id, "resource_type": "batch"}
            self._emit_lifecycle(EventType.RESTORE_STARTED, started_payload)
            try:
                # Upgrade schema once for the whole batch.
                upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
                if upgraded_snapshot is None:
                    raise RuntimeError("Schema upgrade failed; aborting batch restore.")

                # Pre-flight: compute the union of parents referenced by
                # any item in the batch but missing from live. Raise once
                # for the whole batch if the caller hasn't confirmed -
                # avoids the "restore item 1, fail on item 2, leave
                # partial state" trap.
                union_candidates = self._collect_batch_candidate_parents(
                    upgraded_snapshot, resources, vault_root
                )
                batch_missing = self._vault.db.run_immediate_read_task(
                    lambda session: self._find_missing_parent_ids(
                        session, union_candidates
                    )
                )
                if batch_missing and not confirm_restore_dependencies:
                    raise MissingDependenciesError(batch_missing)
                if batch_missing:
                    # Confirmed - restore parents once for the whole batch.
                    self._vault.db.run_task(
                        lambda session: self._restore_parent_rows(
                            session, union_candidates, batch_missing
                        ),
                        priority=0,
                    )

                total = RestoreReport(snapshot_id=snapshot_id, resource_type="batch")
                try:
                    for item in resources:
                        rtype = item.get("type", "")
                        rid = int(item.get("id", 0))
                        if rtype not in _SUPPORTED_RESOURCE_TYPES:
                            total.errors.append(
                                f"Skipped unsupported resource type '{rtype}' "
                                f"(supported: {', '.join(_SUPPORTED_RESOURCE_TYPES)})."
                            )
                            continue
                        try:
                            # Pass confirm=True down because the pre-flight
                            # already restored any missing parents - the
                            # per-item dep check is now a no-op.
                            sub = self._restore_resource_from_snapshot(
                                upgraded_snapshot,
                                snapshot_id,
                                rtype,
                                rid,
                                vault_root,
                                confirm_restore_dependencies=True,
                            )
                            total.missing_files_count += sub.missing_files_count
                            total.upserted_count += sub.upserted_count
                            total.errors.extend(sub.errors)
                        except Exception as exc:
                            msg = f"{rtype}/{rid}: {exc}"
                            logger.error(
                                "RestoreService: batch item restore failed: %s",
                                msg,
                                exc_info=True,
                            )
                            total.errors.append(msg)
                finally:
                    try:
                        os.remove(upgraded_snapshot)
                        shutil.rmtree(
                            os.path.dirname(upgraded_snapshot), ignore_errors=True
                        )
                    except Exception:
                        logger.warning(
                            "RestoreService: failed to remove temp upgraded snapshot and dir: %s",
                            upgraded_snapshot,
                        )

                self._emit_lifecycle(
                    EventType.RESTORE_COMPLETED,
                    {
                        **started_payload,
                        "upserted_count": total.upserted_count,
                        "missing_files_count": total.missing_files_count,
                    },
                )
                return total
            except Exception as exc:
                self._emit_lifecycle(
                    EventType.RESTORE_FAILED,
                    {**started_payload, "error": str(exc)},
                )
                raise
        finally:
            self._restore_lock.release()

    def _restore_resource_from_snapshot(
        self,
        abs_snapshot: str,
        snapshot_id: int,
        resource_type: str,
        resource_id: int,
        vault_root: str,
        confirm_restore_dependencies: bool = False,
    ) -> RestoreReport:
        """Upsert resource rows from the snapshot into the live database.

        Args:
            abs_snapshot: Absolute path to the upgraded snapshot.
            snapshot_id: Original snapshot ID.
            resource_type: Resource type string.
            resource_id: Primary key.
            vault_root: Vault root directory for file existence checks.

        Returns:
            RestoreReport with counts.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        report = RestoreReport(
            snapshot_id=snapshot_id,
            resource_type=resource_type,
            resource_id=resource_id,
        )

        snap_engine = snapshot_engine(abs_snapshot)
        try:
            with Session(snap_engine) as snap_session:
                if resource_type == "picture":
                    picture_ids = [resource_id]
                elif resource_type == "picture_set":
                    members = snap_session.exec(
                        select(PictureSetMember).where(
                            PictureSetMember.set_id == resource_id
                        )
                    ).all()
                    picture_ids = [m.picture_id for m in members]
                elif resource_type == "project":
                    # Collect all picture_set_ids in the project.
                    from pixlstash.db_models.picture_project import (
                        PictureProjectMember as PPM,
                    )

                    ppm_rows = snap_session.exec(
                        select(PPM).where(PPM.project_id == resource_id)
                    ).all()
                    picture_ids = [p.picture_id for p in ppm_rows]
                elif resource_type == "character":
                    picture_ids = []
                else:
                    picture_ids = []

                # Filter to pictures whose files exist on disk.
                valid_picture_ids: list[int] = []
                for pid in picture_ids:
                    pic = snap_session.get(Picture, pid)
                    if pic is None:
                        continue
                    if pic.file_path:
                        try:
                            resolved = ImageUtils.resolve_picture_path(
                                vault_root, pic.file_path
                            )
                            if not os.path.isfile(resolved):
                                report.missing_files_count += 1
                                logger.info(
                                    "RestoreService: skipping picture id=%d "
                                    "(file missing: %s)",
                                    pid,
                                    pic.file_path,
                                )
                                continue
                        except Exception:
                            report.missing_files_count += 1
                            continue
                    valid_picture_ids.append(pid)

                # Honor the permanent-deletion ledger: never resurrect a
                # picture whose file or content hash was permanently deleted,
                # even if its file still happens to be on disk. Filter here,
                # before collecting rows, so the deleted pictures' parents are
                # not pulled in either.
                path_shas, pixel_shas = self._load_deleted_file_index()
                if path_shas or pixel_shas:
                    blocked = self._match_deleted_picture_ids(
                        snap_session, path_shas, pixel_shas
                    )
                    if blocked:
                        kept = [pid for pid in valid_picture_ids if pid not in blocked]
                        skipped = len(valid_picture_ids) - len(kept)
                        if skipped:
                            report.permanently_deleted_count += skipped
                            report.errors.append(
                                f"Skipped {skipped} permanently-deleted "
                                "picture(s); they cannot be restored."
                            )
                            logger.info(
                                "RestoreService: skipping %d permanently-deleted "
                                "picture(s) during %s restore.",
                                skipped,
                                resource_type,
                            )
                        valid_picture_ids = kept

                # Collect all rows to upsert.
                snap_rows = self._collect_rows_for_upsert(
                    snap_session,
                    resource_type,
                    resource_id,
                    valid_picture_ids,
                )
                # Materialise referenced parents (Characters / PictureSets /
                # Projects) so the live session can either reject the restore
                # with a structured 409 (if the user hasn't confirmed) or
                # re-insert the missing ones before upserting children.
                candidate_parents = self._collect_candidate_parents(
                    snap_session, snap_rows
                )
        finally:
            snap_engine.dispose()

        # Upsert in the live DB.
        # Inside the writer task we first check for parents missing in live
        # and either short-circuit with ``MissingDependenciesError`` (the
        # caller has not opted in) or restore them from the snapshot first.
        # The whole sequence runs in one writer-session transaction so a
        # mid-flight failure rolls back both the parent inserts and the
        # subsequent upsert.
        def _check_deps_and_upsert(session):
            missing = self._find_missing_parent_ids(session, candidate_parents)
            if missing:
                if not confirm_restore_dependencies:
                    raise MissingDependenciesError(missing)
                self._restore_parent_rows(session, candidate_parents, missing)
            return self._upsert_rows(session, snap_rows, valid_picture_ids)

        upserted = self._vault.db.run_task(_check_deps_and_upsert, priority=0)
        report.upserted_count = upserted
        return report

    def _collect_batch_candidate_parents(
        self,
        upgraded_snapshot: str,
        resources: list[dict],
        vault_root: str,
    ) -> dict[str, list[dict]]:
        """Open the snapshot once and union the candidate parents from
        every item in the batch.

        Args:
            upgraded_snapshot: Path to the alembic-upgraded snapshot file.
            resources: Batch items ``[{"type": ..., "id": ...}, ...]``.
            vault_root: Vault image root (used to skip pictures whose
                file is missing on disk - those will be dropped by the
                per-item restore, so their FK refs don't matter).

        Returns:
            ``{"characters": [{...}, ...], "picture_sets": [...],
              "projects": [...]}``, deduplicated by parent id.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        seen_ids: dict[str, set] = {
            "characters": set(),
            "picture_sets": set(),
            "projects": set(),
        }
        union: dict[str, list[dict]] = {
            "characters": [],
            "picture_sets": [],
            "projects": [],
        }

        snap_engine = snapshot_engine(upgraded_snapshot)
        try:
            with Session(snap_engine) as snap_session:
                for item in resources:
                    rtype = item.get("type", "")
                    rid = int(item.get("id", 0))
                    if rtype not in _SUPPORTED_RESOURCE_TYPES:
                        continue
                    # Resolve picture_ids for this item.
                    if rtype == "picture":
                        picture_ids = [rid]
                    elif rtype == "picture_set":
                        members = snap_session.exec(
                            select(PictureSetMember).where(
                                PictureSetMember.set_id == rid
                            )
                        ).all()
                        picture_ids = [m.picture_id for m in members]
                    else:
                        picture_ids = []

                    # Filter to pictures whose files exist on disk.
                    valid_pids: list[int] = []
                    for pid in picture_ids:
                        pic = snap_session.get(Picture, pid)
                        if pic is None:
                            continue
                        if pic.file_path:
                            try:
                                resolved = ImageUtils.resolve_picture_path(
                                    vault_root, pic.file_path
                                )
                                if not os.path.isfile(resolved):
                                    continue
                            except Exception as exc:
                                logger.debug(
                                    "Restore: skipping picture %s with "
                                    "unresolvable path %r: %s",
                                    pid,
                                    pic.file_path,
                                    exc,
                                )
                                continue
                        valid_pids.append(pid)

                    snap_rows = self._collect_rows_for_upsert(
                        snap_session, rtype, rid, valid_pids
                    )
                    candidates = self._collect_candidate_parents(
                        snap_session, snap_rows
                    )
                    for key in ("characters", "picture_sets", "projects"):
                        for parent in candidates.get(key, []):
                            pid = parent["id"]
                            if pid not in seen_ids[key]:
                                seen_ids[key].add(pid)
                                union[key].append(parent)
        finally:
            snap_engine.dispose()

        return union

    def _collect_candidate_parents(
        self, snap_session: Session, snap_rows: dict
    ) -> dict[str, list[dict]]:
        """Snapshot the parent rows referenced by ``snap_rows`` so we can
        re-attach them later in the live session if needed.

        We materialise the parent ORM objects as plain dicts here - the
        snap session closes before the live work runs, and a detached
        SQLModel object isn't always portable across sessions.

        Args:
            snap_session: Read session on the snapshot DB.
            snap_rows: Output of ``_collect_rows_for_upsert``.

        Returns:
            ``{"characters": [{...}, ...], "picture_sets": [...],
              "projects": [...]}`` - one dict per parent referenced
            anywhere in ``snap_rows``. Character and picture-set dicts carry an
            extra :data:`_PROJECT_IDS_KEY` entry holding the entity's project
            membership from the snapshot; :meth:`_restore_parent_rows` pops it
            and rebuilds the join rows. Without it a restored entity would come
            back with its scalar ``project_id`` set but no membership row, i.e.
            invisible to every project-scoped read (issue #125: the join is the
            read model).
        """

        def _as_dict(obj):
            if obj is None:
                return None
            mapper = sa_inspect(type(obj))
            return {col.key: getattr(obj, col.key) for col in mapper.column_attrs}

        char_ids = {
            f.character_id
            for f in snap_rows.get("faces", [])
            if f.character_id is not None
        }
        set_ids = {m.set_id for m in snap_rows.get("picture_set_members", [])}
        proj_ids = {m.project_id for m in snap_rows.get("picture_project_members", [])}
        # Root entity memberships are authoritative snapshot collections too.
        # Their projects must participate in dependency preflight before the
        # live join rows are replaced, otherwise the exact replacement can fail
        # late (or be silently narrowed) when a referenced project was deleted.
        proj_ids.update(snap_rows.get("character_project_ids", []))
        proj_ids.update(snap_rows.get("picture_set_project_ids", []))

        def _with_project_ids(row, membership_model, id_column, entity_id):
            if row is None:
                return None
            row[_PROJECT_IDS_KEY] = sorted(
                int(pid)
                for pid in snap_session.execute(
                    sa_select(membership_model.project_id).where(id_column == entity_id)
                )
                .scalars()
                .all()
                if pid is not None
            )
            return row

        characters = [
            _with_project_ids(
                _as_dict(snap_session.get(Character, cid)),
                CharacterProjectMember,
                CharacterProjectMember.character_id,
                cid,
            )
            for cid in sorted(char_ids)
            if snap_session.get(Character, cid) is not None
        ]
        picture_sets = [
            _with_project_ids(
                _as_dict(snap_session.get(PictureSet, sid)),
                PictureSetProjectMember,
                PictureSetProjectMember.set_id,
                sid,
            )
            for sid in sorted(set_ids)
            if snap_session.get(PictureSet, sid) is not None
        ]
        projects = [
            _as_dict(snap_session.get(Project, pid))
            for pid in sorted(proj_ids)
            if snap_session.get(Project, pid) is not None
        ]
        return {
            "characters": [c for c in characters if c is not None],
            "picture_sets": [s for s in picture_sets if s is not None],
            "projects": [p for p in projects if p is not None],
        }

    @staticmethod
    def _find_missing_parent_ids(
        live_session: Session, candidate_parents: dict[str, list[dict]]
    ) -> dict[str, list[int]]:
        """Return the subset of candidate parent IDs that don't exist in live.

        Args:
            live_session: Read session on the live DB.
            candidate_parents: Output of ``_collect_candidate_parents``.

        Returns:
            ``{"characters": [missing_ids...], ...}`` - only keys with at
            least one missing parent are included.
        """
        missing: dict[str, list[int]] = {}

        for plural, model in (
            ("characters", Character),
            ("picture_sets", PictureSet),
            ("projects", Project),
        ):
            wanted = {p["id"] for p in candidate_parents.get(plural, [])}
            if not wanted:
                continue
            live_ids = set(
                live_session.execute(sa_select(model.id).where(model.id.in_(wanted)))
                .scalars()
                .all()
            )
            gone = sorted(wanted - live_ids)
            if gone:
                missing[plural] = gone
        return missing

    @staticmethod
    def _restore_parent_rows(
        live_session: Session,
        candidate_parents: dict[str, list[dict]],
        missing_ids: dict[str, list[int]],
    ) -> int:
        """Re-insert the missing parent rows from the snapshot into live.

        Only rows whose ID is in ``missing_ids`` for their resource type
        are merged; existing live parents (with the same ID) are not
        touched.

        A restored character or picture set also gets its project-membership
        join rows rebuilt from the snapshot (issue #125). The join is the read
        model, so merging the row alone would restore an entity that is
        unreachable from the project it belongs to. Only memberships whose
        project actually exists live are re-created - a project the user did not
        restore would violate the foreign key.

        Args:
            live_session: Live writer session.
            candidate_parents: Output of ``_collect_candidate_parents``.
            missing_ids: Output of ``_find_missing_parent_ids``.

        Returns:
            Number of parent rows restored.
        """
        restored = 0
        pending_memberships: list[tuple[type, str, int, list[int]]] = []
        for plural, model in (
            ("characters", Character),
            ("picture_sets", PictureSet),
            ("projects", Project),
        ):
            wanted = set(missing_ids.get(plural, ()))
            if not wanted:
                continue
            for parent in candidate_parents.get(plural, []):
                if parent["id"] not in wanted:
                    continue
                fields = dict(parent)
                project_ids = fields.pop(_PROJECT_IDS_KEY, None)
                live_session.merge(model(**fields))
                restored += 1
                if project_ids:
                    if model is Character:
                        pending_memberships.append(
                            (
                                CharacterProjectMember,
                                "character_id",
                                int(fields["id"]),
                                project_ids,
                            )
                        )
                    elif model is PictureSet:
                        pending_memberships.append(
                            (
                                PictureSetProjectMember,
                                "set_id",
                                int(fields["id"]),
                                project_ids,
                            )
                        )

        if pending_memberships:
            # Flush the parents first so the membership FKs resolve, and only
            # link projects that exist live.
            live_session.flush()
            wanted_projects = {
                pid for _m, _c, _e, pids in pending_memberships for pid in pids
            }
            live_project_ids = set(
                live_session.execute(
                    sa_select(Project.id).where(Project.id.in_(wanted_projects))
                )
                .scalars()
                .all()
            )
            for (
                membership_model,
                id_field,
                entity_id,
                project_ids,
            ) in pending_memberships:
                for project_id in project_ids:
                    if project_id not in live_project_ids:
                        logger.warning(
                            "Restore: skipping %s membership %s=%s project_id=%s - "
                            "the project does not exist in the live database, so "
                            "the entity comes back without that membership",
                            membership_model.__name__,
                            id_field,
                            entity_id,
                            project_id,
                        )
                        continue
                    live_session.merge(
                        membership_model(
                            **{id_field: entity_id, "project_id": project_id}
                        )
                    )
        return restored

    def _collect_rows_for_upsert(
        self,
        snap_session: Session,
        resource_type: str,
        resource_id: int,
        valid_picture_ids: list[int],
    ) -> dict:
        """Collect rows from the snapshot for all tables we need to upsert.

        Args:
            snap_session: Read session on the snapshot.
            resource_type: The resource type being restored.
            resource_id: Primary key of the resource.
            valid_picture_ids: Filtered list of picture IDs to restore.

        Returns:
            Dict mapping table name → list of row dicts.
        """
        rows: dict = {
            "pictures": [],
            "faces": [],
            "tags": [],
            "picture_set_members": [],
            "picture_project_members": [],
            "character_project_ids": [],
            "picture_set_project_ids": [],
            "character": None,
            "picture_set": None,
            "project": None,
        }

        # Pictures and their dependents.
        for pid in valid_picture_ids:
            pic = snap_session.get(Picture, pid)
            if pic:
                rows["pictures"].append(pic)
            faces = snap_session.exec(select(Face).where(Face.picture_id == pid)).all()
            rows["faces"].extend(faces)
            tags = snap_session.exec(select(Tag).where(Tag.picture_id == pid)).all()
            rows["tags"].extend(tags)
            psms = snap_session.exec(
                select(PictureSetMember).where(PictureSetMember.picture_id == pid)
            ).all()
            rows["picture_set_members"].extend(psms)
            ppms = snap_session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == pid
                )
            ).all()
            rows["picture_project_members"].extend(ppms)

        if resource_type == "picture_set":
            ps = snap_session.get(PictureSet, resource_id)
            rows["picture_set"] = ps
            rows["picture_set_project_ids"] = sorted(
                int(project_id)
                for project_id in snap_session.exec(
                    select(PictureSetProjectMember.project_id).where(
                        PictureSetProjectMember.set_id == resource_id
                    )
                ).all()
                if project_id is not None
            )
        elif resource_type == "project":
            proj = snap_session.get(Project, resource_id)
            rows["project"] = proj
        elif resource_type == "character":
            char = snap_session.get(Character, resource_id)
            rows["character"] = char
            rows["character_project_ids"] = sorted(
                int(project_id)
                for project_id in snap_session.exec(
                    select(CharacterProjectMember.project_id).where(
                        CharacterProjectMember.character_id == resource_id
                    )
                ).all()
                if project_id is not None
            )

        return rows

    def _upsert_rows(
        self, session: Session, snap_rows: dict, valid_picture_ids: list[int]
    ) -> int:
        """Upsert all collected snapshot rows into the live session.

        Args:
            session: Live writer session.
            snap_rows: Collected rows from the snapshot.
            valid_picture_ids: Picture IDs being restored; their picture-scoped
                dependents (Face/Tag/PSM/PPM) are deleted and re-inserted so
                the restored pictures mirror the snapshot exactly.

        Returns:
            Total number of objects upserted.
        """

        count = 0

        def _merge(obj):
            nonlocal count
            if obj is None:
                return
            # Detach from the snapshot session by expunging and merging.
            session.merge(obj)
            count += 1

        if snap_rows.get("project"):
            _merge(snap_rows["project"])
        if snap_rows.get("picture_set"):
            _merge(snap_rows["picture_set"])
        if snap_rows.get("character"):
            _merge(snap_rows["character"])

        # Character/PictureSet project membership is a snapshot-owned
        # collection for root restores, just like a picture's Face/Tag rows.
        # Use the canonical write-both helpers so the join rows are replaced
        # exactly and the legacy scalar project_id is re-derived consistently.
        from pixlstash.services.project_membership_service import (
            set_character_projects,
            set_picture_set_projects,
        )

        root_character = snap_rows.get("character")
        if root_character is not None:
            merged_character = session.get(Character, root_character.id)
            if merged_character is None:
                session.flush()
                merged_character = session.get(Character, root_character.id)
            set_character_projects(
                session,
                merged_character,
                snap_rows.get("character_project_ids", []),
            )

        root_picture_set = snap_rows.get("picture_set")
        if root_picture_set is not None:
            merged_picture_set = session.get(PictureSet, root_picture_set.id)
            if merged_picture_set is None:
                session.flush()
                merged_picture_set = session.get(PictureSet, root_picture_set.id)
            set_picture_set_projects(
                session,
                merged_picture_set,
                snap_rows.get("picture_set_project_ids", []),
            )

        # Scrapheap rows carry the snapshot's ORIGINAL ``deleted_at``. Merging
        # that verbatim hands the retention auto-purge an already-expired
        # deadline, so restoring a snapshot older than the window would let the
        # next 15-minute sweep permanently destroy the very scrapheap the user
        # just restored (and write ``file_removed=True``, making a re-restore
        # unable to bring it back). Re-stamp to now, for the same reason
        # migration 0079 backfills to the migration time: a restore is a fresh
        # start for the retention clock, never a resumed one.
        restore_stamp = datetime.now(timezone.utc)
        for pic in snap_rows.get("pictures", []):
            merged = session.merge(pic)
            count += 1
            if getattr(merged, "deleted", False):
                merged.deleted_at = restore_stamp
                session.add(merged)

        # Picture-scoped dependents (Face, Tag, PictureSetMember,
        # PictureProjectMember): replace, don't merge. Merging by the snapshot
        # row's PK is wrong here because:
        #   1. Face has a surrogate PK shared across all pictures - a snapshot
        #      Face.id can land on an unrelated live face that reused that id.
        #   2. Rows present in live but absent from the snapshot must vanish,
        #      or the picture isn't really reverted to its snapshot state.
        #   3. Tag has UNIQUE(picture_id, tag) which bare merge by snapshot PK
        #      can violate (the original reason this branch existed for tags).
        # Bulk-delete by picture_id, then insert fresh rows. Faces/PSMs/PPMs
        # are constructed without a PK so the DB assigns one.
        if valid_picture_ids:
            for child_model in (Face, Tag, PictureSetMember, PictureProjectMember):
                session.execute(
                    sa_delete(child_model).where(
                        child_model.picture_id.in_(valid_picture_ids)
                    )
                )
            session.flush()
        for face in snap_rows.get("faces", []):
            session.add(
                Face(
                    picture_id=face.picture_id,
                    frame_index=face.frame_index,
                    face_index=face.face_index,
                    character_id=face.character_id,
                    bbox=face.bbox,
                    features=face.features,
                )
            )
            count += 1
        for tag in snap_rows.get("tags", []):
            session.add(Tag(picture_id=tag.picture_id, tag=tag.tag))
            count += 1
        for psm in snap_rows.get("picture_set_members", []):
            session.add(PictureSetMember(set_id=psm.set_id, picture_id=psm.picture_id))
            count += 1
        for ppm in snap_rows.get("picture_project_members", []):
            session.add(
                PictureProjectMember(
                    project_id=ppm.project_id, picture_id=ppm.picture_id
                )
            )
            count += 1

        # Derived columns (embeddings + scores) are intentionally NOT reset:
        # the merged snapshot Picture rows already carry them, so the restored
        # resource comes back fully populated with no regeneration pass.

        session.commit()
        return count
