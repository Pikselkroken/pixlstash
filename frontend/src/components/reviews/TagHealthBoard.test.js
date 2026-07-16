// Render/behavior coverage for the tag health board redesign
// (docs/reviews/tag-review-board-redesign-ux-spec.md). The pure ranking
// logic (whyText) is covered directly in tagHealthBoardLogic.test.js; this
// file covers the things only visible once mounted: the persistent rebuild
// control's visibility, the Priority relabel, the Verified column's removal,
// the Why column's rendered text, and the default sort's tie-break.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { h } from "vue";

vi.mock("../../utils/apiClient", () => ({
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: { value: false },
}));

import TagHealthBoard from "./TagHealthBoard.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";

const VIcon = {
  name: "v-icon",
  setup: (_props, { slots }) => () => h("i", { class: "v-icon" }, slots.default?.()),
};

const globalOpts = { stubs: { "v-icon": VIcon } };

function healthRow(overrides = {}) {
  return {
    tag: "shirt",
    est_wrong: 3,
    est_missing: 1,
    mismatch: 0,
    verified_pct: 40,
    boundary_pct: 10,
    overturn_rate: null,
    model_disputes: 0,
    has_model: true,
    last_reviewed_at: null,
    ...overrides,
  };
}

let store;

beforeEach(() => {
  setActivePinia(createPinia());
  store = useReviewSessionsStore();
});

describe("TagHealthBoard: persistent rebuild control (Spec B)", () => {
  it("is visible with zero rows", () => {
    store.healthRows = [];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    expect(wrapper.find(".rs-board-rebuild-persistent").exists()).toBe(true);
    expect(wrapper.find(".rs-board-rebuild-persistent").text()).toContain("Never built");
  });

  it("is visible with many rows too — never hidden by row count", () => {
    store.healthRows = [healthRow({ tag: "a" }), healthRow({ tag: "b" }), healthRow({ tag: "c" })];
    store.healthComputedAt = new Date().toISOString();
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const btn = wrapper.find(".rs-board-rebuild-persistent");
    expect(btn.exists()).toBe(true);
    expect(btn.text()).toContain("Updated");
  });

  it("tints and swaps icon copy when the cache is stale", () => {
    store.healthRows = [healthRow()];
    store.healthComputedAt = new Date().toISOString();
    store.healthStale = true;
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const btn = wrapper.find(".rs-board-rebuild-persistent");
    expect(btn.classes()).toContain("rs-board-rebuild-persistent--stale");
    expect(btn.attributes("title")).toMatch(/rebuild now/i);
  });

  it("clicking calls store.rebuildHealth()", async () => {
    store.healthRows = [healthRow()];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const spy = vi.spyOn(store, "rebuildHealth").mockResolvedValue();
    await wrapper.find(".rs-board-rebuild-persistent").trigger("click");
    expect(spy).toHaveBeenCalled();
  });
});

describe("TagHealthBoard: Priority relabel (Spec C)", () => {
  it("shows 'Priority', never 'Est. fixes', with the disclaiming tooltip", () => {
    store.healthRows = [healthRow()];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const html = wrapper.html();
    expect(html).toContain("Priority");
    expect(html).not.toContain("Est. fixes");
    const header = wrapper.findAll(".rs-board-hdr").find((h) => h.text().includes("Priority"));
    expect(header.attributes("title")).toMatch(/not a forecast/i);
  });
});

describe("TagHealthBoard: Verified column removed (Spec E 7a)", () => {
  it("renders no Verified header, cell, or sort option", () => {
    store.healthRows = [healthRow({ verified_pct: 77 })];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const html = wrapper.html();
    expect(html).not.toMatch(/Verified/);
    expect(html).not.toContain("77%");
    // 8 data columns now (Verified cut per Spec E, Accuracy scrapped along
    // with the scoring subsystem) — spot check via the header row cell count.
    const headerCells = wrapper.findAll(".rs-board-row--head .rs-board-hdr");
    expect(headerCells.length).toBe(8);
  });
});

describe("TagHealthBoard: Why column (Spec E 7c)", () => {
  it("shows real text with a matching title for a scored row", () => {
    store.healthRows = [healthRow({ est_wrong: 5, est_missing: 1, mismatch: 0 })];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const why = wrapper.find(".rs-board-why");
    expect(why.text()).toBe("mostly wrong — tagged but model disagrees");
    expect(why.attributes("title")).toBe(why.text());
  });
});

describe("TagHealthBoard: default sort tie-break (rawCorrections, not alphabetical)", () => {
  it("breaks a tied rounded Priority by raw disagreement volume, not tag name", () => {
    // Both round to a Priority of 8 (corrections() uses the discounted _adj
    // fields), but "zebra"'s raw est_wrong + est_missing (15) is well above
    // "apple"'s (8). Alphabetically "apple" sorts first — proving a fix that
    // still reads as A-Z order is wrong; the correct order is "zebra" first.
    const zebra = healthRow({
      tag: "zebra",
      est_wrong: 12,
      est_missing: 3,
      mismatch: 0,
      est_wrong_adj: 8.4,
      est_missing_adj: 0,
    });
    const apple = healthRow({
      tag: "apple",
      est_wrong: 8,
      est_missing: 0,
      mismatch: 0,
      est_wrong_adj: 8,
      est_missing_adj: 0,
    });
    store.healthRows = [apple, zebra];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });

    const rows = wrapper.findAll(".rs-board-row:not(.rs-board-row--head)");
    // Displayed Priority number is genuinely tied...
    expect(rows.map((r) => r.find(".rs-board-health-num").text())).toEqual(["8", "8"]);
    // ...but the order is decided by raw volume, not the alphabet.
    expect(rows[0].find(".rs-board-tag-name").text()).toBe("zebra");
    expect(rows[1].find(".rs-board-tag-name").text()).toBe("apple");
  });

  it("falls back to tag name for a genuine full tie, and stays stable regardless of input order", () => {
    function tiedRow(tag) {
      return healthRow({ tag, est_wrong: 5, est_missing: 0, mismatch: 0 });
    }
    const alpha = tiedRow("alpha");
    const beta = tiedRow("beta");
    const gamma = tiedRow("gamma");

    store.healthRows = [gamma, alpha, beta];
    const wrapperA = mount(TagHealthBoard, { global: globalOpts });
    const orderA = wrapperA
      .findAll(".rs-board-row:not(.rs-board-row--head) .rs-board-tag-name")
      .map((n) => n.text());

    store.healthRows = [beta, gamma, alpha];
    const wrapperB = mount(TagHealthBoard, { global: globalOpts });
    const orderB = wrapperB
      .findAll(".rs-board-row:not(.rs-board-row--head) .rs-board-tag-name")
      .map((n) => n.text());

    // Same three rows, two different input orders: the rendered order must
    // not flap between renders.
    expect(orderA).toEqual(["alpha", "beta", "gamma"]);
    expect(orderB).toEqual(["alpha", "beta", "gamma"]);
  });
});
