"""PixlStash's own engines: declared, protected, and pinned to their downloaders.

The declaration is a duplicate of constants that live in modules too heavy to
import at start-up (onnxruntime, torch, cv2). A test is what keeps a duplicate
honest, so the first case here imports the real ones and asserts they agree.
"""

from __future__ import annotations

import logging
import os

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services.builtin_models import (
    BUILTIN_DIRNAME,
    BUILTIN_ENGINES,
    BUILTIN_MODEL_DIR_POINTER,
    BUILTIN_OWNER,
    TOOLING_DIRS,
    builtin_model_dir,
    declare_builtin_models,
    declared_paths,
    set_builtin_model_dir,
    unclaimed_files,
)
from pixlstash.services.builtin_caches import (
    declare_huggingface_cache,
    declare_insightface_packs,
)
from pixlstash.utils.adapter_header import FILE_CHECKPOINT, FILE_ENGINE, FILE_UNKNOWN
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


def test_only_weights_are_reported_as_unclaimed(tmp_path):
    """Every hit becomes a shelf row, so the readout is weights or nothing.

    The folder collects a trainer's `results.csv` and the odd README beside the
    files that matter, and a shelf listing those is one nobody reads carefully
    enough to notice the 339 MB `.pt` among them.
    """
    (tmp_path / "best.pt").write_bytes(b"y" * 20)
    (tmp_path / "results.csv").write_text("epoch,loss\n")
    (tmp_path / "README.md").write_text("# notes")
    (tmp_path / "hub.db").write_bytes(b"SQLite")

    assert [item["relpath"] for item in unclaimed_files(str(tmp_path))] == ["best.pt"]


def test_a_folder_that_has_never_been_downloaded_into_reports_nothing(tmp_path):
    """The normal state before the first run, and not an error."""
    assert unclaimed_files(str(tmp_path / "never-created")) == []


def test_declaring_writes_a_row_per_engine_and_states_which_are_present(
    server_hub, tmp_path
):
    """No parsing and no hashing: an engine that is on disk is `present`, one
    that has not been fetched yet is `not_downloaded` - which is the normal state
    for about half of them, since the ViT-L/14 scorer arrives only with the CLIP
    model that needs it, and NEVER `missing`, which the shelf draws as a fault
    (#926)."""
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
    assert rows["Aesthetic scorer (ViT-L/14)"]["state"] == "not_downloaded"


def test_declaring_twice_does_not_duplicate_a_row(server_hub, tmp_path):
    """It runs on every start, so it has to be idempotent."""
    (tmp_path / "sa_0_4_vit_b_32_linear.pth").write_bytes(b"z" * 8)
    folder_id = declare_builtin_models(server_hub, str(tmp_path))
    declare_builtin_models(server_hub, str(tmp_path))

    count = server_hub.fetchone(
        "SELECT COUNT(*) AS n FROM model_file WHERE model_folder_id = ?", (folder_id,)
    )
    assert count["n"] == len(BUILTIN_ENGINES)


def test_an_unclaimed_file_gets_a_row_the_owner_can_actually_reach(
    server_hub, tmp_path
):
    """#927: the readout knew about `best.pt` and nothing called it.

    Two halves, and the second is the one that makes the first useful. It has to
    appear at all - a file that is on the shelf's own folder and not on the
    shelf is one the owner cannot act on - and it has to appear as `unknown`
    rather than `engine`, because every shelf verb refuses an engine row. A
    leftover declared as ours would be visible and still untouchable.
    """
    (tmp_path / "best.pt").write_bytes(b"y" * 20)

    folder_id = declare_builtin_models(server_hub, str(tmp_path))

    row = server_hub.fetchone(
        "SELECT m.file_kind, m.kind, m.display_name, m.filename, m.file_size, "
        "mf.state FROM model m JOIN model_file mf ON mf.model_id = m.id "
        "WHERE mf.model_folder_id = ? AND mf.relpath = ?",
        (folder_id, "best.pt"),
    )
    assert row is not None, "the leftover got no row, which is the bug"
    assert row["file_kind"] == FILE_UNKNOWN
    assert row["state"] == "present"
    assert row["file_size"] == 20
    assert row["filename"] == "best.pt"
    # No name and no role: we did not put it there and do not know what it is.
    # The shelf derives a name from the filename, which is honest about who
    # decided it.
    assert row["display_name"] is None
    assert row["kind"] is None

    # And it is not counted among PixlStash's own engines.
    engines = server_hub.fetchone(
        "SELECT COUNT(*) AS n FROM model WHERE file_kind = ?", (FILE_ENGINE,)
    )
    assert engines["n"] == len(BUILTIN_ENGINES)


def test_a_sizing_failure_leaves_the_size_a_previous_run_read(
    server_hub, tmp_path, monkeypatch
):
    """The size is COALESCE'd onto the row, so an unsizeable file must report
    `None` and not `0`.

    A zero would win the COALESCE and overwrite a figure a previous start read
    correctly, and the shelf would then say a 339 MB leftover takes no disk -
    from a `stat` that failed for a moment on the one folder the downloaders
    are actively writing into.
    """
    (tmp_path / "best.pt").write_bytes(b"y" * 20)
    declare_builtin_models(server_hub, str(tmp_path))

    real_getsize = os.path.getsize

    def failing_getsize(path):
        if os.path.basename(path) == "best.pt":
            raise OSError("file is being replaced")
        return real_getsize(path)

    monkeypatch.setattr(os.path, "getsize", failing_getsize)
    declare_builtin_models(server_hub, str(tmp_path))

    row = server_hub.fetchone("SELECT file_size FROM model WHERE filename = 'best.pt'")
    assert row["file_size"] == 20


