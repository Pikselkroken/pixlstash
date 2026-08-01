# Stack units in Duplicates, and Mixed stacks

Status: approved by the owner. Phase 0 and Phase 1 (backend) are implemented;
Phase 2 (frontend) is partly done: the queue row's unit model and deck have
landed, and so has Compare (one card per unit, the `Contains` row, D4's
expansion band below the strip, member promotion with its consequence, and a
zoom over pictures). **The queue ROW's own expansion has now landed too**: the
deck's count badge (and `E`) opens a read-only band below the row's columns, at
most one in the queue and only on the focused row, with the members read lazily.
**The Mixed stacks page has now landed too**: the third page of the destination,
its threshold-bound list, the `Split off N` / `Unstack` / `Keep` actions, the
strong-case warning chip on a deck in the queue row (with the dense inversion
below 168px), and the two-way shortcut between the two. What is listed under
"Open" below is still open.

Owner decisions and the reconciled `ui-ux-expert` / `lead-designer` proposals.
Implementation spec: where these disagree with a subagent's report, this file wins.

## Why

The Duplicates queue renders one tile per **picture**, but a stack verdict moves
whole **stacks**. `_stack_members` folds in every member of any stack the group
touches (`pixlstash/services/dedup_verdict_service.py:502-508`), so the row
offers two gestures it cannot honour:

* excluding one picture of an existing stack is a silent no-op, because the rest
  of its stack drags it back in;
* choosing one as cover silently re-covers that whole stack in the library.

The fix is to make the thing on screen be the thing the backend moves.

Measured on a real 17k-picture library: 209 live stacks with 2+ members, 116
unresolved groups where a stack meets at least one other unit (~70 stack + loose,
~10 stack + stack, ~36 where only one member of a stack is in the group).

## Decisions

### D1: Fully collapsed groups leave the Duplicates surface entirely

A group whose live members are already in one and the same stack poses no
decision. The queue list already excludes these via `_live_groups_filter`; **bulk
auto-stack must use the same filter** instead of `stackable_groups_filter`.

Today auto-stack plans 62 groups where the queue shows 3, reports "62 stacks to
create" when it would create 3, and would re-cover 21 already-curated stacks
because `_stack_members` forces the group's preselected cover to position 0.

### D2: The unit, not the picture, is what the queue renders

A **unit** is the smallest thing a stack verdict can move independently:

* a **loose picture**, `stack_id IS NULL`, its own unit;
* a **deck**, all candidates sharing a non-null `stack_id`, collapsed into one.

**A deck stands for the entire existing stack, not the members that happen to be
in the group.** Its depth is the stack's real member count, so a group's picture
total can exceed `candidates.length`. Its face is the stack's leader
(`stack_position` 0), because the cover choice resolves to the leader and a tile
showing one picture while meaning another leads is the mismatch being removed.

Cover selection, exclusion and `Compare all N` all operate on units.
`MIN_STACK_MEMBERS` becomes a floor of two **units**.

**Excluding one picture out of an existing stack is withdrawn**, along with
promoting a non-leader deck member from the queue row. Promotion survives in
Compare, where the consequence sentence has room.

### D3: The button names the outcome

Three outcomes, three labels, matching the backend's own `SweepOutcome`:

| shape | label |
|---|---|
| all loose | `Stack 3` (unchanged) |
| deck + loose | `Add 1 to stack of 4` |
| deck + deck | `Merge 2 stacks` |

The size stays in the button because expansion is opt-in: a user working at speed
with `Enter` never opens one, so the button is the last text before committing.
Degrade under width pressure `Add 1 to stack of 4` → `Add 1 to stack` → `Add 1`;
the size never leaves the header.

Header shows the composition: `Stack of 5 + 1 picture`, `Stack of 5 + stack of 3`.

**When a group contains a deck, the preselected cover is the deck**, not the
server's smart-score pick. Otherwise the default action re-curates a stack the
user already made. `Keep separate` copy becomes "Leave these as they are. The
existing stack stays exactly as it is."

### D4: Expansion in place is the disclosure

Reuse `StackExpansionStrip.vue`. **Note it has never been mounted anywhere**
("NOT MOUNTED YET, deliberately", line 65) and its 128×96 thumbnails contradict
the queue's 112–406px size slider, so first mount must take the row's
height-driven recipe (`height: var(--gthumb-h); width: auto`), width-auto is
EXIF-rotation correctness, not preference.

* Renders as a full-width band below the row's columns, never inline in
  `.gstrip` (which is already an `overflow-x` scroller; nesting a second
  horizontal scroller on the same axis is ambiguous on trackpad and touch).
* **At most one expansion at a time, on the focused row.** `DuplicateQueue` sizes
  both scroll spacers from a single uniform `rowPitchPx`
  (`rows[1].offsetTop - rows[0].offsetTop`, line 612), so a variable-height row
  breaks the arithmetic. One-at-a-time keeps the pitch sampled from collapsed
  rows and makes the expansion a single offset below one known index.
