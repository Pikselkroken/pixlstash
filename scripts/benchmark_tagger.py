#!/usr/bin/env python3
"""Benchmark PixlVault tagger throughput.

Modes:
- full-path (default): runs real `TagTask` pipeline (WD14 + custom + quality crop pass)
- tagger-only: runs `PictureTagger.tag_images(...)` directly

Use environment variables to tune batch settings between runs:
- PIXLVAULT_TAGGER_MAX_CONCURRENT_GPU
- PIXLVAULT_TAGGER_MAX_CONCURRENT_CPU
- PIXLVAULT_CUSTOM_TAGGER_BATCH
"""

from __future__ import annotations

import argparse
import os
import random
import subprocess
import sys
import threading
import time
from pathlib import Path
from statistics import mean


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from pixlvault.picture_tagger import PictureTagger  # noqa: E402
from pixlvault.database import VaultDatabase  # noqa: E402
from pixlvault.db_models import Picture  # noqa: E402
from pixlvault.tasks.tag_task import TagTask  # noqa: E402
from pixlvault.utils.image_processing.image_utils import ImageUtils  # noqa: E402
import torch  # noqa: E402
from sqlalchemy import or_  # noqa: E402
from sqlmodel import select  # noqa: E402


SUPPORTED_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".heic",
    ".heif",
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".webm",
    ".flv",
    ".wmv",
}


def _read_rss_bytes() -> int:
    status_path = Path("/proc/self/status")
    if status_path.exists():
        try:
            for line in status_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
        except Exception:
            pass
    return 0


def _read_nvidia_smi_vram_bytes(pid: int) -> int:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=1.0,
        )
        if result.returncode != 0:
            return 0
        total_mb = 0
        for line in (result.stdout or "").splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                line_pid = int(parts[0])
                used_mb = int(parts[1])
            except ValueError:
                continue
            if line_pid == pid:
                total_mb += used_mb
        return total_mb * 1024 * 1024
    except Exception:
        return 0


def discover_paths(input_dir: Path, limit: int, shuffle: bool) -> list[str]:
    paths = [
        p
        for p in sorted(input_dir.rglob("*"))
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    ]
    if shuffle:
        random.shuffle(paths)
    if limit > 0:
        paths = paths[:limit]
    return [str(p) for p in paths]


class BenchmarkDBAdapter:
    def __init__(self, vault_db: VaultDatabase, dry_run_writes: bool):
        self._vault_db = vault_db
        self._dry_run_writes = dry_run_writes
        self.image_root = vault_db.image_root

    def run_task(self, func, *args, priority=None, **kwargs):
        if self._dry_run_writes and getattr(func, "__name__", "") == "_add_tags_bulk":
            updates = args[0] if args else []
            return [u.get("pic_id") for u in updates if u.get("pic_id") is not None]
        if priority is None:
            return self._vault_db.run_task(func, *args, **kwargs)
        return self._vault_db.run_task(func, *args, priority=priority, **kwargs)


def load_pictures_for_paths(
    vault_db: VaultDatabase,
    discovered_paths: list[str],
    limit: int,
    shuffle: bool,
) -> list[Picture]:
    abs_set = {str(Path(path).resolve()) for path in discovered_paths}
    rel_set = set()
    root = Path(vault_db.image_root).resolve()
    for path in discovered_paths:
        p = Path(path).resolve()
        try:
            rel_set.add(str(p.relative_to(root)))
        except Exception:
            continue

    def fetch_candidates(session):
        clauses = []
        if abs_set:
            clauses.append(Picture.file_path.in_(list(abs_set)))
        if rel_set:
            clauses.append(Picture.file_path.in_(list(rel_set)))
        if not clauses:
            return []
        stmt = select(Picture).where(or_(*clauses)).order_by(Picture.id)
        return session.exec(stmt).all()

    candidates = vault_db.run_immediate_read_task(fetch_candidates)
    available = []
    abs_lookup = set(abs_set)
    for pic in candidates:
        resolved = ImageUtils.resolve_picture_path(vault_db.image_root, pic.file_path)
        if not resolved:
            continue
        resolved_abs = str(Path(resolved).resolve())
        if resolved_abs in abs_lookup and Path(resolved_abs).exists():
            available.append(pic)

    if shuffle:
        random.shuffle(available)
    if limit > 0:
        available = available[:limit]
    return available


