# Merged grid action pill — search + selection in one bottom-edge surface

Status: **built, with the exceptions listed in §12.** Authored jointly by
`lead-designer` (visual) and `ui-ux-expert` (behaviour/a11y), reconciled into one
spec. Every conflict between the two lanes is resolved below under **Resolved
conflicts**; where the resolution went against the visual lane it is because
`CLAUDE.md` makes usability the tiebreaker.

Shipped as `panels/GridActionPill.vue` (the surface) hosting
`widgets/SearchResultBar.vue` and `panels/SelectionBar.vue` as slotted halves.
See `frontend_architecture.md` for the component contracts. **§12 lists what was
deliberately not built and why** — read it before assuming this document
describes the running code in full.

Scope: the grid's bottom edge only. Companion docs: `notice-surface.md` §2.2
(the `--floating-bottom-h` contract), `visual-language.md` §5/§13 (action-bar
heights, standing states), `toolbar-responsive-decisions.md` (prior
compress-don't-hide rulings).

---

## 1. The problem, restated from the code

Three surfaces claim `.grid-content-area`'s bottom edge today, and none of them
knows about the others:

| Surface | Position | z-index | Width | Registers a bottom anchor? |
|---|---|---|---|---|
| `SearchResultBar.vue` | `bottom: 0` | 200 | full | **No** |
| `SelectionBar.vue` (the pill) | `bottom: var(--space-5)` | 200 | `max-content` | Yes (`"selection-bar"`) |
| `.multi-select-toolbar` (`ImageGrid.vue:8745`) | `bottom: 0` | **300** | full, 36px | **No** |

Consequences, all verified in code:

- **The search bar and the selection pill can be up simultaneously** — their
  conditions are independent (`ImageGrid.vue:901` vs `:1005`). This is the
  collision the merge exists to fix.
- **`.multi-select-toolbar` paints over `SearchResultBar`** whenever a text
  search runs in a multi-character / set-overlap view. z 300 over z 200, both at
  `bottom: 0`. Shipped defect.
- **Notice cards land on top of the search bar.** `--floating-bottom-h` is fed
  only by `"selection-bar"` and `"grid-breadcrumb"`; `SearchResultBar` never
  calls `useBottomAnchor`, so with a search up and no selection the notice stack
  rests at the pill-absent inset. Shipped defect.
- `.grid-breadcrumb` (`bottom: 12px`) sits *inside* the search bar's band.

The merge's real deliverable is therefore **one owner of the bottom edge**, which
retires all four problems at once. See §10 — the multi-select toolbar must come
along or the redesign fixes two collisions out of three.

---

## 2. Anatomy

The merged pill inherits `.floating-selection-bar` wholesale: `bottom:
var(--space-5)`, `left: 50%` + `translateX(-50%)`, `width: max-content`,
`max-width: calc(100% - var(--space-6))`, `padding: 6px var(--space-4)`,
`--radius-pill`, `rgba(var(--v-theme-surface), 0.86)`, `--elevation-3`, `1px
solid rgba(var(--v-theme-on-surface), 0.14)`, `backdrop-filter: blur(12px)`.

**Two annotated values are deliberately not touched.** The `6px` block padding is
reserved for the UI/UX-gated 34/40/48/56px action-bar reconciliation
(`visual-language.md` §5/§13) — merging two bars is not the occasion to open it.
`bottom: var(--space-5)` is load-bearing for the notice arithmetic. So the pill
stays **54px** tall (40px control band + 2×6px + 2×1px), and every measurement
below assumes a 40px inner band.

### State A — search only

```
   +--------------------------------------------------------------------------------+
   | 14 possible pictures of Anna | Match >= 82% ---o--- | [Assign 14 to Anna]   Clear search (Esc) |
   +--------------------------------------------------------------------------------+
     ^                             ^                      ^                    ^
     status sentence, --text-sm    label IS the readout    the ONE accent      quiet .stack-btn
     numerals tabular              1px x --rule-h rule     bulk write          + <kbd>Esc</kbd>
                                   each side --space-3
```

### State B — search + selection

```
   +----------------------------------------------------------------------------------------------------+
   | 42 matches for "sunset" | Search everything | Clear search  ||  12 selected v | (x) Clear   (bin) Delete |
   +----------------------------------------------------------------------------------------------------+
                                                                ^
                                                              SEAM: 1px x --rule-h-seam,
                                                              rgb(var(--v-theme-border)),
                                                              margin 0 var(--space-3)
                                                              (16px of air each side)
```

Segment order is **search first, selection second**: state → controls that shape
the set → actions → dismissal, read left to right.

### The divider — two lengths, one colour

| | Colour | Size | Air each side | Marks |
|---|---|---|---|---|
| intra-segment **rule** | `rgb(var(--v-theme-border))` | `1px × var(--rule-h)` (24px) | `--space-3` = 8px (the pill's own gap, `margin: 0`) | "what was found / how it's tuned" vs "what you can do" |
| segment **seam** | `rgb(var(--v-theme-border))` | `1px × var(--rule-h-seam)` (32px) | `--space-5` = 16px (`margin: 0 var(--space-3)` + the pill's 8px gap) | search context vs selection context |

`border`, not `divider`: §4 of the visual language splits these into "subtle" vs
"visible", and under `backdrop-filter: blur(12px)` the subtle one is a whisper —
`divider` is ~1.15:1 on `surface` and does not survive a bright photo bleeding
through the 14% of transparency. `border` is authored per theme (`#d8d3c8` light
/ `#363d45` dark), so it is correct in both without a hand-rolled alpha. These
are decorative separators, not components conveying information, so WCAG 1.4.11
does not apply — the same call the pill's existing 14% border already makes.

Rule and seam differ by **height and air only**, never by a second colour. Two
line weights in one 54px pill is noise. Accepted fallback if review wants fewer
tokens: collapse to one `--rule-h` and differentiate the seam by air alone —
weaker, not wrong.

The threshold groups with the **status**, not with the actions, because dragging
it changes the count. The rule sits after the threshold.

### 2.1 Telling the two halves apart — air and content, not a second fill

A two-tone fill (the search half one colour, the selection half another) was
proposed and **rejected**; the arithmetic is in §11. The want behind it — *"I can
tell at a glance which half I'm looking at"* — is served in the channels that are
free, and this is the rule for it:

**(a) Two counts as the landmarks.** Each half opens with its own count in one
shared recipe: numeral `--text-md` / `--weight-semibold` / `tabular-nums`, noun
`--text-sm` / `--weight-regular` / `rgba(var(--v-theme-on-surface), .65)`.

```
128 results  ...............................  34 selected
```

Two numerals bracketing the pill is the fastest available parse of "left is what
I found, right is what I picked", and it is the *content* doing the work. This is
why §3 folds the scope note and the faces span away and §6.3 forbids the word
"results" on one side and "selected" on the other.

**(b) One identity glyph per half.** Search opens on `mdi-magnify`, selection on
the shipped `mdi-image-multiple-outline`. Both 18px,
`rgba(var(--v-theme-on-surface), .55)`. Already true of the selection half; made
a rule so nobody drops one.

**(c) The seam's gutter is wide, and that is the differentiator.** Air is the
system's own structuring tool (§5, "whitespace is structure"). The seam gets
**16px each side — 32px across — against the pill's 8px internal rhythm**, a 4×
step that reads at a glance and from across the room:

```css
.pill-seam {
  width: 1px;
  height: var(--rule-h-seam);   /* 32px */
  background: rgb(var(--v-theme-border));
  align-self: center;
  flex-shrink: 0;
  margin: 0 var(--space-3);     /* 8px + the pill's 8px gap = 16px each side */
}
.pill-rule {                     /* the intra-half rule */
  width: 1px;
  height: var(--rule-h);        /* 24px */
  background: rgb(var(--v-theme-border));
  align-self: center;
  flex-shrink: 0;
  margin: 0;                    /* the pill's 8px gap alone */
}
```

The boundary is the absence, not an added block of colour — and unlike a fill it
scales correctly, because air at 32px reads as air at every split ratio (the
halves' proportion slides from roughly 50/50 to 25/75 down the ladder).

**(d) If more is ever wanted, escalate structurally, never chromatically.** The
next step up is the seam becoming a **notch**: the pill's top and bottom borders
dip 2px inward at the seam via a `clip-path` on the pill. Same colour, same
weight, a physical event in the silhouette. More expensive to build than air —
do it only if (c) genuinely tests badly, not on spec.

**(e) The one place a differentiated fill IS earned** is the disclosure chip at
the ≤460px endpoint (§7), tinted **only** while `aria-expanded="true"`, with
`rgba(var(--v-theme-accent), .14)` — the shipped `--hover-wash` value, and the
same value `DedupScopePill` uses for a standing state. Transparent when closed.
That is a **state tint, not a region tint**, which is the whole distinction.

---

## 3. Compression — by removal, not by shrinking

The status sentence is the one thing in the pill a user *reads* rather than
clicks, and it is about to sit beside more controls than before. Shrinking its
type is the wrong lever; removing its neighbours is the right one. **Nothing in
the status run goes below `--text-sm`.**

### Deleted

- **The `Searched {category} only` span.** Folded into the status sentence. It
  cost a second text run, a second gap and a second thing to read, to say what
  two words in the sentence carry — and duplicated what the presence of
  `Search everything` already implies.
- **The standalone `N Faces selected` span.** Folded into the menu trigger label.
- **`(N)` as the trigger label.** Replaced by `12 selected`.
- **`margin-right: var(--space-3)` on the assign button.** Its reason — a bulk
  write must not read as a peer of a dismissal — is a semantic boundary, and the
  pill now has a rule and a group gap for that.
- **`box-shadow: 0 -2px 4px rgba(var(--v-theme-shadow), 0.1)`** (off-ladder) and
  the ad-hoc `padding-right: 72px`.

### Added

**The query string.** Verified: nothing on screen today names what was searched —
`SearchResultBar` never shows it, the toolbar popover is closed, and only
`bar-btn--active` hints a search is live. A recognition-over-recall failure worth
fixing while the pill is rebuilt. Truncate to ~24 chars, full string in `title`.

### Status copy — one sentence, scope folded in

| State | Copy |
|---|---|
| Loading | `Searching…` (spinner) |
| Person search, 1 / N | `1 possible picture of Anna` / `14 possible pictures of Anna` |
| Similar faces | `12 similar faces` |
| Reverse image, 1 / N sources | `18 matches for this picture` / `18 matches for 3 pictures` |
| Text, all pictures | `42 matches for "sunset"` |
| Text, category scope | `42 matches for "sunset" in Landscapes` |
| Zero | `No matches for "sunset" in Landscapes` |

Type: numeral `--text-md` / `--weight-semibold` / `tabular-nums`; the rest
`--text-sm` / `--weight-regular` / `rgba(var(--v-theme-on-surface), .65)`.
Deliberately the same recipe as the shipped `.selection-face-count`, so the two
segments' counts read as siblings. Hierarchy by weight and colour, not a new size.

Other copy: `Search All Pictures` → **`Search everything`** (shorter, and
"Pictures" is already inaccurate — the library has videos). Delete emits
unchanged (`search-all`).

### Button weight — one accent, zero tonal

| Control | Today | In the pill | Why |
|---|---|---|---|
| `Assign N to {name}` | accent filled | **accent filled**, `--radius-sm`, 40px, `padding 0 var(--space-4)`, icon 18px, name `max-width: 14ch` ellipsis | The only bulk WRITE. One accent, spent once. |
| `Search everything` | tonal | quiet `.stack-btn` | Three filled buttons in a pill reads cheap. |
| `Clear search` | primary filled | quiet `.stack-btn` with label + `<kbd>Esc</kbd>` chip; icon-only at ≤680px | Demoting it is what frees the accent to mean something. It keeps its **label** at wide widths because that label carries the Esc keycap. |

Three recipes total, all already shipped: `.stack-btn`, the 40×40 quiet icon
button, and one accent fill. When `Clear search` does go icon-only, it uses
`mdi-magnify-close` — **not** `mdi-close` — so it cannot be mistaken for
`mdi-selection-off` sitting ~30px away across the seam. Different silhouette,
same MDI family, and it bookends the leading lens.

### Loading must not empty the pill

Today `imagesLoading` hides the threshold, the scope note and `Search all`, so
the pill collapses to `Searching…` and snaps back to full width — a layout jump
under a cursor already travelling toward `Clear search`. Requirement: keep the
controls mounted and `aria-disabled` while loading, and reserve the status width
with tabular numerals + `min-width`. **The pill's width never changes because of
a load.**

---

## 4. The vertical "Match at least" slider — rejected, on arithmetic

Both lanes independently reject it. The fatal number: the domain is 0.50→0.95 at
`step 0.01` = **45–46 discrete stops**. A vertical range inside a 40px band gets
~32–40px of thumb travel. That is **0.7–0.9px per step**. Today's horizontal
version gets 140–320px, i.e. 3–7px per step — already the low end of usable. A
1px hand tremor would move the cut 1–2 percentage points and jump the count by
tens. This is a 4–8× precision regression on the pill's most-dragged control.

Supporting reasons: target size and travel are in direct conflict (a native thumb
is ~16px; you cannot give it 24px of vertical extent per WCAG 2.2 SC 2.5.8
without eating most of the travel); a 32px fader between two 40px horizontal
controls reads as a piece of a mixing desk that wandered in; and the supported
platform route (`writing-mode: vertical-rl` + `direction: rtl`, Chrome 121 /
Safari 17.4 / Firefox 129 — `-webkit-appearance: slider-vertical` is removed) is
a fresh path with known thumb-sizing and `accent-color` quirks. Keyboard is *not*
an argument against vertical — arrows work either way — and it is not the
blocking reason.

### Instead: the label becomes the readout

Wide widths keep the slider **inline and horizontal**, compressed by deleting
words rather than travel: `Match ≥ 82% ———o———` where `Match ≥` is the `<label>`
and `82%` the `<output>`, one continuous run instead of label + gap + track + gap
+ output. `flex: 1 1 160px; min-width: 120px; max-width: 260px`.

The crowding is smaller than it looks: in person-search mode
`isAllPicturesActive` is forced true (`ImageGrid.vue:911-917`), so
**`Search everything` never co-occurs with the threshold.**

Keep `accent-color: rgb(var(--v-theme-accent))` and the existing
`:focus-visible { box-shadow: var(--focus-ring) }`. Do **not** hand-roll
`::-webkit-slider-thumb` / `::-moz-range-track` — `accent-color` is the one
property that behaves identically across engines.

### Below 780px, and always on coarse pointers: chip + popover

A `Match ≥ 82%` chip (`.stack-btn`, `mdi-tune-variant` 18px + the value at
`--text-base` / `--weight-medium` / `tabular-nums` / `min-width: 4ch`,
`aria-haspopup="dialog"`, `aria-expanded`, accessible name
`Match at least 82%`). Click/Enter only, never hover — this floats over a photo
grid. The popover opens **above** the chip: `--z-dropdown`,
`rgba(var(--v-theme-surface), .96)`, `--radius-lg`, `--elevation-3`,
`padding: var(--space-4)`, `width: 240px`, holding the label in the existing
`.section-label` recipe, the **same horizontal input** at 208–240px of travel
(4.5px/step — better than today's narrow case), `−`/`+` buttons at 1% each for
the fine-tune a slider cannot give, and the live count repeated inside so the
user needn't look past the popover.

The inline slider survives at wide widths because the sweep-and-watch-the-count
gesture *is* the feature, and hiding it behind a click is a real loss. Per §13 a
standing state compresses to its value and never disappears — which is what the
"a filter that hides is a filter the user forgets" rule actually protects.

**Rejected and named so they are not re-proposed:** widening `step` to 0.05
(changes the *data* to fit the chrome); a bare numeric spinner (loses the sweep);
a 5-step segmented preset (throws away resolution the ranked list has).

---

## 5. Motion — the seam animates, the pill's geometry does not

**The pill's width is not transitioned.** Two independent reasons:

1. `width: max-content` is not interpolable. `grid-template-columns: 0fr → 1fr`
   works but needs the segment to be a grid track; `interpolate-size:
   allow-keywords` is Chromium-only today, so half the users get a snap on a core
   control.
2. The killer: the pill is centred with `transform: translateX(-50%)`, so
   animating its width **moves its left edge too**, dragging the search
   segment's controls sideways under a live pointer — sliding `Clear search` out
   from under a click, next to `Delete` and `Assign`.

**The pill's height NEVER animates.** Height changes re-trigger
`useBottomAnchor`'s `ResizeObserver`, which drives `--floating-bottom-h`, the
notice stack's rest position *and* `ActionReceipt`'s measured lift — **per
frame**, up to 60 republishes in 200ms, re-targeting the notice stack's own
`--dur-2` transition every frame. Animating height animates three other surfaces
through a measurement loop.

What animates instead — the pill reflows once, in one frame, and the perceptual
cue rides entirely on compositor properties:

| Element | Property | Duration | Easing |
|---|---|---|---|
| Seam | `transform: scaleY(0) → 1`, `transform-origin: center` | `--dur-1` | `--ease-standard` |
| Entering segment | `translateX(8px) → 0` + `opacity: 0 → 1` | `--dur-2` | `--ease-decelerate` |
| Both, on leave | mirrored | `--dur-1` | `--ease-accelerate` |

The seam grows from its centre first, so the split is announced before the
content lands. Identical property pair, durations and in/out asymmetry as the
shipped `selbar-pop` — one motion vocabulary, three parameters (distance 8px not
120%, axis X not Y).

Rules: **only on the 0↔N transition**, never on a count change (12→13) — the
same discipline `selbar-pop` already keeps. Instant under
`prefers-reduced-motion`. And **suppress the inner transition during the outer
one**: when the pill appears already-expanded, `selbar-pop` owns the entrance,
and a nested slide inside a pill that is itself flying up from below reads as two
events. Gate the segment transition on the pill having been visible the previous
frame.

This matters most at the worst moment: `handleAssignFaceSearchResults` clears the
selection on success, so the pill contracts *while* the receipt appears and
lifts, exactly when the user may want Undo. Geometry-stable expansion keeps the
receipt's lift stable.

### What it means for `--floating-bottom-h`

- The pill's border box height is a constant **54 + 8 = 62px** for its whole
  lifetime, so the notice stack never shifts. Same for `ActionReceipt`, which
  lifts by `useAnchorHeight("selection-bar")`.
- **`flex-wrap: nowrap` is load-bearing, not a style preference.** The merged
  content is roughly double and the pill has `max-width: calc(100% -
  var(--space-6))`. One wrap = a ~40px height jump = the notice stack and the
  receipt both move mid-interaction. The ladder in §7 exists to make wrapping
  impossible above the floor. Needs a code comment **and** a test asserting
  height stability across an expand.
- Ownership of the variable transfers to the merged pill, still **measured**, not
  constant — it wraps at the floor and grows on coarse pointers.

---

## 6. Behaviour

### 6.1 Esc — the ladder already exists; only its visibility is broken

`useGridKeyboardNav.js:175-193` already implements the correct ladder. **No
behavioural change is needed.** What is wrong is that both buttons claim Esc in
their tooltips, so one of them lies whenever the other is on screen.

Order, one layer per press, innermost and most reversible first:

1. An open menu/popover inside the pill (Selection menu, Tag panel, threshold
   popover, plugin/ComfyUI panels) → close it, return focus to its trigger.
2. Selection exists → clear the selection. **The search stays.** The pill
   contracting is the feedback; no extra chrome needed.
3. No selection, multi-character/set-overlap view → clear the multi-selection.
4. No selection, search active → clear the search. The pill unmounts.

The Esc keycap belongs to exactly **one** visible control at a time — the one Esc
will actually hit — rendered as the `<kbd>` chip from `visual-language.md` §13,
with `aria-keyshortcuts="Escape"` on that control only (`aria-keyshortcuts` on a
button that will not get the key is a 4.1.2 lie).

| State | Clear selection | Clear search |
|---|---|---|
| Selection present | `Clear selection` + **Esc** chip + `aria-keyshortcuts` | `Clear search`, no chip, title `Clear search — press Esc twice, or click` |
| No selection | (absent) | `Clear search` + **Esc** chip + `aria-keyshortcuts` |

**Mandatory focus rescue** (new; `SelectionBar` has the same latent bug today).
When Esc collapses the segment holding focus, focus is dumped to `<body>` and a
keyboard user falls out of the tab order. Spec: if the selection segment unmounts
while it `contains(document.activeElement)`, move focus to the search segment's
first interactive element; if the whole pill unmounts, return focus to the grid
cursor cell, else the grid scroll wrapper. The merge moves the DOM and *will*
surface this. WCAG 2.4.3.

### 6.2 Action precedence and destructive adjacency

```
[ status ] [ Match ≥ 82% ▬▬o▬▬ ] [ Search everything ] [ Assign 14 to Anna ] ⟵--space-5--⟶ [ Clear search ]
  ‖ seam ‖
[ 12 selected ▾ ] [ Clear impossible tags ] [ Clear selection ] ⟵--space-5--⟶ [ Delete ]
```

**Assign lives in the search segment, always** — even though it retargets on
selection. It is the goal of person-search mode, and relocating a bulk-write
button across a seam on selection change would move it under a travelling
cursor. Only its label and count change.

**Exactly one accent-weight action visible at a time.** Assign is accent; nothing
else in the pill may be.

Must not be adjacent:

- **Assign ↔ Clear search** — keep the `--space-5` group gap and the reasoning in
  the existing code comment.
- **Assign ↔ Delete** — the pair the merge creates, and the most dangerous in the
  app: two bulk actions on the same eyeballed set, one adds to a person, one
  throws away. The layout puts `Clear search`, the seam and the menu trigger
  between them — maximum separation within the pill. **Pressure-release valve:**
  if a fifth control ever wants in, `Delete` is the one that leaves — it already
  exists in the `N selected ▾` menu, guaranteed by
  `e2e/specs/menu-parity.spec.js`. Hold the five-control ceiling; the pill does
  not grow.
- **Clear selection ↔ Delete** — adjacent today at `--space-3`, two identical
  40px transparent icon buttons, one destructive. Widen to `--space-5`. Keep the
  `error`-coloured icon.

Confirms — what is needed and what is not:

- `Delete selected` in non-scrapheap views moves to Scrapheap, records to the
  operation log and raises the receipt with Undo (`ImageGrid.vue:3462`). **No
  confirm** — undo beats confirm. In Scrapheap view it already routes through the
  tokenized type-to-confirm delete-forever. Correct as shipped.
- `Assign` records to the log and raises the receipt. **No confirm, with one
  exception.**
- **One new guard.** When `assignCount ≥ 50` **and** `assignFromSelection ===
  false` — "write everything above the threshold" — require an `AppDialog`
  confirm naming the number. It is a one-click write over a set the user has not
  individually inspected, and the receipt's dwell window is 5–8s while the grid
  refetches underneath; eight seconds to notice and reverse 312 character
  assignments is not user control. Under 50, or any explicit selection of any
  size, no confirm. Copy: title `Assign 312 pictures to Anna?`, body `They all
  match at 82% or better. You can undo this from the receipt.` **The threshold of
  50 is the one number here to validate against real usage rather than assert**;
  the principle (guard uninspected bulk writes above roughly one screenful) is
  what matters.

### 6.3 Telling the two counts apart

Four numbers currently sit near the word "selected" — search count, `(N)`,
`Assign N selected`, `N Faces selected` — plus `N people selected` in the
multi-select bar.

**Rule: the two counts differ by unit and by affordance, never by position.** The
search count states what was **found** and never uses the word "selected" or
"results" ("results" is a system word; "matches" and "pictures" are the user's).
The selection count states **selected** and is the only thing that may — and it
is a **label on a menu trigger**, not free text, so its affordance says "this is
a control's state". Static text vs control: different affordance, different
meaning.

| Element | Copy |
|---|---|
| Menu trigger | `12 selected` + chevron |
| Trigger title | `Actions for 12 selected — press S` / `…(47 total including stacks) — press S` |
| Faces only | `3 faces selected` |
| Both | `12 selected · 3 faces` |
| Clear selection | `Clear selection (Esc)` when Esc owns it, else `Clear selection` |
| Delete | `Move 12 to Scrapheap (Del)`; in Scrapheap: `Delete 12 forever (Del)` |
| Assign, no explicit selection | `Assign 14 to Anna` |
| Assign, explicit selection | `Assign 12 selected to Anna` |

That `Delete` rename is a correction beyond the merge's scope, taken now because
the button is being touched: the action is reversible and moves to a heap, and
calling it "Delete" mis-sets expectations in the more alarming direction.

**New clause, required.** The selection silently seizing the assign target is
correct behaviour with only a label change as its signal — and that change now
happens inside a pill that is simultaneously opening. When a selection exists and
`selectedCount ≠ matchCount`, show a `--text-xs` muted line in the search
segment: `Using your 12 selected, not all 14 matches.` The button's accessible
name carries the same sentence.

### 6.4 Live regions

Three shipped problems, then the rule.

- **`<output>` is already a live region.** Per HTML-AAM it maps to
  `role="status"`, so `.search-result-threshold-value` announces `82%` on **every
  pointer sample** of the drag, in parallel with the undebounced count region —
  roughly 40 announcements per drag.
- **The range announces the wrong unit.** `<output for>` is a reverse
  relationship ("these are my inputs"), not a labelling one, so the percentage is
  not in the slider's accessible name. A keyboard user hears `Match at least,
  slider, 0.82`.
- **The count region is not debounced**, though the grid rebuild is (200ms,
  `debouncedFaceSearchRecut`).

**Exactly one live region in the pill.** `role="status" aria-live="polite"
aria-atomic="true"`, permanently mounted for the pill's lifetime — never
`v-if`'d, because a region that mounts with content already in it announces
unreliably across SR/browser pairs. It carries the search segment's status
sentence and nothing else.

Announced: the status sentence on change (count, mode, query, scope), **debounced
300ms trailing** so a slider drag reads once; the threshold percentage folded
into that same sentence (`14 possible pictures of Anna at 82% or better`) rather
than spoken separately; the `Searching… → result` transition (the one thing a
non-sighted user cannot otherwise perceive).

Not announced, anywhere: the pill expanding/collapsing or the seam appearing
(pure visual events); the Assign label changing (it is a control — its name is
read on focus; `aria-live` on a control's own label is the anti-pattern the
architecture already calls out for the zoom button); the assign outcome
(`ActionReceipt`'s single app-wide `role="status"` already says `Assigned 12
pictures to Anna · Undo`).

Also required: `aria-live="off"` on the `<output>` (keeps the `for` semantics,
kills the redundant speech), and `aria-valuetext="82%"` on the range.

**The selection count is in no live region.** Announcing per-click selection
would make bulk selection unusable — every Space would speak a number over the
tile's own name. Instead the count lives in the trigger's accessible name (read
on focus), and only **bulk** transitions the user did not make item-by-item get
words: `Ctrl+A`, Esc-clear, a completed Shift-range. Those go through a
**separate visually-hidden `role="status"` owned by `ImageGrid`, not the pill**,
debounced 500ms, cleared to `""` after 3s so it never re-reads on an unrelated
re-render. Copy: `42 selected`, `Selection cleared. 42 matches still showing.`
This region does not exist today; it is a gap.

Double-speak audit — three live regions on the grid, disjoint by content, and no
single event writes to two:

| Region | Owns | Written by |
|---|---|---|
| `ActionReceipt` | what changed in the library | assign, delete, undo |
| Pill search status | what the search is showing | query, mode, count, threshold |
| Grid selection status | what is selected | Ctrl+A, Esc-clear, Shift-range |

### 6.5 Structure and labelling

- Outer container: plain `div`, **no role**. An unnecessary role is worse than
  none.
- Search segment: `<div role="group" aria-label="Search results">`.
- Selection segment: `<div role="group" aria-label="Selection actions">`.
- Both dividers: `aria-hidden="true"`. The **group boundary**, not the divider,
  is what a screen reader navigates by — which is exactly why the segments must
  be real groups and not styled runs.
- Each group's first child carries its number (the status sentence; the trigger
  label), so entering either group announces its count. Symmetric, and neither
  needs a live region for it.

Gaps to close on the menu trigger while it is being touched: it has neither
`aria-haspopup="menu"` nor `aria-expanded` today, which gives a screen-reader
user no signal it opens anything. Add both, plus `aria-keyshortcuts="S"`.

---

## 7. Keyboard, focus, responsive ladder

### Keyboard map (grid focused) — no new global key

| Key | Action | Change |
|---|---|---|
| `F` | Open the search popover (toolbar) | unchanged |
| `Esc` | The 4-step ladder in §6.1 | behaviour unchanged; the keycap now moves |
| `Del` / `Backspace` | Delete selected | unchanged |
| `S` | Toggle the selection menu | unchanged |
| `ArrowDown` | Menu open, focus outside panel → first item | unchanged |
| `Ctrl+A` | Select all in the result set | + announces via the grid region |
| `T`, `1`–`5`, `0`, `Ctrl+Z` | Tag, score, undo | unchanged |
| `F1` | Cheat sheet | **the Esc row must be corrected** |

Threshold when focused, all native: `←`/`↓` −1%, `→`/`↑` +1%,
`PageUp`/`PageDown` ±4.5%, `Home`/`End` min/max. In the popover, `−`/`+` at 1%,
`Esc` closes and returns focus to the chip.

**Do not bind a key to Assign.** A single-key bulk write is the capture-slip
failure `toolbar-responsive-decisions.md` amendment #3 documented in the dedup
queue. Assign stays a deliberate pointer, or Tab-then-Enter, action.

Documented interaction, so nobody "fixes" it later: while the threshold has
focus, `S` and `ArrowDown` belong to the slider, not the menu —
`isEditableElement` returns true for `INPUT`. That is the platform rule and it is
correct.

**The F1 dialog currently lies** — its Esc row says only "Clear selection".
Replace with `Close menu, then clear selection, then clear search (one step per
press)`.

### Focus order

DOM order = visual order = tab order, left to right, search then selection. No
positive `tabindex`. The pill stays after the grid in DOM order.

Worst case is **8 tab stops** (threshold, `Search everything`, Assign, `Clear
search` ‖ trigger, `Clear impossible`, `Clear selection`, `Delete`). **That is the
correct answer, and the APG toolbar pattern — one tab stop plus roving arrows —
is wrong here**: the segments contain a range input whose arrows change its value
and a menu button whose `ArrowDown` enters the menu. Collapsing to one stop
steals both. Eight plainly ordered stops beats one stop with two broken controls.

Regression to test: the `hidden-panel-activator` divs (Tag / Filters / ComfyUI)
are `aria-hidden` + `pointer-events: none` and unfocusable. The merge moves the
DOM around them, and a zero-size focusable div is an invisible tab stop — WCAG
2.4.3 / 2.4.7.

### Responsive ladder (`@container selbar`, declared on `.grid-content-area`)

Width budget at the 40px band: search segment fully loaded ≈550px, selection
segment ≈540px, plus 26px of pill chrome ≈ **1115px** worst case. At 1440px with
both rails open, `.grid-content-area` is ≈912px. The ladder is not optional. It
also correctly reacts to the stats sidebar opening, since the container tracks
grid content width rather than the viewport.

| Step | What gives way | Saves |
|---|---|---|
| **≤1100px** | `.clear-impossible-label` (the shipped 660px rule, moved earlier); Assign's `to {name}` tail → `Assign 128`. Both already carry `title`. **Assign never goes icon-only** above the floor — a bulk write states its blast radius. | ~210px |
| **≤900px** | `Search everything` → icon + `title`/`aria-label` | ~60px |
| **≤780px** | The threshold folds into the `Match ≥ 82%` chip + popover | ~120px |
| **≤680px** | `Clear search` → `mdi-magnify-close` + `title`. Its Esc hint survives in `title` / `aria-keyshortcuts` only; an icon button cannot wear a legible `<kbd>`. | ~90px |
| **≤560px** | The status sentence truncates to `42 matches`, full sentence in `title` and in the live region. **`12 selected` never truncates** — a count is the blast radius of everything in its segment. | ~50px |
| **≤460px** | **Endpoint: the search segment yields; the selection segment stays first-class.** | — |
| **Floor, ≤380px** | `[🔍 42 ▾] ‖ [12 selected ▾] [✕] [🗑]` — 4 targets ≥40px, ≈260–300px, fits a 360px viewport inside the existing `max-width`. Below that the pill may wrap as it already can, and `useBottomAnchor` re-measures so notices and the receipt stay clear. | — |

Why the **search** segment yields at 460px: a selection is the more perishable
and more dangerous state to leave unexplained; the search's existence is
recoverable from the toolbar's `bar-btn--active` while a selection has no other
indicator at all; and the selection segment's contents are irreplaceable (the
only route to bulk actions on touch), whereas the search's have alternates (the
toolbar popover clears the query; Esc-Esc still works).

Nothing becomes unreachable. The search segment degrades to a leading disclosure
chip — `mdi-magnify` + `42`, `aria-label="42 matches for sunset in Landscapes.
Show search options."` — opening a popover (same recipe as the threshold one)
that holds the whole segment as a vertical list at comfortable target sizes:
full sentence, threshold at full travel with its real label, `Search
everything`, **Assign intact with its label and count**, `Clear search`.

**Rejected:** two stacked pills (the thing we just merged, and it doubles
`--floating-bottom-h`); wrapping to two rows above the floor (breaks the height
contract, moves the notice stack and the receipt).

**Coarse pointers.** `.grid-content-area` already bumps `--selbar-height` to 56px
there and §2.2 notes the pill grows. Controls go to 48px (`--bar-height`, and the
touch-target floor), making the pill 62px. Rather than a parallel set of touch
breakpoints, add one `@media (pointer: coarse)` block that unconditionally
applies the ≤780px rules — so the threshold is **always** a chip + popover on
touch. Dragging a 40px inline slider with a finger over a photo grid is a
mis-hit generator. Simple, and it fails in the safe direction.

---

## 8. Tokens

**Used, all existing:** `--space-1`…`--space-6`; `--radius-sm`, `--radius-lg`,
`--radius-pill`; `--text-2xs`, `--text-xs`, `--text-sm`, `--text-base`,
`--text-md`; `--weight-regular`, `--weight-medium`, `--weight-semibold`;
`--leading-snug`; `--tracking-label`; `--elevation-3`; `--focus-ring`;
`--dur-1`, `--dur-2`; `--ease-standard`, `--ease-decelerate`,
`--ease-accelerate`; `--z-floating` (pill), `--z-dropdown` (both popovers);
theme keys `surface` / `on-surface`, `border`, `accent` / `on-accent`,
`primary`, `error`.

**Retired from the search half:** every `on-panel` reference. The pill's
background is `rgba(surface, .86)`, so `on-panel` becomes `on-surface` or it
reads as a slightly-wrong grey. Specifically `.search-result-scope` (.6 →
`rgba(on-surface, .65)`), `.search-result-threshold-label` (.7 → the
`.section-label` recipe inside the popover), `.search-result-threshold-value`
(.7 → full-strength `on-surface`; it is a button label now, not a caption).

**Two new tokens proposed** — both rendered lengths, not gaps, so they cannot
borrow from the spacing scale (the `--countdown-h` precedent):

```css
/* Vertical hairline inside a control band. 24px is already the de-facto value:
   .bar-separator in Toolbar.vue. */
--rule-h: 24px;
/* The taller variant, for a boundary between two CONTEXTS in one container.
   One consumer today: the merged pill's search|selection seam. */
--rule-h-seam: 32px;
```

Nothing here adds a colour, radius, duration or font size. `--floating-bottom-h`
and `--selbar-h` stay runtime layout variables in `style.css` per §11.

---

## 9. Two shipped bugs found on the way

Both are preconditions for this design looking and behaving correctly, and both
are cheap.

1. **`color-scheme: light dark` is declared on `:root` only** (`style.css:6`).
   The native range **track** — the part `accent-color` does not paint — plus
   native selects, checkboxes and scrollbars therefore follow the **OS**
   preference, not the chosen PixlStash theme. Run the app's dark theme on a
   light-mode OS and you get a light slider track inside a dark pill. Fix:
   `color-scheme: light` on `.v-theme--pixlStashLight`, `color-scheme: dark` on
   `.v-theme--pixlStashDark`, beside the existing `--hover-wash` blocks. Two
   lines, no new token.
2. **`Toolbar.vue`'s `.bar-separator` uses an ad-hoc `rgba(on-background, 0.2)`**
   where `rgb(var(--v-theme-border))` is the authored token. Migrate
   opportunistically, not as part of this change.
3. **`surface-variant` is not authored in either PixlStash theme.** Neither
   `pixlStashLight` nor `pixlStashDark` defines it, and Vuetify's
   `parseThemeOptions` does `mergeDeep(defaultTheme, theme)`, so both silently
   inherit the **stock** values: `surface-variant` `#424242` light / `#a3a3a3`
   dark, `on-surface-variant` `#EEEEEE` / `#424242`. Cold neutral grey — the
   thing `visual-language.md` §4 bans — never audited against this palette. It is
   already live as a **solid** fill: `.multi-select-toolbar`
   (`ImageGrid.vue:8755`) is a `#424242` dark-grey 36px bar with near-white text
   sitting inside the light theme. Four more sites use it as a low-alpha wash
   (`SideBar`, `FolderEditor` ×2, `TaggerParametersUI`), and `CharacterEditor`
   has a `var(--v-theme-surface-variant, 127 127 127)` fallback — the tell that
   its author was not sure the key existed. Belongs in the drift audit; do not
   build anything new on it. This is the same component as blocker #1 in §10, so
   the two get fixed together.

---

## 10. Risks, prioritised

1. **`.multi-select-toolbar` is not in the merge's brief and will survive it** —
   still `bottom: 0`, z 300, full width, still painting over whatever the new
   pill puts at the bottom edge in union/overlap views, still registering no
   bottom anchor, and still at an off-ramp `font-size: 13px`. Fold it into the
   same owner or the merge fixes two of three collisions. **Blocker.**
2. **Focus loss when a segment unmounts under Esc** (§6.1). Today's latent bug,
   guaranteed to surface. **Blocker; WCAG 2.4.3.**
3. **`<output>`'s implicit `role="status"` double-speaking** with the count
   region, ~40 announcements per drag. High; shipped defect.
4. **Any height animation cascading through `useBottomAnchor`** into the notice
   stack and the receipt lift. Height must be static. High.
5. **`Assign` ↔ `Delete` adjacency** if the §6.2 ordering is not followed
   exactly. High. This is the one thing in the merge that actively costs users:
   the pill is now the *only* bottom-edge chrome, so the eye and the pointer live
   there permanently.
6. **`--floating-bottom-h` ownership must transfer to the merged pill** and stay
   measured, not constant. High.
7. **`flex-wrap: nowrap` needs a test**, not just a comment — a wrap regression
   is invisible until it silently moves the notice stack and the receipt.
8. **The delete button moves.** With a search active, the pill's centre now falls
   inside the search segment, so `mdi-delete` translates right by ~275px. Real
   muscle-memory cost. Accepted rather than mitigated: the Clear/Delete pair
   stays together and last, which is the relationship that carries the memory,
   and Delete is reversible via the receipt. The rejected alternative
   (asymmetric centring) makes the left edge content-dependent and reintroduces
   the moving-target problem. Putting the **selection segment first** remains
   available if observation shows the cost is real — it protects the memory and
   costs the reading order.
9. **The selection silently seizing the assign target**, now inside an opening
   pill. Needs the `Using your 12 selected…` clause. Medium.
10. **Uninspected bulk assign at scale** vs an 8-second undo window (§6.2).
    Medium.
11. **Invisible tab stops** from the hidden panel activators after the DOM move.
    Medium; regression test.
12. **F1 cheat sheet already wrong about Esc**, and more wrong after the merge.
    Medium.
13. **Component structure.** `SelectionBar.vue` is already 1023 lines; do not
    merge the search markup into it. Introduce a `panels/GridActionPill.vue`
    shell that owns the pill surface, the seam, the ladder and the single
    `useBottomAnchor` registration, hosting two segment components. A
    frontend-architecture call for `senior-frontend-developer`; it will need
    `SearchResultBar.test.js` and `e2e/specs/menu-parity.spec.js` updated.

---

## 11. Resolved conflicts between the two lanes

| Question | Visual lane | Usability lane | Resolution |
|---|---|---|---|
| Vertical threshold slider | reject (0.7px/step, optics, platform risk) | reject (0.9px/step, target size vs travel) | **Rejected.** Horizontal inline at wide widths, chip + popover ≤780px and always on coarse pointers. |
| Threshold visible or in a popover | popover always (frees ~130px) | inline while there is room; hiding loses the sweep gesture | **Usability wins** — the popover is the *narrow and touch* form, not the default. The visual lane's popover spec is used for that form. |
| `Clear search` weight | demote to a 40×40 icon immediately | keep the label so it can wear the `<kbd>Esc</kbd>` chip | **Usability wins** — quiet `.stack-btn` **with** label + keycap, icon-only at ≤680px. Satisfies the "one accent only" rule either way. |
| The scope note | fold it and `Search All Pictures` into one clickable scope chip | delete it; fold the scope into the status sentence, keep `Search everything` as its own state-conditional button | **Usability wins** (it owns copy and flow). Fewer controls, and the sentence carries the fact. |
| Expansion animation | do not transition width at all; animate seam + segment | width may transition at `--dur-1`; height never | **Visual lane's technical argument wins on width** (`max-content` is not interpolable, and a centred element's left edge moves). Both agree height never animates. The perceived expansion rides on the seam's `scaleY` and the segment's fade-in. |
| Segment order | flagged the ~275px delete-button move, deferred | search first (state → set-shaping → actions → dismissal) | **Search first**, with the move accepted and logged as risk #8; selection-first stays the named alternative. |
| Dropping `N faces selected` at ≤760px | proposed | it folds into the trigger label instead (`12 selected · 3 faces`) | **Folded, not dropped** — the count survives at every width. |

### 11.1 Two-tone segment fills — proposed by the user, rejected

The request: give the two halves distinct background colours, the search half
always the same, the selection half different. Ruled on by `lead-designer`.
**Rejected**, and the reasons are measured rather than aesthetic:

1. **The fill you would need *is* a state wash, by the system's own tuning.** An
   `accent` tint at 12% composites to `#f7ebdc` on light `surface` and `#36322d`
   on dark, a contrast step of **1.175:1 / 1.165:1** against the untinted half.
   `visual-language.md` §11 tunes the hover wash so its step lands at ≈1.10
   (light) / ≈1.26 (dark) — so a 12% region tint sits **inside the
   hover-to-selected band in both themes**. Across 25+ shipped call sites a
   tinted surface in PixlStash means *"this element is in a state"*:
   `--hover-wash` `rgba(accent,.14)`, `--active-wash` `rgba(accent,.20)` /
   `rgba(primary,.26)`, `rgba(error,.08–.12)` for destructive zones,
   `rgba(warning,.12)` for caution, `rgba(primary,.06–.22)` for applied tags. A
   standing tint on the half that holds `mdi-delete` is a false state signal on
   the destructive region.
2. **The reliable alpha and the legible alpha do not overlap.** The pill is 86%
   opaque over `blur(12px)` of arbitrary photography, so 14% of the photo bleeds
   through — a worst-case 0.14 absolute luminance swing across the pill's width,
   the same order of magnitude as a 12% tint's own 0.156 step. Over an image
   bright on one side and dark on the other the two-tone **cancels or inverts**;
   typical photos swing less, which is worse than never working, because an
   intermittent boundary is one the eye learns not to trust. Beating the bleed
   reliably needs ~20% alpha — and the `--text-sm` secondary run at
   `rgba(on-surface,.65)` measures **5.04:1** untinted, **4.72:1** at 12%, and
   crosses the 4.5:1 floor at **≈20%**. Disjoint windows, the same shape as the
   action-fill arithmetic in §4 of the manual.
3. **It re-draws the boundary the merge exists to remove.** Area colour is the
   longest-range channel in the box, and this system explicitly reserves it ("the
   photos are the colour on the screen", §9). Spending it to re-assert a division
   a 1px hairline already carries works against the premise.
4. **The second fill has no clean end.** Inside `--radius-pill` it either takes
   the trailing 27px semicircular cap — a silhouette that reads as *a separate
   pill fused on*, manufacturing the "two merged widgets" impression the merge is
   meant to prevent — or insets, which does not fit a 40px control band inside a
   40px inner height. And the pill's `1px rgba(on-surface,.14)` border is one
   stroke around one shape: at the junction you get a T-intersection of two
   differently-coloured 1px lines at the top and bottom edges.
5. **It costs the popovers.** A capped fill needs `overflow: hidden` on the pill,
   which clips the threshold and disclosure popovers unless they are teleported —
   and teleporting escapes the `selbar` container, the exact case
   `toolbar-responsive-decisions.md` already ruled on ("teleport escapes the
   container so the rows could not share the bar's container queries").
6. **It fights the motion spec.** A coloured block fading up reads as *a surface
   arriving*, which is the grammar of a loading skeleton (§9 mandates skeletons,
   so the vocabulary is live) and of `--active-wash` on a grid tile — two false
   readings fired at the moment the user is parsing a new state. It also passes
   through ~6% alpha at the halfway point of `--dur-2`, so the selection half
   looks hovered for ~100ms. And it kills the seam's `scaleY` cue: a line growing
   from its centre is legible because it appears on a uniform field; along an
   already-different fill it reveals nothing.
7. **It does not survive the ladder.** The halves' proportion slides from roughly
   50/50 to 25/75 as the container narrows, so a two-tone split reads as a bug at
   the intermediate widths; and at the endpoint the search half is a single chip,
   so a "region tint" would contain one control — a tinted button. Needing a
   different mechanism at the width where the glance-parse matters most is the
   argument for using that mechanism everywhere.

Also withdrawn during the analysis: an expected light/dark asymmetry forcing two
authored alphas. Measurement says one alpha would work in both (1.175 vs 1.165).
That objection does not stand; the seven above do.

**Adopted instead:** §2.1 — two counts as landmarks, one identity glyph per half,
and the seam's gutter widened to 16px each side (32px across, 4× the pill's 8px
internal rhythm). Zero new tokens, against the two-tone's cost of a new authored
theme key per theme, a new alpha, an `overflow`/teleport workaround, and 60% of
the secondary text recipe's contrast headroom.

**If the decision is ever revisited**, `lead-designer`'s conditions for speccing
it properly: `primary` **not** `accent` (a solid-`accent` `--focus-ring` on an
amber field is the failure §11 calls a defect), **10% alpha maximum**, the fill
takes the trailing cap, the seam hairline **stays**, and it is reviewed over real
photography in both themes before merge — because the photo-bleed inversion is
the one point arithmetic alone cannot settle.

### 11.2 Open: the responsive endpoint width

`lead-designer` put the search-half endpoint at **≤560px**, `ui-ux-expert` at
**≤460px**; §7 records 460. If 460 is kept, the ladder needs one more step
between 560 and 460, and the designer's recommendation for it is to move the
Selection trigger's `(N)` into `title` **before** touching `Clear` or `Delete`.
Which is correct is UI/UX's call, not the visual lane's, and it is unresolved.

---

## 12. What was built, and what was not

Built and covered by tests (`GridActionPill.test.js`, `SearchResultBar.test.js`,
`useGridKeyboardNav.test.js`): the one-surface merge and its single bottom
anchor; the seam and its 32px gutter; the geometry-stable expand; the status
sentence with the query named and the scope folded in; the compressed button
weights (one accent, zero tonal); the `Match ≥ NN%` inline threshold with its
chip + popover form below 780px and on coarse pointers; the single debounced
live region with `aria-live="off"` on the `<output>` and `aria-valuetext` on the
range; the `12 selected` trigger with `aria-haspopup` / `aria-expanded` /
`aria-keyshortcuts`; the Delete rename and its group gap; the focus rescue; the
responsive ladder; the `color-scheme` fix (§9.1); and the `--rule-h` /
`--rule-h-seam` tokens.

**One thing was implemented differently from the spec.** §6.1's table gives the
Esc keycap to `Clear selection` whenever a selection exists, but that control is
a 40×40 icon button and cannot wear a legible `<kbd>` chip — the same reason §7
drops the chip from `Clear search` at ≤680px. So while a selection is live the
keycap is *absent* rather than moved: `Clear selection` carries
`aria-keyshortcuts="Escape"` and the title `Clear selection (Esc)`, and
`Clear search` drops its chip and says `press Esc twice`. Exactly one control
claims Esc at a time, which is the property that mattered; the visible chip is
the part that degrades. Giving the selection half a labelled Clear button would
restore it, at the cost of pill width.

**Not built, each needing a decision this implementation had no mandate to
make:**

1. **`.multi-select-toolbar` still owns part of the bottom edge** (§10 blocker
   #1) — `bottom: 0`, z-index 300, full width, no bottom anchor, off-ramp
   `font-size: 13px`, and a solid `surface-variant` fill that is Vuetify stock
   cold grey (§9.3). It now paints over the merged pill in union/overlap views,
   exactly as it did over the old search bar. It is a **view-mode** control
   (union / overlap / difference / XOR + base picker), not a set of actions on a
   selection, so folding it into the action pill is not obviously right — the
   likelier answer is that it belongs at the *top* of the grid content area
   under the toolbar. That is a design call for `ui-ux-expert`, not a mechanical
   fix.
2. **The `assignCount ≥ 50` confirm** (§6.2). The dialog copy is specified, but
   the threshold of 50 is explicitly the one number the spec says to validate
   against real usage rather than assert.
3. **The grid's own selection live region** for bulk transitions (`Ctrl+A`,
   Esc-clear, a completed Shift-range) (§6.4). It is a new region owned by
   `ImageGrid`, not by the pill, and it needs its own double-speak audit against
   the receipt.
4. **The F1 cheat sheet's Esc row** still says only "Clear selection" (§7). The
   ladder it should describe is now correct in code, so this is a copy fix
   waiting on the cheat sheet's own review.
5. **The endpoint ladder step** between 560px and 460px (§11.2) — unresolved
   above, so the shipped ladder stops at the 560px step and does not yet
   implement the search-half collapse to a disclosure chip.
