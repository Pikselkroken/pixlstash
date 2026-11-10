"""Time Florence-2 on Apple Metal (fp16) against the CPU (fp32) it is held on.

Drives :class:`Florence2Service`'s own load, caption and detection paths, so
the numbers are the ones the app would see. For each variant it loads the
model on the CPU in fp32 (what ships), then on ``mps`` in fp16 (the
checkpoint's own dtype, so no cast), runs captions and ``<OD>`` detection over
the same pictures on each, and prints ms/picture and how many outputs match.

Run on an Apple Silicon Mac with PixlStash installed (issue #1786):

    python scripts/bench_florence_mps.py ~/Pictures/some-folder --count 12
"""

import argparse
import os
import platform
import time
from pathlib import Path

# Before transformers loads anything: one loader thread on Metal (see
# docs/apple-metal-thread-safety.md, cause 1).
os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")

import torch  # noqa: E402
import transformers  # noqa: E402

from pixlstash.tagger_plugins.florence2 import (  # noqa: E402
    FLORENCE_MODEL_VARIANTS,
    Florence2Service,
)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def run(service, task, paths, batch_size):
    """Return ({path: output}, seconds) for *task* over *paths* in batches."""
    outputs = {}
    start = time.perf_counter()
    for i in range(0, len(paths), batch_size):
        chunk = paths[i : i + batch_size]
        if task == "caption":
            outputs.update(service.generate_captions_batch(chunk))
        else:
            outputs.update(service.detect_objects(chunk))
    return outputs, time.perf_counter() - start


def measure(variant, device, dtype, paths, batch_size):
    service = Florence2Service(str(device))
    service.set_model_variant(variant)
    load_start = time.perf_counter()
    service._load_model(device, dtype)
    print(
        f"  loaded {variant} on {device} {dtype} in "
        f"{time.perf_counter() - load_start:.1f} s",
        flush=True,
    )
    results = {}
    for task in ("caption", "detect"):
        run(service, task, paths[:1], 1)  # warm-up: Metal compiles its kernels
        outputs, seconds = run(service, task, paths, batch_size)
        if service._model_device.type != device.type:
            raise SystemExit(
                f"{variant} {task} fell back to {service._model_device}: "
                f"{service._last_fallback_reason}"
            )
        results[task] = (outputs, seconds * 1000 / len(paths))
        print(f"  {task}: {results[task][1]:.0f} ms/picture", flush=True)
    service.unload()
    return results


def boxes_close(a, b, tolerance_px):
    """True when two detection lists name the same labels in nearly the same boxes."""
    if len(a) != len(b):
        return False
    for (label_a, box_a, _), (label_b, box_b, _) in zip(a, b):
        if label_a != label_b:
            return False
        if max(abs(x - y) for x, y in zip(box_a, box_b)) > tolerance_px:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="Folder of still pictures")
    parser.add_argument("--count", type=int, default=12, help="Pictures to use")
    parser.add_argument(
        "--variants", nargs="+", default=list(FLORENCE_MODEL_VARIANTS), metavar="V"
    )
    parser.add_argument("--cpu-batch", type=int, default=2)
    parser.add_argument("--mps-batch", type=int, default=4)
    parser.add_argument("--box-tolerance", type=int, default=4, help="Pixels")
    args = parser.parse_args()

    if not torch.backends.mps.is_available():
        raise SystemExit("No Metal device: run this on an Apple Silicon Mac.")
    paths = sorted(
        str(p) for p in args.folder.iterdir() if p.suffix.lower() in IMAGE_EXTS
    )[: args.count]
    if not paths:
        raise SystemExit(f"No pictures in {args.folder}")

    print(
        f"{platform.platform()} {platform.machine()}, torch {torch.__version__}, "
        f"transformers {transformers.__version__}, {len(paths)} pictures, "
        f"batch cpu {args.cpu_batch} / mps {args.mps_batch}\n"
    )
    rows = []
    for variant in args.variants:
        print(variant)
        cpu = measure(
            variant, torch.device("cpu"), torch.float32, paths, args.cpu_batch
        )
        mps = measure(
            variant, torch.device("mps"), torch.float16, paths, args.mps_batch
        )
        for task in ("caption", "detect"):
            (cpu_out, cpu_ms), (mps_out, mps_ms) = cpu[task], mps[task]
            if task == "caption":
                same = sum(cpu_out.get(p) == mps_out.get(p) for p in paths)
            else:
                same = sum(
                    boxes_close(
                        cpu_out.get(p, []), mps_out.get(p, []), args.box_tolerance
                    )
                    for p in paths
                )
            rows.append((variant, task, cpu_ms, mps_ms, same))
            for p in paths:
                if cpu_out.get(p) != mps_out.get(p):
                    print(f"  {task} differs on {Path(p).name}:")
                    print(f"    cpu fp32: {cpu_out.get(p)}")
                    print(f"    mps fp16: {mps_out.get(p)}")
        print()

    print("| variant | task | CPU fp32 ms/pic | MPS fp16 ms/pic | speed-up | match |")
    print("|---|---|---|---|---|---|")
    for variant, task, cpu_ms, mps_ms, same in rows:
        print(
            f"| {variant} | {task} | {cpu_ms:.0f} | {mps_ms:.0f} | "
            f"{cpu_ms / mps_ms:.2f}x | {same}/{len(paths)} |"
        )


if __name__ == "__main__":
    main()
