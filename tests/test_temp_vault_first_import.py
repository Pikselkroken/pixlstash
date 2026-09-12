"""The first import runs against a temporary vault, not against ``vault.db``.

A folder holds ``vault.db`` only once the owner's answer took: a brand new empty
library, or a first import that finished. Everything before that - the indexing,
an abort, a crash - happens under a hidden temporary name, so a folder the owner
backed out of is left exactly as PixlStash found it.

See ``business/plans/pixlstash-temp-vault-first-import-plan.md``.
"""

import os

from sqlmodel import select

from pixlstash.db_models.picture import Picture
from pixlstash.hub.registry import VAULT_FILENAME
from pixlstash.vault import Vault

#: What the first import's vault is called before it is promoted. Hidden, so
#: the import walk and the root scan prune it as they do every dot-entry.
TEMP_VAULT_FILENAME = ".vault.db.importing"


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
