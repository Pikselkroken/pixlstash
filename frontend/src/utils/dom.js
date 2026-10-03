/**
 * Whether an element is a text-entry target that owns its own keystrokes.
 *
 * The predicate every global key handler needs before claiming a bare letter:
 * typing "s" in a tag field must not also trigger the Selection menu.
 *
 * `SELECT` counts because its type-ahead consumes letters the same way a text
 * field does, and `role="textbox"` catches the ARIA widgets that are not native
 * inputs.
 *
 * @param {EventTarget|null} el
 * @returns {boolean}
 */
export function isEditableElement(el) {
  return (
    el instanceof HTMLElement &&
    (el.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) ||
      el.getAttribute("role") === "textbox")
  );
}

/**
 * Whether a key event should be left to a text-entry target.
 *
 * Checks the event target AND `document.activeElement`, because a keydown can
 * be delivered to an ancestor (or to `body`) while focus genuinely sits in a
 * field - a handler that only inspected the target would steal those keys.
 *
 * Two call sites deliberately do NOT use this and keep their own predicate:
 * `useDedupQueueKeyboard` also treats a Vuetify slider/spinner thumb as owning
 * its arrows, and `ReviewSessionsOverlay` deliberately excludes `SELECT` so a
 * focused select cannot swallow decision keys into its type-ahead.
 *
 * @param {EventTarget|null} target - usually `event.target`.
 * @returns {boolean}
 */
export function isTypingTarget(target) {
  const active = typeof document === "undefined" ? null : document.activeElement;
  return [target, active].some(isEditableElement);
}

/**
 * A duration custom property, in milliseconds.
 *
 * The production CSS minifier rewrites `700ms` as `.7s`, so a bare
 * `parseFloat` reads 0.7 and a 700 ms delay becomes no delay at all; the unit
 * has to be read. Unset or unparseable gives `fallback`.
 *
 * @param {CSSStyleDeclaration} style - usually of `document.documentElement`.
 * @param {string} name - e.g. `--tooltip-delay`.
 * @param {number} [fallback=0]
 * @returns {number}
 */
export function cssDurationMs(style, name, fallback = 0) {
  const value = style.getPropertyValue(name).trim();
  const n = parseFloat(value);
  if (Number.isNaN(n)) return fallback;
  return value.endsWith("s") && !value.endsWith("ms") ? n * 1000 : n;
}
