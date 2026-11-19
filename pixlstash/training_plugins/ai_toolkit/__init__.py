"""The ai-toolkit training plugin, shipped with PixlStash and always registered.

- :mod:`.adapter`: :class:`AiToolkitPlugin`, the trainer itself and the only
  module that knows how ai-toolkit is launched, cancelled and read
  (``pixlstash/data/ai-toolkit-jobs/SEAM.md``).
- :mod:`.install`: finding and checking a local checkout.
- :mod:`.live`: reading the trainer's job row, loss log and info line for the
  live view.
- ``trainer_hook/sitecustomize.py``: loaded into the trainer to record its
  allocator peaks.
"""

from pixlstash.training_plugins.ai_toolkit.adapter import AiToolkitPlugin

__all__ = ["AiToolkitPlugin"]
