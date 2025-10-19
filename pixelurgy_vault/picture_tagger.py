import argparse
import csv
import os
from pathlib import Path

import cv2
import numpy as np
import torch
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from PIL import Image
from tqdm import tqdm


import glob

from .logging import get_logger


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
MAX_DATA_LOADER_N_WORKERS = None
CAPTION_EXTENSION = ".txt"
GENERAL_THRESHOLD = 0.35
CHARACTER_THRESHOLD = 0.35
RECURSIVE = False
REMOVE_UNDERSCORE = False
DEBUG = False
UNDESIRED_TAGS = ""
FREQUENCY_TAGS = False
ONNX = True
APPEND_TAGS = False
USE_RATING_TAGS = True
USE_RATING_TAGS_AS_LAST_TAG = False
CHARACTER_TAGS_FIRST = False
ALWAYS_FIRST_TAGS = None
CAPTION_SEPARATOR = ", "
TAG_REPLACEMENT = None
CHARACTER_TAG_EXPAND = False

def preprocess_image(image):
    image = np.array(image)
    image = image[:, :, ::-1]  # RGB->BGR

    # pad to square
    size = max(image.shape[0:2])
    pad_x = size - image.shape[1]
    pad_y = size - image.shape[0]
    pad_l = pad_x // 2
    pad_t = pad_y // 2
    image = np.pad(image, ((pad_t, pad_y - pad_t), (pad_l, pad_x - pad_l), (0, 0)), mode="constant", constant_values=255)

    image = resize_image(image, image.shape[0], image.shape[1], IMAGE_SIZE, IMAGE_SIZE)

    image = image.astype(np.float32)
    return image

def resize_image(image, h_in, w_in, h_out, w_out):
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
            logger.error(f"Could not load image path / 画像を読み込めません: {img_path}, error: {e}")
            return None

        return (image, img_path)

