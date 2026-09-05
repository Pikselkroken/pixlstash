"""PixlStash Views - sets, people and projects as folders of links (v1.11 Phase 7).

The claims this file exists to keep true, in the order they matter:

1. **Publishing moves nothing.** Every original keeps its path, its inode and
   its bytes; the tree is links. This is the whole feature and it is asserted,
   not eyeballed.
2. **A folder PixlStash did not create is never emptied.** The rebuild deletes
   subtrees, so the marker that authorises those deletes is the one guard
   standing between this feature and someone's pictures folder.
3. **Multi-membership costs nothing.** A picture in two projects appears in two
   folders and its one real file never moves.
4. **A location that cannot hold the tree is refused with a reason** rather than
   half-written - measured refusals, one per hazard the spike found
   (``docs/spikes/views-links.md``).
5. **The link mode is measured, not predicted.** ``test_this_filesystem_offers``
   is the probe running on whatever filesystem the suite is on, which is how the
   Windows answer - symlinks need administrator rights or Developer Mode, hard
   links do not - arrives from the gate's own Windows shards rather than from a
   manual.
"""

import gc
import io
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models.character import Character
from pixlstash.db_models.face import Face
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.picture_set import PictureSet, PictureSetMember
from pixlstash.db_models.project import Project
from pixlstash.server import Server
from pixlstash.services import views_service
from pixlstash.tasks.reference_folder_scan_task import VIEWS_MARKER_NAME
from tests.utils import upload_pictures_and_wait

API = "/api/v1"


# ---------------------------------------------------------------------------
# A vault stand-in, so the filesystem rules are tested without a Server
# ---------------------------------------------------------------------------


class _FakeVault:
    """The three things ``views_service`` asks a vault for, and nothing else."""

    def __init__(self, image_root, collected=None, reference_roots=()):
        self.image_root = image_root
        self.collected = collected or {}
        self._reference_roots = tuple(reference_roots)

    def reference_folder_roots(self):
        return self._reference_roots


def _picture(picture_id, path):
    return Picture(id=picture_id, file_path=path)


@pytest.fixture
def library(tmp_path):
    """A library of three real files under an image root, outside the views root."""
    root = tmp_path / "library"
    (root / "2024 Shoots" / "Mira").mkdir(parents=True)
    paths = []
    for index in range(3):
        target = root / "2024 Shoots" / "Mira" / f"041{index}.png"
        target.write_bytes(b"P" * (100 + index))
        paths.append(str(target))
    return str(root), paths


# ---------------------------------------------------------------------------
# What the filesystem under the suite actually offers
# ---------------------------------------------------------------------------


def test_this_filesystem_offers_a_link_mode(tmp_path):
    """The probe answers by trying, so this records the real answer per platform.

    On Linux and macOS that is a symlink. On Windows it is a symlink when the
    process holds ``SeCreateSymbolicLinkPrivilege`` (administrator or Developer
    Mode) and a hard link otherwise - the gate's Windows shards are where that
    answer comes from, and this assertion is what reports it. A filesystem with
    neither, exFAT above all, returns ``None`` and a reason; that is a supported
    outcome for the feature and a failure here only if it happens on the
    temporary directory a test runner gave us.
    """
    target = tmp_path / "real.bin"
    target.write_bytes(b"x")
    mode, reason = views_service.probe_link_support(str(tmp_path), str(target))
    assert mode in (views_service.SYMLINK, views_service.HARDLINK), reason
    assert reason == ""
    assert sorted(os.listdir(tmp_path)) == ["real.bin"], "the probe left a file behind"


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------


def test_publishing_creates_links_and_moves_no_file(tmp_path, library):
    image_root, paths = library
    before = [(p, os.stat(p).st_ino, open(p, "rb").read()) for p in paths]
    vault = _FakeVault(
        image_root,
        {
            "sets": [
                (7, "mira-lora-v3", [_picture(i + 1, p) for i, p in enumerate(paths)])
            ]
        },
    )
    views_root = str(tmp_path / "views")

    report = views_service.publish(vault, views_root, ["sets"], vault.collected)

    assert report.links == 3
    assert report.folders == 1
    assert report.skipped_missing == 0
    published = os.path.join(views_root, "Sets", "mira-lora-v3")
    assert sorted(os.listdir(published)) == ["0410.png", "0411.png", "0412.png"]
    for path, inode, content in before:
        assert os.path.exists(path), "an original moved"
        assert os.stat(path).st_ino == inode, "an original was rewritten"
        assert open(path, "rb").read() == content
    # Every published entry reads back as the picture it names.
    for name, (path, _inode, content) in zip(sorted(os.listdir(published)), before):
        with open(os.path.join(published, name), "rb") as handle:
            assert handle.read() == content


