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
  // The queue subscribes to the shared store's actions to reload after an
  // undo/redo; the mock records the listeners so a test can drive one.
  const actionListeners = [];
  const operationStoreMock = {
    undo,
    refresh: vi.fn(),
    nextUndo: null,
    nextRedo: null,
    past: [],
    operations: [],
    $onAction: (cb) => {
      actionListeners.push(cb);
      return () => {};
    },
  };
  return {
    useOperationStore: () => operationStoreMock,
    // Named helpers UndoControl imports from the same module.
    summarizeOperation: () => "",
    formatOperationTime: () => "",
    iconForOpType: () => "mdi-history",
    __operationStoreMock: operationStoreMock,
    __actionListeners: actionListeners,
  };
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
import {
  __operationStoreMock,
  __actionListeners,
} from "../../stores/useOperationStore";
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
      "v-slider": true,
      // Its History popover needs Vuetify's v-menu; the queue only needs to
      // know the control is mounted.
      UndoControl: true,
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
  // The queue's thumbnail size is remembered in localStorage, and the row pitch
  // every spacer is sized from follows it. A case that changes the size would
  // otherwise resize the rows of every case after it.
  window.localStorage.clear();
  setActivePinia(createPinia());
  readOnlyRef.value = false;
  routeMock.name = "duplicates";
  routeMock.query = {};
  routerReplace.mockReset();
  __actionListeners.length = 0;
  __operationStoreMock.refresh.mockReset();
  __operationStoreMock.nextUndo = null;
  __operationStoreMock.nextRedo = null;
  __operationStoreMock.past = [];
  __operationStoreMock.operations = [];
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
    for (const phrase of [
      "1 to 9",
      "X leaves",
      "Escape",
      "ever deleted",
      // Amendment #3's scheme, stated where a screen reader can find it.
      "Enter or S",
      "K keeps it separate",
      "Down moves on without deciding",
    ]) {
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

  // A backend that HAS made keep-separate undoable mirrors the stack
  // response (batch_id present): the standard undo receipt narrates it, so
  // the info toast would say the same thing twice and stands down.
  it("hands narration to the receipt when the backend recorded the verdict", async () => {
    const { wrapper } = await mountQueue([group("g1", 3)]);
    keepGroupSeparate.mockResolvedValue({
      verdict: "keep_separate",
      batch_id: "srv-9",
    });
    const info = useNoticeStore().info;

    wrapper.findComponent({ name: "DedupGroupRow" }).vm.$emit("keep-separate");
    await flushPromises();

    expect(info).not.toHaveBeenCalled();
    expect(__operationStoreMock.refresh).toHaveBeenCalledWith({
      narrate: true,
    });
    wrapper.unmount();
  });
});

describe("DuplicateQueue — one toolbar", () => {
  // The queue used to carry a second bar whose right half was a row of key
  // hints. Every one of those keys is already stated on the row it acts on, in
  // Compare's footer, or in the description a screen reader reads, so the bar
  // was explanation the user had to look past on every visit.
  it("carries the count and the Decided toggle, and no key hints", async () => {
    const { wrapper } = await mountQueue([group("g1"), group("g2")]);
    const toolbar = wrapper.find(".dq-toolbar");
    expect(toolbar.find(".qtitle").text()).toContain("2 groups to review");
    expect(toolbar.find(".qdecided").exists()).toBe(true);
    expect(wrapper.find(".qhead").exists()).toBe(false);
    expect(wrapper.find(".khint").exists()).toBe(false);
    expect(toolbar.findAll("kbd")).toHaveLength(0);
    // The keys themselves are not hidden, they are stated where they act: the
    // focused row still wears its Enter/S/C chips.
    expect(wrapper.find(".grow--focus").findAll("kbd").length).toBeGreaterThan(
      0,
    );
    wrapper.unmount();
  });

  // The one thing that stayed on a second row, and it is state rather than
  // explanation: it appears with the selection and leaves with it.
  it("raises the bulk bar only while a selection is live", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    expect(wrapper.find(".qselbar").exists()).toBe(false);

    store.toggleSelected(0);
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".qselbar").text()).toContain("2 groups selected");

    store.clearSelection();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".qselbar").exists()).toBe(false);
    wrapper.unmount();
  });

  // The slider drives the rows, so the height it publishes has to reach them.
  it("hands the size level's height to every row", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    const heightOf = () =>
      wrapper.findComponent({ name: "DedupGroupRow" }).props("thumbHeight");
    expect(heightOf()).toBe(112);

    store.setSizeLevel(6);
    await wrapper.vm.$nextTick();
    expect(heightOf()).toBe(232);
    wrapper.unmount();
  });
});

