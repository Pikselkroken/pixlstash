"""VRAM budget management for GPU-aware batch sizing."""

from __future__ import annotations

import torch

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.vram_utils import query_total_vram_mb, vram_limited_batch_cap

logger = get_logger(__name__)


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
        self._max_vram_usage_mb = requested_mb
        total_mb = query_total_vram_mb()
        if total_mb > 0 and requested_mb > total_mb:
            logger.warning(
                "Configured VRAM budget %.2f GB exceeds detected GPU total %.2f GB; "
                "keeping configured budget as requested.",
                requested_mb / 1024.0,
                total_mb / 1024.0,
            )
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

    def total_vram_mb(self) -> int:
        """Return total installed VRAM in MiB (0 on CPU or if unavailable)."""
        return query_total_vram_mb()