def test_a_published_entry_is_a_link_and_not_a_copy(tmp_path, library):
    """The claim is "nothing is duplicated", and a copy satisfies every other
    assertion in this file: it reads back the same bytes, it survives an rmtree
    of the tree, and the original is untouched. So the property is asserted
    directly - the entry is a symlink, or it shares an inode with the original -
    because an "rmtree survives" test only asserts a documented property of the
    standard library and would still pass with ``publish`` replaced by
    ``shutil.copy``."""
    import shutil

    image_root, paths = library
    vault = _FakeVault(
        image_root,
        {
            "sets": [
                (1, "everything", [_picture(i + 1, p) for i, p in enumerate(paths)])
            ]
        },
    )
    views_root = str(tmp_path / "views")
    views_service.publish(vault, views_root, ["sets"], vault.collected)

    published = os.path.join(views_root, "Sets", "everything")
    for name, source in zip(sorted(os.listdir(published)), paths):
        entry = os.path.join(published, name)
        assert os.path.islink(entry) or (
            os.stat(entry).st_ino == os.stat(source).st_ino
            and os.stat(entry).st_dev == os.stat(source).st_dev
        ), f"{name} is a copy, not a link"

    shutil.rmtree(views_root)

    for path in paths:
        assert os.path.isfile(path)
        assert os.path.getsize(path) > 0


def test_a_picture_in_two_projects_appears_twice_and_never_moves(tmp_path, library):
    """The load-bearing answer to multi-membership: two folders, one real file."""
    image_root, paths = library
    shared = _picture(1, paths[0])
    vault = _FakeVault(
        image_root,
        {"projects": [(1, "Editorial", [shared]), (2, "Portfolio", [shared])]},
    )
    views_root = str(tmp_path / "views")

    report = views_service.publish(vault, views_root, ["projects"], vault.collected)

    assert report.links == 2 and report.folders == 2
    for project in ("Editorial", "Portfolio"):
        entry = os.path.join(views_root, "Projects", project, "0410.png")
        assert os.path.lexists(entry)
        with open(entry, "rb") as handle:
            assert handle.read() == open(paths[0], "rb").read()
    assert os.path.isfile(paths[0])


def test_a_rebuild_never_deletes_a_file_the_owner_put_in_a_view_folder(
    tmp_path, library
):
    """The one that matters. Every line of this feature's copy invites the gesture.

    A view folder looks like an ordinary folder and is described as one, so
    somebody will drop a real file into it. ``shutil.rmtree`` is not link-aware
    and would delete it on the next rebuild, silently, while the UI still said
    "deleting the whole folder loses no picture". The rebuild therefore removes
    only names that are not the last one: a symlink, or a file with another hard
    link elsewhere.
    """
    image_root, paths = library
    picture = _picture(1, paths[0])
    vault = _FakeVault(image_root, {"sets": [(1, "mira-lora-v3", [picture])]})
    views_root = str(tmp_path / "views")
    views_service.publish(vault, views_root, ["sets"], vault.collected)

    intruder = os.path.join(views_root, "Sets", "mira-lora-v3", "irreplaceable.raw")
    with open(intruder, "wb") as handle:
        handle.write(b"the only copy")

    report = views_service.publish(vault, views_root, ["sets"], vault.collected)

    assert os.path.isfile(intruder), "the rebuild deleted the owner's file"
    with open(intruder, "rb") as handle:
        assert handle.read() == b"the only copy"
    assert report.kept_by_owner == ["Sets/mira-lora-v3/irreplaceable.raw"]
    assert report.links == 1, "the links were not rebuilt around it"


def test_turning_views_off_never_deletes_a_file_the_owner_put_there(tmp_path, library):
    image_root, paths = library
    vault = _FakeVault(image_root, {"sets": [(1, "s", [_picture(1, paths[0])])]})
    views_root = str(tmp_path / "views")
    views_service.publish(vault, views_root, ["sets"], vault.collected)
    intruder = os.path.join(views_root, "Sets", "s", "mine.raw")
    with open(intruder, "wb") as handle:
        handle.write(b"mine")

    views_service.remove(views_root)

    assert os.path.isfile(intruder)
    # The marker stays while anything is still standing, or the next
    # reference-folder scan would index every surviving link as a new picture.
    assert os.path.exists(os.path.join(views_root, VIEWS_MARKER_NAME))


