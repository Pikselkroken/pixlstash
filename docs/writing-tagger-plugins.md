# Writing a tagger / captioner plugin

PixlStash loads user-supplied captioning and tagging engines from a folder on your
machine. Anything that can turn an image path into a caption — Qwen-VL, Moondream2, a
Florence-2 promptgen fine-tune, a GGUF model through `llama-cpp-python`, a remote API —
can be plugged in without touching PixlStash itself.

Start from `pixlstash/tagger_plugins/plugin_template.py`; this document is the contract
behind it.

## 1. Where plugins live

| OS | Folder |
|----|--------|
| Linux | `~/.local/share/pixlstash/tagger-plugins/user/` |
| macOS | `~/Library/Application Support/pixlstash/tagger-plugins/user/` |
| Windows | `%LOCALAPPDATA%\pixlstash\pixlstash\tagger-plugins\user\` |

(The doubled `pixlstash` on Windows is `platformdirs` inserting the app author, which
defaults to the app name. It is not a typo.)

**The folder does not exist until you create it, and a folder in the wrong place is
skipped in silence.** So take the exact path for your install from **Settings →
Auto-tagging**, where it is displayed, rather than from the table above; it is also
logged at start-up. The path — and the list of plugins that failed to load — is shown
only to the owner, on the machine running the server or on its LAN (or from anywhere if
the server sets `allow_remote_host_ops`, the same flag that governs every other
host-filesystem operation). A share link never sees either. Installing a plugin means
writing a file into that folder, so there is nothing to do remotely anyway.

Two shapes are accepted:

- a single `.py` file — `my_captioner.py`
- a folder containing `__init__.py` — for a plugin that ships helper modules, which it
  imports relatively (`from . import helper`)

Entries whose name starts with `.` or `_` are skipped, as is any other file type.

**Discovery runs once, at start-up. Restart the server after adding or editing a
plugin.** There is deliberately no reload button: re-instantiating a plugin whose model
is resident would orphan that model in VRAM.

**So check your plugin from the command line instead of paying a restart per typo:**

```bash
pixlstash-cli plugins test ./my_captioner.py            # or the folder
pixlstash-cli plugins test ./my_captioner.py --image ~/Pictures/sample.jpg
```

It imports the file exactly as the server does — same module namespacing, so a folder
plugin's `from . import helper` resolves here too — instantiates every plugin class the
module *defines*, calls `plugin_schema()`, and checks the parameter schema against what
the settings screen can actually render (§3), which the server does not check. `--image`
also runs the plugin over one picture with your defaults and prints what came back.

It asks your `needs_download()` first and stops if you answer yes, so a check does not
start a multi-gigabyte fetch nobody asked for — but that is a courtesy and not a
guarantee. If your `init()` downloads (which is what `from_pretrained_local_first` does,
and therefore what the shipped captioners do), it downloads here too. Fetch models from
**Settings → Auto-tagging** first if you would rather it did not.

It reports two severities and only one of them is a refusal. A **problem** — it did not
load, a parameter has no `name` or `default`, a `type` that will not render, a name a
built-in or an installed plugin already holds — exits `1`. A **warning** — a parameter
with no `label`, or neither capability flag set — is printed and exits `0`, because those
plugins do work: the settings screen falls back to the parameter's `name`, and a plugin
with no flags registers exactly as written, it is simply never called. Script against the
exit code and you are scripting against "will this work", not "is this tidy".

**It is a development aid, not a security scanner.** It does not tell you whether a plugin
is safe — it *runs* it, in that process, with your permissions, exactly as the server
would. Nothing is sandboxed and nothing inspects what the code does, so the rule for
`plugins test` is the same as the rule for installing: only run a plugin you would have
installed anyway. Checking somebody else's plugin with this command is not a way to find
out whether you should trust it; by the time it prints anything, their code has run.

Passing is a contract check and neither a quality one nor a safety one: nothing here says
the captions are any good, and a plugin that hangs at import will hang the server's boot
the same way it hangs this command.

Plugin code runs unsandboxed, in the server process, with your permissions. Only install
plugins you trust — the same caveat as the image plugins.

## 2. The minimum plugin

Subclass `TaggerPlugin` and set the capability flags. The class must be *defined* in the
module you drop in (a subclass merely imported into it is ignored, so importing another
plugin does not register it twice). A module may define several plugins; all of them are
registered.

```python
from pixlstash.tagger_plugins.base import TaggerPlugin


class MyCaptioner(TaggerPlugin):
    name = "my_captioner"            # unique snake_case id
    display_name = "My Captioner"    # label in the settings table
    description = "What it does."
    author = "Your Name <you@example.com>"   # email address or URL
    license = "MIT"                  # your own code, SPDX where there is one
    models = [                       # every model or service you load
        {"name": "microsoft/Florence-2-base", "license": "MIT"},
    ]
    supports_descriptions = True     # appears in the Description plugin table
    supports_tags = False            # ...and/or the Tag plugin table
    requires_download = False        # True offers a download button
    default_enabled = False          # tag plugins only: on by default?
