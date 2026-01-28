import os
from pathlib import Path
import cv2
import argparse
import urllib.request
from ultralytics import YOLO

BODY_PART_MODELS = {
    "hand": {
        "model_path": "yolov8n-hand.pt",  # Download or specify your hand model
        "output_dir": "tmp/hand_crops",
        "label": "hand",
    },
    "face": {
        "model_path": "yolov8n-face.pt",  # Download or specify your face model
        "output_dir": "tmp/face_crops",
        "label": "face",
    },
}

FACE_KEYWORDS = {
    "face",
    "eye",
    "eyes",
    "blue eyes",
    "brown eyes",
    "green eyes",
    "mouth",
    "open mouth",
    "lips",
    "lipstick",
    "teeth",
    "sharp teeth",
    "tongue",
    "nose",
    "cheek",
    "mole",
    "mole on cheek",
    "freckles",
    "forehead",
    "forehead visible",
    "eyebrow",
    "eyebrows",
    "eyelash",
    "eyelashes",
    "ear",
    "ears",
    "earring",
    "earrings",
    "beard",
    "mustache",
    "facial hair",
    "chin",
    "flux chin",
    "smile",
    "grin",
    "closed eyes",
    "looking at the camera",
    "looking to the side",
    "looking at mirror",
    "hair",
    "braid",
    "single braid",
    "ponytail",
    "bun",
    "hair bun",
    "bangs",
    "fringe",
    "multicolored hair",
    "glasses",
    "sunglasses",
    "makeup",
    "malformed teeth",
    "waxy skin",
    "woman",
    "man",
}

HAND_KEYWORDS = {
    "hand",
    "hands",
    "hand on own stomach",
    "hands on own stomach",
    "hands on own face",
    "hands on own cheeks",
    "own hands together",
    "hands up",
    "hands in pockets",
    "cupping hands",
    "finger",
    "fingers",
    "fingernail",
    "fingernails",
    "nail",
    "nails",
    "nail polish",
    "ring",
    "bracelet",
    "watch",
    "wristwatch",
    "holding",
    "holding hands",
    "holding hand",
    "holding a book",
    "holding a phone",
    "holding a ball",
    "holding a strap",
    "holding cup",
    "holding food",
    "holding cigarette",
    "holding pen",
    "holding spoon",
    "holding umbrella",
    "holding leash",
    "palm",
    "wrist",
    "thumb",
    "digit",
    "digits",
    "missing digit",
    "extra digit",
    "fused fingers",
    "malformed hand",
    "waxy skin",
}

GENERIC_KEYWORDS = {
    "photo",
    "woman",
    "man",
    "indoors",
    "outdoors",
    "blurry background",
    "depth of field",
    "looking at the camera",
    "solo focus",
    "upper body",
    "smile",
    "grin",
    "waxy skin",
    "long hair",
    "brown hair",
    "blonde hair",
    "black hair",
}


def parse_tags_from_caption(caption_path: Path) -> list[str]:
    if not caption_path.exists():
        return []
    text = caption_path.read_text(encoding="utf-8", errors="ignore")
    parts = []
    for chunk in text.replace("\n", ",").split(","):
        tag = chunk.strip()
        if tag:
            parts.append(tag)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for tag in parts:
        key = tag.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(tag)
    return unique


def tag_matches_keywords(tag: str, keywords: set[str]) -> bool:
    t = tag.strip().lower()
    return any(keyword in t for keyword in keywords)


def split_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    face_tags = []
    hand_tags = []
    for tag in tags:
        if tag_matches_keywords(tag, FACE_KEYWORDS):
            face_tags.append(tag)
        if tag_matches_keywords(tag, HAND_KEYWORDS):
            hand_tags.append(tag)
    return face_tags, hand_tags


def filter_tags(tags: list[str], keywords: set[str]) -> list[str]:
    return [tag for tag in tags if tag_matches_keywords(tag, keywords)]


def merge_tags(primary: list[str], extra: list[str]) -> list[str]:
    seen = set()
    merged = []
    for tag in primary + extra:
        key = tag.strip().lower()
        if key in seen or not key:
            continue
        seen.add(key)
        merged.append(tag)
    return merged


def write_caption_for_crop(out_path: Path, tags: list[str]) -> None:
    caption_path = out_path.with_suffix(".txt")
    caption_path.write_text(", ".join(tags) + "\n", encoding="utf-8")


def ensure_tag(tags: list[str], required_tag: str) -> list[str]:
    if not tags:
        return [required_tag]
    lower_tags = {t.strip().lower() for t in tags}
    if required_tag.lower() in lower_tags:
        return tags
    return [required_tag] + tags


