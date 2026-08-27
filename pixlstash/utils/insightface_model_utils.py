"""Helpers for selecting and provisioning the InsightFace model pack.

PixlStash's face pipeline runs through
``insightface.app.FaceAnalysis(name=<pack>, root=<root>)``. ``FaceAnalysis``
resolves ``<root>/models/<pack>/`` and loads every ``*.onnx`` file it finds
there. The default ``buffalo_l`` pack is in InsightFace's auto-download zoo, so
it provisions itself. ``auraface`` (``fal/AuraFace-v1``) is **not** in that zoo,
so when it is selected we download it from HuggingFace into the expected
directory before ``FaceAnalysis`` is constructed.

**The root is a recorded location** rather than a constant, so the packs can be
moved off the system drive - see :func:`insightface_root` and
``POST /model-folders/{id}/relocate``. It is recorded exactly as #905 records the
download folder's, and for the same reason: this path is machine-global, so it
does not belong to one deployment's config file.

License note (the provenance decision is the user's; this module only makes the
switch available):

- ``buffalo_l`` (default): trained on WebFace600K - **non-commercial research
  use only**.
- ``auraface``: ``fal/AuraFace-v1`` weights are **Apache-2.0** licensed, suitable
  for users who need commercial use.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.path_utils import resolve_path_within

logger = get_logger(__name__)

# Known, supported InsightFace model packs. Validation is fail-closed: an
# unrecognised value raises instead of letting FaceAnalysis try to fetch an
# arbitrary zoo name. Extend this set (and, for non-zoo packs, _DOWNLOADABLE_PACKS
# below) to add a new pack.
KNOWN_MODEL_PACKS: frozenset[str] = frozenset({"buffalo_l", "auraface"})

# The default pack, mirrored from the server-config default. Kept here so callers
# that need a sane fallback do not have to import the server module.
DEFAULT_MODEL_PACK = "buffalo_l"

# fal/AuraFace-v1 - Apache-2.0 weights. Pinned to a specific commit SHA for
# supply-chain integrity; do NOT track ``main``. SHA verified 2026-06-09 as the
# repo head; the pack bundles scrfd_10g_bnkps.onnx (the same SCRFD-10G detector
# as buffalo_l) plus glintr100.onnx for recognition.
_AURAFACE_REPO = "fal/AuraFace-v1"
_AURAFACE_REVISION = "af6d057c9b0ec4071d4c49c80e3539258798b609"

# Packs that PixlStash provisions itself from HuggingFace (i.e. not in the
# InsightFace auto-download zoo). buffalo_l is intentionally absent: InsightFace
# downloads it on demand.
_DOWNLOADABLE_PACKS: dict[str, tuple[str, str]] = {
    "auraface": (_AURAFACE_REPO, _AURAFACE_REVISION),
}

# Root InsightFace searches for model packs. FaceAnalysis defaults to
# ``~/.insightface`` and looks under ``<root>/models/<pack>/``.
DEFAULT_INSIGHTFACE_ROOT = os.path.expanduser(os.path.join("~", ".insightface"))

# Where a relocation records the packs' new home. A FILE beside the download
# folder's own pointer, for the reason `BUILTIN_MODEL_DIR_POINTER` gives and that
# applies here word for word: this path is machine-global - one set of packs
# serves every library and every server instance on the host, and InsightFace
# itself has exactly one root per machine - while `server-config.json` and the
# hub both belong to one deployment. Recording it per deployment would mean a
# second PixlStash on the same machine kept downloading packs to the old place,
# which is the divergence the single accessor below exists to remove.
INSIGHTFACE_ROOT_POINTER = "insightface.location"

# After a failed download, suppress re-attempts (and repeated error logging) for
# this window so a hard-down network does not hammer HuggingFace or log on every
# planning cycle. Per-process; cleared on a successful download or process restart.
_DOWNLOAD_BACKOFF_SECONDS = 300.0
_download_failures: dict[str, float] = {}
_download_failures_lock = threading.Lock()


def _pointer_path() -> str:
    """Where the recorded root is written.

    Deliberately the *same* directory ``builtin_models`` records its own folder
    in, resolved by calling that module's seam rather than rebuilding
    ``user_data_dir("pixlstash")`` here - a second expression for one directory
    is the drift #905 spent a PR removing, and a test that redirects one pointer
    must redirect both. The import is function-local so ``utils`` does not gain
    a module-level dependency on ``services``, and so the monkeypatched
    attribute is re-read on every call.
    """
    from pixlstash.services.builtin_models import _pixlstash_data_dir

    return os.path.join(_pixlstash_data_dir(), INSIGHTFACE_ROOT_POINTER)


def _configured_root() -> Optional[str]:
    """The root a relocation recorded, or None if it never happened.

    Unreadable is treated as never-relocated and said so loudly, the same policy
    :func:`~pixlstash.services.builtin_models.builtin_model_dir` applies to its
    own pointer: refusing to name a root at all would disable face detection
    entirely over one bad file.
    """
    pointer = _pointer_path()
    try:
        with open(pointer, encoding="utf-8") as handle:
            configured = handle.read().strip()
    except FileNotFoundError:
        # The normal state on a machine that has never relocated the packs.
        return None
    except OSError as exc:
        logger.error(
            "Could not read %s (%s), which records where the InsightFace packs "
            "live. Falling back to the default root; if they were relocated, "
            "the packs there will be downloaded again.",
            pointer,
            exc,
        )
        return None
    if not configured:
        logger.warning(
            "%s is empty, so it names no InsightFace root. Using the default. "
            "Delete the file to silence this.",
            pointer,
        )
        return None
    return configured


def recorded_insightface_root() -> tuple[Optional[str], str]:
    """The root a relocation recorded and the file it is recorded in.

    Both, because the one caller outside this module needs both and neither is
    worth reaching into the privates for: the shelf's declaration asks whether
    the directory it failed to read is the *recorded* one - which is what tells
    a relocated root that has gone apart from a machine that has simply never
    run face detection - and then names the file to delete.

    Quiet on purpose, unlike :func:`_configured_root`, which reports its own
    failures and is called on every resolution: a second caller that only wants
    to know whether a root was recorded would double every line it logs.

    Returns:
        ``(recorded root or None, pointer path)``.
    """
    pointer = _pointer_path()
    try:
        with open(pointer, encoding="utf-8") as handle:
            return (handle.read().strip() or None), pointer
    except OSError:
        # Already reported by the reader that resolves the root for real.
        return None, pointer


def insightface_root() -> str:
    """Where InsightFace keeps its packs.

    **The one answer, and everything that reads or writes the packs asks for
    it**: this module's ``auraface`` download, the shelf's declaration
    (:mod:`pixlstash.services.builtin_caches`) and the ``FaceAnalysis(root=…)``
    the face pipeline constructs. That single-accessor property is what makes
    the root relocatable at all - a location only half the callers read would
    move the shelf's row while the packs kept loading from the old directory,
    which is precisely the failure #905 had to remove from the download folder
    before it could be moved.

    Read on every call rather than cached, so a relocation applies to the next
    download instead of to the next restart.

    Returns:
        The recorded root, or :data:`DEFAULT_INSIGHTFACE_ROOT`.
    """
    return _configured_root() or DEFAULT_INSIGHTFACE_ROOT


def set_insightface_root(path: str) -> None:
    """Record where the InsightFace packs live from now on.

    Written after the packs have landed at *path* and before the hub is told the
    folder moved, so an interruption between the two leaves the pointer naming
    the place the packs really are - the ordering
    :func:`~pixlstash.services.builtin_models.set_builtin_model_dir` uses, for
    the same reason.

    Args:
        path: The InsightFace root the packs now live under.

    Raises:
        OSError: if the pointer could not be written. The caller decides - the
            packs have already moved by then, so this is news to report rather
            than a reason to undo anything.
    """
    pointer = _pointer_path()
    os.makedirs(os.path.dirname(pointer), exist_ok=True)
    with open(pointer, "w", encoding="utf-8") as handle:
        handle.write(path)
    logger.info(
        "InsightFace packs are now read from and downloaded into %s (recorded in %s).",
        path,
        pointer,
    )


def validate_model_pack(model_pack: str) -> str:
    """Return *model_pack* if it is a known pack, else raise (fail-closed).

    Args:
        model_pack: The configured InsightFace model pack name.

    Returns:
        The validated pack name.

    Raises:
        ValueError: If *model_pack* is not in :data:`KNOWN_MODEL_PACKS`.
    """
    if model_pack not in KNOWN_MODEL_PACKS:
        allowed = ", ".join(sorted(KNOWN_MODEL_PACKS))
        logger.error(
            "Unknown InsightFace model pack %r. Allowed packs: %s. Refusing to "
            "construct FaceAnalysis with an unrecognised name.",
            model_pack,
            allowed,
        )
        raise ValueError(
            f"Unknown InsightFace model pack {model_pack!r}. Allowed: {allowed}."
        )
    return model_pack


def _pack_dir(model_pack: str) -> str:
    """Return the on-disk directory FaceAnalysis loads *model_pack* from.

    Resolved per call rather than at import: the root is relocatable, and a
    module-level join would keep downloading into the directory the packs were
    moved out of.
    """
    return os.path.join(insightface_root(), "models", model_pack)


def _pack_is_present(model_pack: str) -> bool:
    """Return ``True`` if *model_pack*'s directory already holds ``.onnx`` files."""
    pack_dir = _pack_dir(model_pack)
    if not os.path.isdir(pack_dir):
        return False
    return any(name.lower().endswith(".onnx") for name in os.listdir(pack_dir))


