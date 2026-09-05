"""Picture scoring package.

Two distinct features that formerly shared ``pixlstash.picture_scoring``:

* :mod:`pixlstash.scoring.smart_score` - anchor-based smart-score heuristic.
* :mod:`pixlstash.scoring.character_likeness` - face↔reference likeness scoring.

This package is the public import path for both. Import the private anchor
constants (``_BUILTIN_MIN_GOOD`` and friends) from the submodule directly.
"""

from pixlstash.scoring.character_likeness import (
    compute_character_likeness_for_faces,
    count_pictures_by_character_likeness,
    find_pictures_by_character_likeness,
    find_pictures_by_character_likeness_sql,
    pack_reference_blobs,
    select_reference_faces_for_character,
)
from pixlstash.scoring.smart_score import (
    attach_anomaly_inputs,
    fetch_anomaly_confidences,
    fetch_smart_score_data,
    fetch_smart_score_unscored_ids,
    find_pictures_by_smart_score,
    get_smart_score_penalised_tags_from_request,
    prepare_smart_score_inputs,
    resolve_penalised_tag_weights,
)

__all__ = [
    "attach_anomaly_inputs",
    "compute_character_likeness_for_faces",
    "count_pictures_by_character_likeness",
    "fetch_anomaly_confidences",
    "fetch_smart_score_data",
    "fetch_smart_score_unscored_ids",
    "find_pictures_by_character_likeness",
    "find_pictures_by_character_likeness_sql",
    "find_pictures_by_smart_score",
    "get_smart_score_penalised_tags_from_request",
    "pack_reference_blobs",
    "prepare_smart_score_inputs",
    "resolve_penalised_tag_weights",
    "select_reference_faces_for_character",
]