def test_a_symlink_standing_in_for_a_kind_folder_never_steers_the_rebuild_out(
    tmp_path, library
):
    """rmtree fails on a symlinked directory and makedirs then follows it.

    Skipped where a *directory* symlink cannot be created at all - Windows
    without Developer Mode or admin rights. The hazard needs one to exist, so a
    host that cannot make one is not exposed to it; failing here would report
    the feature's supported hard-link fallback as a bug.
    """
    try:
        os.symlink(
            str(tmp_path), str(tmp_path / "symlink-probe"), target_is_directory=True
        )
    except OSError as exc:
        pytest.skip(f"this host cannot create a directory symlink: {exc}")
    os.remove(tmp_path / "symlink-probe")

    image_root, paths = library
    outside = tmp_path / "somewhere-else"
    outside.mkdir()
    (outside / "owner-file.txt").write_text("not ours")
    views_root = tmp_path / "views"
    views_root.mkdir()
    (views_root / views_service.MARKER_NAME).write_text("")
    os.symlink(str(outside), str(views_root / "Sets"), target_is_directory=True)
    vault = _FakeVault(image_root, {"sets": [(1, "s", [_picture(1, paths[0])])]})

    views_service.publish(vault, str(views_root), ["sets"], vault.collected)

    assert sorted(os.listdir(outside)) == ["owner-file.txt"], (
        "the rebuild wrote through the symlink and out of the views root"
    )
    assert not os.path.islink(views_root / "Sets")
    assert os.path.isdir(views_root / "Sets" / "s")


def test_switching_a_kind_off_removes_its_folder(tmp_path, library):
    """A kind that is off is a kind whose folder must not survive, stale."""
    image_root, paths = library
    picture = _picture(1, paths[0])
    vault = _FakeVault(
        image_root,
        {"sets": [(1, "s", [picture])], "people": [(1, "Mira", [picture])]},
    )
    views_root = str(tmp_path / "views")
    views_service.publish(vault, views_root, ["sets", "people"], vault.collected)
    assert os.path.isdir(os.path.join(views_root, "Sets"))

    views_service.publish(vault, views_root, ["people"], vault.collected)

    assert not os.path.exists(os.path.join(views_root, "Sets"))
    assert os.path.isdir(os.path.join(views_root, "People"))


def test_publishing_no_kinds_at_all_leaves_an_empty_tree(tmp_path, library):
    """The API says an empty list publishes an empty tree, so it must."""
    image_root, paths = library
    vault = _FakeVault(image_root, {"sets": [(1, "s", [_picture(1, paths[0])])]})
    views_root = str(tmp_path / "views")
    views_service.publish(vault, views_root, ["sets"], vault.collected)

    report = views_service.publish(vault, views_root, [], vault.collected)

    assert report.links == 0
    assert os.listdir(views_root) == [VIEWS_MARKER_NAME]
    assert os.path.isfile(paths[0])


@pytest.mark.parametrize("spelling", ["{root}/", "{root}/.", "{root}/sub/.."])
def test_the_same_folder_spelled_differently_is_the_same_folder(tmp_path, spelling):
    """A picker returns a trailing slash one time and not the next.

    A raw string comparison made the caller believe the folder had changed, so it
    published the whole tree and then removed it again as "the old one",
    reporting success over an empty folder.
    """
    root = tmp_path / "views"
    (root / "sub").mkdir(parents=True)

    assert views_service.same_root(str(root), spelling.format(root=root))


def test_two_different_folders_are_not_the_same_folder(tmp_path):
    """Over-matching would leave the abandoned tree behind for ever."""
    assert not views_service.same_root(str(tmp_path / "a"), str(tmp_path / "b"))


def test_a_republish_drops_a_folder_whose_entity_is_gone(tmp_path, library):
    image_root, paths = library
    picture = _picture(1, paths[0])
    views_root = str(tmp_path / "views")
    vault = _FakeVault(
        image_root, {"sets": [(1, "keep", [picture]), (2, "remove-me", [picture])]}
    )
    views_service.publish(vault, views_root, ["sets"], vault.collected)
    assert os.path.isdir(os.path.join(views_root, "Sets", "remove-me"))

    vault.collected = {"sets": [(1, "keep", [picture])]}
    views_service.publish(vault, views_root, ["sets"], vault.collected)

    assert os.path.isdir(os.path.join(views_root, "Sets", "keep"))
    assert not os.path.exists(os.path.join(views_root, "Sets", "remove-me"))
    assert os.path.isfile(paths[0])


def test_a_missing_original_is_counted_not_fatal(tmp_path, library):
    image_root, paths = library
    os.remove(paths[1])
    vault = _FakeVault(
        image_root,
        {"sets": [(1, "mixed", [_picture(i + 1, p) for i, p in enumerate(paths)])]},
    )

    report = views_service.publish(
        vault, str(tmp_path / "views"), ["sets"], vault.collected
    )

    assert report.links == 2
    assert report.skipped_missing == 1


