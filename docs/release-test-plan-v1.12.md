# PixlStash v1.12.0 Feature Test Plan

What changed between **v1.11.3** and **v1.12.0**, and how to check it by hand.
This plan sits on top of the standing [release test plan](release-test-plan.md):
run that one for installation, packaging, the desktop app and the generic
upgrade chain, then run this one for what is new. The source of truth for
"what is new" is the `changelog.d/` fragments on `develop` plus the three
workflow issues this release also carries: **#1622**, **#1623** and **#1624**
(stages 2–4 of #1620, the Workflow + Recipe model).

**The #1620 stages are not on `develop` at the time of writing.** Section 3
describes what they must do once they land; until then mark it ⏭ with the
reason "not merged". v1.12.0 does not ship without them (#1623 says the
cut-over lands before stable), so section 3 must be green before sign-off.

Mark every row ✅ Pass / ❌ Fail / ⏭ Skip (with the reason). Each row states
what you should see and what **wrong** looks like, so a tester who does not
know the design can tell a regression from intent.

---

## How to use this plan

1. **Order is by risk.** Section 1 can lose data; do it first and on a copy.
   If a step there is about to delete, move or empty something it should not,
   **stop, do not complete the gesture**, and report it.
2. Section 2 is the upgrade: it needs a real v1.11.x library, copied.
3. Sections 3 onwards are features, grouped by screen.
4. Section 12 lists what automated suites already prove, so you can spend your
   attention elsewhere; section 13 is what only a person can judge.

### Test environment

| Need | Why |
|---|---|
| A **copy** of a library last opened by v1.11.x, with ComfyUI pictures, a few A1111/Forge pictures, animated GIFs, TIFFs and some screenshots of text | Upgrade and re-read checks (section 2) |
| A ComfyUI you can reach from PixlStash, with the ComfyUI-PixlStash node pack, Impact Pack (FaceDetailer) and an upscale node | Run popup, Edit tab, stages, pull |
| A second ComfyUI **without** the ComfyUI-PixlStash pack, or the pack disabled | Install-help messages (section 8.7) |
| At least two checkpoints of different families (e.g. an SDXL and a Flux model), a GGUF file, a model file present twice on disk | Model shelf, family flags, Keep one copy |
| An MCP-capable client (Claude Code or similar) | Section 10 |
| A Mac with Apple Silicon | Section 11.1 |
| An empty folder and a folder of loose pictures, e.g. `/home/me/Pictures/new-lib` | First import (section 1.1) |

Never use your real library for section 1. Copy it (`vault.db` and the hub)
and point a test install at the copy. **A copied library still points at your
real model folders**, so every model-shelf delete, Keep one copy or purge acts
on real files: for those rows, use throwaway model files in a scratch folder
added to the shelf for the test (e.g. `/home/me/scratch-models/`), never a
model you want to keep. **The hub is shared by every install on the machine**
(it lives in the platform user-data folder, not the library), so the #1623
conversion (section 3.3) and anything else that writes the hub must run on a
test machine or a user account whose hub you can lose, after backing up the
hub folder.

**Holding Shift on the model shelf turns Delete into "Permanently delete"**
(unlink, no undo). Release Shift before pressing Delete in any row here; if
the button reads *Permanently delete* when you did not mean it, stop.

---

## 1. Data safety — do these first

### 1.1 First import leaves a folder untouched until you answer

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Point PixlStash at a folder of loose pictures. Before answering the question, look in the folder: **no** `vault.db`, **no** thumbnail cache | Any file PixlStash wrote appears in the folder before you chose | |
| 2 | Start the import, then abort part way. The folder is exactly as before: same files, same places, no database, no cache | A half-built database or cache is left behind, or a picture moved | |
| 3 | Close the question dialog instead of answering. Same result as #2; next launch asks about the folder again | It opens a half-imported library | |
| 4 | Kill the process (or pull power on a VM) mid-import. On restart: folder unchanged, the question comes back | A library opens with part of the pictures | |
| 5 | "Start an empty library here" is offered for a folder that already holds pictures, and choosing it indexes nothing | The option is missing when pictures are present, or pictures get imported | |

### 1.2 Folder overlap rules

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Add a watch folder inside an existing reference folder, then the reverse. Both are refused with a reason | Either is accepted | |
| 2 | Add a watch or reference folder inside a library's folder, and one that contains a library's folder. Both refused | Accepted | |
| 3 | Add a library, from the app **and** with the command line, inside or around another library's watch or reference folder. Refused both ways | The CLI path accepts what the app refuses | |
| 4 | Organise an existing folder into a library. It is not registered as a reference folder inside another library's folder | It is | |

### 1.3 Model shelf: Keep one copy, and delete

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Select a model that is on disk twice → **Keep one copy…**. **Nothing is pre-selected**; you must choose which stays | A copy is pre-selected, or the confirm is enabled before you choose | |
| 2 | Before confirming, the dialog says whether your ComfyUI reads the copy you are about to lose | Silent about ComfyUI | |
| 3 | Confirm. The other copies are in the **system trash** (restorable), not gone | Files are hard-deleted | |
| 4 | The shelf row keeps its name, base model, triggers and people. A picture's Recipe tab still names the model | Any of these reset or vanish | |
| 5 | Run a workflow from PixlStash that names the removed copy. It runs on the kept copy and tells you which file it used | It stops on a missing model | |
| 6 | **Delete** a throwaway checkpoint from the shelf (Shift released). The confirm lists, as information, VAEs/encoders that only ran with it, those other models still use, and says when it cannot tell | No breakdown, or it claims a shared VAE is exclusive | |
| 7 | On the Workflow sets screen, select one card and Delete. The file(s) **to be deleted** are only **that card's own model** — never the shared VAE inside it, never a neighbouring set. The informational breakdown from #6 may name other models; that is not a deletion target. **If the list of files to delete names more than the one model, stop and do not confirm** | Another model is among the files to delete | |

### 1.4 Workflow delete, stacks, sets

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Delete an imported workflow. The file goes to the system trash; the workflow's pictures, ratings and notes stay | Pictures lose their workflow or ratings | |
| 2 | Restore that file from the trash. The workflow comes back | It does not | |
| 3 | "Delete" a workflow found only in pictures (no file). It is **hidden**, not deleted, and nothing goes to the trash | Offered as a real delete | |
| 4 | Remove a file from the `workflows` data folder. The workflow is **not** deleted | It disappears | |
| 5 | Stack → **Keep recipes only**. The confirm says why the others stay (no workflow, model not on shelf, no thumbnail yet, or a ghost your setting would not keep). Confirm, then **one Ctrl+Z** puts all of it back | Undo restores only some, or needs several presses | |
| 6 | On a throwaway set, `PUT /picture_sets/{id}/members` with an empty list. Refused unless `allow_empty: true`; with it, the reply says how many pictures were removed | The set is silently emptied | |
| 7 | `DELETE /picture_sets/{id}/members/abc` (non-numeric picture id) returns **422**, not 500 | 500 | |
| 8a | `POST /dedup/verdicts/batch` naming the same pair twice: a clear 4xx saying what is wrong | 500 | |
| 8 | Sidebar → a set → **Suggest more pictures** → add all. One Undo takes back the whole add | Undo takes back one picture | |

### 1.5 Library integrity at start-up

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | With GIF and TIFF pictures in the library, restart twice. They stay in the grid with their tags, people and sets | They drop out on start | |
| 2 | Import pictures, then tag one before auto-tagging reaches it. After the tagger finishes, your tag is still there | Your tag was removed | |
| 3 | Same after a **Retag** cleared the tags | Removed | |

---

## 2. Upgrade from v1.11.x

Run on a **copy** of a v1.11.x library. Migrations 0117–0123 are new in this
release; the standing plan's section 2 covers the generic chain.

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | First start on the copy: no schema error, grid loads, counts of pictures, sets, characters and projects match the v1.11 copy | Any count differs | |
| 2 | A1111/Forge pictures are **read once** after the update: they gain a Recipe tab and are grouped into workflows. Watch the task indicator work through them, then go quiet | They never gain a recipe, or the re-read repeats on every start | |
| 3 | Animated GIFs get new similarity once (averaged over frames); their **faces are kept** as they were | GIF faces are wiped or re-detected | |
| 3a | Import a **new** animated GIF where a face appears only after the first frame, and one whose first frame is blank: the face is found, and the caption comes from a later frame | Face missed, or empty caption | |
| 4 | Text in pictures (OCR) is read in the background for document-like pictures; the backlog drains | It never starts, or keeps re-running | |
| 5 | Models shelf opens on **Workflow sets** once (the grouping resets to the new default one time). Sort, column widths, collapsed groups and folder layout are kept | Sort or widths reset, or the grouping resets on every open | |
| 6 | Many previously "not set" base models now show a base model marked **guessed**. One you set yourself in v1.11 is unchanged | Your own setting was overwritten | |
| 7 | Existing workflows set up in v1.11 still run exactly as before; imported workflows keep their old input mapping | A previously runnable workflow no longer runs | |
| 8 | Settings has **no Workflows section**; ComfyUI host and port moved to Settings › Compute and kept their values | Host/port reset | |
| 9 | Hub conversion (#1623, once merged): see section 3.3 | | |

---

## 3. Workflow + Recipe model (#1620 stages 1–4)

Stage 1 (#1621, per-run stage skip) is merged but has no UI of its own;
stages 2–4 are #1622, #1623 and #1624. After them there are **two layers the
owner sees: Workflow and Recipe.** A workflow is what used to be a stack (one
graph shape); the checkpoint, VAE, encoders, LoRAs and settings are recipe
values over the workflow's **default recipe**.

### 3.1 Identity and default recipe (#1622)

#1622 is additive and has no UI of its own. Rows #2–#6 become observable once
#1623 shows `default_recipe` in the inspector's Models and Defaults (or read
it from `GET /workflows`, which carries `default_recipe` after #1623). Row #1
applies only if #1622 is tested on a build without #1623; otherwise mark it ⏭.

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | With only #1622 merged, the Workflows screen, cards, stacks and Run popup look and behave exactly as before | Anything visible changes | |
| 2 | Pick a workflow whose 4★+ pictures mostly used checkpoint A and a few used B. Its default recipe's checkpoint is **A** | B, or none | |
| 3 | A LoRA present in more than half of the 4★+ pictures is in the default recipe at its most common strength; one present in half or fewer is **not** | The rule is ≥ half, or any LoRA seen once is included | |
| 4 | Prompt, negative and seed are **never** part of the default recipe | A default prompt or seed appears | |
| 5 | A workflow with no 4★+ pictures still gets a default recipe (from all its pictures) | Empty defaults | |
| 5a | Stages: a workflow whose base graph has FaceDetailer, where most 4★+ pictures ran **without** it, has that stage **off** by default; where most ran with it, **on** | Always on | |
| 6 | Run with a checkpoint from a **different family** than the workflow's default. The run is **flagged, and still runs** | Blocked, or no flag | |
| 7 | Pictures do not move between workflows when #1622 lands (they are filed by exact graph) | A workflow's picture count changes | |

### 3.2 Per-run stage skip (#1621, visible once #1623/#1624 add the control)

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | A workflow with FaceDetailer: switch the FaceDetailer stage off in the Run form. The result has no face-detailer pass and lands in the library | Run fails, or the pass still ran | |
| 2 | Same for an upscale stage: result is at the pre-upscale size | Upscaled anyway | |
| 3 | A stage that cannot be taken out cleanly (a hires fix done in pixel space: upscale → VAE encode → sampler) is **refused** with a reason; it never falls back to a full run | It runs the full graph silently, or half-removes the stage | |
| 4 | An img2img workflow whose input picture is resized: that resize is **not** offered as an "upscale" stage | It is | |
| 5 | A FaceDetailer whose MASK or DETAILER_PIPE output feeds another node: switching it off is **refused** | It runs with the pass half-removed | |
| 6 | A graph that saves before **and** after the upscale: with upscale off, **one** picture is imported, not two | Two identical pictures | |
| 7 | Upscale model file missing from ComfyUI, upscale stage off: the run proceeds (the orphaned upscale loader is dropped) | Refused for a missing upscale model | |

### 3.3 Cut-over and conversion of existing state (#1623)

**Merge, never drop.** The owner did not ask for this conversion, so nothing
they typed may disappear. Prepare, on v1.12-dev *before* #1623, a hub copy
with: a stack whose cover card is named and has notes, a non-cover card with
a different name, one hidden member, two members with conflicting defaults, a
manual stack, a partially unstacked stack, a LoRA marked "Workflow"
(structural) on the cover, and a saved recipe made on a checkpoint-B card.
Also: a card with a missing model fixed from the Workflow tab (section 4.4
#7), and a topology whose cards sat in two manual stacks. The **cover** of a
stack is the card at stack position 0, else the one with most variants.
Then upgrade, and keep the server log.

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | The stack is now **one** workflow, named after the cover card | Several workflows, or a generated name | |
| 2 | The non-cover's name appears in the notes as `Also named: …` | Lost | |
| 3 | Notes of every card are kept, each block headed by its card's name | Any note text lost | |
| 4 | The workflow is hidden **only if every** member was hidden; the one-hidden-member stack is visible | Hidden because one member was | |
| 5 | Conflicting defaults: the cover's value wins. A default that could not be translated is named in the log **with its value** | The other member's, or dropped without a log line | |
| 5a | The model fix from section 4.4 #7 still applies: the workflow runs on the replacement model | The missing-model error is back | |
| 5b | The topology that sat in two stacks joins the workflow that held more of its variants | The other one | |
| 6 | Pins and picture inputs are kept (union for pins; cover wins on conflict for inputs) | A pin or pinned picture is gone | |
| 7 | Manual stack: its cards are one workflow | Split apart | |
| 8 | Partial unstack: a topology is split out only if **every** card of it had been unstacked; otherwise it stays in | Split out on one unstacked card | |
| 9 | The structural LoRA on the cover is now a **default-recipe LoRA** | Gone from defaults | |
| 10 | The checkpoint-B saved recipe **runs on B** after the conversion, not on the workflow's default checkpoint | It silently runs on A | |
| 11 | A saved recipe with no matching workflow is left untouched (still listed, not deleted) | Deleted | |
| 12 | Restart again: nothing changes (the conversion runs once and is idempotent) | Names, notes or counts change on the second start | |
| 13 | Links that used `?card=` now use `?workflow=`; an old bookmarked card link does not crash the view | Blank view or error | |
| 14 | Recipes tab on a converted workflow lists the same saved and discovered recipes as before the conversion (union of the old members') | Recipes of non-cover members missing | |

### 3.4 What the owner sees after the cut-over (#1623 frontend minimum)

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Workflows grid: one card per workflow; **no stack panel** opens on double-click or caret | A stack panel still opens | |
| 2 | Workflow inspector: **no "LoRA slots" section** and no Workflow/Recipe switch per loader | Either is still there | |
| 3 | Inspector Models and Defaults read the default recipe (checkpoint, LoRAs, featured values) | Empty or showing one old member's values | |
| 4 | Merge two workflows from the selection bar; split one from the ⋯ menu. Pictures follow; nothing is deleted. Undo or split back restores the original picture counts | Pictures lost or counted twice | |
| 5 | Run popup's Workflow picker lists workflows, **not** stack members | Members listed | |
| 6 | Run popup sends the checkpoint you pick; the result was made with it | Default checkpoint used | |
| 7 | Library filter "this workflow's pictures" combined with a checkpoint or LoRA filter narrows to pictures with **both** | Shows either | |
| 8 | Export a workflow: the file is the base graph with the default recipe; LoRA loaders outside the default are bypassed; default-off stages bypassed; still says what it left out | A non-default LoRA or the prompt is in the file | |
| 8a | Same export: the **default-recipe LoRAs are still loaded** in the file (e.g. an always-on speed LoRA) | The speed LoRA was stripped | |
| 9 | MCP `get_recipe` returns a workflow id; `list_pictures`/`count_pictures` filter by workflow id; MCP write tools talk about workflows, not cards | "card" wording or a card key | |

### 3.5 Docs and design hand-off (#1624)

Not visible in the app. Check once #1624 merges:

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Architecture docs describe the two-layer model; no doc presents `workflow_slot_mark`, `/slots` or stack members as a current concept | Old concepts described as current | |
| 2 | A follow-up design issue exists for the two-layer inspector and Recipes tab, linking #1620 | Missing | |

---

## 4. Workflows screen

### 4.1 Getting workflows in

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | **Add…** a ComfyUI workflow file (API format and editor format). It appears as a card; no input-mapping dialog | A mapping dialog appears | |
| 2 | Add the same file again: no second card | Duplicate | |
| 3 | Add a different file under a name you already use: kept alongside, no replace offer | Offers to replace, or overwrites | |
| 4 | Drop a workflow file into the `workflows` folder in PixlStash's data folder. It appears | Not picked up | |
| 5 | **Pull from ComfyUI** (it also reads ComfyUI's recent run history, used by Clone with new models). A summary says how many were new, how many your pictures already used, which won't run (missing node pack, named) and which name missing model files. PixlStash writes nothing back to ComfyUI | No summary, or ComfyUI's saved workflows change | |
| 6 | Pull again: nothing added twice. Delete a pulled workflow in PixlStash, pull again: it does **not** come back | Duplicates, or the deleted one returns | |
| 7 | A workflow saved in ComfyUI with a bypassed node wired between two others matches the workflow its pictures came from (one card, not two) | Two cards | |
| 7a | A workflow saved from the ComfyUI editor that uses MultiGPU or GGUF CLIP loaders shows those models on its card | Models missing from the card | |
| 8 | An editor-format workflow shows "parameters not available yet" until converted with the node pack's *Convert for PixlStash*; after conversion it runs | Runs half-configured, or no explanation | |

### 4.2 Grid, cards and names

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Grid of cards with pictures, models, LoRAs and type. Sort by rating, last used, picture count, name | A sort does nothing | |
| 2 | An unnamed card reads like its workflow, e.g. "&lt;model&gt;: Text to Image + FaceDetailer"; the model is called what the shelf calls it | "Untitled workflow", or a raw filename like `x_bf16.safetensors` | |
| 3 | A card with no known model is named for what it does ("Upscale"); clashes are numbered ("Upscale (2)") | Two cards with the same generated name | |
| 4 | Model names drop precision suffixes and show a chip instead (`BF16`, `FP8`, `Q4_K_M`) on the shelf, cards and recipes | Suffix still in the name | |
| 5 | A workflow whose checkpoint won't load says **Checkpoint missing** on the card and in the inspector, naming the file (hover for full path) | Shows the name as if fine, or "Not recorded" | |
| 6 | A workflow that never recorded its checkpoint shows the file its graph loads; one with no base model says so | "Not recorded" when the graph names a file | |
| 7 | A workflow whose model names were forgotten reads "N models, names forgotten" | Blank or crash | |

### 4.3 Filters

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Filters bring back one-offs (fewer than three unrated pictures) and hidden workflows; narrow by type, checkpoint, origin, rating | A filter changes nothing | |
| 2 | **Ghosts** keeps only workflows still holding something you deleted | Shows all | |
| 3 | Each active filter has a removable chip; the funnel shows the count | Count wrong | |
| 4 | The picture count in the inspector and a card's ⓘ opens the library on that workflow's pictures as a removable chip | Opens unfiltered | |
| 5 | Opening a workflow from a picture's Recipe tab when the grid hides it (hidden, or a one-off) says so | Leaves you at the top of the grid silently | |

### 4.4 Inspector

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | The inspector opens by default. Close it: a tab on the right edge names the selected workflow and reopens it in one click | Nothing to reopen it with | |
| 2 | **Tasks** tab: start a run from Workflows; the tab pulses; the toast's *Show* jumps to it | No pulse, or *Show* goes elsewhere | |
| 3 | Edit LoRAs (delete, reorder, add from shelf) and save: a **new** workflow is written; the original and its pictures unchanged | Original modified | |
| 4 | Shared LoRAs vs pile: LoRAs in every picture listed as shared; changing ones in a pile you can open or show in library. Before #1623 the pile also offers *promote*; after #1623 promotion is gone (a LoRA becomes a default instead) | A per-picture LoRA listed as shared; *promote* still offered after #1623 | |
| 5 | **Pre-#1623 builds only; ⏭ on the release build.** Stack member picker: a drop-down switches member; Run… runs the picked member | Runs the cover | |
| 6 | **Pre-#1623 builds only; ⏭ on the release build.** An open stack follows the selection; picking a workflow outside it puts the panel away and slides rather than blinks | Stale panel | |
| 7 | **Fix a missing model**: pick a replacement checkpoint/VAE/encoder from the shelf. Pictures, name and settings kept; new pictures land on the same card; the row shows the original model; covers made with the old model are marked and shown after the new ones | New pictures land on a new card | |
| 8 | **Clone with new models…**: pick a checkpoint; VAE and encoders are suggested with where each came from ("Grouped by you" first); LoRAs trained for another family are pointed out and kept; a new card appears beside the original | Original changed, or suggestions without a source | |
| 9 | Clone onto a checkpoint no workflow shares a family with: VAE/encoders of the right type, marked **untested** | No suggestion, or one from the wrong family | |

### 4.5 Recipes tab

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Recipes tab lists every distinct prompt + LoRAs combination on the workflow, with a picture count and a picture that opens | Missing recipes, or counts that do not add up to the workflow's picture total | |
| 2 | **Clone…** copies a recipe into Saved; the original stays, marked Saved | Original disappears | |
| 3 | Reorder saved recipes by drag handle and by **Alt+arrow keys** | Keyboard reorder does nothing | |
| 4 | Select several workflows: the union of their recipes | Only the first workflow's | |
| 5 | ⋯ → rename, export, delete work on a saved recipe | Rename lost on reload | |
| 6 | Export a saved recipe: it lists your prompt word for word, LoRA file names, settings, and warns about a model this machine no longer has, **before** writing; **Export workflow** is offered as the safer choice | File written without the preview | |

### 4.6 Export, duplicate, add a LoRA loader

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Export a workflow. Open the file in a text editor: **no** prompt, seed, look LoRAs, your node titles, save folder, model folder paths, input picture names, values in boxes named like keys/passwords, or names of models this machine lacks (including forgotten ones a picture still records). The dialog says what it left out | Any of those in the file. **This is a privacy check: treat any leak as a release blocker** | |
| 2 | Duplicate a workflow: a copy lands in your workflow folder and opens in ComfyUI; a workflow known only from pictures becomes a file | Original modified | |
| 3 | Add a LoRA loader to a workflow with none: a copy with one loader wired after the model | Original modified | |

---

## 5. Run popup and running workflows

### 5.1 The popup

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Run… from a picture's Recipe tab, from Workflows, and the toolbar Generate button all open the **same** popup | Different dialogs | |
| 2 | Values filled and editable: prompt, LoRAs, size, steps, CFG, seed, checkpoint; the rest under "All N parameters" | Missing fields | |
| 3 | Change a field: a ↺ chip carries the start value and restores it | No chip, or it restores the wrong value | |
| 4 | **Open in Workflows** shows the workflow | Opens the grid unselected | |
| 5 | A run that cannot start stays open with the reason; a missing model is named with the folder it belongs in, and the whole batch stops | The popup closes, or part of the batch runs | |
| 6 | Share link: nothing that starts a run is reachable; the recipe is still readable | A run button works from a share link | |
| 7 | LoRAs the workflow loads are listed, matched to the shelf; swap or change strength; a LoRA the shelf does not know is shown and left as is | Unknown LoRA dropped | |
| 8 | A workflow with **no** LoRA loader says so and offers to run without the LoRA | Silently drops it | |

### 5.2 Selections and picture inputs

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Select 5 pictures → *Make more like these…*: each reruns its own recipe with a new seed; several recipes are called out, and count and seed are set once | All five use one recipe | |
| 2 | *Run a workflow on these…* with a one-picture-input workflow over 5 pictures → **5 runs**, one per picture | One run, or the recipe used instead of the pictures | |
| 3 | Pictures section: selection, a library pick, or a **pinned** picture that later runs reuse | Pin forgotten | |
| 4 | Pinned reference + one open input: the selection goes into the open one with no question. Two open inputs: you choose, and it is remembered | Asks every time | |
| 5 | Delete a pinned picture: its slot is empty and says so | Runs with a stale picture | |
| 6 | "Stack new pictures with the ones they came from" is ticked when pictures are fed in, not remembered across runs; each result joins its source's stack | Results unstacked when ticked | |

### 5.3 Missing pieces handled at run time

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | A LoRA ComfyUI lacks: the popup names it and where the file goes, **before** Run; the run proceeds without it; results are filed under their own workflow | Run refused, or no warning | |
| 2 | A LoRA you asked for yourself, or sharing a loader with LoRAs you have, is **not** skipped | Skipped | |
| 3 | A missing checkpoint, VAE, text encoder or ControlNet still stops the run | Runs | |
| 4 | Skip a LoRA for one run (including a missing one): the workflow itself does not change | Workflow edited | |
| 5 | rgthree **Seed** node missing, feeding one sampler: replaced, the popup says so, seed reaches the sampler. Shared between samplers or "random": asks for the pack | Replaced where it should ask | |
| 6 | WAS Text Multiline / CR Text / Textbox / PrimitiveStringMultiline missing: text written into what it fed, your prompt reaches it, popup says so. A WAS text with a `[token]` or a passthrough Textbox asks for the pack | Prompt ignored | |
| 7 | MultiGPU loaders and the GGUF CLIP loader: a missing UNET/VAE/encoder is reported before the run | Fails only after waiting | |
| 8 | ComfyUI-PixlStash nodes: loaders, searches and likeness gates run; project/set/character loaders run when the library has what they name; the picture loader gets the picture you chose; the picture saver imports each picture **once**; a node that cannot run is named with a reason | A picture imported twice, or the loader reads another picture | |

### 5.4 Saved recipes

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | *Save as recipe* lists prompt, each LoRA on its own row, and each changed setting with the workflow's own value; every line can be unticked | A line cannot be unticked, or unticked lines are saved anyway | |
| 2 | **Seed is unticked** by default | Ticked | |
| 3 | A LoRA the shelf cannot identify is kept, named, with a note that runs will ignore it | Dropped silently | |
| 4 | Saving what you already have reads **Saved** (popup and Recipe tab) | Offers to save a duplicate | |
| 5 | Naming a recipe like an existing one offers **Replace**, naming what it overwrites, and asks first; the recipe keeps its place and picture count | A second row with the same name | |
| 6 | Change a strength by 0.05: saving is offered again | Still reads Saved | |
| 7 | A picture matching a saved recipe shows a banner; its name links to Workflows with the Recipes tab open | Link opens the Workflow tab | |

---

## 6. Picture viewer (lightbox)

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Sidebar has **Info** and **Recipe** tabs. Recipe shows workflow (with link), prompt, models with LoRA strengths and a tick on exact matches, sampler settings, seed, negative, input pictures. Click a model: it opens on the Models shelf | A field missing, or the model click does nothing | |
| 1a | Side panel and Metadata box look like the stats panel's (same tabs and value layout) | A different panel style | |
| 2 | Workflow JSON with Copy and Download is on the Recipe tab, not in Metadata | Still in Metadata | |
| 3 | A picture whose prompt came from a wildcard/style node shows the prompt that **ran** (after a rescan) | Unrelated editor prompt | |
| 4 | An A1111/Forge picture has a Recipe tab; *Generate variants…* is disabled with "no ComfyUI graph". Its LoRA reads as "a file of that name", not a confirmed match; its models count on the shelf | Enabled, or "not connected"; LoRA shown with a tick | |
| 5 | Disabled reasons are distinct: no seed, ComfyUI not connected, read-only share link | All say "not connected" | |
| 6 | An editor-format picture shows its recipe and can be run; a node it could not rebuild is named, prompt/models/seed still shown | "Not made in ComfyUI" | |
| 7 | **Edit** tab (ComfyUI set up): pick an img2img/inpaint/outpaint workflow, type, **Ctrl+Enter**. Result joins the stack; **Show it** steps to it; **More options…** opens Run popup prefilled; **Open** shows the workflow. A picture not made in ComfyUI has the tab too | Result not stacked; tab missing on a non-ComfyUI picture | |
| 8 | After an in-app img2img or upscale run the viewer moves to the new picture and the address bar follows; reload stays on it | Reload returns to the source | |
| 9 | *Use as input for…* (Recipe tab and right-click) closes the viewer, selects that picture, opens the Run popup. No **I2I** menu | I2I still there | |
| 10 | Metadata shows the picture's ID | Missing | |
| 11 | Rotate, then Ctrl+Z and Ctrl+Shift+Z: the picture on screen turns each time; a rotate in another window updates too | Stays rotated until reopened | |
| 12 | **Text** tab on a screenshot of text: click a word, it is outlined on the picture | Nothing highlighted | |

---

## 7. Grid, search and filters

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Search a word that appears only inside a picture's text: it is found, even with one wrong letter. The pill's **All · In text** narrows to text matches | Not found | |
| 2 | Filters menu: a menu of kinds, each opening beside it, staying open while you choose; each choice shows its count in the current view | Menu closes after one choice | |
| 3 | Active filters as chips under the toolbar, each with ×, plus Clear all | × leaves the filter on | |
| 4 | Score: 0 stars = unscored; "At most 0" shows only unscored | Shows everything | |
| 5 | Checkpoint **and** LoRA together: only pictures made with **both** | Either | |
| 6 | "No character" and "In no set" are separate; "No character" shows pictures with nobody named, including pictures in sets | Hides pictures in sets | |
| 7 | "No character" combined with a checkpoint filter narrows the grid | Widens it | |
| 8 | Rate a card while the grid is still loading: the stars stay | Snap back | |
| 9 | Selection menu and right-click: *Make more like these…*, *Run a workflow on these…*, *Edit with ComfyUI…* (opens Run popup on the Flux.2 Klein edit workflow with the selection as input) | An item missing, or Edit opens another workflow | |
| 11 | Sidebar → a set → **Suggest more pictures**: raising the match threshold or the share of the set's usual tags shrinks the list | The list does not change | |
| 10 | Checkpoint and LoRA filter menus and recipe chips use the shelf's name | Raw filename | |

---

## 8. Models shelf

### 8.1 Workflow sets

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Shelf opens on **Workflow sets**: a card per checkpoint; opening one shows VAEs, encoders and LoRAs run with it, each with a picture count | Opens on the list, or a set shows no counts | |
| 2 | "Also in N other sets" / "Only in this set" agree with what you see when you open the other sets | Counts disagree | |
| 3 | Models nothing was made with get their own card saying so | Silently missing | |
| 4 | Cards ↔ comparison list switch works | The two views show different sets | |
| 5 | **Works with** (click a file name in an open set, or right-click a model) ranks pairings by recipe count | Unranked, or a pairing with no recipes | |
| 6 | Click, Ctrl-click and Shift-click select (**release Shift before any Delete**); right-click gives rename, base model, move, thumbnail, forget, delete; the selection bar floats over the cards | Right-click menu differs from the list's | |
| 7 | Old `None`, `Base model`, `Folder`, `Feature` groupings under Group still work | A grouping missing | |
| 8 | Combinations that only ran in ComfyUI are marked "Ran in ComfyUI" and their run counts are kept apart from picture counts | Added into picture counts | |

### 8.2 Sets you make

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | **New workflow set** (button and **N**): fill Checkpoint, Text encoder, VAE, LoRA and Other; your sets and picture evidence come first | N does nothing | |
| 2 | Fill from pictures / Fill from a set fill the slots in one go | Slots left empty | |
| 3 | Every change undoes with Ctrl+Z | A change survives undo | |
| 4 | Move a model's file off the shelf: it stays in the set, marked "Not on shelf" | Removed from the set | |
| 5 | A set whose checkpoint's pictures used models it lacks says "N pictures need M more · Merge…"; the open set shows them dashed. Merge adds all, Add one, Keep separate is remembered until "Offer the merge again"; all undoable | Keep separate forgotten on reload | |

### 8.3 Names, base models, GGUF

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Drop a `.gguf` in a scanned folder: it appears, named without `Q4_K_M` and badged with it | Not catalogued | |
| 2 | A file with no base model in its metadata gets one from its filename or model-spec block, marked **guessed**; setting one yourself wins and survives a rescan | Rescan overwrites yours | |
| 3 | Group, filter and sort by base model agree: spellings of one base model are one group | Two groups for one model | |

### 8.4 Auto-tagging plugin menus

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Settings › Models › Auto-tagging: one menu per plugin kind; an icon marks loaded plugins; hover describes; the gear opens the picked plugin's settings | Radio buttons | |

---

## 9. Settings and privacy

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Settings › Privacy explains what a permanent delete leaves; keep ghosts **Off / Covered only / On** | Only two options | |
| 2 | **Library copy only; purging cannot be undone.** Purge picture ghosts and names of models not on the shelf; each shows a count, and the count drops to 0 after purging | Count stays | |
| 3 | **Library copy only; permanent.** Permanently delete a throwaway picture with ghosts **Off**: no thumbnail or prompt kept. With **On**: the workflow keeps its thumbnail and prompt | Ghost kept with Off | |
| 4 | Settings › Compute (browser: rail item **ComfyUI**) holds host and port | Host/port elsewhere or missing | |
| 5 | Settings › ComfyUI says whether the ComfyUI-PixlStash pack is installed, with a link when not; a workflow needing it names the pack and how to install | Generic error | |
| 6 | Server log: the install type is logged once, not per request | One line per thumbnail | |

---

## 10. MCP server and API tokens

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | **API Tokens → Connect AI agent** mints a read-only token and gives a config to paste. Paste into an MCP client: search, pictures, tags, recipes work | Config needs editing | |
| 2 | The config carries the **configured** port, not the desktop window's random port. On the desktop app, switch **remote access on** first (the configured port is only served then). Restart PixlStash: the agent still connects | Dead after restart with remote access on | |
| 3 | With **Require SSL** on: the agent connects over https and trusts the self-signed certificate | Certificate rejected | |
| 4 | Desktop **Shell command** setting puts `pixlstash-mcp` on PATH; it runs without a Python install of your own | Not on PATH | |
| 5 | Stop the server, start `pixlstash-mcp`: it says at start-up nothing is listening and why | "connection refused" on first question | |
| 6 | A read-only token scoped to one set sees only that set through `list_pictures`, `search_pictures`, `count_pictures` | Sees the whole library | |
| 7 | `count_pictures`, `list_sets`, `list_characters`, `list_projects` work; picture tools filter by set/character/project | A filtered count equals the unfiltered one | |
| 8 | `get_recipe` on a FaceDetailer picture says the model forks into sampler passes, the LoRAs per pass, node counts, and a link to the workflow | No fork reported | |
| 9 | Revoke the token while connected: the next call says the token was rejected and to reconnect | Bare `401` | |
| 10 | **Read and write workflows** mints a full token and adds `--allow-write`. The agent exports a graph, stores an edited one as a **new** workflow, preflights and runs it; pictures land in the library | Overwrites the original | |
| 11 | Without `--allow-write`, write tools are absent | Present | |

---

## 11. Platform and look

### 11.1 Apple Silicon

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Tagging, search and face matching run on the GPU; the built-in tagger is roughly 20× faster than on CPU | Logs say CPU | |
| 2 | Memory budget starts at half the RAM, capped at 8 GB, and shows real use | Shows 0 or VRAM of a card that is not there | |
| 3 | No "missing NVIDIA utility" message | Present | |
| 4 | JoyCaption warns about ~8 GB before you pick it | No warning | |
| 5 | A batch too big for the budget falls back to CPU instead of failing | Fails | |

### 11.2 Consistency pass (look and keyboard)

| # | Check → expected | Wrong if | ✓ |
|---|---|---|---|
| 1 | Text fields are one height and match adjacent buttons; labels above fields; paths and ports in monospace | Mixed heights, labels inside fields | |
| 2 | Segmented controls move with arrow keys; sort menus mark the choice with a check | Filled row | |
| 3 | Every menu: one row height, one hover, check for current; toolbar, lightbox and selection-bar icons larger, menu icons one size | A menu with a filled current row | |
| 4 | Tooltips open on keyboard focus, close on Escape, wait a moment on hover | Flashing across the toolbar | |
| 5 | Dialog buttons not uppercase; focus ring visible in text colour on every control; selection in olive; amber only on the main action | Invisible focus on some control | |
| 6 | Light theme: the in-progress dots and stats icon stand out | Blend into the toolbar | |
| 7 | Stats panel and workflow inspector share one panel shape; selection pill looks the same on grid, shelf and training runs | Two different pills | |
| 8 | Character thumbnails load as WebP (network tab: `image/webp`) | PNG | |
| 9 | `pixlstash-cli plugins test` on a plugin that writes a file lists that write (and any connections or programs seen) | Empty list | |

---

## 12. Already proven by automated suites

Do not spend manual time re-proving these; a green gate covers them:

- Run preflight refusals, LoRA/seed/text-node substitution, stage bypass:
  `tests/test_workflows_api.py`, `tests/test_comfyui_recipe_preflight.py`,
  `tests/test_workflow_stage_bypass.py`.
- Authz for every route on `develop` (both directions): `tests/test_auth*.py`
  and the route-policy guardrail. #1622/#1623 must add their own cases.
- Migrations 0117–0123 on fresh and populated databases:
  `tests/test_migrations.py`.
- Grid rating sync, menu parity, notice surface, read-only features:
  `frontend/e2e/specs/`.
- Vitest suites for WorkflowsView, WorkflowTab, RunDialog, Recipes tab (as
  on `develop`; #1623 updates them).

---

## 13. Only a person can judge

- **The Workflow + Recipe model reads right.** After #1623, does the Workflows
  screen make sense to someone who never saw stacks? Can they find where a
  checkpoint is chosen?
- **Conversion on a real, large hub.** How long the first start after #1623
  takes, and whether the log's per-workflow lines are readable.
- **Run popup on a slow ComfyUI** and with a long LoRA list.
- **Apple Silicon speed** in practice, and memory pressure beside other apps.
- **Windows and macOS desktop builds** for everything in sections 4–6.
- **Export privacy (4.6 #1)** on your own workflows: only a person can tell a
  leaked personal title from a harmless one.

---

## Sign-off

| Section | Tester | Date | Result |
|---|---|---|---|
| 1 Data safety | | | |
| 2 Upgrade | | | |
| 3 Workflow + Recipe (#1621–#1624) | | | |
| 4 Workflows screen | | | |
| 5 Run popup | | | |
| 6 Picture viewer | | | |
| 7 Grid and filters | | | |
| 8 Models shelf | | | |
| 9 Settings and privacy | | | |
| 10 MCP | | | |
| 11 Platform and look | | | |
