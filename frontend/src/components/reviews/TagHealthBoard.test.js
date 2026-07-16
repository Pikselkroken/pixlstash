// Render/behavior coverage for the tag health board redesign
// (docs/reviews/tag-review-board-redesign-ux-spec.md). The pure ranking
// logic (whyText, boost math) is covered directly in
// tagHealthBoardLogic.test.js; this file covers the things only visible once
// mounted: the persistent rebuild control's visibility, the Priority
// relabel, the pending-pill copy, the Verified column's removal, the Why
// column's rendered text, and the boost badge + sort-scoping behavior.

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
    eval_slice_frozen_at: null,
    eval_metric_kind: null,
    eval_threshold_source: null,
    eval_f1: null,
    eval_candidate_n_pos: 2,
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

describe("TagHealthBoard: pending pill 5x ratio copy (Spec D)", () => {
  it("mentions the EVAL-reservation ratio in the tooltip and aria-label", () => {
    store.healthRows = [healthRow({ eval_candidate_n_pos: 3 })];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const pill = wrapper.find(".rs-acc-pill--pending");
    expect(pill.exists()).toBe(true);
    expect(pill.attributes("title")).toMatch(/fifth|EVAL-side/i);
    expect(pill.attributes("aria-label")).toMatch(/one in five/i);
  });
});

describe("TagHealthBoard: Verified column removed (Spec E 7a)", () => {
  it("renders no Verified header, cell, or sort option", () => {
    store.healthRows = [healthRow({ verified_pct: 77 })];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const html = wrapper.html();
    expect(html).not.toMatch(/Verified/);
    expect(html).not.toContain("77%");
    // 9 data columns now (was 10) — spot check via the header row cell count.
    const headerCells = wrapper.findAll(".rs-board-row--head .rs-board-hdr");
    expect(headerCells.length).toBe(9);
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

describe("TagHealthBoard: accuracy tie-breaker boost (Spec F)", () => {
  function eligibleRow(tag) {
    return healthRow({
      tag,
      est_wrong: 2,
      est_missing: 0,
      mismatch: 0,
      eval_slice_frozen_at: "2026-07-01T00:00:00",
      eval_metric_kind: "F1",
      eval_threshold_source: "calibrated",
      eval_f1: 0.1, // well below the 0.7 boost threshold
      eval_n: 20,
      eval_n_pos: 10,
    });
  }

  it("outranks a nearby higher-raw-Priority tag in the default sort, badge shown, displayed number unchanged", async () => {
    // weak: corrections() = 8, frozen+low-F1 (eval_f1=0.1) → boostFactor ~1.26,
    // boostedScore ~10.1. strong: corrections() = 9, not boost-eligible
    // (unfrozen) — close enough that the boost flips the order, per the
    // spec's "can't leapfrog a much larger one" cap behavior.
    const weak = eligibleRow("weak"); // est_wrong: 2 by default — override below
    weak.est_wrong = 8;
    const strong = healthRow({ tag: "strong", est_wrong: 9, est_missing: 0, mismatch: 0 });
    store.healthRows = [strong, weak];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });

    const rows = wrapper.findAll(".rs-board-row:not(.rs-board-row--head)");
    // "weak" now sorts above "strong" under the default score sort.
    expect(rows[0].find(".rs-board-tag-name").text()).toBe("weak");
    expect(rows[1].find(".rs-board-tag-name").text()).toBe("strong");

    // The boosted row carries the badge; the displayed Priority number is
    // still the honest, unboosted corrections() value (8), never altered.
    expect(rows[0].find(".rs-board-boost-chip").exists()).toBe(true);
    expect(rows[0].find(".rs-board-health-num").text()).toBe("8");
    expect(rows[1].find(".rs-board-boost-chip").exists()).toBe(false);
  });

  it("does not affect 'Most wrong' order — bit-for-bit the raw est_wrong ranking", async () => {
    const weak = eligibleRow("weak"); // est_wrong: 2
    const strong = healthRow({ tag: "strong", est_wrong: 30, est_missing: 0, mismatch: 0 });
    store.healthRows = [weak, strong];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });

    await wrapper.find(".rs-board-sort").setValue("wrong");

    const rows = wrapper.findAll(".rs-board-row:not(.rs-board-row--head)");
    // Raw est_wrong: strong=30 > weak=2 — the boost must not reorder this.
    expect(rows[0].find(".rs-board-tag-name").text()).toBe("strong");
    expect(rows[1].find(".rs-board-tag-name").text()).toBe("weak");
    // No boost badge on any row outside the "score" sort.
    expect(wrapper.findAll(".rs-board-boost-chip").length).toBe(0);
  });

  it("never boosts an unfrozen, AP-kind, or uncalibrated row", () => {
    const unfrozen = healthRow({ tag: "unfrozen", est_wrong: 1 });
    const apKind = healthRow({
      tag: "ap-kind",
      est_wrong: 1,
      eval_slice_frozen_at: "2026-07-01T00:00:00",
      eval_metric_kind: "AP",
      eval_ap: 0.1,
    });
    const uncalibrated = healthRow({
      tag: "uncalibrated",
      est_wrong: 1,
      eval_slice_frozen_at: "2026-07-01T00:00:00",
      eval_metric_kind: "F1",
      eval_threshold_source: "uncalibrated_fallback",
      eval_f1: 0.1,
    });
    store.healthRows = [unfrozen, apKind, uncalibrated];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    expect(wrapper.findAll(".rs-board-boost-chip").length).toBe(0);
  });
});
