"""Payload tests for ``_submit_comfyui_prompt`` (issue #628).

The POST /prompt payload must NOT carry ``extra_data.extra_pnginfo.workflow``:
that PNG chunk is where the ComfyUI frontend stores the *UI* node graph, and
it feeds the chunk to ``loadGraphData`` unguarded when an image is dropped on
the canvas. Embedding the API-format graph there breaks drag-back-in. ComfyUI
writes the correct ``prompt`` chunk itself, so nothing needs to be embedded.
"""

import pixlstash.services.comfyui_service as comfyui_service

WORKFLOW = {
    "3": {
        "class_type": "KSampler",
        "inputs": {"seed": 12345, "steps": 20, "model": ["4", 0]},
    },
    "4": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    # PixlStash-internal key: must be stripped before submission, because
    # ComfyUI iterates top-level entries as nodes and crashes on non-dicts.
    "pixlstash_output_nodes": ["3"],
}


class _FakeResponse:
    status_code = 200

    @staticmethod
    def json() -> dict:
        return {"prompt_id": "abc123"}


def _capture_submit_payload(monkeypatch, **submit_kwargs) -> dict:
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _FakeResponse()

    monkeypatch.setattr(comfyui_service.requests, "post", fake_post)
    result = comfyui_service._submit_comfyui_prompt(
        "http://comfy.test", WORKFLOW, **submit_kwargs
    )
    assert result == {"prompt_id": "abc123"}
    return captured


class TestSubmitPayload:
    def test_no_extra_pnginfo_workflow_is_embedded(self, monkeypatch):
        # The regression under test: the API graph must not be placed in the
        # PNG's ``workflow`` chunk via extra_pnginfo (issue #628).
        captured = _capture_submit_payload(monkeypatch)
        payload = captured["payload"]
        extra_pnginfo = payload.get("extra_data", {}).get("extra_pnginfo", {})
        assert "workflow" not in extra_pnginfo
        assert "extra_data" not in payload

    def test_prompt_carries_the_cleaned_workflow(self, monkeypatch):
        captured = _capture_submit_payload(monkeypatch)
        payload = captured["payload"]
        assert captured["url"] == "http://comfy.test/prompt"
        assert payload["prompt"] == {
            k: v for k, v in WORKFLOW.items() if not k.startswith("pixlstash_")
        }
        assert "pixlstash_output_nodes" not in payload["prompt"]

    def test_client_id_is_forwarded(self, monkeypatch):
        captured = _capture_submit_payload(monkeypatch, client_id="tab-1")
        assert captured["payload"]["client_id"] == "tab-1"
