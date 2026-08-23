# v1.11 "Your existing library" — design loop, pass 1

Ten artboards, two pages. Canvas URL in `LINK.md`. Rebuild with
`python3 build.py` then re-seed with the `/design` skill's `seed-canvas.mjs`
against the artboard list and `canvas.json`.

## Chosen

**Direction A, the tree.** Assign an entity to a whole level, override per row.
The deciding argument is that two folders at the same depth can legitimately
mean two different things, and only a tree can say so per row.

**Rejected: the pattern formula.** The 2–3 repeating path shapes as assignable
formulas, six decisions instead of 341. Scales better and reads faster, but a
position in a path can only carry one meaning, so per-folder overrides are
inexpressible. Dropped after the first review.

## The rule the release is built on

**A picture moves only when its folder stops being true.** Not whenever
something about it changes.

| Action | Folder still true? | Files moved |
|---|---|---|
| Import an existing library | true by construction | **none, ever** |
| Add a second project or person | yes | none |
| Rename a project | yes, under a new name | none — the **folder** is renamed |
| Remove the project its folder is named after | no | moves |
| Swap one project for another | no | moves |

Three consequences fall out rather than being designed in:

1. **Import moves nothing.** The assignments are derived *from* the paths, so
   every path is true the moment it is written.
2. **Many-to-many stops mattering.** Nothing is ever re-derived, so a picture in
   three projects never needs a winner picked after import.
3. **A folder outside the layout contradicts nothing**, so it never moves. That
   makes "drag it somewhere of your own" a permanent override needing no
   setting.

Accepted cost: the tree is never *wrong* but drifts from what the owner would
have picked. Hence **Move to match** as an offered action on a picture, never
automatic.

### The mirror, for moves made outside PixlStash

PixlStash changes an assignment only when the owner's move makes it untrue.
The ambiguity is that leaving a project's folder cannot distinguish "left the
project" from "refiled the picture", because a folder holds a picture once and
a project can share it.

Resolved on measurement, not taste. Across the owner's four real libraries
(~59,000 pictures): 91–100% of assigned pictures have exactly **one** project or
set, and nothing anywhere is in more than three. So the move is unambiguous for
almost all of them and is applied; the few with several are listed and left
alone until asked.

The one facet that genuinely breaks is **people, and only in photo libraries**:
0% multi-person in all three generation libraries, 22.4% in `family-images`.
That number only mattered under a re-derive rule. Under move-when-false it does
not, which is why Person survives as a path segment.

## Also decided

- **Person / People**, never Character. `character` is the model name only; the
  shipped UI says People.
- **"Just a folder"** replaced "Leave alone", which implied the *files* would be
  untouched. Every option leaves the files untouched. This one says the name is
  not telling us anything.
- **Managed libraries are gone as a concept.** A PixlStash folder is a
  referenced folder that starts empty, defaulting to `Project / Person or Set`.
  Today's flat libraries need no migration: files at a library root match no
  layout, contradict nothing, and stay put.
- **A layout segment can hold alternatives** (`Person or Set`), first match
  wins, and a segment with nothing to fill it is skipped. That keeps the tree
  two deep rather than five.
- **No hidden `.pixlstash-images` folder.** Pictures arriving somewhere the
  owner cannot see them is the opposite of help for a curated library.
- **Ask for an empty library, point at a folder that is not empty → two ways
  forward only**: bring them in, or pick a different folder. "Start empty in
  here anyway" is a trap.
- **Telemetry is not redesigned.** `TelemetryConsentDialog` already asks on
  first startup and is good. It appears on the first-run artboard only to fix
  the order: the question first, then the folder.
- **Settings › Libraries is drawn as the dialog it lives in** — 820px with the
  nav rail — which is why the list is compact rows rather than a table.

## Open, for pass 2

- **The fixture pack.** Every figure and folder name is placeholder except the
  membership counts. Real tree, real counts, real thumbnails, per the loop
  protocol.
- **The drag interaction**, made operable by keyboard. Pass 1 is static.
- **Keyboard keys.** Digits 1–4 and 0, because Project and Person both want P.
- **The Views spike.** Windows symlinks need admin or Developer Mode; the
  fallback is hard links, which cannot span drives, and exFAT has neither.
  Views is the release's flex candidate if that spike fails.
- **Duplicate facets in the layout builder.** `Project / Person or Set /
  Person or Set` is expressible and meaningless.
