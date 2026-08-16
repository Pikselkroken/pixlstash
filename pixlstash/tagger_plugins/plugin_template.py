# Copyright 2026 Gaute Lindkvist
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Tagger/captioner plugin template — copy this to your user plugin directory.

Licensing note:
    This template and :class:`~pixlstash.tagger_plugins.base.TaggerPlugin` are
    MIT-licensed so external plugins can be authored under any license.

This file is never loaded by PixlStash.  The registry imports the built-in
plugins from an explicit list and only *scans* the user directory, so a
template sitting here needs no ignore-list entry.

Quick-start
-----------
1. Copy this file into the user tagger plugin directory below (create it if it
   does not exist).  PixlStash logs the path on startup and shows it under
   Settings → Auto-tagging — to the owner, on the machine running the server or
   its LAN; a share link or a remote browser is shown no folder at all.
2. Rename it and give the class a unique ``name``.
3. Fill in the parameters and ``generate_descriptions`` (and/or ``tag_images``).
4. Check it without a restart::

       pixlstash-cli plugins test ./my_captioner.py [--image sample.jpg]

   It loads the file the way the server does and says what would register, so a
   typo costs a command rather than a boot.  It is a development aid and not a
   security scanner: it *runs* the plugin, unsandboxed, as you.
5. **Restart PixlStash Server** — discovery only runs at start-up.

Your plugin then appears in the Description plugin (and/or Tag plugin) table,
with the settings UI built automatically from ``parameter_schema()``.

User plugin directories
-----------------------
  Linux   : ~/.local/share/pixlstash/tagger-plugins/user/
  macOS   : ~/Library/Application Support/pixlstash/tagger-plugins/user/
  Windows : %LOCALAPPDATA%\\pixlstash\\pixlstash\\tagger-plugins\\user\\

The doubled folder on Windows is not a typo.  Take the exact path from Settings →
Auto-tagging, where PixlStash prints it for a local owner — a folder in the wrong
place is skipped in silence.

A plugin may be a single ``.py`` file, or a folder containing ``__init__.py``
plus any helper modules it imports relatively.

See ``docs/writing-tagger-plugins.md`` for the full contract.
"""

from __future__ import annotations

from typing import Any

from pixlstash.tagger_plugins.base import TaggerPlugin


class MyCaptioner(TaggerPlugin):
    """One-line description of what this captioner does."""

    # Unique snake_case identifier.  A name already taken by a built-in
    # plugin is rejected — the plugin is skipped and the reason is listed
    # under Settings → Auto-tagging (local owner only, like the folder: the
    # reason is exception text and can name any path on the host).
    name = "my_captioner"

    # Label shown in the settings table.
    display_name = "My Captioner"

    description = "Describe what your captioner does."

    # Who wrote it and under what terms.  Keep these plain literals: tools read
    # the header straight off the source with ``ast``, without importing you.
    author = "Your Name <you@example.com>"  # an email address or a URL
    license = "MIT"  # your *code*, an SPDX identifier where there is one

    # One entry per model or remote service you load, empty when you load none.
    # This is the one a user actually needs — your code's license says nothing
    # about the weights you download.
    models = [
        {"name": "microsoft/Florence-2-base", "license": "MIT"},
    ]

    # Capability flags decide which table the plugin appears in.  Implement
    # tag_images() as well if you set supports_tags.
    supports_tags = False
    supports_descriptions = True

    # True if the model must be fetched before first use.  When True, the UI
    # offers a download button that calls download().
    requires_download = False

    def __init__(self) -> None:
        self._model = None
        self._device = "cpu"

    # ------------------------------------------------------------------
    # Schema — this JSON *is* the settings UI
    # ------------------------------------------------------------------

    def parameter_schema(self) -> list[dict[str, Any]]:
        """Declare the parameters shown in the plugin's settings dialog.

        Types: "number", "integer", "boolean", "select", "string",
        "textarea", "csv-int".  Numeric types accept "min"/"max"/"step";
        "select" requires "options" as ``[{"value": ..., "label": ...}]``.
        """
        return [
            {
                "name": "prompt",
                "label": "Prompt",
                "type": "textarea",
                "default": "Describe this image.",
                "description": "Instruction sent to the model.",
            },
            {
                "name": "max_tokens",
                "label": "Max tokens",
                "type": "integer",
                "default": 128,
                "min": 16,
                "max": 1024,
                "step": 16,
                "description": "Upper bound on caption length.",
            },
        ]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def setup(self, device: str) -> None:
        """Optional hook — receives the inference device ("cuda", "cpu", …).

        The workflow calls this via ``hasattr`` before ``init()``, so it is
        the only way to learn which device to load onto.  Omit the method
        entirely if you do not care.
        """
        self._device = device

    def needs_download(self, parameters: dict[str, Any] | None = None) -> bool:
        """Return True when the model files are not present locally."""
        return False

    def download(self, parameters=None, progress_callback=None) -> None:
        """Fetch model files.  No-op here; see JoyCaption for a real one."""

    def init(self, parameters: dict[str, Any]) -> None:
        """Load the model.  Must be idempotent — it is called per batch."""
        if self._model is not None:
            return
        # self._model = load_your_model(...).to(self._device)
        self._model = object()

    def unload(self) -> None:
        """Release the model.

        Abstract, so you must implement it — but be aware that nothing
        currently calls it on a third-party plugin: the idle-unload path
        knows the built-in services by name and does not walk the plugin
        registry.  A model you load here stays resident for the life of the
        process.  Manage it yourself if that matters.
        """
        self._model = None

    def is_loaded(self) -> bool:
        """Return True while the model is resident and ready."""
        return self._model is not None

    def estimated_vram_mb(
        self, image_count: int, parameters: dict[str, Any] | None = None
    ) -> int:
        """Estimated VRAM for a batch, in MB.

        Nothing calls this on a third-party plugin yet — the description
        workflow charges the budget for Florence-2 only — so overriding it
        is forward-looking rather than protective.  Keep your own footprint
        modest; another model may be scheduled alongside yours regardless.
        """
        return 0

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def generate_descriptions(
        self,
        image_paths: list[str],
        parameters: dict[str, Any],
        stop_event=None,
    ) -> dict[str, str | None]:
        """Caption a batch of images.

        Args:
            image_paths: Absolute image/video paths, in order.
            parameters: Values keyed by the ``name`` in ``parameter_schema``,
                already merged over your defaults.
            stop_event: ``threading.Event`` set when the user cancels.  The
                description workflow does not pass one today, so this is
                always ``None`` here — guard the access, as below.

        Returns:
            ``{path: caption}``.  Map a path to ``None`` to report a
            per-image failure; the rest of the batch is still stored.
        """
        prompt = parameters.get("prompt") or "Describe this image."
        max_tokens = int(parameters.get("max_tokens") or 128)

        results: dict[str, str | None] = {}
        for path in image_paths:
            if stop_event is not None and stop_event.is_set():
                break
            try:
                # ----------------------------------------------------------
                # YOUR INFERENCE GOES HERE
                # ----------------------------------------------------------
                results[path] = f"{prompt} ({max_tokens} tokens, {self._device})"
            except Exception as exc:
                print(f"Captioning failed for {path}: {exc}")
                results[path] = None  # per-image failure signal
        return results