* Disclosure, not a mode: verdicts stay live, other units keep their state and
  numbers, `Enter` immediately after expanding does what it would have anyway.
* Trigger is the deck's count badge (`StackBadge` already emits `activate`),
  placed as an absolutely-positioned **sibling** of `.gthumb`, not a child, a
  button inside a button is invalid markup, per `.dc-zoom`'s precedent.
* Keyboard `E` toggles; digits still address units, never expanded members.
* **Expanded members are read-only in the queue row.** `StackExpansionStrip`
  emits `unstack` and `set-cover`; both would silently rewrite the library from
  inside a panel opened in order to look. Use its `readOnly` prop.
* In Compare, the band sits below `.dc-strip`, never inside a card, so card
  heights stay registered.
* Decided page: a `stacked` row renders as one deck and is expandable; a
  `keep_separate` row renders as units.

The dropped on-tile "1 of 4 matched" marker still goes in the accessible name:
`a stack of 4 pictures, 1 of them matched`. An aria-label has no corner budget.

### D5: Mixed stacks

A **mixed stack** is a live stack whose members do not form one connected cluster
at the current similarity threshold, using the same 64-bit dHash Hamming distance
and connected-components test the near tier already uses.

Measured on the owner's library, and **point-in-time**: the live stack count moved
from 209 to 160 during the day this was written, so quote the ratio with care.
**26 mixed at the default 0.90 threshold and 9 at the 0.65 floor**, both
reproduced independently by two implementations against the same database, and
stable across that change in denominator.

"Stranded" needs its definition stated with the number, because two readings give
two answers. Degree zero (a member sharing an edge with nothing else in its
stack, which is what the service implements as `stranded_picture_ids`) gives 8 at
0.65. The narrower "a real cluster plus one lone singleton" gives 4. The 5 quoted
in earlier drafts was that narrower reading on the pre-split library.

* **Bind the list to the existing threshold slider**, not a constant. 0.90 → 26,
  0.65 → 9. The spectrum becomes drivable with a control already on the toolbar.
* **One surface: a third page of Duplicates.** Not a sidebar destination (the
  architecture rule is that only a destination with a to-do count earns a row,
  and 9-26 items is not one), and **not** a grid filter value.

  The grid-filter half was proposed on the grounds that `unresolved` is
  precedent for a dedup-derived value on `stackStateFilter`. That precedent was
  withdrawn while this spec was being written: `unresolved` is still honoured by
  the store and the API but is no longer offered in the filter panel, because
  "the duplicate queue owns that work" (`useFilterStore.js`). Adding `mixed`
  there would contradict that, and discovery was the only thing the filter value
  bought. The page carries both the discovery and the actions.
* **Primary action** names its outcome: `Split off 1` when there is a clear
  stranger, `Unstack` when there is no majority cluster.
* **`Keep`**: durable, server-side, keyed on stack id **plus a fingerprint of
  its membership**, so adding a member later re-raises it. Without this the ~17
  legitimate-but-odd stacks sit in the list forever and it becomes ignorable.
* Ranked by how little holds the stack together: members joined to nothing
  (desc), component count (desc), weakest edge (asc). No visible rank numerals.
* Count on the page toggle, **never on the sidebar badge**, the queue's to-do
  count is the one number that must stay trusted.
* The row is a list, not a card stack: no per-row border, background, radius or
  focus bar. It must not look like a second queue.

**Only the strong case is marked on a tile.** At 12% a warning is one tile in
eight and becomes a warning field; the soft cases are often legitimate (a burst
where one frame panned off), so marking them trains the user to dismiss the
colour before the real one appears. Soft cases surface in words, in the expansion
and the list.

The mark reuses the deck badge's icon slot, freed because the edge ticks already
say "this is a stack": `mdi-alert-outline` in `--v-theme-warning`, backing
deepened to `--scrim-photo-strong` (required for a chromatic glyph over an
arbitrary photo, and a second non-colour channel), 1px inset warning ring. Below
168px the dense rule inverts: an unflagged deck keeps its numeral and drops the
icon; a flagged deck keeps the icon and drops the numeral.

Badge precedence: expanded > flagged > per-stack tint. No motion; the flag is a
standing fact, not an event. `--v-theme-warning` moving means a refused press;
still means flagged.

**The flag never blocks a verdict.** A mixed stack is one a user may legitimately
want to add to. A warning that blocked would be the third control this feature
offered that it could not honour.

### D6: Naming

`ui-ux-expert` and `lead-designer` independently converged on **"Mixed stacks"**.
It describes the object rather than the user's error and is true of the
legitimate cases too. Rejected: "possible mistakes" (blames), "loose ends"
(metaphorical, and collides with "loose picture" in D2), "check these stacks"
(an instruction in a slot the app fills with nouns).

Code: `mixedStack` / `mixed_stacks`. Row title `Stack of 5` over
`1 picture doesn't match the rest` (strong) or `These don't all match` (soft).
Empty state `No mixed stacks`, mirroring the shipped `No decided groups`.

## Backend contract

