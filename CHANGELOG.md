# [1.11.0] [Security:High]

- Settings → Libraries: add, rename and stop using libraries without the
  command line. Adding a folder works out what it is: an existing library, a
  folder of pictures, or an empty one. Nothing in the folder is moved or copied,
  and stopping using a library deletes nothing.
- Two libraries can no longer share a name.
- Library management is offered on the machine running PixlStash and over your
  local network or Tailscale, not from further away.
- The empty-library screen offers three ways to fill it, leading with pointing
  PixlStash at a folder you already have.
- Folder layouts: give a library a layout such as Project / Person or Set and
  new pictures are filed accordingly. Choosing a layout moves nothing that
  already exists. "Move to match" is offered, counted first, and undoable as one
  batch. A picture moves only when its folder stops being true, never when a
  second project or person is added. Folders you made underneath are kept,
  renames move no files, and a folder that does not match the layout is left
  alone.
- Thumbnails no longer sit next to your pictures. They live in a hidden
  `.pixlstash-thumbnails` folder at the library root; existing ones move there
  the first time they are needed.
- The desktop app's first run asks which library to open before anything else.
  The telemetry question moves there too.
- Tagger plugins can now produce tag predictions, not only the built-in tagger.
- Export a selection to a folder on the machine running PixlStash, sidecars
  included, and the folder opens when it is done. The destination must be
  empty, so an export never writes over anything you already have there.
- Writing a tagger or image plugin no longer starts with the git legwork.
  `pixlstash-cli plugins create` asks what you are building and leaves a
  checkout with the folder, the template and the branch ready;
  `pixlstash-cli plugins submit` runs the repository's own checks and opens the pull request.
- Fixed: sorting by likeness to a person took seconds per page on a large
  library, and the likeness pill had been missing from the grid since 1.10.0.
- Fixed: a library last opened by a newer PixlStash is explained in plain words,
  with a link to the latest release.
- Fixed: a library database that will not open offers to start over instead of
  crashing at startup. The old file is renamed, never deleted.
- Fixed: the desktop GPU overlay could shadow the app's own dependencies on
  Windows and silently fall back to CPU.
- Fixed: the Docker image refused to start over folder permissions. Loose
  permissions now warn instead of refusing to start.
- Fixed: regenerating descriptions ran the captioner twice per picture, left
  the task-manager count stuck, blanked the grid, and could leave pictures
  without a description. The VRAM readout on Windows no longer shows 0.
- Fixed: the desktop runtime's device choice was written into
  `server-config.json`, so a boot on the CPU runtime made later launches run
  on CPU.
- Fixed: the "View changed externally" pill kept reappearing for as long as a
  tagging run lasted.
- Fixed: Collapse all could re-expand stacks while a refresh was in flight.
- Fixed: the Libraries CLI dialog did not list the backup command.
- Fixed: the macOS DMG volume size.
- Dependency updates: transformers 5.12.1 (a high-severity path traversal in a
  writer that PixlStash does not call), xmldom, humanfs, fast-uri.

# [1.10.2]

- Fix scrolling of plugin list to avoid the whole pane having a horisonstal scrollbar
- Handle pasting of updates after pasting pictures
- Fix a CORS issue causing Retry-After failures
- Count install types correctly in the telemetry worker for update checks
- Report the frontend correctly to the update checker. Docker and Electron often
  just reported "pip"
- Follow symlinks in the folder picker
- Further fixes for ALT-TAB icons under certain circumstances

# [1.10.1]

- Add missing dependency to docker images
- Fix desktop icon in Electron app
- Fix multiple icons showing up in macOS dock for CLI
- Add pixlstash CLI to Windows PATH

# [1.10.0]

