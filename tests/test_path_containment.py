"""Containment of stored file paths before they reach the filesystem (#776).

A ``Picture.file_path`` (or a snapshot's ``relative_path``) is a database value
and must be treated as untrusted: a substituted vault DB or restored archive
can put an arbitrary path in it.  These tests assert BOTH directions at every
sink:

- a stored path that escapes the legitimate roots (image_root + configured
  reference folders) is refused at the resolver, the snapshot retention /
  delete paths, the scrapheap purge delete, the caption sidecar write, and the
  picture-serving route; and
- an ordinary in-root path still resolves, is served, and is cleaned up — and,
  most importantly, a picture under a reference folder OUTSIDE image_root
  still works (over-blocking would break real libraries).
"""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import delete, select

from pixlstash.db_models import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.server import Server
from pixlstash.services.scrapheap_service import remove_picture_files
from pixlstash.utils.caption_file_utils import SIDECAR_TYPE_TAGS, writeback_path
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.path_utils import (
    is_allowed_picture_path,
    register_reference_roots_provider,
    unregister_reference_roots_provider,
)


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = os.path.join(tmp, "server-config.json")
        with open(config_path, "w") as fh:
            json.dump({"disable_background_workers": True}, fh)
        with Server(server_config_path=config_path) as srv:
            yield srv


@pytest.fixture(scope="module")
def client(server):
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return client


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


def _add_picture(server, file_path: str, fmt: str = "png") -> int:
    def _do(session):
        pic = Picture(
            file_path=file_path,
            format=fmt,
            original_file_name=os.path.basename(file_path),
        )
        session.add(pic)
        session.commit()
        return pic.id

    return server.vault.db.run_task(_do)


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
# Resolver
# ---------------------------------------------------------------------------


def test_resolver_refuses_paths_outside_the_roots(tmp_path):
    image_root = str(tmp_path / "library")
    os.makedirs(image_root)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"x")
    # Absolute path outside every root (no reference folders registered).
    assert ImageUtils.resolve_picture_path(image_root, str(outside)) is None
    # Relative traversal out of the image root.
    assert ImageUtils.resolve_picture_path(image_root, "../secret.png") is None


def test_resolver_still_resolves_ordinary_paths(tmp_path):
    image_root = str(tmp_path / "library")
    os.makedirs(image_root)
    assert ImageUtils.resolve_picture_path(image_root, "a/b.png") == os.path.join(
        image_root, "a/b.png"
    )
    in_root = os.path.join(image_root, "c.png")
    assert ImageUtils.resolve_picture_path(image_root, in_root) == in_root


def test_resolver_allows_registered_reference_folder_roots(tmp_path):
    image_root = str(tmp_path / "library")
    ref_root = str(tmp_path / "external-refs")
    os.makedirs(image_root)
    os.makedirs(ref_root)
    provider = lambda: [ref_root]  # noqa: E731
    register_reference_roots_provider(image_root, provider)
    try:
        ref_pic = os.path.join(ref_root, "sub", "pic.png")
        assert ImageUtils.resolve_picture_path(image_root, ref_pic) == ref_pic
        # Still refuses a sibling directory that is NOT a registered root.
        assert (
            ImageUtils.resolve_picture_path(image_root, str(tmp_path / "other.png"))
            is None
        )
    finally:
        unregister_reference_roots_provider(image_root, provider)
    # With the provider gone, only image_root remains allowed.
    assert not is_allowed_picture_path(image_root, os.path.join(ref_root, "p.png"))


def test_vault_provider_allows_reference_folders_from_the_db(server, tmp_path):
    """The Vault wires the reference_folder table into the resolver's root set."""
    ref_root = str(tmp_path / "vault-refs")
    os.makedirs(ref_root)
    _add_reference_folder(server, ref_root)
    ref_pic = os.path.join(ref_root, "pic.png")
    assert ImageUtils.resolve_picture_path(server.vault.image_root, ref_pic) == ref_pic


# ---------------------------------------------------------------------------
# Picture serving route
# ---------------------------------------------------------------------------


