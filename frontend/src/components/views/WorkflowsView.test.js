// The Workflows grid (v1.12 F1a) — the four things the design decides that a
// reading of the component cannot confirm.
//
// jsdom has no layout, so the column count comes from a stubbed
// `clientWidth`/`ResizeObserver` pair rather than from a real box. That is the
// point of the arithmetic being in JS: the grid is TOLD how many columns to
// draw, so what the cursor computes and what the browser paints cannot drift,
// and the number is testable without a browser.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { reactive } from "vue";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  // Gated on `modelValue`: an always-open stub makes "the menu is not open
  // yet" unassertable, and the member menu's whole keyboard route is about
  // when it opens.
  const VMenu = {
    name: "VMenu",
    props: ["modelValue"],
    template: `<div><slot name="activator" :props="{}" /><slot v-if="modelValue" /></div>`,
  };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

const push = vi.fn();
// REACTIVE, and not a plain object: the component watches
// `() => route.query?.topology`, and in the real app `useRoute()` is reactive,
// so a test whose route is inert silently cannot see a query CHANGE - only
// whatever was set before mount. That gap hid a bug where the note outlived
// the query that caused it.
const route = reactive({ name: "workflows", query: {} });
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
  useRoute: () => route,
}));

const listWorkflowCards = vi.fn();
const getWorkflowCard = vi.fn();
const reorderStack = vi.fn();
const unstackWorkflow = vi.fn();
const patchWorkflowCard = vi.fn();
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  // `WorkflowCard` renders its covers through this, so a mock without it
  // throws in the render and every assertion in the file goes with it.
  workflowCoverUrl: (cover) => cover,
  reorderStack: (...args) => reorderStack(...args),
  unstackWorkflow: (...args) => unstackWorkflow(...args),
  patchWorkflowCard: (...args) => patchWorkflowCard(...args),
}));
const listImportFolders = vi.fn();
vi.mock("../../api/folders", () => ({
  listImportFolders: (...args) => listImportFolders(...args),
}));
vi.mock("../../api/comfyui", () => ({ importWorkflow: vi.fn() }));

import WorkflowCard from "../widgets/WorkflowCard.vue";
import WorkflowsView from "./WorkflowsView.vue";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useWorkflowPrefsStore } from "../../stores/useWorkflowPrefsStore";

const card = (key, extra = {}) => ({
  key,
  name: key,
  topology_hash: `topology-${key}`,
  models: [],
  loras: [],
  differs_by: [],
  picture_count: 1,
  rating: 3,
  rank: 1,
  covers: [],
  stack_size: 1,
  member_keys: [],
  ...extra,
});

// Six cards, the second and fifth being stacks of three. `last_used` runs
// OPPOSITE to `rank` on purpose, so switching the sort genuinely reverses the
// grid: a test that asserts the cursor stayed on its card proves nothing
// against an order that did not move.
const DAYS = ["01", "02", "03", "04", "05", "06"];
const CARDS = [
  card("a", { rank: 9, last_used: `2026-03-${DAYS[0]}T00:00:00Z` }),
  card("b", {
    rank: 8,
    last_used: `2026-03-${DAYS[1]}T00:00:00Z`,
    stack_size: 3,
    member_keys: ["b1", "b2"],
  }),
  card("c", { rank: 7, last_used: `2026-03-${DAYS[2]}T00:00:00Z` }),
  card("d", { rank: 6, last_used: `2026-03-${DAYS[3]}T00:00:00Z` }),
  card("e", {
    rank: 5,
    last_used: `2026-03-${DAYS[4]}T00:00:00Z`,
    stack_size: 3,
    member_keys: ["e1", "e2"],
  }),
  card("f", { rank: 4, last_used: `2026-03-${DAYS[5]}T00:00:00Z` }),
];

/** The ResizeObserver callbacks jsdom does not provide. */
let observers = [];

function setWidth(wrapper, width) {
  Object.defineProperty(wrapper.find(".wfv-grid").element, "clientWidth", {
    value: width,
    configurable: true,
  });
  observers.forEach((callback) => callback());
}

// `attachTo: document.body` leaves its DOM behind, so every wrapper is
// tracked and unmounted: without it each test reads a document holding every
// earlier test's grid, and `document.activeElement` is whatever the last one
// focused.
const mounted = [];

function mountView() {
  const wrapper = mount(WorkflowsView, { attachTo: document.body });
  mounted.push(wrapper);
  return wrapper;
}

/** Mounted, loaded, and measured at `width` px of grid. */
async function grid(width = 1008) {
  const wrapper = mountView();
  await flush();
  setWidth(wrapper, width);
  await flush();
  return wrapper;
}

const flush = async () => {
  await new Promise((resolve) => setTimeout(resolve, 0));
};

const keys = (wrapper) =>
  wrapper
    .findAll(".wfv-grid [data-key]")
    .map((el) => el.attributes("data-key"));

/**
 * Let a collapsing panel finish.
 *
 * A gesture close holds the stack open until the panel reports its animation
 * done, so jsdom — which runs no animations — has to say so itself. Firing the
 * real event rather than reaching for `finishCollapse` keeps the wiring under
 * test: a panel that stopped reporting would leave these red.
 */
const settleClose = async (wrapper) => {
  const panel = wrapper.find('[data-testid="stack-panel"]');
  if (panel.exists()) panel.element.dispatchEvent(new Event("animationend"));
  await flush();
};

const cardKeys = (wrapper) =>
  wrapper.findAll(".wfv-row").map((el) => el.attributes("data-key"));

const memberKeys = (wrapper) =>
  wrapper
    .findAll(".stack-panel__member")
    .map((el) => el.attributes("data-key"));

const cursorKey = (wrapper) =>
  wrapper
    .findAll(".wfv-grid [data-key]")
    .find((el) => el.attributes("tabindex") === "0")
    ?.attributes("data-key");

beforeEach(() => {
  setActivePinia(createPinia());
  observers = [];
  push.mockClear();
  route.query = {};
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback) {
        observers.push(callback);
      }
      observe() {}
      disconnect() {}
    },
  );
  listImportFolders.mockResolvedValue({ folders: [] });
  reorderStack.mockReset();
  reorderStack.mockResolvedValue({ stack_id: "stack-b", keys: [] });
  unstackWorkflow.mockReset();
  unstackWorkflow.mockResolvedValue({ stack_id: null, keys: [] });
  patchWorkflowCard.mockReset();
  patchWorkflowCard.mockResolvedValue({ card: card("b1") });
  // A FRESH array per call, as a real response is: the store assigns it to
  // `cards`, and handing back the same object would make a refetch a no-op
  // that no watcher on the list could see.
  listWorkflowCards.mockImplementation(() =>
    Promise.resolve({ cards: [...CARDS], one_offs: 0, hidden: 0 }),
  );
  getWorkflowCard.mockImplementation((key) =>
    Promise.resolve({ card: card(key, { stack_size: 3 }) }),
  );
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
});

describe("one stack open at a time", () => {
  it("opening a second stack closes the first", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();

    await store.openStack("b");
    await flush();
    expect(wrapper.findAll('[data-testid="stack-panel"]')).toHaveLength(1);
    expect(keys(wrapper)).toContain("b1");

    await store.openStack("e");
    await flush();
    expect(wrapper.findAll('[data-testid="stack-panel"]')).toHaveLength(1);
    // The first stack's members are gone, the second's are drawn: the panel
    // moved rather than a second one opening below it.
    expect(keys(wrapper)).not.toContain("b1");
    expect(keys(wrapper)).toContain("e1");
  });

  it("marks the open card with ▸ and aria-expanded, and does not select it", async () => {
    const wrapper = await grid();
    await useWorkflowsStore().openStack("b");
    await flush();
    const open = wrapper
      .findAll(".wfv-row")
      .find((row) => row.attributes("data-key") === "b");
    expect(open.attributes("aria-expanded")).toBe("true");
    expect(open.attributes("aria-controls")).toBe("wfv-stack-panel");
    // Opening is not selecting. The wash is `[aria-selected="true"]`'s in
    // both stylesheets, so this is also what keeps the olive off the card and
    // off the band: nothing here is selected, and the panel is not a row that
    // could be. The colours themselves are CSS and are not asserted.
    expect(open.attributes("aria-selected")).toBe("false");
    const panel = wrapper.find('[data-testid="stack-panel"]');
    expect(panel.attributes("aria-selected")).toBeUndefined();
    expect(panel.attributes("role")).toBe("rowgroup");
  });
});

