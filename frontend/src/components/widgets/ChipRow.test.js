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

/**
 * Every rule in a component's stylesheets whose selector list is one of
 * `selectors`, as its raw declarations. Selectors are compared with whitespace
 * collapsed, so a Prettier reflow reports nothing; returning ALL of them (not
 * the first) is what lets a caller insist a selector is declared only once.
 *
 * EVERY `<style>` block, not the first: an SFC may carry several, so reading
 * one of them leaves a second block reverting the chip invisible to the
 * assertions below.
 */
function rules(file, selectors) {
  const wanted = [selectors].flat().map((s) => s.replace(/\s+/g, " ").trim());
  return readFileSync(file, "utf8")
    .split(/<style[^>]*>/)
    .slice(1)
    .join("")
    .replaceAll("</style>", "")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("}")
    .filter((r) => wanted.includes(r.split("{")[0].replace(/\s+/g, " ").trim()))
    .map((r) => r.split("{")[1] ?? "");
}

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
    const [row] = rules("src/components/widgets/ChipRow.vue", ".chip-row");
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
    // jsdom applies no scoped CSS and does not resolve var(), so the variants are
    // read off the sheet. A source grep that takes the FIRST matching rule proves
    // nothing about the cascade - appending a second rule for the same selector
    // reverts the chip and the grep never notices - so `only` asserts each
    // selector is declared exactly once and reads that one rule.
    const only = (selector) => {
      const matches = rules("src/components/widgets/ChipRow.vue", selector);
      expect(matches, selector).toHaveLength(1);
      return matches[0];
    };

    const base = only(".chip-row__chip, .chip-row__more");
    expect(base).toContain("border: 1px solid rgb(var(--v-theme-border))");
    expect(base).toContain("background: rgb(var(--v-theme-input-background))");
    for (const unfilled of [
      ".chip-row__chip--dashed",
      ".chip-row__chip--fact",
      ".chip-row__more",
    ]) {
      expect(only(unfilled), unfilled).toContain("background: transparent");
    }
  });

  it("mutes the glyph once, so a dashed chip's is not 0.7 of 0.7", () => {
    // `--dashed` mutes the chip's own ink, so an `opacity` on the icon on top
    // of it draws that glyph at 0.49 - 3.10:1 on the light card, under the
    // 4.5:1 design-tokens.test.js holds the token to. That guard models one
    // application of the token, so nothing but this notices the product.
    const [icon] = rules(
      "src/components/widgets/ChipRow.vue",
      ".chip-row__icon",
    );
    expect(icon).not.toMatch(/opacity:/);
    expect(icon).toContain(
      "color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary))",
    );
    expect(
      rules("src/components/widgets/ChipRow.vue", ".chip-row__chip--dashed")[0],
    ).toContain(
      "color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary))",
    );
  });

  it("draws the same fact chip ⓘ draws, so one list reads as one list", () => {
    // The popover wraps its chips and the row clips its own, so InfoPopover
    // cannot mount a ChipRow and spells the chip a second time. Nothing but this
    // keeps the two copies agreeing; the comments in both files claim they do.
    const card = rules("src/components/widgets/ChipRow.vue", [
      ".chip-row__chip, .chip-row__more",
      ".chip-row__chip--fact",
    ]).join("");
    const popover = rules(
      "src/components/widgets/InfoPopover.vue",
      ".info-popover__chip",
    ).join("");

    expect(popover).toContain("height: var(--tag-h-xs)");
    expect(popover).toContain("padding: 0 var(--space-2)");
    expect(popover).toContain("font-size: var(--text-2xs)");
    expect(popover).toContain("border-radius: var(--radius-sm)");
    expect(popover).toContain("border: 1px solid rgb(var(--v-theme-border))");
    // Unfilled, like the card's fact chip and unlike its model chip. The
    // longhand counts too, or a `background-color` fill slips past.
    expect(popover).not.toMatch(/background(-color)?:/);
    for (const declaration of [
      "height: var(--tag-h-xs)",
      "padding: 0 var(--space-2)",
      "font-size: var(--text-2xs)",
      "border-radius: var(--radius-sm)",
      "border: 1px solid rgb(var(--v-theme-border))",
    ]) {
      expect(card, declaration).toContain(declaration);
    }
    expect(
      rules("src/components/widgets/ChipRow.vue", ".chip-row__chip--fact"),
    ).toEqual([expect.stringContaining("background: transparent")]);
  });
});
