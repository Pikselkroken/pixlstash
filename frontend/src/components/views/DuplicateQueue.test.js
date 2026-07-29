// The duplicate triage queue.
//
// These tests cover the parts of the destination that are invisible in a
// screenshot and expensive to get wrong: the live region has to outlive the row
// that emptied the queue, the tier popover has to be dismissible without a
// mouse, a failed verdict has to be reported rather than swallowed, and a
// read-only session must not be shown a verdict it can never give.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("../../api/dedup", () => ({
  getPolicy: vi.fn(),
  listGroups: vi.fn(),
  getCounts: vi.fn(),
  startScan: vi.fn(),
  stackGroup: vi.fn(),
  keepGroupSeparate: vi.fn(),
  reopenGroup: vi.fn(),
  autoStackExact: vi.fn(),
  GLOBAL_SCOPE: "global",
}));

// The queue opens itself from the URL's scope, so it needs a route.
const routeMock = { name: "duplicates", query: {} };
const routerReplace = vi.fn();
vi.mock("vue-router", () => ({
  useRoute: () => routeMock,
  useRouter: () => ({ replace: (...a) => routerReplace(...a) }),
}));

vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/pictures/thumbnails/${id}.webp`,
}));

// The read-only flag is a module-level computed over the session; the tests
// drive it directly rather than faking a whole session. The factories are
// hoisted above every top-level binding, so the shared refs and spies are built
// inside them and read back through the mocked modules.
let batchCounter = 0;
vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef } = await import("vue");
  return {
    isReadOnly: makeRef(false),
    API_BASE_URL: "http://backend.test/api/v1",
    newOperationBatchId: () => `cli-test-${(batchCounter += 1)}`,
  };
});

vi.mock("../../stores/useOperationStore", () => {
  const undo = vi.fn();
  return { useOperationStore: () => ({ undo }) };
});

vi.mock("../../stores/useNoticeStore", () => {
  const error = vi.fn();
  const info = vi.fn();
  const warning = vi.fn();
  return { useNoticeStore: () => ({ error, info, warning }) };
});

import {
  getPolicy,
  listGroups,
  getCounts,
  stackGroup,
  keepGroupSeparate,
  reopenGroup,
} from "../../api/dedup";
import { isReadOnly as readOnlyRef } from "../../utils/apiClient";
import { useNoticeStore } from "../../stores/useNoticeStore";
import DuplicateQueue from "./DuplicateQueue.vue";
import { useDedupStore } from "../../stores/useDedupStore";

/** A queue group in the backend's shape, with `n` candidates. */
function group(signature, n = 2) {
  const base = Number(signature.replace(/\D/g, "")) * 100;
  return {
    signature,
    tier: "near",
    confidence: 0.93,
    member_count: n,
    cover_picture_id: null,
    why: [],
    candidates: Array.from({ length: n }, (_, i) => ({
      picture_id: base + i,
      width: 4000,
      height: 3000,
      megapixels: 12,
    })),
  };
}

/** The bounds `GET /dedup/policy` publishes. */
const BOUNDS = {
  min_threshold: 0.65,
  max_threshold: 0.99999,
  tiers: ["exact", "near", "embedding"],
  always_on_tiers: ["exact"],
  tier_requires: { exact: null, near: "exact", embedding: "near" },
  scope_types: ["global", "project", "set", "character", "folder"],
  verdicts: ["stacked", "keep_separate"],
  max_page_size: 200,
};

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-progress-circular": true,
      DedupCompareDialog: true,
      DedupAutoStackDialog: true,
      ActionReceipt: true,
    },
  },
};

/**
 * Mount the queue over one served page and let the first load settle.
 *
 * `total` is the server's count of the WHOLE queue, which is larger than the
 * page whenever there is more to page in.
 */
async function mountQueue(groups, { byTier = {}, total = null } = {}) {
  getPolicy.mockResolvedValue({
    defaults: { near_enabled: false, embedding_enabled: false, threshold: 0.9 },
    bounds: BOUNDS,
  });
  listGroups.mockResolvedValue({
    groups,
    total: total ?? groups.length,
    offset: 0,
    limit: 20,
    scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
  });
  getCounts.mockResolvedValue({
    unresolved_groups: total ?? groups.length,
    by_tier: byTier,
    scopes: [],
    scan: { status: "complete" },
  });
  const store = useDedupStore();
  await store.loadPolicy();
  await store.refreshCounts();
  await store.loadFirstPage();
  const wrapper = mount(DuplicateQueue, {
    ...globalOpts,
    attachTo: document.body,
  });
  await wrapper.vm.$nextTick();
  return { wrapper, store };
}

let errorSpy;

beforeEach(() => {
  setActivePinia(createPinia());
  readOnlyRef.value = false;
  routeMock.name = "duplicates";
  routeMock.query = {};
  routerReplace.mockReset();
  vi.spyOn(console, "warn").mockImplementation(() => {});
  const notices = useNoticeStore();
  errorSpy = notices.error;
  for (const fn of [
    getPolicy,
    listGroups,
    getCounts,
    stackGroup,
    keepGroupSeparate,
    reopenGroup,
    errorSpy,
    notices.info,
    notices.warning,
  ]) {
    fn.mockReset();
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DuplicateQueue — what a screen reader hears", () => {
  // A live region that unmounts with the last row takes the verdict that
  // emptied the queue down with it, so the one announcement a user most needs
  // is the one they would never hear.
  it("keeps the live region alive once the queue empties", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    stackGroup.mockResolvedValue({ ok: true });

    expect(wrapper.find('[role="status"][aria-live="polite"]').exists()).toBe(
      true,
    );
    await wrapper.vm.$nextTick();

    await store.stack(store.groups[0]);
    await wrapper.vm.$nextTick();

    expect(store.hasGroups).toBe(false);
    expect(wrapper.find('[role="status"][aria-live="polite"]').exists()).toBe(
      true,
    );
    wrapper.unmount();
  });

  // The visible hint strip is a row of glyphs hidden from assistive tech, so
  // the full model has to be stated somewhere it can be read, including the two
  // keys the strip has no room for.
  it("describes the whole keyboard model, including X and the digits", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    const help = wrapper.find("#dq-key-help");
    expect(wrapper.attributes("aria-describedby")).toBe("dq-key-help");
    const text = help.text();
    for (const phrase of ["1 to 9", "X leaves", "Escape", "ever deleted"]) {
      expect(text).toContain(phrase);
    }
    wrapper.unmount();
  });
});

describe("DuplicateQueue — the tier popover", () => {
  const TIERS = [{ key: "near", label: "Near duplicates", count: 4 }];

  it("closes on Escape and gives the focus back to its button", async () => {
    const { wrapper } = await mountQueue([group("g1")], { tiers: TIERS });
    const button = wrapper.find(".dq-tier-wrap .dq-btn");

    await button.trigger("click");
    expect(wrapper.findComponent({ name: "DedupTierMenu" }).exists()).toBe(
      true,
    );
    expect(button.attributes("aria-expanded")).toBe("true");

    await wrapper.find(".dq").trigger("keydown", { key: "Escape" });
    expect(wrapper.findComponent({ name: "DedupTierMenu" }).exists()).toBe(
      false,
    );
    expect(document.activeElement).toBe(button.element);
    wrapper.unmount();
  });

  it("closes on a pointer press outside itself", async () => {
    const { wrapper } = await mountQueue([group("g1")], { tiers: TIERS });
    await wrapper.find(".dq-tier-wrap .dq-btn").trigger("click");
    expect(wrapper.findComponent({ name: "DedupTierMenu" }).exists()).toBe(
      true,
    );

    document.dispatchEvent(
      new window.MouseEvent("mousedown", { bubbles: true }),
    );
    await wrapper.vm.$nextTick();
    expect(wrapper.findComponent({ name: "DedupTierMenu" }).exists()).toBe(
      false,
    );
    wrapper.unmount();
  });

  it("leaves the popover alone for a press inside it", async () => {
    const { wrapper } = await mountQueue([group("g1")], { tiers: TIERS });
    const wrap = wrapper.find(".dq-tier-wrap");
    await wrap.find(".dq-btn").trigger("click");

    wrap.element.dispatchEvent(
      new window.MouseEvent("mousedown", { bubbles: true }),
    );
    await wrapper.vm.$nextTick();
    expect(wrapper.findComponent({ name: "DedupTierMenu" }).exists()).toBe(
      true,
    );
    wrapper.unmount();
  });
});

describe("DuplicateQueue — when a verdict does not land", () => {
  // A failed verdict leaves the row where it was, which on a queue whose whole
  // promise is auto-advance reads as a dead keypress.
  it("raises a notice rather than swallowing the failure", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    stackGroup.mockRejectedValue(new Error("nope"));

    wrapper.findComponent({ name: "DedupGroupRow" }).vm.$emit("stack");
    await flushPromises();

    expect(errorSpy).toHaveBeenCalledTimes(1);
    expect(errorSpy.mock.calls[0][0]).toContain("still in the queue");
    expect(store.hasGroups).toBe(true);
    wrapper.unmount();
  });
});

describe("DuplicateQueue — keeping a group separate", () => {
  // The backend deliberately records NO operation for this verdict, so no
  // receipt will come. The narration is transient and points at the Decided
  // page, which is the standing way back (it replaced the sticky notice).
  it("narrates transiently and points at Decided, with no action attached", async () => {
    const { wrapper } = await mountQueue([group("g1", 3)]);
    keepGroupSeparate.mockResolvedValue({ verdict: "keep_separate" });
    const info = useNoticeStore().info;

    wrapper.findComponent({ name: "DedupGroupRow" }).vm.$emit("keep-separate");
    await flushPromises();

    expect(info).toHaveBeenCalledTimes(1);
    const [text, opts] = info.mock.calls[0];
    expect(text).toContain("under Decided");
    expect(opts?.action).toBeUndefined();
    wrapper.unmount();
  });
});

describe("DuplicateQueue — the Decided page", () => {
  it("lists decided groups with their verdict and clears one on demand", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g9"), verdict: "keep_separate", decided_at: "2026-07-29" },
      ],
      total: 1,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });

    await store.toggleDecided();
    await wrapper.vm.$nextTick();

    // The decided request is explicit, so the two pages can never blur.
    expect(listGroups).toHaveBeenLastCalledWith(
      expect.objectContaining({ decided: true }),
    );
    const row = wrapper.findComponent({ name: "DedupGroupRow" });
    expect(row.text()).toContain("Kept separate");
    expect(row.text()).toContain("Clear decision");
    // A decided row gives no verdicts: Enter must be inert here.
    stackGroup.mockResolvedValue({});
    await store.stack(store.groups[0]);
    expect(stackGroup).not.toHaveBeenCalled();

    reopenGroup.mockResolvedValue({
      signature: "g9",
      previous_verdict: "keep_separate",
      group_returned_to_queue: true,
    });
    row.vm.$emit("clear-decision");
    await flushPromises();
    expect(reopenGroup).toHaveBeenCalledWith("g9");
    wrapper.unmount();
  });

  it("multi-selects decided groups and clears every selected decision", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g8"), verdict: "keep_separate", decided_at: "2026-07-29" },
        { ...group("g9"), verdict: "stacked", decided_at: "2026-07-29" },
      ],
      total: 2,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await store.toggleDecided();
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".grow");
    await rows[0].trigger("click", { ctrlKey: true });
    await rows[1].trigger("click", { ctrlKey: true });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".qselchip").text()).toContain("Clear decision applies");

    reopenGroup.mockResolvedValue({ group_returned_to_queue: true });
    const clearButtons = wrapper.findAll(".gbtn");
    const bulkClear = clearButtons.find((b) => b.text().includes("Clear 2 decisions"));
    expect(bulkClear).toBeTruthy();
    await bulkClear.trigger("click");
    await flushPromises();
    expect(reopenGroup).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });
});

describe("DuplicateQueue — filters in the URL", () => {
  it("restores near, threshold and the Decided view from the query", async () => {
    // A full refresh lands here with only the URL; the selection must come
    // back exactly, clamped by the same rules the tier menu enforces.
    routeMock.query = {
      near: "1",
      embedding: "0",
      threshold: "0.8",
      view: "decided",
    };
    getPolicy.mockResolvedValue({
      defaults: { near_enabled: false, embedding_enabled: false, threshold: 0.9 },
      bounds: BOUNDS,
    });
    listGroups.mockResolvedValue({
      groups: [],
      total: 0,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    getCounts.mockResolvedValue({
      unresolved_groups: 0,
      by_tier: {},
      scopes: [],
      scan: { status: "complete" },
    });
    const store = useDedupStore();
    const wrapper = mount(DuplicateQueue, {
      ...globalOpts,
      attachTo: document.body,
    });
    await flushPromises();

    expect(store.nearEnabled).toBe(true);
    expect(store.embeddingEnabled).toBe(false);
    expect(store.threshold).toBe(0.8);
    expect(store.showingDecided).toBe(true);
    wrapper.unmount();
  });

  it("mirrors a filter change into the URL with replace, not push", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    routerReplace.mockReset();

    await store.setTierEnabled("near", true);
    await wrapper.vm.$nextTick();

    const { query } = routerReplace.mock.calls.at(-1)[0];
    expect(query.near).toBe("1");
    expect(query.embedding).toBe("0");
    wrapper.unmount();
  });

  it("drops the filter params when the selection returns to the defaults", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    await store.setTierEnabled("near", true);
    await wrapper.vm.$nextTick();
    routeMock.query = routerReplace.mock.calls.at(-1)[0].query;
    routerReplace.mockReset();

    await store.setTierEnabled("near", false);
    await wrapper.vm.$nextTick();

    const { query } = routerReplace.mock.calls.at(-1)[0];
    expect(query.near).toBeUndefined();
    expect(query.threshold).toBeUndefined();
    wrapper.unmount();
  });
});

describe("DuplicateQueue — multi-select", () => {
  it("ctrl+click selects, the buttons rename, and one verdict takes all", async () => {
    const { wrapper } = await mountQueue([
      group("g1"),
      group("g2"),
      group("g3"),
    ]);
    const rows = wrapper.findAll(".grow");
    await rows[0].trigger("click", { ctrlKey: true });
    await rows[1].trigger("click", { ctrlKey: true });
    await wrapper.vm.$nextTick();

    // The bulk scope is stated twice: once in the header, once on the very
    // buttons that will act.
    expect(wrapper.find(".qselchip").text()).toContain("2 groups selected");
    const stackBtn = wrapper.findAll(".grow")[0].find(".gbtn--stack");
    expect(stackBtn.text()).toContain("Stack 2 groups");

    stackGroup.mockResolvedValue({});
    await stackBtn.trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(stackGroup).toHaveBeenCalledTimes(2);
    const batchIds = stackGroup.mock.calls.map((call) => call[1].batchId);
    // One gesture, one Ctrl+Z: both verdicts share one client batch id.
    expect(batchIds[0]).toMatch(/^cli-/);
    expect(batchIds[1]).toBe(batchIds[0]);
  });

  it("shift+click selects the range from the focus, Escape clears it", async () => {
    const { wrapper, store } = await mountQueue([
      group("g1"),
      group("g2"),
      group("g3"),
    ]);
    const rows = wrapper.findAll(".grow");
    await rows[0].trigger("click");
    await rows[2].trigger("click", { shiftKey: true });
    expect(store.selectionCount).toBe(3);

    await wrapper.trigger("keydown", { key: "Escape" });
    expect(store.selectionCount).toBe(0);
    // Clearing the selection must not cost the user their place.
    expect(store.focusIndex).toBe(2);
  });

  it("every selected row wears the Enter/S chips; C stays on the focus", async () => {
    const { wrapper } = await mountQueue([
      group("g1"),
      group("g2"),
      group("g3"),
    ]);
    const rows = wrapper.findAll(".grow");
    await rows[0].trigger("click", { ctrlKey: true });
    await rows[1].trigger("click", { ctrlKey: true });
    await wrapper.vm.$nextTick();

    const fresh = wrapper.findAll(".grow");
    // Both selected rows say where Enter acts, because it acts on both.
    expect(fresh[0].find(".gbtn--stack kbd").exists()).toBe(true);
    expect(fresh[1].find(".gbtn--stack kbd").exists()).toBe(true);
    expect(fresh[2].find(".gbtn--stack kbd").exists()).toBe(false);
    // Compare opens ONE group, so its chip stays with the keyboard cursor.
    expect(fresh[0].find(".gcompare kbd").exists()).toBe(false);
    expect(fresh[1].find(".gcompare kbd").exists()).toBe(true);
    // The old explicit label is gone.
    expect(wrapper.text()).not.toContain("Keyboard acts here");
  });

  it("a verdict on an unselected group stays single", async () => {
    const { wrapper, store } = await mountQueue([
      group("g1"),
      group("g2"),
      group("g3"),
    ]);
    const rows = wrapper.findAll(".grow");
    await rows[0].trigger("click", { ctrlKey: true });
    await rows[1].trigger("click", { ctrlKey: true });

    stackGroup.mockResolvedValue({});
    // The third row is OUTSIDE the selection: its button must say and do the
    // single-group thing.
    const outsideBtn = wrapper.findAll(".grow")[2].find(".gbtn--stack");
    expect(outsideBtn.text()).not.toContain("groups");
    await outsideBtn.trigger("click");
    await wrapper.vm.$nextTick();
    expect(stackGroup).toHaveBeenCalledTimes(1);
    expect(store.selectionCount).toBe(2);
  });
});

describe("DuplicateQueue — the render window", () => {
  it("follows the scroll, not just the keyboard focus", async () => {
    // The regression this pins: the window was anchored to focusIndex alone,
    // so a mouse user scrolling a 327-group queue saw ~9 rows and then blank
    // spacer for the rest.
    const many = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper } = await mountQueue(many);
    const before = wrapper.vm.windowedGroups.map((e) => e.index);
    expect(before).not.toContain(19);

    const list = wrapper.find(".qlist");
    // ~row 15 at the estimate pitch (happy-dom never refines the measure).
    list.element.scrollTop = 15 * 140;
    await list.trigger("scroll");
    await wrapper.vm.$nextTick();
    const after = wrapper.vm.windowedGroups.map((e) => e.index);
    expect(after).toContain(19);
    // Scroll-anchored, not a union with the focus window: the mounted count
    // must stay a constant, so the head of the queue unmounts behind us.
    expect(after).not.toContain(0);
  });

  it("sizes the scroll track for the whole queue, not the pages loaded so far", async () => {
    // The regression this pins: the spacers stood for the LOADED rows, so the
    // track grew every time a page landed. The thumb shrank and jumped under
    // the user's hand and "the bottom" moved each time they reached it.
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 200 });

    // What the track stands for, in rows: the two spacers plus the rows that
    // are actually mounted between them (which have no height in happy-dom).
    const trackRows = () => {
      const spacers = wrapper
        .findAll(".qspacer")
        .reduce((px, s) => px + parseFloat(s.element.style.height || "0"), 0);
      return spacers / 140 + wrapper.vm.windowedGroups.length;
    };
    expect(trackRows()).toBe(200);

    listGroups.mockResolvedValue({
      groups: Array.from({ length: 20 }, (_, i) => group(`g${i + 21}`)),
      total: 200,
      offset: 20,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    const list = wrapper.find(".qlist");
    list.element.scrollTop = 15 * 140;
    await list.trigger("scroll");
    await flushPromises();
    await wrapper.vm.$nextTick();

    expect(store.groups.length).toBe(40);
    expect(trackRows()).toBe(200);
  });

  it("keeps paging while the scroll sits past the rows it holds", async () => {
    // A drag into the reserved-but-unloaded tail fires ONE scroll event. Without
    // a second trigger on arrival, the chase stalls a page short of the user.
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 200 });
    let served = 20;
    listGroups.mockImplementation(async () => {
      const next = Array.from({ length: 20 }, (_, i) =>
        group(`g${served + i + 1}`),
      );
      served += 20;
      return {
        groups: next,
        total: 200,
        offset: served,
        limit: 20,
        scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
      };
    });

    const list = wrapper.find(".qlist");
    list.element.scrollTop = 70 * 140;
    await list.trigger("scroll");
    for (let i = 0; i < 6; i += 1) {
      await flushPromises();
      await wrapper.vm.$nextTick();
    }
    // It walked out to the scroll position and then stopped, rather than
    // fetching one page or running to the end of the queue.
    expect(store.groups.length).toBeGreaterThanOrEqual(86);
    expect(store.groups.length).toBeLessThan(200);
  });

  it("every mounted row may decode thumbnails", async () => {
    const { wrapper } = await mountQueue([group("g1"), group("g2"), group("g3")]);
    for (const entry of wrapper.vm.windowedGroups) {
      expect(entry.loadThumbnails).toBe(true);
    }
  });
});

describe("DuplicateQueue — the tier gate", () => {
  // Nothing about the ladder is hardcoded: the ids, the prerequisites and which
  // tier cannot be switched off all arrive from GET /dedup/policy.
  it("renders the tiers the server published, with tier 1 locked", async () => {
    const { wrapper } = await mountQueue([group("g1")], {
      byTier: { exact: 1204, near: 96, embedding: 9 },
    });
    wrapper.find(".dq-btn").trigger("click");
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".tierrow");
    expect(rows).toHaveLength(3);
    expect(rows[0].text()).toContain("always included");
    // Tier 3 is unreachable until tier 2 is on, so it must not be pressable.
    expect(rows[2].attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });
});

describe("DuplicateQueue — a read-only session", () => {
  // Navigation stays live because reading the queue is not a verdict; the bulk
  // action is a verdict, so it goes.
  it("hides the bulk auto-stack button", async () => {
    getCounts.mockResolvedValue({
      unresolved_groups: 1,
      by_tier: { exact: 12 },
      tiers: [],
      scan: { state: "idle" },
    });
    readOnlyRef.value = true;
    const { wrapper, store } = await mountQueue([group("g1")]);
    store.exactCount = 12;
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".dq-btn--accent").exists()).toBe(false);
    wrapper.unmount();
  });

  it("tells Compare that the session cannot act", async () => {
    readOnlyRef.value = true;
    const { wrapper } = await mountQueue([group("g1")]);
    expect(
      wrapper.findComponent({ name: "DedupCompareDialog" }).props("readOnly"),
    ).toBe(true);
    wrapper.unmount();
  });
});
