"""The third-party ComfyUI node packs PixlStash knows how to install (#2054).

A pre-flight that finds a workflow's nodes missing names the pack that provides
them, and ``POST /comfyui/node-packs/{pack_id}/install`` has ComfyUI-Manager
install it. Only a pack listed here, at the version listed here: no id, URL or
version comes from the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class NodePack:
    """One installable pack: its Comfy Registry id, name, pin and node classes."""

    id: str
    name: str
    version: str
    classes: tuple[str, ...]


# A code constant rather than data, so that adding a pack or moving a pin is a
# change that goes through code review: each entry is third-party code PixlStash
# offers to install. The version is a Comfy Registry release, never `latest` or
# `nightly`, which would move under the review.
KNOWN_PACKS: tuple[NodePack, ...] = (
    NodePack(
        id="comfyui-inpaint-cropandstitch",
        name="ComfyUI-Inpaint-CropAndStitch",
        version="3.0.11",
        classes=("InpaintCropImproved", "InpaintStitchImproved"),
    ),
)


def pack(pack_id: str) -> Optional[NodePack]:
    """The known pack with exactly this id, or ``None``."""
    return next((known for known in KNOWN_PACKS if known.id == pack_id), None)


def packs_for(classes: Iterable[str]) -> list[dict]:
    """The known packs that provide any of *classes*, and which of them each does."""
    missing = list(classes)
    found = []
    for known in KNOWN_PACKS:
        nodes = [name for name in missing if name in known.classes]
        if nodes:
            found.append(
                {
                    "id": known.id,
                    "name": known.name,
                    "version": known.version,
                    "nodes": nodes,
                }
            )
    return found
