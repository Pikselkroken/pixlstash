"""ComfyUI templates against the shelf: the New workflow set picker (#2001)."""

from datetime import datetime, timedelta, timezone

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services import workflow_templates
from pixlstash.services.workflow_templates import template_rows
from pixlstash.utils.model_utils import quantless_name

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)

Z_TURBO = {
    "name": "image_z_image_turbo",
    "title": "Z-Image-Turbo: Text to Image",
    "kind": "image",
    "tags": ["Text to Image"],
    "models": ["Z-Image-Turbo"],
    "min_comfyui_version": "0.11.0",
    "size": 1,
    "usage": 10,
    "files": [
        {"name": "qwen_3_4b.safetensors", "url": "", "directory": "text_encoders"},
        {"name": "ae.safetensors", "url": "", "directory": "vae"},
        {
            "name": "z_image_turbo_bf16.safetensors",
            "url": "",
            "directory": "diffusion_models",
        },
    ],
}
# Shares Z_TURBO's head once the quant is gone: a variant of the same row.
Z_TURBO_INT8 = {
    **Z_TURBO,
    "name": "image_z_image_turbo_int8",
    "title": "Z-Image-Turbo int8",
    "usage": 99,
    "files": [
        {
            "name": "z_image_turbo_int8.safetensors",
            "url": "",
            "directory": "diffusion_models",
        },
        {"name": "qwen_3_4b.safetensors", "url": "", "directory": "text_encoders"},
        {"name": "ae.safetensors", "url": "", "directory": "vae"},
    ],
}
UPSCALE = {
    **Z_TURBO,
    "name": "utility_upscale",
    "title": "Upscale",
    "files": [{"name": "4x.pth", "url": "", "directory": "upscale_models"}],
}


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(
        workflow_templates,
        "load_templates",
        lambda: {"version": "test", "templates": [Z_TURBO, Z_TURBO_INT8, UPSCALE]},
    )
    database = HubDatabase(str(tmp_path / "hub.db"))
    with database.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (id, path, kind, movable, created_at) "
            "VALUES (1, '/home/me/models', 'user', 'per_item', '2026-10-01')"
        )
    yield database
    database.close()


def _model(hub, filename, kind="checkpoint", arrived_at=None, state="present"):
    with hub.transaction() as conn:
        model_id = conn.execute(
            "INSERT INTO model (file_kind, kind, sha256, filename, provenance, "
            "created_at, arrived_at) VALUES (?, NULL, ?, ?, 'external', "
            "'2026-10-01', ?)",
            (kind, None, filename, arrived_at),
        ).lastrowid
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, 1, ?, ?)",
            (model_id, f"{model_id}/{filename}", state),
        )
    return model_id


def _z_turbo(hub, head="z_image_turbo_bf16.safetensors", arrived=timedelta(hours=1)):
    return {
        "te": _model(hub, "qwen_3_4b.safetensors", "text_encoder"),
        "vae": _model(hub, "ae.safetensors", "vae"),
        "head": _model(
            hub, head, arrived_at=(NOW - arrived).isoformat() if arrived else None
        ),
    }


def _row(found, name="image_z_image_turbo"):
    for row in found["rows"]:
        for variant in row["variants"]:
            if variant["name"] == name:
                return row, variant
    raise AssertionError(f"{name} is in no row")


def _set(set_id, *model_ids, template=None):
    return {
        "id": set_id,
        "name": f"set {set_id}",
        "template": template,
        "members": [{"id": model_id, "on_shelf": True} for model_id in model_ids],
    }


def test_a_complete_template_whose_head_just_arrived_is_new(hub):
    ids = _z_turbo(hub)
    found = template_rows(hub, [], now=NOW)
    row, variant = _row(found)
    assert variant["complete"] and variant["on_shelf"] == 3
    assert variant["new"] and found["new_ready"] == 1
    files = {f["name"]: f for f in variant["files"]}
    assert files["z_image_turbo_bf16.safetensors"]["model_id"] == ids["head"]
    assert files["z_image_turbo_bf16.safetensors"]["slot"] == "checkpoint"
    assert files["qwen_3_4b.safetensors"]["slot"] == "text_encoder"
    assert files["ae.safetensors"]["slot"] == "vae"
    assert variant["covered_by"] is None


@pytest.mark.parametrize("arrived", [None, timedelta(hours=25)])
def test_a_scanned_or_old_head_is_ready_but_not_new(hub, arrived):
    """A first scan registers everything at once and stamps no arrival."""
    _z_turbo(hub, arrived=arrived)
    found = template_rows(hub, [], now=NOW)
    _, variant = _row(found)
    assert variant["complete"] and not variant["new"]
    assert found["new_ready"] == 0


