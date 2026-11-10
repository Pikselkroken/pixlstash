"""LoRA/fine-tune training wrapper for PixlStash.

This package contains the abstraction PixlStash uses to drive an external
trainer (currently ai-toolkit). The public surface is intentionally small:

- :mod:`pixlstash.training.backend` defines the trainer-agnostic interface
  (:class:`TrainerBackend`) and the plain dataclasses passed across it
  (:class:`TrainingSpec`, :class:`TrainingHandle`, :class:`TrainingProgress`).
- :mod:`pixlstash.training.local_ai_toolkit` is the *only* module that knows
  ai-toolkit specifics (the "firewall"). A trainer version bump should be a
  change confined to that module and the SEAM contract it implements against
  (``pixlstash/data/ai-toolkit-jobs/SEAM.md``).
- :mod:`pixlstash.training.config_render` renders the golden JSON config
  template into a concrete job config.

Nothing here imports torch or any heavy ML dependency: the whole package is
importable and unit-testable without a GPU.
"""

from pixlstash.training.backend import (
    TrainerBackend,
    TrainingHandle,
    TrainingProgress,
    TrainingSpec,
)
from pixlstash.training.local_ai_toolkit import LocalAiToolkit

__all__ = [
    "TrainerBackend",
    "TrainingHandle",
    "TrainingProgress",
    "TrainingSpec",
    "LocalAiToolkit",
]
