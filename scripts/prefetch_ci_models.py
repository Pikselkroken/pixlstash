"""Download every model the CI backend gate loads, into the cached locations.

CI's model cache is written by exactly one warming job (``warm_models`` in
``.github/workflows/ci.yml``) and only read by the eight ``backend`` shards, so
whatever this script misses is a model those eight shards fetch from
HuggingFace *in parallel, on every run*. That is what produced the 429
stampede that killed ``backend shard 8`` in run 31304213276: CLIP was absent
from a cache entry that kept hitting its primary key, so ``actions/cache``
never rewrote it and every run re-downloaded the weights.

Prefetching through each service's own entry point, rather than a duplicated
list of repo ids, is what keeps the set honest: a model swap edits the service,
and the service is what runs here.

Three on-disk locations are involved, and all three are in the cached paths:

* ``~/.cache/huggingface`` - the HF hub cache, where CLIP, SBert and Florence
  land.
* ``<user_data_dir>/downloaded_models`` - where the WD14 and PixlStash taggers
  land, because they call ``hf_hub_download(local_dir=...)``, which bypasses
  the hub cache entirely.
* ``~/.insightface/models`` - where InsightFace keeps its face packs. The
  default ``buffalo_l`` pack is not on HuggingFace: ``FaceAnalysis`` downloads
  it from a GitHub release asset, and when that delivery was unhealthy face
  extraction never finished and a whole test module errored at setup (run
  42467486129, ``backend-windows shard 3``).

Failures are fatal on purpose. The warming job only saves the cache when this
script exits 0, so a half-downloaded model set is discarded and retried on the
next run instead of being frozen under the primary key.
"""

from __future__ import annotations

import logging
import os
import sys

from huggingface_hub import snapshot_download
from platformdirs import user_data_dir

from pixlstash.tagger_plugins.clip_service import ClipService
from pixlstash.tagger_plugins.florence2 import (
    DEFAULT_FLORENCE_VARIANT,
    FLORENCE_MODEL_VARIANTS,
)
from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService
from pixlstash.tagger_plugins.sbert import SBertService
from pixlstash.tagger_plugins.wd14 import WD14Service
from pixlstash.utils.insightface_model_utils import (
    DEFAULT_MODEL_PACK,
    insightface_root,
)

logger = logging.getLogger("prefetch_ci_models")


def _insightface_pack_dir(model_pack: str) -> str:
    """The directory ``FaceAnalysis(name=model_pack, root=...)`` loads from."""
    return os.path.join(insightface_root(), "models", model_pack)


def _insightface_pack_is_present(model_pack: str) -> bool:
    """Whether *model_pack*'s directory holds at least one ``.onnx`` file."""
    pack_dir = _insightface_pack_dir(model_pack)
    return os.path.isdir(pack_dir) and any(
        name.lower().endswith(".onnx") for name in os.listdir(pack_dir)
    )


def _prefetch_insightface_pack(model_pack: str) -> None:
    """Download *model_pack* through InsightFace's own zoo downloader.

    Not ``ensure_model_pack_available``: that provisions only the packs
    PixlStash downloads itself (``auraface``) and is deliberately a no-op for
    zoo packs like ``buffalo_l``, which ``FaceAnalysis`` fetches on construction
    via exactly this ``ensure_available`` call. Calling it directly fetches the
    pack without loading five ONNX sessions.

    InsightFace leaves the downloaded ``<pack>.zip`` beside the extracted
    directory. It is removed so the cache entry does not carry the pack twice.
    """
    # Local import: insightface drags in onnxruntime, and nothing else here
    # needs it.
    from insightface.utils.storage import ensure_available

    root = insightface_root()
    ensure_available("models", model_pack, root=root)
    leftover_zip = os.path.join(root, "models", f"{model_pack}.zip")
    if os.path.isfile(leftover_zip):
        os.remove(leftover_zip)


def main() -> int:
    """Populate all three cache locations with the gate's full model set."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    model_dir = os.path.join(user_data_dir("pixlstash"), "downloaded_models")
    os.makedirs(model_dir, exist_ok=True)

    logger.info("CLIP ...")
    ClipService("cpu").ensure_ready()

    logger.info("SBert ...")
    SBertService("cpu").ensure_ready()

    # Only the default Florence variant: the other variant is exercised solely
    # by tests/test_florence_model_variant.py, which never loads a model.
    florence = FLORENCE_MODEL_VARIANTS[DEFAULT_FLORENCE_VARIANT]
    logger.info("Florence-2 (%s) ...", florence["model"])
    snapshot_download(florence["model"], revision=florence["revision"])

    # Both taggers report readiness with a pure file-existence check, and
    # PixlStashTaggerService.download() logs and swallows its own failures, so
    # needs_download() is the only honest confirmation that the bytes landed.
    logger.info("WD14 tagger ...")
    wd14 = WD14Service(device="cpu", model_dir=model_dir, batch_size_fn=lambda: 1)
    wd14.download()

    logger.info("PixlStash tagger ...")
    tagger = PixlStashTaggerService(
        device="cpu", model_dir=model_dir, batch_size_fn=lambda: 1
    )
    tagger.download()

    logger.info("InsightFace %s ...", DEFAULT_MODEL_PACK)
    _prefetch_insightface_pack(DEFAULT_MODEL_PACK)

    missing = [
        name
        for name, service in (("WD14", wd14), ("PixlStash tagger", tagger))
        if service.needs_download()
    ]
    if not _insightface_pack_is_present(DEFAULT_MODEL_PACK):
        missing.append(f"InsightFace {DEFAULT_MODEL_PACK}")
    if missing:
        logger.error("Still missing after download: %s", ", ".join(missing))
        return 1

    logger.info("Model cache is complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
