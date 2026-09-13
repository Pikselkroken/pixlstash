"""The first import runs against a temporary vault, not against ``vault.db``.

A folder holds ``vault.db`` only once the owner's answer took: a brand new empty
library, or a first import that finished. Everything before that - the indexing,
an abort, a crash - happens under a hidden temporary name, so a folder the owner
backed out of is left exactly as PixlStash found it.

See ``business/plans/pixlstash-temp-vault-first-import-plan.md``.
"""

import os
from types import SimpleNamespace

import pytest
from sqlmodel import select

from pixlstash.db_models.picture import Picture
from pixlstash.hub.db import HubDatabase
from pixlstash.hub.registry import (
    TEMP_VAULT_FILENAME,
    VAULT_FILENAME,
    LibraryRegistry,
)
from pixlstash.services.library_switch_service import (
    LibrarySwitchError,
    LibrarySwitchService,
)
from pixlstash.vault import Vault


def _index(vault, file_path: str) -> None:
    """One indexed picture, the way an import leaves them behind."""

    def write(session):
        session.add(Picture(file_path=file_path, pixel_sha="a" * 40))
        session.commit()

    vault.db.run_task(write)


def _indexed(vault) -> list[str]:
    return vault.db.run_immediate_read_task(
        lambda session: list(session.exec(select(Picture.file_path)).all())
    )


def test_the_default_is_still_vault_db(tmp_path):
    image_root = tmp_path / "library"
    with Vault(str(image_root), disable_background_workers=True):
        pass

    assert (image_root / VAULT_FILENAME).is_file()


def test_a_temporary_vault_leaves_no_vault_db_behind(tmp_path):
    """The whole point: nothing in the folder says "library" yet."""
    image_root = tmp_path / "folder-of-pictures"
    with Vault(
        str(image_root),
        disable_background_workers=True,
        db_filename=TEMP_VAULT_FILENAME,
    ) as vault:
        assert (image_root / TEMP_VAULT_FILENAME).is_file()
        assert not (image_root / VAULT_FILENAME).exists()
        # Usable as any vault is: the server serves the whole import from it.
        _index(vault, "holiday/one.jpg")

    assert not (image_root / VAULT_FILENAME).exists()


def test_a_promoted_temporary_vault_opens_under_its_new_name(tmp_path):
    """Close, rename, reopen - the promotion, at its smallest."""
    image_root = tmp_path / "library"
    with Vault(
        str(image_root),
        disable_background_workers=True,
        db_filename=TEMP_VAULT_FILENAME,
    ) as vault:
        _index(vault, "holiday/one.jpg")

    # The promotion moves one file. A -wal left behind by the close would mean
    # renaming the main database away from transactions that live in it.
    assert sorted(p.name for p in image_root.iterdir()) == [TEMP_VAULT_FILENAME]

    os.replace(image_root / TEMP_VAULT_FILENAME, image_root / VAULT_FILENAME)

    with Vault(str(image_root), disable_background_workers=True) as vault:
        assert _indexed(vault) == ["holiday/one.jpg"]


class TestPendingImportRegistration:
    """The hub row that says "this library only exists if the import finishes"."""

    @pytest.fixture
    def registry(self, tmp_path):
        hub = HubDatabase(str(tmp_path / "hub.db"))
        yield LibraryRegistry(hub)
        hub.close()

    def test_creating_one_writes_no_vault_db(self, registry, tmp_path):
        folder = tmp_path / "folder-of-pictures"
        folder.mkdir()

        library = registry.create(str(folder), "Holiday", pending_import=True)

        assert library.pending_import_at is not None
        assert (folder / TEMP_VAULT_FILENAME).is_file()
        assert not (folder / VAULT_FILENAME).exists()
        # Everything that opens this library follows the mark to the right file.
        assert library.vault_path == str(folder / TEMP_VAULT_FILENAME)
        assert library.is_reachable

    def test_an_ordinary_create_is_unchanged(self, registry, tmp_path):
        folder = tmp_path / "empty-library"
        folder.mkdir()

        library = registry.create(str(folder), "Fresh")

        assert library.pending_import_at is None
        assert (folder / VAULT_FILENAME).is_file()
        assert not (folder / TEMP_VAULT_FILENAME).exists()

    def test_it_is_hidden_from_what_the_owner_is_shown(self, registry, tmp_path):
        folder = tmp_path / "folder-of-pictures"
        folder.mkdir()
        registry.create(str(folder), "Holiday", pending_import=True)

        assert registry.list_libraries(include_pending_import=False) == []
        # But not from the checks that ask whether a folder is spoken for: the
        # folder is as taken as any other library's while the import runs.
        assert [lib.name for lib in registry.list_libraries()] == ["Holiday"]
        inside = folder / "summer"
        assert [lib.name for lib in registry.overlapping(str(inside))] == ["Holiday"]

    def test_finishing_it_flips_the_row_to_the_permanent_name(self, registry, tmp_path):
        folder = tmp_path / "folder-of-pictures"
        folder.mkdir()
        library = registry.create(str(folder), "Holiday", pending_import=True)
        os.replace(folder / TEMP_VAULT_FILENAME, folder / VAULT_FILENAME)

        finished = registry.finish_pending_import(library.id)

        assert finished.pending_import_at is None
        assert finished.vault_path == str(folder / VAULT_FILENAME)
        assert [
            lib.name for lib in registry.list_libraries(include_pending_import=False)
        ] == ["Holiday"]

    def test_the_switch_opens_one_under_its_temporary_name(self, registry, tmp_path):
        """It must stay openable: it is the library the owner just pointed at.

        Hidden from the listings is the whole of "not switchable to". Refusing
        to *open* one would make a folder of pictures impossible to set up,
        because the import that promotes it runs against this very vault.

        Built on an uninitialised instance deliberately: `_revalidate` reads
        only `self._server.hub`, and a real Server costs 1.35 s to assert a
        path.
        """
        folder = tmp_path / "folder-of-pictures"
        folder.mkdir()
        library = registry.create(str(folder), "Holiday", pending_import=True)
        service = LibrarySwitchService.__new__(LibrarySwitchService)
        service._server = SimpleNamespace(hub=object())

        opened = service._revalidate(library)

        assert str(opened) == library.path
        assert opened.library.vault_filename == TEMP_VAULT_FILENAME

    def test_the_switch_still_refuses_one_whose_file_is_gone(self, registry, tmp_path):
        """So the check above is not passing for want of looking."""
        folder = tmp_path / "folder-of-pictures"
        folder.mkdir()
        library = registry.create(str(folder), "Holiday", pending_import=True)
        os.remove(folder / TEMP_VAULT_FILENAME)
        service = LibrarySwitchService.__new__(LibrarySwitchService)
        service._server = SimpleNamespace(hub=object())

        with pytest.raises(LibrarySwitchError, match="no longer looks like"):
            service._revalidate(library)
