// The picture strip both duplicate rows are built on.
//
// It was extracted out of `DedupGroupRow` so the Mixed stacks row could mount
// the same tile rather than grow a second copy of it. What is pinned here is
// what both rows depend on and neither can see: the height-driven sizing math,
// the roving tab stop, the corner columns, the chip system, and the member
// cursor's scroll.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

import DedupPictureStrip from "./DedupPictureStrip.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

/** One tile, in the shape a row hands over. */
function tile(over = {}) {
  return {
    key: `t:${over.src ?? "a"}`,
    src: "/thumb/1",
    ariaLabel: "Picture 1 of 2",
    title: "Click it",
    ...over,
  };
}

function mountStrip(props = {}) {
  return mount(DedupPictureStrip, {
    ...globalOpts,
    props: { tiles: [tile({ key: "a" }), tile({ key: "b" })], ...props },
  });
}

describe("DedupPictureStrip: one number lays the strip out", () => {
  // Sizing the box in CSS and the placeholder in JS from two copies of the
  // height is how a row starts jumping as its images decode.
  it("publishes the box, the panorama ceiling and the fallback together", () => {
    const style = mountStrip({ thumbHeight: 184 })
      .find(".gstrip")
      .attributes("style");
    expect(style).toContain("--gthumb-h: 184px");
    // 2.4:1 is the ceiling, and it scales with the height so the widest allowed
    // SHAPE never changes.
    expect(style).toContain("--gthumb-max-w: 442px");
    // The unknown-shape fallback is a 4:3 box at the strip's height.
    expect(style).toContain("--gthumb-fallback-w: 245px");
  });

  // Stored width/height ignore EXIF rotation, so the placeholder is only an
  // estimate; the decoded image corrects it.
  it("estimates a placeholder's shape from stored dimensions", () => {
    const wrapper = mountStrip({
      loadThumbnails: false,
      thumbHeight: 100,
      tiles: [
        tile({ key: "a", box: { width: 300, height: 200 } }),
        tile({ key: "b" }),
      ],
    });
    const boxes = wrapper.findAll(".gt--placeholder");
    expect(boxes[0].attributes("style")).toContain("width: 150px");
    // Unknown shape carries no inline width and falls through to the 4:3 box.
    expect(boxes[1].attributes("style")).toBeUndefined();
    expect(wrapper.find("img").exists()).toBe(false);
  });

  it("clamps a beyond-panoramic estimate at the ceiling", () => {
    const wrapper = mountStrip({
      loadThumbnails: false,
      thumbHeight: 100,
      tiles: [tile({ key: "a", box: { width: 3000, height: 200 } })],
    });
    expect(wrapper.find(".gt--placeholder").attributes("style")).toContain(
      "width: 240px",
    );
  });
});

describe("DedupPictureStrip: the roving tab stop", () => {
  // A screenful of twenty rows holds well over a hundred buttons. A Tab key
  // that walks all of them is a Tab key nobody presses twice.
  it("keeps an unfocused strip out of the tab order", () => {
    const wrapper = mountStrip({ focused: false });
    for (const button of wrapper.findAll(".gthumb")) {
      expect(button.attributes("tabindex")).toBe("-1");
    }
    // The index chips are what `1`-`9` addresses, so they only appear where
    // those keys act.
    expect(wrapper.find(".gnum").exists()).toBe(false);
  });

  it("puts a focused strip's tiles in the tab order, with their indices", () => {
    const wrapper = mountStrip({ focused: true });
    const buttons = wrapper.findAll(".gthumb");
    for (const button of buttons) {
      expect(button.attributes("tabindex")).toBe("0");
    }
    expect(wrapper.findAll(".gnum").map((n) => n.text())).toEqual(["1", "2"]);
  });
});

