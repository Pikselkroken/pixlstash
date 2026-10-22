"""Which GPU PixlStash runs on, applied before anything loads torch.

PixlStash runs on exactly one GPU (G1). The owner picks it in Settings ›
Compute, or leaves it on Auto, which takes a spare: a discrete GPU of at least
``MIN_SPARE_MB`` that ComfyUI is not on, so the two never share a card (G2).

It is applied by visible devices and a restart (G3): before torch or ONNX
Runtime is imported, ``CUDA_VISIBLE_DEVICES`` is set to the chosen GPU's UUID,
so both see that one card as device 0 and every ``"cuda"`` string in the code
keeps working. The VRAM arbiter is unaffected: NVML is not limited by the
variable, so it still sees every card.

**This module imports only the standard library, platformdirs and pynvml,** and
``pixlstash/app.py`` imports it first. Anything imported ahead of it that pulls
in torch makes the variable a no-op (a guardrail test asserts it does not).

ponytail: NVIDIA only. AMD (``HIP_VISIBLE_DEVICES``) and integrated parts are
not listed until an AMD multi-GPU box can be tested; on such a machine nothing
here changes what PixlStash runs on.
"""

from __future__ import annotations

import json
import logging
import os
import sys

from platformdirs import user_config_dir

logger = logging.getLogger(__name__)

try:
    import pynvml
except Exception as exc:  # pragma: no cover - optional dependency
    logger.warning("pynvml is unavailable, so no GPU can be picked: %s", exc)
    pynvml = None

#: ``server-config.json`` keys. ``compute_gpu`` is ``auto`` or a GPU UUID;
#: ``comfyui_gpu_uuid`` is the card ComfyUI was last seen on, so Auto can avoid
#: it at a boot where ComfyUI is not running yet.
SETTING = "compute_gpu"
COMFYUI_GPU = "comfyui_gpu_uuid"
AUTO = "auto"

#: The smallest card Auto takes as a spare.
MIN_SPARE_MB = 4096

#: Set by ``apply`` to the UUID it chose, so the running server can say which
#: GPU it is on without asking torch.
RESOLVED_ENV = "PIXLSTASH_GPU_UUID"

_MB = 1024 * 1024


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def inventory(nvml=pynvml) -> list[dict]:
    """Every NVIDIA GPU NVML lists: ``{uuid, name, vendor, total_mb, free_mb}``."""
    if nvml is None:
        return []
    try:
        nvml.nvmlInit()
        gpus = []
        for index in range(nvml.nvmlDeviceGetCount()):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            memory = nvml.nvmlDeviceGetMemoryInfo(handle)
            gpus.append(
                {
                    "uuid": _text(nvml.nvmlDeviceGetUUID(handle)),
                    "name": _text(nvml.nvmlDeviceGetName(handle)),
                    "vendor": "nvidia",
                    "total_mb": int(memory.total) // _MB,
                    "free_mb": int(memory.free) // _MB,
                    # NVML never lists an integrated GPU.
                    "integrated": False,
                    "backend": "cu128",
                }
            )
        return gpus
    except Exception as exc:
        logger.info("No NVIDIA GPUs to choose from: %s", exc)
        return []


def _short(name: str) -> str:
    return name.replace("NVIDIA ", "").replace("GeForce ", "")


def choose(
    gpus: list[dict], selected: str | None, comfyui_uuid: str | None
) -> tuple[str | None, str]:
    """The GPU PixlStash should run on, and one sentence saying why.

    A GPU named by *selected* that is still here wins. Auto takes the spare
    with the most memory, else the GPU with the most memory, which is then
    shared with ComfyUI and arbitrated.
    """
    if not gpus:
        return None, "No NVIDIA GPU was found."
    if selected and selected != AUTO:
        chosen = next((gpu for gpu in gpus if gpu["uuid"] == selected), None)
        if chosen is not None:
            return chosen["uuid"], f"Uses the {_short(chosen['name'])} you chose."
        logger.warning(
            "The GPU chosen for PixlStash (%s) is not in this machine any more; "
            "picking one automatically.",
            selected,
        )
    if comfyui_uuid is None:
        # Never seen: ComfyUI's own default without --cuda-device is the first.
        comfyui_uuid = gpus[0]["uuid"]
    spares = [
        gpu
        for gpu in gpus
        if gpu["uuid"] != comfyui_uuid
        and not gpu["integrated"]
        and gpu["total_mb"] >= MIN_SPARE_MB
    ]
    if spares:
        spare = max(spares, key=lambda gpu: gpu["total_mb"])
        return spare["uuid"], (
            f"Picks {_short(spare['name'])} ({spare['total_mb'] / 1024:.1f} GB), "
            "which nothing else is using."
        )
    biggest = max(gpus, key=lambda gpu: gpu["total_mb"])
    if len(gpus) == 1:
        return biggest["uuid"], (
            f"Shares the {_short(biggest['name'])} with ComfyUI; PixlStash waits "
            "for room rather than running out of memory."
        )
    return biggest["uuid"], (
        f"Shares the {_short(biggest['name'])} with ComfyUI: no other GPU has "
        f"{MIN_SPARE_MB // 1024} GB."
    )


def config_path(argv: list[str] | None = None) -> str:
    """The server config ``app.py`` will read: ``--server-config``, else the default."""
    argv = sys.argv if argv is None else argv
    for index, arg in enumerate(argv):
        if arg == "--server-config" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--server-config="):
            return arg.split("=", 1)[1]
    return os.path.join(user_config_dir("pixlstash"), "server-config.json")


def read_config(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("Could not read %s to choose a GPU: %s", path, exc)
        return {}


def apply(path: str | None = None, nvml=pynvml) -> str | None:
    """Choose PixlStash's GPU and make it the only one torch will see.

    A ``CUDA_VISIBLE_DEVICES`` the owner set themselves always wins, and is
    logged. With fewer than two GPUs there is nothing to choose and nothing is
    set. Returns the UUID chosen, or None.
    """
    gpus = inventory(nvml)
    if len(gpus) < 2:
        return None
    config = read_config(path or config_path())
    chosen, reason = choose(gpus, config.get(SETTING), config.get(COMFYUI_GPU))
    if chosen is None:
        return None
    own = os.environ.get("CUDA_VISIBLE_DEVICES")
    if own is not None:
        logger.warning(
            "CUDA_VISIBLE_DEVICES is set to %r, so PixlStash uses that rather "
            "than the GPU chosen in Settings (%s).",
            own,
            chosen,
        )
        return None
    if "torch" in sys.modules:
        logger.error(
            "torch was imported before PixlStash chose its GPU, so the choice "
            "(%s) cannot take effect this session.",
            chosen,
        )
        return None
    os.environ["CUDA_VISIBLE_DEVICES"] = chosen
    os.environ[RESOLVED_ENV] = chosen
    logger.info("PixlStash runs on GPU %s: %s", chosen, reason)
    return chosen
