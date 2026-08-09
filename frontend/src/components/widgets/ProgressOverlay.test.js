// The shared progress card used by export, plugin runs and smart-score sorts.
//
// These tests pin the accessibility contract (#758): the bar is announced as a
// progress bar, the live region says start/progress/finish/failure without
// repeating itself per item, and failure survives a monochrome read.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import ProgressOverlay from "./ProgressOverlay.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

function mountOverlay(props = {}) {
  return mount(ProgressOverlay, {
    ...globalOpts,
    props: { visible: true, status: "running", message: "Exporting", ...props },
  });
}

const live = (w) => w.find('[role="status"]').text().replace(/\s+/g, " ");
const bar = (w) => w.find('[role="progressbar"]');
const card = (w) => w.find(".progress-overlay");

describe("ProgressOverlay accessibility", () => {
  it("exposes the fill as a progress bar with its value", () => {
    const w = mountOverlay({ percent: 42 });
    expect(bar(w).attributes("aria-valuenow")).toBe("42");
    expect(bar(w).attributes("aria-valuemin")).toBe("0");
    expect(bar(w).attributes("aria-valuemax")).toBe("100");
    expect(bar(w).attributes("aria-label")).toBe("Exporting");
  });

  it("omits the value when indeterminate rather than claiming zero", () => {
    // A pinned 0% would be read as "no progress yet" forever.
    const w = mountOverlay({ indeterminate: true, percent: 0 });
    expect(bar(w).attributes("aria-valuenow")).toBeUndefined();
    expect(live(w)).toBe("Exporting: working.");
  });

  it("marks the card busy until it reaches a terminal status", async () => {
    const w = mountOverlay({ percent: 10 });
    expect(card(w).attributes("aria-busy")).toBe("true");
    await w.setProps({ status: "completed", percent: 100 });
    expect(card(w).attributes("aria-busy")).toBe("false");
  });

  it("keeps the live region mounted while the card is hidden", async () => {
    // A region inserted together with its first text is not reliably
    // announced, so the opening line would be the one that goes missing.
    const w = mountOverlay({ visible: false, percent: 0 });
    expect(card(w).exists()).toBe(false);
    expect(w.find('[role="status"]').exists()).toBe(true);
    expect(live(w)).toBe("");
    await w.setProps({ visible: true });
    expect(live(w)).toBe("Exporting: 0% complete.");
  });

  it("announces start, progress and completion", async () => {
    const w = mountOverlay({ percent: 0 });
    expect(live(w)).toBe("Exporting: 0% complete.");
    await w.setProps({ percent: 50 });
    expect(live(w)).toBe("Exporting: 50% complete.");
    await w.setProps({ status: "completed", percent: 100 });
    expect(live(w)).toBe("Exporting: complete.");
  });

  it("rounds the announcement to tens so a per-item run is not repeated", async () => {
    // The visible card still ticks per item; the live region must not.
    const w = mountOverlay({ percent: 41 });
    expect(live(w)).toBe("Exporting: 40% complete.");
    await w.setProps({ percent: 49 });
    expect(live(w)).toBe("Exporting: 40% complete.");
  });

  it("keeps the stated percentage inside min/max and survives a NaN", async () => {
    // ARIA requires valuenow within the range, and "NaN% complete" is worse
    // than silence. Every current caller clamps; the next one may not.
    const w = mountOverlay({ percent: 150 });
    expect(bar(w).attributes("aria-valuenow")).toBe("100");
    expect(live(w)).toBe("Exporting: 100% complete.");
    await w.setProps({ percent: -5 });
    expect(bar(w).attributes("aria-valuenow")).toBe("0");
    expect(live(w)).toBe("Exporting: 0% complete.");
    await w.setProps({ percent: NaN });
    expect(bar(w).attributes("aria-valuenow")).toBe("0");
    expect(live(w)).toBe("Exporting: 0% complete.");
  });

  it("draws the fill at the clamped percentage, so the bar agrees with what it says", async () => {
    // The fill used to bind the raw prop: NaN rendered `width: NaN%` and
    // anything over 100 overflowed the track (#782).
    const fill = (w) => w.find(".progress-overlay__fill").attributes("style");
    const w = mountOverlay({ percent: 42 });
    expect(fill(w)).toContain("width: 42%");
    await w.setProps({ percent: 150 });
    expect(fill(w)).toContain("width: 100%");
    await w.setProps({ percent: -5 });
    expect(fill(w)).toContain("width: 0%");
    await w.setProps({ percent: NaN });
    expect(fill(w)).toContain("width: 0%");
    expect(fill(w)).not.toContain("NaN");
  });

  it("announces failure and cancellation", async () => {
    const w = mountOverlay({ status: "failed", percent: 33 });
    expect(live(w)).toBe("Exporting: failed.");
    await w.setProps({ status: "cancelled" });
    expect(live(w)).toBe("Exporting: cancelled.");
  });

  it("still announces a terminal status when the card is hidden in the same tick", async () => {
    // Both export cancel paths set status and visible=false together. Gating
    // the terminal branches on `visible` would end those runs in silence.
    const w = mountOverlay({ percent: 40 });
    expect(live(w)).toBe("Exporting: 40% complete.");
    await w.setProps({ status: "cancelled", visible: false });
    expect(card(w).exists()).toBe(false);
    expect(live(w)).toBe("Exporting: cancelled.");
    await w.setProps({ status: "completed" });
    expect(live(w)).toBe("Exporting: complete.");
  });

  it("goes quiet once a hidden overlay is reset to a non-terminal status", async () => {
    // The reset must clear the region, or the next run's first line would be
    // identical to the last one and a live region never re-reads unchanged text.
    const w = mountOverlay({ status: "completed", visible: false });
    expect(live(w)).toBe("Exporting: complete.");
    await w.setProps({ status: "idle" });
    expect(live(w)).toBe("");
  });

  it("carries failure in a glyph and a word, not only the red card", () => {
    const w = mountOverlay({ status: "failed" });
    expect(card(w).classes()).toContain("progress-overlay--error");
    expect(w.find(".progress-overlay__failed").exists()).toBe(true);
    expect(w.find(".progress-overlay__failed").text()).toContain("Failed");
    expect(
      w.find(".progress-overlay__failed").find("v-icon-stub").exists(),
    ).toBe(true);
  });

  it("shows no failure marker while running", () => {
    expect(
      mountOverlay({ percent: 5 }).find(".progress-overlay__failed").exists(),
    ).toBe(false);
  });
});
