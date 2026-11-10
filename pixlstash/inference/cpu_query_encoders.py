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

**Loaded first, before the engine is published.** The Vault
builds the engine, loads these, and only then assigns ``Vault._engine``
(``Vault._create_engine``). The planner's model finders queue nothing until the
engine exists, so the load never runs beside another model load - which would
race transformers' and accelerate's *imports* rather than Metal, and failed
with ``ImportError: cannot import name 'AcceleratorState' from partially
initialized module 'accelerate.state'``.

**Released when unused, reloaded by the next search.** The copies hold about
0.7 GB of unified memory, so the idle sweep releases them like any other model
(``InferenceEngine.close``), but only once no search has used them for
:data:`IDLE_UNLOAD_S`, and never under a running encode. The next search
reloads them on its own thread, inside ``query_services``: it waits for that
load (and for any model the worker is building at that moment), never for a
GPU batch. That is safe off the GPU worker because the first load
has already done the imports that race, the construction itself is serialised
by ``MODEL_CONSTRUCTION_LOCK``, and a CPU load never touches Metal. A pair that
never loaded at all is not retried: its imports may be what failed.

Every query encode, on any host, goes through
:meth:`~pixlstash.inference.engine.InferenceEngine.query_services`, which picks
these copies or the engine's own services and lets one encode run at a time.
"""

from __future__ import annotations

import time
from typing import Optional

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.accelerator import CPU, MPS, normalise_device

logger = get_logger(__name__)

#: What a route says when the copies failed their first load. Worded for the
#: owner: that load runs at start-up and is not retried, so trying the search
#: again will not help.
LOAD_FAILED_DETAIL = (
    "Search could not load its models. Restart PixlStash; the log says why."
)


#: What a route says when a reload after an idle release failed. The next
#: search tries the load again.
RELOAD_FAILED_DETAIL = (
    "Search could not reload its models. Try again; the log says why."
)

#: How long the copies go unused before the idle sweep may release them, so a
#: search session does not pay a reload every few queries. The earliest they go,
#: not a deadline: the sweep runs only from the worker-progress poll, at most
#: every 180 s, and only while every worker is idle.
IDLE_UNLOAD_S = 180.0


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
        # Set by the first complete load; only a pair that loaded once reloads.
        self._loaded_once = False
        self._last_used = 0.0

    def is_loaded(self) -> bool:
        """Whether **both** copies hold a model.

        Both or neither: the services call ``ensure_ready()`` outside their own
        ``try`` (``clip_service.encode_text``, ``SBertService.encode``), and on
        a copy with no model that is a lazy load inside the encode, outside
        :meth:`services`' reload and its checks.
        """
        return bool(self._clip_service.is_loaded() and self._sbert_service.is_loaded())

    def load(self) -> None:
        """Load both models, before anything else in the process loads one.

        A failure in one copy is logged and does not stop the other from
        loading, so the log names which half is missing. Nothing raises: a
        partial first load makes :meth:`services` refuse every search instead,
        and a partial reload refuses that search and is retried by the next.
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
        if self.is_loaded() and not self._loaded_once:
            self._loaded_once = True
            logger.info(
                "Search queries will be encoded on CPU copies of CLIP and "
                "SBERT, so they never touch the inference device while the "
                "GPU worker is using it."
            )

    def unload_if_idle(self) -> bool:
        """Release both copies if no search has used them for :data:`IDLE_UNLOAD_S`.

        The caller holds the engine's query lock, so no encode is running.

        Returns:
            Whether the copies were released.
        """
        # Either half: a partial reload leaves one copy that serves nobody.
        if not (self._clip_service.is_loaded() or self._sbert_service.is_loaded()):
            return False
        if time.monotonic() - self._last_used < IDLE_UNLOAD_S:
            return False
        self._clip_service.unload()
        self._sbert_service.unload()
        logger.info("Released the CPU query encoders; the next search reloads them.")
        return True

    def services(self) -> tuple:
        """Return ``(clip_service, sbert_service)`` for one query encode.

        Reloads the copies first if the idle sweep released them. The caller
        holds the engine's query lock, so later searches wait for this load.

        Raises:
            CpuQueryEncodersNotReadyError: A copy failed to load.
        """
        if not self.is_loaded():
            if not self._loaded_once:
                raise CpuQueryEncodersNotReadyError(LOAD_FAILED_DETAIL)
            logger.info("Reloading the CPU query encoders for a search.")
            self.load()
            if not self.is_loaded():
                raise CpuQueryEncodersNotReadyError(RELOAD_FAILED_DETAIL)
        self._last_used = time.monotonic()
        return self._clip_service, self._sbert_service
