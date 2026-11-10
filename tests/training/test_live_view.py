"""The live view's reading of a run (#2118): phases, columns, cells and speed."""

import sqlite3

import pytest

from pixlstash.training import live_view


@pytest.mark.parametrize(
    "status, info, step, completed_at, phase",
    [
        ("queued", "", 0, None, "waiting_gpu"),
        ("running", "Starting", 0, None, "loading"),
        ("running", "Loading model", 0, None, "loading"),
        ("running", "Quantizing transformer", 0, None, "loading"),
        ("running", "Loading dataset", 0, None, "caching"),
        ("running", "Caching latents", 0, None, "caching"),
        ("running", "Training", 0, None, "training"),
        ("running", "Generating images - 3/6", 0, None, "previews"),
        ("running", "Generating images - 3/6", 750, None, "previews"),
        ("running", "Saving model", 750, None, "saving"),
        ("running", "Quantizing transformer", 40, None, "training"),
        ("stopping", "Training", 40, None, "training"),
        ("completed", "Training completed", 1500, None, "importing"),
        ("completed", "Training completed", 1500, "2026-10-26T03:12:00+00:00", None),
        ("stopped", "Job stopped", 40, "2026-10-26T03:12:00+00:00", None),
        ("error", "CUDA out of memory", 40, "2026-10-26T03:12:00+00:00", None),
    ],
)
def test_the_phase_follows_the_status_and_the_trainers_info_line(
    status, info, step, completed_at, phase
):
    assert live_view.phase_of(status, info, step, completed_at) == phase


def test_preview_progress_reads_the_sample_being_drawn():
    assert live_view.preview_progress("Generating images - 5/6") == {
        "drawing": 5,
        "total": 6,
    }
    assert live_view.preview_progress("Training") is None


# torch's CUDA OOM paragraph, as ai-toolkit stores it in its job row.
_TORCH_OOM = (
    "CUDA out of memory. Tried to allocate 5.06 GiB. GPU 0 has a total capacity "
    "of 31.41 GiB of which 5.08 GiB is free. Including non-PyTorch memory, this "
    "process has 21.72 GiB memory in use. Process 101 has 498.00 MiB memory in "
    "use. Process 102 has 1.50 GiB memory in use. Of the allocated memory 20.70 "
    "GiB is allocated by PyTorch, and 370.02 MiB is reserved by PyTorch but "
    "unallocated. If reserved but unallocated memory is large try setting "
    "PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True to avoid fragmentation."
)


@pytest.mark.parametrize(
    "info, step, summary",
    [
        (
            _TORCH_OOM,
            500,
            "Ran out of GPU memory at step 500: it needed 5.06 GiB more, with "
            "5.08 GiB of 31.41 GiB free. 2 other programs held 1.99 GiB of the "
            "card; close them and train again.",
        ),
        (
            _TORCH_OOM.replace("Process 102 has 1.50 GiB memory in use. ", ""),
            7,
            "Ran out of GPU memory at step 7: it needed 5.06 GiB more, with "
            "5.08 GiB of 31.41 GiB free. 1 other program held 0.49 GiB of the "
            "card; close them and train again.",
        ),
        (
            "torch.OutOfMemoryError: CUDA out of memory.",
            0,
            "Ran out of GPU memory at step 0.",
        ),
        (
            "Error loading model\nTraceback (most recent call last):",
            0,
            "Error loading model",
        ),
        (
            _TORCH_OOM.replace("1.50 GiB", "0 bytes"),
            7,
            "Ran out of GPU memory at step 7: it needed 5.06 GiB more, with "
            "5.08 GiB of 31.41 GiB free. 2 other programs held 0.49 GiB of the "
            "card; close them and train again.",
        ),
        ("could not read /data/oom/cat.png", 12, "could not read /data/oom/cat.png"),
        ("host out of memory while pinning", 3, "host out of memory while pinning"),
        ("x" * 300, 0, "x" * 199 + "…"),
        ("", 0, "Training failed."),
        (None, 0, "Training failed."),
    ],
)
def test_a_failure_reads_as_one_sentence(info, step, summary):
    assert live_view.failure_summary(info, step) == summary


