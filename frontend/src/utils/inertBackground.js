/**
 * Make every already-open Vuetify overlay except the library-switch modal inert.
 *
 * The app root itself is bound to `inert` in App.vue. Vuetify dialogs teleport
 * beside it under <body>, so they need this second, explicit pass.
 * @returns {Function} restores each overlay's previous inert state.
 */
export function inertSiblingOverlays(activePanel, root = document) {
  const activeOverlay = activePanel?.closest?.(".v-overlay") ?? null;
  const changed = [];

  for (const overlay of root.querySelectorAll(".v-overlay")) {
    if (overlay === activeOverlay || overlay.inert) continue;
    overlay.inert = true;
    changed.push(overlay);
  }

  return () => {
    for (const overlay of changed) overlay.inert = false;
  };
}
