#!/usr/bin/env python3
"""Launch a PixlStash backend for Playwright e2e tests.

Playwright's ``webServer`` runs this script. It does NOT touch the committed
``test-data/`` fixture: it copies the fixture images + vault into a throwaway
work directory, strips any user/token rows so the server boots in *first-run*
state (the e2e global-setup then registers an admin and mints an ALL-scope
token per run — no credentials are committed), seeds a small model folder
for the shelf specs and a reference folder for the Media specs, writes a lean
server-config (the task runner runs, the work planner keeps only the folder
sweeps and an idle face finder, no models, no startup thumbnail generation, CPU
only), and execs the real server. The server also serves the built SPA from
``pixlstash/frontend/dist`` on the same origin, so cookie auth just works.

Env:
    PIXLSTASH_E2E_PORT   Port to bind (default 9600).
    PIXLSTASH_E2E_DATA   Fixture directory to serve (default ``test-data``).
                         The screenshot-reproduction harness points this at
                         ``demo-data`` for a richer, more varied library.

The work directory (``<tmp>/pixlstash-e2e``) is recreated on every launch, so
nothing accumulates and the committed fixture is never modified.
"""

import json
import os
import hashlib
import shutil
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DATA = Path(os.environ.get("PIXLSTASH_E2E_DATA") or (REPO_ROOT / "test-data"))
SRC_IMAGES = SRC_DATA / "images"
PORT = int(os.environ.get("PIXLSTASH_E2E_PORT", "9600"))
# Isolate the work dir per fixture AND per checkout. Per fixture so a
# screenshots run (demo-data) never stomps an e2e run (test-data); per checkout
# because the fixture folder is called "test-data" in every clone, so two
# working copies of the repo would otherwise resolve to the same directory and
# rmtree each other's vault mid-run. That failure is worth naming: the symptom
# is not an error but a handful of unrelated specs failing, or global-setup
# reporting the vault is not in first-run state.
_CHECKOUT_TAG = hashlib.sha1(str(REPO_ROOT).encode()).hexdigest()[:8]
WORK_DIR = (
    Path(tempfile.gettempdir()) / f"pixlstash-e2e-{SRC_DATA.name}-{_CHECKOUT_TAG}"
)
# Not ``models``: that is the managed store the server creates beside its hub.
MODEL_FOLDER = WORK_DIR / "model-shelf"
REFERENCE_FOLDER = WORK_DIR / "reference-folder"
# The seeded file the server moves after its first scan, leaving a review.
REFERENCE_MOVED = "Inbox/fern.png"
# Stands in for the app's data directory: the workflow inbox, the user workflow
# folder and the trash, all of which are otherwise shared with the owner.
USER_DATA = WORK_DIR / "user-data"


def _ignore_backups(_dir, names):
    """Skip backup / sidecar DB files when copying the fixture images."""
    return [n for n in names if ".bck" in n]


