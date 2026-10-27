"""Tests for training recipes published to and fetched from pixlstash.dev."""

from __future__ import annotations

import copy
import hashlib
import http.server
import json
import os
import threading
import time

import pytest

from pixlstash.training import preset_updates, presets

SITE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "website", "training-presets", "v1"
)


def test_published_site_matches_the_shipped_recipes():
    """The guardrail: run scripts/build_training_presets_site.py after editing
    a recipe, or the site serves an older one than the app ships."""
    expected = preset_updates.site_files()
    on_disk = sorted(os.listdir(SITE_DIR))
    assert on_disk == sorted(expected), "website/training-presets/v1 has drifted"
    for name, raw in expected.items():
        with open(os.path.join(SITE_DIR, name), "rb") as handle:
            assert handle.read() == raw, f"website/training-presets/v1/{name} drifted"


def test_every_shipped_recipe_passes_as_its_own_update():
    """A recipe published unchanged must be accepted by every client."""
    for pid, shipped in presets.shipped_presets().items():
        assert presets.validate_update(copy.deepcopy(shipped), shipped)["id"] == pid


def _shipped():
    return presets.shipped_presets()["zimage-turbo"]


def _newer(**changes):
    preset = copy.deepcopy(_shipped())
    preset["version"] = _shipped()["version"] + 1
    preset.update(changes)
    return preset


@pytest.mark.parametrize(
    "mutate",
    [
        # A model source, the family, the code it needs.
        lambda p: p["locked"].__setitem__("model.name_or_path", "someone/other-model"),
        lambda p: p["locked"].__setitem__(
            "model.assistant_lora_path", "x/y.safetensors"
        ),
        lambda p: p["locked"].pop("model.extras_name_or_path"),
        lambda p: p.__setitem__("requires_arch", "flux2"),
        lambda p: p.__setitem__("template", "other"),
        lambda p: p.__setitem__("divisibility", 8),
        # A key outside the schema, anywhere.
        lambda p: p.__setitem__("hooks", {"run": "x"}),
        lambda p: p["hardware_profiles"]["q8"].__setitem__("env", {"A": "1"}),
        lambda p: p["defaults"].__setitem__("model.name_or_path", "someone/other"),
        lambda p: p["effort"]["standard"].__setitem__("process.type", "x"),
        lambda p: p["preview"].__setitem__("other", {"model.arch": "x"}),
        # Wrong types the shipped files never carry.
        lambda p: p.__setitem__("effort", []),
        lambda p: p["hardware_profiles"]["q8"].__setitem__("vram_mb", "16000"),
        lambda p: p["effort"]["gentle"].__setitem__("max_steps", float("inf")),
        lambda p: p.__setitem__("version", "2"),
    ],
)
def test_an_update_may_change_values_never_sources_or_schema(mutate):
    preset = _newer()
    mutate(preset)
    with pytest.raises(presets.PresetError):
        presets.validate_update(preset, _shipped())


def test_an_update_may_change_values_and_profiles():
    preset = _newer()
    preset["effort"]["standard"]["train.lr"] = 0.00015
    preset["hardware_profiles"]["q8"]["vram_mb"] = 15_000
    assert presets.validate_update(preset, _shipped()) is preset


# ---------------------------------------------------------------------------
# check(): a fake pixlstash.dev
# ---------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A cache dir and a fake site holding a newer Z-Image Turbo."""
    presets.use_cache_dir(str(tmp_path / "cache"))
    files: dict[str, bytes] = {}
    fetched: list[str] = []

    def publish(preset, **entry):
        raw = json.dumps(preset).encode()
        files[f"{preset['id']}.json"] = raw
        row = {
            "id": preset["id"],
            "version": preset["version"],
            "file": f"{preset['id']}.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "min_pixlstash": "1.13.0",
        }
        row.update(entry)
        files["index.json"] = json.dumps({"schema": 1, "presets": [row]}).encode()

    def fetch(name):
        fetched.append(name)
        if name not in files:
            raise preset_updates.FetchError(f"{name} answered 404")
        return files[name]

    monkeypatch.setattr(preset_updates, "_fetch", fetch)
    monkeypatch.setattr(preset_updates, "installed_version", lambda: "1.13.0")
    newer = _newer()
    newer["effort"]["standard"]["train.lr"] = 0.00015
    publish(newer)
    try:
        yield {"publish": publish, "files": files, "fetched": fetched, "newer": newer}
    finally:
        presets.use_cache_dir(None)


def test_a_newer_valid_recipe_replaces_the_shipped_one(site):
    result = preset_updates.check()
    assert result == {
        "checked": True,
        "updated": [{"id": "zimage-turbo", "label": "Z-Image Turbo", "version": 2}],
        "error": None,
    }
    in_use = presets.get_preset("zimage-turbo")
    assert in_use["version"] == 2
    assert in_use["effort"]["standard"]["train.lr"] == 0.00015
    # Kept beside the hub: a fresh read (the next start) still uses it.
    presets.reload()
    assert presets.get_preset("zimage-turbo")["version"] == 2
    # Checking again fetches the index only: version 2 is not above 2.
    site["fetched"].clear()
    assert preset_updates.check()["updated"] == []
    assert site["fetched"] == ["index.json"]


