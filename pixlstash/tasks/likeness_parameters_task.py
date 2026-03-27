from pixlstash.database import DBPriority
from pixlstash.db_models.picture import (
    LIKENESS_PARAMETER_SENTINEL,
    LikenessParameter,
    Picture,
)
from pixlstash.utils.likeness.likeness_parameter_utils import (
    LikenessParameterUtils,
    PICTURE_PARAM_FIELDS,
    QUALITY_PARAM_FIELDS,
)
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class LikenessParametersTask(BaseTask):
    """Task that computes one batch of likeness parameters."""

    BATCH_SIZE = 128
    SCAN_LIMIT = 2048

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def __init__(self, database):
        super().__init__(
            task_type="LikenessParametersTask",
            params={
                "batch_size": self.BATCH_SIZE,
                "scan_limit": self.SCAN_LIMIT,
            },
        )
        self._db = database

    def _run_task(self):
        helper = LikenessParameterUtils(self._db)

        def submit_low(func, *args, **kwargs):
            return self._db.result_or_throw(
                self._db.submit_task(func, *args, priority=DBPriority.LOW, **kwargs)
            )

        work = submit_low(
            LikenessParameterUtils.find_next_work,
            self.BATCH_SIZE,
            self.SCAN_LIMIT,
        )
        if not work:
            logger.debug(
                "LikenessParametersTask: find_next_work returned None — no pending work"
            )
            return {"changed_count": 0, "changed": []}

        param, _size_bin, payload = work
        changed = []

        logger.debug("LikenessParametersTask: executing param=%s", param.name)

        if param == LikenessParameter.SIZE_BIN:
            width, height, ids = payload
            size_bin_index = helper.size_bin_index(width, height)
            submit_low(
                LikenessParameterUtils.update_size_bin,
                ids,
                size_bin_index,
                len(LikenessParameter),
            )
            changed = [(Picture, pid, "likeness_parameters", None) for pid in ids]
        else:
            ids, _remaining_in_bin = payload
            if param in QUALITY_PARAM_FIELDS:
                quality_by_id = helper.fetch_quality_for_ids(ids)
                submit_low(
                    LikenessParameterUtils.update_quality_values,
                    ids,
                    quality_by_id,
                    len(LikenessParameter),
                )
            elif param in PICTURE_PARAM_FIELDS:
                picture_by_id, picture_updates = helper.fetch_picture_params_for_ids(
                    ids
                )
                if picture_updates:
                    submit_low(
                        LikenessParameterUtils.update_picture_metadata,
                        picture_updates,
                    )
                submit_low(
                    LikenessParameterUtils.update_picture_values,
                    ids,
                    picture_by_id,
                    len(LikenessParameter),
                )
            else:
                values = [LIKENESS_PARAMETER_SENTINEL for _ in ids]
                submit_low(
                    LikenessParameterUtils.update_parameter_values,
                    ids,
                    int(param),
                    values,
                    len(LikenessParameter),
                )
            changed = [(Picture, pid, "likeness_parameters", None) for pid in ids]

        pending_after = self._db.run_immediate_read_task(
            LikenessParameterUtils.count_pending_parameters
        )
        logger.debug(
            "LikenessParametersTask: param=%s updated %d pictures; pending_parameters_remaining=%s",
            param.name,
            len(changed),
            pending_after,
        )
        return {
            "changed_count": len(changed),
            "changed": changed,
        }
