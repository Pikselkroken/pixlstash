"""The other two roots PixlStash's models live in, declared so they are visible.

:mod:`pixlstash.services.builtin_models` declares the folder PixlStash downloads
its *own* engines into. It is not the only place models land on this disk, and
the other two were invisible: InsightFace keeps face packs under
``~/.insightface/models``, and everything fetched through ``huggingface_hub``
goes to the HuggingFace cache. On a measured machine that is 1.8 GB and 116 GB
respectively - 100x the built-in folder - and the shelf showed neither, so the
owner had no way to see where their disk had gone.

**Declared, never scanned - for the same reason and one more.** The scanner
yields only ``.safetensors``: InsightFace holds ONNX and would list as empty,
and the HuggingFace cache is a content-addressed blob store whose 37
``.safetensors`` sit behind symlinks in ``snapshots/``. Pointing a walk at it
would read 116 GB to learn what its own index already knows. So these carry
``owner``, which is what makes the scanner skip them, exactly as the built-in
folder does.

**Discovered rather than restated, which is the opposite of
``builtin_models``.** That module declares filenames because it *chose* those
downloads and duplicating two strings beats importing onnxruntime at start-up.
Neither applies here: the contents of these roots are whatever the owner and the
tools put there, so a fixed list would be a guess that goes stale. Both are read
from a cheap index instead - one ``listdir`` for InsightFace, and
``scan_cache_dir()`` for HuggingFace, which reads the cache's own bookkeeping and
measured **0.01 s against 116 GB and 26 repos**.

**``provenance`` stays ``builtin`` for every row here**, which is a claim about
*how the row got written* - declared by PixlStash's own registration rather than
scanned out of a folder the owner assembled - and not a claim that PixlStash
chose the model. It did not choose most of them. ``external`` would say the row
came from a scan, which is the thing that never happens to these folders.

**``file_kind`` is where "we chose it" IS the claim, so it is only made for the
repos we chose.** The HuggingFace cache is shared with every other tool on the
machine, and ``engine`` means "PixlStash downloaded this for itself" - which is
what every shelf verb refuses to touch. Written over the whole cache it locked
the owner out of their own models: their checkpoint could not be renamed or
reclassified, and the refusal told them PixlStash had downloaded it. Only
:data:`~pixlstash.services.model_features.OUR_REPOS` gets ``engine``; the rest
are the owner's, and carry ``owner_curatable`` so the declaration states them
once and then stops overwriting what the owner corrects. The InsightFace packs
are not split this way because PixlStash fetches every one of them.
"""

from __future__ import annotations

import os
from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.services.builtin_models import (
    MOVABLE_FIXED,
    STATE_NOT_DOWNLOADED,
    DeclaredEntry,
    declare_folder,
)
from pixlstash.services.model_features import (
    FEATURE_CHECKPOINT,
    OUR_REPOS,
    features_for_repo,
)
from pixlstash.services.model_folder_scanner import STATE_PRESENT
from pixlstash.utils.adapter_header import (
    FILE_CHECKPOINT,
    FILE_ENGINE,
    FILE_UNKNOWN,
)
from pixlstash.utils.insightface_model_utils import (
    DEFAULT_INSIGHTFACE_ROOT,
    KNOWN_MODEL_PACKS,
    insightface_root,
    recorded_insightface_root,
)

logger = get_logger(__name__)

# Lets a deployment point the HuggingFace listing somewhere else. It is the last
# of these left, and it is safe because that cache is `fixed`: it cannot be
# relocated, so there is nothing for an override to disagree with.
#
# **InsightFace had one and no longer does (#906).** `PIXLSTASH_INSIGHTFACE_DIR`
# named the *models* directory, one level below the root that is now recorded, so
# the two could disagree - inert while nothing could relocate, a bug the moment
# something could: the shelf would declare the override path while downloads and
# `FaceAnalysis` used the root, and a relocation identified by
# `insightface_models_dir()` would repoint the row at a directory the next start
# would not declare. It had no callers, in the product or the suite.
HUGGINGFACE_CACHE_DIR_ENV = "PIXLSTASH_HUGGINGFACE_CACHE_DIR"

# InsightFace's own layout: it joins this onto whatever root it is given.
INSIGHTFACE_MODELS_SUBDIR = "models"

# InsightFace stores each pack as a directory. The zip it downloaded to build
# one sits beside it and is the tool's own leftover, not a model - the same
# judgement `TOOLING_DIRS` makes about `.cache`.
_PACK_ARCHIVE_SUFFIX = ".zip"


def insightface_models_dir_under(root: str) -> str:
    """The directory ``FaceAnalysis`` loads packs from, given an InsightFace root.

    ``models`` is InsightFace's own layout and is not ours to choose: the
    library joins it onto whatever ``root`` it is given. That is why relocating
    the packs is a change of *root* rather than of this folder - see
    ``POST /model-folders/{id}/relocate``.

    Args:
        root: An InsightFace root.

    Returns:
        The absolute models directory under it.
    """
    return os.path.join(root, INSIGHTFACE_MODELS_SUBDIR)


