// Selection ▾ / context-menu parity for the entries the e2e spec cannot see
// (#403's class, v1.12 F5).
//
// `frontend/e2e/specs/menu-parity.spec.js` compares the two menus as RENDERED,
// which is the right check and has one blind spot: an entry gated on
// `comfyuiConfigured` renders in NEITHER menu there, because the e2e backend
// has no ComfyUI address. Two absences compare equal, so that spec would pass
// unchanged if one of the two run entries had been added to only one menu —
// which is exactly the gap #403 was.
//
// So the pair is asserted against the sources instead. It lives here rather
// than in the e2e lane because it never touches a browser: it is `readFileSync`
// and a regex, and vitest runs it for free.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";

const COMFYUI_GATED_ACTIONS = [
  { label: "Make more like these…", event: "make-more" },
  { label: "Run a workflow on these…", event: "run-workflow" },
  { label: "Edit with ComfyUI…", event: "edit-with-comfyui" },
];

const MENU_SOURCES = [
  ["SelectionMenu.vue", "./SelectionMenu.vue"],
  ["ImageGridContextMenu.vue", "../widgets/ImageGridContextMenu.vue"],
];

/**
 * The `<button>` element that carries a label, as source text.
 *
 * A bare `source.includes(label)` would be satisfied by the label sitting in a
 * comment, or by a button that has lost its `v-if`, its `:disabled` or its
 * click handler — which is most of the ways one of these entries can rot. The
 * element is pulled out so those can be asserted on.
 */
function buttonCarrying(source, label) {
  for (const match of source.matchAll(/<button[\s\S]*?<\/button>/g)) {
    // A label mentioned only in a comment inside the element is not an entry.
    const withoutComments = match[0].replace(/<!--[\s\S]*?-->/g, "");
    if (withoutComments.includes(label)) return withoutComments;
  }
  return null;
}

describe("the two menus mirror the ComfyUI-gated run entries", () => {
  for (const [name, relative] of MENU_SOURCES) {
    describe(name, () => {
      const source = readFileSync(new URL(relative, import.meta.url), "utf8");

      for (const { label, event } of COMFYUI_GATED_ACTIONS) {
        it(`offers "${label}", gated and wired`, () => {
          const button = buttonCarrying(source, label);
          expect(
            button,
            `${name} has no <button> reading "${label}", which its sibling menu offers`,
          ).not.toBeNull();
          // The three ways the entry rots without the label moving: it stops
          // being gated on ComfyUI, it stops being gated on a selection, or it
          // stops firing the event the other menu fires.
          expect(button, "lost its ComfyUI gate").toContain("comfyuiConfigured");
          expect(button, "lost its empty-selection guard").toMatch(/:disabled=/);
          expect(button, `no longer fires "${event}"`).toContain(event);
        });
      }
    });
  }
});
