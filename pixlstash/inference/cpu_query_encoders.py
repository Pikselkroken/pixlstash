"""CPU copies of the query encoders, for Metal hosts.

A search encodes its query on a request thread: text search and export by
query call ``Vault.generate_text_embedding`` before their database task, and
likeness search's handler runs on a threadpool worker. The GPU worker is
meanwhile running the embedding and tagging batches. On CUDA that is fine - two
threads may use one context - but torch's Metal backend is not safe on two
threads at once: the process dies or hangs instead of raising, which is why no
``except`` around the encode can help. ``docs/apple-metal-thread-safety.md``
has the causes.

Measured on ``develop`` on an M1 Pro, torch 2.13.0: a real server driven
through the HTTP routes at roughly two encodes a second survived 4 runs of 4,
while six threads calling the encoders directly at about a hundred a second,
with the worker embedding an upload, crashed 1 run in 3 with
``NSInvalidArgumentException: attempt to insert nil object`` raised from inside
a ``matmul``. It is a race, so it is load-dependent rather than certain.

Keeping the query encoders off Metal removes the second thread rather than
trying to synchronise it. The copies are the same classes, model names, weights
and preprocessing as the engine's own services, on the ``cpu`` device, so a
query vector is comparable with the stored ones. They are built only when the
engine's device is Metal: a CUDA or CPU host has nothing to protect against and
pays nothing.

**Loaded once, before the engine is published, and then kept.** The Vault
builds the engine, loads these, and only then assigns ``Vault._engine``
(``Vault._create_engine``). The planner's model finders queue nothing until the
engine exists, so the load never runs beside another model load - which would
race transformers' and accelerate's *imports* rather than Metal, and failed
with ``ImportError: cannot import name 'AcceleratorState' from partially
initialized module 'accelerate.state'``. The copies then stay resident for the
life of the engine, about 0.7 GB of unified memory, even with "Keep models in
memory" off; releasing them under the idle sweep is #1774.

Every query encode, on any host, goes through
:meth:`~pixlstash.inference.engine.InferenceEngine.query_services`, which picks
these copies or the engine's own services and lets one encode run at a time.
"""

from __future__ import annotations

from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.accelerator import CPU, MPS, normalise_device

logger = get_logger(__name__)

#: What a route says when the copies failed to load. Worded for the owner: the
#: load runs once, at start-up, so trying the search again will not help.
LOAD_FAILED_DETAIL = (
    "Search could not load its models. Restart PixlStash; the log says why."
)


class CpuQueryEncodersNotReadyError(RuntimeError):
    """The CPU copies did not load, so a search must not encode at all.

    Deliberately not a fallback to the Metal services: that is the
    configuration that takes the process down, so a refused search is the
    better failure.
    """


def build_cpu_query_encoders(device) -> Optional["CpuQueryEncoders"]:
    """Return unloaded CPU copies when *device* needs them, otherwise ``None``.

    Constructs only - no weights are read here, so ``InferenceEngine.create``
    stays as fast as it was. ``Vault._create_engine`` loads them.

    Args:
        device: The engine's resolved inference device.

    Returns:
        A :class:`CpuQueryEncoders`, or ``None`` on a device that does not need
        one.
    """
    if normalise_device(device) != MPS:
        return None
    from pixlstash.tagger_plugins.clip_service import ClipService
    from pixlstash.tagger_plugins.sbert import SBertService

    return CpuQueryEncoders(ClipService(device=CPU), SBertService(device=CPU))


class CpuQueryEncoders:
    """The engine's query encoders, held a second time on the CPU.

    Args:
        clip_service: A :class:`ClipService` already built on the CPU.
        sbert_service: A :class:`SBertService` already built on the CPU.
    """

    def __init__(self, clip_service, sbert_service) -> None:
        self._clip_service = clip_service
        self._sbert_service = sbert_service

    def is_loaded(self) -> bool:
        """Whether **both** copies hold a model.

        Both or neither: the services call ``ensure_ready()`` outside their own
        ``try`` (``clip_service.encode_text``, ``SBertService.encode``), and on
        a copy with no model that is a lazy load on the search thread - the
        import race the up-front load exists to avoid.
        """
        return bool(self._clip_service.is_loaded() and self._sbert_service.is_loaded())

    def load(self) -> None:
        """Load both models, before anything else in the process loads one.

        A failure in one copy is logged and does not stop the other from
        loading, so the log names which half is missing. Nothing raises: a
        partial load makes :meth:`services` refuse every search instead.
        """
        for label, service in (
            ("CLIP", self._clip_service),
            ("SBERT", self._sbert_service),
        ):
            try:
                service.ensure_ready()
            except Exception:
                logger.exception(
                    "Could not load the CPU %s copy used to encode search "
                    "queries; searches will be refused rather than encode on "
                    "the inference device, which can crash the process "
                    "(docs/apple-metal-thread-safety.md)",
                    label,
                )
        if self.is_loaded():
            logger.info(
                "Search queries will be encoded on CPU copies of CLIP and "
                "SBERT, so they never touch the inference device while the "
                "GPU worker is using it."
            )

    def services(self) -> tuple:
        """Return ``(clip_service, sbert_service)`` for one query encode.

        Raises:
            CpuQueryEncodersNotReadyError: A copy failed to load.
        """
        if not self.is_loaded():
            raise CpuQueryEncodersNotReadyError(LOAD_FAILED_DETAIL)
        return self._clip_service, self._sbert_service
