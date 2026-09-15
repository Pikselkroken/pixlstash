/* Roving focus for the shared menu surface (`styles/context-menu.css`).
 *
 * Vuetify's `VList` brought this for free; the one-menu cleanup replaced it
 * with a plain `.ctx-menu` div and the arrow keys went with it. Bind the
 * handler on the surface itself - `event.currentTarget` is the menu, so one
 * handler serves every row without per-row wiring.
 *
 * What is NOT lost, and so is not re-implemented here: a `v-menu`'s activator
 * already answers ArrowDown/ArrowUp by focusing the content's first focusable
 * child (`VMenu.onActivatorKeydown`). Rows must therefore be real `<button>`s -
 * focusability and Enter/Space activation are the browser's job, not a keydown
 * ladder's - and what was missing was only the movement BETWEEN rows once
 * focus is inside.
 *
 * A menu that is not a `v-menu` (a teleported div positioned by hand) gets no
 * such activator, so its opener focuses the surface itself: that is what the
 * `tabindex="-1"` on these surfaces is for. `focusableChildren` skips
 * `[tabindex="-1"]`, so it never steals a row's turn. */

const NAV_KEYS = ["ArrowDown", "ArrowUp", "Home", "End"];

/** @param {KeyboardEvent} event keydown on the `.ctx-menu` surface. */
export function onMenuKeydown(event) {
  if (!NAV_KEYS.includes(event.key)) return;
  const menu = event.currentTarget;
  const rows = [...menu.querySelectorAll(".ctx-item")].filter(
    (el) =>
      // A submenu owns its own rows; only this surface's are navigable here.
      el.closest(".ctx-menu, .ctx-submenu") === menu &&
      !el.disabled &&
      !el.classList.contains("ctx-item--disabled"),
  );
  if (!rows.length) return;
  event.preventDefault();
  const current = rows.indexOf(document.activeElement);
  const last = rows.length - 1;
  let next;
  if (event.key === "Home") next = 0;
  else if (event.key === "End") next = last;
  else if (event.key === "ArrowDown")
    next = current < 0 ? 0 : (current + 1) % rows.length;
  else next = current <= 0 ? last : current - 1;
  rows[next].focus();
}