**B1: the payload must carry stack truth.** `stack_id` alone cannot render a
deck. Per group, a `stacks` block keyed by stack id: `stack_id`, `member_count`,
`leader_picture_id`, `leader_thumbnail_version`, `matched_picture_ids`,
`stackable`, `blocked_by_sets`. Eager count and leader, **lazy members**,
shipping every member of every stack would break the queue's never-render-whole
rule for a 40-member stack; the expansion fetches on open.

**B2: the cover must be allowed to be a stack's leader.**
`apply_stack_verdict_in_session` raises when `cover_id not in included`
(`dedup_verdict_service.py:700`), and a deck's leader is frequently not a group
member. Relax the check to the set of pictures that will end up in the resulting
stack: group members ∪ full membership of every folded stack, which
`_stack_members` already materialises. **Until this lands, D2's cover semantics
are unimplementable.**

**B3: withdrawn.** An earlier draft claimed `stackable` was blind to locked
siblings outside the group. It is not: `_locked_sets_by_picture` calls
`expand_picture_ids_to_stacks` first (`set_lock_service.py:65`) and
`locked_sets_for_pictures` rolls each frozen picture's sets up to its stack and
back onto every input id (`:114-140`). Locks already propagate. No change needed.

**B4: count honesty.** `summary=f"Stacked {len(included)} duplicates"`
(`dedup_verdict_service.py:779`) and `_dry_run_summary_in_session` both count
group members only, so both under-report when a stack is folded in. Receipts and
the auto-stack consent dialog must count the stack-expanded set, and the queue's
announcement must use the server's returned `picture_ids`, never a client
estimate.

**B5: cohesion scoring.** A finder in `pixlstash/tasks/` registered with the
`WorkPlanner`, scoring stacks the way the other `Missing*Finder` classes do, so
the flag stays current as stacks change. Computed, not stored per row. Cost is
O(Σn²) over stacks, and stacks are small.

**Two fingerprints, not one.** An earlier draft said "cache keyed on membership",
which is wrong: `perceptual_hash` changes without membership changing (the
embedding worker filling a NULL, a replaced reference-folder file), and a
membership-keyed cache would then pin a member as stranded forever, precisely
the false positive this feature cannot afford. So the **cohesion cache** is keyed
on member ids *and* their hashes, while the **`Keep` dismissal** stays keyed on
member ids alone, because D5 is explicit that adding a member is what re-raises a
kept stack. Each key is right for its own job.

Every new data route declares its `AccessPolicy` in `pixlstash/authz/registry.py`.
The gate is deny-by-default and an undeclared route 403s and fails CI.

## Free fixes, independent of all the above

1. **`.gnum` and `.glock` collide today.** Both sit at
   `top: var(--space-2); left: var(--space-2)` (`DedupGroupRow.vue:829`, `:865`),
   so a focused row containing a locked candidate draws the index under the lock
   chip. Both top corners become flex columns, mirroring
   `.thumbnail-top-left-badges` / `.thumbnail-top-right-badges` in `ImageGrid`.
2. **The excluded fade hides its own explanation.** `.gthumb--out` fades the
   whole button including the chips that explain the state, which is why
   `.glock` needs a flash animation to be noticed. Move the fade onto `.gt` (the
   image); ticks, badges and chips stay full strength.
3. **Raw opacity drift**: `0.4` at `DedupGroupRow.vue:798` and `:855`, `0.38` at
   `:1000` → `var(--opacity-disabled)`.
4. **Motion drift**: `.gstars` / `.gsmart` use `transition: opacity 0.15s ease`
   → `var(--dur-1) var(--ease-standard)`.

## Sequencing

* **Phase 0**: the free fixes. No backend, no design risk. Must land before the
  deck work, which touches the same corners of the same file.
* **Phase 1**: backend: D1, B1, B2, B4, B5, and the `Keep` store + migration.
* **Phase 2**: frontend: units and the deck in `DedupGroupRow`, then expansion,
  then Compare, then Mixed stacks. Complete. The grid filter value named in an
  earlier draft of this line was withdrawn by D5 itself and was never built.
* **Gate**: `chief-security-officer` review before merge; new data routes and
  their `ROUTE_POLICIES` entries are exactly the BOLA class this repo has been
  burned by.

## Open

* **`bulk_auto_stack`'s websocket announcement is still narrow.** It broadcasts
  each result's `picture_ids` (the group's own members), so a folded stack's
  siblings never get the update: its own comment admits this as a "rare,
  exact-tier gap". D1 does not fix it and slightly widens its reach, since the
  run now folds stacks more deliberately. Fixing it means returning the
  stack-expanded ids from `bulk_auto_stack_in_session` and passing them through.
  Deliberately deferred rather than smuggled into the D1 commit.
* Whether users expand at all. If they never do, the header and button text carry
  the entire disclosure and the button's size suffix must not degrade. One
  afternoon of observation settles it; the measurements cannot.
* Whether the flag is computed live or at render. If it can appear mid-scroll it
  needs the landing pulse rather than popping in; the spec assumes stable.
