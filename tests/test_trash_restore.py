"""Taking a file back out of the system trash (`utils/trash_restore.py`).

The freedesktop half runs against send2trash's own freedesktop code with its
home trash pointed into tmp_path, so what is restored is exactly what the real
trash call writes. The Windows half reads hand-written `$I` records, the format
Windows writes, from a folder standing in for `$Recycle.Bin\\<SID>`.
"""

import os
import struct
import sys

import pytest

from pixlstash.utils import trash_restore
from pixlstash.utils.trash_restore import NotInTrash, restore_from_trash


@pytest.fixture
def home_trash(monkeypatch, tmp_path):
    """send2trash's freedesktop trash, kept under tmp_path; returns its trash().

    The Windows record tests below need no trash at all and run everywhere.
    """
    if sys.platform in ("win32", "darwin"):
        pytest.skip("freedesktop trash only")
    from send2trash import plat_other  # imports os.getuid: not on Windows

    data_home = tmp_path / "share"
    monkeypatch.setattr(plat_other, "XDG_DATA_HOME", os.fsencode(data_home))
    monkeypatch.setattr(plat_other, "HOMETRASH_B", os.fsencode(data_home / "Trash"))
    # Every path on "one device", so nothing lands in a volume trash at the
    # top of the real tmp filesystem.
    monkeypatch.setattr(plat_other, "get_dev", lambda path: 0)
    return plat_other.send2trash


def test_a_trashed_file_comes_back_where_it_was(home_trash, tmp_path):
    original = tmp_path / "models" / "alice.safetensors"
    original.parent.mkdir()
    original.write_bytes(b"weights")
    home_trash(str(original))
    assert not original.exists()

    restore_from_trash(str(original))

    assert original.read_bytes() == b"weights"
    info = tmp_path / "share" / "Trash" / "info"
    assert list(info.iterdir()) == [], "the trash still lists the restored file"


def test_a_trashed_directory_comes_back_whole(home_trash, tmp_path):
    original = tmp_path / "alice_samples"
    original.mkdir()
    (original / "1__0_0.jpg").write_bytes(b"x")
    home_trash(str(original))

    restore_from_trash(str(original))

    assert (original / "1__0_0.jpg").read_bytes() == b"x"


def test_the_newest_of_two_entries_from_one_path_comes_back(home_trash, tmp_path):
    original = tmp_path / "alice.safetensors"
    original.write_bytes(b"old")
    home_trash(str(original))
    original.write_bytes(b"new")
    home_trash(str(original))
    info = tmp_path / "share" / "Trash" / "info"
    # Same-second DeletionDates are common; the newer record's mtime decides.
    old_info = info / "alice.safetensors.trashinfo"
    os.utime(old_info, ns=(1, 1))

    restore_from_trash(str(original))

    assert original.read_bytes() == b"new"


def test_a_file_from_another_path_is_never_restored(home_trash, tmp_path):
    other = tmp_path / "elsewhere" / "alice.safetensors"
    other.parent.mkdir()
    other.write_bytes(b"theirs")
    home_trash(str(other))

    with pytest.raises(NotInTrash):
        restore_from_trash(str(tmp_path / "alice.safetensors"))
    assert not (tmp_path / "alice.safetensors").exists()


def test_an_occupied_path_is_never_replaced(home_trash, tmp_path):
    original = tmp_path / "alice.safetensors"
    original.write_bytes(b"trashed")
    home_trash(str(original))
    original.write_bytes(b"new owner")

    with pytest.raises(FileExistsError):
        restore_from_trash(str(original))
    assert original.read_bytes() == b"new owner"


def test_an_emptied_trash_says_so(home_trash, tmp_path):
    with pytest.raises(NotInTrash):
        restore_from_trash(str(tmp_path / "never.safetensors"))


def _windows_record(path: str, deleted_at: int, version: int = 2) -> bytes:
    head = struct.pack("<qqq", version, 7, deleted_at)
    encoded = (path + "\x00").encode("utf-16-le")
    if version == 1:
        return head + encoded.ljust(520, b"\x00")
    return head + struct.pack("<i", len(path) + 1) + encoded


@pytest.mark.parametrize("version", [1, 2])
def test_a_recycle_bin_record_is_found_by_its_original_path(tmp_path, version):
    bin_dir = tmp_path / "S-1-5-21"
    bin_dir.mkdir()
    original = "C:\\Users\\me\\models\\Alice.safetensors"
    (bin_dir / "$IOLD.safetensors").write_bytes(_windows_record(original, 100, version))
    (bin_dir / "$ROLD.safetensors").write_bytes(b"old")
    (bin_dir / "$INEW.safetensors").write_bytes(_windows_record(original, 200, version))
    (bin_dir / "$RNEW.safetensors").write_bytes(b"new")
    (bin_dir / "$IOTHER.safetensors").write_bytes(
        _windows_record("C:\\Users\\me\\Bob.safetensors", 300, version)
    )
    (bin_dir / "$ROTHER.safetensors").write_bytes(b"bob")

    # Windows paths compare case-insensitively.
    data, info = trash_restore._find_windows_entry(original.lower(), [str(bin_dir)])

    assert os.path.basename(data) == "$RNEW.safetensors"
    assert os.path.basename(info) == "$INEW.safetensors"
    with pytest.raises(NotInTrash):
        trash_restore._find_windows_entry("C:\\Users\\me\\Carol.pt", [str(bin_dir)])


def test_the_fingerprint_picks_its_own_entry_over_a_newer_one(home_trash, tmp_path):
    """Undoing the older of two deletes from one path brings back its own file,
    never the newer delete's, whose undo still finds its own."""
    original = tmp_path / "alice.safetensors"
    original.write_bytes(b"first file")
    first = trash_restore.fingerprint(str(original))
    home_trash(str(original))
    original.write_bytes(b"second, longer file")
    second = trash_restore.fingerprint(str(original))
    home_trash(str(original))

    restore_from_trash(str(original), first)
    assert original.read_bytes() == b"first file"

    original.rename(tmp_path / "moved-aside")
    restore_from_trash(str(original), second)
    assert original.read_bytes() == b"second, longer file"


def test_a_fingerprint_nothing_matches_is_not_in_the_trash(home_trash, tmp_path):
    original = tmp_path / "alice.safetensors"
    original.write_bytes(b"weights")
    home_trash(str(original))

    with pytest.raises(NotInTrash):
        restore_from_trash(str(original), (999, 1))
    assert not original.exists()


def test_a_trashed_symlink_comes_back_by_its_own_fingerprint(home_trash, tmp_path):
    """A symlinked model loses its link to the trash, so the link's own stats
    are what is fingerprinted and matched, never its target's."""
    target = tmp_path / "elsewhere.safetensors"
    target.write_bytes(b"the bytes, which stay where they are")
    link = tmp_path / "alice.safetensors"
    link.symlink_to(target)
    stamp = trash_restore.fingerprint(str(link))
    assert stamp is not None
    home_trash(str(link))

    restore_from_trash(str(link), stamp)

    assert link.is_symlink() and link.resolve() == target
