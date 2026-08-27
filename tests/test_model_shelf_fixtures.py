"""The model-shelf fixture generator produces files the shelf's readers accept.

This is the acceptance test for ``scripts/generate_model_shelf_fixtures.py``.
The fixtures exist so B4's scanner and the F1+ shelf UI are built against
realistic data instead of invented data, and that is only true while the two
readers that will consume them -
:mod:`pixlstash.utils.adapter_header` and :mod:`pixlstash.utils.aitoolkit_run` -
read the generated tree exactly as they read the real thing. So every assertion
below goes through those readers rather than re-parsing the fixtures here.

The adapter count is small on purpose: 1,800 files prove nothing 60 do not, and
the name generator is exercised at full scale separately without touching disk.
"""

import hashlib
import importlib.util
import os
import struct
import sys

import pytest

from pixlstash.utils.adapter_header import (
    FILE_ADAPTER,
    FILE_CHECKPOINT,
    FILE_UNKNOWN,
    describe_adapter,
)
from pixlstash.utils.aitoolkit_run import read_output_root, read_run

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "generate_model_shelf_fixtures.py",
)


def _load_generator():
    """Load the fixture generator, which lives under scripts/, not in a package."""
    spec = importlib.util.spec_from_file_location(
        "generate_model_shelf_fixtures", _SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["generate_model_shelf_fixtures"] = module
    spec.loader.exec_module(module)
    return module


generator = _load_generator()

SMALL_ADAPTER_COUNT = 60


@pytest.fixture(scope="module")
def tree(tmp_path_factory):
    """One generated tree, shared: generating it is the expensive part."""
    root = tmp_path_factory.mktemp("model-shelf")
    return generator.generate(root, adapters=SMALL_ADAPTER_COUNT)


# ── the big adapter folder ───────────────────────────────────────────────────


class TestAdapterFolder:
    def test_every_adapter_is_readable_by_the_header_parser(self, tree):
        """The acceptance condition: nothing here is a file the shelf cannot read."""
        files = sorted(tree.adapter_folder.glob("*.safetensors"))
        assert len(files) == SMALL_ADAPTER_COUNT
        for path in files:
            info = describe_adapter(str(path))
            assert info is not None, f"header parser could not read {path.name}"
            assert info.tensor_count > 0, path.name
            assert info.param_count > 0, path.name

    def test_marker_free_files_read_as_unknown_never_checkpoint(self, tree):
        """`unknown` must never be stored or shown as checkpoint (plan §6.5).

        The generator writes a few marker-free adapters deliberately. They are
        small, so the parameter-count rule must leave them unknown rather than
        promoting them to checkpoint.
        """
        kinds = {
            path.name: describe_adapter(str(path)).file_kind
            for path in tree.adapter_folder.glob("*.safetensors")
        }
        assert FILE_ADAPTER in kinds.values()
        assert FILE_UNKNOWN in kinds.values()
        assert FILE_CHECKPOINT not in kinds.values()

    def test_the_folder_carries_more_than_one_algorithm(self, tree):
        """A shelf whose kind column is all `lora` is not a useful fixture."""
        kinds = {
            describe_adapter(str(path)).kind
            for path in tree.adapter_folder.glob("*.safetensors")
        }
        assert len(kinds) > 1, kinds

    def test_both_the_metadata_rich_and_the_bare_case_are_present(self, tree):
        """All three provenances, with the informative one in the majority.

        Measured 2026-08-09 over a real 91-file folder: 57 carried trainer
        ``ss_*`` metadata, 22 carried only ``format``, 9 carried no block at
        all. The generator used to have that backwards, which made the shelf's
        "you will have to name this yourself" path look like the common case
        when it is the minority one. The direction is pinned here; the exact
        ratio is not, because it moves with how much of a library was
        downloaded rather than trained.
        """
        described = [
            describe_adapter(str(path))
            for path in tree.adapter_folder.glob("*.safetensors")
        ]
        assert any(info.has_metadata for info in described)
        assert any(not info.has_metadata for info in described)
        assert any(info.trigger_words for info in described)
        rich = sum(1 for info in described if info.has_metadata)
        assert rich > len(described) / 2, f"{rich} of {len(described)}"

    def test_more_than_one_dtype_is_emitted(self):
        """bf16 dominates, but fp16 and fp32 files exist and weigh differently.

        Two adapters of one rank can differ in size for no reason but dtype, so
        a shelf reading size as a proxy for rank is reading it wrong.
        """
        dtypes = {plan.dtype for plan in generator.iter_adapters(SMALL_ADAPTER_COUNT)}
        assert dtypes == {"BF16", "F16", "F32"}, dtypes

    def test_sizes_are_reported_in_full_but_not_paid_for(self, tree):
        """Real sizes, sparse payloads: that is what makes 1,800 adapters fit.

        Without this the folder is either hundreds of real gigabytes or a folder
        of 4 KB files that makes a size-sorted shelf meaningless.
        """
        sizes = [
            os.path.getsize(path) for path in tree.adapter_folder.glob("*.safetensors")
        ]
        # The spread is the point, and it is wider than it looks: the same
        # measured folder ran from 17 MB to 20.5 GB, so a size-sorted shelf and
        # F2's capacity meters both have something real to work with. The
        # multi-gigabyte end is also the one B4 defers hashing for.
        assert min(sizes) > 15 * 1024 * 1024
        assert max(sizes) > 1024**3
        assert tree.reported_bytes == sum(sizes)
        assert tree.disk_bytes < tree.reported_bytes / 50

    def test_no_two_adapters_are_the_same_file(self, tree):
        """Every adapter is its own file, checked on the bytes that were written.

        This is the regression: real adapters cluster hard on shape, so the
        generator repeats shapes on purpose, and a sparse payload is all zeroes.
        That made a file's SHA-256 a pure function of its header, and 1,800
        fixtures collapsed onto 478 distinct files - the shelf then showed 478
        rows and one model carried 202 locations. The payload slug is what fixes
        it, so this hashes the header *and* the slug behind it, straight off
        disk. Everything after them is a hole the header itself measures, which
        is why reading the whole 4 GB file would prove nothing extra.

        At 1,800 the same holds by construction rather than by test: the slug is
        a pure function of the filename, and
        :meth:`test_names_are_unique_at_full_scale` pins those at full scale.
        """
        digests = set()
        for path in sorted(tree.adapter_folder.glob("*.safetensors")):
            with open(path, "rb") as handle:
                (header_len,) = struct.unpack("<Q", handle.read(8))
                leading = handle.read(header_len + generator._PAYLOAD_SLUG_BYTES)
            digests.add(hashlib.sha256(leading).hexdigest())
        assert len(digests) == SMALL_ADAPTER_COUNT

    def test_names_are_unique_at_full_scale(self):
        """1,800 files in one folder; two of them cannot share a name."""
        names = generator._adapter_names(1800)
        assert len(names) == 1800
        assert len(set(names)) == 1800


# ── the ai-toolkit runs ──────────────────────────────────────────────────────


class TestFullRun:
    def test_the_reader_sees_five_steps_and_the_bare_final(self, tree):
        run = read_run(str(tree.full_run))
        assert run.steps == [250, 500, 750, 1000, 1250]
        assert run.checkpoints[-1].is_final
        assert sum(1 for c in run.checkpoints if c.is_final) == 1

    def test_it_carries_the_full_sample_grid(self, tree):
        run = read_run(str(tree.full_run))
        assert len(run.samples) == 130
        assert len(run.samples_for_step(750)) == 26
        assert [s.index for s in run.samples_for_step(750)] == list(range(26))

    def test_the_samples_are_real_images(self, tree):
        """The shelf renders these, so a zero-byte placeholder will not do."""
        from PIL import Image

        run = read_run(str(tree.full_run))
        with Image.open(run.samples[0].path) as image:
            assert image.size == (64, 96)

    def test_the_config_yields_base_model_trigger_and_rank(self, tree):
        run = read_run(str(tree.full_run))
        assert run.config_error is None
        assert run.base_model == "black-forest-labs/FLUX.1-dev"
        assert run.trigger_words == ["aur0ra"]
        assert run.rank == 16

    def test_its_checkpoints_read_as_adapters(self, tree):
        run = read_run(str(tree.full_run))
        for checkpoint in run.checkpoints:
            info = describe_adapter(checkpoint.path)
            assert info is not None, checkpoint.filename
            assert info.file_kind == FILE_ADAPTER
            assert info.trained_by == "ai-toolkit 0.9.11"


class TestRunWithoutABareFinal:
    def test_no_checkpoint_claims_to_be_the_final(self, tree):
        """The unconfirmed-cover state: every save is a step, none is settled."""
        run = read_run(str(tree.no_final_run))
        assert run.steps == [500, 1000, 1500]
        assert not any(c.is_final for c in run.checkpoints)

    def test_it_still_has_samples_and_a_config(self, tree):
        run = read_run(str(tree.no_final_run))
        assert len(run.samples) == 24
        assert run.rank == 32
        assert run.base_model == "Qwen/Qwen-Image"


def test_the_output_root_lists_both_runs(tree):
    runs = read_output_root(str(tree.aitoolkit_output))
    assert [run.name for run in runs] == ["Aurora", "Nightfall"]


# ── the states that are not a training run ───────────────────────────────────


def test_the_manual_stack_has_two_adapters_and_no_samples(tree):
    """Two hand-imports of one subject: a stack the shelf must cover without art."""
    files = sorted(tree.manual_stack.glob("*.safetensors"))
    assert [path.name for path in files] == [
        "Cyanwood_v1.safetensors",
        "Cyanwood_v2.safetensors",
    ]
    assert not (tree.manual_stack / "samples").exists()
    for path in files:
        assert describe_adapter(str(path)).file_kind == FILE_ADAPTER


def test_the_manual_stack_is_the_deliberate_duplicate_pair(tree):
    """These two really are one file under two names, and must stay that way.

    A copy that landed in two registered folders, or a move interrupted between
    the write and the unlink, are states the shelf has to recognise, so the
    fixture set owes it one duplicate pair. It is the only one: everything in
    ``adapters/`` is distinct, which is what
    :meth:`TestAdapterFolder.test_no_two_adapters_are_the_same_file` pins.
    """
    digests = {
        hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(tree.manual_stack.glob("*.safetensors"))
    }
    assert len(digests) == 1


def test_the_offline_mount_does_not_resolve(tree):
    """An unmounted share: the parent is there, the mount point is not.

    A scanner has to mark this `unreachable`, which is a different row state
    from `missing`, so the fixture must not be merely an empty directory.
    """
    assert tree.offline_mount.parent.is_dir()
    assert not tree.offline_mount.exists()
    with pytest.raises(NotADirectoryError):
        read_run(str(tree.offline_mount))


class TestTheAdapterFolderIsOnlyCleanedWhenItIsOurs:
    """`root` is a positional CLI argument, so the cleanup needs a leash.

    Regenerating deletes the previous run's adapters, which is required for
    idempotency: the names come from a seeded list, so a shorter second run
    would otherwise leave the first run's tail behind. The danger is that the
    same code, pointed at a real model library, deletes it. The marker file is
    what separates the two cases.
    """

    def test_a_folder_we_generated_is_cleaned_and_regenerated(self, tmp_path):
        folder = tmp_path / "adapters"
        generator.generate_adapter_folder(folder, count=6)
        (folder / "MANUALLY_ADDED.safetensors").write_bytes(b"stray")

        generator.generate_adapter_folder(folder, count=4)

        names = sorted(p.name for p in folder.glob("*.safetensors"))
        assert len(names) == 4, names
        assert "MANUALLY_ADDED.safetensors" not in names
        assert (folder / generator._FIXTURE_MARKER).exists()

    def test_an_unmarked_folder_holding_models_is_refused_untouched(self, tmp_path):
        """The mistyped-path case: someone else's models, left alone."""
        folder = tmp_path / "real-loras"
        folder.mkdir()
        (folder / "precious.safetensors").write_bytes(b"somebody's 4090-hours")

        with pytest.raises(RuntimeError, match="did not create it"):
            generator.generate_adapter_folder(folder, count=2)

        assert (
            folder / "precious.safetensors"
        ).read_bytes() == b"somebody's 4090-hours"
        assert not (folder / generator._FIXTURE_MARKER).exists()

    def test_an_empty_unmarked_folder_is_adopted(self, tmp_path):
        """Nothing to lose, so generating into a fresh directory still works."""
        folder = tmp_path / "fresh"
        generator.generate_adapter_folder(folder, count=3)
        assert len(list(folder.glob("*.safetensors"))) == 3
