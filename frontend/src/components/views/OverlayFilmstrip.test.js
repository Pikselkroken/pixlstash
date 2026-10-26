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

// Scrolled natively over a spacer: 30 thumbs of 100px in a 700px viewport,
// side by side.
describe("OverlayFilmstrip scrolling", () => {
  const OFFSETS = Array.from({ length: 30 }, (_, i) => i * 100);
  const WIDTHS = OFFSETS.map(() => 100);

  function mountScrolling(scrollLeft = 0, props = {}) {
    const strip = mount(OverlayFilmstrip, {
      props: {
        items: ITEMS.map((item, i) => ({ ...item, index: i + 4 })),
        offsets: OFFSETS,
        widths: WIDTHS,
        ...props,
      },
      global: { stubs: { "v-icon": true, "transition-group": false } },
    });
    const el = strip.find(".filmstrip-viewport").element;
    Object.defineProperty(el, "clientWidth", { value: 700 });
    el.scrollLeft = scrollLeft;
    el.scrollTo = vi.fn();
    return { strip, el };
  }

  it("is as wide as every thumb, with the drawn ones placed where they belong", () => {
    const { strip } = mountScrolling();
    expect(strip.find(".filmstrip-sizer").attributes("style")).toContain(
      "width: 3000px",
    );
    const style = strip.findAll(".filmstrip-thumb")[0].attributes("style");
    expect(style).toContain("left: 400px");
    expect(style).toContain("width: 100px");
  });

  it("draws each thumb at the width it is given", () => {
    // The lightbox sizes each thumb to its picture's shape.
    const { strip } = mountScrolling(0, {
      offsets: [0, 58, 100],
      widths: [56, 40, 75],
      items: ITEMS,
    });
    const thumbs = strip.findAll(".filmstrip-thumb");
    expect(thumbs[1].attributes("style")).toContain("left: 58px");
    expect(thumbs[1].attributes("style")).toContain("width: 40px");
    expect(strip.find(".filmstrip-sizer").attributes("style")).toContain(
      "width: 175px",
    );
  });

  it("marks the picture on screen", () => {
    const { strip } = mountScrolling(0, {
      items: ITEMS.map((item, i) => ({ ...item, index: i, isActive: i === 1 })),
    });
    const thumbs = strip.findAll(".filmstrip-thumb");
    expect(thumbs[1].classes()).toContain("filmstrip-thumb--active");
    expect(thumbs[1].attributes("aria-current")).toBe("true");
    expect(thumbs[0].classes()).not.toContain("filmstrip-thumb--active");
  });

  it("drops a thumb that scrolls out of the window at once", async () => {
    // A leaving thumb kept in the DOM for a frame shifted the strip on each
    // step of a scroll.
    const { strip } = mountScrolling();
    await strip.setProps({ items: ITEMS.slice(1) });
    expect(strip.findAll(".filmstrip-thumb")).toHaveLength(ITEMS.length - 1);
  });

  it("reports the thumbs in view as it scrolls", async () => {
    const { strip, el } = mountScrolling();
    el.scrollLeft = 350;
    await strip.find(".filmstrip-viewport").trigger("scroll");
    expect(strip.emitted("visible-range")).toEqual([[{ first: 3, last: 10 }]]);
  });

  it("turns a mouse wheel into a sideways scroll", async () => {
    const { strip, el } = mountScrolling(100);
    const wheel = new WheelEvent("wheel", {
      deltaY: 120,
      bubbles: true,
      cancelable: true,
    });
    el.dispatchEvent(wheel);
    expect(el.scrollLeft).toBe(220);
    expect(wheel.defaultPrevented).toBe(true);
    // A trackpad's own sideways swipe is left to the browser.
    const swipe = new WheelEvent("wheel", {
      deltaX: 80,
      deltaY: 10,
      bubbles: true,
      cancelable: true,
    });
    el.dispatchEvent(swipe);
    expect(swipe.defaultPrevented).toBe(false);
    expect(el.scrollLeft).toBe(220);
    // A wheel that counts lines (Firefox) moves by more than a pixel a line.
    el.dispatchEvent(
      new WheelEvent("wheel", { deltaY: 3, deltaMode: 1, cancelable: true }),
    );
    expect(el.scrollLeft).toBe(340);
    strip.unmount();
  });

  it("leaves a thumb that is in view where it is", () => {
    const { strip, el } = mountScrolling(0);
    strip.vm.reveal(6);
    expect(el.scrollLeft).toBe(0);
    expect(el.scrollTo).not.toHaveBeenCalled();
  });

  it("eases a thumb just past an edge in by the least that shows it", () => {
    const { strip, el } = mountScrolling(500);
    strip.vm.reveal(12);
    expect(el.scrollTo).toHaveBeenLastCalledWith({
      left: 600,
      behavior: "smooth",
    });
    strip.vm.reveal(4);
    expect(el.scrollTo).toHaveBeenLastCalledWith({
      left: 400,
      behavior: "smooth",
    });
  });

  it("centres a far thumb at once, and reports it", () => {
    const { strip, el } = mountScrolling(0);
    strip.vm.reveal(20);
    expect(el.scrollTo).not.toHaveBeenCalled();
    expect(el.scrollLeft).toBe(1700);
    expect(strip.emitted("visible-range")).toEqual([[{ first: 17, last: 24 }]]);
  });

  it("centres when asked, and stops at the ends", () => {
    const { strip, el } = mountScrolling(0);
    strip.vm.reveal(5, { centre: true });
    expect(el.scrollLeft).toBe(200);
    strip.vm.reveal(29, { centre: true });
    expect(el.scrollLeft).toBe(2300);
    strip.vm.reveal(1, { centre: true });
    expect(el.scrollLeft).toBe(0);
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