describe("DuplicateQueue — who owns the keyboard", () => {
  // The bug: the handler was bound on the queue root, so the shortcuts only
  // worked while the DOM focus was inside it. One click on a sidebar row and
  // every key went dead, with nothing on screen to say why.
  it("still answers keys after the focus has left the queue", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    const elsewhere = document.createElement("button");
    document.body.appendChild(elsewhere);
    elsewhere.focus();
    expect(document.activeElement).toBe(elsewhere);

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
    );
    expect(store.focusIndex).toBe(1);

    elsewhere.remove();
    wrapper.unmount();
  });

  // The other half of the same coin: a document-bound handler must not answer
  // keys meant for a dialog raised over the queue.
  it("hands the keyboard to a dialog the queue did not open", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    const scrim = document.createElement("div");
    scrim.className = "v-overlay--active";
    scrim.innerHTML = '<div class="v-overlay__scrim"></div>';
    document.body.appendChild(scrim);

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
    );
    expect(store.focusIndex).toBe(0);

    scrim.remove();
    wrapper.unmount();
  });

  // And it must stop listening when the destination is left, or a key pressed
  // in the grid would move a cursor in a queue that is no longer on screen.
  it("stops listening once the view is gone", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    wrapper.unmount();
    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
    );
    expect(store.focusIndex).toBe(0);
  });

  // Ctrl+A pages the queue in, so it is not instant and it can stop short.
  // Both facts are narrated: a bulk verdict on a set whose size the user never
  // saw is exactly what the announcement is for.
  it("narrates what Ctrl+A actually selected", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 30 });
    listGroups.mockResolvedValue({
      groups: Array.from({ length: 10 }, (_, i) => group(`g${i + 21}`)),
      total: 30,
      offset: 30,
      limit: 200,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", {
        key: "a",
        ctrlKey: true,
        bubbles: true,
      }),
    );
    await flushPromises();
    await wrapper.vm.$nextTick();

    expect(store.selectionCount).toBe(30);
    expect(wrapper.find('[data-testid="dedup-announcement"]').text()).toContain(
      "Selected all 30 groups",
    );
    wrapper.unmount();
  });
});

describe("DuplicateQueue — the toolbar hands the keyboard back", () => {
  function key(name, target = document) {
    const event = new window.KeyboardEvent("keydown", {
      key: name,
      bubbles: true,
      cancelable: true,
    });
    target.dispatchEvent(event);
    return event;
  }

  // The user's report: after changing something in the toolbar, Enter/S and
  // the arrows went to the focused control (or a still-open popover) instead
  // of the queue, until a click in the grid.
  it("a tier toggle returns focus to the queue and the keys act again", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    await wrapper.find(".dq-tier-wrap .dq-btn").trigger("click");
    wrapper
      .findComponent({ name: "DedupTierMenu" })
      .vm.$emit("toggle", "near", true);
    await flushPromises();

    expect(document.activeElement).toBe(wrapper.find(".dq").element);
    key("ArrowDown");
    expect(store.focusIndex).toBe(1);
    stackGroup.mockResolvedValue({});
    key("Enter");
    await flushPromises();
    expect(stackGroup).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("a pointer-committed threshold change hands the keyboard back with the popover open", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    await wrapper.find(".dq-tier-wrap .dq-btn").trigger("click");
    const menu = wrapper.findComponent({ name: "DedupTierMenu" });
    // The drag begins with a pointer press inside the popover.
    menu.element.dispatchEvent(
      new window.MouseEvent("mousedown", { bubbles: true }),
    );
    menu.vm.$emit("threshold", 0.8);
    await flushPromises();

    // The popover stays open (the count is what the user tunes against)...
    expect(wrapper.findComponent({ name: "DedupTierMenu" }).exists()).toBe(
      true,
    );
    // ...but the keyboard is the queue's again.
    expect(document.activeElement).toBe(wrapper.find(".dq").element);
    key("ArrowDown");
    expect(store.focusIndex).toBe(1);
    wrapper.unmount();
  });

  // Every keyboard arrow fires its own change; yanking focus after the first
  // would turn the rest of the tuning into row moves.
  it("keyboard threshold tuning keeps focus on the slider", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    // The slider is disabled until a looser tier is on.
    store.nearEnabled = true;
    await wrapper.find(".dq-tier-wrap .dq-btn").trigger("click");
    await wrapper.vm.$nextTick();
    const input = wrapper.find(".tm-threshold-input");
    input.element.focus();
    wrapper.findComponent({ name: "DedupTierMenu" }).vm.$emit("threshold", 0.85);
    await flushPromises();
    expect(document.activeElement).toBe(input.element);
    wrapper.unmount();
  });

  it("keys pressed on the threshold slider never fire verdicts", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    await wrapper.find(".dq-tier-wrap .dq-btn").trigger("click");
    const input = wrapper.find(".tm-threshold-input").element;
    input.focus();
    stackGroup.mockResolvedValue({});
    key("s", input);
    key("Enter", input);
    await flushPromises();
    expect(stackGroup).not.toHaveBeenCalled();
    expect(keepGroupSeparate).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("Escape on the threshold slider still dismisses the popover to its trigger", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    store.nearEnabled = true;
    const trigger = wrapper.find(".dq-tier-wrap .dq-btn");
    await trigger.trigger("click");
    await wrapper.vm.$nextTick();
    const input = wrapper.find(".tm-threshold-input");
    input.element.focus();
    await input.trigger("keydown", { key: "Escape" });
    expect(wrapper.findComponent({ name: "DedupTierMenu" }).exists()).toBe(
      false,
    );
    expect(document.activeElement).toBe(trigger.element);
    wrapper.unmount();
  });

  it("a pointer-committed size change hands the keyboard back", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    wrapper.findComponent(".dq-size-slider").vm.$emit("end", 4);
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(wrapper.find(".dq").element);
    wrapper.unmount();
  });

  it("flipping to Decided hands the keyboard to the list", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    await wrapper.find(".qdecided").trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(wrapper.find(".dq").element);
    wrapper.unmount();
  });

  // Moving BETWEEN controls must never get focus yanked: only a committed
  // change hands it back.
  it("Tab through the toolbar is never claimed by the queue", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    const decided = wrapper.find(".qdecided").element;
    decided.focus();
    const event = key("Tab", decided);
    expect(event.defaultPrevented).toBe(false);
    expect(document.activeElement).toBe(decided);
    wrapper.unmount();
  });

  // Settings opens a dialog, and the dialog owns focus per the a11y rules:
  // nothing may steal it to the queue.
  it("opening Settings leaves focus with the dialog flow", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    const btn = wrapper.find('button[title="Settings"]');
    btn.element.focus();
    await btn.trigger("click");
    expect(document.activeElement).toBe(btn.element);
    wrapper.unmount();
  });
});

