#!/usr/bin/env python3
"""Fetch the pinned LGPL ffmpeg from the PixlStash mirror release.

The desktop build and every Dockerfile call this with the same pin
(ffmpeg_pin.json), so they cannot drift. The mirror release is produced by
.github/workflows/ffmpeg-mirror.yml. A placeholder pin fails loudly: a build
must never ship without its ffmpeg.

    python fetch_ffmpeg.py --platform linux-x64 --out /usr/local/bin/ffmpeg
    python fetch_ffmpeg.py --platform win-x64 --out electron/resources/ffmpeg/ffmpeg.exe --notices-dir electron/resources/ffmpeg

--notices-dir receives what must ship beside the binary: NOTICE.txt, the
licence texts, SOURCE.txt (where the source is) and THIRD_PARTY_LICENSES.txt.
"""

import argparse
import hashlib
import json
import lzma
import shutil
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = "https://github.com/Pikselkroken/pixlstash/releases"


def fetch(release: str, entry: dict) -> bytes:
    """Download one release asset and check it against its pinned SHA256."""
    url = f"{REPO}/download/{release}/{entry['file']}"
    print(f"fetching {url}", flush=True)
    with urllib.request.urlopen(url) as resp:  # noqa: S310
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        raise SystemExit(f"{url}: sha256 {digest} != pinned {entry['sha256']}")
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--notices-dir", type=Path, help="also write the notice and licence files here"
    )
    args = ap.parse_args()

    pin = json.loads((HERE / "ffmpeg_pin.json").read_text())
    asset = pin["assets"].get(args.platform)
    if asset is None:
        raise SystemExit(f"no ffmpeg pinned for platform {args.platform!r}")
    if (
        "PLACEHOLDER" in pin["release"]
        or "PLACEHOLDER" in asset["sha256"]
        or "PLACEHOLDER" in pin["licenses"]["sha256"]
    ):
        raise SystemExit(
            f"ffmpeg_pin.json is still a placeholder for {args.platform}: run "
            ".github/workflows/ffmpeg-mirror.yml and paste the pin JSON from its "
            "publish job's log (or step summary) into packaging/ffmpeg/ffmpeg_pin.json"
        )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(lzma.decompress(fetch(pin["release"], asset)))
    args.out.chmod(0o755)

    if args.notices_dir:
        args.notices_dir.mkdir(parents=True, exist_ok=True)
        for name in (
            "NOTICE.txt",
            "COPYING.LGPLv2.1",
            "COPYING.LGPLv3",
            "COPYING.GPLv3",
        ):
            shutil.copy(HERE / name, args.notices_dir / name)
        (args.notices_dir / "THIRD_PARTY_LICENSES.txt").write_bytes(
            fetch(pin["release"], pin["licenses"])
        )
        (args.notices_dir / "SOURCE.txt").write_text(
            "The corresponding source of this ffmpeg build, and the licences of the\n"
            "libraries in it, are published at:\n"
            f"  {REPO}/tag/{pin['release']}\n"
            "(SOURCES.txt in that release names each commit.)\n"
        )


if __name__ == "__main__":
    main()
