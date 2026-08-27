"""VRAM budget management for GPU-aware batch sizing."""

from __future__ import annotations

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.vram_utils import query_total_vram_mb, vram_limited_batch_cap

logger = get_logger(__name__)

# Share of the configured budget each ORT CUDA session may hold in its arena.
# A cap, not a reservation: an arena only grows to what a run needs, so the
# caps may sum past 1.0 as long as the arenas that actually fill do not.
# Starting points, to be re-measured (plan §9.1) before moving any of these.
# WD14 at 448 px is the one that balloons. ``FaceAnalysis`` hands one dict to
# all five InsightFace sessions, so the share is sized for the largest:
# recognition (w600k_r50, 112 px) measured ~650 MB at 16 faces and ~1.0 GB
# at 32 (kSameAsRequested, HEURISTIC); detection at 256 px, batch 1, is
# ~70-90 MB and the landmark/attribute sessions are smaller still. The
# PixlStash tagger (~20 %) and CLIP allocate through torch, not ORT, and take
# what these leave.
ORT_ARENA_SHARE = {
    "wd14": 0.40,
    # None: uncapped. InsightFace was capped at 0.15 for one evening and it
    # failed in the field within the hour - "Available memory of 43939328 is
    # smaller than requested bytes of 102908160": five sessions sharing one
    # limit, and `kSameAsRequested` fragmenting the detector's arena until a
    # 98 MB request found 42 MB free. Its arena never ballooned (recognition
    # is chunked, detection is per image); WD14's did, and WD14 keeps its cap.
    "insightface_session": None,
}


class VramBudget:
    """Stateful VRAM budget for GPU-memory-aware batch sizing.

    Owns the configured budget ceiling and answers ``limited_batch_cap``
    queries from any inference workflow that needs to know how many images
    it can safely process in one pass.

    Args:
        device: Inference device string (``"cuda"`` or ``"cpu"``).
    """

    def __init__(self, device: str) -> None:
        self._device = device
        self._max_vram_usage_mb: int | None = None

    @property
    def device(self) -> str:
        """Inference device this budget is scoped to."""
        return self._device

    @property
    def max_vram_usage_mb(self) -> int | None:
        """Configured VRAM ceiling in MiB, or ``None`` for unlimited."""
        return self._max_vram_usage_mb

    def set_budget_gb(self, max_vram_gb: float | None) -> None:
        """Set the VRAM budget in gigabytes.

        No-ops (sets unlimited) when the device is not CUDA.

        Args:
            max_vram_gb: Budget in GiB, or ``None`` for unlimited.
        """
        if self._device != "cuda":
            self._max_vram_usage_mb = None
            logger.debug(
                "Ignoring VRAM budget because inference device is %s.",
                self._device,
            )
            return

        if max_vram_gb is None:
            self._max_vram_usage_mb = None
            return
        try:
            requested_mb = int(float(max_vram_gb) * 1024)
        except Exception:
            self._max_vram_usage_mb = None
            return
        if requested_mb <= 0:
            self._max_vram_usage_mb = None
            return
        total_mb = query_total_vram_mb()
        if total_mb > 0 and requested_mb > total_mb:
            logger.warning(
                "Configured VRAM budget %.2f GB exceeds detected GPU total %.2f GB; "
                "clamping to the installed total.",
                requested_mb / 1024.0,
                total_mb / 1024.0,
            )
            requested_mb = total_mb
        self._max_vram_usage_mb = requested_mb
        # Local import: torch costs seconds to import and is only needed once a
        # budget is actually being set on a CUDA device. Importing it at module
        # scope would make the API server and every test pay for it at startup.
        import torch

        try:
            free_bytes, _ = torch.cuda.mem_get_info()
            free_gb = free_bytes / 1024**3
            free_str = f"{free_gb:.1f} GB free VRAM"
        except Exception:
            free_str = "VRAM unknown"
        try:
            gpu_name = torch.cuda.get_device_name(0)
        except Exception:
            gpu_name = "GPU"
        logger.info(
            "CUDA inference: %s, %s, budget %.2f GB",
            gpu_name,
            free_str,
            self._max_vram_usage_mb / 1024.0,
        )

    def ort_cuda_provider_options(self, share: float | None) -> dict[str, object]:
        """CUDAExecutionProvider options for one ONNX Runtime session.

        ORT's default arena doubles on every growth (``kNextPowerOfTwo``) and
        never shrinks, which is where the "20+ GB" arenas came from and why
        the finders used to tear sessions down on every drain.
        ``kSameAsRequested`` grows by what was asked for, and ``HEURISTIC``
        skips the EXHAUSTIVE cudnn search that cost seconds per reload for an
        input size that never changes. The limit is ``share`` of the budget
        and is left unset when there is none: a cap nobody configured is an
        OOM nobody asked for.

        Args:
            share: Fraction of the configured budget, from
                :data:`ORT_ARENA_SHARE`; ``None`` for a session that must never
                be capped (only the cudnn search setting applies).

        Returns:
            Options dict for ``provider_options`` / a ``providers`` tuple.
        """
        options: dict[str, object] = {"cudnn_conv_algo_search": "HEURISTIC"}
        if share is None:
            # Uncapped, and ORT's own arena strategy with it: kSameAsRequested
            # only pays off against a limit, and fragments without one.
            return options
        options["arena_extend_strategy"] = "kSameAsRequested"
        if self._max_vram_usage_mb is not None:
            options["gpu_mem_limit"] = int(self._max_vram_usage_mb * share) * 1024**2
        return options

    def limited_batch_cap(self, base_mb: int, per_item_mb: int) -> int:
        """Return the maximum batch size that fits within the configured budget.

        Args:
            base_mb: Fixed model footprint in MiB (loaded once).
            per_item_mb: Incremental VRAM per image/item in MiB.

        Returns:
            Maximum item count, or ``10_000`` when the budget is inactive.
        """
        return vram_limited_batch_cap(
            self._max_vram_usage_mb,
            self._device,
            base_mb,
            per_item_mb,
        )
