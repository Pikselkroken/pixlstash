#!/usr/bin/env python3
"""Fetch the pinned LGPL ffmpeg from the PixlStash mirror release.

The desktop build and every Dockerfile call this with the same pin
(ffmpeg_pin.json), so they cannot drift. The mirror release is produced by
.github/workflows/ffmpeg-mirror.yml. A placeholder pin fails loudly: a build
must never ship without its ffmpeg.

    python fetch_ffmpeg.py --platform linux-x64 --out /usr/local/bin/ffmpeg
    python fetch_ffmpeg.py --platform win-x64 --out electron/resources/ffmpeg/ffmpeg.exe --notices-dir electron/resources/ffmpeg
"""

import argparse
import hashlib
import json
import lzma
import shutil
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RELEASES = "https://github.com/Pikselkroken/pixlstash/releases/download"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--notices-dir", type=Path, help="also copy NOTICE and licence texts here"
    )
    args = ap.parse_args()

    pin = json.loads((HERE / "ffmpeg_pin.json").read_text())
    asset = pin["assets"].get(args.platform)
    if asset is None:
        raise SystemExit(f"no ffmpeg pinned for platform {args.platform!r}")
    if "PLACEHOLDER" in pin["release"] or "PLACEHOLDER" in asset["sha256"]:
        raise SystemExit(
            f"ffmpeg_pin.json is still a placeholder for {args.platform}: run "
            ".github/workflows/ffmpeg-mirror.yml and paste its release tag and "
            "SHA256SUMS into packaging/ffmpeg/ffmpeg_pin.json"
        )

    url = f"{RELEASES}/{pin['release']}/{asset['file']}"
    print(f"fetching {url}", flush=True)
    with urllib.request.urlopen(url) as resp:  # noqa: S310
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != asset["sha256"]:
        raise SystemExit(f"{url}: sha256 {digest} != pinned {asset['sha256']}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(lzma.decompress(data))
    args.out.chmod(0o755)

    if args.notices_dir:
        args.notices_dir.mkdir(parents=True, exist_ok=True)
        for name in ("NOTICE.txt", "COPYING.LGPLv3", "COPYING.GPLv3"):
            shutil.copy(HERE / name, args.notices_dir / name)


if __name__ == "__main__":
    main()
