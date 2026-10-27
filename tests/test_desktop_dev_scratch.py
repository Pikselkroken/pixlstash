"""Tests for scripts/desktop_dev_scratch.py - the dev run's copy of the data."""

import contextlib
import importlib.util
import json
import os
import sqlite3

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.registry import LibraryRegistry

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "desktop_dev_scratch.py",
)


def _load_module():
    spec = importlib.util.spec_from_file_location("desktop_dev_scratch", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _registered_paths(hub_path: str) -> list[str]:
    hub = HubDatabase(hub_path)
    try:
        return [library.path for library in LibraryRegistry(hub).list_libraries()]
    finally:
        hub.close()


def _make_real_data(tmp_path):
    """A desktop data folder with one registered library holding one picture."""
    real_dir = tmp_path / "real"
    library_dir = tmp_path / "pictures" / "library"
    real_dir.mkdir(mode=0o700)
    hub = HubDatabase(str(real_dir / "hub.db"))
    try:
        LibraryRegistry(hub).create(str(library_dir), "Library")
    finally:
        hub.close()
    (library_dir / "a.png").write_bytes(b"picture")
    return real_dir, library_dir


def test_refresh_copies_the_data_and_never_points_at_the_real_library(tmp_path):
    scratch_mod = _load_module()
    real_dir, library_dir = _make_real_data(tmp_path)
    scratch_dir = tmp_path / "scratch"
    real_vault = library_dir / "vault.db"
    with contextlib.closing(sqlite3.connect(real_vault)) as conn:
        conn.execute(
            "INSERT INTO import_folder (folder, label, delete_after_import) "
            "VALUES ('/home/me/inbox', 'inbox', 1)"
        )
        conn.commit()
    with contextlib.closing(sqlite3.connect(real_dir / "hub.db")) as conn:
        conn.execute(
            "INSERT INTO model_folder (path, kind, movable, delete_after_import) "
            "VALUES ('/home/me/models', 'user', 'yes', 1)"
        )
        conn.commit()
    hub = HubDatabase(str(real_dir / "hub.db"))
    try:
        LibraryRegistry(hub).create(
            str(library_dir / "inner"), "Inner", check_folder_overlap=False
        )
    finally:
        hub.close()
    # Written in place by the backend, so a hard link would carry the write back.
    (library_dir / "tmp" / "set_thumbnails").mkdir(parents=True)
    (library_dir / "tmp" / "set_thumbnails" / "set_1.png").write_bytes(b"cache")
    (library_dir / "a_watermarked.png").write_bytes(b"twin")
    (tmp_path / "elsewhere.png").write_bytes(b"outside")
    (library_dir / "linked.png").symlink_to(tmp_path / "elsewhere.png")
    (real_dir / "server-config.json").write_text(
        json.dumps({"image_root": str(library_dir)})
    )

    scratch_mod.refresh(str(real_dir), str(scratch_dir))

    copy = tmp_path / "pictures" / ".library.pixlstash-dev-scratch"
    assert _registered_paths(str(scratch_dir / "hub.db")) == [str(copy)]
    assert sorted(_registered_paths(str(real_dir / "hub.db"))) == [
        str(library_dir),
        str(library_dir / "inner"),
    ]
    assert os.path.samefile(copy / "a.png", library_dir / "a.png")
    assert not (copy / "tmp").exists()
    assert not (copy / "a_watermarked.png").exists()
    assert not (copy / "inner").exists()
    assert not (library_dir / ".inner.pixlstash-dev-scratch").exists()
    assert os.readlink(copy / "linked.png") == str(tmp_path / "elsewhere.png")
    for hub_path, flag in ((scratch_dir / "hub.db", 0), (real_dir / "hub.db", 1)):
        with contextlib.closing(sqlite3.connect(hub_path)) as conn:
            assert conn.execute(
                "SELECT delete_after_import FROM model_folder "
                "WHERE path = '/home/me/models'"
            ).fetchall() == [(flag,)]
    assert not os.path.samefile(copy / "vault.db", real_vault)
    config = json.loads((scratch_dir / "server-config.json").read_text())
    assert config["image_root"] == str(copy)
    with contextlib.closing(sqlite3.connect(copy / "vault.db")) as conn:
        assert conn.execute(
            "SELECT delete_after_import FROM import_folder"
        ).fetchall() == [(0,)]
    with contextlib.closing(sqlite3.connect(real_vault)) as conn:
        assert conn.execute(
            "SELECT delete_after_import FROM import_folder"
        ).fetchall() == [(1,)]

    # A branch's migration lands on the copy; the next refresh starts again
    # from the real revision, and whatever the dev run made is gone.
    with contextlib.closing(sqlite3.connect(real_vault)) as conn:
        real_revision = conn.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()
    with contextlib.closing(sqlite3.connect(copy / "vault.db")) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '9999_from_a_branch'")
        conn.commit()
    (copy / "made-by-the-dev-run.png").write_bytes(b"new")
    scratch_mod.refresh(str(real_dir), str(scratch_dir))
    with contextlib.closing(sqlite3.connect(copy / "vault.db")) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (
            real_revision
        )
    assert not (copy / "made-by-the-dev-run.png").exists()
    assert (library_dir / "a.png").read_bytes() == b"picture"


_NEWER_MIGRATION = """
from alembic import op

revision = "9999_from_a_newer_branch"
down_revision = "{head}"


def upgrade():
    op.create_table("from_a_newer_branch")


def downgrade():
    op.drop_table("from_a_newer_branch")
"""


def test_refresh_migrates_a_copy_from_a_newer_branch_back_down(tmp_path):
    scratch_mod = _load_module()
    real_dir, library_dir = _make_real_data(tmp_path)
    real_vault = library_dir / "vault.db"
    with contextlib.closing(sqlite3.connect(real_vault)) as conn:
        (head,) = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        conn.execute("CREATE TABLE from_a_newer_branch (id INTEGER PRIMARY KEY)")
        conn.execute(
            "UPDATE alembic_version SET version_num = '9999_from_a_newer_branch'"
        )
        conn.commit()
    asked = []

    def from_git(revision):
        asked.append(revision)
        return _NEWER_MIGRATION.format(head=head)

    scratch_mod._migration_from_any_branch = from_git

    scratch_mod.refresh(str(real_dir), str(tmp_path / "scratch"))

    assert asked == ["9999_from_a_newer_branch"]
    copy = tmp_path / "pictures" / ".library.pixlstash-dev-scratch" / "vault.db"
    with contextlib.closing(sqlite3.connect(copy)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
            (head,)
        ]
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'from_a_newer_branch'"
        ).fetchone()
    with contextlib.closing(sqlite3.connect(real_vault)) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall() == [
            ("9999_from_a_newer_branch",)
        ]


def test_refresh_refuses_while_a_dev_run_holds_the_copy(tmp_path):
    scratch_mod = _load_module()
    real_dir, _library_dir = _make_real_data(tmp_path)
    scratch_dir = tmp_path / "scratch"
    scratch_dir.mkdir()
    (scratch_dir / "SingletonLock").symlink_to(f"example-host-{os.getpid()}")

    with pytest.raises(SystemExit, match="another dev run"):
        scratch_mod.refresh(str(real_dir), str(scratch_dir))
    assert (scratch_dir / "SingletonLock").is_symlink()
