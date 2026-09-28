"""CPU copies of the query encoders, for Metal hosts.

A search encodes its query on a request thread: text search and export by
query call ``Vault.generate_text_embedding`` before their database task, and
likeness search runs ``_encode_query_image`` on a threadpool worker. The GPU
worker is meanwhile running the embedding and tagging batches. On CUDA that
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
measured on a real library, boot 1.95 s to 9.11 s. Instead the Vault queues a
:class:`~pixlstash.tasks.cpu_query_encoder_load_task.CpuQueryEncoderLoadTask`,
normally from ``Vault.ensure_ready`` straight after building the engine. That
keeps boot where it was **and** keeps the load off request threads: loading a
model beside the worker's own loads races transformers' and accelerate's
*imports* rather than Metal, and failed with ``ImportError: cannot import name
'AcceleratorState' from partially initialized module 'accelerate.state'``.

A search that arrives before the load finishes waits for it
(:meth:`CpuQueryEncoders.ensure_serving`) rather than falling back to the Metal
services, because with the worker running that fallback is the crash.

**When the runner refuses the load, the caller uses the device.** The crash
needs *two* threads on Metal, and a runner that refuses tasks has no GPU worker
doing Metal work to collide with. ``ensure_serving`` returns ``False`` there,
which is permission rather than failure. Refusing instead broke search in
configurations with an engine but no running worker, which the multi-project
authz suite caught. The reasoning has two gaps: a stopping runner refuses tasks
while its worker finishes its last batch, and two searches in that state are two
threads on Metal (``docs/apple-metal-thread-safety.md``, "Not covered").
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Optional

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

#: What a route says when the copies are loading, or were unloaded a moment
#: ago: the next search queues the reload, so trying again is the answer.
STILL_LOADING_DETAIL = (
    "Search is still loading its models; try the search again shortly."
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
    ``InferenceEngine.create`` stays as fast as it was. The Vault queues the
    load, normally from ``Vault.ensure_ready``.

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
    #: the alternative to waiting is refusing a search the owner asked for. The
    #: wait covers the load after start-up, and the reload after an idle sweep
    #: or a library switch.
    DEFAULT_WAIT_S = 60.0

    #: How long :meth:`unload` waits for encodes already running. An encode
    #: takes well under a second; the bound is for one that hangs, which would
    #: otherwise hold the idle sweep's thread - and, from the settings route,
    #: the event loop - for as long as it hangs.
    UNLOAD_DRAIN_S = 10.0

    def __init__(self, clip_service, sbert_service) -> None:
        self._clip_service = clip_service
        self._sbert_service = sbert_service
        self._loaded = threading.Event()
        self._lock = threading.Lock()
        # Signalled when the last running encode finishes and when an unload
        # ends, for whichever of ``unload`` and ``load`` is waiting on it.
        self._idle = threading.Condition(self._lock)
        self._encodes_running = 0
        # Unloads in progress. While any is, encodes refuse and a load may not
        # flag the pair, so searches arriving mid-drain cannot hold it open.
        self._unloading = 0
        # Bumped each time an unload releases the models, so a load that one
        # overtook can tell its work was undone.
        self._unload_generation = 0
        # Serialises encodes that fall back to the inference device.
        self._fallback_lock = threading.Lock()
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

        Injected by the Vault rather than taken in ``__init__``:
        ``InferenceEngine.create`` builds the copies without any reference to
        the Vault's task runner, and the loader needs that runner.

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

        An unload in progress is waited out before the pair is flagged. If that
        unload released what this load had just loaded, the load runs once
        more: the searches waiting on it would otherwise sit out their whole
        timeout for a flag nothing was going to set.
        """
        loaded = False
        for _attempt in range(2):
            with self._lock:
                generation = self._unload_generation
            self._load_services()
            # Checked and flagged under the lock ``unload`` holds, and never
            # while one is counted, so an unload cannot land between the two
            # and leave the flag over released models.
            with self._lock:
                while self._unloading:
                    self._idle.wait()
                loaded = self.is_loaded()
                if loaded:
                    self._loaded.set()
                    break
                if self._unload_generation == generation:
                    # Nothing released the models: a load failure, already
                    # logged, which another attempt would only repeat.
                    break
        if loaded:
            logger.info(
                "Search queries will be encoded on CPU copies of CLIP and "
                "SBERT, so they never touch the inference device while the "
                "GPU worker is using it."
            )

    def unload(self) -> bool:
        """Release both copies, so the idle sweep can have their memory back.

        Clearing the loaded flag is what makes the next search reload: without
        it, :meth:`ensure_serving` returns at once for a pair whose models have
        gone, and the encode fails on the search thread instead of queueing a
        reload. ``_pending`` is deliberately left alone - ``_request_load``
        already re-queues once a task has settled, and dropping a task that is
        still running would queue a second load beside it.

        On Apple Silicon this is the same memory pool the accelerator uses, so
        leaving ~0.7 GB here would quietly defeat a reclaim the owner asked
        for. The cost is that the next search waits for a reload.

        **Waits for encodes already running**, because the idle sweep that
        calls this cannot see a search: releasing a model under one makes the
        service reload it lazily on the search thread, which is the import race
        the load task exists to avoid. While it waits, new encodes refuse and a
        load may not flag the pair, so searches that keep arriving cannot hold
        it open; they wait for the reload instead. The wait is bounded by
        :data:`UNLOAD_DRAIN_S`: an encode still running then is left alone, the
        models are kept, and the next sweep tries again.

        Returns:
            ``True`` when the models were released, ``False`` when a running
            encode outlasted the wait and they were kept.
        """
        with self._lock:
            self._unloading += 1
            try:
                self._loaded.clear()
                deadline = time.monotonic() + self.UNLOAD_DRAIN_S
                while self._encodes_running:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        logger.warning(
                            "Kept the CPU query encoders loaded: %d search "
                            "encode(s) were still running after %.0f s. The "
                            "next idle sweep will try again.",
                            self._encodes_running,
                            self.UNLOAD_DRAIN_S,
                        )
                        if self.is_loaded():
                            self._loaded.set()
                        return False
                    self._idle.wait(timeout=remaining)
                # Another unload that gave up during the wait may have set it
                # again; the models are going, so the flag goes with them.
                self._loaded.clear()
                # Still under the lock and still counted, so no load can flag
                # the pair between the flag clearing and the models going.
                self._release_services()
                self._unload_generation += 1
                return True
            finally:
                self._unloading -= 1
                self._idle.notify_all()

    def start_loading(self) -> bool:
        """Queue a load if one is not already in flight, without waiting.

        Idempotent and never raises, because the Vault calls it from
        ``ensure_ready``, ``start`` and the lazy engine build in
        ``get_worker_future``. The call in ``ensure_ready`` is the one that
        queues the load, since a runner accepts tasks before it starts; the
        others are safety nets. A load none of them could queue is left to the
        first search.

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

        **Returns ``False`` when the runner refuses the load, and that is a
        permission to use the accelerator rather than a failure.** The crash
        these copies exist to prevent needs *two* threads on Metal, and a runner
        that refuses tasks has no GPU worker doing Metal work to collide with.
        Refusing instead would break search in configurations with an engine
        but no running worker, which the authz suites caught. Two gaps: a
        stopping runner refuses tasks while its worker finishes its last batch,
        and two searches in that state are two threads on Metal.

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
                STILL_LOADING_DETAIL, worker_running=True
            )
        return True

    @contextmanager
    def device_fallback(self) -> Iterator[None]:
        """Hold the turn for one encode that falls back to the inference device.

        Taken by a caller whose :meth:`ensure_serving` returned ``False``. With
        no worker, nothing in the background is using the device, but two
        searches falling back at once would still be two threads on it, so they
        take turns.
        """
        with self._fallback_lock:
            yield

    def encode_query(self, query: str) -> list:
        """Encode *query* into an SBERT embedding on the CPU.

        The caller must have had :meth:`ensure_serving` return ``True`` first.

        Args:
            query: Search query. Lower-cased before encoding, as
                ``TextEmbeddingWorkflow.encode_query`` does.

        Returns:
            A one-element list holding the embedding, or an empty list when
            *query* is blank.

        Raises:
            CpuQueryEncodersNotReadyError: The pair was unloaded after that
                check; see :meth:`_encoding`.
        """
        if not query:
            return []
        with self._encoding():
            return self._sbert_service.encode([query.lower()])

    def encode_clip_query(self, query: str) -> Optional[np.ndarray]:
        """Encode *query* into a normalised CLIP text embedding on the CPU.

        The caller must have had :meth:`ensure_serving` return ``True`` first.

        Args:
            query: Search query.

        Returns:
            A 1-D array, or ``None`` on failure.

        Raises:
            CpuQueryEncodersNotReadyError: The pair was unloaded after that
                check; see :meth:`_encoding`.
        """
        with self._encoding():
            return self._clip_service.encode_text(query)

    def encode_query_image(self, image: "Image") -> Optional[np.ndarray]:
        """Encode an uploaded likeness-search image on the CPU.

        The picture embeddings this is compared against are written by the GPU
        worker; only the *query* image comes through here, so the cost is one
        image per search rather than per library picture. The caller must have
        had :meth:`ensure_serving` return ``True`` first.

        Args:
            image: The uploaded query image.

        Returns:
            A float32 array of shape ``(1, D)``, or ``None`` on failure.

        Raises:
            CpuQueryEncodersNotReadyError: The pair was unloaded after that
                check; see :meth:`_encoding`.
        """
        with self._encoding():
            return self._clip_service.encode_image_batch([image])

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

    @contextmanager
    def _encoding(self) -> Iterator[None]:
        """Hold the models in place for one encode, so ``unload`` waits for it.

        Refuses when the pair is not flagged as loaded rather than encoding
        anyway: that happens when the idle sweep unloads between a search's
        :meth:`ensure_serving` and its encode, and a service with no model
        reloads it lazily on the search thread - the import race the load task
        exists to avoid. The next search queues the reload instead.

        Raises:
            CpuQueryEncodersNotReadyError: The pair is not loaded.
        """
        with self._lock:
            if self._unloading or not self._loaded.is_set():
                raise CpuQueryEncodersNotReadyError(
                    STILL_LOADING_DETAIL, worker_running=True
                )
            self._encodes_running += 1
        try:
            yield
        finally:
            with self._lock:
                self._encodes_running -= 1
                if not self._encodes_running:
                    self._idle.notify_all()

    def _load_services(self) -> None:
        """Run each copy's ``ensure_ready``, logging a failure per copy."""
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

    def _release_services(self) -> None:
        """Unload each copy, logging a failure per copy. Caller holds the lock."""
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