def test_a_file_we_could_not_look_at_is_unreachable_not_undownloaded(
    server_hub, tmp_path, monkeypatch
):
    """ENOENT is the only absence that means "nobody fetched this yet".

    A permission denial or an IO error is us being unable to LOOK, and reporting
    that as `not_downloaded` would hide a real filesystem fault behind a
    download glyph - the same false reassurance #926 fixed, in the other
    direction.
    """
    real_stat = os.stat

    def refuse(path, *args, **kwargs):
        if str(path).endswith("sa_0_4_vit_b_32_linear.pth"):
            raise PermissionError(13, "Permission denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", refuse)
    folder_id = declare_builtin_models(server_hub, str(tmp_path))

    states = {
        row["relpath"]: row["state"]
        for row in server_hub.fetchall(
            "SELECT relpath, state FROM model_file WHERE model_folder_id = ?",
            (folder_id,),
        )
    }
    assert states["sa_0_4_vit_b_32_linear.pth"] == "unreachable"
    # Every other engine is absent for the ordinary reason and keeps the
    # ordinary word: one unreadable file does not repaint the folder.
    assert states["pixlstash-anomaly-tagger.safetensors"] == "not_downloaded"


def test_declaring_again_does_not_wipe_a_name_the_owner_gave_a_leftover(
    server_hub, tmp_path
):
    """The declaration is the authority for an engine, not for a leftover.

    An engine restates its name on every start because nobody may rename one.
    An unclaimed file declares no name at all, so restating it outright would
    reset whatever the owner typed on the one row class here they are allowed
    to curate - every server start, silently.
    """
    (tmp_path / "best.pt").write_bytes(b"y" * 20)
    declare_builtin_models(server_hub, str(tmp_path))
    with server_hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET display_name = 'YOLO detector', kind = 'detector' "
            "WHERE filename = 'best.pt'"
        )

    declare_builtin_models(server_hub, str(tmp_path))

    row = server_hub.fetchone(
        "SELECT display_name, kind FROM model WHERE filename = 'best.pt'"
    )
    assert (row["display_name"], row["kind"]) == ("YOLO detector", "detector")


def test_a_leftover_merged_into_a_real_model_does_not_drag_it_to_unknown(
    server_hub, tmp_path
):
    """The sharp edge of giving leftovers a row, and the reason `file_kind` is
    COALESCE'd rather than restated.

    A leftover enters with `sha256` NULL, so `CheckpointHashTask` picks it up,
    and if it hashes to a digest already registered the two `model` rows MERGE:
    this folder's `model_file` is repointed at the survivor, which is somebody's
    real adapter in their own folder. Restating `unknown` onto that row on the
    next start would drop the adapter out of `/adapters` everywhere, over a
    second copy the owner happened to leave in the download folder.

    The merge is simulated rather than driven through the hash task: what is
    under test is the declaration's behaviour once a location it wrote points at
    a row it did not.
    """
    (tmp_path / "stray.safetensors").write_bytes(b"y" * 20)
    folder_id = declare_builtin_models(server_hub, str(tmp_path))

    with server_hub.transaction() as conn:
        adapter_id = conn.execute(
            "INSERT INTO model (file_kind, kind, sha256, display_name, "
            "provenance, created_at) VALUES ('adapter', 'lora', ?, "
            "'Cyanwood Style', 'external', '2026-08-01T00:00:00Z')",
            ("a" * 64,),
        ).lastrowid
        # What `CheckpointHashTask._merge` does: the survivor keeps its identity
        # and takes the other row's locations.
        conn.execute(
            "UPDATE model_file SET model_id = ? "
            "WHERE model_folder_id = ? AND relpath = ?",
            (adapter_id, folder_id, "stray.safetensors"),
        )

    declare_builtin_models(server_hub, str(tmp_path))

    row = server_hub.fetchone(
        "SELECT file_kind, kind, display_name FROM model WHERE id = ?", (adapter_id,)
    )
    assert row["file_kind"] == "adapter", "the merged adapter was demoted to unknown"
    assert (row["kind"], row["display_name"]) == ("lora", "Cyanwood Style")


def test_a_leftover_that_is_deleted_outside_pixlstash_goes_missing(
    server_hub, tmp_path
):
    """Which is what then lets the owner forget the row: `POST /models/forget`
    refuses anything with a copy still `present`."""
    leftover = tmp_path / "best.pt"
    leftover.write_bytes(b"y" * 20)
    folder_id = declare_builtin_models(server_hub, str(tmp_path))
    os.unlink(leftover)

    declare_builtin_models(server_hub, str(tmp_path))

    state = server_hub.fetchone(
        "SELECT state FROM model_file WHERE model_folder_id = ? AND relpath = ?",
        (folder_id, "best.pt"),
    )
    assert state["state"] == "missing"


def test_claiming_a_path_the_owner_registered_resets_every_column(server_hub, tmp_path):
    """The upsert has to assert `movable` too, not just `kind` and `owner`.

    The managed store is relocatable as a whole and never per file, which is
    what `root_only` says. A path the owner had already registered as an
    ordinary `user` folder carries `per_item`, and an ON CONFLICT that updated
    only `kind` and `owner` would leave that standing - the built-in folder
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


def test_a_file_that_disappears_stops_claiming_to_be_present(server_hub, tmp_path):
    """Declared, not scanned - but the state still has to tell the truth.

    `not_downloaded` rather than `missing` even here: we re-fetch a declared
    engine the moment something needs it, so a deleted one is pending, not lost,
    and the owner has nothing to do about it either way.
    """
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
    assert state["state"] == "not_downloaded"


def test_the_checkpoint_hash_worker_never_picks_up_an_engine(server_hub, tmp_path):
    """Engines carry no `sha256` by design - we know what they are without one -
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
    # Known but not downloaded: a state, not a warning - and it has to be said
    # with a word the shelf does not draw as a fault (#926).
    for pack in KNOWN_MODEL_PACKS:
        assert rows[f"InsightFace {pack}"]["state"] == "not_downloaded"


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
    server_hub, tmp_path, caplog
):
    """InsightFace creates the directory on its first download, so an absent one
    is a normal machine and must not raise on the start-up path - nor warn."""
    with caplog.at_level("WARNING"):
        assert declare_insightface_packs(server_hub, str(tmp_path / "nope")) is None
    assert _warnings(caplog) == [], caplog.text


