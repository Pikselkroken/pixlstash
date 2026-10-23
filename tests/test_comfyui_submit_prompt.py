"""Payload tests for ``_submit_comfyui_prompt`` (issue #628).

The POST /prompt payload must NOT carry ``extra_data.extra_pnginfo.workflow``:
that PNG chunk is where the ComfyUI frontend stores the *UI* node graph, and
it feeds the chunk to ``loadGraphData`` unguarded when an image is dropped on
the canvas. Embedding the API-format graph there breaks drag-back-in. ComfyUI
writes the correct ``prompt`` chunk itself, so nothing needs to be embedded.
"""

import json as stdlib_json

import pytest

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

    def test_a_nan_is_changed_from_a_prompt_chunk_is_dropped(self, monkeypatch):
        # ComfyUI writes `is_changed: NaN` for a loader whose file it could not
        # hash, and requests refuses to serialise a NaN, so the run never left.
        sent = {}

        def strict_post(url, json=None, timeout=None):
            sent["body"] = stdlib_json.dumps(json, allow_nan=False)
            return _FakeResponse()

        monkeypatch.setattr(comfyui_service.requests, "post", strict_post)
        graph = {
            "81": {
                "class_type": "LoadImage",
                "inputs": {"image": "a.png"},
                "is_changed": float("nan"),
            }
        }
        comfyui_service._submit_comfyui_prompt("http://comfy.test", graph)
        assert stdlib_json.loads(sent["body"])["prompt"] == {
            "81": {"class_type": "LoadImage", "inputs": {"image": "a.png"}}
        }


# --- the runs in flight, and the one socket that follows them (#1862) --------

_REAL_LISTENER = comfyui_service._listen_for_progress
COMFY = "https://comfy.test"


def _progress(prompt_id, value, top=4) -> str:
    return stdlib_json.dumps(
        {
            "type": "progress",
            "data": {"prompt_id": prompt_id, "value": value, "max": top},
        }
    )


class _Upstream:
    """A ComfyUI socket that says *messages*, then calls *then* and is silent."""

    def __init__(self, messages, then):
        self.messages = list(messages)
        self.then = then
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True
        return False

    def recv(self, timeout=None):
        if self.messages:
            return self.messages.pop(0)
        self.then()
        raise TimeoutError


@pytest.fixture
def listening(monkeypatch):
    """The registries empty, the address already marked as listened on (so
    ``follow_run`` starts no thread: each test runs the loop itself), and no
    queue or sleep reaching a real ComfyUI or a real clock."""
    monkeypatch.setattr(comfyui_service, "_live_runs", {})
    monkeypatch.setattr(comfyui_service, "_listening", {COMFY})
    monkeypatch.setattr(comfyui_service, "_run_outcomes", {})
    monkeypatch.setattr(comfyui_service, "_note_running_prompts", lambda url: None)
    monkeypatch.setattr(comfyui_service.time, "sleep", lambda seconds: None)


def test_the_listener_reads_progress_until_no_run_is_in_flight(listening, monkeypatch):
    """One socket per ComfyUI, opened as the client every run is submitted
    under, up only while a run is in flight."""
    opened = []
    seen: dict = {}

    def all_heard():
        seen.update({run["prompt_id"]: run for run in comfyui_service.live_runs("lib")})
        comfyui_service.record_run_outcome("run-a", "completed")
        comfyui_service.record_run_outcome("run-b", "failed", message="boom")

    upstream = _Upstream(
        [
            b"\x00\x00\x00\x01 a preview",
            "not json",
            stdlib_json.dumps({"type": "status", "data": {"status": {}}}),
            stdlib_json.dumps(
                {"type": "executing", "data": {"prompt_id": "run-b", "node": "3"}}
            ),
            _progress("run-b", 3),
            # A step count no float holds, and a stranger's run: neither stops it.
            _progress("run-b", 10**400),
            _progress("stranger", 1),
        ],
        all_heard,
    )

    def fake_connect(url, **kwargs):
        opened.append(url)
        return upstream

    monkeypatch.setattr(comfyui_service, "ws_connect", fake_connect)
    comfyui_service.follow_run("run-a", COMFY, library_uuid="lib")
    comfyui_service.follow_run("run-b", COMFY, "Portrait", "wf-1", "lib")

    _REAL_LISTENER(COMFY)

    assert opened == [f"wss://comfy.test/ws?clientId={comfyui_service.RUN_CLIENT_ID}"]
    assert seen == {
        "run-a": {
            "run_id": None,
            "prompt_id": "run-a",
            "label": "ComfyUI",
            "workflow_id": None,
            "status": "queued",
            "percent": 0.0,
        },
        "run-b": {
            "run_id": None,
            "prompt_id": "run-b",
            "label": "Portrait",
            "workflow_id": "wf-1",
            "status": "running",
            "percent": 75.0,
        },
    }
    # Struck off, and only once its socket was closed: the next run starts a
    # listener of its own, and never beside this one.
    assert upstream.closed
    assert comfyui_service._listening == set()
    assert comfyui_service.live_runs("lib") == []


