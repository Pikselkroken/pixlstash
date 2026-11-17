"""LoRA/fine-tune training wrapper for PixlStash.

This package holds what every trainer shares. The trainers themselves are
training plugins (:mod:`pixlstash.training_plugins`); ai-toolkit is the one
PixlStash ships. The public surface is intentionally small:

- :mod:`pixlstash.training.backend` defines the trainer-agnostic interface
  (:class:`TrainerBackend`) and the plain dataclasses passed across it
  (:class:`TrainingSpec`, :class:`TrainingHandle`, :class:`TrainingProgress`).
- :mod:`pixlstash.training.registry` names the installed training plugins.
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

__all__ = [
    "TrainerBackend",
    "TrainingHandle",
    "TrainingProgress",
    "TrainingSpec",
]
