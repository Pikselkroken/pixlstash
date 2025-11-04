import os
from tqdm import tqdm

import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pixelurgy_vault.vault import Vault
from pixelurgy_vault.picture_iteration import PictureIteration


def regenerate_thumbnails(image_root=None):
    vault = Vault(image_root=image_root)
    vault.stop_background_workers()
    updated = 0
    for pic_id in tqdm(list(vault.pictures), desc="Pictures"):
        try:
            pic = vault.pictures[pic_id]
        except Exception as e:
            print(f"Failed to fetch picture {pic_id}: {e}")
            continue
        its = vault.iterations.find(picture_id=pic.id)
        for it in its:
            try:
                # Only regenerate if file exists
                if it.file_path and os.path.exists(it.file_path):
                    from PIL import Image

                    with Image.open(it.file_path) as img:
                        thumb_bytes = PictureIteration._generate_thumbnail_bytes(img)
                        if thumb_bytes:
                            it.thumbnail = thumb_bytes
                            vault.iterations.update_iteration(it)
                            updated += 1
            except Exception as e:
                print(f"Failed to regenerate thumbnail for {it.file_path}: {e}")
    print(f"Regenerated {updated} thumbnails.")
    vault.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Regenerate all thumbnails in the vault."
    )
    parser.add_argument(
        "--image-root", type=str, default=None, help="Path to image root (optional)"
    )
    args = parser.parse_args()
    regenerate_thumbnails(image_root=args.image_root)
