#!/usr/bin/env python3
"""Evaluate the custom tagger against a ground-truth annotation set.

For each image in the evaluation folder, a sidecar `.txt` file with the same
stem must exist containing comma-separated ground-truth tags (the same format
produced by PixlVault's tag-export).

Model inference is run ONCE; thresholds are swept in post-processing, so even a
wide grid is fast.

The evaluation mirrors the production pipeline in tag_task.py:
  1. Full-image inference at the swept threshold.
  2. Face detection + expand-to-square crop inference using the custom tagger.
  3. For images that have face crops: whitelist quality tags (e.g. "pixelated")
     from the full-image pass are REPLACED by what the face crops confirmed.
     Images without face crops are left untouched.

Example usage:
    python scripts/evaluate_custom_tagger.py
    python scripts/evaluate_custom_tagger.py path/to/my_eval_set --force-cpu
    python scripts/evaluate_custom_tagger.py --min-thresh 0.2 --max-thresh 0.95 --step 0.05
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import torch

# ---------------------------------------------------------------------------
# Project root on path so pixlvault imports work when run as a script.
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from pixlvault.db_models.face import Face  # noqa: E402
from pixlvault.db_models.tag import DEFAULT_SMART_SCORE_PENALIZED_TAGS  # noqa: E402
from pixlvault.picture_tagger import (  # noqa: E402
    CUSTOM_TAGGER_THRESHOLD_FULL,
    QUALITY_CROP_TAG_WHITELIST,
    PictureTagger,
)
from pixlvault.tag_naturaliser import TagNaturaliser  # noqa: E402
from pixlvault.tasks.feature_extraction_task import CROP_EXPAND_SCALE  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif"}
DEFAULT_EVAL_DIR = _PROJECT_ROOT / "evaluation_set"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def discover_pairs(root: Path) -> list[tuple[Path, Path]]:
    """Recursively find (image_path, caption_path) pairs under *root*."""
    pairs = []
    for img_path in sorted(root.rglob("*")):
        if img_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        caption_path = img_path.with_suffix(".txt")
        if not caption_path.exists():
            print(f"  [skip] no caption file for {img_path.relative_to(root)}")
            continue
        pairs.append((img_path, caption_path))
    return pairs


def read_ground_truth(caption_path: Path) -> set[str]:
    """Read and normalise tags from a comma-separated caption file."""
    text = caption_path.read_text(encoding="utf-8").strip()
    if not text:
        return set()
    tags = set()
    for raw in text.split(","):
        natural = TagNaturaliser.get_natural_tag(raw.strip())
        if natural:
            tags.add(natural)
    return tags


def run_inference(
    tagger: PictureTagger, pairs: list[tuple[Path, Path]]
) -> dict[str, np.ndarray]:
    """Run the custom model on all images and return raw sigmoid probabilities.

    Returns:
        Dict mapping str(image_path) → np.ndarray of shape (n_labels,).
    """
    from PIL import Image

    # Ensure model is loaded.
    if tagger._custom_model is None:
        tagger._init_custom_tagger()

    image_size = tagger._custom_tagger_image_size_full
    transform = tagger._custom_transform_cache.get(image_size)
    if transform is None:
        transform = tagger._build_custom_transform(image_size)
        tagger._custom_transform_cache[image_size] = transform

    device = getattr(tagger, "_custom_device", tagger._device)
    batch_size = max(1, tagger._custom_tagger_batch)
    items = []
    for img_path, _ in pairs:
        try:
            img = Image.open(img_path).convert("RGB")
            items.append((str(img_path), img))
        except Exception as exc:
            print(f"  [warn] failed to load {img_path.name}: {exc}")

    all_probs: dict[str, np.ndarray] = {}
    for batch_start in range(0, len(items), batch_size):
        batch = items[batch_start : batch_start + batch_size]
        tensors = []
        valid_paths = []
        for path, img in batch:
            try:
                tensors.append(transform(img))
                valid_paths.append(path)
            except Exception as exc:
                print(f"  [warn] transform failed for {Path(path).name}: {exc}")

        if not tensors:
            continue

        inputs = torch.stack(tensors).to(device)
        with torch.inference_mode():
            logits = tagger._custom_model(inputs)
            probs = torch.sigmoid(logits).cpu().numpy()

        for path, prob in zip(valid_paths, probs):
            all_probs[path] = prob

        done = min(batch_start + batch_size, len(items))
        print(f"  Inference: {done}/{len(items)} images", end="\r", flush=True)

    print(f"  Inference: {len(items)}/{len(items)} images — done.          ")
    return all_probs


def detect_faces(
    img_paths: list[str],
    force_cpu: bool = False,
) -> dict[str, list]:
    """Detect face bounding boxes for every image using InsightFace.

    Returns:
        face_bboxes_by_path: dict mapping image path → list of [x1,y1,x2,y2] bboxes
    """
    import cv2

    face_bboxes_by_path: dict[str, list] = {}

    insightface_app = None
    try:
        from insightface.app import FaceAnalysis

        insightface_app = FaceAnalysis(
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        insightface_app.prepare(
            ctx_id=-1 if force_cpu else 0,
            det_thresh=0.25,
            det_size=(480, 480),
        )
        print("  Face detector (InsightFace) loaded.")
    except Exception as exc:
        print(f"  [warn] InsightFace unavailable, skipping face crops: {exc}")
        return face_bboxes_by_path

    face_expand_fraction = max(0.0, CROP_EXPAND_SCALE - 1.0)

    for i, path in enumerate(img_paths):
        img = cv2.imread(path)
        if img is None:
            continue
        h, w = img.shape[:2]
        try:
            faces = insightface_app.get(img)
            bboxes = []
            for face in faces:
                expanded = Face.expand_face_bbox(face.bbox, w, h, face_expand_fraction)
                if expanded:
                    bboxes.append(expanded)
            if bboxes:
                face_bboxes_by_path[path] = bboxes
        except Exception as exc:
            print(f"  [warn] face detection failed for {Path(path).name}: {exc}")

        print(f"  Detection: {i + 1}/{len(img_paths)} images", end="\r", flush=True)

    print(f"  Detection: {len(img_paths)}/{len(img_paths)} images — done.          ")
    n_face = sum(len(v) for v in face_bboxes_by_path.values())
    print(f"  Found {n_face} face crops across {len(face_bboxes_by_path)} images.")
    return face_bboxes_by_path


def run_quality_crop_tags(
    tagger: PictureTagger,
    img_paths: list[str],
    face_bboxes_by_path: dict[str, list],
) -> dict[str, set[str]]:
    """Mirror tag_task.py: expand each face bbox to a square at the custom tagger
    resolution, run the custom tagger, and return only QUALITY_CROP_TAG_WHITELIST
    tags per image path.

    Returns:
        Dict mapping image path → set of confirmed quality tags.
        Images without face bboxes are absent from the dict.
    """
    from PIL import Image as PILImage

    target = tagger.custom_tagger_image_size_full()
    quality_crop_tags_by_path: dict[str, set[str]] = {}

    items = []
    key_to_path: dict[str, str] = {}

    for path in img_paths:
        bboxes = face_bboxes_by_path.get(path, [])
        if not bboxes:
            continue
        try:
            img = PILImage.open(path).convert("RGB")
            w, h = img.size
        except Exception as exc:
            print(f"  [warn] could not open {Path(path).name} for crop pass: {exc}")
            continue

        for idx, bbox in enumerate(bboxes):
            if not bbox:
                continue
            expanded = PictureTagger._expand_bbox_to_square(bbox, w, h, target)
            crop = img.crop(expanded)
            key = f"{path}#face{idx}"
            items.append((key, crop))
            key_to_path[key] = path

    if not items:
        print("  No face crops available for quality inference.")
        return quality_crop_tags_by_path

    print(f"  Running quality crop inference on {len(items)} face crops...")
    raw_results = tagger.tag_quality_crops(items)

    # Accumulate quality tags per image path (union across all its face crops)
    for key, quality_tags in raw_results.items():
        src = key_to_path.get(key)
        if src:
            quality_crop_tags_by_path.setdefault(src, set()).update(quality_tags)

    n_images = len(quality_crop_tags_by_path)
    print(
        f"  Quality crop pass done — {n_images} images had at least one quality tag confirmed."
    )
    return quality_crop_tags_by_path


def apply_quality_crop_tags(
    full_tags_by_path: dict[str, set[str]],
    quality_crop_tags_by_path: dict[str, set[str]],
) -> dict[str, set[str]]:
    """Mirror tag_task.py merge logic.

    For images that have face crop results: strip QUALITY_CROP_TAG_WHITELIST tags
    from the full-image predictions, then add only what the crops confirmed.
    Images without face crops are returned unchanged.
    """
    result: dict[str, set[str]] = {}
    for path, full_tags in full_tags_by_path.items():
        crop_quality = quality_crop_tags_by_path.get(path)
        if crop_quality is not None:
            result[path] = (full_tags - set(QUALITY_CROP_TAG_WHITELIST)) | crop_quality
        else:
            result[path] = full_tags
    return result


def predict_at_threshold(
    probs: np.ndarray,
    labels: list[str],
    threshold: float,
    tag_filter: set[str] | None = None,
) -> set[str]:
    """Apply threshold, naturalize, and return predicted tag set.

    If *tag_filter* is provided, only tags present in that set are returned.
    """
    predicted = set()
    for label, p in zip(labels, probs):
        if p >= threshold:
            natural = TagNaturaliser.get_natural_tag(label)
            if natural:
                if tag_filter is None or natural in tag_filter:
                    predicted.add(natural)
    return predicted


def compute_metrics(
    all_probs: dict[str, np.ndarray],
    ground_truth: dict[str, set[str]],
    labels: list[str],
    threshold: float,
    tag_filter: set[str] | None = None,
    quality_crop_tags: dict[str, set[str]] | None = None,
) -> dict[str, float]:
    """Compute macro-averaged precision, recall, F1 at the given threshold.

    Args:
        quality_crop_tags: Per-image quality tags from the face crop pass.
            For images that have an entry here, QUALITY_CROP_TAG_WHITELIST tags are
            stripped from the full-image prediction and replaced with only what the
            crops confirmed — mirroring tag_task.py's ground-truth merge logic.
    """
    precisions, recalls, f1s = [], [], []
    tp_total = fp_total = fn_total = 0

    for path, gt in ground_truth.items():
        probs = all_probs.get(path)
        if probs is None:
            continue
        pred = predict_at_threshold(probs, labels, threshold, tag_filter)
        if quality_crop_tags is not None:
            crop_quality = quality_crop_tags.get(path)
            if crop_quality is not None:
                filtered_crop = (
                    crop_quality & tag_filter if tag_filter else crop_quality
                )
                pred = (pred - set(QUALITY_CROP_TAG_WHITELIST)) | filtered_crop

        tp = len(pred & gt)
        fp = len(pred - gt)
        fn = len(gt - pred)

        # Per-image precision / recall / F1
        prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if not pred else 0.0)
        rec = tp / (tp + fn) if (tp + fn) > 0 else (1.0 if not gt else 0.0)
        f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0

        precisions.append(prec)
        recalls.append(rec)
        f1s.append(f1)

        tp_total += tp
        fp_total += fp
        fn_total += fn

    macro_p = float(np.mean(precisions)) if precisions else 0.0
    macro_r = float(np.mean(recalls)) if recalls else 0.0
    macro_f1 = float(np.mean(f1s)) if f1s else 0.0

    n = len(precisions)
    micro_p = tp_total / (tp_total + fp_total) if (tp_total + fp_total) > 0 else 0.0
    micro_r = tp_total / (tp_total + fn_total) if (tp_total + fn_total) > 0 else 0.0
    micro_f1 = (
        (2 * micro_p * micro_r / (micro_p + micro_r))
        if (micro_p + micro_r) > 0
        else 0.0
    )

    return {
        "macro_p": macro_p,
        "macro_r": macro_r,
        "macro_f1": macro_f1,
        "micro_p": micro_p,
        "micro_r": micro_r,
        "micro_f1": micro_f1,
        "n_images": n,
        "fp_total": fp_total,
        "fn_total": fn_total,
        "avg_fp": fp_total / n if n > 0 else 0.0,
        "avg_fn": fn_total / n if n > 0 else 0.0,
    }


def print_results_table(rows: list[dict], best: dict[str, float]) -> None:
    """Print the threshold sweep results as a sorted table."""
    header = (
        f"{'Thresh':>7}  {'Macro-P':>8}  {'Macro-R':>8}  {'Macro-F1':>9}"
        f"  {'Micro-P':>8}  {'Micro-R':>8}  {'Micro-F1':>9}"
        f"  {'FP total':>8}  {'Avg FP':>6}  {'FN total':>8}  {'Avg FN':>6}"
    )
    sep = "-" * len(header)
    print(f"\n{sep}")
    print(header)
    print(sep)

    # Sort by macro-F1 descending
    rows_sorted = sorted(rows, key=lambda r: r["macro_f1"], reverse=True)

    for row in rows_sorted:
        t = row["threshold"]
        markers = []
        if abs(row["macro_f1"] - best["macro_f1"]) < 1e-9:
            markers.append("← best macro-F1")
        if abs(row["micro_f1"] - best["micro_f1"]) < 1e-9:
            markers.append("← best micro-F1")
        if abs(row["macro_p"] - best["macro_p"]) < 1e-9:
            markers.append("← best precision")
        if abs(row["macro_r"] - best["macro_r"]) < 1e-9:
            markers.append("← best recall")
        note = "  " + "  ".join(markers) if markers else ""
        print(
            f"{t:>7.2f}  {row['macro_p']:>8.4f}  {row['macro_r']:>8.4f}  "
            f"{row['macro_f1']:>9.4f}  {row['micro_p']:>8.4f}  {row['micro_r']:>8.4f}  "
            f"{row['micro_f1']:>9.4f}"
            f"  {row['fp_total']:>8d}  {row['avg_fp']:>6.2f}  {row['fn_total']:>8d}  {row['avg_fn']:>6.2f}"
            f"{note}"
        )

    print(sep)
    print("\nBest threshold by metric:")
    print(f"  Macro-F1:   {best['thresh_macro_f1']:.2f}  (F1 = {best['macro_f1']:.4f})")
    print(f"  Micro-F1:   {best['thresh_micro_f1']:.2f}  (F1 = {best['micro_f1']:.4f})")
    print(f"  Precision:  {best['thresh_macro_p']:.2f}  (P  = {best['macro_p']:.4f})")
    print(f"  Recall:     {best['thresh_macro_r']:.2f}  (R  = {best['macro_r']:.4f})")


def print_tag_errors_report(
    all_probs: dict[str, np.ndarray],
    ground_truth: dict[str, set[str]],
    labels: list[str],
    threshold: float,
    top_n: int = 20,
    tag_filter: set[str] | None = None,
    quality_crop_tags: dict[str, set[str]] | None = None,
    mode_label: str = "full image",
) -> None:
    """Print the most-missed (FN) and most-overestimated (FP) tags at the given threshold."""
    from collections import Counter

    fn_counts: Counter = Counter()
    fp_counts: Counter = Counter()
    gt_counts: Counter = Counter()  # how often each tag appears in ground truth
    pred_counts: Counter = Counter()  # how often each tag is predicted

    for path, gt in ground_truth.items():
        probs = all_probs.get(path)
        if probs is None:
            continue
        pred = predict_at_threshold(probs, labels, threshold, tag_filter)
        if quality_crop_tags is not None:
            crop_quality = quality_crop_tags.get(path)
            if crop_quality is not None:
                filtered_crop = (
                    crop_quality & tag_filter if tag_filter else crop_quality
                )
                pred = (pred - set(QUALITY_CROP_TAG_WHITELIST)) | filtered_crop
        for tag in gt - pred:
            fn_counts[tag] += 1
        for tag in pred - gt:
            fp_counts[tag] += 1
        for tag in gt:
            gt_counts[tag] += 1
        for tag in pred:
            pred_counts[tag] += 1

    n_images = len(ground_truth)

    print(
        f"\nTag error summary [{mode_label}] at threshold={threshold:.2f} (n={n_images} images):"
    )

    # --- Most missed (false negatives) ------------------------------------
    print(f"\n  Most frequently missed tags (false negatives) — top {top_n}:")
    header = f"    {'Tag':<40} {'Missed':>6}  {'Of GT':>6}  {'Miss rate':>9}"
    print(header)
    print("    " + "-" * (len(header) - 4))
    for tag, count in fn_counts.most_common(top_n):
        gt_c = gt_counts.get(tag, 0)
        rate = count / gt_c if gt_c else 0.0
        print(f"    {tag:<40} {count:>6}  {gt_c:>6}  {rate:>8.1%}")

    # --- Most over-predicted (false positives) ----------------------------
    print(f"\n  Most frequently over-predicted tags (false positives) — top {top_n}:")
    header = f"    {'Tag':<40} {'FP':>6}  {'Predicted':>9}  {'FP rate':>7}"
    print(header)
    print("    " + "-" * (len(header) - 4))
    for tag, count in fp_counts.most_common(top_n):
        pred_c = pred_counts.get(tag, 0)
        rate = count / pred_c if pred_c else 0.0
        print(f"    {tag:<40} {count:>6}  {pred_c:>9}  {rate:>6.1%}")


def print_crop_comparison(
    rows_full: list[dict],
    rows_crop: list[dict],
) -> None:
    """Print F1 comparison between full-image-only and full+face-crops at each threshold."""
    col_w = 14
    header = f"  {'Thresh':>7}  {'Full only':>{col_w}}  {'+ Face crops':>{col_w}}  {'Delta':>8}"
    sep = "  " + "-" * (len(header) - 2)
    print("\nCrop mode comparison — macro-F1:")
    print(sep)
    print(header)
    print(sep)

    best_full = max(r["macro_f1"] for r in rows_full)
    best_crop = max(r["macro_f1"] for r in rows_crop)

    for rf, rc in zip(rows_full, rows_crop):
        t = rf["threshold"]
        f1_full = rf["macro_f1"]
        f1_crop = rc["macro_f1"]
        delta = f1_crop - f1_full
        sign = "+" if delta >= 0 else ""
        star_full = "★" if abs(f1_full - best_full) < 1e-9 else " "
        star_crop = "★" if abs(f1_crop - best_crop) < 1e-9 else " "
        print(
            f"  {t:>7.2f}  {f1_full:>{col_w - 1}.4f}{star_full}  "
            f"{f1_crop:>{col_w - 1}.4f}{star_crop}  {sign}{delta:>7.4f}"
        )
    print(sep)

    best_full_row = max(rows_full, key=lambda r: r["macro_f1"])
    best_crop_row = max(rows_crop, key=lambda r: r["macro_f1"])
    print(
        f"\n  Best full-only:    thresh={best_full_row['threshold']:.2f}  "
        f"F1={best_full_row['macro_f1']:.4f}  P={best_full_row['macro_p']:.4f}  "
        f"R={best_full_row['macro_r']:.4f}  FP={best_full_row['fp_total']}  FN={best_full_row['fn_total']}"
    )
    print(
        f"  Best + face crops: thresh={best_crop_row['threshold']:.2f}  "
        f"F1={best_crop_row['macro_f1']:.4f}  P={best_crop_row['macro_p']:.4f}  "
        f"R={best_crop_row['macro_r']:.4f}  FP={best_crop_row['fp_total']}  FN={best_crop_row['fn_total']}"
    )


def print_per_image_report(
    all_probs: dict[str, np.ndarray],
    ground_truth: dict[str, set[str]],
    labels: list[str],
    threshold: float,
    root: Path,
) -> None:
    """Print per-image breakdown at the given (best) threshold."""
    print(f"\nPer-image breakdown at threshold={threshold:.2f}:")
    print("-" * 70)
    for path_str, gt in sorted(ground_truth.items()):
        probs = all_probs.get(path_str)
        if probs is None:
            continue
        pred = predict_at_threshold(probs, labels, threshold)
        tp = pred & gt
        fp = pred - gt
        fn = gt - pred
        prec = len(tp) / len(pred) if pred else 1.0
        rec = len(tp) / len(gt) if gt else 1.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        name = (
            Path(path_str).relative_to(root)
            if root in Path(path_str).parents
            else Path(path_str).name
        )
        print(f"  {name}")
        print(f"    P={prec:.3f}  R={rec:.3f}  F1={f1:.3f}")
        if fp:
            print(f"    False positives: {sorted(fp)}")
        if fn:
            print(f"    False negatives: {sorted(fn)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "eval_dir",
        nargs="?",
        default=str(DEFAULT_EVAL_DIR),
        help=f"Evaluation set directory (default: {DEFAULT_EVAL_DIR})",
    )
    parser.add_argument(
        "--force-cpu",
        action="store_true",
        help="Run inference on CPU even when a GPU is available",
    )
    parser.add_argument(
        "--min-thresh",
        type=float,
        default=0.20,
        help="Minimum threshold to evaluate (default: 0.20)",
    )
    parser.add_argument(
        "--max-thresh",
        type=float,
        default=0.95,
        help="Maximum threshold to evaluate (default: 0.95)",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=0.05,
        help="Threshold step size (default: 0.05)",
    )
    parser.add_argument(
        "--per-image",
        action="store_true",
        help="Print per-image breakdown at best macro-F1 threshold",
    )
    parser.add_argument(
        "--top-tags",
        type=int,
        default=20,
        help="Number of tags to show in the FP/FN tag error report (default: 20)",
    )
    parser.add_argument(
        "--all-tags",
        action="store_true",
        help="Evaluate all tags instead of only smart-score-penalized anomaly tags (default: anomaly tags only)",
    )
    parser.add_argument(
        "--no-face-crops",
        action="store_true",
        help="Skip face detection and the quality crop pass",
    )
    args = parser.parse_args()

    eval_root = Path(args.eval_dir).resolve()
    if not eval_root.exists():
        print(f"ERROR: evaluation directory not found: {eval_root}")
        sys.exit(1)

    print(f"Evaluation set: {eval_root}")

    # -----------------------------------------------------------------------
    # Build tag filter
    # -----------------------------------------------------------------------
    if args.all_tags:
        tag_filter: set[str] | None = None
        print("Tag filter: all tags")
    else:
        tag_filter = {
            TagNaturaliser.get_natural_tag(t)
            for t in DEFAULT_SMART_SCORE_PENALIZED_TAGS
            if TagNaturaliser.get_natural_tag(t)
        }
        print(
            f"Tag filter: {len(tag_filter)} smart-score-penalized anomaly tags  (use --all-tags to disable)"
        )

    # -----------------------------------------------------------------------
    # Discover images + captions
    # -----------------------------------------------------------------------
    print("Discovering annotated images...")
    pairs = discover_pairs(eval_root)
    if not pairs:
        print(
            "ERROR: no (image, caption) pairs found. Each image needs a .txt sidecar file."
        )
        sys.exit(1)
    print(f"Found {len(pairs)} annotated images.")

    # -----------------------------------------------------------------------
    # Build ground truth
    # -----------------------------------------------------------------------
    ground_truth: dict[str, set[str]] = {}
    for img_path, caption_path in pairs:
        gt = read_ground_truth(caption_path)
        if gt:
            ground_truth[str(img_path)] = gt
        else:
            print(f"  [skip] empty caption file: {caption_path.name}")

    if not ground_truth:
        print("ERROR: all caption files are empty.")
        sys.exit(1)

    # Apply tag filter to ground truth — restrict each image's GT to the
    # penalized subset. Images with no penalized GT tags are kept because the
    # model may still produce false positives for them.
    if tag_filter is not None:
        ground_truth = {path: gt & tag_filter for path, gt in ground_truth.items()}
        n_without_gt = sum(1 for gt in ground_truth.values() if not gt)
        if n_without_gt:
            print(
                f"  [info] {n_without_gt} images have no penalized tags in ground truth "
                f"(kept — model may still produce false positives for them)."
            )

    all_tags_in_gt = set().union(*ground_truth.values())
    print(f"Unique ground-truth tags across evaluation set: {len(all_tags_in_gt)}")

    # -----------------------------------------------------------------------
    # Initialise tagger
    # -----------------------------------------------------------------------
    print("Loading custom tagger model...")
    if args.force_cpu:
        PictureTagger.FORCE_CPU = True

    tagger = PictureTagger(silent=True)
    if not tagger._use_custom_tagger:
        print(
            "ERROR: custom tagger model not found and could not be downloaded from HuggingFace."
        )
        sys.exit(1)

    if tagger._custom_model is None:
        tagger._init_custom_tagger()

    labels: list[str] = tagger._custom_labels
    print(
        f"Custom tagger loaded — {len(labels)} labels, device={tagger._custom_device}"
    )

    # Warn about ground-truth tags that the model has never seen
    known = {TagNaturaliser.get_natural_tag(lbl) for lbl in labels}
    unknown_gt = all_tags_in_gt - known
    if unknown_gt:
        print(
            f"  [warn] {len(unknown_gt)} ground-truth tag(s) not in the model vocabulary:"
        )
        for t in sorted(unknown_gt):
            print(f"    {t!r}")

    # -----------------------------------------------------------------------
    # Run full-image inference (once)
    # -----------------------------------------------------------------------
    print("Running full-image inference...")
    valid_pairs = [(p, c) for p, c in pairs if str(p) in ground_truth]
    all_probs = run_inference(tagger, valid_pairs)

    if not all_probs:
        print("ERROR: no inference results. Check that images are readable.")
        sys.exit(1)

    # -----------------------------------------------------------------------
    # Face detection + quality crop pass (mirrors tag_task.py)
    # -----------------------------------------------------------------------
    quality_crop_tags: dict[str, set[str]] | None = None
    if not args.no_face_crops:
        print("\nRunning face detection + quality crop pass...")
        face_bboxes_by_path = detect_faces(
            list(all_probs.keys()), force_cpu=args.force_cpu
        )
        if face_bboxes_by_path:
            quality_crop_tags = run_quality_crop_tags(
                tagger, list(all_probs.keys()), face_bboxes_by_path
            )
            whitelist_in_filter = (
                set(QUALITY_CROP_TAG_WHITELIST) & tag_filter
                if tag_filter
                else set(QUALITY_CROP_TAG_WHITELIST)
            )
            print(
                f"  Quality crop whitelist tags relevant to filter: {sorted(whitelist_in_filter)}"
            )
        else:
            print("  No faces detected; quality crop pass skipped.")

    # -----------------------------------------------------------------------
    # Threshold sweep
    # -----------------------------------------------------------------------
    thresholds = np.arange(args.min_thresh, args.max_thresh + 1e-9, args.step)
    thresholds = [round(float(t), 4) for t in thresholds]

    print(
        f"\nSweeping {len(thresholds)} thresholds from {thresholds[0]:.2f} to {thresholds[-1]:.2f}..."
    )

    rows_full: list[dict] = []
    rows_crop: list[dict] = []

    for t in thresholds:
        m_full = compute_metrics(all_probs, ground_truth, labels, t, tag_filter)
        rows_full.append({"threshold": t, **m_full})
        if quality_crop_tags is not None:
            m_crop = compute_metrics(
                all_probs, ground_truth, labels, t, tag_filter, quality_crop_tags
            )
            rows_crop.append({"threshold": t, **m_crop})
        print(f"  threshold={t:.2f}  full-F1={m_full['macro_f1']:.4f}", end="\r")

    print(" " * 70, end="\r")

    # -----------------------------------------------------------------------
    # Find bests and print main table (full-image mode)
    # -----------------------------------------------------------------------
    rows = rows_full
    best_macro_f1_row = max(rows, key=lambda r: r["macro_f1"])
    best_micro_f1_row = max(rows, key=lambda r: r["micro_f1"])
    best_macro_p_row = max(rows, key=lambda r: r["macro_p"])
    best_macro_r_row = max(rows, key=lambda r: r["macro_r"])

    best = {
        "macro_f1": best_macro_f1_row["macro_f1"],
        "micro_f1": best_micro_f1_row["micro_f1"],
        "macro_p": best_macro_p_row["macro_p"],
        "macro_r": best_macro_r_row["macro_r"],
        "thresh_macro_f1": best_macro_f1_row["threshold"],
        "thresh_micro_f1": best_micro_f1_row["threshold"],
        "thresh_macro_p": best_macro_p_row["threshold"],
        "thresh_macro_r": best_macro_r_row["threshold"],
    }

    print("\n=== Full-image only ===")
    print_results_table(rows_full, best)

    # -----------------------------------------------------------------------
    # Crop comparison table (if face crops were run)
    # -----------------------------------------------------------------------
    if rows_crop:
        best_crop_row = max(rows_crop, key=lambda r: r["macro_f1"])
        best_crop = {
            "macro_f1": best_crop_row["macro_f1"],
            "micro_f1": max(rows_crop, key=lambda r: r["micro_f1"])["micro_f1"],
            "macro_p": max(rows_crop, key=lambda r: r["macro_p"])["macro_p"],
            "macro_r": max(rows_crop, key=lambda r: r["macro_r"])["macro_r"],
            "thresh_macro_f1": best_crop_row["threshold"],
            "thresh_micro_f1": max(rows_crop, key=lambda r: r["micro_f1"])["threshold"],
            "thresh_macro_p": max(rows_crop, key=lambda r: r["macro_p"])["threshold"],
            "thresh_macro_r": max(rows_crop, key=lambda r: r["macro_r"])["threshold"],
        }
        print("\n=== Full image + face quality crops ===")
        print_results_table(rows_crop, best_crop)
        print_crop_comparison(rows_full, rows_crop)

    # -----------------------------------------------------------------------
    # Tag error reports
    # -----------------------------------------------------------------------
    best_thresh = best["thresh_macro_f1"]
    print_tag_errors_report(
        all_probs,
        ground_truth,
        labels,
        best_thresh,
        top_n=args.top_tags,
        tag_filter=tag_filter,
        mode_label="full image",
    )

    if rows_crop:
        best_crop_thresh = max(rows_crop, key=lambda r: r["macro_f1"])["threshold"]
        print_tag_errors_report(
            all_probs,
            ground_truth,
            labels,
            best_crop_thresh,
            top_n=args.top_tags,
            tag_filter=tag_filter,
            quality_crop_tags=quality_crop_tags,
            mode_label="full image + face quality crops",
        )

    if args.per_image:
        print_per_image_report(
            all_probs,
            ground_truth,
            labels,
            best_thresh,
            eval_root,
        )

    # -----------------------------------------------------------------------
    # Current defaults comparison
    # -----------------------------------------------------------------------
    current = next(
        (
            r
            for r in rows_full
            if abs(r["threshold"] - CUSTOM_TAGGER_THRESHOLD_FULL) < 1e-9
        ),
        None,
    )
    if current:
        print(f"\nCurrent default threshold ({CUSTOM_TAGGER_THRESHOLD_FULL:.2f}):")
        print(
            f"  Macro-F1={current['macro_f1']:.4f}  "
            f"Macro-P={current['macro_p']:.4f}  Macro-R={current['macro_r']:.4f}"
        )
        print(
            f"  FP total={current['fp_total']}  avg per image={current['avg_fp']:.2f}  "
            f"FN total={current['fn_total']}  avg per image={current['avg_fn']:.2f}"
        )
        delta_f1 = best["macro_f1"] - current["macro_f1"]
        if delta_f1 > 0.001:
            print(
                f"  → Setting threshold to {best['thresh_macro_f1']:.2f} would "
                f"improve macro-F1 by +{delta_f1:.4f}"
            )
        else:
            print("  → Current threshold is already at or near optimal for macro-F1.")

    print()


if __name__ == "__main__":
    main()