describe("the panel re-anchors on resize", () => {
  it("the notch follows the stack card's column, not a stored offset", async () => {
    // 1008px ÷ (240 + 12) = 4 columns. "b" is index 1, so its centre is
    // (1 + 0.5) / 4 = 37.5% across the panel.
    const wrapper = await grid(1008);
    await useWorkflowsStore().openStack("b");
    await flush();
    const panel = () => wrapper.find('[data-testid="stack-panel"]').element;
    expect(panel().style.getPropertyValue("--notch")).toBe("37.5%");
    expect(panel().style.getPropertyValue("--wf-columns")).toBe("4");

    // Narrow to 3 columns: "b" keeps index 1 and is now (1 + 0.5) / 3 across.
    setWidth(wrapper, 756);
    await flush();
    expect(panel().style.getPropertyValue("--notch")).toBe("50%");
    expect(panel().style.getPropertyValue("--wf-columns")).toBe("3");

    // One column: no centre worth pointing at, so the caret falls back to the
    // shipped `--start` class rather than to a copy of its inset.
    setWidth(wrapper, 300);
    await flush();
    expect(panel().style.getPropertyValue("--notch")).toBe("");
    expect(wrapper.find(".tbm-caret").classes()).toContain("tbm-caret--start");
  });

  it("the member block is padded to whole rows so later cards keep their column", async () => {
    const wrapper = await grid(1008);
    await useWorkflowsStore().openStack("b");
    await flush();
    // Four columns, so the panel opens after "d" — the end of "b"'s row — and
    // its three members plus one padding hole fill exactly one row:
    //   a b c d | b b1 b2 · | e f
    // "b" is in the grid AND, flagged, as the panel's first member.
    expect(cardKeys(wrapper)).toEqual(["a", "b", "c", "d", "e", "f"]);
    expect(memberKeys(wrapper)).toEqual(["b", "b1", "b2"]);
    expect(wrapper.find(".stack-cover-flag").text()).toBe("Cover");

    const gridEl = wrapper.find(".wfv-grid");
    // Down from "b1" (the member in column 1) reaches "f", which the browser
    // also draws in column 1 of the row after the panel. Drop the padding and
    // this is out of range: the block would be three long, not four.
    await wrapper.findAll(".stack-panel__member")[1].trigger("click");
    expect(cursorKey(wrapper)).toBe("b1");
    await gridEl.trigger("keydown", { key: "ArrowDown" });
    expect(cursorKey(wrapper)).toBe("f");

    // And the hole itself is not a stop: Down from "d" (column 3) skips it.
    await wrapper.findAll(".wfv-row")[3].trigger("click");
    await gridEl.trigger("keydown", { key: "ArrowDown" });
    expect(cursorKey(wrapper)).toBe("e");
  });

  it("a stack in the last, incomplete row keeps the panel reachable", async () => {
    // Six cards over four columns leaves the second row holding two, and "e"
    // is a stack drawn in column 1 of it:
    //   a b c d | e f
    // Splicing the member block in at `cards.length` — 6, not a multiple of
    // 4 — puts it at column 2, so from there `index % columns` names the
    // wrong column for every row: Down from "e" landed on a padding hole
    // with nothing below it and the cursor did not move at all, and Up from
    // a member crossed a column on the way. The stack's own row is padded to
    // whole first, which is what keeps the arithmetic true.
    //   a b c d | e f · · | e e1 e2 ·
    const wrapper = await grid(1008);
    await useWorkflowsStore().openStack("e");
    await flush();
    expect(memberKeys(wrapper)).toEqual(["e", "e1", "e2"]);

    const gridEl = wrapper.find(".wfv-grid");
    // "e" is flat index 4, column 0; the panel's cover row is index 8.
    await wrapper.findAll(".wfv-row")[4].trigger("click");
    expect(cursorKey(wrapper)).toBe("e");
    await gridEl.trigger("keydown", { key: "ArrowDown" });
    expect(cursorKey(wrapper)).toBe("e");
    expect(document.activeElement.className).toContain("stack-panel__member");

    // Up from "e1" (the member in column 1) is "f", which the browser draws
    // in column 1 of the row above the panel — not "b", three columns over.
    await wrapper.findAll(".stack-panel__member")[1].trigger("click");
    await gridEl.trigger("keydown", { key: "ArrowUp" });
    expect(cursorKey(wrapper)).toBe("f");
  });
});

describe("the keyboard crosses the panel boundary", () => {
  it("Down from the stack's row lands in the panel at the same column", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();

    // Put the cursor on "b" (flat index 1, column 1), then go down.
    await wrapper.findAll(".wfv-row")[1].trigger("click");
    expect(cursorKey(wrapper)).toBe("b");
    const gridEl = wrapper.find(".wfv-grid");
    await gridEl.trigger("keydown", { key: "ArrowDown" });
    // Flat index 1 + 4 = 5, which is the member drawn in column 1 of the
    // panel's row: the cover is column 0, b1 column 1.
    expect(cursorKey(wrapper)).toBe("b1");

    // And back up to the card that opened it.
    await gridEl.trigger("keydown", { key: "ArrowUp" });
    expect(cursorKey(wrapper)).toBe("b");
  });

  it("Space selects, and a member's key mixes with a top-level one", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();
    const gridEl = wrapper.find(".wfv-grid");

    await wrapper.findAll(".wfv-row")[0].trigger("click");
    await gridEl.trigger("keydown", { key: "ArrowRight" });
    // Space on the stack card takes the whole stack.
    await gridEl.trigger("keydown", { key: " " });
    expect(store.selectedKeys).toEqual(["a", "b", "b1", "b2"]);

    // And Space on one of its members takes that member back out, which is
    // the only way the panel's rows ever mark themselves.
    await gridEl.trigger("keydown", { key: "ArrowDown" });
    await gridEl.trigger("keydown", { key: " " });
    expect(store.selectedKeys).toEqual(["a", "b", "b2"]);
  });
});

