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

        # Store device for both CLIP and ONNX
        if device is not None:
            self._device = device
        else:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"

        logger.info(f"PictureTagger initialized with device: {self._device}")

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

        # Initialize Florence-2 for optional captioning
        self._florence_model = None
        self._florence_processor = None
        self._use_florence = False

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

    def enable_florence_captioning(self):
        """
        Enable Florence-2 for natural language captioning instead of tag-based descriptions.
        This will download the model on first use (~900MB).
        """
        if self._florence_model is not None:
            logger.info("Florence-2 already loaded")
            return

        try:
            logger.info("Loading Florence-2 model for captioning...")
            from transformers import AutoProcessor, AutoModelForCausalLM
            import transformers

            # Check transformers version
            version = transformers.__version__
            logger.info(f"Transformers version: {version}")

            model_name = (
                "microsoft/Florence-2-base"  # Use base model for faster inference
            )

            # Try GPU first, fall back to CPU if needed
            if torch.cuda.is_available():
                try:
                    logger.info("Attempting to load Florence-2 on GPU with FP16...")
                    device = torch.device("cuda")
                    dtype = torch.float16

                    self._florence_processor = AutoProcessor.from_pretrained(
                        model_name, trust_remote_code=True
                    )
                    self._florence_model = AutoModelForCausalLM.from_pretrained(
                        model_name,
                        trust_remote_code=True,
                        dtype=dtype,
                        attn_implementation="eager",
                    ).to(device)
                    self._florence_model.eval()
                    self._florence_device = device

                    self._use_florence = True
                    logger.info("Florence-2 loaded successfully on GPU (~500MB VRAM)")
                except Exception as gpu_error:
                    logger.warning(
                        f"GPU loading failed, falling back to CPU: {gpu_error}"
                    )
                    device = torch.device("cpu")
                    dtype = torch.float32

                    self._florence_processor = AutoProcessor.from_pretrained(
                        model_name, trust_remote_code=True
                    )
                    self._florence_model = AutoModelForCausalLM.from_pretrained(
                        model_name,
                        trust_remote_code=True,
                        dtype=dtype,
                        attn_implementation="eager",
                    ).to(device)
                    self._florence_model.eval()
                    self._florence_device = device

                    self._use_florence = True
                    logger.info("Florence-2 loaded successfully on CPU")
            else:
                # No GPU available, use CPU
                logger.info("No GPU available, loading Florence-2 on CPU with FP32...")
                device = (
                    self._device
                    if isinstance(self._device, torch.device)
                    else torch.device(self._device)
                )
                dtype = torch.float32

                self._florence_processor = AutoProcessor.from_pretrained(
                    model_name, trust_remote_code=True
                )
                self._florence_model = AutoModelForCausalLM.from_pretrained(
                    model_name,
                    trust_remote_code=True,
                    dtype=dtype,
                    attn_implementation="eager",
                ).to(device)
                self._florence_model.eval()
                self._florence_device = device

                self._use_florence = True
                logger.info("Florence-2 loaded successfully on CPU")

        except Exception as e:
            logger.error(f"Failed to load Florence-2: {e}")
            logger.info("Falling back to tag-based captioning")
            logger.info("Try: pip install --upgrade transformers")
            self._use_florence = False

    def _generate_florence_caption(self, image_path, character_name=None):
        """
        Generate a natural language caption for an image using Florence-2.

        Args:
            image_path (str): Path to the image file
            character_name (str, optional): Name of the character to include as context

        Returns:
            str: Natural language caption
        """
        if not self._use_florence or self._florence_model is None:
            return None

        try:
            from PIL import Image

            image = Image.open(image_path).convert("RGB")

            # Use detailed captioning task
            # Note: Florence-2 requires the task token to be alone, we cannot add character context in the prompt
            prompt = "<MORE_DETAILED_CAPTION>"

            # Process inputs
            logger.debug(f"Processing image with prompt: {prompt}")
            logger.debug(f"Image size: {image.size}")
            inputs = self._florence_processor(
                text=prompt, images=image, return_tensors="pt"
            )
            logger.debug(f"Processor output keys: {inputs.keys()}")

            # Move inputs to device (use Florence's device, not the general device)
            florence_device = getattr(self, "_florence_device", self._device)
            # Match the dtype of the model (FP16 on GPU, FP32 on CPU)
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

            # Use the Florence-2 specific generation method
            with torch.no_grad():
                generated_ids = self._florence_model.generate(
                    input_ids=inputs["input_ids"],
                    pixel_values=inputs["pixel_values"],
                    max_new_tokens=100,
                    early_stopping=False,
                    do_sample=False,
                    num_beams=1,  # Use greedy decoding
                    use_cache=False,  # Disable KV cache to avoid past_key_values issues
                )

            generated_text = self._florence_processor.batch_decode(
                generated_ids, skip_special_tokens=False
            )[0]

            # Florence-2 output format: "<s><MORE_DETAILED_CAPTION>caption text</s>"
            # Extract the caption between the prompt and end token, removing special tokens
            if prompt in generated_text:
                caption = generated_text.split(prompt)[1].replace("</s>", "").strip()
            else:
                caption = generated_text.replace("</s>", "").strip()

            # Remove any remaining special tokens like <s>
            caption = caption.replace("<s>", "").strip()

            logger.info(f"Florence-2 caption: {caption}")
            return caption

        except Exception as e:
            import traceback

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
            logger.info("Initializing WD14 tagger with CPUExecutionProvider")
            self.ort_sess = ort.InferenceSession(
                onnx_path, providers=["CPUExecutionProvider"]
            )
        else:
            # Allow GPU providers when not explicitly set to CPU
            logger.info(f"Initializing WD14 tagger with device: {self._device}")
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
            # Only process dataclasses with explicit include_in_embedding metadata
            import dataclasses

            if dataclasses.is_dataclass(obj):
                for field in dataclasses.fields(obj):
                    if field.metadata.get("include_in_embedding", False):
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
        logger.info("Removing tags: " + ", ".join(undesired_tags))

        dataset = ImageLoadingDatasetPrepper(image_paths)
        worker_count = min(
            MAX_CONCURRENT_IMAGES, os.cpu_count() // 2 or 1, len(image_paths)
        )
        logger.info(
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

        logger.info(f"Got some tags: {data}")
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

        logger.info(f"Completed tagging for {len(all_results)} images.")
        return self._merge_video_frame_tags(all_results)

    def generate_embedding(self, character=None, picture=None):
        """
        Generate a CLIP embedding from all text found in character and picture objects (recursively), avoiding cycles.
        Can use Florence-2 for natural language captioning or TagNaturaliser for tag-based descriptions.

        Args:
            character (Character, optional): Character object.
            picture (Picture, optional): Picture object.

        Returns:
            tuple: A tuple containing the embedding (numpy array) and the full text (str).
        """

        # Try Florence-2 captioning first if enabled and picture has file_path
        full_text = None
        if (
            self._use_florence
            and picture
            and hasattr(picture, "file_path")
            and picture.file_path
        ):
            florence_caption = self._generate_florence_caption(picture.file_path)
            if florence_caption:
                # Integrate character name naturally if available
                character_name_capitalized = None
                if character and hasattr(character, "name") and character.name:
                    # Capitalize character name (title case each word)
                    character_name_capitalized = " ".join(
                        word.capitalize() for word in character.name.split()
                    )

                    # Add "named CHARACTER_NAME" after the first mention of a person
                    import re

                    # Pattern to find first mention of a person (case-insensitive)
                    person_pattern = r"\b(a young woman|a woman|the woman|a young man|a man|the man|a person|the person)\b"
                    match = re.search(person_pattern, florence_caption, re.IGNORECASE)
                    if match:
                        # Insert "named CHARACTER_NAME" right after the matched term
                        insert_pos = match.end()
                        florence_caption = (
                            florence_caption[:insert_pos]
                            + f" named {character_name_capitalized}"
                            + florence_caption[insert_pos:]
                        )
                    else:
                        # If no person term found, prepend character name
                        florence_caption = (
                            f"{character_name_capitalized}. {florence_caption}"
                        )

                # Keep the original capitalization - CLIP handles mixed case fine
                full_text = florence_caption

                logger.info(f"Embedding: using Florence-2 caption: {full_text}")

        # Fall back to tag-based approach if Florence didn't work
        if full_text is None:
            # Collect text from both character and picture
            texts = []
            if character:
                texts.extend(self._collect_text(character))
            if picture:
                texts.extend(self._collect_text(picture))

            texts = self._filter_texts(texts)
            logger.debug(f"Embedding: texts used for embedding (filtered): {texts}")
            if not texts:
                logger.error(
                    "Embedding: No text data for embedding. character=%s, picture=%s",
                    character,
                    picture,
                )
                raise ValueError("No text data for embedding.")

            logger.info(f"Embedding: tags going into description: {texts}")
            full_text = self._tag_naturaliser.tags_to_sentence(texts)
            full_text = full_text.lower()
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
