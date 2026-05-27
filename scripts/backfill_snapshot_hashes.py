"""One-off script: populate metadata_hash for all pictures in checkpoint snapshots.

Iterates every checkpoint snapshot file, runs the schema migration if the
metadata_hash column is missing, then computes and persists the SHA-256
metadata hash for each picture that does not have one yet.

Usage:
    python scripts/backfill_snapshot_hashes.py <image_root>

Example:
    python scripts/backfill_snapshot_hashes.py ~/.config/pixlstash/images
"""

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Backfill metadata_hash in all checkpoint snapshot files."
    )
    parser.add_argument(
        "image_root",
        help="Path to the vault image root directory (contains vault.db).",
    )
    args = parser.parse_args()

    image_root = os.path.abspath(args.image_root)
    if not os.path.isdir(image_root):
        print(f"Error: image_root does not exist: {image_root}", file=sys.stderr)
        sys.exit(1)

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from pixlstash.vault import Vault

    print(f"Opening vault at {image_root} …")
    vault = Vault(image_root=image_root, disable_background_workers=True)
    print("Backfilling snapshot hashes …")
    vault.restore_service.backfill_all_snapshot_hashes(reset_all=True)
    print("Done.")


if __name__ == "__main__":
    main()
