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

#: WD14's **process** VRAM in MiB: everything the tagger costs the card once
#: loaded, including the ~400 MiB CUDA context, plus one 448 px image on top.
#: This is the scheduler's admission model - what ``limited_batch_cap`` sizes a
#: batch against and what ``estimated_vram_mb`` reports - and it is the figure
#: measured after load (~904 MiB against 377 MiB of weights on disk).
WD14_BASE_MB = 900
WD14_PER_ITEM_MB = 220

#: The same session's **ORT arena** in MiB, which is a different quantity: ORT's
#: ``gpu_mem_limit`` governs only the CUDA EP's own allocator, not the context
#: and not anything torch holds. Bisected on an RTX 5090 against the real
#: ``wd-convnext-tagger-v3`` (ORT 1.28, CUDA EP) as ``385 + 111·n``, which
#: predicts every observed outcome: loads at 409 MiB, batch 1 needs 496, batch
#: 3 needs 718 and does run under the 819 MiB that a 2 GB budget's 40 % share
#: gives it. Carries ~10 % over the fit, because one card's bisect is not every
#: driver's allocator.
#:
#: Conflating the two is what made the first version of this wrong: the process
#: figure is roughly twice the arena figure, so using it as the arena floor
#: overshot every budget and left ``ORT_ARENA_SHARE["wd14"]`` applying nowhere.
WD14_ARENA_BASE_MB = 425
WD14_ARENA_PER_ITEM_MB = 122

#: Most images WD14 is ever asked to do at once, whatever the budget allows -
#: the tagging workflow's own concurrency ceiling. The arena floor is sized for
#: the batch that will actually run, so a large budget does not float the floor
#: past a share that already covers it.
MAX_CONCURRENT_GPU_IMAGES = 64


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

    def ort_cuda_provider_options(
        self, share: float | None, min_limit_mb: int = 0
    ) -> dict[str, object]:
        """CUDAExecutionProvider options for one ONNX Runtime session.

        ORT's default arena doubles on every growth (``kNextPowerOfTwo``) and
        never shrinks, which is where the "20+ GB" arenas came from and why
        the finders used to tear sessions down on every drain.
        ``kSameAsRequested`` grows by what was asked for, and ``HEURISTIC``
        skips the EXHAUSTIVE cudnn search that cost seconds per reload for an
        input size that never changes. The limit is ``share`` of the budget
        and is left unset when there is none: a cap nobody configured is an
        OOM nobody asked for.

        *share* is a split of the budget between sessions and knows nothing
        about what the session will be asked to run, so on a small budget it
        can land under what a single batch needs: WD14's 40 % of a 1 GB budget
        is 409 MiB, which loads the model and cannot run batch 1 (496 MiB).
        ``min_limit_mb`` - :meth:`wd14_arena_limit_mb` for that session - is
        the other half of the question and raises the cap where the share
        genuinely falls short. It is a floor, never a target: where the share
        already covers the batch it wins and the configured split stands, and
        the budget is the ceiling in either case.

        Args:
            share: Fraction of the configured budget, from
                :data:`ORT_ARENA_SHARE`; ``None`` for a session that must never
                be capped (only the cudnn search setting applies).
            min_limit_mb: Floor for the cap, in MiB - what this session's own
                arena needs for the batch it will be given. Ignored when there
                is no budget (nothing is capped) or when the share is larger.

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
            limit_mb = max(int(self._max_vram_usage_mb * share), int(min_limit_mb))
            limit_mb = min(limit_mb, self._max_vram_usage_mb)
            options["gpu_mem_limit"] = limit_mb * 1024**2
        return options

    def wd14_arena_limit_mb(self) -> int:
        """MiB WD14's ORT arena needs for the batch this budget will hand it.

        Two models, deliberately: the batch is sized against the **process**
        pair (:data:`WD14_BASE_MB` / :data:`WD14_PER_ITEM_MB`), because that is
        what the scheduler admits work against and what
        ``limited_batch_cap`` is calibrated for; the resulting batch is then
        costed against the **arena** pair, because ``gpu_mem_limit`` governs
        only the CUDA EP's allocator. Sizing with one and capping with the
        other is the whole point - and using the process pair for both is what
        made this overshoot.

        The batch is clamped to :data:`MAX_CONCURRENT_GPU_IMAGES`, the most
        the workflow ever runs at once, so a large budget does not inflate the
        floor past a share that already covers the real batch.

        Returns:
            Floor in MiB, or ``0`` when no budget is set - nothing is capped,
            so there is nothing to reconcile against.
        """
        if self._max_vram_usage_mb is None:
            return 0
        batch = min(
            MAX_CONCURRENT_GPU_IMAGES,
            self.limited_batch_cap(WD14_BASE_MB, WD14_PER_ITEM_MB),
        )
        return WD14_ARENA_BASE_MB + WD14_ARENA_PER_ITEM_MB * batch

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
