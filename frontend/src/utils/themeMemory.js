// The theme this browser used last, so the first frame is the right one.
//
// The theme a person chose lives on their user record, which is a round trip
// away: everything painted before that answer lands is painted in whatever the
// app decided to default to. Dark is that default (it matches the desktop
// shell), so someone who chose light saw a dark frame first and someone new saw
// nothing jarring at all. Remembering the last one locally removes the flash
// for both: the first paint is a fact about this browser, not a guess about
// this user, and the stored preference still wins the moment it arrives.
//
// `localStorage` is the right home for exactly this reason - it is per-browser,
// survives a reload, and reaching it costs nothing. It is a cache of a decision
// made elsewhere, never the decision itself: a stale or missing value can only
// cost one repaint.

const KEY = "pixlstash.themeMode";
const MODES = new Set(["light", "dark"]);

/**
 * The remembered theme, or null when there is none to trust.
 *
 * @returns {"light"|"dark"|null}
 */
export function readRememberedTheme() {
  try {
    const stored = localStorage.getItem(KEY);
    return MODES.has(stored) ? stored : null;
  } catch (e) {
    // A blocked or unavailable localStorage (private windows, a locked-down
    // profile) is not a failure: there is simply nothing remembered, and the
    // caller falls back to the default exactly as it would on a first run.
    console.warn("Could not read the remembered theme:", e);
    return null;
  }
}

/**
 * Remember the theme now in use, for the next launch's first paint.
 *
 * @param {string} mode - "light" or "dark"; anything else is ignored.
 */
export function rememberTheme(mode) {
  if (!MODES.has(mode)) return;
  try {
    localStorage.setItem(KEY, mode);
  } catch (e) {
    console.warn("Could not remember the theme:", e);
  }
}
