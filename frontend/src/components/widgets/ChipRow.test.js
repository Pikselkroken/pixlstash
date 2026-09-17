// ChipRow clips to one line and reports "+N". jsdom has no layout, so each test
// hands the row the widths a browser would have measured.

import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import ChipRow from "./ChipRow.vue";

const ITEMS = ["alpha", "beta", "gamma", "delta", "epsilon"].map((label) => ({
  label,
}));

/** Mount, then lay the row out: every chip `chipW` wide, the row `rowW`. */
function laidOut(rowW, chipW = 40) {
  const wrapper = mount(ChipRow, { props: { items: ITEMS } });
  resize(wrapper, rowW, chipW);
  return wrapper;
}

function resize(wrapper, rowW, chipW = 40) {
  const row = wrapper.element;
  Object.defineProperty(row, "clientWidth", {
    value: rowW,
    configurable: true,
  });
  for (const el of wrapper.find(".chip-row__measure").element.children) {
    el.getBoundingClientRect = () => ({ width: chipW });
  }
  // jsdom does not compute `column-gap`, so the 4px gap is handed over too.
  vi.spyOn(window, "getComputedStyle").mockImplementation((el) => ({
    columnGap: el === row ? "4px" : "",
  }));
  wrapper.vm.measure();
}

/** The row's own chips (not the measuring copies) that are on screen. */
const own = (wrapper) =>
  [...wrapper.element.children].filter((el) =>
    el.classList.contains("chip-row__chip"),
  );

const shown = (wrapper) =>
  own(wrapper)
    .filter((el) => !el.classList.contains("chip-row__chip--more"))
    .filter((el) => el.style.display !== "none")
    .map((el) => el.textContent.trim());

const moreChip = (wrapper) =>
  own(wrapper).find((el) => el.classList.contains("chip-row__chip--more"));

describe("ChipRow", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows everything with no +N when the row is wide enough", async () => {
    // 5 × 40 + 4 × 4 = 216.
    const wrapper = laidOut(216);
    await wrapper.vm.$nextTick();
    expect(shown(wrapper)).toHaveLength(5);
    expect(moreChip(wrapper)).toBeUndefined();
    expect(wrapper.emitted("overflow")).toBeUndefined();
  });

  it("clips what does not fit and emits the hidden count", async () => {
    // Two chips plus the +N chip: 40+4+40+4+40 = 128.
    const wrapper = laidOut(130);
    await wrapper.vm.$nextTick();
    expect(shown(wrapper)).toEqual(["alpha", "beta"]);
    const more = moreChip(wrapper);
    expect(more.textContent).toBe("+3");
    expect(more.getAttribute("aria-hidden")).toBe("true");
    expect(wrapper.emitted("overflow").at(-1)).toEqual([3]);
  });

  it("counts the gaps between chips", async () => {
    // 5 × 40 = 200 fits 205 only if the four 4px gaps are ignored.
    const wrapper = laidOut(205);
    await wrapper.vm.$nextTick();
    expect(shown(wrapper)).toHaveLength(3);
  });

  it("shows the clipped chips again when the row grows", async () => {
    const wrapper = laidOut(130);
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("overflow").at(-1)).toEqual([3]);
    resize(wrapper, 216);
    await wrapper.vm.$nextTick();
    expect(shown(wrapper)).toHaveLength(5);
    expect(moreChip(wrapper)).toBeUndefined();
    expect(wrapper.emitted("overflow").at(-1)).toEqual([0]);
  });

  it("variants reach the chip class", () => {
    const wrapper = mount(ChipRow, {
      props: { items: [{ label: "slot", variant: "dashed" }] },
    });
    expect(wrapper.find(".chip-row__chip--dashed").exists()).toBe(true);
  });
});
