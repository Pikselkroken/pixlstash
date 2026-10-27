// The score strip's favourite heart: drawn only where the host asks for it,
// red (the `--on` class) only while the picture is a favourite, and a click
// on it toggles without setting a score.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import StarRatingOverlay from "./StarRatingOverlay.vue";

const mountStrip = (props) =>
  mount(StarRatingOverlay, {
    props: { score: 3, ...props },
    global: { stubs: { Tooltip: true } },
  });

describe("StarRatingOverlay favourite heart", () => {
  it("draws no heart unless the host makes it favouritable", () => {
    const wrapper = mountStrip({});
    expect(wrapper.find(".star-overlay__heart").exists()).toBe(false);
    expect(wrapper.find(".star-overlay__rule").exists()).toBe(false);
  });

  it("ends the strip with an outline heart that turns red when on", async () => {
    const wrapper = mountStrip({ favouritable: true });
    const heart = wrapper.find(".star-overlay__heart");
    expect(heart.text()).toBe("mdi-heart-outline");
    expect(heart.classes()).not.toContain("star-overlay__heart--on");
    expect(wrapper.classes()).not.toContain("star-overlay--fav-on");

    await wrapper.setProps({ favourite: true });
    expect(heart.text()).toBe("mdi-heart");
    expect(heart.attributes("aria-pressed")).toBe("true");
    expect(heart.classes()).toContain("star-overlay__heart--on");
    // The host collapses a favourite's strip to its heart off this class.
    expect(wrapper.classes()).toContain("star-overlay--fav-on");
  });

  it("toggles the favourite without setting a score", async () => {
    const wrapper = mountStrip({ favouritable: true });
    await wrapper.find(".star-overlay__heart").trigger("click");
    expect(wrapper.emitted("toggle-favourite")).toHaveLength(1);
    expect(wrapper.emitted("set-score")).toBeUndefined();
  });

  it("keeps the heart when the stars are switched off", () => {
    const wrapper = mountStrip({ favouritable: true, stars: false });
    expect(wrapper.findAll(".star-slot")).toHaveLength(1);
    expect(wrapper.find(".star-overlay__heart").exists()).toBe(true);
  });
});
