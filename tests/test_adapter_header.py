"""Tests for reading a safetensors adapter header without loading it.

Fixtures are built here rather than committed: a real adapter is hundreds of
megabytes, and every property under test lives in the first few hundred bytes.
The shapes below are taken from real files measured on 2026-08-07 (ai-toolkit
output and a model-site download), not invented.
"""

import json
import struct

import pytest

from pixlstash.utils.adapter_header import (
    KIND_UNKNOWN,
    describe_adapter,
    detect_adapter_kind,
    read_safetensors_header,
)


def _write_safetensors(path, tensors, metadata=None, *, payload=b"", header_len=None):
    """Write a minimal safetensors file.

    Args:
        path: Destination path.
        tensors: Tensor names to declare.
        metadata: Optional ``__metadata__`` dict.
        payload: Bytes to append after the header, standing in for tensor data.
        header_len: Override the length prefix, to forge a corrupt file.
    """
    header = {
        name: {"dtype": "F16", "shape": [1], "data_offsets": [0, 2]} for name in tensors
    }
    if metadata is not None:
        header["__metadata__"] = metadata
    raw = json.dumps(header).encode()
    declared = len(raw) if header_len is None else header_len
    path.write_bytes(struct.pack("<Q", declared) + raw + payload)
    return path


# ── kind detection: the only signal present on every file ────────────────────


@pytest.mark.parametrize(
    "tensors, expected",
    [
        (
            [
                "diffusion_model.layers.0.attn.lora_A.weight",
                "diffusion_model.layers.0.attn.lora_B.weight",
            ],
            "lora",
        ),
        (["lora_unet_down.lora_up.weight", "lora_unet_down.lora_down.weight"], "lora"),
        (["lycoris.blocks.0.lokr_w1", "lycoris.blocks.0.lokr_w2"], "lokr"),
        (["lycoris.blocks.0.hada_w1_a", "lycoris.blocks.0.hada_w2_a"], "loha"),
        (["net.blocks.0.oft_blocks"], "oft"),
        (["model.embed.weight", "model.norm.bias"], KIND_UNKNOWN),
        ([], KIND_UNKNOWN),
    ],
)
def test_detect_adapter_kind_reads_tensor_names(tensors, expected):
    assert detect_adapter_kind(tensors) == expected


def test_dora_wins_over_lora_because_it_carries_both():
    """DoRA is LoRA plus a magnitude vector, so both markers are present.

    If the more specific marker did not win, every DoRA would be filed as a
    plain LoRA and the distinction would be unrecoverable from the shelf.
    """
    tensors = [
        "diffusion_model.layers.0.lora_A.weight",
        "diffusion_model.layers.0.lora_B.weight",
        "diffusion_model.layers.0.dora_scale",
    ]
    assert detect_adapter_kind(tensors) == "dora"


def test_detect_ignores_non_string_keys():
    assert detect_adapter_kind(["a.lora_A.weight", None, 7]) == "lora"


# ── header reading is bounded, because the file is untrusted ─────────────────


def test_reads_a_well_formed_header(tmp_path):
    path = _write_safetensors(
        tmp_path / "a.safetensors", ["x.lora_A.weight"], {"format": "pt"}
    )
    header = read_safetensors_header(str(path))
    assert header is not None
    assert header["__metadata__"] == {"format": "pt"}


def test_refuses_an_implausible_declared_header_length(tmp_path):
    """The length prefix is file-controlled; an 8-byte field can ask for 16 EiB."""
    path = _write_safetensors(
        tmp_path / "hostile.safetensors", ["x.lora_A.weight"], header_len=2**63
    )
    assert read_safetensors_header(str(path)) is None


def test_refuses_a_zero_length_header(tmp_path):
    path = _write_safetensors(tmp_path / "zero.safetensors", ["x"], header_len=0)
    assert read_safetensors_header(str(path)) is None


def test_truncated_header_is_rejected_not_raised(tmp_path):
    path = tmp_path / "short.safetensors"
    path.write_bytes(struct.pack("<Q", 4096) + b'{"a":')
    assert read_safetensors_header(str(path)) is None


def test_non_json_header_is_rejected_not_raised(tmp_path):
    path = tmp_path / "junk.safetensors"
    body = b"\x00\x01\x02\x03not json at all"
    path.write_bytes(struct.pack("<Q", len(body)) + body)
    assert read_safetensors_header(str(path)) is None


def test_header_that_is_json_but_not_an_object_is_rejected(tmp_path):
    path = tmp_path / "list.safetensors"
    body = b"[1, 2, 3]"
    path.write_bytes(struct.pack("<Q", len(body)) + body)
    assert read_safetensors_header(str(path)) is None


def test_file_too_short_for_a_prefix(tmp_path):
    path = tmp_path / "tiny.safetensors"
    path.write_bytes(b"\x00\x01")
    assert read_safetensors_header(str(path)) is None


