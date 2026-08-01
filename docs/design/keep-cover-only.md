# Keep cover only: collapsing a stack to its cover

Status: **shipped** (backend and frontend). Designed and approved by the owner,
reconciled from the `ui-ux-expert` / `lead-designer` proposals. Where they
disagreed, the resolution and its reasoning are recorded here; this file wins.

Where it lives: `pixlstash/services/keep_cover_only_service.py` +
`pixlstash/routes/stacks.py` on the backend; on the frontend
`frontend/src/api/stacks.js` (the two URLs),
`frontend/src/utils/keepCoverOnly.js` (the copy and the two selection
computations, both pure), `frontend/src/components/widgets/KeepCoverOnlyDialog.vue`
(the consent), the menu item in `ImageGridContextMenu.vue` /
`SelectionMenu.vue`, and the preview / run / ghosting in `ImageGrid.vue`. The
wire contract is `docs/integration_architecture.md` §2.2.

Companion to `mixed-stacks-and-stack-units.md`, which owns the queue and the
deck model. This document owns the one destructive action in the flow.

## Why

The dedup surface is deliberately additive, and says so to the user repeatedly:
*"No picture is ever deleted"*, *"Every file stays on disk"*, `Files deleted: 0`.
`pixlstash/routes/dedup.py` states it as an invariant. The consequence is that a
user can triage thousands of groups, end up with tidy stacks, and reclaim
nothing.

Measured on the owner's library: **160 stacks holding 574 pictures; 414 are
non-cover copies, about 1.15 GB.**

This is not a departure from the release plan. That plan already reserved this
shape while explaining why the sweep itself is non-destructive: *"Deletion to the
Scrapheap never gets the post-hoc framing and is never automated; it stays a
separate explicit aggregate action."*

## Naming

**`Keep cover only`.** Confirm button: **`Move 414 to the Scrapheap`.**

"Squash" was the owner's word and is rejected on a safety argument, not taste.
The vocabulary already has `Stack` and `Unstack`, and `Unstack` loses nothing.
A third verb on the same object that *does* lose things, distinguished by a word
that in git means **merge without losing content**, is a name that can cause the
accident. "Squash to cover image" was considered and also rejected: a collapsed
stack already renders as its cover, so the label describes something that looks
as though it has already happened.

**The title says what you keep; the button says what you lose.** That pairing is
the dialog's safety property and it survives whatever the menu item is called.
The button label and the headline figure render from the **same computed value**,
not merely the same endpoint, so they can never disagree.

Code / op type: `keepCoverOnly` / `stack.keep_cover_only`. Keep `squash` out of
identifiers too; a git-literate reader grepping it will assume merge.

## What it does

**Soft-delete to the Scrapheap, never permanent.** `scrapheap_service` opens by
stating there is deliberately **no second destruction path**, guarded by preview,
a single-use `confirm_token` and type-to-confirm. This must not become the
second one. It reuses the same soft delete the grid's `Delete` already uses:
recoverable, one op-log batch, one `Ctrl+Z`.

**No type-to-confirm.** That gate is reserved for destroying an on-disk original.
Spending it here would flatten the distinction between "recoverable" and "gone",
which is the distinction the whole Scrapheap design rests on.

**Do not dissolve the stack, do not detach members.** A soft-deleted picture
keeps its `stack_id`. Leaving the row intact makes undo a flag flip, and
`restore_pictures` (`routes/pictures/_crud.py:1115`) already clears `deleted_at`
and calls `normalize_stack_positions`, so a restored copy genuinely rejoins its
stack instead of landing loose. No "stack of 1" is ever rendered because the
badge gates on **live** members.

**The cover is always the stack's current leader.** This action never picks a new
one; fusing a cover choice into a destructive click is two decisions in one press.

### The metadata union is mandatory, and this is the sharpest point

`apply_metadata_union_in_session` is called from **exactly one place**, the dedup
stack verdict. Stacks made by hand in the grid have never been unioned.

Measured: **110 of the owner's 160 stacks have a copy carrying tags the cover
lacks**, and 8 have a copy outscoring their cover. Collapsing without unioning
first would silently destroy metadata on two-thirds of them.

So: **re-run the union onto the cover before any soft delete, unconditionally.**
It is idempotent where it already ran. Do not optimise it away on the grounds
that the queue does it: the queue is not the only way stacks get made.

* **Tags** union onto the cover; **score** lifts to the stack's best.
* **Characters:** the union deliberately refuses to guess when members reference
  more than one character. Under stacking that is right, because nothing is lost.
  Here it means a character link can be **destroyed** when the only picture
  carrying it leaves. **Skip those stacks, count them, name them.**
* **Set memberships** are safe by construction: stacks reconcile to the union of
  their members' sets, so the cover is already in every set the stack touches.
* **Reference-folder members** need their own count: the row moves but the file
  is user-managed and is not touched.

### Locked sets refuse the whole stack