describe("DedupPictureStrip: the chip system", () => {
  it("draws the cover border and its corner word", () => {
    const wrapper = mountStrip({
      tiles: [tile({ key: "a", cover: true, cornerLabel: "Cover" })],
    });
    expect(wrapper.find(".gthumb").classes()).toContain("gthumb--cover");
    expect(wrapper.find(".gcv").text()).toBe("Cover");
  });

  // A marked tile is the row's EVIDENCE. Fading it would say "inert" about the
  // only tiles that are not, so the mark is a border plus a glyph chip and
  // never the excluded fade.
  it("marks a stranger with a border and a glyph, never a fade", () => {
    const wrapper = mountStrip({
      tiles: [tile({ key: "a", marked: true, markIcon: "mdi-call-split" })],
    });
    const button = wrapper.find(".gthumb");
    expect(button.classes()).toContain("gthumb--marked");
    expect(button.classes()).not.toContain("gthumb--out");
    expect(wrapper.find(".gmark").exists()).toBe(true);
    // Glyph only, no word, at every size.
    expect(wrapper.find(".gmark").text()).toBe("");
  });

  // The two bottom-left tenants never coexist: a mixed stack has no cover to
  // name, and a duplicate group has no stranger to mark.
  it("gives the bottom-left corner to the word when both are offered", () => {
    const wrapper = mountStrip({
      tiles: [
        tile({ key: "a", cornerLabel: "Cover", markIcon: "mdi-call-split" }),
      ],
    });
    expect(wrapper.find(".gcv").exists()).toBe(true);
    expect(wrapper.find(".gmark").exists()).toBe(false);
  });

  it("draws the hover chip only while thumbnails are loaded", () => {
    const chip = { icon: "mdi-brain", text: "2.50", title: "Smart score 2.50" };
    expect(
      mountStrip({ tiles: [tile({ key: "a", chip })] })
        .find(".gsmart")
        .text(),
    ).toBe("2.50");
    expect(
      mountStrip({ loadThumbnails: false, tiles: [tile({ key: "a", chip })] })
        .find(".gsmart")
        .exists(),
    ).toBe(false);
  });

  // The queue row's case: the lock is a per-unit fact, so the chip follows it.
  it("names a locked tile with the chip by default", () => {
    const wrapper = mountStrip({ tiles: [tile({ key: "a", locked: true })] });
    expect(wrapper.find(".gthumb").classes()).toContain("gthumb--locked");
    expect(wrapper.find(".glock").exists()).toBe(true);
  });

  // The Mixed row's case: a locked set freezes the whole STACK and the payload
  // names no member, so every tile is locked and only the pictures a refusal
  // actually named wear the chip. A chip on every tile would be a lock field.
  it("lets a row hold the chip back while every tile is still frozen", () => {
    const wrapper = mountStrip({
      tiles: [
        tile({ key: "a", locked: true, lockChip: false }),
        tile({ key: "b", locked: true, lockChip: true }),
      ],
    });
    const units = wrapper.findAll(".gunit");
    expect(units[0].find(".gthumb").classes()).toContain("gthumb--locked");
    expect(units[0].find(".glock").exists()).toBe(false);
    expect(units[1].find(".glock").exists()).toBe(true);
  });
});

describe("DedupPictureStrip: the member cursor", () => {
  // A RAIL, not a ring: the tile's border already carries two meanings (accent
  // for the cover, warning for a stranger) and a third would be a third colour
  // on one edge nobody could read.
  it("marks the cursor tile without touching its border", () => {
    const wrapper = mountStrip({ cursorIndex: 1 });
    const units = wrapper.findAll(".gunit");
    expect(units[0].classes()).not.toContain("gunit--cursor");
    expect(units[1].classes()).toContain("gunit--cursor");
    // The strip pays for the rail's height only when it draws one.
    expect(wrapper.find(".gstrip").classes()).toContain("gstrip--cursor");
  });

  it("draws no rail and no extra room when there is no cursor", () => {
    const wrapper = mountStrip({ cursorIndex: -1 });
    expect(wrapper.find(".gunit--cursor").exists()).toBe(false);
    expect(wrapper.find(".gstrip").classes()).not.toContain("gstrip--cursor");
  });

  // The strip is a horizontal scroller, so a digit that moved the cursor past
  // the right edge would otherwise read as a dead key.
  it("scrolls the cursor into view when it moves", async () => {
    const wrapper = mountStrip({ cursorIndex: 0 });
    const units = wrapper.findAll(".gunit");
    const spies = units.map((u) => {
      const spy = vi.fn();
      u.element.scrollIntoView = spy;
      return spy;
    });
    await wrapper.setProps({ cursorIndex: 1 });
    expect(spies[1]).toHaveBeenCalledWith({
      block: "nearest",
      inline: "nearest",
    });
    expect(spies[0]).not.toHaveBeenCalled();
  });
});

describe("DedupPictureStrip: what it reports", () => {
  it("emits the tile that was clicked and the one that was right-clicked", async () => {
    const wrapper = mountStrip();
    const buttons = wrapper.findAll(".gthumb");
    await buttons[1].trigger("click");
    await buttons[0].trigger("contextmenu");
    expect(wrapper.emitted("pick")[0][0].key).toBe("b");
    expect(wrapper.emitted("pick")[0][1]).toBe(1);
    expect(wrapper.emitted("toggle")[0][0].key).toBe("a");
  });

  it("carries each tile's own name and tooltip", () => {
    const wrapper = mountStrip({
      tiles: [tile({ key: "a", ariaLabel: "Picture 1 of 1", title: "Do it" })],
    });
    const button = wrapper.find(".gthumb");
    expect(button.attributes("aria-label")).toBe("Picture 1 of 1");
    expect(button.attributes("title")).toBe("Do it");
  });
});
