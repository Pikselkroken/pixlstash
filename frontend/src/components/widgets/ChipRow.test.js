// ChipRow clips to one line and reports "+N". jsdom has no layout, so each test
// hands the row the widths a browser would have measured.

import { readFileSync } from "node:fs";
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
  [...wrapper.element.children].filter(
    (el) =>
      el.classList.contains("chip-row__chip") ||
      el.classList.contains("chip-row__more"),
  );

const shown = (wrapper) =>
  own(wrapper)
    .filter((el) => el.classList.contains("chip-row__chip"))
    .filter((el) => el.style.display !== "none")
    .map((el) => el.textContent.trim());

const moreChip = (wrapper) =>
  own(wrapper).find((el) => el.classList.contains("chip-row__more"));

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

  it("clips the row rather than wrapping it", () => {
    // jsdom lays nothing out, so this one is read off the stylesheet: without
    // it the clipped chips are simply drawn over the rest of the card.
    const css = readFileSync("src/components/widgets/ChipRow.vue", "utf8")
      .split("<style scoped>")[1]
      .replace(/\/\*[\s\S]*?\*\//g, "");
    const row = css
      .split("}")
      .find((r) => r.split("{")[0].trim() === ".chip-row");
    expect(row).toContain("overflow: hidden");
    expect(row).toContain("white-space: nowrap");
  });

  it("marks a recipe slot dashed and leaves a model chip plain", () => {
    const wrapper = mount(ChipRow, {
      props: {
        items: [{ label: "lightning" }, { label: "slot", dashed: true }],
      },
    });
    const chips = wrapper.findAll(".chip-row > .chip-row__chip");
    expect(chips[0].classes()).not.toContain("chip-row__chip--dashed");
    expect(chips[1].classes()).toContain("chip-row__chip--dashed");
  });

  it("drops the fill on a fact chip and keeps it on a model chip", () => {
    const wrapper = mount(ChipRow, {
      props: {
        items: [{ label: "realvis" }, { label: "txt2img", fact: true }],
      },
    });
    const chips = wrapper.findAll(".chip-row > .chip-row__chip");
    expect(chips[0].classes()).not.toContain("chip-row__chip--fact");
    expect(chips[1].classes()).toContain("chip-row__chip--fact");
  });

  it("gives the measuring copy the same glyph as the chip it stands for", () => {
    // The probe is what decides how many chips fit. A glyph drawn only on the
    // visible chip measures every chip short and the row overflows.
    const wrapper = mount(ChipRow, {
      props: { items: [{ label: "lightning", icon: "layers" }] },
    });
    expect(
      wrapper.find(".chip-row > .chip-row__chip .chip-row__icon").text(),
    ).toBe("mdi-layers");
    expect(wrapper.find(".chip-row__measure .chip-row__icon").text()).toBe(
      "mdi-layers",
    );
  });

  it("outlines every chip and fills only the ones that name a model", () => {
    // jsdom applies no scoped CSS, so the three variants are read off the sheet.
    const css = readFileSync("src/components/widgets/ChipRow.vue", "utf8")
      .split("<style scoped>")[1]
      .replace(/\/\*[\s\S]*?\*\//g, "");
    const rule = (selector) =>
      css.split("}").find((r) => r.split("{")[0].trim() === selector);

    const base = rule(".chip-row__chip,\n.chip-row__more");
    expect(base).toContain("border: 1px solid rgb(var(--v-theme-border))");
    expect(base).toContain("background: rgb(var(--v-theme-input-background))");
    for (const unfilled of [
      ".chip-row__chip--dashed",
      ".chip-row__chip--fact",
      ".chip-row__more",
    ]) {
      expect(rule(unfilled), unfilled).toContain("background: transparent");
    }
  });
});
