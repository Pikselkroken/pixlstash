"""The folder-structure read: propose what each level of a folder tree is.

v1.11 Phase 2 (``docs/plans/v1.11.0-existing-library.md`` §4). Four signals, all
deterministic and local — a folder name is a string, so no LLM and no language
reading of names:

``cardinality``
    Few names repeating under many parents is a facet, not a thing → Tag. A
    property of a *level*, so it is the only signal that speaks at level scope.
``sidecars``
    A caption ``.txt``/``.caption`` beside every picture in a folder → Set. A
    filesystem fact.
``faces``
    One identity across a folder's pictures → Person, **sampled at
    ``SAMPLED_PER_FOLDER`` pictures per folder**, which is what makes the read
    two minutes instead of an hour.
``name_match``
    The folder name against entities the vault already has → that entity. A
    lookup, not an inference.

Every proposal carries the evidence that produced it; a signal that cannot state
its reason proposes nothing. Where the signals only narrow the answer the
remaining ``candidates`` are returned rather than one of them being picked.

**This module reads. It never writes** — no row is created and no file is opened
for writing, moved or renamed. The wire contract is
``docs/integration_architecture.md`` §20.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PIL import Image

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: Pictures sampled per folder for the face signal. The whole point of the
#: number: 20 × a few hundred folders is two minutes of inference, the full
#: folders would be an hour. The full pass runs later as background work.
SAMPLED_PER_FOLDER = 20

#: Below this many pictures in a folder the face signal stays silent. "One face,
#: 2 of 3" is not evidence anyone should act on, and this signal's contract is
#: that it either states a reason or says nothing.
MIN_FACE_SAMPLE = 5

#: Share of the *sampled* pictures that must carry the same identity for the
#: folder to read as one person.
FACE_MAJORITY = 0.7

# ponytail: one cosine threshold and a medoid vote, not a clustering library.
# InsightFace ArcFace embeddings are L2-normalised, so this is a plain dot
# product; 0.35 is the conventional same-identity floor for ArcFace and is
# deliberately on the strict side — a missed Person row costs the owner one
# dropdown, a wrong one costs them trust in every other row on the screen.
# Upgrade path if it proves noisy: agglomerative clustering over the folder.
SAME_IDENTITY_COSINE = 0.35

#: Wall-clock budget for one whole read. The face signal has a per-batch
#: timeout, but a per-batch timeout multiplied by 20,000 folders is 69 days, so
#: the bound that actually holds has to be on the read. Past it the read stops
#: and returns what it found — the same shape a cancel produces.
DEFAULT_DEADLINE_S = 30 * 60.0

#: Hard bound on the walk. An arbitrary caller-supplied path can be ``/``, and
#: the result is a JSON document a browser has to hold. Hitting it truncates the
#: read and says so rather than running out of memory.
MAX_FOLDERS = 20_000

#: Cardinality reads a level as Tag when its names repeat: at most this many
#: distinct names, at least ``_TAG_REPEAT_FACTOR`` folders per name, and spread
#: over at least ``_TAG_MIN_PARENTS`` parents.
_TAG_MAX_DISTINCT_NAMES = 12
_TAG_REPEAT_FACTOR = 3
_TAG_MIN_PARENTS = 3

#: Share of a level's folders that must agree before the level takes their
#: answer as its own. Compared with integer arithmetic — ``round(0.6 * 4)`` is
#: 2, which would quietly make this a fifty-percent rule on a level of four.
_LEVEL_VOTE_SHARE_PCT = 60

#: Kinds a level can narrow to once cardinality has ruled Tag out.
_NON_TAG_KINDS = ("project", "set", "person")

_IMAGE_EXTS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".heic", ".heif", ".avif"}
)
#: Lower-cased and compared lower-cased: a `.TXT` beside every picture is a
#: caption file, and a dataset exported on Windows is the obvious victim of
#: matching this case-sensitively.
_SIDECAR_EXTS = (".txt", ".caption")

#: Max side the sampled pictures are decoded at for detection. Mirrors
#: FaceExtractionTask.INFERENCE_MAX_SIDE — 2× InsightFace's det_size.
_INFERENCE_MAX_SIDE = 512

#: I/O + decode threads feeding the (sequential) detection batch.
_PRELOAD_WORKERS = 4

_ENTITY_KIND = {
    "project": "project",
    "set": "set",
    "character": "person",
    "tag": "tag",
}


def normalise_name(name: str) -> str:
    """Fold a folder name for comparison against an entity name.

    Case, separators and runs of punctuation are noise here: ``2024_Shoots``,
    ``2024 shoots`` and ``2024-Shoots`` are the same name to an owner. So are
    ``Jose`` and ``José`` — accents are folded, which is why the decomposition
    runs before the substitution.

    **Unicode-aware on purpose.** An ASCII-only class here does not merely miss
    a match, it folds every Cyrillic, CJK, Greek or Hebrew name to the *same*
    empty string, at which point a level of fifteen distinct people reads as one
    repeated name and the cardinality signal confidently proposes Tag.
    """
    decomposed = unicodedata.normalize("NFKD", name.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", stripped, flags=re.UNICODE).strip()


@dataclass
class _Folder:
    """One folder found by the walk, before any signal has run."""

    depth: int
    index: int
    name: str
    abs_path: str
    rel_path: str
    parent_index: Optional[int]
    direct_pictures: list[str] = field(default_factory=list)
    with_sidecar: int = 0
    child_count: int = 0
    picture_count: int = 0  # recursive; filled after the walk
    face_sampled: int = 0
    face_matched: int = 0


class ReadCancelled(Exception):
    """Raised inside the read when the caller cancelled it."""


class FolderStructureRead:
    """One run of the read over one folder tree.

    Args:
        root: Absolute path to the folder to read. Already validated and
            contained by the caller — this class does no authorization.
        detect_faces: ``(list[np.ndarray]) -> list[list[FaceResult]]``, or
            ``None`` to skip the face signal entirely (no inference engine).
        existing_entities: ``[(entity_type, id, name), …]`` the vault already
            holds, for the name-match signal.
        progress: Called as ``(stage, processed, total)`` whenever either moves.
        deadline_s: Wall-clock budget for the whole read. Past it the read stops
            where it is and returns what it has, exactly as a cancel does.
    """

    def __init__(
        self,
        root: str,
        *,
        detect_faces: Optional[Callable[[list], list]] = None,
        existing_entities: Optional[list[tuple[str, Optional[int], str]]] = None,
        progress: Optional[Callable[[str, int, int], None]] = None,
        deadline_s: float = DEFAULT_DEADLINE_S,
    ) -> None:
        self._root = root
        self._detect_faces = detect_faces
        self._progress = progress or (lambda stage, processed, total: None)
        self._cancel = threading.Event()
        self._folders: list[_Folder] = []
        self._truncated = False
        self._unreadable = 0
        self._faces_ran = False
        self._deadline = time.monotonic() + deadline_s
        # key -> entity_type -> the rows of that type sharing the name.
        by_type: dict[str, dict[str, list[tuple[str, Optional[int], str]]]] = {}
        for entity_type, entity_id, name in existing_entities or []:
            key = normalise_name(name)
            if not key:
                continue
            by_type.setdefault(key, {}).setdefault(entity_type, []).append(
                (entity_type, entity_id, name)
            )
        # Two rows of the SAME type sharing a name (PictureSet.name is not
        # unique, and a real vault has duplicates on day one) means the name
        # does not address one entity. Keep the kind — that much IS known — and
        # drop the id rather than hand back whichever row the query returned
        # first for §20's "that row's real primary key".
        self._by_name: dict[str, list[tuple[str, Optional[int], str]]] = {}
        self._ambiguous_types: dict[str, dict[str, int]] = {}
        for key, per_type in by_type.items():
            self._by_name[key] = [rows[0] for rows in per_type.values()]
            duplicated = {t: len(rows) for t, rows in per_type.items() if len(rows) > 1}
            if duplicated:
                self._ambiguous_types[key] = duplicated

    def cancel(self) -> None:
        """Ask the run to stop at its next checkpoint."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def run(self) -> dict[str, Any]:
        """Walk, run the four signals, and return the §20 result document.

        A cancel between stages stops the run and returns whatever the stages
        that did complete found — a partial read is still worth showing.
        """
        try:
            self._walk()
            self._read_faces()
        except ReadCancelled:
            logger.info(
                "Folder-structure read cancelled after %d folders", len(self._folders)
            )
        return self._build_result()

    # ── stages ──────────────────────────────────────────────────────────

    def _checkpoint(self) -> None:
        if self._cancel.is_set():
            raise ReadCancelled()
        if time.monotonic() > self._deadline:
            logger.warning(
                "Folder-structure read: out of time after %d folders — returning "
                "what was found rather than running on",
                len(self._folders),
            )
            self._cancel.set()
            raise ReadCancelled()

    def _walk(self) -> None:
        """Collect every folder under the root, and count its sidecars as it goes.

        One pass. ``os.walk`` already hands back the filenames the sidecar
        signal needs, so listing every folder a second time would buy nothing
        but a TOCTOU window.
        """
        self._progress("walking", 0, 0)
        # index of a folder by its absolute path, so a child can name its parent
        by_path: dict[str, int] = {}
        root = os.path.normpath(self._root)

        def on_error(exc: OSError) -> None:
            """``os.walk`` swallows these by default. Do not let it.

            A folder the process cannot read is dropped from the tree with no
            exception and no return value, so the read would otherwise report a
            *complete* map of a library it only partly saw — and the owner would
            accept a mapping that silently omits whatever was unreadable.
            """
            self._unreadable += 1
            logger.warning(
                "Folder-structure read: skipping %r (%s: %s) — it will be absent "
                "from the map and is counted in unreadable_folders",
                getattr(exc, "filename", "?"),
                type(exc).__name__,
                exc,
            )

        # followlinks=False is load-bearing: a symlink loop under a
        # caller-supplied path would otherwise walk forever.
        for dirpath, dirnames, filenames in os.walk(
            root, followlinks=False, onerror=on_error
        ):
            self._checkpoint()
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            if len(self._folders) >= MAX_FOLDERS:
                # break, not continue: past the bound every further iteration
                # would scandir a directory whose contents are already discarded.
                self._truncated = True
                break

            rel = os.path.relpath(dirpath, root)
            rel = "" if rel == "." else rel.replace(os.sep, "/")
            depth = 1 if not rel else rel.count("/") + 2
            parent_path = os.path.dirname(dirpath)
            folder = _Folder(
                depth=depth,
                index=len(self._folders),
                name=os.path.basename(root) if not rel else os.path.basename(dirpath),
                abs_path=dirpath,
                rel_path=rel,
                parent_index=by_path.get(parent_path) if rel else None,
                child_count=len(dirnames),
            )
            lowered = {f.lower() for f in filenames}
            folder.direct_pictures = sorted(
                f
                for f in filenames
                if os.path.splitext(f)[1].lower() in _IMAGE_EXTS
                and not f.startswith(".")
            )
            for picture in folder.direct_pictures:
                stem = os.path.splitext(picture)[0].lower()
                if any(stem + ext in lowered for ext in _SIDECAR_EXTS):
                    folder.with_sidecar += 1
            by_path[dirpath] = folder.index
            self._folders.append(folder)
            self._progress("walking", len(self._folders), 0)

        # Recursive picture counts, deepest first so a parent sums finished children.
        for folder in sorted(self._folders, key=lambda f: f.depth, reverse=True):
            folder.picture_count += len(folder.direct_pictures)
            if folder.parent_index is not None:
                self._folders[folder.parent_index].picture_count += folder.picture_count

    def _read_faces(self) -> None:
        """Sample each folder's pictures and look for one identity across them."""
        if self._detect_faces is None:
            logger.info(
                "Folder-structure read: no inference engine, skipping the face "
                "signal — no folder will be proposed as a Person"
            )
            return
        candidates = [
            f for f in self._folders if len(f.direct_pictures) >= MIN_FACE_SAMPLE
        ]
        total = len(candidates)
        self._faces_ran = True
        self._progress("faces", 0, total)
        # One pool for the whole read, not one per folder: a fresh pool per
        # folder is 20,000 thread-pool creations for the same four threads.
        with ThreadPoolExecutor(max_workers=_PRELOAD_WORKERS) as pool:
            for done, folder in enumerate(candidates, start=1):
                self._checkpoint()
                self._read_folder_faces(folder, pool)
                self._progress("faces", done, total)

    def _read_folder_faces(self, folder: _Folder, pool: ThreadPoolExecutor) -> None:
        paths = [
            os.path.join(folder.abs_path, name)
            for name in _evenly_spaced(folder.direct_pictures, SAMPLED_PER_FOLDER)
        ]
        images = list(pool.map(_load_bgr, paths))
        try:
            per_image = self._detect_faces(images)
        except Exception as exc:  # noqa: BLE001 — one folder must not kill the read
            logger.warning(
                "Folder-structure read: face detection failed for %r (%s: %s) — "
                "the folder gets no face evidence and the read continues",
                folder.rel_path or ".",
                type(exc).__name__,
                exc,
            )
            return

        embeddings = []
        for faces in per_image:
            biggest = _largest_face(faces)
            if biggest is not None:
                embeddings.append(biggest)
        folder.face_sampled = len(paths)
        folder.face_matched = _dominant_identity_count(embeddings)

    # ── assembling the answer ───────────────────────────────────────────

    def _build_result(self) -> dict[str, Any]:
        levels: dict[int, list[_Folder]] = {}
        for folder in self._folders:
            levels.setdefault(folder.depth, []).append(folder)

        rows_by_depth: dict[int, list[dict[str, Any]]] = {}
        for depth, folders in levels.items():
            rows_by_depth[depth] = [self._folder_row(f) for f in folders]

        level_docs = []
        for depth in sorted(levels):
            folders = levels[depth]
            level_docs.append(
                {
                    "depth": depth,
                    "folder_count": len(folders),
                    # Direct only, and named so: summing the recursive counts of
                    # a level would count every picture once per ancestor.
                    "direct_picture_count": sum(
                        len(f.direct_pictures) for f in folders
                    ),
                    "proposal": self._level_proposal(folders, rows_by_depth[depth]),
                    "folders": rows_by_depth[depth],
                }
            )

        root = self._folders[0] if self._folders else None
        # The filename lists were only ever input to the signals, and the route
        # holds this object for the process lifetime. A 28,000-picture library
        # would otherwise pin all 28,000 filenames until the next read.
        for folder in self._folders:
            folder.direct_pictures = []
        return {
            "root": {
                "path": self._root,
                "name": root.name if root else os.path.basename(self._root),
                "picture_count": root.picture_count if root else 0,
            },
            "sampled_per_folder": SAMPLED_PER_FOLDER,
            "folder_count": len(self._folders),
            "picture_count": root.picture_count if root else 0,
            "truncated": self._truncated,
            "max_folders": MAX_FOLDERS,
            # A folder the process could not read is absent from `levels`, and a
            # count of zero is the only way a client can tell "complete" from
            # "complete apart from what I was not allowed to open".
            "unreadable_folders": self._unreadable,
            # False means the face signal never ran (no inference engine), so no
            # folder could be proposed as a Person. Without it the same tree
            # answers differently depending on whether models had loaded, and
            # the client cannot tell that from a library with nobody in it.
            "face_signal_ran": self._faces_ran,
            "levels": level_docs,
        }

    def _folder_row(self, folder: _Folder) -> dict[str, Any]:
        return {
            "id": f"{folder.depth}/{folder.index}",
            "parent_id": (
                None
                if folder.parent_index is None
                else "{}/{}".format(
                    self._folders[folder.parent_index].depth, folder.parent_index
                )
            ),
            "depth": folder.depth,
            "name": folder.name,
            "relative_path": folder.rel_path,
            "picture_count": folder.picture_count,
            "direct_picture_count": len(folder.direct_pictures),
            "child_count": folder.child_count,
            "proposal": self._folder_proposal(folder),
        }

    def _folder_proposal(self, folder: _Folder) -> dict[str, Any]:
        """Combine the per-folder signals into one proposal for this row.

        Name match wins when it is unambiguous — it is a lookup and the other two
        are inferences — but every signal that fired still contributes its
        evidence, so a folder read as a person *and* named after a person says
        both. Two signals that disagree produce ``candidates``, never a pick.
        """
        evidence: list[dict[str, Any]] = []
        kinds: list[str] = []
        match: Optional[dict[str, Any]] = None

        key = normalise_name(folder.name)
        # An entity type this module does not know about is a caller error, not
        # a reason to lose the whole read: skip it and say so.
        matches = [m for m in self._by_name.get(key, []) if m[0] in _ENTITY_KIND]
        for unknown in (
            m for m in self._by_name.get(key, []) if m[0] not in _ENTITY_KIND
        ):
            logger.warning(
                "Folder-structure read: ignoring unknown entity type %r for %r",
                unknown[0],
                folder.name,
            )
        duplicated = self._ambiguous_types.get(key, {})
        if len(matches) == 1:
            entity_type, entity_id, entity_name = matches[0]
            kinds.append(_ENTITY_KIND[entity_type])
            copies = duplicated.get(entity_type, 1)
            if copies > 1:
                # The kind is known; which row is not, and §20 promises `id` is a
                # real primary key. Say the count instead of picking one.
                evidence.append(
                    {
                        "signal": "name_match",
                        "text": f"matches {copies} existing "
                        f"{_ENTITY_LABEL[entity_type]}s",
                    }
                )
            else:
                match = {
                    "entity_type": entity_type,
                    "id": entity_id,
                    "name": entity_name,
                }
                evidence.append(
                    {
                        "signal": "name_match",
                        "text": f"matches the {_ENTITY_LABEL[entity_type]} "
                        f"{entity_name}",
                    }
                )
        elif matches:
            # Two kinds of entity share this name. That narrows; it does not answer.
            named = " and ".join(
                f"an existing {_ENTITY_LABEL[t]}" for t, _, _ in matches
            )
            kinds.extend(_ENTITY_KIND[t] for t, _, _ in matches)
            evidence.append({"signal": "name_match", "text": f"matches {named}"})

        if folder.face_sampled and folder.face_matched >= max(
            1, round(FACE_MAJORITY * folder.face_sampled)
        ):
            evidence.insert(
                0,
                {
                    "signal": "faces",
                    "text": f"one face, {folder.face_matched} of {folder.face_sampled}",
                    "sampled": folder.face_sampled,
                    "matched": folder.face_matched,
                },
            )
            if "person" not in kinds:
                kinds.append("person")

        pictures = len(folder.direct_pictures)
        if pictures and folder.with_sidecar == pictures:
            evidence.append(
                {
                    "signal": "sidecars",
                    "text": (
                        f"a caption file beside all {pictures} "
                        f"{'picture' if pictures == 1 else 'pictures'}"
                    ),
                    "pictures": pictures,
                    "with_sidecar": folder.with_sidecar,
                }
            )
            if "set" not in kinds:
                kinds.append("set")

        if len(kinds) == 1:
            return {
                "kind": kinds[0],
                "candidates": [],
                "match": match,
                "evidence": evidence,
            }
        if len(kinds) > 1:
            # Signals disagree: return what is left rather than picking one.
            return {
                "kind": None,
                "candidates": kinds,
                "match": None,
                "evidence": evidence,
            }
        return {"kind": None, "candidates": [], "match": None, "evidence": []}

    def _level_proposal(
        self, folders: list[_Folder], rows: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Read the level as a whole. The only place ``cardinality`` speaks."""
        if len(folders) <= 1:
            # One folder is not a level with a shape; the root especially.
            return {"kind": None, "candidates": [], "match": None, "evidence": []}

        names = Counter(normalise_name(f.name) for f in folders)
        distinct = len(names)
        parents = len({f.parent_index for f in folders})

        if (
            distinct <= _TAG_MAX_DISTINCT_NAMES
            and len(folders) >= _TAG_REPEAT_FACTOR * distinct
            and parents >= _TAG_MIN_PARENTS
        ):
            return {
                "kind": "tag",
                "candidates": [],
                "match": None,
                "evidence": [
                    {
                        "signal": "cardinality",
                        "text": f"{distinct} names under {parents} parents",
                        "names": distinct,
                        "parents": parents,
                    }
                ],
            }

        # A level whose rows mostly agree is answered by its rows, not by shape.
        voted = Counter(
            r["proposal"]["kind"] for r in rows if r["proposal"]["kind"] is not None
        )
        if voted:
            best = max(voted.values())
            # Integer arithmetic, not round(): round(0.6 * 4) is 2, which would
            # let a 50% plurality through a rule written as sixty percent.
            if best >= 2 and best * 100 >= _LEVEL_VOTE_SHARE_PCT * len(folders):
                # At or above 60% at most one kind can qualify (two would need
                # 120% of the level), so there is no tie to break here and no
                # most_common insertion order to depend on. That is the reason
                # the share is compared exactly rather than rounded: round(0.6*4)
                # is 2, and a 2-2 split *would* be a tie decided by the alphabet.
                leaders = [k for k, c in voted.items() if c == best]
                assert len(leaders) == 1, leaders
                kind = leaders[0]
                return {
                    "kind": kind,
                    "candidates": [],
                    "match": None,
                    "evidence": [
                        {
                            "signal": _LEVEL_VOTE_SIGNAL[kind],
                            "text": f"{best} of {len(folders)} folders read as "
                            f"{_KIND_LABEL[kind]}",
                        }
                    ],
                }

        if distinct == len(folders):
            # Every name used once, so they are not labels — which rules Tag out
            # and rules nothing in.
            return {
                "kind": None,
                "candidates": list(_NON_TAG_KINDS),
                "match": None,
                "evidence": [
                    {
                        "signal": "cardinality",
                        "text": f"{distinct} names under {parents} parents, "
                        "used once each, so not labels",
                        "names": distinct,
                        "parents": parents,
                    }
                ],
            }

        return {"kind": None, "candidates": [], "match": None, "evidence": []}


_ENTITY_LABEL = {
    "project": "project",
    "set": "set",
    "character": "person",
    "tag": "tag",
}

_KIND_LABEL = {
    "project": "Project",
    "set": "Set",
    "person": "Person",
    "tag": "Tag",
    "folder": "just a folder",
}

_LEVEL_VOTE_SIGNAL = {
    "person": "faces",
    "set": "sidecars",
    "project": "name_match",
    "tag": "name_match",
    "folder": "name_match",
}


def load_existing_entities(db) -> list[tuple[str, Optional[int], str]]:
    """Every entity name the vault already has, for the name-match signal.

    Returns ``(entity_type, id, name)`` triples. ``tag`` rows carry ``None`` for
    the id: a tag in this vault is a string on a picture (``Tag.tag``), not a row
    of its own, so the name is the handle (integration_architecture.md §20).
    """
    from sqlmodel import select

    from pixlstash.db_models.character import Character
    from pixlstash.db_models.picture_set import PictureSet
    from pixlstash.db_models.project import Project
    from pixlstash.db_models.tag import Tag

    def fetch(session):
        rows: list[tuple[str, Optional[int], str]] = []
        for model, entity_type in (
            (Project, "project"),
            (PictureSet, "set"),
            (Character, "character"),
        ):
            for entity_id, name in session.exec(select(model.id, model.name)):
                if name:
                    rows.append((entity_type, entity_id, name))
        for tag in session.exec(select(Tag.tag).distinct()):
            if tag:
                rows.append(("tag", None, tag))
        return rows

    return db.run_immediate_read_task(fetch)


def _evenly_spaced(items: list[str], count: int) -> list[str]:
    """Take *count* items spread across *items*, deterministically.

    Evenly spaced rather than the first N: the first 20 files of a shoot are
    often one burst of the same frame, and a folder ordered by date would then
    be judged on its first minute.
    """
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[int(i * step)] for i in range(count)]


def _load_bgr(path: str):
    """Decode one picture to a downscaled BGR array, or ``None`` if unreadable."""
    try:
        with Image.open(path) as img:
            img.draft("RGB", (_INFERENCE_MAX_SIDE, _INFERENCE_MAX_SIDE))
            img = img.convert("RGB")
            longest = max(img.size)
            if longest > _INFERENCE_MAX_SIDE:
                scale = _INFERENCE_MAX_SIDE / longest
                img = img.resize(
                    (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
                )
            return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    except Exception as exc:  # noqa: BLE001 — a corrupt file is not a failed read
        logger.warning(
            "Folder-structure read: could not decode %s (%s: %s) — sampled as no-face",
            os.path.basename(path),
            type(exc).__name__,
            exc,
        )
        return None


def _largest_face(faces) -> Optional[np.ndarray]:
    """The normalised embedding of the biggest face in one picture, if any."""
    best = None
    best_area = 0.0
    for face in faces or []:
        if getattr(face, "embedding", None) is None:
            continue
        bbox = face.bbox
        area = float((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))
        if area > best_area:
            best_area = area
            best = face.embedding
    if best is None:
        return None
    vector = np.asarray(best, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 1e-8 else None


def _dominant_identity_count(embeddings: list[np.ndarray]) -> int:
    """How many of these faces are the same person as the most common one.

    A medoid vote: for each face, count the faces within ``SAME_IDENTITY_COSINE``
    of it (itself included) and take the largest count. O(n²) over at most
    ``SAMPLED_PER_FOLDER`` vectors, so 400 dot products per folder.
    """
    if not embeddings:
        return 0
    matrix = np.stack(embeddings)
    similarity = matrix @ matrix.T
    return int((similarity >= SAME_IDENTITY_COSINE).sum(axis=1).max())
