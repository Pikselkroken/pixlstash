#################################################################
# Adapted from Kohya_ss https://github.com/kohya-ss/sd-scripts/ #
# Under the Apache 2.0 License                                  #
# https://github.com/kohya-ss/sd-scripts/blob/main/LICENSE.md   #
#################################################################
import open_clip
import csv
import cv2
import glob
import numpy as np
import onnxruntime as ort
import os
import torch

from pathlib import Path
from PIL import Image
from tqdm import tqdm


from .logging import get_logger
from pixelurgy_vault.tag_naturaliser import TagNaturaliser

logger = get_logger(__name__)

IMAGE_SIZE = 448
DEFAULT_WD14_TAGGER_REPO = "SmilingWolf/wd-v1-4-convnext-tagger-v2"
FILES = ["keras_metadata.pb", "saved_model.pb", "selected_tags.csv"]
FILES_ONNX = ["model.onnx"]
SUB_DIR = "variables"
SUB_DIR_FILES = ["variables.data-00000-of-00001", "variables.index"]
CSV_FILE = FILES[-1]
MODEL_DIR = "wd14_tagger_model"
BATCH_SIZE = 1
MAX_CONCURRENT_IMAGES = 8
GENERAL_THRESHOLD = 0.35
CHARACTER_THRESHOLD = 0.35
RECURSIVE = False
REMOVE_UNDERSCORE = True
UNDESIRED_TAGS = ""
FREQUENCY_TAGS = False
ONNX = True
APPEND_TAGS = False
USE_RATING_TAGS = True
USE_RATING_TAGS_AS_LAST_TAG = False
ALWAYS_FIRST_TAGS = None
CAPTION_SEPARATOR = ", "
TAG_REPLACEMENT = None
CHARACTER_TAG_EXPAND = False


def preprocess_image(image, image_size=IMAGE_SIZE):
    image = np.array(image)
    image = image[:, :, ::-1]  # RGB->BGR

    # pad to square
    size = max(image.shape[0:2])
    pad_x = size - image.shape[1]
    pad_y = size - image.shape[0]
    pad_l = pad_x // 2
    pad_t = pad_y // 2
    image = np.pad(
        image,
        ((pad_t, pad_y - pad_t), (pad_l, pad_x - pad_l), (0, 0)),
        mode="constant",
        constant_values=255,
    )

    image = resize_image(image, image_size, image_size)

    image = image.astype(np.float32)
    return image


def resize_image(image, h_out, w_out):
    return cv2.resize(image, (w_out, h_out), interpolation=cv2.INTER_AREA)


class ImageLoadingPrepDataset(torch.utils.data.Dataset):
    def __init__(self, image_paths):
        self.images = image_paths

    def __len__(self):
        return len(self.images)

    def __getitem__(self, idx):
        img_path = str(self.images[idx])

        try:
            image = Image.open(img_path).convert("RGB")
            image = preprocess_image(image)
            # ...existing code...
        except Exception as e:
            logger.error(f"Could not load image path: {img_path}, error: {e}")
            return None

        return (image, img_path)