def _run_with_instrumentation(
    tagger: PictureTagger, run_callable, item_count: int
) -> dict:
    wd14_stats = {
        "calls": 0,
        "items": 0,
        "elapsed_s": 0.0,
    }
    custom_stats = {
        "elapsed_s": 0.0,
    }
    quality_stats = {
        "calls": 0,
        "items": 0,
        "elapsed_s": 0.0,
    }

    original_run_batch = tagger._run_batch
    original_tag_custom_items = tagger._tag_custom_items
    original_tag_quality_crops = tagger.tag_quality_crops

    peak_ram_bytes = 0
    peak_vram_torch_bytes = 0
    peak_vram_nvidia_bytes = 0
    current_pid = os.getpid()
    monitor_stop = threading.Event()

    if torch.cuda.is_available() and getattr(tagger, "_device", "cpu") == "cuda":
        try:
            torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass

    def monitor_memory() -> None:
        nonlocal peak_ram_bytes, peak_vram_torch_bytes, peak_vram_nvidia_bytes
        while not monitor_stop.is_set():
            peak_ram_bytes = max(peak_ram_bytes, _read_rss_bytes())
            if (
                torch.cuda.is_available()
                and getattr(tagger, "_device", "cpu") == "cuda"
            ):
                try:
                    peak_vram_torch_bytes = max(
                        peak_vram_torch_bytes,
                        int(torch.cuda.max_memory_reserved()),
                    )
                except Exception:
                    pass
                peak_vram_nvidia_bytes = max(
                    peak_vram_nvidia_bytes,
                    _read_nvidia_smi_vram_bytes(current_pid),
                )
            monitor_stop.wait(0.05)

    monitor_thread = threading.Thread(target=monitor_memory, daemon=True)
    monitor_thread.start()

    def timed_run_batch(path_imgs, undesired_tags):
        start = time.perf_counter()
        try:
            return original_run_batch(path_imgs, undesired_tags)
        finally:
            wd14_stats["calls"] += 1
            wd14_stats["items"] += len(path_imgs)
            wd14_stats["elapsed_s"] += time.perf_counter() - start

    def timed_tag_custom_items(items, stop_event=None, threshold=None, image_size=None):
        start = time.perf_counter()
        try:
            return original_tag_custom_items(
                items,
                stop_event=stop_event,
                threshold=threshold,
                image_size=image_size,
            )
        finally:
            custom_stats["elapsed_s"] += time.perf_counter() - start

    def timed_tag_quality_crops(items, stop_event=None):
        start = time.perf_counter()
        try:
            return original_tag_quality_crops(items, stop_event=stop_event)
        finally:
            quality_stats["calls"] += 1
            quality_stats["items"] += len(items or [])
            quality_stats["elapsed_s"] += time.perf_counter() - start

    tagger._run_batch = timed_run_batch
    tagger._tag_custom_items = timed_tag_custom_items
    tagger.tag_quality_crops = timed_tag_quality_crops

    total_start = time.perf_counter()
    try:
        result_payload = run_callable()
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=1.0)
        tagger._run_batch = original_run_batch
        tagger._tag_custom_items = original_tag_custom_items
        tagger.tag_quality_crops = original_tag_quality_crops
    total_elapsed_s = time.perf_counter() - total_start

    if torch.cuda.is_available() and getattr(tagger, "_device", "cpu") == "cuda":
        try:
            peak_vram_torch_bytes = max(
                peak_vram_torch_bytes,
                int(torch.cuda.max_memory_reserved()),
            )
        except Exception:
            pass
        peak_vram_nvidia_bytes = max(
            peak_vram_nvidia_bytes,
            _read_nvidia_smi_vram_bytes(current_pid),
        )

    peak_vram_bytes = peak_vram_nvidia_bytes or peak_vram_torch_bytes

    wd14_calls = max(1, int(wd14_stats["calls"]))
    effective_wd14_batch = wd14_stats["items"] / wd14_calls

    result_items = 0
    if isinstance(result_payload, dict):
        result_items = int(
            result_payload.get("changed_count", 0) or len(result_payload)
        )
    elif isinstance(result_payload, (list, tuple, set)):
        result_items = len(result_payload)

    return {
        "items": item_count,
        "result_items": result_items,
        "total_elapsed_s": total_elapsed_s,
        "throughput_items_per_s": (item_count / total_elapsed_s)
        if total_elapsed_s > 0
        else 0.0,
        "wd14_calls": wd14_stats["calls"],
        "wd14_items": wd14_stats["items"],
        "wd14_elapsed_s": wd14_stats["elapsed_s"],
        "wd14_effective_batch": effective_wd14_batch,
        "custom_elapsed_s": custom_stats["elapsed_s"],
        "quality_calls": quality_stats["calls"],
        "quality_items": quality_stats["items"],
        "quality_elapsed_s": quality_stats["elapsed_s"],
        "peak_ram_mb": peak_ram_bytes / (1024 * 1024),
        "peak_vram_mb": peak_vram_bytes / (1024 * 1024),
        "peak_vram_torch_mb": peak_vram_torch_bytes / (1024 * 1024),
        "peak_vram_nvidia_mb": peak_vram_nvidia_bytes / (1024 * 1024),
    }


