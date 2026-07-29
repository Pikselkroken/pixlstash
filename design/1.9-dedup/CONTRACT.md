# Dedup → Stacks — implementation contract (v1.9, routed 2026-07-29)

Authority: the owner's "Deduplication → Stacks" card in the PixlStash Design
System (`ui_kits/app/dedup-stacks.html` + `dedup-stacks.babel.js`; exact copies
sit next to this file). This lifts the master plan's Loop 1 design gate. Where
the design and previously shipped backend disagree, **the design wins**; the
deltas are listed explicitly below. The design's own §7 "Rules" block is the
handoff checklist — implement it verbatim unless a line below overrides it.

## The feature in one paragraph

Duplicate detection becomes a sidebar **Duplicates** destination with a live
to-do count. Detection is tiered: exact matches from an indexed hash query
(~ms, always on), near-dupes from perceptual hashes compared **only within
candidate buckets** (same dimensions / capture minute / import batch / folder,
streamed as buckets finish), and embedding similarity as an opt-in background
tier reusing the existing likeness data. The queue shows group rows (one
focused, keyboard-driven: Enter stack · S keep separate · C compare · 1–9 set
cover · X exclude candidate · Ctrl+Z undo, auto-advance). The only verdicts
are **stack** or **keep separate** — no deletion anywhere in 1.9. Exact
matches go through a bulk auto-stack dialog (one batch op, one Ctrl+Z).
Verdicts persist keyed on group signature so rescans never re-ask. The
"Similarity to …" / Likeness-Groups sort order is removed from the sort menu,
with a one-time notice pointing at the new sidebar entry.

## Design-over-shipped-backend deltas (backend adapts)

1. **Cover selection formula** replaces the shipped planner's keeper order
   (score → smart score → recency): `pixels×4 + tags×3 + userScore×2 + RAW
   bonus`, ties break to **oldest capture time**. Always a visible
   preselection the user can override (1–9); never silent.
2. **Policy model** replaces `SweepPolicy`'s auto/review split semantics with
   tier gating: Tier 1 exact is always included and cannot be switched off;
   each looser tier is a separate opt-in with its own live count and enabling
   one requires the tier above it; near-dupe threshold default **0.90**;
   below **0.65 nothing is suggested at all** (hard floor). The shipped
   dry-run/report API and non-destructive planner remain the foundation —
   rework, don't discard.
3. **Tier 1 (exact hash) and Tier 2 (bucketed perceptual hash) are new.**
   The shipped planner covers only the embedding tier. Investigate what hash
   columns exist (e.g. metadata/content hashes) and document which one Tier 1
   uses; if a new column is needed it follows the conditional-migration rules
   and backfills via the finder/task system, incrementally on import.
4. **Verdict memory is a new table**: verdict keyed on a group signature
   (sorted member content hashes). "Keep separate" is permanent until the
   user reopens it from the Stacks view. Re-imports and rescans never re-ask.
   This is what makes the sidebar count trustworthy.
5. **Metadata union on stacking**: stacking unions tags, characters and set
   membership onto the stack and takes the highest score. Nothing is
   overwritten or lost. (Reconcile with existing stack semantics; if current
   "Stack groups" behaviour differs, the design's union wins.)

## Performance rules (design §1, binding)

- Never block on a full pass: the queue opens with whatever has been found,
  with a "scanned N% of M" banner streaming.
- Queue is virtual: one group in the DOM, prefetch next group's thumbnails
  only; group list paged from the DB by confidence descending, never loaded
  whole. 10 groups and 10,000 must perform identically.
- Scoped scans (project/set/character/folder context menu "Find duplicates
  in…" with live count) reuse cached hashes and return instantly; queue opens
  with a dismissible scope pill.

## Undo integration (binding)

Every verdict raises the standard action receipt and lands in the operation
log (stacking is already a recorded facet). Bulk auto-stack coalesces into a
single batch id, so N stacks reverse with one Ctrl+Z. The frontend consumes
the shipped `useOperationStore` / `ActionReceipt` from the undo lane.

## UI inventory (frontend lane)

Sidebar Duplicates row (live count badge, spinner while scanning); scan
banner; queue rows (focused row = accent bar + caret + tinted bg + filled
Stack button + "keyboard acts here" label; thumbnails at grid scale carrying
NO metadata; why-pills: olive check = matching evidence, red × = evidence
against, e.g. "different resolution", "subject moved"); Compare view (= issue
#156: every candidate field-by-field, best value highlighted per column, full
paths, Stack / Keep separate in its footer); auto-stack dialog (dry-run counts,
the single consent); stacks in the grid (count badge, edge ticks, expansion
strip with cover marker, grid-view expand-stacks toggle); Filters popover
gains a Stacks segment row (Any / Stacked / Unstacked / Unresolved
duplicates) with the choice spelled out and live match count; context-menu
scoped entry + scope pill; done-state; one-time sort-menu migration notice.
File paths shown ONLY for reference-folder pictures. Confidence pill per
group (`exact` styled distinctly). No em-dashes in shipped copy; design
tokens only; the design's mock CSS maps onto repo tokens.

## Explicitly out of scope for 1.9

Deletion / dehydration (v1.11), auto-at-import policy setting (plan item 6 —
only if trivially cheap once the finder exists), Immich-style per-group
mandatory adjudication of exact matches.