Pip, source, headless and Docker installations need one preparation command
between installing and first startup — see
[Multiple libraries](README.md#multiple-libraries). The desktop app runs it for
you. Startup then moves owner credentials into the installation hub and clears
owner, token and guest rows from the vault and its snapshots, so guests must
reopen their links.

- Multiple image libraries, switched from Settings → Libraries. Each keeps its
  own pictures, tags, scores and snapshots; your account and preferences stay
  with the installation.
- API tokens and share links belong to the library that created them and stop
  working while another library is active, so create a token in each library
  your scripts use. Settings warns before you switch away from live share links.
- A functional pixlstash CLI to perform backups, restore from backup, install
  plugins and add picture libraries. Lets you perform regular backups with cron.
- The desktop app can run that CLI itself: `PixlStash.AppImage cli libraries
  list` works whether or not the app is open. Settings → Backend → Desktop →
  Shell command adds a `pixlstash` command for any terminal — in `~/.local/bin`
  on Linux and macOS, and on Windows in `%LOCALAPPDATA%\PixlStash\bin`, which it
  adds to your user PATH so the command works in both cmd and PowerShell. Open a
  new terminal after switching it on. Settings → Libraries shows the exact
  command for your install.
- Add `pixlstash-cli libraries backup` for an owner-readable local archive of a
  library and its hub, and `libraries restore` to read one back into a new
  folder and make it the library that opens — password and API tokens included.
  Restore needs PixlStash stopped and never overwrites: your current config and
  hub are moved into a dated `pre-restore-` folder, and it prints the launch
  command for both the restored library and the one you had.
- Add `pixlstash-cli plugins install|list|remove` for captioning plugins and
  image filters, from the PixlStash-plugins repository, a zip, a folder or a
  single `.py`. A plugin's `requirements.txt` is only installed with
  `--with-deps`.
- Add `pixlstash-cli plugins available` to list what the plugins repository
  publishes — name, title, summary and, where declared, author and licence, with
  `*` marking one you already have. Add a word to search. Until now the only way
  to learn a plugin's name was to guess one wrong and read the error.
- Add `pixlstash-cli plugins test <plugin>`, so writing a captioning plugin no
  longer costs a server restart per typo. It loads the file the way the server
  does, registers every plugin class it defines, and checks the parameter schema
  is one the settings screen can render; `--image` also runs it over one picture.
  It imports the plugin, so that code runs unsandboxed with your permissions —
  a development aid, **not a security scanner**.
- An image filter plugin now registers the first concrete `ImagePlugin` subclass
  the file itself defines, rather than one it merely imports, and a name
  collision with a built-in is logged against your file instead of the built-in.
- Fixed the documented Windows plugin directory, which was missing a path
  component: it is `%LOCALAPPDATA%\pixlstash\pixlstash\image-plugins\user\`.
- Both command lines now document themselves in full, including the exit codes a
  script needs.
- Removed `pixlstash-server --retag-and-embed`; nothing ever read it.
- PixlStash can now serve an adapter's file to another machine, addressed by its
  content hash, so a ComfyUI running on a different box can use a LoRA this one
  catalogues instead of needing its own copy. Owner-only, and reachable from
  your own network rather than the internet.
- The model shelf can delete models from disk — from the selection pill, the
  row's right-click menu or the `Delete` key. It moves files to your Trash, or
  deletes them permanently with Shift held. Only your own model folders and
  PixlStash's own store are touched; shared caches and drives that are not
  plugged in are refused.

# [1.9.0]

Coming from 1.8.0 or earlier, updating to 1.9 clears every API token, exactly as described under 1.8.1 below: create replacements from Settings, share your links again with their new values, and enter your public URL and ComfyUI URL again. If you already updated to 1.8.1 this has happened and your tokens are left alone.

- Full Undo/Redo for soft-deletion, tagging, editing descriptions, image associations, image movement, etc. Use standard keyboard shortcuts (CTRL-Z, CTRL-Y),
  undo/redo-buttons or the undo history to undo more than one edit.
- A brand new Duplicates view that helps you stack duplicates in your current view.
- New Privacy pane in Settings, holding the update check and a new option to send an anonymous install ID. Both are off unless you turn them on, and upgrading leaves them off. The ID is a random number stored next to your server config, never derived from anything about your computer, and you can replace it whenever you like. It lets us tell whether people keep using PixlStash rather than just downloading it, which plain download counts cannot. When it is on, PixlStash sends that number, your version and your install type once a day, and nothing else. PRIVACY.md shows the exact message.
- Characters and picture sets can belong to more than one project at a time, so a character you use across two shoots no longer has to be duplicated or moved back and forth. Their pictures show up in every project they are shared with, and removing them from one project leaves the others alone. Existing single-project assignments carry over untouched.
- Filter your views on stacked or unstacked images, and collapse a stack down to the copy you want to keep with **Keep cover only**. The rest go to the Scrapheap, so you can change your mind.
- Easily assign pictures to a person by using the new context menu "Suggest More Pictures" which shows you a view of all pictures that look like that person. An easy to use button lets you assign all found (or all selected) items to the person.

# [1.8.5]

- Fixed: opening a picture and then reloading the page, or following a link straight to a picture, could leave the overlay showing a broken-image icon instead of the picture. The overlay now waits until it knows the file's format before loading it, and a stale error no longer hides the picture once it does load.
- Save, Save as… and Copy are now available on pictures and videos from both the image overlay and the grid's right-click menu, with keyboard shortcuts. Save as… keeps the original file extension so the saved file still opens as what it is.
- Update the undici HTTP library (to 6.28.0 and 7.29.0) and fast-uri (to 3.1.5) that our build and test tooling pulls in, picking up upstream security fixes, one of them rated high. This release carries no security tag: both are build and test dependencies only, neither is part of the app you run, so no installed version was ever exposed.

# [1.8.4]

- Fix issue #694 (Database Query Error): Sometimes a query failed due to too many bound variables in a SQL statement.

# [1.8.3] [Security:Moderate]

If you have given a share link to anyone, please update. Once you have, your existing links are safe again and keep working. Reissue a link only if you would rather the person you gave it to had never been able to see the information described below.

- Fixed: a share link could be used to read details about pictures outside what was actually shared, including their file names and the folders they sit in on your computer. Nobody could see the pictures themselves, only information about them, and only while using one specific combination of filters. Affects every release from 1.3.0 onward. We will publish the details once people have had a chance to update.

# [1.8.2]

- Signed Windows installers and nothing else. If you already installed or you're not on Windows, this is not the release for you.
- If a Windows SmartScreen warning has been putting you off trying PixlStash, the warning will still be there for a while, but now
  it will at least tell you the installer was built by "Open Source Developer Gaute Lindkvist" and not just "Unknown publisher".
- Once more people have downloaded and installed PixlStash, the Windows SmartScreen warning should eventually go away.

# [1.8.1] [Security:Critical]

Updating clears every API token as a precaution, so you will need to create replacements from Settings. Any script or integration that signs in with a token stops working until you do, and **existing share links stop working and have to be shared again** with their new values. Your public URL and ComfyUI URL are cleared along with them and need entering again, so replacement share links point where you expect. This is deliberate and applies to every library: a token created before this release cannot be distinguished from one that should not exist, so all of them are reissued rather than some. Password sign-in and the desktop app are unaffected, but **if you have turned password sign-in off on this server, turn it back on before updating** — with no tokens left there would be no other way in.

- Fixed: how API tokens are accepted when signing in. Revoking a token now also ends any session created from it. Please update. We will publish the details once people have had a chance to do so.
- Restoring a snapshot now also clears your API tokens, whichever snapshot you restore. Create replacements from Settings afterwards and share your links again with their new values.

# [1.8.0] [Security:Moderate]

Smart Scores are recomputed for your whole library the first time you open 1.8.0, so the grid may re-order compared to 1.7. Part of that is a fix: the built-in reference points that score a library with few ratings of its own had stopped loading, so if you have rated only a handful of pictures your scores should be better than before. This is expected, not a bug: the scoring weights are rebalanced so the top of the 1-5 range is reachable again and your best pictures can score like it. Your originals and snapshots are untouched, and the recompute runs in the background in small batches without re-running any AI models, so it stays out of your way.

- New justified (Google Photos style) grid layout, plus a single thumbnail-size control that replaces the columns slider.
- Remember the expansion state of sidebar items
- Imports stream to the server and finish in the background, so closing the tab no longer cancels them. Running imports show up in the task manager and can be aborted.
- Fixed: Scrapheap: delete forever now really removes the files on disk, behind a preview and a type-to-confirm dialog.
- Scrapheap auto-empty ships off. PixlStash never removes anything from disk on a timer unless you pick a retention window in Settings, and installs upgrading from 1.7 stay off until you do. If you had already saved a window, your choice is kept.
- Real context menus in the Scrapheap and the image overlay.
- New Agreement chart in the stats panel: a grid of your star ratings against the smart score, so you can see where the two disagree and click straight through to those pictures.
- Fixed: Segment from the image overlay now draws its new boxes as soon as the run finishes, instead of only after you close and reopen the picture.
- Fixed: Find similar faces from the image overlay now shows its results, the way Reverse image search already did.
- Fix snapshot restore hard-deleting files that were added after the snapshot was taken, and stop deletions from destroying pictures in locked sets.
- Smart Score updates in the overlay as you edit tags, and your own edits no longer trigger the refresh pill.
- Fixed: Smart Score only counts defects that are actually in a picture's tag list, so pictures the tagger flagged behind the scenes but never tagged are no longer pushed down the grid. Affected scores are recalculated once in the background when you upgrade.
- Unloading a model frees the VRAM it was holding, and the budget readout reflects it.
- Refreshed Appearance pane and a unified amber palette across the app and the website, with dark mode fixes on the website and install pages.
- Fixed: Status labels and buttons in the review rail, lightbox and notices are readable again on dark backgrounds. The refreshed palette had left several of them too dark to make out.
- Symmetrical sidebars, a stats panel that docks at every width, and full-width pair review in the review overlay.
- Fix watch folders importing the same picture twice when it was picked up while the file was still being copied in.
- Every API route now goes through one deny-by-default authorization gate that enforces object access from a declared policy, and routes that declare nothing are refused instead of served [Security:Moderate]
- Fixed: Locality checks fail closed on IPs they cannot parse, and the test-hooks route is loopback-only [Security:Low]
- Fix Windows updates failing with "Failed to uninstall old application files: 2" (often preceded by a misleading "PixlStash cannot be closed" prompt). The bundled torch ships license files nested so deeply that the previous version's uninstaller pushed them past the Windows 260-character path limit and aborted, killing every update. The installer now detects this and updates over the existing files instead, a failing old uninstaller no longer aborts the update, and new runtimes no longer bundle over-long paths.

# [1.7.2] [Security:Critical]

- Fixed: Restoring a snapshot could silently drop pictures whose image files were still on disk, most often pictures in reference folders, and emptying the scrapheap afterwards could then delete those files for good. Pictures removed from the library while their file was deliberately kept are no longer recorded as permanently deleted, so a restore keeps them. If you have used snapshot restore, update before restoring again; anything lost this way can be recovered from an earlier snapshot once you are on this version.
- Update setuptools and several node build dependencies flagged by npm audit.

# [1.7.1] [Security:Critical]

- Update tar, brace-expansion and js-yaml node packages
- We don't actually use the feature that makes the tar vulnerability critical, but better safe than sorry.

# [1.7.0] [Security:Moderate]

Smart Scores are recomputed for your whole library the first time you open 1.7.0, so the grid may re-order compared to 1.6. This is expected, not a bug: scoring is now calibrated and precision-aware, so it judges image defects more accurately. Your originals and snapshots are untouched, and the recompute runs in the background.

- New tag-fix review queue: a ranked queue for cleaning up your tags where you can review, bulk-confirm, or reject tagger suggestions, and teach the tagger from your decisions. Includes bulk clearing of impossible tags with source and object filters, plus review scope filters.
- Lockable picture sets: lock a set to freeze its pictures' tags, descriptions, and scores as a read-only eval or training set. A lock badge and tooltip show wherever the pictures appear, editing is blocked until you unlock, and locked sets are greyed out and unselectable in tag review while their pictures can still serve as read-only reference images.
- Smarter Smart Score: calibrated, precision-aware scoring that uses per-tag confidence instead of all-or-nothing weights, groups related defects so correlated flaws do not double-count, and adds three new full-image checks for compression blockiness, noise, and watermarks.
- Expanded, recalibrated tagger model shipped with per-tag thresholds and precision weights, so high-confidence tags drive suggestions and scoring more strongly.
- Object detection via a new Florence-2 backend: a Segment action that finds objects and draws bounding-box overlays in the grid and lightbox, with an option to export the boxes as COCO or Ideogram JSON.
- Smoother grid updates: live changes now coalesce into a single "click to refresh" pill instead of a hard refresh, your own edits no longer trigger it, and tag-suggestion notifications are batched.
- Refreshed visual design across the app with consistent type, colour, spacing, and iconography built on a new design-token system.
- Reference folders can be relocated and reorganised by drag and drop, with metadata tools and sidecar sync.
- ComfyUI generation progress now shows in the task manager; image-to-image and filter outputs can optionally stack onto the source, and the image-to-image dialog closes itself when a run starts.
- Watch folders now retry on transient hash failures instead of skipping the file.
- Harden object-scope enforcement on the tag-prediction endpoints: the confirm, reject, delete, and reset handlers now run the deny-by-default scope check, closing a defense-in-depth gap (issue #504).
- Fix bug stopping the manually drawn face boxes from being stored
- Update Axios [Security:Moderate]

# [1.6.12]
- Synced versions to frontend and ensure all builds succeed

# [1.6.11] [Security:Moderate]
- Update torch and pillow due to memory corruption and heap out-of-bounds bugs.

# [1.6.10] [Security:High]
- Update transformer version due to arbitrary code execution in transformers < 5.5.0

# [1.6.9]
- Fix windows electron installer build issue

# [1.6.8]
- Re-enable ComfyUI-filters after a property watch change caused it to get disabled
- Restore filter tag pill and section styling
- Fix filter menu tag suggestions not being clickable

# [1.6.7]
- Fix the Windows installer build (NSIS) and the macOS desktop signing so the desktop apps build and ship

# [1.6.6]
- Fix macOS and Docker build issues on GitHub

# [1.6.5]
- Re-enable macOS build
- Harden the desktop auto-update teardown so a failed or interrupted update can't leave the app in a broken state; write an installer log and surface a link for reporting issues
- Fix issue #478: stop the caption sidecar from writing debug output, cap overly long download filenames, and widen the torchvision range to ~=0.27
- Fix the tag menu re-opening on the next selection after dismissing it with ESC

# [1.6.4] [Security:High]
- Just released v1.6.3 to see a serious security issue in the undici NPM package

# [1.6.3] [Security:Low]
- Stop the PixlStash process before installing
- Let the user choose GPU runtime install location and default to install folder on Windows
- Fix some UI refresh bugs
- Show the tagger threshold offset as a percentage and preview per-label thresholds live as you adjust it
- Harden the scope of tokens to avoid future footguns
- Allow for sidecar synchronisation with reference folder even if it does not exist before
- Allow for sidecar synchronisation of both tags and descriptions

# [1.6.2] [Security:High]
- Desktop app: choose where the GPU runtime (~2.5 GB) installs instead of always using the system drive. On Windows it now defaults to *inside the install folder you picked*; the first-run wizard and Settings → Compute let you change it, and changing it moves an already-installed runtime so the download isn't repeated.
- Update several NPM packages (hasown, mime-types, form-data) for a [Security:High] alert
- Support for batched combine mode for the face likeness gate node

# [1.6.1]
- Fix drag and drop of pictures onto characters in the Electron app
- Help users make a username and password on a fresh install when enabling the server

# [1.6.0] [Security:High]
- New cross-platform desktop app (Windows/macOS/Linux) built on Electron. It runs the PixlStash server for you in a native window — no Python, Node, or browser tab required.
- LM Studio-style downloadable compute backends: on first run the app auto-detects your hardware and fetches a matching AI runtime (NVIDIA CUDA, Apple Silicon Metal, or CPU). Manage and switch backends from Backends → Compute Backends…
- Seamless local sign-in: the desktop window opens straight into your library, while password and share-token auth stay intact for remote access.
- Honour `PIXLSTASH_HOST` / `PIXLSTASH_PORT` env overrides when starting the server.
- Report a distinct `electron` install type in active-install telemetry.
- Bump esbuild from 0.27.2 due to security issue
- Fix problem initialising CUDA on Windows

# [1.5.2] [Security:Moderate]
- Fix some more scoping issues + add ready flag to character-likeness query to let client improve performance

# [1.5.1] [Security:High]
- Fix a scope-enforcement gap where a read-only or resource-scoped token could read pictures and metadata outside the resource it was granted. Update recommended for anyone who has issued share links or API tokens.

# [1.5.0]
- Snapshot and restore
- Breadcrumb navigation
- Drag characters and sets between projects

# [1.5.0rc2] [Security:High]
- Fix unauthorised and unscoped access to web sockets

# [1.5.0rc1]
- Introduce snapshots and restore functionality
- Breadcrump navigation
- Drag characters and sets between projects
- Stacking bug fixes

# [1.4.1]
- Bump axios package in frontend due to a vulnerability [Security:Low]

# [1.4.0]
- Increase ComfyUI timeouts

# [1.4.0rc1]
- Add reverse picture search and face search to both UI and API
- Further improve grid loading speed

# [1.3.1]
- Remove out-of-scope read access from several API endpoints [Security:Moderate]

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
