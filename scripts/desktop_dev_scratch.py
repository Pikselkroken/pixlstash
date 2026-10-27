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
disk space but needs the copy on the library's own filesystem. A picture is
rewritten by writing a new file and renaming it over the old one, so that never
reaches the original; the few caches written in place instead (``tmp/`` and the
``_watermarked``/``_oriented`` twins) are left out, and the dev run regenerates
them. Symlinks are copied as symlinks. The copied hub's registrations are
relocated onto the copies; a library that is unreachable, mid-import or inside
another library is forgotten in the copy rather than left pointing at the real
folder.

A copied vault that a newer branch has already migrated past this checkout is
migrated back down on the copy: the newer migration files are read from
whichever branch in this repository has them, and their ``downgrade()`` runs.
The hub has no downgrades, so a hub ahead of this checkout still refuses.

Shared, not copied: model folders, reference folders and import folders.
Delete-after-import is switched off on all of them in the copy (import folders
in each vault, model folders in the hub), since it would delete real files into
data the next run throws away. A dev run that deletes or downloads a model, or
deletes a picture living in a reference folder, still changes the real file.

Refuses to refresh while another dev run is using the copy.

Usage:
    python scripts/desktop_dev_scratch.py [--keep]

Prints the scratch directory. ``--keep`` reuses the last copy as it is, if
that refresh finished; a half-made one can still name real folders.
"""

from __future__ import annotations

import argparse
import ast
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
from pixlstash.utils.atomic_write import write_json_atomic

REAL_DIR = user_config_dir("pixlstash-desktop", roaming=True)
SCRATCH_DIR = user_config_dir("pixlstash-desktop-dev-scratch", roaming=True)
COPY_SUFFIX = ".pixlstash-dev-scratch"
# Inside SCRATCH_DIR: the library copies the last refresh made, for the next
# one to remove.
COPIES_FILE = "library-copies.json"
# Inside SCRATCH_DIR: written last, so only a finished refresh is ever kept.
COMPLETE_FILE = "refresh-complete"
_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")
# Files the backend writes in place rather than by write-and-rename: through a
# hard link that would rewrite the real file. All are caches.
# ponytail: a list of today's writers; a new in-place writer into the library
# writes through until it is added here.
_IN_PLACE_CACHE_DIR = "tmp"  # set and face thumbnails
_IN_PLACE_TWINS = ("_watermarked", "_oriented")  # routes/pictures/_serving.py
REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = Path(pixlstash.__file__).resolve().parent / "migrations"


def _snapshot_sqlite(source: str, target: str) -> None:
    """Copy a live SQLite database consistently, without writing to *source*."""
    with (
        contextlib.closing(
            sqlite3.connect(
                # as_uri percent-encodes a "?" or "#" in a folder name.
                f"{Path(source).absolute().as_uri()}?mode=ro",
                uri=True,
            )
        ) as src,
        contextlib.closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)
    os.chmod(target, 0o600)


def _link_library(source: str, target: str, other_libraries: set[str]) -> None:
    """Hard-link the files under *source* into *target*.

    Leaves out the vault, the caches written in place, and any folder that is
    another library or a copy. Symlinks are never followed.
    """
    skip = {VAULT_FILENAME + suffix for suffix in ("",) + _SQLITE_SIDECARS}
    for folder, dirs, files in os.walk(source):
        relative = os.path.relpath(folder, source)
        destination = os.path.normpath(os.path.join(target, relative))
        os.makedirs(destination, exist_ok=True)
        # makedirs applies the umask; the vault guard refuses a group-writable
        # library, so give each folder its original's mode instead.
        os.chmod(destination, os.stat(folder).st_mode & 0o7777)
        for name in list(dirs):
            path = os.path.join(folder, name)
            if (
                (relative == "." and name == _IN_PLACE_CACHE_DIR)
                or name.endswith(COPY_SUFFIX)
                or os.path.realpath(path) in other_libraries
            ):
                dirs.remove(name)
            elif os.path.islink(path):
                os.symlink(os.readlink(path), os.path.join(destination, name))
        names = set(files)
        for name in files:
            path = os.path.join(folder, name)
            if relative == "." and name in skip:
                continue
            # A twin sits beside its original, "a_oriented.png" by "a.png";
            # a picture that merely ends in the suffix is the owner's own.
            stem, ext = os.path.splitext(name)
            if any(
                stem.endswith(twin) and stem[: -len(twin)] + ext in names
                for twin in _IN_PLACE_TWINS
            ):
                continue
            # A symlink is linked as itself, never as its target outside.
            os.link(path, os.path.join(destination, name), follow_symlinks=False)


def _disarm_import_deletes(vault_path: str) -> None:
    with contextlib.closing(sqlite3.connect(vault_path)) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(import_folder)")}
        if "delete_after_import" in columns:
            conn.execute("UPDATE import_folder SET delete_after_import = 0")
            conn.commit()


def _migration_from_any_branch(revision: str) -> str:
    """Return the source of *revision*'s migration from a branch of this repo.

    Local branches and origin's only: the file's downgrade() is run, so a
    fetched pull request from a fork is not a source. Assumes the file is named
    for its revision id, as every one here is.
    """
    if not re.fullmatch(r"\w+", revision):
        raise SystemExit(f"desktop-dev: {revision!r} is not a revision id.")
    path = f"pixlstash/migrations/versions/{revision}.py"
    commit = subprocess.run(
        [
            "git",
            "-C",
            str(REPO_ROOT),
            "log",
            "--branches",
            "--remotes=origin",
            # Only commits after which the file exists: never one deleting it.
            "--diff-filter=AM",
            "-1",
            "--format=%H",
            "--",
            path,
        ],
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


def _down_revisions(source: str) -> list[str]:
    """Return the parents a migration's source names in ``down_revision``.

    Parsed rather than matched, for the tuple a merge migration uses and the
    parenthesised string some here wrap over two lines.
    """
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "down_revision" for t in targets):
            value = ast.literal_eval(node.value)
            if value is None:
                return []
            return [value] if isinstance(value, str) else list(value)
    return []


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
            for parent in _down_revisions(source):
                if (
                    parent not in known
                    and parent not in pending
                    and not (migrations / "versions" / f"{parent}.py").exists()
                ):
                    pending.append(parent)
        config = Config()
        config.set_main_option("script_location", str(migrations))
        # Through a file URI, as in _snapshot_sqlite: a "?" or "#" in a folder
        # name would otherwise end SQLAlchemy's URL.
        uri = Path(vault_path).absolute().as_uri()
        engine = create_engine(
            "sqlite://",
            creator=lambda: sqlite3.connect(uri, uri=True),
            poolclass=pool.NullPool,
        )
        try:
            with engine.connect() as connection:
                config.attributes["connection"] = connection
                command.downgrade(config, target)
        finally:
            engine.dispose()


def _in_use(scratch_dir: str) -> bool:
    """True while an Electron run holds the copy's single-instance lock."""
    lock = os.path.join(scratch_dir, "SingletonLock")
    if not os.path.islink(lock):
        return False
    pid = os.readlink(lock).rpartition("-")[2]  # "<hostname>-<pid>"
    if not pid.isdigit():
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False  # Left by a run that crashed.
    except PermissionError:
        return False  # Another user's process: the pid was recycled.
    # A crashed run's pid may since have been recycled. Where /proc shows the
    # environment, only a process started for this copy counts; elsewhere a
    # live pid is taken at its word.
    environ = f"/proc/{pid}/environ"
    if not os.path.exists(environ):
        return True
    marker = f"PIXLSTASH_DESKTOP_USER_DATA={scratch_dir}".encode()
    try:
        with open(environ, "rb") as handle:
            return marker in handle.read().split(b"\0")
    except FileNotFoundError:
        return False  # Exited between the checks.
    except PermissionError:
        return False  # Not dumpable, so not the dev run's Electron, whose is readable.