def test_a_relocated_pack_root_that_is_gone_is_not_called_normal(
    server_hub, tmp_path, data_dir, caplog
):
    """The same silence as the download folder, in the folder beside it.

    "Normal on a machine that has not run face detection" is exactly wrong for a
    root a relocation recorded: that machine *has* run face detection, which is
    why it has a record, and what the absence means is that the packs will be
    fetched again into a directory PixlStash re-creates.
    """
    from pixlstash.services.builtin_caches import insightface_models_dir_under
    from pixlstash.utils import insightface_model_utils as model_utils

    gone = str(tmp_path / "unplugged" / ".insightface")
    model_utils.set_insightface_root(gone)

    with caplog.at_level("WARNING"):
        assert (
            declare_insightface_packs(server_hub, insightface_models_dir_under(gone))
            is None
        )
    assert gone in caplog.text, caplog.text
    assert "downloaded again" in caplog.text, caplog.text


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


def test_a_repo_deleted_from_the_cache_stops_claiming_its_bytes(
    server_hub, tmp_path, monkeypatch
):
    """The sweep these folders have nowhere else to get.

    The scanner marks what it did not see `missing` on every walk, and it skips
    these folders because they carry an `owner`. So a repo that leaves the
    HuggingFace index - `huggingface-cli delete-cache` - would otherwise keep a
    `present` row claiming 32 GB that is not on the disk, which is exactly the
    number `present_bytes` reports on the folder list.
    """
    import huggingface_hub

    class _Repo:
        def __init__(self, repo_id, path, size):
            self.repo_id = repo_id
            self.repo_type = "model"
            self.repo_path = path
            self.size_on_disk = size

    def _cache(repos):
        return type("_Info", (), {"repos": repos})()

    both = [
        _Repo("org/keep", "models--org--keep", 100),
        _Repo("org/drop", "models--org--drop", 32_000),
    ]
    # `monkeypatch`, not assignment: a bare write here outlives the test and
    # every later one in the shard would get this stub instead of the library.
    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda _p: _cache(both))
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))

    def _state(name):
        row = server_hub.fetchone(
            "SELECT mf.state FROM model_file mf JOIN model m ON m.id = mf.model_id "
            "WHERE mf.model_folder_id = ? AND m.display_name = ?",
            (folder_id, name),
        )
        return None if row is None else row["state"]

    assert _state("org/drop") == "present"

    # The owner deletes one from the cache; the next declaration must notice.
    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda _p: _cache(both[:1]))
    declare_huggingface_cache(server_hub, str(tmp_path))

    assert _state("org/drop") == "missing", (
        "a repo that left the cache still claims its bytes are on the disk"
    )
    # The positive control: the survivor is untouched, so the sweep is not just
    # marking everything missing.
    assert _state("org/keep") == "present"


def test_the_sweep_does_not_touch_a_declared_engine_that_is_simply_absent(
    server_hub, tmp_path
):
    """A declared engine's state is decided by the existence check, not by
    the sweep: the built-in entry set is fixed and always names every row, so a
    re-declaration must leave a present engine present."""
    (tmp_path / "sa_0_4_vit_b_32_linear.pth").write_bytes(b"z" * 8)
    folder_id = declare_builtin_models(server_hub, str(tmp_path))
    declare_builtin_models(server_hub, str(tmp_path))

    row = server_hub.fetchone(
        "SELECT state FROM model_file WHERE model_folder_id = ? AND relpath = ?",
        (folder_id, "sa_0_4_vit_b_32_linear.pth"),
    )
    assert row["state"] == "present"


def test_the_huggingface_cache_is_fixed_not_merely_root_only(
    server_hub, tmp_path, monkeypatch
):
    """`root_only` says a folder relocates as a whole. That is true of our own
    downloads and of the InsightFace packs; it is false of the HuggingFace cache,
    whose location is `HF_HOME` read at import by a library shared with every
    other tool on the machine. "Moving" it is a restart and a re-download, so the
    column says `fixed` and the UI can offer an explanation instead of a verb."""
    import huggingface_hub

    class _Repo:
        repo_id = "org/thing"
        repo_type = "model"
        repo_path = "models--org--thing"
        size_on_disk = 10
        revisions = frozenset()

    monkeypatch.setattr(
        huggingface_hub,
        "scan_cache_dir",
        lambda _p: type("_I", (), {"repos": (_Repo(),)})(),
    )
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))
    row = server_hub.fetchone(
        "SELECT movable FROM model_folder WHERE id = ?", (folder_id,)
    )
    assert row["movable"] == "fixed"


def test_a_fixed_folder_refuses_a_per_item_move_the_same_as_root_only(tmp_path):
    """The pair is the point: both values forbid a per-item move out, so keying
    the guard on one of them would leave the other open."""
    from pixlstash.hub.db import HubDatabase
    from pixlstash.services.model_mover import ModelMover, MoveRefused

    source = tmp_path / "cache"
    source.mkdir()
    (source / "models--org--thing").mkdir()
    destination = tmp_path / "loras"
    destination.mkdir()

    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model_folder (id, path, kind, owner, movable, "
                "created_at) VALUES (1, ?, 'foreign', 'pixlstash', 'fixed', "
                "'2026-08-12T00:00:00Z')",
                (str(source),),
            )
            conn.execute(
                "INSERT INTO model_folder (id, path, kind, movable, created_at) "
                "VALUES (2, ?, 'user', 'per_item', '2026-08-12T00:00:00Z')",
                (str(destination),),
            )
            cursor = conn.execute(
                "INSERT INTO model (file_kind, kind, display_name, filename, "
                "provenance, file_size, created_at) VALUES ('engine', 'other', "
                "'org/thing', 'models--org--thing', 'builtin', 10, "
                "'2026-08-12T00:00:00Z')"
            )
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                "state, seen_at) VALUES (?, 1, 'models--org--thing', 'present', "
                "'2026-08-12T00:00:00Z')",
                (int(cursor.lastrowid),),
            )
        with pytest.raises(MoveRefused):
            ModelMover(hub).plan([(1, "models--org--thing")], 2)
    finally:
        hub.close()


