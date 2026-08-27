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
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: { value: false },
}));

import TagHealthBoard from "./TagHealthBoard.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useEntityListsStore } from "../../stores/useEntityListsStore";

const VIcon = {
  name: "v-icon",
  setup:
    (_props, { slots }) =>
    () =>
      h("i", { class: "v-icon" }, slots.default?.()),
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

// The board's scope lists are a view onto the shared entity-list cache, so the
// set rows are seeded there rather than on the review store.
function seedSets(rows) {
  useEntityListsStore().lists = { characters: [], projects: [], sets: rows };
}

beforeEach(() => {
  setActivePinia(createPinia());
  store = useReviewSessionsStore();
});

describe("TagHealthBoard: persistent rebuild control (Spec B)", () => {
  it("is visible with zero rows", () => {
    store.healthRows = [];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    expect(wrapper.find(".rs-board-rebuild-persistent").exists()).toBe(true);
    expect(wrapper.find(".rs-board-rebuild-persistent").text()).toContain(
      "Never built",
    );
  });

  it("is visible with many rows too - never hidden by row count", () => {
    store.healthRows = [
      healthRow({ tag: "a" }),
      healthRow({ tag: "b" }),
      healthRow({ tag: "c" }),
    ];
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
    const header = wrapper
      .findAll(".rs-board-hdr")
      .find((h) => h.text().includes("Priority"));
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
    // with the scoring subsystem) - spot check via the header row cell count.
    const headerCells = wrapper.findAll(".rs-board-row--head .rs-board-hdr");
    expect(headerCells.length).toBe(8);
  });
});

describe("TagHealthBoard: Why column (Spec E 7c)", () => {
  it("shows real text with a matching title for a scored row", () => {
    store.healthRows = [
      healthRow({ est_wrong: 5, est_missing: 1, mismatch: 0 }),
    ];
    const wrapper = mount(TagHealthBoard, { global: globalOpts });
    const why = wrapper.find(".rs-board-why");
    expect(why.text()).toBe("mostly wrong - tagged but model disagrees");
    expect(why.attributes("title")).toBe(why.text());
  });
});

describe("TagHealthBoard: default sort tie-break (rawCorrections, not alphabetical)", () => {
  it("breaks a tied rounded Priority by raw disagreement volume, not tag name", () => {
    // Both round to a Priority of 8 (corrections() uses the discounted _adj
    // fields), but "zebra"'s raw est_wrong + est_missing (15) is well above
    // "apple"'s (8). Alphabetically "apple" sorts first - proving a fix that
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
    expect(rows.map((r) => r.find(".rs-board-health-num").text())).toEqual([
      "8",
      "8",
    ]);
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

// A row whose review is PROVABLY empty (no confirmed examples in scope and no
// confident predictions) gets a disabled "Start review" rather than a click
// that produces a review with zero cards. The gate is deliberately narrow: it
// is only sound when the board's scope is the review's scope, and it must never
// fire on a tag that merely scores Priority 0.
describe("TagHealthBoard: provably-empty Start review gate", () => {
  const SCOPED = { projectId: null, setId: 5, characterId: null };

  function startBtn(w, index = 0) {
    return w.findAll(".rs-board-row:not(.rs-board-row--head) .rs-board-btn")[
      index
    ];
  }

  function mountRows(rows, scope = SCOPED) {
    store.healthRows = rows;
    store.healthScope = { ...scope };
    return mount(TagHealthBoard, { global: globalOpts });
  }

  it("disables the button, with a cause-and-remedy tooltip, for a zero-yield row", () => {
    const w = mountRows([
      healthRow({ ground_truth: 0, est_wrong: 0, est_missing: 0, mismatch: 0 }),
    ]);

    const btn = startBtn(w);
    expect(btn.attributes("disabled")).toBeDefined();
    expect(btn.classes()).toContain("rs-board-btn--blocked");

    // The reason names the cause AND the remedy - not a bare "unavailable".
    const title = w.find(".rs-board-action").attributes("title");
    expect(title).toMatch(/nothing to compare/i);
    expect(title).toMatch(/confirm this tag on a few pictures/i);
    expect(title).not.toMatch(/unavailable/i);
  });

  it("does not emit start-review when the blocked button is clicked", async () => {
    const w = mountRows([
      healthRow({ ground_truth: 0, est_wrong: 0, est_missing: 0, mismatch: 0 }),
    ]);
    await startBtn(w).trigger("click");
    expect(w.emitted("start-review")).toBeUndefined();
  });

  // THE regression that invalidates the whole design if it slips: a tag with
  // confirmed examples but a Priority of 0 still has reviewable work, because
  // the kNN scan behind a review is a different mechanism from the Priority
  // estimate. Its button must stay live.
  it("keeps the button ENABLED for a Priority-0 tag that has ground truth", async () => {
    const w = mountRows([
      healthRow({
        tag: "shirt",
        ground_truth: 40,
        est_wrong: 0,
        est_missing: 0,
        mismatch: 0,
      }),
    ]);

    expect(w.find(".rs-board-health-num").text()).toBe("0"); // Priority 0 …
    const btn = startBtn(w);
    expect(btn.attributes("disabled")).toBeUndefined(); // … still enabled.
    expect(btn.classes()).not.toContain("rs-board-btn--blocked");
    expect(w.find(".rs-board-action").attributes("title")).toBeUndefined();

    await btn.trigger("click");
    expect(w.emitted("start-review")[0]).toEqual(["shirt"]);
  });

  // est_missing_adj discounts the raw count by measured precision and the cell
  // ROUNDS it, so this row displays "Est. missing: 0" while having 3 genuine
  // confident predictions. Gating on the displayed number would disable it.
  it("keeps the button ENABLED when est_missing is non-zero but displays as 0", () => {
    const w = mountRows([
      healthRow({
        ground_truth: 0,
        est_wrong: 0,
        est_missing: 3,
        est_missing_adj: 0.4,
        mismatch: 0,
      }),
    ]);
    // The Est. missing cell really does render 0 …
    const cells = w.findAll(".rs-board-row:not(.rs-board-row--head) .rs-board-num");
    expect(cells[1].text()).toBe("0");
    // … and the button is still live, because the gate reads the raw count.
    expect(startBtn(w).attributes("disabled")).toBeUndefined();
  });

  it("leaves every button enabled on an UNSCOPED board, where scopes can differ", () => {
    // ReviewSessionsOverlay only inherits the board scope into the dialog when
    // store.healthScoped is true; unscoped, the dialog prefills from the app
    // selection, so this row's numbers may not describe the resulting review.
    const w = mountRows(
      [healthRow({ ground_truth: 0, est_wrong: 0, est_missing: 0, mismatch: 0 })],
      { projectId: null, setId: null, characterId: null },
    );
    expect(store.healthScoped).toBe(false);
    expect(startBtn(w).attributes("disabled")).toBeUndefined();
    expect(w.find(".rs-board-action").attributes("title")).toBeUndefined();
  });

  it("re-evaluates when the scope changes", async () => {
    const w = mountRows(
      [healthRow({ ground_truth: 0, est_wrong: 0, est_missing: 0, mismatch: 0 })],
      { projectId: null, setId: null, characterId: null },
    );
    expect(startBtn(w).attributes("disabled")).toBeUndefined();

    store.healthScope = { projectId: 3, setId: null, characterId: null };
    await w.vm.$nextTick();
    expect(startBtn(w).attributes("disabled")).toBeDefined();
  });

  it("does not blocked-tag a row that already has an open session", () => {
    // That row renders "Open", not "Start review" - the session and its cards
    // already exist, so the would-be-empty reason does not apply to it.
    store.sessions = [{ id: 11, tag: "shirt" }];
    const w = mountRows([
      healthRow({
        tag: "shirt",
        ground_truth: 0,
        est_wrong: 0,
        est_missing: 0,
        mismatch: 0,
      }),
    ]);
    const btn = w.find(".rs-board-btn--open");
    expect(btn.exists()).toBe(true);
    expect(btn.attributes("disabled")).toBeUndefined();
    expect(w.find(".rs-board-action").attributes("title")).toBeUndefined();
  });

  it("does not fire on rows that predate the ground_truth field", () => {
    const r = healthRow({ est_wrong: 0, est_missing: 0, mismatch: 0 });
    delete r.ground_truth;
    const w = mountRows([r]);
    expect(startBtn(w).attributes("disabled")).toBeUndefined();
  });
});

// The board renders a row for EVERY tag, which is a very long list in a mature
// vault. The Priority-0 rows collapse behind a disclosure - they are never
// dropped, because a Priority of 0 does not mean a review would find nothing.
describe("TagHealthBoard: zero-Priority tail disclosure", () => {
  const scored = (tag, v) =>
    healthRow({ tag, est_wrong: v, est_missing: 0, mismatch: 0, ground_truth: 5 });
  const zero = (tag) =>
    healthRow({ tag, est_wrong: 0, est_missing: 0, mismatch: 0, ground_truth: 5 });

  function mountMixed() {
    store.healthRows = [
      scored("alpha", 9),
      scored("bravo", 4),
      zero("xray"),
      zero("yankee"),
      zero("zulu"),
    ];
    return mount(TagHealthBoard, { global: globalOpts });
  }

  function names(w) {
    return w
      .findAll(".rs-board-row:not(.rs-board-row--head) .rs-board-tag-name")
      .map((n) => n.text());
  }

  it("hides the zero-Priority tail by default and counts it accurately", () => {
    const w = mountMixed();
    expect(names(w)).toEqual(["alpha", "bravo"]);

    const more = w.find(".rs-board-more");
    expect(more.exists()).toBe(true);
    expect(more.element.tagName).toBe("BUTTON");
    expect(more.text()).toContain("Show 3 tags");
    expect(more.attributes("aria-expanded")).toBe("false");
    expect(more.attributes("aria-controls")).toBe("rs-board-table");
    expect(w.find("#rs-board-table").exists()).toBe(true);
    // The rows are collapsed, not filtered - the control says so.
    expect(more.attributes("title")).toMatch(/can still find work/i);
  });

  it("reveals the full row set when expanded, and collapses again", async () => {
    const w = mountMixed();
    await w.find(".rs-board-more").trigger("click");

    expect(names(w)).toEqual(["alpha", "bravo", "xray", "yankee", "zulu"]);
    const more = w.find(".rs-board-more");
    expect(more.attributes("aria-expanded")).toBe("true");
    expect(more.text()).toContain("Hide 3 tags");

    await more.trigger("click");
    expect(names(w)).toEqual(["alpha", "bravo"]);
    expect(w.find(".rs-board-more").attributes("aria-expanded")).toBe("false");
  });

  it("singularises the count for a one-row tail", () => {
    store.healthRows = [scored("alpha", 9), zero("zulu")];
    const w = mount(TagHealthBoard, { global: globalOpts });
    expect(w.find(".rs-board-more").text()).toContain("Show 1 tag with");
  });

  it("shows no disclosure when nothing scores 0", () => {
    store.healthRows = [scored("alpha", 9), scored("bravo", 4)];
    const w = mount(TagHealthBoard, { global: globalOpts });
    expect(w.find(".rs-board-more").exists()).toBe(false);
    expect(names(w)).toEqual(["alpha", "bravo"]);
  });

  it("shows every row, and no disclosure, when ALL rows score 0", () => {
    // Collapsing the whole board would leave a header over an empty table,
    // which reads as "no tags at all" - worse than the density it saves.
    store.healthRows = [zero("alpha"), zero("bravo"), zero("zulu")];
    const w = mount(TagHealthBoard, { global: globalOpts });
    expect(names(w)).toEqual(["alpha", "bravo", "zulu"]);
    expect(w.find(".rs-board-more").exists()).toBe(false);
  });

  it("drops the disclosure under a non-Priority sort, where zeros interleave", async () => {
    // Under A–Z the zero rows are not a contiguous tail, so hiding them would
    // be a filter, not a disclosure. Every row must be on screen instead.
    const w = mountMixed();
    expect(w.find(".rs-board-more").exists()).toBe(true);

    const sortSelect = w.find("select.rs-board-sort");
    await sortSelect.setValue("tag");

    expect(w.find(".rs-board-more").exists()).toBe(false);
    expect(names(w)).toEqual(["alpha", "bravo", "xray", "yankee", "zulu"]);
  });

  it("returns to a consistent collapsed state after a round-trip through another sort", async () => {
    const w = mountMixed();
    const sortSelect = w.find("select.rs-board-sort");

    await sortSelect.setValue("tag");
    expect(names(w)).toHaveLength(5);

    await sortSelect.setValue("score");
    // Back on the Priority sort: the tail is collapsed again and the count is
    // still right - nothing was stranded by the detour.
    expect(names(w)).toEqual(["alpha", "bravo"]);
    const more = w.find(".rs-board-more");
    expect(more.text()).toContain("Show 3 tags");
    expect(more.attributes("aria-expanded")).toBe("false");
  });

  it("keeps the count in step with the tag filter", async () => {
    const w = mountMixed();
    expect(w.find(".rs-board-more").text()).toContain("Show 3 tags");

    // Narrow to two zero rows plus no scored row… every match scores 0, so the
    // whole set stays visible with no disclosure.
    await w.find(".rs-board-filter-input").setValue("y");
    expect(w.find(".rs-board-more").exists()).toBe(false);
    expect(names(w)).toEqual(["xray", "yankee"]);
  });

  it("does not collapse anything in the locked-set terminal state", () => {
    store.healthRows = [scored("alpha", 9), zero("zulu")];
    seedSets([{ id: 2, name: "Frozen eval", locked: true }]);
    store.healthScope = { projectId: null, setId: 2, characterId: null };
    const w = mount(TagHealthBoard, { global: globalOpts });
    expect(w.find(".rs-board-locked").exists()).toBe(true);
    expect(w.find(".rs-board-more").exists()).toBe(false);
    expect(w.find(".rs-board-table").exists()).toBe(false);
  });
});

// The Set scope filter is a native <select>, whose <option>s can carry neither
// an icon nor a title - so the lock state has to ride in the label text, or the
// user scopes the board to a locked set and only hits the block later in the
// review dialog.
describe("TagHealthBoard: locked sets in the Set scope filter", () => {
  it("suffixes locked set labels with (locked) and leaves order untouched", () => {
    store.healthRows = [];
    seedSets([
      { id: 1, name: "Portraits", locked: false },
      { id: 2, name: "Frozen eval", locked: true },
      { id: 3, name: "Landscapes", locked: false },
    ]);

    const w = mount(TagHealthBoard, { global: globalOpts });
    const setSelect = w.findAll("select.rs-board-scope")[1];
    const labels = setSelect.findAll("option").map((o) => o.text());

    // Natural order preserved - locked sets are NOT sorted to the bottom.
    expect(labels).toEqual([
      "Set: Any",
      "Portraits",
      "Frozen eval (locked)",
      "Landscapes",
    ]);
  });

  it("falls back to the id-based name and still marks the lock", () => {
    store.healthRows = [];
    seedSets([{ id: 7, name: "", locked: true }]);

    const w = mount(TagHealthBoard, { global: globalOpts });
    const setSelect = w.findAll("select.rs-board-scope")[1];
    expect(setSelect.findAll("option")[1].text()).toBe("Set 7 (locked)");
  });
});

// Scoping the board to a locked set is ALLOWED (the native <select> can't
// disable an option without hiding the reason), but it is then a terminal
// state: a locked set's pictures are read-only, so no row on it is reviewable.
// The board replaces its whole body with the explanation instead of offering a
// "Start review" the backend would 423.
describe("TagHealthBoard: locked-set scope terminal state", () => {
  const SETS = [
    { id: 1, name: "Portraits", locked: false },
    { id: 2, name: "Frozen eval", locked: true },
  ];

  function mountScoped(setId) {
    store.healthRows = [healthRow({ tag: "shirt" }), healthRow({ tag: "hat" })];
    seedSets(SETS);
    store.healthScope = { projectId: null, setId, characterId: null };
    return mount(TagHealthBoard, { global: globalOpts });
  }

  it("renders the locked state, and no rows or Start review, for a locked set", () => {
    const w = mountScoped(2);

    const locked = w.find(".rs-board-locked");
    expect(locked.exists()).toBe(true);
    expect(locked.text()).toContain("Picture set is locked");
    // Cause AND remedy, in the shared lockedSetTitle() wording.
    expect(locked.text()).toContain("'Frozen eval' is locked");
    expect(locked.text()).toContain("its pictures are read-only");
    expect(locked.text()).toContain("Unlock it to review its tags");
    // Announced, and focusable so focus isn't stranded when the rows go.
    expect(locked.attributes("role")).toBe("status");
    expect(locked.attributes("tabindex")).toBe("-1");
    expect(locked.find("h3").exists()).toBe(true);

    expect(w.find(".rs-board-table").exists()).toBe(false);
    expect(w.findAll(".rs-board-row")).toHaveLength(0);
    expect(w.text()).not.toContain("Start review");
    expect(w.find(".rs-board-legend").exists()).toBe(false);
  });

  it("keeps the Set filter usable so the scope is recoverable", () => {
    const w = mountScoped(2);
    const selects = w.findAll("select.rs-board-scope");
    expect(selects).toHaveLength(3);
    expect(selects[1].attributes("disabled")).toBeUndefined();
    expect(selects[1].findAll("option").map((o) => o.text())).toEqual([
      "Set: Any",
      "Portraits",
      "Frozen eval (locked)",
    ]);
  });

  it("restores the rows when the scope moves to an unlocked set or Any", async () => {
    const w = mountScoped(2);
    expect(w.find(".rs-board-locked").exists()).toBe(true);

    store.healthScope = { projectId: null, setId: 1, characterId: null };
    await w.vm.$nextTick();
    expect(w.find(".rs-board-locked").exists()).toBe(false);
    expect(w.findAll(".rs-board-row").length).toBeGreaterThan(1);

    store.healthScope = { projectId: null, setId: 2, characterId: null };
    await w.vm.$nextTick();
    expect(w.find(".rs-board-locked").exists()).toBe(true);

    store.healthScope = { projectId: null, setId: null, characterId: null };
    await w.vm.$nextTick();
    expect(w.find(".rs-board-locked").exists()).toBe(false);
    expect(w.findAll(".rs-board-row").length).toBeGreaterThan(1);
  });

  it("leaves an unlocked-set scope completely unaffected", () => {
    const w = mountScoped(1);
    expect(w.find(".rs-board-locked").exists()).toBe(false);
    expect(w.find(".rs-board-table").exists()).toBe(true);
    expect(w.text()).toContain("Start review");
  });

  it("does not fire on an unknown set id (sets not fetched yet)", () => {
    store.healthRows = [healthRow()];
    seedSets([]);
    store.healthScope = { projectId: null, setId: 99, characterId: null };
    const w = mount(TagHealthBoard, { global: globalOpts });
    expect(w.find(".rs-board-locked").exists()).toBe(false);
    expect(w.find(".rs-board-table").exists()).toBe(true);
  });
});