def test_only_the_requested_kinds_are_published(tmp_path, library):
    image_root, paths = library
    picture = _picture(1, paths[0])
    vault = _FakeVault(
        image_root,
        {"sets": [(1, "a set", [picture])], "people": [(1, "Mira", [picture])]},
    )
    views_root = str(tmp_path / "views")

    views_service.publish(vault, views_root, ["people"], vault.collected)

    assert os.path.isdir(os.path.join(views_root, "People"))
    assert not os.path.exists(os.path.join(views_root, "Sets"))


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Mira / studio", "Mira: outdoor", "..", "  ", "trailing.", "back\\slash"],
)
def test_an_entity_name_becomes_exactly_one_path_component(tmp_path, library, name):
    image_root, paths = library
    vault = _FakeVault(image_root, {"sets": [(42, name, [_picture(1, paths[0])])]})
    views_root = str(tmp_path / "views")

    views_service.publish(vault, views_root, ["sets"], vault.collected)

    published = os.listdir(os.path.join(views_root, "Sets"))
    assert len(published) == 1
    assert os.sep not in published[0] and "/" not in published[0]
    assert published[0] not in ("", ".", "..")


def test_a_very_long_name_is_truncated_rather_than_lost(tmp_path, library):
    """A component over NAME_MAX is ENAMETOOLONG, and MAX_PATH is nearer still."""
    image_root, paths = library
    long_name = "Mira " * 80
    vault = _FakeVault(image_root, {"sets": [(1, long_name, [_picture(1, paths[0])])]})
    views_root = str(tmp_path / "views")

    report = views_service.publish(vault, views_root, ["sets"], vault.collected)

    assert report.links == 1
    published = os.listdir(os.path.join(views_root, "Sets"))
    assert len(published) == 1
    assert len(published[0]) <= 80
    assert published[0].startswith("Mira")


def test_two_entities_with_the_same_name_get_separate_folders(tmp_path, library):
    image_root, paths = library
    vault = _FakeVault(
        image_root,
        {
            "people": [
                (1, "Mira", [_picture(1, paths[0])]),
                (2, "Mira", [_picture(2, paths[1])]),
            ]
        },
    )
    views_root = str(tmp_path / "views")

    report = views_service.publish(vault, views_root, ["people"], vault.collected)

    assert report.folders == 2
    assert len(os.listdir(os.path.join(views_root, "People"))) == 2


def test_two_long_names_sharing_a_prefix_still_get_separate_folders(tmp_path, library):
    """Truncation must not silently merge two entities into one folder."""
    image_root, paths = library
    prefix = "Shoot " * 30
    vault = _FakeVault(
        image_root,
        {
            "sets": [
                (1, prefix + "one", [_picture(1, paths[0])]),
                (2, prefix + "two", [_picture(2, paths[1])]),
            ]
        },
    )
    views_root = str(tmp_path / "views")

    report = views_service.publish(vault, views_root, ["sets"], vault.collected)

    assert report.folders == 2
    assert report.links == 2
    assert len(set(os.listdir(os.path.join(views_root, "Sets")))) == 2


def test_a_name_that_already_looks_disambiguated_still_gets_its_own_folder(
    tmp_path, library
):
    """Suffixing once is not enough when the suffixed form is itself taken.

    Two sets called ``s`` and ``s (2)`` collide the moment the first is given
    picture 2's disambiguator, and a single pass would hand back a duplicate -
    an ``EEXIST`` at link time, reported to the owner as a filesystem refusal.
    """
    image_root, paths = library
    # The ids are what makes this collide: set 7's disambiguated name is
    # "s (7)", which set 3 is already literally called.
    vault = _FakeVault(
        image_root,
        {
            "sets": [
                (1, "s", [_picture(1, paths[0])]),
                (3, "s (7)", [_picture(2, paths[1])]),
                (7, "s", [_picture(3, paths[2])]),
            ]
        },
    )
    views_root = str(tmp_path / "views")

    report = views_service.publish(vault, views_root, ["sets"], vault.collected)

    published = os.listdir(os.path.join(views_root, "Sets"))
    assert len(published) == 3, f"two sets shared a folder: {published}"
    assert report.folders == 3
    assert report.links == 3


