"""Model downloads: where they land, what is refused, and how they end."""

import contextlib
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services import model_downloads
from pixlstash.services.model_downloads import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    DownloadRefused,
    cancel_download,
    live_downloads,
    start_download,
)
from pixlstash.services.model_mover import PARTIAL_SUFFIX
from tests.test_downloads_intake import _register, write_adapter


@contextlib.contextmanager
def serve_bytes(body, content_type="application/octet-stream", gate=None):
    """Serve *body* at a loopback URL; *gate*, when set, holds the second half."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
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
    return open(write_adapter(tmp_path / "src.safetensors"), "rb").read()


def _wait(download_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = next(d for d in live_downloads() if d["id"] == download_id)
        if entry["status"] != "running":
            return entry
        time.sleep(0.05)
    raise AssertionError(f"download {download_id} never ended")


def test_a_download_lands_in_the_marked_folder_and_is_registered(
    hub, target, adapter_bytes
):
    chosen, chosen_id = target
    arrived = []
    with serve_bytes(adapter_bytes) as url:
        entry = start_download(
            hub, url, "SDXL\\Style.safetensors", "loras", on_arrived=arrived.append
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
    hub, target, adapter_bytes
):
    chosen, _ = target
    with serve_bytes(adapter_bytes) as url:
        done = _wait(start_download(hub, url, "flat.safetensors", "checkpoints")["id"])
    assert done["status"] == STATUS_DONE, done
    assert (chosen / "flat.safetensors").is_file()


def test_with_no_marked_folder_the_default_folder_takes_it(
    hub, tmp_path, adapter_bytes
):
    default = tmp_path / "default"
    default.mkdir()
    _register(hub, default, "user", is_default=1)
    with serve_bytes(adapter_bytes) as url:
        done = _wait(start_download(hub, url, "a.safetensors")["id"])
    assert done["status"] == STATUS_DONE, done
    assert (default / "a.safetensors").is_file()


@pytest.mark.parametrize(
    "url, file, status",
    [
        ("ftp://example.com/a.safetensors", "a.safetensors", 400),
        ("https://example.com/a.txt", "a.txt", 400),
        ("https://example.com/a", "../../escape.safetensors", 400),
        ("https://example.com/a", "taken.safetensors", 409),
    ],
)
def test_refused_before_a_byte_is_fetched(hub, target, url, file, status):
    chosen, _ = target
    (chosen / "taken.safetensors").write_bytes(b"x")
    with pytest.raises(DownloadRefused) as caught:
        start_download(hub, url, file)
    assert caught.value.status_code == status
    assert not any(d["file"] == os.path.basename(file) for d in live_downloads())


def test_the_same_file_twice_at_once_is_refused(hub, target, adapter_bytes):
    gate = threading.Event()
    with serve_bytes(adapter_bytes, gate=gate) as url:
        first = start_download(hub, url, "twice.safetensors")
        with pytest.raises(DownloadRefused) as caught:
            start_download(hub, url, "twice.safetensors")
        assert caught.value.status_code == 409
        gate.set()
        assert _wait(first["id"])["status"] == STATUS_DONE


def test_a_cancel_stops_it_and_leaves_nothing(hub, target, adapter_bytes):
    chosen, chosen_id = target
    gate = threading.Event()
    with serve_bytes(adapter_bytes * 16384, gate=gate) as url:
        entry = start_download(hub, url, "cancelled.safetensors")
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
    assert hub.fetchone(
        "SELECT 1 FROM model_file WHERE model_folder_id = ?", (chosen_id,)
    ) is None
    assert cancel_download(entry["id"]) is False


def test_a_web_page_is_not_saved_as_a_model(hub, target):
    chosen, _ = target
    with serve_bytes(b"<html>sign in</html>", content_type="text/html") as url:
        done = _wait(start_download(hub, url, "page.safetensors")["id"])
    assert done["status"] == STATUS_FAILED
    assert "web page" in done["error"]
    assert os.listdir(chosen) == ["loras"]


def test_bytes_that_are_not_a_model_are_removed_again(hub, target):
    chosen, chosen_id = target
    with serve_bytes(b"not a safetensors header") as url:
        done = _wait(start_download(hub, url, "junk.safetensors")["id"])
    assert done["status"] == STATUS_FAILED
    assert "does not read as a model" in done["error"]
    assert os.listdir(chosen) == ["loras"]


def test_an_ended_download_is_forgotten_after_a_while(
    hub, target, adapter_bytes, monkeypatch
):
    with serve_bytes(adapter_bytes) as url:
        entry = start_download(hub, url, "old.safetensors")
        _wait(entry["id"])
    monkeypatch.setattr(model_downloads, "FINISHED_KEEP_SECONDS", -1)
    assert all(d["id"] != entry["id"] for d in live_downloads())
