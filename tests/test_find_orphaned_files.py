"""Tests for scripts/find_orphaned_files.py — the disk→DB orphan scanner.

Builds a minimal image_root (a sqlite ``picture`` table plus files on disk)
without booting a full Vault, so these stay fast and stdlib-only.
"""

import importlib.util
import os
import sqlite3
import subprocess
import sys

import pytest

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "find_orphaned_files.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("find_orphaned_files", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _touch(path: str, content: bytes = b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


def _make_vault(tmp: str, picture_paths: list[str]) -> str:
    """Create image_root/vault.db with a picture table and the given rows."""
    db = os.path.join(tmp, "vault.db")
    conn = sqlite3.connect(db)
    try:
        conn.execute("CREATE TABLE picture (id INTEGER PRIMARY KEY, file_path TEXT)")
        conn.executemany(
            "INSERT INTO picture (file_path) VALUES (?)",
            [(p,) for p in picture_paths],
        )
        conn.commit()
    finally:
        conn.close()
    return db


@pytest.fixture
def vault(tmp_path):
    """A populated image_root: one managed picture (with thumb + sidecar),
    several orphans, and system content that must be ignored."""
    root = str(tmp_path)
    _make_vault(root, ["2026/05/31/keep.jpg"])

    # Managed picture + its legitimate companions (all DB-accounted).
    _touch(os.path.join(root, "2026/05/31/keep.jpg"))
    _touch(os.path.join(root, "2026/05/31/keep_thumb.webp"))
    _touch(os.path.join(root, "2026/05/31/keep.txt"))

    # Orphans: image with no row, its stray thumb + sidecar, a loose file.
    _touch(os.path.join(root, "2026/05/31/orphan.jpg"))
    _touch(os.path.join(root, "2026/05/31/orphan_thumb.webp"))
    _touch(os.path.join(root, "2026/05/31/orphan.caption"))
    _touch(os.path.join(root, "stray.bin"))

    # System content that must be ignored.
    _touch(os.path.join(root, "vault.db-wal"))
    _touch(os.path.join(root, "snapshots/2026/05/31/abc.sqlite"))
    _touch(os.path.join(root, ".ref_thumbs/ref_thumb.webp"))
    _touch(os.path.join(root, "tmp/set_thumbnails/cache.webp"))
    return root


def test_detects_exactly_the_orphans(vault):
    mod = _load_module()
    known = mod._known_paths(os.path.join(vault, "vault.db"), vault)
    orphans = {os.path.relpath(p, vault) for p in mod._iter_orphans(vault, known)}

    assert orphans == {
        os.path.join("2026", "05", "31", "orphan.jpg"),
        os.path.join("2026", "05", "31", "orphan_thumb.webp"),
        os.path.join("2026", "05", "31", "orphan.caption"),
        "stray.bin",
    }, f"unexpected orphan set: {orphans}"


def test_keeps_managed_picture_and_companions(vault):
    mod = _load_module()
    known = mod._known_paths(os.path.join(vault, "vault.db"), vault)
    orphan_rel = {os.path.relpath(p, vault) for p in mod._iter_orphans(vault, known)}

    for kept in (
        "2026/05/31/keep.jpg",
        "2026/05/31/keep_thumb.webp",
        "2026/05/31/keep.txt",
    ):
        assert os.path.normpath(kept) not in orphan_rel, (
            f"{kept} (DB-accounted) must not be flagged"
        )


def test_ignores_system_dirs_and_db_files(vault):
    mod = _load_module()
    known = mod._known_paths(os.path.join(vault, "vault.db"), vault)
    orphan_rel = {os.path.relpath(p, vault) for p in mod._iter_orphans(vault, known)}

    assert not any(
        r.startswith(("snapshots", ".ref_thumbs", "tmp")) or r.startswith("vault.db")
        for r in orphan_rel
    ), f"system content must be excluded; got {orphan_rel}"


def test_absolute_reference_paths_are_not_scanned(tmp_path):
    """A reference-folder picture (absolute file_path outside image_root) is
    never under the walk, so it is neither known-as-file nor flagged."""
    root = str(tmp_path / "vault")
    os.makedirs(root)
    ext = str(tmp_path / "external" / "ref.jpg")
    _touch(ext)
    _make_vault(root, [ext])  # absolute path row

    mod = _load_module()
    known = mod._known_paths(os.path.join(root, "vault.db"), root)
    # Absolute picture contributes nothing under image_root.
    assert all(os.path.commonpath([root, k]) == root for k in known) or not known
    orphans = list(mod._iter_orphans(root, known))
    assert orphans == [], f"external reference file must not be walked: {orphans}"


def test_delete_moves_orphans_to_trash(vault):
    """End-to-end --delete: orphans move under .orphan_trash/ preserving
    structure; DB-accounted files stay put."""
    result = subprocess.run(
        [sys.executable, _SCRIPT, vault, "--delete"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    # Orphans gone from their original location, present under trash.
    assert not os.path.exists(os.path.join(vault, "2026/05/31/orphan.jpg"))
    assert os.path.isfile(os.path.join(vault, ".orphan_trash", "2026/05/31/orphan.jpg"))
    assert not os.path.exists(os.path.join(vault, "stray.bin"))
    assert os.path.isfile(os.path.join(vault, ".orphan_trash", "stray.bin"))

    # Managed picture + companions untouched.
    assert os.path.isfile(os.path.join(vault, "2026/05/31/keep.jpg"))
    assert os.path.isfile(os.path.join(vault, "2026/05/31/keep_thumb.webp"))

    # A second run finds nothing (trash is excluded, orphans already moved).
    rerun = subprocess.run(
        [sys.executable, _SCRIPT, vault],
        capture_output=True,
        text=True,
    )
    assert "No orphaned files found" in rerun.stdout, rerun.stdout


def test_clean_vault_reports_nothing(tmp_path):
    root = str(tmp_path)
    _make_vault(root, ["a/b.jpg"])
    _touch(os.path.join(root, "a/b.jpg"))
    result = subprocess.run(
        [sys.executable, _SCRIPT, root],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "No orphaned files found" in result.stdout, result.stdout