describe("a stack wears one mark, not one per row", () => {
  // jsdom applies no SFC `<style>`, so what these pin is the hook each rule is
  // keyed on — `.stack-panel--selected` on the band, and the `selected` prop
  // the member cards no longer get while it is there. The colours themselves
  // are not assertable here and are not asserted.
  it("the band takes the hook while the whole stack is in, and gives it up when part comes out", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();

    const panel = () => wrapper.find('[data-testid="stack-panel"]');
    // What a member card is TOLD, which is what draws its own mark
    // (`WorkflowCard.vue`, `.wf-card--selected`): the band's mark replaces it.
    const markedCards = () =>
      wrapper
        .findAll(".stack-panel__member")
        .filter((row) => row.findComponent(WorkflowCard).props("selected"))
        .map((row) => row.attributes("data-key"));
    const selectedRows = () =>
      wrapper
        .findAll(".stack-panel__member")
        .filter((row) => row.attributes("aria-selected") === "true")
        .map((row) => row.attributes("data-key"));

    expect(panel().classes()).not.toContain("stack-panel--selected");

    // Clicking the stack card selects all three, so the band takes the mark and
    // the rows give theirs up: the cards stop being told they are selected, and
    // in List the row rule is switched off by its `:not(.stack-panel--selected)`
    // scope.
    await wrapper.findAll(".wfv-row")[1].trigger("click");
    expect(store.selectedKeys).toEqual(["b", "b1", "b2"]);
    expect(panel().classes()).toContain("stack-panel--selected");
    expect(markedCards()).toEqual([]);
    // The rows still SAY they are selected: they are, and a screen reader is
    // owed that whichever box the olive is painted on.
    expect(selectedRows()).toEqual(["b", "b1", "b2"]);

    // Take one member back out and the band gives the mark up: two of three
    // is not "this stack", and only the rows can say which two.
    await wrapper
      .findAll(".stack-panel__member")[1]
      .trigger("click", { ctrlKey: true });
    expect(store.selectedKeys).toEqual(["b", "b2"]);
    expect(selectedRows()).toEqual(["b", "b2"]);
    expect(panel().classes()).not.toContain("stack-panel--selected");
    // And the two that are still in go back to marking themselves, which is
    // the only way to see WHICH two.
    expect(markedCards()).toEqual(["b", "b2"]);
  });

  it("a stack that is merely open, or partly selected, does not take the hook", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    const panel = () => wrapper.find('[data-testid="stack-panel"]');

    // A different card selected, then this stack opened: opening is not
    // selecting, and the wash must not follow the panel around.
    store.select("a");
    await store.openStack("b");
    await flush();
    expect(panel().classes()).not.toContain("stack-panel--selected");

    // The cover alone is not the stack — this is what separates "every member
    // is in" from "any member is in", and it is the state a click on the
    // panel's Cover row leaves behind.
    await wrapper.findAll(".stack-panel__member")[0].trigger("click");
    expect(store.selectedKeys).toEqual(["b"]);
    expect(panel().classes()).not.toContain("stack-panel--selected");
  });

  it("the cover row inside the panel is separable from the stack it heads", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();

    // Same key, two rows: the grid's stack card and the panel's first member.
    // Clicking the card means the stack; clicking the row means that one
    // workflow, or the cover is the one card in a stack you cannot single out.
    await wrapper.findAll(".wfv-row")[1].trigger("click");
    expect(store.selectedKeys).toEqual(["b", "b1", "b2"]);
    await wrapper.findAll(".stack-panel__member")[0].trigger("click");
    expect(store.selectedKeys).toEqual(["b"]);
  });

  it("a Shift range into an open panel does not drag the rest of the stack back in", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();

    // Flat order is `a b c d | b b1 b2 · | e f`. Click "c" (index 2), then
    // Shift-click the panel's `b1` (index 5): the range is c, d, the panel's
    // COVER row and b1. The stack's CARD row is index 1 and outside it, so
    // nothing expands and `b2` — which the range never reaches — stays out.
    // Expanding every key in the store put `b2` back, so Shift undid what a
    // Ctrl-click had just done.
    await wrapper.findAll(".wfv-row")[2].trigger("click");
    await wrapper
      .findAll(".stack-panel__member")[1]
      .trigger("click", { shiftKey: true });
    expect(store.selectedKeys).toEqual(["c", "d", "b", "b1"]);

    // And a range that stays inside the panel takes only the rows it covers.
    await wrapper.findAll(".stack-panel__member")[1].trigger("click");
    expect(store.selectedKeys).toEqual(["b1"]);
    await wrapper
      .findAll(".stack-panel__member")[2]
      .trigger("click", { shiftKey: true });
    expect(store.selectedKeys).toEqual(["b1", "b2"]);

    // A range that DOES cross the stack card takes its whole stack, the same
    // way it would with the panel shut: "a" is index 0, so the range reaches
    // the card at index 1 and `b2` comes in through it.
    await wrapper.findAll(".wfv-row")[0].trigger("click");
    await wrapper
      .findAll(".stack-panel__member")[1]
      .trigger("click", { shiftKey: true });
    expect(store.selectedKeys).toEqual(["a", "b", "b1", "b2", "c", "d"]);
  });
});

describe("Esc closes the innermost thing first", () => {
  it("the panel before the selection, and never both at once", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();
    const gridEl = wrapper.find(".wfv-grid");
    await wrapper.findAll(".wfv-row")[1].trigger("click");
    expect(store.selectedKeys).toEqual(["b", "b1", "b2"]);

    await gridEl.trigger("keydown", { key: "Escape" });
    await settleClose(wrapper);
    expect(store.openStackKey).toBe(null);
    // The selection survives the first Escape: it is the outer thing.
    expect(store.selectedKeys).toEqual(["b", "b1", "b2"]);
    // And the cursor comes back to the card that had the panel.
    expect(cursorKey(wrapper)).toBe("b");

    await gridEl.trigger("keydown", { key: "Escape" });
    expect(store.selectedKeys).toEqual([]);
  });

  it("the sort popover owns its own Escape", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();
    wrapper.vm.sortMenuOpen = true;
    await wrapper.vm.$nextTick();
    await wrapper.find(".wfv-grid").trigger("keydown", { key: "Escape" });
    // The popover is what Escape was for; the panel stays open behind it.
    expect(store.openStackKey).toBe("b");
  });
});

// Five cards and a stack of FIVE members, so the member block is 5 over 4
// columns: one full row plus a ragged one with three holes in it. That is the
// arrangement in which a linear scan over the holes walks out of its column,
// and it is exactly what the block-of-three fixture above cannot show.
const RAGGED = [
  card("a", { rank: 9 }),
  card("b", {
    rank: 8,
    stack_size: 5,
    member_keys: ["b1", "b2", "b3", "b4"],
  }),
  card("c", { rank: 7 }),
  card("d", { rank: 6 }),
  card("e", { rank: 5 }),
  card("f", { rank: 4 }),
];

describe("the cursor survives the list being rebuilt", () => {
  it("a ragged member row still moves down by whole rows", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: RAGGED,
      one_offs: 0,
      hidden: 0,
    });
    const wrapper = await grid(1008);
    await useWorkflowsStore().openStack("b");
    await flush();
    // a b c d | b b1 b2 b3 | b4 · · · | e f
    const gridEl = wrapper.find(".wfv-grid");
    await wrapper.findAll(".stack-panel__member")[1].trigger("click");
    expect(cursorKey(wrapper)).toBe("b1");

    // b1 is at flat index 5, column 1. One row down is index 9, a hole; the
    // next row down is 13, which is "f" — also column 1, and what the browser
    // draws directly beneath it. A linear scan over the hole would hand back
    // "e", in column 0.
    await gridEl.trigger("keydown", { key: "ArrowDown" });
    expect(cursorKey(wrapper)).toBe("f");
  });

  it("keeps its card when the sort reorders the grid under it", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await wrapper.findAll(".wfv-row")[0].trigger("click");
    expect(cursorKey(wrapper)).toBe("a");

    // `last_used` runs opposite to `rank`, so this flips the grid end to end
    // and "a" goes from first to last. The cursor is an id, so it travels with
    // the card; held as an index it would now be sitting on "f".
    store.setSortKey("used");
    await wrapper.vm.$nextTick();
    expect(cardKeys(wrapper)).toEqual(["f", "e", "d", "c", "b", "a"]);
    expect(cursorKey(wrapper)).toBe("a");
  });

  it("closing a stack from the card's own caret leaves the grid reachable", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();

    // Cursor on a card AFTER the panel, so closing shrinks the list past it.
    await wrapper
      .findAll(".wfv-row")
      .find((row) => row.attributes("data-key") === "f")
      .trigger("click");
    expect(cursorKey(wrapper)).toBe("f");

    // ▸ on the open card, which is not the panel's Close: the list loses its
    // member block and every index after it shifts.
    store.toggleStack("b");
    await wrapper.vm.$nextTick();
    await settleClose(wrapper);
    expect(store.openStackKey).toBe(null);
    // The grid must still have exactly one tab stop, and it must still be "f".
    const stops = wrapper
      .findAll(".wfv-grid [data-key]")
      .filter((el) => el.attributes("tabindex") === "0");
    expect(stops).toHaveLength(1);
    expect(stops[0].attributes("data-key")).toBe("f");
  });
});