Not skip-the-member. Stack membership reconciles to the union of its members'
sets, so removing a member from a stack a locked set touches is exactly the
mutation the lock forbids, and a partial collapse is the worst available
outcome: some copies gone, the stack still there, no visible reason.

Menu state follows the shipped `Delete` item: disabled with the lock reason only
when **every** selected stack is locked; otherwise enabled, with the dialog
reporting the skips.

## The confirm dialog

Every figure comes from **one server-side dry run over the same selection, in one
read**. Buckets are disjoint and sum to the total; never derive one by
subtraction. This is the direct lesson of the neighbouring auto-stack dialog,
which reported "62 stacks to create" for work that would create 3.

Order:

1. **Lede.** Each stack keeps its cover. Every other picture moves to the
   Scrapheap, where you can restore it.
2. **The headline figure**, one instance, larger than the dialog's own heading:
   **414** over "pictures move to the Scrapheap". The numeral is `on-surface`,
   never `error`: the hue goes on a leading rail, because a 22px red numeral
   would be the loudest object in the app. While the preview is in flight or has
   failed it shows an en dash at the same size and the confirm is disabled:
   never a zero, never a stale number.
3. **The rows** (`<dl>`, neutral, no hue): stacks collapsed, covers kept, covers
   gaining metadata from copies, stacks skipped, and **originals deleted from
   disk: 0**, stated out loud exactly as the sibling states its own zero.
4. **The recovery panel**, info-tinted rather than error. Recovery is
   reassuring, and `DeleteForeverDialog` already made this call for the same
   reason.
5. **Undo**, matching the sibling verbatim.

**The retention sentence must read the live setting.** `DEFAULT_RETENTION_DAYS`
is `None`: on a default install **the Scrapheap never empties on its own**.
Hardcoding "30 days" would be the same class of error the whole dialog exists to
avoid. Copy branches on the user's configured value.

**Never claims:** space "freed", "reclaimed" or "saved" in the present tense; the
words "permanently" or "delete"; a headline that includes skipped stacks; a
retention period it has not read from config.

**1.15 GB is a sentence, not a figure.** The general rule, worth keeping: a
figure block is for what changes now; a sentence is for what changes later.
Deferred outcomes never get the figure treatment.

**Cancel is focused by default and plain `Enter` does not accept**, deliberately
inverting the app's dialog convention: users arrive with `Enter` under their
finger from the queue's verdict keys. Document it so it is not "fixed" later.

## Menus

Context menu and the selection pill's overflow, wearing the shipped
`.ctx-item--danger`, in the existing trailing danger group behind a separator,
ordered by escalating severity: Keep cover only → Move to Scrapheap → Delete
forever. Glyph `mdi-layers-minus`, the inverse of the `mdi-layers-plus` the user
pressed to build these stacks. Not `mdi-delete`, which would over-claim.

**No top-level button in the selection pill.** A floating pill over a photo grid
is the wrong place for an `error`-filled control, and this is periodic cleanup,
not a high-frequency verb.

The unit is the stack: a selection *names* stacks and each collapses to its own
cover. Loose pictures in a mixed selection are ignored, which is honest only
because the label counts stacks: `Keep cover only (3 stacks)`. A partial
selection inside a stack collapses the **whole** stack, and the dialog must say
so, because it is the one place the action does more than the selection literally
names. Partial eligibility goes in the label: `Keep cover only (12 of 20)`.

**No new keyboard shortcut.** `Delete` already means "move selection to the
Scrapheap"; a second, differently-scoped destructive key is how the wrong one
gets pressed.

## The route from Duplicates

The **queue-clear state**, and only there. It is already the end-of-task surface,
and it is the moment the user has stacks and nothing left to triage. The toolbar
is wrong: it would put this in front of someone mid-triage.

Shown whenever the library has at least one live stack with 2+ members, **not**
gated on this session's tally: the owner has 160 stacks predating the feature.

**The shortcut goes to the place, not to the action.** It lands in All Pictures
with the stacked filter applied, nothing selected, nothing about to happen. A
one-click path from a satisfying "Queue clear" screen into a confirm for 414
deletions is how you get a bad afternoon. A real route change carrying
`stack_state=stacked`, so it is reloadable and Back returns to the queue.

## The receipt

Inherits `DESTRUCTIVE_RECEIPT_MS` (8s) automatically via `isDestructiveOpType`,
no new duration, no new component. Glyph matches the menu item and the confirm
button so the operation is named identically at all three moments.

**The pill is not tinted.** The receipt appears after an action the user
deliberately confirmed; painting it red says "something bad happened" about their
own decision. The system already encodes this asymmetry: a destructive *duration*
exists, a destructive *colour* does not.

Text names the consequence in the user's unit: *"414 pictures moved to the
Scrapheap"*, with Undo. Skips get a second sentence rather than a separate toast.
**No space figure in the receipt**: it was a potential in the preview; in a
receipt it would be false at the moment it is displayed.

