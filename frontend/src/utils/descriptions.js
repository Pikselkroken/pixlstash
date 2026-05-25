const DESCRIPTION_SENTINEL_PREFIX = "__description::";

/**
 * Returns true if the value is a pending-redescribe sentinel (`__description::…`).
 */
export function isDescriptionSentinel(value) {
  return typeof value === "string" && value.startsWith(DESCRIPTION_SENTINEL_PREFIX);
}

/**
 * Returns a user-friendly display label for a description value.
 * Sentinel values are converted to readable strings; all others are returned unchanged.
 *   '__description::'         → 'Generating description…'
 *   '__description::joycaption' → 'Generating with joycaption…'
 */
export function formatDescriptionSentinel(value) {
  if (typeof value !== "string") return value;
  if (!value.startsWith(DESCRIPTION_SENTINEL_PREFIX)) return value;
  const engine = value.slice(DESCRIPTION_SENTINEL_PREFIX.length).trim();
  if (engine) return `Generating with ${engine}\u2026`;
  return "Generating description\u2026";
}
