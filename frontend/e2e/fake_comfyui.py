#!/usr/bin/env python3
"""A fake ComfyUI for the Playwright e2e backend (#2360).

Playwright's second ``webServer`` runs this script, and the e2e backend's
ComfyUI address points at it (``e2e_comfyui_url``). It answers the routes
PixlStash calls the way ComfyUI does, so a spec can run a workflow through the
real backend with no ``page.route`` on the run routes:

* ``GET /system_stats``, ``GET /object_info[/<class>]``, ``GET /extensions``
  (the ComfyUI-PixlStash scripts, so the pack reads as installed), ``GET /queue``
  (always empty: every prompt finishes as it is queued), ``POST /queue`` and
  ``POST /interrupt``;
* ``POST /prompt`` records the graph and "runs" it at once: each ``SaveImage``
  makes one small PNG with the graph in its ``prompt`` chunk, as ComfyUI's
  does, and ``GET /history/<id>`` reports it done with those images;
* ``GET /view`` serves those PNGs, which is how PixlStash fetches and imports
  a run's pictures.

It never sees a ``PixlStashPictureSaver``: PixlStash swaps every one for a
``SaveImage`` before it submits (``swap_pixlstash_savers``) and files the
pictures itself, under the project, set and people the run names.

For specs, under ``/__fake/``:

* ``GET /__fake/prompts``: every graph submitted, oldest first, as
  ``{prompt_id, client_id, prompt}``. Find yours by the ``prompt_id`` the run
  answered with: the fake is shared by every spec in a run.
* ``PUT /__fake/fail`` with ``{"mode": ...}``: ``missing_models`` lists no model
  file in ``object_info`` (the pre-flight refuses), ``node_error`` answers
  ``POST /prompt`` with ComfyUI's validation failure, ``null`` turns it off. A
  spec that sets one turns it off again (``afterEach``).

Binds 127.0.0.1 only, and never 8188, so it can neither answer for nor collide
with a real ComfyUI on the machine. Stdlib and Pillow only.

Env:
    PIXLSTASH_E2E_COMFYUI_PORT   Port to bind (default 9800).
"""

import io
import json
import os
import struct
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from PIL import Image
from PIL.PngImagePlugin import PngInfo

# The launcher's seeded model folder: object_info lists what it holds.
from serve_e2e_backend import MODEL_FOLDER

PORT = int(os.environ.get("PIXLSTASH_E2E_COMFYUI_PORT", "9800"))
FAIL_MODES = {None, "missing_models", "node_error"}
# The pack's scripts PixlStash looks for (comfyui_service.PACK_*).
PACK_SCRIPTS = [
    f"/extensions/ComfyUI-PixlStash/{name}"
    for name in ("open_workflow.js", "save_workflow.js", "open_run.js")
]

_lock = threading.Lock()
_prompts: list[dict] = []
_history: dict[str, dict] = {}
_images: dict[str, bytes] = {}
_fail: dict = {"mode": None}


def _model_files() -> tuple[list[str], list[str]]:
    """``(checkpoints, loras)`` in the seeded model folder, by file name.

    Read per request: the launcher lays the folder out while this starts. A
    file whose header names a ``lora`` tensor is a LoRA, as the shelf's
    scanner decides; everything else is a checkpoint.
    """
    checkpoints, loras = [], []
    for path in sorted(MODEL_FOLDER.rglob("*.safetensors")):
        try:
            with path.open("rb") as fh:
                (length,) = struct.unpack("<Q", fh.read(8))
                header = json.loads(fh.read(length))
        except (OSError, struct.error, ValueError) as exc:
            # A shelf spec's move can leave a file half copied for a moment.
            sys.stderr.write(f"[fake-comfyui] skipping unreadable {path.name}: {exc}\n")
            continue
        is_lora = any("lora" in key.lower() for key in header)
        (loras if is_lora else checkpoints).append(path.name)
    return checkpoints, loras


def _node(inputs: dict, outputs: list[str], *, output_node=False, optional=None):
    """One ``object_info`` entry, in ComfyUI's shape."""
    return {
        "input": {"required": inputs, **({"optional": optional} if optional else {})},
        "output": outputs,
        "output_is_list": [False] * len(outputs),
        "output_name": outputs,
        "output_node": output_node,
        "category": "fake",
    }


