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
import sqlite3
import tempfile
import threading
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from pixlstash.cli import EXIT_OK
from pixlstash.cli import main as cli_main

from pixlstash.server import Server
from pixlstash.services import folder_structure_commit_service as svc
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


def test_watch_and_reference_folders_refuse_overlapping_another_library(
    client, server, workspace
):
    """A watch folder pointed at a second registered library empties it.

    The watcher copies every file it finds into the *active* library and, with
    ``delete_after_import``, unlinks the original - so the other library's
    ``Picture`` rows end up pointing at files that no longer exist. A watch
    folder *containing* that library reaches the same files (#1223).
    """
    parent = _mkdir(workspace, "parent-of-watched-second-library")
    other = server.library_registry.create(
        os.path.join(parent, "watched-second-library"), "WatchedSecond"
    )
    inside_other = _mkdir(other.path, "photos")

    refused = _add_import_folder(
        client, inside_other, "into-another-library", delete_after_import=True
    )
    assert refused.status_code == 409, refused.text
    assert "overlaps a library" in refused.json().get("detail", "")

    around = _add_import_folder(client, parent, "around-another-library")
    assert around.status_code == 409, around.text
    assert "overlaps a library" in around.json().get("detail", "")

    # The reference-folder routes apply the same rule, both ways round.
    for folder in (_mkdir(other.path, "ref-photos"), parent):
        refused = _add_reference_folder(client, folder, "ref-over-another-library")
        assert refused.status_code == 409, refused.text
        assert "overlaps a library" in refused.json().get("detail", "")
    repointable = _mkdir(workspace, "repoint-toward-another-library")
    added = _add_reference_folder(client, repointable, "repoint-toward")
    assert added.status_code == 200, added.text
    repointed = client.patch(
        f"/reference-folders/{added.json()['id']}",
        json={"folder": _mkdir(other.path, "repointed")},
    )
    assert repointed.status_code == 409, repointed.text
    assert "overlaps a library" in repointed.json().get("detail", "")

    # Positive control: a folder in no library is still accepted, with the same
    # `delete_after_import` flag. Refusing every watch folder would satisfy the
    # assertions above and break the feature.
    free = _mkdir(workspace, "free-watch-folder")
    accepted = _add_import_folder(client, free, "free", delete_after_import=True)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["folder"] == free


def test_watch_and_reference_folders_may_overlap_each_other(client, workspace):
    """Watch and reference folders may nest inside each other, either way
    round (#1223): only a library's folder is out of bounds for them."""
    reference = _mkdir(workspace, "reference-with-watch-inside")
    assert _add_reference_folder(client, reference, "ref-outer").status_code == 200
    watch_inside = _add_import_folder(
        client, _mkdir(reference, "incoming"), "watch-inside-ref"
    )
    assert watch_inside.status_code == 200, watch_inside.text

    watched = _mkdir(workspace, "watch-with-reference-inside")
    assert _add_import_folder(client, watched, "watch-outer").status_code == 200
    reference_inside = _add_reference_folder(
        client, _mkdir(watched, "pictures"), "ref-inside-watch"
    )
    assert reference_inside.status_code == 200, reference_inside.text


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
    assert "overlaps a library" in refused.json().get("detail", "")
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


def test_a_reference_commit_refuses_a_folder_overlapping_a_library(
    client, server, workspace
):
    """#1223 item 2: the folder-structure commit registers reference folders
    through ``register_reference_folder``, not the route, and so skipped the
    route's refusal of a folder overlapping another registered library.
    """
    parent = _mkdir(workspace, "parent-of-commit-target-library")
    other = server.library_registry.create(
        os.path.join(parent, "commit-target-library"), "CommitTarget"
    )
    with pytest.raises(svc.CommitError, match="overlaps a library"):
        svc.register_reference_folder(server, _mkdir(other.path, "photos"))
    with pytest.raises(svc.CommitError, match="overlaps a library"):
        svc.register_reference_folder(server, parent)

    # Positive controls: inside a watch folder is allowed, as is a free folder.
    watched = _mkdir(workspace, "watched-for-commit")
    assert _add_import_folder(client, watched, "watched-for-commit").status_code == 200
    inside_watch = _mkdir(watched, "pictures")
    assert svc.register_reference_folder(server, inside_watch).folder == (
        os.path.realpath(inside_watch)
    )
    free = _mkdir(workspace, "free-commit-folder")
    assert svc.register_reference_folder(server, free).folder == os.path.realpath(free)


