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
  // yet" unassertable.
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
// `path` is read by the cover link's `?from=`, so a route without one silently
// produces `from: undefined` — which is a link that does not come back.
const route = reactive({ name: "workflows", path: "/workflows", query: {} });
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
  useRoute: () => route,
}));

const listWorkflowCards = vi.fn();
const getWorkflowCard = vi.fn();
const patchWorkflowCard = vi.fn();
const duplicateWorkflow = vi.fn();
const deleteWorkflow = vi.fn();
const exportWorkflow = vi.fn();
const readModelSwap = vi.fn();
const cloneWorkflowWithModels = vi.fn();
const planSetClones = vi.fn();
const getLoraChain = vi.fn();
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  // `WorkflowCard` renders its covers through this, so a mock without it
  // throws in the render and every assertion in the file goes with it.
  workflowCoverUrl: (cover) => cover?.url ?? "",
  patchWorkflowCard: (...args) => patchWorkflowCard(...args),
  // The selection bar's verbs. Named here even where nothing in this file
  // calls them: `vi.mock` replaces the WHOLE module, so a name the store
  // imports and this factory omits is `undefined` at the call - which fails
  // as "not a function" inside a handler rather than as a missing mock.
  duplicateWorkflow: (...args) => duplicateWorkflow(...args),
  deleteWorkflow: (...args) => deleteWorkflow(...args),
  exportWorkflow: (...args) => exportWorkflow(...args),
  readModelSwap: (...args) => readModelSwap(...args),
  cloneWorkflowWithModels: (...args) => cloneWorkflowWithModels(...args),
  planSetClones: (...args) => planSetClones(...args),
  getLoraChain: (...args) => getLoraChain(...args),
  saveLoraChain: vi.fn(),
}));
const fetchWorkflowSets = vi.fn();
vi.mock("../../api/modelShelf", () => ({
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
  listAdapters: vi.fn(async () => []),
}));
const listImportFolders = vi.fn();
vi.mock("../../api/folders", () => ({
  listImportFolders: (...args) => listImportFolders(...args),
}));
const listUnfiledRecipes = vi.fn();
// The Unfiled recipes section is the Recipes tab in its `unfiled` mode, so it
// reads through this module the moment the view mounts.
vi.mock("../../api/recipes", () => ({
  listUnfiledRecipes: (...args) => listUnfiledRecipes(...args),
  listSavedRecipes: vi.fn(async () => []),
  listUsedLooks: vi.fn(async () => []),
  deleteSavedRecipe: vi.fn(),
  editSavedRecipe: vi.fn(),
  reorderSavedRecipes: vi.fn(),
  extractRecipeWorkflow: vi.fn(),
  exportSavedRecipe: vi.fn(),
}));
const getWorkflowPull = vi.fn();
const importWorkflow = vi.fn();
vi.mock("../../api/comfyui", () => ({
  importWorkflow: (...args) => importWorkflow(...args),
  getWorkflowPull: (...args) => getWorkflowPull(...args),
}));

import WorkflowsView from "./WorkflowsView.vue";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useFilterStore } from "../../stores/useFilterStore";
import {
  PULL_WATCH_MS,
  useWorkflowPullStore,
} from "../../stores/useWorkflowPullStore";
import { useTasksStore } from "../../stores/useTasksStore";

// `key` is the workflow's `id`; the grid row carries it as `data-key`.
const card = (key, extra = {}) => ({
  id: key,
  name: key,
  base_topology: `topology-${key}`,
  topologies: [`topology-${key}`],
  models: [],
  loras: [],
  picture_count: 1,
  rating: 3,
  rank: 1,
  covers: [],
  ...extra,
});

// Six cards; "b" holds two topologies. `last_used` runs
// OPPOSITE to `rank` on purpose, so switching the sort genuinely reverses the
// grid: a test that asserts the cursor stayed on its card proves nothing
// against an order that did not move.
const DAYS = ["01", "02", "03", "04", "05", "06"];
const CARDS = [
  card("a", { rank: 9, last_used: `2026-03-${DAYS[0]}T00:00:00Z` }),
  card("b", {
    rank: 8,
    last_used: `2026-03-${DAYS[1]}T00:00:00Z`,
    topologies: ["topology-b", "topology-b2"],
  }),
  card("c", { rank: 7, last_used: `2026-03-${DAYS[2]}T00:00:00Z` }),
  card("d", { rank: 6, last_used: `2026-03-${DAYS[3]}T00:00:00Z` }),
  card("e", { rank: 5, last_used: `2026-03-${DAYS[4]}T00:00:00Z` }),
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

/**
 * A pointer click (`detail` 1) and a right-button press: `trigger` cannot set
 * either field, jsdom makes them getters. A keyboard click has `detail` 0.
 */
async function mouseClick(wrapper) {
  wrapper.element.dispatchEvent(
    new MouseEvent("click", { bubbles: true, detail: 1 }),
  );
}

async function rightPress(wrapper) {
  wrapper.element.dispatchEvent(
    new MouseEvent("pointerdown", { bubbles: true, button: 2 }),
  );
}

const cardKeys = (wrapper) =>
  wrapper.findAll(".wfv-row").map((el) => el.attributes("data-key"));

const cursorKey = (wrapper) =>
  wrapper
    .findAll(".wfv-grid [data-key]")
    .find((el) => el.attributes("tabindex") === "0")
    ?.attributes("data-key");

beforeEach(() => {
  setActivePinia(createPinia());
  // Both rails' open flags are persisted; a test that closes the inspector
  // must not hand the next one a closed inspector.
  window.localStorage.clear();
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
      unobserve() {}
      disconnect() {}
    },
  );
  listImportFolders.mockResolvedValue({ folders: [] });
  listUnfiledRecipes.mockResolvedValue([]);
  patchWorkflowCard.mockReset();
  patchWorkflowCard.mockResolvedValue({ card: card("b") });
  // A FRESH array per call, as a real response is: the store assigns it to
  // `cards`, and handing back the same object would make a refetch a no-op
  // that no watcher on the list could see.
  listWorkflowCards.mockImplementation(() =>
    Promise.resolve({ cards: [...CARDS], one_offs: 0, hidden: 0 }),
  );
  getWorkflowCard.mockImplementation((key) =>
    Promise.resolve({ card: card(key) }),
  );
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
});

describe("Show all pictures using these workflows (#1797)", () => {
  it("lands on the grid with one Workflow entry per selected card, and a clean view", async () => {
    const wrapper = await grid();
    const filters = useFilterStore();
    filters.tagFilter = ["hat"];
    useWorkflowsStore().selectRange(["a", "c"]);
    await flush();

    await wrapper.find('[data-verb="show-pictures"]').trigger("click");

    // "All pictures" says all: the tag filter goes, the two workflows stay.
    expect(filters.tagFilter).toEqual([]);
    expect(filters.workflowFilter).toEqual([
      { id: "a", name: "a" },
      { id: "c", name: "c" },
    ]);
    expect(push).toHaveBeenCalledWith("/");
  });
});

describe("Ctrl+A", () => {
  it("selects every card from the empty background, with nothing selected", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    expect(store.selectedKeys).toEqual([]);
    // A click on the background focuses the scroller, not `<body>`, so the
    // key arrives with the scroller itself as its target.
    const scroller = wrapper.find(".wfv-scroll");
    scroller.element.focus();
    expect(document.activeElement).toBe(scroller.element);
    await scroller.trigger("keydown", { key: "a", ctrlKey: true });
    expect(store.selectedKeys).toHaveLength(6);
  });

  it("the background takes no key that acts on the unseen cursor card", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    const scroller = wrapper.find(".wfv-scroll");
    await scroller.trigger("keydown", { key: " " });
    await scroller.trigger("keydown", { key: "ArrowRight" });
    expect(store.selectedKeys).toEqual([]);
    expect(cursorKey(wrapper)).toBe("a");
  });

  it("a key on a row is handled once, not again by the scroller", async () => {
    const wrapper = await grid(1008);
    await wrapper.find(".wfv-row").trigger("keydown", { key: "ArrowRight" });
    expect(cursorKey(wrapper)).toBe("b");
  });
});

