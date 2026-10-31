// Every `.ctx-submenu` hangs down from its row (`top: 0` in
// `styles/context-menu.css`), so one opened near the bottom of the window ran
// off it. `v-submenu-fit` on the submenu opens it upwards instead, its bottom
// level with its row's, when it does not fit below; and when it fits neither
// way it is pushed back on screen from the top. Measured on mount and again on
// every update, since some fill in after they open (Open with's app list).

const EDGE_PX = 8;

function fit(el) {
  el.style.top = "";
  const row = el.parentElement?.getBoundingClientRect();
  if (!row) return;
  const height = el.offsetHeight;
  const floor = window.innerHeight - EDGE_PX;
  let top = row.top;
  if (top + height > floor) top = row.bottom - height;
  top = Math.max(EDGE_PX, Math.min(top, floor - height));
  if (top !== row.top) el.style.top = `${top - row.top}px`;
}

export const vSubmenuFit = { mounted: fit, updated: fit };