def _object_info() -> dict:
    checkpoints, loras = (
        ([], []) if _fail["mode"] == "missing_models" else _model_files()
    )
    strength = ["FLOAT", {"default": 1.0, "min": -100.0, "max": 100.0}]
    pixlstash = {"forceInput": True}
    return {
        "CheckpointLoaderSimple": _node(
            {"ckpt_name": [checkpoints]}, ["MODEL", "CLIP", "VAE"]
        ),
        "LoraLoader": _node(
            {
                "model": ["MODEL"],
                "clip": ["CLIP"],
                "lora_name": [loras],
                "strength_model": strength,
                "strength_clip": strength,
            },
            ["MODEL", "CLIP"],
        ),
        "LoraLoaderModelOnly": _node(
            {"model": ["MODEL"], "lora_name": [loras], "strength_model": strength},
            ["MODEL"],
        ),
        "CLIPTextEncode": _node(
            {"text": ["STRING", {"multiline": True}], "clip": ["CLIP"]},
            ["CONDITIONING"],
        ),
        "EmptyLatentImage": _node(
            {
                "width": ["INT", {"default": 512}],
                "height": ["INT", {"default": 512}],
                "batch_size": ["INT", {"default": 1}],
            },
            ["LATENT"],
        ),
        "KSampler": _node(
            {
                "model": ["MODEL"],
                "seed": ["INT", {"default": 0, "min": 0, "max": 2**64 - 1}],
                "steps": ["INT", {"default": 20}],
                "cfg": ["FLOAT", {"default": 8.0}],
                "sampler_name": [["euler", "euler_ancestral", "dpmpp_2m"]],
                "scheduler": [["normal", "karras", "simple"]],
                "positive": ["CONDITIONING"],
                "negative": ["CONDITIONING"],
                "latent_image": ["LATENT"],
                "denoise": ["FLOAT", {"default": 1.0}],
            },
            ["LATENT"],
        ),
        "VAEDecode": _node({"samples": ["LATENT"], "vae": ["VAE"]}, ["IMAGE"]),
        "SaveImage": _node(
            {
                "images": ["IMAGE"],
                "filename_prefix": ["STRING", {"default": "ComfyUI"}],
            },
            [],
            output_node=True,
        ),
        # The pack's LoRA loader, which loads a shelf file by its digest: how a
        # run adds a LoRA this ComfyUI's folders do not hold, such as one just
        # trained (round-trip.spec.js).
        "PixlStashAdapterLoader": _node(
            {
                "model": ["MODEL"],
                "adapter_kind": [["— Any —", "lora"]],
                "base_model": [["(loading…)"]],
                "adapter_sha256": ["STRING", {"default": ""}],
            },
            ["MODEL", "CLIP", "STRING"],
            optional={
                "clip": ["CLIP"],
                "strength_model": strength,
                "strength_clip": strength,
            },
        ),
        "PixlStashPictureSaver": _node(
            {
                "images": ["IMAGE"],
                "filename_prefix": ["STRING", {"default": "comfyui"}],
                "save_workflow": ["BOOLEAN", {"default": True}],
            },
            ["STRING"],
            output_node=True,
            optional={
                "pixlstash_project": ["PIXLSTASH_PROJECT", pixlstash],
                "pixlstash_set": ["PIXLSTASH_SET", pixlstash],
                "pixlstash_character": ["PIXLSTASH_CHARACTER", pixlstash],
            },
        ),
    }


def _png(prompt: dict) -> bytes:
    """A small picture of its own, with the graph in it as ComfyUI writes it.

    Random pixels, so no two runs make the same picture and the import never
    files one as a duplicate of the last.
    """
    image = Image.frombytes("RGB", (32, 32), os.urandom(32 * 32 * 3))
    info = PngInfo()
    info.add_text("prompt", json.dumps(prompt))
    buf = io.BytesIO()
    image.save(buf, format="PNG", pnginfo=info)
    return buf.getvalue()


def _run(prompt_id: str, number: int, prompt: dict) -> dict:
    """The history entry of *prompt* run to the end: one PNG per SaveImage."""
    outputs = {}
    for node_id, node in prompt.items():
        if node.get("class_type") != "SaveImage":
            continue
        prefix = node.get("inputs", {}).get("filename_prefix") or "ComfyUI"
        filename = f"{prefix}_{number:05d}_.png"
        _images[filename] = _png(prompt)
        outputs[node_id] = {
            "images": [{"filename": filename, "subfolder": "", "type": "output"}]
        }
    return {
        "prompt": [number, prompt_id, prompt, {}, list(outputs)],
        "outputs": outputs,
        "status": {
            "status_str": "success",
            "completed": True,
            "messages": [
                ["execution_start", {"prompt_id": prompt_id}],
                ["execution_success", {"prompt_id": prompt_id}],
            ],
        },
        "meta": {},
    }