describe("Escape", () => {
  it("clears the selection", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    store.select("b");
    await wrapper.find(".wfv-grid").trigger("keydown", { key: "Escape" });
    expect(store.selectedKeys).toEqual([]);
  });

  it("a click on the background clears it too, a click on a card does not", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    store.select("b");
    await wrapper.find(".wfv-row").trigger("click", { ctrlKey: true });
    expect(store.selectedKeys).toEqual(["b", "a"]);
    // A press that slips from one card to another clicks the grid, their
    // common ancestor: that is a fumbled pick and leaves the selection alone.
    await wrapper.find(".wfv-row").trigger("pointerdown");
    await mouseClick(wrapper.find(".wfv-grid"));
    expect(store.selectedKeys).toEqual(["b", "a"]);
    // A right-press on the background ends without a click; a later keyboard
    // click (detail 0) must not find it armed.
    await rightPress(wrapper.find(".wfv-grid"));
    await mouseClick(wrapper.find(".wfv-grid"));
    expect(store.selectedKeys).toEqual(["b", "a"]);
    await wrapper.find(".wfv-grid").trigger("pointerdown");
    await wrapper.find(".wfv-grid").trigger("click");
    expect(store.selectedKeys).toEqual(["b", "a"]);
    await wrapper.find(".wfv-grid").trigger("pointerdown");
    await mouseClick(wrapper.find(".wfv-grid"));
    expect(store.selectedKeys).toEqual([]);
    store.select("b");
    await wrapper.find(".wfv-scroll").trigger("pointerdown");
    await mouseClick(wrapper.find(".wfv-scroll"));
    expect(store.selectedKeys).toEqual([]);
    // A background press that never became a click (a scrollbar drag) leaves
    // nothing armed for the next card click: that click's own press reaches
    // the scroller first and re-records where it started.
    store.select("b");
    await wrapper.find(".wfv-scroll").trigger("pointerdown");
    await wrapper.find(".wfv-row").trigger("pointerdown");
    await mouseClick(wrapper.find(".wfv-row"));
    expect(store.selectedKeys).toEqual(["a"]);
  });

  it("the sort popover owns its own Escape", async () => {
    const wrapper = await grid(1008);
    const store = useWorkflowsStore();
    store.select("b");
    wrapper.vm.sortMenuOpen = true;
    await wrapper.vm.$nextTick();
    await wrapper.find(".wfv-grid").trigger("keydown", { key: "Escape" });
    // The popover is what Escape was for; the selection stays behind it.
    expect(store.selectedKeys).toEqual(["b"]);
  });
});

