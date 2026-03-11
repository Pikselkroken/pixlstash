"""Download 10 high-quality CC0/open-licensed reference images from Wikimedia Commons.

Each image is saved to pictures/good/ as Good1.jpg … Good10.jpg.
Run from the repository root:  python scripts/download_anchor_images.py
"""

import pathlib
import sys

import requests

# 10 Wikimedia Commons featured / quality images (all CC-BY-SA or CC0).
# Chosen for sharpness, good exposure, diverse subjects.
GOOD_URLS = [
    # Sharp orange tabby portrait
    "https://upload.wikimedia.org/wikipedia/commons/thumb/6/68/Orange_tabby_cat_sitting_on_fallen_leaves-Hisashi-01A.jpg/1280px-Orange_tabby_cat_sitting_on_fallen_leaves-Hisashi-01A.jpg",
    # Jumping spider macro (Thomas Shahan, CC-BY)
    "https://upload.wikimedia.org/wikipedia/commons/thumb/9/95/Phidippus_johnsoni_2.jpg/1280px-Phidippus_johnsoni_2.jpg",
    # Milky Way over mountain lake
    "https://upload.wikimedia.org/wikipedia/commons/thumb/7/76/Milky_Way_Arch.jpg/1280px-Milky_Way_Arch.jpg",
    # Sharp bald eagle in flight
    "https://upload.wikimedia.org/wikipedia/commons/thumb/1/11/Bald_Eagle_Just_After_Diving_For_Fish.jpg/1280px-Bald_Eagle_Just_After_Diving_For_Fish.jpg",
    # Macro of a bee on lavender
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/4d/Apis_mellifera_Western_honey_bee.jpg/1280px-Apis_mellifera_Western_honey_bee.jpg",
    # Sharp landscape: Lofoten islands
    "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3d/Vestv%C3%A5g%C3%B8ya.jpg/1280px-Vestv%C3%A5g%C3%B8ya.jpg",
    # Colosseum, Rome – sharp architectural detail
    "https://upload.wikimedia.org/wikipedia/commons/thumb/d/de/Colosseum_in_Rome%2C_Italy_-_April_2007.jpg/1280px-Colosseum_in_Rome%2C_Italy_-_April_2007.jpg",
    # Lioness portrait (sharp eyes)
    "https://upload.wikimedia.org/wikipedia/commons/thumb/7/73/Lion_waiting_in_Namibia.jpg/1280px-Lion_waiting_in_Namibia.jpg",
    # Red squirrel – sharp fur detail
    "https://upload.wikimedia.org/wikipedia/commons/thumb/e/e3/Squirrel_posing.jpg/1280px-Squirrel_posing.jpg",
    # Tulip macro – sharp petals, bokeh background
    "https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Simple_flower_-_bred_for_beauty.jpg/1280px-Simple_flower_-_bred_for_beauty.jpg",
]

HEADERS = {
    "User-Agent": "PixlStash-anchor-bootstrap/1.0 (https://github.com/pixlstash; contact via repo issues)"
}


def download(url: str, dest: pathlib.Path) -> bool:
    try:
        r = requests.get(url, headers=HEADERS, timeout=30, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
        size_kb = dest.stat().st_size // 1024
        print(f"  ✓ {dest.name}  ({size_kb} kB)")
        return True
    except Exception as e:
        print(f"  ✗ {dest.name}  FAILED: {e}")
        if dest.exists():
            dest.unlink()
        return False


def main():
    repo_root = pathlib.Path(__file__).parent.parent
    out_dir = repo_root / "pictures" / "good"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading {len(GOOD_URLS)} good-quality reference images → {out_dir}\n")
    ok = 0
    for i, url in enumerate(GOOD_URLS, start=1):
        ext = ".jpg"
        dest = out_dir / f"Good{i}{ext}"
        if dest.exists():
            print(f"  – Good{i}{ext}  already exists, skipping")
            ok += 1
            continue
        if download(url, dest):
            ok += 1

    print(f"\nDownloaded {ok}/{len(GOOD_URLS)} images.")
    if ok < len(GOOD_URLS):
        print("Some downloads failed. Replace with alternative CCO images and re-run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
