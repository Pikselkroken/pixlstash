"""Adapter versus checkpoint, and the display name derived from a filename.

Split from ``test_adapter_header.py`` because these cover a different question.
That file asks "what does this adapter say about itself"; this one asks "what
*is* this file, and what do we call it when it will not say".

The rule under test throughout: **``unknown`` is never upgraded to
``checkpoint``.** A marker-free file is a checkpoint only when it is large
enough to be one. Anything smaller is most likely an adapter format we have not
met yet, and calling it a checkpoint would put it in the wrong list with no way
for the user to see why.
"""

import json
import struct

import pytest

from pixlstash.utils.adapter_header import (
    FILE_ADAPTER,
    FILE_CHECKPOINT,
    FILE_TEXT_ENCODER,
    FILE_UNKNOWN,
    FILE_VAE,
    KIND_UNKNOWN,
    classify_model_file,
    count_parameters,
    describe_adapter,
    has_adapter_markers,
)
from pixlstash.utils.model_utils import clean_asset_name, derive_model_name


def _write_safetensors(path, tensors, metadata=None):
    """Write a syntactically valid safetensors file with no tensor payload.

    Only the header is ever read, so the payload is omitted deliberately: it
    keeps the fixtures small and proves the reader never reaches past the
    header.
    """
    header = dict(tensors)
    if metadata is not None:
        header["__metadata__"] = metadata
    blob = json.dumps(header).encode("utf-8")
    path.write_bytes(struct.pack("<Q", len(blob)) + blob)
    return str(path)


def _tensor(shape):
    return {"dtype": "F16", "shape": list(shape), "data_offsets": [0, 0]}


class TestParameterCount:
    def test_sums_the_product_of_every_shape(self):
        header = {"a": _tensor([2, 3]), "b": _tensor([10])}
        assert count_parameters(header) == 16

    def test_ignores_the_metadata_entry(self):
        header = {"a": _tensor([4]), "__metadata__": {"format": "pt"}}
        assert count_parameters(header) == 4

    def test_a_scalar_tensor_counts_as_one(self):
        assert count_parameters({"s": _tensor([])}) == 1

    @pytest.mark.parametrize(
        "bad",
        [
            {"dtype": "F16"},  # no shape at all
            {"dtype": "F16", "shape": "3"},  # shape not a list
            {"dtype": "F16", "shape": [2, "x"]},  # non-integer dim
            {"dtype": "F16", "shape": [2, -1]},  # negative dim
        ],
    )
    def test_a_malformed_entry_contributes_nothing_rather_than_raising(self, bad):
        # This runs in the import path. A file we cannot measure is still a file
        # the user wants on the shelf.
        assert count_parameters({"good": _tensor([5]), "bad": bad}) == 5


class TestAdapterMarkers:
    def test_markers_are_found_by_name(self):
        assert has_adapter_markers(["blocks.0.lora_A.weight"])

    def test_a_marker_free_file_reports_no_markers(self):
        assert not has_adapter_markers(["model.diffusion_model.input_blocks.0.weight"])

    def test_non_string_keys_are_skipped(self):
        assert not has_adapter_markers([None, 42, ("tuple",)])


class TestFileClassification:
    def test_markers_make_it_an_adapter_at_any_size(self):
        # Positive evidence beats size: a tiny adapter is still an adapter.
        assert classify_model_file(["x.lora_A.weight"], param_count=1) == FILE_ADAPTER

    def test_markers_win_even_at_checkpoint_scale(self):
        assert (
            classify_model_file(["x.lora_A.weight"], param_count=12_000_000_000)
            == FILE_ADAPTER
        )

    def test_marker_free_and_large_is_a_checkpoint(self):
        assert (
            classify_model_file(["model.weight"], param_count=2_600_000_000)
            == FILE_CHECKPOINT
        )

    def test_marker_free_and_small_stays_unknown(self):
        # The regression that matters: this must NOT become a checkpoint. It is
        # far more likely an adapter format the marker table has not learned.
        assert (
            classify_model_file(["some.unrecognised.weight"], param_count=40_000_000)
            == FILE_UNKNOWN
        )

    def test_an_empty_file_is_unknown(self):
        assert classify_model_file([], param_count=0) == FILE_UNKNOWN


