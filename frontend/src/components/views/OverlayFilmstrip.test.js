// The lightbox filmstrip's picks for Compare: Ctrl or Cmd picks a thumb, a
// plain click still goes to it, and each pick says which side it lands on.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", async (importOriginal) => ({
  ...(await importOriginal()),
  // Tooltip.vue wraps VTooltip: render the activator only, as a closed tip does.
  VTooltip: {
    name: "VTooltip",
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

import OverlayFilmstrip from "./OverlayFilmstrip.vue";

const ITEMS = [
  { id: 7, index: 0, thumbSrc: "t7.webp" },
  { id: 5, index: 1, thumbSrc: "t5.webp" },
  { id: 3, index: 2, thumbSrc: "t3.webp" },
];

function mountStrip(pickedIds = []) {
  return mount(OverlayFilmstrip, {
    props: { items: ITEMS, pickedIds },
    global: { stubs: { "v-icon": true, "transition-group": false } },
  });
}

describe("OverlayFilmstrip picks", () => {
  it("picks with Ctrl or Cmd and goes to the picture on a plain click", async () => {
    const strip = mountStrip();
    const thumbs = strip.findAll(".filmstrip-thumb");
    await thumbs[1].trigger("click", { ctrlKey: true });
    await thumbs[2].trigger("click", { metaKey: true });
    await thumbs[0].trigger("click");
    expect(strip.emitted("pick").map(([item]) => item.id)).toEqual([5, 3]);
    expect(strip.emitted("select")).toEqual([[0]]);
  });

  it("marks each pick with the side it lands on, A then B", () => {
    const strip = mountStrip(["5", "7"]);
    const thumbs = strip.findAll(".filmstrip-thumb");
    expect(thumbs[1].find(".filmstrip-badge--pick").text()).toBe("A");
    expect(thumbs[0].find(".filmstrip-badge--pick").text()).toBe("B");
    expect(thumbs[2].find(".filmstrip-badge--pick").exists()).toBe(false);
    expect(thumbs[1].classes()).toContain("filmstrip-thumb--picked");
    expect(thumbs[2].attributes("aria-pressed")).toBe("false");
  });
});
