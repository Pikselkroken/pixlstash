# The Guide: concept catalogue

This is the content contract for the in-app **Guide**: the visual, searchable
catalogue of what PixlStash can do, opened from the title bar. The design pass
and the build both work from it. It decides **what** the Guide holds: the
concepts, their tasks, where each task starts on screen, what library content
each needs, and what each illustration shows. It does not decide how any of it
looks. The chosen look is the *Guide screen* direction (a full screen in the
shell, a sidebar to browse by goal or workspace, an A–Z grid of concept cards).

Entry points were checked against `develop` when this file was written. Line
numbers are left out because they drift. Grep the control's label in the named
file.

## Contents

- [How to read this](#how-to-read-this)
- [Prerequisites and where they are answered](#prerequisites-and-where-they-are-answered)
- [New in 2.x](#new-in-2x)
- [Get pictures in](#get-pictures-in)
- [Find](#find)
- [Organise](#organise)
- [Review](#review)
- [Generate](#generate)
- [Edit](#edit)
- [Train](#train)
- [Share & connect](#share--connect)
- [Keep it safe](#keep-it-safe)
- [Greying and Start here](#greying-and-start-here)
- [Startup guide](#startup-guide)
- [Where this departs from the plan](#where-this-departs-from-the-plan)

## How to read this

- A **concept** is a group of similar tasks, named with the app's own words so
  the A–Z grid and the search field match what is on screen. A concept opens
  to its **task list**. **Each task is one wizard.** The written documentation
  is **one page per concept with one section per task**.
- Every concept can be browsed two ways: by **goal** (the `##` sections below)
  and by **workspace** (Media, Studio, Workflows, Models, Settings).
- **Needs** is the library content a concept or task needs before it can be
  tried. A concept's need is what its core tasks need, the ones its card
  illustrates. Tasks grey on their own either way: a task that needs more
  greys inside a Normal concept, and a reference or set-up task that needs
  less (*Keyboard shortcuts*, *Choose a tagger*, *Add a workflow file*) can be
  tried inside a greyed one.
- **Search terms** are the extra words the Guide's search field matches for
  a concept, beside its name, its summary and its task names. They are the
  words someone types who does not know the app's own: synonyms, both
  spellings (*colour*, *color*), names other apps use (*album*, *catalog*,
  *smart folder*) and community jargon (*txt2img*, *cull*, *dedupe*). A word
  may sit under more than one concept; search then shows both. Match them
  case-insensitively, from the start of any word.
- **Illustration** is one line on what the shot shows. Shots are real
  screenshots of the Demo Data library, made by the existing screenshot rig
  (`frontend/e2e/screenshots/`). Demo data is public in the hosted demo, so a
  brief must only call for content that is fine to publish. Where capturing
  finds content genuinely missing, the capture flags it then. Nothing here
  audits demo data.
- **Unstable** marks a concept or task whose controls may still move. Pass 2
  and the capture rig must not freeze screenshots of it until it ships.
- **Startup** marks the six concepts in the welcome carousel, plus
  *Import pictures*, which the welcome offers on an empty library with
  Generate with ComfyUI (in both). **New** marks a
  concept that owns a *New in 2.x* headline.

## Prerequisites and where they are answered

Every need is library content. A missing tool (ComfyUI, AI-toolkit, an MCP
client) is never a need: it turns the concept's first task into its set-up task
instead. Each need below is answered by an endpoint that already exists. Paths
are under `/api/v1`.

| Need | Met when | Answered by |
|---|---|---|
| **none** | always | (nothing to ask) |
| **picture** | at least 1 picture | `GET /pictures/count` → `count ≥ 1` |
| **2 pictures** | at least 2 pictures | `GET /pictures/count` → `count ≥ 2` |
| **face** | a picture with a detected face | `GET /pictures/count?face_filter=with_face` → `count ≥ 1` |
| **video** | a video in the library | `GET /pictures/count` with `format=` each video format the importer accepts, spelled as `GET /pictures/stats` reports formats → `count ≥ 1` |
| **person** | at least 1 person | `GET /characters` → non-empty |
| **person with pictures** | a person with at least 1 picture | `GET /pictures/count?character_id=<id>` for a person from `GET /characters` → `count ≥ 1` |
| **person with a LoRA** | a person with a trained or attached LoRA | **needs a count** |
| **set** | at least 1 picture set | `GET /picture_sets` → non-empty |
| **2 sets** | at least 2 picture sets | `GET /picture_sets` → 2 or more |
| **recipe** | a picture made in ComfyUI | `GET /workflows?include_one_offs=true` → a card with `manual: false` (an automatic workflow exists only because pictures carry its recipe; without the flag, one with fewer than three pictures is left out of `cards`) |
| **own workflow** | a workflow that is not built in: imported, duplicated, or one PixlStash found in your pictures | `GET /workflows?include_one_offs=true` → a card with `built_in: false` |
| **outpaint workflow** | an `(outpaint)` workflow | `GET /workflows` → a card with `type: "outpaint"` |
| **duplicates** | the Duplicates queue holds a group | `GET /dedup/groups` → a non-empty page |
| **stack** | a picture in a stack | `GET /pictures/count?stack_state=stacked` → `count ≥ 1` |
| **shared** | something has a share link | `GET /pictures/count?shared_only=true` → `count ≥ 1`; a share of a person, set or project with no picture shared on its own **needs a count** |
| **pending moves** | files moved outside PixlStash, waiting | `GET /moves/pending` → non-empty |
| **text in a picture** | a picture where text was found | **needs a count** |
| **training run** | a LoRA training run exists | `GET /training/jobs` → non-empty |
| **2 libraries** | another library is registered | `GET /libraries` → 2 or more |
| **snapshot** | a snapshot exists | `GET /snapshots` → non-empty |
| **history** | an undoable operation exists | `GET /operations/undo-state` → something to undo |
| **location** | a Studio location exists | `GET /studio/overview` → `locations` non-empty |
| **scene** | a Studio scene exists | `GET /studio/overview` → `scenes` non-empty |

The "needs a count" rows have no endpoint, and the build adds none: their
tasks stay lit (owner's ruling, #2825). Do not answer them by reading the
library database.

## New in 2.x

Pinned first in the grid, outside the A–Z order. **Needs:** none, never
greyed. It holds no tasks of its own: each headline is a link into the concept
that owns it, and opens that concept's matching task.

**Search terms:** new, what's new, whats new, latest, recent, release notes, changelog, changes, update, updated, upgrade, new features, new version, 2.0, 2.1, highlights, announcements.

**Illustration:** a mosaic of the headline shots below, the newest first.

| Headline | Opens | Release |
|---|---|---|
| Three workspaces: Media, Workflows and Models | Browse & inspect › Grid layouts | 2.0 |
| Train a LoRA from a person or a set | Consistent characters › Train a LoRA | 2.0 |
| Workflows as cards you can run | Workflows › Workflow cards | 2.0 |
| A Models workspace with sets and folders | Models › Model shelf | 2.0 |
| Search by colour | Search › Colour search | next |
| Smart sets | Filters & smart sets › Save as smart set | next |
| Video frames in the lightbox | Browse & inspect › Video storyboard | next |
| Faces and reference cluster | Similar pictures & faces › Face clusters | next |
| Extend a picture (outpaint) | Edit a picture › Extend | next (**unstable**) |
| The Studio: scenes with a cast, locations and props | Studio › Stage a scene | next (**unstable**) |
| Outfit, location and prop sheets | Studio › Cast and wardrobe | next (**unstable**) |

"next" is what `changelog.d/` holds for the coming release. The release cut
decides the final list: a headline is added here when its fragment lands, and
the welcome opens on New after any update that adds one.

## Get pictures in

### Import pictures

Drop files, watch a folder, or add a folder you already keep pictures in.

- **Goal:** Get pictures in · **Workspace:** Media
- **Needs:** none
- **Startup:** yes, on an empty library only · **New:** no
- **Search terms:** import, add, add photos, add images, upload, ingest, load, bring in, open files, drag and drop, drop files, copy in, transfer, migrate, folder, watch folder, watched folder, hot folder, auto-import, monitor folder, sync folder, sidecar, caption files, .txt captions, existing folder, camera, phone, screenshots, scans, first pictures, get started, empty library
- **Illustration:** the grid's drop overlay, "Drop files here to import", over an empty library.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Drop files | drag files onto the window or the grid | `frontend/src/components/views/ImageGrid.vue`, `frontend/src/composables/useWindowFileImport.js` | none | files mid-drag over the grid, the overlay lit |
| Import into a project | sidebar footer **Import** (reads "Import to <project>" in a project) › *Import photos* › *Import to* | `frontend/src/components/panels/SideBar.vue`, `frontend/src/components/io/PhotosImportDialog.vue` | none | the Import photos dialog with a project chosen in *Import to* |
| Watched import folders | Settings › Libraries & imports › Import folders › *Add import folder…* | `frontend/src/components/settings/ImportFoldersSection.vue` | none | the Import folders list with one folder being watched |
| Caption files beside your pictures | *Add a library* preview step › *Caption files beside your pictures*; later Settings › Libraries & imports › library ⋯ › *Caption files…* | `frontend/src/components/folders/FolderMappingPreviewStep.vue`, `frontend/src/components/settings/LibraryCaptionsDialog.vue` | none | the caption card with one pattern set to Tags and one to Description |
| Add a library from a folder | title-bar library picker › *Add a library…* | `frontend/src/components/TitleBarLibraryPicker.vue`, `frontend/src/components/folders/FolderMappingWizard.vue` | none | the *Add a library* wizard at "Before anything is written" |
| Move to library | right-click pictures › *Move to library* › a library | `frontend/src/components/widgets/ImageGridContextMenu.vue` | picture, 2 libraries | the *Move to library* submenu naming a second library |

### Libraries

Keep several libraries apart and switch between them from the title bar.

- **Goal:** Get pictures in · **Workspace:** title bar, Settings
- **Needs:** none
- **Startup:** no · **New:** no
- **Search terms:** library, libraries, catalog, catalogue, vault, database, separate library, second library, multiple libraries, switch library, profile, storage, storage location, drive, disk, disk space, free space, full disk, external drive, NAS, data folder, where are my files, location on disk, library stats, insights, overview, about
- **Illustration:** the open title-bar library picker, three libraries with their free-space bars.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Switch library | title-bar library picker › a library row | `frontend/src/components/TitleBarLibraryPicker.vue` | 2 libraries | the picker open, the pointer on a second library |
| Manage libraries | library picker › *Manage libraries…* | `frontend/src/components/settings/LibrariesSection.vue` | none | Settings › Libraries & imports with a row's ⋯ menu open |
| Drive usage | the fill bar under the library name; "<size> free" on each picker row | `frontend/src/components/TitleBarLibraryPicker.vue` | none | the title bar close up, the fill bar and the picker's free-space figures |
| About your library | right-click *All media* › *About your library* | `frontend/src/components/panels/SideBar.vue`, `frontend/src/components/views/LibraryInsights.vue` | picture | the About your library page with its findings |

## Find

### Browse & inspect

Move through the grid, open a picture large, zoom, compare and score it.

- **Goal:** Find · **Workspace:** Media
- **Needs:** picture
- **Startup:** no · **New:** yes (three workspaces; video frames)
- **Search terms:** browse, view, look, gallery, grid, thumbnails, thumbnail size, contact sheet, justified, masonry, square, layout, lightbox, viewer, full screen, fullscreen, preview, large view, magnify, zoom in, pan, 100 %, pixel peep, side by side, A/B, before and after, compare, filmstrip, carousel, slideshow, next picture, video, frames, storyboard, scrub, rating, rate, stars, star rating, favourite, favorite, like, heart, best, pick, shortcuts, hotkeys, keybindings, keys, keyboard
- **Illustration:** the lightbox open over the grid, filmstrip along the bottom, stars in the details panel.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Grid layouts | toolbar *View* › *Grid view* (size, compact, stacks, overlays); Settings › Appearance › *Thumbnail layout* | `frontend/src/components/panels/Toolbar.vue`, `frontend/src/components/settings/AppearanceSection.vue` | picture | the same pictures in Square and Justified, side by side |
| Open in the lightbox | double-click a thumbnail | `frontend/src/components/views/ImageGrid.vue`, `frontend/src/components/views/Lightbox.vue` | picture | a landscape open in the lightbox inside the shell |
| Zoom | lightbox toolbar magnifier "%" (Z, or the wheel) | `frontend/src/components/views/Lightbox.vue` | picture | the lightbox at 100 % on a detail |
| Compare | select two pictures › right-click › *Compare* | `frontend/src/components/widgets/ImageGridContextMenu.vue` | 2 pictures | two near-identical shots split in compare |
| Filmstrip | the strip along the lightbox's bottom | `frontend/src/components/views/LightboxFilmstrip.vue` | 2 pictures | the filmstrip with two pictures Ctrl-picked |
| Video storyboard | lightbox on a video › band switch › *Frames of this clip* | `frontend/src/components/views/Lightbox.vue`, `frontend/src/components/widgets/VideoStoryboard.vue` | video | a clip's eleven frames under the player |
| Favourites and stars | thumbnail hover stars and heart; sidebar *Favourites* | `frontend/src/components/widgets/StarRatingOverlay.vue`, `frontend/src/components/panels/SideBar.vue` | picture | a thumbnail with its hover stars, four lit, heart filled |
| Keyboard shortcuts | `?` or the floating `?` button | `frontend/src/components/widgets/ShortcutsDialog.vue` | none | the Keyboard shortcuts dialog |

### Search

Find pictures by what is in them, their text, a tag, a face or a colour.

- **Goal:** Find · **Workspace:** Media
- **Needs:** picture
- **Startup:** yes (slide 1, colour search) · **New:** yes (colour search)
- **Search terms:** search, find, look for, query, lookup, search bar, semantic search, natural language, describe, AI search, CLIP, meaning, concept search, OCR, text in image, words, writing, signs, tag search, by tag, face search, by person, by model, by checkpoint, by LoRA, by workflow, tokens, chips, colour, color, hue, palette, dominant colour, swatch, eyedropper, where is
- **Illustration:** the search field with a blue swatch picked and a grid of blue-water pictures.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Search by meaning | toolbar search field, type and Enter | `frontend/src/components/panels/Toolbar.vue`, `frontend/src/components/panels/SearchTokenField.vue` | picture | "boat at dusk" typed, the grid answering with boats at dusk |
| Text in pictures | the same search; read it in the lightbox *Text* tab | `frontend/src/components/panels/SearchTokenField.vue`, `frontend/src/components/views/LightboxDescriptionPanel.vue` | text in a picture | a sign's words in the Text tab beside the picture |
| Tags, faces, models and workflows as tokens | type in the search field, pick from Tags / Faces / Checkpoint / LoRA / Workflow | `frontend/src/components/panels/SearchTokenField.vue` | picture | the dropdown's sections with two tokens already in the field |
| Colour search | swatches at the foot of the search dropdown; Filters › *Colour* | `frontend/src/components/panels/SearchTokenField.vue`, `frontend/src/components/panels/FilterMenu.vue` | picture | the twelve swatches, one picked, the grid filtered to it |

### Filters & smart sets

Narrow the grid by tag, score or size, and save it as a set that keeps itself current.

- **Goal:** Find · **Workspace:** Media
- **Needs:** picture
- **Startup:** no · **New:** yes (smart sets)
- **Search terms:** filter, filters, narrow, refine, facets, criteria, conditions, rules, query builder, score, rating filter, minimum stars, resolution, size, dimensions, megapixels, orientation, landscape, portrait, square, aspect ratio, format, file type, PNG, JPEG, WebP, video only, date, sort, order, arrange, newest, oldest, smart album, smart collection, smart folder, saved search, saved filter, dynamic set, live set, auto-updating set, rule-based set, virtual folder
- **Illustration:** the active-filter strip (score ≥ 4, a tag, landscape) with *Save as smart set…* under the pointer.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Filter the grid | toolbar *Filter* | `frontend/src/components/panels/Toolbar.vue`, `frontend/src/components/panels/FilterMenu.vue` | picture | the Filter menu open with a score range set |
| Sort | toolbar sort split button (labels from `pixlstash/db_models/picture.py`) | `frontend/src/components/panels/Toolbar.vue` | picture | the sort menu open, *Smart Score* highlighted |
| Save as smart set | filter strip › *Save as smart set…* | `frontend/src/components/panels/FilterStrip.vue` | picture | the *New smart set* dialog over the filtered grid |

### Similar pictures & faces

Sort by likeness, search with a picture, and let a face find more of a person.

- **Goal:** Find · **Workspace:** Media
- **Needs:** picture
- **Startup:** yes (slide 2, *Suggest more pictures*) · **New:** yes (faces and reference cluster)
- **Search terms:** similar, similarity, lookalike, look-alike, alike, related, more like this, visually similar, reverse image search, search by image, image search, query by example, find matches, face recognition, facial recognition, face detection, face match, same person, who is this, identify, recognise, recognize, cluster, clustering, group faces, unknown faces, unnamed faces, suggest, suggestions, likeness, resemblance, embedding, nearest neighbours
- **Illustration:** one person's thumbnail beside a grid of library pictures that look like them (*Suggest more pictures*).

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Reverse image search | right-click a picture › *Find & compare* › *Reverse image search* | `frontend/src/components/widgets/ImageGridContextMenu.vue` | picture | the source picture beside the grid it ranked |
| Find similar faces | right-click a picture › *Find & compare* › *Find similar faces* | `frontend/src/components/widgets/ImageGridContextMenu.vue` | face | a face crop and the faces ranked under it |
| Sort by similarity to a person | toolbar sort › *Similarity to …* › a person | `frontend/src/components/panels/Toolbar.vue` | person with pictures | the grid sorted by likeness to one person |
| Suggest more pictures | right-click a person › *Suggest more pictures* | `frontend/src/components/panels/SideBar.vue` | person with pictures | a person's thumbnail beside the pictures suggested for them |
| Faces and reference cluster | right-click a person › *Faces and reference cluster…* | `frontend/src/components/panels/SideBar.vue`, `frontend/src/components/io/FaceClustersDialog.vue` | person with pictures | the person's faces in clusters, the reference cluster marked |
| Likeness filter | in a person's view, toolbar *Filter* › *Likeness* | `frontend/src/components/panels/FilterMenu.vue` | person with pictures | the Likeness slider narrowing a person's pictures |

## Organise

### People

Name the people in your pictures, then find and generate them by name.

- **Goal:** Organise · **Workspace:** Media
- **Needs:** none ("Create a person" needs nothing)
- **Startup:** no · **New:** no
- **Search terms:** people, person, persons, character, characters, OC, original character, persona, subject, identity, who, face, faces, name, name a face, label a face, tag a person, assign, unassign, face box, bounding box, draw box, missed face, add face, family, friends, avatar, profile picture, cover picture, thumbnail, portrait
- **Illustration:** the sidebar People list with four people and the *+* button lit.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Create a person | sidebar *People* heading › *+* (*Add person*) › *New person* | `frontend/src/components/panels/SideBar.vue`, `frontend/src/components/editors/CharacterEditor.vue` | none | the New person dialog with a name typed |
| Assign faces | picture details › face crops › *Edit faces* › *Who is this?* | `frontend/src/components/panels/RailPicture.vue`, `frontend/src/components/widgets/FacesPanel.vue` | face, person | a face crop with the *Who is this?* picker open |
| Draw a face box | lightbox › Faces panel › *Draw a face box* | `frontend/src/components/views/Lightbox.vue` | picture | a box being dragged round a face the detector missed |
| Pick a thumbnail | right-click a person › *Edit* › click a reference picture | `frontend/src/components/panels/SideBar.vue`, `frontend/src/components/editors/CharacterEditor.vue` | person with pictures | the Edit person dialog, a reference picture under the pointer |

### Sets & projects

Gather pictures into sets, nest and lock them, and scope the library to a project.

- **Goal:** Organise · **Workspace:** Media
- **Needs:** picture
- **Startup:** no · **New:** no
- **Search terms:** set, sets, album, albums, collection, collections, folder, folders, gallery, group, board, moodboard, mood board, selection, shortlist, project, projects, client, job, scope, nested, subfolder, sub-set, hierarchy, tree, lock, protect, read-only, freeze, icon, colour, color, label, combine, merge sets, union, overlap, intersection, difference, organise, organize, sort into, file away, categorise, categorize
- **Illustration:** the sidebar Sets list with a nested set open and one set locked.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Make a set | sidebar *Sets* › *+* (*Create new set*) | `frontend/src/components/panels/SideBar.vue` | picture | a new set in the sidebar, pictures being dropped onto it |
| Nest sets | right-click a set › *New set inside "<set>"…* | `frontend/src/components/panels/SideBar.vue` | set | a set holding two sets, three levels at most |
| Lock a set | right-click a set › *Lock set* | `frontend/src/components/panels/SideBar.vue` | set | a locked set's padlock and its frozen pictures |
| Icon and colour | right-click a set › *Icon* / *Color* | `frontend/src/components/panels/SideBar.vue`, `frontend/src/components/editors/PictureSetEditor.vue` | set | sets with their own icons and colours |
| Combine sets | Ctrl-click two sets › mode above the grid: Union / Overlap / Difference / Unique | `frontend/src/components/views/ImageGrid.vue` | 2 sets | two sets selected, *Overlap* chosen, the grid showing the shared pictures |
| Scope to a project | sidebar scope button (*Any project*) › *Limit view to* | `frontend/src/components/panels/SidebarScopeSlot.vue` | picture | the scope menu with a project chosen, the sidebar narrowed |

### Tags & descriptions

Let the tagger describe every picture, then correct, copy and group the tags.

- **Goal:** Organise · **Workspace:** Media
- **Needs:** picture
- **Startup:** no · **New:** no
- **Search terms:** tag, tags, tagging, keyword, keywords, label, labels, labelling, labeling, metadata, auto-tag, autotag, auto tagging, AI tags, tagger, booru tags, danbooru, caption, captions, captioning, description, describe, alt text, interrogate, Florence, JoyCaption, copy tags, paste tags, bulk tag, batch tag, edit tags, remove tag, wrong tag, tag groups, tag categories, taxonomy, vocabulary, import tags
- **Illustration:** the side panel's Tags section with grouped chips and the Description under it.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Tag automatically | right-click › *Analyse* › *Tag automatically*; or the wand in the Tags section | `frontend/src/components/widgets/ImageGridContextMenu.vue`, `frontend/src/components/panels/RailTags.vue` | picture | the Analyse submenu with a tagger chosen |
| Tag by hand | Tags section › *Add a tag* (T) | `frontend/src/components/panels/RailTags.vue` | picture | a tag being typed with its suggestions |
| Copy and paste tags | Tags header › *Copy all tags*, then paste on a selection | `frontend/src/components/panels/RailTags.vue` | 2 pictures | the paste button reading "Paste … on all 12" |
| Generate a description | right-click › *Analyse* › *Generate description* | `frontend/src/components/widgets/ImageGridContextMenu.vue`, `frontend/src/components/views/LightboxDescriptionPanel.vue` | picture | a generated description beside its picture |
| Tag groups | sidebar *Review* › *Tags* › *Groups* | `frontend/src/components/reviews/TagHealthBoard.vue`, `frontend/src/components/reviews/TagGroupsBoard.vue` | picture | the Groups board with People, Hair and Clothing columns |
| Import tag groups | Tags › Groups › *Import…* (Danbooru `.xlsx`/`.csv`) | `frontend/src/components/reviews/TagGroupsBoard.vue` | none | the Import tag groups dialog with a file read |
| Choose a tagger | Settings › Tagging › *Auto-tagging* | `frontend/src/components/settings/BehaviourSection.vue` | none | the Tag plugin and Description plugin pickers |

## Review

### Stacks & duplicates

Fold near-identical pictures into stacks and clear the Duplicates queue.

- **Goal:** Review · **Workspace:** Media
- **Needs:** picture
- **Startup:** no · **New:** no
- **Search terms:** stack, stacks, stacking, group, bundle, burst, variants, variations, versions, collapse, fold, duplicate, duplicates, dupes, dedupe, dedup, de-duplicate, near-duplicate, near duplicate, copies, identical, same picture twice, clean up duplicates, cover, top picture, representative, keep best, pick best, perceptual hash
- **Illustration:** the Duplicates queue with a group of near-identical shots and *Keep cover only*.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Stack pictures | select pictures › right-click › *Stack* | `frontend/src/components/widgets/ImageGridContextMenu.vue` | 2 pictures | a stack's badge on its cover, the members fanned |
| Choose the cover | right-click in an open stack › *Edit stack* › *Make cover* | `frontend/src/components/widgets/EditStackMenu.vue` | stack | the Edit stack menu with *Make cover* |
| Duplicates queue | sidebar *Review* › *Duplicates* | `frontend/src/components/views/DuplicateQueue.vue` | duplicates | the queue with its first group open |
| Keep cover only | right-click a stack › *Edit stack* › *Keep cover only* | `frontend/src/components/widgets/EditStackMenu.vue` | stack | the confirm naming how many pictures go to the Scrapheap |

### Review & clean up

Work through the Inbox, check doubtful tags and look at what the score flags.

- **Goal:** Review · **Workspace:** Media
- **Needs:** picture
- **Startup:** no · **New:** no
- **Search terms:** review, triage, cull, culling, sort through, go through, inbox, queue, to do, keep, skip, reject, discard, scrap, delete, trash, clean up, tidy, declutter, prune, quality, bad pictures, blurry, artefacts, artifacts, anomalies, outliers, smart score, aesthetic score, quality score, ranking, tag review, wrong tags, doubtful tags, confidence, tag health, moves, pending moves, moved files, renamed files, files moved on disk
- **Illustration:** the Inbox with its Keep / Skip / Scrap keys on screen.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Inbox | sidebar *Review* › *Inbox* | `frontend/src/components/panels/SideBar.vue` | picture | an Inbox picture with K, S and X shown |
| Tag reviews | sidebar *Review* › *New review…* | `frontend/src/components/panels/SideBar.vue` | picture | a review's ranked queue, one tag in doubt |
| Tag health | *Review* › *Tags* › *Health* | `frontend/src/components/reviews/TagHealthBoard.vue` | picture | the Health board with a *Start review* row |
| Smart score and anomalies | toolbar sort › *Smart Score*; Tags › Health › *Anomalies* | `frontend/src/components/panels/Toolbar.vue`, `frontend/src/components/reviews/TagHealthBoard.vue` | picture | the grid by Smart Score, the side panel's score chart |
| Moves | sidebar *Review* › *Moves* (only while moves are pending) | `frontend/src/components/panels/SideBar.vue` | pending moves | the Moves review pairing a moved file with its picture |

## Generate

### Generate with ComfyUI

Connect ComfyUI and make new pictures from a template or from pictures you like.

- **Goal:** Generate · **Workspace:** Workflows
- **Needs:** none (without ComfyUI the first task is *Connect ComfyUI*)
- **Startup:** yes (slide 3, *Make more like these*; and on an empty library) · **New:** no
- **Search terms:** generate, generation, create, make, render, new picture, AI art, AI image, image generation, txt2img, text to image, img2img, image to image, prompt, prompting, Stable Diffusion, SD, SDXL, Flux, diffusion, ComfyUI, Comfy, connect, connection, server, host, URL, address, remote GPU, second GPU, batch, variations, more like this, same style, template, starter workflow, blank, from scratch, queue, open in ComfyUI, send to ComfyUI
- **Illustration:** the *Make more like these* dialog over three selected pictures.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Connect ComfyUI | Settings › ComfyUI; the Workflows sidebar's *Set up ComfyUI* row | `frontend/src/components/settings/ComfyuiHostSection.vue`, `frontend/src/components/panels/WorkflowsComfyuiSlot.vue` | none | "Where is ComfyUI running?" with a connection answering |
| Add a template | Workflows sidebar › *Templates* › a card › *Add to my workflows* | `frontend/src/components/panels/WorkflowsSidebar.vue`, `frontend/src/components/panels/TemplateInspector.vue` | none | the Templates grid with one template's inspector open |
| Run a workflow | Workflows › a card › *Run…* (R); or right-click pictures › *Generate* › *Run a workflow on these…* | `frontend/src/components/panels/WorkflowTab.vue`, `frontend/src/components/io/RunDialog.vue` | none | the Run popup with count and seed set |
| Blank picture | Run popup › *Blank picture* | `frontend/src/components/io/RunDialog.vue` | none | the Run popup with *Blank picture* ticked |
| Make more like these | right-click pictures › *Generate* › *Make more like these…* | `frontend/src/components/widgets/ImageGridContextMenu.vue`, `frontend/src/components/io/MakeMoreDialog.vue` | picture | the dialog with its source pictures in a row |
| Several ComfyUI connections | Settings › ComfyUI › *Connections* › *Add connection* | `frontend/src/components/settings/ComfyuiHostSection.vue`, `frontend/src/components/settings/ComfyuiConnectionsTable.vue` | none | two connections, *Run on* set to the second |
| Open or finish in ComfyUI | Workflows inspector › *Open in ComfyUI*; a Draft card › *Finish in ComfyUI…* | `frontend/src/components/panels/WorkflowTab.vue`, `frontend/src/components/widgets/WorkflowCard.vue` | none | a Draft card with *Finish in ComfyUI…* |

### Models

See every checkpoint and LoRA on your shelf, look them up and download more.

- **Goal:** Generate · **Workspace:** Models
- **Needs:** none (the shelf is never empty in practice; downloads need nothing on it)
- **Startup:** no · **New:** yes (the Models workspace)
- **Search terms:** model, models, checkpoint, checkpoints, ckpt, safetensors, LoRA, LoRAs, lora, LyCORIS, embedding, textual inversion, VAE, upscaler, base model, SD 1.5, SDXL, Pony, Illustrious, Flux, shelf, model library, model manager, model sets, model collections, Civitai, look up, download, downloads, install model, model folder, models path, extra_model_paths, disk space, duplicate models, copies, hard link, one copy, trigger words
- **Illustration:** the model shelf with checkpoints and LoRAs, sizes and base models showing.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Model shelf | title bar *Models* | `frontend/src/components/views/ModelShelf.vue` | none | the shelf grouped by type |
| Model sets | Models sidebar › *Sets*; shelf selection bar › *Assign to set* | `frontend/src/components/panels/ModelsSideBar.vue`, `frontend/src/components/views/ModelSetGrid.vue` | none | a model set with its checkpoint, VAE and encoder |
| Look up on Civitai | select a model › ⋯ › *Look up base model on Civitai…* | `frontend/src/components/panels/ShelfSelectionBar.vue` | none | the confirm *Look up on Civitai*, then the base model filled in |
| Folders | Models sidebar › *Folders*; *Model folders* button › Settings › Model folders | `frontend/src/components/panels/ModelsSideBar.vue`, `frontend/src/components/settings/ModelFoldersSection.vue` | none | the folder tree with a model being dragged onto a folder |
| Downloads folder | Settings › Model folders › "<Downloads> watching" pill | `frontend/src/components/settings/ModelFoldersSection.vue` | none | the dot on *Models* after a download lands |
| Keep one copy | select a duplicated model › ⋯ › *Keep one copy…* | `frontend/src/components/panels/ShelfSelectionBar.vue`, `frontend/src/components/panels/MergeCopiesDialog.vue` | none | the Keep one copy dialog naming the copy it keeps |

### Recipes

Read how a picture was made and run the recipe again.

- **Goal:** Generate · **Workspace:** Media (lightbox), Workflows
- **Needs:** recipe
- **Startup:** no · **New:** no
- **Search terms:** recipe, recipes, how was this made, how did I make this, generation info, generation data, parameters, PNG info, infotext, metadata, prompt, negative prompt, seed, sampler, scheduler, steps, CFG, guidance, resolution, settings, reproduce, regenerate, re-run, rerun, again, remix, copy prompt, provenance, save recipe, export recipe, share settings
- **Illustration:** the lightbox *Recipe* tab: sampler, steps, CFG and size, with *Run…*.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Read a recipe | lightbox › *Recipe* tab | `frontend/src/components/views/LightboxRecipePanel.vue` | recipe | the Recipe tab beside the picture it made |
| Run it again | Recipe tab › *Run…* | `frontend/src/components/views/LightboxRecipePanel.vue` | recipe | the Run popup prefilled from the recipe |
| Save as recipe | Recipe tab › *Save as recipe* | `frontend/src/components/views/LightboxRecipePanel.vue` | recipe | the button reading *Saved* |
| Export a recipe | Workflows › a workflow › *Recipes* tab › ⋯ › *Export…* | `frontend/src/components/panels/WorkflowRecipesTab.vue`, `frontend/src/components/io/ExportRecipeDialog.vue` | recipe | the Export recipe dialog |

### Workflows

Keep ComfyUI workflows as cards, swap their models and expose what you change.

- **Goal:** Generate · **Workspace:** Workflows
- **Needs:** own workflow
- **Startup:** no · **New:** yes (workflows as cards)
- **Search terms:** workflow, workflows, graph, node graph, nodes, pipeline, ComfyUI workflow, JSON, workflow file, API format, import workflow, load workflow, add workflow, cards, inspector, parameters, inputs, expose, knobs, controls, sliders, swap model, change checkpoint, change LoRA, missing models, red nodes, broken workflow, fix workflow, replace model, versions, version history, revert, roll back, save over, save as new, workflow sets, organise workflows
- **Illustration:** the Workflows grid with one card selected and its inspector showing Checkpoint, LoRA and version.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Workflow cards | title bar *Workflows* | `frontend/src/components/views/WorkflowsView.vue`, `frontend/src/components/widgets/WorkflowCard.vue` | own workflow | the card grid with covers from its pictures |
| Add a workflow file | drop a `.json` on the Workflows view; sidebar *Add workflow* | `frontend/src/components/views/WorkflowsView.vue`, `frontend/src/components/panels/WorkflowsSidebar.vue` | none | a workflow file mid-drop on the grid |
| Swap models and LoRAs | inspector › *Change model set…*; LoRAs › *Edit LoRAs* | `frontend/src/components/panels/WorkflowUseSetMenu.vue`, `frontend/src/components/panels/WorkflowTab.vue` | own workflow | the LoRA editor with two LoRAs and their strengths |
| Expose a parameter | inspector › Parameters › *Edit parameters* › *Expose a parameter…* | `frontend/src/components/panels/WorkflowParamsDialog.vue` | own workflow | the picker with one input chosen |
| Versions | inspector › *Version N* › Versions menu | `frontend/src/components/panels/WorkflowVersionMenu.vue` | own workflow | the Versions menu with *Go back to this version* |
| Workflow sets | title bar *Models* › *Sets* › *New workflow set* | `frontend/src/components/views/ModelSetGrid.vue` | own workflow | a workflow set of three workflows |
| Replace missing models | Models › *Sets* › a set with missing models › *Replace…* | `frontend/src/components/panels/ModelSetPanel.vue`, `frontend/src/components/io/ReplaceMissingDialog.vue` | own workflow | the Replace missing dialog offering a shelf model |

### Studio

Dress your cast, draw locations and props, and stage scenes. **Unstable.**

- **Goal:** Generate · **Workspace:** Studio (title bar *Studio*)
- **Needs:** person (a scene can be made with nothing, but the Studio's core is its cast)
- **Startup:** yes (slide 6) · **New:** yes (the Studio; outfit, location and prop sheets)
- **Search terms:** studio, scene, scenes, stage, staging, compose, composition, director, storyboard, shoot, shot, takes, cast, casting, actors, characters, wardrobe, outfit, outfits, costume, clothes, clothing, dress, dress up, outfit sheet, location, locations, background, backdrop, environment, setting, place, angles, camera angle, viewpoint, props, objects, items, prop sheet, comic, story, visual novel, consistent scene
- **Illustration (unstable):** a scene's stage: two of the cast placed on a location's picture, the Takes strip below.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Open the Studio | title bar *Studio*; sidebar Scenes, Favourites, Cast, Locations, Props | `frontend/src/components/TitleBar.vue`, `frontend/src/components/panels/StudioSidebar.vue` | none | the Studio sidebar with a project picked (**unstable**) |
| Make a scene | Studio sidebar › *New scene* › *Make scene* | `frontend/src/components/views/StudioView.vue` | none | the New scene dialog with a name and a location (**unstable**) |
| Cast and wardrobe | Studio › *Cast* › a person › *Wardrobe* › *New outfit…*; or right-click a person in Media › *Draw outfit sheets…* | `frontend/src/components/views/StudioItem.vue`, `frontend/src/components/panels/SideBar.vue` | person | a person's wardrobe, each outfit drawn from four angles (**unstable**) |
| Locations | Studio › *Locations* › *Add a set…* or *New sheets…*; or right-click the Media *Sets* heading › *Draw location sheets…* | `frontend/src/components/views/StudioView.vue`, `frontend/src/components/views/StudioItem.vue` | none | a location's Wide, Reverse, Left and Right views (**unstable**) |
| More angles | Studio › a location › *More angles…* | `frontend/src/components/views/StudioItem.vue`, `frontend/src/components/io/LocationAnglesDialog.vue` | location | new views of one place from one of its pictures (**unstable**) |
| Props | Studio › *Props* › *Add a set…* or *New sheets…*; or the Media *Sets* heading › *Draw prop sheets…* | `frontend/src/components/views/StudioView.vue`, `frontend/src/components/views/StudioItem.vue` | none | one prop from Front, ¾, Side and Back (**unstable**) |
| Stage a scene | a scene › place the cast on the location (drag, or the arrow keys); *Outfit*, *Pose*, *Brings* per person | `frontend/src/components/views/StudioScene.vue` | scene | the stage with each person's mark and the inspector showing Wearing and Doing (**unstable**) |
| Shoot takes | a scene › *Shoot* (stills or a clip); *Make a clip from this take* | `frontend/src/components/views/StudioScene.vue` | scene | the Takes strip with three stills and a clip (**unstable**) |

## Edit

### Edit a picture

Crop, inpaint, extend, upscale or cut out objects without leaving the lightbox.

- **Goal:** Edit · **Workspace:** Media (lightbox)
- **Needs:** picture
- **Startup:** yes (slide 4, Extend) · **New:** yes (Extend)
- **Search terms:** edit, editing, editor, crop, cropping, trim, rotate, straighten, flip, inpaint, inpainting, fix, repair, retouch, touch up, remove object, erase, replace, fill, generative fill, mask, masking, brush, outpaint, outpainting, extend, expand, uncrop, zoom out, widen, bigger canvas, upscale, upscaling, enlarge, upsize, super resolution, enhance, increase resolution, filters, adjust, brightness, contrast, blur, sharpen, greyscale, grayscale, sepia, invert, effects, segment, segmentation, detect objects, object detection, open with, external editor, Photoshop, GIMP, Krita
- **Illustration:** the lightbox *Edit* tab with the *Mark where* tools and a selection on the picture.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Crop and rotate | lightbox › *Edit* › *Quick changes* › *Crop* (C) / *Rotate* | `frontend/src/components/views/Lightbox.vue` | picture | a crop frame on a landscape |
| Inpaint | lightbox › *Edit* › *Mark where* (wand, rectangle, lasso, brush), then an Inpaint workflow | `frontend/src/components/views/PaintToolbox.vue`, `frontend/src/components/views/LightboxEditPanel.vue` | picture | a lasso selection round an object, an Inpaint workflow chosen |
| Extend (outpaint) | lightbox › *Edit* › *Mark where* › *Extend* (X), then an Outpaint workflow | `frontend/src/components/views/PaintToolbox.vue`, `frontend/src/components/views/LightboxEditPanel.vue` | picture, outpaint workflow | a landscape's edges dragged outwards, the new border dashed (**unstable**) |
| Make an Outpaint workflow | Workflows › right-click a workflow › *Convert to Outpainting workflow* | `frontend/src/components/panels/WorkflowSelectionBar.vue` | own workflow | the menu item on a workflow card (**unstable**) |
| Upscale | lightbox › *Edit* › *Quick changes* › *Upscale…* | `frontend/src/components/views/Lightbox.vue` | picture | the upscale choice with the size before and after |
| Filters | lightbox › *Edit* › *Quick changes* › *Filters…* | `frontend/src/components/views/Lightbox.vue` | picture | a filter with its parameters and the result |
| Segment | right-click › *Analyse* › *Segment* | `frontend/src/components/widgets/ImageGridContextMenu.vue` | picture | a picture with its detected objects boxed |
| Open with | right-click › *Open with* › an app | `frontend/src/components/widgets/OpenWithMenu.vue` | picture | the Open with submenu naming an image editor |

## Train

### Consistent characters

Draw character sheets, build a training set and train a LoRA of one person.

- **Goal:** Train · **Workspace:** Media → Models
- **Needs:** person (no pictures needed: sheets can be drawn from words)
- **Startup:** yes (slide 5) · **New:** yes (train a LoRA from a person or a set)
- **Search terms:** consistent character, character consistency, same face, keep a face, same person every time, LoRA training, train, training, fine-tune, finetune, custom model, my own LoRA, character LoRA, face LoRA, person LoRA, dataset, training set, training data, training captions, character sheet, reference sheet, turnaround, model sheet, front side back, poses, AI-toolkit, ai-toolkit, musubi, kohya, steps, epochs, checkpoints, train longer, continue training, resume training, runs, training progress, use LoRA, create with LoRA, trigger word
- **Illustration:** a character sheet (front, ¾, side, back) beside the Train dialog.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Character sheets | right-click a person › *Generate character sheets…* | `frontend/src/components/panels/SideBar.vue`, `frontend/src/components/io/CharacterSheetsDialog.vue` | person | a sheet of four angles, the kept one marked |
| Create a training set | right-click a person › *Create a training set…* | `frontend/src/components/views/TrainingSetBuild.vue` | person with pictures | the training-set builder with suggested pictures |
| Train a LoRA | right-click a person or set › *Train LoRA…* | `frontend/src/components/io/TrainLoraDialog.vue` | person with pictures | the Train dialog doing the sums |
| Training runs | title bar *Models* › sidebar *Training runs* | `frontend/src/components/panels/ModelsSideBar.vue`, `frontend/src/components/views/TrainingRuns.vue` | training run | a run's previews improving step by step |
| Train longer | Training runs › right-click a run › *Train longer…* | `frontend/src/components/views/TrainingRuns.vue` | training run | the steps slider extending a kept run |
| Create with LoRA | right-click a person › *Create with LoRA…* | `frontend/src/components/panels/SideBar.vue` | person with a LoRA | new pictures of the person from their LoRA |

## Share & connect

### Export

Take pictures out as a ZIP, to a folder or as a training dataset.

- **Goal:** Share & connect · **Workspace:** Media
- **Needs:** picture
- **Startup:** no · **New:** no
- **Search terms:** export, download, save, save as, save to disk, copy out, take out, ZIP, zip archive, archive, bulk download, batch export, folder export, send to folder, dataset, training dataset, COCO, Ideogram, bounding boxes, annotations, labels, captions, export recipe
- **Illustration:** the Export panel over 24 selected pictures, *Bounding boxes* set to COCO.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Export as a ZIP | select pictures › selection bar *Export* › *Export* | `frontend/src/components/panels/SelectionBar.vue`, `frontend/src/components/panels/TbExportPanel.vue` | picture | the Export panel with its options |
| Export to a folder | Export panel › *Export to Folder…* | `frontend/src/components/panels/TbExportPanel.vue` | picture | the folder picker from the panel |
| Bounding boxes (COCO, Ideogram) | Export panel › *Bounding boxes* › COCO JSON / Ideogram 4 JSON | `frontend/src/components/panels/TbExportPanel.vue` (options in `frontend/src/stores/useExportStore.js`) | picture | the dropdown open on the two formats |
| Export a recipe | see Recipes › *Export a recipe* | `frontend/src/components/panels/WorkflowRecipesTab.vue` | recipe | (shared with Recipes) |

### Share links

Send a read-only link to a picture, set, person or project, and revoke it later.

- **Goal:** Share & connect · **Workspace:** Media, Settings
- **Needs:** picture
- **Startup:** no · **New:** no
- **Search terms:** share, sharing, link, share link, public link, send, send to a friend, show someone, client, gallery link, read-only, view-only, guest, embed, watermark, watermarking, signature, revoke, unshare, stop sharing, remove share, remote access, access from phone, access from anywhere, outside my network, internet, remote, port
- **Illustration:** the Share dialog for a set, read-only, *Embed watermark* ticked, `example.com` link.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Share a picture | right-click one picture › *Share image* | `frontend/src/components/widgets/ImageGridContextMenu.vue`, `frontend/src/components/io/ShareDialog.vue` | picture | the Share dialog with its link |
| Share a set, person or project | right-click it in the sidebar › *Share "<name>"* | `frontend/src/components/panels/SideBar.vue` | set, person or picture | the sidebar menu with the Share item |
| Watermark | Share dialog › *Embed watermark*; Settings › Privacy & sharing › Watermark | `frontend/src/components/io/ShareDialog.vue`, `frontend/src/components/settings/SharingSection.vue` | picture | a shared picture with the watermark in its corner |
| Remove shares | right-click a shared item › *Remove all shares* | `frontend/src/components/widgets/ImageGridContextMenu.vue`, `frontend/src/components/panels/SideBar.vue` | shared | the menu item on a shared set |
| Remote access | Settings › Server › Remote access (desktop app only) | `frontend/src/components/settings/ComputeSection.vue` | none | the Server pane with the server enabled |

### API

Script PixlStash from your own tools with a token and the REST API.

- **Goal:** Share & connect · **Workspace:** Settings
- **Needs:** none (`POST /pictures/import` fills an empty library)
- **Startup:** no · **New:** no
- **Search terms:** API, REST, REST API, HTTP, endpoint, endpoints, token, API token, API key, key, bearer, access token, personal access token, automation, automate, script, scripting, Python, curl, integration, developer, programmatic, OpenAPI, Swagger, Scalar, API docs, API reference, permissions, scopes, read-only token, revoke token
- **Illustration:** the API reference page at `/scalar` beside a `curl` call with a Bearer token.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Create an API token | Settings › Account & tokens › *API Tokens* › *New token* | `frontend/src/components/settings/AccountSection.vue` | none | the New token dialog choosing read-only or full access |
| Read the API reference | Settings › Server › *API* › *API reference* › *Open* (the server's `/scalar` page) | `frontend/src/components/settings/ComputeSection.vue` | none | the reference open on `GET /pictures` |
| Call the API | any HTTP client with `Authorization: Bearer <token>` | `pixlstash/openapi_custom.py` (the quick start) | none | a terminal fetching the first 50 pictures with `curl` |
| Revoke a token | *API Tokens* › a token row › delete | `frontend/src/components/settings/AccountSection.vue` | none | the *Delete token?* confirm |

### Connect an AI agent

Let an AI assistant search and tag your library over MCP.

- **Goal:** Share & connect · **Workspace:** Settings
- **Needs:** picture
- **Startup:** no · **New:** no
- **Search terms:** AI agent, agent, assistant, AI assistant, chatbot, chat, LLM, MCP, Model Context Protocol, MCP server, Claude, Claude Desktop, Claude Code, copilot, ask questions, talk to my library, natural language, tools, tool use, AI tagging, connect agent
- **Illustration:** an agent chat answering "find my best lake pictures and tag them portfolio" with a `search_pictures` call.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Connect an agent | Settings › Account & tokens › *API Tokens* › *Connect AI agent* | `frontend/src/components/settings/AccountSection.vue`, `frontend/src/components/settings/ConnectAgentDialog.vue` | none | the Connect an AI agent dialog with its command |
| Ask about your pictures | the agent, over MCP | (outside the app) | picture | an agent's reply quoting tags and scores from the library |

## Keep it safe

### Undo & recovery

Undo a change, bring back deleted pictures and restore a snapshot.

- **Goal:** Keep it safe · **Workspace:** Media, Settings
- **Needs:** picture
- **Startup:** no · **New:** no
- **Search terms:** undo, redo, revert, go back, history, operation history, Ctrl+Z, mistake, oops, recover, recovery, restore, bring back, undelete, deleted pictures, trash, recycle bin, bin, wastebasket, scrapheap, snapshot, snapshots, backup, back up, restore point, rollback, roll back, time machine, ghosts, gone pictures, removed models, lost pictures
- **Illustration:** the title-bar History panel listing the last four operations.

| Task | Entry point | File | Needs | Illustration |
|---|---|---|---|---|
| Undo history | title bar Undo / Redo and the *History* chevron | `frontend/src/components/panels/UndoControl.vue` | history | the History panel open |
| Scrapheap | sidebar *Scrapheap* › select › *Restore Selected*; retention in Settings › Recovery › Scrapheap | `frontend/src/components/panels/SideBar.vue`, `frontend/src/components/panels/SelectionMenu.vue`, `frontend/src/components/settings/ScrapheapSection.vue` | picture | the Scrapheap view with *Restore* |
| Snapshots | Settings › Recovery › Snapshots; right-click › *Restore from snapshot* | `frontend/src/components/settings/SnapshotsSection.vue` | snapshot | the Snapshots list with a restore preview |
| Ghosts | Settings › Recovery › Ghosts | `frontend/src/components/settings/GhostsSection.vue` | picture | picture and model ghosts listed |

## Greying and Start here

### Three states, from library content only

| State | Meaning | When |
|---|---|---|
| **Start here** | the best next concepts for this library | by the rules below |
| **Normal** | can be tried now | its Needs are met |
| **Greyed** | looks greyed in the grid, **and always opens** | its Needs are not met |

**A greyed concept is a hint, not a lock. Nothing in the Guide is disabled.**
Opening a greyed concept shows exactly what a normal one shows: its
illustrations, its written page, its task list and its wizards. A wizard whose
task's content is missing shows its illustration and sentence, then
"First: <the concept that provides it>" instead of the click steps.

Greyed must not reuse the system's disabled state (`--opacity-disabled`, the
`not-allowed` cursor): those read as a lock. The design draws a greyed card
with its border and click target kept, only its picture desaturated, a
medium-weight title and a "Needs …" line.

Greying depends on library content only. A missing ComfyUI, AI-toolkit or MCP
client never greys anything. It makes the concept's first task its set-up task
(*Connect ComfyUI*, *Set up AI-toolkit*). That is why Generate with ComfyUI is
lit on an empty library.

### Start here rules

1. **Empty library** (no picture): Import pictures, Generate with ComfyUI.
2. **Pictures but no person:** People, Similar pictures & faces.
3. **A person exists:** Consistent characters, Studio.
4. **Otherwise:** none, and the grid is plain.

### Check: the empty library

On an empty library the concepts that are **not** greyed are New in 2.x,
Import pictures, Libraries, Generate with ComfyUI, People, Models and API (seven:
the plan's six plus API, added after the plan). Only Import pictures and
Generate with ComfyUI are Start here. Every other concept needs at least a
picture, a recipe, a workflow of the user's own or a person.

## Startup guide

- **When it shows:** on the first launch on a **non-empty** library, and after
  any update that adds a *New in 2.x* headline, when it opens on New.
- **What it shows:** a carousel of six concepts, each one illustration and one
  sentence, with *Try it* starting that concept's first wizard:
  1. Search (colour search)
  2. Similar pictures & faces (*Suggest more pictures*)
  3. Generate with ComfyUI (*Make more like these*)
  4. Edit a picture (Extend, **unstable**)
  5. Consistent characters
  6. Studio (**unstable**)
- **Empty library:** only Import pictures and Generate with ComfyUI.
- **Exiting:** Esc, × or *Not now* at any step. It never shows again for that
  version, and can be replayed from the Guide (*Play the welcome again*).
- **Where "seen" is stored:** beside `last_seen_version` and `whats_changed` in
  the server-side user config (`pixlstash/db_models/user.py`), as
  `guide_welcome_seen`. A session holding only a read-only token never writes
  it.
- **Greyed slides:** on a library with pictures and no person (the likely
  first launch), slides 5 and 6 are greyed and *Try it* reads "First: People",
  as a greyed task does (owner's ruling, #2825).

## Where this departs from the plan

Checking each task against `develop` changed these:

- **A concept's need is its core tasks' need, not its weakest task's.** The
  plan said "weakest", but its own table contradicts that: Browse & inspect,
  Tags & descriptions, Workflows, Share links and Connect an AI agent each hold
  a task that needs nothing (shortcuts, a tagger setting, dropping a workflow
  file, remote access, the connect dialog). Read literally, the rule would
  light all five on an empty library. The plan's table wins, and those tasks
  stay tryable inside their greyed concept.
- **API is a concept** (owner, after the plan). It needs nothing, so the empty
  library leaves seven concepts lit, not six.
- **People › Colour and icon is dropped.** People have no colour or icon
  control; sets do, so *Icon and colour* moved to Sets & projects. *Assign
  faces* needs a face and a person; *Draw a face box* needs only a picture.
- **Studio needs a person**, though *Make a scene*, locations and props
  need nothing: its core is the cast, as the plan and the design draw it.
- **Extend needs an Outpaint workflow**, made by *Convert to Outpainting
  workflow*. That workflow is library content, so the Extend task greys on its
  own ("First: Workflows"), though Edit a picture as a concept does not. The
  convert step is listed as a task.
- **Renamed to the app's words:** *Generate character sheets…*, *Run…* (not
  "rerun"), *Remove all shares* (revoke), *Segment* (detect objects), *Faces
  and reference cluster…*, *Import…* (Danbooru), *Tag automatically*, *Add a
  library* (the mapping wizard), *Connect AI agent*, Union / Overlap /
  Difference / Unique (set operations).
- **Moved:** exporting a recipe lives in the Workflows *Recipes* tab, not the
  lightbox. Workflow sets live in the Models workspace. "Downloads" is a
  watched Downloads folder, not a downloads list. The Moves row exists only
  while moves are pending. Remote access is desktop-only.
- **Similarity sort needs a person**: the sort is "Similarity to" a person.
- **F1 opens the Guide.** Keyboard shortcuts keep `?` only (owner's ruling,
  #2825).
- **Some needs have no endpoint yet** ("person with a LoRA", "text in a
  picture", a share of a person, set or project). They are marked "needs a
  count".
