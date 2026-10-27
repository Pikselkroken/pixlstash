"""Refresh the scratch copy of the desktop app's data that a dev run uses.

``scripts/desktop-dev.sh run`` points Electron at this copy instead of the real
data, so a branch's migrations only ever upgrade the copy. The next run starts
again from the real data, at the revision the installed app understands: that
is the downgrade a development build needs, without any migration running
backwards.

Copied: every ``*.json`` beside ``server-config.json``, the ``training-presets``
folder, a consistent snapshot of ``hub.db``, and every registered library. A
library is copied into a hidden ``.<name>.pixlstash-dev-scratch`` folder beside
it: its ``vault.db`` snapshotted, every other file hard-linked, which costs no
disk space but needs the copy on the library's own filesystem. PixlStash
rewrites a picture by writing a new file and renaming it over the old one, so a
change in the copy never reaches the original. The copied hub's registrations
are relocated onto the copies; a library that is unreachable or mid-import is
forgotten in the copy rather than left pointing at the real folder.

A copied vault that a newer branch has already migrated past this checkout is
migrated back down on the copy: the newer migration files are read from
whichever branch in this repository has them, and their ``downgrade()`` runs.
The hub has no downgrades, so a hub ahead of this checkout still refuses.

Shared, not copied: model folders, reference folders and import folders. An
import folder's delete-after-import is switched off in the copy, since it would
delete real files into a library the next run throws away.

Usage:
    python scripts/desktop_dev_scratch.py [--keep]

Prints the scratch directory. ``--keep`` reuses an existing copy as it is.
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from platformdirs import user_config_dir
from sqlalchemy import create_engine, pool

import pixlstash
from pixlstash.hub.bootstrap import known_vault_revisions
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.registry import VAULT_FILENAME, LibraryRegistry

REAL_DIR = user_config_dir("pixlstash-desktop", roaming=True)
SCRATCH_DIR = user_config_dir("pixlstash-desktop-dev-scratch", roaming=True)
COPY_SUFFIX = ".pixlstash-dev-scratch"
# Inside SCRATCH_DIR: the library copies the last refresh made, for the next
# one to remove.
COPIES_FILE = "library-copies.json"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = Path(pixlstash.__file__).resolve().parent / "migrations"
_DOWN_REVISION = re.compile(
    r'^down_revision(?:\s*:\s*[^=]+)?\s*=\s*["\']([^"\']+)', re.M
)


def _snapshot_sqlite(source: str, target: str) -> None:
    """Copy a live SQLite database consistently, without writing to *source*."""
    with (
        contextlib.closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as src,
        contextlib.closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)
    os.chmod(target, 0o600)


def _link_library(source: str, target: str) -> None:
    """Hard-link every file under *source* into *target*, except the vault."""
    skip = {VAULT_FILENAME + suffix for suffix in ("",) + _SQLITE_SIDECARS}
    for folder, _dirs, files in os.walk(source):
        relative = os.path.relpath(folder, source)
        destination = os.path.normpath(os.path.join(target, relative))
        os.makedirs(destination, exist_ok=True)
        # makedirs applies the umask; the vault guard refuses a group-writable
        # library, so give each folder its original's mode instead.
        os.chmod(destination, os.stat(folder).st_mode & 0o7777)
        for name in files:
            if relative == "." and name in skip:
                continue
            os.link(os.path.join(folder, name), os.path.join(destination, name))


def _disarm_import_deletes(vault_path: str) -> None:
    with contextlib.closing(sqlite3.connect(vault_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(import_folder)")}
        if "delete_after_import" in columns:
            conn.execute("UPDATE import_folder SET delete_after_import = 0")
            conn.commit()


def _migration_from_any_branch(revision: str) -> str:
    """Return the source of *revision*'s migration from any ref in this repo.

    Assumes the file is named for its revision id, as every one here is.
    """
    path = f"pixlstash/migrations/versions/{revision}.py"
    commit = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "log", "--all", "-1", "--format=%H", "--", path],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not commit:
        raise SystemExit(
            f"desktop-dev: no branch in {REPO_ROOT} has the migration {revision}, "
            "so the library copy cannot be migrated back down to this checkout."
        )
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{commit}:{path}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _downgrade_to_this_checkout(vault_path: str) -> None:
    """Migrate a copied vault down to this checkout's head, if it is past it."""
    known = known_vault_revisions()
    with contextlib.closing(sqlite3.connect(vault_path)) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone():
            return
        current = [
            row[0] for row in conn.execute("SELECT version_num FROM alembic_version")
        ]
    pending = [revision for revision in current if revision not in known]
    if not pending:
        return
    target = ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    print(
        f"desktop-dev: migrating the copy at {vault_path} down from "
        f"{', '.join(pending)} to {target}",
        file=sys.stderr,
    )
    with tempfile.TemporaryDirectory() as tmp:
        migrations = Path(tmp) / "migrations"
        shutil.copytree(
            MIGRATIONS_DIR, migrations, ignore=shutil.ignore_patterns("__pycache__")
        )
        while pending:
            revision = pending.pop()
            source = _migration_from_any_branch(revision)
            (migrations / "versions" / f"{revision}.py").write_text(
                source, encoding="utf-8"
            )
            parent = _DOWN_REVISION.search(source)
            if (
                parent
                and parent.group(1) not in known
                and not (migrations / "versions" / f"{parent.group(1)}.py").exists()
            ):
                pending.append(parent.group(1))
        config = Config()
        config.set_main_option("script_location", str(migrations))
        engine = create_engine(f"sqlite:///{vault_path}", poolclass=pool.NullPool)
        try:
            with engine.connect() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, target)
        finally:
            engine.dispose()


