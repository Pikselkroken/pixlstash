#!/usr/bin/env python3
"""prewarm_watermark_cache.py — Pre-generate watermarked image files for the demo.

Visits every picture through a share token so the server writes
{stem}_watermarked.{ext} files next to the originals. Those files are then
picked up by the Dockerfile.demo COPY step and baked into the image, meaning
first-time visitors get instant image loads rather than waiting for PIL to
decode and re-encode each file.

Usage:
    # 1. Start the server against demo data:
    #    python -m pixlstash.app --server-config demo-data/server-config.json
    # 2. In another terminal, run this script:
    python scripts/prewarm_watermark_cache.py YOUR_SHARE_TOKEN
"""

import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE_URL = "http://localhost:8080"
DB_PATH = "demo-data/images/vault.db"
WORKERS = 2


def fetch_pictures() -> list[tuple[int, str]]:
    """Return (id, format) for every non-deleted picture in the vault."""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, format FROM picture WHERE deleted = 0 OR deleted IS NULL"
        ).fetchall()
        return [(int(r[0]), str(r[1]).lower()) for r in rows if r[1]]
    finally:
        conn.close()


def prewarm(
    token: str, pictures: list[tuple[int, str]], session: requests.Session
) -> tuple[int, int]:
    ok = 0
    failed = 0

    def fetch_one(pic_id: int, fmt: str) -> tuple[bool, str]:
        url = f"{BASE_URL}/api/v1/pictures/{pic_id}.{fmt}?token={token}"
        try:
            r = session.get(url, timeout=120)
            if r.status_code in (200, 304):
                return True, url
            return False, f"HTTP {r.status_code}: {url}"
        except Exception as exc:
            return False, f"{exc}: {url}"

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {
            pool.submit(fetch_one, pic_id, fmt): (pic_id, fmt)
            for pic_id, fmt in pictures
        }
        total = len(futures)
        done = 0
        for future in as_completed(futures):
            success, info = future.result()
            done += 1
            if success:
                ok += 1
                print(f"[{done}/{total}] OK  {info}")
            else:
                failed += 1
                print(f"[{done}/{total}] ERR {info}", file=sys.stderr)

    return ok, failed


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} SHARE_TOKEN", file=sys.stderr)
        sys.exit(1)
    token = sys.argv[1]

    print(f"Reading picture list from {DB_PATH}...")
    pictures = fetch_pictures()
    print(f"Found {len(pictures)} pictures.")

    session = requests.Session()

    print(f"Pre-warming watermark cache via {BASE_URL} with {WORKERS} workers...")
    t0 = time.monotonic()
    ok, failed = prewarm(token, pictures, session)
    elapsed = time.monotonic() - t0

    print(f"\nDone in {elapsed:.1f}s — {ok} OK, {failed} failed.")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
