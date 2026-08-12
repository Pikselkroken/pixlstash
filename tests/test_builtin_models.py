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
from pixlstash.services.builtin_caches import (
    declare_huggingface_cache,
    declare_insightface_packs,
)
from pixlstash.utils.adapter_header import FILE_ENGINE
from pixlstash.utils.insightface_model_utils import KNOWN_MODEL_PACKS


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


def test_claiming_a_path_the_owner_registered_resets_every_column(server_hub, tmp_path):
    """The upsert has to assert `movable` too, not just `kind` and `owner`.

    The managed store is relocatable as a whole and never per file, which is
    what `root_only` says. A path the owner had already registered as an
    ordinary `user` folder carries `per_item`, and an ON CONFLICT that updated
    only `kind` and `owner` would leave that standing — the built-in folder
    claimed for PixlStash while still advertising that its engines may be moved
    out one at a time. Reported by the review of #876.
    """
    with server_hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (path, kind, movable, created_at) "
            "VALUES (?, 'user', 'per_item', '2026-08-09T00:00:00Z')",
            (str(tmp_path),),
        )

    folder_id = declare_builtin_models(server_hub, str(tmp_path))

    row = server_hub.fetchone(
        "SELECT kind, owner, movable FROM model_folder WHERE id = ?", (folder_id,)
    )
    assert row["owner"] == "pixlstash"
    assert row["kind"] == "foreign"
    assert row["movable"] == "root_only"


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


def test_the_checkpoint_hash_worker_never_picks_up_an_engine(server_hub, tmp_path):
    """Engines carry no `sha256` by design — we know what they are without one —
    so they match the finder's plain `sha256 IS NULL` like any unhashed
    checkpoint. Without the exclusion this hands a 339 MB tagger and a pile of
    ONNX to the hash worker to read, and writes a digest onto a row that never
    wanted one."""
    from pixlstash.tasks.missing_checkpoint_hash_finder import (
        MissingCheckpointHashFinder,
    )

    for engine in BUILTIN_ENGINES:
        target = tmp_path / engine.relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x" * 8)
    declare_builtin_models(server_hub, str(tmp_path))

    finder = MissingCheckpointHashFinder(server_hub)
    total, pending = finder.progress()
    assert (total, pending) == (0, 0), (
        "declared engines were counted as checkpoints awaiting a hash"
    )
    assert finder.find_task() is None, "an engine was handed to the hash worker"


# --- The other two roots: InsightFace packs and the HuggingFace cache ---------
#
# Same writer, same folder protection, different way of learning what is there.
# They live in this file rather than one of their own because `server_hub` is
# exactly the environment they need, and a warm module beats a new one.


def test_insightface_declares_what_is_on_disk_and_what_we_know_about(
    server_hub, tmp_path
):
    """The union, not either half. Listing only the known packs would hide the
    `antelopev2` a real machine has; listing only what is on disk would drop a
    pack we provision that has not downloaded yet."""
    (tmp_path / "antelopev2").mkdir()
    (tmp_path / "antelopev2" / "det.onnx").write_bytes(b"x" * 64)

    folder_id = declare_insightface_packs(server_hub, str(tmp_path))
    assert folder_id is not None

    rows = {
        row["display_name"]: row
        for row in server_hub.fetchall(
            "SELECT m.display_name, m.kind, m.file_size, mf.state "
            "FROM model m JOIN model_file mf ON mf.model_id = m.id "
            "WHERE mf.model_folder_id = ?",
            (folder_id,),
        )
    }
    # On disk but not in KNOWN_MODEL_PACKS: still declared, still visible.
    assert rows["InsightFace antelopev2"]["state"] == "present"
    assert rows["InsightFace antelopev2"]["file_size"] == 64
    assert rows["InsightFace antelopev2"]["kind"] == "face"
    # Known but not downloaded: `missing` is a state, not a warning.
    for pack in KNOWN_MODEL_PACKS:
        assert rows[f"InsightFace {pack}"]["state"] == "missing"


