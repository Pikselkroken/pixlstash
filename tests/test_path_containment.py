"""Containment of stored paths before they reach a destructive operation (#776).

A snapshot's ``relative_path`` and a ``Picture.file_path`` are database values.
A wrong one, written by a faulty import or a bug, previously reached an
unattended ``os.remove``. These tests assert BOTH directions at every sink that
deletes or overwrites:

- a stored path that escapes the vault root is refused at snapshot delete, GFS
  retention and the scrapheap purge, and a fabricated sidecar column cannot
  redirect a write; and
- ordinary in-root paths are still deleted and written, and a picture under a
  reference folder OUTSIDE the image root is still purged, because
  over-blocking a delete strands files and is its own regression.

Reads are deliberately not contained. Whoever can write the vault DB can read
those files directly, so refusing to serve them buys little, while a false
refusal presents to a desktop user as their pictures failing to load.
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image
from sqlmodel import delete, select

from pixlstash.db_models import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.server import Server
from pixlstash.services.operation_log_service import apply_orientation
from pixlstash.services.scrapheap_service import remove_picture_files
from pixlstash.utils.caption_file_utils import SIDECAR_TYPE_TAGS, writeback_path
from pixlstash.utils.image_processing.orientation import read_orientation
from pixlstash.utils.path_utils import path_is_within
from pixlstash.utils.reference_folder_validator import (
    validate_reference_folder_path,
)


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = os.path.join(tmp, "server-config.json")
        with open(config_path, "w") as fh:
            json.dump({"disable_background_workers": True}, fh)
        with Server(server_config_path=config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_db(server):
    def _wipe(session):
        session.exec(delete(Snapshot))
        session.exec(delete(Picture))
        session.exec(delete(ReferenceFolder))
        session.commit()

    server.vault.db.run_task(_wipe)
    yield
    server.vault.db.run_task(_wipe)


def _add_reference_folder(server, folder: str) -> int:
    def _do(session):
        rf = ReferenceFolder(
            folder=folder, label="ext", status=ReferenceFolderStatus.ACTIVE
        )
        session.add(rf)
        session.commit()
        return rf.id

    return server.vault.db.run_task(_do)


def _write_png(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", (8, 8), (200, 30, 30)).save(path, format="PNG")


# ---------------------------------------------------------------------------
# path_is_within
# ---------------------------------------------------------------------------


def test_path_is_within_accepts_containment_and_refuses_escape(tmp_path):
    base = str(tmp_path / "root")
    os.makedirs(base)
    assert path_is_within(os.path.join(base, "a", "b.png"), base)
    assert path_is_within(base, base)
    assert not path_is_within(str(tmp_path / "elsewhere.png"), base)
    assert not path_is_within(os.path.join(base, "..", "escape.png"), base)
    # An empty side is never contained, rather than matching everything.
    assert not path_is_within("", base)
    assert not path_is_within(str(tmp_path / "x.png"), "")


def test_path_is_within_follows_a_symlinked_root(tmp_path):
    real = tmp_path / "real-root"
    real.mkdir()
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    # The same directory spelled through its alias is still contained.
    assert path_is_within(str(real / "pic.png"), str(link))


def test_blocklist_refuses_a_symlink_pointing_into_a_restricted_directory(tmp_path):
    """A link the caller owns must not launder a blocked target past the check.

    The blocklist compares literal strings, so before this was resolved the
    link's own path matched no entry and the route behind the check went on to
    operate on the target. Every caller that passed the string it was given -
    most of them - inherited that.
    """
    link = tmp_path / "innocent-looking"
    try:
        link.symlink_to("/etc", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    if not os.path.isdir("/etc"):
        pytest.skip("no /etc on this platform")

    assert validate_reference_folder_path(str(link)) is not None
    # Through the link's own children too, not only the root of it.
    assert validate_reference_folder_path(str(link / "ssl")) is not None


def test_blocklist_allows_a_resolved_alias_that_is_not_a_system_directory():
    """Resolving turns three ordinary locations into blocked-looking ones.

    macOS resolves `/tmp` to `/private/tmp` and `$TMPDIR` under
    `/private/var/folders`, which the `/private` entry would then swallow;
    FreeBSD and TrueNAS ship `/home` as a symlink to `/usr/home`, which `/usr`
    would swallow and strand the whole library. `/tmp` and `/home/me` were
    accepted before paths were resolved and must stay accepted; their resolved
    spellings were refused when typed literally and now pass, which is the
    widening this buys - one directory answering the same way under both of its
    names. Asserted on Linux, where `/usr` is blocked, so the exception is
    exercised on the CI platform rather than only on the one with no runner.
    """
    assert validate_reference_folder_path("/usr/home/me/Pictures") is None
    assert validate_reference_folder_path("/usr/home") is None

    # The exception is a prefix, not a substring: the rest of /usr still goes.
    assert validate_reference_folder_path("/usr/lib/x") is not None
    assert validate_reference_folder_path("/usr/homework") is not None


def test_macos_private_prefix_blocks_system_paths_but_not_the_temp_aliases(
    monkeypatch,
):
    """The macOS rule asserted on Linux, because the gate has no macOS runner.

    `/private` cannot leave the macOS list - it is the only entry that catches
    `/etc` once that resolves to `/private/etc` - so the temp aliases underneath
    it have to be excepted by name. Both halves are asserted: the system
    subtrees still refuse, the two temp locations do not.
    """
    from pixlstash.utils import reference_folder_validator as validator

    monkeypatch.setattr(validator, "_get_blocklist", lambda: validator._MACOS_BLOCKLIST)

    assert validator.validate_reference_folder_path("/private/etc/ssl") is not None
    assert validator.validate_reference_folder_path("/private/var/db") is not None
    assert validator.validate_reference_folder_path("/Library/Preferences") is not None

    assert validator.validate_reference_folder_path("/private/tmp/export") is None
    assert (
        validator.validate_reference_folder_path("/private/var/folders/ab/T/x") is None
    )


def test_blocklist_still_accepts_an_ordinary_folder_and_a_benign_symlink(tmp_path):
    """Over-blocking is its own regression: resolution must not refuse the normal case."""
    ordinary = tmp_path / "pictures"
    ordinary.mkdir()
    assert validate_reference_folder_path(str(ordinary)) is None

    link = tmp_path / "shortcut"
    try:
        link.symlink_to(ordinary, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    assert validate_reference_folder_path(str(link)) is None

    # A path that does not exist yet still resolves as far as it goes, so the
    # Docker pending-mount callers keep working rather than being refused.
    assert validate_reference_folder_path(str(ordinary / "not-created-yet")) is None

    # A relative path is still refused before any resolution happens, so it can
    # never be quietly resolved against the server's working directory.
    assert validate_reference_folder_path("pictures") is not None


# ---------------------------------------------------------------------------
# Snapshot retention + delete
# ---------------------------------------------------------------------------


def _add_snapshot_row(server, kind, created_at, rel_path, manifest_rel):
    def _do(session):
        session.add(
            Snapshot(
                kind=kind,
                created_at=created_at,
                relative_path=rel_path,
                manifest_relative_path=manifest_rel,
                byte_size=1,
                picture_count=0,
                schema_version="test",
            )
        )
        session.commit()

    server.vault.db.run_task(_do)


def test_gfs_retention_refuses_deletes_outside_the_vault_root(server, tmp_path):
    vault_root = server.vault.image_root
    now = datetime.now(timezone.utc)

    victim = tmp_path / "victim-gfs.sqlite"
    victim.write_bytes(b"keep me")
    victim_manifest = tmp_path / "victim-gfs.manifest.json"
    victim_manifest.write_bytes(b"keep me too")
    hostile_rel = os.path.relpath(str(victim), vault_root)
    hostile_manifest_rel = os.path.relpath(str(victim_manifest), vault_root)
    assert hostile_rel.startswith("..")

    # Oldest row is hostile; second-oldest is a legitimate in-root snapshot
    # whose files retention MUST still clean up (the positive direction).
    legit_rel = "snapshots/legit-old.sqlite.zst"
    legit_manifest_rel = "snapshots/legit-old.manifest.json"
    legit_abs = os.path.join(vault_root, legit_rel)
    legit_manifest_abs = os.path.join(vault_root, legit_manifest_rel)
    os.makedirs(os.path.dirname(legit_abs), exist_ok=True)
    for p in (legit_abs, legit_manifest_abs):
        with open(p, "wb") as fh:
            fh.write(b"old snapshot bits")

    _add_snapshot_row(
        server, "DAILY", now - timedelta(days=30), hostile_rel, hostile_manifest_rel
    )
    _add_snapshot_row(
        server, "DAILY", now - timedelta(days=20), legit_rel, legit_manifest_rel
    )
    for i in range(7):
        _add_snapshot_row(
            server,
            "DAILY",
            now - timedelta(days=i),
            f"snapshots/recent-{i}.sqlite.zst",
            f"snapshots/recent-{i}.manifest.json",
        )

    server.vault.snapshot_service._apply_gfs_retention(now)

    # Hostile files survived; the legitimate pruned snapshot's files are gone.
    assert victim.read_bytes() == b"keep me"
    assert victim_manifest.read_bytes() == b"keep me too"
    assert not os.path.exists(legit_abs)
    assert not os.path.exists(legit_manifest_abs)
    remaining = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(Snapshot)).all()
    )
    assert len(remaining) == 7


def test_delete_snapshot_refuses_files_outside_the_vault_root(server, tmp_path):
    vault_root = server.vault.image_root
    victim = tmp_path / "victim-delete.sqlite"
    victim.write_bytes(b"still here")
    hostile_rel = os.path.relpath(str(victim), vault_root)
    _add_snapshot_row(
        server,
        "MANUAL",
        datetime.now(timezone.utc),
        hostile_rel,
        hostile_rel + ".manifest.json",
    )
    snap_id = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(Snapshot.id)).one()
    )
    assert server.vault.snapshot_service.delete_snapshot(snap_id) is True
    assert victim.read_bytes() == b"still here"


def test_delete_snapshot_still_removes_in_root_files(server):
    vault_root = server.vault.image_root
    rel = "snapshots/deletable.sqlite.zst"
    manifest_rel = "snapshots/deletable.manifest.json"
    abs_path = os.path.join(vault_root, rel)
    abs_manifest = os.path.join(vault_root, manifest_rel)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    for p in (abs_path, abs_manifest):
        with open(p, "wb") as fh:
            fh.write(b"snapshot bits")
    _add_snapshot_row(server, "MANUAL", datetime.now(timezone.utc), rel, manifest_rel)
    snap_id = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(Snapshot.id)).one()
    )
    assert server.vault.snapshot_service.delete_snapshot(snap_id) is True
    assert not os.path.exists(abs_path)
    assert not os.path.exists(abs_manifest)


# ---------------------------------------------------------------------------
# Scrapheap purge delete
# ---------------------------------------------------------------------------


def test_scrapheap_purge_refuses_out_of_root_targets(tmp_path):
    image_root = str(tmp_path / "library")
    os.makedirs(image_root)
    victim = tmp_path / "victim-purge.png"
    victim.write_bytes(b"precious")
    unconfirmed = remove_picture_files(image_root, [(1, str(victim), False)])
    unconfirmed += remove_picture_files(image_root, [(2, "../victim-purge.png", False)])
    assert victim.read_bytes() == b"precious"
    # Refused is reported as not-confirmed-gone, so the ledger is corrected and
    # restore can still resurrect the row.
    assert len(unconfirmed) == 2


def test_scrapheap_purge_still_removes_in_root_files(tmp_path):
    image_root = str(tmp_path / "library")
    doomed = os.path.join(image_root, "doomed.png")
    _write_png(doomed)
    unconfirmed = remove_picture_files(image_root, [(1, "doomed.png", False)])
    assert not os.path.exists(doomed)
    assert unconfirmed == []


def test_scrapheap_purge_still_removes_reference_folder_files(tmp_path):
    """Over-blocking regression: reference-folder pictures live outside the
    image root and must still be deleted by delete-forever."""
    image_root = str(tmp_path / "library")
    ref_root = str(tmp_path / "external-refs")
    os.makedirs(image_root)
    doomed = os.path.join(ref_root, "sub", "ref.png")
    _write_png(doomed)
    unconfirmed = remove_picture_files(image_root, [(1, doomed, True)], (ref_root,))
    assert not os.path.exists(doomed)
    assert unconfirmed == []


def test_vault_supplies_the_reference_roots_the_purge_needs(server, tmp_path):
    """The wiring: the roots the purge is handed come from the folder table."""
    ref_root = str(tmp_path / "vault-refs")
    os.makedirs(ref_root)
    _add_reference_folder(server, ref_root)
    assert ref_root in server.vault.reference_folder_roots()


# ---------------------------------------------------------------------------
# Caption sidecar write-back
# ---------------------------------------------------------------------------


def test_writeback_ignores_fabricated_existing_path(tmp_path):
    image_root = str(tmp_path / "library")
    os.makedirs(image_root)
    image = os.path.join(image_root, "img.png")
    # A tags_file column pointing anywhere else must not become a write target;
    # the suffix-derived sidecar path is used instead.
    assert writeback_path(
        image, SIDECAR_TYPE_TAGS, "_tags.txt", str(tmp_path / "authorized_keys")
    ) == os.path.join(image_root, "img_tags.txt")


def test_writeback_honours_a_legitimate_existing_path(tmp_path):
    image_root = str(tmp_path / "library")
    os.makedirs(image_root)
    image = os.path.join(image_root, "img.png")
    recorded = os.path.join(image_root, "img_tags.txt")
    assert writeback_path(image, SIDECAR_TYPE_TAGS, None, recorded) == recorded


# ---------------------------------------------------------------------------
# In-place rotate - the first sink that overwrites an ORIGINAL file (#950)
# ---------------------------------------------------------------------------


def _add_picture(server, file_path: str, reference_folder_id=None) -> int:
    def _do(session):
        pic = Picture(file_path=file_path, reference_folder_id=reference_folder_id)
        session.add(pic)
        session.commit()
        return pic.id

    return server.vault.db.run_task(_do)


def _rotate_in_session(
    server, picture_id: int, orientation: int, image_root: str | None = None
) -> bool:
    def _do(session):
        turned = apply_orientation(
            session,
            picture_id,
            orientation,
            image_root=image_root or server.vault.image_root,
        )
        session.commit()
        return turned

    return server.vault.db.run_task(_do)


@pytest.mark.parametrize("escape", ["absolute", "dot-dot"])
def test_rotate_refuses_to_overwrite_a_file_outside_the_vault_root(
    server, tmp_path, escape
):
    """`Picture.file_path` is a database value, and rotate WRITES to it.

    `resolve_picture_path` hands an absolute path straight back and joins a
    relative one without normalising, so a wrong row - a faulty import, an
    edited DB - resolves wherever it says. Every sibling destructive sink checks
    containment first; this one overwrites the user's original bytes, so it is
    the last place that should take the row's word for it.
    """
    victim = tmp_path / "outside" / "not-in-the-library.png"
    _write_png(str(victim))
    untouched = victim.read_bytes()

    stored = (
        str(victim)
        if escape == "absolute"
        else os.path.relpath(str(victim), server.vault.image_root)
    )
    picture_id = _add_picture(server, stored)

    assert _rotate_in_session(server, picture_id, 6) is False
    assert victim.read_bytes() == untouched
    assert read_orientation(str(victim)) == 1


def test_rotate_still_turns_an_ordinary_in_root_picture(server):
    """Over-blocking regression: the containment check must not refuse the
    normal case, which is every picture in the library."""
    in_root = os.path.join(server.vault.image_root, "rotatable.png")
    _write_png(in_root)
    picture_id = _add_picture(server, "rotatable.png")

    assert _rotate_in_session(server, picture_id, 6) is True
    assert read_orientation(in_root) == 6


def test_rotate_refuses_a_reference_folder_file_at_the_sink(server, tmp_path):
    """Refused in the applier, not only in the route, so undo inherits it.

    A picture rotated while library-managed and later re-homed to a reference
    folder would otherwise have its external file rewritten by an undo.
    """
    ref_root = str(tmp_path / "external-refs")
    external = os.path.join(ref_root, "theirs.png")
    _write_png(external)
    folder_id = _add_reference_folder(server, ref_root)
    picture_id = _add_picture(server, external, reference_folder_id=folder_id)

    assert _rotate_in_session(server, picture_id, 6) is False
    assert read_orientation(external) == 1


def _cleanup(*paths) -> None:
    """Undo what a test left in the module-scoped vault: `clean_db` wipes rows,
    not files, and a symlink pointing out of the library must not outlive its
    test."""
    for path in paths:
        if not os.path.lexists(path):
            continue
        # Windows removes a directory symlink with rmdir; POSIX with unlink.
        if os.name == "nt" and os.path.islink(path) and os.path.isdir(path):
            os.rmdir(path)
        else:
            os.unlink(path)


def test_rotate_accepts_a_library_root_reached_through_a_symlink(server, tmp_path):
    """Over-blocking guard for the strict check, not a demonstration of it.

    `path_is_within` already accepted an aliased root, so this passes either
    way; it is here so that a future tightening cannot refuse every library
    whose folder is reached through a symlink without a test going red.
    """
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(server.vault.image_root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")

    in_root = os.path.join(server.vault.image_root, "aliased.png")
    _write_png(in_root)
    picture_id = _add_picture(server, "aliased.png")
    try:
        assert _rotate_in_session(server, picture_id, 6, image_root=str(linked_root))
        assert read_orientation(in_root) == 6
    finally:
        _cleanup(in_root)


def test_rotate_refuses_a_symlink_inside_the_root_that_points_outside(server, tmp_path):
    """The topology `path_is_within` accepts on purpose and a write sink cannot.

    The harm is a read escape, not a write one: `os.replace` replaces a symlink
    rather than following it, so the outside file is never rewritten - but
    `read_orientation` follows it, and the rotate then lands a copy of that
    file's bytes inside the library under the link's name, carrying its mode and
    owner across. Asserting the link is still a link is what fails on the
    lexical check; the byte assertion holds either way and is kept as the
    statement of what must never become true.
    """
    victim = tmp_path / "outside" / "theirs.png"
    _write_png(str(victim))
    untouched = victim.read_bytes()

    link = os.path.join(server.vault.image_root, "looks-in-root.png")
    try:
        os.symlink(str(victim), link)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    picture_id = _add_picture(server, "looks-in-root.png")

    try:
        turned = _rotate_in_session(server, picture_id, 6)
        assert os.path.islink(link), "the outside file was read into the library"
        assert victim.read_bytes() == untouched
        assert turned is False
    finally:
        _cleanup(link)


def test_rotate_declines_a_symlinked_subfolder_inside_the_root(server, tmp_path):
    """The deliberate cost of the strict check, pinned so it is not a surprise.

    Realpath containment cannot tell a planted link from photos legitimately
    kept on a second disk, so both are refused. Rotate declines the picture and
    logs why; nothing else about it changes. If this ever has to be relaxed,
    relax it here and not in `path_is_within`.
    """
    elsewhere = tmp_path / "second-disk"
    elsewhere.mkdir()
    _write_png(str(elsewhere / "beach.png"))

    linked_sub = os.path.join(server.vault.image_root, "2024")
    try:
        os.symlink(str(elsewhere), linked_sub, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    picture_id = _add_picture(server, os.path.join("2024", "beach.png"))

    try:
        assert _rotate_in_session(server, picture_id, 6) is False
        assert read_orientation(str(elsewhere / "beach.png")) == 1
    finally:
        _cleanup(linked_sub)
