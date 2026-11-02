"""How much each training preview looks like the run's character (#2522).

A run's previews are files in its folder, not library pictures, so the face
pipeline never sees them. :class:`~pixlstash.tasks.training_preview_faces_task.TrainingPreviewFacesTask`
embeds their faces as they land, into ``<work_dir>/preview_faces.json``; the
live view scores those embeddings against the character's reference faces on
every read, so a reference added later counts without embedding anything again.

The score is the grid's character likeness, shown 0-100. A step's likeness is
the mean over its previews that have a face: a back view is left out rather
than scored 0.
"""

from __future__ import annotations

import base64
import json
import os
import statistics
import tempfile
from types import SimpleNamespace

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.scoring import compute_character_likeness_for_faces

logger = get_logger(__name__)

PREVIEW_FACES_FILE = "preview_faces.json"
_VERSION = 1


def faces_path(work_dir: str) -> str:
    """Where a run keeps its previews' face embeddings."""
    return os.path.join(work_dir, PREVIEW_FACES_FILE)


def read_faces(work_dir: str, model_pack: str | None = None) -> dict:
    """The previews looked at so far: ``filename -> embeddings``.

    An empty list is a preview with no face; None one that could not be read.
    A cache written by another InsightFace pack than *model_pack* reads as
    empty, so its previews are embedded again; *model_pack* None takes any.
    """
    path = faces_path(work_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            document = json.load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning(
            "Unreadable preview faces %s, so its previews are embedded again: %s",
            path,
            exc,
        )
        return {}
    if not isinstance(document, dict) or document.get("version") != _VERSION:
        return {}
    if model_pack is not None and document.get("model_pack") != model_pack:
        return {}
    previews = document.get("previews")
    if not isinstance(previews, dict):
        return {}
    out = {}
    for filename, faces in previews.items():
        if faces is None:
            out[filename] = None
            continue
        try:
            out[filename] = [
                np.frombuffer(base64.b64decode(face), dtype=np.float32)
                for face in faces
            ]
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Skipping preview %s in %s, its faces do not decode: %s",
                filename,
                path,
                exc,
            )
    return out


def write_faces(work_dir: str, model_pack: str, found: dict) -> bool:
    """Merge *found* (as :func:`read_faces` returns it) into the run's cache.

    Written whole to a temporary file and moved into place, so a reader never
    sees half of it. Only the task writes, one per run at a time.

    Returns:
        False when the run's work dir is gone (the run was deleted).
    """
    if not os.path.isdir(work_dir):
        logger.info("Run folder %s is gone; its preview faces are dropped.", work_dir)
        return False
    previews = {**read_faces(work_dir, model_pack), **found}
    document = {
        "version": _VERSION,
        "model_pack": model_pack,
        "previews": {
            filename: None
            if faces is None
            else [
                base64.b64encode(np.asarray(face, np.float32).tobytes()).decode()
                for face in faces
            ]
            for filename, faces in previews.items()
        },
    }
    fd, tmp = tempfile.mkstemp(prefix=".preview_faces.", dir=work_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(document, fh)
        os.replace(tmp, faces_path(work_dir))
    except OSError:
        try:
            os.remove(tmp)
        except OSError as exc:
            logger.warning("Could not remove the temporary file %s: %s", tmp, exc)
        raise
    return True


def _percent(similarity: float) -> int:
    return round(max(0.0, min(1.0, similarity)) * 100)


def score(rows: list[dict], columns: list[int], faces: dict, references: list) -> dict:
    """Score every preview in *rows* in place, and give the run's two series.

    Each cell gains ``face`` (True, False for no face, None when not looked at
    or unreadable) and ``likeness`` (0-100, None without a face or without
    reference faces to compare with).

    Returns:
        ``{with_trigger, without_trigger, best_step, best, pending, references}``:
        one mean per column for the prompts with the trigger word and for those
        without (None where no preview has a face), the step after step 0 that
        is most like the character and its score, how many previews are still
        to be looked at, and how many reference faces were compared with.
    """
    candidates = [
        SimpleNamespace(
            id=(filename, n), features=np.asarray(face, np.float32).tobytes()
        )
        for filename, found in faces.items()
        for n, face in enumerate(found or [])
    ]
    similarity = compute_character_likeness_for_faces(references, candidates)
    best_of: dict[str, float] = {}
    for (filename, _), value in similarity.items():
        best_of[filename] = max(value, best_of.get(filename, -1.0))

    pending = 0
    series = {True: [[] for _ in columns], False: [[] for _ in columns]}
    for row in rows:
        for i, cell in enumerate(row.get("cells") or []):
            cell["face"] = None
            cell["likeness"] = None
            filename = cell.get("filename")
            if cell.get("state") != "sample" or not filename:
                continue
            if filename not in faces:
                pending += 1
                continue
            found = faces[filename]
            if found is None:
                continue
            cell["face"] = bool(found)
            if filename in best_of:
                cell["likeness"] = _percent(best_of[filename])
                if row.get("with_trigger") in series:
                    series[row["with_trigger"]][i].append(best_of[filename])

    def means(scores):
        return [_percent(statistics.fmean(s)) if s else None for s in scores]

    with_trigger = means(series[True])
    best_index = max(
        (
            i
            for i, value in enumerate(with_trigger)
            if columns[i] > 0 and value is not None
        ),
        key=lambda i: with_trigger[i],
        default=None,
    )
    return {
        "with_trigger": with_trigger,
        "without_trigger": means(series[False]),
        "best_step": columns[best_index] if best_index is not None else None,
        "best": with_trigger[best_index] if best_index is not None else None,
        "pending": pending,
        "references": len(references),
    }


__all__ = [
    "PREVIEW_FACES_FILE",
    "faces_path",
    "read_faces",
    "score",
    "write_faces",
]