# --- Which feature a cached repo powers ---------------------------------------


def _repo(repo_id, snapshot=None):
    """A stand-in for `scan_cache_dir()`'s CachedRepoInfo."""

    class _Rev:
        snapshot_path = snapshot or "/nonexistent"

    class _Repo:
        pass

    r = _Repo()
    r.repo_id = repo_id
    r.repo_type = "model"
    # A frozenset, like the real one - the ordering trap this code had to fix.
    r.revisions = frozenset({_Rev()}) if snapshot else frozenset()
    return r


def test_our_own_downloaders_repos_are_facts_not_guesses():
    """Restated here for the same reason `builtin_models` restates filenames, so
    the duplicate is pinned against the modules that own the real constants -
    imported in the test, where the torch/onnxruntime cost is free."""
    from pixlstash.services.model_features import OUR_REPOS, feature_for_repo
    from pixlstash.tagger_plugins.wd14 import WD14_HF_REPO
    from pixlstash.tagger_plugins.pixlstash_tagger import PIXLSTASH_TAGGER_HF_REPO

    assert WD14_HF_REPO in OUR_REPOS
    assert PIXLSTASH_TAGGER_HF_REPO in OUR_REPOS
    assert feature_for_repo(_repo(WD14_HF_REPO)) == "tagger"


def test_a_base_model_in_the_shipped_table_needs_no_guess():
    """43 curated entries already map a repo id to a base model, so the shelf
    does not get to have a second opinion about it."""
    from pixlstash.services.model_features import feature_for_repo

    assert feature_for_repo(_repo("Tongyi-MAI/Z-Image-Turbo")) == "checkpoint"


def test_a_text_encoder_is_not_labelled_a_captioner(tmp_path):
    """The trap that made this measurable rather than assumed.

    `T5ForConditionalGeneration` shares its suffix with every vision-language
    captioner, so matching the suffix alone put "Captioning" on
    `google/flan-t5-base` - a text encoder that captions nothing - in the column
    a reader uses to decide what is safe to delete. A vision tower is required.
    """
    from pixlstash.services.model_features import feature_for_repo

    snap = tmp_path / "t5"
    snap.mkdir()
    (snap / "config.json").write_text(
        '{"architectures": ["T5ForConditionalGeneration"], "model_type": "t5"}'
    )
    assert feature_for_repo(_repo("google/flan-t5-base", str(snap))) == "other"

    # The positive control: the same suffix WITH a vision tower is the real
    # thing, so the guard must not have closed the door on captioners.
    vlm = tmp_path / "vlm"
    vlm.mkdir()
    (vlm / "config.json").write_text(
        '{"architectures": ["Qwen2_5_VLForConditionalGeneration"], '
        '"vision_config": {"depth": 32}}'
    )
    assert feature_for_repo(_repo("Qwen/Qwen2.5-VL-7B", str(vlm))) == "captioner"


def test_a_repo_with_nothing_to_go_on_says_other_rather_than_guessing(tmp_path):
    """`other` is a real state. A VAE and a bare weight file are components of
    somebody else's pipeline, and forcing them into a feature label would be a
    confident wrong answer where an honest blank costs nothing."""
    from pixlstash.services.model_features import feature_for_repo

    snap = tmp_path / "vae"
    snap.mkdir()
    (snap / "raw.safetensors").write_bytes(b"\x00")
    assert feature_for_repo(_repo("ai-toolkit/flux2_vae", str(snap))) == "other"


def test_every_readable_revision_is_consulted_not_one_at_random(tmp_path):
    """`repo.revisions` is a frozenset, so "the first snapshot" was whatever the
    set iterated to that run. A repo holding a complete revision beside a
    half-downloaded one classified differently on different runs off the same
    disk."""
    from pixlstash.services.model_features import feature_for_repo

    empty = tmp_path / "aaa-partial"
    empty.mkdir()
    (empty / "tokenizer.json").write_text("{}")
    full = tmp_path / "bbb-complete"
    full.mkdir()
    (full / "config.json").write_text(
        '{"architectures": ["BlipForConditionalGeneration"], "model_type": "blip"}'
    )

    class _Rev:
        def __init__(self, p):
            self.snapshot_path = p

    repo = _repo("Salesforce/blip-image-captioning-base")
    repo.revisions = frozenset({_Rev(str(empty)), _Rev(str(full))})
    # Deterministic whichever way the set iterates.
    assert feature_for_repo(repo) == "captioner"


# --- Everything a model can do, not just the first thing --------------------


def test_a_model_that_serves_two_features_declares_both():
    """The rule this table exists for: a multi-capability model genuinely cannot
    be filed under one heading, so it says both and the shelf lists it twice.

    Florence-2 is the worked example - ONE setting and one set of weights drive
    `FlorenceService.get_captions` and `.detect_objects`, the latter being what
    `DetectionTask` runs. A single label answers "what breaks if I delete this"
    wrongly for exactly the rows a reader is deciding about.
    """
    from pixlstash.services.model_features import (
        feature_for_repo,
        features_for_repo,
    )
    from pixlstash.tagger_plugins.florence2 import FLORENCE_MODEL_VARIANTS

    for variant in FLORENCE_MODEL_VARIANTS.values():
        repo = _repo(variant["model"])
        assert features_for_repo(repo) == ("captioner", "detector")
        # Primary first, and `model.kind` still holds exactly that one word.
        assert feature_for_repo(repo) == "captioner"