def _node_error(prompt: dict) -> dict:
    """ComfyUI's 400 for a graph that fails validation, on its first node."""
    node_id, node = next(iter(prompt.items()))
    return {
        "error": {
            "type": "prompt_outputs_failed_validation",
            "message": "Prompt outputs failed validation",
            "details": "",
        },
        "node_errors": {
            node_id: {
                "class_type": node.get("class_type"),
                "dependent_outputs": [],
                "errors": [
                    {
                        "type": "fake_node_error",
                        "message": "Fake node error",
                        "details": "The fake ComfyUI was told to fail",
                    }
                ],
            }
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "FakeComfyUI/1.0"

    def _send(self, status: int, body, content_type="application/json") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        """The request's JSON, or ``None`` when it cannot be read as JSON."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(length) or b"null")
        except ValueError:
            return None

    def do_GET(self):
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        with _lock:
            if path == "/system_stats":
                return self._send(
                    200,
                    {
                        "system": {
                            "os": "posix",
                            "comfyui_version": "0.3.60",
                            "python_version": sys.version,
                            "embedded_python": False,
                            "argv": ["main.py"],
                        },
                        "devices": [
                            {
                                "name": "cpu",
                                "type": "cpu",
                                "index": None,
                                "vram_total": 0,
                                "vram_free": 0,
                            }
                        ],
                    },
                )
            if path == "/object_info":
                return self._send(200, _object_info())
            if path.startswith("/object_info/"):
                name = path.removeprefix("/object_info/")
                info = _object_info()
                return self._send(200, {name: info[name]} if name in info else {})
            if path == "/extensions":
                return self._send(200, PACK_SCRIPTS)
            if path == "/queue":
                return self._send(200, {"queue_running": [], "queue_pending": []})
            if path == "/history":
                return self._send(200, _history)
            if path.startswith("/history/"):
                prompt_id = path.removeprefix("/history/")
                entry = _history.get(prompt_id)
                return self._send(200, {prompt_id: entry} if entry else {})
            if path == "/view":
                image = _images.get((query.get("filename") or [""])[0])
                if image is None:
                    return self._send(404, {"error": "no such image"})
                return self._send(200, image, "image/png")
            if path == "/__fake/prompts":
                return self._send(200, _prompts)
        self._unknown()

    def do_POST(self):
        path = urlsplit(self.path).path
        with _lock:
            if path == "/prompt":
                body = self._body()
                prompt = body.get("prompt") if isinstance(body, dict) else None
                if not isinstance(prompt, dict) or not prompt:
                    # ComfyUI's own answer to a graph it cannot read.
                    error = {"type": "invalid_prompt", "message": "Invalid prompt"}
                    return self._send(400, {"error": error, "node_errors": {}})
                if _fail["mode"] == "node_error":
                    return self._send(400, _node_error(prompt))
                prompt_id = str(uuid.uuid4())
                number = len(_prompts)
                _prompts.append(
                    {
                        "prompt_id": prompt_id,
                        "client_id": body.get("client_id"),
                        "prompt": prompt,
                    }
                )
                _history[prompt_id] = _run(prompt_id, number, prompt)
                return self._send(
                    200, {"prompt_id": prompt_id, "number": number, "node_errors": {}}
                )
            if path in ("/queue", "/interrupt"):
                return self._send(200, {})
        self._unknown()

    def do_PUT(self):
        if urlsplit(self.path).path == "/__fake/fail":
            body = self._body()
            mode = body.get("mode", "") if isinstance(body, dict) else ""
            if mode not in FAIL_MODES:
                return self._send(400, {"error": f"mode is one of {FAIL_MODES}"})
            with _lock:
                _fail["mode"] = mode
            return self._send(200, _fail)
        self._unknown()

    def _unknown(self):
        # Said out loud: a route PixlStash calls that the fake does not answer
        # is the first thing to look for when a spec against it fails.
        sys.stderr.write(f"[fake-comfyui] not faked: {self.command} {self.path}\n")
        self._send(404, {"error": "not faked"})

    def log_message(self, format, *args):
        # Quiet: the routes it answers are the expected traffic.
        pass


def main() -> int:
    if PORT == 8188:
        sys.stderr.write("[fake-comfyui] refusing 8188, ComfyUI's own port\n")
        return 1
    print(f"[fake-comfyui] listening on http://127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