describe("DuplicateQueue — the shell chrome", () => {
  // Duplicates replaces the grid, and with it the grid's toolbar; the
  // app-wide chrome (Settings, the stats rail toggle, undo/redo) is not the
  // grid's and must not vanish with it.
  it("carries Settings, the stats toggle and undo/redo like every other view", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    expect(wrapper.find('button[title="Settings"]').exists()).toBe(true);
    expect(wrapper.find(".tb-stats-btn").exists()).toBe(true);
    expect(wrapper.findComponent({ name: "UndoControl" }).exists()).toBe(true);
    wrapper.unmount();
  });

  it("asks App.vue for the settings dialog, like the grid toolbar does", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    await wrapper.find('button[title="Settings"]').trigger("click");
    expect(wrapper.emitted("open-settings")).toHaveLength(1);
    wrapper.unmount();
  });

  // The decision record's canonical tail, identical in every view:
  // [separator] [UndoControl] [TbGlobalActions]. No ⋯ anywhere in this bar
  // (amendment #2): every foldable here compresses or hides in its own group.
  it("orders the tail separator → UndoControl → TbGlobalActions, no burger", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    const undo = wrapper.findComponent({ name: "UndoControl" }).element;
    // TbGlobalActions is multi-root; its Settings button is a stable anchor.
    const globalActions = wrapper.find('button[title="Settings"]').element;
    const follows = (a, b) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(undo.previousElementSibling.classList.contains("dq-tb-sep")).toBe(
      true,
    );
    expect(follows(undo, globalActions)).toBe(true);
    expect(wrapper.find(".tbo-wrap").exists()).toBe(false);
    wrapper.unmount();
  });

  // The separator amendments: with the Decided toggle COMPRESSING instead of
  // folding (amendment #2), D-S1's left flank is always populated — so both
  // rules render at all widths and neither carries a fold class.
  it("both separators render at all widths, neither carrying a fold class", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    const separators = wrapper.findAll(".dq-toolbar .dq-tb-sep");
    expect(separators).toHaveLength(2);
    for (const separator of separators) {
      expect(separator.classes()).not.toContain("dq-fold-720");
    }
    wrapper.unmount();
  });

  // The Decided toggle compresses (icon-only at ≤720, the Auto-stack
  // pattern), so it must carry its own accessible name and keep its pressed
  // state at every width.
  it("the Decided toggle exposes its label and keeps aria-pressed", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    const toggle = wrapper.find(".dq-toolbar .qdecided");
    expect(toggle.attributes("title")).toBe("Decided");
    expect(toggle.attributes("aria-label")).toBe("Decided");
    expect(toggle.attributes("aria-pressed")).toBe("false");
    expect(toggle.find(".qdecided-label").text()).toBe("Decided");

    listGroups.mockResolvedValue({
      groups: [],
      total: 0,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await store.toggleDecided();
    await wrapper.vm.$nextTick();
    const flipped = wrapper.find(".dq-toolbar .qdecided");
    expect(flipped.attributes("title")).toBe("Back to review");
    expect(flipped.attributes("aria-label")).toBe("Back to review");
    expect(flipped.attributes("aria-pressed")).toBe("true");
    wrapper.unmount();
  });

  // The tier trigger's label ellipsizes under pressure and hides entirely at
  // ≤720, so the button must carry its own accessible name at every width
  // (WCAG 4.1.2 — a hidden span would leave it empty).
  it("the tier button exposes its label as title and aria-label", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    const button = wrapper.find(".dq-tier-wrap .dq-btn");
    const label = wrapper.vm.tierLabel;
    expect(label).toBeTruthy();
    expect(button.attributes("title")).toBe(label);
    expect(button.attributes("aria-label")).toBe(label);
    expect(button.find(".dq-tier-label").text()).toBe(label);
    wrapper.unmount();
  });

  // Undo is owner-only on the server; reading the queue is not.
  it("drops undo/redo in a read-only session but keeps Settings", async () => {
    readOnlyRef.value = true;
    const { wrapper } = await mountQueue([group("g1")]);
    expect(wrapper.findComponent({ name: "UndoControl" }).exists()).toBe(false);
    expect(wrapper.find('button[title="Settings"]').exists()).toBe(true);
    wrapper.unmount();
  });
});