## Interactions during the Scrapheap stay: verified

Checked because a picture may now sit there for 30/60/90+ days:

* **Restore rejoins the stack.** Clears `deleted_at`, re-normalizes positions.
* **Undo after a purge fails closed.** `_enforce_scrapheap_targets_exist` returns
  410 and refuses the whole undo rather than half-restoring.
* **A restore racing a purge cannot lose the rescued rows.**
  `purge_rows_in_session` re-evaluates the `deleted` predicate inside the same
  transaction, for exactly this reason.
* **Shortening retention has a grace floor**, and locked pictures are exempt from
  auto-purge.
* **Snapshot restore is safe.** A snapshot is a full SQLite copy of the DB and
  does not copy image files, so the hazard is restoring a pre-collapse snapshot
  after a purge. Restore already drops rows whose files are missing, cross-checks
  the permanent-deletion ledger so purged pictures are never resurrected, and
  applies a ratio-based guard that refuses to wipe metadata when the pattern
  looks like a mount failure instead of a deletion.

### The one real gap: re-import cannot see the Scrapheap

`routes/pictures/_helpers.py:132` matches by content hash with
`Picture.find(..., pixel_shas=shas)`, and `include_deleted` defaults to `False`.
Re-importing a file whose picture is scrapheaped creates a second row while the
original is still there, so a collapse is silently undone, disk use roughly
doubles, and the duplicate queue refills.

Pre-existing for any scrapheaped picture, but this action makes it predictable:
it scrapheaps hundreds of pictures at once, and they are by definition copies of
files that still exist wherever the user imports from.

**Owner decision: import matches against scrapheaped rows and offers to restore
rather than importing a second copy.** Reported as a third, disjoint bucket
alongside imported and duplicate: never automatic, because the user deliberately
scrapheapped them. In progress separately.

## New token (shipped)

`--rail-w: 3px`, the leading status rail. The tokens file already names two
rendered lengths on the reasoning that they "cannot borrow from the spacing
scale", and `--countdown-h`'s comment says naming it "is what stops the next
surface picking 3px". Three components already carry a raw 3px rail.

## Cleanups this work should take

1. ~~`AppButton.vue`'s danger comment claims `on-error` "flips to the warm
   near-black" in dark. `main.js` has `"on-error": "#f7f1ea"` in **both** themes,
   one line even saying "(same value in both themes)". Fix the comment, not the
   value: a stale contrast note gets "corrected" in the wrong direction.~~
   **Done** with this lane; the note now records what was wrong and why, so the
   next reader does not re-derive it from the value.
2. Two destructive-button looks: `AppButton variant="danger"` (solid fill) versus
   `DeleteForeverDialog`'s hand-rolled tinted `.btn-danger`. Converge on the
   `App*` layer. **Deferred.** `KeepCoverOnlyDialog` is built on the `App*` layer
   already, so it adds no drift; converging the other way changes how the
   type-to-confirm dialog looks, which is a visible change to the app's most
   destructive surface and goes past the UI/UX expert first.
3. Four tinted consequence panels with four different alpha pairs. Converge on
   one `.notice-panel` recipe with a hue modifier. **Deferred**, same reason: the
   new dialog's panel matches `DeleteForeverDialog`'s `.lock-note` exactly
   (`info` at 0.08 fill / 0.5 border), so it is a fifth call site of an existing
   recipe rather than a fifth recipe, but collapsing all four is a re-render of
   three shipped surfaces.
4. ~~`DeleteForeverDialog.vue:419` raw `opacity: 0.38` → `--opacity-disabled`.~~
   **Done** with this lane. Identical value, no visual change.
5. A fourth `<kbd>` recipe; converge on `AppButton`'s `key-hint`. **Deferred.**
   `KeepCoverOnlyDialog` copies `DedupAutoStackDialog`'s `<kbd>` verbatim, which
   is what "matching the sibling verbatim" asks for above; converging them is one
   change across all the surfaces that carry keycaps, not a per-dialog decision.

## Acceptance criteria

1. No figure in the dialog derives from a different query than the one the button
   acts on, and no bucket is computed by subtraction.
2. The dialog states `0` for originals deleted from disk, and its retention
   sentence matches the live `scrapheap_retention_days`, including "never".
3. Collapsing a hand-made stack unions tags and lifts the score onto the cover
   before any soft delete.
4. A stack whose only character link sits on a non-cover member is skipped,
   counted and named.
5. A stack containing a locked-set member is refused whole; siblings proceed.
6. After the action the survivor renders as a plain picture, no stack row is
   dissolved, and one `Ctrl+Z` restores the stack with cover and positions.
7. Restoring a member from the Scrapheap returns it to its stack, not to loose.
8. No path from the queue-clear screen reaches a destructive confirm in fewer
   than two deliberate steps.
9. Neither the receipt nor the announcement claims any space was freed.