def test_missing_file(tmp_path):
    assert read_safetensors_header(str(tmp_path / "nope.safetensors")) is None


def test_tensor_payload_is_never_read(tmp_path):
    """A huge payload must not be loaded: the shelf describes 800 MB files."""
    payload = b"\xab" * (4 * 1024 * 1024)
    path = _write_safetensors(
        tmp_path / "big.safetensors",
        ["x.lora_A.weight"],
        {"format": "pt"},
        payload=payload,
    )
    info = describe_adapter(str(path))
    assert info is not None and info.tensor_count == 1


# ── describe_adapter against the two shapes that actually occur ──────────────


def test_ai_toolkit_output_is_fully_described(tmp_path):
    """The rich case, mirroring a real ai-toolkit file."""
    metadata = {
        "format": "pt",
        "name": "JimmyVehicle",
        "software": json.dumps(
            {
                "name": "ai-toolkit",
                "repo": "https://github.com/ostris/ai-toolkit",
                "version": "0.9.11",
            }
        ),
        "training_info": json.dumps({"step": 3000, "epoch": 333}),
        "ss_base_model_version": "zimage",
        "ss_output_name": "JimmyVehicle",
        "ss_tag_frequency": json.dumps(
            {"1_jimmyvehicle": {"jimmyvehicle": 4, "portrait": 9}}
        ),
    }
    path = _write_safetensors(
        tmp_path / "jc.safetensors",
        [
            "diffusion_model.layers.0.lora_A.weight",
            "diffusion_model.layers.0.lora_B.weight",
        ],
        metadata,
    )
    info = describe_adapter(str(path))

    assert info.kind == "lora"
    assert info.tensor_count == 2
    assert info.base_model == "zimage"
    assert info.display_name == "JimmyVehicle"
    assert info.training_step == 3000
    assert info.training_epoch == 333
    assert info.trained_by == "ai-toolkit 0.9.11"
    assert info.has_metadata is True
    # Most frequent tag leads, so the real trigger is not buried.
    assert info.trigger_words == ["portrait", "jimmyvehicle"]


def test_downloaded_adapter_with_only_format_is_the_normal_case(tmp_path):
    """A model-site download routinely carries nothing but ``format``.

    One measured at 819 MB / 448 tensors with a single metadata key. It must
    still describe as an adapter, with everything else left unset so the shelf
    can tell "the file did not say" from "the value is empty".
    """
    path = _write_safetensors(
        tmp_path / "civitai.safetensors",
        ["diffusion_model.blocks.0.attn.gate.lora_A.weight"],
        {"format": "pt"},
    )
    info = describe_adapter(str(path))

    assert info.kind == "lora"
    assert info.has_metadata is False
    assert info.base_model is None
    assert info.display_name is None
    assert info.trigger_words == []
    assert info.training_step is None
    assert info.trained_by is None


def test_no_metadata_block_at_all(tmp_path):
    path = _write_safetensors(tmp_path / "bare.safetensors", ["x.lokr_w1"], None)
    info = describe_adapter(str(path))
    assert info.kind == "lokr"
    assert info.has_metadata is False


def test_base_model_is_free_text_not_an_enum(tmp_path):
    """Real values seen: zimage, krea2, minimax_h3. Not sdxl/sd15/flux."""
    for value in ("zimage", "krea2", "minimax_h3", "something-nobody-predicted"):
        path = _write_safetensors(
            tmp_path / f"{value}.safetensors",
            ["x.lora_A.weight"],
            {"format": "pt", "ss_base_model_version": value},
        )
        assert describe_adapter(str(path)).base_model == value


def test_malformed_metadata_values_degrade_instead_of_raising(tmp_path):
    """A hostile file must not break the import path, only under-describe."""
    metadata = {
        "format": "pt",
        "software": "{not json",
        "training_info": "[]",
        "ss_tag_frequency": "{{{",
        "ss_base_model_version": "",
    }
    path = _write_safetensors(
        tmp_path / "bad.safetensors", ["x.lora_A.weight"], metadata
    )
    info = describe_adapter(str(path))

    assert info.kind == "lora"
    assert info.trained_by is None
    assert info.training_step is None
    assert info.trigger_words == []
    assert info.base_model is None


def test_tag_frequency_with_unusable_counts_still_yields_tags(tmp_path):
    metadata = {
        "format": "pt",
        "ss_tag_frequency": json.dumps({"1_x": {"solo": "many"}}),
    }
    path = _write_safetensors(
        tmp_path / "tf.safetensors", ["x.lora_A.weight"], metadata
    )
    assert describe_adapter(str(path)).trigger_words == ["solo"]


def test_describe_returns_none_when_the_header_is_unreadable(tmp_path):
    path = tmp_path / "broken.safetensors"
    path.write_bytes(b"\x00")
    assert describe_adapter(str(path)) is None
