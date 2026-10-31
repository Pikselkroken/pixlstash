"""Model downloads: where they land, what is refused, and how they end."""

import contextlib
import gzip
import hashlib
import importlib.util
import json
import os
import sqlite3
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.routes.workflows import RunGroup, _mark_live_downloads
from pixlstash.services import comfyui_templates, model_downloads
from pixlstash.services.model_downloads import (
    STATUS_CANCELLED,
    STATUS_DONE,
    STATUS_FAILED,
    DownloadRefused,
    cancel_download,
    digest_source,
    download_source,
    live_download_source,
    live_downloads,
    start_download,
    trusted_live_link,
)
from pixlstash.services.model_folder_scanner import ModelFolderScanner
from pixlstash.services.model_mover import PARTIAL_SUFFIX, SHELF_IO_LOCK
from pixlstash.services.workflow_run_service import (
    MISSING_MODELS,
    MODELS_DOWNLOADING,
    blocks_batch,
    bypass_missing_loras,
    judge,
    missing_digest_models,
)
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

    def link(url, directory="loras", sha256=None):
        monkeypatch.setattr(
            model_downloads,
            "download_source",
            lambda file: {"url": url, "directory": directory, "sha256": sha256},
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
    # What a workflow row or a set slot offers it under while it is on its way.
    assert (entry["directory"], entry["slot"], entry["sha256"]) == (
        "loras",
        "lora",
        None,
    )
    # By its own name, never the loader's subfolder it was asked under: what
    # `running_download` and the client's `runningDownload` compare a name to.
    assert entry["file"] == done["file"] == "Style.safetensors"
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


def test_a_download_that_is_not_the_listed_file_is_refused(
    hub, target, adapter_bytes, linked
):
    """Wrong if a body whose digest is not the one the list records lands on
    the shelf: a loader asking for the listed digest would stay missing."""
    chosen, chosen_id = target
    with serve_bytes(adapter_bytes) as url:
        linked(url, sha256="0" * 64)
        refused = _wait(start_download(hub, "other.safetensors")["id"])
        linked(url, sha256=hashlib.sha256(adapter_bytes).hexdigest())
        kept = _wait(start_download(hub, "listed.safetensors")["id"])
    assert refused["status"] == STATUS_FAILED, refused
    assert "not the file the template list names" in refused["error"]
    assert not (chosen / "loras" / "other.safetensors").exists()
    assert not (chosen / "loras" / ("other.safetensors" + PARTIAL_SUFFIX)).exists()
    assert kept["status"] == STATUS_DONE, kept
    assert (chosen / "loras" / "listed.safetensors").read_bytes() == adapter_bytes


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


# The Qwen VAE's digest, as the template list records it for its link.
_QWEN_VAE = "a70580f0213e67967ee9c95f05bb400e8fb08307e017a924bf3441223e023d1f"
_ON_SHELF = "b" * 64
_UNLISTED = "c" * 64


def test_a_model_a_digest_loader_names_and_the_shelf_lacks_is_missing():
    """By the listed name and offered, or by its digest when the list has none.

    Wrong if a digest the shelf holds is reported, if an emptied adapter row
    counts, if a missing one is not reported at all (it would only fail inside
    ComfyUI), or if a caller that gives no shelf check sees a change.
    """
    graph = {
        "1": {"class_type": "PixlStashVAELoader", "inputs": {"vae_sha256": _QWEN_VAE}},
        "2": {
            "class_type": "PixlStashCLIPLoader",
            "inputs": {"clip_sha256": _ON_SHELF, "type": "qwen_image"},
        },
        "3": {
            "class_type": "PixlStashMultiAdapterLoader",
            "inputs": {"adapter_sha256": _UNLISTED.upper(), "adapter_sha256_2": ""},
        },
        "4": {"class_type": "SaveImage", "inputs": {}},
    }
    object_info = {
        cls: {"input": {"required": {}}}
        for cls in (
            "PixlStashVAELoader",
            "PixlStashCLIPLoader",
            "PixlStashMultiAdapterLoader",
            "SaveImage",
        )
    }
    reasons, _ = judge(
        graph, object_info, None, digest_present=lambda d: d == _ON_SHELF
    )
    missing = next(r for r in reasons if r.code == MISSING_MODELS).detail["models"]
    assert missing == [
        {
            "file": "qwen_image_vae.safetensors",
            "folder": "vae",
            "sha256": _QWEN_VAE,
            "downloadable": True,
        },
        {"file": _UNLISTED, "folder": "loras", "sha256": _UNLISTED},
    ]
    reasons, _ = judge(graph, object_info, None)
    assert all(r.code != MISSING_MODELS for r in reasons)


def test_every_digest_a_built_in_names_can_be_downloaded():
    """Each model the shipped workflows load by digest is a file the template
    list links under that digest, so a machine without it is offered it."""
    folder = os.path.join(
        os.path.dirname(model_downloads.__file__),
        "..",
        "data",
        "comfyui-workflows",
        "built-in",
    )
    seen = 0
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), encoding="utf-8") as handle:
            graph = json.load(handle)
        for model in missing_digest_models(graph, lambda _digest: False):
            seen += 1
            source = digest_source(model["sha256"])
            assert source is not None, (name, model)
            assert download_source(source["name"])["url"] == source["url"], name
    assert seen >= 5