def test_a_file_that_vanishes_mid_prune_does_not_abort_the_publish(tmp_path, library):
    """The prune's error path must not re-``lstat`` a path that has gone."""
    image_root, paths = library
    vault = _FakeVault(image_root, {"sets": [(1, "s", [_picture(1, paths[0])])]})
    views_root = str(tmp_path / "views")
    views_service.publish(vault, views_root, ["sets"], vault.collected)

    published = os.path.join(views_root, "Sets", "s", "0410.png")
    assert os.path.lexists(published)

    real_remove = os.remove

    def remove_and_vanish(path, *args, **kwargs):
        # The race the handler has to survive: the unlink reports a failure and
        # the entry is gone anyway by the time anything looks again. Re-lstating
        # it there raised INSIDE the except block and aborted the whole publish.
        if os.path.basename(path) == "0410.png":
            real_remove(path)
            raise OSError(13, "Permission denied")
        return real_remove(path, *args, **kwargs)

    original = os.remove
    os.remove = remove_and_vanish
    try:
        report = views_service.publish(vault, views_root, ["sets"], vault.collected)
    finally:
        os.remove = original

    assert report.links == 1, "the publish aborted instead of carrying on"
    assert report.kept_by_owner == [], "a vanished entry is not something kept"


def test_two_pictures_with_the_same_filename_both_appear(tmp_path):
    root = tmp_path / "library"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "0412.png").write_bytes(b"A")
    (root / "b" / "0412.png").write_bytes(b"B")
    vault = _FakeVault(
        str(root),
        {
            "sets": [
                (
                    1,
                    "collides",
                    [
                        _picture(1, str(root / "a" / "0412.png")),
                        _picture(2, str(root / "b" / "0412.png")),
                    ],
                )
            ]
        },
    )
    views_root = str(tmp_path / "views")

    report = views_service.publish(vault, views_root, ["sets"], vault.collected)

    assert report.links == 2
    assert len(os.listdir(os.path.join(views_root, "Sets", "collides"))) == 2


# ---------------------------------------------------------------------------
# Where views may not go - one refusal per hazard the spike measured
# ---------------------------------------------------------------------------


def test_a_folder_that_pixlstash_did_not_create_is_never_emptied(tmp_path, library):
    """The one guard between a rebuild's deletes and somebody's pictures folder."""
    image_root, paths = library
    someones_pictures = tmp_path / "Pictures"
    someones_pictures.mkdir()
    (someones_pictures / "wedding.jpg").write_bytes(b"irreplaceable")
    vault = _FakeVault(image_root, {"sets": [(1, "s", [_picture(1, paths[0])])]})

    with pytest.raises(views_service.ViewsError) as refusal:
        views_service.publish(vault, str(someones_pictures), ["sets"], vault.collected)

    assert "already has things in it" in str(refusal.value)
    assert (someones_pictures / "wedding.jpg").read_bytes() == b"irreplaceable"
    assert os.listdir(someones_pictures) == ["wedding.jpg"]


def test_an_empty_folder_is_adopted_and_then_reused(tmp_path, library):
    image_root, paths = library
    chosen = tmp_path / "empty"
    chosen.mkdir()
    vault = _FakeVault(image_root, {"sets": [(1, "s", [_picture(1, paths[0])])]})

    views_service.publish(vault, str(chosen), ["sets"], vault.collected)
    assert (chosen / VIEWS_MARKER_NAME).exists()
    views_service.publish(vault, str(chosen), ["sets"], vault.collected)

    assert os.path.isdir(chosen / "Sets" / "s")


def test_a_views_root_inside_the_library_is_refused(tmp_path, library):
    """Backups refuse a symlinked payload, so a tree under image_root breaks them."""
    image_root, _paths = library
    vault = _FakeVault(image_root)

    with pytest.raises(views_service.ViewsError, match="inside the library"):
        views_service.check_views_root(os.path.join(image_root, "views"), vault)


def test_a_views_root_containing_the_library_is_refused(tmp_path, library):
    image_root, _paths = library
    vault = _FakeVault(image_root)

    with pytest.raises(views_service.ViewsError, match="contains the library"):
        views_service.check_views_root(os.path.dirname(image_root), vault)


def test_a_views_root_inside_another_registered_library_is_refused(tmp_path, library):
    """v1.11 lets the owner register several libraries from Settings.

    A views tree inside a dormant one breaks that library's backups exactly as
    it would the active one's, and this vault cannot see the hub registry - so
    the roots are passed in.
    """
    image_root, _paths = library
    other = tmp_path / "second-library"
    other.mkdir()
    vault = _FakeVault(image_root)

    with pytest.raises(views_service.ViewsError, match="overlaps another library"):
        views_service.check_views_root(
            str(other / "views"), vault, other_library_roots=[str(other)]
        )


def test_a_views_root_containing_another_registered_library_is_refused(
    tmp_path, library
):
    image_root, _paths = library
    other = tmp_path / "outer" / "second-library"
    other.mkdir(parents=True)
    vault = _FakeVault(image_root)

    with pytest.raises(views_service.ViewsError, match="overlaps another library"):
        views_service.check_views_root(
            str(tmp_path / "outer"), vault, other_library_roots=[str(other)]
        )


