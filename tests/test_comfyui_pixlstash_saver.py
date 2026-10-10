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

import pytest

import pixlstash.services.comfyui_service as comfyui_service
from pixlstash.services import workflow_run_service
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


def _node(class_type: str, **inputs) -> dict:
    return {"class_type": class_type, "inputs": inputs}


def _why(graph: dict, **run) -> dict:
    """``{node_id: why}`` for every node the policy refuses."""
    return {
        entry["node_id"]: entry["why"]
        for entry in comfyui_service.pixlstash_node_refusals(graph, **run)
    }


# The strictest run and the most permissive one. A node allowed under STRICT is
# allowed everywhere; one refused under OPEN is refused everywhere.
STRICT: dict = {}
OPEN = {
    "library_ids": {
        "project": {3: "Holiday"},
        "set": {4: "Holiday"},
        "character": {5: "Holiday"},
    },
    "picture_loader": True,
    "from_file": True,
}


class TestNodePolicy:
    """Each ComfyUI-PixlStash class has its own answer (#1521), both ways."""

    def test_digest_loaders_searches_and_gates_run_everywhere(self):
        for cls in (
            "PixlStashAdapterLoader",
            "PixlStashVAELoader",
            "PixlStashCLIPLoader",
            "PixlStashLikenessSearch",
            "PixlStashSemanticSearch",
            "PixlStashFaceLikenessGate",
            "PixlStashPictureLikenessGate",
        ):
            assert _why({"9": _node(cls)}, **STRICT) == {}, cls

    def test_a_library_loader_runs_when_its_id_is_in_this_library(self):
        for cls, field, kind, library_id in (
            ("PixlStashProjectLoader", "pixlstash_project", "project", 3),
            ("PixlStashSetLoader", "pixlstash_set", "set", 4),
            ("PixlStashCharacterLoader", "pixlstash_character", "character", 5),
        ):
            graph = {"9": _node(cls, **{field: f"Holiday #{library_id}"})}
            assert comfyui_service.library_ids_named(graph) == {kind: {library_id}}
            assert _why(graph, library_ids={kind: {library_id: "Holiday"}}) == {}
            # The same id under another kind is no answer, and nor is the same
            # id under another name: ids start at 1 in every library.
            other = "set" if kind != "set" else "project"
            for present in (
                {other: {library_id: "Holiday"}},
                {kind: {library_id: "Portraits"}},
            ):
                refused = comfyui_service.pixlstash_node_refusals(
                    graph, library_ids=present
                )
                assert [(e["why"], e["kind"], e["id"]) for e in refused] == [
                    ("not_in_library", kind, library_id)
                ], (cls, present)

    def test_a_library_loader_with_no_choice_names_nothing_to_check(self):
        graph = {"9": _node("PixlStashSetLoader", pixlstash_set="(loading…)")}
        assert comfyui_service.library_ids_named(graph) == {}
        assert _why(graph, **STRICT) == {}

    def test_a_wired_library_choice_cannot_be_checked_so_is_refused(self):
        graph = {"9": _node("PixlStashProjectLoader", pixlstash_project=["1", 0])}
        assert _why(graph, **OPEN) == {"9": "unreadable_id"}

    def test_the_picture_loader_runs_only_where_the_run_feeds_it(self):
        graph = {"9": _node("PixlStashPictureLoader", picture_ids="1,2")}
        assert _why(graph, picture_loader=True) == {}
        assert _why(graph, **{**OPEN, "picture_loader": False}) == {
            "9": "picks_its_own_picture"
        }
        # The run's half: a loader it did not write ids into is refused.
        assert [
            e["why"] for e in comfyui_service.unfed_picture_loaders(graph, set())
        ] == ["picks_its_own_picture"]
        assert comfyui_service.unfed_picture_loaders(graph, {"9"}) == []

    def test_the_checkpoint_loader_runs_only_from_a_stored_file(self):
        graph = {"9": _node("PixlStashCheckpointLoader", checkpoint_id="12")}
        assert _why(graph, from_file=True) == {}
        assert _why(graph, **{**OPEN, "from_file": False}) == {
            "9": "per_hub_checkpoint"
        }

    def test_the_saver_is_refused_until_swapped_and_runs_as_save_image(self):
        graph = {
            "9": {
                **_node(
                    "PixlStashPictureSaver",
                    images=["3", 0],
                    filename_prefix="v",
                    save_workflow=True,
                    pixlstash_set=["8", 1],
                ),
                "_meta": {"title": "Keep"},
            }
        }
        assert _why(graph, **OPEN) == {"9": "imports_itself"}
        assert comfyui_service.swap_pixlstash_savers(graph) == ["9"]
        assert graph["9"] == {
            "class_type": "SaveImage",
            "inputs": {"images": ["3", 0], "filename_prefix": "v"},
            "_meta": {"title": "Keep"},
        }
        assert _why(graph, **STRICT) == {}

    def test_a_saver_whose_output_is_read_is_not_swapped(self):
        # SaveImage has no output to hand on, so the swap would break the link.
        graph = {
            "9": _node("PixlStashPictureSaver", images=["3", 0]),
            "10": _node("ShowText", text=["9", 0]),
        }
        assert comfyui_service.swap_pixlstash_savers(graph) == []
        assert _why(graph, **OPEN) == {"9": "imports_itself"}

    def test_a_saver_with_no_images_is_not_swapped(self):
        graph = {"9": _node("PixlStashPictureSaver", filename_prefix="v")}
        assert comfyui_service.swap_pixlstash_savers(graph) == []
        assert _why(graph, **OPEN) == {"9": "imports_itself"}

    def test_a_pack_node_without_an_entry_stays_refused(self):
        graph = {"9": _node("PixlStashSomethingNew")}
        assert _why(graph, **OPEN) == {"9": "no_policy"}

    def test_an_ordinary_graph_and_a_lookalike_are_not_pack_nodes(self):
        graph = {
            "3": _node("KSampler"),
            "8": _node("NotPixlStashSaver"),
            "9": _node("SaveImage"),
        }
        assert _why(graph, **STRICT) == {}
        assert comfyui_service.swap_pixlstash_savers(graph) == []


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
    def _run(
        self, monkeypatch, images, pixlstash_ids, imported=(), duplicates=(), **kwargs
    ):
        server = _FakeServer()
        calls = {
            "downloads": 0,
            "stacked": None,
            "sourced": None,
            "filed": None,
            "person": None,
        }

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
            lambda *a, **kw: (list(imported), list(duplicates)),
        )
        monkeypatch.setattr(
            comfyui_service,
            "_assign_outputs_to_lora_person",
            lambda srv, person, ids: calls.__setitem__("person", (person, ids)),
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
        monkeypatch.setattr(
            comfyui_service,
            "_set_run_workflow_id",
            lambda srv, workflow_id, ids, version=None: calls.__setitem__(
                "filed", (workflow_id, ids, version)
            ),
        )

        comfyui_service._process_comfyui_outputs(
            server, "http://comfy", "prompt-1", ["9"], 7, None, **kwargs
        )
        return server, calls

    @staticmethod
    def _ending(event):
        """``(run_id, status)`` of a ``PLUGIN_PROGRESS`` event."""
        event_type, payload = event
        assert event_type == EventType.PLUGIN_PROGRESS
        return payload["run_id"], payload["status"]

    def test_a_manual_workflows_run_files_what_it_made_on_it(self, monkeypatch):
        """Imported and saver-reported pictures both; a plain run files none."""
        _server, calls = self._run(
            monkeypatch,
            images=[{"filename": "out_00001.png", "subfolder": "", "type": "output"}],
            pixlstash_ids=[41],
            imported=[40],
            run_workflow_id="manual:" + "a" * 32,
            run_workflow_version=3,
        )
        # With the version of its document the run submitted.
        assert calls["filed"] == ("manual:" + "a" * 32, [40, 41], 3)
        _server, calls = self._run(
            monkeypatch, images=[], pixlstash_ids=[41], imported=[]
        )
        assert calls["filed"] is None

    def test_what_the_run_made_is_of_the_person_whose_lora_it_loaded(self, monkeypatch):
        """Imported and saver-reported both; a duplicate keeps the people it has."""
        _server, calls = self._run(
            monkeypatch,
            images=[{"filename": "out_00001.png", "subfolder": "", "type": "output"}],
            pixlstash_ids=[41],
            imported=[40],
            duplicates=[39],
            lora_character_id=5,
        )
        assert calls["person"] == (5, [40, 41])
        _server, calls = self._run(
            monkeypatch, images=[], pixlstash_ids=[41], imported=[]
        )
        assert calls["person"] == (None, [41])

    def test_ids_reported_by_the_saver_are_stacked_and_announced(self, monkeypatch):
        server, calls = self._run(
            monkeypatch,
            images=[],
            pixlstash_ids=[41, 42],
        )
        assert calls["downloads"] == 0
        assert calls["stacked"] == (7, [41, 42])
        assert calls["sourced"] == (None, [41, 42])
        # The import, then the end of the run: the order a tab following the
        # run needs, so its row leaves once the pictures are there.
        assert server.vault.events[0] == (
            EventType.PICTURE_IMPORTED,
            {"ids": [41, 42], "source": "ui", "change_kind": "added"},
        )
        assert [self._ending(event) for event in server.vault.events[1:]] == [
            ("comfyui-prompt-1", "completed")
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
        # The run still ended, and its row must not wait for a picture that
        # is never coming.
        assert [self._ending(event) for event in server.vault.events] == [
            ("comfyui-prompt-1", "completed")
        ]

    def test_a_genuinely_empty_run_still_reports_failure(self, monkeypatch):
        # No saver ran and nothing was written: the pre-existing failure path
        # must not be swallowed by the new "ids is not None" branch.
        server, _calls = self._run(monkeypatch, images=[], pixlstash_ids=None)
        assert [event for event, _payload in server.vault.events] == [
            EventType.PLUGIN_PROGRESS
        ]
        assert self._ending(server.vault.events[0]) == ("comfyui-prompt-1", "failed")

    def test_an_empty_run_reports_the_outputs_comfyui_dropped(self, monkeypatch):
        # ComfyUI accepted the prompt but dropped the save output at validation;
        # its reason, not "finished without outputs", is what the owner sees.
        server, _calls = self._run(
            monkeypatch,
            images=[],
            pixlstash_ids=None,
            rejected="KSamplerAdvanced (node 94): sampler_name: 'res_2s' not in []",
        )
        assert server.vault.events[0][1]["message"] == (
            "KSamplerAdvanced (node 94): sampler_name: 'res_2s' not in []"
        )

    def test_a_finished_run_with_no_outputs_stops_waiting(self, monkeypatch):
        monkeypatch.setattr(
            comfyui_service, "_fetch_comfyui_history", lambda *a: _history({})
        )
        monkeypatch.setattr(
            comfyui_service.time,
            "sleep",
            lambda _s: (_ for _ in ()).throw(AssertionError("kept polling")),
        )
        assert comfyui_service._wait_for_comfyui_outputs(
            "http://comfy", "prompt-1", ["9"]
        ) == ([], None)


class TestVideoSavers:
    """A video saver's file is collected as a ``SaveImage``'s is."""

    def test_a_video_saver_is_the_output_node_and_a_preview_is_not(self):
        graph = {
            "7": _node("SaveVideo", video=["6", 0], filename_prefix="video/out"),
            "8": _node("PreviewImage", images=["5", 0]),
        }
        assert comfyui_service._extract_output_node_ids(graph, {}) == ["7"]

    def test_a_saved_video_is_listed_for_download(self):
        # What ComfyUI's history holds for SaveVideo: the file under `images`.
        payload = _history(
            {
                "7": {
                    "images": [
                        {
                            "filename": "out_00001_.mp4",
                            "subfolder": "video",
                            "type": "output",
                        }
                    ],
                    "animated": [True],
                }
            }
        )
        assert comfyui_service._extract_comfyui_output_images(
            payload, "prompt-1", ["7"]
        ) == [{"filename": "out_00001_.mp4", "subfolder": "video", "type": "output"}]

    def test_a_video_helper_suite_file_is_listed_for_download(self):
        # VHS_VideoCombine reports under `gifs`, with fields of its own.
        payload = _history(
            {
                "7": {
                    "gifs": [
                        {
                            "filename": "AnimateDiff_00001.mp4",
                            "subfolder": "",
                            "type": "output",
                            "format": "video/h264-mp4",
                        }
                    ]
                }
            }
        )
        assert comfyui_service._extract_comfyui_output_images(
            payload, "prompt-1", ["7"]
        ) == [{"filename": "AnimateDiff_00001.mp4", "subfolder": "", "type": "output"}]

    def test_a_video_helper_suite_preview_is_not_collected(self):
        # `save_output` off: a `temp` file its author chose not to keep. The
        # SaveImage beside it is still the run's output.
        payload = _history(
            {
                "7": {"gifs": [{"filename": "AnimateDiff_00001.mp4", "type": "temp"}]},
                "8": {"images": [{"filename": "out_00001_.png"}]},
            }
        )
        images = comfyui_service._extract_comfyui_output_images(
            payload, "prompt-1", ["7", "8"]
        )
        assert [image["filename"] for image in images] == ["out_00001_.png"]

    def test_an_animation_keeps_its_frames_and_a_video_latent_is_one_video(self):
        # AnimateDiff's frames are a batch of pictures; pinned to 1, the run
        # would save a one-frame video. A video latent counts frames in
        # `length`, so its batch is still how many videos.
        graph = {
            "1": _node("EmptyLatentImage", width=512, height=512, batch_size=16),
            "2": _node("EmptyHunyuanLatentVideo", length=33, batch_size=2),
            "7": _node("VHS_VideoCombine", images=["1", 0], filename_prefix="out"),
        }
        assert workflow_run_service.pin_batch_size(graph) == ["2"]
        assert graph["1"]["inputs"]["batch_size"] == 16
        assert graph["2"]["inputs"]["batch_size"] == 1
        # Saving pictures, the same batch is how many pictures: pinned.
        graph["7"] = _node("SaveImage", images=["1", 0], filename_prefix="out")
        assert workflow_run_service.pin_batch_size(graph) == ["1"]


class TestWaitingOutARun:
    """The budget is for a prompt ComfyUI has let go of, not for the run."""

    @staticmethod
    def _clock(monkeypatch, histories, queued):
        """Each poll advances a fake clock past the budget; *histories* are
        the history answers in order, *queued* the queue's answers in order."""
        now = [0.0]
        monkeypatch.setattr(comfyui_service.time, "time", lambda: now[0])
        monkeypatch.setattr(
            comfyui_service.time,
            "sleep",
            lambda _s: now.__setitem__(0, now[0] + 400),
        )
        asked = []

        def fake_queued(base_url, prompt_id):
            asked.append(prompt_id)
            return queued.pop(0)

        monkeypatch.setattr(comfyui_service, "_comfyui_prompt_queued", fake_queued)
        monkeypatch.setattr(
            comfyui_service, "_fetch_comfyui_history", lambda *a: histories.pop(0)
        )
        return asked

    def test_a_prompt_still_in_the_queue_is_waited_for_past_the_budget(
        self, monkeypatch
    ):
        done = _history({"7": {"images": [{"filename": "out_00001_.mp4"}]}})
        asked = self._clock(monkeypatch, [{}, {}, {}, done], [True, True, True])
        images, _ids = comfyui_service._wait_for_comfyui_outputs(
            "http://comfy", "prompt-1", ["7"]
        )
        assert [image["filename"] for image in images] == ["out_00001_.mp4"]
        assert asked == ["prompt-1"] * 3

    def test_a_prompt_comfyui_no_longer_has_is_a_stopped_run(self, monkeypatch):
        """In neither the queue nor the history: taken off the queue before
        its turn, or lost to a restart. Said so, not "finished"."""
        asked = self._clock(monkeypatch, [{}, {}], [False])
        with pytest.raises(comfyui_service.ComfyUIRunStopped, match="no longer has"):
            comfyui_service._wait_for_comfyui_outputs("http://comfy", "prompt-1", ["7"])
        assert asked == ["prompt-1"]

    def test_the_apps_own_abort_is_noticed_at_once(self, monkeypatch):
        """Abort clears ComfyUI's queue, and a prompt that was waiting in it
        never reaches the history: its poller asks the queue on its next pass
        rather than at its next check, so the run is said to have stopped
        before the owner has started the next one."""
        asked = []
        monkeypatch.setattr(
            comfyui_service,
            "_comfyui_prompt_queued",
            lambda *a: asked.append(1) or False,
        )
        monkeypatch.setattr(comfyui_service, "_fetch_comfyui_history", lambda *a: {})
        monkeypatch.setattr(
            comfyui_service.requests,
            "post",
            lambda *a, **k: type("R", (), {"status_code": 200, "text": ""})(),
        )
        # The first pass finds nothing and sleeps; the abort lands meanwhile.
        # A second sleep means the abort went unnoticed.
        slept = []

        def abort_while_asleep(_s):
            assert not slept, "kept waiting after the queue was cleared"
            slept.append(1)
            comfyui_service._comfyui_abort("http://comfy")

        monkeypatch.setattr(comfyui_service.time, "sleep", abort_while_asleep)
        with pytest.raises(comfyui_service.ComfyUIRunStopped):
            comfyui_service._wait_for_comfyui_outputs(
                "http://comfy", "prompt-1", None, timeout_s=3600
            )
        assert asked == [1]

    @pytest.mark.parametrize("status, cleared", [(200, True), (500, False)])
    def test_only_a_queue_that_was_cleared_sends_the_pollers_to_ask(
        self, monkeypatch, status, cleared
    ):
        """A clear ComfyUI refused emptied nothing, so there is nothing new
        for a poller to learn from the queue."""
        monkeypatch.setattr(
            comfyui_service.requests,
            "post",
            lambda *a, **k: type("R", (), {"status_code": status, "text": ""})(),
        )
        before = comfyui_service._queue_cleared
        assert (
            comfyui_service._comfyui_abort("http://comfy")["queue_cleared"] is cleared
        )
        assert (comfyui_service._queue_cleared != before) is cleared

    def test_a_clear_that_never_reached_comfyui_sends_nobody(self, monkeypatch):
        def refuse(*a, **k):
            raise comfyui_service.requests.ConnectionError("refused")

        monkeypatch.setattr(comfyui_service.requests, "post", refuse)
        before = comfyui_service._queue_cleared
        comfyui_service._comfyui_abort("http://comfy")
        assert comfyui_service._queue_cleared == before

    def test_a_prompt_in_the_history_with_no_ending_finishes_empty(self, monkeypatch):
        # ComfyUI still has it on record, so it ran: nothing to import, and
        # the caller says "finished without outputs".
        unfinished = {"prompt-1": {"outputs": {}}}
        self._clock(monkeypatch, [unfinished, unfinished], [False])
        assert comfyui_service._wait_for_comfyui_outputs(
            "http://comfy", "prompt-1", ["7"]
        ) == ([], None)

    def test_a_prompt_that_finishes_as_it_leaves_the_queue_is_collected(
        self, monkeypatch
    ):
        """The queue is asked BEFORE the history is read. The other way round,
        a prompt that finishes between the two reads is in neither answer and
        its video is never collected."""
        done = _history({"7": {"images": [{"filename": "out_00001_.mp4"}]}})
        asked = self._clock(monkeypatch, [], [False])
        # The prompt finishes at the moment the queue is asked about it.
        monkeypatch.setattr(
            comfyui_service,
            "_fetch_comfyui_history",
            lambda *a: done if asked else {},
        )
        images, _ids = comfyui_service._wait_for_comfyui_outputs(
            "http://comfy", "prompt-1", ["7"]
        )
        assert [image["filename"] for image in images] == ["out_00001_.mp4"]

    @pytest.mark.parametrize(
        "queue, held",
        [
            ({"queue_running": [[3, "prompt-1", {}]], "queue_pending": []}, True),
            ({"queue_running": [], "queue_pending": [[4, "prompt-1", {}]]}, True),
            ({"queue_running": [[3, "other", {}]], "queue_pending": []}, False),
            # Not an answer about the prompt at all: not known to be gone.
            ([], True),
        ],
    )
    def test_the_queue_is_read_for_the_prompt(self, monkeypatch, queue, held):
        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return queue

        monkeypatch.setattr(
            comfyui_service.requests, "get", lambda *a, **k: _Response()
        )
        assert (
            comfyui_service._comfyui_prompt_queued("http://comfy", "prompt-1") is held
        )

    def test_a_queue_that_cannot_be_read_does_not_abandon_the_run(self, monkeypatch):
        def refuse(*a, **k):
            raise comfyui_service.requests.ConnectionError("refused")

        monkeypatch.setattr(comfyui_service.requests, "get", refuse)
        assert (
            comfyui_service._comfyui_prompt_queued("http://comfy", "prompt-1") is True
        )


ERROR_EVENT = {
    "prompt_id": "prompt-1",
    "node_id": "12",
    "node_type": "UNETLoader",
    "exception_message": "'asym_w4a8_int8'",
    "exception_type": "KeyError",
    "traceback": ["..."],
}
TITLED_GRAPH = {
    "12": {
        "class_type": "UNETLoader",
        "inputs": {},
        "_meta": {"title": "Load Diffusion Model"},
    }
}


def _ended(event_name: str, event: dict, graph: dict | None = TITLED_GRAPH) -> dict:
    """A history answer for a prompt ComfyUI ended with *event_name*."""
    entry = {
        "outputs": {},
        "status": {
            "status_str": "error",
            "completed": False,
            "messages": [
                ["execution_start", {"prompt_id": "prompt-1"}],
                [event_name, event],
            ],
        },
    }
    if graph is not None:
        entry["prompt"] = [3, "prompt-1", graph, {}, ["9"]]
    return {"prompt-1": entry}


class TestHowARunEnded:
    """A run that ends without a picture says which node ended it and why."""

    def _read(self, *args, **kwargs):
        return comfyui_service._extract_history_status_and_error(
            _ended(*args, **kwargs), "prompt-1"
        )

    def test_a_failed_node_is_named_with_its_title_class_and_exception(self):
        # A KeyError's message is the bare key, which says nothing alone.
        assert self._read("execution_error", ERROR_EVENT) == (
            "error",
            "Load Diffusion Model (UNETLoader) failed: KeyError 'asym_w4a8_int8'",
        )

    @pytest.mark.parametrize(
        "event, graph, expected",
        [
            # A title that only repeats the class is said once.
            (
                ERROR_EVENT,
                {"12": {"class_type": "UNETLoader", "_meta": {"title": "UNETLoader"}}},
                "UNETLoader failed: KeyError 'asym_w4a8_int8'",
            ),
            # No graph on the history entry: the class the event names.
            (ERROR_EVENT, None, "UNETLoader failed: KeyError 'asym_w4a8_int8'"),
            # The event names no class: the graph's.
            (
                {**ERROR_EVENT, "node_type": None},
                TITLED_GRAPH,
                "Load Diffusion Model (UNETLoader) failed: KeyError 'asym_w4a8_int8'",
            ),
            # No node at all: the exception still carries its type.
            (
                {"exception_type": "KeyError", "exception_message": "'x'"},
                None,
                "KeyError 'x'",
            ),
            # One line, however ComfyUI wrapped it.
            (
                {**ERROR_EVENT, "exception_message": "CUDA out of memory.\n  Tried\n"},
                None,
                "UNETLoader failed: KeyError CUDA out of memory. Tried",
            ),
            # A graph that is not shaped like one is not read, and not raised on.
            (
                ERROR_EVENT,
                ["not", "a", "graph"],
                "UNETLoader failed: KeyError 'asym_w4a8_int8'",
            ),
        ],
    )
    def test_the_sentence_is_built_from_what_comfyui_gave(self, event, graph, expected):
        assert self._read("execution_error", event, graph)[1] == expected

    def test_a_long_exception_is_cut_to_what_a_card_can_carry(self):
        # A state_dict size mismatch lists every tensor.
        event = {**ERROR_EVENT, "exception_message": "size mismatch " * 200}
        _status, text = self._read("execution_error", event)
        assert text.startswith(
            "Load Diffusion Model (UNETLoader) failed: KeyError size"
        )
        assert text.endswith("…")
        prefix = len("Load Diffusion Model (UNETLoader) failed: ")
        assert len(text) - prefix == comfyui_service.MAX_FAILURE_REASON + 1

    def test_an_interrupt_is_a_stopped_run_and_names_where(self):
        """ComfyUI files an interrupt under status ``error``; it is told apart
        here, and never shown as the event's JSON."""
        event = {"prompt_id": "prompt-1", "node_id": "12", "node_type": "UNETLoader"}
        assert self._read("execution_interrupted", event) == (
            "interrupted",
            "Interrupted at Load Diffusion Model (UNETLoader)",
        )
        assert self._read("execution_interrupted", {"prompt_id": "prompt-1"}, None) == (
            "interrupted",
            "Interrupted",
        )

    def test_the_wait_tells_a_stopped_run_from_a_failed_one(self, monkeypatch):
        monkeypatch.setattr(
            comfyui_service,
            "_fetch_comfyui_history",
            lambda *a: _ended("execution_error", ERROR_EVENT),
        )
        with pytest.raises(RuntimeError, match="UNETLoader\\) failed") as failed:
            comfyui_service._wait_for_comfyui_outputs("http://comfy", "prompt-1", None)
        assert not isinstance(failed.value, comfyui_service.ComfyUIRunStopped)

        monkeypatch.setattr(
            comfyui_service,
            "_fetch_comfyui_history",
            lambda *a: _ended("execution_interrupted", {"node_type": "KSampler"}),
        )
        with pytest.raises(comfyui_service.ComfyUIRunStopped, match="at KSampler"):
            comfyui_service._wait_for_comfyui_outputs("http://comfy", "prompt-1", None)

    def test_comfyui_going_away_says_so(self, monkeypatch):
        def refuse(*a, **k):
            raise comfyui_service.requests.ConnectionError("refused")

        monkeypatch.setattr(comfyui_service.requests, "get", refuse)
        with pytest.raises(comfyui_service.HTTPException) as gone:
            comfyui_service._fetch_comfyui_history("http://comfy", "prompt-1")
        assert gone.value.detail == "ComfyUI stopped answering before the run finished"


class TestTheEndingIsToldAndKept:
    """Each ending reaches the tab as an event and is kept for the route."""

    def _end(self, monkeypatch, prompt_id, wait, **kwargs):
        server = _FakeServer()
        monkeypatch.setattr(comfyui_service, "_wait_for_comfyui_outputs", wait)
        comfyui_service._process_comfyui_outputs(
            server, "http://comfy", prompt_id, None, None, None, **kwargs
        )
        (event,) = server.vault.events
        return event[1], comfyui_service.run_outcome(prompt_id)

    @staticmethod
    def _raises(exc):
        def wait(*a, **k):
            raise exc

        return wait

    def test_a_failure_names_its_workflow_and_is_not_a_stopped_run(self, monkeypatch):
        payload, outcome = self._end(
            monkeypatch,
            "ending-failed",
            self._raises(RuntimeError("UNETLoader failed: KeyError 'x'")),
            workflow_id="manual:" + "a" * 32,
        )
        assert (
            payload["status"],
            payload["message"],
            payload["workflow_id"],
            payload["stopped"],
        ) == ("failed", "UNETLoader failed: KeyError 'x'", "manual:" + "a" * 32, False)
        assert outcome == {
            "prompt_id": "ending-failed",
            "status": "failed",
            "message": "UNETLoader failed: KeyError 'x'",
            "stopped": False,
            "picture_ids": [],
            "library_uuid": None,
        }

    def test_a_stopped_run_is_marked_stopped(self, monkeypatch):
        payload, outcome = self._end(
            monkeypatch,
            "ending-stopped",
            self._raises(comfyui_service.ComfyUIRunStopped("Interrupted at KSampler")),
        )
        assert (payload["status"], payload["stopped"]) == ("failed", True)
        assert (outcome["status"], outcome["stopped"], outcome["message"]) == (
            "failed",
            True,
            "Interrupted at KSampler",
        )

    def test_comfyui_going_away_is_told_in_words_not_a_status_code(self, monkeypatch):
        # str() of an HTTPException is "502: ..."; the detail is the sentence.
        payload, outcome = self._end(
            monkeypatch,
            "ending-gone",
            self._raises(
                comfyui_service.HTTPException(
                    status_code=502,
                    detail="ComfyUI stopped answering before the run finished",
                )
            ),
        )
        assert payload["message"] == "ComfyUI stopped answering before the run finished"
        assert outcome["message"] == payload["message"] and not outcome["stopped"]

    def test_a_finished_run_is_kept_with_the_pictures_it_added(self, monkeypatch):
        monkeypatch.setattr(
            comfyui_service, "_set_source_picture_id_on_pictures", lambda *a: None
        )
        monkeypatch.setattr(
            comfyui_service, "_copy_set_and_project_assignments", lambda *a: None
        )
        server = _FakeServer()
        monkeypatch.setattr(
            comfyui_service, "_wait_for_comfyui_outputs", lambda *a, **k: ([], [41, 42])
        )
        comfyui_service._process_comfyui_outputs(
            server,
            "http://comfy",
            "ending-done",
            None,
            None,
            None,
            workflow_id="auto:x",
        )
        assert server.vault.events[-1][1]["workflow_id"] == "auto:x"
        assert comfyui_service.run_outcome("ending-done") == {
            "prompt_id": "ending-done",
            "status": "completed",
            "message": None,
            "stopped": False,
            "picture_ids": [41, 42],
            "library_uuid": None,
        }

    def test_only_the_newest_outcomes_are_kept(self, monkeypatch):
        monkeypatch.setattr(comfyui_service, "MAX_RUN_OUTCOMES", 2)
        monkeypatch.setattr(comfyui_service, "_run_outcomes", {})
        for prompt_id in ("kept-1", "kept-2", "kept-3"):
            comfyui_service.record_run_outcome(prompt_id, "running")
        # Nothing has ended, so the oldest goes.
        assert comfyui_service.run_outcome("kept-1") is None
        # An update replaces a run's row rather than adding one, and a run
        # that has ended goes before an older one that is still going.
        comfyui_service.record_run_outcome("kept-3", "completed")
        comfyui_service.record_run_outcome("kept-4", "running")
        assert comfyui_service.run_outcome("kept-3") is None
        assert comfyui_service.run_outcome("kept-2")["status"] == "running"
        assert comfyui_service.run_outcome("kept-4")["status"] == "running"
        assert comfyui_service.run_outcome("never-run") is None

    def test_a_runs_library_is_kept_through_its_ending(self):
        comfyui_service.record_run_outcome("lib-run", "running", library_uuid="lib-a")
        comfyui_service.record_run_outcome("lib-run", "completed", picture_ids=[7])
        assert comfyui_service.run_outcome("lib-run")["library_uuid"] == "lib-a"

    def test_outputs_of_a_library_that_was_switched_away_are_kept_as_a_failure(
        self, monkeypatch
    ):
        """No event (the tab is on another library by then), but a reader of
        the run is not left with "running" for good."""

        class _Gone:
            def acquire_read(self):
                return None

        server = _FakeServer()
        server.library_coordinator = _Gone()
        monkeypatch.setattr(
            comfyui_service, "_wait_for_comfyui_outputs", lambda *a, **k: ([], [41])
        )
        comfyui_service._process_comfyui_outputs(
            server, "http://comfy", "ending-switched", None, None, None
        )
        assert server.vault.events == []
        outcome = comfyui_service.run_outcome("ending-switched")
        assert outcome["status"] == "failed" and "library changed" in outcome["message"]


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
