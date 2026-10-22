"""Model downloads: where they land, what is refused, and how they end."""

import contextlib
import gzip
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services import model_downloads
from pixlstash.services.model_downloads import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    DownloadRefused,
    cancel_download,
    download_source,
    live_downloads,
    start_download,
)
from pixlstash.services.model_folder_scanner import ModelFolderScanner
from pixlstash.services.model_mover import PARTIAL_SUFFIX, SHELF_IO_LOCK
from pixlstash.services.workflow_run_service import MISSING_MODELS, judge
from tests.test_downloads_intake import _register, write_adapter


@contextlib.contextmanager
def serve_bytes(
    body,
    content_type="application/octet-stream",
    gate=None,
    length=None,
    encoding=None,
    asked=None,
):
    """Serve *body* at a loopback URL; *gate*, when set, holds the second half.

    *length* is the Content-Length claimed, the body's own unless given;
    ``-1`` sends none. *encoding* is a Content-Encoding to claim, and *asked*,
    a list, collects each request's headers.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if asked is not None:
                asked.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            if encoding:
                self.send_header("Content-Encoding", encoding)
            if length != -1:
                self.send_header(
                    "Content-Length", str(len(body) if length is None else length)
                )
            self.end_headers()
            half = len(body) // 2
            self.wfile.write(body[:half])
            self.wfile.flush()
            if gate is not None:
                gate.wait(10)
            try:
                self.wfile.write(body[half:])
            except OSError as exc:
                # The client hung up on a cancel: expected here.
                print(f"serve_bytes: client went away ({exc})")

        def log_message(self, *args):
            return

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}/model.safetensors"
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def no_downloads_left():
    """The registry is the process's: start each test with it empty."""
    model_downloads._downloads.clear()
    yield
    model_downloads._downloads.clear()


@pytest.fixture
def linked(monkeypatch):
    """Point every file name at *url*, in the ComfyUI folder *directory*."""

    def link(url, directory="loras"):
        monkeypatch.setattr(
            model_downloads,
            "download_source",
            lambda file: {"url": url, "directory": directory},
        )

    return link


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    yield database
    database.close()


@pytest.fixture
def target(hub, tmp_path):
    """A default folder and a marked download folder with a `loras` subfolder."""
    default = tmp_path / "default"
    chosen = tmp_path / "chosen"
    for path in (default, chosen, chosen / "loras"):
        path.mkdir()
    _register(hub, default, "user", is_default=1)
    chosen_id = _register(hub, chosen, "user")
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE model_folder SET is_download_target = 1 WHERE id = ?",
            (chosen_id,),
        )
    return chosen, chosen_id


@pytest.fixture
def adapter_bytes(tmp_path):
    source = tmp_path / "src.safetensors"
    write_adapter(source)
    return source.read_bytes()