def test_the_zip_insightface_downloaded_a_pack_from_is_not_a_pack(server_hub, tmp_path):
    """`buffalo_l.zip` sits beside `buffalo_l/` and is the tool's leftover. It
    gets no row, the same judgement `TOOLING_DIRS` makes about `.cache`."""
    (tmp_path / "buffalo_s").mkdir()
    (tmp_path / "buffalo_s.zip").write_bytes(b"pk" * 8)

    folder_id = declare_insightface_packs(server_hub, str(tmp_path))
    names = {
        row["display_name"]
        for row in server_hub.fetchall(
            "SELECT m.display_name FROM model m "
            "JOIN model_file mf ON mf.model_id = m.id "
            "WHERE mf.model_folder_id = ?",
            (folder_id,),
        )
    }
    assert "InsightFace buffalo_s" in names
    assert not any(name.endswith(".zip") for name in names)


def test_a_machine_that_has_never_run_face_detection_declares_nothing(
    server_hub, tmp_path
):
    """InsightFace creates the directory on its first download, so an absent one
    is a normal machine and must not raise on the start-up path."""
    assert declare_insightface_packs(server_hub, str(tmp_path / "nope")) is None


def test_the_huggingface_cache_is_declared_per_repo_not_per_file(
    server_hub, tmp_path, monkeypatch
):
    """The cache is content-addressed: a per-file listing shows the same weights
    once per revision. `repo_id` is the unit a person recognises and
    `size_on_disk` is the number they came for, both read from the cache's own
    index rather than by walking 116 GB."""

    class _Repo:
        def __init__(self, repo_id, repo_type, path, size):
            self.repo_id = repo_id
            self.repo_type = repo_type
            self.repo_path = path
            self.size_on_disk = size

    class _Info:
        repos = (
            _Repo(
                "Qwen/Qwen3-VL-4B-Instruct", "model", "models--Qwen--Qwen3-VL", 8_889
            ),
            _Repo(
                "laion/CLIP-ViT-H-14", "model", "models--laion--CLIP-ViT-H-14", 3_940
            ),
        )

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda _path: _Info())

    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))
    assert folder_id is not None

    rows = {
        row["display_name"]: row
        for row in server_hub.fetchall(
            "SELECT m.display_name, m.file_size, mf.relpath, mf.state "
            "FROM model m JOIN model_file mf ON mf.model_id = m.id "
            "WHERE mf.model_folder_id = ?",
            (folder_id,),
        )
    }
    assert set(rows) == {"Qwen/Qwen3-VL-4B-Instruct", "laion/CLIP-ViT-H-14"}
    assert rows["Qwen/Qwen3-VL-4B-Instruct"]["file_size"] == 8_889
    # Identity inside the folder is the repo's directory, not the display name.
    assert rows["Qwen/Qwen3-VL-4B-Instruct"]["relpath"] == "models--Qwen--Qwen3-VL"


def test_an_unreadable_huggingface_cache_does_not_fail_start_up(
    server_hub, tmp_path, monkeypatch
):
    """`CacheNotFound` on a machine that has downloaded nothing is the usual
    case, and start-up must survive it."""
    import huggingface_hub

    def _boom(_path):
        raise OSError("no cache here")

    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", _boom)
    assert declare_huggingface_cache(server_hub, str(tmp_path)) is None


def test_both_extra_roots_are_owned_so_the_scanner_skips_them(server_hub, tmp_path):
    """`owner` is the marker the folder scanner reads. Without it the walk would
    read 116 GB of HuggingFace blobs and sweep every ONNX pack to `missing`."""
    (tmp_path / "buffalo_l").mkdir()
    folder_id = declare_insightface_packs(server_hub, str(tmp_path))

    folder = server_hub.fetchone(
        "SELECT owner, kind, movable FROM model_folder WHERE id = ?", (folder_id,)
    )
    assert folder["owner"] == BUILTIN_OWNER
    assert folder["kind"] == "foreign"
    assert folder["movable"] == "root_only"
