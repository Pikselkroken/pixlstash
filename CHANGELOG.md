# [1.0.0rc2] - 2026-03-28:
- Very quick update to fix a last minute regression in grid refresh progress bar.
# [1.0.0rc1] - 2026-03-28
- **Project System:** Big change to allow the creation of projects and association of pictures, sets and characters with projects.
- **Fast Multi-Tagging:** Add tags and toggle existing tags on multiple selected images in one go. With auto-complete and keyboard shortcuts.
- **More keyboard shortcuts and shortcut overview:** A friendly dialog with a list of available keyboard shortcuts.
- **Search and filtering on ComfyUI metadata:** model names, loras, prompt text.
- **Better ComfyUI workflow validation:** recognise input nodes better.
- **Much improved import:** Automatically assign to current project.
- **Improved VRAM-handling**
- **Cleaned up API with online documentation**
- **Fixed Florence-2 loading issues**
- **Loads of other bugfixes**
# [1.0.0b4] - 2026-03-22
# [1.0.0b3] - 2026-03-21

### Added
- **Server bootstrapping on first run:** set image path, username/password and watch folders
- **Minor UI improvements:** copy button for tokens, 

### Fixed
- **Florence-2 failed on newer transformer versions:** important compatibility fix to let
  PixlStash run properly on newer transformers.

### Added
- **ComfyUI workflow metadata:** LoRA names, model name, prompt text and
  seed are now extracted from embedded ComfyUI workflows and stored in the
  database.
- **ComfyUI search filters:** pictures can now be filtered by model
  or LoRA directly from the toolbar.
- **Text embedding enrichment:** ComfyUI UNET model, LoRAs and prompt are included
  in the text embedding so AI search can match on generation metadata.
- **Original filename preservation:** the original file name is stored on
  import and returned in the `Content-Disposition` header on download. An
  option to preserve the original name during export is also available.

### Changed
- Default VRAM budget raised from 4 GB to 6 GB.
- Windows: falls back to `python -m pixlstash.app` when the installed
  entry-point executable is not found.

### Fixed
- Fixed a TOCTOU race condition in worker futures.
- Fixed grid refresh failures after tag or score changes in the image
  overlay.
- ESC now correctly closes the overlay when the face assignment UI is open.
- Fixed the overlay chrome not hiding when clicking a picture in the overlay.
- Character list now refreshes immediately after adding or removing a
  character assignment.
- Smart score is preserved when changing character assignment or score in
  the overlay.
- Prevent spurious re-import of thumbnails already in the vault when
  dragging into the sidebar.