def ensure_tags(tags: list[str], required_tags: list[str]) -> list[str]:
    result = tags
    for required in required_tags:
        result = ensure_tag(result, required)
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Crop body parts (hand/face) from images in a folder using YOLOv8."
    )
    parser.add_argument(
        "--body_part",
        choices=BODY_PART_MODELS.keys(),
        default="hand",
        help="Body part to crop (hand or face)",
    )
    parser.add_argument("--input_dir", default="pictures", help="Input image directory")
    parser.add_argument(
        "--output_dir",
        default=None,
        help="Output directory for crops (overrides default)",
    )
    parser.add_argument(
        "--model_path", default=None, help="Path to YOLOv8 model (overrides default)"
    )
    args = parser.parse_args()

    config = BODY_PART_MODELS[args.body_part]
    input_dir = args.input_dir
    output_dir = args.output_dir or config["output_dir"]
    model_path = args.model_path or config["model_path"]
    label = config["label"]

    # URLs for pretrained models (replace with actual URLs for your models)
    MODEL_URLS = {
        "yolov8n-hand.pt": "https://huggingface.co/Bingsu/adetailer/resolve/main/hand_yolov8n.pt",
        "yolov8n-face.pt": "https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8n.pt",
    }

    def download_model_if_needed(path, url_map=None):
        filename = os.path.basename(path)
        if not os.path.exists(path):
            urls = (url_map or MODEL_URLS).get(filename)
            if not urls:
                raise FileNotFoundError(
                    f"Model file {filename} not found and no download URL is set."
                )
            if isinstance(urls, str):
                urls = [urls]
            last_error = None
            for url in urls:
                try:
                    print(f"Downloading {filename} from {url}...")
                    urllib.request.urlretrieve(url, path)
                    print(f"Downloaded {filename}.")
                    return
                except Exception as exc:
                    last_error = exc
                    print(f"Failed to download from {url}: {exc}")
            raise FileNotFoundError(
                f"Model file {filename} could not be downloaded. Last error: {last_error}"
            )

    os.makedirs(output_dir, exist_ok=True)
    download_model_if_needed(model_path)
    yolo_model = YOLO(model_path)

    def bboxes_overlap(box1, box2):
        x1_min, y1_min, x1_max, y1_max = box1
        x2_min, y2_min, x2_max, y2_max = box2
        return not (
            x1_max < x2_min or x2_max < x1_min or y1_max < y2_min or y2_max < y1_min
        )

    def merge_bboxes(boxes):
        if not boxes:
            return []
        merged = []
        used = [False] * len(boxes)
        for i, box in enumerate(boxes):
            if used[i]:
                continue
            x_min, y_min, x_max, y_max = box
            group = [box]
            used[i] = True
            for j in range(i + 1, len(boxes)):
                if used[j]:
                    continue
                if bboxes_overlap(box, boxes[j]):
                    group.append(boxes[j])
                    used[j] = True
            if len(group) > 1:
                # Merge all overlapping boxes
                x_min = min(b[0] for b in group)
                y_min = min(b[1] for b in group)
                x_max = max(b[2] for b in group)
                y_max = max(b[3] for b in group)
                merged.append((x_min, y_min, x_max, y_max))
            else:
                merged.append(box)
        return merged

    for img_path in Path(input_dir).glob("**/*"):
        if img_path.suffix.lower() not in [".jpg", ".jpeg", ".png", ".bmp", ".webp"]:
            continue
        caption_path = img_path.with_suffix(".txt")
        all_tags = parse_tags_from_caption(caption_path)
        face_tags, hand_tags = split_tags(all_tags)
        generic_tags = filter_tags(all_tags, GENERIC_KEYWORDS)
        image = cv2.imread(str(img_path))
        if image is None:
            print(f"Failed to read {img_path}")
            continue
        results = yolo_model(image)
        boxes = list(results[0].boxes.xyxy.cpu().numpy())
        n = len(boxes)
        if n == 0:
            continue
        if label == "hand":
            x_min = min(b[0] for b in boxes)
            y_min = min(b[1] for b in boxes)
            x_max = max(b[2] for b in boxes)
            y_max = max(b[3] for b in boxes)
            crop = image[int(y_min) : int(y_max), int(x_min) : int(x_max)]
            out_name = f"{img_path.stem}_hands{img_path.suffix}"
            out_path = os.path.join(output_dir, out_name)
            cv2.imwrite(out_path, crop)
            print(f"Saved {out_path}")
            fallback = ["hands"]
            hand_base = merge_tags(hand_tags or fallback, generic_tags)
            hand_caption = ensure_tags(hand_base, ["hands", "close-up"])
            write_caption_for_crop(Path(out_path), hand_caption)
        else:
            if label == "face":
                largest_box = max(
                    boxes,
                    key=lambda b: max(0, (b[2] - b[0])) * max(0, (b[3] - b[1])),
                )
                x_min, y_min, x_max, y_max = map(int, largest_box)
                crop = image[y_min:y_max, x_min:x_max]
                out_name = f"{img_path.stem}_face{img_path.suffix}"
                out_path = os.path.join(output_dir, out_name)
                cv2.imwrite(out_path, crop)
                print(f"Saved {out_path}")
                fallback = ["face"]
                face_base = merge_tags(face_tags or fallback, generic_tags)
                face_caption = ensure_tags(face_base, ["face", "close-up"])
                write_caption_for_crop(Path(out_path), face_caption)
            else:
                for i, box in enumerate(boxes):
                    x_min, y_min, x_max, y_max = map(int, box)
                    crop = image[y_min:y_max, x_min:x_max]
                    out_name = f"{img_path.stem}_{label}{i + 1}{img_path.suffix}"
                    out_path = os.path.join(output_dir, out_name)
                    cv2.imwrite(out_path, crop)
                    print(f"Saved {out_path}")


if __name__ == "__main__":
    main()