class PictureTagger:
    def __init__(
        self,
        model_location=os.path.join(
            MODEL_DIR, DEFAULT_WD14_TAGGER_REPO.replace("/", "_")
        ),
        force_download=False,
        silent=True,
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
        self._clip_device = "cuda" if torch.cuda.is_available() else "cpu"
        self._clip_model = self._clip_model.to(self._clip_device)
        self._clip_tokenizer = open_clip.get_tokenizer("ViT-B-32")

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
        self._character_tags = [row[1] for row in rows[0:] if row[2] == "4"]

        # preprocess tags in advance
        if CHARACTER_TAG_EXPAND:
            for i, tag in enumerate(self._character_tags):
                if tag.endswith(")"):
                    tags = tag.split("(")
                    character_tag = "(".join(tags[:-1])
                    if character_tag.endswith("_"):
                        character_tag = character_tag[:-1]
                    series_tag = tags[-1].replace(")", "")
                    self._character_tags[i] = (
                        character_tag + CAPTION_SEPARATOR + series_tag
                    )

        if REMOVE_UNDERSCORE:
            self._rating_tags = [
                tag.replace("_", " ") if len(tag) > 3 else tag
                for tag in self._rating_tags
            ]
            self._general_tags = [
                tag.replace("_", " ") if len(tag) > 3 else tag
                for tag in self._general_tags
            ]
            self._character_tags = [
                tag.replace("_", " ") if len(tag) > 3 else tag
                for tag in self._character_tags
            ]

        if TAG_REPLACEMENT is not None:
            escaped_tag_replacements = TAG_REPLACEMENT.replace("\\,", "@@@@").replace(
                "\\;", "####"
            )
            tag_replacements = escaped_tag_replacements.split(";")
            for tag_replacement in tag_replacements:
                tags = tag_replacement.split(",")  # source, target
                assert len(tags) == 2, (
                    f"tag replacement must be in the format of `source,target`: {TAG_REPLACEMENT}"
                )
                source, target = [
                    tag.replace("@@@@", ",").replace("####", ";") for tag in tags
                ]
                logger.debug(f"replacing tag: {source} -> {target}")
                if source in self._general_tags:
                    self._general_tags[self._general_tags.index(source)] = target
                elif source in self._character_tags:
                    self._character_tags[self._character_tags.index(source)] = target
                elif source in self._rating_tags:
                    self._rating_tags[self._rating_tags.index(source)] = target

    def _ensure_model_files(self, force_download):
        # hf_hub_download
        # deprecated
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

    def _glob_images_pathlib(self, path, recursive):
        pattern = "**/*" if recursive else "*"
        exts = ["png", "jpg", "jpeg", "webp", "bmp"]
        files = []
        for ext in exts:
            files.extend(
                glob.glob(
                    os.path.join(str(path), f"{pattern}.{ext}"), recursive=recursive
                )
            )
        return files

    def _collate_fn_remove_corrupted(self, batch):
        """Collate function that allows to remove corrupted examples in the
        dataloader. It expects that the dataloader returns 'None' when that occurs.
        The 'None's in the batch are removed.
        """
        # Filter out all the Nones (corrupted examples)
        batch = list(filter(lambda x: x is not None, batch))
        return batch

    def _run_batch(
        self, path_imgs, tag_freq, caption_separator, undesired_tags, always_first_tags
    ):
        imgs = np.array([im for _, im in path_imgs])
        probs = self.ort_sess.run(None, {self.input_name: imgs})[0]  # onnx output numpy
        probs = probs[: len(path_imgs)]
        result = {}
        for (image_path, _), prob in zip(path_imgs, probs):
            # Build all tags (general, character, rating) with their probabilities
            tag_probs = []
            # Ratings
            if USE_RATING_TAGS or USE_RATING_TAGS_AS_LAST_TAG:
                ratings_probs = prob[:4]
                rating_index = ratings_probs.argmax()
                found_rating = self._rating_tags[rating_index]
                if found_rating not in undesired_tags:
                    tag_probs.append((found_rating, ratings_probs[rating_index]))
                    tag_freq[found_rating] = tag_freq.get(found_rating, 0) + 1
            # General tags
            for i, p in enumerate(prob[4 : 4 + len(self._general_tags)]):
                tag_name = self._general_tags[i]
                if p >= GENERAL_THRESHOLD and tag_name not in undesired_tags:
                    tag_probs.append((tag_name, p))
                    tag_freq[tag_name] = tag_freq.get(tag_name, 0) + 1
            # Character tags
            for i, p in enumerate(prob[4 + len(self._general_tags) :]):
                tag_name = self._character_tags[i]
                if p >= CHARACTER_THRESHOLD and tag_name not in undesired_tags:
                    tag_probs.append((tag_name, p))
                    tag_freq[tag_name] = tag_freq.get(tag_name, 0) + 1
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

    def tag_training_directory(self, train_data_dir="."):
        train_data_dir_path = Path(train_data_dir)
        image_paths = self._glob_images_pathlib(train_data_dir_path, RECURSIVE)
        return self.tag_images(image_paths)

    def tag_images(self, image_paths):
        tag_freq = {}

        caption_separator = CAPTION_SEPARATOR
        stripped_caption_separator = caption_separator.strip()
        undesired_tags = UNDESIRED_TAGS.split(stripped_caption_separator)
        undesired_tags = set(
            [tag.strip() for tag in undesired_tags if tag.strip() != ""]
        )

        always_first_tags = None
        if ALWAYS_FIRST_TAGS is not None:
            always_first_tags = [
                tag
                for tag in ALWAYS_FIRST_TAGS.split(stripped_caption_separator)
                if tag.strip() != ""
            ]

        # ...existing code...
        if MAX_CONCURRENT_IMAGES is not None:
            dataset = ImageLoadingPrepDataset(image_paths)
            data = torch.utils.data.DataLoader(
                dataset,
                batch_size=BATCH_SIZE,
                shuffle=False,
                num_workers=MAX_CONCURRENT_IMAGES,
                collate_fn=self._collate_fn_remove_corrupted,
                drop_last=False,
            )
        else:
            data = [[(None, ip)] for ip in image_paths]

        b_imgs = []
        all_results = {}
        for data_entry in tqdm(data, smoothing=0.0, disable=self._silent):
            for data in data_entry:
                if data is None:
                    continue

                image, image_path = data
                if image is None:
                    try:
                        image = Image.open(image_path)
                        if image.mode != "RGB":
                            image = image.convert("RGB")
                        image = preprocess_image(image)
                    except Exception as e:
                        logger.error(
                            f"Could not load image path: {image_path}, error: {e}"
                        )
                        continue
                b_imgs.append((image_path, image))

                if len(b_imgs) >= BATCH_SIZE:
                    b_imgs = [
                        (str(image_path), image) for image_path, image in b_imgs
                    ]  # Convert image_path to string
                    batch_result = self._run_batch(
                        b_imgs,
                        tag_freq,
                        caption_separator,
                        undesired_tags,
                        always_first_tags,
                    )
                    all_results.update(batch_result)
                    b_imgs.clear()

        if len(b_imgs) > 0:
            b_imgs = [
                (str(image_path), image) for image_path, image in b_imgs
            ]  # Convert image_path to string
            batch_result = self._run_batch(
                b_imgs, tag_freq, caption_separator, undesired_tags, always_first_tags
            )
            all_results.update(batch_result)

        if FREQUENCY_TAGS:
            sorted_tags = sorted(tag_freq.items(), key=lambda x: x[1], reverse=True)
            logger.debug("Tag frequencies:")
            for tag, freq in sorted_tags:
                logger.debug(f"{tag}: {freq}")

        return all_results

    def generate_embedding(self, character=None, picture=None):
        """
        Generate a CLIP embedding from all text found in character and picture objects (recursively), avoiding cycles.
        Uses the TagNaturaliser to convert tags to natural language.
        """

        def collect_text(obj, visited=None):
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
                    # If this is a tags field, map tags
                    if k == "tags" and isinstance(v, (list, tuple, set)):
                        mapped = [TagNaturaliser.get_natural_tag(tag) for tag in v]
                        mapped = [m for m in mapped if m]
                        texts.extend(mapped)
                    else:
                        texts.extend(collect_text(v, visited))
            elif isinstance(obj, (list, tuple, set)):
                for item in obj:
                    texts.extend(collect_text(item, visited))
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
                    # If this is a tags field, map tags
                    if attr == "tags" and isinstance(value, (list, tuple, set)):
                        mapped = [TagNaturaliser.get_natural_tag(tag) for tag in value]
                        mapped = [m for m in mapped if m]
                        texts.extend(mapped)
                    else:
                        texts.extend(collect_text(value, visited))
            return texts

        logger.debug(
            f"generate_embedding called with character={character}, picture={picture}"
        )
        import re

        texts = []
        texts.extend(collect_text(character))
        texts.extend(collect_text(picture))
        # Remove duplicates, empty strings, UUIDs, and date strings
        uuid_regex = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )
        date_regex = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z$")
        texts = [
            t
            for t in texts
            if t and not uuid_regex.match(t) and not date_regex.match(t)
        ]
        logger.debug(f"Embedding: texts used for embedding (filtered): {texts}")
        if not texts:
            logger.error(
                "Embedding: No text data for embedding. character=%s, picture=%s",
                character,
                picture,
            )
            raise ValueError("No text data for embedding.")
        full_text = ", ".join(texts)
        logger.debug(f"Embedding: full_text for CLIP: {full_text}")
        try:
            with torch.no_grad():
                text_tokens = self._clip_tokenizer([full_text]).to(self._clip_device)
                embedding = self._clip_model.encode_text(text_tokens)
                embedding = embedding.cpu().numpy()[0]
            return embedding
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
                return embedding
            else:
                raise
