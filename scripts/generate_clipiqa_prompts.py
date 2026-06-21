"""Precompute the CLIP-IQA quality-probe prompt embeddings and bundle them.

The smart scorer adds an opinion-unaware perceptual-quality term computed from the CLIP
image embedding already stored on every picture:

    q = softmax(scale * [cos(img, good), cos(img, bad)])[0]

``good`` / ``bad`` are fixed text-prompt embeddings, so they are precomputed once here
and bundled as ``.npy`` files (mirroring ``data/anchors/builtin_good.npy``). That keeps
``SmartScoreUtils.calculate_smart_score_batch_numpy`` engine-free and deterministic at
scoring time — just two dot products. Re-run this whenever the CLIP model changes.

Usage:
    python -m scripts.generate_clipiqa_prompts        # writes the two .npy files
    python -m scripts.generate_clipiqa_prompts --probe # also prints a separation sanity check

The prompts are small ensembles (averaged, then renormalised) for stability. CLIP-IQA
quality depends on backbone and prompt pair; validate on a labelled sample before
trusting the term, and tune the prompts here if the separation is weak.
"""

import argparse
import pathlib

import numpy as np

from pixlstash.tagger_plugins.clip_service import ClipService

GOOD_PROMPTS = (
    "a high quality photo",
    "a sharp, clear, detailed photo",
    "a well-exposed, professional photo",
)
BAD_PROMPTS = (
    "a low quality photo",
    "a blurry, noisy photo",
    "a compressed, distorted, low-detail photo",
)

OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "pixlstash" / "data" / "anchors"
GOOD_PATH = OUT_DIR / "clipiqa_good.npy"
BAD_PATH = OUT_DIR / "clipiqa_bad.npy"


def _encode_mean(clip: ClipService, prompts) -> np.ndarray:
    """Encode a prompt ensemble, average the unit vectors, and renormalise."""
    vecs = []
    for prompt in prompts:
        emb = clip.encode_text(prompt)
        if emb is None:
            raise RuntimeError(f"CLIP failed to encode prompt: {prompt!r}")
        vecs.append(np.asarray(emb, dtype=np.float32))
    mean = np.mean(np.stack(vecs), axis=0)
    norm = np.linalg.norm(mean)
    if norm <= 0:
        raise RuntimeError("Encoded prompt mean has zero norm")
    return (mean / norm).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Print the cosine separation between the two prompt vectors.",
    )
    args = parser.parse_args()

    clip = ClipService(device="cpu")
    clip.ensure_ready()

    good_vec = _encode_mean(clip, GOOD_PROMPTS)
    bad_vec = _encode_mean(clip, BAD_PROMPTS)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    np.save(GOOD_PATH, good_vec)
    np.save(BAD_PATH, bad_vec)
    print(f"Wrote {GOOD_PATH} (shape {good_vec.shape})")
    print(f"Wrote {BAD_PATH} (shape {bad_vec.shape})")

    if args.probe:
        sep = float(np.dot(good_vec, bad_vec))
        print(f"good·bad cosine = {sep:.4f} (lower = better separated prompts)")


if __name__ == "__main__":
    main()
