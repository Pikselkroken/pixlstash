#################################################################
# Adapted from Kohya_ss https://github.com/kohya-ss/sd-scripts/ #
# Under the Apache 2.0 License                                  #
# https://github.com/kohya-ss/sd-scripts/blob/main/LICENSE.md   #
#################################################################
import open_clip
import csv
import numpy as np
import onnxruntime as ort
import os
import re
import torch

from tqdm import tqdm


from .logging import get_logger
from pixelurgy_vault.tag_naturaliser import TagNaturaliser
from pixelurgy_vault.image_loading_dataset_prepper import ImageLoadingDatasetPrepper

logger = get_logger(__name__)

DEFAULT_WD14_TAGGER_REPO = "SmilingWolf/wd-v1-4-convnext-tagger-v2"
FILES = ["keras_metadata.pb", "saved_model.pb", "selected_tags.csv"]
FILES_ONNX = ["model.onnx"]
SUB_DIR = "variables"
SUB_DIR_FILES = ["variables.data-00000-of-00001", "variables.index"]
CSV_FILE = FILES[-1]
MODEL_DIR = "wd14_tagger_model"
BATCH_SIZE = 1
MAX_CONCURRENT_IMAGES = 8
GENERAL_THRESHOLD = 0.4
UNDESIRED_TAGS = "solo, general, male_focus, meme, blurry, sensitive, realistic"
CAPTION_SEPARATOR = ", "


