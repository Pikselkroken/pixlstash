// MovesReview - the reconciliation queue screen (v1.11 Phase 5).
//
// The screen is a thin view over useMovesStore (its own test covers fetch /
// apply / dismiss semantics); what is worth pinning here is what the VIEW
// decides: an unambiguous row reads as a clean swap, an ambiguous row offers
// exactly the two named choices and never applies on its own, and an
// off-layout row offers no action at all.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getPendingMoves = vi.fn();
const applyMoves = vi.fn();
const dismissMoves = vi.fn();
vi.mock("../../api/moves", () => ({
  getPendingMoves: (...args) => getPendingMoves(...args),
  applyMoves: (...args) => applyMoves(...args),
  dismissMoves: (...args) => dismissMoves(...args),
}));

import MovesReview from "./MovesReview.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

function summary({ unambiguous = [], ambiguous = [], off_layout = [] } = {}) {
  return { unambiguous, ambiguous, off_layout };
}

async function mountScreen(body) {
  getPendingMoves.mockResolvedValue(body);
  const wrapper = mount(MovesReview, globalOpts);
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  getPendingMoves.mockReset();
  applyMoves.mockReset().mockResolvedValue({ applied_picture_ids: [] });
  dismissMoves.mockReset().mockResolvedValue({ dismissed_review_ids: [] });
});

describe("MovesReview - unambiguous", () => {
  it("reads a same-facet swap as one arrow, not two separate tags", async () => {
    const wrapper = await mountScreen(
      summary({
        unambiguous: [
          {
            review_id: 1,
            picture_id: 11,
            old_path: "/lib/2024 Shoots/mira.png",
            new_path: "/lib/Client · Nordvik/mira.png",
            removals: [{ facet: "project", name: "2024 Shoots" }],
            additions: [{ facet: "project", name: "Client · Nordvik" }],
          },
        ],
      }),
    );

    expect(wrapper.find(".mv-etag--out").text()).toContain("2024 Shoots");
    expect(wrapper.find(".mv-etag--in").text()).toContain("Client · Nordvik");
  });

  it("Apply all N calls applyMoves with every unambiguous review_id", async () => {
    const wrapper = await mountScreen(
      summary({
        unambiguous: [
          { review_id: 1, picture_id: 11, removals: [], additions: [] },
          { review_id: 2, picture_id: 12, removals: [], additions: [] },
        ],
      }),
    );

    await wrapper.find(".mv-card--clear button").trigger("click");
    await flushPromises();

    expect(applyMoves).toHaveBeenCalledWith([1, 2]);
  });
});

describe("MovesReview - ambiguous", () => {
  const AMBIGUOUS_ITEM = {
    review_id: 7,
    picture_id: 70,
    old_path: "/lib/2024 Shoots/mira.png",
    new_path: "/lib/Client · Nordvik/mira.png",
    removals: [{ facet: "project", name: "2024 Shoots" }],
    additions: [],
    current: { project: ["2024 Shoots", "Client · Nordvik"] },
  };

  it("shows the current memberships that make it ambiguous", async () => {
    const wrapper = await mountScreen(summary({ ambiguous: [AMBIGUOUS_ITEM] }));
    expect(wrapper.find(".mv-current").text()).toContain("2024 Shoots");
    expect(wrapper.find(".mv-current").text()).toContain("Client · Nordvik");
  });

  it("'Keep both' dismisses and never calls apply", async () => {
    const wrapper = await mountScreen(summary({ ambiguous: [AMBIGUOUS_ITEM] }));
    const buttons = wrapper.findAll(".mv-row--ambiguous button");
    const keepBoth = buttons.find((b) => b.text().includes("Keep both"));

    await keepBoth.trigger("click");
    await flushPromises();

    expect(dismissMoves).toHaveBeenCalledWith([7]);
    expect(applyMoves).not.toHaveBeenCalled();
  });

  it("names the destination, never a generic verb, on the canonical no-addition case", async () => {
    // This is the design mock's own example: the picture already belongs to
    // the folder it moved into, so there is nothing to ADD, only something
    // to LEAVE. A button reading "Leave it" here would be applying a removal
    // under a label that describes doing nothing - exactly what shipped once.
    const wrapper = await mountScreen(summary({ ambiguous: [AMBIGUOUS_ITEM] }));
    const buttons = wrapper.findAll(".mv-row--ambiguous button");
    const resolve = buttons.find((b) => !b.text().includes("Keep both"));

    expect(resolve.text()).toContain("Only Client · Nordvik now");
  });

  it("the resolve button applies only that one review_id", async () => {
    const wrapper = await mountScreen(summary({ ambiguous: [AMBIGUOUS_ITEM] }));
    const buttons = wrapper.findAll(".mv-row--ambiguous button");
    const resolve = buttons.find((b) => !b.text().includes("Keep both"));

    await resolve.trigger("click");
    await flushPromises();

    expect(applyMoves).toHaveBeenCalledWith([7]);
    expect(dismissMoves).not.toHaveBeenCalled();
  });
});

describe("MovesReview - off-layout", () => {
  it("offers no action at all - already followed, nothing to decide", async () => {
    const wrapper = await mountScreen(
      summary({
        off_layout: [
          { review_id: 9, picture_id: 90, new_path: "/lib/_unsorted/a.png" },
        ],
      }),
    );

    expect(wrapper.find(".mv-chip").exists()).toBe(true);
    expect(wrapper.find(".mv-chip button").exists()).toBe(false);
  });
});

describe("MovesReview - nothing pending", () => {
  it("says so rather than rendering empty cards", async () => {
    const wrapper = await mountScreen(summary());
    expect(wrapper.text()).toContain("Nothing to reconcile");
  });
});

describe("MovesReview - a failed fetch", () => {
  it("shows the error rather than reading as an empty, clean queue", async () => {
    // useMovesStore.fetchPending() catches its own failure into store.error
    // and never rethrows, so the screen has to read that back rather than
    // relying on a try/catch around the call. A screen that fell through to
    // "Nothing to reconcile" on a network failure would tell the owner their
    // library has nothing to review when it simply could not ask.
    getPendingMoves.mockRejectedValue(new Error("network down"));
    const wrapper = mount(MovesReview, globalOpts);
    await flushPromises();

    expect(wrapper.text()).toContain("network down");
    expect(wrapper.text()).not.toContain("Nothing to reconcile");
  });
});