describe("the panel while its members are still arriving", () => {
  it("draws the cover and the stack's real size, not what has landed", async () => {
    const release = [];
    getWorkflowCard.mockImplementation(
      (key) =>
        new Promise((resolve) => {
          release.push(() => resolve({ card: card(key, { stack_size: 3 }) }));
        }),
    );
    const wrapper = await grid(1008);
    useWorkflowsStore().openStack("b");
    await wrapper.vm.$nextTick();

    // The panel is on screen at once, holding the card the grid already had.
    expect(wrapper.find('[data-testid="stack-panel"]').exists()).toBe(true);
    expect(memberKeys(wrapper)).toEqual(["b"]);
    // And it says three, because that is how many the stack HAS.
    expect(wrapper.find(".stack-panel__count").text()).toBe("3 workflows");
    expect(wrapper.find(".stack-panel__pending").text()).toBe(
      "reading the rest…",
    );

    release.forEach((resolve) => resolve());
    await flush();
    expect(memberKeys(wrapper)).toEqual(["b", "b1", "b2"]);
    expect(wrapper.find(".stack-panel__pending").exists()).toBe(false);
  });

  it("says so when a member could not be read, instead of counting it out", async () => {
    getWorkflowCard.mockRejectedValue(new Error("nope"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = await grid(1008);
    await useWorkflowsStore().openStack("b");
    await flush();

    expect(memberKeys(wrapper)).toEqual(["b"]);
    // Wrong if this reads "1 workflow": the stack still has three, and a
    // header that counts what arrived turns a failed request into a lie.
    expect(wrapper.find(".stack-panel__count").text()).toBe("3 workflows");
    expect(wrapper.find(".stack-panel__pending").text()).toBe(
      "2 could not be read",
    );
    warn.mockRestore();
  });
});

describe("what a screen reader is told", () => {
  it("owns the panel's rows from the expanded card, which they do not follow", async () => {
    const wrapper = await grid(1008);
    await useWorkflowsStore().openStack("b");
    await flush();
    const open = wrapper
      .findAll(".wfv-row")
      .find((row) => row.attributes("data-key") === "b");

    // In DOM order the member rows follow "d", the last card of "b"'s row, so
    // without `aria-owns` a reader on "b" pressing Down is handed "c".
    const owned = open.attributes("aria-owns").split(" ");
    expect(owned).toEqual([
      "wfv-stack-panel-row-b",
      "wfv-stack-panel-row-b1",
      "wfv-stack-panel-row-b2",
    ]);
    for (const id of owned) {
      expect(wrapper.find(`#${id}`).attributes("role")).toBe("row");
    }
    expect(wrapper.find(".wfv-grid").attributes("aria-multiselectable")).toBe(
      "true",
    );
  });

  it("announces the open once, with the real count, and announces the close", async () => {
    const release = [];
    getWorkflowCard.mockImplementation(
      (key) =>
        new Promise((resolve) => {
          release.push(() => resolve({ card: card(key, { stack_size: 3 }) }));
        }),
    );
    const wrapper = await grid(1008);
    const live = () => wrapper.find('[role="status"]').text();
    const store = useWorkflowsStore();

    store.openStack("b");
    await wrapper.vm.$nextTick();
    // Wrong if this says "0 workflows": counting what has arrived announces
    // twice, and the first of the two is false.
    expect(live()).toBe("b opened, 3 workflows");
    release.forEach((resolve) => resolve());
    await flush();
    expect(live()).toBe("b opened, 3 workflows");

    store.closeStack();
    await wrapper.vm.$nextTick();
    // Every row below the panel moves on the way back too.
    expect(live()).toBe("b closed");
  });
});

describe("the panel follows a change of selection", () => {
  const row = (wrapper, key) =>
    wrapper.findAll(".wfv-row").find((el) => el.attributes("data-key") === key);

  const panel = (wrapper) => wrapper.find('[data-testid="stack-panel"]');

  it("opens the stack a plain click selects, and moves it to the next", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();

    await row(wrapper, "b").trigger("click");
    await flush();
    expect(store.openStackKey).toBe("b");
    expect(memberKeys(wrapper)).toEqual(["b", "b1", "b2"]);

    await row(wrapper, "e").trigger("click");
    await flush();
    expect(store.openStackKey).toBe("e");
    expect(wrapper.findAll('[data-testid="stack-panel"]')).toHaveLength(1);
    expect(memberKeys(wrapper)).toEqual(["e", "e1", "e2"]);
  });

  it("holds the panel on screen, shrinking, while it closes", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    await row(wrapper, "b").trigger("click");
    await flush();

    await row(wrapper, "f").trigger("click", { ctrlKey: true });
    // Wrong if the panel has already gone: the rows the collapse is drawn on
    // go with the open key, so dropping it first leaves nothing to animate.
    expect(panel(wrapper).exists()).toBe(true);
    expect(panel(wrapper).classes()).toContain("stack-panel--closing");
    expect(memberKeys(wrapper)).toEqual(["b", "b1", "b2"]);

    await settleClose(wrapper);
    expect(store.openStackKey).toBe(null);
    expect(panel(wrapper).exists()).toBe(false);
  });

  it("keeps the panel open while the picking stays inside it", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    await row(wrapper, "b").trigger("click");
    await flush();

    const member = (key) =>
      wrapper
        .findAll(".stack-panel__member")
        .find((el) => el.attributes("data-key") === key);
    await member("b1").trigger("click");
    await member("b2").trigger("click", { ctrlKey: true });
    expect(store.selectedKeys).toEqual(["b1", "b2"]);
    expect(store.openStackKey).toBe("b");
    expect(panel(wrapper).classes()).not.toContain("stack-panel--closing");
  });

  it("brings the cursor back out of a panel it closes", async () => {
    // The grid has ONE tab stop. Ctrl-clicking a member while a card outside
    // the stack is selected shuts the panel from under a cursor standing on a
    // member row, and without this the stop, and the focus with it, leaves the
    // screen entirely.
    const wrapper = await grid();
    await row(wrapper, "f").trigger("click");
    await flush();
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();

    await wrapper
      .findAll(".stack-panel__member")
      .find((el) => el.attributes("data-key") === "b1")
      .trigger("click", { ctrlKey: true });
    expect(store.selectedKeys).toEqual(["f", "b1"]);
    expect(cursorKey(wrapper)).toBe("b1");

    await settleClose(wrapper);
    const stops = wrapper
      .findAll(".wfv-grid [data-key]")
      .filter((el) => el.attributes("tabindex") === "0");
    expect(stops).toHaveLength(1);
    expect(stops[0].attributes("data-key")).toBe("b");
  });

  it("gives the second Escape to the selection while the panel shuts", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    const gridEl = wrapper.find(".wfv-grid");
    await row(wrapper, "b").trigger("click");
    await flush();

    await gridEl.trigger("keydown", { key: "Escape" });
    expect(store.panelClosing).toBe(true);
    // Wrong if this is swallowed by the panel a second time: the collapse is
    // already running, so the innermost thing left is the selection.
    await gridEl.trigger("keydown", { key: "Escape" });
    expect(store.selectedKeys).toEqual([]);
  });
});

