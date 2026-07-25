"""Compatibility shim for the former monolithic ``picture_scoring`` module.

The module was split (Backend Refactor Phase 2 §4.6) into two focused siblings:

* :mod:`pixlstash.scoring.smart_score` — anchor-based smart-score heuristic.
* :mod:`pixlstash.scoring.character_likeness` — face↔reference likeness scoring.

This module re-exports every public (and the handful of internal) symbol that
external callers historically imported from ``pixlstash.picture_scoring`` so
those import paths keep resolving unchanged. New code should import from
:mod:`pixlstash.scoring` (or the submodules) directly.
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
    _BUILTIN_MIN_BAD,
    _BUILTIN_MIN_GOOD,
    _BuiltinAnchor,
    _load_builtin_anchors,
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
    "_BUILTIN_MIN_BAD",
    "_BUILTIN_MIN_GOOD",
    "_BuiltinAnchor",
    "_load_builtin_anchors",
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