describe("DuplicateQueue — undo puts the queue back", () => {
  /** Drive the queue's operation-store subscription as Pinia would. */
  async function runUndoAction(name, args = []) {
    const afters = [];
    for (const listener of __actionListeners) {
      listener({ name, args, after: (cb) => afters.push(cb) });
    }
    for (const cb of afters) await cb();
  }

  // The regression this pins: undoing a stack verdict reopened the group
  // server-side and corrected the badge, but the visible list kept showing
  // one row fewer until a remount — the count said N+1 over N rows.
  it("reloads the queue after an undo that reverted a dedup operation", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    // The undo toast is the shared receipt, mounted here like every view.
    expect(wrapper.findComponent({ name: "ActionReceipt" }).exists()).toBe(
      true,
    );

    __operationStoreMock.nextUndo = {
      id: 9,
      op_type: "dedup.stack",
      batch_id: "b1",
    };
    listGroups.mockClear();
    listGroups.mockResolvedValue({
      groups: [group("g1"), group("g2"), group("g9")],
      total: 3,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });

    await runUndoAction("undo");
    await flushPromises();
    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(listGroups.mock.calls[0][0].offset).toBe(0);
    expect(store.groups.map((g) => g.signature)).toContain("g9");
    wrapper.unmount();
  });

  it("redo of a dedup operation reloads the same way", async () => {
    const { wrapper } = await mountQueue([group("g1")]);
    __operationStoreMock.nextRedo = { id: 4, op_type: "dedup.stack" };
    listGroups.mockClear();
    await runUndoAction("redo");
    await flushPromises();
    expect(listGroups).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  // The Decided screen participates in the same lifecycle: the post-undo
  // reload targets whichever flip is showing, because loadFirstPage carries
  // `decided: showingDecided`. Undoing a verdict from the flip removes the
  // group from Decided (it is back in the queue)...
  it("undo of a keep-separate while ON the Decided flip reloads the Decided list", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g9"), verdict: "keep_separate", decided_at: "2026-07-30" },
      ],
      total: 1,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await store.toggleDecided();
    await wrapper.vm.$nextTick();
    expect(store.groups.map((g) => g.signature)).toEqual(["g9"]);

    __operationStoreMock.nextUndo = { id: 5, op_type: "dedup.keep_separate" };
    listGroups.mockClear();
    getCounts.mockClear();
    listGroups.mockResolvedValue({
      groups: [],
      total: 0,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await runUndoAction("undo");
    await flushPromises();

    expect(listGroups).toHaveBeenCalledTimes(1);
    expect(listGroups.mock.calls[0][0].decided).toBe(true);
    expect(store.showingDecided).toBe(true);
    expect(store.groups).toHaveLength(0);
    // The badge reconciles from the server on the same pass (item 5).
    expect(getCounts).toHaveBeenCalled();
    wrapper.unmount();
  });

  // ...and redo puts it back on Decided.
  it("redo while ON the Decided flip returns the group to Decided", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    listGroups.mockResolvedValue({
      groups: [],
      total: 0,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await store.toggleDecided();
    await wrapper.vm.$nextTick();
    expect(store.groups).toHaveLength(0);

    __operationStoreMock.nextRedo = { id: 6, op_type: "dedup.keep_separate" };
    listGroups.mockClear();
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g9"), verdict: "keep_separate", decided_at: "2026-07-30" },
      ],
      total: 1,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await runUndoAction("redo");
    await flushPromises();

    expect(listGroups.mock.calls[0][0].decided).toBe(true);
    expect(store.groups.map((g) => g.signature)).toEqual(["g9"]);
    expect(store.showingDecided).toBe(true);
    wrapper.unmount();
  });

  // The undo controls and the receipt surface are toolbar/root chrome, not
  // the queue list's: the flip must not unmount them.
  it("keeps the undo controls and receipt surface on the Decided flip", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g9"), verdict: "stacked", decided_at: "2026-07-30" },
      ],
      total: 1,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await store.toggleDecided();
    await wrapper.vm.$nextTick();
    expect(wrapper.findComponent({ name: "UndoControl" }).exists()).toBe(true);
    expect(wrapper.findComponent({ name: "ActionReceipt" }).exists()).toBe(
      true,
    );
    wrapper.unmount();
  });

  // A reopened verdict is rescan-proof server-side, but the group may not
  // match the CURRENT lens (rescanned away, tier switched off since). The
  // reload must land on an honest empty state, never a crash or stale count.
  it("tolerates an undone group that does not reappear in the current lens", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    stackGroup.mockResolvedValue({ batch_id: "srv-1" });
    await store.stack(store.groups[0]);
    expect(store.hasGroups).toBe(false);

    __operationStoreMock.nextUndo = { id: 7, op_type: "dedup.stack" };
    listGroups.mockClear();
    listGroups.mockResolvedValue({
      groups: [],
      total: 0,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await runUndoAction("undo");
    await flushPromises();
    await wrapper.vm.$nextTick();

    expect(store.groups).toHaveLength(0);
    expect(store.focusIndex).toBe(-1);
    expect(store.total).toBe(0);
    expect(wrapper.find(".qdone").exists()).toBe(true);
    wrapper.unmount();
  });

  // Undoing an unrelated change must not yank a triage in progress back to
  // the top: the reload is scoped to dedup operations.
  it("leaves the queue alone for an undo that touched nothing dedup", async () => {
    const { wrapper } = await mountQueue([group("g1"), group("g2")]);
    __operationStoreMock.nextUndo = { id: 3, op_type: "tags.edit" };
    listGroups.mockClear();
    await runUndoAction("undo");
    await flushPromises();
    expect(listGroups).not.toHaveBeenCalled();
    wrapper.unmount();
  });
});

