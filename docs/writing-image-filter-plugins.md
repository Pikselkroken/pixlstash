# Writing an image filter plugin

PixlStash loads user-supplied image transforms from a folder on your machine. A plugin
takes PIL images in and gives PIL images back — a filter, a resize, an upscale, a
watermark, a caption-conditioned overlay, anything — and its outputs are imported as new
pictures beside the originals.

Start from `pixlstash/image_plugins/built-in/plugin_template.py`; this document is the
contract behind it. Its sibling is `docs/writing-tagger-plugins.md`, for plugins that
*describe* a picture rather than transform it. **The two systems are deliberately similar
but not identical, and §10 lists every place they differ** — read it if you have written
a tagger plugin already, because three of the differences will bite you.

## 1. Where plugins live

| OS | Folder |
|----|--------|
| Linux | `~/.local/share/pixlstash/image-plugins/user/` |
| macOS | `~/Library/Application Support/pixlstash/image-plugins/user/` |
| Windows | `%LOCALAPPDATA%\pixlstash\pixlstash\image-plugins\user\` |

(The doubled `pixlstash` on Windows is `platformdirs` inserting the app author, which
defaults to the app name. It is not a typo.)

**The folder does not exist until you create it, and a folder in the wrong place is
skipped in silence.** The path is logged at start-up.

`pixlstash-cli plugins install ./my_filter.py` works out the folder and the filename for
you, and refuses the shapes that fail silently; `plugins list` and `plugins remove` are
its siblings. Copying the file in by hand still works and is what the command does.

**Only single `.py` files are loaded.** Unlike tagger plugins, a folder with an
`__init__.py` is *not* scanned, so a plugin that wants helper modules has to inline them
or install them as a normal importable package.

**Discovery re-runs on every call.** The registry reloads before listing plugins and
before every run, so an edited file takes effect the next time the plugin menu opens —
no restart. That is the one place the image system is friendlier than the tagger one, and
it is possible only because image plugins hold no model.

A file named `plugin_template.py` is skipped by name, so the shipped template can sit in
the folder without registering.

Plugin code runs unsandboxed, in the server process, with your permissions. Only install
plugins you trust.

## 2. The minimum plugin

Subclass `ImagePlugin` and implement `parameter_schema()` and `run()`.

```python
from typing import Any

from PIL import Image

from pixlstash.image_plugins.base import ImagePlugin


class MyFilter(ImagePlugin):
    name = "my_filter"                 # unique snake_case id
    display_name = "My Filter"         # label in the menus
    description = "What it does."      # shown above the parameters
    supports_images = True             # offered for still images
    supports_videos = False            # ...and/or video files, via run_video

    def parameter_schema(self) -> list[dict[str, Any]]:
        return []

    def run(self, images, parameters=None, progress_callback=None,
            error_callback=None, captions=None) -> list[Image.Image]:
        return [image.copy() for image in images]