def test_the_list_build_asks_every_link_and_keeps_a_digest_only_when_unreachable(
    monkeypatch,
):
    """Wrong if a link the old list holds is not asked again: a file
    re-uploaded under it would keep a digest every download is then refused
    against. Wrong too if one unreachable link loses its known digest."""
    spec = importlib.util.spec_from_file_location(
        "build_template_suggestions",
        os.path.join(
            os.path.dirname(__file__), "..", "scripts", "build_template_suggestions.py"
        ),
    )
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    hf = "https://huggingface.co/"
    asked = []

    def sha256_of(url):
        asked.append(url)
        if url.endswith("down"):
            raise urllib.error.URLError("offline")
        return {hf + "moved": "1" * 64}.get(url)

    monkeypatch.setattr(build, "_sha256_of", sha256_of)

    def files(*pairs):
        return {"templates": [{"files": [dict(url=u, **d) for u, d in pairs]}]}

    document = files(
        (hf + "moved", {}), (hf + "down", {}), (hf + "moved", {}), (hf + "gated", {})
    )
    old = files(
        (hf + "moved", {"sha256": "0" * 64}),
        (hf + "down", {"sha256": "2" * 64}),
        (hf + "gated", {"sha256": "3" * 64}),
    )
    build.add_digests(document, old)
    assert [f.get("sha256") for f in document["templates"][0]["files"]] == [
        "1" * 64,
        "2" * 64,
        "1" * 64,
        None,
    ]
    assert sorted(asked) == [hf + "down", hf + "gated", hf + "moved"]


def test_a_bypassed_lora_the_list_links_is_marked_downloadable():
    """A missing LoRA is left out of the run, not refused, but still offered."""
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": "Wuli-Qwen-Image-2512-Turbo-LoRA-2steps-V1.0-bf16.safetensors",
                "model": ["1", 0],
            },
        },
        "3": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "my_own_lora.safetensors", "model": ["2", 0]},
        },
        "4": {"class_type": "KSampler", "inputs": {"model": ["3", 0]}},
    }
    lora_spec = {
        "input": {
            "required": {
                "model": ["MODEL"],
                "lora_name": [["other.safetensors"], {}],
            }
        },
        "output": ["MODEL"],
    }
    object_info = {
        "CheckpointLoaderSimple": {"input": {"required": {}}, "output": ["MODEL"]},
        "LoraLoader": lora_spec,
        "KSampler": {"input": {"required": {"model": ["MODEL"]}}, "output": []},
    }
    bypassed = bypass_missing_loras(graph, object_info)
    assert {entry["file"]: entry.get("downloadable") for entry in bypassed} == {
        "Wuli-Qwen-Image-2512-Turbo-LoRA-2steps-V1.0-bf16.safetensors": True,
        "my_own_lora.safetensors": None,
    }
    assert graph["4"]["inputs"]["model"] == ["1", 0]


