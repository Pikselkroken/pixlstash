"""One blocklist, three routes that put files somewhere: #1206 item 1.

``POST /pictures/export/folder`` grew the complete list of directories this
installation reads or writes - ``image_root``, the reference folders, the watch
folders, every registered library - while its two siblings kept partial copies
of it:

* ``POST /import-folders`` knew only the active vault's ``image_root``, so a
  watch folder could be pointed at a reference folder or at another registered
  library. It then imports that folder's contents into the active library, and
  one carrying ``delete_after_import`` unlinks each source file afterwards
  (``pixlstash/tasks/watch_folder_import_task.py``), leaving the library that
  owns those pictures with rows pointing at files that are gone.
* The reference-folder create/update/relocate routes knew ``image_root`` and
  the other reference-folder rows. ``POST /reference-folders/{id}/relocate``
  physically ``shutil.move``s every file below the old root, so its destination
  is the one that can move pictures into another library or into a folder a
  watcher then eats.

Both siblings sit on a *wider* authorization tier than the export
(``LOCAL_OWNER_ONLY`` vs ``LOOPBACK_OWNER_ONLY``) and they *move* files where
the export only copies, so the partial copies were the more dangerous two.

Every negative here has its positive control beside it in the same environment:
over-blocking would break import folders and reference folders outright and
would look exactly like the fix working.
"""

import gc
import json
import os
import tempfile
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from pixlstash.server import Server
from pixlstash.utils.path_utils import LibraryRootsUnavailable


@pytest.fixture(scope="module")
def _env():
    """One logged-in Server + TestClient shared by every test in this module.

    No pictures are imported anywhere in this file - every assertion is about a
    folder registration being accepted or refused - so there is no background
    sweep to fight and nothing to reset between tests. Isolation comes from
    each test using its own directories under the shared temp dir and asserting
    on *which* path was refused rather than on any count.
    """
    temp_dir = tempfile.TemporaryDirectory()
    try:
        os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
        server_config_path = os.path.join(temp_dir.name, "server-config.json")
        with open(server_config_path, "w") as fh:
            fh.write(json.dumps({"port": 8000}))
        with Server(server_config_path) as server:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200
            yield client, server, temp_dir.name
    finally:
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture
def client(_env):
    return _env[0]


@pytest.fixture
def server(_env):
    return _env[1]


@pytest.fixture
def workspace(_env):
    return _env[2]


def _mkdir(*parts):
    path = os.path.join(*parts)
    os.makedirs(path, exist_ok=True)
    return path


def _add_reference_folder(client, folder, label):
    return client.post("/reference-folders", json={"folder": folder, "label": label})


def _add_import_folder(client, folder, label, *, delete_after_import=False):
    return client.post(
        "/import-folders",
        json={
            "folder": folder,
            "label": label,
            "delete_after_import": delete_after_import,
        },
    )


def test_import_folder_refuses_a_folder_inside_another_library(
    client, server, workspace
):
    """A watch folder pointed at a second registered library empties it.

    The watcher copies every file it finds into the *active* library and, with
    ``delete_after_import``, unlinks the original - so the other library's
    ``Picture`` rows end up pointing at files that no longer exist. The route
    only ever checked the active vault's ``image_root``, which is not that
    library.
    """
    other = server.library_registry.create(
        os.path.join(workspace, "watched-second-library"), "WatchedSecond"
    )
    inside_other = _mkdir(other.path, "photos")

    refused = _add_import_folder(
        client, inside_other, "into-another-library", delete_after_import=True
    )
    assert refused.status_code == 409, refused.text
    assert "part of your library" in refused.json().get("detail", "")

    # Positive control: a folder in no library is still accepted, with the same
    # `delete_after_import` flag. Refusing every watch folder would satisfy the
    # assertion above and break the feature.
    free = _mkdir(workspace, "free-watch-folder")
    accepted = _add_import_folder(client, free, "free", delete_after_import=True)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["folder"] == free


def test_import_folder_refuses_a_folder_inside_a_reference_folder(client, workspace):
    """A reference folder is scanned in place; a watch folder over it imports
    and (with ``delete_after_import``) deletes the very files that scan indexes.
    Neither subsystem knew about the other."""
    reference = _mkdir(workspace, "reference-for-watch-clash")
    added = _add_reference_folder(client, reference, "ref-for-watch-clash")
    assert added.status_code == 200, added.text

    refused = _add_import_folder(
        client,
        _mkdir(reference, "incoming"),
        "over-a-reference-folder",
        delete_after_import=True,
    )
    assert refused.status_code == 409, refused.text
    assert "part of your library" in refused.json().get("detail", "")

    # Positive control: a sibling of that reference folder, not inside it.
    sibling = _mkdir(workspace, "reference-for-watch-clash-sibling")
    accepted = _add_import_folder(client, sibling, "sibling")
    assert accepted.status_code == 200, accepted.text


def test_reference_folder_refuses_a_folder_inside_a_watched_folder(client, workspace):
    """The mirror of the test above, from the other route.

    ``validate_reference_folder_conflicts`` compares against ``image_root`` and
    the other reference-folder rows only, so a reference folder laid over a
    ``delete_after_import`` watch folder was accepted.
    """
    watched = _mkdir(workspace, "watched-for-reference-clash")
    added = _add_import_folder(
        client, watched, "watched-for-ref-clash", delete_after_import=True
    )
    assert added.status_code == 200, added.text

    refused = _add_reference_folder(
        client, _mkdir(watched, "pictures"), "over-a-watch-folder"
    )
    assert refused.status_code == 409, refused.text
    assert "part of your library" in refused.json().get("detail", "")

    # Positive control: an ordinary folder is still accepted as a reference
    # folder in the same environment.
    free = _mkdir(workspace, "free-reference-folder")
    accepted = _add_reference_folder(client, free, "free-ref")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["folder"] == free


