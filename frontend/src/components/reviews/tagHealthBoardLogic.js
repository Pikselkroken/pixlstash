// Pure, unit-testable logic for TagHealthBoard.vue's ranking/explanation
// columns. Split out of the <script setup> SFC — which can't be imported by
// name without mounting it — so `whyText()` can be exercised by direct
// import, mirroring the store's existing pattern of exporting pure
// decision-mapping functions (binaryAction/pairAction in
// useReviewSessionsStore.js) for the same reason.

// The board's ranking signal uses the reliability-discounted counts when the
// cache has them (est_wrong_adj/est_missing_adj — precision-weighted, so an
// unreliable tag doesn't dominate "Priority"), falling back to the raw counts
// for cache rows that predate the field.
export function corrections(r) {
  const wrong = r.est_wrong_adj ?? r.est_wrong ?? 0;
  const missing = r.est_missing_adj ?? r.est_missing ?? 0;
  return Math.round(wrong + missing + (r.mismatch ?? 0));
}

// Displayed value for an "Est. wrong"/"Est. missing" cell: the
// precision-adjusted estimate (rounded) when the cache has it, else the raw
// count. The column header already reads "Est." — the number IS the estimate
// of genuine fixes, discounted by the tag's measured reliability, not the raw
// model-flag count (which moves to the tooltip, see `estRawTitle`).
export function estDisplay(raw, adj) {
  return Math.round(adj ?? raw ?? 0);
}

// Tooltip for an estimate cell: names the raw model-flag count behind the
// discounted number. Returns `undefined` (no tooltip) when there's no discount
// to explain — either the cache predates the `_adj` field, or precision was
// ~1.0 so the raw and adjusted numbers coincide.
export function estRawTitle(raw, adj) {
  const r = raw ?? 0;
  if (adj == null || Math.round(adj) === r) return undefined;
  return `${r} flagged by the model; shown discounted by this tag's measured reliability`;
}

// Tie-break signal for the default "Suggested (health)" sort (see `sorted` in
// TagHealthBoard.vue, key === "score"). Two tags routinely round to the same
// displayed Priority number in a lightly-reviewed vault, and without a
// deterministic secondary key `Array.prototype.sort` falls through to
// whichever order the row happened to arrive in — which reads as alphabetical
// because `sorted()`'s final fallback is `tag.localeCompare`. That fallback
// firing on the PRIMARY signal (not as a last-resort for genuine ties) makes
// the board's "prioritized by likely-wrongness" promise false for every tied
// pair.
//
// Fix: always use the raw, un-rounded, un-precision-discounted
// `est_wrong + est_missing + mismatch` as the secondary key. This is the same
// underlying signal `corrections()` summarises (not an unrelated axis like
// recency) at full precision — two tags that tie once rounded to an integer
// and once discounted by reliability can still differ in raw disagreement
// volume, and that's the more specific, still-explicable story for why one
// outranks the other ("mostly missing"/"mostly wrong" in `whyText()` already
// reads directly off these same raw counts). Never used for the *displayed*
// Priority number — `corrections()` remains that number unchanged.
export function rawCorrections(r) {
  return (r.est_wrong ?? 0) + (r.est_missing ?? 0) + (r.mismatch ?? 0);
}

// "Why it ranks here": computed client-side from fields already on the row
// (there is no `why` field from the backend — see the Spec E design note in
// docs/reviews/tag-review-board-redesign-ux-spec.md §7c). Priority order: a
// human/model dispute is the rarest, most specific story on the row, so it
// wins when present; otherwise the dominant est_wrong/est_missing/mismatch
// signal explains the ranking directly; only when none of those fired does a
// strongly one-sided overturn_rate get a look-in, as a secondary trust
// signal — a middling overturn rate isn't worth a sentence.
export function whyText(r) {
  if (r.has_model === false)
    return "not in the tagger's vocabulary — similarity review still works";
  const wrong = r.est_wrong_adj ?? r.est_wrong ?? 0;
  const missing = r.est_missing_adj ?? r.est_missing ?? 0;
  const mismatch = r.mismatch ?? 0;
  const disputes = r.model_disputes ?? 0;
  if (disputes > 0)
    return `model disputes ${disputes} of your past call${disputes === 1 ? "" : "s"}`;
  if (wrong === 0 && missing === 0 && mismatch === 0) {
    if (r.overturn_rate != null) {
      const pct = Math.round(r.overturn_rate * 100);
      if (r.overturn_rate >= 0.66) return `past suggestions mostly confirmed (${pct}%)`;
      if (r.overturn_rate <= 0.33)
        return `past suggestions mostly dismissed (${pct}%) — low signal`;
    }
    return "";
  }
  return [
    { label: "mostly missing — model is confident but untagged", v: missing },
    { label: "mostly wrong — tagged but model disagrees", v: wrong },
    { label: "near-identical shots disagree on this tag", v: mismatch },
  ].sort((a, b) => b.v - a.v)[0].label;
}
