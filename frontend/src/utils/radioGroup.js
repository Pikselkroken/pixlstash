/**
 * Roving focus for a `radiogroup` (Segmented, OptionRows). Left/Up step back,
 * Right/Down step forward, wrapping and skipping disabled options. The arrow
 * SELECTS as it moves, which is the radiogroup contract, and focus follows.
 * A handled arrow does not propagate.
 *
 * A `horizontal` group answers Left/Right only and lets Up/Down through, for a
 * switch sitting in a grid's header: there Down means "into the grid below",
 * and flipping the switch back is the last thing the reader expects.
 *
 * @param {KeyboardEvent} event - keydown on the radiogroup element.
 * @param {Array<{id: *, disabled?: boolean}>} options
 * @param {*} value - the current selection.
 * @param {"both"|"horizontal"} [orientation="both"]
 * @returns {*} the id to select, or undefined when the key is not an arrow.
 */
export function arrowStep(event, options, value, orientation = "both") {
  const step = {
    ArrowRight: 1,
    ArrowLeft: -1,
    ...(orientation === "horizontal" ? {} : { ArrowDown: 1, ArrowUp: -1 }),
  }[event.key];
  if (!step) return undefined;
  const live = options.filter((o) => !o.disabled);
  if (!live.length) return undefined;
  // The group owns the arrow while it has focus, so a grid's or a queue's
  // window-level key model must not act on the same press.
  event.preventDefault();
  event.stopPropagation();
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
