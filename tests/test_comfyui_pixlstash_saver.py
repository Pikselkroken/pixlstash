"""Handling of graphs that end in a ComfyUI-PixlStash saver node.

``PixlStashPictureSaver`` does not write a file for PixlStash to collect: it
uploads straight into the vault over the API and reports the picture ids it
created in its history entry, alongside ``type: "temp"`` previews of images
that are, by then, already imported.

Treating it as "no save node" made Generate Variants refuse the whole class of
workflows built around the node pack. Treating its previews as ordinary outputs
would re-download and re-import pictures the node had just imported, which
dedups to nothing and so loses the stack placement, the source lineage and the
import event. Both are covered here.
"""

import pixlstash.services.comfyui_service as comfyui_service
from pixlstash.event_types import EventType

SAVER_GRAPH = {
    "3": {"class_type": "KSampler", "inputs": {"seed": 1}},
    "9": {"class_type": "PixlStashPictureSaver", "inputs": {"filename_prefix": "v"}},
}


def _history(outputs: dict) -> dict:
    return {"prompt-1": {"outputs": outputs, "status": {"status_str": "success"}}}


class TestGraphInspection:
    def test_the_saver_counts_as_an_output_node(self):
        assert comfyui_service._extract_output_node_ids(SAVER_GRAPH, {}) == ["9"]

    def test_graph_has_pixlstash_saver(self):
        assert comfyui_service.graph_has_pixlstash_saver(SAVER_GRAPH) is True
        assert (
            comfyui_service.graph_has_pixlstash_saver(
                {"9": {"class_type": "SaveImage", "inputs": {}}}
            )
            is False
        )


class TestHistoryExtraction:
    def test_picture_ids_are_parsed_from_the_comma_joined_string(self):
        payload = _history(
            {
                "9": {
                    "images": [{"filename": "v_00001.png", "type": "temp"}],
                    "picture_ids": ["41,42"],
                }
            }
        )
        assert comfyui_service._extract_pixlstash_picture_ids(
            payload, "prompt-1", ["9"]
        ) == [41, 42]

    def test_no_saver_node_reports_none_not_an_empty_list(self):
        # None means "no PixlStash saver ran, import the images normally".
        payload = _history({"9": {"images": [{"filename": "out_00001.png"}]}})
        assert (
            comfyui_service._extract_pixlstash_picture_ids(payload, "prompt-1", None)
            is None
        )

    def test_a_saver_that_imported_nothing_new_reports_an_empty_list(self):
        # Every image was a duplicate of one already in the vault. Distinct from
        # None: there is still nothing to download.
        payload = _history(
            {
                "9": {
                    "images": [{"filename": "v_00001.png", "type": "temp"}],
                    "picture_ids": [""],
                }
            }
        )
        assert (
            comfyui_service._extract_pixlstash_picture_ids(payload, "prompt-1", None)
            == []
        )

    def test_saver_previews_are_not_offered_for_import(self):
        payload = _history(
            {
                "9": {
                    "images": [{"filename": "v_00001.png", "type": "temp"}],
                    "picture_ids": ["41"],
                }
            }
        )
        assert (
            comfyui_service._extract_comfyui_output_images(payload, "prompt-1", None)
            == []
        )

    def test_a_sibling_save_image_is_still_imported(self):
        # Mixed graph: the SaveImage output is ours to collect, the saver's is
        # not, and both have to end up in the same new_ids set downstream.
        payload = _history(
            {
                "8": {"images": [{"filename": "out_00001.png"}]},
                "9": {
                    "images": [{"filename": "v_00001.png", "type": "temp"}],
                    "picture_ids": ["41"],
                },
            }
        )
        images = comfyui_service._extract_comfyui_output_images(
            payload, "prompt-1", None
        )
        assert [img["filename"] for img in images] == ["out_00001.png"]


class _FakeVault:
    def __init__(self):
        self.events = []

    def notify(self, event_type, payload):
        self.events.append((event_type, payload))