describe("the empty state", () => {
  it("offers the two routes in that do not need a connection", async () => {
    listWorkflowCards.mockResolvedValue({ cards: [], one_offs: 0, hidden: 0 });
    const wrapper = mountView();
    await flush();
    expect(wrapper.find(".wfv-empty__title").text()).toBe("Nothing found yet");
    // The stubbed icon renders as its own glyph name beside the label.
    const actions = wrapper
      .findAll(".wfv-empty__actions button")
      .map((el) => el.text())
      .join(" | ");
    expect(actions).toContain("Drop a workflow file");
    // ComfyUI is not connected here, so its route out is offered.
    expect(actions).toContain("Connect ComfyUI");
    // No watched folder in this library, so neither the button nor the path.
    expect(actions).not.toContain("Open watched folder");
    expect(wrapper.find(".wfv-empty__path").exists()).toBe(false);
  });

  it("names the watched folder by its host path and opens it", async () => {
    listWorkflowCards.mockResolvedValue({ cards: [], one_offs: 0, hidden: 0 });
    listImportFolders.mockResolvedValue({
      folders: [
        // `folder` is where the server sees it; `host_path` is where the person
        // does, and is what a person can act on, so it wins when both exist.
        {
          id: 7,
          folder: "/srv/incoming",
          host_path: "/home/me/ComfyUI/output",
        },
        { id: 8, folder: "/srv/second" },
      ],
    });
    const wrapper = mountView();
    await flush();

    expect(wrapper.find(".wfv-empty__path").text()).toBe(
      "/home/me/ComfyUI/output",
    );
    const open = wrapper
      .findAll(".wfv-empty__actions button")
      .find((button) => button.text().includes("Open watched folder"));
    await open.trigger("click");
    expect(push).toHaveBeenCalledWith({
      name: "import-folder",
      params: { id: "7" },
    });
  });
});

// ── The Recipe section's Open ─────────────────────────────────────────────
//
// `/workflows?topology=<hash>` is the link a picture's Recipe section pushes.
// The shelf honoured it and the grid replaced the shelf, so it has to land on
// the card rather than at the top of the list.
describe("arriving on ?topology=", () => {
  it("selects the card that topology made and puts the cursor on it", async () => {
    route.query = { topology: "topology-d" };
    const wrapper = await grid();

    expect(useWorkflowsStore().selectedKeys).toEqual(["d"]);
    expect(cursorKey(wrapper)).toBe("d");
  });

  // The link names ONE workflow — it is pushed by one picture's Recipe panel —
  // so it selects one card even when that card heads a stack. Whole-stack
  // selection is a GESTURE on the stack card, which is why `select`'s `whole`
  // is opt-in: defaulting it to true made this link select "b", "b1" and "b2"
  // for a picture made by "b" alone.
  it("selects one workflow, not its stack, when the link lands on a cover", async () => {
    route.query = { topology: "topology-b" };
    const wrapper = await grid();

    expect(useWorkflowsStore().selectedKeys).toEqual(["b"]);
    expect(cursorKey(wrapper)).toBe("b");
  });

  // The grid is not every card: `GET /workflows/cards` leaves out the hidden
  // ones and the one-offs, which is the ordinary state of a workflow used
  // once. Saying nothing would drop the reader at the top of a grid that does
  // not hold what they clicked, looking as though the link did nothing.
  it("says so, rather than nothing, when the grid does not list it", async () => {
    route.query = { topology: "topology-nothing" };
    const wrapper = await grid();

    expect(useWorkflowsStore().selectedKeys).toEqual([]);
    // The grid's own default: the first row holds the only tab stop.
    expect(cursorKey(wrapper)).toBe("a");
    expect(wrapper.find(".wfv-note").text()).toContain(
      "That workflow is not in this grid",
    );
  });

  // The verdict is the PAYLOAD's, not the drawn grid's. A client-side filter
  // hiding the card is one × away from fixed, and calling that "not in this
  // grid" sends the reader looking for a workflow they already have.
  it("tells a filtered-out card apart from one the answer never held", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    // Every card is rated 3, so 5★ and up hides all of them — including `d`,
    // which the link below names and the payload plainly holds.
    store.setFilters({ minRating: 5 });
    await flush();

    route.query = { topology: "topology-d" };
    await flush();

    const note = wrapper.find('[data-testid="wfv-link-filtered"]');
    expect(note.exists()).toBe(true);
    expect(note.text()).toContain("in this grid, but a filter is hiding it");
    expect(wrapper.text()).not.toContain("That workflow is not in this grid");

    // And it is one control away from fixed, which the other note never is.
    await note.find(".wfv-note-clear").trigger("click");
    await flush();
    expect(wrapper.find('[data-testid="wfv-link-filtered"]').exists()).toBe(
      false,
    );
    expect(store.selectedKeys).toEqual(["d"]);
  });

  it("names what is being left out, since that is usually the reason", async () => {
    listWorkflowCards.mockImplementation(() =>
      Promise.resolve({ cards: [...CARDS], one_offs: 12, hidden: 40 }),
    );
    route.query = { topology: "topology-nothing" };
    const wrapper = await grid();

    const note = wrapper.find(".wfv-note").text();
    expect(note).toContain("52 are being left out");
    expect(note).toContain("40 hidden");
    expect(note).toContain("12 counted as one-offs");
  });

  // `withheld` is a count, and 1 is the common shape of a small library.
  it('says "1 is being left out", not "1 are"', async () => {
    listWorkflowCards.mockImplementation(() =>
      Promise.resolve({ cards: [...CARDS], one_offs: 0, hidden: 1 }),
    );
    route.query = { topology: "topology-nothing" };
    const wrapper = await grid();

    const note = wrapper.find(".wfv-note").text();
    expect(note).toContain("1 is being left out");
    expect(note).not.toContain("1 are being left out");
    // The live region is built from the same parts and says it too.
    expect(wrapper.find('[role="status"]').text()).toContain(
      "1 is being left out",
    );
  });

  it("says nothing while the cards are still on the wire", async () => {
    // "Not here" is false until the grid has been read, and a note that
    // appears and then corrects itself is worse than one that waits.
    let land;
    listWorkflowCards.mockImplementation(
      () => new Promise((resolve) => (land = resolve)),
    );
    route.query = { topology: "topology-d" };
    const wrapper = mountView();
    await flush();

    expect(wrapper.find(".wfv-note").exists()).toBe(false);

    land({ cards: [...CARDS], one_offs: 0, hidden: 0 });
    await flush();
    expect(wrapper.find(".wfv-note").exists()).toBe(false);
    expect(useWorkflowsStore().selectedKeys).toEqual(["d"]);
  });

  // The store outlives the component, so the `immediate` pass runs during
  // setup against whatever the last visit left in `cards` - before
  // `onMounted` refetches. A verdict reached there must stay revisable.
  it("revises a miss decided on a stale card list", async () => {
    await grid();
    mounted.pop().unmount(); // leaves `cards` and `loaded` in the store

    // The workflow crossed the one-off threshold while we were away, so the
    // fresh answer holds a card the stale list does not.
    const arrived = card("newcomer", { rank: 1 });
    listWorkflowCards.mockImplementation(() =>
      Promise.resolve({ cards: [...CARDS, arrived], one_offs: 0, hidden: 0 }),
    );
    route.query = { topology: "topology-newcomer" };

    const second = await grid();

    expect(second.find(".wfv-note").exists()).toBe(false);
    expect(useWorkflowsStore().selectedKeys).toEqual(["newcomer"]);
  });

  // The sidebar's Workflows entry pushes this same route with no query, so
  // the query can go without a remount. The note belongs to the link.
  it("drops the note when the query goes away", async () => {
    route.query = { topology: "topology-nothing" };
    const wrapper = await grid();
    expect(wrapper.find(".wfv-note").exists()).toBe(true);

    route.query = {};
    await flush();

    expect(wrapper.find(".wfv-note").exists()).toBe(false);
  });

  // `flatRows` is rebuilt when `columns` lands, and `measure()` runs as a
  // pre-flush job — so a row looked up BEFORE the tick names a different seat
  // by the time `moveCursor` reads it.
  //
  // The window needs `columns` to move between the watcher's synchronous part
  // and its `nextTick`, which means the grid must measure a real width on its
  // FIRST `measure()` — jsdom reports 0, so the prototype is stubbed for this
  // test alone. With a stack open (which survives unmount) the two disagree by
  // a whole row: `f` is index 8 at one column, and index 8 at four columns is
  // `e`.
  it("lands on the right card when the columns change under it", async () => {
    await grid();
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();
    mounted.pop().unmount();
    expect(store.openStackKey).toBe("b");

    const owned = Object.getOwnPropertyDescriptor(
      HTMLElement.prototype,
      "clientWidth",
    );
    Object.defineProperty(HTMLElement.prototype, "clientWidth", {
      configurable: true,
      get: () => 1008,
    });
    try {
      route.query = { topology: "topology-f" };
      const second = mountView();
      await flush();

      expect(cursorKey(second)).toBe("f");
    } finally {
      if (owned)
        Object.defineProperty(HTMLElement.prototype, "clientWidth", owned);
      else delete HTMLElement.prototype.clientWidth;
    }
  });

  it("does not yank focus out of the open Sort popover", async () => {
    // The cards arrive asynchronously, so this can fire a second after the
    // screen went interactive — possibly mid-gesture.
    let land;
    listWorkflowCards.mockImplementation(
      () => new Promise((resolve) => (land = resolve)),
    );
    route.query = { topology: "topology-d" };
    const wrapper = mountView();
    await flush();
    setWidth(wrapper, 1008);

    wrapper.vm.sortMenuOpen = true;
    await wrapper.vm.$nextTick();
    land({ cards: [...CARDS], one_offs: 0, hidden: 0 });
    await flush();

    // Selected, because that is what the link asked for and it costs no focus.
    expect(useWorkflowsStore().selectedKeys).toEqual(["d"]);
    // But the cursor never moved, so nothing was taken off the popover.
    expect(document.activeElement).not.toBe(
      wrapper.find('[data-key="d"]').element,
    );
  });

  it("does not take focus back after a refetch", async () => {
    route.query = { topology: "topology-d" };
    const wrapper = await grid();
    expect(cursorKey(wrapper)).toBe("d");

    // The reader moves on, and something re-reads the grid under them - a
    // file added, a LoRA slot flipped. The link must not be applied twice.
    await wrapper.find('[data-key="f"]').trigger("click");
    expect(cursorKey(wrapper)).toBe("f");

    await useWorkflowsStore().fetchCards();
    await flush();

    expect(cursorKey(wrapper)).toBe("f");
  });
});

