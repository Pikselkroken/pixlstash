/**
 * Roving focus for a `radiogroup` (Segmented, OptionRows). Left/Up step back,
 * Right/Down step forward, wrapping and skipping disabled options. The arrow
 * SELECTS as it moves, which is the radiogroup contract, and focus follows.
 *
 * @param {KeyboardEvent} event - keydown on the radiogroup element.
 * @param {Array<{id: *, disabled?: boolean}>} options
 * @param {*} value - the current selection.
 * @returns {*} the id to select, or undefined when the key is not an arrow.
 */
export function arrowStep(event, options, value) {
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[
    event.key
  ];
  if (!step) return undefined;
  const live = options.filter((o) => !o.disabled);
  if (!live.length) return undefined;
  event.preventDefault();
  const at = live.findIndex((o) => o.id === value);
  const next =
    at < 0
      ? live[step > 0 ? 0 : live.length - 1]
      : live[(at + step + live.length) % live.length];
  const radios = event.currentTarget.querySelectorAll('[role="radio"]');
  radios[options.indexOf(next)]?.focus();
  return next.id;
}

/**
 * The option holding the group's one tab stop: the selected one, or the first
 * live option when nothing is selected yet.
 */
export function tabStopId(options, value) {
  if (options.some((o) => o.id === value && !o.disabled)) return value;
  return options.find((o) => !o.disabled)?.id;
}