def test_serving_refuses_out_of_root_file_path(server, client, tmp_path):
    secret = tmp_path / "hostname-secret.png"
    _write_png(str(secret))
    pic_id = _add_picture(server, str(secret))
    resp = client.get(f"/pictures/{pic_id}.png")
    assert resp.status_code == 404
    assert secret.read_bytes() not in (resp.content,)


def test_serving_in_root_picture_still_works(server, client):
    rel = "contain/in_root.png"
    _write_png(os.path.join(server.vault.image_root, rel))
    pic_id = _add_picture(server, rel)
    resp = client.get(f"/pictures/{pic_id}.png")
    assert resp.status_code == 200, resp.text


def test_serving_reference_folder_picture_outside_image_root_still_works(
    server, client, tmp_path
):
    """The over-blocking regression test: reference-folder pictures live under
    roots that are NOT the image root and must keep working end to end."""
    ref_root = str(tmp_path / "served-refs")
    abs_path = os.path.join(ref_root, "ref_pic.png")
    _write_png(abs_path)
    _add_reference_folder(server, ref_root)
    pic_id = _add_picture(server, abs_path)
    resp = client.get(f"/pictures/{pic_id}.png")
    assert resp.status_code == 200, resp.text


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


# ---------------------------------------------------------------------------
# Scrapheap purge delete
# ---------------------------------------------------------------------------


def test_scrapheap_purge_refuses_out_of_root_targets(tmp_path):
    image_root = str(tmp_path / "library")
    os.makedirs(image_root)
    victim = tmp_path / "victim-purge.png"
    victim.write_bytes(b"precious")
    remove_picture_files(image_root, [(1, str(victim), False)])
    remove_picture_files(image_root, [(2, "../victim-purge.png", False)])
    assert victim.read_bytes() == b"precious"


def test_scrapheap_purge_still_removes_in_root_files(tmp_path):
    image_root = str(tmp_path / "library")
    doomed = os.path.join(image_root, "doomed.png")
    _write_png(doomed)
    unconfirmed = remove_picture_files(image_root, [(1, "doomed.png", False)])
    assert not os.path.exists(doomed)
    assert unconfirmed == []


# ---------------------------------------------------------------------------
# Caption sidecar write-back
# ---------------------------------------------------------------------------


def test_writeback_refuses_image_path_outside_the_roots(tmp_path):
    image_root = str(tmp_path / "library")
    os.makedirs(image_root)
    outside_image = str(tmp_path / "loose" / "img.png")
    assert (
        writeback_path(
            outside_image, SIDECAR_TYPE_TAGS, "_tags.txt", None, image_root=image_root
        )
        is None
    )


def test_writeback_ignores_fabricated_existing_path(tmp_path):
    image_root = str(tmp_path / "library")
    os.makedirs(image_root)
    image = os.path.join(image_root, "img.png")
    # A tags_file column pointing anywhere else must not become a write target;
    # the suffix-derived sidecar path is used instead.
    assert writeback_path(
        image,
        SIDECAR_TYPE_TAGS,
        "_tags.txt",
        str(tmp_path / "authorized_keys"),
        image_root=image_root,
    ) == os.path.join(image_root, "img_tags.txt")


def test_writeback_still_works_for_reference_folder_pictures(tmp_path):
    image_root = str(tmp_path / "library")
    ref_root = str(tmp_path / "refs")
    os.makedirs(image_root)
    os.makedirs(ref_root)
    provider = lambda: [ref_root]  # noqa: E731
    register_reference_roots_provider(image_root, provider)
    try:
        image = os.path.join(ref_root, "img.png")
        assert writeback_path(
            image, SIDECAR_TYPE_TAGS, "_tags.txt", None, image_root=image_root
        ) == os.path.join(ref_root, "img_tags.txt")
        # A legitimately recorded stem+suffix existing path is honoured.
        assert writeback_path(
            image,
            SIDECAR_TYPE_TAGS,
            None,
            os.path.join(ref_root, "img.txt"),
            image_root=image_root,
        ) == os.path.join(ref_root, "img.txt")
    finally:
        unregister_reference_roots_provider(image_root, provider)