def test_the_phase_log_appends_only_a_new_phase():
    log = []
    assert live_view.append_phase(log, "loading", "2026-10-26T00:00:00+00:00")
    assert not live_view.append_phase(log, "loading", "2026-10-26T00:00:05+00:00")
    assert not live_view.append_phase(log, None, "2026-10-26T00:00:06+00:00")
    assert live_view.append_phase(log, "training", "2026-10-26T00:02:35+00:00")
    assert [entry["phase"] for entry in log] == ["loading", "training"]
    durations = live_view.phase_durations(log, "2026-10-26T00:03:35+00:00")
    assert durations == [
        {"phase": "loading", "seconds": 155.0},
        {"phase": "training", "seconds": 60.0},
    ]


def test_an_unreadable_phase_log_reads_as_empty():
    assert live_view.parse_phase_log("{not json") == []
    assert live_view.parse_phase_log(None) == []


def test_columns_are_step_zero_every_preview_step_and_the_last():
    assert live_view.checkpoint_columns(1500, 250, []) == [
        0,
        250,
        500,
        750,
        1000,
        1250,
        1500,
    ]
    # A last step off the preview pitch still gets its column.
    assert live_view.checkpoint_columns(600, 250, []) == [0, 250, 500, 600]
    # Without a config, the columns are the steps the samples name.
    assert live_view.checkpoint_columns(None, None, [0, 40, 80]) == [0, 40, 80]


def test_prompt_rows_mark_the_trigger_word_as_a_whole_word():
    rows = live_view.prompt_rows(
        ["photo of ohwx woman", "a woman in a raincoat", "ohwxy on a beach"], "ohwx"
    )
    assert [row["with_trigger"] for row in rows] == [True, False, False]
    assert [row["index"] for row in rows] == [0, 1, 2]


def test_prompt_rows_tell_the_lookalike_from_the_stranger():
    prompts = [
        "skpr reading a newspaper",
        "an elderly man reading a newspaper",
        "skpr at a cafe",
        "a young woman at a cafe",
    ]
    rows = live_view.prompt_rows(prompts, "skpr", "an elderly man", "a young woman")
    assert [row["role"] for row in rows] == [None, "lookalike", None, "stranger"]
    # A prompt the owner wrote about something else makes no claim.
    edited = live_view.prompt_rows(
        ["a dog in a park"], "skpr", "an elderly man", "a young woman"
    )
    assert edited[0]["role"] is None
    # Without the description the run makes no claim about either.
    assert {row["role"] for row in live_view.prompt_rows(prompts, "skpr")} == {None}


def test_preservation_is_read_from_the_resolved_values():
    def resolution(on):
        return {
            "values": {
                "train.diff_output_preservation": {"value": on, "source": "captions"},
                "train.diff_output_preservation_class": {
                    "value": "an elderly man",
                    "source": "captions",
                },
            }
        }

    assert live_view.preservation(resolution(True)) == {
        "on": True,
        "description": "an elderly man",
        "stranger": "a young woman",
    }
    assert live_view.preservation(resolution(False))["on"] is False
    # A class word pinned blank, or one word, never breaks the run screen.
    blank = resolution(True)
    blank["values"]["train.diff_output_preservation_class"]["value"] = "  "
    assert live_view.preservation(blank) is None
    bare = resolution(True)
    bare["values"]["train.diff_output_preservation_class"]["value"] = "person"
    assert live_view.preservation(bare)["stranger"] == "a child"
    # A run from before the switch, or not of a person, never decided.
    assert live_view.preservation({"values": {}}) is None
    assert live_view.preservation(None) is None


def test_sample_settings_read_the_rendered_config():
    config = {
        "config": {
            "process": [
                {
                    "sample": {
                        "sample_every": 250,
                        "samples": [{"prompt": "a"}, {"prompt": "b"}],
                    },
                    "save": {"save_every": 250},
                    "model": {
                        "layer_offloading": True,
                        "layer_offloading_transformer_percent": 0.15,
                    },
                }
            ]
        }
    }
    settings = live_view.sample_settings(config)
    assert settings == {
        "sample_every": 250,
        "save_every": 250,
        "prompts": ["a", "b"],
        "layer_offloading": True,
        "offload_percent": 0.15,
    }
    assert live_view.sample_settings(None)["prompts"] == []


def _cell(**overrides):
    args = {
        "column": 1000,
        "index": 0,
        "has_sample": False,
        "phase": "previews",
        "step": 1000,
        "drawing_column": 1000,
        "previews": {"drawing": 5, "total": 6},
        "previews_deferred": False,
    }
    args.update(overrides)
    return live_view.cell_state(**args)