describe("DuplicateQueue — verdicts inside Compare", () => {
  function compare(wrapper) {
    return wrapper.findComponent({ name: "DedupCompareDialog" });
  }

  function pressC() {
    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "c", bubbles: true }),
    );
  }

  // The regression this pins: a verdict from Compare's footer closed the
  // dialog, so triaging a run of groups there meant reopening it per group.
  // Now the store's auto-advance flips the dialog to the next group in place,
  // and it closes only when the queue has nothing left.
  it("a footer verdict advances to the next group with the dialog open", async () => {
    const { wrapper } = await mountQueue([
      group("g1"),
      group("g2"),
      group("g3"),
    ]);
    stackGroup.mockResolvedValue({});
    pressC();
    await wrapper.vm.$nextTick();
    expect(compare(wrapper).props("open")).toBe(true);

    compare(wrapper).vm.$emit("stack");
    await flushPromises();
    expect(compare(wrapper).props("open")).toBe(true);
    expect(compare(wrapper).props("group").signature).toBe("g2");

    keepGroupSeparate.mockResolvedValue({});
    compare(wrapper).vm.$emit("keep-separate");
    await flushPromises();
    expect(compare(wrapper).props("open")).toBe(true);
    expect(compare(wrapper).props("group").signature).toBe("g3");
    wrapper.unmount();
  });

  it("the verdict on the LAST group closes the dialog", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    stackGroup.mockResolvedValue({});
    pressC();
    await wrapper.vm.$nextTick();
    expect(compare(wrapper).props("open")).toBe(true);

    compare(wrapper).vm.$emit("stack");
    await flushPromises();
    expect(store.hasGroups).toBe(false);
    expect(compare(wrapper).props("open")).toBe(false);
    wrapper.unmount();
  });

  // Enter and S while Compare is open must do exactly what the footer
  // buttons do: decide, advance in place, close only at the end.
  it("keyboard Enter and S advance the same way", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    stackGroup.mockResolvedValue({});
    pressC();
    await wrapper.vm.$nextTick();

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
    await flushPromises();
    expect(compare(wrapper).props("open")).toBe(true);
    expect(store.focusedGroup.signature).toBe("g2");

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
    );
    await flushPromises();
    expect(store.hasGroups).toBe(false);
    expect(compare(wrapper).props("open")).toBe(false);
    wrapper.unmount();
  });

  // Up/Down leaf through the queue from inside Compare: the dialog renders
  // the focused group, so a focus move flips it in place.
  it("ArrowDown and ArrowUp switch the compared group, clamped at the ends", async () => {
    const { wrapper, store } = await mountQueue([
      group("g1"),
      group("g2"),
      group("g3"),
    ]);
    pressC();
    await wrapper.vm.$nextTick();
    expect(compare(wrapper).props("group").signature).toBe("g1");

    const key = (name) =>
      document.dispatchEvent(
        new window.KeyboardEvent("keydown", { key: name, bubbles: true }),
      );
    key("ArrowDown");
    await wrapper.vm.$nextTick();
    expect(compare(wrapper).props("open")).toBe(true);
    expect(compare(wrapper).props("group").signature).toBe("g2");

    key("ArrowUp");
    await wrapper.vm.$nextTick();
    expect(compare(wrapper).props("group").signature).toBe("g1");
    // The queue never wraps; neither does Compare.
    key("ArrowUp");
    await wrapper.vm.$nextTick();
    expect(compare(wrapper).props("group").signature).toBe("g1");

    // A verdict after an arrow switch hits the group being SHOWN.
    key("ArrowDown");
    stackGroup.mockResolvedValue({});
    key("Enter");
    await flushPromises();
    expect(stackGroup).toHaveBeenCalledWith("g2", expect.anything());
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g3"]);
    wrapper.unmount();
  });

  // A failed verdict changes nothing: same group, dialog still up, failure
  // reported. Advancing past a group that was NOT resolved would bury it.
  it("stays on the same group when the verdict fails", async () => {
    const { wrapper } = await mountQueue([group("g1"), group("g2")]);
    stackGroup.mockRejectedValue(new Error("locked"));
    pressC();
    await wrapper.vm.$nextTick();

    compare(wrapper).vm.$emit("stack");
    await flushPromises();
    expect(compare(wrapper).props("open")).toBe(true);
    expect(compare(wrapper).props("group").signature).toBe("g1");
    expect(errorSpy).toHaveBeenCalled();
    wrapper.unmount();
  });
});

