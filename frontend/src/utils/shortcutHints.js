// Platform-correct keycap labels for the undo/redo affordances.
//
// The binding itself is the same everywhere - the global handler accepts either
// Ctrl or Meta - but the HINT has to match the keyboard in front of the user.
// A macOS user shown "Ctrl+Z" learns the wrong habit, and the toolbar tooltip
// is where the shortcut is taught once the receipt is gone.
//
// Detection prefers `navigator.userAgentData.platform` (the modern, non-frozen
// source) and falls back to the user-agent string, which is the only signal
// available in Safari and in jsdom.

/**
 * Is this an Apple platform (macOS, iPadOS, iOS)?
 *
 * @param {Object} [nav=navigator] - injectable for tests.
 * @returns {boolean} true when the ⌘ key is the modifier the user expects.
 */
export function isApplePlatform(
  nav = typeof navigator !== "undefined" ? navigator : null,
) {
  if (!nav) return false;
  const modern = nav.userAgentData?.platform;
  if (typeof modern === "string" && modern) return /mac|ios/i.test(modern);
  const agent = nav.userAgent ?? "";
  return /Mac|iPhone|iPad|iPod/i.test(agent);
}

/**
 * Keycap labels for "undo", in press order.
 * @param {Object} [nav] - injectable navigator.
 * @returns {Array<string>} e.g. `["Ctrl", "Z"]` or `["⌘", "Z"]`.
 */
export function undoKeyHint(nav) {
  return isApplePlatform(nav) ? ["⌘", "Z"] : ["Ctrl", "Z"];
}

/**
 * Keycap labels for "redo", in press order.
 *
 * macOS has no Ctrl+Y convention: the system redo is ⇧⌘Z, so that is what the
 * hint shows even though the handler also accepts Y.
 *
 * @param {Object} [nav] - injectable navigator.
 * @returns {Array<string>} e.g. `["Ctrl", "Y"]` or `["⇧", "⌘", "Z"]`.
 */
export function redoKeyHint(nav) {
  return isApplePlatform(nav) ? ["⇧", "⌘", "Z"] : ["Ctrl", "Y"];
}

/**
 * Keycap labels for "select all", in press order.
 *
 * The shelf, the grid and the duplicate queue all accept either Ctrl or Meta;
 * only the label differs.
 *
 * @param {Object} [nav] - injectable navigator.
 * @returns {Array<string>} e.g. `["Ctrl", "A"]` or `["⌘", "A"]`.
 */
export function selectAllKeyHint(nav) {
  return isApplePlatform(nav) ? ["⌘", "A"] : ["Ctrl", "A"];
}

/**
 * The same hint as one flat string, for a `title` attribute or an aria-label
 * where separate keycap elements are not available.
 * @param {Array<string>} keys - a hint from {@link undoKeyHint}.
 * @returns {string} e.g. `"Ctrl+Z"`.
 */
export function formatKeyHint(keys) {
  return (Array.isArray(keys) ? keys : []).join("+");
}