describe("the cursor survives the list being rebuilt", () => {

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

  // Falling back to the first row focused it after a Delete, and the browser
  // scrolled the grid to the top to show it.
  it.each([
    [["d"], "d", "e"],
    [["f"], "f", "e"],
    [["d", "e"], "d", "f"],
    [["d", "e"], null, "f"],
  ])(
    "moves beside %j, not to the first card, when deleted (cursor on %s)",
    async (gone, clicked, next) => {
      const manual = CARDS.map((entry) => ({ ...entry, manual: true }));
      listWorkflowCards.mockImplementation(() =>
        Promise.resolve({
          cards: manual.filter((entry) => !gone.includes(entry.id)),
          one_offs: 0,
          hidden: 0,
        }),
      );
      const wrapper = await grid(1008);
      const store = useWorkflowsStore();
      store.cards = manual;
      await flush();
      // `null`: selected without the cursor ever landing, as Ctrl+A does.
      if (clicked) {
        await wrapper.find(`.wfv-row[data-key="${clicked}"]`).trigger("click");
        expect(cursorKey(wrapper)).toBe(clicked);
      }
      store.selectRange(gone);

      const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
      deleteWorkflow.mockResolvedValue({});
      await wrapper
        .findComponent({ name: "WorkflowSelectionBar" })
        .vm.$emit("delete");
      await flush();
      expect(cardKeys(wrapper)).toEqual(
        CARDS.map((entry) => entry.id).filter((id) => !gone.includes(id)),
      );
      expect(cursorKey(wrapper)).toBe(next);
      expect(document.activeElement.dataset.key).toBe(next);
      ask.mockRestore();
    },
  );

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

// ── Arriving by workflow id ───────────────────────────────────────────────
//
// `/workflows?workflow=<id>` is what the Run popup's Open in Workflows and the
// lightbox Edit tab's Open push. WorkflowTab selects the card; the grid has to
// show it, which means the cursor (and so focus, and so the scroll) on its row.
describe("arriving on ?workflow=", () => {
  it("puts the cursor and focus on that workflow's row", async () => {
    route.query = { workflow: "f" };
    const wrapper = await grid();
    await flush();

    expect(cursorKey(wrapper)).toBe("f");
    expect(document.activeElement?.dataset?.key).toBe("f");
  });

  it("moves nothing for an id the grid does not list, and says so", async () => {
    route.query = { workflow: "nothing" };
    const wrapper = await grid();
    await flush();

    expect(cursorKey(wrapper)).toBe("a");
    expect(wrapper.find(".wfv-note").text()).toContain(
      "That workflow is not in this grid",
    );
  });

  it("says a filter is hiding it, as ?topology= does", async () => {
    const wrapper = await grid();
    useWorkflowsStore().setFilters({ minRating: 5 });
    await flush();

    route.query = { workflow: "d" };
    await flush();

    const note = wrapper.find('[data-testid="wfv-link-filtered"]');
    expect(note.text()).toContain("in this grid, but a filter is hiding it");
    await note.find(".wfv-note-clear").trigger("click");
    await flush();
    expect(wrapper.find('[data-testid="wfv-link-filtered"]').exists()).toBe(false);
    expect(cursorKey(wrapper)).toBe("d");
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

  // The grid is not every card: `GET /workflows` leaves out the hidden
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

// ── The closed inspector ──────────────────────────────────────────────────
//
// The Workflows inspector has its own open flag and defaults OPEN, so a click
// never reflows the grid. Closed, a vertical edge tab names the selection and
// bounces on a new one, and the rail toggle's glyph flashes. Nothing but the
// reader (or a deep link) opens it.
describe("the closed inspector", () => {
  const edgeTab = (wrapper) =>
    wrapper.find('[data-testid="inspector-edge-tab"]');
  const tabLabel = (wrapper) => {
    const label = edgeTab(wrapper).find(".inspector-edge-tab__label");
    return label.exists() ? label.text() : "";
  };
  const railButton = (wrapper) => wrapper.find(".wfv-toolbar .tb-stats-btn");
  const nudgeClass = (el) =>
    el.classes().find((name) => /--nudge-[ab]$/.test(name)) ?? "";

  async function closedGrid() {
    const wrapper = await grid();
    useSidebarStore().workflowInspectorOpen = false;
    await flush();
    return wrapper;
  }

  it("is open on a fresh install, and the Library's stats stay shut", () => {
    const sidebar = useSidebarStore();
    expect(sidebar.workflowInspectorOpen).toBe(true);
    expect(sidebar.statsOpen).toBe(false);
  });

  it("keeps a closed inspector closed on the next visit", () => {
    window.localStorage.setItem("pixlstash:workflowInspectorOpen", "false");
    setActivePinia(createPinia());
    expect(useSidebarStore().workflowInspectorOpen).toBe(false);
  });

  it("does not open or close on a plain card click", async () => {
    const wrapper = await closedGrid();
    const sidebar = useSidebarStore();
    await wrapper.findAll(".wfv-row")[0].trigger("click");
    await flush();
    expect(sidebar.workflowInspectorOpen).toBe(false);
    expect(sidebar.statsOpen).toBe(false);

    sidebar.workflowInspectorOpen = true;
    await wrapper.findAll(".wfv-row")[2].trigger("click");
    await flush();
    expect(sidebar.workflowInspectorOpen).toBe(true);
    expect(sidebar.statsOpen).toBe(false);
  });

  it("opens, without persisting, on a ?topology= hit", async () => {
    route.query = { topology: "topology-d" };
    window.localStorage.setItem("pixlstash:workflowInspectorOpen", "false");
    setActivePinia(createPinia());
    await grid();
    const sidebar = useSidebarStore();
    expect(useWorkflowsStore().selectedKeys).toEqual(["d"]);
    expect(sidebar.workflowInspectorOpen).toBe(true);
    expect(window.localStorage.getItem("pixlstash:workflowInspectorOpen")).toBe(
      "false",
    );
  });

  it("stays closed when ?topology= names a card the grid does not list", async () => {
    route.query = { topology: "topology-nothing" };
    window.localStorage.setItem("pixlstash:workflowInspectorOpen", "false");
    setActivePinia(createPinia());
    await grid();
    expect(useSidebarStore().workflowInspectorOpen).toBe(false);
  });

  it("stays closed when ?topology= names a card a filter hides", async () => {
    const wrapper = await closedGrid();
    useWorkflowsStore().setFilters({ minRating: 5 });
    await flush();
    route.query = { topology: "topology-d" };
    await flush();
    expect(wrapper.find('[data-testid="wfv-link-filtered"]').exists()).toBe(
      true,
    );
    expect(useSidebarStore().workflowInspectorOpen).toBe(false);
  });

  it("draws the edge tab only while the inspector is closed", async () => {
    const wrapper = await grid();
    expect(edgeTab(wrapper).exists()).toBe(false);
    useSidebarStore().workflowInspectorOpen = false;
    await flush();
    expect(edgeTab(wrapper).exists()).toBe(true);
  });

  it("names one workflow, counts several, and is a bare handle for none", async () => {
    const wrapper = await closedGrid();
    expect(tabLabel(wrapper)).toBe("");
    expect(edgeTab(wrapper).attributes("aria-label")).toBe("Show inspector");

    await wrapper.findAll(".wfv-row")[0].trigger("click");
    await flush();
    expect(tabLabel(wrapper)).toBe("a");
    expect(edgeTab(wrapper).attributes("aria-label")).toBe(
      "Show inspector: a",
    );

    await wrapper.findAll(".wfv-row")[2].trigger("click", { ctrlKey: true });
    await flush();
    expect(tabLabel(wrapper)).toBe("2 workflows");
  });

  it("opens the inspector on a click, and keeps it open", async () => {
    const wrapper = await closedGrid();
    await edgeTab(wrapper).trigger("click");
    await flush();
    expect(useSidebarStore().workflowInspectorOpen).toBe(true);
    expect(window.localStorage.getItem("pixlstash:workflowInspectorOpen")).toBe(
      "true",
    );
    expect(edgeTab(wrapper).exists()).toBe(false);
  });

  // The tab unmounts as it opens the inspector; focus must follow into the
  // inspector (its active tab), never drop to `body`. The inspector lives in
  // App.vue, so a stand-in carrying its classes is mounted beside the grid.
  it("hands focus to the inspector's active tab", async () => {
    const wrapper = await closedGrid();
    const pane = document.createElement("div");
    pane.className = "wftab";
    pane.innerHTML =
      '<button class="inspector-tab">Recipes</button>' +
      '<button class="inspector-tab inspector-tab--active">Workflow</button>';
    document.body.appendChild(pane);
    try {
      edgeTab(wrapper).element.focus();
      await edgeTab(wrapper).trigger("click");
      await flush();
      expect(document.activeElement).toBe(pane.children[1]);
    } finally {
      pane.remove();
    }
  });

  it("nudges once per NEW selection, never for the same one again", async () => {
    const wrapper = await closedGrid();
    const sidebar = useSidebarStore();
    const start = sidebar.inspectorNudge;

    await wrapper.findAll(".wfv-row")[0].trigger("click");
    await flush();
    expect(sidebar.inspectorNudge).toBe(start + 1);
    const first = nudgeClass(edgeTab(wrapper));
    expect(first).not.toBe("");
    expect(nudgeClass(railButton(wrapper))).not.toBe("");

    // The same card again: a fresh array, the same selection.
    await wrapper.findAll(".wfv-row")[0].trigger("click");
    await flush();
    expect(sidebar.inspectorNudge).toBe(start + 1);
    expect(nudgeClass(edgeTab(wrapper))).toBe(first);

    // Another card: news, and both animations restart on the twin class.
    const rail = nudgeClass(railButton(wrapper));
    await wrapper.findAll(".wfv-row")[2].trigger("click");
    await flush();
    expect(sidebar.inspectorNudge).toBe(start + 2);
    expect(nudgeClass(edgeTab(wrapper))).not.toBe(first);
    expect(nudgeClass(railButton(wrapper))).not.toBe(rail);
  });

  it("does not flash the rail glyph while the inspector is open", async () => {
    const wrapper = await grid();
    await wrapper.findAll(".wfv-row")[0].trigger("click");
    await flush();
    expect(useSidebarStore().inspectorNudge).toBe(1);
    expect(nudgeClass(railButton(wrapper))).toBe("");
  });

  // The amber busy pulse owns the glyph while anything runs.
  it("leaves the glyph to the busy pulse while tasks run, and still bounces the tab", async () => {
    const wrapper = await closedGrid();
    useTasksStore().setImportRun("run-1", { status: "running" });
    await flush();
    await wrapper.findAll(".wfv-row")[0].trigger("click");
    await flush();
    expect(nudgeClass(railButton(wrapper))).toBe("");
    expect(nudgeClass(edgeTab(wrapper))).not.toBe("");
  });
});

// ── The app-wide toolbar tail ─────────────────────────────────────────────
//
// #1415: this view replaces the grid and its toolbar, so without the tail
// nothing on this screen opens Settings or the right rail - and `WorkflowTab`
// only renders while that rail is open (`AppInspector` gates it on
// `sidebarStore.workflowInspectorOpen`), so F3's whole deliverable is
// unreachable.
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

    // From the open state a fresh session starts in: the first press closes
    // the inspector, the second opens it again. Only the Workflows flag moves,
    // and the reader's choice is kept; the Library's stats sidebar is not
    // touched.
    expect(sidebar.workflowInspectorOpen).toBe(true);
    await wrapper.find(".wfv-toolbar .tb-stats-btn").trigger("click");
    expect(sidebar.workflowInspectorOpen).toBe(false);
    expect(
      window.localStorage.getItem("pixlstash:workflowInspectorOpen"),
    ).toBe("false");
    await wrapper.find(".wfv-toolbar .tb-stats-btn").trigger("click");
    expect(sidebar.workflowInspectorOpen).toBe(true);
    expect(
      window.localStorage.getItem("pixlstash:workflowInspectorOpen"),
    ).toBe("true");
    expect(sidebar.statsOpen).toBe(false);
    expect(window.localStorage.getItem("pixlstash:statsSidebarOpen")).toBe(
      null,
    );
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
  // A fixed name: open or shut is said by `aria-pressed`, not by Show/Hide.
  it("names the rail it actually opens here", async () => {
    const wrapper = await grid();
    const stats = wrapper.find(".wfv-toolbar .tb-stats-btn");
    expect(stats.attributes("aria-label")).toBe("Inspector");
    expect(stats.attributes("aria-pressed")).toBe("true");
    useSidebarStore().workflowInspectorOpen = false;
    await wrapper.vm.$nextTick();
    expect(stats.attributes("aria-label")).toBe("Inspector");
    expect(stats.attributes("aria-pressed")).toBe("false");
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

  it("marks nothing when nothing is selected", async () => {
    const wrapper = await grid();
    expect(wrapper.findAll(".wf-card--selected")).toHaveLength(0);
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

  it("filters by origin: each option keeps only its own cards", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: [
        card("a", { origin_category: "comfyui" }),
        card("b", { origin_category: "pictures" }),
        card("c", { origin_category: "own" }),
        card("d", { origin_category: "comfyui" }),
      ],
      one_offs: 0,
      hidden: 0,
    });
    const wrapper = await grid();
    const store = useWorkflowsStore();
    const keys = () =>
      wrapper.findAll(".wfv-row").map((row) => row.attributes("data-key"));
    expect(keys()).toHaveLength(4);

    for (const [origin, expected] of [
      ["comfyui", ["a", "d"]],
      ["pictures", ["b"]],
      ["own", ["c"]],
    ]) {
      store.setFilters({ origin });
      await flush();
      expect([...keys()].sort()).toEqual(expected);
      expect(wrapper.find(".filter-chip").text()).toContain(
        { comfyui: "ComfyUI", pictures: "From pictures", own: "Our own" }[
          origin
        ],
      );
    }
    // All: back to every card, no chip.
    store.setFilters({ origin: null });
    await flush();
    expect(keys()).toHaveLength(4);
    expect(wrapper.find(".filter-chip").exists()).toBe(false);
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

// ── Opening a picture is a MENU verb, never a click (#1455) ──────────────
describe("a cover tile", () => {
  const WITH_COVERS = [
    card("p", {
      rank: 9,
      picture_count: 2,
      covers: [
        { url: "/pictures/thumbnails/11.webp", picture_id: 11 },
        { url: "/pictures/thumbnails/22.webp", picture_id: 22 },
      ],
    }),
  ];

  async function coverGrid() {
    listWorkflowCards.mockResolvedValue({
      cards: WITH_COVERS,
      one_offs: 0,
      hidden: 0,
    });
    return grid();
  }

  it("is not a control: clicking one selects the card, as it always did", async () => {
    const wrapper = await coverGrid();
    const tiles = wrapper.findAll('.wfv-row[data-key="p"] .wf-card__pic');
    expect(tiles.length).toBeGreaterThan(0);
    // No button, no tabstop, nothing drawn over the picture. A cover tile was
    // briefly all three and it cost the card its ordinary click.
    for (const tile of tiles) {
      expect(tile.element.tagName).toBe("SPAN");
      expect(tile.attributes("tabindex")).toBeUndefined();
    }
    expect(wrapper.find(".wf-card__open").exists()).toBe(false);

    await tiles[1].trigger("click");
    expect(useWorkflowsStore().selectedKeys).toEqual(["p"]);
    expect(push).not.toHaveBeenCalled();
  });

  it("carries its picture's identity, which only a right-click reads", async () => {
    const wrapper = await coverGrid();
    const tiles = wrapper.findAll('.wfv-row[data-key="p"] .wf-card__pic');
    expect(tiles[1].attributes("data-picture-id")).toBe("22");
    expect(tiles[1].attributes("data-picture-index")).toBe("2");
    expect(tiles[1].attributes("data-picture-total")).toBe("2");
  });
});

// ── The card menu (#1455) ─────────────────────────────────────────────────
describe("right-clicking a card", () => {
  const menuLabels = (wrapper) =>
    wrapper
      .findAll(".wf-menu .ctx-item")
      .map((el) => el.find(".ctx-label-text").text());

  it("selects an unselected card, and leaves a selection it is part of alone", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();

    await wrapper
      .find('.wfv-row[data-key="c"]')
      .trigger("contextmenu", { clientX: 10, clientY: 20 });
    expect(store.selectedKeys).toEqual(["c"]);

    // Select three, then right-click one of them: the other two must survive,
    // or the commonest gesture in a bulk edit silently drops them.
    store.selectRange(["a", "c", "d"]);
    await wrapper
      .find('.wfv-row[data-key="d"]')
      .trigger("contextmenu", { clientX: 10, clientY: 20 });
    expect(store.selectedKeys).toEqual(["a", "c", "d"]);
  });

  it("the ContextMenu key opens the card menu, where it used to open ⓘ", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    // Nothing is open before the key: the menu stub only renders its content
    // once `modelValue` is set, so an always-open stub would make this pass
    // vacuously.
    expect(menuLabels(wrapper)).toHaveLength(0);

    await wrapper.find(".wfv-grid").trigger("keydown", { key: "ContextMenu" });
    await flush();

    expect(store.selectedKeys).toEqual(["a"]);
    expect(menuLabels(wrapper)).toContain("Run…");
  });
});

// ── Two verbs whose consequences outlive the click (#1455) ────────────────
describe("the verbs the bar fires", () => {
  const bar = (wrapper) =>
    wrapper.findComponent({ name: "WorkflowSelectionBar" });

  it("Run opens the popup on the selected workflow, by id", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    store.select("b");

    await bar(wrapper).vm.$emit("run");
    await flush();
    expect(useRunDialogStore().source).toMatchObject({
      kind: "card",
      workflowId: "b",
    });
    expect(useRunDialogStore().source.workflowKey).toBeUndefined();
  });

  it("Delete asks first, and deletes nothing when the answer is no", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    store.cards = [card("a", { imported: true, manual: true })];
    store.selectRange(["a"]);

    const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
    await bar(wrapper).vm.$emit("delete");
    await flush();
    expect(ask).toHaveBeenCalled();
    expect(deleteWorkflow).not.toHaveBeenCalled();

    // And with a yes it goes through, so the refusal above is the ANSWER
    // being no rather than the verb being unwired.
    ask.mockReturnValue(true);
    deleteWorkflow.mockResolvedValue({ deleted: "a" });
    await bar(wrapper).vm.$emit("delete");
    await flush();
    expect(deleteWorkflow).toHaveBeenCalledWith("a");
    // It says the pictures stay, where they go, and what becomes of recipes.
    const asked = ask.mock.calls.at(-1)[0];
    expect(asked).toContain("pictures stay");
    expect(asked).toContain("automatic workflow");
    expect(asked).toContain("Unfiled recipes");
    expect(wrapper.find('[role="status"]').text()).toBe("Workflow deleted");
    ask.mockRestore();
  });

  it("the Delete key asks the same question, and only of a manual workflow", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    store.cards = [card("a"), card("b", { imported: true, manual: true })];
    await flush();
    const ask = vi.spyOn(window, "confirm").mockReturnValue(true);
    deleteWorkflow.mockResolvedValue({ deleted: "b" });

    // Nothing selected: the key acts on the selection, so it does nothing,
    // not even select the cursor card.
    await wrapper.find(".wfv-grid").trigger("keydown", { key: "Delete" });
    await flush();
    expect(ask).not.toHaveBeenCalled();
    expect(store.selectedKeys).toEqual([]);

    // An automatic card: the menu row is disabled, so the key does nothing.
    store.select("a");
    await wrapper.find(".wfv-grid").trigger("keydown", { key: "Delete" });
    await flush();
    expect(ask).not.toHaveBeenCalled();

    // A held key repeats; one press, one dialog.
    store.select("b");
    await wrapper
      .find(".wfv-grid")
      .trigger("keydown", { key: "Delete", repeat: true });
    await flush();
    expect(ask).not.toHaveBeenCalled();

    await wrapper.find(".wfv-grid").trigger("keydown", { key: "Delete" });
    await flush();
    expect(ask).toHaveBeenCalledTimes(1);
    expect(deleteWorkflow).toHaveBeenCalledWith("b");
    ask.mockRestore();
  });

  it("the Delete key works from the background too, after Ctrl+A", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    store.cards = [card("b", { imported: true, manual: true })];
    await flush();
    const ask = vi.spyOn(window, "confirm").mockReturnValue(false);
    const scroller = wrapper.find(".wfv-scroll");

    await scroller.trigger("keydown", { key: "a", ctrlKey: true });
    await scroller.trigger("keydown", { key: "Delete" });
    await flush();
    expect(ask).toHaveBeenCalledTimes(1);
    ask.mockRestore();
  });

  it("the menu's Delete… row teaches the key", async () => {
    const wrapper = await grid();
    await wrapper.find(".wfv-grid").trigger("keydown", { key: "ContextMenu" });
    await flush();
    const row = wrapper
      .findAll(".wf-menu .ctx-item")
      .find((el) => el.find(".ctx-label-text").text() === "Delete…");
    expect(row.find(".ctx-shortcut").text()).toBe("Del");
  });

  it("Duplicate selects the copy and puts the cursor on it", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    store.select("a");
    duplicateWorkflow.mockResolvedValue({ name: "a (copy)", workflow_id: "e" });

    await bar(wrapper).vm.$emit("duplicate");
    await flush();
    await flush();
    expect(duplicateWorkflow).toHaveBeenCalledWith("a");
    expect(store.selectedKeys).toEqual(["e"]);
    expect(cursorKey(wrapper)).toBe("e");
  });

  it("Rename with an empty field clears the stored name rather than setting one", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    store.selectRange(["a"]);
    patchWorkflowCard.mockResolvedValue({});

    await bar(wrapper).vm.$emit("rename");
    await flush();
    // The field opens EMPTY on a card whose name is always resolved, so
    // pressing Rename without typing must not freeze the generated name into
    // a stored one — it has to clear the stored name instead.
    await wrapper
      .find(".app-dialog__footer button:last-child")
      .trigger("click");
    await flush();
    expect(patchWorkflowCard).toHaveBeenCalledWith("a", { name: null });

    await bar(wrapper).vm.$emit("rename");
    await flush();
    await wrapper.find(".app-dialog input").setValue("  Portrait pass  ");
    await wrapper
      .find(".app-dialog__footer button:last-child")
      .trigger("click");
    await flush();
    expect(patchWorkflowCard).toHaveBeenLastCalledWith("a", {
      name: "Portrait pass",
    });
  });
});

describe("Clone onto a workflow set", () => {
  const bar = (wrapper) =>
    wrapper.findComponent({ name: "WorkflowSelectionBar" });

  const loader = (was, now, extra = {}) => ({
    node_id: "1",
    kind: "unet",
    was_class: "UNETLoader",
    now_class: "UNETLoader",
    was: [was],
    now: [now],
    pack: null,
    installed: null,
    ...extra,
  });

  beforeEach(() => {
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [
        {
          id: 7,
          name: "Flux dev GGUF",
          members: [
            // A checkpoint whose file left the shelf, listed FIRST: never
            // sent, so the plan neither refuses the set nor swaps onto a
            // missing file, and never what the clone is named after.
            { id: 13, slot: "checkpoint", on_shelf: false, name: "flux1-low" },
            {
              id: 11,
              slot: "checkpoint",
              on_shelf: true,
              name: "Flux Dev Q8",
              // Cased unlike the plan's file: the lookup must fold case.
              filename: "Flux1-Dev-Q8_0.gguf",
            },
            { id: 12, slot: "lora", on_shelf: true, name: "mara" },
          ],
          covers: [],
          picture_count: 0,
        },
        {
          id: 8,
          name: "Chroma",
          members: [{ id: 21, slot: "checkpoint", on_shelf: true, name: "chroma" }],
          covers: [],
          picture_count: 0,
        },
      ],
    });
    getLoraChain.mockResolvedValue({
      editable: true,
      loaders: [{ node_id: "5", name: "mara_v3", strength: 0.85 }],
      lanes: [],
    });
    planSetClones.mockResolvedValue({
      base_filename: "flux1-dev-fp8.safetensors",
      base_model: "FLUX.1 dev",
      plans: [
        {
          key: "hand:7",
          fit: "same_base_model",
          reason: null,
          base_model: "FLUX.1 dev",
          keeps_loras: true,
          swaps: { "flux1-dev-fp8.safetensors": "flux1-dev-Q8_0.gguf" },
          loaders: [
            loader("flux1-dev-fp8.safetensors", "flux1-dev-Q8_0.gguf", {
              now_class: "UnetLoaderGGUF",
              pack: "ComfyUI-GGUF",
              installed: false,
            }),
          ],
        },
        {
          key: "hand:8",
          fit: "other",
          reason: null,
          base_model: "Chroma",
          keeps_loras: false,
          swaps: { "flux1-dev-fp8.safetensors": "chroma.safetensors" },
          loaders: [loader("flux1-dev-fp8.safetensors", "chroma.safetensors")],
        },
      ],
    });
    cloneWorkflowWithModels.mockResolvedValue({
      name: "a · Chroma.json",
      workflow_id: "c",
      swapped: [],
      unswapped: [],
      loaders: [],
      verified: true,
    });
  });

  async function openOn(wrapper) {
    useWorkflowsStore().selectRange(["a"]);
    await bar(wrapper).vm.$emit("clone-onto-set");
    await flush();
    await flush();
  }

  const cloneButton = (wrapper) =>
    wrapper
      .findAll(".app-dialog__footer button")
      .find((b) => b.text().includes("Clone"));

  it("plans every set and clones with the LoRAs removed on another base model", async () => {
    const wrapper = await grid();
    await openOn(wrapper);
    expect(planSetClones).toHaveBeenCalledWith("a", [
      { key: "hand:7", checkpoint_ids: [11], model_ids: [] },
      { key: "hand:8", checkpoint_ids: [21], model_ids: [] },
    ]);
    // "Fits this graph" hides the set of another base model that does not
    // fill the graph's loaders one for one.
    expect(wrapper.find('[data-testid="cos-set-hand:8"]').exists()).toBe(false);
    await wrapper.findAll(".cos-tab")[1].trigger("click");
    await wrapper.find('[data-testid="cos-set-hand:8"]').trigger("click");
    expect(wrapper.find('[data-testid="cos-removed"]').text()).toContain(
      "mara_v3",
    );
    await cloneButton(wrapper).trigger("click");
    await flush();
    expect(cloneWorkflowWithModels).toHaveBeenCalledWith("a", {
      name: "a · Chroma",
      swaps: { "flux1-dev-fp8.safetensors": "chroma.safetensors" },
      loras: { entries: [], lanes: null },
    });
    expect(useWorkflowsStore().selectedKeys).toEqual(["c"]);
  });

  it("lists a set of another base model that maps cleanly under Fits", async () => {
    const reply = await planSetClones();
    planSetClones.mockResolvedValue({
      ...reply,
      plans: reply.plans.map((plan) =>
        plan.key === "hand:8"
          ? {
              ...plan,
              maps_cleanly: true,
              // The encoder file stays; only the type it loads as changes.
              loaders: [
                ...plan.loaders,
                loader("t5.safetensors", "t5.safetensors", {
                  node_id: "2",
                  kind: "clip",
                  was_class: "CLIPLoader",
                  now_class: "CLIPLoader",
                  was_type: "flux",
                  now_type: "chroma",
                }),
              ],
            }
          : plan,
      ),
    });
    const wrapper = await grid();
    await openOn(wrapper);
    const chroma = wrapper.find('[data-testid="cos-set-hand:8"]');
    expect(chroma.exists()).toBe(true);
    expect(chroma.attributes("disabled")).toBeUndefined();
    await chroma.trigger("click");
    const clip = wrapper.findAll('[data-testid="cos-loaders"] li')[1];
    expect(clip.classes()).not.toContain("cos-row--same");
    expect(clip.find(".cos-was").text()).toContain("flux");
    expect(clip.find(".cos-now").text()).toContain("chroma");
  });

  it("lets the owner swap the two VAEs of a set before cloning (#1831)", async () => {
    const sets = await fetchWorkflowSets();
    fetchWorkflowSets.mockResolvedValue({
      ...sets,
      hand_made: [
        {
          ...sets.hand_made[0],
          // No name of its own, and nobody named its files: every name the
          // shelf has for them is the file's, extension and all.
          name: "",
          members: [
            {
              id: 11,
              slot: "checkpoint",
              on_shelf: true,
              name: "h3_finetune.safetensors",
              filename: "h3_finetune.safetensors",
            },
            {
              id: 31,
              slot: "vae",
              on_shelf: true,
              name: "H3_Audio_VAE_fp16.safetensors",
              filename: "H3_Audio_VAE_fp16.safetensors",
            },
            {
              id: 32,
              slot: "vae",
              on_shelf: true,
              name: "H3_Video_VAE_fp16.safetensors",
              filename: "H3_Video_VAE_fp16.safetensors",
            },
          ],
        },
      ],
    });
    const vae = (node_id, was, now) =>
      loader(was, now, {
        node_id,
        kind: "vae",
        was_class: "VAELoader",
        now_class: "VAELoader",
      });
    const reply = await planSetClones();
    const plan = {
      ...reply.plans[0],
      takes: { "h3/video.safetensors": 32, "h3/audio.safetensors": 31 },
      choices: { vae: [31, 32], clip: [] },
      loaders: [
        ...reply.plans[0].loaders,
        vae("8", "h3/video.safetensors", "h3/video.safetensors"),
        vae("9", "h3/audio.safetensors", "h3/audio.safetensors"),
      ],
    };
    const crossed = {
      ...plan,
      swaps: {
        ...plan.swaps,
        "h3/video.safetensors": "h3/audio.safetensors",
        "h3/audio.safetensors": "h3/video.safetensors",
      },
      takes: { "h3/video.safetensors": 31, "h3/audio.safetensors": 32 },
      loaders: [
        plan.loaders[0],
        vae("8", "h3/video.safetensors", "h3/audio.safetensors"),
        vae("9", "h3/audio.safetensors", "h3/video.safetensors"),
      ],
    };
    planSetClones.mockResolvedValue({ ...reply, plans: [plan] });
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.find('[data-testid="cos-set-hand:7"]').trigger("click");
    const picks = () =>
      wrapper.findAll('[data-testid="cos-loaders"] select').map((s) => ({
        value: s.element.value,
        label: s.attributes("aria-label"),
        options: s.findAll("option").map((o) => o.text()),
      }));
    // Each loader starts on the file it already loads; the checkpoint row,
    // which the set holds one file for, offers nothing. A file is called by
    // its own name, whole but for the extension: the tidied name would drop
    // the precision, which may be all that tells two files apart.
    const files = ["H3_Audio_VAE_fp16", "H3_Video_VAE_fp16"];
    expect(picks()).toEqual([
      { value: "32", label: "VAE in place of video", options: files },
      { value: "31", label: "VAE in place of audio", options: files },
    ]);
    // Nothing changes yet, and each row still says which file its loader
    // has, unstruck, over the select.
    const vaeRows = wrapper.findAll('[data-testid="cos-loaders"] li').slice(1);
    expect(
      vaeRows.map((li) =>
        li.findAll(".cos-line-label").map((l) => l.text().split(" ")[0]),
      ),
    ).toEqual([
      ["Original", "To"],
      ["Original", "To"],
    ]);
    expect(vaeRows.map((li) => li.find(".cos-was").text())).toEqual([
      "VAELoader video",
      "VAELoader audio",
    ]);
    // "Unchanged" sits under the kind, and the class is not said twice.
    expect(wrapper.findAll(".cos-same--under")).toHaveLength(2);
    expect(vaeRows[0].find(".cos-now .cos-class").exists()).toBe(false);
    expect(wrapper.findAll(".cos-was--kept")).toHaveLength(2);
    // No `.safetensors` on the set's card or in the name the clone is offered.
    expect(wrapper.find('[data-testid="cos-set-hand:7"]').text()).not.toContain(
      "safetensors",
    );
    expect(wrapper.find(".cos-name input").element.value).toBe("a · h3 finetune");

    planSetClones.mockClear();
    planSetClones.mockResolvedValue({ ...reply, plans: [crossed] });
    await wrapper
      .find('[data-testid="cos-pick-h3/video.safetensors"] select')
      .setValue("31");
    await flush();
    // The loader that had the audio VAE takes this one's: a swap, one re-plan
    // of this set alone, with the whole pairing.
    expect(planSetClones).toHaveBeenCalledTimes(1);
    expect(planSetClones).toHaveBeenCalledWith("a", [
      {
        key: "hand:7",
        checkpoint_ids: [11],
        model_ids: [31, 32],
        picks: { "h3/video.safetensors": 31, "h3/audio.safetensors": 32 },
      },
    ]);
    expect(picks().map((p) => p.value)).toEqual(["31", "32"]);
    expect(wrapper.findAll(".cos-was--kept")).toHaveLength(0);
    expect(wrapper.findAll(".cos-same--under")).toHaveLength(0);
    // The row nobody touched changed too: said, in a status region.
    expect(wrapper.find('[data-testid="cos-swapped"]').text()).toContain(
      "Swapped with the loader of audio",
    );
    await cloneButton(wrapper).trigger("click");
    await flush();
    expect(cloneWorkflowWithModels).toHaveBeenCalledWith(
      "a",
      expect.objectContaining({ swaps: crossed.swaps }),
    );
  });

  /** hand:7 holding two VAEs and one text encoder, open on its plan. */
  async function openOnTwoVaes() {
    const sets = await fetchWorkflowSets();
    fetchWorkflowSets.mockResolvedValue({
      ...sets,
      hand_made: [
        {
          ...sets.hand_made[0],
          members: [
            ...sets.hand_made[0].members,
            { id: 31, slot: "vae", on_shelf: true, name: "Audio VAE" },
            { id: 32, slot: "vae", on_shelf: true, name: "Video VAE" },
          ],
        },
      ],
    });
    const reply = await planSetClones();
    const plan = {
      ...reply.plans[0],
      // The one text encoder the set holds is no choice: its row is text.
      takes: {
        "video.safetensors": 32,
        "audio.safetensors": 31,
        "t5.safetensors": 41,
      },
      choices: { vae: [31, 32], clip: [41] },
      loaders: ["video", "audio", "t5"].map((file, index) =>
        loader(`${file}.safetensors`, `${file}.safetensors`, {
          node_id: String(8 + index),
          kind: file === "t5" ? "clip" : "vae",
          was_class: file === "t5" ? "CLIPLoader" : "VAELoader",
          now_class: file === "t5" ? "CLIPLoader" : "VAELoader",
        }),
      ),
    };
    planSetClones.mockResolvedValue({ ...reply, plans: [plan] });
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.find('[data-testid="cos-set-hand:7"]').trigger("click");
    const values = () =>
      wrapper
        .findAll('[data-testid="cos-loaders"] select')
        .map((s) => s.element.value);
    const pickFile = (file, id) =>
      wrapper
        .find(`[data-testid="cos-pick-${file}.safetensors"] select`)
        .setValue(id);
    const pickVideo = (id) => pickFile("video", id);
    return { wrapper, reply, plan, values, pickVideo, pickFile };
  }

  it("puts a pick the server refused back and keeps the plan it had", async () => {
    const { wrapper, reply, plan, values, pickVideo } = await openOnTwoVaes();
    planSetClones.mockRejectedValue(new Error("offline"));
    await pickVideo("31");
    await flush();
    expect(wrapper.find('[role="alert"]').text()).toContain("offline");
    expect(values()).toEqual(["32", "31"]);

    // A pick ComfyUI refuses: the reason is said beside the diff, where the
    // owner is looking, and Clone waits for another pick.
    planSetClones.mockResolvedValue({
      ...reply,
      plans: [
        { ...plan, fit: "wont_load", reason: "ComfyUI does not list audio" },
      ],
    });
    await pickVideo("31");
    await flush();
    expect(wrapper.find('[data-testid="cos-reason"]').text()).toContain(
      "ComfyUI does not list audio",
    );
    expect(cloneButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("applies a pick made while another is still being re-planned", async () => {
    const { wrapper, reply, plan, values, pickVideo, pickFile } =
      await openOnTwoVaes();
    const crossed = {
      ...plan,
      takes: { ...plan.takes, "video.safetensors": 31, "audio.safetensors": 32 },
    };
    let answer;
    const held = () =>
      planSetClones.mockReturnValueOnce(
        new Promise((resolve) => {
          answer = resolve;
        }),
      );
    planSetClones.mockClear();
    held();
    await pickVideo("31");
    // Before the server has answered, the owner changes their mind.
    expect(
      wrapper.find('[data-testid="cos-loaders"]').attributes("aria-busy"),
    ).toBe("true");
    await pickVideo("32");
    expect(planSetClones).toHaveBeenCalledTimes(1);
    planSetClones.mockResolvedValue({ ...reply, plans: [plan] });
    answer({ ...reply, plans: [crossed] });
    await flush();
    await flush();
    // Their last pick is asked for, against the plan the first one left, and
    // is what the selects end on. It took nothing from another loader that
    // the first pick had not put there, and it was not the same file again.
    expect(planSetClones).toHaveBeenCalledTimes(2);
    expect(planSetClones.mock.calls[1][1][0].picks).toEqual(plan.takes);
    expect(values()).toEqual(["32", "31"]);
    expect(
      wrapper.find('[data-testid="cos-loaders"]').attributes("aria-busy"),
    ).toBe("false");

    // A pick that waited for what the answer brought anyway asks nothing: the
    // swap already gave the audio loader the video VAE.
    held();
    await pickVideo("31");
    await pickFile("audio", "32");
    answer({ ...reply, plans: [crossed] });
    await flush();
    await flush();
    expect(planSetClones).toHaveBeenCalledTimes(3);
    expect(values()).toEqual(["31", "32"]);
  });

  it("names the clone after the model the plan loads and marks a set with no cover", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    // Replaced, not mutated: the card objects are the shared fixture.
    const at = store.cards.findIndex((c) => c.id === "a");
    store.cards[at] = {
      ...store.cards[at],
      name: "Flux Dev: Text to Image (2)",
      type_label: "Text to Image",
    };
    await openOn(wrapper);
    const set = wrapper.find('[data-testid="cos-set-hand:7"]');
    // No picture: an icon, not a blank cover, and no cut-short badge.
    expect(set.find(".cos-cover-none").exists()).toBe(true);
    expect(set.find(".cos-badge").exists()).toBe(false);
    await set.trigger("click");
    expect(wrapper.find(".cos-name input").element.value).toBe(
      "Flux Dev Q8: Text to Image",
    );
  });

  it("keeps the chain on the same base model and warns of a missing pack", async () => {
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.find('[data-testid="cos-set-hand:7"]').trigger("click");
    expect(wrapper.find('[data-testid="cos-set-hand:7"]').text()).toContain(
      "Keeps its 1 LoRA",
    );
    expect(wrapper.find('[data-testid="cos-pack-note"]').text()).toContain(
      "ComfyUI-GGUF is not installed",
    );
    await cloneButton(wrapper).trigger("click");
    await flush();
    expect(cloneWorkflowWithModels).toHaveBeenLastCalledWith("a", {
      name: "a · Flux dev GGUF",
      swaps: { "flux1-dev-fp8.safetensors": "flux1-dev-Q8_0.gguf" },
      loras: null,
    });
  });

  it("names the set a dropped LoRA was added for when the set changes", async () => {
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.findAll(".cos-tab")[1].trigger("click");
    await wrapper.find('[data-testid="cos-set-hand:8"]').trigger("click");
    await wrapper.findComponent({ name: "EditLorasDialog" }).vm.$emit("done", {
      rows: [{ id: "new:1", name: "mara_chroma", isNew: true, sha256: "sha-m" }],
      body: { entries: [{ node_id: null, sha256: "sha-m", strength: 1 }] },
      changes: 1,
    });
    await wrapper.find('[data-testid="cos-set-hand:7"]').trigger("click");
    expect(wrapper.text()).toContain(
      "Dropped the LoRA edits made for Chroma, including mara_chroma.",
    );
  });

  it("says a dropped edit that only deleted a LoRA was dropped too", async () => {
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.find('[data-testid="cos-set-hand:7"]').trigger("click");
    await wrapper.findComponent({ name: "EditLorasDialog" }).vm.$emit("done", {
      rows: [{ id: "n:5", name: "mara_v3", isNew: false, deleted: true }],
      body: { entries: [] },
      changes: 1,
    });
    await wrapper.findAll(".cos-tab")[1].trigger("click");
    await wrapper.find('[data-testid="cos-set-hand:8"]').trigger("click");
    expect(wrapper.text()).toContain(
      "Dropped the LoRA edits made for Flux dev GGUF.",
    );
  });

  it("counts the LoRAs an edit leaves, not the ones the plan kept", async () => {
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.find('[data-testid="cos-set-hand:7"]').trigger("click");
    expect(wrapper.find('[data-testid="cos-summary"]').text()).toBe(
      "1 loader rewritten · LoRAs kept · 1 node pack",
    );
    // Same base model, but the owner deleted the kept LoRA and added one.
    await wrapper.findComponent({ name: "EditLorasDialog" }).vm.$emit("done", {
      rows: [
        { id: "n:5", name: "mara_v3", isNew: false, deleted: true },
        { id: "new:1", name: "film-look", isNew: true, sha256: "sha-f" },
      ],
      body: { entries: [{ node_id: null, sha256: "sha-f", strength: 1 }] },
    });
    expect(wrapper.find('[data-testid="cos-summary"]').text()).toBe(
      "1 loader rewritten · 1 LoRA removed, 1 LoRA added · 1 node pack",
    );
  });

  it("names a missing pack and unchecked names in one notice", async () => {
    cloneWorkflowWithModels.mockResolvedValue({
      name: "a · Flux dev GGUF.json",
      workflow_id: "c",
      swapped: [],
      unswapped: [],
      loaders: [{ node_id: "1", pack: "ComfyUI-GGUF", installed: false }],
      verified: false,
    });
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.find('[data-testid="cos-set-hand:7"]').trigger("click");
    await cloneButton(wrapper).trigger("click");
    await flush();
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("warning");
    expect(notice.text).toContain("It needs ComfyUI-GGUF");
    expect(notice.text).toContain("run it once to check");
  });

  it("names a pack nobody could check, beside the unchecked names", async () => {
    cloneWorkflowWithModels.mockResolvedValue({
      name: "a · Flux dev GGUF.json",
      workflow_id: "c",
      swapped: [],
      unswapped: [],
      loaders: [{ node_id: "1", pack: "ComfyUI-GGUF", installed: null }],
      verified: false,
    });
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.find('[data-testid="cos-set-hand:7"]').trigger("click");
    await cloneButton(wrapper).trigger("click");
    await flush();
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("warning");
    expect(notice.text).toContain(
      "It needs ComfyUI-GGUF, and PixlStash could not ask ComfyUI whether it has it.",
    );
  });

  it("hands per-file picking to Clone with new models", async () => {
    readModelSwap.mockResolvedValue({
      slots: [],
      checkpoints: [],
      vaes: [],
      text_encoders: [],
      proposals: {},
      flags: [],
    });
    const wrapper = await grid();
    await openOn(wrapper);
    await wrapper.findAll(".cos-tab")[1].trigger("click");
    await wrapper.find('[data-testid="cos-pick-files"]').trigger("click");
    await flush();
    expect(readModelSwap).toHaveBeenCalledWith("a");
    expect(useWorkflowsStore().cloneOntoSetKey).toBe("");
  });
});

// ── What the UX review asked for (#1455) ──────────────────────────────────
describe("the keyboard can reach everything the pointer can", () => {
  const bar = (wrapper) =>
    wrapper.findComponent({ name: "WorkflowSelectionBar" });

  it("opens the cover picture from the menu, not only from the tile", async () => {
    listWorkflowCards.mockResolvedValue({
      cards: [
        card("p", {
          picture_count: 2,
          covers: [
            { url: "/a", picture_id: 7 },
            { url: "/b", picture_id: 8 },
          ],
        }),
      ],
      one_offs: 0,
      hidden: 0,
    });
    const wrapper = await grid();
    useWorkflowsStore().selectRange(["p"]);
    await flush();

    // The tiles are at `tabindex="-1"` — the grid owns Tab — so this row is
    // the ONLY way a keyboard reaches the new action (WCAG 2.1.1).
    await bar(wrapper).vm.$emit("open-cover");
    expect(push).toHaveBeenCalledWith({
      name: "all-pictures",
      query: { overlay: "7", from: "/workflows" },
    });
  });

  it("opens a picture the Recipes tab asks for, and clears the ask", async () => {
    await grid();
    const store = useWorkflowsStore();
    store.requestOpenPicture(55);
    await flush();
    expect(push).toHaveBeenCalledWith({
      name: "all-pictures",
      query: { overlay: "55", from: "/workflows" },
    });
    expect(store.pictureToOpen).toBe(null);
  });

  it("hands focus back to the cursor row when the menu closes", async () => {
    const wrapper = await grid();
    await wrapper.find(".wfv-grid").trigger("keydown", { key: "ContextMenu" });
    await flush();
    // A context menu's activator is a pair of coordinates, so Vuetify has
    // nothing to restore focus to and it lands on `document.body` — outside
    // the grid, with the cursor's row marked and the next arrow key dead.
    document.body.focus();

    bar(wrapper).vm.$emit("menu-closed");
    await flush();
    expect(document.activeElement.getAttribute("data-key")).toBe("a");
  });
});

describe("a bulk verb that half worked says so", () => {
  const bar = (wrapper) =>
    wrapper.findComponent({ name: "WorkflowSelectionBar" });

  it("names how many went rather than claiming none did", async () => {
    // This assertion moved here with the verb: it used to live on the rail's
    // own Hide button, which the pill now owns (#1455). Losing it would lose
    // the whole reason the loop runs to the end instead of throwing on the
    // first refusal.
    const wrapper = await grid();
    const store = useWorkflowsStore();
    store.selectRange(["a", "c"]);
    patchWorkflowCard
      .mockResolvedValueOnce({})
      .mockRejectedValueOnce(new Error("nope"));

    await bar(wrapper).vm.$emit("hide", false);
    await flush();

    expect(patchWorkflowCard).toHaveBeenCalledTimes(2);
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    expect(useNoticeStore().notices.at(-1).text).toContain("1 of 2");
  });

  it("refuses a second press while the first is still out", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    store.selectRange(["a", "c"]);
    let release;
    patchWorkflowCard.mockImplementation(
      () => new Promise((resolve) => (release = resolve)),
    );

    bar(wrapper).vm.$emit("hide", false);
    await flush();
    // A second press re-issues writes against state the first has already
    // changed; the 404s that come back are counted as refusals, so the
    // reader is told it FAILED right after it succeeded.
    bar(wrapper).vm.$emit("hide", false);
    await flush();
    expect(patchWorkflowCard).toHaveBeenCalledTimes(1);

    release({});
    await flush();
  });
});

