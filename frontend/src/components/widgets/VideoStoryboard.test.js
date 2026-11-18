// VideoStoryboard - a clip's eleven frames in the lightbox's band (#2147).

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import VideoStoryboard from "./VideoStoryboard.vue";

const SRC = "/api/v1/pictures/storyboards/7.webp";

function mountBoard(props = {}) {
  return mount(VideoStoryboard, {
    props: { src: SRC, duration: 5, currentTime: 0, ...props },
  });
}

async function loadSprite(wrapper, width = 11 * 320, height = 180) {
  const probe = wrapper.find("img.storyboard-probe");
  Object.defineProperty(probe.element, "naturalWidth", { value: width });
  Object.defineProperty(probe.element, "naturalHeight", { value: height });
  await probe.trigger("load");
}

describe("VideoStoryboard", () => {
  it("draws eleven tiles, and tile i seeks to duration * i / 10", async () => {
    const w = mountBoard({ duration: 5 });
    const tiles = w.findAll("button.storyboard-tile");
    expect(tiles).toHaveLength(11);
    await tiles[0].trigger("click");
    await tiles[6].trigger("click");
    await tiles[10].trigger("click");
    expect(w.emitted("seek").map(([s]) => s)).toEqual([0, 3, 5]);
    expect(tiles[6].attributes("aria-label")).toBe("Seek to 0:03");
  });

  it("marks the tile nearest the playing time as current", async () => {
    const w = mountBoard({ duration: 10, currentTime: 0 });
    const current = () =>
      w
        .findAll("button.storyboard-tile")
        .findIndex((t) => t.attributes("aria-current") === "true");
    expect(current()).toBe(0);
    await w.setProps({ currentTime: 4.6 });
    expect(current()).toBe(5);
    await w.setProps({ currentTime: 10 });
    expect(current()).toBe(10);
  });

  it("does not seek, focus or mark a tile while the clip has no duration", async () => {
    const w = mountBoard({ duration: null });
    const tiles = w.findAll("button.storyboard-tile");
    await tiles[4].trigger("click");
    expect(w.emitted("seek")).toBeUndefined();
    expect(tiles[4].attributes("tabindex")).toBe("-1");
    expect(tiles[4].attributes("aria-disabled")).toBe("true");
    expect(tiles.some((t) => t.attributes("aria-current"))).toBe(false);
    expect(w.find(".storyboard-playhead").exists()).toBe(false);
  });

  it("hides the whole strip and says so when the storyboard 404s", async () => {
    const w = mountBoard();
    await w.find("img.storyboard-probe").trigger("error");
    expect(w.find(".storyboard").exists()).toBe(false);
    expect(w.emitted("unavailable")).toHaveLength(1);
  });

  it("takes the frame's shape from the sprite and draws frames only once loaded", async () => {
    const w = mountBoard();
    expect(w.find(".storyboard-frame").exists()).toBe(false);
    await loadSprite(w, 11 * 320, 180);
    expect(w.findAll(".storyboard-frame")).toHaveLength(11);
    const style = w.find(".storyboard").attributes("style");
    expect(style).toContain(`--frame-aspect: ${320 / 180}`);
    expect(style).toContain(SRC);
  });

  it("lifts the hovered tile's whole frame with its time and place", async () => {
    const w = mountBoard({ duration: 5 });
    await loadSprite(w);
    await w.findAll("button.storyboard-tile")[8].trigger("mouseenter");
    const preview = w.find(".storyboard-preview");
    expect(preview.text()).toContain("Seek to 0:04");
    expect(preview.text()).toContain("9 / 11");
    await w.findAll("button.storyboard-tile")[8].trigger("mouseleave");
    expect(w.find(".storyboard-preview").exists()).toBe(false);
  });
});
