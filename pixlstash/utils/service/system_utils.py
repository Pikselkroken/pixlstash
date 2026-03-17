"""System-level utilities (hardware detection, etc.)."""

import subprocess


def default_max_vram_gb() -> float:
    """Return default VRAM budget in GB: min(4GB, 50% of available VRAM).

    Falls back to 6GB when VRAM cannot be detected.
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
            return 6.0
        half_gb = (total_mb / 1024.0) / 2.0
        return round(min(4.0, half_gb), 2)
    except Exception:
        return 6.0