describe("DuplicateQueue — End means the true end", () => {
  // The regression this pins: End focused the last LOADED row, so on a paging
  // queue it had to be pressed once per page. The scroll track is already
  // sized from the server total, so one press pins the scroll to the real
  // bottom while the store pages the rest in and lands the focus there.
  it("one End press lands on the true last group with nothing left to page", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 60 });
    let served = 20;
    listGroups.mockImplementation(async ({ limit }) => {
      const size = Math.min(limit, 60 - served);
      const next = Array.from({ length: size }, (_, i) =>
        group(`g${served + i + 1}`),
      );
      served += size;
      return {
        groups: next,
        total: 60,
        offset: served,
        limit,
        scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
      };
    });

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "End", bubbles: true }),
    );
    for (let i = 0; i < 6; i += 1) {
      await flushPromises();
      await wrapper.vm.$nextTick();
    }

    expect(store.groups.length).toBe(60);
    expect(store.focusIndex).toBe(59);
    expect(store.hasMore).toBe(false);
    expect(store.endChaseActive).toBe(false);
    // The completion is narrated as an ordinary focus move onto the last row.
    expect(
      wrapper.find('[data-testid="dedup-announcement"]').text(),
    ).toContain("Group 60 of 60");
    wrapper.unmount();
  });

  // Over a LARGE gap the total tells End exactly which cards to fetch: one
  // offset request for the tail page, the window rebased onto it, no walk
  // through the middle and no skeletons streaming past.
  it("End over a large gap jumps straight to the tail page", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 200 });
    listGroups.mockClear();
    listGroups.mockImplementation(async ({ offset = 0, limit }) => {
      const size = Math.max(0, Math.min(limit, 200 - offset));
      return {
        groups: Array.from({ length: size }, (_, i) =>
          group(`g${offset + i + 1}`),
        ),
        total: 200,
        offset,
        limit,
        scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
      };
    });

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "End", bubbles: true }),
    );
    for (let i = 0; i < 6; i += 1) {
      await flushPromises();
      await wrapper.vm.$nextTick();
    }

    // ONE tail request, offset-only — never a cursor alongside it.
    expect(listGroups).toHaveBeenCalledTimes(1);
    const tail = listGroups.mock.calls[0][0];
    expect(tail.offset).toBe(180);
    expect(tail.cursor).toBeUndefined();
    expect(store.windowStart).toBe(180);
    expect(store.groups.length).toBe(20);
    expect(store.focusIndex).toBe(199);
    // The tail's cards are mounted at their absolute indices.
    const indices = wrapper.vm.windowedGroups.map((e) => e.index);
    expect(indices).toContain(199);
    expect(
      wrapper.find('[data-testid="dedup-announcement"]').text(),
    ).toContain("Group 200 of 200");
    wrapper.unmount();
  });

  it("scrolling up from the jumped tail backfills the page above, spacers intact", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 200 });
    listGroups.mockImplementation(async ({ offset = 0, limit }) => {
      const size = Math.max(0, Math.min(limit, 200 - offset));
      return {
        groups: Array.from({ length: size }, (_, i) =>
          group(`g${offset + i + 1}`),
        ),
        total: 200,
        offset,
        limit,
        scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
      };
    });
    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "End", bubbles: true }),
    );
    for (let i = 0; i < 6; i += 1) {
      await flushPromises();
      await wrapper.vm.$nextTick();
    }
    expect(store.windowStart).toBe(180);

    // The user drags up past the window's start: the previous page prepends,
    // the window grows upwards, and the track's height does not move.
    const trackRows = () => {
      const spacers = wrapper
        .findAll(".qspacer")
        .reduce((px, s) => px + parseFloat(s.element.style.height || "0"), 0);
      return spacers / 140 + wrapper.vm.windowedGroups.length;
    };
    const list = wrapper.find(".qlist");
    list.element.scrollTop = 170 * 140;
    await list.trigger("scroll");
    await flushPromises();
    await wrapper.vm.$nextTick();

    expect(store.windowStart).toBe(160);
    expect(store.groups.length).toBe(40);
    expect(store.groups[0].signature).toBe("g161");
    expect(trackRows()).toBe(200);
    // The rows around the scroll position are mounted, absolute indices.
    expect(wrapper.vm.windowedGroups.map((e) => e.index)).toContain(170);
    wrapper.unmount();
  });

  it("Home after an End jump returns to the top window", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 200 });
    listGroups.mockImplementation(async ({ offset = 0, limit }) => {
      const size = Math.max(0, Math.min(limit, 200 - offset));
      return {
        groups: Array.from({ length: size }, (_, i) =>
          group(`g${offset + i + 1}`),
        ),
        total: 200,
        offset,
        limit,
        scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
      };
    });
    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "End", bubbles: true }),
    );
    for (let i = 0; i < 6; i += 1) {
      await flushPromises();
      await wrapper.vm.$nextTick();
    }
    expect(store.windowStart).toBe(180);

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Home", bubbles: true }),
    );
    for (let i = 0; i < 6; i += 1) {
      await flushPromises();
      await wrapper.vm.$nextTick();
    }
    expect(store.windowStart).toBe(0);
    expect(store.focusIndex).toBe(0);
    expect(store.groups[0].signature).toBe("g1");
    expect(wrapper.vm.windowedGroups.map((e) => e.index)).toContain(0);
    wrapper.unmount();
  });

  it("End with everything loaded focuses the last row at once, as before", async () => {
    const { wrapper, store } = await mountQueue([
      group("g1"),
      group("g2"),
      group("g3"),
    ]);
    listGroups.mockClear();
    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "End", bubbles: true }),
    );
    await wrapper.vm.$nextTick();
    expect(store.focusIndex).toBe(2);
    expect(listGroups).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("Home mid-chase cancels the jump and the user's position wins", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 60 });
    let release;
    listGroups.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "End", bubbles: true }),
    );
    expect(store.endChaseActive).toBe(true);
    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Home", bubbles: true }),
    );
    expect(store.endChaseActive).toBe(false);

    // The page already on the wire lands, but the focus stays where the user
    // put it: a chase that yanked them back down would be worse than the bug.
    release({
      groups: [group("g21")],
      total: 60,
      offset: 21,
      limit: 200,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await flushPromises();
    await wrapper.vm.$nextTick();
    expect(store.focusIndex).toBe(0);
    wrapper.unmount();
  });

  it("a scroll away from the tail mid-chase cancels it", async () => {
    const page1 = Array.from({ length: 20 }, (_, i) => group(`g${i + 1}`));
    const { wrapper, store } = await mountQueue(page1, { total: 60 });
    listGroups.mockImplementation(() => new Promise(() => {}));

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "End", bubbles: true }),
    );
    expect(store.endChaseActive).toBe(true);
    // Let the pin land before the user drags away from it.
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    const list = wrapper.find(".qlist");
    list.element.scrollTop = 0;
    await list.trigger("scroll");
    expect(store.endChaseActive).toBe(false);
    wrapper.unmount();
  });
});