class TestTheFolderNamesTheRole:
    """The support kinds, which no header signal can assert.

    A VAE and a text encoder carry no marker, and their parameter counts land on
    both sides of the checkpoint threshold - a CLIP-L below it, a T5-XXL well
    above. The directory is what actually knows, because ComfyUI and the
    launchers that front it file models by role.
    """

    def test_a_vae_folder_names_a_vae_however_small(self):
        # Below the checkpoint threshold, and previously stored as `unknown`.
        assert (
            classify_model_file(
                ["decoder.conv_in.weight"],
                param_count=83_000_000,
                path="/models/VAE/sdxl_vae.safetensors",
            )
            == FILE_VAE
        )

    def test_a_text_encoder_folder_beats_the_checkpoint_threshold(self):
        # The regression that motivated the kind: a T5-XXL is 4.9 B parameters,
        # clears `_CHECKPOINT_MIN_PARAMS`, and was filed as a base model.
        assert (
            classify_model_file(
                ["encoder.block.0.layer.0.SelfAttention.q.weight"],
                param_count=4_890_000_000,
                path="/models/TextEncoders/t5xxl_fp16.safetensors",
            )
            == FILE_TEXT_ENCODER
        )

    @pytest.mark.parametrize(
        "folder", ["text_encoders", "TextEncoders", "text-encoders", "TEXTENCODERS"]
    )
    def test_spelling_and_case_of_one_folder_are_one_folder(self, folder):
        assert (
            classify_model_file(
                ["encoder.block.0.weight"],
                param_count=123_000_000,
                path=f"/models/{folder}/clip_l.safetensors",
            )
            == FILE_TEXT_ENCODER
        )

    def test_comfyui_spells_its_text_encoders_clip_too(self):
        assert (
            classify_model_file(
                ["text_model.encoder.layers.0.weight"],
                param_count=123_000_000,
                path="/ComfyUI/models/clip/clip_l.safetensors",
            )
            == FILE_TEXT_ENCODER
        )

    def test_markers_still_win_over_the_folder(self):
        # Positive evidence outranks a directory. Someone keeping a LoRA beside
        # their VAEs has a misfiled LoRA, not a VAE.
        assert (
            classify_model_file(
                ["x.lora_A.weight"],
                param_count=40_000_000,
                path="/models/VAE/mislaid.safetensors",
            )
            == FILE_ADAPTER
        )

    def test_a_lora_folder_names_nothing_so_a_big_file_is_still_a_checkpoint(self):
        # `loras` is deliberately absent from the table. All-in-one checkpoints
        # get dropped in LoRA folders, and the parameter count already catches
        # them - trusting the folder here would BREAK that.
        assert (
            classify_model_file(
                ["model.weight"],
                param_count=10_260_000_000,
                path="/models/Lora/everything_aio.safetensors",
            )
            == FILE_CHECKPOINT
        )

    @pytest.mark.parametrize("folder", ["ApproxVAE", "clip_vision", "Downloads"])
    def test_a_folder_that_names_no_role_leaves_the_answer_where_it_was(self, folder):
        # TAESD previews are not VAEs and a CLIP-Vision is not a text encoder,
        # despite sharing a word with one. Not recognising a folder must read as
        # "no evidence", never as "not a VAE".
        assert (
            classify_model_file(
                ["some.unrecognised.weight"],
                param_count=40_000_000,
                path=f"/models/{folder}/thing.safetensors",
            )
            == FILE_UNKNOWN
        )

    def test_no_path_at_all_leaves_the_old_behaviour_intact(self):
        assert (
            classify_model_file(["model.weight"], param_count=2_600_000_000)
            == FILE_CHECKPOINT
        )

    def test_a_bare_filename_has_no_folder_and_asserts_nothing(self):
        assert (
            classify_model_file(
                ["model.weight"], param_count=40_000_000, path="vae.safetensors"
            )
            == FILE_UNKNOWN
        )

    def test_only_the_files_own_folder_counts_not_an_ancestor(self):
        # A registered root called `vae` says nothing about a subdirectory of
        # checkpoints inside it.
        assert (
            classify_model_file(
                ["model.weight"],
                param_count=2_600_000_000,
                path="/models/vae/checkpoints/big.safetensors",
            )
            == FILE_CHECKPOINT
        )

    def test_describe_adapter_files_a_real_file_by_its_folder(self, tmp_path):
        folder = tmp_path / "TextEncoders"
        folder.mkdir()
        path = _write_safetensors(
            folder / "t5xxl_fp16.safetensors",
            {"encoder.block.0.weight": _tensor([4096, 4096])},
        )
        info = describe_adapter(str(path))
        assert info is not None
        assert info.file_kind == FILE_TEXT_ENCODER