def test_a_cell_is_drawn_queued_drawing_future_or_missing():
    assert _cell(has_sample=True) == "sample"
    assert _cell(index=4) == "drawing"
    assert _cell(index=5) == "queued"
    # A cell earlier in the round without its picture never reads as queued.
    assert _cell(index=2) == "missing"
    assert _cell(column=1250, phase="training", previews=None) == "future"
    assert _cell(column=750, phase="training", previews=None) == "missing"
    assert _cell(column=1250, phase=None, previews=None) == "missing"
    assert _cell(column=1250, previews_deferred=True, previews=None) == "deferred"
    # Step 0 is the before picture, drawn before training whatever the policy.
    assert _cell(column=0, step=0, previews_deferred=True, previews=None) != "deferred"


def _loss_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE steps (step INTEGER PRIMARY KEY, wall_time REAL)")
    conn.executemany("INSERT INTO steps VALUES (?, ?)", rows)
    conn.commit()
    conn.close()


def test_speed_is_the_median_step_and_the_range_includes_the_pauses(tmp_path):
    # Steps 1..11 two seconds apart, with a 30 s preview between 5 and 6.
    rows = []
    wall = 1000.0
    for step in range(1, 12):
        rows.append((step, wall))
        wall += 32.0 if step == 5 else 2.0
    path = str(tmp_path / "loss_log.db")
    _loss_db(path, rows)

    times = live_view.read_step_times(path)
    assert times[0] == (1, 1000.0)
    pace = live_view.speed(times, 21)

    assert pace["seconds_per_step"] == 2.0
    # Ten steps to go: 20 s at the median, 50 s at the run's own average (5 s).
    assert pace["remaining_low_s"] == 20.0
    assert pace["remaining_high_s"] == 50.0
    assert pace["last_step_at"] == rows[-1][1]


def test_speed_is_unknown_before_two_steps_and_without_a_log(tmp_path):
    assert live_view.speed([], 100)["seconds_per_step"] is None
    assert live_view.speed([(1, 5.0)], 100)["remaining_low_s"] is None
    assert live_view.read_step_times(str(tmp_path / "missing.db")) == []


def test_the_trainer_info_line_is_read_from_its_job_row(tmp_path):
    path = str(tmp_path / "job.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE Job (id TEXT PRIMARY KEY, info TEXT)")
    conn.execute("INSERT INTO Job VALUES ('job-1', 'Generating images - 2/4')")
    conn.commit()
    conn.close()
    assert live_view.read_trainer_info(path, "job-1") == "Generating images - 2/4"
    assert live_view.read_trainer_info(path, "job-2") == ""
    assert live_view.read_trainer_info(str(tmp_path / "none.db"), "job-1") == ""


def test_a_malformed_step_time_is_skipped_not_fatal(tmp_path):
    path = str(tmp_path / "loss_log.db")
    _loss_db(path, [(1, 10.0), (2, "not a time"), (3, 14.0)])
    assert live_view.read_step_times(path) == [(1, 10.0), (3, 14.0)]


@pytest.mark.parametrize(
    ("log", "fetching"),
    [
        # huggingface_hub's xet lines, as ai-toolkit's log carries them.
        ("Loading transformer\n\rDownloading bytes: ##  | 1.2GB, 30MB/s", True),
        ("x: reconstructing file:  17%|#7  | 2.1GB / 12.3GB", True),
        ("Fetching 3 files:  33%|###3      | 1/3 [01:55<03:50]", True),
        ("Reconstructing (incomplete total...):   0%|  0.00B / 8.04GB", True),
        ("model-00001-of-00002.safetensors:  45%|####5  | 2.2G/4.9G", True),
        # Done fetching, or never fetching.
        ("\rDownloading bytes: ##########| 6.97GB\rDownload complete: : 6.97GB", False),
        ("Fetching 3 files: 100%|##########| 3/3 [01:55<00:00]\n", False),
        (
            "Downloading bytes: ##| 1GB\nQuantizing (qfloat8)\n - quantizing 36 blocks\n",
            False,
        ),
        ("Loading weights: 100%|##########| 398/398\n", False),
        ("Loading model from /home/me/Downloads/zimage\n", False),
        ("", False),
    ],
)
def test_a_download_is_read_from_the_last_line_of_the_trainer_log(
    tmp_path, log, fetching
):
    path = tmp_path / "log.txt"
    path.write_text(log, encoding="utf-8")
    assert live_view.downloading(str(path)) is fetching


def test_a_missing_trainer_log_is_not_a_download(tmp_path):
    assert live_view.downloading(str(tmp_path / "absent.txt")) is False
