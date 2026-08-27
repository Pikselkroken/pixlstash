#!/usr/bin/env python3
"""Build the Python runtime embedded in the PixlStash desktop installer.

Unlike the old hosted "compute backend" tarballs, this runtime is **bundled into
the installer** (electron-builder ``extraResources``) and ships a fully-working
CPU env on Windows/Linux, or a Metal env on macOS (the default PyPI macOS torch
includes MPS). GPU acceleration (CUDA/ROCm) is *not* baked in - the desktop app
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
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
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


def _sha256_of(path: Path) -> str:
    """Return the hex SHA256 digest of a file, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_expected_sha256(triple: str) -> str:
    """Fetch the published SHA256 for the pinned python-build-standalone asset.

    Every install_only asset ships a sibling ``<asset>.sha256`` sidecar in the
    same release whose body is the raw hex digest (optionally
    ``<digest>  <filename>``). We fetch and parse it so the tarball can be
    verified against the upstream-published value before we trust it. Raises
    ``SystemExit`` with context on any HTTP error or unparseable body so a build
    fails loudly rather than shipping an unverified interpreter.
    """
    url = pbs_asset_url(triple) + ".sha256"
    log(f"fetching checksum {url}")
    try:
        with urllib.request.urlopen(url) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", "strict").strip()
    except urllib.error.URLError as exc:
        raise SystemExit(
            f"could not fetch the published SHA256 sidecar for the CPython "
            f"asset ({url}): {exc}. Verify PBS_RELEASE={PBS_RELEASE} and "
            f"PYTHON_VERSION={PYTHON_VERSION} point at a real published asset."
        ) from exc
    # The sidecar is either a bare hex digest or "<digest>  <filename>".
    digest = body.split()[0] if body else ""
    if len(digest) != 64 or not all(c in "0123456789abcdef" for c in digest.lower()):
        raise SystemExit(
            f"published SHA256 sidecar at {url} did not contain a 64-char hex "
            f"digest (got {body!r}). Refusing to proceed without a usable checksum."
        )
    return digest.lower()


def fetch_standalone_python(triple: str, dest_dir: Path, cache_dir: Path) -> Path:
    """Extract standalone CPython into ``dest_dir/python``, caching the tarball.

    The python-build-standalone archive is pinned by version+release in its
    filename, so a cached copy is always valid for the same pins; we reuse it
    across builds instead of re-downloading ~30 MB every time. The download is
    verified against the upstream-published ``.sha256`` sidecar **before** it is
    trusted (and a previously cached copy is re-verified), so a truncated body,
    a 302-to-HTML, or a poisoned mirror cannot be cached and then fail
    confusingly at ``tarfile.open`` (or, worse, ship a tampered interpreter).
    """
    archive = cache_dir / pbs_asset_name(triple)
    expected = fetch_expected_sha256(triple)

    if archive.is_file() and archive.stat().st_size > 0:
        actual = _sha256_of(archive)
        if actual == expected:
            log(f"using cached CPython {archive} (sha256 verified)")
        else:
            # A cached file that no longer matches is corrupt or was poisoned
            # before verification existed; never trust it. Drop and re-fetch.
            log(
                f"cached CPython {archive} sha256 mismatch "
                f"(expected {expected}, got {actual}); re-downloading"
            )
            archive.unlink()

    if not archive.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Download to a temp path and only promote to the cache name once the
        # checksum verifies, so a bad download is never cached as "valid".
        tmp = archive.with_suffix(archive.suffix + ".part")
        download(pbs_asset_url(triple), tmp)
        actual = _sha256_of(tmp)
        if actual != expected:
            tmp.unlink(missing_ok=True)
            raise SystemExit(
                f"downloaded CPython tarball failed SHA256 verification: "
                f"expected {expected}, got {actual} (url={pbs_asset_url(triple)}). "
                f"The body may be truncated, an HTML error/redirect page, or "
                f"tampered with. Refusing to extract or cache it."
            )
        tmp.replace(archive)
        log(f"downloaded and verified CPython {archive}")

    log("extracting standalone CPython")
    with tarfile.open(archive) as tf:
        tf.extractall(dest_dir)  # noqa: S202 - trusted upstream artifact
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


def compile_bytecode(py: Path, python_dir: Path, target_os: str) -> None:
    """Precompile the stdlib + site-packages tree to .pyc.

    The runtime installs read-only in practice (e.g. root-owned under
    /opt for the .deb), so the interpreter can never write a bytecode
    cache on first launch either -- without this, EVERY launch pays to
    parse and compile the full dependency tree (pixlstash + FastAPI +
    torch + everything else, 10k+ modules) from source. Measured on a
    real installed .deb's bundled runtime: ~2.2s to import pixlstash.app
    with no cache vs ~0.7s precompiled -- most of the app's cold-boot time.

    Scoped to the stdlib/site-packages tree rather than the whole
    ``python_dir`` -- python-build-standalone also vendors legacy Tcl/Tk
    extras (e.g. a Tix8.4.3 demo script with mixed tabs/spaces) that fail
    to compile under Python 3 and are never imported by the app anyway.
    """
    major_minor = ".".join(PYTHON_VERSION.split(".")[:2])
    target = (
        python_dir / "Lib"
        if target_os == "win"
        else python_dir / "lib" / f"python{major_minor}"
    )
    log(f"precompiling bytecode (compileall): {target}")
    # -s strips the build-time prefix from the paths baked into the .pyc, so
    # tracebacks quote runtime-relative paths instead of the CI checkout the
    # tree was built in. The runtime is relocated at install time, so the
    # absolute build path was never resolvable at runtime anyway.
    subprocess.run(
        [
            str(py),
            "-m",
            "compileall",
            "-q",
            "-j",
            "0",
            "-s",
            str(python_dir),
            str(target),
        ],
        check=True,
    )


