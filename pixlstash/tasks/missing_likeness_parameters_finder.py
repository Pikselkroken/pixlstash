from sqlmodel import Session

from pixlstash.utils.likeness.likeness_parameter_utils import LikenessParameterUtils
from pixlstash.worker_config import LIKENESS_PARAMETERS_MAX_INFLIGHT

from .base_task_finder import BaseTaskFinder
from .likeness_parameters_task import LikenessParametersTask
from .quality_task import QualityTask
from .task_type import TaskType
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class MissingLikenessParametersFinder(BaseTaskFinder):
    """Discover pending likeness-parameter work and create pre-fetched tasks.

    The finder does all the work: SQL scan, claim-filtering, and data
    pre-fetch (quality metrics / picture metadata).  Worker tasks receive
    ready-to-write payloads and never touch the serialised DB queue for reads.
    """

    def __init__(self, database):
        super().__init__()
        self._db = database

    def finder_name(self) -> str:
        return "MissingLikenessParametersFinder"

    def depends_on(self) -> list[TaskType]:
        # Quality metrics are used as likeness parameters (brightness, contrast,
        # etc.).  Wait for all inflight quality tasks to drain before starting
        # parameter batches so every picture gets its real quality values written
        # atomically rather than sentinel-filled and needing a reset later.
        return [TaskType.QUALITY]

    def max_inflight_tasks(self) -> int:
        return LIKENESS_PARAMETERS_MAX_INFLIGHT

    def find_task(self):
        # Hard gate: if any quality work remains, wait.  This ensures likeness
        # parameters are written with final quality values, not sentinels.
        if self._db.run_immediate_read_task(QualityTask.count_missing_quality) > 0:
            return None

        if not self._db.run_immediate_read_task(
            LikenessParameterUtils.has_pending_work
        ):
            return None

        # Discover work and pre-fetch all required data inside a single
        # immediate read session — no write-queue involvement.
        work = self._db.run_immediate_read_task(
            self._find_and_prefetch,
            self._db.image_root,
        )
        if work is None:
            return None

        ids, payload = work
        if not ids:
            return None

        return LikenessParametersTask(
            database=self._db,
            ids=ids,
            payload=payload,
        )

    def _find_and_prefetch(self, session: Session, image_root: str):
        """Run inside a read session: find next work batch and pre-fetch all data."""
        id_wh_list = LikenessParameterUtils.find_next_work(
            session, LikenessParametersTask.SCAN_LIMIT
        )
        if not id_wh_list:
            return None

        all_ids = [t[0] for t in id_wh_list]
        # Claim-filter: exclude IDs already in-flight with another task.
        ids = self._claim_ids(all_ids[: LikenessParametersTask.BATCH_SIZE])
        if not ids:
            return None

        id_set = set(ids)
        size_bin_by_id = {
            pid: LikenessParameterUtils.size_bin_index(w, h)
            for pid, w, h in id_wh_list
            if pid in id_set
        }
        quality_by_id = LikenessParameterUtils.fetch_quality_for_ids(session, ids)
        picture_by_id, picture_updates = (
            LikenessParameterUtils.fetch_picture_params_for_ids(
                session, ids, image_root
            )
        )
        return ids, {
            "size_bin_by_id": size_bin_by_id,
            "quality_by_id": quality_by_id,
            "picture_by_id": picture_by_id,
            "picture_updates": picture_updates,
        }

    def _claim_ids(self, ids: list) -> list:
        """Claim IDs atomically; return only unclaimed ones."""
        claimed = []
        with self._claim_lock:
            for pid in ids:
                if pid not in self._claimed_picture_ids:
                    self._claimed_picture_ids.add(pid)
                    claimed.append(pid)
        return claimed

    @staticmethod
    def _count_pending_parameters(session: Session) -> int:
        return LikenessParameterUtils.count_pending_parameters(session)
