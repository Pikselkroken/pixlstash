// The clip's sound lane (#2807): click and arrow keys seek on the video's
// clock, the aria values name the position, and the audio covers its share
// of the lane.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";
import AudioWaveform from "./AudioWaveform.vue";

const PEAKS = Array.from({ length: 512 }, () => 0.5);

function mountLane(props = {}) {
  return mount(AudioWaveform, {
    props: { peaks: PEAKS, duration: 10, videoDuration: 10, currentTime: 0, ...props },
    attachTo: document.body,
  });
}

// jsdom has no layout: give the lane a 200px box so clientX maps to a share.
function clickAt(w, clientX) {
  const el = w.find(".audio-waveform").element;
  el.getBoundingClientRect = () => ({ left: 0, width: 200, top: 0, right: 200, bottom: 16, height: 16 });
  return w.find(".audio-waveform").trigger("click", { clientX });
}

describe("AudioWaveform", () => {
  it("seeks to the clicked share of the video's length", async () => {
    const w = mountLane({ videoDuration: 20, duration: 20 });
    await clickAt(w, 50); // a quarter of 200px
    expect(w.emitted("seek")[0][0]).toBeCloseTo(5, 5);
    w.unmount();
  });

  it("steps five seconds on the arrow keys, clamped to the clip", async () => {
    const w = mountLane({ currentTime: 3 });
    await w.find(".audio-waveform").trigger("keydown", { key: "ArrowRight" });
    await w.find(".audio-waveform").trigger("keydown", { key: "ArrowLeft" });
    await w.find(".audio-waveform").trigger("keydown", { key: "ArrowLeft" });
    expect(w.emitted("seek").map((e) => e[0])).toEqual([8, 0, 0]);
    w.unmount();
  });

  it("does not let the arrow keys reach the lightbox", async () => {
    const w = mountLane({ currentTime: 3 });
    const outer = vi.fn();
    document.addEventListener("keydown", outer);
    await w.find(".audio-waveform").trigger("keydown", { key: "ArrowRight" });
    document.removeEventListener("keydown", outer);
    expect(outer).not.toHaveBeenCalled();
    w.unmount();
  });

  it("exposes its position as a slider, in m:ss", () => {
    const w = mountLane({ currentTime: 83, videoDuration: 200, duration: 200 });
    const el = w.find('[role="slider"]');
    expect(el.attributes("aria-valuenow")).toBe("83");
    expect(el.attributes("aria-valuemax")).toBe("200");
    expect(el.attributes("aria-valuetext")).toBe("1:23");
    w.unmount();
  });

  it("neither seeks nor takes focus before the video's length is known", async () => {
    const w = mountLane({ videoDuration: null });
    await clickAt(w, 100);
    await w.find(".audio-waveform").trigger("keydown", { key: "ArrowRight" });
    expect(w.emitted("seek")).toBeUndefined();
    expect(w.find('[role="slider"]').attributes("tabindex")).toBe("-1");
    w.unmount();
  });

  it("covers its share of the lane: the audio is the video's length times the ratio", () => {
    // Audio 5 s of a 10 s video: the peaks fill the left half only.
    const w = mountLane({ duration: 5, videoDuration: 10, currentTime: 0 });
    const rest = w.find(".audio-waveform-rest").attributes("d");
    const xs = [...rest.matchAll(/M([0-9.]+) /g)].map((m) => Number(m[1]));
    expect(Math.max(...xs)).toBeLessThan(0.5);
    expect(Math.max(...xs)).toBeGreaterThan(0.4);
    w.unmount();
  });
});