```

`name` must be non-empty. **A user plugin whose name matches a built-in replaces the
built-in**, because the user folder is scanned first and the first registration of a name
wins. This is the opposite of the tagger system, where the built-in wins — so naming a
plugin `rotate`, `scaling`, `pixelate`, `blur_sharpen`, `colour_filter` or
`brightness_contrast` silently takes over that menu entry rather than being rejected.

**Only the first `ImagePlugin` subclass in the module is registered**, and the search does
not check where the class was defined. A subclass you `import` at the top of your file is
found before the class you wrote below it, and the import wins. Do not import other
plugins into a plugin.

`supports_images` and `supports_videos` decide where the plugin is offered: the grid
context menu, the selection bar and the overlay each hide a plugin that does not claim the
media type currently selected. A plugin with `supports_videos = False` is simply absent
when a video is selected, rather than failing when run.

## 3. `parameter_schema()` — this JSON *is* the settings UI

Return a list of parameter definitions. `PluginParametersUI.vue` builds the form straight
from it, so there is nothing else to write for the UI. Values arrive back in `run()` as
the `parameters` dict, keyed by `name`, and they are **not** type-checked — read them
defensively.

Required keys: `name` (snake_case), `label`, `type`, `default`. Optional: `description`
(help text under the field).

| `type` | Control |
|--------|---------|
| `number` / `integer` | Numeric input |
| `boolean` | Checkbox |
| `string` | Single-line text field — **or a dropdown, when `enum` is present** |

**A dropdown is `type: "string"` plus an `enum` list**, optionally with an `enumLabels`
map from value to display text:

```python
{
    "name": "algorithm",
    "label": "Algorithm",
    "type": "string",
    "default": "lanczos",
    "enum": ["bicubic", "bilinear", "lanczos", "nearest_neighbour"],
    "enumLabels": {"nearest_neighbour": "Nearest neighbour"},
    "description": "Interpolation algorithm used during scaling.",
}
```

`ImagePlugin.parameter_schema`'s own docstring and the shipped template both describe a
`"select"` type with an `"options"` key. **That form is not rendered as a dropdown** — the
UI has no `select` branch, so such a field falls through to a free-text input. Every
built-in uses `string` + `enum`; follow the built-ins, not the docstring. (Tagger plugins
*do* have a real `select` type with `options`. The two schemas are not the same schema.)

`base.py` gives you `_coerce_number` and `_coerce_positive_number` for reading numeric
parameters back safely.

## 4. `run()` — the transform

```python
def run(self, images, parameters=None, progress_callback=None,
        error_callback=None, captions=None) -> list[Image.Image]:
```

- `images` are PIL images, already EXIF-oriented (`ImageOps.exif_transpose`) and
  **converted to RGB** — there is no alpha channel on the way in, whatever the source
  file had.
- **The returned list must be the same length as `images`, in the same order.** A
  mismatch raises and the whole run fails, so on a per-image failure append a fallback
  (usually `image.copy()`) and report the error rather than dropping the entry.
- `captions` is one string per image — the picture's stored description, or `""` — so a
  transform can be caption-conditioned. The caller may override it, but you should treat
  it as "the text for this image".
- Return a **different size** freely; nothing downstream assumes the output matches the
  input.

Report progress and failures through the two helpers rather than by hand, so the payload
shape stays consistent — they become `PLUGIN_PROGRESS` WebSocket events that drive the
progress UI:

```python
self.report_progress(progress_callback, current=idx + 1, total=total,
                     message="Processed")
self.report_error(error_callback, index=idx, message="Failed",
                  details={"error": str(exc)})
```

Both are no-ops when the callback is `None`, so they are always safe to call.

`run()` executes on a worker thread (`asyncio.to_thread`), so it may block; it must not
assume an event loop.

## 5. Video

Set `supports_videos = True` and override `run_video(source_path, parameters, ...)`. It
receives a **path**, not frames, and returns either encoded `bytes` or a `(bytes,
extension)` tuple.

Do not write the decode/encode loop. `base.py` has it:

```python
def run_video(self, source_path, parameters=None, progress_callback=None,
              error_callback=None):
    strength = self._coerce_number((parameters or {}).get("strength"), 1.0)
    return self.transform_video(
        source_path,
        lambda frame: self._apply(frame, strength),
        progress_callback=progress_callback,
        error_callback=error_callback,
        error_message="Failed to process video",
        progress_verb="Blurred",
    )