def test_the_clip_the_embedder_loads_is_both_encoder_and_scorer_backbone():
    """`ImageEmbeddingTask` runs ONE forward pass through these weights and uses
    the result twice: as the search embedding and as the aesthetic predictor's
    input. Deleting the repo stops search AND quality scores.

    The repo id is pinned against the two constants that choose the model, so a
    switch to another CLIP cannot leave this entry quietly naming the old one.
    `open_clip` itself is not imported: it pulls torch, and the pin is a string
    fact rather than a resolution.
    """
    from pixlstash.services.model_features import OUR_REPOS, features_for_repo
    from pixlstash.tagger_plugins.clip_service import (
        CLIP_MODEL_NAME,
        CLIP_MODEL_WEIGHTS,
    )

    named = [
        repo_id
        for repo_id, caps in OUR_REPOS.items()
        if "scorer" in caps and "search" in caps
    ]
    assert len(named) == 1, "exactly one cached repo is the embedder's CLIP"
    repo_id = named[0].lower().replace("_", "-")
    assert CLIP_MODEL_NAME.lower() in repo_id
    assert CLIP_MODEL_WEIGHTS.lower().replace("_", "-") in repo_id
    assert features_for_repo(_repo(named[0])) == ("search", "scorer")


def test_a_model_that_does_one_thing_says_it_once(tmp_path):
    """The common case stays a one-element tuple rather than growing a list of
    near-synonyms. A single label is right for most rows and honest for the
    rest, which is why `other` is still reachable."""
    from pixlstash.services.model_features import features_for_repo
    from pixlstash.tagger_plugins.wd14 import WD14_HF_REPO

    assert features_for_repo(_repo(WD14_HF_REPO)) == ("tagger",)

    vae = tmp_path / "vae"
    vae.mkdir()
    (vae / "raw.safetensors").write_bytes(b"\x00")
    assert features_for_repo(_repo("ai-toolkit/flux2_vae", str(vae))) == ("other",)


def _capabilities_by_name(hub, folder_id):
    """`display_name -> [capability, …]` for one declared folder."""
    grouped: dict[str, list[str]] = {}
    for row in hub.fetchall(
        "SELECT m.display_name, c.capability FROM model m "
        "JOIN model_file mf ON mf.model_id = m.id "
        "JOIN model_capability c ON c.model_id = m.id "
        "WHERE mf.model_folder_id = ? ORDER BY c.rowid",
        (folder_id,),
    ):
        grouped.setdefault(row["display_name"], []).append(row["capability"])
    return grouped


def _hf_cache(monkeypatch, repos):
    """Point `scan_cache_dir` at a fake cache holding *repos*."""

    class _Repo:
        def __init__(self, repo_id):
            self.repo_id = repo_id
            self.repo_type = "model"
            self.repo_path = "models--" + repo_id.replace("/", "--")
            self.size_on_disk = 4_096
            self.revisions = frozenset()

    class _Info:
        pass

    info = _Info()
    info.repos = tuple(_Repo(repo_id) for repo_id in repos)

    import huggingface_hub

    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda _path: info)


def test_declaring_a_cache_writes_the_whole_capability_set(
    server_hub, tmp_path, monkeypatch
):
    """The join table is what the shelf reads to list a model under each feature
    it serves, so the declaration has to fill it - and `model.kind` keeps the
    primary label so the Kind column and the curation verbs are unchanged."""
    _hf_cache(
        monkeypatch,
        ("florence-community/Florence-2-base", "SmilingWolf/wd-convnext-tagger-v3"),
    )
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))
    assert folder_id is not None

    assert _capabilities_by_name(server_hub, folder_id) == {
        "florence-community/Florence-2-base": ["captioner", "detector"],
        "SmilingWolf/wd-convnext-tagger-v3": ["tagger"],
    }
    kinds = {
        row["display_name"]: row["kind"]
        for row in server_hub.fetchall(
            "SELECT m.display_name, m.kind FROM model m "
            "JOIN model_file mf ON mf.model_id = m.id "
            "WHERE mf.model_folder_id = ?",
            (folder_id,),
        )
    }
    assert kinds["florence-community/Florence-2-base"] == "captioner"


def test_a_capability_the_declaration_drops_stops_being_listed(
    server_hub, tmp_path, monkeypatch
):
    """The declaration is the authority, so the set is restated wholesale rather
    than merged. Without that, a model that stopped serving a feature would
    still be listed under it forever - and re-declaring is what every start-up
    does, so the leak would be permanent rather than rare."""
    from pixlstash.services import builtin_caches

    _hf_cache(monkeypatch, ("florence-community/Florence-2-base",))
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))
    assert _capabilities_by_name(server_hub, folder_id) == {
        "florence-community/Florence-2-base": ["captioner", "detector"]
    }

    # The same repo, now classified as serving one feature.
    monkeypatch.setattr(
        builtin_caches, "features_for_repo", lambda _repo: ("captioner",)
    )
    assert declare_huggingface_cache(server_hub, str(tmp_path)) == folder_id
    assert _capabilities_by_name(server_hub, folder_id) == {
        "florence-community/Florence-2-base": ["captioner"]
    }


def _file_kinds_by_name(hub, folder_id):
    """`display_name -> file_kind` for one declared folder."""
    return {
        row["display_name"]: row["file_kind"]
        for row in hub.fetchall(
            "SELECT m.display_name, m.file_kind FROM model m "
            "JOIN model_file mf ON mf.model_id = m.id "
            "WHERE mf.model_folder_id = ?",
            (folder_id,),
        )
    }