def _remove_previous(scratch_dir: str) -> None:
    """Remove the last refresh: its library copies, then the scratch folder."""
    try:
        with open(os.path.join(scratch_dir, COPIES_FILE), encoding="utf-8") as handle:
            copies = json.load(handle)
    except FileNotFoundError:
        copies = []
    for copy in copies:
        # Only ever a folder this script named; never trust the list further.
        if os.path.basename(copy).endswith(COPY_SUFFIX) and os.path.isdir(copy):
            shutil.rmtree(copy)
    if os.path.lexists(scratch_dir):
        shutil.rmtree(scratch_dir)


def refresh(real_dir: str, scratch_dir: str) -> None:
    """Replace *scratch_dir* with a fresh copy of *real_dir* and its libraries."""
    _remove_previous(scratch_dir)
    os.makedirs(scratch_dir, mode=0o700)
    for path in glob.glob(os.path.join(real_dir, "*.json")):
        shutil.copy2(path, scratch_dir)
    presets = os.path.join(real_dir, "training-presets")
    if os.path.isdir(presets):
        shutil.copytree(presets, os.path.join(scratch_dir, "training-presets"))
    real_hub = os.path.join(real_dir, "hub.db")
    if not os.path.isfile(real_hub):
        return  # Never set up: the dev run gets the first-run wizard.
    scratch_hub = os.path.join(scratch_dir, "hub.db")
    _snapshot_sqlite(real_hub, scratch_hub)

    moved: dict[str, str] = {}
    hub = HubDatabase(scratch_hub)
    try:
        registry = LibraryRegistry(hub)
        for library in registry.list_libraries():
            if library.pending_import_at or not library.is_reachable:
                print(
                    f'desktop-dev: leaving out "{library.name}" ({library.path}): '
                    "unreachable or mid-import",
                    file=sys.stderr,
                )
                registry.forget(library.id, allow_active=True)
                continue
            real = os.path.realpath(library.path)
            copy = os.path.join(
                os.path.dirname(real), f".{os.path.basename(real)}{COPY_SUFFIX}"
            )
            # Recorded before it exists, so a refresh that fails half-way is
            # still cleaned up by the next one.
            moved[real] = copy
            with open(
                os.path.join(scratch_dir, COPIES_FILE), "w", encoding="utf-8"
            ) as handle:
                json.dump(sorted(moved.values()), handle)
            if os.path.isdir(copy):
                shutil.rmtree(copy)
            _link_library(real, copy)
            vault = os.path.join(copy, VAULT_FILENAME)
            _snapshot_sqlite(library.vault_path, vault)
            _downgrade_to_this_checkout(vault)
            _disarm_import_deletes(vault)
            registry.relocate(library.id, copy, check_folder_overlap=False)
    finally:
        hub.close()

    # The legacy single-library setting is still read at start-up, and the log
    # would land in the real app's: point both at the copy.
    config_path = os.path.join(scratch_dir, "server-config.json")
    if os.path.isfile(config_path):
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        root = config.get("image_root")
        if root and os.path.realpath(root) in moved:
            config["image_root"] = moved[os.path.realpath(root)]
        log_file = config.get("log_file")
        if log_file and os.path.dirname(os.path.realpath(log_file)) == os.path.realpath(
            real_dir
        ):
            config["log_file"] = os.path.join(scratch_dir, os.path.basename(log_file))
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--keep", action="store_true", help="Reuse an existing scratch copy."
    )
    args = parser.parse_args()
    if not (args.keep and os.path.isdir(SCRATCH_DIR)):
        refresh(REAL_DIR, SCRATCH_DIR)
    print(SCRATCH_DIR)


if __name__ == "__main__":
    main()
