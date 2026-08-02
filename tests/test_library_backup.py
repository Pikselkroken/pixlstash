"""Backing a library up, and the honesty the output owes the user.

Three properties carry this feature: the copy is consistent even while the
library is open, the archive contains the hub and says so, and it does **not**
contain reference folders and says that too. The last one is the failure users
would otherwise discover only when restoring.
"""

import json
import os
import sqlite3
import tarfile

import pytest
import zstandard

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.registry import LibraryRegistry
from pixlstash.services.library_backup_service import BackupError, create_backup


def make_vault(folder, *, pictures=3, reference_folders=()):
    """Build a vault-shaped database with some images beside it."""
    os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(os.path.join(folder, "vault.db"))
    conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    conn.execute("INSERT INTO alembic_version VALUES ('0094_pending')")
    conn.execute("CREATE TABLE picture (id INTEGER PRIMARY KEY, file_path TEXT)")
    for index in range(pictures):
        conn.execute("INSERT INTO picture (file_path) VALUES (?)", (f"{index}.png",))
    conn.execute("CREATE TABLE referencefolder (id INTEGER PRIMARY KEY, folder TEXT)")
    for path in reference_folders:
        conn.execute("INSERT INTO referencefolder (folder) VALUES (?)", (path,))
    conn.commit()
    conn.close()

    for index in range(pictures):
        with open(os.path.join(folder, f"{index}.png"), "wb") as handle:
            handle.write(b"not really a png")
    return folder


@pytest.fixture
def registry(tmp_path):
    hub = HubDatabase(str(tmp_path / "hub.db"))
    yield LibraryRegistry(hub)
    hub.close()


@pytest.fixture
def library(registry, tmp_path):
    return registry.attach(make_vault(str(tmp_path / "library")), "Family Photos")


def read_archive(path):
    """Return ``{arcname: bytes}`` for a .tar.zst or .tar archive."""
    if path.endswith(".zst"):
        with open(path, "rb") as raw:
            data = zstandard.ZstdDecompressor().stream_reader(raw).read()
        import io

        fileobj = io.BytesIO(data)
        tar = tarfile.open(fileobj=fileobj, mode="r")
    else:
        tar = tarfile.open(path, "r")
    try:
        return {
            member.name: tar.extractfile(member).read()
            for member in tar.getmembers()
            if member.isfile()
        }
    finally:
        tar.close()


class TestWhatIsInTheArchive:
    def test_it_holds_the_database_the_images_and_the_hub(
        self, registry, library, tmp_path
    ):
        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )

        entries = read_archive(result.path)
        assert "vault.db" in entries
        assert "hub.db" in entries, "credentials must be recoverable with the pictures"
        assert "manifest.json" in entries
        assert sum(1 for name in entries if name.startswith("images/")) == 3

    def test_the_manifest_records_what_this_is(self, registry, library, tmp_path):
        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )

        manifest = json.loads(read_archive(result.path)["manifest.json"])
        assert manifest["library_uuid"] == library.uuid
        assert manifest["library_name"] == "Family Photos"
        assert manifest["picture_count"] == 3
        assert manifest["vault_revision"] == "0094_pending"
        assert manifest["contains_hub"] is True

    def test_the_live_database_files_are_not_copied_raw(
        self, registry, library, tmp_path
    ):
        """Only the VACUUM INTO copy belongs in the archive.

        A raw copy of an open database, or a stray -wal, would be inconsistent
        and useless without the exact file it belongs to.
        """
        open(os.path.join(library.path, "vault.db-wal"), "wb").write(b"junk")

        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )

        entries = read_archive(result.path)
        assert not any("vault.db-wal" in name for name in entries)
        assert not any(name == "images/vault.db" for name in entries)

    def test_metadata_only_skips_the_images(self, registry, library, tmp_path):
        result = create_backup(
            library,
            str(tmp_path / "meta.tar.zst"),
            registry.hub_path,
            metadata_only=True,
        )

        entries = read_archive(result.path)
        assert "vault.db" in entries
        assert not any(name.startswith("images/") for name in entries)
        assert result.metadata_only is True

    def test_an_uncompressed_archive_is_a_plain_tar(self, registry, library, tmp_path):
        result = create_backup(
            library, str(tmp_path / "plain.tar"), registry.hub_path, compress=False
        )

        assert tarfile.is_tarfile(result.path)
        assert "vault.db" in read_archive(result.path)


