"""Generate built-in CLIP anchor embeddings from reference images.

Reads  pictures/good/Good*.{jpg,png,jpeg,webp}  → pixlvault/data/anchors/builtin_good.npy
Reads  pictures/bad/Bad*.{jpg,png,jpeg,webp}     → pixlvault/data/anchors/builtin_bad.npy

Each .npy file is shape (N, 512) float32 – one row per image.

Run from the repository root (model weights are downloaded automatically the
first time):
    python scripts/generate_anchor_embeddings.py

Add --cpu to force CPU inference (slower but no GPU needed):
    python scripts/generate_anchor_embeddings.py --cpu
"""

import argparse
import pathlib
import sys

import numpy as np
import open_clip
import torch
from PIL import Image

# Must match the CLIP model used for all image_embedding blobs in the DB.
CLIP_MODEL_NAME = "ViT-B-32"
CLIP_MODEL_WEIGHTS = "laion2b_s34b_b79k"
EMBEDDING_DIM = 512

REPO_ROOT = pathlib.Path(__file__).parent.parent
GOOD_DIR = REPO_ROOT / "pictures" / "good"
BAD_DIR = REPO_ROOT / "pictures" / "bad"
OUT_DIR = REPO_ROOT / "pixlvault" / "data" / "anchors"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def load_images(directory: pathlib.Path, pattern: str) -> list[pathlib.Path]:
    """Return sorted list of image files matching glob pattern in directory."""
    if not directory.is_dir():
        return []
    files = sorted(p for p in directory.glob(pattern) if p.suffix.lower() in IMAGE_EXTS)
    return files


def embed_images(
    paths: list[pathlib.Path],
    model,
    preprocess,
    device: torch.device,
    batch_size: int = 16,
) -> np.ndarray:
    """Embed a list of image paths using CLIP; returns (N, dim) float32 array."""
    embeddings = []
    for i in range(0, len(paths), batch_size):
        batch_paths = paths[i : i + batch_size]
        batch_tensors = []
        for p in batch_paths:
            try:
                img = Image.open(p).convert("RGB")
                batch_tensors.append(preprocess(img))
            except Exception as e:
                print(f"  WARNING: could not load {p.name}: {e}", file=sys.stderr)
        if not batch_tensors:
            continue
        tensor = torch.stack(batch_tensors).to(device)
        with torch.no_grad():
            features = model.encode_image(tensor)
            features = features / features.norm(dim=-1, keepdim=True)
        embeddings.append(features.cpu().float().numpy())
    if not embeddings:
        return np.zeros((0, EMBEDDING_DIM), dtype=np.float32)
    return np.concatenate(embeddings, axis=0)


def main():
    parser = argparse.ArgumentParser(
        description="Generate built-in CLIP anchor embeddings."
    )
    parser.add_argument("--cpu", action="store_true", help="Force CPU inference")
    args = parser.parse_args()

    device = (
        torch.device("cpu")
        if args.cpu
        else (
            torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")
        )
    )
    print(f"Using device: {device}")
    print(f"Loading CLIP model {CLIP_MODEL_NAME} / {CLIP_MODEL_WEIGHTS} …")
    model, _, preprocess = open_clip.create_model_and_transforms(
        CLIP_MODEL_NAME, pretrained=CLIP_MODEL_WEIGHTS
    )
    model = model.to(device).eval()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # --- Good anchors ---
    good_paths = load_images(GOOD_DIR, "Good*")
    if not good_paths:
        print(f"ERROR: No Good*.jpg/png files found in {GOOD_DIR}", file=sys.stderr)
        sys.exit(1)
    print(f"\nEmbedding {len(good_paths)} good-anchor images:")
    for p in good_paths:
        print(f"  {p.name}")
    good_emb = embed_images(good_paths, model, preprocess, device)
    good_out = OUT_DIR / "builtin_good.npy"
    np.save(good_out, good_emb)
    print(f"\nSaved → {good_out}  shape={good_emb.shape}")

    # --- Bad anchors ---
    bad_paths = load_images(BAD_DIR, "Bad*")
    if not bad_paths:
        print(f"ERROR: No Bad*.jpg/png files found in {BAD_DIR}", file=sys.stderr)
        sys.exit(1)
    print(f"\nEmbedding {len(bad_paths)} bad-anchor images:")
    for p in bad_paths:
        print(f"  {p.name}")
    bad_emb = embed_images(bad_paths, model, preprocess, device)
    bad_out = OUT_DIR / "builtin_bad.npy"
    np.save(bad_out, bad_emb)
    print(f"Saved → {bad_out}  shape={bad_emb.shape}")

    print("\nDone. Commit pixlvault/data/anchors/builtin_good.npy and builtin_bad.npy.")


if __name__ == "__main__":
    main()
