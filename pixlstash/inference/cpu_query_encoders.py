"""CPU copies of the query encoders, for Metal hosts.

A search encodes its query on whatever thread is handling the request: the text
routes through ``Vault.generate_text_embedding`` inside the database task, and
likeness search calls ``_encode_query_image`` inline in an async handler. The
GPU worker is meanwhile running the embedding and tagging batches. On CUDA that
is fine - two threads may use one context - but torch's Metal backend fills its
kernel-name set without a lock, and every dtype cast routes through that lookup,
so two threads casting at once corrupt it. The process then dies or hangs
instead of raising, which is why no ``except`` around the encode can help.

Measured against ``upstream/develop`` on an M1 Pro, torch 2.13.0: a real server
driven through the HTTP routes at roughly two encodes a second survived 4 runs
of 4, and the same server driven at a hundred a second crashed 1 run in 3 with
``NSInvalidArgumentException: attempt to insert nil object`` raised from inside
a ``matmul``. It is a race, so it is load-dependent rather than certain; see
``docs/apple-metal-thread-safety.md``.

Keeping the query encoders off Metal removes the second thread rather than
trying to synchronise it. The copies are the same classes, model names, weights
and preprocessing as the engine's own services, on the ``cpu`` device, so a
query vector is comparable with the stored ones. They are built only when the
engine's device is Metal: a CUDA or CPU host has nothing to protect against and
pays nothing.

**The weights load on the GPU worker, not at boot.** Every service the engine
owns is lazy, so ``InferenceEngine.create`` loads nothing and returns in
milliseconds; loading these two inline made it take 7.3 s instead, because they
were then the first models in the process and paid the whole cold-import cost -
measured on a real library, boot 1.95 s to 9.11 s. Instead ``Vault.start``
queues a
:class:`~pixlstash.tasks.cpu_query_encoder_load_task.CpuQueryEncoderLoadTask`
once the runner is up. That keeps boot where it was **and** keeps the load off
request threads: loading a model beside the worker's own loads races
transformers' and accelerate's *imports* rather than Metal, and failed with
``ImportError: cannot import name 'AcceleratorState' from partially initialized
module 'accelerate.state'``.

A search that arrives before the load finishes waits for it
(:meth:`CpuQueryEncoders.ensure_serving`) rather than falling back to the Metal
services, because with the worker running that fallback is the crash.

**With no GPU worker at all, the device is safe and the caller uses it.** The
crash needs *two* threads on Metal; a task runner that is not running has no
GPU worker doing Metal work, so there is nothing to collide with.
``ensure_serving`` returns ``False`` there, which is permission rather than
failure. Refusing instead broke search in every configuration that has an
engine but no running worker - the e2e backend, a runner stopped for a library
switch, and the multi-project authz suite, which is what caught it.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Optional

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.accelerator import CPU, MPS, normalise_device

if TYPE_CHECKING:
    from PIL.Image import Image

logger = get_logger(__name__)

#: What a route says when the copies cannot be readied because nothing will run
#: the load. Worded for the owner rather than the developer: a GPU worker that
#: is not running is a restart, not something waiting longer will fix.
NO_GPU_WORKER_DETAIL = (
    "Search cannot run because the background worker is not running. "
    "Restart PixlStash if this persists."
)


class CpuQueryEncodersNotReadyError(RuntimeError):
    """The CPU copies are not loaded, so a search must not encode at all.

    Deliberately not a fallback to the Metal services: that is the
    configuration that takes the process down, so a delayed or refused search
    is the better failure.

    Attributes:
        worker_running: Whether a GPU worker exists to load them. ``False``
            means waiting longer cannot help and the owner should restart.
    """

    def __init__(self, message: str, worker_running: bool) -> None:
        super().__init__(message)
        self.worker_running = worker_running


def build_cpu_query_encoders(device) -> Optional["CpuQueryEncoders"]:
    """Return unloaded CPU copies when *device* needs them, otherwise ``None``.

    Constructs only - no weights are read here, so this costs microseconds and
    ``InferenceEngine.create`` stays as fast as it was. ``Vault.start`` queues
    the load once there is a worker to run it.

    Args:
        device: The engine's resolved inference device.

    Returns:
        A :class:`CpuQueryEncoders`, or ``None`` on a device that does not need
        one.
    """
    if normalise_device(device) != MPS:
        return None
    return CpuQueryEncoders.create()


class CpuQueryEncoders:
    """The engine's query encoders, held a second time on the CPU.

    Args:
        clip_service: A :class:`ClipService` already built on the CPU.
        sbert_service: A :class:`SBertService` already built on the CPU.
    """

    #: How long a search waits for the copies before refusing. Generous because
    #: the wait only happens in the seconds after start-up, and the alternative
    #: to waiting is refusing a search the owner asked for.
    DEFAULT_WAIT_S = 60.0

    def __init__(self, clip_service, sbert_service) -> None:
        self._clip_service = clip_service
        self._sbert_service = sbert_service
        self._loaded = threading.Event()
        self._lock = threading.Lock()
        self._loader = None
        self._pending = None

    @classmethod
    def create(cls) -> "CpuQueryEncoders":
        """Build both CPU copies without loading their weights.

        Returns:
            An instance whose models load on :meth:`load`.
        """
        from pixlstash.tagger_plugins.clip_service import ClipService
        from pixlstash.tagger_plugins.sbert import SBertService

        return cls(ClipService(device=CPU), SBertService(device=CPU))

    @property
    def device(self) -> str:
        """The device these copies run on, which is always the CPU."""
        return CPU

    def bind_loader(self, loader) -> None:
        """Give the copies a way to get themselves loaded.

        Injected by ``Vault.start`` rather than taken in ``__init__`` because
        the engine is built before the task runner exists, and the loader needs
        the runner.

        Args:
            loader: Zero-argument callable that queues a load task and returns
                it. It may raise when the runner is not running.
        """
        with self._lock:
            self._loader = loader

    def is_loaded(self) -> bool:
        """Whether **both** copies hold a model.

        Both or neither: a caller only checks that it has a pair, and the
        services call ``ensure_ready()`` outside their own ``try``
        (``clip_service.encode_text``, ``SBertService.encode``), so serving with
        one model missing would raise out of every search rather than refuse
        once, here, where the caller can answer 503.
        """
        return bool(self._clip_service.is_loaded() and self._sbert_service.is_loaded())

    def load(self) -> None:
        """Load both models. Runs on the GPU worker, never on a request thread.

        A failure in one copy is logged and does not stop the other from
        loading, so the log names which half is missing. Nothing raises: the
        task that calls this reports :meth:`is_loaded`, and a partial load is
        refused by :meth:`ensure_serving` rather than half-served.
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
                    "queries; searches will be refused until this succeeds "
                    "rather than encode on the inference device, which can "
                    "crash the process (docs/apple-metal-thread-safety.md)",
                    label,
                )
        if self.is_loaded():
            self._loaded.set()
            logger.info(
                "Search queries will be encoded on CPU copies of CLIP and "
                "SBERT, so they never touch the inference device while the "
                "GPU worker is using it."
            )

    def unload(self) -> None:
        """Release both copies, so the idle sweep can have their memory back.

        Clearing the loaded flag is the whole of it: without that,
        :meth:`ensure_serving` returns at once for a pair whose models have
        gone, and the encode fails on the search thread instead of queueing a
        reload. ``_pending`` is deliberately left alone - ``_request_load``
        already re-queues once a task has settled, and dropping a task that is
        still running would queue a second load beside it.

        On Apple Silicon this is the same memory pool the accelerator uses, so
        leaving ~630 MB here would quietly defeat a reclaim the owner asked
        for. The cost is that the next search waits for a reload.
        """
        with self._lock:
            self._loaded.clear()
        for label, service in (
            ("CLIP", self._clip_service),
            ("SBERT", self._sbert_service),
        ):
            try:
                service.unload()
            except Exception:
                logger.exception(
                    "Could not unload the CPU %s copy; its memory stays held "
                    "until the process exits",
                    label,
                )

    def start_loading(self) -> bool:
        """Queue a load if one is not already in flight, without waiting.

        Idempotent and never raises, because the Vault calls it from both the
        points that could be the right one and neither always is: at boot
        ``Vault.start`` runs inside ``Server.__init__``, before ``app`` builds
        the engine, while on a library switch ``ensure_ready`` builds the engine
        before the new runner has started. Whichever comes second does the work;
        a search does it if both were too early.

        Returns:
            ``True`` when a load is queued or already done.
        """
        try:
            self._request_load()
            return True
        except CpuQueryEncodersNotReadyError:
            return False

    def ensure_serving(self, timeout_s: Optional[float] = None) -> bool:
        """Whether the copies will serve this query, waiting for a queued load.

        **Returns ``False`` when there is no GPU worker, and that is a
        permission to use the accelerator rather than a failure.** The crash
        these copies exist to prevent needs *two* threads on Metal; with no task
        runner there is no GPU worker doing Metal work, so nothing is there to
        collide with and the engine's own services are safe. Refusing instead
        would break search in every configuration that has an engine but no
        running worker - the e2e backend, a runner stopped for a library
        switch, and the authz suites, one of which caught exactly that.

        A worker that *is* running is the opposite case: the copies are the only
        safe encoder, so a caller waits for them rather than falling back.

        Args:
            timeout_s: How long to wait; :data:`DEFAULT_WAIT_S` when ``None``.

        Returns:
            ``True`` when the copies are loaded and should be used. ``False``
            when no worker exists to load them, meaning the caller may encode
            on the inference device.

        Raises:
            CpuQueryEncodersNotReadyError: A worker is running but the load did
                not finish within *timeout_s*. Falling back here is the crash.
        """
        if self._loaded.is_set():
            return True
        try:
            self._request_load()
        except CpuQueryEncodersNotReadyError:
            # ``_request_load`` raises for one reason only - no worker will run
            # the load - so there is nothing to distinguish here. A worker that
            # is running but slow surfaces as the wait timeout below instead.
            logger.debug(
                "No GPU worker is running, so nothing else is using the "
                "inference device; encoding this query on it directly."
            )
            return False
        wait = self.DEFAULT_WAIT_S if timeout_s is None else timeout_s
        if not self._loaded.wait(timeout=wait):
            raise CpuQueryEncodersNotReadyError(
                "Search is still loading its models; try the search again shortly.",
                worker_running=True,
            )
        return True

    def _request_load(self) -> None:
        """Queue a load unless one is in flight or has already succeeded.

        A load that failed or was cancelled - a full restore cancels pending
        tasks - leaves the pair unloaded, so the next search queues another
        rather than waiting on a task that will never finish.
        """
        # Imported here to break a circular dependency: ``inference.engine``
        # imports this module, and ``pixlstash.tasks`` imports the engine back
        # through ``face_extraction_task``.
        from pixlstash.tasks.base_task import TaskStatus  # noqa: PLC0415

        with self._lock:
            if self._loaded.is_set():
                return
            if self._loader is None:
                raise CpuQueryEncodersNotReadyError(
                    NO_GPU_WORKER_DETAIL, worker_running=False
                )
            if self._pending is not None and self._pending.status not in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                return
            try:
                self._pending = self._loader()
            except Exception as exc:
                self._pending = None
                raise CpuQueryEncodersNotReadyError(
                    NO_GPU_WORKER_DETAIL, worker_running=False
                ) from exc

    def encode_query(self, query: str) -> list:
        """Encode *query* into an SBERT embedding on the CPU.

        Args:
            query: Search query. Lower-cased before encoding, as
                ``TextEmbeddingWorkflow.encode_query`` does.

        Returns:
            A one-element list holding the embedding, or an empty list when
            *query* is blank.

        The caller must have had :meth:`ensure_serving` return ``True``
        first; this does not check.
        """
        if not query:
            return []
        return self._sbert_service.encode([query.lower()])

    def encode_clip_query(self, query: str) -> Optional[np.ndarray]:
        """Encode *query* into a normalised CLIP text embedding on the CPU.

        Args:
            query: Search query.

        Returns:
            A 1-D array, or ``None`` on failure.

        The caller must have had :meth:`ensure_serving` return ``True``
        first; this does not check.
        """
        return self._clip_service.encode_text(query)

    def encode_query_image(self, image: "Image") -> Optional[np.ndarray]:
        """Encode an uploaded likeness-search image on the CPU.

        The picture embeddings this is compared against are written by the GPU
        worker; only the *query* image comes through here, so the cost is one
        image per search rather than per library picture.

        Args:
            image: The uploaded query image.

        Returns:
            A float32 array of shape ``(1, D)``, or ``None`` on failure.

        The caller must have had :meth:`ensure_serving` return ``True``
        first; this does not check.
        """
        return self._clip_service.encode_image_batch([image])