def test_a_folder_beside_every_registered_library_is_allowed(tmp_path, library):
    """Over-refusing is its own regression, and the registry lists this vault too."""
    image_root, _paths = library
    vault = _FakeVault(image_root)

    views_service.check_views_root(
        str(tmp_path / "_PixlStash Views"),
        vault,
        other_library_roots=[image_root, str(tmp_path / "second-library")],
    )


def test_a_views_root_inside_a_reference_folder_is_refused(tmp_path, library):
    """The scan lists symlinked files, so the links would be indexed as pictures."""
    image_root, _paths = library
    reference = tmp_path / "reference"
    reference.mkdir()
    vault = _FakeVault(image_root, reference_roots=(str(reference),))

    with pytest.raises(views_service.ViewsError, match="inside a reference folder"):
        views_service.check_views_root(str(reference / "views"), vault)


def test_a_views_root_containing_a_reference_folder_is_refused(tmp_path, library):
    image_root, _paths = library
    reference = tmp_path / "outer" / "reference"
    reference.mkdir(parents=True)
    vault = _FakeVault(image_root, reference_roots=(str(reference),))

    with pytest.raises(views_service.ViewsError, match="contains a reference folder"):
        views_service.check_views_root(str(tmp_path / "outer"), vault)


@pytest.mark.parametrize("marker", [".dropbox.cache", ".tmp.driveupload"])
def test_a_cloud_sync_folder_is_refused_by_its_marker(tmp_path, library, marker):
    """A sync client uploads the file a link points at, duplicating the library."""
    image_root, _paths = library
    synced = tmp_path / "synced"
    (synced / "PixlStash").mkdir(parents=True)
    (synced / marker).write_text("")
    vault = _FakeVault(image_root)

    with pytest.raises(views_service.ViewsError, match="cloud client"):
        views_service.check_views_root(str(synced / "PixlStash"), vault)


def test_a_cloud_sync_folder_is_refused_by_its_name(tmp_path, library):
    image_root, _paths = library
    synced = tmp_path / "OneDrive" / "PixlStash"
    synced.mkdir(parents=True)
    vault = _FakeVault(image_root)

    with pytest.raises(views_service.ViewsError, match="cloud client"):
        views_service.check_views_root(str(synced), vault)


def test_a_sync_clients_config_in_the_home_directory_refuses_nothing(
    tmp_path, library, monkeypatch
):
    """The Dropbox client keeps ``~/.dropbox`` beside the synced folder, not in it.

    Walking past the home directory made that config file refuse every path a
    Dropbox user could pick, with no override and a message asserting something
    untrue of the folder they chose. Over-refusing is its own regression.
    """
    image_root, _paths = library
    home = tmp_path / "home"
    (home / ".dropbox").mkdir(parents=True)
    (home / "Pictures" / "Views").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    vault = _FakeVault(image_root)

    views_service.check_views_root(str(home / "Pictures" / "Views"), vault)


def test_an_ordinary_folder_beside_the_library_is_allowed(tmp_path, library):
    """Over-refusing is its own regression: the normal choice must still pass."""
    image_root, _paths = library
    vault = _FakeVault(image_root)

    views_service.check_views_root(str(tmp_path / "_PixlStash Views"), vault)


def test_a_views_root_in_a_restricted_system_directory_is_refused(tmp_path, library):
    """Every refusal here used to be relative to the library, so anywhere else passed.

    A root that is not inside the library, not inside a reference folder and not
    synced was accepted anywhere on the disk, and `_prepare_root` then created
    the folder and wrote the marker into it.
    """
    image_root, _paths = library
    vault = _FakeVault(image_root)

    with pytest.raises(views_service.ViewsError, match="restricted system directory"):
        views_service.check_views_root("/etc/pixlstash-views", vault)