def _remove_previous(scratch_dir: str) -> None:
    """Remove the last refresh: its library copies, then the scratch folder."""
    copies_file = os.path.join(scratch_dir, COPIES_FILE)
    copies = []
    if os.path.isfile(copies_file):
        with open(copies_file, encoding="utf-8") as handle:
            copies = json.load(handle)
    for copy in copies:
        # Only ever a folder this script named; never trust the list further.
        if os.path.basename(copy).endswith(COPY_SUFFIX) and os.path.isdir(copy):
            shutil.rmtree(copy)
    if os.path.lexists(scratch_dir):
        shutil.rmtree(scratch_dir)


def _copy_libraries(real_hub: str, scratch_dir: str) -> dict[str, str]:
    """Copy the hub and every library it registers; map each real folder to its copy."""
    scratch_hub = os.path.join(scratch_dir, "hub.db")
    _snapshot_sqlite(real_hub, scratch_hub)
    moved: dict[str, str] = {}
    hub = HubDatabase(scratch_hub)
    try:
        registry = LibraryRegistry(hub)
        libraries = registry.list_libraries()
        roots = {os.path.realpath(library.path) for library in libraries}
        for library in libraries:
            real = os.path.realpath(library.path)
            nested = any(real.startswith(root + os.sep) for root in roots)
            if library.pending_import_at or not library.is_reachable or nested:
                print(
                    f'desktop-dev: leaving out "{library.name}" ({library.path}): '
                    "unreachable, mid-import or inside another library",
                    file=sys.stderr,
                )
                registry.forget(library.id, allow_active=True)
                continue
            copy = os.path.join(
                os.path.dirname(real), f".{os.path.basename(real)}{COPY_SUFFIX}"
            )
            # Recorded before it exists, so a refresh that fails half-way is
            # still cleaned up by the next one.
            moved[real] = copy
            write_json_atomic(
                os.path.join(scratch_dir, COPIES_FILE), sorted(moved.values())
            )
            if os.path.isdir(copy):
                shutil.rmtree(copy)
            try:
                _link_library(real, copy, roots - {real})
            except OSError as exc:
                raise SystemExit(
                    f"desktop-dev: could not copy {real} into {copy} ({exc}). The "
                    "copy hard-links pictures, so it must be on the library's own "
                    "filesystem and its parent folder must be writable."
                ) from exc
            vault = os.path.join(copy, VAULT_FILENAME)
            _snapshot_sqlite(library.vault_path, vault)
            _downgrade_to_this_checkout(vault)
            _disarm_import_deletes(vault)
            registry.relocate(library.id, copy, check_folder_overlap=False)
        with hub.transaction() as conn:
            conn.execute("UPDATE model_folder SET delete_after_import = 0")
    finally:
        hub.close()
    return moved


