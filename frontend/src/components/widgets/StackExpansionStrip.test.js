// The header strip above an expanded stack.
//
// These pin the claims the strip makes about the stack - exactly one cover, the
// evidence it was grouped on - and the two gestures that follow from disagreeing
// with it, including the one that must stay a no-op.

import { describe, it, expect, vi } from "vitest";
import { mount } from "@vue/test-utils";

// The thumbnail URL builder pulls in the Axios client; the strip only needs a
// string for `<img src>`.
// Mirrors the real builder, which now spells the API base itself rather than
// taking it from the caller: an <img src> never reaches the Axios interceptor.
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id, options = {}) =>
    `http://backend.test/api/v1/pictures/thumbnails/${id}.webp?v=${options.version ?? ""}`,
}));
vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "http://backend.test/api/v1",
}));

import StackExpansionStrip from "./StackExpansionStrip.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

const MEMBERS = [
  { id: 7, thumbnail_version: 2 },
  { id: 8, thumbnail_version: 1 },
  { id: 9, thumbnail_version: 1 },
];

/** Mount the strip over the three-member stack, with overrides. */
function mountStrip(props = {}) {
  return mount(StackExpansionStrip, {
    ...globalOpts,
    props: { count: MEMBERS.length, members: MEMBERS, ...props },
  });
}

describe("StackExpansionStrip - what it says", () => {
  it("leads with the count and carries the evidence beside it", () => {
    // The reason and the date are what make the grouping inspectable rather
    // than something the app just did.
    const wrapper = mountStrip({
      reason: "Burst, 81% similar",
      capturedLabel: "12 May 2026",
    });
    expect(wrapper.find(".sxtitle").text()).toBe("Stack of 3");
    const meta = wrapper.findAll(".sxmeta").map((node) => node.text());
    expect(meta).toEqual(["Burst, 81% similar", "12 May 2026"]);
  });

  it("omits the evidence line when there is none to show", () => {
    // An empty secondary slot would leave a gap that reads as missing data.
    expect(mountStrip().findAll(".sxmeta")).toHaveLength(0);
  });

  it("renders one button per member in stack order", () => {
    // The strip is the only place the stack's order is visible.
    const wrapper = mountStrip();
    const thumbs = wrapper.findAll('[data-testid="stack-member"]');
    expect(thumbs).toHaveLength(3);
    expect(thumbs[0].find("img").attributes("src")).toContain("/7.webp");
    expect(thumbs[2].find("img").attributes("src")).toContain("/9.webp");
  });

  it("loads thumbnails from the backend API origin", () => {
    const src = mountStrip()
      .find('[data-testid="stack-member"] img')
      .attributes("src");
    expect(src).toBe(
      "http://backend.test/api/v1/pictures/thumbnails/7.webp?v=2",
    );
  });
});

describe("StackExpansionStrip - the cover", () => {
  it("marks exactly one member as the cover", () => {
    // Two flagged covers, or none, and the user cannot tell which frame the
    // collapsed grid tile will show.
    const wrapper = mountStrip({ coverId: 8 });
    const pressed = wrapper
      .findAll('[data-testid="stack-member"]')
      .filter((node) => node.attributes("aria-pressed") === "true");
    expect(pressed).toHaveLength(1);
    expect(wrapper.findAll(".sxcv")).toHaveLength(1);
    expect(wrapper.findAll(".sxthumb--cover")).toHaveLength(1);
  });

  it("falls back to stack order when no cover was handed in", () => {
    // A caller that has not lifted the cover into its own state still gets one
    // flagged member rather than none.
    const wrapper = mountStrip();
    const thumbs = wrapper.findAll('[data-testid="stack-member"]');
    expect(thumbs[0].attributes("aria-pressed")).toBe("true");
    expect(thumbs[1].attributes("aria-pressed")).toBe("false");
  });

  it("emits set-cover with the picture id when another member is clicked", () => {
    // The id is the whole payload; the strip owns no stack state.
    const wrapper = mountStrip({ coverId: 7 });
    wrapper.findAll('[data-testid="stack-member"]')[2].trigger("click");
    expect(wrapper.emitted("set-cover")).toEqual([[9]]);
  });

  it("stays silent when the cover itself is clicked", () => {
    // The commonest misclick in the strip; emitting would hand the caller a
    // redundant write for the user to undo.
    const wrapper = mountStrip({ coverId: 7 });
    wrapper.findAll('[data-testid="stack-member"]')[0].trigger("click");
    expect(wrapper.emitted("set-cover")).toBeUndefined();
  });
});

describe("StackExpansionStrip - actions", () => {
  it("emits unstack from the trailing action", () => {
    const wrapper = mountStrip();
    wrapper.get('[data-testid="stack-unstack"]').trigger("click");
    expect(wrapper.emitted("unstack")).toHaveLength(1);
  });

  it("drops both actions in a read-only view", () => {
    // A shared or guest view can look at the stack but cannot rewrite it, so
    // neither Unstack nor a cover change may be reachable.
    const wrapper = mountStrip({ readOnly: true });
    expect(wrapper.find('[data-testid="stack-unstack"]').exists()).toBe(false);

    const thumbs = wrapper.findAll('[data-testid="stack-member"]');
    expect(thumbs).toHaveLength(3);
    expect(thumbs[1].attributes("disabled")).toBeDefined();
    thumbs[1].trigger("click");
    expect(wrapper.emitted("set-cover")).toBeUndefined();
  });
});
