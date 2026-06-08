#!/usr/bin/env python3
"""Build the Python runtime embedded in the PixlStash desktop installer.

Unlike the old hosted "compute backend" tarballs, this runtime is **bundled into
the installer** (electron-builder ``extraResources``) and ships a fully-working
CPU env on Windows/Linux, or a Metal env on macOS (the default PyPI macOS torch
includes MPS). GPU acceleration (CUDA/ROCm) is *not* baked in — the desktop app
adds it on first use as a PYTHONPATH overlay by pip-installing the heavy wheels
straight from PyPI / PyTorch. So we host nothing.

This produces ``<output-dir>/python`` (a relocatable standalone CPython with the
``pixlstash`` wheel + all deps + CPU/Metal torch) and ``<output-dir>/runtime.json``
recording the pinned torch/torchvision/onnxruntime versions the GPU overlay must
match. Run on a **native runner** so the platform wheels are correct.

Example:
    python scripts/build_desktop_runtime.py \
        --wheel dist/pixlstash-1.6.0-py3-none-any.whl \
        --os linux --arch x64 --accel cpu \
        --output-dir electron/resources
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# A pinned python-build-standalone release. Bump deliberately; the tag and the
# CPython version must both exist as a published asset.
PBS_RELEASE = "20250106"
PYTHON_VERSION = "3.12.8"

# (os, arch) -> python-build-standalone target triple for the install_only build.
PBS_TRIPLES = {
    ("linux", "x64"): "x86_64-unknown-linux-gnu",
    ("linux", "arm64"): "aarch64-unknown-linux-gnu",
    ("mac", "x64"): "x86_64-apple-darwin",
    ("mac", "arm64"): "aarch64-apple-darwin",
    ("win", "x64"): "x86_64-pc-windows-msvc",
}

# torch install source per bundled accelerator. ``None`` => default PyPI (macOS
# wheels already include Metal/MPS). Mirrors TORCH_INDEX in electron/src/config.ts.
TORCH_INDEX = {
    "cpu": "https://download.pytorch.org/whl/cpu",
    "metal": None,
}

# onnxruntime flavour bundled per accelerator (always the CPU build; the GPU
# build is added on demand by the desktop app).
ONNX_PACKAGE = {
    "cpu": "onnxruntime",
    "metal": "onnxruntime",
}


def log(msg: str) -> None:
    print(f"[build-runtime] {msg}", flush=True)


def pbs_asset_name(triple: str) -> str:
    return f"cpython-{PYTHON_VERSION}+{PBS_RELEASE}-{triple}-install_only.tar.gz"


def pbs_asset_url(triple: str) -> str:
    return (
        "https://github.com/astral-sh/python-build-standalone/releases/"
        f"download/{PBS_RELEASE}/{pbs_asset_name(triple)}"
    )


def download(url: str, dest: Path) -> None:
    log(f"downloading {url}")
    with urllib.request.urlopen(url) as resp, open(dest, "wb") as out:  # noqa: S310
        shutil.copyfileobj(resp, out)


def fetch_standalone_python(triple: str, dest_dir: Path, cache_dir: Path) -> Path:
    """Extract standalone CPython into ``dest_dir/python``, caching the tarball.

    The python-build-standalone archive is pinned by version+release in its
    filename, so a cached copy is always valid for the same pins; we reuse it
    across builds instead of re-downloading ~30 MB every time.
    """
    archive = cache_dir / pbs_asset_name(triple)
    if archive.is_file() and archive.stat().st_size > 0:
        log(f"using cached CPython {archive}")
    else:
        cache_dir.mkdir(parents=True, exist_ok=True)
        download(pbs_asset_url(triple), archive)
    log("extracting standalone CPython")
    with tarfile.open(archive) as tf:
        tf.extractall(dest_dir)  # noqa: S202 — trusted upstream artifact
    python_dir = dest_dir / "python"
    if not python_dir.is_dir():
        raise SystemExit(f"expected {python_dir} after extraction")
    return python_dir


def interpreter(python_dir: Path, target_os: str) -> Path:
    if target_os == "win":
        return python_dir / "python.exe"
    return python_dir / "bin" / "python3"


def pip_install(py: Path, args: list[str], cache_dir: Path) -> None:
    # A persistent pip cache (not --no-cache-dir) so the multi-hundred-MB torch
    # wheels are downloaded once and reused on every later build. The cache is
    # separate from the installed env, so this never bloats the shipped runtime.
    cmd = [
        str(py),
        "-m",
        "pip",
        "install",
        "--cache-dir",
        str(cache_dir / "pip"),
        *args,
    ]
    log("pip install " + " ".join(args))
    subprocess.run(cmd, check=True)


def populate_env(py: Path, wheel: Path, accel: str, cache_dir: Path) -> None:
    pip_install(py, ["--upgrade", "pip", "setuptools", "wheel"], cache_dir)

    # 1. torch/torchvision FIRST from the accelerator-specific index, so the
    #    subsequent wheel install sees the requirement already satisfied and
    #    does not pull a different build over it.
    torch_args = ["torch", "torchvision"]
    index = TORCH_INDEX[accel]
    if index:
        torch_args += ["--index-url", index]
    pip_install(py, torch_args, cache_dir)

    # 2. The matching onnxruntime flavour (CPU; GPU is added on demand).
    pip_install(py, [ONNX_PACKAGE[accel]], cache_dir)

    # 3. The PixlStash wheel + remaining deps from PyPI.
    pip_install(py, [str(wheel)], cache_dir)

    # 4. The spaCy English model used by the description pipeline.
    log("downloading spaCy en_core_web_sm")
    subprocess.run([str(py), "-m", "spacy", "download", "en_core_web_sm"], check=True)


def installed_version(py: Path, dist: str) -> str:
    out = subprocess.run(
        [str(py), "-c", f"import importlib.metadata as m; print(m.version('{dist}'))"],
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


def strip_env(python_dir: Path) -> None:
    """Drop caches and test trees to shrink the installer."""
    log("stripping caches/tests")
    removed = 0
    for root, dirs, _files in os.walk(python_dir):
        for d in list(dirs):
            if d in {"__pycache__", "tests", "test"}:
                shutil.rmtree(Path(root) / d, ignore_errors=True)
                dirs.remove(d)
                removed += 1
    log(f"removed {removed} cache/test directories")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wheel", required=True, type=Path)
    ap.add_argument("--os", required=True, choices=["win", "mac", "linux"])
    ap.add_argument("--arch", required=True, choices=["x64", "arm64"])
    ap.add_argument(
        "--accel",
        required=True,
        choices=["cpu", "metal"],
        help="Bundled accelerator: 'cpu' on Windows/Linux, 'metal' on macOS.",
    )
    ap.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Destination (e.g. electron/resources); receives python/ and runtime.json.",
    )
    ap.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / ".build-cache",
        help="Holds the cached CPython tarball + pip download cache, reused "
        "across builds (default: <repo>/.build-cache). Cache this path in CI.",
    )
    ap.add_argument(
        "--reuse-env",
        action="store_true",
        help="Fast path: reinstall ONLY the PixlStash wheel into the existing "
        "env, skipping the CPython download and the torch/onnxruntime install. "
        "For quick local iteration on app code; do a full build (omit this) when "
        "dependencies change.",
    )
    args = ap.parse_args()

    if not args.wheel.is_file():
        raise SystemExit(f"wheel not found: {args.wheel}")
    triple = PBS_TRIPLES.get((args.os, args.arch))
    if not triple:
        raise SystemExit(f"unsupported os/arch: {args.os}/{args.arch}")

    out = args.output_dir
    cache_dir = args.cache_dir
    python_dir = out / "python"

    if args.reuse_env and python_dir.is_dir():
        # Only the app changed: overwrite the pixlstash package in place and keep
        # the (expensive) CPython + torch install. Seconds instead of minutes.
        py = interpreter(python_dir, args.os)
        log("reuse-env: reinstalling only the PixlStash wheel")
        pip_install(py, ["--force-reinstall", "--no-deps", str(args.wheel)], cache_dir)
    else:
        if args.reuse_env:
            log("reuse-env requested but no existing env — doing a full build")
        # Start clean so a rebuild never layers onto a stale interpreter.
        if python_dir.exists():
            shutil.rmtree(python_dir)
        out.mkdir(parents=True, exist_ok=True)
        python_dir = fetch_standalone_python(triple, out, cache_dir)
        py = interpreter(python_dir, args.os)
        populate_env(py, args.wheel, args.accel, cache_dir)
        strip_env(python_dir)

    runtime = {
        "accel": args.accel,
        "torch": installed_version(py, "torch"),
        "torchvision": installed_version(py, "torchvision"),
        "onnxruntime": installed_version(py, ONNX_PACKAGE[args.accel]),
    }
    (out / "runtime.json").write_text(json.dumps(runtime, indent=2) + "\n")

    log(f"done: {python_dir} ({args.accel})")
    log(f"runtime.json: {runtime}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
