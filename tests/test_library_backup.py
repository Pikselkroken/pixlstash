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
    os.chmod(folder, 0o700)
    conn = sqlite3.connect(os.path.join(folder, "vault.db"))
    conn.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
    conn.execute(
        "INSERT INTO alembic_version VALUES ('0100_add_pending_score_invalidation')"
    )
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
        assert manifest["vault_revision"] == "0100_add_pending_score_invalidation"
        assert manifest["contains_hub"] is True

    def test_a_prepositioned_writable_wal_sidecar_is_reported(
        self, registry, library, tmp_path, caplog
    ):
        """A loose-mode sidecar is warned about; the backup still runs."""
        open(os.path.join(library.path, "vault.db-wal"), "wb").write(b"junk")
        os.chmod(os.path.join(library.path, "vault.db-wal"), 0o666)
        destination = tmp_path / "out.tar.zst"

        create_backup(library, str(destination), registry.hub_path)

        assert any(
            "group/world-writable" in record.message and record.levelname == "WARNING"
            for record in caplog.records
        )
        assert destination.exists()

    def test_a_real_live_wal_is_captured_but_not_archived_raw(
        self, registry, library, tmp_path
    ):
        vault_path = os.path.join(library.path, "vault.db")
        writer = sqlite3.connect(vault_path)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO picture (file_path) VALUES ('committed-in-wal.png')"
        )
        with open(os.path.join(library.path, "committed-in-wal.png"), "wb") as image:
            image.write(b"committed image")
        writer.commit()
        wal_path = vault_path + "-wal"
        assert os.path.exists(wal_path)
        os.chmod(wal_path, 0o600)
        try:
            result = create_backup(
                library, str(tmp_path / "live-wal.tar.zst"), registry.hub_path
            )
        finally:
            writer.close()

        entries = read_archive(result.path)
        assert not any("vault.db-wal" in name for name in entries)
        assert not any(name == "images/vault.db" for name in entries)
        extracted = tmp_path / "live-wal-vault.db"
        extracted.write_bytes(entries["vault.db"])
        conn = sqlite3.connect(extracted)
        try:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM picture "
                    "WHERE file_path='committed-in-wal.png'"
                ).fetchone()[0]
                == 1
            )
        finally:
            conn.close()

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


class TestLegacySnapshotIdentity:
    """A backup must never carry pre-hub credentials out of the machine.

    ``_library_files`` packages ``snapshots/**`` verbatim and ``_DATABASE_FILES``
    excludes only the root-level vault, so an archive that still holds the old
    ``user`` / ``usertoken`` rows would travel inside the tarball. The
    restore-path scrub cannot help: nothing materializes these archives here,
    they are copied as bytes. Now that the scrub runs in the background rather
    than before the listening socket, a backup can genuinely race it.
    """

    @staticmethod
    def _legacy_snapshot(library, marker):
        """Register one unscrubbed legacy archive containing *marker*."""
        snapshots = os.path.join(library.path, "snapshots")
        os.makedirs(snapshots, exist_ok=True)
        archive = os.path.join(snapshots, "legacy.sqlite")
        conn = sqlite3.connect(archive)
        conn.execute("CREATE TABLE user (id INTEGER PRIMARY KEY, password_hash TEXT)")
        conn.execute("INSERT INTO user (password_hash) VALUES (?)", (marker,))
        conn.execute("CREATE TABLE usertoken (id INTEGER PRIMARY KEY, token_hash TEXT)")
        conn.execute("INSERT INTO usertoken (token_hash) VALUES (?)", (marker,))
        conn.commit()
        conn.close()

        vault = sqlite3.connect(os.path.join(library.path, "vault.db"))
        vault.execute(
            "CREATE TABLE snapshot (id INTEGER PRIMARY KEY, relative_path TEXT, "
            "byte_size INTEGER, identity_scrubbed_at TIMESTAMP)"
        )
        vault.execute(
            "INSERT INTO snapshot (id, relative_path, byte_size) "
            "VALUES (1, 'snapshots/legacy.sqlite', 0)"
        )
        vault.commit()
        vault.close()
        return archive

    def test_backup_scrubs_outstanding_archives_before_packaging_them(
        self, registry, library, tmp_path
    ):
        marker = b"BACKUP-MUST-NOT-CARRY-THIS-4f21"
        self._legacy_snapshot(library, marker.decode())

        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )

        entries = read_archive(result.path)
        packaged = [name for name in entries if "snapshots/" in name]
        assert packaged, "the archive should still be included, just scrubbed"
        for name in packaged:
            assert marker not in entries[name], (
                f"{name} carried pre-hub credentials out of the machine"
            )
        # The scrub is recorded, so the background finder does not redo it.
        vault = sqlite3.connect(os.path.join(library.path, "vault.db"))
        outstanding = vault.execute(
            "SELECT COUNT(*) FROM snapshot WHERE identity_scrubbed_at IS NULL"
        ).fetchone()[0]
        vault.close()
        assert outstanding == 0

    def test_a_failed_scrub_refuses_to_publish(
        self, registry, library, tmp_path, monkeypatch
    ):
        """Better no backup than one holding credentials."""
        import pixlstash.services.library_backup_service as backup_module
        from pixlstash.services.portable_identity import PortableIdentityScrubError

        self._legacy_snapshot(library, "UNSCRUBBABLE-9c02")
        destination = tmp_path / "refused.tar.zst"

        def explode(*_args, **_kwargs):
            raise PortableIdentityScrubError("injected scrub failure")

        monkeypatch.setattr(backup_module, "sanitize_historical_snapshots", explode)

        with pytest.raises(BackupError, match="stale owner credentials"):
            create_backup(library, str(destination), registry.hub_path)

        assert not destination.exists()