class PictureTagger:
    def __init__(self, model_location=os.path.join(MODEL_DIR, DEFAULT_WD14_TAGGER_REPO.replace("/", "_")), force_download=False):
        self.model_location = model_location
        self.ensure_model_files(force_download=force_download)

    def ensure_model_files(self, force_download):
        # hf_hub_download
        # deprecated
        # https://github.com/toriato/stable-diffusion-webui-wd14-tagger/issues/22
        if not os.path.exists(self.model_location) or force_download:
            os.makedirs(self.model_location, exist_ok=True)
            logger.info(f"downloading wd14 tagger model from hf_hub. id: {DEFAULT_WD14_TAGGER_REPO}")
            # Always download ONNX model and selected_tags.csv
            from huggingface_hub import hf_hub_download
            # Download ONNX model
            onnx_model_path = os.path.join(self.model_location, "model.onnx")
            tags_csv_path = os.path.join(self.model_location, "selected_tags.csv")
            logger.info(f"Downloading ONNX model to {onnx_model_path}")
            hf_hub_download(repo_id=DEFAULT_WD14_TAGGER_REPO, filename="model.onnx", local_dir=self.model_location, force_download=True)
            logger.info(f"Downloading selected_tags.csv to {tags_csv_path}")
            hf_hub_download(repo_id=DEFAULT_WD14_TAGGER_REPO, filename="selected_tags.csv", local_dir=self.model_location, force_download=True)

    def glob_images_pathlib(self, path, recursive):
        pattern = '**/*' if recursive else '*'
        exts = ['png', 'jpg', 'jpeg', 'webp', 'bmp']
        files = []
        for ext in exts:
            files.extend(glob.glob(os.path.join(str(path), f'{pattern}.{ext}'), recursive=recursive))
        return files



    def collate_fn_remove_corrupted(self, batch):
        """Collate function that allows to remove corrupted examples in the
        dataloader. It expects that the dataloader returns 'None' when that occurs.
        The 'None's in the batch are removed.
        """
        # Filter out all the Nones (corrupted examples)
        batch = list(filter(lambda x: x is not None, batch))
        return batch

    def tag_training_directory(self, train_data_dir="."):
        onnx_path = f"{self.model_location}/model.onnx"
        logger.info("Running wd14 tagger with onnx")
        logger.info(f"loading onnx model: {onnx_path}")
        if not os.path.exists(onnx_path):
            raise Exception(
                f"onnx model not found: {onnx_path}, please redownload the model with --force_download"
            )
        if "OpenVINOExecutionProvider" in ort.get_available_providers():
            ort_sess = ort.InferenceSession(
                onnx_path,
                providers=(['OpenVINOExecutionProvider']),
                provider_options=[{'device_type' : "GPU", "precision": "FP32"}],
            )
        else:
            ort_sess = ort.InferenceSession(
                onnx_path,
                providers=(
                    ["CUDAExecutionProvider"] if "CUDAExecutionProvider" in ort.get_available_providers() else
                    ["ROCMExecutionProvider"] if "ROCMExecutionProvider" in ort.get_available_providers() else
                    ["CPUExecutionProvider"]
                ),
            )
        input_name = ort_sess.get_inputs()[0].name

        # label_names = pd.read_csv("2022_0000_0899_6549/selected_tags.csv")
        # ...existing code...

        with open(os.path.join(self.model_location, CSV_FILE), "r", encoding="utf-8") as f:
            reader = csv.reader(f)
            line = [row for row in reader]
            header = line[0]  # tag_id,name,category,count
            rows = line[1:]
        assert header[0] == "tag_id" and header[1] == "name" and header[2] == "category", f"unexpected csv format: {header}"

        rating_tags = [row[1] for row in rows[0:] if row[2] == "9"]
        general_tags = [row[1] for row in rows[0:] if row[2] == "0"]
        character_tags = [row[1] for row in rows[0:] if row[2] == "4"]

        # preprocess tags in advance
        if CHARACTER_TAG_EXPAND:
            for i, tag in enumerate(character_tags):
                if tag.endswith(")"):
                    # chara_name_(series) -> chara_name, series
                    # chara_name_(costume)_(series) -> chara_name_(costume), series
                    tags = tag.split("(")
                    character_tag = "(".join(tags[:-1])
                    if character_tag.endswith("_"):
                        character_tag = character_tag[:-1]
                    series_tag = tags[-1].replace(")", "")
                    character_tags[i] = character_tag + CAPTION_SEPARATOR + series_tag

        if REMOVE_UNDERSCORE:
            rating_tags = [tag.replace("_", " ") if len(tag) > 3 else tag for tag in rating_tags]
            general_tags = [tag.replace("_", " ") if len(tag) > 3 else tag for tag in general_tags]
            character_tags = [tag.replace("_", " ") if len(tag) > 3 else tag for tag in character_tags]

        if TAG_REPLACEMENT is not None:
            # escape , and ; in tag_replacement: wd14 tag names may contain , and ;
            escaped_tag_replacements = TAG_REPLACEMENT.replace("\\,", "@@@@").replace("\\;", "####")
            tag_replacements = escaped_tag_replacements.split(";")
            for tag_replacement in tag_replacements:
                tags = tag_replacement.split(",")  # source, target
                assert len(tags) == 2, f"tag replacement must be in the format of `source,target`: {TAG_REPLACEMENT}"

                source, target = [tag.replace("@@@@", ",").replace("####", ";") for tag in tags]
                logger.info(f"replacing tag: {source} -> {target}")

                if source in general_tags:
                    general_tags[general_tags.index(source)] = target
                elif source in character_tags:
                    character_tags[character_tags.index(source)] = target
                elif source in rating_tags:
                    rating_tags[rating_tags.index(source)] = target

        # ...existing code...
        train_data_dir_path = Path(train_data_dir)
        image_paths = self.glob_images_pathlib(train_data_dir_path, RECURSIVE)
        logger.info(f"found {len(image_paths)} images.")

        tag_freq = {}

        caption_separator = CAPTION_SEPARATOR
        stripped_caption_separator = caption_separator.strip()
        undesired_tags = UNDESIRED_TAGS.split(stripped_caption_separator)
        undesired_tags = set([tag.strip() for tag in undesired_tags if tag.strip() != ""])

        always_first_tags = None
        if ALWAYS_FIRST_TAGS is not None:
            always_first_tags = [tag for tag in ALWAYS_FIRST_TAGS.split(stripped_caption_separator) if tag.strip() != ""]

        def run_batch(path_imgs):
            imgs = np.array([im for _, im in path_imgs])

            probs = ort_sess.run(None, {input_name: imgs})[0]  # onnx output numpy
            probs = probs[: len(path_imgs)]

            for (image_path, _), prob in zip(path_imgs, probs):
                # Debug: print ONNX output scores, max/min, and tags above threshold
                print(f"[WD14 DEBUG] Image: {image_path}")
                print("[WD14 DEBUG] ONNX output scores (first 20):", prob[:20])
                print("[WD14 DEBUG] Max score:", np.max(prob), "Min score:", np.min(prob))
                # Show only tags above threshold, ordered by probability
                all_tags = list(zip(general_tags + character_tags, list(prob[4:4+len(general_tags)]) + list(prob[4+len(general_tags):])))
                all_tags_above = [(tag, val) for tag, val in all_tags if val >= min(GENERAL_THRESHOLD, CHARACTER_THRESHOLD)]
                all_tags_sorted = sorted(all_tags_above, key=lambda x: x[1], reverse=True)
                print("[WD14 DEBUG] Tags above threshold (ordered):")
                for tag, val in all_tags_sorted:
                    print(f"  {tag}: {val:.3f}")
                combined_tags = []
                rating_tag_text = ""
                character_tag_text = ""
                general_tag_text = ""

                # ...existing code...
                # First 4 labels are ratings, the rest are tags: pick any where prediction confidence >= threshold
                for i, p in enumerate(prob[4:]):
                    if i < len(general_tags) and p >= GENERAL_THRESHOLD:
                        tag_name = general_tags[i]

                        if tag_name not in undesired_tags:
                            tag_freq[tag_name] = tag_freq.get(tag_name, 0) + 1
                            general_tag_text += caption_separator + tag_name
                            combined_tags.append(tag_name)
                    elif i >= len(general_tags) and p >= CHARACTER_THRESHOLD:
                        tag_name = character_tags[i - len(general_tags)]

                        if tag_name not in undesired_tags:
                            tag_freq[tag_name] = tag_freq.get(tag_name, 0) + 1
                            character_tag_text += caption_separator + tag_name
                            if CHARACTER_TAGS_FIRST: # insert to the beginning
                                combined_tags.insert(0, tag_name)
                            else:
                                combined_tags.append(tag_name)

                # ...existing code...
                # First 4 labels are actually ratings: pick one with argmax
                if USE_RATING_TAGS or USE_RATING_TAGS_AS_LAST_TAG:
                    ratings_probs = prob[:4]
                    rating_index = ratings_probs.argmax()
                    found_rating = rating_tags[rating_index]

                    if found_rating not in undesired_tags:
                        tag_freq[found_rating] = tag_freq.get(found_rating, 0) + 1
                        rating_tag_text = found_rating
                        if USE_RATING_TAGS:
                            combined_tags.insert(0, found_rating) # insert to the beginning
                        else:
                            combined_tags.append(found_rating)

                # ...existing code...
                # Always put some tags at the beginning
                if always_first_tags is not None:
                    for tag in always_first_tags:
                        if tag in combined_tags:
                            combined_tags.remove(tag)
                            combined_tags.insert(0, tag)

                # ...existing code...
                if len(general_tag_text) > 0:
                    general_tag_text = general_tag_text[len(caption_separator) :]
                if len(character_tag_text) > 0:
                    character_tag_text = character_tag_text[len(caption_separator) :]

                caption_file = os.path.splitext(image_path)[0] + CAPTION_EXTENSION

                tag_text = caption_separator.join(combined_tags)

                if APPEND_TAGS:
                    # Check if file exists
                    if os.path.exists(caption_file):
                        with open(caption_file, "rt", encoding="utf-8") as f:
                            # Read file and remove new lines
                            existing_content = f.read().strip("\n")  # Remove newlines

                        # Split the content into tags and store them in a list
                        existing_tags = [tag.strip() for tag in existing_content.split(stripped_caption_separator) if tag.strip()]

                        # Check and remove repeating tags in tag_text
                        new_tags = [tag for tag in combined_tags if tag not in existing_tags]

                        # Create new tag_text
                        tag_text = caption_separator.join(existing_tags + new_tags)

                with open(caption_file, "wt", encoding="utf-8") as f:
                    f.write(tag_text + "\n")
                    if DEBUG:
                        logger.info("")
                        logger.info(f"{image_path}:")
                        logger.info(f"\tRating tags: {rating_tag_text}")
                        logger.info(f"\tCharacter tags: {character_tag_text}")
                        logger.info(f"\tGeneral tags: {general_tag_text}")

        # ...existing code...
        if MAX_DATA_LOADER_N_WORKERS is not None:
            dataset = ImageLoadingPrepDataset(image_paths)
            data = torch.utils.data.DataLoader(
                dataset,
                batch_size=BATCH_SIZE,
                shuffle=False,
                num_workers=MAX_DATA_LOADER_N_WORKERS,
                collate_fn=self.collate_fn_remove_corrupted,
                drop_last=False,
            )
        else:
            data = [[(None, ip)] for ip in image_paths]

        b_imgs = []
        for data_entry in tqdm(data, smoothing=0.0):
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
                        logger.error(f"Could not load image path / 画像を読み込めません: {image_path}, error: {e}")
                        continue
                b_imgs.append((image_path, image))

                if len(b_imgs) >= BATCH_SIZE:
                    b_imgs = [(str(image_path), image) for image_path, image in b_imgs]  # Convert image_path to string
                    run_batch(b_imgs)
                    b_imgs.clear()

        if len(b_imgs) > 0:
            b_imgs = [(str(image_path), image) for image_path, image in b_imgs]  # Convert image_path to string
            run_batch(b_imgs)

        if FREQUENCY_TAGS:
            sorted_tags = sorted(tag_freq.items(), key=lambda x: x[1], reverse=True)
            print("Tag frequencies:")
            for tag, freq in sorted_tags:
                print(f"{tag}: {freq}")

        logger.info("done!")