class TestConsistencyAndSafety:
    def test_the_archived_database_is_readable_and_complete(
        self, registry, library, tmp_path
    ):
        """VACUUM INTO gives a real database, not a byte copy of a live file."""
        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )

        extracted = tmp_path / "extracted.db"
        extracted.write_bytes(read_archive(result.path)["vault.db"])
        conn = sqlite3.connect(str(extracted))
        try:
            assert conn.execute("SELECT COUNT(*) FROM picture").fetchone()[0] == 3
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()

    def test_backing_up_does_not_modify_the_library(self, registry, library, tmp_path):
        vault = os.path.join(library.path, "vault.db")
        before = open(vault, "rb").read()

        create_backup(library, str(tmp_path / "out.tar.zst"), registry.hub_path)

        assert open(vault, "rb").read() == before

    def test_the_archive_is_owner_readable_only(self, registry, library, tmp_path):
        """It contains the hub, so it contains the password and token hashes."""
        import stat

        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )

        assert stat.S_IMODE(os.stat(result.path).st_mode) == 0o600

    def test_an_unreadable_library_is_refused_with_a_usable_message(
        self, registry, library, tmp_path
    ):
        os.remove(os.path.join(library.path, "vault.db"))

        with pytest.raises(BackupError) as excinfo:
            create_backup(library, str(tmp_path / "out.tar.zst"), registry.hub_path)

        assert "not readable" in str(excinfo.value)


class TestReferenceFolders:
    def test_external_folders_are_reported_and_not_included(self, registry, tmp_path):
        """The failure a user would otherwise find only when restoring."""
        folder = make_vault(
            str(tmp_path / "with-refs"),
            reference_folders=["/mnt/external/shoot-a", "/mnt/external/shoot-b"],
        )
        library = registry.attach(folder, "With refs")

        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )

        assert result.has_external_folders
        assert len(result.reference_folders) == 2
        manifest = json.loads(read_archive(result.path)["manifest.json"])
        assert manifest["reference_folders"] == result.reference_folders

    def test_a_library_without_reference_folders_says_nothing(
        self, registry, library, tmp_path
    ):
        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )
        assert not result.has_external_folders


class TestDestinationHandling:
    def test_a_directory_destination_gets_a_dated_name(
        self, registry, library, tmp_path
    ):
        out = tmp_path / "backups"
        out.mkdir()

        result = create_backup(library, str(out), registry.hub_path)

        assert result.path.startswith(str(out))
        assert result.path.endswith(".tar.zst")
        assert "Family-Photos" in os.path.basename(result.path)


class TestTheCli:
    def test_backup_reports_the_hub_and_the_external_folders(self, tmp_path, capsys):
        from pixlstash.cli import main as cli_main

        hub_path = str(tmp_path / "hub.db")
        folder = make_vault(
            str(tmp_path / "cli-library"), reference_folders=["/mnt/elsewhere"]
        )
        assert cli_main(["--hub", hub_path, "libraries", "attach", folder]) == 0
        capsys.readouterr()

        code = cli_main(
            [
                "--hub",
                hub_path,
                "libraries",
                "backup",
                "cli-library",
                str(tmp_path / "out.tar.zst"),
            ]
        )

        captured = capsys.readouterr()
        assert code == 0
        assert "login and tokens" in captured.out
        assert "NOT in the archive" in captured.err
        assert "/mnt/elsewhere" in captured.err

    def test_relocate_keeps_the_identity(self, tmp_path, capsys):
        import shutil

        from pixlstash.cli import main as cli_main

        hub_path = str(tmp_path / "hub.db")
        folder = make_vault(str(tmp_path / "movable"))
        assert cli_main(["--hub", hub_path, "libraries", "attach", folder]) == 0
        capsys.readouterr()

        moved = str(tmp_path / "moved")
        shutil.move(folder, moved)

        code = cli_main(["--hub", hub_path, "libraries", "relocate", "movable", moved])

        captured = capsys.readouterr()
        assert code == 0
        assert "keep working" in captured.out
        assert moved in captured.out