def ensure_model_pack_available(model_pack: str) -> None:
    """Make sure *model_pack* is on disk where FaceAnalysis can find it.

    Validates the pack name first (fail-closed). For packs that InsightFace
    auto-downloads (e.g. ``buffalo_l``) this is a no-op - ``FaceAnalysis`` fetches
    them itself. For packs PixlStash provisions (e.g. ``auraface``), the pack is
    downloaded from a pinned HuggingFace revision into
    ``<insightface_root()>/models/<pack>/`` if it is not already present.

    Args:
        model_pack: The configured InsightFace model pack name.

    Raises:
        ValueError: If *model_pack* is not a known pack.
        RuntimeError: If a required download fails. The message explains that the
            user can place the pack manually in the pack directory.
    """
    validate_model_pack(model_pack)

    if model_pack not in _DOWNLOADABLE_PACKS:
        # InsightFace auto-downloads this pack; nothing to provision.
        logger.debug(
            "InsightFace pack %r is auto-downloaded by FaceAnalysis; skipping "
            "PixlStash provisioning.",
            model_pack,
        )
        return

    if _pack_is_present(model_pack):
        logger.debug(
            "InsightFace pack %r already present at %s; skipping download.",
            model_pack,
            _pack_dir(model_pack),
        )
        return

    with _download_failures_lock:
        last_failure = _download_failures.get(model_pack)
        if last_failure is not None:
            elapsed = time.monotonic() - last_failure
            if elapsed < _DOWNLOAD_BACKOFF_SECONDS:
                remaining = _DOWNLOAD_BACKOFF_SECONDS - elapsed
                # Quiet (debug) on purpose: the first failure already logged an
                # error; re-attempts inside the window must not spam the log.
                logger.debug(
                    "InsightFace pack %r download is in backoff (%.0fs remaining "
                    "after a recent failure); not retrying.",
                    model_pack,
                    remaining,
                )
                raise RuntimeError(
                    f"InsightFace pack {model_pack!r} download is in backoff after "
                    f"a recent failure; retry in {remaining:.0f}s or place the pack "
                    f"manually in {_pack_dir(model_pack)}."
                )

    repo_id, revision = _DOWNLOADABLE_PACKS[model_pack]
    pack_dir = _pack_dir(model_pack)
    logger.info(
        "InsightFace pack %r not found locally; downloading %s (revision %s) into %s …",
        model_pack,
        repo_id,
        revision,
        pack_dir,
    )
    try:
        from huggingface_hub import snapshot_download  # type: ignore[import]

        os.makedirs(pack_dir, exist_ok=True)
        # AuraFace lays its .onnx files at the repo root, so the snapshot contents
        # need flattening into pack_dir. Download into a cache, then copy the
        # .onnx files to the exact layout FaceAnalysis expects:
        #   ~/.insightface/models/<pack>/<file>.onnx
        snapshot_path = snapshot_download(
            repo_id,
            revision=revision,
            allow_patterns=["*.onnx"],
        )
        copied = 0
        for root, _dirs, files in os.walk(snapshot_path):
            for name in files:
                if not name.lower().endswith(".onnx"):
                    continue
                src = os.path.join(root, name)
                # Harden against a crafted filename escaping pack_dir (defence in
                # depth beyond the SHA pin + allow_patterns); also marks the join
                # as sanitised for CodeQL.
                dst = resolve_path_within(pack_dir, name)
                shutil.copy2(src, dst)
                copied += 1
        if copied == 0:
            raise RuntimeError(
                f"No .onnx files found in downloaded snapshot for {repo_id}"
            )
        logger.info(
            "InsightFace pack %r downloaded: copied %d .onnx file(s) into %s",
            model_pack,
            copied,
            pack_dir,
        )
        with _download_failures_lock:
            _download_failures.pop(model_pack, None)
    except Exception as exc:
        with _download_failures_lock:
            _download_failures[model_pack] = time.monotonic()
        logger.error(
            "Failed to download InsightFace pack %r from %s (revision %s): %s",
            model_pack,
            repo_id,
            revision,
            exc,
        )
        raise RuntimeError(
            f"Could not provision InsightFace pack {model_pack!r} from "
            f"{repo_id}@{revision}: {exc}. You can place the pack manually in "
            f"{pack_dir} (the .onnx files at the repository root) and retry."
        ) from exc
