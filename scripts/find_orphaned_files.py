"""Find (and optionally remove) files under the vault image root that have no
corresponding row in the database.

This is the disk→DB direction. The complementary ``MissingFilePurgeTask``
handles DB→disk (picture rows whose file is gone). Orphaned files typically
accumulate from:

  * restoring an older snapshot — pictures imported *after* that snapshot keep
    their files on disk but lose their DB row;
  * crashes between writing a file and committing its row;
  * manual edits to the image folder.

What counts as "not orphaned" (kept):
  * every managed picture file (``Picture.file_path`` joined to image_root);
  * its thumbnail ``{stem}_thumb.webp``;
  * its sidecar caption ``{stem}.txt`` / ``{stem}.caption``;
  * system content under image_root: ``vault.db`` (+ ``-wal``/``-shm``/
    ``-journal``), and the ``snapshots/``, ``.ref_thumbs/``, ``tmp/`` and
    ``.orphan_trash/`` directories.

Reference-folder pictures have absolute ``file_path`` (outside image_root), so
they are never scanned.

Usage:
    python scripts/find_orphaned_files.py <image_root>               # report only
    python scripts/find_orphaned_files.py <image_root> --delete      # move to .orphan_trash/
    python scripts/find_orphaned_files.py <image_root> --hard-delete # permanently remove

Examples:
    python scripts/find_orphaned_files.py ~/.config/pixlstash/images
    python scripts/find_orphaned_files.py /vault --delete
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys

# Kept in sync with pixlstash.utils.image_processing.image_utils.
_THUMBNAIL_SUFFIX = "_thumb.webp"
_SIDECAR_EXTENSIONS = (".txt", ".caption")

# Top-level directories under image_root that hold system / derived content
# and must never be treated as orphans. Pruned from the walk entirely.
_EXCLUDED_DIRS = {"snapshots", ".ref_thumbs", "tmp", ".orphan_trash"}

# System files that legitimately live at the image_root top level.
_EXCLUDED_FILES = {
    "vault.db",
    "vault.db-wal",
    "vault.db-shm",
    "vault.db-journal",
}

_TRASH_DIRNAME = ".orphan_trash"


def _known_paths(db_path: str, image_root: str) -> set[str]:
    """Return the absolute paths the DB accounts for: every managed picture
    plus its thumbnail and sidecar caption companions."""
    known: set[str] = set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT file_path FROM picture WHERE file_path IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    for (file_path,) in rows:
        if not file_path:
            continue
        # Reference-folder pictures store an absolute path outside image_root;
        # they (and their .ref_thumbs thumbnail) are never under the walk.
        if os.path.isabs(file_path):
            continue
        resolved = os.path.normpath(os.path.join(image_root, file_path))
        known.add(resolved)
        stem, _ext = os.path.splitext(resolved)
        known.add(f"{stem}{_THUMBNAIL_SUFFIX}")
        for sidecar_ext in _SIDECAR_EXTENSIONS:
            known.add(f"{stem}{sidecar_ext}")
    return known


def _classify(path: str) -> str:
    name = os.path.basename(path).lower()
    if name.endswith(_THUMBNAIL_SUFFIX):
        return "thumbnail"
    if any(name.endswith(ext) for ext in _SIDECAR_EXTENSIONS):
        return "sidecar caption"
    return "image / other"


def _iter_orphans(image_root: str, known: set[str]):
    """Yield absolute paths of files under image_root not accounted for."""
    for dirpath, dirnames, filenames in os.walk(image_root):
        # Prune excluded directories (only at the top level by name, plus any
        # __pycache__ anywhere).
        if dirpath == image_root:
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]

        for fname in filenames:
            if dirpath == image_root and fname in _EXCLUDED_FILES:
                continue
            abspath = os.path.normpath(os.path.join(dirpath, fname))
            if abspath in known:
                continue
            yield abspath


def _human_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f} {unit}"
        f /= 1024
    return f"{n} B"


def _trash_target(image_root: str, orphan: str) -> str:
    """Map an orphan to its destination under image_root/.orphan_trash/,
    preserving the relative path; disambiguates collisions with a suffix."""
    rel = os.path.relpath(orphan, image_root)
    target = os.path.join(image_root, _TRASH_DIRNAME, rel)
    base, ext = os.path.splitext(target)
    n = 1
    while os.path.exists(target):
        target = f"{base}.{n}{ext}"
        n += 1
    return target


def main() -> None:
    # Windows consoles (and piped stdout) default to cp1252, which can't
    # encode the ✓/… glyphs this script prints — emitting one raises
    # UnicodeEncodeError. Force UTF-8 so output is identical on every platform.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description=(
            "Find files under the vault image root with no matching database "
            "row. Reports by default; --delete moves them to .orphan_trash/, "
            "--hard-delete removes them permanently."
        )
    )
    parser.add_argument("image_root", help="Path to the vault image root directory.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--delete",
        action="store_true",
        help="Move orphans into <image_root>/.orphan_trash/ (recoverable).",
    )
    group.add_argument(
        "--hard-delete",
        action="store_true",
        help="Permanently delete orphans (prompts for confirmation).",
    )
    args = parser.parse_args()

    image_root = os.path.abspath(args.image_root)
    if not os.path.isdir(image_root):
        print(f"Error: image_root does not exist: {image_root}", file=sys.stderr)
        sys.exit(1)

    db_path = os.path.join(image_root, "vault.db")
    if not os.path.isfile(db_path):
        print(f"Error: no vault.db at {db_path}", file=sys.stderr)
        sys.exit(1)

    known = _known_paths(db_path, image_root)
    orphans = sorted(_iter_orphans(image_root, known))

    if not orphans:
        print("No orphaned files found. ✓")
        return

    # Group for the report.
    by_kind: dict[str, list[str]] = {}
    total_bytes = 0
    for path in orphans:
        try:
            total_bytes += os.path.getsize(path)
        except OSError:
            pass
        by_kind.setdefault(_classify(path), []).append(path)

    print(
        f"Found {len(orphans)} orphaned file(s) under {image_root} "
        f"({_human_bytes(total_bytes)}):"
    )
    for kind in sorted(by_kind):
        paths = by_kind[kind]
        print(f"\n  {kind} ({len(paths)}):")
        for path in paths[:50]:
            print(f"    {os.path.relpath(path, image_root)}")
        if len(paths) > 50:
            print(f"    … and {len(paths) - 50} more")

    if not args.delete and not args.hard_delete:
        print(
            "\nReport only. Re-run with --delete (move to .orphan_trash/) or "
            "--hard-delete (permanent) to remove them."
        )
        return

    if args.hard_delete:
        print(
            f"\nAbout to PERMANENTLY delete {len(orphans)} file(s) "
            f"({_human_bytes(total_bytes)}). This cannot be undone."
        )
        if input("Type 'yes' to continue: ").strip().lower() != "yes":
            print("Aborted; nothing deleted.")
            return
        removed = 0
        for path in orphans:
            try:
                os.remove(path)
                removed += 1
            except OSError as exc:
                print(f"  ! failed to delete {path}: {exc}", file=sys.stderr)
        print(f"Permanently deleted {removed} file(s).")
        return

    # --delete: move to trash, preserving relative structure.
    moved = 0
    for path in orphans:
        target = _trash_target(image_root, path)
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(path, target)
            moved += 1
        except OSError as exc:
            print(f"  ! failed to move {path}: {exc}", file=sys.stderr)
    print(
        f"\nMoved {moved} file(s) to {os.path.join(image_root, _TRASH_DIRNAME)} "
        f"({_human_bytes(total_bytes)} reclaimable). Delete that directory once "
        "you've confirmed nothing important was moved."
    )


if __name__ == "__main__":
    main()
