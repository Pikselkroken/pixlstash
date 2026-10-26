// The star keys (#2139): a digit rates only when it is pressed on its own, so
// Ctrl+2 no longer sets two stars.
//
// The digit is read by POSITION (`e.code`), never by character: a French
// layout needs Shift for the digits themselves, and macOS Option+digit types a
// symbol.

/**
 * The digit under the pressed key, by position: `Digit0`-`Digit9` and the
 * numpad while it types digits. Falls back to `e.key` only when the event
 * carries no code at all.
 *
 * @param {KeyboardEvent} e
 * @returns {number|null}
 */
export function digitFromEvent(e) {
  const code = e?.code || "";
  const typedDigit = /^[0-9]$/.test(e?.key || "");
  const match = /^(?:Digit|Numpad)([0-9])$/.exec(code);
  // With Num Lock off a numpad key is an arrow, Home or End: the code still
  // says Numpad4, but the key it types is no digit.
  if (match && (code.startsWith("Digit") || typedDigit)) return Number(match[1]);
  if (!code && typedDigit) return Number(e.key);
  return null;
}

/**
 * The digit of a press with no modifier held, for the star keys.
 *
 * @param {KeyboardEvent} e
 * @returns {number|null} 0-9, or null when a modifier is held or it is no digit.
 */
export function bareDigit(e) {
  if (e.ctrlKey || e.shiftKey || e.altKey || e.metaKey) return null;
  return digitFromEvent(e);
}