def test_a_library_may_not_overlap_a_watch_or_reference_folder(client, workspace):
    """The reverse rule (#1223): ``POST /libraries`` refuses a library inside,
    or around, one of this library's watch or reference folders, and the
    picker's inspection says so before the Add button is offered."""
    watched = _mkdir(workspace, "watch-holding-a-library")
    assert _add_import_folder(client, watched, "watch-holding").status_code == 200
    reference = _mkdir(workspace, "reference-holding-a-library")
    assert _add_reference_folder(client, reference, "ref-holding").status_code == 200

    around = _mkdir(workspace, "around-a-watch-folder")
    assert (
        _add_import_folder(
            client, _mkdir(around, "incoming"), "watch-in-around"
        ).status_code
        == 200
    )

    for folder in (
        _mkdir(watched, "library"),
        _mkdir(reference, "library"),
        around,
    ):
        inspected = client.get("/libraries/inspect", params={"path": folder})
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()["can_add"] is False, inspected.json()
        refused = client.post("/libraries", json={"path": folder})
        assert refused.status_code == 409, refused.text
        assert "watch or reference folder" in refused.json().get("detail", "")

    # Positive control: a free folder is still added.
    free = _mkdir(workspace, "free-library-folder")
    assert client.post("/libraries", json={"path": free}).status_code == 201


def test_the_cli_library_verbs_apply_the_same_rule(client, server, workspace):
    """``libraries create`` and ``libraries attach`` call the registry directly,
    so the rule lives there rather than in ``POST /libraries`` (#1223)."""
    hub = server.hub.path
    unregistered_vault = _mkdir(workspace, "watched-parent-of-a-vault", "vault")
    made = server.library_registry.create(unregistered_vault)
    with server.hub.transaction() as conn:
        conn.execute("DELETE FROM library WHERE id = ?", (made.id,))
    watched = os.path.dirname(unregistered_vault)
    assert _add_import_folder(client, watched, "cli-watched").status_code == 200

    nested = os.path.join(watched, "created-by-the-cli")
    assert cli_main(["--hub", hub, "libraries", "create", nested]) != EXIT_OK
    assert not os.path.exists(os.path.join(nested, "vault.db"))
    assert cli_main(["--hub", hub, "libraries", "attach", unregistered_vault]) != (
        EXIT_OK
    )

    # Positive control: the CLI still creates a library in a free folder.
    free = os.path.join(workspace, "created-by-the-cli-freely")
    assert cli_main(["--hub", hub, "libraries", "create", free]) == EXIT_OK


def test_a_watch_folder_of_another_library_blocks_a_library_too(
    client, server, workspace
):
    """Every registered library's watch and reference folders count, read from
    its vault file, not only the active library's: a switch must not open a
    way around the rule, and the check must not depend on which vault is open.
    """
    other = server.library_registry.create(
        os.path.join(workspace, "library-with-its-own-watch-folder"), "OwnWatch"
    )
    their_watch = _mkdir(workspace, "watched-by-another-library")
    conn = sqlite3.connect(os.path.join(other.path, "vault.db"))
    try:
        conn.execute(
            "INSERT INTO import_folder (folder, label, delete_after_import) "
            "VALUES (?, ?, 0)",
            (their_watch, "theirs"),
        )
        conn.commit()
    finally:
        conn.close()

    refused = client.post("/libraries", json={"path": _mkdir(their_watch, "a-library")})
    assert refused.status_code == 409, refused.text
    assert "OwnWatch" in refused.json().get("detail", "")


def test_a_library_and_a_watch_folder_added_at_once_cannot_both_land(
    client, server, workspace, monkeypatch
):
    """The check and the write are one step (#1223). A library add paused just
    after its check, inside ``create``, must keep a watch folder around it
    waiting, and that watch folder must then be refused rather than accepted
    against the snapshot it read before the library existed."""
    watched = _mkdir(workspace, "raced-watch-folder")
    nested = _mkdir(watched, "raced-library")
    registry = server.library_registry
    original = registry.refuse_overlapping_watch_or_reference_folder
    checked, release = threading.Event(), threading.Event()
    calls = []

    def pausing_check(folder):
        original(folder)
        calls.append(folder)
        # The first call is the picker inspection, outside the lock; the second
        # is `create`'s own, under it.
        if len(calls) == 2:
            checked.set()
            release.wait(10)

    monkeypatch.setattr(
        registry, "refuse_overlapping_watch_or_reference_folder", pausing_check
    )
    results = {}
    adding_library = threading.Thread(
        target=lambda: results.update(
            library=client.post("/libraries", json={"path": nested})
        )
    )
    adding_watch = threading.Thread(
        target=lambda: results.update(
            watch=_add_import_folder(client, watched, "raced-watch")
        )
    )
    adding_library.start()
    try:
        assert checked.wait(10), "the library add never reached its check"
        adding_watch.start()
        adding_watch.join(0.5)
        assert adding_watch.is_alive(), (
            "the watch folder was written while a library add held its check"
        )
    finally:
        release.set()
        adding_library.join(10)
        if adding_watch.ident is not None:
            adding_watch.join(10)

    assert results["library"].status_code == 201, results["library"].text
    assert results["watch"].status_code == 409, results["watch"].text
