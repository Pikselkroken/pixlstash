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
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  const VMenu = {
    name: "VMenu",
    template: `<div><slot name="activator" :props="{}" /><slot /></div>`,
  };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

const push = vi.fn();
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));

const listWorkflowCards = vi.fn();
const getWorkflowCard = vi.fn();
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
}));
const listImportFolders = vi.fn();
vi.mock("../../api/folders", () => ({
  listImportFolders: (...args) => listImportFolders(...args),
}));
vi.mock("../../api/comfyui", () => ({ importWorkflow: vi.fn() }));

import WorkflowsView from "./WorkflowsView.vue";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";

const card = (key, extra = {}) => ({
  key,
  name: key,
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
  listWorkflowCards.mockResolvedValue({
    cards: CARDS,
    one_offs: 0,
    hidden: 0,
  });
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
  // jsdom applies no SFC `<style>`, so what these pin is the hook each CSS rule
  // is keyed on — `.stack-panel--selected` on the band and `.wfv-row--banded`
  // on the card — and the `aria-selected` the rules read. The colours
  // themselves are not assertable here and are not asserted.
  it("the band takes the hook while the whole stack is in, and gives it up when part comes out", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    await store.openStack("b");
    await flush();

    const panel = () => wrapper.find('[data-testid="stack-panel"]');
    const stackRow = () =>
      wrapper
        .findAll(".wfv-row")
        .find((row) => row.attributes("data-key") === "b");
    const selectedRows = () =>
      wrapper
        .findAll(".stack-panel__member")
        .filter((row) => row.attributes("aria-selected") === "true")
        .map((row) => row.attributes("data-key"));

    expect(panel().classes()).not.toContain("stack-panel--selected");

    // Clicking the stack card selects all three, so the band takes the mark
    // and the per-row rule is switched off by its `:not(.stack-panel--selected)`
    // scope. The card above drops to the rail so the pair is one closed box.
    await wrapper.findAll(".wfv-row")[1].trigger("click");
    expect(store.selectedKeys).toEqual(["b", "b1", "b2"]);
    expect(panel().classes()).toContain("stack-panel--selected");
    expect(stackRow().classes()).toContain("wfv-row--banded");
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
    expect(stackRow().classes()).not.toContain("wfv-row--banded");
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