def _wait(download_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = next(d for d in live_downloads() if d["id"] == download_id)
        if entry["status"] != "running":
            return entry
        time.sleep(0.05)
    raise AssertionError(f"download {download_id} never ended")


def test_the_template_list_says_which_files_can_be_downloaded():
    # A file the committed list links, spelled as a loader might spell it.
    found = download_source("SDXL\\HUNYUAN_3D_V2.1.safetensors")
    assert found["url"].startswith("https://")
    assert found["directory"] == "checkpoints"
    assert download_source("my_own_merge_v3.safetensors") is None
    assert download_source("") is None


def test_a_download_lands_in_the_marked_folder_and_is_registered(
    hub, target, adapter_bytes, linked
):
    chosen, chosen_id = target
    arrived = []
    with serve_bytes(adapter_bytes) as url:
        linked(url)
        entry = start_download(
            hub, "SDXL\\Style.safetensors", on_arrived=arrived.append
        )
        done = _wait(entry["id"])
    assert done["status"] == STATUS_DONE, done
    assert (done["received"], done["total"], done["percent"]) == (
        len(adapter_bytes),
        len(adapter_bytes),
        100.0,
    )
    path = chosen / "loras" / "SDXL" / "Style.safetensors"
    assert path.read_bytes() == adapter_bytes
    assert not os.path.exists(str(path) + PARTIAL_SUFFIX)
    row = hub.fetchone(
        "SELECT model_id FROM model_file WHERE model_folder_id = ? AND relpath = ?",
        (chosen_id, "loras/SDXL/Style.safetensors"),
    )
    assert row is not None and arrived == [row["model_id"]] == [done["model_id"]]


def test_without_the_comfyui_subfolder_the_file_goes_at_the_top(
    hub, target, adapter_bytes, linked
):
    chosen, _ = target
    with serve_bytes(adapter_bytes) as url:
        linked(url, "checkpoints")
        done = _wait(start_download(hub, "flat.safetensors")["id"])
    assert done["status"] == STATUS_DONE, done
    assert (chosen / "flat.safetensors").is_file()


def test_with_no_marked_folder_the_default_folder_takes_it(
    hub, tmp_path, adapter_bytes, linked
):
    default = tmp_path / "default"
    default.mkdir()
    _register(hub, default, "user", is_default=1)
    with serve_bytes(adapter_bytes) as url:
        linked(url)
        done = _wait(start_download(hub, "a.safetensors")["id"])
    assert done["status"] == STATUS_DONE, done
    assert (default / "a.safetensors").is_file()


def test_a_file_the_list_does_not_link_is_refused(hub, target):
    with pytest.raises(DownloadRefused) as caught:
        start_download(hub, "my_own_merge_v3.safetensors")
    assert caught.value.status_code == 404
    assert live_downloads() == []


@pytest.mark.parametrize(
    "file, status",
    [
        ("a.txt", 400),
        ("../../escape.safetensors", 400),
        ("taken.safetensors", 409),
    ],
)
def test_refused_before_a_byte_is_fetched(hub, target, linked, file, status):
    chosen, _ = target
    (chosen / "taken.safetensors").write_bytes(b"x")
    linked("https://example.com/never-fetched", None)
    with pytest.raises(DownloadRefused) as caught:
        start_download(hub, file)
    assert caught.value.status_code == status
    assert not any(d["file"] == os.path.basename(file) for d in live_downloads())


def test_the_same_file_twice_at_once_is_refused(hub, target, adapter_bytes, linked):
    gate = threading.Event()
    with serve_bytes(adapter_bytes, gate=gate) as url:
        linked(url)
        first = start_download(hub, "twice.safetensors")
        with pytest.raises(DownloadRefused) as caught:
            start_download(hub, "twice.safetensors")
        assert caught.value.status_code == 409
        gate.set()
        assert _wait(first["id"])["status"] == STATUS_DONE


def test_a_cancel_stops_it_and_leaves_nothing(hub, target, adapter_bytes, linked):
    chosen, chosen_id = target
    gate = threading.Event()
    with serve_bytes(adapter_bytes * 16384, gate=gate) as url:
        linked(url, None)
        entry = start_download(hub, "cancelled.safetensors")
        deadline = time.monotonic() + 5
        while not any(
            d["id"] == entry["id"] and d["received"] for d in live_downloads()
        ):
            assert time.monotonic() < deadline
            time.sleep(0.02)
        assert cancel_download(entry["id"]) is True
        gate.set()
        done = _wait(entry["id"])
    assert done["status"] == STATUS_CANCELLED, done
    assert os.listdir(chosen) == ["loras"]
    assert (
        hub.fetchone("SELECT 1 FROM model_file WHERE model_folder_id = ?", (chosen_id,))
        is None
    )
    assert cancel_download(entry["id"]) is False


def test_a_body_cut_short_is_not_saved(hub, target, adapter_bytes, linked):
    chosen, _ = target
    with serve_bytes(adapter_bytes, length=len(adapter_bytes) + 100) as url:
        linked(url, None)
        done = _wait(start_download(hub, "short.safetensors")["id"])
    assert done["status"] == STATUS_FAILED, done
    assert os.listdir(chosen) == ["loras"]


def test_a_web_page_is_not_saved_as_a_model(hub, target, linked):
    chosen, _ = target
    with serve_bytes(b"<html>sign in</html>", content_type="text/html") as url:
        linked(url, None)
        done = _wait(start_download(hub, "page.safetensors")["id"])
    assert done["status"] == STATUS_FAILED
    assert "web page" in done["error"]
    assert os.listdir(chosen) == ["loras"]


def test_a_compressed_body_is_asked_against_and_refused(
    hub, target, adapter_bytes, linked
):
    """Content-Length would be the compressed size while the decoded bytes are
    what lands, so the space check would not hold. Wrong if the request lets
    the server compress, or if a server that compresses anyway gets saved."""
    chosen, _ = target
    asked = []
    body = gzip.compress(adapter_bytes)
    with serve_bytes(body, encoding="gzip", asked=asked) as url:
        linked(url, None)
        done = _wait(start_download(hub, "packed.safetensors")["id"])
    assert asked[0]["Accept-Encoding"] == "identity"
    assert done["status"] == STATUS_FAILED
    assert "compressed" in done["error"]
    assert os.listdir(chosen) == ["loras"]


def test_bytes_that_are_not_a_model_are_removed_again(hub, target, linked):
    chosen, chosen_id = target
    with serve_bytes(b"not a safetensors header") as url:
        linked(url, None)
        done = _wait(start_download(hub, "junk.safetensors")["id"])
    assert done["status"] == STATUS_FAILED
    assert "does not read as a model" in done["error"]
    assert os.listdir(chosen) == ["loras"]


def test_an_ended_download_is_forgotten_after_a_while(
    hub, target, adapter_bytes, monkeypatch, linked
):
    with serve_bytes(adapter_bytes) as url:
        linked(url)
        entry = start_download(hub, "old.safetensors")
        _wait(entry["id"])
    monkeypatch.setattr(model_downloads, "FINISHED_KEEP_SECONDS", -1)
    assert all(d["id"] != entry["id"] for d in live_downloads())


def test_a_missing_model_the_list_links_is_marked_downloadable():
    graph = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "hunyuan_3d_v2.1.safetensors"},
        },
        "2": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "my_own_lora.safetensors"},
        },
        "3": {"class_type": "SaveImage", "inputs": {}},
    }
    object_info = {
        "CheckpointLoaderSimple": {
            "input": {"required": {"ckpt_name": [["other.safetensors"], {}]}}
        },
        "LoraLoader": {
            "input": {"required": {"lora_name": [["other.safetensors"], {}]}}
        },
        "SaveImage": {"input": {"required": {}}},
    }
    reasons, _ = judge(graph, object_info, None)
    missing = next(r for r in reasons if r.code == MISSING_MODELS).detail["models"]
    assert missing == [
        {
            "file": "hunyuan_3d_v2.1.safetensors",
            "folder": "checkpoints",
            "downloadable": True,
        },
        {"file": "my_own_lora.safetensors", "folder": "loras"},
    ]