# Longest path allowed inside the runtime, relative to the python/ root. Windows
# caps a classic (non-\\?\-prefixed) path at 259 usable characters, and neither
# NSIS nor electron-builder's installer/uninstaller are long-path aware. The
# worst prefix the runtime gets moved under is the OLD UNINSTALLER's atomic
# rename during an update: %TEMP%\ns?????.tmp\old-install\resources\python\...
# - about 90 characters with a 20-character Windows user name. 259 - 90, with
# margin, gives the 150 budget. torch blows this today: its dist-info ships
# vendored license texts nested ~190 characters deep
# (…\kineto\…\dynolog\…\prometheus-cpp\…\civetweb\…\duktape-1.5.2\LICENSE.txt),
# which made the 1.7.0-rc.5 uninstaller's rename overflow MAX_PATH, abort with
# exit code 2, and hard-fail every subsequent over-the-top update.
MAX_RELATIVE_PATH = 150


def flatten_deep_license_trees(python_dir: Path) -> None:
    """Cap runtime path depth so Windows installs/updates never hit MAX_PATH.

    Files whose path relative to ``python_dir`` exceeds ``MAX_RELATIVE_PATH``
    are only tolerated inside a ``*.dist-info/licenses`` tree (vendored license
    texts - torch is the known offender). Each offending top-level subtree under
    ``licenses/`` is concatenated into a single ``<subtree>-CONSOLIDATED.txt``
    beside it (every text is kept, with its original relative path as a header,
    so license compliance is preserved) and the deep tree is removed. This makes
    the package's pip RECORD stale for those entries, which pip only notices as
    a warning on uninstall/upgrade of that dist - never at runtime. Any over-long
    file OUTSIDE a licenses tree fails the build so a new offender is caught
    here, at build time, instead of aborting user updates in the field.
    """
    root = python_dir.resolve()
    over = [
        p
        for p in root.rglob("*")
        if p.is_file() and len(str(p.relative_to(root))) > MAX_RELATIVE_PATH
    ]
    if not over:
        log(f"path-depth check OK (all paths <= {MAX_RELATIVE_PATH} chars)")
        return

    unexpected: list[Path] = []
    subtrees: set[Path] = set()
    for p in over:
        rel_parts = p.relative_to(root).parts
        subtree = None
        for i in range(1, len(rel_parts) - 1):
            if rel_parts[i] == "licenses" and rel_parts[i - 1].endswith(".dist-info"):
                subtree = root.joinpath(*rel_parts[: i + 2])
                break
        if subtree is None:
            unexpected.append(p)
        else:
            subtrees.add(subtree)

    if unexpected:
        listing = "\n  ".join(str(p.relative_to(root)) for p in unexpected[:10])
        raise SystemExit(
            f"{len(unexpected)} file(s) exceed {MAX_RELATIVE_PATH} chars relative "
            f"to the runtime root and are not vendored license texts, so they "
            f"cannot be flattened automatically. They would break Windows "
            f"installs/updates (MAX_PATH). Offenders:\n  {listing}"
        )

    for subtree in sorted(subtrees):
        target = subtree.parent / f"{subtree.name}-CONSOLIDATED.txt"
        files = sorted(p for p in subtree.rglob("*") if p.is_file())
        with target.open("w", encoding="utf-8") as out:
            out.write(
                "Consolidated license texts. The originals lived under the "
                "directory tree named below; it was flattened into this file "
                "because its paths exceeded the Windows path-length limit.\n"
            )
            for p in files:
                out.write(
                    f"\n{'=' * 70}\n{p.relative_to(subtree.parent)}\n{'=' * 70}\n"
                )
                out.write(p.read_text(encoding="utf-8", errors="replace"))
        shutil.rmtree(subtree)
        log(
            f"flattened {len(files)} deep license file(s): "
            f"{subtree.relative_to(root)} -> {target.relative_to(root)}"
        )

    still_over = [
        p
        for p in root.rglob("*")
        if p.is_file() and len(str(p.relative_to(root))) > MAX_RELATIVE_PATH
    ]
    if still_over:
        listing = "\n  ".join(str(p.relative_to(root)) for p in still_over[:10])
        raise SystemExit(
            f"path-depth check still failing after flattening:\n  {listing}"
        )
    log(f"path-depth check OK after flattening (<= {MAX_RELATIVE_PATH} chars)")


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
            log("reuse-env requested but no existing env - doing a full build")
        # Start clean so a rebuild never layers onto a stale interpreter.
        if python_dir.exists():
            shutil.rmtree(python_dir)
        out.mkdir(parents=True, exist_ok=True)
        python_dir = fetch_standalone_python(triple, out, cache_dir)
        py = interpreter(python_dir, args.os)
        populate_env(py, args.wheel, args.accel, cache_dir)
        strip_env(python_dir)

    # Both branches: ship a complete bytecode cache so the interpreter never
    # recompiles from source at launch (strip_env, above, drops whatever
    # partial cache pip's install left; this rebuilds it deliberately for
    # 100% coverage, including stdlib).
    compile_bytecode(py, python_dir, args.os)

    # Both branches, Windows only: a runtime must never ship over-long paths
    # (MAX_PATH breaks installs/updates - see flatten_deep_license_trees).
    # macOS/Linux have no such limit and their torch builds legitimately ship
    # deep non-license paths (e.g. ARM64 KleidiAI headers under
    # torch/include/kai/ukernels/), so the check would only false-positive there.
    if args.os == "win":
        flatten_deep_license_trees(python_dir)

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
