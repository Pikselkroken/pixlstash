"""The DINOv3 ViT-B/16 anomaly tagger: building, loading and Grad-CAM.

A tiny random ViT stands in for the real 86M-parameter checkpoint; what is
under test is the module layout (the trainer's state-dict keys must load
strictly) and the token-to-grid reshape Grad-CAM depends on.
"""

import json

import pytest
import torch
from PIL import Image
from safetensors.torch import save_file
from torchvision import transforms

from pixlstash.utils.model_utils import MODEL_CONSTRUCTION_LOCK
from pixlstash.tagger_plugins.pixlstash_tagger import (
    PIXLSTASH_TAGGER_CONFIG_FILENAME,
    PIXLSTASH_TAGGER_DINOV3_ARCH,
    PIXLSTASH_TAGGER_FILENAME,
    PIXLSTASH_TAGGER_META_FILENAME,
    PixlStashTaggerService,
    _build_dinov3_tagger,
    _vit_patch_tokens_to_nchw,
)

LABELS = ["malformed hand", "waxy skin", "1girl"]

# The real dinov3_config.json, shrunk: same token layout (CLS + 4 registers,
# 16 px patches), a fraction of the width and depth.
TINY_CONFIG = {
    "model_type": "dinov3_vit",
    "hidden_size": 32,
    "intermediate_size": 64,
    "num_attention_heads": 2,
    "num_hidden_layers": 2,
    "num_register_tokens": 4,
    "patch_size": 16,
    "image_size": 64,
    "layerscale_value": 1.0,
    "pos_embed_rescale": 2.0,
    "rope_theta": 100.0,
    "key_bias": False,
    "query_bias": True,
    "value_bias": True,
    "proj_bias": True,
    "mlp_bias": True,
}


@pytest.fixture
def model_dir(tmp_path):
    """A model folder holding a random tiny ViT tagger as the downloader leaves it."""
    config_path = tmp_path / PIXLSTASH_TAGGER_CONFIG_FILENAME
    config_path.write_text(json.dumps(TINY_CONFIG))
    torch.manual_seed(0)
    reference = _build_dinov3_tagger(str(config_path), len(LABELS))
    save_file(reference.state_dict(), str(tmp_path / PIXLSTASH_TAGGER_FILENAME))
    (tmp_path / PIXLSTASH_TAGGER_META_FILENAME).write_text(
        json.dumps({"arch": PIXLSTASH_TAGGER_DINOV3_ARCH, "labels": LABELS})
    )
    return tmp_path, reference.eval()


def _service(model_dir) -> PixlStashTaggerService:
    service = PixlStashTaggerService(
        device="cpu", model_dir=str(model_dir), batch_size_fn=lambda: 2
    )
    service.init()
    return service


def test_the_trainers_state_dict_loads_strictly_and_scores_the_same(model_dir):
    folder, reference = model_dir
    keys = set(reference.state_dict())
    # pixltagger's layout; a renamed module would no longer load its checkpoints.
    for key in (
        "features.embeddings.patch_embeddings.weight",
        "features.embeddings.register_tokens",
        "features.model.layer.1.attention.q_proj.weight",
        "features.norm.weight",
        "classifier.0.weight",
        "classifier.2.weight",
    ):
        assert key in keys, key
    assert not any(k.startswith("classifier.1.") for k in keys)

    service = _service(folder)  # load_state_dict is strict
    x = torch.randn(2, 3, 64, 96)
    with torch.inference_mode():
        assert torch.equal(service._model(x), reference(x))
    assert service._model.classifier[2].out_features == len(LABELS)


def test_the_model_is_built_under_the_process_wide_construction_lock(
    model_dir, monkeypatch
):
    """A Transformers load elsewhere can switch construction to the meta device."""
    folder, _ = model_dir
    held = []
    real_build = PixlStashTaggerService._build_model

    def spy(self, arch, num_labels):
        held.append(MODEL_CONSTRUCTION_LOCK._is_owned())
        return real_build(self, arch, num_labels)

    monkeypatch.setattr(PixlStashTaggerService, "_build_model", spy)
    _service(folder)
    assert held == [True]


def test_a_missing_config_is_named_rather_than_crashing_in_transformers(model_dir):
    folder, _ = model_dir
    (folder / PIXLSTASH_TAGGER_CONFIG_FILENAME).unlink()
    service = PixlStashTaggerService(
        device="cpu", model_dir=str(folder), batch_size_fn=lambda: 2
    )
    assert service.needs_download()
    with pytest.raises(FileNotFoundError, match="DINOv3 config"):
        service.init()


def test_patch_tokens_land_on_their_grid_cell_for_a_non_square_input():
    skip, height, width, patch = 5, 2 * 16, 3 * 16, 16
    # Token i carries the value i in every channel.
    tokens = torch.arange(skip + 6, dtype=torch.float32).view(1, -1, 1).expand(1, -1, 4)
    grid = _vit_patch_tokens_to_nchw(tokens, skip, (height, width), patch)
    assert grid.shape == (1, 4, 2, 3)
    for r in range(2):
        for c in range(3):
            assert grid[0, 0, r, c].item() == skip + r * 3 + c


def test_gradcam_runs_on_a_non_square_token_grid(model_dir, monkeypatch):
    folder, _ = model_dir
    service = _service(folder)
    service._image_size_full = 64
    non_square = transforms.Compose(
        [
            transforms.Resize((48, 80)),
            transforms.ToTensor(),
        ]
    )
    monkeypatch.setattr(service, "_get_transform", lambda size: non_square)
    cam = service._compute_gradcam(Image.new("RGB", (120, 70), (200, 90, 40)), 0)
    assert cam.shape == (64, 64)
    # Normalised to its peak, so a dead hook (an all-zero map) fails here.
    assert float(cam.min()) == 0.0 and float(cam.max()) == pytest.approx(1.0)
    assert service._model.classifier[2].weight.grad is None  # zeroed afterwards