def test_a_body_of_unknown_length_is_refused(hub, target, adapter_bytes, linked):
    chosen, _ = target
    with serve_bytes(adapter_bytes, length=-1) as url:
        linked(url, None)
        done = _wait(start_download(hub, "unsized.safetensors")["id"])
    assert done["status"] == STATUS_FAILED
    assert "how large" in done["error"]
    assert os.listdir(chosen) == ["loras"]


def test_a_row_the_hub_refuses_takes_the_file_with_it(
    hub, target, adapter_bytes, linked, monkeypatch
):
    chosen, _ = target

    def refuse(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(ModelFolderScanner, "register_file", refuse)
    with serve_bytes(adapter_bytes) as url:
        linked(url, None)
        done = _wait(start_download(hub, "refused.safetensors")["id"])
    assert done["status"] == STATUS_FAILED
    # Nothing stranded at the real name, so a retry is not "already there".
    assert os.listdir(chosen) == ["loras"]


def test_a_cancel_while_waiting_for_the_shelf_wins(hub, target, adapter_bytes, linked):
    chosen, _ = target
    with serve_bytes(adapter_bytes) as url:
        linked(url, None)
        with SHELF_IO_LOCK:
            entry = start_download(hub, "waiting.safetensors")
            deadline = time.monotonic() + 5
            while next(d for d in live_downloads() if d["id"] == entry["id"])[
                "received"
            ] < len(adapter_bytes):
                assert time.monotonic() < deadline
                time.sleep(0.02)
            assert cancel_download(entry["id"]) is True
            done = _wait(entry["id"])
    assert done["status"] == STATUS_CANCELLED
    assert os.listdir(chosen) == ["loras"]


def test_a_cancel_after_the_commit_says_no_and_the_file_lands(
    hub, target, adapter_bytes, linked, monkeypatch
):
    chosen, _ = target
    committed, release = threading.Event(), threading.Event()
    real = model_downloads.publish_no_clobber

    def held(partial, final):
        committed.set()
        release.wait(5)
        real(partial, final)

    monkeypatch.setattr(model_downloads, "publish_no_clobber", held)
    with serve_bytes(adapter_bytes) as url:
        linked(url, None)
        entry = start_download(hub, "landing.safetensors")
        assert committed.wait(5)
        assert cancel_download(entry["id"]) is False
        release.set()
        done = _wait(entry["id"])
    assert done["status"] == STATUS_DONE
    assert (chosen / "landing.safetensors").is_file()


def test_two_spellings_of_one_file_are_one_download(hub, target, adapter_bytes, linked):
    gate = threading.Event()
    with serve_bytes(adapter_bytes, gate=gate) as url:
        linked(url, None)
        first = start_download(hub, "Spelled.safetensors")
        with pytest.raises(DownloadRefused) as caught:
            start_download(hub, "spelled.SAFETENSORS")
        assert caught.value.status_code == 409
        gate.set()
        assert _wait(first["id"])["status"] == STATUS_DONE


@pytest.mark.parametrize("file", ["loras/style.safetensors", "Loras/style.safetensors"])
def test_a_name_already_under_its_folder_is_not_put_under_it_twice(
    hub, target, adapter_bytes, linked, file
):
    """Wrong if a workflow's own spelling of the folder nests it a second time."""
    chosen, _ = target
    with serve_bytes(adapter_bytes) as url:
        linked(url, "loras")
        done = _wait(start_download(hub, file)["id"])
    assert done["status"] == STATUS_DONE, done
    assert (chosen / "loras" / "style.safetensors").is_file()


@pytest.mark.parametrize(
    "hops, refused",
    [
        (["https://example.com/a"], False),
        (["https://example.com/a", "https://cdn.example.com/a"], False),
        (["https://example.com/a", "http://cdn.example.com/a"], True),
        (
            [
                "https://example.com/a",
                "http://example-hop.test/a",
                "https://cdn.example.com/a",
            ],
            True,
        ),
    ],
)
def test_no_hop_of_a_redirect_may_be_plain_http(hops, refused):
    """Wrong if a chain that passes through plain http and back is let in."""
    response = SimpleNamespace(
        url=hops[-1], history=[SimpleNamespace(url=hop) for hop in hops[:-1]]
    )
    assert model_downloads._left_https(hops[0], response) is refused


def test_a_thread_that_will_not_start_leaves_no_entry_behind(
    hub, target, linked, monkeypatch
):
    def refuse(self):
        raise RuntimeError("can't start new thread")

    linked("https://example.com/never-fetched", None)
    monkeypatch.setattr(threading.Thread, "start", refuse)
    with pytest.raises(DownloadRefused) as caught:
        start_download(hub, "unstarted.safetensors")
    assert caught.value.status_code == 503
    assert live_downloads() == []
    monkeypatch.undo()
    linked("https://example.com/never-fetched", None)
    # Not "already being downloaded": nothing was left registered.
    (target[0] / "unstarted.safetensors").write_bytes(b"x")
    with pytest.raises(DownloadRefused) as caught:
        start_download(hub, "unstarted.safetensors")
    assert "already in" in str(caught.value)
