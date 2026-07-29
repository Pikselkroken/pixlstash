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
import time

from datetime import datetime

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.dedup import (
    SCAN_COMPLETE,
    SCAN_FAILED,
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

    def _run_task(self):
        start = time.time()
        try:
            summary = self._db.run_task(
                DedupScanTask.run_scan_in_session,
                self._scan_id,
                priority=DBPriority.LOW,
            )
        except Exception as exc:
            logger.error("DedupScanTask failed for scan_id=%s: %s", self._scan_id, exc)
            self._db.run_task(
                DedupScanTask._mark_failed,
                self._scan_id,
                str(exc),
                priority=DBPriority.LOW,
            )
            raise
        logger.info(
            "DedupScanTask scan_id=%s finished in %.2fs: %s",
            self._scan_id,
            time.time() - start,
            summary,
        )
        return summary

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

        # --- Tier 1: exact. Indexed GROUP BY, so the queue fills immediately.
        exact_groups = dedup_tier_service.find_exact_groups_in_session(session, scope)
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
            scan = session.get(DedupScan, scan_id)
            scan.total_buckets = len(buckets)
            scan.updated_at = datetime.utcnow()
            session.add(scan)
            session.commit()

            seen_pictures: set[int] = set()
            pair_cache: dict[tuple[int, int], float] = {}
            for index, bucket in enumerate(buckets, start=1):
                for a, b, similarity in dedup_tier_service.near_pairs_in_bucket(
                    session, bucket, policy.threshold
                ):
                    key = (a, b)
                    if similarity > pair_cache.get(key, 0.0):
                        pair_cache[key] = similarity
                seen_pictures.update(bucket.picture_ids)
                # Groups are re-derived from every pair seen so far, so a chain
                # that spans two buckets becomes one group rather than two.
                groups = dedup_tier_service.groups_from_pairs(
                    session,
                    [(a, b, sim) for (a, b), sim in pair_cache.items()],
                    policy,
                    DedupTier.NEAR,
                )
                dedup_tier_service.persist_groups_in_session(session, groups, scan_id)
                scan = session.get(DedupScan, scan_id)
                scan.scanned_buckets = index
                scan.scanned_pictures = min(len(seen_pictures), total_pictures)
                scan.groups_found = found + len(groups)
                scan.updated_at = datetime.utcnow()
                session.add(scan)
                session.commit()
            found = session.get(DedupScan, scan_id).groups_found

        # --- Tier 3: embedding, appended to the same queue.
        if policy.embedding_enabled:
            embedding_groups = dedup_tier_service.find_embedding_groups_in_session(
                session, policy, scope
            )
            found += dedup_tier_service.persist_groups_in_session(
                session, embedding_groups, scan_id
            )

        scan = session.get(DedupScan, scan_id)
        scan.status = SCAN_COMPLETE
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
