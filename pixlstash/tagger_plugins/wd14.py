#################################################################
# Adapted from Kohya_ss https://github.com/kohya-ss/sd-scripts/ #
# Under the Apache 2.0 License                                  #
# https://github.com/kohya-ss/sd-scripts/blob/main/LICENSE.md   #
#################################################################
"""WD14 ONNX tagger plugin (SmilingWolf/wd-convnext-tagger-v3)."""

import csv
import os
import platform

import numpy as np
import onnxruntime as ort
import torch
from tqdm import tqdm

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.caption_utils import naturalize_tags, sanitise_tag

logger = get_logger(__name__)

WD14_HF_REPO = "SmilingWolf/wd-convnext-tagger-v3"
WD14_CSV_FILE = "selected_tags.csv"
WD14_GENERAL_THRESHOLD = 0.85
WD14_UNDESIRED_TAGS = "solo, general, male_focus, meme, sensitive"
WD14_CAPTION_SEPARATOR = ", "
WD14_DATALOADER_TIMEOUT = 30


class WD14Service:
    """WD14 ONNX tagger (SmilingWolf/wd-convnext-tagger-v3).

    Manages ONNX session lifecycle, tag CSV parsing, model downloading,
    and batched image inference.  Designed as a stateful service object
    owned by ``PictureTagger``.

    Args:
        device: Inference device string (``"cuda"`` or ``"cpu"``).
        model_dir: Base directory under which a ``WD14_HF_REPO``-named
            subdirectory is created to hold ``model.onnx`` and
            ``selected_tags.csv``.
        batch_size_fn: Zero-argument callable returning the effective batch
            size to use for inference (should include any VRAM caps).
        silent: When ``True`` suppress tqdm progress bars.
    """

    def __init__(
        self,
        device: str,
        model_dir: str,
        batch_size_fn,
        silent: bool = True,
    ):
        self._device = device
        self._model_location = os.path.join(model_dir, WD14_HF_REPO.replace("/", "_"))
        self._batch_size_fn = batch_size_fn
        self._silent = silent
        self._threshold = WD14_GENERAL_THRESHOLD

        self._ort_sess = None
        self._input_name: str | None = None
        self._onnx_batch_capacity: int = 1
        self._rating_tags: list | None = None
        self._general_tags: list | None = None

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def is_loaded(self) -> bool:
        """Return ``True`` if the ONNX session and tag list are ready."""
        return self._ort_sess is not None and self._general_tags is not None

    def needs_download(self) -> bool:
        """Return ``True`` if the ONNX model or tag CSV are missing."""
        onnx_path = os.path.join(self._model_location, "model.onnx")
        csv_path = os.path.join(self._model_location, WD14_CSV_FILE)
        return not (os.path.exists(onnx_path) and os.path.exists(csv_path))

    def download(self, force_download: bool = False) -> None:
        """Download the ONNX model and tag CSV from HuggingFace."""
        from huggingface_hub import hf_hub_download

        os.makedirs(self._model_location, exist_ok=True)
        onnx_path = os.path.join(self._model_location, "model.onnx")
        csv_path = os.path.join(self._model_location, WD14_CSV_FILE)
        logger.debug("Downloading WD14 model from HuggingFace: %s", WD14_HF_REPO)
        logger.debug("Downloading ONNX model to %s", onnx_path)
        hf_hub_download(
            repo_id=WD14_HF_REPO,
            filename="model.onnx",
            local_dir=self._model_location,
            force_download=force_download,
        )
        logger.debug("Downloading %s to %s", WD14_CSV_FILE, csv_path)
        hf_hub_download(
            repo_id=WD14_HF_REPO,
            filename=WD14_CSV_FILE,
            local_dir=self._model_location,
            force_download=force_download,
        )

    def init(self) -> None:
        """Load the ONNX session and tag list (idempotent)."""
        if self.is_loaded():
            return
        if self._device == "cuda":
            providers = ort.get_available_providers()
            if "CUDAExecutionProvider" not in providers:
                logger.warning(
                    "CUDAExecutionProvider unavailable for onnxruntime "
                    "(WD14 tagger will use CPU; all PyTorch models still use CUDA). "
                    "Fix with: pip uninstall -y onnxruntime && pip install onnxruntime-gpu"
                )
        self._init_onnx_session()
        if self._rating_tags is None or self._general_tags is None:
            self._load_tags()

    def unload(self) -> None:
        """Release the ONNX session and tag list."""
        if self._ort_sess is not None:
            del self._ort_sess
            self._ort_sess = None
            logger.debug("WD14Service: ONNX session unloaded.")
        self._input_name = None
        self._onnx_batch_capacity = 1
        self._rating_tags = None
        self._general_tags = None

    def batch_capacity(self) -> int:
        """Return the ONNX model's batch dimension (1 when not yet loaded)."""
        return self._onnx_batch_capacity

    def set_threshold(self, threshold: float) -> None:
        """Update the general tag confidence threshold."""
        self._threshold = float(threshold)

    def tag_images(
        self,
        image_paths,
        stop_event=None,
        preloaded_map: dict | None = None,
    ) -> dict:
        """Run WD14 inference and return ``{path: [tag, …]}``.

        Args:
            image_paths: Ordered list of image file paths to tag.
            stop_event: Optional :class:`threading.Event`; inference stops
                when it is set.
            preloaded_map: Optional ``{str_path: preprocessed_array}``
                mapping for images already pre-processed by
                :meth:`ImageLoadingDatasetPrepper._preprocess_image`.
                These bypass the DataLoader.

        Returns:
            Dict mapping each path to its list of tags.
        """
        preloaded_map = preloaded_map or {}
        undesired_tags = {
            t.strip()
            for t in WD14_UNDESIRED_TAGS.split(WD14_CAPTION_SEPARATOR.strip())
            if t.strip()
        }
        logger.debug("WD14: removing tags: %s", ", ".join(sorted(undesired_tags)))

        remaining_paths = [p for p in image_paths if str(p) not in preloaded_map]
        inference_batch_size = max(1, int(self._batch_size_fn()))

        logger.debug(
            "[TAG_PRELOAD] total=%s preloaded_hits=%s dataloader_misses=%s",
            len(image_paths),
            len(image_paths) - len(remaining_paths),
            len(remaining_paths),
        )
        logger.debug(
            "[TAG_BATCH] inference_batch_size=%s onnx_batch_capacity=%s",
            inference_batch_size,
            self._onnx_batch_capacity,
        )

        if platform.system() == "Darwin":
            worker_count = 0
        else:
            worker_count = min(
                inference_batch_size,
                os.cpu_count() // 2 or 1,
                max(1, len(remaining_paths)),
            )

        all_results: dict = {}

        # Run inference on pre-loaded (already pre-processed) images.
        self._run_preloaded(
            image_paths,
            preloaded_map,
            inference_batch_size,
            undesired_tags,
            all_results,
        )

        # Run DataLoader-based inference on remaining paths.
        b_imgs, failed = self._run_dataloader(
            remaining_paths,
            stop_event,
            inference_batch_size,
            worker_count,
            undesired_tags,
            all_results,
        )
        if failed:
            logger.warning(
                "Tagging failed due to dataloader issues; no tags will be returned."
            )
            return {}

        # Flush any remaining images in the accumulation buffer.
        if b_imgs and not (stop_event is not None and stop_event.is_set()):
            b_imgs = [(str(p), img) for p, img in b_imgs]
            batch_result = self._run_batch(b_imgs, undesired_tags)
            if batch_result is None:
                logger.warning("Tagging failed for batch: %s", [p for p, _ in b_imgs])
            else:
                for k, tags in batch_result.items():
                    tags = [sanitise_tag(t) for t in tags]
                    tags = [t for t in tags if t]
                    batch_result[k] = tags
                all_results.update(batch_result)

        logger.debug("Completed WD14 tagging for %s images.", len(all_results))
        return all_results

    # ------------------------------------------------------------------ #
    # Private helpers                                                      #
    # ------------------------------------------------------------------ #

    def _init_onnx_session(self) -> None:
        onnx_path = os.path.join(self._model_location, "model.onnx")
        logger.debug("Running WD14 tagger with ONNX")
        logger.debug("Loading ONNX model: %s", onnx_path)
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(
                f"ONNX model not found: {onnx_path}. "
                "Re-download with force_download=True."
            )
        if self._device == "cpu":
            logger.debug("Initialising WD14 tagger with CPUExecutionProvider")
            self._ort_sess = ort.InferenceSession(
                onnx_path, providers=["CPUExecutionProvider"]
            )
        else:
            logger.debug("Initialising WD14 tagger with device: %s", self._device)
            if "OpenVINOExecutionProvider" in ort.get_available_providers():
                self._ort_sess = ort.InferenceSession(
                    onnx_path,
                    providers=["OpenVINOExecutionProvider"],
                    provider_options=[{"device_type": "GPU", "precision": "FP32"}],
                )
            else:
                self._ort_sess = ort.InferenceSession(
                    onnx_path,
                    providers=(
                        [
                            (
                                "CUDAExecutionProvider",
                                {
                                    # Use same-as-requested arena growth so ORT does
                                    # not round up allocations to the next power of two.
                                    # This prevents the default "double on each growth"
                                    # behaviour without imposing a hard memory cap that
                                    # would OOM inference for larger models like WD14.
                                    "arena_extend_strategy": "kSameAsRequested",
                                },
                            )
                        ]
                        if "CUDAExecutionProvider" in ort.get_available_providers()
                        else [("ROCMExecutionProvider", {})]
                        if "ROCMExecutionProvider" in ort.get_available_providers()
                        else ["CPUExecutionProvider"]
                    ),
                )
        self._input_name = self._ort_sess.get_inputs()[0].name
        self._onnx_batch_capacity = self._resolve_batch_capacity()

    def _resolve_batch_capacity(self) -> int:
        if self._ort_sess is None:
            return 1
        try:
            input_meta = self._ort_sess.get_inputs()[0]
            input_shape = getattr(input_meta, "shape", None)
            if not input_shape:
                return 1
            batch_dim = input_shape[0]
            if isinstance(batch_dim, int):
                return max(1, int(batch_dim))
            if batch_dim is None or isinstance(batch_dim, str):
                # Dynamic batch dimension: return a large upper bound.
                # The effective inference batch size is always constrained
                # by batch_size_fn() which applies VRAM and concurrency caps.
                return 512
        except Exception as exc:
            logger.warning("Could not resolve ONNX batch capacity: %s", exc)
        return 1

    def _load_tags(self) -> None:
        csv_path = os.path.join(self._model_location, WD14_CSV_FILE)
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            lines = list(reader)
        header, rows = lines[0], lines[1:]
        assert (
            header[0] == "tag_id" and header[1] == "name" and header[2] == "category"
        ), f"Unexpected CSV format: {header}"
        self._rating_tags = [row[1] for row in rows if row[2] == "9"]
        self._general_tags = [row[1] for row in rows if row[2] == "0"]

    def _run_batch(self, path_imgs: list, undesired_tags: set) -> dict | None:
        imgs = np.array([im for _, im in path_imgs])
        try:
            probs = self._ort_sess.run(None, {self._input_name: imgs})[0]
        except Exception as exc:
            logger.error("Error running ONNX model: %s", exc)
            logger.error("Images causing error: %s", [p for p, _ in path_imgs])
            return None
        probs = probs[: len(path_imgs)]
        result = {}
        for (image_path, _), prob in zip(path_imgs, probs):
            tag_probs = [
                (self._general_tags[i], p)
                for i, p in enumerate(prob[4 : 4 + len(self._general_tags)])
                if p >= self._threshold and self._general_tags[i] not in undesired_tags
            ]
            combined_tags = [
                tag for tag, _ in sorted(tag_probs, key=lambda x: x[1], reverse=True)
            ]
            result[image_path] = combined_tags
            logger.debug("%s:", image_path)
            logger.debug("\tTags: %s", combined_tags)
        return result

    @staticmethod
    def _collate_fn_remove_corrupted(batch: list) -> list:
        return [x for x in batch if x is not None]

    @staticmethod
    def _flatten_data_entry(data_entry) -> list:
        flat_data = []
        for item in data_entry:
            if isinstance(item, list):
                flat_data.extend(item)
            else:
                flat_data.append(item)
        return flat_data

    def _run_preloaded(
        self,
        image_paths,
        preloaded_map: dict,
        inference_batch_size: int,
        undesired_tags: set,
        out_results: dict,
    ) -> None:
        from pixlstash.image_loading_dataset_prepper import ImageLoadingDatasetPrepper

        if not preloaded_map:
            return
        wd14_batch = []
        for path in image_paths:
            loaded_img = preloaded_map.get(str(path))
            if loaded_img is None:
                continue
            try:
                prepared = ImageLoadingDatasetPrepper._preprocess_image(loaded_img)
            except Exception as exc:
                logger.error("Could not preprocess preloaded image %s: %s", path, exc)
                continue
            wd14_batch.append((str(path), prepared))
            if len(wd14_batch) >= inference_batch_size:
                batch_result = self._run_batch(wd14_batch, undesired_tags)
                if batch_result is not None:
                    out_results.update(naturalize_tags(batch_result))
                wd14_batch.clear()
        if wd14_batch:
            batch_result = self._run_batch(wd14_batch, undesired_tags)
            if batch_result is not None:
                out_results.update(naturalize_tags(batch_result))

    def _run_tagging_loop(
        self,
        data_loader,
        stop_event,
        inference_batch_size: int,
        undesired_tags: set,
    ):
        b_imgs: list = []
        results: dict = {}
        failed = False
        for data_entry in tqdm(data_loader, smoothing=0.0, disable=self._silent):
            if stop_event is not None and stop_event.is_set():
                logger.info("Tagging interrupted by stop event.")
                break
            if failed:
                break
            for data in self._flatten_data_entry(data_entry):
                if stop_event is not None and stop_event.is_set():
                    logger.info("Tagging interrupted by stop event.")
                    failed = True
                    break
                if data is None:
                    continue
                image, image_path = data
                b_imgs.append((image_path, image))
                if len(b_imgs) >= inference_batch_size:
                    b_imgs = [(str(p), img) for p, img in b_imgs]
                    batch_result = self._run_batch(b_imgs, undesired_tags)
                    if batch_result is None:
                        logger.error(
                            "Tagging failed for batch: %s", [p for p, _ in b_imgs]
                        )
                        failed = True
                        break
                    results.update(naturalize_tags(batch_result))
                    b_imgs.clear()
        return failed, b_imgs, results

    def _run_dataloader(
        self,
        remaining_paths,
        stop_event,
        inference_batch_size: int,
        worker_count: int,
        undesired_tags: set,
        out_results: dict,
    ):
        from pixlstash.image_loading_dataset_prepper import ImageLoadingDatasetPrepper

        if not remaining_paths:
            return [], False

        logger.debug(
            "Starting tagger dataloader with worker count: %s and dataset size: %s",
            worker_count,
            len(remaining_paths),
        )

        def make_loader(workers, timeout):
            dataset = ImageLoadingDatasetPrepper(remaining_paths)
            return torch.utils.data.DataLoader(
                dataset,
                batch_size=inference_batch_size,
                shuffle=False,
                num_workers=workers,
                collate_fn=self._collate_fn_remove_corrupted,
                drop_last=False,
                timeout=timeout,
            )

        try:
            loader = make_loader(
                worker_count, WD14_DATALOADER_TIMEOUT if worker_count > 0 else 0
            )
            failed, b_imgs, dataloader_results = self._run_tagging_loop(
                loader, stop_event, inference_batch_size, undesired_tags
            )
            out_results.update(dataloader_results)
            return b_imgs, failed
        except RuntimeError as exc:
            logger.warning("Tagging dataloader stalled: %s", exc)
            if worker_count > 0 and (stop_event is None or not stop_event.is_set()):
                logger.warning(
                    "Retrying tagger dataloader with num_workers=0 for %s items",
                    len(remaining_paths),
                )
                loader = make_loader(0, 0)
                failed, b_imgs, dataloader_results = self._run_tagging_loop(
                    loader, stop_event, inference_batch_size, undesired_tags
                )
                out_results.update(dataloader_results)
                return b_imgs, failed
            return [], True