describe("DuplicateQueue — the Decided page", () => {
  it("lists decided groups with their verdict and clears one on demand", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g9"), verdict: "keep_separate", decided_at: "2026-07-29" },
        { ...group("g8"), verdict: "stacked", decided_at: "2026-07-29" },
      ],
      total: 2,
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
    // BOTH verdict kinds land here with their state and the way back.
    const rows = wrapper.findAllComponents({ name: "DedupGroupRow" });
    expect(rows[0].text()).toContain("Kept separate");
    expect(rows[0].text()).toContain("Clear decision");
    expect(rows[1].text()).toContain("Stacked");
    expect(rows[1].text()).toContain("Clear decision");
    const row = rows[0];
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

  // Escape peels one layer at a time, and the Decided flip is a layer: one
  // press returns to the review queue, keyboard handed straight back.
  it("Escape on the Decided flip returns to the review queue with focus", async () => {
    const { wrapper, store } = await mountQueue([group("g1"), group("g2")]);
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g9"), verdict: "keep_separate", decided_at: "2026-07-30" },
      ],
      total: 1,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await store.toggleDecided();
    await wrapper.vm.$nextTick();
    expect(store.showingDecided).toBe(true);

    listGroups.mockResolvedValue({
      groups: [group("g1"), group("g2")],
      total: 2,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    await flushPromises();

    expect(store.showingDecided).toBe(false);
    expect(store.groups.map((g) => g.signature)).toEqual(["g1", "g2"]);
    expect(document.activeElement).toBe(wrapper.find(".dq").element);
    wrapper.unmount();
  });

  // A dialog or popover on top still takes precedence: Escape closes IT
  // first, and only the next press leaves Decided.
  it("Escape peels an open popover before leaving Decided", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g9"), verdict: "stacked", decided_at: "2026-07-30" },
      ],
      total: 1,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await store.toggleDecided();
    await wrapper.vm.$nextTick();
    await wrapper.find(".dq-tier-wrap .dq-btn").trigger("click");

    await wrapper.find(".dq").trigger("keydown", { key: "Escape" });
    expect(wrapper.findComponent({ name: "DedupTierMenu" }).exists()).toBe(
      false,
    );
    expect(store.showingDecided).toBe(true);

    document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    await flushPromises();
    expect(store.showingDecided).toBe(false);
    wrapper.unmount();
  });

  // The backend orders the decided listing newest-decision-first; the client
  // renders the SERVER's order and never re-sorts it.
  it("renders decided rows in the server's order", async () => {
    const { wrapper, store } = await mountQueue([group("g1")]);
    listGroups.mockResolvedValue({
      groups: [
        { ...group("g5"), verdict: "keep_separate", decided_at: "2026-07-30" },
        { ...group("g2"), verdict: "stacked", decided_at: "2026-07-29" },
        { ...group("g9"), verdict: "keep_separate", decided_at: "2026-07-28" },
      ],
      total: 3,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    await store.toggleDecided();
    await wrapper.vm.$nextTick();

    expect(store.groups.map((g) => g.signature)).toEqual(["g5", "g2", "g9"]);
    expect(
      wrapper
        .findAllComponents({ name: "DedupGroupRow" })
        .map((row) => row.props("group").signature),
    ).toEqual(["g5", "g2", "g9"]);
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

  // The regression this pins (user report): the params were mirrored INTO the
  // URL, but a full reload dropped them again — the mirror ran on the policy
  // landing, one microtask before openQueue adopted the URL's filters, read
  // the still-default gate as "the user chose the defaults", and replaced the
  // URL without its filter params while the store was only just adopting
  // them. The filtersRestored gate keeps the mirror silent until then.
  it("a full reload keeps the filter params in the URL", async () => {
    routeMock.query = { near: "1", embedding: "0", threshold: "0.8" };
    getPolicy.mockResolvedValue({
      defaults: { near_enabled: false, embedding_enabled: false, threshold: 0.9 },
      bounds: BOUNDS,
    });
    listGroups.mockResolvedValue({
      groups: [group("g1")],
      total: 1,
      offset: 0,
      limit: 20,
      scan: { status: "complete", scanned_pictures: 1, total_pictures: 1 },
    });
    getCounts.mockResolvedValue({
      unresolved_groups: 1,
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
    await wrapper.vm.$nextTick();
    await flushPromises();

    // The filters are in force...
    expect(store.nearEnabled).toBe(true);
    expect(store.embeddingEnabled).toBe(false);
    expect(store.threshold).toBe(0.8);
    // ...and no mirror write ever stripped them from the address.
    for (const call of routerReplace.mock.calls) {
      expect(call[0].query.near).toBe("1");
      expect(call[0].query.threshold).toBe("0.8");
    }
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