```

`name` must be non-empty and must not collide with an existing plugin. **Built-in plugins
are loaded first and win a collision** — a user plugin named `wd14`, `pixlstash_tagger`,
`florence2` or `joycaption` is skipped and shown as a load error rather than silently
replacing (or being silently bypassed by) the built-in.

A plugin that raises on import, that raises on construction, or whose
`parameter_schema()` raises does not stop the others: the failure is logged, listed under
**Settings → Auto-tagging** with its message (to a local owner — the message is exception
text and can name any path on the host), and the server boots normally. (The schema
is exercised once at load precisely so a later failure cannot take the settings screen —
or the boot — down with it.)

## 3. `parameter_schema()` — this JSON *is* the settings UI

Return a list of parameter definitions. PixlStash builds the plugin's settings dialog
straight from it, so there is nothing else to write for the UI. Saved settings are
validated against the parameter *names* you declare; values are not type-checked, so read
them defensively (`int(parameters.get("max_tokens") or 128)`).

Required keys: `name` (snake_case), `label`, `type`, `default`.
Optional: `description` (tooltip), `min` / `max` / `step` (numeric types),
`options` (required for `select`, as `[{"value": ..., "label": ...}]`).

| `type` | Control |
|--------|---------|
| `number` | Numeric input / slider (float) |
| `integer` | Numeric input (int) |
| `boolean` | Checkbox |
| `select` | Dropdown — needs `options` |
| `string` | Single-line text field |
| `textarea` | Multi-line text field |
| `csv-int` | Comma-separated integers |

Write those. Two spellings beyond them are *accepted* by the component and are not worth
using: `bool` is an alias for `boolean`, and a `select` may name its choices `enum`
instead of `options`. `pixlstash-cli plugins test` mirrors what
`TaggerParametersUI.vue` renders rather than what this table recommends, so it passes both
— a guardrail test keeps that list and the component in step.

None of the ways to get this wrong raise anywhere. A `type` the component has no branch
for renders as a plain text box, and so does a `select` with neither `options` nor `enum`;
give it an *empty* `options` list instead and you get a real dropdown with nothing in it.
Silent in all three cases, which is why `plugins test` checks it.

```python
def parameter_schema(self):
    return [
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
```

Saved values live in the user's `tagger_settings` JSON under your plugin's name. A
parameter you add later is filled in from its `default` for existing users, so schema
changes need no migration — but **renaming a parameter loses its saved value**.

## 4. Lifecycle

`init()` is called before every batch and must be idempotent. `is_loaded()` must tell the
truth between calls — the settings table polls it.

| Method | Required | Called by PixlStash today |
|--------|----------|---------------------------|
| `setup(device)` | optional | **Yes** — via `hasattr`, just before `init()`. The only way to learn the device (`"cuda"`, `"cpu"`, …), so implement it if you use a GPU. |
| `init(parameters)` | yes | **Yes**, before every batch. Return early when already loaded. |
| `is_loaded()` | yes | **Yes** — the settings table, and `plugin_schema()`. |
| `unload()` | yes (abstract) | **Yes** — when the workers go idle with "Keep models in memory" off. See below. |
| `estimated_vram_mb(image_count, parameters)` | no | **Yes** for a description plugin, before its batch is scheduled. Not yet for a tag plugin. See below. |
| `effective_batch_size(parameters)` | no | **Yes** — it caps the `image_count` your VRAM estimate is asked about, and sizes a tag batch. |

**Two of these arrive from outside your batch**, so they are worth reading before you
implement them (issue #967 wired both; older guides say they are never called):

- **`unload()` is called when the user turns "Keep models in memory" off and every worker
  has gone idle.** The host unloads its own services first and then walks the plugin
  registry, calling `unload()` on every plugin whose `is_loaded()` returns true. It runs
  on the sweep's thread, not yours. The idle condition means it will not normally land
  mid-batch, but it is not a guarantee you should lean on: **serialise unload against
  load** — the built-in services hold one lock across both — rather than freeing memory
  another thread is still writing into, which is a segfault and not an exception. A raise
  here is logged and contained; it will not stop the other plugins being released.
- **`estimated_vram_mb()` is asked before a description batch runs**, when your plugin is
  the active description plugin. (A *tag* plugin is not asked yet — `TaggingWorkflow`
  still bills its own constants.) `image_count` is already capped at your
  `effective_batch_size(parameters)`.

  **0 has two meanings and the host has to guess.** `TaggerPlugin.estimated_vram_mb`
  documents 0 as "CPU-only", and 0 is also what the default returns for a plugin that
  never overrode the method. On a CUDA engine the host cannot distinguish them, so it
  reads any 0 as *no answer* and charges the Florence-2 figure instead — around 900 MB
  for `base`, 2.6 GB for `large-ft`. Which way that hurts depends on which you meant:

  | You return | You meant | What actually happens |
  |---|---|---|
  | 0 (no override) | "I haven't thought about it" | Billed the Florence figure. Fine for a small model, badly wrong for a big one. |
  | 0 (CPU-only model) | "I need no VRAM" | Billed the Florence figure anyway. Harmless over-charge: your batch may wait, it will not break. |
  | 0 (GPU model, not loaded yet) | "nothing is resident *right now*" | **The dangerous one.** Billed ~1 GB for the several you are about to allocate, and another model is scheduled alongside you. |
  | a real figure | "this is what I will occupy" | Budgeted correctly. |

  So charge for the **cold start** as well as the warm one — `JoyCaptionPlugin` bills its
  full 8 GB footprint until its weights are actually sitting on the CPU — and keep 0 for
  a model that genuinely holds nothing on the card.

## 5. Inference

```python
def generate_descriptions(self, image_paths, parameters, stop_event=None):
    return {path: "a caption" for path in image_paths}

def tag_images(self, image_paths, parameters, preloaded=None, stop_event=None):
    return {path: [TagResult(tag="cat", confidence=0.91)] for path in image_paths}
```

- `image_paths` are absolute paths, and may include video files — check the extension if
  you cannot handle them.
- `parameters` arrives already merged over your `default_params()`.
- **Map a path to `None` to report a per-image failure.** That is the documented signal;
  the rest of the batch is still stored. Raising instead loses the whole batch.
- `stop_event` is a `threading.Event` set when the user cancels or the server shuts
  down. **Check it between images and return what you have** — a batch that runs to
  the end regardless holds up shutdown for as long as your slowest image takes.
  `DescriptionWorkflow.generate_batch` passes one to `generate_descriptions`; the tag
  path does **not** pass one to `tag_images` yet (`TagTask` calls
  `TaggingWorkflow.tag_images` without it), and nothing stops a caller invoking your
  plugin directly. So it can still be `None` — guard the access.
- `TagResult.confidence` may be `None` for models that do not produce probabilities.

## 6. Downloads

A plugin that fetches weights implements the quartet. `JoyCaptionPlugin`
(`pixlstash/tagger_plugins/joycaption.py`) is the reference implementation.

| Method | Contract |
|--------|----------|
| `needs_download(parameters)` | `True` when the files are absent. Drives the download button. |
| `download(parameters, progress_callback)` | Fetch the files. Runs on a background thread. |
| `list_downloaded_artifacts()` | List of dicts, each with **`"name"`** and `"size_bytes"`; `"label"` is shown if present. |
| `delete_artifact(name)` | Remove one artifact by that `"name"`. Raise `ValueError` for an unknown one. |

**Use `"name"` as the artifact key.** `DELETE /taggers/{name}/artifacts/{id}` matches on
`"name"`. The built-in JoyCaption plugin emits `"id"` instead, so copy the route's
expectation rather than that plugin's dict.

Pin your revisions. An unpinned HuggingFace ref is a silent supply-chain change.

## 7. Dependencies

Whatever your plugin imports must already be installed in the environment PixlStash runs
in — `pip install llama-cpp-python`, and so on. PixlStash does not read a manifest and
will not install anything for you; a missing import simply shows up as that plugin's load
error. Say what you need in your plugin's own README.

## 8. Licensing

`pixlstash/tagger_plugins/base.py` and `plugin_template.py` are MIT-licensed, as an
explicit exception to the GPL-3.0 backend, so your plugin can carry whatever license you
like (see [licensing.md](licensing.md)). Importing anything else from `pixlstash` puts
you back under the GPL.

**Say which license, in the class.** `author`, `license` and `models` default to `""`,
`""` and `[]`, so a plugin that omits them still loads and a caller simply shows nothing
for it — but they are how anyone finds out what they are about to run before they run it.
`models` is the one that matters: it lists every model or remote service the plugin loads,
each entry a `{"name": ..., "license": ...}` dict, because *your* license says nothing
about the weights you download. Use an SPDX identifier where the model declares one, and
where it does not, say plainly what it does ship rather than guessing an SPDX id at it.

The built-ins declare theirs:

| Plugin | Code | Models |
|---|---|---|
| `florence2` | `GPL-3.0-only` | `florence-community/Florence-2-base`, `…-large-ft` — MIT |
| `wd14` | `GPL-3.0-only AND Apache-2.0` (it adapts Kohya_ss) | `SmilingWolf/wd-convnext-tagger-v3` — Apache-2.0 |
| `joycaption` | `GPL-3.0-only` | `fancyfeast/llama-joycaption-beta-one-hf-llava` — no declared license; ships the Llama 3.1 Community License |
| `pixlstash_tagger` | `GPL-3.0-only` | `PersonalJeebus/pixlvault-anomaly-tagger` — MIT |

Keep all three as plain literals. They are meant to be read off the source with `ast`,
without importing the module and running it, so a computed value reads as absent.

## 9. Known limitations

- **The model shelf will not label your model.** Weights in the HuggingFace cache appear
  on the shelf via the cache scan, but the repo → capability map is hand-maintained
  (`pixlstash/services/model_features.py`), so a third-party model shows up without a
  capability tag.
- **`florence2` is special-cased.** `DescriptionWorkflow` routes that name down a native
  fast path, and it is also the fallback when the configured description plugin fails to
  initialise. You cannot override it by taking the name.
- **No reload.** Restart after every edit.