def test_a_run_queued_as_the_last_one_ends_keeps_the_same_listener(
    listening, monkeypatch
):
    """ComfyUI keys its clients by id, so two sockets under one would cost
    the second its messages when the first closes. A run queued while the
    listener is closing is followed by that thread, on a new socket."""
    sockets = []

    def first_ends_as_another_is_queued():
        comfyui_service.record_run_outcome("run-a", "completed")

    def second_ends():
        comfyui_service.record_run_outcome("run-b", "completed")

    def fake_connect(url, **kwargs):
        if not sockets:
            sockets.append(_Upstream([], first_ends_as_another_is_queued))
        else:
            # Queued after the first socket closed and before the listener
            # looked again: it is still listed as listening, so no thread.
            assert comfyui_service._listening == {COMFY}
            sockets.append(_Upstream([_progress("run-b", 1)], second_ends))
        return sockets[-1]

    real_still_listening = comfyui_service._still_listening

    def still_listening(base_url):
        if len(sockets) == 1 and sockets[0].closed and not comfyui_service._live_runs:
            # Still listed while its socket was closing, or this run would
            # start a second listener beside it.
            assert COMFY in comfyui_service._listening
            comfyui_service.follow_run("run-b", COMFY, library_uuid="lib")
        return real_still_listening(base_url)

    monkeypatch.setattr(comfyui_service, "ws_connect", fake_connect)
    monkeypatch.setattr(comfyui_service, "_still_listening", still_listening)
    comfyui_service.follow_run("run-a", COMFY, library_uuid="lib")

    _REAL_LISTENER(COMFY)

    assert len(sockets) == 2 and all(socket.closed for socket in sockets)
    assert comfyui_service._listening == set()


def test_the_listener_outlives_a_socket_it_cannot_open(listening, monkeypatch):
    """The socket is only the step progress: whatever goes wrong with it, the
    listener says so, tries again, and is struck off when it does stop."""
    attempts = []

    def fake_connect(url, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            raise TypeError("an argument this websockets does not take")
        if len(attempts) == 2:
            raise OSError("connection refused")
        return _Upstream(
            [_progress("run-a", 2)],
            lambda: comfyui_service.record_run_outcome("run-a", "completed"),
        )

    monkeypatch.setattr(comfyui_service, "ws_connect", fake_connect)
    comfyui_service.follow_run("run-a", COMFY, library_uuid="lib")
    _REAL_LISTENER(COMFY)
    assert len(attempts) == 3
    assert comfyui_service._listening == set()

    # And one that dies outright still leaves the address free for the next.
    comfyui_service._listening.add(COMFY)
    comfyui_service.follow_run("run-b", COMFY, library_uuid="lib")
    monkeypatch.setattr(
        comfyui_service,
        "ws_connect",
        lambda url, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    with pytest.raises(KeyboardInterrupt):
        _REAL_LISTENER(COMFY)
    assert comfyui_service._listening == set()


def test_a_run_already_started_is_read_off_the_queue(monkeypatch):
    """The run that starts the listener was submitted before its socket was
    open, so what ComfyUI said of it then is read from the queue."""
    monkeypatch.setattr(comfyui_service, "_live_runs", {})
    monkeypatch.setattr(comfyui_service, "_listening", {COMFY})
    monkeypatch.setattr(comfyui_service, "_run_outcomes", {})

    class _Queue:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "queue_running": [[0, "run-a", {}]],
                "queue_pending": [[1, "run-b", {}]],
            }

    asked = []
    monkeypatch.setattr(
        comfyui_service.requests,
        "get",
        lambda url, timeout=None: asked.append(url) or _Queue(),
    )
    comfyui_service.follow_run("run-a", COMFY, library_uuid="lib")
    comfyui_service.follow_run("run-b", COMFY, library_uuid="lib")
    comfyui_service._note_running_prompts(COMFY)
    assert asked == [f"{COMFY}/queue"]
    assert {
        run["prompt_id"]: run["status"] for run in comfyui_service.live_runs("lib")
    } == {
        "run-a": "running",
        "run-b": "queued",
    }


def test_a_run_still_going_keeps_its_run_id_past_newer_ended_runs(monkeypatch):
    monkeypatch.setattr(comfyui_service, "MAX_RUN_OUTCOMES", 2)
    for name in ("_run_outcomes", "_run_ids", "_live_runs", "_held_cancels"):
        monkeypatch.setattr(comfyui_service, name, {})
    monkeypatch.setattr(comfyui_service, "_listening", {COMFY})
    comfyui_service.follow_run("p-long", COMFY, run_id="run-long")
    for n in (1, 2):
        comfyui_service.follow_run(f"p-{n}", COMFY, run_id=f"run-{n}")
        comfyui_service.record_run_outcome(f"p-{n}", "completed")
    comfyui_service.follow_run("p-3", COMFY, run_id="run-3")
    assert comfyui_service.run_outcome("run-long")["status"] == "running"
    assert comfyui_service.run_outcome("run-3")["status"] == "running"
    assert len(comfyui_service._run_ids) == 2


def test_following_a_run_never_drops_its_own_run_id(monkeypatch):
    for name in ("_run_outcomes", "_run_ids", "_live_runs", "_held_cancels"):
        monkeypatch.setattr(comfyui_service, name, {})
    monkeypatch.setattr(comfyui_service, "_listening", {COMFY})
    comfyui_service.follow_run("p-a", COMFY, run_id="run-a")
    comfyui_service.follow_run("p-b", COMFY, run_id="run-b")
    monkeypatch.setattr(comfyui_service, "MAX_RUN_OUTCOMES", 1)
    # Followed again, so first in the map, with every run still going.
    comfyui_service.follow_run("p-a2", COMFY, run_id="run-a")
    assert comfyui_service._run_ids == {"run-a": "p-a2"}