class PictureTagger:
    def __init__(
        self,
        model_location=os.path.join(
            MODEL_DIR, DEFAULT_WD14_TAGGER_REPO.replace("/", "_")
        ),
        force_download=False,
        silent=True,
        device=None,
    ):
        self._model_location = model_location
        self._silent = silent
        self._ensure_model_files(force_download=force_download)
        self._init_onnx_session()
        self._load_and_preprocess_tags()
        # Load CLIP model at construction for efficiency
        self._clip_model, _, self._clip_preprocess = (
            open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k"
            )
        )
        if device is not None:
            self._clip_device = device
        else:
            self._clip_device = "cuda" if torch.cuda.is_available() else "cpu"

        self._clip_model = self._clip_model.to(self._clip_device)
        self._clip_tokenizer = open_clip.get_tokenizer("ViT-B-32")

        self._tag_naturaliser = TagNaturaliser()

    def __enter__(self):
        logger.debug("PictureTagger.__enter__ called.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Release ONNX/PyTorch resources here
        # For ONNX: self.session = None
        # For PyTorch: del self.model; torch.cuda.empty_cache()
        import gc

        del self._clip_model
        self.ort_sess = None
        torch.cuda.empty_cache()

        gc.collect()
        logger.debug("PictureTagger.exit called, resources released.")

    def _init_onnx_session(self):
        onnx_path = f"{self._model_location}/model.onnx"
        logger.debug("Running wd14 tagger with onnx")
        logger.debug(f"loading onnx model: {onnx_path}")
        if not os.path.exists(onnx_path):
            raise Exception(
                f"onnx model not found: {onnx_path}, please redownload the model with --force_download"
            )
        if "OpenVINOExecutionProvider" in ort.get_available_providers():
            self.ort_sess = ort.InferenceSession(
                onnx_path,
                providers=(["OpenVINOExecutionProvider"]),
                provider_options=[{"device_type": "GPU", "precision": "FP32"}],
            )
        else:
            self.ort_sess = ort.InferenceSession(
                onnx_path,
                providers=(
                    ["CUDAExecutionProvider"]
                    if "CUDAExecutionProvider" in ort.get_available_providers()
                    else ["ROCMExecutionProvider"]
                    if "ROCMExecutionProvider" in ort.get_available_providers()
                    else ["CPUExecutionProvider"]
                ),
            )
        self.input_name = self.ort_sess.get_inputs()[0].name

    def _load_and_preprocess_tags(self):
        with open(
            os.path.join(self._model_location, CSV_FILE), "r", encoding="utf-8"
        ) as f:
            reader = csv.reader(f)
            line = [row for row in reader]
            header = line[0]  # tag_id,name,category,count
            rows = line[1:]
        assert (
            header[0] == "tag_id" and header[1] == "name" and header[2] == "category"
        ), f"unexpected csv format: {header}"

        self._rating_tags = [row[1] for row in rows[0:] if row[2] == "9"]
        self._general_tags = [row[1] for row in rows[0:] if row[2] == "0"]

    def _ensure_model_files(self, force_download):
        # hf_hub_download

        # https://github.com/toriato/stable-diffusion-webui-wd14-tagger/issues/22
        if not os.path.exists(self._model_location) or force_download:
            os.makedirs(self._model_location, exist_ok=True)
            logger.debug(
                f"downloading wd14 tagger model from hf_hub. id: {DEFAULT_WD14_TAGGER_REPO}"
            )
            # Always download ONNX model and selected_tags.csv
            from huggingface_hub import hf_hub_download

            # Download ONNX model
            onnx_model_path = os.path.join(self._model_location, "model.onnx")
            tags_csv_path = os.path.join(self._model_location, "selected_tags.csv")
            logger.debug(f"Downloading ONNX model to {onnx_model_path}")
            hf_hub_download(
                repo_id=DEFAULT_WD14_TAGGER_REPO,
                filename="model.onnx",
                local_dir=self._model_location,
                force_download=True,
            )
            logger.debug(f"Downloading selected_tags.csv to {tags_csv_path}")
            hf_hub_download(
                repo_id=DEFAULT_WD14_TAGGER_REPO,
                filename="selected_tags.csv",
                local_dir=self._model_location,
                force_download=True,
            )

    def _collate_fn_remove_corrupted(self, batch):
        """Collate function that allows to remove corrupted examples in the
        dataloader. It expects that the dataloader returns 'None' when that occurs.
        The 'None's in the batch are removed.
        """
        # Filter out all the Nones (corrupted examples)
        batch = list(filter(lambda x: x is not None, batch))
        return batch

    def _run_batch(self, path_imgs, undesired_tags, always_first_tags):
        imgs = np.array([im for _, im in path_imgs])
        try:
            probs = self.ort_sess.run(None, {self.input_name: imgs})[
                0
            ]  # onnx output numpy
        except Exception as e:
            logger.error(f"Error occurred while running ONNX model: {e}")
            logger.error(f"Images causing error: {[p for p, _ in path_imgs]}")
            return None

        probs = probs[: len(path_imgs)]
        result = {}
        for (image_path, _), prob in zip(path_imgs, probs):
            # Build all tags with their probabilities
            tag_probs = []
            # General tags
            for i, p in enumerate(prob[4 : 4 + len(self._general_tags)]):
                tag_name = self._general_tags[i]
                if p >= GENERAL_THRESHOLD and tag_name not in undesired_tags:
                    tag_probs.append((tag_name, p))
            # Sort all tags by probability
            all_tags_sorted = sorted(tag_probs, key=lambda x: x[1], reverse=True)
            combined_tags = [tag for tag, _ in all_tags_sorted]
            # Move always_first_tags to the front if present
            if always_first_tags is not None:
                for tag in reversed(always_first_tags):
                    if tag in combined_tags:
                        combined_tags.remove(tag)
                        combined_tags.insert(0, tag)
            # Instead of writing to file, store tags in result dict
            result[image_path] = combined_tags
            logger.debug("")
            logger.debug(f"{image_path}:")
            logger.debug(f"\tTags: {combined_tags}")
        return result

    @staticmethod
    def _flatten_data_entry(data_entry):
        flat_data = []
        for item in data_entry:
            if isinstance(item, list):
                flat_data.extend(item)
            else:
                flat_data.append(item)
        return flat_data

    @staticmethod
    def _naturalize_tags(batch_result):
        # Naturalize tags for each image
        for k, tags in batch_result.items():
            tags = [TagNaturaliser.get_natural_tag(tag) for tag in tags]
            tags = [t for t in tags if t]
            batch_result[k] = tags
        return batch_result

    @staticmethod
    def _merge_video_frame_tags(frame_tags):
        merged_results = {}
        for path, tags in frame_tags.items():
            if "#frame" in path:
                base_path = path.split("#frame")[0]
                if base_path not in merged_results:
                    merged_results[base_path] = set()
                merged_results[base_path].update(tags)
            else:
                merged_results[path] = set(tags)
        # Convert sets back to sorted lists
        merged_results = {k: sorted(list(v)) for k, v in merged_results.items()}
        return merged_results

    @staticmethod
    def _filter_texts(texts):
        # Remove duplicates, empty strings, UUIDs, and date strings
        uuid_regex = re.compile(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
        )
        date_regex = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")
        texts = [
            t
            for t in texts
            if t and not uuid_regex.match(t) and not date_regex.match(t)
        ]
        return texts

    @classmethod
    def _collect_text(cls, obj, visited=None):
        if visited is None:
            visited = set()
        texts = []
        obj_id = id(obj)
        if obj is None or obj_id in visited:
            return texts
        visited.add(obj_id)
        if isinstance(obj, str):
            if obj.strip():
                texts.append(obj.strip())
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k == "tags" and isinstance(v, (list, tuple, set)):
                    texts.extend([t for t in v if t])
                else:
                    texts.extend(cls._collect_text(v, visited))
        elif isinstance(obj, (list, tuple, set)):
            for item in obj:
                texts.extend(cls._collect_text(item, visited))
        elif hasattr(obj, "__dict__"):
            for attr, value in obj.__dict__.items():
                if attr.startswith("_"):
                    continue
                if attr in (
                    "parent",
                    "self",
                    "picture_iterations",
                    "picture_tagger",
                ):
                    continue
                if attr == "tags" and isinstance(value, (list, tuple, set)):
                    texts.extend([t for t in value if t])
                else:
                    texts.extend(cls._collect_text(value, visited))
        return texts

    def tag_images(self, image_paths):
        """
        Tag images using the WD14 tagger model.

        Args:
            image_paths (list of str): List of image file paths to be tagged.

        Returns:
            dict: A dictionary mapping image paths to their corresponding list of tags.
        """
        undesired_tags = UNDESIRED_TAGS.split(CAPTION_SEPARATOR.strip())
        undesired_tags = set(
            [tag.strip() for tag in undesired_tags if tag.strip() != ""]
        )
        logger.debug("Removing tags: " + ", ".join(undesired_tags))

        always_first_tags = None

        dataset = ImageLoadingDatasetPrepper(image_paths)
        worker_count = min(MAX_CONCURRENT_IMAGES, os.cpu_count() or 1, len(image_paths))
        data = torch.utils.data.DataLoader(
            dataset,
            batch_size=BATCH_SIZE,
            shuffle=False,
            num_workers=worker_count,
            collate_fn=self._collate_fn_remove_corrupted,
            drop_last=False,
        )

        b_imgs = []
        all_results = {}

        tagging_failed = False
        for data_entry in tqdm(data, smoothing=0.0, disable=self._silent):
            if tagging_failed:
                break

            flat_data = self._flatten_data_entry(data_entry)

            for data in flat_data:
                if data is None:
                    continue
                image, image_path = data
                b_imgs.append((image_path, image))
                if len(b_imgs) >= BATCH_SIZE:
                    b_imgs = [(str(image_path), image) for image_path, image in b_imgs]
                    batch_result = self._run_batch(
                        b_imgs,
                        undesired_tags,
                        always_first_tags,
                    )
                    if batch_result is None:
                        logger.error(
                            f"Tagging failed for batch: {[p for p, _ in b_imgs]}"
                        )
                        tagging_failed = True
                        break

                    all_results.update(self._naturalize_tags(batch_result))
                    b_imgs.clear()

        if len(b_imgs) > 0:
            b_imgs = [(str(image_path), image) for image_path, image in b_imgs]
            batch_result = self._run_batch(b_imgs, undesired_tags, always_first_tags)
            for k, tags in batch_result.items():
                tags = [TagNaturaliser.get_natural_tag(tag) for tag in tags]
                tags = [t for t in tags if t]
                batch_result[k] = tags
            all_results.update(batch_result)

        return self._merge_video_frame_tags(all_results)

    def generate_embedding(self, character=None, picture=None):
        """
        Generate a CLIP embedding from all text found in character and picture objects (recursively), avoiding cycles.
        Uses the TagNaturaliser to convert tags to natural language.

        Args:
            character (Character, optional): Character object.
            picture (Picture, optional): Picture object.

        Returns:
            tuple: A tuple containing the embedding (numpy array) and the full text (str).
        """

        logger.info(
            f"generate_embedding called with character={character}, picture={picture}"
        )

        texts = self._collect_text(picture)
        texts = self._filter_texts(texts)
        logger.debug(f"Embedding: texts used for embedding (filtered): {texts}")
        if not texts:
            logger.error(
                "Embedding: No text data for embedding. character=%s, picture=%s",
                character,
                picture,
            )
            raise ValueError("No text data for embedding.")

        logger.info(f"Embedding: tags going into LM: {texts}")
        full_text = self._tag_naturaliser.tags_to_sentence(texts)
        full_text = (
            character.name + ", " if character and character.name else ""
        ) + full_text.lower()
        logger.info(f"Embedding: full_text for CLIP: {full_text}")

        try:
            with torch.no_grad():
                text_tokens = self._clip_tokenizer([full_text]).to(self._clip_device)
                embedding = self._clip_model.encode_text(text_tokens)
                embedding = embedding.cpu().numpy()[0]
            return embedding, full_text
        except RuntimeError as e:
            if (
                ("CUDA out of memory" in str(e))
                or ("not compatible" in str(e))
                or ("CUDA error" in str(e))
            ):
                logger.warning(
                    f"CLIP embedding failed on CUDA: {e}. Falling back to CPU."
                )
                self._clip_device = "cpu"
                self._clip_model = self._clip_model.to(self._clip_device)
                with torch.no_grad():
                    text_tokens = self._clip_tokenizer([full_text]).to(
                        self._clip_device
                    )
                    embedding = self._clip_model.encode_text(text_tokens)
                    embedding = embedding.cpu().numpy()[0]
                return embedding, full_text
            else:
                raise
