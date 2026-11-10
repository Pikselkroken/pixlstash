// Every `.ctx-submenu` hangs down from its row (`top: 0` in
// `styles/context-menu.css`), so one opened near the bottom of the window ran
// off it. `v-submenu-fit` on the submenu opens it upwards instead, its bottom
// level with its row's, when it does not fit below; and when it fits neither
// way it is pushed back on screen from the top. Measured on mount, on every
// update (some fill in after they open: Open with's app list), and on window
// resize or any scroll while open, since the menus holding them stay open.

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

const refits = new WeakMap();

export const vSubmenuFit = {
  mounted(el) {
    fit(el);
    const refit = () => fit(el);
    refits.set(el, refit);
    window.addEventListener("resize", refit);
    window.addEventListener("scroll", refit, true);
  },
  updated: fit,
  unmounted(el) {
    const refit = refits.get(el);
    if (!refit) return;
    window.removeEventListener("resize", refit);
    window.removeEventListener("scroll", refit, true);
    refits.delete(el);
  },
};