def _running(file, sha256=None, slot=None):
    """A download in flight, 43% in, as the registry holds one."""
    entry = {
        "id": f"dl-{file}",
        "file": file,
        "status": "running",
        "received": 43,
        "total": 100,
        "sha256": sha256,
        "slot": slot,
        "ended_at": None,
    }
    model_downloads._downloads[entry["id"]] = entry
    return entry


def test_a_model_still_downloading_refuses_the_run_until_it_lands():
    """Wrong if a model on its way is reported missing (and offered again for
    download), if a run naming it would go ahead, or if it stays refused once
    the download has ended. By name in any case, and by digest."""
    graph = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "SDXL/Hunyuan_3D_v2.1.safetensors"},
        },
        "2": {"class_type": "PixlStashVAELoader", "inputs": {"vae_sha256": _QWEN_VAE}},
        "3": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "my_own_lora.safetensors"},
        },
        "4": {"class_type": "SaveImage", "inputs": {}},
    }
    object_info = {
        "CheckpointLoaderSimple": {
            "input": {"required": {"ckpt_name": [["other.safetensors"], {}]}}
        },
        "LoraLoader": {
            "input": {"required": {"lora_name": [["other.safetensors"], {}]}}
        },
        "PixlStashVAELoader": {"input": {"required": {}}},
        "SaveImage": {"input": {"required": {}}},
    }
    _running("hunyuan_3D_V2.1.safetensors", slot="checkpoint")
    # By digest, though the list files it under another kind's slot.
    _running("renamed_vae.safetensors", sha256=_QWEN_VAE, slot="other")
    reasons, _ = judge(graph, object_info, None, digest_present=lambda d: False)
    by_code = {r.code: r.detail["models"] for r in reasons}
    assert by_code[MODELS_DOWNLOADING] == [
        {
            "file": "SDXL/Hunyuan_3D_v2.1.safetensors",
            "folder": "checkpoints",
            "percent": 43.0,
        },
        {
            "file": "qwen_image_vae.safetensors",
            "folder": "vae",
            "sha256": _QWEN_VAE,
            "percent": 43.0,
        },
    ]
    assert by_code[MISSING_MODELS] == [
        {"file": "my_own_lora.safetensors", "folder": "loras"}
    ]
    assert blocks_batch([r for r in reasons if r.code == MODELS_DOWNLOADING])

    for entry in model_downloads._downloads.values():
        entry["status"] = STATUS_DONE
    reasons, _ = judge(graph, object_info, None, digest_present=lambda d: False)
    assert MODELS_DOWNLOADING not in {r.code for r in reasons}
    assert (
        len(next(r for r in reasons if r.code == MISSING_MODELS).detail["models"]) == 3
    )


def test_a_lora_still_downloading_is_not_bypassed():
    """Wrong if the run quietly goes without a LoRA the owner put in and is
    fetching: it keeps its loader, and the pre-flight refuses on it."""
    lora_spec = {
        "input": {
            "required": {"model": ["MODEL"], "lora_name": [["other.safetensors"], {}]}
        },
        "output": ["MODEL"],
    }
    object_info = {
        "CheckpointLoaderSimple": {"input": {"required": {}}, "output": ["MODEL"]},
        "LoraLoader": lora_spec,
        "KSampler": {"input": {"required": {"model": ["MODEL"]}}, "output": []},
    }
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Coming.safetensors", "model": ["1", 0]},
        },
        "3": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "gone.safetensors", "model": ["2", 0]},
        },
        "4": {"class_type": "KSampler", "inputs": {"model": ["3", 0]}},
    }
    _running("coming.safetensors", slot="lora")
    bypassed = bypass_missing_loras(graph, object_info)
    assert [entry["file"] for entry in bypassed] == ["gone.safetensors"]
    assert graph["4"]["inputs"]["model"] == ["2", 0]
    reasons, _ = judge(graph, object_info, None)
    assert [r.detail["models"] for r in reasons if r.code == MODELS_DOWNLOADING] == [
        [{"file": "Coming.safetensors", "folder": "loras", "percent": 43.0}]
    ]


