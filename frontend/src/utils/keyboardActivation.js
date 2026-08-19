/**
 * Give a non-native composite row the same Enter/Space activation contract as
 * a button without stealing key presses from real controls nested inside it.
 *
 * Composite rows are used where the row contains its own edit/delete buttons,
 * so changing the outer element to <button> would create invalid nested
 * buttons. Callers still need role="button" and tabindex="0" in their markup.
 */
export function activateOnEnterOrSpace(event) {
  if (event?.target !== event?.currentTarget) return false;
  if (event.key !== "Enter" && event.key !== " ") return false;

  event.preventDefault();

  const target = event.currentTarget;
  const MouseEventCtor = event.view?.MouseEvent ?? globalThis.MouseEvent;
  if (typeof MouseEventCtor === "function") {
    target.dispatchEvent(
      new MouseEventCtor("click", {
        bubbles: true,
        cancelable: true,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        shiftKey: event.shiftKey,
      }),
    );
  } else {
    target.click();
  }
  return true;
}