class TestConsistencyAndSafety:
    def test_missing_internal_picture_refuses_to_publish(
        self, registry, library, tmp_path
    ):
        os.remove(os.path.join(library.path, "1.png"))
        destination = tmp_path / "missing.tar.zst"

        with pytest.raises(BackupError, match="missing from the backup payload"):
            create_backup(library, str(destination), registry.hub_path)

        assert not destination.exists()

    def test_picture_purged_between_collection_and_stream_refuses_to_publish(
        self, registry, library, tmp_path, monkeypatch
    ):
        import pixlstash.services.library_backup_service as backup_module

        destination = tmp_path / "purged-during-backup.tar.zst"
        real_write = backup_module._write_archive

        def purge_then_write(payload, output, compress, payload_bytes):
            os.remove(os.path.join(library.path, "1.png"))
            return real_write(payload, output, compress, payload_bytes)

        monkeypatch.setattr(backup_module, "_write_archive", purge_then_write)
        with pytest.raises(BackupError, match="Could not read backup payload"):
            create_backup(library, str(destination), registry.hub_path)

        assert not destination.exists()
        assert not list(tmp_path.glob(".pixlstash-backup-*.tmp"))

    def test_stream_failure_leaves_no_final_and_retry_succeeds(
        self, registry, library, tmp_path, monkeypatch
    ):
        destination = tmp_path / "retryable.tar.zst"
        real_add = tarfile.TarFile.add
        calls = 0

        def fail_midstream(tar, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected archive read failure")
            return real_add(tar, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(tarfile.TarFile, "add", fail_midstream)
            with pytest.raises(BackupError, match="injected archive read failure"):
                create_backup(library, str(destination), registry.hub_path)

        assert not destination.exists()
        assert not list(tmp_path.glob(".pixlstash-backup-*.tmp"))
        assert create_backup(library, str(destination), registry.hub_path).path == str(
            destination
        )

    def test_publication_fsync_failure_removes_final_and_allows_retry(
        self, registry, library, tmp_path, monkeypatch
    ):
        import pixlstash.services.library_backup_service as backup_module

        destination = tmp_path / "publication-fsync.tar.zst"
        real_fsync_directory = backup_module._fsync_directory
        failed = False

        def fail_first(directory):
            nonlocal failed
            if not failed:
                failed = True
                raise BackupError("injected directory fsync failure")
            return real_fsync_directory(directory)

        with monkeypatch.context() as patch:
            patch.setattr(backup_module, "_fsync_directory", fail_first)
            with pytest.raises(BackupError, match="injected directory fsync"):
                create_backup(library, str(destination), registry.hub_path)

        assert not destination.exists()
        assert create_backup(library, str(destination), registry.hub_path).path == str(
            destination
        )

    def test_temp_unlink_fsync_failure_does_not_misreport_committed_backup(
        self, registry, library, tmp_path, monkeypatch
    ):
        import pixlstash.services.library_backup_service as backup_module

        destination = tmp_path / "cleanup-fsync.tar.zst"
        real_fsync_directory = backup_module._fsync_directory
        calls = 0

        def fail_second(directory):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise BackupError("injected cleanup fsync failure")
            return real_fsync_directory(directory)

        monkeypatch.setattr(backup_module, "_fsync_directory", fail_second)
        result = create_backup(library, str(destination), registry.hub_path)

        assert result.path == str(destination)
        assert destination.exists()
        assert read_archive(result.path)["vault.db"]

    def test_symlinked_payload_is_refused(self, registry, library, tmp_path):
        target = tmp_path / "outside.png"
        target.write_bytes(b"outside")
        os.remove(os.path.join(library.path, "1.png"))
        os.symlink(target, os.path.join(library.path, "1.png"))
        destination = tmp_path / "symlink-payload.tar.zst"

        with pytest.raises(BackupError, match="symlinked library payload"):
            create_backup(library, str(destination), registry.hub_path)

        assert not destination.exists()

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

    def test_hub_path_swap_cannot_replace_the_validated_backup_source(
        self, registry, library, tmp_path, monkeypatch
    ):
        import pixlstash.services.library_backup_service as backup_module

        with registry._hub.transaction() as conn:
            conn.execute("INSERT INTO user (username, is_admin) VALUES ('real', 1)")
        decoy_path = str(tmp_path / "decoy-hub.db")
        decoy = HubDatabase(decoy_path)
        with decoy.transaction() as conn:
            conn.execute("INSERT INTO user (username, is_admin) VALUES ('decoy', 1)")
        decoy.close()
        held_real = str(tmp_path / "held-real-hub.db")
        held_decoy = str(tmp_path / "held-decoy-hub.db")
        real_copy = backup_module._vacuum_connection_into
        swapped = False

        def swap_then_copy(source, destination, source_label):
            nonlocal swapped
            if source_label == registry.hub_path and not swapped:
                swapped = True
                os.rename(registry.hub_path, held_real)
                os.rename(decoy_path, registry.hub_path)
            return real_copy(source, destination, source_label)

        monkeypatch.setattr(backup_module, "_vacuum_connection_into", swap_then_copy)
        try:
            result = create_backup(
                library, str(tmp_path / "out.tar.zst"), registry.hub_path
            )
            extracted = tmp_path / "archived-hub.db"
            extracted.write_bytes(read_archive(result.path)["hub.db"])
            conn = sqlite3.connect(extracted)
            try:
                assert conn.execute("SELECT username FROM user").fetchone()[0] == "real"
            finally:
                conn.close()
        finally:
            if os.path.exists(registry.hub_path):
                os.rename(registry.hub_path, held_decoy)
            if os.path.exists(held_real):
                os.rename(held_real, registry.hub_path)

    def test_an_unreadable_library_is_refused_with_a_usable_message(
        self, registry, library, tmp_path
    ):
        os.remove(os.path.join(library.path, "vault.db"))

        with pytest.raises(BackupError) as excinfo:
            create_backup(library, str(tmp_path / "out.tar.zst"), registry.hub_path)

        assert "not readable" in str(excinfo.value)

    @pytest.mark.parametrize(
        "hub_kind", ["missing", "symlink", "directory", "not-sqlite"]
    )
    def test_an_invalid_hub_fails_closed_without_an_archive(
        self, registry, library, tmp_path, hub_kind
    ):
        hub_path = tmp_path / f"{hub_kind}.db"
        if hub_kind == "symlink":
            target = tmp_path / "real-target.db"
            target.write_bytes(b"not the configured hub")
            hub_path.symlink_to(target)
        elif hub_kind == "directory":
            hub_path.mkdir()
        elif hub_kind == "not-sqlite":
            hub_path.write_bytes(b"not sqlite")
        destination = tmp_path / f"{hub_kind}.tar.zst"

        with pytest.raises(BackupError, match="[Hh]ub"):
            create_backup(library, str(destination), str(hub_path))

        assert not destination.exists()


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


class TestOrphanedFiles:
    """Picture files no catalogue row names: counted, asked about, honoured."""

    @staticmethod
    def _drop_orphan(folder, name="stray.png"):
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(b"nobody catalogued me")
        # Not pictures, so never counted: our own thumbnail and cache files.
        with open(os.path.join(folder, "0_thumb.webp"), "wb") as handle:
            handle.write(b"thumb")
        os.makedirs(os.path.join(folder, "tmp"), exist_ok=True)
        with open(os.path.join(folder, "tmp", "cache.png"), "wb") as handle:
            handle.write(b"cache")

    def test_orphans_are_counted_and_included_by_default(
        self, registry, library, tmp_path
    ):
        self._drop_orphan(library.path)

        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path
        )

        assert result.orphan_count == 1
        assert result.orphans_included
        archive = read_archive(result.path)
        assert "images/stray.png" in archive
        manifest = json.loads(archive["manifest.json"])
        assert manifest["orphan_count"] == 1 and manifest["orphans_included"]

    def test_the_owner_is_asked_and_a_no_leaves_them_out(
        self, registry, library, tmp_path
    ):
        self._drop_orphan(library.path)
        asked: list[str] = []

        def decline(message):
            asked.append(message)
            return False

        result = create_backup(
            library,
            str(tmp_path / "out.tar.zst"),
            registry.hub_path,
            ask_orphans=decline,
        )

        assert asked and "1 picture file(s)" in asked[0]
        assert result.orphan_count == 1 and not result.orphans_included
        archive = read_archive(result.path)
        assert "images/stray.png" not in archive
        assert "images/0.png" in archive, "catalogued pictures are untouched"
        assert not json.loads(archive["manifest.json"])["orphans_included"]

    def test_a_decided_answer_is_not_asked_again(self, registry, library, tmp_path):
        self._drop_orphan(library.path)

        def never(message):
            raise AssertionError("include_orphans was given; nothing to ask")

        result = create_backup(
            library,
            str(tmp_path / "out.tar.zst"),
            registry.hub_path,
            include_orphans=False,
            ask_orphans=never,
        )
        assert "images/stray.png" not in read_archive(result.path)

    def test_a_clean_library_asks_nothing(self, registry, library, tmp_path):
        def never(message):
            raise AssertionError("no orphans, nothing to ask")

        result = create_backup(
            library, str(tmp_path / "out.tar.zst"), registry.hub_path, ask_orphans=never
        )
        assert result.orphan_count == 0

    def test_the_cli_reports_and_skips_on_request(self, tmp_path, capsys):
        from pixlstash.cli import main as cli_main

        hub_path = str(tmp_path / "hub.db")
        folder = make_vault(str(tmp_path / "cli-orphans"))
        self._drop_orphan(folder)
        assert cli_main(["--hub", hub_path, "libraries", "attach", folder]) == 0
        capsys.readouterr()

        code = cli_main(
            [
                "--hub",
                hub_path,
                "libraries",
                "backup",
                "cli-orphans",
                str(tmp_path / "out.tar.zst"),
                "--skip-orphans",
            ]
        )

        captured = capsys.readouterr()
        assert code == 0
        assert (
            "1 picture file(s) in the library folder have no PixlStash record"
            in captured.err
        )
        assert "NOT included" in captured.err
        assert "images/stray.png" not in read_archive(str(tmp_path / "out.tar.zst"))


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

    def test_existing_destination_is_never_overwritten(
        self, registry, library, tmp_path
    ):
        destination = tmp_path / "existing.tar.zst"
        destination.write_bytes(b"keep me")

        with pytest.raises(BackupError, match="already exists"):
            create_backup(library, str(destination), registry.hub_path)

        assert destination.read_bytes() == b"keep me"

    def test_destination_symlink_is_refused(self, registry, library, tmp_path):
        target = tmp_path / "target.tar.zst"
        target.write_bytes(b"do not overwrite")
        destination = tmp_path / "linked.tar.zst"
        destination.symlink_to(target)

        with pytest.raises(BackupError, match="symlink"):
            create_backup(library, str(destination), registry.hub_path)

        assert target.read_bytes() == b"do not overwrite"


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