// ── Right-click names the picture under the pointer ───────────────────────
describe("the card menu opens the picture you pointed at", () => {
  const WITH_THREE = [
    card("p", {
      rank: 9,
      picture_count: 3,
      covers: [
        { url: "/1", picture_id: 11 },
        { url: "/2", picture_id: 22 },
        { url: "/3", picture_id: 33 },
      ],
    }),
  ];

  async function threeGrid() {
    listWorkflowCards.mockResolvedValue({
      cards: WITH_THREE,
      one_offs: 0,
      hidden: 0,
    });
    return grid();
  }

  const openRow = (wrapper) =>
    wrapper
      .findAll('[data-testid="wf-verbs"] .ctx-item')
      .find((el) => el.attributes("data-verb") === "open-cover");

  it("names and opens the tile that was right-clicked", async () => {
    const wrapper = await threeGrid();
    const tiles = wrapper.findAll('.wfv-row[data-key="p"] .wf-card__pic');
    expect(tiles).toHaveLength(3);

    // Right-click the THIRD thumbnail. The press lands on the <img> inside
    // the button, which is why the lookup walks up with `closest`.
    await tiles[2].find("img").trigger("contextmenu", {
      clientX: 5,
      clientY: 6,
    });
    await flush();

    expect(openRow(wrapper).find(".ctx-label-text").text()).toBe(
      "Open picture 3 of 3",
    );
    await openRow(wrapper).trigger("click");
    expect(push).toHaveBeenCalledWith({
      name: "all-pictures",
      query: { overlay: "33", from: "/workflows" },
    });
  });

  it("falls back to the cover when the right-click missed a tile", async () => {
    const wrapper = await threeGrid();
    // The name row is part of the card and not a picture, so "this card's
    // picture" is the cover.
    await wrapper
      .find('.wfv-row[data-key="p"] .wf-card__name')
      .trigger("contextmenu", { clientX: 5, clientY: 6 });
    await flush();

    expect(openRow(wrapper).find(".ctx-label-text").text()).toBe(
      "Open cover picture",
    );
    await openRow(wrapper).trigger("click");
    expect(push).toHaveBeenCalledWith({
      name: "all-pictures",
      query: { overlay: "11", from: "/workflows" },
    });
  });
});

