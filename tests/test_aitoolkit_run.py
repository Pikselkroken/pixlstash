"""Reading an ai-toolkit run off disk.

The rule under test throughout: **the reader only reads.** It must never hash,
copy, move or write, because the shelf runs it against a folder the user has
merely pointed at and has not yet agreed to import.

The second rule: a run missing its config is still a run. Steps and previews
come from filenames, so a broken or absent ``config.yaml`` costs the base model,
triggers and rank, and nothing else.
"""

import os

import pytest

from pixlstash.utils.aitoolkit_run import (
    Checkpoint,
    TrainingRun,
    read_output_root,
    read_run,
)

CONFIG = """\
job: extension
config:
  name: MyCharacter
  process:
    - type: sd_trainer
      model:
        name_or_path: black-forest-labs/FLUX.1-dev
      network:
        type: lora
        linear: 16
        linear_alpha: 16
      trigger_word: ohwx
"""


def _run_folder(tmp_path, name="MyCharacter", steps=(250, 2750), final=True):
    run = tmp_path / name
    (run / "samples").mkdir(parents=True)
    for step in steps:
        (run / f"{name}_{step:09d}.safetensors").write_bytes(b"")
        for index in range(2):
            (run / "samples" / f"1712345678901__{step:09d}_{index}.jpg").write_bytes(
                b""
            )
    if final:
        (run / f"{name}.safetensors").write_bytes(b"")
    return run


class TestCheckpoints:
    def test_finds_every_step_and_the_bare_final(self, tmp_path):
        run = read_run(str(_run_folder(tmp_path)))
        assert run.steps == [250, 2750]
        assert [c.filename for c in run.checkpoints] == [
            "MyCharacter_000000250.safetensors",
            "MyCharacter_000002750.safetensors",
            "MyCharacter.safetensors",
        ]

    def test_the_final_sorts_last_not_first(self, tmp_path):
        # The final carries no step. Treating that as step 0 would sort the
        # finished adapter to the front, which is the opposite of useful.
        run = read_run(str(_run_folder(tmp_path)))
        assert run.checkpoints[-1].is_final
        assert run.checkpoints[-1].step is None

    def test_step_zero_is_a_step_not_a_final(self, tmp_path):
        # `_000000000` is a real save ai-toolkit can emit. It must not be
        # confused with the bare final.
        run_dir = _run_folder(tmp_path, steps=(0, 500), final=False)
        run = read_run(str(run_dir))
        assert run.steps == [0, 500]
        assert not any(c.is_final for c in run.checkpoints)

    def test_a_run_whose_name_ends_in_digits_is_not_mis_split(self, tmp_path):
        # "sdxl_v2" at step 500, not "sdxl" at some version.
        run_dir = tmp_path / "sdxl_v2"
        run_dir.mkdir()
        (run_dir / "sdxl_v2_000000500.safetensors").write_bytes(b"")
        run = read_run(str(run_dir))
        assert run.steps == [500]

    def test_a_digit_run_inside_the_name_is_not_taken_as_the_step(self, tmp_path):
        # The case the end-anchor actually protects: the name carries its own
        # 4+ digit group. A non-greedy or unanchored pattern reads "flux_2024"
        # as run "flux" at step 2024 and loses the real step entirely.
        run_dir = tmp_path / "flux_2024"
        run_dir.mkdir()
        (run_dir / "flux_2024_000000500.safetensors").write_bytes(b"")
        run = read_run(str(run_dir))
        assert run.steps == [500]

    def test_a_final_whose_run_name_ends_in_digits_is_still_final(self, tmp_path):
        # `Archive_2025/Archive_2025.safetensors` matches the step pattern on
        # the run name alone. Accepting it gives the final `step=2025`, sorts it
        # among the intermediates and invents a step with no samples, so the
        # parsed name has to equal the run's.
        run_dir = tmp_path / "Archive_2025"
        run_dir.mkdir()
        (run_dir / "Archive_2025.safetensors").write_bytes(b"")
        (run_dir / "Archive_2025_000000500.safetensors").write_bytes(b"")
        run = read_run(str(run_dir))
        assert run.steps == [500]
        assert [(c.filename, c.step) for c in run.checkpoints] == [
            ("Archive_2025_000000500.safetensors", 500),
            ("Archive_2025.safetensors", None),
        ]

    def test_non_checkpoint_files_are_ignored(self, tmp_path):
        run_dir = _run_folder(tmp_path)
        (run_dir / "notes.txt").write_bytes(b"")
        (run_dir / "optimizer.pt").write_bytes(b"")
        run = read_run(str(run_dir))
        assert all(c.filename.endswith(".safetensors") for c in run.checkpoints)


