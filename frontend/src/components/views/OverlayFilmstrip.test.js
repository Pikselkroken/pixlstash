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

  it("hands a right-click on a thumb to the lightbox's menu", async () => {
    const strip = mountStrip();
    await strip.findAll(".filmstrip-thumb")[1].trigger("contextmenu");
    expect(strip.emitted("context-menu")[0][0].id).toBe(5);
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

// Scrolled natively over a spacer: 30 thumbs of 100px in a 700px viewport.
describe("OverlayFilmstrip scrolling", () => {
  function mountScrolling(scrollTop = 0) {
    const strip = mount(OverlayFilmstrip, {
      props: {
        items: ITEMS,
        virtual: true,
        total: 30,
        start: 4,
        slotSize: 100,
      },
      global: { stubs: { "v-icon": true, "transition-group": false } },
    });
    const el = strip.find(".filmstrip-viewport").element;
    Object.defineProperty(el, "clientHeight", { value: 700 });
    el.scrollTop = scrollTop;
    el.scrollTo = vi.fn();
    return { strip, el };
  }

  it("is as tall as every thumb, with the drawn ones placed where they belong", () => {
    const { strip } = mountScrolling();
    expect(strip.find(".filmstrip-sizer").attributes("style")).toContain(
      "height: 3000px",
    );
    expect(strip.find(".filmstrip-list").attributes("style")).toContain(
      "translateY(400px)",
    );
  });

  it("reports the first thumb in view as it scrolls", async () => {
    const { strip, el } = mountScrolling();
    el.scrollTop = 350;
    await strip.find(".filmstrip-viewport").trigger("scroll");
    expect(strip.emitted("first-visible")).toEqual([[3]]);
  });

  it("leaves a thumb that is in view where it is", () => {
    const { strip, el } = mountScrolling(0);
    strip.vm.reveal(6);
    expect(el.scrollTop).toBe(0);
    expect(el.scrollTo).not.toHaveBeenCalled();
  });

  it("eases a thumb just past an edge in by the least that shows it", () => {
    const { strip, el } = mountScrolling(500);
    strip.vm.reveal(12);
    expect(el.scrollTo).toHaveBeenLastCalledWith({
      top: 600,
      behavior: "smooth",
    });
    strip.vm.reveal(4);
    expect(el.scrollTo).toHaveBeenLastCalledWith({
      top: 400,
      behavior: "smooth",
    });
  });

  it("centres a far thumb at once, and reports it", () => {
    const { strip, el } = mountScrolling(0);
    strip.vm.reveal(20);
    expect(el.scrollTo).not.toHaveBeenCalled();
    expect(el.scrollTop).toBe(1700);
    expect(strip.emitted("first-visible")).toEqual([[17]]);
  });

  it("centres when asked, and stops at the ends", () => {
    const { strip, el } = mountScrolling(0);
    strip.vm.reveal(5, { centre: true });
    expect(el.scrollTop).toBe(200);
    strip.vm.reveal(29, { centre: true });
    expect(el.scrollTop).toBe(2300);
    strip.vm.reveal(1, { centre: true });
    expect(el.scrollTop).toBe(0);
  });

  it("leaves the arrow keys to the lightbox instead of scrolling", () => {
    const { strip } = mountScrolling();
    const thumb = strip.find(".filmstrip-thumb").element;
    for (const key of ["ArrowDown", "ArrowUp", "PageDown"]) {
      const event = new KeyboardEvent("keydown", {
        key,
        bubbles: true,
        cancelable: true,
      });
      thumb.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(key !== "PageDown");
    }
  });
});