// ── The app-wide toolbar tail ─────────────────────────────────────────────
//
// #1415: this view replaces the grid and its toolbar, so without the tail
// nothing on this screen opens Settings or the right rail - and `WorkflowTab`
// only renders while that rail is open (`AppInspector` gates it on
// `sidebarStore.statsOpen`), so F3's whole deliverable is unreachable.
//
// Six assertions ported from the retired shelf's suite, which pinned exactly
// this and went with the shelf. `Toolbar.test.js` does NOT stand in for them:
// it reads the `<style>` block as text, so it stays green against a component
// whose template no longer draws the tail at all.
describe("the app-wide toolbar tail", () => {
  it("asks App.vue for Settings and toggles the rail itself", async () => {
    const wrapper = await grid();
    const sidebar = useSidebarStore();

    await wrapper
      .find(".wfv-toolbar button[aria-label='Settings']")
      .trigger("click");
    expect(wrapper.emitted("open-settings")).toHaveLength(1);

    // From the shut state a fresh session starts in: the first press opens the
    // rail, the second closes it.
    sidebar.statsOpen = false;
    await wrapper.find(".wfv-toolbar .tb-stats-btn").trigger("click");
    expect(sidebar.statsOpen).toBe(true);
    await wrapper.find(".wfv-toolbar .tb-stats-btn").trigger("click");
    expect(sidebar.statsOpen).toBe(false);
  });

  it("orders the tail separator → TbGlobalActions, last in the bar", async () => {
    // Placement, not presence: the app-wide chrome sits after this view's own
    // controls, ruled off from them, at the end - as ModelShelf's bar does.
    const wrapper = await grid();
    const bar = wrapper.find(".wfv-toolbar").element;
    const tail = wrapper.find(".wfv-bar-tail").element;
    // TbGlobalActions is multi-root; its Settings button is a stable anchor.
    const settings = wrapper.find(
      ".wfv-toolbar button[aria-label='Settings']",
    ).element;
    const separator = wrapper.find(".wfv-toolbar .bar-separator").element;
    // "Add…" is the last of this view's own controls.
    const add = wrapper
      .findAll(".wfv-toolbar button")
      .find((button) => button.text().includes("Add")).element;
    const follows = (a, b) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(follows(add, separator)).toBe(true);
    // Adjacency, not merely order: the rule marks the boundary, so nothing may
    // slip between it and the chrome it rules off.
    expect(separator.nextElementSibling).toBe(settings);
    // Nothing of this view's own follows the app-wide chrome. TbGlobalActions
    // is multi-root, so its stats button is the tail's last element.
    expect(bar.lastElementChild).toBe(tail);
    expect(tail.lastElementChild.classList.contains("tb-stats-btn")).toBe(true);
  });

  // Nothing here writes to the operation log, so undo/redo and the History
  // popover are not offered at all - the model shelf's exception, for its
  // reason. `UNDO_BLIND_ROOTS` declines the chord to match.
  it("mounts no undo control", async () => {
    const wrapper = await grid();
    expect(wrapper.findComponent({ name: "UndoControl" }).exists()).toBe(false);
  });

  // The toggle's tooltip is its accessible name, and the rail on this screen
  // is the workflow inspector, not the stats sidebar it is everywhere else.
  it("names the rail it actually opens here", async () => {
    const wrapper = await grid();
    const stats = wrapper.find(".wfv-toolbar .tb-stats-btn");
    expect(stats.attributes("aria-label")).toBe("Show inspector");
    useSidebarStore().statsOpen = true;
    await wrapper.vm.$nextTick();
    expect(stats.attributes("aria-label")).toBe("Hide inspector");
  });

  // The emit above is only half of it: App.vue has to listen, and nothing else
  // in this suite fails if that binding is deleted. Asserted against the source
  // because App.vue needs a mount harness this suite does not have - the same
  // `readFileSync` shape `Toolbar.test.js` uses for its bar recipe.
  it("is listened to by App.vue, which owns the Settings dialog", async () => {
    const { readFileSync } = await import("node:fs");
    const app = readFileSync(`${process.cwd()}/src/App.vue`, "utf8");
    const tag = app.slice(
      app.indexOf("<WorkflowsView"),
      app.indexOf(">", app.indexOf("<WorkflowsView")),
    );
    expect(tag).toContain('@open-settings="openSettingsDialog"');
  });

  // Nothing contends for this rail any more. The run panel that used to
  // outrank it became a popup in v1.12 F5 (#1407) - mounted in App.vue over
  // everything rather than in the rail - so `/workflows` is the one branch
  // left ahead of the stats panel, and a second one reappearing here would
  // mean the rail could open on the wrong thing again. Source-asserted for
  // the reason above.
  it("gives the rail to the Workflow tab, with nothing contending", async () => {
    const { readFileSync } = await import("node:fs");
    const app = readFileSync(`${process.cwd()}/src/App.vue`, "utf8");
    expect(app).not.toContain("WorkflowRunPanel");
    const rail = app.indexOf("<WorkflowTab v-if");
    expect(rail).toBeGreaterThan(-1);
    expect(app.slice(rail, rail + 80)).toContain('v-if="isWorkflowsView"');
    // The stats panel is still the fallback, not a second branch above it.
    expect(app.slice(rail, rail + 200)).toContain("<StatsSidebar v-else");
  });
});

// ── Selection is visible ─────────────────────────────────────────────────
//
// `aria-selected` on the row was right all along; what was missing was
// anything a sighted reader could see, because the mark sat on the cell under
// an opaque card. These assert the PAINTED class, on both sides of the panel
// boundary, since a mixed selection has to read as one thing.
describe("the selection mark", () => {
  const markedKeys = (wrapper) =>
    wrapper
      .findAll(".wf-card--selected")
      .map((el) => el.element.closest("[data-key]")?.dataset.key);

  it("marks a selected grid card", async () => {
    const wrapper = await grid();

    await wrapper.find('[data-key="c"]').trigger("click");

    expect(useWorkflowsStore().selectedKeys).toEqual(["c"]);
    expect(markedKeys(wrapper)).toEqual(["c"]);
  });

  it("marks selected members inside an open stack", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();

    await wrapper.find('.stack-panel__member[data-key="b1"]').trigger("click");
    await flush();

    expect(store.selectedKeys).toEqual(["b1"]);
    expect(markedKeys(wrapper)).toEqual(["b1"]);
  });

  it("marks nothing when nothing is selected", async () => {
    const wrapper = await grid();
    expect(wrapper.findAll(".wf-card--selected")).toHaveLength(0);
  });
});

