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
from sentence_transformers import SentenceTransformer

from .logging import get_logger
from pixlvault.tag_naturaliser import TagNaturaliser
from pixlvault.image_loading_dataset_prepper import ImageLoadingDatasetPrepper

logger = get_logger(__name__)

DEFAULT_WD14_TAGGER_REPO = "SmilingWolf/wd-v1-4-convnext-tagger-v2"
FILES = ["keras_metadata.pb", "saved_model.pb", "selected_tags.csv"]
FILES_ONNX = ["model.onnx"]
SUB_DIR = "variables"
SUB_DIR_FILES = ["variables.data-00000-of-00001", "variables.index"]
CSV_FILE = FILES[-1]
MODEL_DIR = "wd14_tagger_model"
BATCH_SIZE = 1
MAX_CONCURRENT_IMAGES_GPU = 32
MAX_CONCURRENT_IMAGES_CPU = 8
GENERAL_THRESHOLD = 0.4
UNDESIRED_TAGS = "solo, general, male_focus, meme, blurry, sensitive, realistic"
CAPTION_SEPARATOR = ", "
FLORENCE_REVISION = "5ca5edf5bd017b9919c05d08aebef5e4c7ac3bac"


class PictureTagger:
    """
    Generates natural captions using Florence-2.
    Also generates tags with WD14 and corrects them using the captions provided by Florence-2.
    Generates text embeddings using OpenCLIP.
    """

    FAST_CAPTIONS = False  # Class variable to control fast caption mode
    FORCE_CPU = False  # Class variable to control CPU inference

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

        # Store device for both CLIP and ONNX
        if PictureTagger.FORCE_CPU:
            self._device = "cpu"
        else:
            if device is not None:
                self._device = device
            else:
                self._device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.debug(f"PictureTagger initialized with device: {self._device}")

        self._ensure_model_files(force_download=force_download)
        self._init_onnx_session()
        self._load_and_preprocess_tags()
        # Load CLIP model at construction for efficiency
        self._clip_model, _, self._clip_preprocess = (
            open_clip.create_model_and_transforms(
                "ViT-B-32", pretrained="laion2b_s34b_b79k"
            )
        )

        self._clip_device = self._device
        self._clip_model = self._clip_model.to(self._clip_device)
        self._clip_tokenizer = open_clip.get_tokenizer("ViT-B-32")

        self._tag_naturaliser = TagNaturaliser()

        # Initialize Florence-2 for captioning
        logger.debug("initialising Florence-2 for captioning...")
        self._florence_model = None
        self._florence_processor = None

        self._florence_device = None
        self._florence_model_name = "microsoft/Florence-2-base"

        self._florence_max_tokens = 40 if PictureTagger.FAST_CAPTIONS else 60

        self._init_florence_captioning()

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

    def max_concurrent_images(self):
        if self._device == "cpu":
            return MAX_CONCURRENT_IMAGES_CPU
        else:
            return MAX_CONCURRENT_IMAGES_GPU

    def _init_florence_captioning(self):
        """
        Enable Florence-2 for natural language captioning instead of tag-based descriptions.
        This will download the model on first use (~900MB).
        """
        if self._florence_model is not None:
            logger.debug("Florence-2 already loaded")
            return

        try:
            logger.debug("Loading Florence-2 model for captioning...")
            import transformers

            # Check transformers version
            version = transformers.__version__
            logger.debug(f"Transformers version: {version}")

            # Check if device was explicitly set to CPU
            device_str = str(self._device)
            use_cpu = PictureTagger.FORCE_CPU or device_str == "cpu"

            if use_cpu:
                # Device explicitly set to CPU - respect that
                logger.debug(
                    "Device set to CPU, loading Florence-2 on CPU with FP32..."
                )
                self._load_florence_model(torch.device("cpu"), torch.float32)
                logger.debug("Florence-2 loaded successfully on CPU")
            elif torch.cuda.is_available():
                try:
                    logger.debug("Attempting to load Florence-2 on GPU with FP16...")
                    self._load_florence_model(torch.device("cuda"), torch.float16)
                    logger.debug("Florence-2 loaded successfully on GPU (~500MB VRAM)")
                except Exception as gpu_error:
                    logger.warning(
                        f"GPU loading failed, falling back to CPU: {gpu_error}"
                    )
                    self._load_florence_model(torch.device("cpu"), torch.float32)
                    logger.debug("Florence-2 loaded successfully on CPU")
            else:
                # No GPU available, use CPU
                logger.debug("No GPU available, loading Florence-2 on CPU with FP32...")
                device = (
                    self._device
                    if isinstance(self._device, torch.device)
                    else torch.device(self._device)
                )
                self._load_florence_model(device, torch.float32)
                logger.debug("Florence-2 loaded successfully on CPU")

        except Exception as e:
            logger.error(f"Failed to load Florence-2: {e}")
            logger.error("Try: pip install --upgrade transformers")

    def _load_florence_model(self, device, dtype):
        from transformers import AutoProcessor, AutoModelForCausalLM

        if not isinstance(device, torch.device):
            device = torch.device(device)

        self._florence_processor = AutoProcessor.from_pretrained(
            self._florence_model_name,
            revision=FLORENCE_REVISION,
            trust_remote_code=True,
        )

        # Try SDPA first, fall back to eager if not supported
        attn_impl = "sdpa"
        try:
            self._florence_model = AutoModelForCausalLM.from_pretrained(
                self._florence_model_name,
                trust_remote_code=True,
                revision=FLORENCE_REVISION,
                dtype=dtype,
                attn_implementation=attn_impl,
            ).to(device)
        except (TypeError, AttributeError) as e:
            logger.debug(f"SDPA not supported, falling back to eager attention: {e}")
            attn_impl = "eager"
            self._florence_model = AutoModelForCausalLM.from_pretrained(
                self._florence_model_name,
                trust_remote_code=True,
                revision=FLORENCE_REVISION,
                dtype=dtype,
                attn_implementation=attn_impl,
            ).to(device)

        self._florence_model.eval()

        # Try to compile the model for better performance (PyTorch 2.0+)
        try:
            if hasattr(torch, "compile") and device.type == "cuda":
                logger.debug("Compiling Florence-2 model for better performance...")
                self._florence_model = torch.compile(
                    self._florence_model,
                    mode="reduce-overhead",  # Balance compilation time and performance
                )
                logger.debug("Model compilation successful")
        except Exception as compile_error:
            logger.warning(f"Model compilation failed (not critical): {compile_error}")

        self._florence_device = device

    def _reload_florence_on_cpu(self):
        logger.warning(
            "Florence-2 GPU inference failed; attempting to reload on CPU..."
        )
        try:
            self._florence_model = None
            self._florence_processor = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            self._load_florence_model(torch.device("cpu"), torch.float32)
            logger.debug("Florence-2 reloaded on CPU")
            return True
        except Exception as cpu_error:
            logger.error(
                f"Failed to reload Florence-2 on CPU: {cpu_error}", exc_info=True
            )
            return False

    def _generate_florence_caption(
        self, image_path, character_name=None, _retry_on_cpu=True
    ):
        """
        Generate a natural language caption for an image using Florence-2.

        Args:
            image_path (str): Path to the image file
            character_name (str, optional): Name of the character to include as context

        Returns:
            str: Natural language caption
        """
        if self._florence_model is None:
            logger.error("Florence-2 model is not initialized")
            return None

        try:
            import os

            ext = os.path.splitext(image_path)[1].lower()
            video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
            from PIL import Image

            caption = None
            if ext in video_exts:
                from pixlvault.picture_utils import PictureUtils

                frames = PictureUtils.extract_video_frames(image_path)
                for idx, pil_img in enumerate(frames):
                    # Resize large images to speed up processing
                    MAX_DIM = 640
                    if max(pil_img.size) > MAX_DIM:
                        aspect_ratio = pil_img.width / pil_img.height
                        if pil_img.width > pil_img.height:
                            new_width = MAX_DIM
                            new_height = int(MAX_DIM / aspect_ratio)
                        else:
                            new_height = MAX_DIM
                            new_width = int(MAX_DIM * aspect_ratio)
                        pil_img = pil_img.resize(
                            (new_width, new_height), Image.Resampling.LANCZOS
                        )
                        logger.debug(
                            f"Resized video frame to {new_width}x{new_height} for faster processing"
                        )
                    inputs = self._florence_processor(
                        text="<MORE_DETAILED_CAPTION>",
                        images=pil_img,
                        return_tensors="pt",
                    )
                    florence_device = getattr(self, "_florence_device", self._device)
                    target_dtype = (
                        self._florence_model.dtype
                        if hasattr(self._florence_model, "dtype")
                        else None
                    )
                    if target_dtype and target_dtype == torch.float16:
                        inputs = {
                            k: v.to(florence_device).half()
                            if torch.is_tensor(v) and v.dtype == torch.float32
                            else v.to(florence_device)
                            if torch.is_tensor(v)
                            else v
                            for k, v in inputs.items()
                        }
                    else:
                        inputs = {
                            k: v.to(florence_device) if torch.is_tensor(v) else v
                            for k, v in inputs.items()
                        }
                    logger.debug(f"Inputs moved to {florence_device}")
                    with torch.inference_mode():
                        generated_ids = self._florence_model.generate(
                            input_ids=inputs["input_ids"],
                            pixel_values=inputs["pixel_values"],
                            max_new_tokens=self._florence_max_tokens,
                            early_stopping=False,
                            do_sample=False,
                            num_beams=1,
                            use_cache=False,
                            pad_token_id=self._florence_processor.tokenizer.pad_token_id,
                        )
                    generated_text = self._florence_processor.batch_decode(
                        generated_ids, skip_special_tokens=False
                    )[0]
                    caption = (
                        generated_text.replace("<s>", "").replace("</s>", "").strip()
                    )
                    # Ensure caption ends at last sentence-ending punctuation
                    last_punct = max([caption.rfind(p) for p in [".", "!", "?"]])
                    if last_punct != -1:
                        caption = caption[: last_punct + 1].strip()
                    if caption:
                        logger.debug(f"Florence-2 caption (frame {idx}): {caption}")
                        break
            else:
                image = Image.open(image_path).convert("RGB")
                MAX_DIM = 640
                if max(image.size) > MAX_DIM:
                    aspect_ratio = image.width / image.height
                    if image.width > image.height:
                        new_width = MAX_DIM
                        new_height = int(MAX_DIM / aspect_ratio)
                    else:
                        new_height = MAX_DIM
                        new_width = int(MAX_DIM * aspect_ratio)
                    image = image.resize(
                        (new_width, new_height), Image.Resampling.LANCZOS
                    )
                    logger.debug(
                        f"Resized image to {new_width}x{new_height} for faster processing"
                    )
                inputs = self._florence_processor(
                    text="<MORE_DETAILED_CAPTION>", images=image, return_tensors="pt"
                )
                florence_device = getattr(self, "_florence_device", self._device)
                target_dtype = (
                    self._florence_model.dtype
                    if hasattr(self._florence_model, "dtype")
                    else None
                )
                if target_dtype and target_dtype == torch.float16:
                    inputs = {
                        k: v.to(florence_device).half()
                        if torch.is_tensor(v) and v.dtype == torch.float32
                        else v.to(florence_device)
                        if torch.is_tensor(v)
                        else v
                        for k, v in inputs.items()
                    }
                else:
                    inputs = {
                        k: v.to(florence_device) if torch.is_tensor(v) else v
                        for k, v in inputs.items()
                    }
                logger.debug(f"Inputs moved to {florence_device}")
                with torch.inference_mode():
                    generated_ids = self._florence_model.generate(
                        input_ids=inputs["input_ids"],
                        pixel_values=inputs["pixel_values"],
                        max_new_tokens=self._florence_max_tokens,
                        early_stopping=False,
                        do_sample=False,
                        num_beams=1,
                        use_cache=False,
                        pad_token_id=self._florence_processor.tokenizer.pad_token_id,
                    )
                generated_text = self._florence_processor.batch_decode(
                    generated_ids, skip_special_tokens=False
                )[0]
                caption = generated_text.replace("<s>", "").replace("</s>", "").strip()
                # Ensure caption ends at last sentence-ending punctuation
                last_punct = max([caption.rfind(p) for p in [".", "!", "?"]])
                if last_punct != -1:
                    caption = caption[: last_punct + 1].strip()
                if caption:
                    logger.debug(f"Florence-2 caption: {caption}")
            # Insert character name if provided
            if caption and character_name:
                person_pattern = r"\b(woman|man|person|girl|boy|lady|gentleman|individual|figure|character)\b"
                match = re.search(person_pattern, caption, re.IGNORECASE)
                if match:
                    insert_pos = match.end()
                    caption = (
                        caption[:insert_pos]
                        + f" named {character_name}"
                        + caption[insert_pos:]
                    )
                else:
                    caption = f"{character_name}: {caption}"
            return caption

        except Exception as e:
            import traceback

            is_cuda_issue = "cuda" in str(e).lower()
            using_cuda = (
                getattr(self, "_florence_device", None) is not None
                and getattr(self._florence_device, "type", "") == "cuda"
            )

            if _retry_on_cpu and using_cuda and is_cuda_issue:
                logger.warning(
                    "Florence-2 captioning failed on GPU (%s); retrying on CPU.", e
                )
                if self._reload_florence_on_cpu():
                    return self._generate_florence_caption(
                        image_path, character_name, _retry_on_cpu=False
                    )

            logger.error(f"Florence-2 captioning failed for {image_path}: {e}")
            logger.debug(traceback.format_exc())
            return None

    def _init_onnx_session(self):
        onnx_path = f"{self._model_location}/model.onnx"
        logger.debug("Running wd14 tagger with onnx")
        logger.debug(f"loading onnx model: {onnx_path}")
        if not os.path.exists(onnx_path):
            raise Exception(
                f"onnx model not found: {onnx_path}, please redownload the model with --force_download"
            )

        # Use CPU-only when device is set to "cpu" to coexist with LLMs and diffusion models
        if self._device == "cpu":
            logger.debug("initialising WD14 tagger with CPUExecutionProvider")
            self.ort_sess = ort.InferenceSession(
                onnx_path, providers=["CPUExecutionProvider"]
            )
        else:
            # Allow GPU providers when not explicitly set to CPU
            logger.debug(f"initialising WD14 tagger with device: {self._device}")
            if "OpenVINOExecutionProvider" in ort.get_available_providers():
                self.ort_sess = ort.InferenceSession(
                    onnx_path,
                    providers=["OpenVINOExecutionProvider"],
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

    def _run_batch(self, path_imgs, undesired_tags):
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
            # Only process dataclasses with explicit include_in_text_embedding metadata
            import dataclasses

            if dataclasses.is_dataclass(obj):
                for field in dataclasses.fields(obj):
                    if field.metadata.get("include_in_text_embedding", False):
                        value = getattr(obj, field.name)
                        if field.name == "tags" and isinstance(
                            value, (list, tuple, set)
                        ):
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

        dataset = ImageLoadingDatasetPrepper(image_paths)
        if self._device == "cpu":
            max_concurrent = MAX_CONCURRENT_IMAGES_CPU
        else:
            max_concurrent = MAX_CONCURRENT_IMAGES_GPU
        worker_count = min(max_concurrent, os.cpu_count() // 2 or 1, len(image_paths))
        logger.debug(
            "Starting tagger dataloader with worker count: "
            + str(worker_count)
            + " and dataset size: "
            + str(len(dataset))
        )
        data = torch.utils.data.DataLoader(
            dataset,
            batch_size=BATCH_SIZE,
            shuffle=False,
            num_workers=worker_count,
            collate_fn=self._collate_fn_remove_corrupted,
            drop_last=False,
        )

        logger.debug(f"Got some tags: {data}")
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
            batch_result = self._run_batch(b_imgs, undesired_tags)
            for k, tags in batch_result.items():
                tags = [TagNaturaliser.get_natural_tag(tag) for tag in tags]
                tags = [t for t in tags if t]
                batch_result[k] = tags
            all_results.update(batch_result)

        logger.debug(f"Completed tagging for {len(all_results)} images.")
        return self._merge_video_frame_tags(all_results)

    def generate_description(self, picture, character=None):
        florence_caption = self._generate_florence_caption(picture.file_path)
        if florence_caption:
            character_name_capitalized = None
            if character and hasattr(character, "name") and character.name:
                character_name_capitalized = " ".join(
                    word.capitalize() for word in character.name.split()
                )
                import re

                person_pattern = r"\b(a young woman|a woman|the woman|a young man|a man|the man|a person|the person)\b"
                match = re.search(person_pattern, florence_caption, re.IGNORECASE)
                if match:
                    insert_pos = match.end()
                    florence_caption = (
                        florence_caption[:insert_pos]
                        + f" named {character_name_capitalized}"
                        + florence_caption[insert_pos:]
                    )
                else:
                    florence_caption = (
                        f"{character_name_capitalized}. {florence_caption}"
                    )
            logger.debug(
                f"Text embedding: using Florence-2 caption: {florence_caption}"
            )
        else:
            logger.error(
                "Florence captioning failed for %s",
                getattr(picture, "file_path", None),
            )
            raise RuntimeError("Florence captioning failed.")
        return florence_caption

    def generate_text_embedding(self, picture, character=None):
        """
        Generate a SBERT embedding from all text found in character and picture objects (recursively), avoiding cycles.
        Returns text_embedding and full_text.
        """

        texts = []
        if character:
            texts.extend(self._collect_text(character))
        if picture:
            texts.extend(self._collect_text(picture))
        texts = self._filter_texts(texts)
        logger.debug(f"Text Embedding: texts used for embedding (filtered): {texts}")
        if not texts:
            logger.error(
                "Text Embedding: No text data for embedding. character=%s, picture=%s",
                character,
                picture,
            )
            raise ValueError("No text data for embedding.")
        logger.debug(f"Text Embedding: tags going into description: {texts}")
        full_text = self._tag_naturaliser.tags_to_sentence(texts)
        full_text = full_text.lower()
        logger.debug(f"Text Embedding: full_text for SBERT: {full_text}")

        # Generate text embedding using SBERT
        sbert_model = getattr(self, "_sbert_model", None)
        if sbert_model is None:
            sbert_model = SentenceTransformer("all-MiniLM-L6-v2")
            self._sbert_model = sbert_model

        text_embedding = None
        try:
            text_embedding = sbert_model.encode(full_text)
        except RuntimeError as e:
            if "CUDA" in str(e):
                logger.warning(
                    f"SBERT embedding failed on CUDA: {e}. Falling back to CPU."
                )
                sbert_model = SentenceTransformer("all-MiniLM-L6-v2", device="cpu")
                self._sbert_model = sbert_model
                text_embedding = sbert_model.encode(full_text)
            else:
                logger.error(f"Failed to generate text embedding: {e}")
                raise
        return text_embedding, full_text

    def generate_facial_features(self, picture):
        """
        Generate facial features from picture object if face_bbox is present.
        Returns facial_features or None.
        """
        facial_features = None
        if (
            picture
            and hasattr(picture, "file_path")
            and hasattr(picture, "face_bbox")
            and picture.face_bbox
        ):
            try:
                from pixlvault.picture_utils import PictureUtils
                import os

                ext = os.path.splitext(picture.file_path)[1].lower()
                video_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"}
                if ext in video_exts:
                    import cv2

                    cap = cv2.VideoCapture(picture.file_path)
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    for idx in range(frame_count):
                        ret, frame = cap.read()
                        if not ret or frame is None:
                            continue
                        face_crop = PictureUtils.crop_face_from_frame(
                            frame, picture.face_bbox
                        )
                        if face_crop is not None:
                            if isinstance(face_crop, np.ndarray):
                                from PIL import Image

                                face_crop = Image.fromarray(
                                    cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB)
                                )
                            img_input = (
                                self._clip_preprocess(face_crop)
                                .unsqueeze(0)
                                .to(self._clip_device)
                            )
                            with torch.no_grad():
                                facial_features = (
                                    self._clip_model.encode_image(img_input)
                                    .cpu()
                                    .numpy()[0]
                                )
                            break
                    cap.release()
                else:
                    face_crop = PictureUtils.load_and_crop_face_bbox(
                        picture.file_path, picture.face_bbox
                    )
                    if face_crop is not None:
                        img_input = (
                            self._clip_preprocess(face_crop)
                            .unsqueeze(0)
                            .to(self._clip_device)
                        )
                        with torch.no_grad():
                            facial_features = (
                                self._clip_model.encode_image(img_input)
                                .cpu()
                                .numpy()[0]
                            )
            except RuntimeError as e:
                if (
                    ("CUDA out of memory" in str(e))
                    or ("not compatible" in str(e))
                    or ("CUDA error" in str(e))
                ):
                    self._clip_device = "cpu"
                    self._clip_model = self._clip_model.to(self._clip_device)
                    try:
                        if face_crop is not None:
                            img_input = (
                                self._clip_preprocess(face_crop)
                                .unsqueeze(0)
                                .to(self._clip_device)
                            )
                            with torch.no_grad():
                                facial_features = (
                                    self._clip_model.encode_image(img_input)
                                    .cpu()
                                    .numpy()[0]
                                )
                    except Exception:
                        facial_features = None
                else:
                    facial_features = None
        return facial_features

    def correct_tags_with_florence(self, florence_desc, current_tags=None):
        """
        Use Florence-2 description to extract candidate tags and update image tags.
        Returns corrected tag list.
        """
        try:
            import spacy

            nlp = None
            try:
                nlp = spacy.load("en_core_web_sm")
            except OSError:
                import spacy.cli

                spacy.cli.download("en_core_web_sm")
            assert nlp is not None, "Failed to load spaCy model"
            doc = nlp(florence_desc)
            candidates = set()
            for token in doc:
                if token.pos_ in ("NOUN", "PROPN", "ADJ") and len(token.text) > 2:
                    candidates.add(token.lemma_.lower())
            # Map candidates to known tags using tag_naturaliser
            mapped_tags = []
            for cand in candidates:
                nat_tag = TagNaturaliser.get_natural_tag(cand)
                if nat_tag:
                    mapped_tags.append(nat_tag)
                else:
                    mapped_tags.append(cand)
            # Optionally merge with current tags
            if current_tags:
                # Keep tags that are in both or add new ones
                merged = set(current_tags) | set(mapped_tags)
                return sorted(merged)
            return sorted(mapped_tags)
        except Exception as e:
            logger.error(f"Failed to extract tags from Florence description: {e}")
            return current_tags or []
