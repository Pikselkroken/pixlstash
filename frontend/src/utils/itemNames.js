/**
 * A Studio item's name from the words its sheets were drawn from (#2819):
 * "a mustard raincoat over a white shirt, rubber boots" names itself
 * "Mustard raincoat over a white shirt". The first clause, without its
 * article, capitalised, cut on a word to fit a name.
 */
const MAX = 40;
const ARTICLE = /^(a|an|the|some)\s+/i;

export function nameFromDescription(description) {
  const first = String(description || "")
    .split(/[,.;:\n]/)[0]
    .trim()
    .replace(ARTICLE, "")
    .replace(/\s+/g, " ");
  if (!first) return "";
  let name = first;
  if (name.length > MAX) {
    const cut = name.slice(0, MAX + 1);
    name = cut.slice(0, cut.lastIndexOf(" ") > 0 ? cut.lastIndexOf(" ") : MAX);
  }
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** Whether *name* is one the Studio gave an item for want of one ("New
 * outfit", "New outfit copy 2"), so a better one may replace it. */
export function isPlaceholderName(name, newName) {
  return new RegExp(`^${newName}( copy( \\d+)?)?( \\(.*\\))?$`, "i").test(String(name || "").trim());
}