def test_relocating_a_reference_folder_into_another_library_is_refused(
    client, server, workspace
):
    """The worst of the three: relocation MOVES the files.

    ``_validate_relocation_destination`` refused a destination nested with the
    old root and one clashing with ``image_root`` or another reference-folder
    row, and nothing else - so relocating into a second registered library
    physically moved the pictures into a folder that library indexes as its own.
    """
    source = _mkdir(workspace, "relocatable-folder")
    with open(os.path.join(source, "a-file.txt"), "w") as fh:
        fh.write("relocate me")
    added = _add_reference_folder(client, source, "relocatable")
    assert added.status_code == 200, added.text
    folder_id = added.json()["id"]

    other = server.library_registry.create(
        os.path.join(workspace, "relocate-target-library"), "RelocateTarget"
    )
    into_other = os.path.join(other.path, "moved-here")

    refused = client.post(
        f"/reference-folders/{folder_id}/relocate",
        json={"destination_folder": into_other},
    )
    assert refused.status_code == 409, refused.text
    assert "part of your library" in refused.json().get("detail", "")
    # The refusal must come before the first move, not after some of them.
    assert os.path.isfile(os.path.join(source, "a-file.txt")), (
        "nothing may be moved on a refusal"
    )
    assert not os.path.exists(into_other), "no destination may be created either"

    # Positive control: relocating to an ordinary folder still works, in the
    # same environment and for the same row.
    good = os.path.join(workspace, "relocated-somewhere-free")
    accepted = client.post(
        f"/reference-folders/{folder_id}/relocate",
        json={"destination_folder": good},
    )
    assert accepted.status_code == 200, accepted.text
    assert os.path.isfile(os.path.join(good, "a-file.txt"))
    assert not os.path.exists(os.path.join(source, "a-file.txt"))


def test_repointing_a_reference_folder_into_its_own_subfolder_still_works(
    client, workspace
):
    """The over-blocking regression this check could most easily cause.

    ``PATCH /reference-folders/{id}`` repoints a row without moving anything,
    and repointing it *into its own subtree* is legitimate - the row is its own
    root, not a conflict with itself. A blocklist that forgot to exclude the
    row being edited would refuse it, and the refusal would look identical to
    the fix working.
    """
    root = _mkdir(workspace, "repointable-folder")
    deeper = _mkdir(root, "2026")
    added = _add_reference_folder(client, root, "repointable")
    assert added.status_code == 200, added.text
    folder_id = added.json()["id"]

    moved = client.patch(f"/reference-folders/{folder_id}", json={"folder": deeper})
    assert moved.status_code == 200, moved.text
    assert moved.json()["folder"] == deeper


def test_import_folder_refuses_when_the_library_list_cannot_be_read(
    client, server, workspace
):
    """A blocklist that cannot be read must refuse, not permit (#1177 item 59).

    ``[]`` and "we do not know" are the same value with opposite safety here,
    so the shared helper raises and every caller answers 503. This is the
    registry half of that rule, which had no test on either route: the folder
    export's two 503 branches covered the reference-folder and watch-folder
    reads only.
    """
    destination = _mkdir(workspace, "unreadable-registry-watch-folder")

    with mock.patch.object(
        server.library_registry,
        "list_libraries",
        side_effect=RuntimeError("test-induced library registry read failure"),
    ):
        refused = _add_import_folder(client, destination, "unreadable")
    assert refused.status_code == 503, refused.text

    # Positive control: the same folder is accepted once the registry reads.
    accepted = _add_import_folder(client, destination, "readable-again")
    assert accepted.status_code == 200, accepted.text


def test_library_content_roots_wraps_every_read_failure(server):
    """The helper's single failure shape, asserted directly.

    ``LibraryRootsUnavailable`` from the folder reads and a plain exception from
    the registry both have to reach the caller as the one type it maps to 503;
    the export route used to carry two separate ``except`` branches for that and
    only one of them was tested.
    """
    from pixlstash.utils import library_roots

    with mock.patch.object(
        server.library_registry,
        "list_libraries",
        side_effect=RuntimeError("test-induced registry failure"),
    ):
        with pytest.raises(LibraryRootsUnavailable):
            library_roots.library_content_roots(server, server.vault)

    with mock.patch.object(
        server.vault,
        "reference_folder_roots",
        side_effect=LibraryRootsUnavailable("test-induced folder read failure"),
    ):
        with pytest.raises(LibraryRootsUnavailable):
            library_roots.library_content_roots(server, server.vault)

    # Positive control: it returns the roots when everything reads, and
    # `image_root` is among them - an empty list would pass both negatives.
    roots = library_roots.library_content_roots(server, server.vault)
    assert server.vault.image_root in roots


def test_detached_libraries_are_still_in_the_blocklist(server, workspace):
    """``list_libraries()`` defaults to attached libraries only.

    A detached library is not being read *today*, but re-attaching it is one
    click and everything written into it then imports as new pictures, so the
    shared list passes ``include_detached=True``.
    """
    from pixlstash.utils import library_roots

    detached = server.library_registry.create(
        os.path.join(workspace, "detached-library"), "Detached"
    )
    server.library_registry.detach(detached.id)

    assert detached.path in library_roots.library_content_roots(server, server.vault)
