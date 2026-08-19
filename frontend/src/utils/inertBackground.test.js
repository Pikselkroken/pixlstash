import { afterEach, describe, expect, it, vi } from "vitest";

import { inertSiblingOverlays } from "./inertBackground";

afterEach(() => {
  document.body.innerHTML = "";
});

describe("inertSiblingOverlays", () => {
  it("blocks already-open Settings/Shortcuts overlays but not the switch modal", () => {
    document.body.innerHTML = `
      <div class="v-overlay settings"><button>Setting</button></div>
      <div class="v-overlay shortcuts"><button>Shortcut</button></div>
      <div class="v-overlay switch"><section class="active"></section></div>
    `;
    const settingsClick = vi.fn();
    document.querySelector(".settings button").addEventListener(
      "click",
      settingsClick,
    );
    const active = document.querySelector(".active");

    const restore = inertSiblingOverlays(active);

    expect(document.querySelector(".settings").inert).toBe(true);
    expect(document.querySelector(".shortcuts").inert).toBe(true);
    expect(document.querySelector(".switch").inert).not.toBe(true);
    // Native inert, rather than click interception, is the browser mechanism:
    // focus cannot enter either background subtree.
    expect(document.querySelector(".settings").matches(":focus-within")).toBe(
      false,
    );
    expect(settingsClick).not.toHaveBeenCalled();

    restore();
    expect(document.querySelector(".settings").inert).toBe(false);
    expect(document.querySelector(".shortcuts").inert).toBe(false);
  });
});