describe("reordering a stack from the keyboard", () => {
  // `b` is a stack of three: cover `b`, then `b1` and `b2`. Its `stack_id` is
  // what `PUT /workflows/stacks/{id}/order` is addressed by, and the grid is
  // the only thing that carries it.
  const STACKED = CARDS.map((entry) =>
    entry.key === "b" ? { ...entry, stack_id: "stack-b" } : entry,
  );

  /** A grid whose stack `b` is open, with its members drawn. */
  async function openStackB() {
    listWorkflowCards.mockResolvedValue({
      cards: STACKED,
      one_offs: 0,
      hidden: 0,
    });
    const wrapper = await grid();
    await useWorkflowsStore().openStack("b");
    await flush();
    return wrapper;
  }

  /** Alt+Arrow on whichever row holds the cursor. */
  async function altArrow(wrapper, key) {
    await wrapper.find(".wfv-grid").trigger("keydown", { key, altKey: true });
    await flush();
  }

  it("sends the WHOLE order, with the moved key in its new place", async () => {
    const wrapper = await openStackB();
    // Put the cursor on the second member, which is `b1`.
    await wrapper.find('.stack-panel__member[data-key="b1"]').trigger("click");
    await altArrow(wrapper, "ArrowDown");

    // Not `["b1"]` and not a delta: the route refuses anything but a complete
    // ordered list of what the stack holds, because a key left out would be
    // dropped from the stack with no record that it had gone.
    expect(reorderStack).toHaveBeenCalledWith("stack-b", ["b", "b2", "b1"]);
  });

  it("moves a member up, and makes it the cover at position 0", async () => {
    const wrapper = await openStackB();
    await wrapper.find('.stack-panel__member[data-key="b1"]').trigger("click");
    await altArrow(wrapper, "ArrowUp");
    expect(reorderStack).toHaveBeenCalledWith("stack-b", ["b1", "b", "b2"]);
  });

  it("does nothing at either end rather than wrapping round", async () => {
    const wrapper = await openStackB();
    // The cover cannot move earlier…
    await wrapper.find('.stack-panel__member[data-key="b"]').trigger("click");
    await altArrow(wrapper, "ArrowUp");
    expect(reorderStack).not.toHaveBeenCalled();
    // …and the last member cannot move later.
    await wrapper.find('.stack-panel__member[data-key="b2"]').trigger("click");
    await altArrow(wrapper, "ArrowDown");
    expect(reorderStack).not.toHaveBeenCalled();
  });

  it("swallows a top-level card's Alt+Arrow rather than moving the cursor", async () => {
    const wrapper = await openStackB();
    await wrapper.find('.wfv-row[data-key="a"]').trigger("click");
    await altArrow(wrapper, "ArrowDown");
    // The grid's order is the sort, so there is nothing to reorder — and Alt
    // must not fall through to the plain cursor either, or a held Alt+Down
    // reorders a member twice and then walks off down the grid.
    expect(reorderStack).not.toHaveBeenCalled();
    expect(cursorKey(wrapper)).toBe("a");
  });

  it("says nothing, and announces nothing, with no stack id to address", async () => {
    // A stack the payload carries no `stack_id` for — which is what the
    // server serves for a stack the grid drew only part of.
    const wrapper = await grid();
    await useWorkflowsStore().openStack("b");
    await flush();
    await wrapper.find('.stack-panel__member[data-key="b1"]').trigger("click");
    await altArrow(wrapper, "ArrowDown");
    expect(reorderStack).not.toHaveBeenCalled();
    // The live region must not report a move that did not happen: it is the
    // only thing a screen-reader user has to tell them the list is unchanged.
    expect(wrapper.find('[role="status"]').text()).not.toMatch(/position/);
  });

  it("announces the new position once a move lands", async () => {
    const wrapper = await openStackB();
    await wrapper.find('.stack-panel__member[data-key="b1"]').trigger("click");
    await altArrow(wrapper, "ArrowDown");
    // The mocked server hands the same cards back, so the ORDER cannot move
    // here — what is asserted is that a landed move speaks at all, against
    // the test above proving a dropped one stays silent.
    expect(wrapper.find('[role="status"]').text()).toMatch(
      /b1, position \d+ of 3/,
    );
  });

  it("opens the member menu from Shift+F10, the only keyboard route to it", async () => {
    const wrapper = await openStackB();
    await wrapper.find('.stack-panel__member[data-key="b1"]').trigger("click");
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(false);

    await wrapper
      .find(".wfv-grid")
      .trigger("keydown", { key: "F10", shiftKey: true });
    await flush();
    // Both ⋯ buttons are `tabindex="-1"` — the grid owns Tab — so without
    // this Unstack and Hide are reachable by pointer only.
    const menu = wrapper.find('[data-testid="member-menu"]');
    expect(menu.exists()).toBe(true);
    expect(menu.findAll(".ctx-item")).toHaveLength(5);
  });

  it("unstacks and hides through the routes those verbs name", async () => {
    const wrapper = await openStackB();
    await wrapper.find('.stack-panel__member[data-key="b1"]').trigger("click");
    await wrapper
      .find(".wfv-grid")
      .trigger("keydown", { key: "F10", shiftKey: true });
    await flush();

    // Index 3 is Unstack, index 4 is Hide. Driven end to end rather than off
    // the panel's emits: the store reaches for `patchWorkflowCard`, and a
    // mock naming anything else is swallowed by `hideMember`'s own catch and
    // reads as the feature working.
    await wrapper.findAll(".ctx-item")[3].trigger("click");
    await flush();
    expect(unstackWorkflow).toHaveBeenCalledWith("b1");

    await wrapper.find('.stack-panel__member[data-key="b1"]').trigger("click");
    await wrapper
      .find(".wfv-grid")
      .trigger("keydown", { key: "F10", shiftKey: true });
    await flush();
    await wrapper.findAll(".ctx-item")[4].trigger("click");
    await flush();
    expect(patchWorkflowCard).toHaveBeenCalledWith("b1", { hidden: true });
    expect(useWorkflowsStore().error).toBe("");
  });
});

