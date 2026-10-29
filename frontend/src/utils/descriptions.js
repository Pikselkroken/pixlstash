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

/**
 * Where a quote from a description sits in it, ignoring case: the head's
 * title is its first sentence, capitalised and without its article.
 *
 * @param {string} text - the description.
 * @param {string} quote
 * @returns {[number, number]|null} the quote's start and end in `text`.
 */
export function findInDescription(text, quote) {
  if (!text || !quote) return null;
  const pattern = quote.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(pattern, "i").exec(text);
  return match ? [match.index, match.index + match[0].length] : null;
}
