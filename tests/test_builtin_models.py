"""PixlStash's own engines: declared, protected, and pinned to their downloaders.

The declaration is a duplicate of constants that live in modules too heavy to
import at start-up (onnxruntime, torch, cv2). A test is what keeps a duplicate
honest, so the first case here imports the real ones and asserts they agree.
"""

from __future__ import annotations

import os

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services.builtin_models import (
    BUILTIN_ENGINES,
    BUILTIN_OWNER,
    TOOLING_DIRS,
    declare_builtin_models,
    declared_paths,
    unclaimed_files,
)
from pixlstash.utils.adapter_header import FILE_ENGINE


@pytest.fixture
def server_hub(tmp_path):
    """A hub of its own, opened at the current schema and closed after.

    Module scope would be wrong here: three of these cases assert on the rows a
    declaration wrote, so they need a hub that holds nothing else.
    """
    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        yield hub
    finally:
        hub.close()


def test_the_declaration_matches_what_the_downloaders_actually_write():
    """The whole reason a duplicate is acceptable. These constants live beside
    imports too heavy for start-up, so they are restated in the declaration and
    pinned here, where the heavy import is free."""
    from pixlstash.tagger_plugins.pixlstash_tagger import (
        PIXLSTASH_TAGGER_FILENAME,
        PIXLSTASH_TAGGER_META_FILENAME,
    )
    from pixlstash.tagger_plugins.wd14 import WD14_CSV_FILE

    declared = declared_paths()
    assert PIXLSTASH_TAGGER_FILENAME in declared
    assert PIXLSTASH_TAGGER_META_FILENAME in declared
    assert any(path.endswith(WD14_CSV_FILE) for path in declared)


def test_every_engine_names_a_role_the_shelf_can_show():
    """`file_kind` stays four values wide; the role rides in `kind`, which
    already holds free text and already renders as the row's label."""
    for engine in BUILTIN_ENGINES:
        assert engine.role in {"tagger", "captioner", "scorer", "face"}
        assert engine.display_name and engine.relpath


def test_an_undeclared_file_is_reported_and_a_declared_one_is_not(tmp_path):
    """The readout that found a 339 MB leftover on a real machine."""
    (tmp_path / "pixlstash-anomaly-tagger.safetensors").write_bytes(b"x" * 10)
    (tmp_path / "pixlstash-anomaly-tagger.revision").write_text("abc")
    (tmp_path / "best.pt").write_bytes(b"y" * 20)

    found = unclaimed_files(str(tmp_path))
    assert [item["relpath"] for item in found] == ["best.pt"]
    assert found[0]["size"] == 20


def test_the_download_tools_own_bookkeeping_is_not_unclaimed(tmp_path):
    """`hf_hub_download(local_dir=...)` leaves `.cache/huggingface` beside what
    it writes, at the top level and inside every subdirectory. It is neither
    ours nor the owner's, and reporting it would train the reader to ignore the
    list."""
    cache = tmp_path / TOOLING_DIRS[0] / "huggingface"
    cache.mkdir(parents=True)
    (cache / "CACHEDIR.TAG").write_text("Signature")
    nested = tmp_path / "SmilingWolf_wd-convnext-tagger-v3" / TOOLING_DIRS[0]
    nested.mkdir(parents=True)
    (nested / "download.metadata").write_text("{}")

    assert unclaimed_files(str(tmp_path)) == []


def test_a_folder_that_has_never_been_downloaded_into_reports_nothing(tmp_path):
    """The normal state before the first run, and not an error."""
    assert unclaimed_files(str(tmp_path / "never-created")) == []


def test_declaring_writes_a_row_per_engine_and_states_which_are_present(
    server_hub, tmp_path
):
    """No parsing and no hashing: an engine that is on disk is `present`, one
    that has not been fetched yet is `missing` — which is the normal state for
    about half of them, since the ViT-L/14 scorer arrives only with the CLIP
    model that needs it."""
    (tmp_path / "pixlstash-anomaly-tagger.safetensors").write_bytes(b"x" * 32)

    folder_id = declare_builtin_models(server_hub, str(tmp_path))
    assert folder_id is not None

    folder = server_hub.fetchone(
        "SELECT owner, movable FROM model_folder WHERE id = ?", (folder_id,)
    )
    assert folder["owner"] == BUILTIN_OWNER
    assert folder["movable"] == "root_only"

    rows = {
        row["display_name"]: row
        for row in server_hub.fetchall(
            "SELECT m.display_name, m.file_kind, m.kind, m.file_size, mf.state "
            "FROM model m JOIN model_file mf ON mf.model_id = m.id "
            "WHERE mf.model_folder_id = ?",
            (folder_id,),
        )
    }
    assert len(rows) == len(BUILTIN_ENGINES)
    tagger = rows["PixlStash anomaly tagger"]
    assert (tagger["file_kind"], tagger["kind"], tagger["state"]) == (
        FILE_ENGINE,
        "tagger",
        "present",
    )
    assert tagger["file_size"] == 32
    assert rows["Aesthetic scorer (ViT-L/14)"]["state"] == "missing"


def test_declaring_twice_does_not_duplicate_a_row(server_hub, tmp_path):
    """It runs on every start, so it has to be idempotent."""
    (tmp_path / "sa_0_4_vit_b_32_linear.pth").write_bytes(b"z" * 8)
    folder_id = declare_builtin_models(server_hub, str(tmp_path))
    declare_builtin_models(server_hub, str(tmp_path))

    count = server_hub.fetchone(
        "SELECT COUNT(*) AS n FROM model_file WHERE model_folder_id = ?", (folder_id,)
    )
    assert count["n"] == len(BUILTIN_ENGINES)


def test_a_file_that_disappears_flips_its_row_to_missing(server_hub, tmp_path):
    """Declared, not scanned — but the state still has to tell the truth."""
    weights = tmp_path / "sa_0_4_vit_b_32_linear.pth"
    weights.write_bytes(b"z" * 8)
    folder_id = declare_builtin_models(server_hub, str(tmp_path))
    os.unlink(weights)
    declare_builtin_models(server_hub, str(tmp_path))

    state = server_hub.fetchone(
        "SELECT mf.state FROM model_file mf WHERE mf.model_folder_id = ? "
        "AND mf.relpath = ?",
        (folder_id, "sa_0_4_vit_b_32_linear.pth"),
    )
    assert state["state"] == "missing"