class TestDescribeAdapterCarriesTheNewFields:
    def test_an_adapter_reports_is_adapter_and_its_kind(self, tmp_path):
        path = _write_safetensors(
            tmp_path / "a.safetensors",
            {"blocks.0.lora_A.weight": _tensor([32, 768])},
        )
        info = describe_adapter(path)
        assert info.is_adapter is True
        assert info.kind == "lora"
        assert info.file_kind == FILE_ADAPTER
        assert info.param_count == 32 * 768

    def test_an_unrecognised_adapter_is_still_an_adapter(self, tmp_path):
        # kind is unknown, is_adapter is false, and the file is small: exactly
        # the case that used to be indistinguishable from a checkpoint.
        path = _write_safetensors(
            tmp_path / "b.safetensors",
            {"blocks.0.some_future_format.weight": _tensor([8, 8])},
        )
        info = describe_adapter(path)
        assert info.kind == KIND_UNKNOWN
        assert info.is_adapter is False
        assert info.file_kind == FILE_UNKNOWN

    def test_a_large_marker_free_file_reports_checkpoint(self, tmp_path):
        path = _write_safetensors(
            tmp_path / "c.safetensors",
            {"model.diffusion_model.weight": _tensor([100_000, 20_000])},
        )
        info = describe_adapter(path)
        assert info.is_adapter is False
        assert info.file_kind == FILE_CHECKPOINT
        assert info.param_count == 2_000_000_000


class TestDerivedName:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("JimmyVehicle_000002750.safetensors", "JimmyVehicle"),
            ("ohwx_woman-step00004500.safetensors", "ohwx woman"),
            ("hmmotion_minimax-h3_epoch12.safetensors", "hmmotion minimax h3"),
            ("k3nk-13ep.safetensors", "k3nk"),
            ("ClementineDetailed.safetensors", "ClementineDetailed"),
        ],
    )
    def test_training_bookkeeping_is_dropped(self, filename, expected):
        assert derive_model_name(filename) == expected

    @pytest.mark.parametrize(
        "filename,expected",
        [
            ("portrait_mix_v2.safetensors", "portrait mix v2"),
            ("JimmyVehicle2.safetensors", "JimmyVehicle2"),
            ("sdxl_1.safetensors", "sdxl 1"),
        ],
    )
    def test_version_suffixes_survive(self, filename, expected):
        # A short trailing number is a version, not a step count. Stripping it
        # would merge JimmyVehicle and JimmyVehicle2, which are two different runs.
        assert derive_model_name(filename) == expected

    def test_repeated_suffixes_are_all_dropped(self):
        assert derive_model_name("subject_epoch3_000001500.safetensors") == "subject"

    def test_a_name_that_is_only_bookkeeping_derives_to_empty(self):
        # The caller decides what to show; the shelf renders "no name in file".
        assert derive_model_name("step00001500.safetensors") == ""

    def test_clean_asset_name_is_unchanged(self):
        # Guards the embedding path: this output is baked into stored vectors,
        # so derive_model_name layers on top rather than altering it.
        assert clean_asset_name("z_image_turbo_bf16.safetensors") == (
            "z image turbo bf16"
        )
        assert clean_asset_name("JimmyVehicle_000002750.safetensors") == (
            "JimmyVehicle 000002750"
        )
