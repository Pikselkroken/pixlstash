# [1.3.0]
- No changes since rc3

# [1.3.0rc3]
- Increase reliability of tagging in low VRAM situations
- Delay queuing tasks until the models are loaded
- Fix "new pictures - click to load" pill showing up when automatically refreshing

# [1.3.0rc2]
- Support changing the watermark setting on existing tokens
- Disable watermark checkbox for full access tokens
- Fix eternal retries for JoyCaption for missing bitsandbytes
- Fix loading of thumbnails with maximum number of columns
- Fix issue of newly imported ComfyUI and filter creations not getting tagged
- Fix issue of sometimes not loading the full grid with many columns visible
- Ensure bitsandbytes are installed in Docker images
- Various JoyCaption and grid fixes

# [1.3.0rc1]
- Add keyboard navigation to selection menu
- Add hint in help dialog for S (selection menu shortcut)
- Context menu improvements: sub-menus open to the left near right edge, consistent ordering of Project/Person/Set entries
- Make write-operations visible but disabled in read-only mode
- Make add-to menus readable in read-only mode
- Preserve token in Vue routes
- Pin the JoyCaption SHA
- Fix issue where Filter and ComfyUI menus re-open on selection

# [1.3.0.dev2]
- Add auto-tagging and auto-descriptions to the context menu
- Ensure that a refresh of the Vue route sets the correct sidebar entries

# [1.3.0.dev1]
- Fixed many bugs related to the grid loading optimisation
- Add support for JoyCaption for both descriptions and tagging (with some parameters)
- Make both tagging and description engine selectable
- Add support for regenerating both description and tags with a choice of engine on a case-by-case basis
- Add support for bulk auto-tagging with a choice of engine on a case-by-case basis
- Add support for dragging tags in the tag panel

# [1.3.0.dev0]
- Massive refactoring of both backend and frontend
- Massive speedup of grid loading. Grid appears practically instant now even with 30k images
- js-cookie update [Security:High]
- Improve version checks to give a proper alert for security updates
- Add Vue router so that you get a proper URL to all the different views
- A refresh now refreshes the actual view you're watching

# [1.2.2]
- Update brace-expansion NPM package for frontend [Security:Moderate]

# [1.2.1]
- Fix some clipboard issues for copying tokens
- Guard against the tagging of deleted pictures
- Some import folder bug fixes
- Add project id scoping for GET /characters

# [1.2.0]
- Fix Docker commands and a few more bugfixes

# [1.2.0b2]
- Improve GUI (Sidebar, Toolbars, Selection, context menus) on both Desktop and mobile
- Fix large ZIP-file uploads
- Make it possible for Picture sets to have icons and colors instead of thumbnails (which are a bit useless at small sizes)
- Massively improved the dock sidebar.

# [1.2.0b1]
- Share picture sets, projects, characters and single pictures by easily creating read-tokens
- Copy and paste in chat or emails
- Create share in the context menus
- Filter on shared images to easily remove the share
- Add a user-specific or company-specific watermark to your shared images
- Massively improve the speed of the asynchronous tasks AND massively reduce VRAM usage. Face extraction, tagging, embedding calculation, likeness etc. should now be from 3x to 50x faster. This by doing pipelining instead of trying to do GPU tasks concurrently
- Allow for limiting full logins to a local network (i.e. through VPN) and only allow read tokens over the Internet
- Add demo site on https://demo.pixlstash.dev/

# [1.1.2]
- Fix issue causing very slow tagging of many pictures at the same time
- Limit the optional version checks to once per 24h

# [1.1.1]
- Fix counts for project characters when some pictures are not in the project
- Update Pixlstash tagger to give less false positives

# [1.1.0]
- Fix handling of import and reference folders in Docker mode. Provide copyable restart command to get the folders in.
- Improve ComfyUI-workflow error handling.

# [1.1.0rc1]
- Support multi-select and boolean set operations on characters and picture sets
  * Union, Overlap, Difference or Unique
- Include Import Folders in the UI together with reference folders
- Add context menus to the ImageGrid and the sidebar

# [1.1.0b1]
- Support reference folders: add folders to include in app but not import into database folder
- Add statistics sidebar in Image Grid
- Lots of bugfixes
# [1.0.4]
- Fix issue where the sort menu didn't show entries until the first character was created

# [1.0.3]
- Attempt at fixing the sort menu not showing any entries.

# [1.0.2]
- Fix problem where the "update available" check thought 1.0.1rc3 was newer than 1.0.1.

# [1.0.1]
- Updated two dependencies (pillow and npm_and_yarn) due to vulnerabilities
- Shifted to a self-contained Python way of generating SSL certs so we don't rely on external OpenSSL.

# [1.0.0]
- Improved tagging interface
- Improved speed and reliability of image uploads
- Support more file formats
- Make the choice of tagger(s) optional
- Improved keyboard shortcuts
- Improved PixlStash tagger
- Many bug fixes

# [1.0.0rc5] - 2026-04-08:
- Fixed git tag to ensure a proper build

# [1.0.0rc4] - 2026-04-07:
- Add filter on tag prediction confidence (find pictures the tagger is unsure of for specific tags)
- Many UI improvements
- Many bugfixes for stacks, keyboard shortcuts, ComfyUI workflows
- Update custom tagger

# [1.0.0rc3] - 2026-04-01:
- Fix missing docker image depedency

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