def test_a_lora_named_like_a_checkpoint_on_its_way_is_not_on_its_way():
    """Wrong if a checkpoint download keeps a LoRA of the same file name from
    being bypassed, or refuses its run as downloading: nothing fetches it."""
    lora_spec = {
        "input": {
            "required": {"model": ["MODEL"], "lora_name": [["other.safetensors"], {}]}
        },
        "output": ["MODEL"],
    }
    object_info = {
        "CheckpointLoaderSimple": {"input": {"required": {}}, "output": ["MODEL"]},
        "LoraLoader": lora_spec,
        "KSampler": {"input": {"required": {"model": ["MODEL"]}}, "output": []},
    }
    graph = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {}},
        "2": {
            "class_type": "LoraLoader",
            "inputs": {"lora_name": "Shared.safetensors", "model": ["1", 0]},
        },
        "3": {"class_type": "KSampler", "inputs": {"model": ["2", 0]}},
    }
    _running("shared.safetensors", slot="checkpoint")
    assert [e["file"] for e in bypass_missing_loras(graph, object_info)] == [
        "Shared.safetensors"
    ]
    assert (
        model_downloads.running_download("Shared.safetensors", folder="loras") is None
    )
    # The kind it is: a diffusion model's folder is a checkpoint's slot too.
    assert model_downloads.running_download(
        "Shared.safetensors", folder="diffusion_models"
    )


def test_a_digest_another_download_shares_a_name_with_is_still_missing():
    """Wrong if a VAE asked for by one digest reads as on its way while a file
    of the same name but another digest downloads: that download will not
    satisfy the loader, so it must stay missing and offered."""
    _running("qwen_image_vae.safetensors", sha256="e" * 64, slot="vae")
    graph = {
        "1": {"class_type": "PixlStashVAELoader", "inputs": {"vae_sha256": _QWEN_VAE}},
        "2": {"class_type": "SaveImage", "inputs": {}},
    }
    object_info = {
        cls: {"input": {"required": {}}} for cls in ("PixlStashVAELoader", "SaveImage")
    }
    reasons, _ = judge(graph, object_info, None, digest_present=lambda d: False)
    assert MODELS_DOWNLOADING not in {r.code for r in reasons}
    assert [
        m["sha256"]
        for m in next(r for r in reasons if r.code == MISSING_MODELS).detail["models"]
    ] == [_QWEN_VAE]
    # A download with no listed digest still matches by its name.
    model_downloads._downloads.clear()
    _running("qwen_image_vae.safetensors", slot="vae")
    reasons, _ = judge(graph, object_info, None, digest_present=lambda d: False)
    assert MODELS_DOWNLOADING in {r.code for r in reasons}


def test_a_listed_digest_is_kept_lowercase(hub, target, adapter_bytes, linked):
    """Wrong if a digest the list spells in capitals is stored as spelled:
    every digest it is compared with (a loader's, a set member's) is lowercase."""
    with serve_bytes(adapter_bytes) as url:
        linked(url, sha256="AB" * 32)
        entry = start_download(hub, "upper.safetensors")
        assert entry["sha256"] == "ab" * 32
        _wait(entry["id"])


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


# A ComfyUI address for the live-template tests; nothing is ever asked there.
COMFYUI = "http://127.0.0.1:1"
HF_LINK = "https://huggingface.co/someone/repo/resolve/main/loras/{}"


@pytest.fixture
def live_listing(monkeypatch):
    """The owner's ComfyUI serves one template linking *files* ``{name: url}``."""

    def serve(files):
        listing = comfyui_templates.Listing(
            [
                comfyui_templates.Template(
                    module="default",
                    name="example_edit",
                    source="builtin",
                    title="Example edit",
                    files=[
                        comfyui_templates.TemplateFile(name, "loras", url)
                        for name, url in files.items()
                    ],
                )
            ],
            read_at=0.0,
        )
        monkeypatch.setattr(
            comfyui_templates, "read_listing", lambda url, object_info=None: listing
        )

    return serve


