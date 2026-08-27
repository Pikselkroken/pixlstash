"""Run one duplicate scan, streaming its groups into the queue as it goes.

The design's first performance rule is that the queue never blocks on a full
pass: it opens with whatever has been found so far, behind a "scanned N of M"
banner. This task is what makes that true.

* **Tier 1** runs first and in one shot. It is an indexed ``GROUP BY``, so it
  completes in milliseconds and the queue is never empty while the slow tiers
  work.
* **Tier 2** iterates the candidate buckets built by
  :func:`~pixlstash.services.dedup_tier_service.build_near_buckets`, persisting
  and committing after **each bucket**. A bucket's groups are visible in the
  queue the moment that bucket finishes, and the scan's ``scanned_buckets``
  counter advances with it.
* **Tier 3** appends the embedding groups last, because it is the tier whose
  input (the likeness edge table) is the one that may still be filling.

The task is driven by a :class:`~pixlstash.db_models.dedup.DedupScan` row, which
is both the request (status ``pending``) and the progress readout. Restarting a
scan is safe: group persistence upserts on the signature, so a re-run refreshes
rows rather than duplicating them.
"""

import json
import threading
import time

from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.dedup import (
    SCAN_COMPLETE,
    SCAN_FAILED,
    SCAN_PARTIAL,
    SCAN_PENDING,
    SCAN_RUNNING,
    DedupScan,
)
from pixlstash.db_models.picture import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.services import dedup_tier_service
from pixlstash.services.dedup_tier_service import (
    DedupScope,
    DedupTier,
    ScopeType,
    TierPolicy,
)
from pixlstash.tasks.base_task import BaseTask, TaskPriority

logger = get_logger(__name__)

EMBEDDING_COMPONENTS_PER_SLICE = 100


class _DedupScanCancelled(RuntimeError):
    """Internal control flow for a cooperative scan cancellation."""


