# Workflow inspector and Recipes tab: the two layers

Status: **proposed** (#1653, follow-up to #1620). Flows by the `ui-ux-expert`,
visual spec by the `lead-designer`, reconciled here; where they disagreed the
settlement is recorded in §2.10. Nothing here is built yet. The data model and
API are #1622 / #1623 and are not changed by this document: where a control
needs a field the API does not have, it is marked **[needs F-n]** and the field
is named under "Follow-up issues" (§1.8).

Surfaces: the Workflow tab (`frontend/src/components/panels/WorkflowTab.vue`,
`WorkflowDefaultRow.vue`, `WorkflowLoraPile.vue`), the Recipes tab
(`WorkflowRecipesTab.vue`), the Run dialog (`components/io/RunDialog.vue`) and
the grid's filter strip. Neither Claude Design project carries a Workflows or
Recipes screen spec, so the shipped Vue is the reference chrome: rebuild that
look and change only the feature. The wire contract is
`docs/integration_architecture.md` §2.3; the model is
`docs/backend_architecture.md`, "Workflows and recipes: the two layers".

## Contents

1. Flows and controls
   - 1.1 Workflow defaults shown against recipe values
   - 1.2 "Make this a default"
   - 1.3 Recipe cards that show only what they change
   - 1.4 Stage on/off in the Run form
   - 1.5 Filtering a workflow's pictures by checkpoint or LoRA
   - 1.6 Cross-cutting
   - 1.7 Open questions for the owner
   - 1.8 Follow-up issues
2. Visual spec (§2.0 ground rules, §2.1-2.8 per element, §2.9 light theme,
   §2.10 objections, §2.11 open visual questions)

## 1. Flows and controls

Everything here uses shipped API unless it is marked **[needs F-n]**, which points
at "Follow-up issues" at the end.

**User and job.** The owner, going back to a workflow that already has pictures,
wants to answer three questions: *what does this make by default*, *what did I
change in the recipes I kept*, and *show me the pictures made with X*. Success
means answering each from the rail without opening ComfyUI. The costly failure
is a screen that says "every picture" when that is not true, or a verb the owner
thinks moved or re-filed pictures when it only changed what the next run starts
from.

**The one rule that settles most of the decisions below:** the Workflow tab
describes the **default recipe** (what Run starts from). The Recipes tab
describes **only differences from it**. Pictures are evidence, and they appear as
counts and "Show N" links, never as structure.

---

### 1.1 Workflow defaults shown against recipe values

#### Decision

- Rename **IN EVERY PICTURE** to **DEFAULT RECIPE**. The old label is false now:
  a default LoRA only has to be in >50% of 4★+ pictures. The section is built
  from `default_recipe` (models, LoRAs, stages), not from `lora-summary.shared`.
- The pile stays as it is and gets renamed **ALSO USED**. It lists only the
  LoRAs that are **not** in the default recipe: `lora-summary.varying` minus the
  filenames in `default_recipe.loras`, worked out on the client. A LoRA appears
  once on the tab, either as a default row or in the pile, never both.
- A default LoRA that is not in every picture says so on its own row: "in 31 of
  40" (from `lora-summary` shared/varying `pictures`). A LoRA that is in every
  picture gets no count. Having no count means "all of them".
- Merge **DEFAULTS** (parameters) into the same idea under the label
  **PARAMETERS**. Pins and "All N parameters" keep working as today.
- Section order, top to bottom: head, **DEFAULT RECIPE** (Checkpoint, other
  models, LoRAs, Stages), **ALSO USED** (pile, shown only if non-empty),
  **PARAMETERS**, Notes, Node names, footer. Models before parameters because
  that is the order the owner reasons in ("which model, which LoRAs, then
  numbers"), and because the missing-checkpoint fix is the most urgent thing on
  the tab.
- **Provenance** sits under the section label as one note, and on a row only
  when that row is an exception:
  - Section note: "Most used in your 4★+ pictures." If `sampled` says there are
    no 4★+ pictures, it reads "Most used in all 40 pictures, none rated 4★ yet."
  - A row whose provenance is `all` while its siblings are `best`: no mark. The
    owner cannot act on that difference, so it would only be noise.
  - A row whose provenance is `edited`: the text "Yours" plus a reset button
    ("Back to the most used: <value>"). This is the only per-row provenance
    mark. It is the one the owner can act on, and the one that explains why the
    value disagrees with the counts.

```
 ┌ 288px ───────────────────────────────┐
 │ Portrait SDXL                        │
 │ 40 pictures                          │ <- link, shows all
 │                                      │
 │ DEFAULT RECIPE         Edit LoRAs…   │
 │ Most used in your 4★+ pictures.      │
 │ Checkpoint  juggernautXL_v9     Show 34│
 │             +2 others ▸              │ <- disclosure, §1.5
 │ VAE         sdxl_vae                 │
 │ LoRA        detail_tweaker 0.6       │
 │ LoRA        film_grain 0.4  in 31 of 40│
 │ LoRA        anya_v2 0.8  Yours  ↺    │
 │ Stages      Upscale · Face detailer off│
 │                                      │
 │ ALSO USED                            │
 │ LoRA  [ lighting_v3        +4 ]      │ <- pile, fans over the grid
 │ In some pictures, not in the default.│
 │                                      │
 │ PARAMETERS          📌 = shown in Run│
 │ Steps       30              📌       │
 │ CFG         6.5   Yours ↺   📌       │
 │ ▸ All 14 parameters                  │
 │ ▸ Notes   ▸ Node names               │
 │──────────────────────────────────────│
 │ [ Run… ]  ⧉  ⋯                       │
 └──────────────────────────────────────┘
```

#### Flow

1. The owner selects one workflow card. The inspector shows the Workflow tab
   only if it is already open. It never opens by itself; a closed rail gets the
   standard edge-tab nudge.
2. The head and DEFAULT RECIPE render from the `GET /workflows/{id}` detail. The
   pile renders from `lora-summary` when it arrives. If a default LoRA's filename
   is in `varying`, that LoRA goes into its default row with an "in N of M"
   count and leaves the pile.
3. The owner reads down the tab. The "in N of M" counts and "Yours" answer "why
   is this the default".
4. The owner opens the pile. The fan lists ALSO USED LoRAs with their strips and
   "Show N". The verbs added in §1.2 live on these rows.

#### Controls

| Control | Where | What it does | Keyboard / a11y |
|---|---|---|---|
| Section label DEFAULT RECIPE | tab | Names the group | `h3`-level heading, or `role=heading aria-level=3`, so SR users can jump by heading |
| Provenance note | under label | States where the values come from | Plain text, read in order |
| "in N of M" | default LoRA row | States coverage | Part of the row's text, not a tooltip, so SR users hear it |
| "Yours" + ↺ | edited row | ↺ resets to the computed value. No confirm, 5 s Undo toast | ↺ is a Button with `aria-label="Reset <field> to the most used, <value>"`; focus stays on the row after reset |
| Stages row | DEFAULT RECIPE | Read-only summary (see §1.4) | Text |
| Pile | ALSO USED | Opens the fan (unchanged) | Existing: button with `aria-expanded`; fan is a `role=dialog`; Esc closes and returns focus to the pile |

#### States

- **Loading detail:** keep the head. The DEFAULT RECIPE body reads "Reading its
  default recipe…". Do not render the card's `models` as if they were the
  default: the list card has `default_recipe: null`.
- **Detail failed:** "Could not read its default recipe just now." plus a Retry
  button. Run… stays enabled, because the server builds the default itself.
- **lora-summary loading or failed:** the default LoRA rows render without "in N
  of M" and the pile section is absent. On failure the existing note "Could not
  read which LoRAs change between pictures just now." sits under ALSO USED.
  Never show a count of 0.
- **No 4★+ pictures:** the note switches to the "all pictures" wording above.
- **No LoRAs in the default:** no LoRA rows. "Edit LoRAs…" stays (#1478).
- **Nothing varies:** no ALSO USED section at all, not an empty one.

#### Wrong looks like

- The label still reads "In every picture", or a default LoRA row has no count
  while the pile shows it in fewer than all pictures.
- One LoRA appears both as a default row and in the pile.
- "Yours" shows on a row that nobody edited, or ↺ shows on a `best`/`all` row.
- The tab shows "0 of 40" or "in 40 of 40". A LoRA in every picture carries no
  count.
- The inspector opens by itself when a workflow is selected.

---

### 1.2 "Make this a default"

#### Decision

- **One verb, "Make default"**, placed where the owner meets the value:
  1. **Pile fan row** (ALSO USED): "Make default" beside "Show N". This covers a
     LoRA **[needs F-1]**.
  2. **Checkpoint "+2 others" disclosure** (§1.5): "Make default" per other
     checkpoint **[needs F-1]**.
  3. **Recipe card ⋯ menu:** "Make these the defaults…". Takes the card's whole
     diff (§1.3), except the prompt, into the default recipe. It is the only
     multi-value path, so it is the only one that asks for confirmation.
- Its inverse lives on the default rows: a default LoRA row's overflow gets
  "Take out of default". An edited row's ↺ already covers "go back to computed".
- **Single-value verbs do not confirm.** They are reversible, so they get an
  Undo toast instead: "detail_tweaker is now in the default recipe. Your
  pictures stay where they are. [Undo]". The toast stays 8 s and is
  `role=status`. Undo restores the previous default, i.e. a
  PUT back of the prior value, or a reset if the prior value was computed.
- **The recipe-card version confirms** with a small dialog listing each change
  as a checkbox row, all ticked:
  "Checkpoint → realvisXL", "Add anya_v2 0.8", "Take out film_grain",
  "Steps 30 → 40". The primary button (amber) is "Make N defaults". The body
  line reads "Runs from this workflow start from these. Your pictures and saved
  recipes are not changed." Cancel and Esc do nothing.
- **What it says about pictures:** nothing moves. The copy says so once in the
  toast or dialog and nowhere else. The pile's count for that LoRA stays the
  same; the LoRA just changes section (pile to default row, with "Yours").
- **Consequence the owner must see:** saved recipes are diffed live against the
  default (§1.3), so after "Make default" another recipe's card can gain a line
  such as "without detail_tweaker". That is the truth: running it will not load
  that LoRA. Do not paper over it (see Open question 2).
- **API gap:** `PUT /workflows/{id}/defaults` refuses models and LoRAs (422).
  The **parameter** part ships now. The recipe-card dialog lists parameter rows
  only, until F-1 lands, and shows no disabled model or LoRA rows. Pile-row and
  checkpoint "Make default" **are not rendered** until F-1. Do not ship dead
  controls.

#### Flow (pile row, after F-1)

1. Open the pile and focus moves into the fan. Tab to the LoRA's "Make default".
2. Activate it. A PUT is sent; the button shows pending, not dimmed.
3. On success the fan stays open, the row leaves the fan, and focus moves to the
   next row's "Make default", or to the fan heading if it was the last row. The
   DEFAULT RECIPE gains the row marked "Yours". The toast appears.
4. Undo in the toast brings the row back into the pile. If the fan was closed by
   then, focus stays where it is.
5. On failure the row stays and gets an inline message, "Could not change the
   default: <reason>", announced with `role=alert`.

#### Flow (recipe card)

1. On a card, ⋯ → "Make these the defaults…".
2. The dialog lists the changes. The owner can untick any. With none ticked, the
   primary button is disabled with the reason "Tick at least one".
3. Confirm sends one PUT per kind, or one combined PUT after F-1. The dialog
   closes, focus returns to the card's ⋯, the toast offers Undo for the whole
   set, and that card's diff line now reads "Only the prompt" if everything was
   taken.

#### Controls

| Control | Where | What it does | Keyboard / a11y |
|---|---|---|---|
| Make default | pile fan row, checkpoint disclosure row | Adds that value to the default recipe; marks it "Yours" | Button; `aria-label="Make <name> part of the default recipe"`; pending uses `aria-busy`, never dims |
| Take out of default | default LoRA row ⋯ | Removes the LoRA from the default recipe [F-1] | Menu item; the toast offers Undo |
| Make these the defaults… | recipe card ⋯ | Opens the confirm dialog | Menu item; the dialog traps focus, first checkbox focused, Esc cancels, focus returns to ⋯ |
| Change checkboxes | dialog | Choose which differences to take | DS Checkbox; label is the full sentence ("Add anya_v2 at 0.8") |
| Undo | toast | Reverts the last default change | Reachable by Tab from the page (toast region), `role=status` live region; toast pauses while focused |

#### States

- **Pending:** the verb shows a spinner and keeps its label. Other rows stay
  usable.
- **Failed:** inline `role=alert` message on the row, and nothing moves.
- **Stale:** if the default changed underneath (another tab), the PUT's answer
  wins and the rail re-reads the detail.
- **Multi-selection of workflows:** no "Make default". The verbs belong to one
  workflow.

#### Wrong looks like

- Any picture's workflow, stack or listing changes after "Make default". The
  count on the workflow card must be identical before and after.
- A "Make default" is visible on a LoRA or checkpoint before F-1 ships, or
  clicking it shows a 422.
- The row stays in the pile as well as appearing in DEFAULT RECIPE.
- The single-value verb opens a confirm dialog, or the multi-value one does not.
- Undo leaves the value marked "Yours" when it was computed before.
- Focus drops to `<body>` after the dialog closes or after the pile row leaves.

---

### 1.3 Recipe cards that show only what they change

#### Decision

- **Card content order:** thumb, name, ⋯ on one line; then the **diff line**;
  then the prompt clamped to 2 lines; then Run….
  The diff comes before the prompt because it is the thing that differs *by
  kind*. The prompt differs on every card, so it identifies the recipe, but the
  diff is what tells cards apart.
- **Diff line**, computed on the client against `default_recipe`, in a fixed
  order separated by " · ":
  1. models: "realvisXL" (checkpoint, filename without folders and extension;
     other kinds prefixed, e.g. "VAE: xl_vae")
  2. added LoRAs: "+ anya_v2 0.8"
  3. strength-only changes: "detail_tweaker 0.6 → 0.9"
  4. removed default LoRAs: "without film_grain". The word carries the meaning,
     not a strike-through, so it reads the same to a screen reader and in
     monochrome.
  5. parameters: "Steps 40", "CFG 7"
  6. stages: "no upscale"
  7. seed: "seed 1234" only when `keep_seed`
- The line shows at most 2 lines, then "+N more" as a button that expands the
  card in place ("Fewer" collapses it). No tooltip-only content.
- **Changes nothing but the prompt:** the line reads "Only the prompt", in the
  quiet style. Never leave it empty. An empty line is indistinguishable from
  "still loading".
- **Negative prompt differs:** list "own negative prompt" in position 5.
- **Card chips are gone.** The one-chip-per-LoRA row and the facts line are
  replaced by the diff line. LoRA names go through the shelf name resolver, not
  the raw filename (`modelDisplayName` is `title || name` today, so chips print raw filenames the shelf would shorten).
- **FROM YOUR PICTURES looks:** same card shape. `/recipes/used` gives only
  prompt and LoRA filenames, so the diff line shows the **LoRA part only**
  (added and without), followed by "N pictures". It never says "Only the
  prompt", because models and values are unknown. When the LoRAs match the
  default it reads "Default LoRAs · 12 pictures". Full diff **[needs F-2]**.

```
 ┌──────────────────────────────────────┐
 │ ▣  Anya in rain                  ⋯   │
 │    realvisXL · + anya_v2 0.8 ·       │
 │    without film_grain · Steps 40     │
 │    +2 more                           │
 │    "anya, standing in the rain, neon │
 │    reflections, 35mm…"               │
 │                              [Run…]  │
 └──────────────────────────────────────┘
 ┌──────────────────────────────────────┐
 │ ▣  Plain portrait                ⋯   │
 │    Only the prompt                   │
 │    "portrait of an old fisherman…"   │
 │                              [Run…]  │
 └──────────────────────────────────────┘
```

#### Flow

1. Open the Recipes tab. The cards render once both the saved recipes and the
   workflow detail (`default_recipe`) have arrived. The diff needs both.
2. The owner scans the diff lines to find "the one with the other checkpoint".
3. "+N more" expands in place and focus stays on the button (now "Fewer").
4. Run… opens the Run dialog prefilled with the recipe, as today.

#### Controls

| Control | Where | What it does | Keyboard / a11y |
|---|---|---|---|
| Diff line | card | States the differences | Text; the card's accessible name is "<name>, <diff line text>" so SR users hear the difference first |
| +N more / Fewer | end of diff line | Expands/collapses the full diff | Button, `aria-expanded`, focus stays |
| ⋯ | card head | Rename / Make these the defaults… / Export… / Delete | Existing menu; new item per §1.2 |
| Run… | card foot | Opens Run with the recipe | Unchanged: the shipped secondary button, never amber (amber is the footer Run… only) |

#### States

- **Detail not yet read:** cards render name, thumb, prompt, and the diff line
  reads "Comparing with the default…". Do not show "Only the prompt" before the
  comparison exists.
- **Detail failed:** the diff line reads "Could not compare with the default."
  Run… still works.
- **Recipe from before a default changed:** the diff is live, so it may now show
  "without X". That is correct.
- **No saved recipes:** the existing empty text, plus one line pointing at FROM
  YOUR PICTURES ("Clone a look below to keep it").

#### Wrong looks like

- A card still lists every LoRA as a chip, or repeats the default checkpoint.
- A prompt-only recipe shows an empty diff line or "Default".
- A removed LoRA is shown only by strike-through, or is missing.
- A look from pictures says "Only the prompt" (the data cannot support that).
- Raw filenames with folders or `.safetensors` appear in the diff.
- The diff order changes between cards, e.g. LoRAs before the checkpoint on one
  card.

---

### 1.4 Stage on/off in the Run form

#### Decision

- **Keep the checkbox rows.** A checkbox is the right control for a boolean
  applied at submit time. Change the label to the stage's name alone,
  "Upscale" / "Face detailer", grouped under one "Stages" legend (`fieldset`).
  Repeating "Run the … stage" on every row makes four words to read for one
  word of information.
- **Default state:** the default recipe's value. When a recipe is loaded, the
  recipe's value. A row that differs from the default gets the same
  `RunResetChip` the Checkpoint row uses ("Default: on"), so stages follow the
  pattern already in the form.
- **A default-off stage is hidden, as shipped.** Showing it disabled would offer
  a thing the owner cannot get from here. The Workflow tab's Stages row is where
  "off" is visible (§1.1).
  Hidden rather than disabled because the fix is in ComfyUI, not in this dialog.
- **Workflow tab Stages row: read-only for now.** It reads "Upscale · Face
  detailer off". An off stage gets a quiet reason on focus/expand: "Off in the
  graph. Turn it on in ComfyUI." Editing the default's stage on/off needs a
  write the API does not have **[needs F-3]**. When F-3 lands, the row becomes
  DS Switches that only allow on → off (skip by default), since the server
  cannot turn a graph-off stage on.

#### Flow

1. Run… opens the dialog. The Stages fieldset shows one checkbox per stage that
   the base graph has and the default recipe runs, ticked per the default or
   the recipe.
2. The owner unticks Upscale. "Default: on" appears. Space toggles.
3. Submit sends `skip_stages: ["upscale"]`.
4. If the server answers `stage_not_skippable`, the row gets an inline error,
   "This workflow cannot run without its upscale stage.", the box re-ticks, and
   focus moves to it.

#### Controls

| Control | Where | What it does | Keyboard / a11y |
|---|---|---|---|
| Stages fieldset | Run dialog | Groups the rows | `<fieldset><legend>Stages</legend>`; omitted entirely when no rows |
| Stage checkbox | Run dialog | Unticked → `skip_stages` | Native/DS Checkbox, label "Upscale"; Space toggles |
| Reset chip | row | Back to the default value | Existing RunResetChip semantics, `aria-label="Reset upscale to the default, on"` |
| Stages row | Workflow tab | Read-only summary | Text; the off reason is visible text, not a tooltip |

#### States

- **No optional stages:** no fieldset, no empty legend.
- **Detail not read:** no stage rows. The server's default applies.
- **Refused:** inline error per the flow.

#### Wrong looks like

- A stage row appears for a stage that is off in the default recipe.
- The box starts unticked for a stage the default runs (with no recipe loaded).
- Loading a recipe with "no upscale" leaves the box ticked.
- `skip_stages` is sent for a ticked row, or an empty `skip_stages: []` is
  sent when nothing is off (harmless but noisy; worth a test).
- The Workflow tab offers a toggle for stages before F-3.

---

### 1.5 Filtering a workflow's pictures by checkpoint or LoRA

#### Decision

- **The entry point is the inspector rows**, not a new grid filter. The owner
  is already reading "which checkpoint, how many" there, and the grid's filter
  menu has no idea which workflow's values are relevant.
  - A DEFAULT RECIPE model or LoRA row gets "Show N" (ghost, small), N from
    `recipe_values`, **only when N is below the workflow's picture count**. A
    value in every picture would only repeat the head's "N pictures" link, and
    on a workflow with three universal LoRAs that is a column of identical
    "Show 40" buttons (the lead designer's objection, accepted).
  - The Checkpoint row gets "+K others ▸": a disclosure (not a menu) listing the
    other `recipe_values.checkpoints`, each with its count, "Show N", and later
    "Make default".
  - Pile fan rows keep their existing "Show N" (`workflow_lora`).
- **Where it lands:** All Pictures, through the existing `showFiltered` path.
  The filter strip shows **two removable chips**: "Workflow: Portrait SDXL"
  and "Checkpoint: realvisXL" (or "LoRA: anya_v2"). Today the pile makes one
  combined chip ("anya_v2 in Portrait SDXL"). Split it, so "back to the whole
  workflow" is one × on the second chip.
  Checkpoint uses `workflow` + `comfyui_model`; LoRA-from-row uses
  `workflow` + `comfyui_lora`; the pile keeps `workflow_lora=asset:<sha>`.
- **What the reader sees:** the grid, count matching N (see Wrong below), the
  chips, and the inspector (if open) switching to the Picture tab as it does for
  any grid view.
- **How they get back:** × on the value chip leaves the workflow's pictures. × on
  the Workflow chip, or "Clear all", gives the whole library. Browser Back
  returns to the Workflows view with the same workflow still selected and the
  tab scroll kept, where the router already does so. No new "back" button.
- **Replaces stack browsing:** no stack-member list anywhere. A LoRA/checkpoint
  row plus Show N is the whole way to reach a subset of a workflow's pictures.

#### Flow

1. Workflow tab, DEFAULT RECIPE, Checkpoint row: "+2 others ▸". Enter opens it
   and focus stays on the toggle.
2. Tab to "Show 6" on realvisXL and activate.
3. The grid route loads with two chips and 6 pictures. Focus goes to the filter
   strip's first chip. The live region announces "6 pictures, Portrait SDXL,
   checkpoint realvisXL".
4. × on "Checkpoint: realvisXL" brings back 40 pictures. Back returns to the
   Workflows view.

#### Controls

| Control | Where | What it does | Keyboard / a11y |
|---|---|---|---|
| Show N | default model/LoRA rows, other-checkpoint rows, fan rows | Opens the grid filtered to workflow + that value | Button; `aria-label="Show the N pictures in <workflow> made with <value>"` (matches the existing pile label) |
| +K others ▸ | Checkpoint row | Discloses the other checkpoints used | `<details>`/button with `aria-expanded`; list is a plain `ul` |
| Value chip × | grid filter strip | Drops the value, keeps the workflow | Existing chip removal; after removal, focus goes to the next chip |

#### States

- **recipe_values missing** (list card not loaded yet): rows render without
  Show N. The button must not appear with N blank.
- **N = 0** (value only in the default by edit, e.g. "Yours" with no pictures):
  no Show button. The row text says "not in any picture yet".
- **Only one checkpoint:** no "+0 others".
- **Grid empty after filtering** (a picture was deleted since the count was
  read): the grid's empty state, with both chips visible so the cause is clear.

#### Wrong looks like

- "Show 6" lands on a grid of anything other than 6. The count difference is a
  bug: `recipe_values` and the listing disagree. **Test this one first.**
- Landing shows one combined chip that cannot be narrowed back to the workflow.
- Landing keeps a previous tag filter or sidebar character (the path must clear
  the view as `showFiltered` does).
- A Show button on a "Yours" value with no pictures, or "Show 0".
- Any stack/"members" UI appears.

---

### 1.6 Cross-cutting

- **Amber** only on the footer Run… and on the confirm dialog's primary button.
  Show N, Make default, and ↺ are ghost buttons; the recipe card's Run… stays the shipped secondary button.
- **Multi-selection** of workflows keeps today's "N workflows selected" block.
  Merge/split stay on the bottom pill. None of the new verbs appear there.
- **Focus return:** every dialog/fan returns focus to its opener. Rows that
  disappear after an action hand focus to the next row (see §1.2).
- **Reduced motion:** the pile's fan and the row moving between sections must
  not animate position under `prefers-reduced-motion`.

### 1.7 Open questions for the owner

1. **Section name: "DEFAULT RECIPE" or keep "IN EVERY PICTURE" with counts?**
   Recommend DEFAULT RECIPE. The old name is now false for any LoRA under 100%.
2. **When a default changes, should saved recipes follow it?** Today a saved
   recipe stores its full LoRA list, so making X a default makes old recipes
   read "without X" (and run without it). Alternative: store recipes as
   diffs, so they inherit. Recommend keeping stored lists and showing the
   honest "without X" for v1.12. Changing recipe storage is a model change, not
   a UI one.
3. **"Take out of default" on a computed LoRA:** allowed (becomes "Yours:
   removed") or only reset of edits? Recommend allowed. Otherwise a LoRA in 51%
   of pictures can never leave the default.
4. **Stage default editing (F-3): worth doing in v1.12?** Recommend no. The Run
   form covers per-run skipping, and the read-only row is enough.

### 1.8 Follow-up issues

- **F-1: write the default recipe's models and LoRAs.** Extend
  `PUT /workflows/{id}/defaults` (or add `PUT /workflows/{id}/default-recipe`)
  with `models: [{address, filename}]` and
  `loras: [{filename, sha256, strength} | {filename, remove: true}]`, each
  answered with `provenance: edited` and a reset (`null`) to go back to
  computed. It unblocks "Make default" on the pile and checkpoint rows, "Take
  out of default", and the model/LoRA rows of the recipe-card dialog.
- **F-2: `/recipes/used` looks need the rest of the recipe.** Add `models:
  [{address, filename}]`, `loras[].strength`, and `values: {address: value}`
  (the most used among that look's pictures). A server-computed
  `differs: [...]` against the default would also do. It unblocks the full diff
  line and "Only the prompt" on FROM YOUR PICTURES.
- **F-3: default-recipe stage write.** `stages: {upscale: false}` on the same
  PUT, allowing on → off only (refuse turning on a graph-off stage with a named
  reason). It unblocks Switches on the Workflow tab's Stages row.
- **F-4 (frontend-only, no API): split the pile's combined filter chip** into
  Workflow + LoRA chips (§1.5). It is listed so it is not lost if §1.5 ships in
  parts.

## 2. Visual spec

Reference chrome is the shipped Vue (`WorkflowTab.vue`, `WorkflowDefaultRow.vue`,
`WorkflowLoraPile.vue`, `WorkflowRecipesTab.vue`, `RunDialog.vue`,
`RunResetChip.vue`, `FilterStrip.vue`). Rebuild that look; change only the feature.

**New values: none.** Every value below is an existing token or an existing class.

### 2.0 Ground rules for every element here

- Width budget. Rail is `--stats-panel-w` (288). `.inspector-body` pads
  `--space-3` inline, so content is about 270px, about 260 with a classic
  scrollbar. `.wftab-field` gives the 96px label column plus a `--space-3` gap,
  which leaves **about 156 to 166px for the value column**. Everything in §2.1 is
  sized against that figure.
- Hue budget. Amber (`accent`): footer Run… and the confirm dialog's primary
  only. Olive (`primary` / `selected-ink`): the checkbox tick in §2.5 and §2.7,
  nothing else. No raspberry or teal. Status hues: the existing missing-model
  `surface-warning` flag and inline failure text in `surface-error`.
- Hover is `--hover-wash`. Focus is the global ink ring (`--focus-width`
  `--focus-stroke` `--focus-offset`). Pending means the AppButton spinner with no dimming.
- A secondary line is `rgba(var(--v-theme-on-surface), var(--opacity-text-secondary))`,
  which is the existing `.wftab-quiet` / `.wfrt-quiet`. Never a raw `0.6`.

---

### 2.1 DEFAULT RECIPE section

#### Anatomy (one model or LoRA row)

```
[label col 96px ]  [value col, ~160px                              ]
 LoRA               ┌ detail_tweaker ……………… 0.6  ↺ ┐   <- .wftab-value, 28px
 Yours              └────────────────────────────┘
                    in 31 of 40               Show 31    <- meta line, 24px, only if it has content
```

- **Label column**: `.wftab-label` ("LoRA", "Checkpoint", "VAE", "Stages").
  When the row is edited, "Yours" sits **under the label, in the label column**.
  That is the same place `WorkflowDefaultRow` already puts its provenance
  subline, so §2.1 and §2.3 share one position.
- **Value field**: unchanged `.wftab-value` + `.wftab-lora-value`: name
  (`.wftab-chain-name`, flex 1, end ellipsis), strength (`.wftab-chain-strength`,
  tabular-nums, secondary), then ↺ when the row is "Yours". ↺ is the same control
  as `.wfdef-reset`: `AppButton size="sm" variant="ghost" icon-only
  icon-left="restore"`, square `--control-h-sm`. It goes **inside** the field
  at the inline end, so the field keeps its 28px height.
- **Meta line** (new, in the value column under the field): a flex row,
  `justify-content: space-between`, `min-height: var(--control-h-sm)`,
  `gap: var(--space-2)`, `margin-top: var(--space-1)`.
  - Left: the coverage text, `--text-xs`, `.wftab-quiet`,
    `font-variant-numeric: tabular-nums`. It reads "in 31 of 40", or "not in any
    picture yet" when N = 0.
  - Right: "Show N", `AppButton size="sm" variant="ghost"`, the same control as
    the pile fan's Show.
  - The line is rendered only when either side has content. A row with neither
    (recipe_values not loaded, the LoRA is in every picture, N unknown) gets no
    empty 24px strip.
  - **Why a second line.** "Show 34" as a ghost sm button is about 60px. Put
    inline, it leaves the name about 70px ("detail_t… 0.6"). Filenames are how
    the owner identifies a row, so the name gets the full field.
- Rows stack at the tab's existing rhythm: the `.inspector-section` gap stays
  as shipped. Nothing is added between rows beyond the meta line itself.

#### "Yours"

- Plain text, not a chip or a badge. Badges carry counts (visual-language §12), and a pill
  would look like one more removable filter chip.
- `--text-2xs`, `--weight-semibold`, `rgb(var(--v-theme-on-surface))` at full
  ink. It is the one exception mark on the tab, so it sits one step above its
  secondary label through weight and ink, not hue. Sentence case, no tracking,
  so it cannot be mistaken for a section label.

#### Section head and note

- `.wftab-sec-head`: `.section-label` "Default recipe" (CSS uppercases it) plus
  the existing ghost sm "Edit LoRAs…", unchanged.
- Provenance note under the head: `.wftab-note.wftab-quiet` ("Most used in your
  4★+ pictures." / the all-pictures wording). Loading, failed and Retry reuse the
  existing note lines. Retry is a ghost sm AppButton after the note.

#### Checkpoint "+K others ▸"

- It sits in the Checkpoint row's value column, below the meta line. The toggle
  is a bare text button styled like `.wftab-disclose > summary`, but at
  `--text-xs` and `.wftab-quiet`, with `mdi-chevron-right` at 16px in front
  (turning to `mdi-chevron-down` when open, no rotation animation under reduced
  motion). Pointer target: `min-height: var(--control-h-sm)`.
- The list is a plain `ul`, no bullets, no inline padding, so it aligns with the
  value field's left edge. Each `li` is one row, flex,
  `min-height: var(--control-h-sm)`, `gap: var(--space-2)`: the name
  (`--text-sm`, flex 1, end ellipsis) then "Show N" (ghost sm). The count lives
  in "Show N" and nowhere else.
- **After F-1**: "Make default" does not fit beside Show N in about 160px. The
  row becomes two lines: the name on line 1, then "Make default" and "Show N"
  right-aligned on line 2, both ghost sm, `gap: var(--space-2)`. Rows are
  separated by `--space-2`.

#### Stages row

- `.wftab-field`, label "Stages", value `.wftab-value` with the read-only text
  "Upscale · Face detailer off". Name and "off" are ink. The " · " separator is
  secondary. No strike-through, no hue, no switch before F-3.
- The off reason ("Off in the graph. Turn it on in ComfyUI.") goes under the
  field as `.wftab-note.wftab-quiet`, as visible text.

#### Long filename at 288px

- The display name comes from the shelf resolver (short name), with end ellipsis
  in `.wftab-chain-name`. Strength, ↺ and the ⋯ below never shrink
  (`flex-shrink: 0`).
- Tooltip: `Tooltip` component (keyboard- and touch-reachable) on the name,
  carrying the **full recorded file** (folders + extension), as the missing-model
  row already does. Never a `title=` attribute, because this is information not on screen.
- Missing-model states stay exactly as shipped (`.wftab-missing`, `.wftab-warn`).

#### After F-1: "Take out of default"

An icon-only ghost sm `mdi-dots-horizontal` inside the value field, after the
strength and before ↺. It opens the standard `.ctx-menu`.

| Part | Token / class |
|---|---|
| Row grid | `.wftab-field` (96px local column, `--space-3` gap) |
| Value field | `.wftab-value` (`--control-h`, `--radius-sm`, `divider` border, on-surface 0.06 fill, `--text-sm`) |
| "Yours" | `--text-2xs`, `--weight-semibold`, `on-surface` full ink |
| ↺ | AppButton ghost sm icon-only, `--control-h-sm` square |
| Meta line | `min-height: --control-h-sm`, `margin-top: --space-1`, `gap: --space-2` |
| Coverage text | `--text-xs`, `--opacity-text-secondary`, tabular-nums |
| Show N / Make default | AppButton ghost sm (label `on-surface` 0.7, hover `--hover-wash`) |
| Disclosure toggle | `--text-xs`, secondary, 16px chevron, `min-height: --control-h-sm` |
| Section note | `.wftab-note.wftab-quiet` |

**Wrong looks like**
- "Yours" in olive, amber or any hue, or drawn as a pill or chip.
- "Show N" filled, amber or olive, or taller than 24px.
- The value field grows past 28px because ↺ or Show N wrapped inside it.
- An empty 24px gap under a row that has no count and no Show.
- A filename cut to "detail_t…" while Show N sits beside it on the same line.
- The tooltip on a truncated name only repeats the truncated text (it must give the full file).
- "off" in the Stages row drawn in red, struck through, or as a switch.
- Every row still carries a provenance subline ("from your best pictures").

---

### 2.2 ALSO USED pile

- Only the section label changes: `.section-label` "Also used". The
  `.wftab-legend` count, the pile and the note stay as shipped.
- The fan row (`.wfpile-row`) gets **"Make default" (F-1) immediately before "Show
  N"**. Both are ghost sm, `gap: var(--space-3)` (the row's existing gap). Show N
  keeps the trailing slot so it stays in one column across rows, including the
  "No LoRA" row, which has no Make default.
- Width check: fan `min(440px, …)` minus `--space-5` × 2 padding, a 100px strip,
  two `--space-3` gaps and about 155px of buttons leaves about 130px for
  `.wfpile-lname`. That is enough. The name ellipsises before either button shrinks.
- Inline failure under the row: `--text-xs`, `rgb(var(--v-theme-surface-error))`,
  `mdi-alert-circle-outline` 16px, `gap: --space-2`, `margin-top: --space-2`.

| Part | Token / class |
|---|---|
| Label | `.section-label` |
| Make default / Show N | AppButton ghost sm, `--space-3` apart |
| Row failure | `--text-xs`, `surface-error` |

**Wrong looks like**
- The label still reads IN EVERY PICTURE or CHANGES PER PICTURE.
- "Make default" is amber or filled, sits after Show N, or pushes Show N out of its column.
- The row dims while the PUT is pending.

---

### 2.3 PARAMETERS

- `.section-label` "Parameters" with the existing pin legend (`.wftab-legend`).
- In `WorkflowDefaultRow`, `.wfdef-prov` stops printing on `best` / `all` rows.
  On an `edited` row it prints "Yours" with the §2.1 styling: `--text-2xs`,
  `--weight-semibold`, full ink, in place of today's secondary 2xs. The position
  under the name stays. ↺ stays `.wfdef-reset` inside `.wfdef-value`, as shipped.

| Part | Token / class |
|---|---|
| "Yours" | `.wfdef-prov` restyled: `--text-2xs`, `--weight-semibold`, `on-surface` |
| ↺ | `.wfdef-reset` (unchanged) |

**Wrong looks like**
- "Yours" styled differently here and in DEFAULT RECIPE (different weight, ink or position).
- "from your best pictures" still under unedited parameter names.

---

### 2.4 Recipe card (Saved and From your pictures)

#### Order inside `.wfrt-card`

1. `.wfrt-top`: handle, thumb (`--space-8` square), name, ⋯. Unchanged.
2. A new body block, `display: flex; flex-direction: column; gap: var(--space-2)`,
   holding the diff line, "+N more" / "Fewer", then the prompt. These are
   related, so they get the in-group step and not the card's `--space-3`.
3. `.wfrt-bot`: Run… right-aligned. On looks, "N pictures" moves into the diff
   line and Clone… keeps its place.

The card keeps `padding: --space-3`, `gap: --space-3`, `--radius-md`, 1px
`border`, `surface` fill and no shadow. The list keeps `gap: --space-3`.

#### Diff line

- `--text-xs`, `--leading-body`, `--weight-regular`, **full `on-surface` ink**.
  It is what tells cards apart, so it reads above the prompt, which stays
  secondary.
- Segments are inline, in the fixed order from §1.3. Separator " · " and the
  arrow "→" in `.wfrt-quiet` (secondary) so the names carry the line. Numbers
  (strengths, 30 → 40, seed) are `tabular-nums` in the UI face, not mono.
  `.wfrt-strength`'s mono does not apply inside a sentence.
- "+ anya_v2 0.8": the "+" is the same ink, followed by a space. "without
  film_grain": all ink, the word does the work. "detail_tweaker 0.6 → 0.9": old
  and new both ink, arrow secondary.
- **No emphasis.** No weight change, no colour, no chip, no strike-through. Hue
  here would claim status or selection that the line does not have.
- Clamp: `display: -webkit-box; -webkit-line-clamp: 2; overflow: hidden`, the
  same mechanism as `.wfrt-prompt`. Expanded, the clamp is removed.
- **"+N more" / "Fewer"**: an inline text button on its own line under the
  clamped diff. `--text-xs`, `--weight-medium`, ink, underline with
  `text-underline-offset: 2px`, which is the `.wftab-link` treatment. Hover
  thickens the underline (as `.wftab-link:hover`). Pointer target grown to 24px
  with the `RunResetChip` `::after` inset trick, not with padding.
- **"Only the prompt"**, "Comparing with the default…" and "Could not compare
  with the default." all use the same line with `.wfrt-quiet`. The failure is not
  `.wfrt-bad`, because it is a read that did not happen and not an action that failed.
- Looks (From your pictures): "Default LoRAs · 12 pictures" and "+ x · without
  y · 12 pictures" follow the same rules. The count is the last segment, secondary.

#### Prompt

`.wfrt-prompt` unchanged: `--text-xs`, secondary, 2-line clamp.

#### Run…

Keep the shipped `AppButton size="sm" icon-left="play"`, which is the
**secondary** (neutral `cancel-button` fill) variant. Never `primary`: amber
belongs to the footer Run… only. See Objection 2 on "ghost".

#### Removed

`.wfrt-chips` / `.wfrt-chip` and `.wfrt-facts` go from the card.

| Part | Token / class |
|---|---|
| Card | `.wfrt-card` (unchanged) |
| Body block gap | `--space-2` |
| Diff text | `--text-xs`, `--leading-body`, `on-surface` |
| Separators, arrow, look count | `.wfrt-quiet` |
| +N more / Fewer | `--text-xs`, `--weight-medium`, underline offset 2px |
| Quiet states | `.wfrt-quiet` |
| Run… | AppButton sm, secondary |

**Wrong looks like**
- The diff line is dimmer than the prompt, or the same secondary grey.
- Any segment is coloured, bold, chipped or struck through ("without" shown by strike alone).
- LoRA chips or the facts line still under the prompt.
- Separators or arrows at the same ink as the names, so the line reads as a solid block.
- "+2 more" drawn as a filled or bordered button, or placed mid-line.
- Run… amber on any card.
- A third line of the diff peeking out under the clamp.

---

### 2.5 "Make these the defaults…" dialog

- `AppDialog size="sm"`, which is `--dialog-w-sm` (420). Title: "Make these the
  defaults" (the `h2` AppDialog already renders).
- Body is flex column, `gap: --space-5` (AppDialog's own body rule).
  1. The body line, `--text-base`, ink: "Runs from this workflow start from
     these. Your pictures and saved recipes are not changed."
  2. The list: flex column, `gap: --space-2`. Each row copies `.rund-check`:
     `display: flex; align-items: center; gap: --space-3; --text-sm`, with a
     native `input type="checkbox"` styled as `.rund-box` (16px,
     `accent-color: rgb(var(--v-theme-primary))`, which is an olive tick) and a
     `<label>` carrying the full sentence. Values use tabular-nums, the arrow is
     secondary, and "Add" / "Take out" are ink. `min-height: --control-h-sm`
     per row, so the whole row is a 24px target.
  3. "Tick at least one" is a `.wftab-note`-style secondary line under the list,
     shown only while nothing is ticked, and referenced by the primary's
     `aria-describedby`.
- Footer: Cancel (secondary, `key-hint` Esc), then **"Make N defaults"**
  (`variant="primary"`, amber, `key-hint` ↵). With none ticked it is
  `aria-disabled` (0.38) and keeps its place. While sending it shows the
  AppButton spinner at full opacity with the label unchanged.

| Part | Token / class |
|---|---|
| Width | `--dialog-w-sm` via `size="sm"` |
| Body gap | `--space-5` |
| Body line | `--text-base`, `on-surface` |
| Rows | `.rund-check` + `.rund-box`, `gap: --space-2`, `min-height: --control-h-sm` |
| Primary | AppButton primary (`accent` / `accent-on`) |

**Wrong looks like**
- A pixel width, or a dialog wider than the notice (420) for four short rows.
- Hand-drawn checkboxes, an amber tick, or rows filled or highlighted when ticked.
- Two amber buttons, or Cancel amber.
- The primary dims while pending, or its label changes to "Making…".
- Model or LoRA rows shown disabled before F-1 (they must be absent).

---

### 2.6 Undo toast

- Surface: the notice host (`NoticeHost.vue` / `useNoticeStore`), variant
  **`success`**: `mdi-check-circle-outline`, `success` rail, border and 8% tint,
  with glyph and message in `on-surface` (notice-surface §3.2).
- Action "Undo": the standard notice action (`--text-sm`, semibold, ink,
  underlined). Not a button fill.
- Timing: the caller passes an explicit `timeout: 8000` (§1.2). Per notice-surface §6 rule 1 it
  still pauses on hover, focus-within and a hidden tab. Countdown hairline as standard.
- Failure is **not** a toast. It is the inline row error of §2.2, in `surface-error`.
- An Undo that itself fails pushes a normal sticky `error` notice.

**Wrong looks like**
- An `info` or `warning` card, or a green message text or glyph.
- A bespoke snackbar or a Vuetify `v-snackbar`.
- The toast disappears while the pointer is on it.
- "Undo" drawn as a primary or amber button.

---

### 2.7 Run dialog Stages fieldset

- `<fieldset class="rund-f rund-f--4">` reset: `border: 0; margin: 0; padding:
  0; min-width: 0`. It keeps `.rund-f`'s column flex and `gap: --space-2`, so the
  legend-to-row and row-to-row steps are both `--space-2`.
- `<legend>` styled as `.rund-l`: `--text-xs`, secondary, `min-height:
  --space-5`. Use `--opacity-text-secondary` and not the raw `0.6` `.rund-l` has
  today (drift, see Objections). `padding: 0` so the legend sits on the fieldset
  edge like every other label row.
- Rows: the existing `.rund-check` + `.rund-box`, labels "Upscale" / "Face detailer".
- Reset: the existing `RunResetChip`, **inside the check row after the label**.
  Its own `margin-left: auto` pushes it to the row end, value "on". The chip is
  `--space-5` tall, which fits the row without raising it.
- `stage_not_skippable`: `.rund-note.rund-note--bad` directly under that row.

| Part | Token / class |
|---|---|
| Group | `fieldset.rund-f.rund-f--4`, `gap: --space-2` |
| Legend | `.rund-l` look, `--text-xs`, `--opacity-text-secondary` |
| Row | `.rund-check`, `.rund-box` |
| Reset | `RunResetChip` (unchanged) |

**Wrong looks like**
- A browser-default fieldset border or legend inset.
- The legend larger or bolder than the other Run labels, or uppercase.
- The reset chip on its own line, or a row taller when edited than when not.
- "Run the upscale stage" wording still on the rows.

---

### 2.8 Grid filter chips

- The existing `FilterStrip` `.filter-chip`: `--control-h-sm`, `--radius-sm`,
  10% ink wash, `--text-sm`, with `.filter-chip-kind` in secondary before the value.
  - Chip 1: kind "Workflow", value the workflow name.
  - Chip 2: kind "Checkpoint" or "LoRA", value the short (resolver) name.
- Order: Workflow first, so × on the second chip leaves the first standing.
  Gap `--space-2`, as the strip does. No new chip component, no olive, no amber.

**Wrong looks like**
- One combined chip "anya_v2 in Portrait SDXL".
- A raw `.safetensors` filename in the value chip.
- Chips styled differently from the other filter chips (pill radius, hue).

---

### 2.9 Light theme

- Nothing here changes by theme except the olive checkbox tick, which follows
  `primary` per theme through `accent-color`.
- Secondary text on the rail is measured on `sidebar`, not `surface`: 0.7 passes
  (5.41:1 worst, visual-language §11). Do not drop the coverage text or
  separators below `--opacity-text-secondary` to "quieten" them.
- The recipe diff line's full-ink text on a light `surface` card is fine. The
  card sits on the lighter sidebar, so check that its 1px `border` still reads.
  That is the shipped card, not a new risk.

### 2.10 Objections, and how they were settled

1. **Show N on a value in every picture.** Accepted: Show N renders only when N
   is below the workflow's picture count (§1.5). A row with no coverage text and
   no Show N gets no meta line.
2. **Recipe card Run….** Settled as the shipped secondary button, not ghost: it
   is the card's main verb and a ghost button would leave the card with no
   visible action at rest.
3. **Drift seen on surfaces this touches**, to fix in the implementing PR:
   `.wfpile:focus-visible .wfpile-top` paints an olive (`primary`) outline where
   focus must be the ink ring (`--focus-width` `--focus-stroke`
   `--focus-offset`), and `.rund-l` uses a raw `0.6` alpha where
   `--opacity-text-secondary` belongs.

### 2.11 Open visual questions for the owner

1. **Checkbox primitive.** The design system names a Checkbox, but the app has no
   `AppCheckbox`. The Run dialog uses a native input with an olive
   `accent-color`. Recommend reusing that `.rund-box` pattern for §2.5 and §2.7
   now, and not building a primitive inside this feature.
2. **Meta line vs. inline Show N** in DEFAULT RECIPE rows. Recommend the meta
   line (the full name beats a compact row at about 160px), combined with
   Objection 1 so it appears only where it says something.
