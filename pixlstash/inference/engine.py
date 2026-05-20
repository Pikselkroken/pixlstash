"""InferenceEngine: DI root holding the service registry, VRAM budget, and lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pixlstash.inference.vram_budget import VramBudget
from pixlstash.inference.model_lifecycle import ModelLifecycleManager
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.tagger_plugins.clip_service import ClipService
    from pixlstash.tagger_plugins.sbert import SBertService
    from pixlstash.tagger_plugins.pixlstash_tagger import PixlStashTaggerService
    from pixlstash.tagger_plugins.wd14 import WD14Service
    from pixlstash.tagger_plugins.florence2 import Florence2Service

logger = get_logger(__name__)


class InferenceEngine:
    """Dependency-injection root for one worker-process inference context.

    Holds the five service instances, the :class:`VramBudget`, and the
    :class:`ModelLifecycleManager`.  Has no inference logic of its own —
    it is a plain data holder that workflow objects receive via constructor
    injection.

    Args:
        device: Inference device string (``"cuda"`` or ``"cpu"``).
        clip_service: :class:`ClipService` instance (lazy-loaded on first use).
        sbert_service: :class:`SBertService` instance.
        wd14_service: :class:`WD14Service` instance.
        custom_service: :class:`PixlStashTaggerService` instance.
        florence_service: :class:`Florence2Service` instance.
        vram_budget: Pre-configured :class:`VramBudget` for this engine.
        lifecycle: :class:`ModelLifecycleManager` for this engine.
    """

    def __init__(
        self,
        device: str,
        clip_service: "ClipService",
        sbert_service: "SBertService",
        wd14_service: "WD14Service",
        custom_service: "PixlStashTaggerService",
        florence_service: "Florence2Service",
        vram_budget: VramBudget,
        lifecycle: ModelLifecycleManager,
        force_cpu: bool = False,
    ) -> None:
        self.device = device
        self.clip_service = clip_service
        self.sbert_service = sbert_service
        self.wd14_service = wd14_service
        self.custom_service = custom_service
        self.florence_service = florence_service
        self.vram_budget = vram_budget
        self.lifecycle = lifecycle
        self.force_cpu = force_cpu