class DedupScanTask(BaseTask):
    """Execute the tiers requested by one :class:`DedupScan` row."""

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def __init__(self, database, scan_id: int):
        super().__init__(
            task_type="DedupScanTask",
            # ``picture_ids`` is the key BaseTaskFinder releases its claims from;
            # this task claims no pictures, so it is deliberately empty.
            params={"picture_ids": [], "scan_id": int(scan_id)},
        )
        self._db = database
        self._scan_id = int(scan_id)
        self._cancel_requested = threading.Event()
        self._progress_lock = threading.Lock()
        # Task Manager progress counts cooperative scan phases, not pictures.
        # Picture enumeration can reach the library total while near/embedding
        # work is still running, so exposing scanned_pictures here would render
        # a false "N / N" completion for an active task.
        self._processed_count = 0
        self._total_count = 2  # indexed exact setup + finalisation

    def _run_task(self):
        start = time.time()
        try:
            initial = self._run_low_slice(
                DedupScanTask._start_scan_slice,
                self._scan_id,
            )
            policy = TierPolicy(**initial["policy"])
            scope = DedupScope(
                scope_type=ScopeType(initial["scope_type"]),
                scope_id=initial["scope_id"],
            )
            total_pictures = int(initial["total_pictures"])
            found = int(initial["groups_found"])
            signatures_by_tier: dict[str, set[str]] = {
                DedupTier.EXACT.value: set(initial.get("exact_signatures", [])),
                DedupTier.NEAR.value: set(),
                DedupTier.EMBEDDING.value: set(),
            }
            incomplete_tiers: set[str] = set()
            partial_reasons: list[str] = []
            # Initialise for every persisted tier combination. The public
            # TierPolicy currently requires embedding => near, but task resume
            # must not turn that API invariant into an unbound local if an old
            # or manually repaired row contains exact+embedding only.
            total_phases = 2 + int(policy.embedding_enabled)
            self._set_task_progress(1, total_phases)

            if policy.near_enabled:
                buckets = self._run_low_slice(
                    DedupScanTask._prepare_near_slice,
                    self._scan_id,
                    scope,
                )
                # exact setup + near setup + one phase per bucket + optional
                # embedding + finalisation. With zero buckets this still leaves
                # distinct setup/final phases and never reports terminal early.
                total_phases = 3 + len(buckets) + int(policy.embedding_enabled)
                self._set_task_progress(2, total_phases)
                if any(bucket.oversized for bucket in buckets):
                    incomplete_tiers.add(DedupTier.NEAR.value)
                    partial_reasons.append(
                        "near scan used overlapping shards for oversized buckets; "
                        "cross-shard comparisons are incomplete"
                    )
                seen_pictures: set[int] = set()
                pair_cache: dict[tuple[int, int], float] = {}
                pair_cap_reported = False
                for index, bucket in enumerate(buckets, start=1):
                    result = self._run_low_slice(
                        DedupScanTask._run_near_bucket_slice,
                        self._scan_id,
                        bucket,
                        policy,
                        index,
                        len(buckets),
                        total_pictures,
                        int(initial["groups_found"]),
                        pair_cache,
                        seen_pictures,
                        pair_cap_reported,
                    )
                    found = int(result["groups_found"])
                    pair_cap_reported = bool(result["pair_cap_reported"])
                    signatures_by_tier[DedupTier.NEAR.value] = set(
                        result["near_signatures"]
                    )
                    if result["incomplete"]:
                        incomplete_tiers.add(DedupTier.NEAR.value)
                        partial_reasons.extend(result["partial_reasons"])
                    self._set_task_progress(2 + index, total_phases)

            if policy.embedding_enabled:
                in_scope = self._run_low_slice(
                    DedupScanTask._embedding_scope_slice,
                    scope,
                )
                forest = dedup_tier_service.dedup_sweep_service._LikenessForest()
                cursor = (-1, -1)
                while True:
                    page = self._run_low_slice(
                        DedupScanTask._embedding_edge_page_slice,
                        policy.threshold,
                        cursor,
                        in_scope,
                    )
                    for picture_id_a, picture_id_b, likeness in page["edges"]:
                        forest.add_edge(picture_id_a, picture_id_b, likeness)
                    if page["done"]:
                        break
                    cursor = tuple(page["cursor"])

                components = forest.components(policy.min_group_size)
                for start_index in range(
                    0, len(components), EMBEDDING_COMPONENTS_PER_SLICE
                ):
                    persisted = self._run_low_slice(
                        DedupScanTask._persist_embedding_slice,
                        self._scan_id,
                        components[
                            start_index : start_index + EMBEDDING_COMPONENTS_PER_SLICE
                        ],
                        policy,
                    )
                    found += int(persisted["unresolved"])
                    signatures_by_tier[DedupTier.EMBEDDING.value].update(
                        persisted["signatures"]
                    )
                self._set_task_progress(total_phases - 1, total_phases)

            summary = self._run_low_slice(
                DedupScanTask._finish_scan_slice,
                self._scan_id,
                total_pictures,
                found,
                scope.key,
                signatures_by_tier,
                incomplete_tiers,
                partial_reasons,
            )
            self._set_task_progress(self._total_count, self._total_count)
        except _DedupScanCancelled:
            # TaskRunner.stop() calls on_cancel() while the active slice still
            # owns its request-local session. The slice is allowed to finish;
            # only then do we publish the restartable state in a fresh, short
            # callback. Never pass a Session across that boundary.
            self._db.run_task(
                DedupScanTask._mark_pending_after_cancel,
                self._scan_id,
                priority=DBPriority.IMMEDIATE,
            )
            summary = {
                "scan_id": self._scan_id,
                "status": SCAN_PENDING,
                "cancelled": True,
            }
            logger.info(
                "DedupScanTask scan_id=%s stopped at a slice boundary in %.2fs; "
                "the durable scan is pending for restart",
                self._scan_id,
                time.time() - start,
            )
            return summary
        except Exception as exc:
            logger.error("DedupScanTask failed for scan_id=%s: %s", self._scan_id, exc)
            # Failure bookkeeping must still run if cancellation happened at
            # the same time as a genuine slice error.
            self._db.run_task(
                DedupScanTask._mark_failed,
                self._scan_id,
                str(exc),
                priority=DBPriority.IMMEDIATE,
            )
            raise
        logger.info(
            "DedupScanTask scan_id=%s finished in %.2fs: %s",
            self._scan_id,
            time.time() - start,
            summary,
        )
        return summary

    def _run_low_slice(self, func, *args):
        """Run one cooperative DB slice and yield before submitting the next."""
        if self._cancel_requested.is_set():
            raise _DedupScanCancelled
        result = self._db.run_task(func, *args, priority=DBPriority.LOW)
        # Observe cancellation that arrived while the callback was executing.
        # The callback and its request-local session are fully finished here,
        # and no later scan slice will be submitted.
        if self._cancel_requested.is_set() and func is not self._finish_scan_slice:
            raise _DedupScanCancelled
        return result

    def on_cancel(self) -> None:
        """Request a cooperative stop after the current database slice."""
        self._cancel_requested.set()

    def _set_task_progress(self, processed: int, total: int) -> None:
        """Publish an atomic, phase-based Task Manager snapshot."""
        with self._progress_lock:
            self._total_count = max(1, int(total))
            self._processed_count = min(max(0, int(processed)), self._total_count)

    def task_progress(self) -> tuple[int, int]:
        """Return ``(processed phases, total phases)`` for Task Manager."""
        with self._progress_lock:
            return self._processed_count, self._total_count

    @staticmethod
    def _policy_for(scan: DedupScan) -> TierPolicy:
        """Rebuild the requested tier policy from the persisted scan row."""
        tiers = set(json.loads(scan.tiers or "[]"))
        return TierPolicy(
            near_enabled=DedupTier.NEAR.value in tiers,
            embedding_enabled=DedupTier.EMBEDDING.value in tiers,
            threshold=float(scan.threshold or dedup_tier_service.DEFAULT_THRESHOLD),
        )

    @staticmethod
    def _scope_for(scan: DedupScan) -> DedupScope:
        return DedupScope(scope_type=ScopeType(scan.scope_type), scope_id=scan.scope_id)

    @staticmethod
    def _mark_failed(session: Session, scan_id: int, error: str) -> None:
        scan = session.get(DedupScan, scan_id)
        if scan is None:
            logger.warning(
                "[dedup-scan] cannot mark scan %s failed: the row is gone", scan_id
            )
            return
        scan.status = SCAN_FAILED
        scan.error = error[:2000]
        scan.updated_at = datetime.utcnow()
        session.add(scan)
        session.commit()

    @staticmethod
    def _mark_pending_after_cancel(session: Session, scan_id: int) -> None:
        """Make an interrupted scan discoverable on the next planner run."""
        scan = session.get(DedupScan, scan_id)
        if scan is None:
            logger.warning(
                "[dedup-scan] cannot mark cancelled scan %s pending: the row is gone",
                scan_id,
            )
            return
        # A cancellation racing with the final slice must not regress a scan
        # whose complete result has already committed.
        if scan.status in (SCAN_COMPLETE, SCAN_PARTIAL):
            return
        scan.status = SCAN_PENDING
        scan.error = None
        scan.finished_at = None
        scan.updated_at = datetime.utcnow()
        session.add(scan)
        session.commit()

    @staticmethod
    def _start_scan_slice(session: Session, scan_id: int) -> dict:
        """Initialise a scan and publish tier 1 in one bounded indexed slice."""
        scan = session.get(DedupScan, scan_id)
        if scan is None:
            raise ValueError(f"DedupScan {scan_id} no longer exists")
        policy = DedupScanTask._policy_for(scan)
        scope = DedupScanTask._scope_for(scan)

        dedup_tier_service.prune_stale_groups_in_session(session)
        total_query = select(Picture.id).where(Picture.deleted.is_(False))
        predicate = scope.picture_predicate()
        if predicate is not None:
            total_query = total_query.where(predicate)
        total_pictures = len(session.exec(total_query).all())

        scan.status = SCAN_RUNNING
        scan.total_pictures = total_pictures
        scan.scanned_pictures = 0
        scan.scanned_buckets = 0
        scan.total_buckets = 0
        scan.groups_found = 0
        scan.error = None
        scan.finished_at = None
        scan.updated_at = datetime.utcnow()
        session.add(scan)
        session.commit()

        exact_groups = dedup_tier_service.find_exact_groups_in_session(session, scope)
        found = dedup_tier_service.persist_groups_in_session(
            session, exact_groups, scan_id
        )
        scan = session.get(DedupScan, scan_id)
        scan.groups_found = found
        scan.scanned_pictures = total_pictures if not policy.near_enabled else 0
        scan.updated_at = datetime.utcnow()
        session.add(scan)
        session.commit()
        return {
            "policy": policy.as_dict(),
            "scope_type": scope.scope_type.value,
            "scope_id": scope.scope_id,
            "total_pictures": total_pictures,
            "groups_found": found,
            "exact_signatures": [group.signature for group in exact_groups],
        }

    @staticmethod
    def _prepare_near_slice(
        session: Session, scan_id: int, scope: DedupScope
    ) -> list[dedup_tier_service.NearBucket]:
        """Build bounded candidate buckets and publish their total."""
        buckets = dedup_tier_service.build_near_buckets(session, scope)
        scan = session.get(DedupScan, scan_id)
        if scan is None:
            raise ValueError(f"DedupScan {scan_id} no longer exists")
        scan.total_buckets = len(buckets)
        scan.updated_at = datetime.utcnow()
        session.add(scan)
        session.commit()
        return buckets

    @staticmethod
    def _run_near_bucket_slice(
        session: Session,
        scan_id: int,
        bucket: dedup_tier_service.NearBucket,
        policy: TierPolicy,
        index: int,
        total_buckets: int,
        total_pictures: int,
        exact_found: int,
        pair_cache: dict[tuple[int, int], float],
        seen_pictures: set[int],
        pair_cap_reported: bool,
    ) -> dict:
        """Compare and persist one capped bucket; all carried state is plain."""
        touched: set[int] = set()
        bucket_status: dict = {}
        for a, b, similarity in dedup_tier_service.near_pairs_in_bucket(
            session, bucket, policy.threshold, bucket_status
        ):
            key = (a, b)
            if key not in pair_cache and len(pair_cache) >= (
                dedup_tier_service.MAX_TRACKED_PAIRS
            ):
                if not pair_cap_reported:
                    logger.warning(
                        "[dedup-scan] scan %s reached the %d tracked-pair cap "
                        "at bucket %d of %d; further cross-bucket chaining is "
                        "dropped and some chains may be reported as separate "
                        "groups. Narrow the scope or raise MAX_TRACKED_PAIRS.",
                        scan_id,
                        dedup_tier_service.MAX_TRACKED_PAIRS,
                        index,
                        total_buckets,
                    )
                    pair_cap_reported = True
                continue
            if similarity > pair_cache.get(key, 0.0):
                pair_cache[key] = similarity
                touched.update(key)
        seen_pictures.update(bucket.picture_ids)

        near_groups = 0
        groups = []
        if pair_cache:
            groups = dedup_tier_service.groups_from_pairs(
                session,
                [(a, b, sim) for (a, b), sim in pair_cache.items()],
                policy,
                DedupTier.NEAR,
            )
            near_groups = len(groups)
            if touched:
                changed = [
                    group for group in groups if touched.intersection(group.picture_ids)
                ]
                dedup_tier_service.persist_groups_in_session(session, changed, scan_id)

        found = exact_found + near_groups
        scan = session.get(DedupScan, scan_id)
        if scan is None:
            raise ValueError(f"DedupScan {scan_id} no longer exists")
        scan.scanned_buckets = index
        scan.scanned_pictures = min(len(seen_pictures), total_pictures)
        scan.groups_found = found
        scan.updated_at = datetime.utcnow()
        session.add(scan)
        session.commit()
        partial_reasons = []
        if bucket_status.get("truncated"):
            partial_reasons.append(
                f"near bucket {bucket.kind}={bucket.key} hit the "
                f"{dedup_tier_service.MAX_PAIRS_PER_BUCKET}-pair cap"
            )
        if pair_cap_reported:
            partial_reasons.append(
                f"near scan hit the {dedup_tier_service.MAX_TRACKED_PAIRS} "
                "tracked-pair cap"
            )
        return {
            "groups_found": found,
            "pair_cap_reported": pair_cap_reported,
            "near_signatures": [group.signature for group in groups]
            if pair_cache
            else [],
            "incomplete": bool(partial_reasons),
            "partial_reasons": partial_reasons,
        }

    @staticmethod
    def _embedding_scope_slice(
        session: Session, scope: DedupScope
    ) -> Optional[set[int]]:
        """Return a plain live-id filter for a scoped embedding scan."""
        predicate = scope.picture_predicate()
        if predicate is None:
            return None
        return {
            int(row)
            for row in session.exec(
                select(Picture.id).where(Picture.deleted.is_(False), predicate)
            ).all()
        }

    @staticmethod
    def _embedding_edge_page_slice(
        session: Session,
        threshold: float,
        cursor: tuple[int, int],
        in_scope: Optional[set[int]],
    ) -> dict:
        """Read one keyset page of embedding edges, returning plain tuples."""
        page_size = dedup_tier_service.dedup_sweep_service.EDGE_PAGE_SIZE
        rows = dedup_tier_service.dedup_sweep_service.likeness_edge_page_in_session(
            session,
            threshold,
            after=cursor,
            page_size=page_size,
        )
        edges = rows
        if in_scope is not None:
            edges = [
                edge for edge in rows if edge[0] in in_scope and edge[1] in in_scope
            ]
        return {
            "edges": edges,
            "cursor": rows[-1][:2] if rows else cursor,
            "done": len(rows) < page_size,
        }

    @staticmethod
    def _persist_embedding_slice(
        session: Session,
        scan_id: int,
        components: list[tuple[list[int], float, float]],
        policy: TierPolicy,
    ) -> dict:
        """Assemble and persist one bounded component batch."""
        groups = dedup_tier_service.groups_from_components(
            session, components, policy, DedupTier.EMBEDDING
        )
        return {
            "unresolved": dedup_tier_service.persist_groups_in_session(
                session, groups, scan_id
            ),
            "signatures": [group.signature for group in groups],
        }

    @staticmethod
    def _finish_scan_slice(
        session: Session,
        scan_id: int,
        total_pictures: int,
        found: int,
        scope_key: str,
        signatures_by_tier: dict[str, set[str]],
        incomplete_tiers: set[str],
        partial_reasons: list[str],
    ) -> dict:
        scan = session.get(DedupScan, scan_id)
        if scan is None:
            raise ValueError(f"DedupScan {scan_id} no longer exists")
        requested_tiers = set(json.loads(scan.tiers or "[]"))
        complete_tiers = requested_tiers - set(incomplete_tiers)
        retired = dedup_tier_service.retire_obsolete_scan_groups_in_session(
            session,
            scan_id,
            signatures_by_tier,
            complete_tiers,
        )
        unique_reasons = list(dict.fromkeys(partial_reasons))
        scan.status = SCAN_PARTIAL if unique_reasons else SCAN_COMPLETE
        scan.error = "; ".join(unique_reasons)[:2000] if unique_reasons else None
        scan.scanned_pictures = total_pictures
        scan.groups_found = found
        scan.finished_at = datetime.utcnow()
        scan.updated_at = scan.finished_at
        session.add(scan)
        session.commit()
        return {
            "scan_id": scan_id,
            "scope": scope_key,
            "total_pictures": total_pictures,
            "groups_found": found,
            "status": scan.status,
            "retired_groups": retired,
            "partial_reasons": unique_reasons,
        }

    @staticmethod
    def run_scan_in_session(session: Session, scan_id: int) -> dict:
        """Run every requested tier for *scan_id*, committing as tiers finish."""
        scan = session.get(DedupScan, scan_id)
        if scan is None:
            raise ValueError(f"DedupScan {scan_id} no longer exists")
        policy = DedupScanTask._policy_for(scan)
        scope = DedupScanTask._scope_for(scan)

        dedup_tier_service.prune_stale_groups_in_session(session)

        total_query = select(Picture.id).where(Picture.deleted.is_(False))
        predicate = scope.picture_predicate()
        if predicate is not None:
            total_query = total_query.where(predicate)
        total_pictures = len(session.exec(total_query).all())

        scan.status = SCAN_RUNNING
        scan.total_pictures = total_pictures
        scan.scanned_pictures = 0
        scan.scanned_buckets = 0
        scan.total_buckets = 0
        scan.groups_found = 0
        scan.error = None
        scan.finished_at = None
        scan.updated_at = datetime.utcnow()
        session.add(scan)
        session.commit()

        found = 0
        signatures_by_tier: dict[str, set[str]] = {
            DedupTier.EXACT.value: set(),
            DedupTier.NEAR.value: set(),
            DedupTier.EMBEDDING.value: set(),
        }
        incomplete_tiers: set[str] = set()
        partial_reasons: list[str] = []

        # --- Tier 1: exact. Indexed GROUP BY, so the queue fills immediately.
        exact_groups = dedup_tier_service.find_exact_groups_in_session(session, scope)
        signatures_by_tier[DedupTier.EXACT.value] = {
            group.signature for group in exact_groups
        }
        found += dedup_tier_service.persist_groups_in_session(
            session, exact_groups, scan_id
        )
        scan = session.get(DedupScan, scan_id)
        scan.groups_found = found
        scan.scanned_pictures = total_pictures if not policy.near_enabled else 0
        scan.updated_at = datetime.utcnow()
        session.add(scan)
        session.commit()

        # --- Tier 2: bucketed near, one commit per bucket.
        if policy.near_enabled:
            buckets = dedup_tier_service.build_near_buckets(session, scope)
            if any(bucket.oversized for bucket in buckets):
                incomplete_tiers.add(DedupTier.NEAR.value)
                partial_reasons.append(
                    "near scan used overlapping shards for oversized buckets; "
                    "cross-shard comparisons are incomplete"
                )
            scan = session.get(DedupScan, scan_id)
            scan.total_buckets = len(buckets)
            scan.updated_at = datetime.utcnow()
            session.add(scan)
            session.commit()

            seen_pictures: set[int] = set()
            # Pairs are kept across buckets so a chain spanning two buckets folds
            # into ONE group rather than two. That is a real requirement, but it
            # is also the scan's only unbounded structure, so it is capped -
            # see MAX_TRACKED_PAIRS.
            pair_cache: dict[tuple[int, int], float] = {}
            pair_cap_reported = False
            near_groups = 0
            groups = []
            for index, bucket in enumerate(buckets, start=1):
                # Ids whose grouping this bucket could have changed. Only groups
                # touching one of these are re-persisted below; previously the
                # scan re-derived and re-wrote EVERY group after EVERY bucket,
                # which is O(buckets x groups) DELETE+INSERT on the single DB
                # writer thread - every import, tag edit and verdict queues
                # behind a running scan.
                touched: set[int] = set()
                bucket_status: dict = {}
                for a, b, similarity in dedup_tier_service.near_pairs_in_bucket(
                    session, bucket, policy.threshold, bucket_status
                ):
                    key = (a, b)
                    if key not in pair_cache and len(pair_cache) >= (
                        dedup_tier_service.MAX_TRACKED_PAIRS
                    ):
                        if not pair_cap_reported:
                            # Never silent: the scan continues and every bucket is
                            # still compared, but cross-bucket chaining stops
                            # growing, so two buckets' halves of one chain can be
                            # reported as two groups instead of one.
                            logger.warning(
                                "[dedup-scan] scan %s reached the %d tracked-pair "
                                "cap at bucket %d of %d; further cross-bucket "
                                "chaining is dropped and some chains may be "
                                "reported as separate groups. Narrow the scope or "
                                "raise MAX_TRACKED_PAIRS.",
                                scan_id,
                                dedup_tier_service.MAX_TRACKED_PAIRS,
                                index,
                                len(buckets),
                            )
                            pair_cap_reported = True
                        continue
                    if similarity > pair_cache.get(key, 0.0):
                        pair_cache[key] = similarity
                        touched.update(key)
                seen_pictures.update(bucket.picture_ids)

                if touched:
                    groups = dedup_tier_service.groups_from_pairs(
                        session,
                        [(a, b, sim) for (a, b), sim in pair_cache.items()],
                        policy,
                        DedupTier.NEAR,
                    )
                    near_groups = len(groups)
                    # Persist only the groups this bucket could have changed. A
                    # group untouched by this bucket is byte-identical to the row
                    # already stored, so rewriting it buys nothing.
                    changed = [
                        group
                        for group in groups
                        if touched.intersection(group.picture_ids)
                    ]
                    dedup_tier_service.persist_groups_in_session(
                        session, changed, scan_id
                    )
                if pair_cache:
                    # ``groups`` is the complete forest over every retained pair
                    # so far, not only this bucket's changed slice.
                    signatures_by_tier[DedupTier.NEAR.value] = {
                        group.signature for group in groups
                    }
                if bucket_status.get("truncated"):
                    incomplete_tiers.add(DedupTier.NEAR.value)
                    partial_reasons.append(
                        f"near bucket {bucket.kind}={bucket.key} hit the "
                        f"{dedup_tier_service.MAX_PAIRS_PER_BUCKET}-pair cap"
                    )
                scan = session.get(DedupScan, scan_id)
                scan.scanned_buckets = index
                scan.scanned_pictures = min(len(seen_pictures), total_pictures)
                scan.groups_found = found + near_groups
                scan.updated_at = datetime.utcnow()
                session.add(scan)
                session.commit()
            found = session.get(DedupScan, scan_id).groups_found
            if pair_cap_reported:
                incomplete_tiers.add(DedupTier.NEAR.value)
                partial_reasons.append(
                    f"near scan hit the {dedup_tier_service.MAX_TRACKED_PAIRS} "
                    "tracked-pair cap"
                )

        # --- Tier 3: embedding, appended to the same queue.
        if policy.embedding_enabled:
            embedding_groups = dedup_tier_service.find_embedding_groups_in_session(
                session, policy, scope
            )
            signatures_by_tier[DedupTier.EMBEDDING.value] = {
                group.signature for group in embedding_groups
            }
            found += dedup_tier_service.persist_groups_in_session(
                session, embedding_groups, scan_id
            )

        scan = session.get(DedupScan, scan_id)
        complete_tiers = set(json.loads(scan.tiers or "[]")) - incomplete_tiers
        retired = dedup_tier_service.retire_obsolete_scan_groups_in_session(
            session,
            scan_id,
            signatures_by_tier,
            complete_tiers,
        )
        unique_reasons = list(dict.fromkeys(partial_reasons))
        scan.status = SCAN_PARTIAL if unique_reasons else SCAN_COMPLETE
        scan.error = "; ".join(unique_reasons)[:2000] if unique_reasons else None
        scan.scanned_pictures = total_pictures
        scan.groups_found = found
        scan.finished_at = datetime.utcnow()
        scan.updated_at = scan.finished_at
        session.add(scan)
        session.commit()
        return {
            "scan_id": scan_id,
            "scope": scope.key,
            "total_pictures": total_pictures,
            "groups_found": found,
            "status": scan.status,
            "retired_groups": retired,
            "partial_reasons": unique_reasons,
        }

    @staticmethod
    def find_pending_scan(session: Session) -> "DedupScan | None":
        """Oldest scan waiting to run, or the one interrupted mid-flight.

        A ``running`` row with no live task means the server restarted during a
        scan; picking it up again is correct because group persistence is an
        upsert on the signature.
        """
        return session.exec(
            select(DedupScan)
            .where(DedupScan.status.in_([SCAN_PENDING, SCAN_RUNNING]))
            .order_by(DedupScan.started_at)
        ).first()