```

`transform_video` opens the source, runs your per-frame callable over RGB PIL frames,
sizes the writer **from the first transformed frame** (so a transform that changes the
frame size needs no arithmetic of its own), tries several container/codec pairs until one
opens, keeps a `.webm` source in its own container where it can, reports per-frame
progress, and cleans up its temporary file. Your callable must return the same size for
every frame — OpenCV silently drops writes that disagree with the writer.

A video source reaching a plugin that only sets `supports_videos = True` without
overriding `run_video` falls back to the still-image path on the extracted frame, rather
than failing.

## 6. What happens to your output

Outputs are imported as new pictures, not written over the source. Per output:

- **Format follows the source** — a JPEG in, a JPEG out — unless you returned
  `(bytes, ".ext")`, which is taken as-is.
- **Placed in the source's stack** by default; the caller can pass `stack=false` to skip
  the stack placement while keeping every association below.
- **Set and project memberships are copied** from the source.
- **Faces are re-detected, never copied.** The output is given a `source_picture_id`, and
  the normal finders extract its real faces and inherit a character from the source only
  where the two faces actually match. This is why `get_bbox_transform()` exists and does
  nothing: see §9.
- A picture living in a **reference folder** has its output written next to it, inheriting
  the folder and the source's base file name.

## 7. Dependencies

`PIL`, `numpy` and `cv2` are already imported by the host, so use them freely. Anything
else must be installed into the same environment PixlStash runs in — there is no
per-plugin dependency manifest and no isolation. An `ImportError` at module scope is
caught and recorded as a load error rather than taking the server down, but the plugin
will simply be absent.

## 8. Licensing

`pixlstash/image_plugins/base.py` and the template are MIT-licensed carve-outs (see
`docs/licensing.md`) precisely so plugins can be authored under any license. The only
import a plugin should take from PixlStash is
`from pixlstash.image_plugins.base import ImagePlugin`. Anything deeper is not a contract.

## 9. Known limitations

Honest gaps, not things to design around:

- **A load error is not shown anywhere in the UI.** The registry records the file and the
  message, but `GET /pictures/plugins` deliberately no longer returns them: an error row
  carries the absolute path of the failing plugin, and that route is reachable by any
  token. **The server log is the only place a broken image plugin is reported.** (The
  tagger system moved its errors onto a local-owner-only route instead; doing the same
  here is worth an issue.)
- **`get_bbox_transform()` is never called.** Its only caller was the face-copy step,
  which was removed because copying a bbox through a transform assumed the output still
  contained the face. Implementing it has no effect today; `scaling` and `rotate` still
  do, and the contract is kept in case bbox mapping is needed again.
- **There is no cancellation.** No stop event is passed, and a long `run()` cannot be
  interrupted. Keep an eye on batch size.
- **There is no VRAM accounting.** Nothing asks an image plugin what it will use before
  scheduling it, so a GPU upscaler competes with the captioner and the face models on
  trust alone.
- **There is no download lifecycle.** Unlike tagger plugins there is no
  `needs_download()`/`download()` pair and no download button; a plugin that needs weights
  has to fetch them itself, on first use, without a UI.

## 10. Differences from tagger plugins

If you have written one of the other kind, these are the ones that will catch you out —
the first three silently, which is why they are first.

| | Image filter plugins | Tagger / captioner plugins |
|---|---|---|
| **Name collision** | **User plugin wins**, replacing the built-in | **Built-in wins**; the user plugin is rejected and listed as an error |
| **Classes per file** | **Only the first** `ImagePlugin` subclass found, including one merely imported | **Every** concrete class the module *defines*; imported ones are excluded |
| **Load errors** | Server log only | Listed in Settings → Auto-tagging (local owner only) |
| Plugin shape | Single `.py` file | Single `.py` file **or** a package folder with `__init__.py` |
| Reload | Re-scanned on every list and every run | Start-up only; a restart is required |
| Dropdown parameter | `type: "string"` + `enum` (+ `enumLabels`) | `type: "select"` + `options` as `[{value, label}]` |
| Parameter types | `number`, `integer`, `boolean`, `string` | those plus `select`, `textarea`, `csv-int` |
| Model lifecycle | None — no `init`, no `unload`, no download hooks | `setup`/`init`/`unload`/`is_loaded` + a download pair |
| Base class | `ImagePlugin` (`pixlstash/image_plugins/base.py`) | `TaggerPlugin` (`pixlstash/tagger_plugins/base.py`) |
| Folder | `…/pixlstash/image-plugins/user/` | `…/pixlstash/tagger-plugins/user/` |

Both are MIT-licensed base classes, both run unsandboxed in the server process, and
neither has any dependency isolation.
