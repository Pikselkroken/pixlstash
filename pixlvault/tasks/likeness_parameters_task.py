from pixlvault.database import DBPriority
from pixlvault.db_models.picture import (
    LIKENESS_PARAMETER_SENTINEL,
    LikenessParameter,
    Picture,
)
from pixlvault.picture_likeness_parameter_utils import (
    PictureLikenessParameterUtils,
    PICTURE_PARAM_FIELDS,
    QUALITY_PARAM_FIELDS,
)
from pixlvault.task_runner import BaseTask


class LikenessParametersTask(BaseTask):
    """Task that computes one likeness-parameter batch."""

    BATCH_SIZE = 128
    SCAN_LIMIT = 2048

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
        helper = PictureLikenessParameterUtils(self._db)

        def submit_low(func, *args, **kwargs):
            return self._db.result_or_throw(
                self._db.submit_task(func, *args, priority=DBPriority.LOW, **kwargs)
            )

        work = submit_low(
            PictureLikenessParameterUtils.find_next_work,
            self.BATCH_SIZE,
            self.SCAN_LIMIT,
        )
        if not work:
            return {"changed_count": 0, "changed": []}

        param, _size_bin, payload = work
        changed = []

        if param == LikenessParameter.SIZE_BIN:
            width, height, ids = payload
            size_bin_index = helper._size_bin_index(width, height)
            submit_low(
                PictureLikenessParameterUtils.update_size_bin,
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
                    PictureLikenessParameterUtils.update_quality_values,
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
                        PictureLikenessParameterUtils.update_picture_metadata,
                        picture_updates,
                    )
                submit_low(
                    PictureLikenessParameterUtils.update_picture_values,
                    ids,
                    picture_by_id,
                    len(LikenessParameter),
                )
            else:
                values = [LIKENESS_PARAMETER_SENTINEL for _ in ids]
                submit_low(
                    PictureLikenessParameterUtils.update_parameter_values,
                    ids,
                    int(param),
                    values,
                    len(LikenessParameter),
                )
            changed = [(Picture, pid, "likeness_parameters", None) for pid in ids]

        return {
            "changed_count": len(changed),
            "changed": changed,
        }