def test_only_the_repos_pixlstash_fetches_are_declared_as_engines(
    server_hub, tmp_path, monkeypatch
):
    """The HuggingFace cache is shared with every other tool on the machine, and
    `engine` is the claim "PixlStash downloaded this for itself" - the claim
    every shelf verb refuses on. Stamped over the whole cache it locked the owner
    out of their own models: correcting the Kind of a checkpoint they downloaded
    came back "1 of these are engines PixlStash downloaded for itself", about a
    file PixlStash has never loaded."""
    _hf_cache(
        monkeypatch,
        (
            "florence-community/Florence-2-base",  # ours
            "Qwen/Qwen-Image",  # theirs, and a known base model
            "krea/Krea-2-Raw",  # theirs, and nothing recognises it
        ),
    )
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))

    assert _file_kinds_by_name(server_hub, folder_id) == {
        "florence-community/Florence-2-base": FILE_ENGINE,
        # Classified off the repo, which for a known base model is a real
        # answer...
        "Qwen/Qwen-Image": FILE_CHECKPOINT,
        # ...and otherwise is the same word the unclaimed leftovers carry.
        "krea/Krea-2-Raw": FILE_UNKNOWN,
    }


def test_a_found_repo_keeps_the_correction_its_owner_made(
    server_hub, tmp_path, monkeypatch
):
    """The half that makes the curation stick. Every start-up re-declares the
    cache, so a declaration that restated its own guess would revert the edit
    between one launch and the next - the owner would see it land and then find
    it undone, which is worse than the refusal it replaced."""
    from pixlstash.services.model_shelf_service import update_models

    _hf_cache(monkeypatch, ("krea/Krea-2-Raw",))
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))
    model_id = server_hub.fetchone(
        "SELECT model_id FROM model_file WHERE model_folder_id = ?", (folder_id,)
    )["model_id"]

    update_models(
        server_hub,
        [model_id],
        {"file_kind": "checkpoint", "kind": None, "display_name": "Krea 2 Raw"},
    )
    assert declare_huggingface_cache(server_hub, str(tmp_path)) == folder_id

    row = server_hub.fetchone(
        "SELECT file_kind, kind, display_name FROM model WHERE id = ?", (model_id,)
    )
    assert row["file_kind"] == "checkpoint"
    assert row["kind"] is None
    assert row["display_name"] == "Krea 2 Raw"
    # The capability was our classification of the file, and the owner has just
    # overruled it. Restating it would leave the shelf's Feature axis filing the
    # row under the guess while its Kind column read the correction.
    assert _capabilities_by_name(server_hub, folder_id) == {}


def test_a_repo_we_already_mislabelled_as_ours_is_handed_back(
    server_hub, tmp_path, monkeypatch
):
    """Every existing install has these rows stored as `engine` already, and the
    rule above deliberately stops restating a found repo's file kind - so
    without this the fix would only ever reach a cache declared for the first
    time, and the owner who reported it would still be locked out. Safe to do
    silently because `engine` is not a value any verb can set."""
    _hf_cache(monkeypatch, ("krea/Krea-2-Raw",))
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))
    model_id = server_hub.fetchone(
        "SELECT model_id FROM model_file WHERE model_folder_id = ?", (folder_id,)
    )["model_id"]
    # The row as the previous release wrote it.
    with server_hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET file_kind = ? WHERE id = ?", (FILE_ENGINE, model_id)
        )

    assert declare_huggingface_cache(server_hub, str(tmp_path)) == folder_id

    assert _file_kinds_by_name(server_hub, folder_id) == {
        "krea/Krea-2-Raw": FILE_UNKNOWN
    }


def test_our_own_engines_are_still_the_declaration_to_state(
    server_hub, tmp_path, monkeypatch
):
    """The other side of the same rule, and the reason it is keyed on OUR_REPOS
    rather than dropped. PixlStash's own captioner is ours: the row says what we
    say, and a rename would make the shelf lie about a file we load by path."""
    _hf_cache(monkeypatch, ("florence-community/Florence-2-base",))
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))
    model_id = server_hub.fetchone(
        "SELECT model_id FROM model_file WHERE model_folder_id = ?", (folder_id,)
    )["model_id"]

    # No verb offers this - `_refuse_builtin_engines` still refuses every one of
    # them - so it is written underneath to pin what a re-declaration does.
    with server_hub.transaction() as conn:
        conn.execute(
            "UPDATE model SET display_name = 'Mine now', kind = 'lora' WHERE id = ?",
            (model_id,),
        )
    assert declare_huggingface_cache(server_hub, str(tmp_path)) == folder_id

    row = server_hub.fetchone(
        "SELECT file_kind, kind, display_name FROM model WHERE id = ?", (model_id,)
    )
    assert row["file_kind"] == FILE_ENGINE
    assert row["kind"] == "captioner"
    assert row["display_name"] == "florence-community/Florence-2-base"


def test_forgetting_a_model_takes_its_capabilities_with_it(server_hub, tmp_path):
    """Foreign keys are on for the hub, so `model_capability` is not a row that
    leaks quietly if it is forgotten - it ABORTS the delete. Both directions
    matter: the delete must succeed, and nothing must be left behind."""
    from pixlstash.services.model_shelf_service import forget_models

    with server_hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (id, path, kind, movable, created_at) "
            "VALUES (7, '/models/x', 'user', 'per_item', '2026-08-13T00:00:00Z')"
        )
        cursor = conn.execute(
            "INSERT INTO model (file_kind, kind, sha256, filename, provenance) "
            "VALUES ('adapter', 'lora', 'a' * 64, 'x.safetensors', 'external')"
        )
        model_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO model_capability (model_id, capability) VALUES (?, 'search')",
            (model_id,),
        )

    forgotten, refused = forget_models(server_hub, [model_id])
    assert forgotten == [model_id], refused
    assert not server_hub.fetchall(
        "SELECT 1 FROM model_capability WHERE model_id = ?", (model_id,)
    )


