"""The one model folder PixlStash owns (shelf plan B7).

Exactly one ``kind='managed'`` folder always exists, so a fresh install has a
place to drop a file into and a place to import a run into. It is created on
first run, it is relocatable, and it is not removable - there is no association
to dissolve, only a place for the bytes to be.

The negative cases matter more than the positive one here. ``ensure_managed_folder``
runs on **every** start, so it has to be idempotent against a store that already
exists, against a store the owner has relocated to another drive (its ``path`` is
the authority and this function must never overrule it), and against a directory
that cannot be created (a degraded shelf is bad; a server that will not start is
worse).

Environment: a hub and a tmp directory. The delete-refusal's HTTP half, in both
directions, is in the warm ``tests/test_model_shelf_api.py``.
"""

from __future__ import annotations

import os

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services.managed_model_store import (
    MANAGED_KIND,
    MANAGED_MOVABLE,
    MANAGED_OWNER,
    ensure_managed_folder,
    find_managed_folder,
    managed_store_path,
)

SKIP_AS_ROOT = pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0,
    reason="root ignores the permission bits this test removes",
)


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    yield database
    database.close()


def managed_rows(hub):
    return hub.fetchall(
        "SELECT * FROM model_folder WHERE kind = ? ORDER BY id", (MANAGED_KIND,)
    )


def test_first_run_creates_exactly_one_managed_folder(hub, tmp_path):
    config_dir = str(tmp_path / "config")
    os.makedirs(config_dir)

    row = ensure_managed_folder(hub, config_dir)

    assert row is not None
    assert row["path"] == managed_store_path(config_dir)
    assert row["owner"] == MANAGED_OWNER
    assert row["movable"] == MANAGED_MOVABLE
    assert os.path.isdir(row["path"]), "the directory must exist, not just the row"
    assert len(managed_rows(hub)) == 1


def test_the_store_lives_beside_the_config_not_at_a_fixed_platform_path(hub, tmp_path):
    """The hub already follows ``--server-config`` rather than sitting at
    ``user_data_dir`` (issue #168), and the reason is stronger for a directory
    files are copied into and unlinked from: a fixed path would have every test
    run writing into the owner's real store."""
    config_dir = str(tmp_path / "elsewhere")
    os.makedirs(config_dir)
    row = ensure_managed_folder(hub, config_dir)
    assert row["path"].startswith(config_dir)


def test_starting_again_creates_nothing_new(hub, tmp_path):
    config_dir = str(tmp_path / "config")
    os.makedirs(config_dir)

    first = ensure_managed_folder(hub, config_dir)
    for _ in range(3):
        again = ensure_managed_folder(hub, config_dir)
        assert again["id"] == first["id"]
    assert len(managed_rows(hub)) == 1


def test_a_relocated_store_is_never_dragged_back(hub, tmp_path):
    """The row's ``path`` is the authority. A start that re-pointed the store at
    the config dir would silently strand every file the owner had moved."""
    config_dir = str(tmp_path / "config")
    elsewhere = str(tmp_path / "big-drive" / "models")
    os.makedirs(config_dir)
    os.makedirs(elsewhere)
    ensure_managed_folder(hub, config_dir)
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET path = ? WHERE kind = ?", (elsewhere, MANAGED_KIND)
        )

    row = ensure_managed_folder(hub, config_dir)

    assert row["path"] == elsewhere
    assert len(managed_rows(hub)) == 1


def test_a_folder_already_registered_at_the_path_is_promoted_not_duplicated(
    hub, tmp_path
):
    """``model_folder.path`` is UNIQUE, so an owner who registered this exact
    directory as a ``user`` folder first would otherwise make every start fail on
    the insert."""
    config_dir = str(tmp_path / "config")
    os.makedirs(config_dir)
    path = managed_store_path(config_dir)
    os.makedirs(path)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (path, kind, movable, created_at) "
            "VALUES (?, 'user', 'per_item', '2026-08-09T00:00:00+00:00')",
            (path,),
        )

    row = ensure_managed_folder(hub, config_dir)

    assert row["kind"] == MANAGED_KIND
    assert len(hub.fetchall("SELECT id FROM model_folder")) == 1


@SKIP_AS_ROOT
def test_a_store_that_cannot_be_created_degrades_instead_of_failing_the_start(
    hub, tmp_path, caplog
):
    """A shelf with no default destination is bad. A server that will not boot
    because a directory is unwritable is worse, and the owner has no way to fix
    it from a product that will not start."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_dir.chmod(0o500)
    try:
        row = ensure_managed_folder(hub, str(config_dir))
    finally:
        config_dir.chmod(0o700)

    assert row is None
    assert managed_rows(hub) == []
    assert "Could not create the managed model store" in caplog.text


def test_more_than_one_managed_row_is_reported_and_never_tidied_away(
    hub, tmp_path, caplog
):
    """Nothing should be able to produce two. If something does, deleting one to
    tidy up would drop its ``model_file`` rows, tombstoning real locations to fix
    a bookkeeping error."""
    config_dir = str(tmp_path / "config")
    os.makedirs(config_dir)
    ensure_managed_folder(hub, config_dir)
    second = str(tmp_path / "second")
    os.makedirs(second)
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (path, kind, owner, movable, created_at) "
            "VALUES (?, ?, ?, ?, '2026-08-09T00:00:00+00:00')",
            (second, MANAGED_KIND, MANAGED_OWNER, MANAGED_MOVABLE),
        )

    row = find_managed_folder(hub)

    assert row["path"] == managed_store_path(config_dir), "the lowest id wins"
    assert len(managed_rows(hub)) == 2, "nothing was deleted"
    assert "exactly one is expected" in caplog.text


def test_there_is_no_managed_folder_before_the_first_run(hub):
    assert find_managed_folder(hub) is None