class TestSamples:
    def test_groups_previews_by_step(self, tmp_path):
        run = read_run(str(_run_folder(tmp_path)))
        assert [s.index for s in run.samples_for_step(2750)] == [0, 1]
        assert all(s.step == 2750 for s in run.samples_for_step(2750))

    def test_parses_the_double_underscore_layout(self, tmp_path):
        run = read_run(str(_run_folder(tmp_path)))
        first = run.samples_for_step(250)[0]
        assert first.timestamp == "1712345678901"
        assert (first.step, first.index) == (250, 0)

    def test_a_users_own_images_are_skipped_not_fatal(self, tmp_path):
        run_dir = _run_folder(tmp_path)
        (run_dir / "samples" / "my-reference.jpg").write_bytes(b"")
        run = read_run(str(run_dir))
        assert "my-reference.jpg" not in [s.filename for s in run.samples]
        assert len(run.samples) == 4

    def test_a_run_with_no_samples_folder_still_reads(self, tmp_path):
        run_dir = tmp_path / "Bare"
        run_dir.mkdir()
        (run_dir / "Bare_000000100.safetensors").write_bytes(b"")
        run = read_run(str(run_dir))
        assert run.samples == []
        assert run.steps == [100]


class TestConfig:
    def test_reads_base_model_trigger_and_rank(self, tmp_path):
        run_dir = _run_folder(tmp_path)
        (run_dir / "config.yaml").write_text(CONFIG, encoding="utf-8")
        run = read_run(str(run_dir))
        assert run.base_model == "black-forest-labs/FLUX.1-dev"
        assert run.trigger_words == ["ohwx"]
        assert run.rank == 16
        assert run.config_error is None

    def test_finds_keys_wherever_ai_toolkit_nests_them(self, tmp_path):
        # The nesting path has moved between ai-toolkit versions. Searching by
        # key rather than by path means a layout change costs nothing.
        run_dir = _run_folder(tmp_path)
        (run_dir / "config.yaml").write_text(
            "a:\n  b:\n    - c:\n        name_or_path: some/model\n", encoding="utf-8"
        )
        run = read_run(str(run_dir))
        assert run.base_model == "some/model"

    def test_a_missing_config_is_recorded_not_raised(self, tmp_path):
        run = read_run(str(_run_folder(tmp_path)))
        assert run.config_error is not None
        assert run.base_model is None
        # The part that matters: the run is still fully usable.
        assert run.steps == [250, 2750]
        assert len(run.samples) == 4

    def test_malformed_yaml_is_recorded_not_raised(self, tmp_path):
        run_dir = _run_folder(tmp_path)
        (run_dir / "config.yaml").write_text("a: [unclosed\n", encoding="utf-8")
        run = read_run(str(run_dir))
        assert run.config_error is not None
        assert run.steps == [250, 2750]

    def test_linear_true_is_not_read_as_rank_one(self, tmp_path):
        # bool is a subclass of int, so a naive isinstance check invents rank 1.
        run_dir = _run_folder(tmp_path)
        (run_dir / "config.yaml").write_text("network:\n  linear: true\n", "utf-8")
        run = read_run(str(run_dir))
        assert run.rank is None

    def test_a_list_of_triggers_is_kept_whole(self, tmp_path):
        run_dir = _run_folder(tmp_path)
        (run_dir / "config.yaml").write_text(
            "trigger_word:\n  - ohwx\n  - woman\n", encoding="utf-8"
        )
        run = read_run(str(run_dir))
        assert run.trigger_words == ["ohwx", "woman"]


class TestOutputRoot:
    def test_lists_every_run_by_name(self, tmp_path):
        _run_folder(tmp_path, name="Bravo")
        _run_folder(tmp_path, name="Alpha")
        runs = read_output_root(str(tmp_path))
        assert [r.name for r in runs] == ["Alpha", "Bravo"]

    def test_skips_a_folder_that_holds_no_checkpoints(self, tmp_path):
        _run_folder(tmp_path, name="Real")
        (tmp_path / "datasets").mkdir()
        (tmp_path / "datasets" / "a.jpg").write_bytes(b"")
        assert [r.name for r in read_output_root(str(tmp_path))] == ["Real"]

    def test_a_missing_root_is_an_error_worth_raising(self, tmp_path):
        with pytest.raises(NotADirectoryError):
            read_output_root(str(tmp_path / "nope"))

    def test_a_file_where_a_run_folder_belongs_is_an_error(self, tmp_path):
        target = tmp_path / "afile"
        target.write_bytes(b"")
        with pytest.raises(NotADirectoryError):
            read_run(str(target))


class TestReadsOnly:
    def test_nothing_on_disk_changes(self, tmp_path):
        """The load-bearing guarantee: this runs against folders the user has
        only pointed at, so it must not write, move or delete anything."""
        _run_folder(tmp_path, name="Alpha")
        run_dir = tmp_path / "Alpha"
        (run_dir / "config.yaml").write_text(CONFIG, encoding="utf-8")

        def snapshot():
            seen = {}
            for base, _dirs, files in os.walk(tmp_path):
                for name in files:
                    path = os.path.join(base, name)
                    stat = os.stat(path)
                    seen[path] = (stat.st_size, stat.st_mtime_ns)
            return seen

        before = snapshot()
        runs = read_output_root(str(tmp_path))
        assert runs and runs[0].checkpoints
        assert snapshot() == before


class TestDataclassSurface:
    def test_a_checkpoint_knows_whether_it_is_final(self):
        assert Checkpoint(path="p", filename="f", step=None).is_final
        assert not Checkpoint(path="p", filename="f", step=10).is_final

    def test_an_empty_run_reports_no_steps_rather_than_failing(self):
        assert TrainingRun(name="x", path="/tmp/x").steps == []
