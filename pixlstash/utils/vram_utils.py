"""VRAM budget utilities for GPU memory-aware batch sizing."""

import subprocess

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def query_total_vram_mb() -> int:
    """Return the total installed VRAM across all NVIDIA GPUs in MiB.

    Uses ``nvidia-smi`` to query installed VRAM.  Returns 0 if the query
    fails (e.g. on CPU-only machines or when nvidia-smi is not installed).
    """
    try:
        output = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        totals = []
        for line in output.splitlines():
            value = line.strip()
            if not value:
                continue
            totals.append(int(float(value)))
        return sum(totals)
    except Exception:
        return 0


def vram_limited_batch_cap(
    budget_mb: int | None,
    device: str,
    base_mb: int,
    per_item_mb: int,
) -> int:
    """Return the maximum batch size that fits within a VRAM budget.

    Args:
        budget_mb: Configured VRAM budget in MiB, or ``None`` for unlimited.
        device: Inference device string (``"cuda"`` enables the cap).
        base_mb: Fixed model footprint in MiB (loaded once).
        per_item_mb: Incremental VRAM per image/item in MiB.

    Returns:
        Maximum item count that fits, or ``10_000`` when the cap is inactive.
    """
    if device != "cuda" or not budget_mb:
        return 10_000
    reserve_mb = max(256, int(budget_mb * 0.20))
    task_budget_mb = max(1, budget_mb - reserve_mb)
    if task_budget_mb <= base_mb:
        return 1
    return max(1, int((task_budget_mb - base_mb) / max(1, per_item_mb)))
