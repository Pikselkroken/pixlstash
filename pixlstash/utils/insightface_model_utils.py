"""Helpers for selecting and provisioning the InsightFace model pack.

PixlStash's face pipeline runs through ``insightface.app.FaceAnalysis(name=<pack>)``.
``FaceAnalysis`` resolves ``~/.insightface/models/<pack>/`` and loads every
``*.onnx`` file it finds there. The default ``buffalo_l`` pack is in InsightFace's
auto-download zoo, so it provisions itself. ``auraface`` (``fal/AuraFace-v1``) is
**not** in that zoo, so when it is selected we download it from HuggingFace into
the expected directory before ``FaceAnalysis`` is constructed.

License note (the provenance decision is the user's; this module only makes the
switch available):

- ``buffalo_l`` (default): trained on WebFace600K — **non-commercial research
  use only**.
- ``auraface``: ``fal/AuraFace-v1`` weights are **Apache-2.0** licensed, suitable
  for users who need commercial use.
"""

from __future__ import annotations

import os
import shutil
import threading
import time

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.path_utils import resolve_path_within

logger = get_logger(__name__)

# Known, supported InsightFace model packs. Validation is fail-closed: an
# unrecognised value raises instead of letting FaceAnalysis try to fetch an
# arbitrary zoo name. Extend this set (and, for non-zoo packs, _DOWNLOADABLE_PACKS
# below) to add a new pack.
KNOWN_MODEL_PACKS: frozenset[str] = frozenset({"buffalo_l", "auraface"})

# The default pack, mirrored from the server-config default. Kept here so callers
# that need a sane fallback do not have to import the server module.
DEFAULT_MODEL_PACK = "buffalo_l"

# fal/AuraFace-v1 — Apache-2.0 weights. Pinned to a specific commit SHA for
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
_INSIGHTFACE_ROOT = os.path.expanduser(os.path.join("~", ".insightface"))

# After a failed download, suppress re-attempts (and repeated error logging) for
# this window so a hard-down network does not hammer HuggingFace or log on every
# planning cycle. Per-process; cleared on a successful download or process restart.
_DOWNLOAD_BACKOFF_SECONDS = 300.0
_download_failures: dict[str, float] = {}
_download_failures_lock = threading.Lock()


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
    """Return the on-disk directory FaceAnalysis loads *model_pack* from."""
    return os.path.join(_INSIGHTFACE_ROOT, "models", model_pack)


def _pack_is_present(model_pack: str) -> bool:
    """Return ``True`` if *model_pack*'s directory already holds ``.onnx`` files."""
    pack_dir = _pack_dir(model_pack)
    if not os.path.isdir(pack_dir):
        return False
    return any(name.lower().endswith(".onnx") for name in os.listdir(pack_dir))


def ensure_model_pack_available(model_pack: str) -> None:
    """Make sure *model_pack* is on disk where FaceAnalysis can find it.

    Validates the pack name first (fail-closed). For packs that InsightFace
    auto-downloads (e.g. ``buffalo_l``) this is a no-op — ``FaceAnalysis`` fetches
    them itself. For packs PixlStash provisions (e.g. ``auraface``), the pack is
    downloaded from a pinned HuggingFace revision into
    ``~/.insightface/models/<pack>/`` if it is not already present.

    Args:
        model_pack: The configured InsightFace model pack name.

    Raises:
        ValueError: If *model_pack* is not a known pack.
        RuntimeError: If a required download fails. The message explains that the
            user can place the pack manually in ``~/.insightface/models/<pack>/``.
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