def test_a_symlink_into_a_restricted_directory_is_refused_as_a_views_root(
    tmp_path, library
):
    """The blocklist is literal, so the root is resolved before it is compared."""
    image_root, _paths = library
    vault = _FakeVault(image_root)
    link = tmp_path / "looks-ordinary"
    try:
        link.symlink_to("/etc", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    if not os.path.isdir("/etc"):
        pytest.skip("no /etc on this platform")

    with pytest.raises(views_service.ViewsError, match="restricted system directory"):
        views_service.check_views_root(str(link / "pixlstash-views"), vault)


def test_a_relative_views_root_is_refused(tmp_path, library):
    image_root, _paths = library
    vault = _FakeVault(image_root)

    with pytest.raises(views_service.ViewsError, match="full path"):
        views_service.check_views_root("views", vault)


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------


def test_removing_a_tree_leaves_the_folder_and_every_picture(tmp_path, library):
    image_root, paths = library
    vault = _FakeVault(image_root, {"sets": [(1, "s", [_picture(1, paths[0])])]})
    views_root = str(tmp_path / "views")
    views_service.publish(vault, views_root, ["sets"], vault.collected)

    views_service.remove(views_root)

    assert os.path.isdir(views_root)
    assert os.listdir(views_root) == []
    assert os.path.isfile(paths[0])


def test_remove_refuses_a_folder_with_no_marker(tmp_path):
    unclaimed = tmp_path / "not-ours"
    (unclaimed / "Sets").mkdir(parents=True)
    (unclaimed / "Sets" / "keep.txt").write_text("mine")

    views_service.remove(str(unclaimed))

    assert (unclaimed / "Sets" / "keep.txt").exists()


# ---------------------------------------------------------------------------
# Through the API, against a real vault
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _env():
    """One logged-in Server shared by the API tests in this module."""
    temp_dir = tempfile.TemporaryDirectory()
    try:
        os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
        server_config_path = os.path.join(temp_dir.name, "server-config.json")
        with open(server_config_path, "w") as fh:
            fh.write(json.dumps({"port": 8000}))
        server = Server(server_config_path)
        try:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200
            yield client, server, temp_dir.name
        finally:
            server.close()
    finally:
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture(scope="module")
def _seeded(_env):
    """Two imported pictures, in one set, one project and against one character."""
    client, server, _tmp = _env
    files = []
    for index in range(2):
        buffer = io.BytesIO()
        Image.new("RGB", (20 + index, 20 + index), (index * 60, 40, 90)).save(
            buffer, format="PNG"
        )
        files.append(
            ("file", (f"view-seed-{index}.png", buffer.getvalue(), "image/png"))
        )
    upload_pictures_and_wait(client, files)

    def _seed(session: Session):
        ids = [
            picture.id
            for picture in session.exec(select(Picture).order_by(Picture.id)).all()
        ][:2]
        assert len(ids) == 2, "the seed upload did not land"
        picture_set = PictureSet(name="mira-lora-v3")
        project = Project(name="Editorial")
        character = Character(name="Mira")
        session.add_all([picture_set, project, character])
        session.commit()
        session.refresh(picture_set)
        session.refresh(project)
        session.refresh(character)
        for picture_id in ids:
            session.add(PictureSetMember(set_id=picture_set.id, picture_id=picture_id))
        session.add(PictureProjectMember(picture_id=ids[0], project_id=project.id))
        session.add(
            Face(
                picture_id=ids[0],
                character_id=character.id,
                frame_index=0,
                face_index=0,
            )
        )
        session.commit()
        return ids

    return server.vault.db.run_task(_seed)


def test_views_are_off_until_a_folder_is_named(_env):
    client, _server, _tmp = _env
    body = client.get(f"{API}/server-config/views").json()
    assert body["views_root"] is None
    assert body["available_kinds"] == ["people", "sets", "projects"]


def test_patch_publishes_the_tree_and_get_reports_it(_env, _seeded, tmp_path):
    client, _server, _tmp = _env
    views_root = str(tmp_path / "api-views")

    resp = client.patch(
        f"{API}/server-config/views",
        json={"views_root": views_root, "kinds": ["sets", "people", "projects"]},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["views_root"] == views_root
    assert body["kinds"] == ["people", "sets", "projects"]
    assert body["last_publish"]["links"] == 4  # 2 in the set, 1 project, 1 person
    assert os.path.isdir(os.path.join(views_root, "Sets", "mira-lora-v3"))
    assert os.path.isdir(os.path.join(views_root, "People", "Mira"))
    assert os.path.isdir(os.path.join(views_root, "Projects", "Editorial"))
    assert client.get(f"{API}/server-config/views").json()["views_root"] == views_root


def test_a_views_root_outside_the_configured_filesystem_roots_is_refused(
    _env, _seeded, tmp_path, monkeypatch
):
    """An operator who confined the picker did not exempt the route that makes links."""
    client, server, _tmp = _env
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setitem(server._server_config, "filesystem_roots", [str(allowed)])

    outside = client.patch(
        f"{API}/server-config/views",
        json={"views_root": str(tmp_path / "outside"), "kinds": ["sets"]},
    )
    assert outside.status_code == 403, outside.text
    assert not os.path.exists(str(tmp_path / "outside"))

    # Over-blocking is its own regression: inside the root still publishes.
    inside = client.patch(
        f"{API}/server-config/views",
        json={"views_root": str(allowed / "views"), "kinds": ["sets"]},
    )
    assert inside.status_code == 200, inside.text


def test_a_refused_folder_never_becomes_the_recorded_one(_env, _seeded, tmp_path):
    client, server, _tmp = _env
    good = str(tmp_path / "recorded-views")
    client.patch(
        f"{API}/server-config/views", json={"views_root": good, "kinds": ["sets"]}
    )

    inside_library = os.path.join(server.vault.image_root, "views")
    resp = client.patch(
        f"{API}/server-config/views",
        json={"views_root": inside_library, "kinds": ["sets"]},
    )

    assert resp.status_code == 400
    assert "inside the library" in resp.json()["detail"]
    assert client.get(f"{API}/server-config/views").json()["views_root"] == good
    assert not os.path.exists(inside_library)


def test_an_empty_views_root_is_refused_and_never_removes_the_tree(
    _env, _seeded, tmp_path
):
    """`null` turns views off. An empty string is a malformed body, not "off".

    Treating any falsy value as off meant a malformed request removed the
    published tree with no location check having run at all.
    """
    client, _server, _tmp = _env
    views_root = str(tmp_path / "empty-string-views")
    client.patch(
        f"{API}/server-config/views", json={"views_root": views_root, "kinds": ["sets"]}
    )
    assert os.path.isdir(os.path.join(views_root, "Sets"))

    resp = client.patch(
        f"{API}/server-config/views", json={"views_root": "", "kinds": ["sets"]}
    )

    assert resp.status_code == 400
    assert "full path" in resp.json()["detail"]
    assert os.path.isdir(os.path.join(views_root, "Sets")), "the tree was removed"
    assert client.get(f"{API}/server-config/views").json()["views_root"] == views_root


def test_turning_views_off_removes_the_tree_and_keeps_the_pictures(
    _env, _seeded, tmp_path
):
    client, server, _tmp = _env
    views_root = str(tmp_path / "off-views")
    client.patch(
        f"{API}/server-config/views", json={"views_root": views_root, "kinds": ["sets"]}
    )
    assert os.path.isdir(os.path.join(views_root, "Sets"))

    resp = client.patch(
        f"{API}/server-config/views", json={"views_root": None, "kinds": []}
    )

    assert resp.status_code == 200
    assert resp.json()["views_root"] is None
    assert not os.path.exists(os.path.join(views_root, "Sets"))
    assert len(os.listdir(server.vault.image_root)) > 0, "the library was touched"


def test_a_views_root_inside_the_previous_one_is_refused(_env, _seeded, tmp_path):
    """Publishing under the old root wrote the whole tree and then deleted it.

    `publish` runs first and `remove(previous_root)` second, so a new root
    inside the old one had the removal walk the folder just written. The
    response still reported the links it had made, over an empty folder.
    """
    client, _server, _tmp = _env
    outer = str(tmp_path / "outer-views")
    first = client.patch(
        f"{API}/server-config/views", json={"views_root": outer, "kinds": ["sets"]}
    )
    assert first.status_code == 200, first.text
    assert os.path.isdir(os.path.join(outer, "Sets", "mira-lora-v3"))

    nested = os.path.join(outer, "People", "Sub")
    resp = client.patch(
        f"{API}/server-config/views", json={"views_root": nested, "kinds": ["sets"]}
    )

    assert resp.status_code == 400, resp.text
    assert "cannot be inside" in resp.json()["detail"]
    # The tree that was already there is untouched, and still the recorded one.
    assert os.path.isdir(os.path.join(outer, "Sets", "mira-lora-v3"))
    assert client.get(f"{API}/server-config/views").json()["views_root"] == outer


def test_a_views_root_containing_the_previous_one_is_refused(_env, _seeded, tmp_path):
    """The same hazard from the other side: the removal would delete a subtree
    of the folder just published."""
    client, _server, _tmp = _env
    inner = str(tmp_path / "wrapper" / "inner-views")
    first = client.patch(
        f"{API}/server-config/views", json={"views_root": inner, "kinds": ["sets"]}
    )
    assert first.status_code == 200, first.text

    resp = client.patch(
        f"{API}/server-config/views",
        json={"views_root": str(tmp_path / "wrapper"), "kinds": ["sets"]},
    )

    assert resp.status_code == 400, resp.text
    assert os.path.isdir(os.path.join(inner, "Sets", "mira-lora-v3"))


def test_republishing_to_the_same_root_is_still_allowed(_env, _seeded, tmp_path):
    """The guard is containment, not equality - re-publishing in place must
    keep working, and it is the case `same_root` already handles."""
    client, _server, _tmp = _env
    root = str(tmp_path / "same-views")
    assert (
        client.patch(
            f"{API}/server-config/views", json={"views_root": root, "kinds": ["sets"]}
        ).status_code
        == 200
    )
    again = client.patch(
        f"{API}/server-config/views",
        json={"views_root": root + os.sep, "kinds": ["sets", "people"]},
    )
    assert again.status_code == 200, again.text
    assert os.path.isdir(os.path.join(root, "Sets", "mira-lora-v3"))