def test_a_support_file_arriving_does_not_make_the_template_new(hub):
    """Only the head's arrival counts, or one VAE would offer fifty sets."""
    _model(hub, "qwen_3_4b.safetensors", "text_encoder", arrived_at=NOW.isoformat())
    _model(hub, "ae.safetensors", "vae", arrived_at=NOW.isoformat())
    _model(hub, "z_image_turbo_bf16.safetensors")
    assert template_rows(hub, [], now=NOW)["new_ready"] == 0


def test_another_quant_on_the_shelf_counts_and_says_which(hub):
    _z_turbo(hub, head="z_image_turbo_fp8_e4m3fn.safetensors")
    _, variant = _row(template_rows(hub, [], now=NOW))
    head = next(f for f in variant["files"] if f["directory"] == "diffusion_models")
    assert variant["complete"]
    assert head["other_quant"] == "z_image_turbo_fp8_e4m3fn.safetensors"


def test_a_missing_file_leaves_the_template_partial(hub):
    _model(hub, "z_image_turbo_bf16.safetensors")
    _model(hub, "ae.safetensors", "vae")
    _model(hub, "qwen_3_4b.safetensors", "text_encoder", state="missing")
    _, variant = _row(template_rows(hub, [], now=NOW))
    assert not variant["complete"] and variant["on_shelf"] == 2
    absent = [f for f in variant["files"] if not f["on_shelf"]]
    assert [f["name"] for f in absent] == ["qwen_3_4b.safetensors"]
    assert absent[0]["model_id"] is None


def test_templates_sharing_a_head_are_one_row_led_by_the_complete_one(hub):
    """The int8 variant is used more, but its head is not on the shelf."""
    _z_turbo(hub)
    found = template_rows(hub, [], now=NOW)
    row, _ = _row(found)
    assert [v["name"] for v in row["variants"]] == [
        "image_z_image_turbo",
        "image_z_image_turbo_int8",
    ]
    assert row is _row(found, "image_z_image_turbo_int8")[0]
    upscale_row, _ = _row(found, "utility_upscale")
    assert upscale_row["key"] == "template:utility_upscale"


def test_a_set_holding_its_files_covers_it(hub):
    ids = _z_turbo(hub)
    found = template_rows(hub, [_set(7, *ids.values(), 999)], now=NOW)
    _, variant = _row(found)
    assert variant["covered_by"] == {"id": 7, "name": "set 7"}
    assert not variant["new"] and found["new_ready"] == 0


def test_a_set_lacking_one_file_does_not_cover_it(hub):
    ids = _z_turbo(hub)
    _, variant = _row(template_rows(hub, [_set(7, ids["head"], ids["vae"])], now=NOW))
    assert variant["covered_by"] is None and variant["new"]


def test_a_set_made_from_the_template_covers_it_whatever_it_holds(hub):
    _z_turbo(hub)
    sets = [_set(3, template="image_z_image_turbo")]
    _, variant = _row(template_rows(hub, sets, now=NOW))
    assert variant["covered_by"] == {"id": 3, "name": "set 3"}


def test_a_namesake_in_the_set_is_the_file(hub):
    """Two shelf rows answer to one name; holding either holds the file."""
    ids = _z_turbo(hub)
    twin = _model(hub, "ae.safetensors", "vae")
    held = [ids["head"], ids["te"], twin]
    _, variant = _row(template_rows(hub, [_set(4, *held)], now=NOW))
    assert variant["covered_by"] == {"id": 4, "name": "set 4"}


def test_a_head_the_shelf_files_as_a_vae_joins_as_other(hub):
    """The create refuses a checkpoint slot for it, so it is not offered one."""
    _model(hub, "z_image_turbo_bf16.safetensors", "vae")
    _, variant = _row(template_rows(hub, [], now=NOW))
    head = next(f for f in variant["files"] if f["directory"] == "diffusion_models")
    assert head["slot"] == "other"


def test_quantless_name_folds_quant_extension_and_case():
    assert quantless_name("z_image_turbo_bf16.safetensors") == "z image turbo"
    assert quantless_name("Flux1-Dev-Q4_K_M.gguf") == quantless_name("flux1-dev.sft")
    assert quantless_name("ae.safetensors") == "ae"


def test_the_shipped_list_reads_and_every_template_names_files():
    listing = workflow_templates.load_templates.__wrapped__()
    assert listing["version"] and listing["templates"]
    for template in listing["templates"]:
        assert template["files"], template["name"]
        assert all(f["name"] and f["directory"] for f in template["files"])