def refresh(real_dir: str, scratch_dir: str) -> None:
    """Replace *scratch_dir* with a fresh copy of *real_dir* and its libraries."""
    if _in_use(scratch_dir):
        raise SystemExit(
            f"desktop-dev: another dev run is using {scratch_dir}; quit it first."
        )
    _remove_previous(scratch_dir)
    os.makedirs(scratch_dir, mode=0o700)
    for path in glob.glob(os.path.join(real_dir, "*.json")):
        shutil.copy2(path, scratch_dir)
    presets = os.path.join(real_dir, "training-presets")
    if os.path.isdir(presets):
        shutil.copytree(presets, os.path.join(scratch_dir, "training-presets"))
    real_hub = os.path.join(real_dir, "hub.db")
    # No hub means the dev run gets the first-run wizard; the config below is
    # still rewritten, since start-up would otherwise register image_root.
    moved = _copy_libraries(real_hub, scratch_dir) if os.path.isfile(real_hub) else {}

    # With no usable active library, start-up registers and migrates
    # image_root, so it never names a real folder: a copy, or an empty library
    # inside the scratch folder. The log would land in the real app's too.
    config_path = os.path.join(scratch_dir, "server-config.json")
    if os.path.isfile(config_path):
        with open(config_path, encoding="utf-8") as handle:
            config = json.load(handle)
        root = config.get("image_root")
        if root:
            config["image_root"] = moved.get(
                os.path.realpath(root), os.path.join(scratch_dir, "library")
            )
        log_file = config.get("log_file")
        if log_file and os.path.dirname(os.path.realpath(log_file)) == os.path.realpath(
            real_dir
        ):
            config["log_file"] = os.path.join(scratch_dir, os.path.basename(log_file))
        write_json_atomic(config_path, config)


def prepare(real_dir: str, scratch_dir: str, *, keep: bool = False) -> None:
    """Refresh *scratch_dir*, unless *keep* and the last refresh finished."""
    complete = os.path.join(scratch_dir, COMPLETE_FILE)
    if keep and os.path.isfile(complete):
        return
    refresh(real_dir, scratch_dir)
    Path(complete).touch()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--keep",
        action="store_true",
        help="Reuse the last scratch copy, if that refresh finished.",
    )
    args = parser.parse_args()
    prepare(REAL_DIR, SCRATCH_DIR, keep=args.keep)
    print(SCRATCH_DIR)


if __name__ == "__main__":
    main()