def insightface_models_dir() -> str:
    """Where InsightFace keeps its model packs.

    ``FaceAnalysis`` looks under ``<root>/models/<pack>/``, so the *models*
    subdirectory is the folder that holds models - the root itself also holds
    the tool's own state and would be a less honest thing to call a model
    folder.

    **Derived from :func:`~pixlstash.utils.insightface_model_utils.insightface_root`
    and from nothing else**, so this and the two callers that resolve pack paths
    for themselves cannot name different directories. There is deliberately no
    environment override beside it (see the note above): a second source for
    this one path is exactly what makes a relocation move the shelf's row away
    from where the packs actually load.

    Returns:
        The absolute path.
    """
    return insightface_models_dir_under(insightface_root())


def is_insightface_models_dir(path: str) -> bool:
    """Whether *path* is the folder the InsightFace packs live in.

    By path rather than by ``kind``/``owner``/``movable``, for the reason
    :func:`~pixlstash.services.builtin_models.is_builtin_model_dir` gives about
    its own folder: ``declare_folder`` writes the same three values for every
    root PixlStash declares, so those columns cannot tell them apart.
    """
    return os.path.realpath(path) == os.path.realpath(insightface_models_dir())


def huggingface_cache_dir() -> Optional[str]:
    """Where ``huggingface_hub`` caches everything it downloads.

    Asked of the library rather than rebuilt from ``HF_HOME``: the resolution
    order is the library's (``HF_HUB_CACHE``, then ``HF_HOME``, then the
    platform default) and restating it here would be a copy that drifts the
    first time they change it.

    Returns:
        The absolute path, or None when ``huggingface_hub`` is not installed.
    """
    override = os.environ.get(HUGGINGFACE_CACHE_DIR_ENV, "").strip()
    if override:
        return override
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        return str(HF_HUB_CACHE)
    except ImportError as exc:
        # Not an error: the cache only exists because something downloaded
        # through the library, so its absence means there is nothing to show.
        logger.info(
            "huggingface_hub is not installed (%s); the HuggingFace cache will "
            "not be listed on the shelf. Nothing is wrong - there is simply no "
            "cache to describe.",
            exc,
        )
        return None


def _directory_size(path: str) -> Optional[int]:
    """Total bytes of a directory tree, or None if it could not be read.

    Used only on InsightFace packs, which are a handful of files each. It is
    never pointed at the HuggingFace cache, whose size comes from an index.
    """
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.stat(os.path.join(root, name)).st_size
                except OSError as exc:
                    logger.warning(
                        "Could not size %r inside InsightFace pack %r (%s); the "
                        "pack's reported size will be short by that file.",
                        name,
                        path,
                        exc,
                    )
    except OSError as exc:
        logger.warning(
            "Could not walk InsightFace pack %r (%s); it will be declared "
            "without a size rather than omitted, so it stays visible.",
            path,
            exc,
        )
        return None
    return total


def declare_insightface_packs(hub, folder_path: str) -> Optional[int]:
    """Register the InsightFace models folder and a row per pack.

    **The packs on disk union the packs we know about.** Listing only
    :data:`KNOWN_MODEL_PACKS` would hide ``antelopev2`` and ``buffalo_s``, which
    a measured machine has and which are exactly what the owner wants to see;
    listing only what is on disk would silently drop a pack we provision that
    has not downloaded yet. Both are declared, and an absent known pack lands as
    ``missing`` - a state, not a warning, the same as the ViT-L/14 scorer.

    Args:
        hub: The open hub database.
        folder_path: The InsightFace models directory.

    Returns:
        The ``model_folder.id``, or None if nothing could be declared.
    """
    try:
        on_disk = {
            name
            for name in os.listdir(folder_path)
            if not name.endswith(_PACK_ARCHIVE_SUFFIX)
            and os.path.isdir(os.path.join(folder_path, name))
        }
    except OSError as exc:
        recorded, pointer = recorded_insightface_root()
        recorded_is_this_folder = recorded is not None and os.path.realpath(
            folder_path
        ) == os.path.realpath(insightface_models_dir_under(recorded))
        if recorded_is_this_folder and os.path.normpath(
            folder_path
        ) == os.path.normpath(insightface_models_dir_under(DEFAULT_INSIGHTFACE_ROOT)):
            # Recorded back onto the root InsightFace uses anyway. The warning
            # would tell the owner to delete the record to go back to the
            # directory they are already on, so it falls through to the ordinary
            # line below instead of saying nothing at all.
            recorded_is_this_folder = False
        if recorded_is_this_folder:
            # A relocation put the packs here and they are not here, which is
            # the download folder's failure in the folder beside it: they are
            # re-fetched into a path PixlStash re-creates, and until this line
            # nothing said so. Not "normal" - the machine has run face
            # detection, which is why it has a recorded root at all. Recognised
            # by the record rather than by "not the default", because the owner
            # who symlinked `~/.insightface` at their big drive and then
            # recorded it has a default that resolves to the same place.
            logger.warning(
                "A relocation recorded %s as the InsightFace root and %s cannot "
                "be read (%s), so every pack will be downloaded again into a "
                "re-created path. Restore, mount or re-permission it. If it is "
                "gone for good, deleting %s sends face detection back to %s - "
                "but nothing is moved back, and PixlStash stops recognising the "
                "old root as one it can relocate.",
                recorded,
                folder_path,
                exc,
                pointer,
                DEFAULT_INSIGHTFACE_ROOT,
            )
            return None
        # InsightFace creates this the first time it downloads a pack, so an
        # absent directory is a machine that has not used face detection yet.
        logger.info(
            "InsightFace models directory %r could not be listed (%s); it will "
            "not be declared. This is normal on a machine that has not run face "
            "detection.",
            folder_path,
            exc,
        )
        return None

    entries = []
    for pack in sorted(on_disk | set(KNOWN_MODEL_PACKS)):
        absolute = os.path.join(folder_path, pack)
        present = pack in on_disk
        entries.append(
            DeclaredEntry(
                relpath=pack,
                display_name=f"InsightFace {pack}",
                role="face",
                size=_directory_size(absolute) if present else None,
                # The listing above succeeded, so an absent pack is a fact we
                # read rather than one we could not look at: `not_downloaded`,
                # never `unreachable`. A directory we could not list at all
                # returns early instead of declaring anything.
                state=STATE_PRESENT if present else STATE_NOT_DOWNLOADED,
            )
        )
    return declare_folder(hub, folder_path, entries)


