// Shared copy for "this picture set is locked" explanations across the review
// UI. Extracted from NewReviewDialog.vue's `lockedOptionTitle()` when the tag
// health board grew its own locked-scope state: the two surfaces must say the
// same thing in the same voice, and a second hand-written string would drift.
//
// Convention (kept from the dialog): name the SET, state the CAUSE (its
// pictures are read-only), then the REMEDY (unlock it to review its tags).

// Headline for the board's terminal locked-scope state. Deliberately the
// user's own words for the situation, not jargon.
export const LOCKED_SET_HEADLINE = "Picture set is locked";

// One-sentence cause + remedy. Used as the locked option/trigger tooltip in
// NewReviewDialog and as the body copy of the board's locked state.
export function lockedSetTitle(name) {
  return `'${name}' is locked - its pictures are read-only. Unlock it to review its tags.`;
}

// --- Decision-card lock copy --------------------------------------------------
//
// The review queue can serve a card whose SUSPECT or whose TWIN sits in a locked
// set. The two block different decisions (accept/dismiss write the suspect;
// fix-twin/swap write the twin), so the strings below take the locking set names
// and the SIDE that is frozen. This module is the only source of lock strings in
// `reviews/` - never hand-write one at a call site.

// Normalise the payload's `locked_sets` / `twin_locked_sets` entries
// (`[{id, name}]`, already sorted by id) - or a plain array of names from
// `useLockedSetsStore` - to a list of non-empty name strings.
export function lockedSetNamesOf(entries) {
  if (!Array.isArray(entries)) return [];
  return entries
    .map((e) => (e && typeof e === "object" ? e.name : e))
    .filter((n) => n != null && String(n).length > 0)
    .map((n) => String(n));
}

// "'Holiday 2019'" / "'Holiday 2019', 'Archive'". Empty string when unknown, so
// callers can fall back to set-less wording rather than printing "''".
function joinLockedSetNames(names) {
  const list = lockedSetNamesOf(names);
  if (!list.length) return "";
  return list.map((n) => `'${n}'`).join(", ");
}

function remedy(names, verb) {
  const joined = joinLockedSetNames(names);
  if (!joined) return `Unlock the set to ${verb}, or Skip to move on.`;
  const it = lockedSetNamesOf(names).length > 1 ? "them" : "it";
  return `Unlock ${it} to ${verb}, or Skip to move on.`;
}

// The persistent chip on the decision bar: what is frozen, and the way out.
export function lockedDecisionChipLabel(names) {
  const joined = joinLockedSetNames(names);
  return joined
    ? `Locked by ${joined} - Skip to move on`
    : "Locked - Skip to move on";
}

// Announced (and shown) when a blocked decision is pressed. `side` is which half
// of the card is frozen: 'suspect' | 'twin' | 'both'.
export function blockedDecisionMessage(names, side = "suspect") {
  const joined = joinLockedSetNames(names);
  const where = joined ? ` in the locked set ${joined}` : " in a locked set";
  if (side === "twin") {
    return `Can't decide - the other version is${where}, so this decision can't be written to it. ${remedy(names, "decide")}`;
  }
  if (side === "both") {
    return `Can't decide - both versions are${where}, so this decision can't be written. ${remedy(names, "decide")}`;
  }
  return `Can't decide - this picture is${where}, so its tags are read-only. ${remedy(names, "decide")}`;
}

// Tooltip for the pane whose lock is BLOCKING a decision. Distinct from
// `buildReferenceReason` in useLockedSetsStore, which explains an inert
// reference thumbnail that was never going to carry controls.
export function blockingPaneTitle(names) {
  const joined = joinLockedSetNames(names);
  const by = joined ? ` by ${joined}` : "";
  return `Locked${by} - decisions that would change this picture are unavailable until the set is unlocked.`;
}

// Chip label for the case where the CURRENT card is free but the last decision
// can no longer be reopened - the lock landed on the card behind it.
export const LOCKED_UNDO_CHIP_LABEL = "Last decision is final - its set is locked";

// Undo reopens a suggestion, and the backend guards BOTH sides of the card
// unconditionally - so a decision made on a locked-twin card is final until the
// set is unlocked. Say that, rather than letting Undo fail silently.
export function blockedUndoMessage(names) {
  const joined = joinLockedSetNames(names);
  const where = joined ? ` in the locked set ${joined}` : " in a locked set";
  const it = lockedSetNamesOf(names).length > 1 ? "them" : "it";
  return `Can't undo - reopening this card would change a picture${where}. Unlock ${it} to undo this decision.`;
}

// The `progress.locked` bucket: suspects frozen mid-session and held out of the
// queue. Explains the count the review "lost" instead of dropping it silently.
export function lockedProgressNote(count) {
  const n = Number(count) || 0;
  return `${n} suspect${n === 1 ? "" : "s"} frozen by a locked set - held out of this review until the set is unlocked.`;
}
