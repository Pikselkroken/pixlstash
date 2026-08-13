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


def test_a_repo_deleted_from_the_cache_stops_claiming_its_bytes(
    server_hub, tmp_path, monkeypatch
):
    """The sweep these folders have nowhere else to get.

    The scanner marks what it did not see `missing` on every walk, and it skips
    these folders because they carry an `owner`. So a repo that leaves the
    HuggingFace index — `huggingface-cli delete-cache` — would otherwise keep a
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
    """`missing` for a declared engine is decided by the existence check, not by
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
    # A frozenset, like the real one — the ordering trap this code had to fix.
    r.revisions = frozenset({_Rev()}) if snapshot else frozenset()
    return r


def test_our_own_downloaders_repos_are_facts_not_guesses():
    """Restated here for the same reason `builtin_models` restates filenames, so
    the duplicate is pinned against the modules that own the real constants —
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
    `google/flan-t5-base` — a text encoder that captions nothing — in the column
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

    Florence-2 is the worked example — ONE setting and one set of weights drive
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
    it serves, so the declaration has to fill it — and `model.kind` keeps the
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
    still be listed under it forever — and re-declaring is what every start-up
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


def test_forgetting_a_model_takes_its_capabilities_with_it(server_hub, tmp_path):
    """Foreign keys are on for the hub, so `model_capability` is not a row that
    leaks quietly if it is forgotten — it ABORTS the delete. Both directions
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