def test_re_declaring_an_unchanged_set_writes_nothing(
    server_hub, tmp_path, monkeypatch
):
    """Every root here is re-declared on every server start, and the set changes
    about never - so an unchanged declaration must not rewrite the rows.

    This is not only churn. Rewriting ~35 entries' capabilities on every Server
    a process builds was enough extra hub write traffic to take the Windows CI
    shard down with a SIGSEGV during interpreter teardown, seconds after a fully
    green test run (PR #922). Asserted on `rowid`, which is what a
    delete-and-reinsert changes and a no-op leaves alone.
    """
    _hf_cache(
        monkeypatch,
        ("florence-community/Florence-2-base", "SmilingWolf/wd-convnext-tagger-v3"),
    )
    folder_id = declare_huggingface_cache(server_hub, str(tmp_path))
    assert folder_id is not None

    def rowids():
        return [
            (row["rowid"], row["capability"])
            for row in server_hub.fetchall(
                "SELECT rowid, capability FROM model_capability ORDER BY rowid"
            )
        ]

    before = rowids()
    assert [cap for _, cap in before] == ["captioner", "detector", "tagger"]

    assert declare_huggingface_cache(server_hub, str(tmp_path)) == folder_id
    assert rowids() == before, "an unchanged declaration rewrote the rows"