def run_single_tagger(tagger: PictureTagger, image_paths: list[str]) -> dict:
    return _run_with_instrumentation(
        tagger,
        run_callable=lambda: tagger.tag_images(image_paths),
        item_count=len(image_paths),
    )


def run_single_full_path(
    tagger: PictureTagger, benchmark_db, pictures: list[Picture]
) -> dict:
    task = TagTask(
        database=benchmark_db,
        picture_tagger=tagger,
        pictures=pictures,
    )
    task.on_queued()
    return _run_with_instrumentation(
        tagger,
        run_callable=lambda: task.run(),
        item_count=len(pictures),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark PixlVault tagger throughput"
    )
    parser.add_argument(
        "input_dir", type=Path, help="Directory containing images/videos"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=256,
        help="Max number of files to benchmark (0 = all)",
    )
    parser.add_argument(
        "--warmup-runs",
        type=int,
        default=1,
        help="Warmup runs before measured runs",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Measured runs",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Shuffle discovered files before limiting",
    )
    parser.add_argument(
        "--force-cpu",
        action="store_true",
        help="Force CPU inference for this benchmark process",
    )
    parser.add_argument(
        "--allow-no-custom",
        action="store_true",
        help="Allow benchmark to run without custom tagger (WD14-only)",
    )
    parser.add_argument(
        "--pipeline",
        choices=["full-path", "tagger-only"],
        default="full-path",
        help="Benchmark mode: full TagTask path or tagger-only path",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("vault.db"),
        help="Path to vault DB (used in full-path mode)",
    )
    parser.add_argument(
        "--write-tags",
        action="store_true",
        help="Allow full-path benchmark to persist tag updates to DB",
    )
    args = parser.parse_args()

    if not args.input_dir.exists() or not args.input_dir.is_dir():
        raise FileNotFoundError(f"Input directory not found: {args.input_dir}")

    image_paths = discover_paths(args.input_dir, args.limit, args.shuffle)
    if not image_paths:
        raise RuntimeError(f"No supported files found in {args.input_dir}")

    if args.force_cpu:
        PictureTagger.FORCE_CPU = True

    print("Tagger benchmark configuration")
    print(f"  input_dir: {args.input_dir}")
    print(f"  files: {len(image_paths)}")
    print(f"  pipeline: {args.pipeline}")
    print(f"  warmup_runs: {args.warmup_runs}")
    print(f"  measured_runs: {args.runs}")
    print("  env:")
    print(
        "    PIXLVAULT_TAGGER_MAX_CONCURRENT_GPU="
        + str(os.getenv("PIXLVAULT_TAGGER_MAX_CONCURRENT_GPU", "<default>"))
    )
    print(
        "    PIXLVAULT_TAGGER_MAX_CONCURRENT_CPU="
        + str(os.getenv("PIXLVAULT_TAGGER_MAX_CONCURRENT_CPU", "<default>"))
    )
    print(
        "    PIXLVAULT_CUSTOM_TAGGER_BATCH="
        + str(os.getenv("PIXLVAULT_CUSTOM_TAGGER_BATCH", "<default>"))
    )

    benchmark_db = None
    pictures = []
    vault_db = None
    if args.pipeline == "full-path":
        if not args.db_path.exists():
            raise FileNotFoundError(f"DB not found: {args.db_path}")
        vault_db = VaultDatabase(str(args.db_path))
        benchmark_db = BenchmarkDBAdapter(
            vault_db=vault_db,
            dry_run_writes=not args.write_tags,
        )
        pictures = load_pictures_for_paths(
            vault_db=vault_db,
            discovered_paths=image_paths,
            limit=args.limit,
            shuffle=args.shuffle,
        )
        if not pictures:
            if vault_db is not None:
                vault_db.close()
            raise RuntimeError(
                "No DB pictures matched discovered files for full-path benchmark. "
                "Use input_dir that matches DB picture paths."
            )
        print(f"  matched_db_pictures: {len(pictures)}")
        print(f"  write_tags: {args.write_tags}")

    with PictureTagger(silent=True) as tagger:
        tagger._ensure_tagging_ready()
        custom_ready = tagger.custom_tagger_ready()
        print(f"  custom_tagger_ready: {custom_ready}")
        if not custom_ready and not args.allow_no_custom:
            raise RuntimeError(
                "Custom tagger is not ready. This benchmark requires custom tagging by default. "
                "Use --allow-no-custom to run WD14-only."
            )

        for index in range(args.warmup_runs):
            if args.pipeline == "full-path":
                _ = run_single_full_path(tagger, benchmark_db, pictures)
            else:
                _ = run_single_tagger(tagger, image_paths)
            print(f"Warmup {index + 1}/{args.warmup_runs} complete")

        measured = []
        for index in range(args.runs):
            if args.pipeline == "full-path":
                result = run_single_full_path(tagger, benchmark_db, pictures)
            else:
                result = run_single_tagger(tagger, image_paths)
            measured.append(result)
            print(
                "Run {}/{}: total={:.3f}s, throughput={:.2f}/s, wd14_batch={:.2f}, wd14_s={:.3f}, custom_s={:.3f}, quality_s={:.3f}, quality_items={}, peak_ram={:.1f}MB, peak_vram={:.1f}MB (nvidia={:.1f}, torch={:.1f})".format(
                    index + 1,
                    args.runs,
                    result["total_elapsed_s"],
                    result["throughput_items_per_s"],
                    result["wd14_effective_batch"],
                    result["wd14_elapsed_s"],
                    result["custom_elapsed_s"],
                    result["quality_elapsed_s"],
                    result["quality_items"],
                    result["peak_ram_mb"],
                    result["peak_vram_mb"],
                    result["peak_vram_nvidia_mb"],
                    result["peak_vram_torch_mb"],
                )
            )

    if vault_db is not None:
        vault_db.close()

    avg_total = mean([item["total_elapsed_s"] for item in measured])
    avg_throughput = mean([item["throughput_items_per_s"] for item in measured])
    avg_wd14_batch = mean([item["wd14_effective_batch"] for item in measured])
    avg_wd14_s = mean([item["wd14_elapsed_s"] for item in measured])
    avg_custom_s = mean([item["custom_elapsed_s"] for item in measured])
    avg_quality_s = mean([item["quality_elapsed_s"] for item in measured])
    avg_quality_items = mean([item["quality_items"] for item in measured])
    avg_peak_ram_mb = mean([item["peak_ram_mb"] for item in measured])
    avg_peak_vram_mb = mean([item["peak_vram_mb"] for item in measured])
    avg_peak_vram_torch_mb = mean([item["peak_vram_torch_mb"] for item in measured])
    avg_peak_vram_nvidia_mb = mean([item["peak_vram_nvidia_mb"] for item in measured])
    max_peak_ram_mb = max([item["peak_ram_mb"] for item in measured])
    max_peak_vram_mb = max([item["peak_vram_mb"] for item in measured])
    max_peak_vram_torch_mb = max([item["peak_vram_torch_mb"] for item in measured])
    max_peak_vram_nvidia_mb = max([item["peak_vram_nvidia_mb"] for item in measured])

    print("\nSummary")
    print(f"  avg_total_s: {avg_total:.3f}")
    print(f"  avg_throughput_items_per_s: {avg_throughput:.2f}")
    print(f"  avg_wd14_effective_batch: {avg_wd14_batch:.2f}")
    print(f"  avg_wd14_elapsed_s: {avg_wd14_s:.3f}")
    print(f"  avg_custom_elapsed_s: {avg_custom_s:.3f}")
    print(f"  avg_quality_elapsed_s: {avg_quality_s:.3f}")
    print(f"  avg_quality_items: {avg_quality_items:.1f}")
    print(f"  avg_peak_ram_mb: {avg_peak_ram_mb:.1f}")
    print(f"  max_peak_ram_mb: {max_peak_ram_mb:.1f}")
    print(f"  avg_peak_vram_mb: {avg_peak_vram_mb:.1f}")
    print(f"  max_peak_vram_mb: {max_peak_vram_mb:.1f}")
    print(f"  avg_peak_vram_nvidia_mb: {avg_peak_vram_nvidia_mb:.1f}")
    print(f"  max_peak_vram_nvidia_mb: {max_peak_vram_nvidia_mb:.1f}")
    print(f"  avg_peak_vram_torch_mb: {avg_peak_vram_torch_mb:.1f}")
    print(f"  max_peak_vram_torch_mb: {max_peak_vram_torch_mb:.1f}")


if __name__ == "__main__":
    main()
