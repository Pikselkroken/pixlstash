// Pure, unit-testable logic for TagHealthBoard.vue's ranking/explanation
// columns. Split out of the <script setup> SFC — which can't be imported by
// name without mounting it — so `whyText()` and the Spec F accuracy
// tie-breaker boost can be exercised by direct import, mirroring the store's
// existing pattern of exporting pure decision-mapping functions
// (binaryAction/pairAction in useReviewSessionsStore.js) for the same reason.

// The board's ranking signal uses the reliability-discounted counts when the
// cache has them (est_wrong_adj/est_missing_adj — precision-weighted, so an
// unreliable tag doesn't dominate "Priority"), falling back to the raw counts
// for cache rows that predate the field.
export function corrections(r) {
  const wrong = r.est_wrong_adj ?? r.est_wrong ?? 0;
  const missing = r.est_missing_adj ?? r.est_missing ?? 0;
  return Math.round(wrong + missing + (r.mismatch ?? 0));
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

// --- Accuracy tie-breaker (Spec F) -------------------------------------------
//
// Continuous, capped multiplier applied ONLY to the default "Suggested
// (health)" sort's key (TagHealthBoard.vue's `sorted` computed, key ===
// "score") — never to "Most wrong"/"Most missing"/"Ranking score"/"Accuracy",
// which keep their own single-number or partitioned-scale (RANK_KINDS)
// contracts intact. Never changes the DISPLAYED Priority number
// (corrections(r) above) — only where a row lands in that one sort.
export const F1_BOOST_THRESHOLD = 0.7; // eval_f1 at/above this: no boost
export const F1_BOOST_MAX = 1.3; // eval_f1 = 0: full 1.3x cap

export function isBoostEligible(r) {
  return (
    r.eval_metric_kind === "F1" &&
    r.eval_threshold_source != null &&
    r.eval_threshold_source !== "uncalibrated_fallback" &&
    (r.eval_f1 ?? 1) < F1_BOOST_THRESHOLD
  );
}

export function boostFactor(r) {
  if (!isBoostEligible(r)) return 1;
  const deficit = (F1_BOOST_THRESHOLD - r.eval_f1) / F1_BOOST_THRESHOLD; // (0,1]
  return 1 + (F1_BOOST_MAX - 1) * Math.min(1, deficit);
}

export function boostedScore(r) {
  return corrections(r) * boostFactor(r); // sort key only — never the displayed number
}