describe("the cursor in List", () => {
  // The flat index space is the same in both views — the member block is
  // still padded to whole grid rows so the cards AFTER the panel keep naming
  // their column. Only the STEP changes, and a step of `columns` over a list
  // drawn one member per line walks past two of every three.
  async function listGrid() {
    const wrapper = await grid();
    useWorkflowPrefsStore().setStackView("list");
    await useWorkflowsStore().openStack("b");
    await flush();
    return wrapper;
  }

  const arrow = async (wrapper, key) => {
    await wrapper.find(".wfv-grid").trigger("keydown", { key });
    await flush();
  };

  it("walks the members one at a time", async () => {
    const wrapper = await listGrid();
    await wrapper.find('.stack-panel__member[data-key="b"]').trigger("click");
    expect(cursorKey(wrapper)).toBe("b");

    await arrow(wrapper, "ArrowDown");
    expect(cursorKey(wrapper)).toBe("b1");
    await arrow(wrapper, "ArrowDown");
    expect(cursorKey(wrapper)).toBe("b2");
    await arrow(wrapper, "ArrowUp");
    expect(cursorKey(wrapper)).toBe("b1");
  });

  it("enters the panel on the member nearest the way in", async () => {
    const wrapper = await listGrid();
    // Down from a card in the stack's row: every member sits in one column,
    // so "keep your column" has exactly one answer — the first of them.
    await wrapper.find('.wfv-row[data-key="a"]').trigger("click");
    await arrow(wrapper, "ArrowDown");
    expect(cursorKey(wrapper)).toBe("b");

    // …and coming back up from the grid below lands on the last.
    await arrow(wrapper, "ArrowDown");
    await arrow(wrapper, "ArrowDown");
    await arrow(wrapper, "ArrowDown");
    expect(cursorKey(wrapper)).not.toBe("b2");
    await arrow(wrapper, "ArrowUp");
    expect(cursorKey(wrapper)).toBe("b2");
  });

  it("reaches the panel from every column, not only the filled ones", async () => {
    // A TWO-member block over four columns leaves holes in two of them, and
    // the whole-row walk steps clean over the panel from those — a panel that
    // is keyboard-reachable or not depending on the window's width.
    listWorkflowCards.mockResolvedValue({
      cards: CARDS.map((entry) =>
        entry.key === "b"
          ? { ...entry, stack_size: 2, member_keys: ["b1"] }
          : entry,
      ),
      one_offs: 0,
      hidden: 0,
    });
    const wrapper = await grid();
    useWorkflowPrefsStore().setStackView("list");
    await useWorkflowsStore().openStack("b");
    await flush();

    // `d` is the fourth card, the last of the stack's row: its column holds
    // no member row at all.
    await wrapper.find('.wfv-row[data-key="d"]').trigger("click");
    await arrow(wrapper, "ArrowDown");
    expect(cursorKey(wrapper)).toBe("b");

    // And back up into it from the row below, which is the same jump the
    // other way.
    await arrow(wrapper, "ArrowDown");
    await arrow(wrapper, "ArrowDown");
    expect(["b", "b1"]).not.toContain(cursorKey(wrapper));
    await arrow(wrapper, "ArrowUp");
    expect(cursorKey(wrapper)).toBe("b1");
  });

  it("still keeps the column in Grid", async () => {
    const wrapper = await grid();
    useWorkflowPrefsStore().setStackView("grid");
    await useWorkflowsStore().openStack("b");
    await flush();
    await wrapper.find('.stack-panel__member[data-key="b"]').trigger("click");
    // Three members over four columns is one row: Down leaves the block.
    await arrow(wrapper, "ArrowDown");
    expect(["b", "b1", "b2"]).not.toContain(cursorKey(wrapper));
  });
});

describe("the menu's move verbs follow the row too", () => {
  // The menu reaches the same three writes as Alt+↑/↓, by pointer and by
  // Shift+F10. Wired straight to the store they skipped the follow-and-
  // announce step entirely: the write tears every member row down, the
  // menu's activator is a pair of coordinates with no element to restore
  // focus to, and the live region said only that the panel closed and
  // reopened.
  const STACKED = CARDS.map((entry) =>
    entry.key === "b" ? { ...entry, stack_id: "stack-b" } : entry,
  );

  async function menuOn(wrapper, key) {
    await wrapper
      .find(`.stack-panel__member[data-key="${key}"]`)
      .trigger("click");
    await wrapper
      .find(".wfv-grid")
      .trigger("keydown", { key: "F10", shiftKey: true });
    await flush();
  }

  /** The grid as it stands, until a reorder re-derives which card it draws. */
  function serveStack(coverKey, memberKeys) {
    listWorkflowCards.mockResolvedValue({
      cards: STACKED.map((entry) =>
        entry.key === "b"
          ? {
              ...entry,
              key: coverKey,
              name: coverKey,
              member_keys: memberKeys,
            }
          : entry,
      ),
      one_offs: 0,
      hidden: 0,
    });
  }

  async function openStackB() {
    serveStack("b", ["b1", "b2"]);
    // The cover is what the grid draws, and the server re-derives it from the
    // new order — so a mock that keeps serving the old cover would have the
    // panel close on every *Make it the cover*, and hide the very thing these
    // two tests are about.
    reorderStack.mockImplementation(async (stack_id, keys) => {
      serveStack(keys[0], keys.slice(1));
      return { stack_id, keys };
    });
    const wrapper = await grid();
    await useWorkflowsStore().openStack("b");
    await flush();
    return wrapper;
  }

  it("announces and re-seats the cursor after Move later", async () => {
    const wrapper = await openStackB();
    await menuOn(wrapper, "b1");
    await wrapper.findAll(".ctx-item")[2].trigger("click");
    await flush();

    expect(reorderStack).toHaveBeenCalledWith("stack-b", ["b", "b2", "b1"]);
    expect(wrapper.find('[role="status"]').text()).toMatch(
      /b1, position \d+ of 3/,
    );
    // The row the reader was on is the row they are still on.
    expect(cursorKey(wrapper)).toBe("b1");
  });

  it("announces and re-seats the cursor after Make it the cover", async () => {
    const wrapper = await openStackB();
    await menuOn(wrapper, "b2");
    await wrapper.findAll(".ctx-item")[0].trigger("click");
    await flush();

    expect(reorderStack).toHaveBeenCalledWith("stack-b", ["b2", "b", "b1"]);
    expect(wrapper.find('[role="status"]').text()).toMatch(
      /b2, position \d+ of 3/,
    );
    expect(cursorKey(wrapper)).toBe("b2");
  });
});

// The Filters panel's three surfaces on the screen itself (F7). The panel's
// own rules are in `WorkflowFilterMenu.test.js`; what is asserted here is that
// the chip strip, the button's badge and the subtitle all read the SAME list,
// so none of them can go on describing a filter another one has dropped.
describe("WorkflowsView filters", () => {
  const filterButton = (wrapper) =>
    wrapper.findAll("button").find((b) => b.text().includes("Filters"));

  it("has no chips, no badge and no withheld counts on a clean grid", async () => {
    const wrapper = await grid();
    expect(wrapper.find(".filter-strip").exists()).toBe(false);
    expect(filterButton(wrapper).find(".bar-filter-badge").exists()).toBe(
      false,
    );
    expect(wrapper.find(".wfv-sub").text()).toBe("6 workflows");
  });

  it("draws one chip and one badge per filter that is on", async () => {
    const wrapper = await grid();
    useWorkflowsStore().setFilters({ type: "upscale", ghosts: true });
    await flush();

    expect(filterButton(wrapper).find(".bar-filter-badge").text()).toBe("2");
    expect(wrapper.findAll(".filter-chip")).toHaveLength(2);
  });

  it("says which of the two counts is still being withheld", async () => {
    // On the payload, not on the store: ticking a checkbox re-reads the grid,
    // and a count written straight onto the store is overwritten by the
    // answer - which would make this pass for the wrong reason.
    listWorkflowCards.mockImplementation(() =>
      Promise.resolve({ cards: [...CARDS], one_offs: 7, hidden: 4 }),
    );
    const wrapper = await grid();
    const store = useWorkflowsStore();
    await store.fetchCards();
    await flush();
    expect(wrapper.find(".wfv-sub").text()).toBe(
      "6 workflows · 4 hidden · 7 one-offs",
    );

    // Shown is not withheld: the subtitle must stop counting what the reader
    // is now looking at, or it captions the grid with its own contents.
    store.setFilters({ showHidden: true });
    await flush();
    expect(wrapper.find(".wfv-sub").text()).toBe("6 workflows · 7 one-offs");
  });

  it("says so when the filters leave nothing, rather than showing first-run help", async () => {
    const wrapper = await grid();
    useWorkflowsStore().setFilters({ minRating: 5 });
    await flush();

    expect(wrapper.findAll(".wfv-row")).toHaveLength(0);
    expect(wrapper.find(".wfv-empty").exists()).toBe(false);
    expect(wrapper.text()).toContain("No workflow matches these filters");

    await wrapper.find(".wfv-note-clear").trigger("click");
    await flush();
    expect(wrapper.findAll(".wfv-row")).toHaveLength(6);
  });
});