# ===========================================================================
# Where the folder is: one accessor, and a location that can be recorded (#905)
# ===========================================================================


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Stand in for the platform user data directory."""
    from pixlstash.services import builtin_models

    root = tmp_path / "userdata"
    root.mkdir()
    monkeypatch.setattr(builtin_models, "_pixlstash_data_dir", lambda: str(root))
    monkeypatch.delenv(builtin_models.BUILTIN_MODEL_DIR_ENV, raising=False)
    return root


def test_the_folder_defaults_to_where_it_has_always_been(data_dir):
    """A machine that never relocated it must not see the folder move."""
    assert builtin_model_dir() == os.path.join(str(data_dir), BUILTIN_DIRNAME)


def test_a_recorded_location_survives_the_process_that_wrote_it(data_dir):
    """The relocation is only real if the next start agrees with it - otherwise
    every engine is downloaded again into the folder that was just emptied."""
    set_builtin_model_dir("/mnt/big/models")
    assert builtin_model_dir() == "/mnt/big/models"


def test_an_unreadable_record_names_the_default_rather_than_nothing(
    data_dir, monkeypatch, caplog
):
    """Refusing to name a folder at all would disable every engine over one bad
    file, so the failure is loud and the default answers."""
    set_builtin_model_dir("/mnt/big/models")

    def _boom(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("builtins.open", _boom)
    with caplog.at_level("ERROR"):
        assert builtin_model_dir() == os.path.join(str(data_dir), BUILTIN_DIRNAME)
    assert "permission denied" in caplog.text


def _warnings(caplog) -> list[str]:
    """Every WARNING-or-worse message captured, and nothing else.

    ``caplog.text`` is whatever the capture handler saw, which includes INFO
    when the runner asks for it - the gate passes ``--log-level=INFO``. So
    ``caplog.text == ""`` is not "this said nothing worrying", it is "this said
    nothing at all, at whatever level today's command line happens to capture",
    and an ordinary INFO such as ``set_builtin_model_dir``'s own "now downloads
    its engines to …" turns a correct silence into a failure. The assertions
    below mean the narrow thing: no warning.
    """
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ]


def test_a_start_up_declaration_says_when_the_recorded_folder_cannot_be_read(
    server_hub, data_dir, tmp_path, caplog
):
    """Half of the line whose absence made a stale record an investigation.

    The unplugged-drive moment, and the two silences that keep it worth
    reading: the folder being there is not news, and neither is a machine that
    never relocated anything, whose default folder does not exist yet because it
    has downloaded nothing.
    """
    gone = str(tmp_path / "unplugged" / "models")
    set_builtin_model_dir(gone)

    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, gone)
    assert gone in caplog.text, caplog.text
    assert "cannot be read" in caplog.text, caplog.text

    caplog.clear()
    os.makedirs(gone)
    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, gone)
    assert _warnings(caplog) == [], caplog.text

    caplog.clear()
    os.remove(data_dir / BUILTIN_MODEL_DIR_POINTER)
    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, str(tmp_path / "never-downloaded"))
    assert _warnings(caplog) == [], caplog.text


def test_a_start_up_declaration_says_when_the_engines_stayed_where_they_were(
    server_hub, data_dir, tmp_path, caplog
):
    """The other half, and the one that lasts.

    "The recorded folder cannot be read" is true for a single start: the
    download that follows creates the path, and every start after that sees a
    perfectly readable directory with nothing in it. The steady state of the
    accident - which is the state the machine that reported it was found in - is
    a recorded folder holding none of the engines while the default still holds
    them. Silent when the default is empty too, because that is a machine that
    has downloaded nothing rather than one that lost track of what it has.
    """
    default = data_dir / BUILTIN_DIRNAME
    default.mkdir()
    elsewhere = tmp_path / "big-drive" / "models"
    elsewhere.mkdir(parents=True)
    set_builtin_model_dir(str(elsewhere))

    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, str(elsewhere))
    assert _warnings(caplog) == [], "nothing has been downloaded anywhere yet"

    (default / "pixlstash-anomaly-tagger.safetensors").write_bytes(b"x" * 8)
    caplog.clear()
    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, str(elsewhere))
    assert str(elsewhere) in caplog.text, caplog.text
    assert str(default) in caplog.text, caplog.text
    assert "should have emptied" in caplog.text, caplog.text

    # And it keeps saying so after the re-download has filled the recorded
    # folder, which is the state that lasts: two copies, and every start still
    # fetching into the wrong one. Stopping here would report the accident only
    # inside the window its own download closes.
    (elsewhere / "pixlstash-anomaly-tagger.safetensors").write_bytes(b"x" * 8)
    caplog.clear()
    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, str(elsewhere))
    assert "should have emptied" in caplog.text, caplog.text

    # It stops when the default really is empty, which is a relocation that
    # worked: the files went with it.
    os.remove(default / "pixlstash-anomaly-tagger.safetensors")
    caplog.clear()
    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, str(elsewhere))
    assert _warnings(caplog) == [], caplog.text


def test_a_volume_the_environment_names_is_not_reported_as_a_lost_relocation(
    server_hub, data_dir, tmp_path, monkeypatch, caplog
):
    """The override exists for a deployment that points the folder at a volume
    *without moving anything into it*, so a volume that has not mounted yet is a
    first start and nothing was ever fetched. Warning there would be a false
    alarm on the override's primary use, and it would name a remedy - delete the
    pointer file - that does nothing while the environment wins.
    """
    from pixlstash.services.builtin_models import BUILTIN_MODEL_DIR_ENV

    volume = str(tmp_path / "model-volume" / "models")
    # A record as well, because the override wins over one and the warning has
    # to stay quiet anyway: recording alone would make this pass for having
    # nothing to report.
    set_builtin_model_dir(volume)
    monkeypatch.setenv(BUILTIN_MODEL_DIR_ENV, volume)

    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, builtin_model_dir())
    assert _warnings(caplog) == [], caplog.text


def test_a_recorded_folder_reached_through_a_symlinked_default_still_reports(
    server_hub, data_dir, tmp_path, caplog
):
    """Recognised by the record, not by "this is not the default path".

    The owner who symlinked the default folder at their big drive and then
    relocated onto it has a default that *resolves to* the recorded location, so
    a "not the default" test goes quiet exactly for the person who did the most
    to move their models.
    """
    recorded = tmp_path / "big-drive" / "models"
    recorded.mkdir(parents=True)
    os.symlink(str(recorded), os.path.join(str(data_dir), BUILTIN_DIRNAME))
    set_builtin_model_dir(str(recorded))
    os.rmdir(recorded)

    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, builtin_model_dir())
    assert str(recorded) in caplog.text, caplog.text
    assert "fetched again" in caplog.text, caplog.text

    # And the other half of the same shape: with the drive back, the engines in
    # it are reached through the link as well, so a "still in the default
    # folder" count would report every one of them as left behind, for ever.
    # One directory has nothing to have left behind in the other.
    recorded.mkdir()
    (recorded / "pixlstash-anomaly-tagger.safetensors").write_bytes(b"x" * 8)
    caplog.clear()
    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, builtin_model_dir())
    assert _warnings(caplog) == [], caplog.text


def test_a_record_naming_the_default_folder_says_nothing(
    server_hub, data_dir, tmp_path, caplog
):
    """Relocated away and then back again. Both remedies would name the folder
    they start from, and there is no second place for anything to be left in."""
    default = os.path.join(str(data_dir), BUILTIN_DIRNAME)
    set_builtin_model_dir(default)

    with caplog.at_level("WARNING"):
        declare_builtin_models(server_hub, builtin_model_dir())
    assert _warnings(caplog) == [], caplog.text


def test_no_test_can_name_the_machines_own_recorded_locations():
    """The suite must not be able to write either machine-global pointer.

    Both records outlive the process that writes them and are read by the real
    product on the same machine, so a test that writes one has changed where
    PixlStash downloads its engines for good. That is not hypothetical: a record
    left naming a finished run's ``tmp_path`` had every later start re-create
    the deleted directory and download ~750 MB into it.

    Asserted on the *path* rather than by writing, because the path is what
    ``set_builtin_model_dir`` and ``set_insightface_root`` open - sandbox the
    name and the write follows it. No fixture: the session-scoped redirection in
    ``conftest`` is exactly what is under test, and a per-test ``data_dir``
    would hide it. Drop that fixture and this goes red.
    """
    from platformdirs import user_data_dir

    from pixlstash.services import builtin_models
    from pixlstash.utils import insightface_model_utils

    machine = user_data_dir("pixlstash")
    for pointer in (
        builtin_models._pointer_path(),
        insightface_model_utils._pointer_path(),
    ):
        assert not pointer.startswith(machine), (
            f"{pointer} is the machine's own record; a test that writes it "
            "changes where the real PixlStash downloads its engines"
        )


def test_the_environment_override_still_wins(data_dir, monkeypatch):
    """It exists for a deployment that mounts the folder elsewhere without
    moving anything into it, so a recorded location must not overrule it."""
    from pixlstash.services.builtin_models import BUILTIN_MODEL_DIR_ENV

    set_builtin_model_dir("/mnt/big/models")
    monkeypatch.setenv(BUILTIN_MODEL_DIR_ENV, "/srv/models")
    assert builtin_model_dir() == "/srv/models"


def test_every_downloader_asks_the_accessor_rather_than_rebuilding_the_path(
    data_dir,
):
    """The prerequisite the whole feature rests on. The declaration, the
    inference engine and the aesthetic scorer each used to build
    ``user_data_dir("pixlstash")/downloaded_models`` for themselves: they agreed
    by convention, so a relocated folder would have been declared in one place
    and filled in another. Asserted on the aesthetic table because it is the one
    that resolved its paths at IMPORT time, which is what blocked the change."""
    from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
    from pixlstash.tagger_plugins.clip_service import CLIP_MODEL_NAME

    set_builtin_model_dir("/mnt/big/models")
    config = ImageEmbeddingTask.AESTHETIC_MODELS.get(CLIP_MODEL_NAME)
    if config is None:
        pytest.skip(f"no aesthetic scorer for CLIP model {CLIP_MODEL_NAME}")
    assert ImageEmbeddingTask._aesthetic_config()["path"] == os.path.join(
        "/mnt/big/models", config["filename"]
    )


def test_no_module_builds_the_download_path_for_itself():
    """The convention that used to hold the three callers together, pinned so a
    fourth caller cannot quietly reintroduce it."""
    import pathlib

    import pixlstash

    root = pathlib.Path(pixlstash.__file__).parent
    offenders = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if BUILTIN_DIRNAME in path.read_text(encoding="utf-8")
        and path.name != "builtin_models.py"
    ]
    assert offenders == [], (
        f"{offenders} name the download folder themselves; ask "
        "builtin_model_dir() instead, or the folder cannot be relocated."
    )