@pytest.mark.parametrize(
    "name, url, trusted",
    [
        ("a.safetensors", HF_LINK.format("a.safetensors"), True),
        ("a.gguf", HF_LINK.format("a.gguf"), True),
        ("a.safetensors", "http://huggingface.co/x/a.safetensors", False),
        ("a.safetensors", "https://example.com/x/a.safetensors", False),
        ("a.safetensors", "https://huggingface.co.example.com/a.safetensors", False),
        ("a.safetensors", "https://user@huggingface.co/a.safetensors", False),
        ("a.safetensors", "https://huggingface.co:8443/a.safetensors", False),
        # A pickle: loading it runs code, so a live link never fetches one.
        ("a.pth", HF_LINK.format("a.pth"), False),
        ("a.ckpt", HF_LINK.format("a.ckpt"), False),
        ("a.safetensors", None, False),
    ],
)
def test_a_live_template_link_is_taken_only_from_hugging_face(name, url, trusted):
    assert trusted_live_link(name, url) is trusted


def test_a_file_only_the_live_templates_link_is_found_there(live_listing):
    live_listing(
        {
            "Fresh_Edit_LoRA.safetensors": HF_LINK.format(
                "Fresh_Edit_LoRA.safetensors"
            ),
            "elsewhere.safetensors": "https://example.com/elsewhere.safetensors",
        }
    )
    assert download_source("fresh_edit_lora.safetensors") is None
    found = live_download_source("loras/fresh_edit_lora.safetensors", COMFYUI)
    assert found == {
        "url": HF_LINK.format("Fresh_Edit_LoRA.safetensors"),
        "directory": "loras",
        "sha256": None,
    }
    assert live_download_source("elsewhere.safetensors", COMFYUI) is None
    assert live_download_source("fresh_edit_lora.safetensors", None) is None


def test_no_live_templates_is_no_live_link(monkeypatch):
    def unavailable(url, object_info=None):
        raise comfyui_templates.TemplatesUnavailable("nothing answers")

    monkeypatch.setattr(comfyui_templates, "read_listing", unavailable)
    assert live_download_source("fresh.safetensors", COMFYUI) is None


def test_a_download_falls_back_to_the_live_templates(hub, target, live_listing):
    chosen, _ = target
    live_listing({"fresh.safetensors": HF_LINK.format("fresh.safetensors")})
    # Already there: refused only after a link was found, so nothing is fetched.
    (chosen / "loras" / "fresh.safetensors").write_bytes(b"x")
    with pytest.raises(DownloadRefused) as caught:
        start_download(hub, "fresh.safetensors", comfyui_url=COMFYUI)
    assert caught.value.status_code == 409
    with pytest.raises(DownloadRefused) as caught:
        start_download(hub, "fresh.safetensors")
    assert caught.value.status_code == 404
    assert live_downloads() == []


def test_the_preflight_marks_what_only_the_live_templates_link(live_listing):
    live_listing({"fresh.safetensors": HF_LINK.format("fresh.safetensors")})
    group = RunGroup(
        reasons=[
            {
                "code": MISSING_MODELS,
                "models": [
                    {"file": "fresh.safetensors", "folder": "loras"},
                    {"file": "own_merge.safetensors", "folder": "checkpoints"},
                    # Named by digest: a live template has none to match.
                    {"file": "fresh.safetensors", "sha256": "ab" * 32},
                ],
            }
        ],
        bypassed_loras=[
            {"file": "fresh.safetensors", "requested": False},
            {"file": "fresh.safetensors", "requested": True},
        ],
    )
    _mark_live_downloads([group], COMFYUI, object_info={})
    models = group.reasons[0]["models"]
    assert [model.get("downloadable") for model in models] == [True, None, None]
    assert [lora.get("downloadable") for lora in group.bypassed_loras] == [True, None]


def test_the_preflight_asks_no_templates_while_comfyui_is_down(monkeypatch):
    def never(url, object_info=None):
        raise AssertionError("asked a ComfyUI that is not answering")

    monkeypatch.setattr(comfyui_templates, "read_listing", never)
    group = RunGroup(bypassed_loras=[{"file": "fresh.safetensors"}])
    _mark_live_downloads([group], COMFYUI, object_info=None)
    assert "downloadable" not in group.bypassed_loras[0]
