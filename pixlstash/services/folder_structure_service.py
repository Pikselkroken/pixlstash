"""The folder-structure read: propose what each level of a folder tree is.

v1.11 Phase 2 (``docs/plans/v1.11.0-existing-library.md`` §4). Eight signals,
all deterministic and local - a folder name is a string, so no LLM and no
language reading of names:

``cardinality``
    Few names repeating under many parents is a facet, not a thing → Tag. A
    property of a *level*, so it speaks at level scope.
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
``leaf``
    ``MIN_LEAF_PICTURES`` or more pictures and no folders below → Set. A date
    *with other words* (``2006-09-08 Anna wedding``) is a curated name and
    strengthens it; a *bare* date (``2006-09-08``) is a date bucket - Lightroom,
    phones and Google Photos exports all file by capture day whether or not the
    pictures belong together - so it proposes nothing and says ``date_bucket``.
``container``
    Folders below mostly read as Sets, People or date buckets, and this folder
    holds few pictures of its own → Project. The other level-scoped signal: it
    needs the level below it read first.
``capture_day``
    EXIF capture dates from the same sample the face pass opens: one or two
    distinct days → Set. Silent on and directly under bare-date folders, where
    one day is true by construction.
``batch_numbering``
    Most direct pictures named ``<prefix><digits>`` with one prefix
    (``IMG_0412``, ``DSC01234``) → Set. Additional evidence: it proposes only
    where nothing else spoke and never contradicts another signal.

Every proposal carries the evidence that produced it; a signal that cannot state
its reason proposes nothing. Where the signals only narrow the answer the
remaining ``candidates`` are returned rather than one of them being picked.

**This module reads. It never writes** - no row is created and no file is opened
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
from functools import partial
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PIL import Image

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.library_layout import Facet
from pixlstash.utils.media_files import (
    SUPPORTED_IMAGE_EXTS,
    is_pixlstash_thumbnail,
    is_supported_media_file,
)
from pixlstash.utils.reference_folder_validator import (
    validate_reference_folder_path,
)

logger = get_logger(__name__)

#: Pictures sampled per folder for the face signal. The whole point of the
#: number: 20 × a few hundred folders is two minutes of inference, the full
#: folders would be an hour. The full pass runs later as background work.
SAMPLED_PER_FOLDER = 20

#: Below this many pictures in a folder the face signal stays silent. "One face,
#: 2 of 3" is not evidence anyone should act on, and this signal's contract is
#: that it either states a reason or says nothing.
MIN_FACE_SAMPLE = 5

#: Below this many pictures the sidecar signal stays silent. One `a.jpg` beside
#: one `a.txt` is a caption pair, not a Set - and "a caption file beside all 1
#: picture" is the same weak evidence `MIN_FACE_SAMPLE` exists to refuse. It
#: matters at level scope too: a level of one-picture folders would otherwise
#: clear the 60% vote and be proposed as Set entire.
MIN_SIDECAR_PICTURES = 3

#: Below this many direct pictures the leaf and batch-numbering signals stay
#: silent, for the reason `MIN_SIDECAR_PICTURES` does: two pictures and nothing
#: below is a folder, not a Set anybody should be offered.
MIN_LEAF_PICTURES = 3

#: A folder with subfolders reads as a container only while its own direct
#: pictures are at most this share of everything under it. A folder holding most
#: of its pictures itself is a Set with a stray subfolder, not a Project.
_CONTAINER_MAX_DIRECT_PCT = 10

#: Share of a folder's direct picture stems that must share one
#: `<prefix><digits>` shape for the folder to read as one numbered batch, and
#: the shape itself: a non-digit prefix (possibly empty) and at least three
#: digits. `IMG_0412`, `DSC01234`, `00017-1234`.
_BATCH_SHARE_PCT = 80
_BATCH_STEM = re.compile(r"^(\D*)\d{3,}")

#: Distinct EXIF capture days at or below which a sampled folder reads as one
#: Set, and the share of the sample that must carry a date before the days are
#: counted at all - a folder where most pictures have no EXIF says nothing.
_CAPTURE_MAX_DAYS = 2
_CAPTURE_MIN_DATED_PCT = 50

#: Share of the *sampled* pictures that must carry the same identity for the
#: folder to read as one person. A whole percent compared with integer
#: arithmetic, for the reason `_LEVEL_VOTE_SHARE_PCT` is: `round(0.7 * 6)` is
#: `4`, so a float rule written as seventy percent passes at **66.7%** on a
#: six-picture sample. Rounding decides in the wrong direction for a threshold
#: - the whole job of one is to be a floor.
FACE_MAJORITY_PCT = 70

# ponytail: one cosine threshold and a medoid vote, not a clustering library.
# InsightFace ArcFace embeddings are L2-normalised, so this is a plain dot
# product; 0.35 is the conventional same-identity floor for ArcFace and is
# deliberately on the strict side - a missed Person row costs the owner one
# dropdown, a wrong one costs them trust in every other row on the screen.
# Upgrade path if it proves noisy: agglomerative clustering over the folder.
SAME_IDENTITY_COSINE = 0.35

#: Wall-clock budget for one whole read. The face signal has a per-batch
#: timeout, but a per-batch timeout multiplied by 20,000 folders is weeks, so
#: the bound that actually holds has to be on the read: 180 s per batch over
#: 20,000 folders is 41 days. Past it the read stops
#: and returns what it found - the same shape a cancel produces.
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
#: answer as its own. Compared with integer arithmetic - ``round(0.6 * 4)`` is
#: 2, which would quietly make this a fifty-percent rule on a level of four.
_LEVEL_VOTE_SHARE_PCT = 60

#: "Just a folder" - the owner's answer on the mapping screen, meaning the name
#: is not telling us anything. Deliberately NOT a `Facet`: it is the *absence*
#: of one, which is why it cannot come out of that enum. No signal proposes it.
JUST_A_FOLDER = "folder"

#: Every `kind` this API can return: the layout's four facets plus
#: `JUST_A_FOLDER`. Sourced from `Facet` rather than spelled again, so Phase 4
#: renaming a facet breaks this loudly instead of leaving the read proposing a
#: word the layout no longer knows.
KINDS = tuple(f.value for f in Facet) + (JUST_A_FOLDER,)

#: Kinds a level can narrow to once cardinality has ruled Tag out.
_NON_TAG_KINDS = (Facet.PROJECT.value, Facet.SET.value, Facet.PERSON.value)

#: What a folder below a container may read as for the container to count it,
#: plus `_DATE_BUCKET` for a bare-date folder, which proposes nothing itself but
#: is still a group of pictures the folder above it holds together.
_CONTAINED_KINDS = (Facet.SET.value, Facet.PERSON.value)
_DATE_BUCKET = "date"
_CONTAINABLE = set(_CONTAINED_KINDS) | {_DATE_BUCKET}

#: What the face pass may SAMPLE. Videos are deliberately excluded - this
#: signal decodes what it samples and a video frame is not what the face pass is
#: built on - so the image half is imported rather than `is_supported_media_file`.
#:
#: **This is not what the read COUNTS.** Those were the same list until a real
#: import reported a total that matched nothing: the commit indexes every
#: supported media file, videos included, so a library of holiday clips came out
#: with more pictures than the dialog had promised. Counting is
#: `is_supported_media_file`, sampling is this - see `_Folder.direct_media`.
_IMAGE_EXTS = SUPPORTED_IMAGE_EXTS
#: Lower-cased and compared lower-cased: a `.TXT` beside every picture is a
#: caption file, and a dataset exported on Windows is the obvious victim of
#: matching this case-sensitively.
_SIDECAR_EXTS = (".txt", ".caption")

#: Max side the sampled pictures are decoded at for detection. Mirrors
#: FaceExtractionTask.INFERENCE_MAX_SIDE - 2× InsightFace's det_size.
_INFERENCE_MAX_SIDE = 512

#: I/O + decode threads feeding the (sequential) detection batch.
_PRELOAD_WORKERS = 4

#: An entity type as the vault stores it -> the `kind` this API speaks. The two
#: vocabularies differ in exactly one place and that place is deliberate:
#: `character` is the model name, and the shipped UI says People
#: (`design/1.11-existing-library/DECISIONS.md`).
_ENTITY_KIND = {
    "project": Facet.PROJECT.value,
    "set": Facet.SET.value,
    "character": Facet.PERSON.value,
    "tag": Facet.TAG.value,
}

#: The same mapping in prose, for an evidence string. Separate from
#: `_ENTITY_KIND` because one is a wire value and the other is display text;
#: they happen to agree today and are not the same idea.
_ENTITY_KIND_LABEL = {
    "project": "project",
    "set": "set",
    "character": "person",
    "tag": "tag",
}


def normalise_name(name: str) -> str:
    """Fold a folder name for comparison against an entity name.

    Case, separators and runs of punctuation are noise here: ``2024_Shoots``,
    ``2024 shoots`` and ``2024-Shoots`` are the same name to an owner. So are
    ``Jose`` and ``José`` - accents are folded, which is why the decomposition
    runs before the substitution.

    **Unicode-aware on purpose.** An ASCII-only class here does not merely miss
    a match, it folds every Cyrillic, CJK, Greek or Hebrew name to the *same*
    empty string, at which point a level of fifteen distinct people reads as one
    repeated name and the cardinality signal confidently proposes Tag.

    **Deliberately looser than ``library_layout._match_key``, and the asymmetry
    is the point.** That one is NFC + casefold: accents and separators survive,
    so ``José`` and ``Jose`` stay two folders. It has to be exact, because it
    decides whether a picture *moves*. This one only decides what to *propose*
    on a screen the owner then confirms, where a wrong guess costs one dropdown
    and a missed one costs a lookup they wanted. They are not a duplicated
    helper to merge: merging them would either start moving files on a fuzzy
    match or stop the read recognising ``2024_Shoots``.
    """
    decomposed = unicodedata.normalize("NFKD", name.lower())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", stripped, flags=re.UNICODE).strip()


#: A date at the front of a folder name: ``2009``, ``2006-09``, ``2006-09-08``,
#: ``20060908``, and the ``_``/``.``/space spellings of those. Exactly the shape
#: a camera, a phone and PixlStash's own dated layout produce.
_DATE_PREFIX = (
    r"(19|20)\d{2}"
    r"(?:[-_. ]?(?:0[1-9]|1[0-2])"
    r"(?:[-_. ]?(?:0[1-9]|[12]\d|3[01]))?)?"
)
#: A name that is nothing but a date, an importer's ``_1``/``-2`` suffix allowed.
_DATE_ONLY_NAME = re.compile(rf"^{_DATE_PREFIX}(?:[-_ ]\d{{1,3}})?$")
#: A date followed by other words: ``2006-09-08 Anna wedding``, ``2024-03 Iceland``.
_DATED_NAME = re.compile(rf"^{_DATE_PREFIX}[-_. ]+\S")
#: A bare year, ``2009``: the one container shape this read refuses to pick.
_YEAR_ONLY_NAME = re.compile(r"^(19|20)\d{2}$")


def reads_as_a_date(name: str) -> bool:
    """True when this folder name is a date and nothing else - a date bucket.

    Lightroom, phones and Google Photos exports all file by capture day whether
    or not the pictures belong together, so a bare date says *when*, never
    *what*. ``2006-09-08_1``-style importer suffixes count as bare. A date with
    other words after it is `reads_as_dated` instead.
    """
    return bool(_DATE_ONLY_NAME.match(name.strip()))


def reads_as_dated(name: str) -> bool:
    """True when this folder name starts with a date and then says more.

    ``2006-09-08 Anna wedding`` is a name somebody chose, so it is evidence the
    pictures belong together where the bare form is only evidence of a date.
    """
    stripped = name.strip()
    return not _DATE_ONLY_NAME.match(stripped) and bool(_DATED_NAME.match(stripped))


@dataclass
class _Folder:
    """One folder found by the walk, before any signal has run."""

    depth: int
    index: int
    name: str
    abs_path: str
    rel_path: str
    parent_index: Optional[int]
    #: Images only, thumbnails excluded - the face pass's sample source, and
    #: what the sidecar signal counts caption files against.
    direct_pictures: list[str] = field(default_factory=list)
    #: Everything in this folder the commit will index: images AND videos, our
    #: own thumbnails excluded. What the owner is shown and promised.
    direct_media: int = 0
    with_sidecar: int = 0
    child_count: int = 0
    picture_count: int = 0  # recursive; filled after the walk
    face_sampled: int = 0
    face_matched: int = 0
    #: The capture-day signal, from the same sample the face pass opened:
    #: pictures opened, pictures that carried an EXIF date, distinct days.
    capture_sampled: int = 0
    capture_dated: int = 0
    capture_days: int = 0


class ReadCancelled(Exception):
    """Raised inside the read when the caller cancelled it."""


class FolderStructureRead:
    """One run of the read over one folder tree.

    Args:
        root: Absolute path to the folder to read. Already validated and
            contained by the caller - this class does no authorization.
        detect_faces: ``(list[np.ndarray]) -> list[list[FaceResult]]``, or
            ``None`` to skip the face signal entirely (no inference engine).
        existing_entities: ``[(entity_type, id, name), …]`` the vault already
            holds, for the name-match signal.
        progress: Called as ``(stage, processed, total)`` whenever either moves.
        deadline_s: Wall-clock budget for the whole read. Past it the read stops
            where it is and returns what it has, exactly as a cancel does.
        exclude: Absolute paths the walk must not descend into, matched on
            realpath. Used for the library's own ``snapshots/`` tree, which is
            not the owner's pictures.
    """

    def __init__(
        self,
        root: str,
        *,
        detect_faces: Optional[Callable[[list], list]] = None,
        existing_entities: Optional[list[tuple[str, Optional[int], str]]] = None,
        progress: Optional[Callable[[str, int, int], None]] = None,
        deadline_s: float = DEFAULT_DEADLINE_S,
        exclude: Optional[set[str]] = None,
    ) -> None:
        self._root = root
        self._exclude = {os.path.realpath(p) for p in (exclude or ())}
        self._detect_faces = detect_faces
        self._progress = progress or (lambda stage, processed, total: None)
        self._cancel = threading.Event()
        self._folders: list[_Folder] = []
        self._truncated = False
        self._unreadable = 0
        self._skipped_hidden = 0
        self._skipped_restricted = 0
        self._faces_ran = False
        self._date_levels: set[int] = set()
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
        # does not address one entity. Keep the kind - that much IS known - and
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
        """Walk, sample, run the signals, and return the §20 result document.

        A cancel between stages stops the run and returns whatever the stages
        that did complete found - a partial read is still worth showing.
        """
        try:
            self._walk()
            self._sample_folders()
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
                "Folder-structure read: out of time after %d folders - returning "
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
            *complete* map of a library it only partly saw - and the owner would
            accept a mapping that silently omits whatever was unreadable.
            """
            self._unreadable += 1
            logger.warning(
                "Folder-structure read: skipping %r (%s: %s) - it will be absent "
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
            kept = []
            for name in sorted(dirnames):
                if name.startswith("."):
                    # `.pixlstash` sidecars and a vault's own thumbnail cache.
                    # Counted, because §24's whole argument against `os.walk`'s
                    # default is that a silently omitted subtree reads as a
                    # complete map.
                    self._skipped_hidden += 1
                    continue
                child = os.path.join(dirpath, name)
                if os.path.realpath(child) in self._exclude:
                    # The library's own snapshots/ tree. Not the owner's
                    # pictures, so it is not theirs to map - and creating it
                    # later instead would only move the problem: a GFS or
                    # safety snapshot can land at any time, including before a
                    # second run of the wizard on the same root.
                    continue
                if validate_reference_folder_path(os.path.realpath(child)):
                    # The route validates the ROOT. That is not containment for a
                    # recursive walk: `POST {"path": "/"}` names no restricted
                    # directory and then walks every one of them. The blocklist
                    # has to run per directory or it is a check on one string.
                    logger.warning(
                        "Folder-structure read: refusing to descend into a "
                        "restricted system directory below the root: %s",
                        name,
                    )
                    self._skipped_restricted += 1
                    continue
                kept.append(name)
            dirnames[:] = kept
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
                name=(_display_name(root) if not rel else os.path.basename(dirpath)),
                abs_path=dirpath,
                rel_path=rel,
                parent_index=by_path.get(parent_path) if rel else None,
                child_count=len(dirnames),
            )
            lowered = {f.lower() for f in filenames}
            # `is_supported_media_file` drops our own `_thumb.webp` files, which
            # a library indexed before #1164 is full of, sitting beside every
            # original. Counting them made the total grow on every re-read
            # of the same folder - the "different number each time" - and
            # sampling them spent the face pass on 96px thumbnails.
            folder.direct_media = sum(
                1
                for f in filenames
                if not f.startswith(".") and is_supported_media_file(f)
            )
            folder.direct_pictures = sorted(
                f
                for f in filenames
                if os.path.splitext(f)[1].lower() in _IMAGE_EXTS
                and not f.startswith(".")
                and not is_pixlstash_thumbnail(f)
            )
            for picture in folder.direct_pictures:
                lowered_name = picture.lower()
                stem = os.path.splitext(lowered_name)[0]
                # Both conventions: `a.txt` beside `a.jpg`, and `a.jpg.txt`.
                if any(
                    stem + ext in lowered or lowered_name + ext in lowered
                    for ext in _SIDECAR_EXTS
                ):
                    folder.with_sidecar += 1
            by_path[dirpath] = folder.index
            self._folders.append(folder)
            self._progress("walking", len(self._folders), 0)

    def _total_picture_counts(self) -> None:
        """Fill every folder's recursive count, deepest first.

        Called from ``_build_result`` and **not** from the end of ``_walk``: a
        cancel or a deadline raises ``ReadCancelled`` from inside the walk loop,
        so a version that summed here left every row at ``picture_count: 0``
        while still reporting a real ``direct_picture_count`` - a partial result
        that says the library is empty, on the one path whose whole
        justification is that the partial result is showable.
        """
        for folder in self._folders:
            folder.picture_count = folder.direct_media
        for folder in sorted(self._folders, key=lambda f: f.depth, reverse=True):
            if folder.parent_index is not None:
                self._folders[folder.parent_index].picture_count += folder.picture_count

    def _sample_folders(self) -> None:
        """Open ``SAMPLED_PER_FOLDER`` pictures per folder, once, for both sampled
        signals: the capture day comes out of the same open the face decode
        needs, so with no inference engine the sample still runs and only the
        faces are missing. The stage stays ``faces`` on the wire (§20)."""
        if self._detect_faces is None:
            logger.info(
                "Folder-structure read: no inference engine, skipping the face "
                "signal - no folder will be proposed as a Person"
            )
        candidates = [
            f for f in self._folders if len(f.direct_pictures) >= MIN_FACE_SAMPLE
        ]
        total = len(candidates)
        self._faces_ran = self._detect_faces is not None
        self._progress("faces", 0, total)
        # One pool for the whole read, not one per folder: a fresh pool per
        # folder is 20,000 thread-pool creations for the same four threads.
        with ThreadPoolExecutor(max_workers=_PRELOAD_WORKERS) as pool:
            for done, folder in enumerate(candidates, start=1):
                self._checkpoint()
                self._sample_folder(folder, pool)
                self._progress("faces", done, total)

    def _sample_folder(self, folder: _Folder, pool: ThreadPoolExecutor) -> None:
        paths = [
            os.path.join(folder.abs_path, name)
            for name in _evenly_spaced(folder.direct_pictures, SAMPLED_PER_FOLDER)
        ]
        decode = self._detect_faces is not None
        samples = list(pool.map(partial(_load_sample, decode=decode), paths))
        days = [day for _image, day in samples if day]
        folder.capture_sampled = len(paths)
        folder.capture_dated = len(days)
        folder.capture_days = len(set(days))
        if not decode:
            return
        images = [image for image, _day in samples]
        try:
            per_image = self._detect_faces(images)
        except Exception as exc:  # noqa: BLE001 - one folder must not kill the read
            logger.warning(
                "Folder-structure read: face detection failed for %r (%s: %s) - "
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
        self._total_picture_counts()
        levels: dict[int, list[_Folder]] = {}
        for folder in self._folders:
            levels.setdefault(folder.depth, []).append(folder)

        # A level of mostly bare-date folders is a date-bucketed level, which
        # the capture-day signal must know about for the folders under it.
        self._date_levels = {
            depth
            for depth, folders in levels.items()
            if _clears_share(
                sum(1 for f in folders if reads_as_a_date(f.name)),
                len(folders),
                _LEVEL_VOTE_SHARE_PCT,
            )
        }
        # Deepest first: a folder reads as a container off what the level below
        # it read as, so the rows below must exist before the rows above.
        rows_by_depth: dict[int, list[dict[str, Any]]] = {}
        grouped: dict[int, list[set[str]]] = {}
        for depth in sorted(levels, reverse=True):
            folders = levels[depth]
            rows_by_depth[depth] = [
                self._folder_row(f, grouped.get(f.index)) for f in folders
            ]
            # depth > 2: the parents of these rows are at depth 2 or deeper, so
            # the root - the library itself - never reads as a container.
            grouped = (
                _grouped_by_parent(folders, rows_by_depth[depth]) if depth > 2 else {}
            )

        level_docs = []
        for depth in sorted(levels):
            folders = levels[depth]
            level_docs.append(
                {
                    "depth": depth,
                    "folder_count": len(folders),
                    # Direct only, and named so: summing the recursive counts of
                    # a level would count every picture once per ancestor.
                    "direct_picture_count": sum(f.direct_media for f in folders),
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
                "name": root.name if root else _display_name(self._root),
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
            # Folders deliberately not walked, as opposed to ones that failed:
            # dot-folders (a vault's own caches) and anything the blocklist
            # names. Reported for the same reason `unreadable_folders` is - a
            # map that omits a subtree must not read as a complete one.
            "skipped_folders": {
                "hidden": self._skipped_hidden,
                "restricted": self._skipped_restricted,
            },
            # False means the face signal never ran (no inference engine), so no
            # folder could be proposed as a Person. Without it the same tree
            # answers differently depending on whether models had loaded, and
            # the client cannot tell that from a library with nobody in it.
            "face_signal_ran": self._faces_ran,
            "levels": level_docs,
        }

    def _folder_row(
        self, folder: _Folder, grouped: Optional[list[set[str]]]
    ) -> dict[str, Any]:
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
            "direct_picture_count": folder.direct_media,
            "child_count": folder.child_count,
            "proposal": self._folder_proposal(folder, grouped),
        }

    def _folder_proposal(
        self, folder: _Folder, grouped: Optional[list[set[str]]]
    ) -> dict[str, Any]:
        """Combine the per-folder signals into one proposal for this row.

        Name match wins when it is unambiguous - it is a lookup and the others
        are inferences - but every signal that fired still contributes its
        evidence, so a folder read as a person *and* named after a person says
        both. Two signals that disagree produce ``candidates``, never a pick.

        ``grouped`` is what this folder's children read as, one set of kinds per
        child, when the level below cleared the container bar; ``None`` when it
        did not.
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
                        f"{_ENTITY_KIND_LABEL[entity_type]}s",
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
                        "text": f"matches the {_ENTITY_KIND_LABEL[entity_type]} "
                        f"{entity_name}",
                    }
                )
        elif matches:
            # Two kinds of entity share this name. That narrows; it does not answer.
            named = " and ".join(
                f"an existing {_ENTITY_KIND_LABEL[t]}" for t, _, _ in matches
            )
            kinds.extend(_ENTITY_KIND[t] for t, _, _ in matches)
            evidence.append({"signal": "name_match", "text": f"matches {named}"})

        # The face signal alone never makes a dated folder a Person. One day
        # of one holiday is mostly one person, so "one face, 34 of 40" fires on
        # `2006-09-08` (and `2006-09-08 Anna wedding`) exactly as it does on
        # `Anna` - and a level of date folders then clears the 60% vote and
        # proposes People entire. The name is the stronger evidence: a date is
        # not a name anybody has. The row says nothing about faces rather than
        # something else - the owner still picks Person if they meant it.
        dated = reads_as_a_date(folder.name) or reads_as_dated(folder.name)
        if (
            folder.face_sampled
            and not dated
            and _clears_share(
                folder.face_matched, folder.face_sampled, FACE_MAJORITY_PCT
            )
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
        if pictures >= MIN_SIDECAR_PICTURES and folder.with_sidecar == pictures:
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

        if folder.depth > 1:
            # The root is the library itself, not a thing in it: no shape signal
            # reads it. Name match and sidecars above still may.
            # A single name match is a lookup and the shape signals are
            # inferences: they explain the row, they do not contest it.
            self._read_shape(folder, grouped, kinds, evidence, len(matches) != 1)

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
        # No kind, but possibly a reason for the blank (`date_bucket`,
        # `batch_numbering`): the tooltip explains it, the row proposes nothing.
        return {"kind": None, "candidates": [], "match": None, "evidence": evidence}

    def _read_shape(
        self,
        folder: _Folder,
        grouped: Optional[list[set[str]]],
        kinds: list[str],
        evidence: list[dict[str, Any]],
        may_propose: bool,
    ) -> None:
        """The four shape signals: leaf, container, capture_day, batch_numbering.

        Each appends its evidence to the caller's list. With ``may_propose`` it
        appends its kind too, and kinds that disagree become ``candidates`` in
        the caller, exactly as the older signals do; without it - the name
        matched exactly one entity - the evidence stands and the match holds.

        ``faces`` outranks all four as well: it is evidence about the pictures,
        these are priors about the folder, and a person's folder is a leaf by
        construction. Letting a leaf contest a one-face folder turned every
        Person the read used to find into "Person or Set".
        """
        faces_spoke = "person" in kinds

        def propose(kind: str) -> None:
            if may_propose and not faces_spoke and kind not in kinds:
                kinds.append(kind)

        pictures = len(folder.direct_pictures)
        bare_date = reads_as_a_date(folder.name)
        if bare_date:
            evidence.append({"signal": "date_bucket", "text": "filed by date"})

        if pictures >= MIN_LEAF_PICTURES and not folder.child_count and not bare_date:
            named = reads_as_dated(folder.name)
            evidence.append(
                {
                    "signal": "leaf",
                    "text": (
                        "dated and named, pictures and no folders below"
                        if named
                        else "pictures and no folders below"
                    ),
                    "pictures": pictures,
                }
            )
            propose("set")

        if (
            grouped
            and folder.child_count
            and folder.direct_media * 100
            <= _CONTAINER_MAX_DIRECT_PCT * folder.picture_count
        ):
            read = [
                _KIND_LABEL[k]
                for k in _CONTAINED_KINDS
                if any(k in child for child in grouped)
            ]
            parts = ["read as " + " or ".join(read)] if read else []
            if any(_DATE_BUCKET in child for child in grouped):
                parts.append("filed by date")
            count = len(grouped)
            evidence.append(
                {
                    "signal": "container",
                    "text": f"groups {count} {'folder' if count == 1 else 'folders'} "
                    + " or ".join(parts),
                    "grouped": count,
                }
            )
            propose("project")
            if _YEAR_ONLY_NAME.match(folder.name.strip()):
                # A bare year over Sets is not picked: the owner of the library
                # this was built against files `2009`, `2010` as Sets, and a
                # year is also the one container name that says nothing about
                # what it groups. Both are offered.
                propose("set")

        if (
            folder.capture_sampled >= MIN_FACE_SAMPLE
            and not bare_date
            and (folder.depth - 1) not in self._date_levels
            and 1 <= folder.capture_days <= _CAPTURE_MAX_DAYS
            and _clears_share(
                folder.capture_dated, folder.capture_sampled, _CAPTURE_MIN_DATED_PCT
            )
        ):
            # Not on a date bucket, nor directly under a level of them: there
            # "one day" is true by construction and says nothing.
            evidence.append(
                {
                    "signal": "capture_day",
                    "text": f"shot on {folder.capture_days} "
                    f"{'day' if folder.capture_days == 1 else 'days'}",
                    "sampled": folder.capture_sampled,
                    "dated": folder.capture_dated,
                    "days": folder.capture_days,
                }
            )
            propose("set")

        if pictures >= MIN_LEAF_PICTURES:
            prefixes = Counter()
            first_by_prefix: dict[str, str] = {}
            for name in folder.direct_pictures:
                stem = os.path.splitext(name)[0]
                matched = _BATCH_STEM.match(stem)
                if matched:
                    prefixes[matched.group(1)] += 1
                    first_by_prefix.setdefault(matched.group(1), stem)
            if prefixes:
                prefix, share = prefixes.most_common(1)[0]
                if _clears_share(share, pictures, _BATCH_SHARE_PCT):
                    # Additional evidence: it proposes Set only where nothing
                    # else spoke, and never contradicts another signal's kind.
                    evidence.append(
                        {
                            "signal": "batch_numbering",
                            "text": f"numbered as one batch ({first_by_prefix[prefix]}…)",
                            "pictures": pictures,
                            "numbered": share,
                        }
                    )
                    if not kinds and not bare_date:
                        propose("set")

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
            if best >= 2 and _clears_share(best, len(folders), _LEVEL_VOTE_SHARE_PCT):
                # At or above 60% at most one kind can qualify (two would need
                # 120% of the level), so there is no tie to break here and no
                # most_common insertion order to depend on. That is the reason
                # the share is compared exactly rather than rounded: round(0.6*4)
                # is 2, and a 2-2 split *would* be a tie decided by the alphabet.
                leaders = sorted(k for k, c in voted.items() if c == best)
                if len(leaders) > 1:
                    # Unreachable at 60, and this is not an assert: it runs in
                    # _build_result, after a read that may have cost half an
                    # hour, and a lowered share should degrade to "narrowed"
                    # rather than throw that work away. It is also what an
                    # `assert` cannot do under `python -O`.
                    logger.warning(
                        "Folder-structure read: %d-way tie at %d%% of a level of "
                        "%d - returning the candidates rather than picking",
                        len(leaders),
                        _LEVEL_VOTE_SHARE_PCT,
                        len(folders),
                    )
                    return {
                        "kind": None,
                        "candidates": leaders,
                        "match": None,
                        "evidence": [
                            {
                                "signal": "level_vote",
                                "text": "{} of {} folders each read as {}".format(
                                    best,
                                    len(folders),
                                    " or ".join(_KIND_LABEL[k] for k in leaders),
                                ),
                            }
                        ],
                    }
                kind = leaders[0]
                return {
                    "kind": kind,
                    "candidates": [],
                    "match": None,
                    "evidence": [
                        {
                            # `level_vote`, not the row signal that produced the
                            # majority: this is a claim about the level, and
                            # labelling it `sidecars` would hand a client a
                            # per-folder filesystem fact it could render an
                            # affordance off.
                            "signal": "level_vote",
                            "text": f"{best} of {len(folders)} folders read as "
                            f"{_KIND_LABEL[kind]}",
                        }
                    ],
                }

        dated = sum(1 for f in folders if reads_as_a_date(f.name))
        if _clears_share(dated, len(folders), _LEVEL_VOTE_SHARE_PCT):
            # A level of date buckets. Its names are used once each too, but
            # "not labels" is not the point here: the owner sets the whole
            # level in one gesture if they do want a Set per day.
            return {
                "kind": None,
                "candidates": [],
                "match": None,
                "evidence": [
                    {
                        "signal": "date_bucket",
                        "text": f"{dated} of {len(folders)} folders filed by date",
                        "dated": dated,
                    }
                ],
            }

        if distinct == len(folders):
            # Every name used once, so they are not labels - which rules Tag out
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


_KIND_LABEL = {
    Facet.PROJECT.value: "Project",
    Facet.SET.value: "Set",
    Facet.PERSON.value: "Person",
    Facet.TAG.value: "Tag",
    JUST_A_FOLDER: "just a folder",
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


def _display_name(path: str) -> str:
    r"""A non-empty name for a folder row.

    ``os.path.basename`` is empty for a filesystem root - ``/`` on POSIX, ``C:\``
    on Windows - so the root row of a read rooted there would reach the mapping
    screen with a blank name and no way to refer to it. Fall back to the path,
    which is the only name such a folder has.
    """
    return os.path.basename(path.rstrip(os.sep) or path) or path


def _clears_share(part: int, whole: int, share_pct: int) -> bool:
    """Whether *part* is at least *share_pct* of *whole*, without rounding.

    Both thresholds in this module go through here. `round(share * whole)` is
    the tempting spelling and it is wrong in the direction that matters: it
    rounds a threshold **down**, so `round(0.7 * 6) == 4` lets 66.7% clear a
    seventy-percent rule and `round(0.6 * 4) == 2` lets a 50% plurality clear a
    sixty-percent one. A floor that sometimes is not one is not a floor.
    """
    return part >= 1 and part * 100 >= share_pct * whole


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


def _grouped_by_parent(
    folders: list[_Folder], rows: list[dict[str, Any]]
) -> dict[int, list[set[str]]]:
    """What each parent's children read as, when this level clears the container bar.

    A child counts when its row's kind - or every one of its ``candidates`` -
    is a Set or a Person, or when it is a bare-date bucket: whichever of those
    the owner settles on, the folder above holds groups of pictures together.
    Empty when fewer than ``_LEVEL_VOTE_SHARE_PCT`` of the level count.
    """
    contained = []
    for folder, row in zip(folders, rows):
        proposal = row["proposal"]
        if reads_as_a_date(folder.name):
            kinds = {_DATE_BUCKET}
        elif proposal["kind"] is not None:
            kinds = {proposal["kind"]}
        else:
            kinds = set(proposal["candidates"])
        contained.append(kinds if kinds and kinds <= _CONTAINABLE else set())
    read = sum(1 for kinds in contained if kinds)
    if not _clears_share(read, len(folders), _LEVEL_VOTE_SHARE_PCT):
        return {}
    grouped: dict[int, list[set[str]]] = {}
    for folder, kinds in zip(folders, contained):
        if kinds and folder.parent_index is not None:
            grouped.setdefault(folder.parent_index, []).append(kinds)
    return grouped


def _capture_day(exif) -> Optional[str]:
    """``YYYY:MM:DD`` from EXIF DateTimeOriginal, else DateTime, else ``None``."""
    value = exif.get_ifd(0x8769).get(36867) or exif.get(306)
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return None


def _load_sample(path: str, decode: bool):
    """One sampled picture: ``(bgr, capture_day)`` from a single open.

    ``bgr`` is the downscaled array the face pass detects on, ``None`` when the
    file is unreadable or ``decode`` is off (no inference engine - the EXIF
    read is the only reason to open the file then).
    """
    try:
        with Image.open(path) as img:
            try:
                day = _capture_day(img.getexif())
            except Exception as exc:  # noqa: BLE001 - bad EXIF must not cost the face
                logger.warning(
                    "Folder-structure read: unreadable EXIF in %s (%s: %s) - "
                    "sampled as undated",
                    os.path.basename(path),
                    type(exc).__name__,
                    exc,
                )
                day = None
            if not decode:
                return None, day
            img.draft("RGB", (_INFERENCE_MAX_SIDE, _INFERENCE_MAX_SIDE))
            img = img.convert("RGB")
            longest = max(img.size)
            if longest > _INFERENCE_MAX_SIDE:
                scale = _INFERENCE_MAX_SIDE / longest
                img = img.resize(
                    (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
                )
            return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR), day
    except Exception as exc:  # noqa: BLE001 - a corrupt file is not a failed read
        logger.warning(
            "Folder-structure read: could not decode %s (%s: %s) - sampled as "
            "no-face and undated",
            os.path.basename(path),
            type(exc).__name__,
            exc,
        )
        return None, None


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
