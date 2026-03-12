"""System-level utilities (hardware detection, etc.)."""

import subprocess


def default_max_vram_gb() -> float:
    """Return default VRAM budget in GB: min(4GB, 25% of available VRAM).

    Falls back to 4GB when VRAM cannot be detected.
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
        totals_mb = []
        for line in output.splitlines():
            value = line.strip()
            if not value:
                continue
            totals_mb.append(int(float(value)))
        total_mb = sum(totals_mb)
        if total_mb <= 0:
            return 4.0
        quarter_gb = (total_mb / 1024.0) / 4.0
        return round(min(4.0, quarter_gb), 2)
    except Exception:
        return 4.0