describe("pulling from ComfyUI (#1440)", () => {
  const SUMMARY = {
    listed: 82,
    pulled: 21,
    matched: 61,
    nodes_checked: true,
    missing_nodes: 39,
    missing_node_classes: ["LoRACharacterPromptBuilder"],
    missing_models: 12,
    missing_model_files: ["flux-2-klein-9b-fp8.safetensors"],
    models_unread: 5,
  };
  const checking = (wrapper) =>
    wrapper.find('[data-testid="wfv-checking"]');

  // Fake timers for the whole block: the poll is a timer, and one scheduled
  // under real timers cannot be advanced. `flush` here lets them settle.
  const flush = () => vi.advanceTimersByTimeAsync(0);

  beforeEach(() => {
    vi.useFakeTimers();
    listWorkflowCards.mockResolvedValue({ cards: CARDS, one_offs: 0, hidden: 0 });
    getWorkflowPull.mockResolvedValue({ status: "idle" });
  });

  afterEach(() => {
    vi.useRealTimers();
    useWorkflowPullStore().reset();
    useFilterStore().comfyuiConfigured = false;
  });

  it("has no Pull button, connected or not, in the toolbar or the empty state", async () => {
    useFilterStore().comfyuiConfigured = true;
    const wrapper = mountView();
    await flush();
    expect(wrapper.text()).not.toContain("Pull from ComfyUI");
    expect(wrapper.find('[data-testid="wfv-pull"]').exists()).toBe(false);

    listWorkflowCards.mockResolvedValue({ cards: [], one_offs: 0, hidden: 0 });
    await useWorkflowsStore().fetchCards();
    await flush();
    expect(wrapper.find(".wfv-empty").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("Pull from ComfyUI");
    expect(wrapper.find('[data-testid="wfv-empty-pull"]').exists()).toBe(false);
  });

  it("says Checking ComfyUI… only while a pull runs, and blocks nothing", async () => {
    getWorkflowPull.mockResolvedValue({ status: "running", task_id: "t1" });
    useFilterStore().comfyuiConfigured = true;
    const wrapper = mountView();
    await flush();
    expect(checking(wrapper).attributes("role")).toBe("status");
    expect(checking(wrapper).text()).toBe("Checking ComfyUI…");
    expect(wrapper.find(".wfv-grid").attributes("aria-busy")).toBe("false");

    getWorkflowPull.mockResolvedValue({ status: "idle" });
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    await flush();
    expect(checking(wrapper).text()).toBe("");
  });

  it("asks nothing while ComfyUI is not connected, and stops when the view goes", async () => {
    const wrapper = mountView();
    await flush();
    expect(getWorkflowPull).not.toHaveBeenCalled();

    useFilterStore().comfyuiConfigured = true;
    await flush();
    expect(getWorkflowPull).toHaveBeenCalledTimes(1);

    wrapper.unmount();
      await vi.advanceTimersByTimeAsync(PULL_WATCH_MS * 4);
    expect(getWorkflowPull).toHaveBeenCalledTimes(1);
  });

  it("reports a pull that changed something, per machine, and re-reads the grid", async () => {
    // A finished pull from before the view opened is the baseline, not news.
    getWorkflowPull.mockResolvedValue({
      status: "completed",
      task_id: "old",
      summary: { listed: 3, pulled: 3 },
    });
    useFilterStore().comfyuiConfigured = true;
    const wrapper = mountView();
    await flush();
    expect(wrapper.find('[data-testid="wfpull"]').exists()).toBe(false);
    const reads = listWorkflowCards.mock.calls.length;

    getWorkflowPull.mockResolvedValue({
      status: "completed",
      task_id: "t2",
      comfyui_url: "http://127.0.0.1:8188",
      summary: SUMMARY,
    });
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    await flush();

    expect(listWorkflowCards.mock.calls.length).toBe(reads + 1);
    const band = wrapper.find('[data-testid="wfpull"]');
    expect(band.find(".wfpull-headline").text()).toBe(
      "Found 82 workflows on 127.0.0.1:8188: 21 new, 61 already here.",
    );
    const kinds = band.findAll(".wfpull-line").map((li) => li.attributes("data-kind"));
    // Wrong if "won't run" and "not checked" ever share a kind: they must not
    // look alike (the issue's rule).
    expect(kinds).toContain("error");
    expect(kinds).toContain("warning");
    expect(kinds).toContain("unchecked");
    expect(band.text()).toContain("won't run on 127.0.0.1:8188");
    expect(band.text()).not.toMatch(/broken/i);
    expect(band.text()).not.toMatch(/one-off/i);

    await band.find('[aria-label="Dismiss the ComfyUI pull result"]').trigger("click");
    await flush();
    expect(wrapper.find('[data-testid="wfpull"]').exists()).toBe(false);
    // Focus goes to the grid's scroller, not to <body>.
    expect(document.activeElement).toBe(wrapper.find(".wfv-scroll").element);
  });

  it("hands focus to the empty state's first action when the band goes", async () => {
    listWorkflowCards.mockResolvedValue({ cards: [], one_offs: 0, hidden: 0 });
    getWorkflowPull.mockResolvedValue({ status: "idle" });
    useFilterStore().comfyuiConfigured = true;
    const wrapper = mountView();
    await flush();
    getWorkflowPull.mockResolvedValue({
      status: "completed",
      task_id: "t1",
      comfyui_url: "http://127.0.0.1:8188",
      summary: SUMMARY,
    });
    await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
    await flush();
    expect(wrapper.find(".wfv-empty").exists()).toBe(true);
    await wrapper
      .find('[aria-label="Dismiss the ComfyUI pull result"]')
      .trigger("click");
    await flush();
    expect(document.activeElement).toBe(
      wrapper.find(".wfv-empty__actions button").element,
    );
  });

  it("shows nothing for a pull that changed nothing, or one that failed", async () => {
    getWorkflowPull.mockResolvedValue({ status: "idle" });
    useFilterStore().comfyuiConfigured = true;
    const wrapper = mountView();
    await flush();
    const reads = listWorkflowCards.mock.calls.length;

    for (const state of [
      { status: "completed", task_id: "t1", summary: { listed: 5, matched: 5, unchanged: 5 } },
      { status: "failed", task_id: "t2", error: "ComfyUI runs with --multi-user" },
    ]) {
      getWorkflowPull.mockResolvedValue(state);
      await vi.advanceTimersByTimeAsync(PULL_WATCH_MS);
      await flush();
    }
    expect(wrapper.find('[data-testid="wfpull"]').exists()).toBe(false);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(listWorkflowCards.mock.calls.length).toBe(reads);
  });
});

describe("Add… and Unfiled recipes", () => {
  it("imports each file as a new workflow and opens the last one", async () => {
    const wrapper = await grid();
    const store = useWorkflowsStore();
    importWorkflow.mockReset();
    importWorkflow.mockResolvedValue({
      status: "imported",
      name: "flow",
      matched: false,
      workflow_id: "d",
    });
    const input = wrapper.find(".wfv-file-input");
    const file = new File(['{"nodes": []}'], "flow.json", {
      type: "application/json",
    });
    Object.defineProperty(input.element, "files", {
      value: [file],
      configurable: true,
    });
    await input.trigger("change");
    await flush();
    await flush();

    // Only the name and the file: there is no overwrite or keep-both any more.
    expect(importWorkflow).toHaveBeenCalledWith({
      name: "flow",
      workflow: { nodes: [] },
    });
    expect(store.selectedKeys).toEqual(["d"]);
    expect(cursorKey(wrapper)).toBe("d");
    expect(wrapper.find('[role="status"]').text()).toBe("Added flow");
  });

  it("announces how many workflows a multi-file add brought in", async () => {
    const wrapper = await grid();
    importWorkflow.mockReset();
    importWorkflow.mockResolvedValue({ name: "flow", workflow_id: "d" });
    const input = wrapper.find(".wfv-file-input");
    const files = ["a.json", "b.json"].map(
      (name) => new File(['{"nodes": []}'], name, { type: "application/json" }),
    );
    Object.defineProperty(input.element, "files", {
      value: files,
      configurable: true,
    });
    await input.trigger("change");
    await flush();
    await flush();
    expect(wrapper.find('[role="status"]').text()).toBe("Added 2 workflows");
  });

  it("lists the unfiled recipes below the grid, and nothing while there are none", async () => {
    const empty = await grid();
    expect(empty.find(".wfv-unfiled .wfrt-head").exists()).toBe(false);

    listUnfiledRecipes.mockResolvedValue([
      { id: 9, name: "Orphan", workflow_id: null, loras: [], overrides: {} },
    ]);
    const wrapper = await grid();
    expect(wrapper.find(".wfv-unfiled .wfrt-title").text()).toBe(
      "Unfiled recipes",
    );
    expect(wrapper.find(".wfv-unfiled .wfrt-name").text()).toBe("Orphan");
  });
});

describe("a run started from the Workflows view", () => {
  it("shows up in the task manager, and leaves it with the view", async () => {
    // The runner follows progress over a socket; jsdom's would dial out.
    vi.stubGlobal(
      "WebSocket",
      class {
        close() {}
      },
    );
    try {
      const wrapper = await grid();
      const runDialog = useRunDialogStore();
      const tasks = useTasksStore();
      expect(runDialog.hasRunner).toBe(true);

      runDialog.started([{ prompt_id: "p-1" }]);
      await flush();
      const runs = Object.values(tasks.comfyuiRuns);
      expect(runs).toHaveLength(1);
      expect(runs[0].status).toBe("queued");

      mounted.splice(mounted.indexOf(wrapper), 1);
      wrapper.unmount();
      expect(runDialog.hasRunner).toBe(false);
      expect(Object.keys(tasks.comfyuiRuns)).toHaveLength(0);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