def _strip_credentials(db_path: Path) -> None:
    """Force first-run state: checkpoint the WAL and delete user/token rows.

    Operates only on the throwaway copy, never the committed fixture.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for table in ("usertoken", "user"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError as exc:
                # Table may not exist in older fixtures — log and continue.
                print(f"[e2e-backend] skip stripping {table}: {exc}")
        conn.commit()
    finally:
        conn.close()
    # Remove now-stale WAL sidecars so the server opens a clean DB.
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()


def _write_stub_model(path: Path, tensors: dict, metadata: dict) -> None:
    """Write a header-only ``.safetensors``: the scanner reads only the header.

    Every tensor's byte range is empty, so the file is complete by its own
    header but cannot be loaded. Same shape as the pytest fixtures' stubs.
    """
    header = {
        name: {"dtype": "F16", "shape": shape, "data_offsets": [0, 0]}
        for name, shape in tensors.items()
    }
    header["__metadata__"] = metadata
    blob = json.dumps(header).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<Q", len(blob)) + blob)


def _seed_model_folder(root: Path) -> None:
    """Lay out a small model folder for the shelf specs, under the work dir.

    Two LoRAs and a checkpoint (mixed-case names, as real ones are), an empty
    subfolder, and a preview ``.png`` the scanner must not list. The server
    registers and scans it at start-up (``e2e_model_folder``), because the
    background rescan is off here.
    """
    lora = {"blocks.0.lora_A.weight": [8, 16], "blocks.0.lora_B.weight": [16, 8]}
    _write_stub_model(
        root / "Clementine_Portrait.safetensors",
        lora,
        {"format": "pt", "ss_output_name": "Clementine_Portrait"},
    )
    _write_stub_model(
        root / "Lora" / "DetailTweaker_v2.safetensors",
        lora,
        {"format": "pt", "ss_output_name": "DetailTweaker_v2"},
    )
    (root / "Lora" / "DetailTweaker_v2.preview.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _write_stub_model(
        root / "StableDiffusion" / "RealVisXL_V4.safetensors",
        {"model.weight": [40000, 40000]},
        {"format": "pt"},
    )
    (root / "Empty").mkdir()


def _seed_reference_folder(root: Path) -> None:
    """Lay out a small reference folder for the Media specs, under the work dir.

    Three flat images in different colours, so no two share pixels with each
    other or with the fixture (the dedup specs count the fixture's pairs). The
    server registers and scans it at start-up (``e2e_reference_folder``) and
    moves ``REFERENCE_MOVED`` so ``/moves`` has a review to show.
    """
    for name, colour in (
        ("fern.png", (52, 120, 61)),
        ("moss.png", (98, 112, 40)),
        ("stone.png", (128, 124, 118)),
    ):
        path = root / "Inbox" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 48), colour).save(path)


def _harden_permissions(root: Path) -> None:
    """Strip group/world write bits from the throwaway work directory.

    ``shutil.copytree`` runs ``copystat`` on every directory it creates, so the
    committed fixture's 0775 directories (and its 0664 ``vault.db``) are
    reproduced verbatim in the work copy. ``pixlstash.trusted_sqlite`` refuses
    to open a SQLite database whose file or immediate parent is
    group/world-writable, which fails the launch outright on any machine whose
    umask is not 0022. The umask set in ``main()`` does not cover this: an
    explicit ``chmod`` ignores it. Tightening a throwaway copy is always safe.
    """
    for path in (root, *root.rglob("*")):
        if path.is_symlink():
            continue
        try:
            path.chmod(path.stat().st_mode & ~0o022)
        except OSError as exc:
            print(f"[e2e-backend] could not tighten permissions on {path}: {exc}")


def server_overrides(work_images: Path) -> dict:
    """The fields that make the fixture's config a fast, hermetic test server."""
    return {
        "host": "127.0.0.1",
        "port": PORT,
        "image_root": str(work_images),
        "require_ssl": False,
        "cookie_secure": False,
        "disable_password_auth": False,
        # The suite reloads the SPA many times in quick succession; the
        # global rate limiter (120 public requests / 60s) would otherwise
        # 429 later navigations. Safe to disable on this hermetic backend.
        "disable_rate_limit": True,
        # The task runner runs, so an import or a folder scan a spec
        # starts completes. The planner keeps only these finders: the
        # folder sweeps, and the face finder the import routes require,
        # which queues nothing because this vault builds no inference
        # engine. The sweeps that would rewrite the fixture (tags,
        # embeddings, scores, thumbnails) stay off. Read only with
        # enable_test_hooks.
        "disable_background_workers": False,
        "e2e_planner_finders": [
            "ReferenceFolderScanTask",
            "WatchFolderImportTask",
            "FaceExtractionTask",
        ],
        # Register the e2e-only WS test-hooks router so specs can inject
        # grid events deterministically (the sweeps that fire the
        # tag/face/score events the grid reacts to are off). Off by default
        # everywhere else: production configs MUST NEVER set this flag — it
        # exposes /api/v1/test-hooks/ws-event, which emits arbitrary
        # owner-only WebSocket events.
        "enable_test_hooks": True,
        # Read only when enable_test_hooks is on: registered as a `user`
        # model folder and scanned synchronously at start-up.
        "e2e_model_folder": str(MODEL_FOLDER),
        # Read only when enable_test_hooks is on: the workflow folders and the
        # trash move here, so nothing under the shared data directory changes.
        "e2e_data_dir": str(USER_DATA),
        # Likewise: registered with a layout, scanned, one file moved and
        # scanned again, so /moves lists a pending review.
        "e2e_reference_folder": {
            "path": str(REFERENCE_FOLDER),
            "move": REFERENCE_MOVED,
        },
        "generate_thumbnails_on_startup": False,
        "default_device": "cpu",
        "daily_snapshots": False,
        "log_level": "warning",
    }


def main() -> int:
    # Everything below lives in a throwaway directory that holds a SQLite
    # database, so create it private rather than at the developer's umask. The
    # exec'd server inherits this and writes its own snapshots the same way.
    os.umask(0o077)

    if not (SRC_IMAGES / "vault.db").exists():
        print(
            f"[e2e-backend] ERROR: fixture vault not found at {SRC_IMAGES / 'vault.db'}.\n"
            f"              Provide a seeded test-data/ fixture before running e2e tests.",
            file=sys.stderr,
        )
        return 1

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    WORK_DIR.mkdir(parents=True, mode=0o700)

    work_images = WORK_DIR / "images"
    print(f"[e2e-backend] copying fixture -> {work_images}")
    shutil.copytree(SRC_IMAGES, work_images, ignore=_ignore_backups)
    _strip_credentials(work_images / "vault.db")
    _seed_model_folder(MODEL_FOLDER)
    _seed_reference_folder(REFERENCE_FOLDER)

    # Build a lean server-config from the fixture's own config, overriding the
    # fields that matter for a fast, hermetic, password-auth test server.
    base_config = json.loads((SRC_DATA / "server-config.json").read_text())
    base_config.update(server_overrides(work_images))
    config_path = WORK_DIR / "server-config.json"
    config_path.write_text(json.dumps(base_config, indent=2))

    _harden_permissions(WORK_DIR)

    print(f"[e2e-backend] starting server on http://127.0.0.1:{PORT}")
    os.execvp(
        sys.executable,
        [sys.executable, "-m", "pixlstash.app", "--server-config", str(config_path)],
    )


if __name__ == "__main__":
    sys.exit(main())
