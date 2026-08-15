import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import StatsHistogram from "./StatsHistogram.vue";

const BUCKETS = [
  { label: "Unscored", count: 0 },
  { label: "1", count: 5 },
  { label: "2", count: 10 },
];

const CONF_BUCKETS = [
  { label: "0.9+", count: 4 },
  { label: "0.8", count: 2 },
];

const mountHist = (props = {}) =>
  mount(StatsHistogram, {
    props: { buckets: BUCKETS, ariaLabel: "Score distribution", ...props },
  });

describe("StatsHistogram", () => {
  it("scales bars against the tallest bucket", () => {
    const rects = mountHist().findAll("rect");
    expect(Number(rects[2].attributes("width"))).toBe(208); // the max
    expect(Number(rects[1].attributes("width"))).toBe(104); // half of it
  });

  // A single picture must still be a visible sliver rather than nothing.
  it("gives a non-zero count at least 2px, and zero exactly none", () => {
    const rects = mountHist({
      buckets: [
        { label: "a", count: 0 },
        { label: "b", count: 1 },
        { label: "c", count: 10000 },
      ],
    }).findAll("rect");
    expect(Number(rects[0].attributes("width"))).toBe(0);
    expect(Number(rects[1].attributes("width"))).toBe(2);
  });

  it("prints the count inside a wide bar and outside a narrow one", () => {
    const w = mountHist();
    expect(w.findAll(".bar-count-inner")).toHaveLength(2); // 104px and 208px
    expect(w.findAll(".bar-count-outer")).toHaveLength(0);
    const narrow = mountHist({
      buckets: [
        { label: "a", count: 1 },
        { label: "b", count: 100 },
      ],
    });
    expect(narrow.findAll(".bar-count-outer")).toHaveLength(1);
  });

  it("omits an empty bucket's count entirely", () => {
    expect(mountHist().findAll("text")).toHaveLength(5); // 3 labels + 2 counts
  });

  // The reason interactivity is a predicate: a row that announces itself as a
  // button and then ignores the press is worse than one that never claimed to
  // be. The live case is the confidence chart, which is inert until a tag is
  // selected (StatsSidebar passes `() => !!selectedConfTag`).
  it("gives a dead row no role, no tabindex and no select", async () => {
    const w = mountHist({ buckets: CONF_BUCKETS, interactive: () => false });
    const rows = w.findAll("g");
    expect(rows[0].attributes("role")).toBeUndefined();
    expect(rows[0].attributes("tabindex")).toBeUndefined();
    expect(rows[0].classes()).toContain("hist-bar-row--disabled");
    await rows[0].trigger("click");
    await rows[0].trigger("keydown.enter");
    expect(w.emitted("select")).toBeUndefined();
  });

  it("emits select with the bucket and its index", async () => {
    const w = mountHist({ interactive: (item) => item.label !== "1" });
    const rows = w.findAll("g");
    await rows[1].trigger("click");
    expect(w.emitted("select")).toBeUndefined();
    await rows[2].trigger("click");
    expect(w.emitted("select")[0]).toEqual([BUCKETS[2], 2]);
  });

  // The score chart passes no predicate at all now: "Unscored" is a filter of
  // its own (`unscored=1`), so its row is as clickable as any star's.
  it("makes every row interactive when no predicate is passed", async () => {
    const w = mountHist();
    const rows = w.findAll("g");
    expect(rows[0].attributes("role")).toBe("button");
    expect(rows[0].attributes("tabindex")).toBe("0");
    expect(rows[0].classes()).toContain("hist-bar-row");
    await rows[0].trigger("click");
    expect(w.emitted("select")[0]).toEqual([BUCKETS[0], 0]);
    await rows[0].trigger("keydown.enter");
    expect(w.emitted("select")[1]).toEqual([BUCKETS[0], 0]);
  });

  it("marks the active row", () => {
    const rows = mountHist({ active: (item) => item.label === "2" }).findAll(
      "g",
    );
    expect(rows[2].classes()).toContain("hist-bar-row--active");
    expect(rows[1].classes()).not.toContain("hist-bar-row--active");
  });
});
