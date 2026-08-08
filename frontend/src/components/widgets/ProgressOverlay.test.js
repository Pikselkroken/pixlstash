/**
 * ProgressOverlay accessibility (issue #758).
 *
 * The shared progress surface for export, plugin progress and, in v1.10.0, the
 * model shelf's cross-drive move. It shipped with no ARIA at all, a failure
 * state encoded only as a red card, and an indeterminate animation that ran
 * regardless of `prefers-reduced-motion`.
 *
 * The announcement tests are the ones with teeth. Getting ARIA *present* is
 * easy; the failure mode that actually makes a screen reader unusable is a
 * correct-looking `aria-live` region that re-reads on every tick of a
 * multi-gigabyte copy.
 */

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import ProgressOverlay from "./ProgressOverlay.vue";

const stubs = {
  "v-icon": { template: "<i class='v-icon'><slot /></i>" },
};

const mountOverlay = (props = {}) =>
  mount(ProgressOverlay, {
    props: { visible: true, message: "Exporting", ...props },
    global: { stubs },
  });

const live = (w) => w.find("[aria-live]");
const bar = (w) => w.find('[role="progressbar"]');

describe("the progress bar exposes its value", () => {
  it("reports a determinate value with the full range", () => {
    const w = mountOverlay({ percent: 42, status: "running" });
    expect(bar(w).attributes("aria-valuenow")).toBe("42");
    expect(bar(w).attributes("aria-valuemin")).toBe("0");
    expect(bar(w).attributes("aria-valuemax")).toBe("100");
  });

  it("omits aria-valuenow when indeterminate rather than claiming zero", () => {
    // A progressbar reporting 0 forever is a worse lie than one reporting
    // nothing: "no value" is the defined way to say the total is unknown.
    const w = mountOverlay({ indeterminate: true, status: "running" });
    expect(bar(w).attributes("aria-valuenow")).toBeUndefined();
  });

  it("labels itself from the message", () => {
    const w = mountOverlay({ message: "Moving adapters" });
    expect(bar(w).attributes("aria-label")).toBe("Moving adapters");
  });

  it("marks the overlay busy only while work is in flight", () => {
    expect(mountOverlay({ status: "running" }).attributes("aria-busy")).toBe(
      "true",
    );
    expect(
      mountOverlay({ status: "completed" }).attributes("aria-busy"),
    ).toBeUndefined();
  });
});

describe("announcements stay coarse enough to be usable", () => {
  it("does not re-announce on every percent", async () => {
    // The regression this exists to stop. The region is aria-atomic, so any
    // text change re-reads the whole thing. A long copy ticking 1% at a time
    // would make the reader talk without pause.
    const w = mountOverlay({ percent: 30, status: "running" });
    const before = live(w).text();
    await w.setProps({ percent: 31 });
    expect(live(w).text()).toBe(before);
    await w.setProps({ percent: 44 });
    expect(live(w).text()).toBe(before);
  });

  it("does announce when a quartile is crossed", async () => {
    const w = mountOverlay({ percent: 30, status: "running" });
    const before = live(w).text();
    await w.setProps({ percent: 51 });
    expect(live(w).text()).not.toBe(before);
    expect(live(w).text()).toContain("50 percent");
  });

  it("announces each terminal state by name", async () => {
    const w = mountOverlay({ percent: 100, status: "running" });
    for (const [status, word] of [
      ["completed", "Done"],
      ["failed", "Failed"],
      ["cancelled", "Cancelled"],
    ]) {
      await w.setProps({ status });
      expect(live(w).text()).toContain(word);
    }
  });

  it("says it is working when there is no total to report", () => {
    const w = mountOverlay({ indeterminate: true, status: "running" });
    expect(live(w).text()).toContain("Working");
  });

  it("escalates to role=alert only on failure", async () => {
    const w = mountOverlay({ status: "running" });
    expect(live(w).attributes("role")).toBe("status");
    await w.setProps({ status: "failed" });
    expect(live(w).attributes("role")).toBe("alert");
  });

  it("keeps the tick-by-tick counter away from assistive tech", () => {
    // Same numbers already reach a reader through aria-valuenow. Left exposed,
    // this line is a second thing changing inside the atomic region.
    const w = mountOverlay({ count: 3, total: 9, status: "running" });
    const meta = w.find(".progress-overlay__meta");
    expect(meta.text()).toContain("3 / 9");
    expect(meta.attributes("aria-hidden")).toBe("true");
  });
});

describe("failure is not carried by colour alone", () => {
  it("adds a glyph and a word, not just the red card", () => {
    const w = mountOverlay({ status: "failed" });
    expect(w.classes()).toContain("progress-overlay--error");
    expect(w.find(".progress-overlay__state-ico").exists()).toBe(true);
    expect(w.find(".progress-overlay__state-word").text()).toContain("Failed");
  });

  it("shows no state word while merely running", () => {
    const w = mountOverlay({ status: "running" });
    expect(w.find(".progress-overlay__state-word").exists()).toBe(false);
  });

  it("names cancelled and completed too, so neither is colour or absence", () => {
    for (const [status, word] of [
      ["cancelled", "Cancelled"],
      ["completed", "Done"],
    ]) {
      const w = mountOverlay({ status });
      expect(w.find(".progress-overlay__state-word").text()).toContain(word);
    }
  });
});

describe("the indeterminate animation respects reduced motion", () => {
  // Asserted against the component source: jsdom does not evaluate media
  // queries, and this defect lives in the stylesheet, not the markup.
  const source = readFileSync(
    resolve("src/components/widgets/ProgressOverlay.vue"),
    "utf8",
  );
  const reducedMotionBlock = source.slice(
    source.indexOf("@media (prefers-reduced-motion: reduce)"),
  );

  it("has a reduced-motion block at all", () => {
    expect(source).toContain("@media (prefers-reduced-motion: reduce)");
  });

  it("stops the slide rather than merely slowing it", () => {
    expect(reducedMotionBlock).toContain("animation: none");
  });

  it("still shows the track as busy once the motion is gone", () => {
    // Killing the animation without widening the fill would leave a 38% stub
    // parked at the left, which reads as stalled rather than working.
    expect(reducedMotionBlock).toMatch(/width:\s*100%/);
  });
});