@pytest.mark.parametrize(
    "entry",
    [
        {"sha256": "0" * 64},
        {"min_pixlstash": "1.14.0"},
        {"version": 3},  # the file says 2
        {"id": "unknown-family"},
    ],
)
def test_a_recipe_that_does_not_verify_is_ignored(site, entry):
    site["publish"](site["newer"], **entry)
    assert preset_updates.check()["updated"] == []
    assert presets.get_preset("zimage-turbo")["version"] == 1


def test_a_recipe_that_changes_a_model_source_is_ignored(site):
    bad = copy.deepcopy(site["newer"])
    bad["locked"]["model.name_or_path"] = "someone/other-model"
    site["publish"](bad)
    assert preset_updates.check()["updated"] == []
    assert presets.get_preset("zimage-turbo")["locked"]["model.name_or_path"] == (
        "Tongyi-MAI/Z-Image-Turbo"
    )


def test_an_unreachable_site_changes_nothing_and_says_so(site):
    site["files"].clear()
    result = preset_updates.check()
    assert result["checked"] is True
    assert result["updated"] == []
    assert "pixlstash.dev" in result["error"]
    assert presets.get_preset("zimage-turbo")["version"] == 1


def test_the_automatic_check_runs_at_most_once_a_day(site):
    assert preset_updates.check(automatic=True)["checked"] is True
    site["fetched"].clear()
    assert preset_updates.check(automatic=True) == {
        "checked": False,
        "updated": [],
        "error": None,
    }
    assert site["fetched"] == []
    # The button always checks.
    assert preset_updates.check()["checked"] is True


def test_a_cached_recipe_no_newer_than_the_shipped_one_is_ignored(site):
    """After an app upgrade ships version 2, a cached version 2 is not used."""
    preset_updates.check()
    shipped = copy.deepcopy(_shipped())
    shipped["version"] = 2
    cached = os.path.join(presets.cache_dir(), "zimage-turbo.json")
    with open(cached, encoding="utf-8") as handle:
        assert json.load(handle)["version"] == 2
    assert presets._cached(shipped) is None


def test_a_tampered_cache_file_falls_back_to_the_shipped_recipe(site):
    preset_updates.check()
    cached = os.path.join(presets.cache_dir(), "zimage-turbo.json")
    with open(cached, encoding="utf-8") as handle:
        preset = json.load(handle)
    preset["locked"]["model.name_or_path"] = "someone/other-model"
    with open(cached, "w", encoding="utf-8") as handle:
        json.dump(preset, handle)
    presets.reload()
    assert presets.get_preset("zimage-turbo")["version"] == 1


# ---------------------------------------------------------------------------
# _fetch against a real HTTP server: redirects, size, a slow trickle
# ---------------------------------------------------------------------------


@pytest.fixture
def server(monkeypatch):
    """A local server whose routes are ``{path: handler(request)}``."""
    routes = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            routes[self.path](self)

        def log_message(self, *args):
            return None

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(
        preset_updates, "SITE_URL", f"http://127.0.0.1:{httpd.server_port}/v1"
    )
    try:
        yield routes
    finally:
        httpd.shutdown()
        httpd.server_close()


def _send(body: bytes, status=200, headers=None):
    def handle(request):
        request.send_response(status)
        for key, value in (headers or {}).items():
            request.send_header(key, value)
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)

    return handle


def test_fetch_reads_the_file(server):
    server["/v1/index.json"] = _send(b'{"presets": []}')
    assert preset_updates._fetch("index.json") == b'{"presets": []}'


def test_fetch_refuses_a_redirect(server):
    server["/v1/index.json"] = _send(
        b"", status=302, headers={"Location": "http://127.0.0.1:1/elsewhere"}
    )
    with pytest.raises(preset_updates.FetchError, match="302"):
        preset_updates._fetch("index.json")


def test_fetch_refuses_a_file_over_the_cap(server, monkeypatch):
    monkeypatch.setattr(preset_updates, "MAX_BYTES", 100)
    server["/v1/index.json"] = _send(b"x" * 1000)
    with pytest.raises(preset_updates.FetchError, match="larger"):
        preset_updates._fetch("index.json")


def test_fetch_gives_up_on_a_trickle_at_the_deadline(server, monkeypatch):
    """One byte at a time never trips the per-read timeout; the total does."""
    monkeypatch.setattr(preset_updates, "TIMEOUT_SECONDS", 0.5)

    def trickle(request):
        request.send_response(200)
        request.send_header("Content-Length", "100")
        request.end_headers()
        try:
            for _ in range(100):
                request.wfile.write(b"x")
                request.wfile.flush()
                time.sleep(0.05)
        except OSError:
            return  # the client gave up, as it should

    server["/v1/index.json"] = trickle
    started = time.monotonic()
    with pytest.raises(preset_updates.FetchError, match="longer"):
        preset_updates._fetch("index.json")
    assert time.monotonic() - started < 2


@pytest.mark.parametrize(
    "text",
    [b'{"presets": [], "x": NaN}', b'{"presets": [], "x": -Infinity}', b"[" * 100_000],
)
def test_index_text_without_finite_values_or_too_deep_is_refused(site, text):
    site["files"]["index.json"] = text
    result = preset_updates.check()
    assert result["updated"] == [] and "pixlstash.dev" in result["error"]


def test_a_second_entry_for_an_id_is_not_fetched_again(site):
    index = json.loads(site["files"]["index.json"])
    index["presets"].append(dict(index["presets"][0]))
    site["files"]["index.json"] = json.dumps(index).encode()
    assert len(preset_updates.check()["updated"]) == 1
    assert site["fetched"] == ["index.json", "zimage-turbo.json"]
