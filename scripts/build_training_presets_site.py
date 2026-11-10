"""Write website/training-presets/v1/ from pixlstash/data/training-presets/.

Run it after changing a recipe; tests/training/test_preset_updates.py fails
while the published copy has drifted from the shipped files.

    python scripts/build_training_presets_site.py
"""

from __future__ import annotations

import os

from pixlstash.training.preset_updates import site_files

SITE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..",
    "website",
    "training-presets",
    "v1",
)


def main() -> None:
    os.makedirs(SITE_DIR, exist_ok=True)
    files = site_files()
    for name in os.listdir(SITE_DIR):
        if name not in files:
            os.remove(os.path.join(SITE_DIR, name))
    for name, raw in files.items():
        with open(os.path.join(SITE_DIR, name), "wb") as handle:
            handle.write(raw)
    print(f"Wrote {len(files)} files to {os.path.normpath(SITE_DIR)}")


if __name__ == "__main__":
    main()