def declare_huggingface_cache(hub, folder_path: str) -> Optional[int]:
    """Register the HuggingFace cache and a row per cached repo.

    **A repo, not a file.** The cache is content-addressed: the bytes live in
    ``blobs/`` under their hash and ``snapshots/`` symlinks names onto them, so
    a per-file listing would show the same weights once per revision and mean
    nothing to the reader. ``repo_id`` is the unit a person recognises, and
    ``size_on_disk`` is the number they came to find.

    Args:
        hub: The open hub database.
        folder_path: The HuggingFace cache directory.

    Returns:
        The ``model_folder.id``, or None if the cache could not be read.
    """
    try:
        from huggingface_hub import scan_cache_dir
    except ImportError as exc:
        logger.info(
            "huggingface_hub is not installed (%s); its cache will not be "
            "listed on the shelf.",
            exc,
        )
        return None

    try:
        info = scan_cache_dir(folder_path)
    except Exception as exc:
        # `CacheNotFound` when nothing has been downloaded, and `CorruptedCache`
        # when the cache is mid-write. Neither is worth failing start-up over,
        # and both are named rather than swallowed.
        logger.info(
            "Could not read the HuggingFace cache at %r (%s); it will not be "
            "listed this session. A cache that has never been written is the "
            "usual reason.",
            folder_path,
            exc,
        )
        return None

    entries = []
    for repo in info.repos:
        # The features it powers, not `repo_type` - which is `model` for all 26
        # repos on a real machine and therefore says nothing. Classified once
        # per repo: the primary label goes in `model.kind` for the Kind column
        # and the whole set goes to `model_capability`, because Florence-2 and
        # the embedder's CLIP each serve two and a reader deciding what is safe
        # to delete has to see both.
        capabilities = features_for_repo(repo)
        # **This cache is shared, and most of it is not ours.** `OUR_REPOS` is
        # the list PixlStash's own code fetches; everything else beside it was
        # put there by the owner or by another tool, and calling those rows
        # `engine` made the shelf claim we downloaded them. That claim is what
        # every verb refuses on - so correcting the Kind of a checkpoint the
        # owner downloaded themselves came back "1 of these are engines
        # PixlStash downloaded for itself … listed so you can see them, not
        # curated", about a file PixlStash has never loaded.
        #
        # `file_kind` for those is what the classifier read off the repo, which
        # for a full diffusers pipeline is a genuine answer and otherwise is
        # `unknown` - the same word the unclaimed leftovers in the built-in
        # folder carry, chosen there for this exact reason: an `engine` row is
        # the one state on the shelf nothing can act on.
        ours = str(getattr(repo, "repo_id", "") or "") in OUR_REPOS
        entries.append(
            DeclaredEntry(
                # The repo's own directory name (`models--org--name`), which is
                # what it is called inside this folder. `repo_id` is the display
                # name.
                relpath=os.path.basename(str(repo.repo_path)),
                display_name=repo.repo_id,
                role=capabilities[0],
                size=int(repo.size_on_disk),
                # Every repo the cache index yields is one it holds bytes for.
                state=STATE_PRESENT,
                capabilities=capabilities,
                file_kind=(
                    FILE_ENGINE
                    if ours
                    else (
                        FILE_CHECKPOINT
                        if capabilities[0] == FEATURE_CHECKPOINT
                        else FILE_UNKNOWN
                    )
                ),
                owner_curatable=not ours,
            )
        )
    return declare_folder(hub, folder_path, entries, movable=MOVABLE_FIXED)
