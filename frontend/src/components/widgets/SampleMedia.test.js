// A training sample is an image, or a video model's MP4 clip.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import SampleMedia from "./SampleMedia.vue";

describe("SampleMedia", () => {
  it("draws an image sample as an image", () => {
    const w = mount(SampleMedia, { props: { src: "/s/a.png?t=1", alt: "step 500" } });
    expect(w.element.tagName).toBe("IMG");
    expect(w.attributes("alt")).toBe("step 500");
  });

  it("draws a clip's first frame in a grid, and plays it full size", async () => {
    const w = mount(SampleMedia, {
      props: { src: "/s/1__000000500_0.mp4?t=1", alt: "step 500" },
      attrs: { class: "trl-cell" },
    });
    expect(w.element.tagName).toBe("VIDEO");
    expect(w.attributes("src")).toBe("/s/1__000000500_0.mp4?t=1#t=0.1");
    expect(w.attributes("aria-label")).toBe("step 500");
    expect(w.classes()).toContain("trl-cell");
    expect(w.element.muted).toBe(true);
    expect(w.attributes("controls")).toBeUndefined();

    await w.setProps({ playing: true });
    expect(w.attributes("src")).toBe("/s/1__000000500_0.mp4?t=1");
    expect(w.attributes("controls")).toBeDefined();
    expect(w.attributes("autoplay")).toBeDefined();
  });
});