class _FakeServer:
    def __init__(self):
        self.vault = _FakeVault()


class TestOutputProcessing:
    def _run(self, monkeypatch, images, pixlstash_ids, imported=()):
        server = _FakeServer()
        calls = {"downloads": 0, "stacked": None, "sourced": None}

        monkeypatch.setattr(
            comfyui_service,
            "_wait_for_comfyui_outputs",
            lambda *a, **kw: (images, pixlstash_ids),
        )

        def fake_download(base_url, entry):
            calls["downloads"] += 1
            return b"png-bytes", ".png"

        monkeypatch.setattr(comfyui_service, "_download_comfyui_image", fake_download)
        monkeypatch.setattr(
            comfyui_service,
            "_import_comfyui_outputs",
            lambda *a, **kw: (list(imported), []),
        )
        monkeypatch.setattr(
            comfyui_service,
            "_assign_outputs_to_stack_top",
            lambda srv, stack_id, ids: calls.__setitem__("stacked", (stack_id, ids)),
        )
        monkeypatch.setattr(
            comfyui_service,
            "_set_source_picture_id_on_pictures",
            lambda srv, src, ids: calls.__setitem__("sourced", (src, ids)),
        )
        monkeypatch.setattr(
            comfyui_service, "_copy_set_and_project_assignments", lambda *a, **kw: None
        )

        comfyui_service._process_comfyui_outputs(
            server, "http://comfy", "prompt-1", ["9"], 7, None
        )
        return server, calls

    def test_ids_reported_by_the_saver_are_stacked_and_announced(self, monkeypatch):
        server, calls = self._run(
            monkeypatch,
            images=[],
            pixlstash_ids=[41, 42],
        )
        assert calls["downloads"] == 0
        assert calls["stacked"] == (7, [41, 42])
        assert calls["sourced"] == (None, [41, 42])
        assert server.vault.events == [
            (
                EventType.PICTURE_IMPORTED,
                {"ids": [41, 42], "source": "ui", "change_kind": "added"},
            )
        ]

    def test_a_mixed_graph_merges_both_sets_of_ids(self, monkeypatch):
        _server, calls = self._run(
            monkeypatch,
            images=[{"filename": "out_00001.png", "subfolder": "", "type": "output"}],
            pixlstash_ids=[41],
            imported=[40],
        )
        assert calls["downloads"] == 1
        assert calls["stacked"] == (7, [40, 41])

    def test_a_saver_run_with_no_new_pictures_emits_no_import_event(self, monkeypatch):
        # All duplicates. Nothing to stack, nothing to announce, and crucially
        # not reported as "ComfyUI finished without outputs" either.
        server, calls = self._run(monkeypatch, images=[], pixlstash_ids=[])
        assert calls["downloads"] == 0
        assert calls["stacked"] is None
        assert server.vault.events == []

    def test_a_genuinely_empty_run_still_reports_failure(self, monkeypatch):
        # No saver ran and nothing was written: the pre-existing failure path
        # must not be swallowed by the new "ids is not None" branch.
        server, _calls = self._run(monkeypatch, images=[], pixlstash_ids=None)
        assert [event for event, _payload in server.vault.events] == [
            EventType.PLUGIN_PROGRESS
        ]
        assert server.vault.events[0][1]["status"] == "failed"


def demo() -> None:
    """Smoke the extraction split without pytest."""
    payload = _history(
        {
            "8": {"images": [{"filename": "out_00001.png"}]},
            "9": {
                "images": [{"filename": "v_00001.png", "type": "temp"}],
                "picture_ids": ["41,42"],
            },
        }
    )
    assert comfyui_service._extract_pixlstash_picture_ids(
        payload, "prompt-1", None
    ) == [
        41,
        42,
    ]
    assert [
        img["filename"]
        for img in comfyui_service._extract_comfyui_output_images(
            payload, "prompt-1", None
        )
    ] == ["out_00001.png"]
    print("ok")


if __name__ == "__main__":
    demo()
