// The `Show` menu is the other screens' Filters cascade: a root list of filter
// kinds that never grows with the library, each opening one submenu beside it.
// Inside the submenus its claims are the old panel's: the null base model is a
// labelled option, a partial kind selection says so on the block's checkbox
// rather than lying in either direction, and turning a block off disables the
// boxes that narrow it (which is also what takes them out of the tab order)
// without clearing them.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listAdapters = vi.fn();
const listCheckpoints = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => listAdapters(...args),
  listCheckpoints: (...args) => listCheckpoints(...args),
}));

import ShelfShowPanel from "./ShelfShowPanel.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useFavouritesStore } from "../../stores/useFavouritesStore";

const mountOpts = {
  props: { open: true },
  global: { stubs: { "v-icon": true, Tooltip: true } },
  attachTo: document.body,
};

/** A root row, by its filter key. */
const rootRow = (wrapper, kind) => wrapper.find(`[data-kind="${kind}"]`);

/** What a root row says about its filter: "" at rest. */
const rowValue = (wrapper, kind) => rootRow(wrapper, kind).find(".fm-n").text();

/** Open a root row's submenu and return it. */
async function openSub(wrapper, kind) {
  if (rootRow(wrapper, kind).attributes("aria-expanded") !== "true") {
    await rootRow(wrapper, kind).trigger("click");
    await flushPromises();
  }
  const sub = wrapper.find(`[data-testid="shelf-show-sub-${kind}"]`);
  if (!sub.exists()) throw new Error(`no submenu for ${kind}`);
  return sub;
}

/** A submenu's checkbox labels, in the order drawn. */
const boxLabels = (sub) => sub.findAll("label.tbm-check").map((l) => l.text());

function adapter(overrides = {}) {
  return {
    id: 1,
    file_kind: "adapter",
    kind: "lora",
    base_model: "sdxl",
    file_size: 1024,
    locations: [],
    attachments: [],
    ...overrides,
  };
}

const mounted = [];

function mountShow() {
  const wrapper = mount(ShelfShowPanel, mountOpts);
  mounted.push(wrapper);
  return wrapper;
}

async function mountPanel(rows) {
  listAdapters.mockResolvedValue(rows);
  listCheckpoints.mockResolvedValue([]);
  const store = useModelShelfStore();
  await store.fetchRows();
  const wrapper = mountShow();
  await flushPromises();
  return { wrapper, store };
}

// The capability boxes narrow Engines the way the kinds narrow Adapters.
// Mocked per request kind, because the store asks the SAME route twice and a
// single `mockResolvedValue` would answer both with these rows.
async function mountWithEngines(engineRows) {
  listAdapters.mockImplementation((opts) =>
    Promise.resolve(opts?.fileKind === "engine" ? engineRows : []),
  );
  listCheckpoints.mockResolvedValue([]);
  const store = useModelShelfStore();
  await store.fetchRows();
  const wrapper = mountShow();
  await flushPromises();
  return { wrapper, store };
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  listAdapters.mockReset();
  listCheckpoints.mockReset();
});

// Attached to the document so a submenu can take focus; left mounted, the
// next test's queries would find this one's menu.
afterEach(() => {
  mounted.splice(0).forEach((wrapper) => wrapper.unmount());
});

describe("ShelfShowPanel", () => {
  it("keeps every option out of the root, so the root cannot outgrow the window", async () => {
    // The bug this menu was rebuilt for: one panel holding every box had to
    // scroll, around a base-model list that scrolled inside it. The root is
    // now one row per filter kind however many models the shelf holds.
    const { wrapper } = await mountPanel(
      Array.from({ length: 40 }, (_, i) =>
        adapter({ id: i + 1, kind: `Kind${i}`, base_model: `Base ${i}` }),
      ),
    );
    expect(wrapper.findAll(".fm-row").map((r) => r.text())).toEqual([
      "Type",
      "Adapter kind",
      "Base model",
      "Copies",
      "Hidden models",
    ]);
    // The one box on the root is Favourites only, above the kinds (#1578).
    const boxes = wrapper.findAll('input[type="checkbox"]');
    expect(boxes).toHaveLength(1);
    expect(boxes[0].element.closest("label").textContent).toContain(
      "Favourites only",
    );
  });

  it("Favourites only counts the shelf's favourites, filters on tick, and is cleared by reset", async () => {
    useFavouritesStore().keys = { model: ["a".repeat(64)], workflow: [], set: [] };
    const { wrapper } = await mountPanel([
      adapter({ id: 1, sha256: "a".repeat(64) }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ]);
    const store = useModelShelfStore();
    const box = wrapper.find('[data-testid="shelf-show-favourites"]');
    expect(box.text()).toContain("Favourites only");
    expect(box.text()).toContain("1");
    await box.find("input").setValue(true);
    expect(store.filters.favouritesOnly).toBe(true);
    expect(store.activeCount).toBe(1);
    store.resetFilters();
    expect(store.filters.favouritesOnly).toBe(false);
  });

  it("opens one submenu at a time, and none when the menu reopens", async () => {
    const { wrapper } = await mountPanel([adapter()]);
    await openSub(wrapper, "type");
    expect(rootRow(wrapper, "type").attributes("aria-expanded")).toBe("true");
    await openSub(wrapper, "copies");
    expect(wrapper.find('[data-testid="shelf-show-sub-type"]').exists()).toBe(
      false,
    );
    expect(rootRow(wrapper, "type").attributes("aria-expanded")).toBe("false");
    expect(rootRow(wrapper, "copies").attributes("aria-controls")).toBe(
      wrapper.find(".shelf-show-sub-slot").attributes("id"),
    );
    // The v-menu keeps its content mounted while closed.
    await wrapper.setProps({ open: false });
    expect(wrapper.find(".shelf-show-sub-slot").exists()).toBe(false);
  });

  it("offers no row for a list with nothing in it", async () => {
    const { wrapper } = await mountPanel([adapter()]);
    expect(rootRow(wrapper, "adapterKind").exists()).toBe(true);
    // No engine on this shelf, so no engine feature to pick.
    expect(rootRow(wrapper, "capability").exists()).toBe(false);
  });

  it("closes a submenu whose row a rescan took away", async () => {
    // A scan is minutes long and can finish under an open menu. A submenu left
    // standing with no row beside it has no way back to the root.
    const { wrapper, store } = await mountPanel([adapter()]);
    await openSub(wrapper, "baseModel");
    listAdapters.mockResolvedValue([]);
    await store.fetchRows();
    await flushPromises();
    expect(rootRow(wrapper, "baseModel").exists()).toBe(false);
    expect(wrapper.find(".shelf-show-sub-slot").exists()).toBe(false);
  });

  it("says on each root row whether its filter is on", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "lokr" }),
    ]);
    for (const kind of ["type", "adapterKind", "baseModel", "copies"]) {
      expect(rowValue(wrapper, kind)).toBe("");
    }
    store.setFilters({
      checkpoints: false,
      adapterKinds: ["lokr"],
      baseModels: ["sdxl"],
      duplicatesOnly: true,
    });
    await flushPromises();
    // Type rests with every block on, so it counts what is left showing.
    expect(rowValue(wrapper, "type")).toBe("4 of 5");
    expect(rowValue(wrapper, "adapterKind")).toBe("1");
    expect(rowValue(wrapper, "baseModel")).toBe("1");
    expect(rowValue(wrapper, "copies")).toBe("1");
    // A pick under a block that is off narrows nothing, so its row is silent:
    // a `1` there would claim a filter the shelf is not applying.
    store.setFilters({ adapters: false });
    await flushPromises();
    expect(rowValue(wrapper, "adapterKind")).toBe("");
  });

  it("wires each Type box to the block the store reads, and refetches", async () => {
    // A mis-spelled key here writes a field nothing reads: the box ticks and
    // the shelf does not narrow.
    const { wrapper, store } = await mountPanel([adapter()]);
    const sub = await openSub(wrapper, "type");
    expect(boxLabels(sub)).toEqual([
      "Adapters",
      "Checkpoints",
      "Support files",
      "Unclassified",
      "Engines",
    ]);
    const keys = [
      "adapters",
      "checkpoints",
      "support",
      "unclassified",
      "engines",
    ];
    // The three whose word needs explaining say what it means on hover.
    expect(sub.findAll("tooltip-stub").length).toBe(3);
    const boxes = sub.findAll("input");
    for (const [i, key] of keys.entries()) {
      expect(store.filters[key]).toBe(true);
      listAdapters.mockClear();
      listCheckpoints.mockClear();
      await boxes[i].setValue(false);
      await flushPromises();
      expect(store.filters[key]).toBe(false);
      expect(
        listAdapters.mock.calls.length + listCheckpoints.mock.calls.length,
      ).toBeGreaterThan(0);
      await boxes[i].setValue(true);
      await flushPromises();
    }
  });

  it("narrows by name from the field above the kinds, and counts it", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1, display_name: "Cyanwood Style" }),
      adapter({ id: 2, display_name: "Other" }),
    ]);
    await wrapper
      .find('input[aria-label="Filter models by name"]')
      .setValue("cyan");
    expect(store.filters.name).toBe("cyan");
    // Distinct, because the mock answers every block's fetch with these rows.
    expect([...new Set(store.visibleRows.map((r) => r.id))]).toEqual([1]);
    expect(store.activeCount).toBe(1);
  });

  it("wires Only duplicates to the filter the store actually reads", async () => {
    const { wrapper, store } = await mountPanel([adapter()]);
    const sub = await openSub(wrapper, "copies");
    const rows = sub.findAll(".optrow");
    expect(rows.map((r) => r.text())).toEqual(["All", "Only duplicates"]);
    expect(store.filters.duplicatesOnly).toBe(false);
    await rows[1].trigger("click");
    expect(store.filters.duplicatesOnly).toBe(true);
    await rows[0].trigger("click");
    expect(store.filters.duplicatesOnly).toBe(false);
  });

  it("wires Hidden models to the filter the store reads, and counts them", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1 }),
      adapter({ id: 2, hidden: true }),
    ]);
    expect(rowValue(wrapper, "hidden")).toBe("");
    const sub = await openSub(wrapper, "hidden");
    const rows = sub.findAll(".optrow");
    // The count says there is something to show before Show is picked.
    expect(
      rows.map((r) => [
        r.find(".optrow__label").text(),
        r.find(".fm-n").text(),
      ]),
    ).toEqual([
      ["Hide", "1"],
      ["Show", "1"],
    ]);
    // As sets: this file's one double answers every block with the same rows.
    const shown = () => new Set(store.visibleRows.map((r) => r.id));
    expect(shown()).toEqual(new Set([1]));

    await rows[1].trigger("click");
    expect(store.filters.showHidden).toBe(true);
    expect(shown()).toEqual(new Set([1, 2]));
    expect(rowValue(wrapper, "hidden")).toBe("1");

    await rows[0].trigger("click");
    expect(store.filters.showHidden).toBe(false);
    expect(shown()).toEqual(new Set([1]));
  });

  it("counts zero where this library hides nothing", async () => {
    const { wrapper } = await mountPanel([adapter({ id: 1 })]);
    const sub = await openSub(wrapper, "hidden");
    expect(sub.findAll(".fm-n").map((n) => n.text())).toEqual(["0", "0"]);
  });

  it("offers the null base model as 'Not set' rather than omitting it", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1 }),
      adapter({ id: 2, base_model: null }),
    ]);
    const sub = await openSub(wrapper, "baseModel");
    expect(boxLabels(sub)).toEqual(["sdxl", "Not set"]);
    // ...and never leaks the wire sentinel into the UI, while still sending it.
    expect(wrapper.text()).not.toContain("UNASSIGNED");
    await sub.findAll('input[type="checkbox"]')[1].setValue(true);
    expect(store.filters.baseModels).toEqual(["UNASSIGNED"]);
    await sub.find("button.tbm-ghost").trigger("click");
    expect(store.filters.baseModels).toEqual([]);
  });

  it("says 'some' on the block when some of its kinds are ticked", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "lokr" }),
    ]);
    const sub = await openSub(wrapper, "type");
    const [adapters, checkpoints] = sub.findAll('input[type="checkbox"]');
    expect(adapters.element.indeterminate).toBe(false);
    store.setFilters({ adapterKinds: ["lokr"] });
    await flushPromises();
    expect(adapters.element.indeterminate).toBe(true);
    expect(checkpoints.element.indeterminate).toBe(false);
  });

  it("spells an algorithm the way the rest of the shelf spells it, once", async () => {
    // The row's Kind cell and the `feature` group header both read `LoRA`, so
    // a raw `lora` here was a third spelling of one thing on one screen. And
    // the facet folds: two spellings of one algorithm are ONE box, or the
    // menu offers two boxes with the same label that each tick half the rows.
    const { wrapper } = await mountPanel([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "LoRA" }),
      adapter({ id: 3, kind: "lokr" }),
    ]);
    expect(boxLabels(await openSub(wrapper, "adapterKind"))).toEqual([
      "LoKr",
      "LoRA",
    ]);
  });

  it("offers the unidentified adapters as a box, spelled like everything else", async () => {
    // `unknown` is `detect_adapter_kind`'s refusal, and the group axis declines
    // to head a group with it - but "which of these could we not identify" is
    // still a real selection, so it stays a facet. Spelled `Unknown`, because a
    // lowercase box beside `LoRA` is the third-spelling problem again.
    const { wrapper } = await mountPanel([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "unknown" }),
    ]);
    expect(boxLabels(await openSub(wrapper, "adapterKind"))).toEqual([
      "LoRA",
      "Unknown",
    ]);
  });

  it("ticks a kind into the store, and Clear empties it", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "lokr" }),
    ]);
    const sub = await openSub(wrapper, "adapterKind");
    const clear = sub.find("button.tbm-ghost");
    expect(clear.attributes("disabled")).toBeDefined();
    await sub.findAll("input")[1].setValue(true);
    expect(store.filters.adapterKinds).toEqual(["lora"]);
    await sub.findAll("input")[0].setValue(true);
    await sub.findAll("input")[1].setValue(false);
    expect(store.filters.adapterKinds).toEqual(["lokr"]);
    await clear.trigger("click");
    expect(store.filters.adapterKinds).toEqual([]);
  });

  it("disables the kinds when Adapters is off, keeping the selection", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1, kind: "lora" }),
    ]);
    store.setFilters({ adapterKinds: ["lora"] });
    const sub = await openSub(wrapper, "adapterKind");
    expect(sub.find(".tbm-footer").exists()).toBe(false);
    // With the refetch the component really performs: without it the rows
    // survived in the test and the disabled state it asserts was unreachable
    // in the app, where the narrowed fetch unmounted the whole list.
    await store.setFilters({ adapters: false }, { refetch: true });
    await flushPromises();
    const kindBox = sub.find("input");
    expect(kindBox.exists()).toBe(true);
    expect(kindBox.attributes("disabled")).toBeDefined();
    expect(kindBox.element.checked).toBe(true);
    // A greyed list says why, to a screen reader as well.
    const note = sub.find(".tbm-footer");
    expect(note.text()).toBe("Adapters are turned off in Type.");
    expect(sub.attributes("aria-describedby")).toBe(note.attributes("id"));
  });

  it("puts focus on a greyed list itself, which has no box to take it", async () => {
    // Left on the root row, a keyboard never reaches the line saying why the
    // boxes are greyed.
    const { wrapper, store } = await mountPanel([adapter()]);
    await store.setFilters({ adapters: false }, { refetch: true });
    await flushPromises();
    const sub = await openSub(wrapper, "adapterKind");
    expect(document.activeElement).toBe(sub.element);
    await sub.trigger("keydown", { key: "ArrowLeft" });
    expect(document.activeElement).toBe(
      rootRow(wrapper, "adapterKind").element,
    );
  });

  it("names each capability in the screen's words, not the database's", async () => {
    const { wrapper } = await mountWithEngines([
      adapter({
        id: 900,
        file_kind: "engine",
        kind: "scorer",
        capabilities: ["scorer", "search"],
      }),
    ]);
    const sub = await openSub(wrapper, "capability");
    expect(boxLabels(sub)).toEqual(["Quality score", "Search"]);
    // The stored vocabulary stays stored.
    expect(wrapper.text()).not.toContain("scorer");
  });

  it("disables the capabilities when Engines is off, keeping the selection", async () => {
    const { wrapper, store } = await mountWithEngines([
      adapter({
        id: 900,
        file_kind: "engine",
        kind: "captioner",
        capabilities: ["captioner", "detector"],
      }),
    ]);
    store.setFilters({ capabilities: ["detector"] });
    await store.setFilters({ engines: false }, { refetch: true });
    await flushPromises();

    const sub = await openSub(wrapper, "capability");
    const boxes = sub.findAll("input");
    expect(boxes.length).toBe(2);
    expect(boxes[1].attributes("disabled")).toBeDefined();
    expect(boxes[1].element.checked).toBe(true);
    expect(sub.find(".tbm-footer").text()).toBe(
      "Engines are turned off in Type.",
    );
  });

  it("puts focus in the submenu it opens, and Left brings it back to the row", async () => {
    const { wrapper } = await mountPanel([adapter()]);
    // Right opens, as it does in the other two menus - and in a system menu
    // whose submenu has flipped to the left for want of room.
    await rootRow(wrapper, "type").trigger("keydown", { key: "ArrowRight" });
    await flushPromises();
    // Found, not opened with `openSub`: that would click the row open and the
    // key would be asserting nothing.
    const sub = wrapper.find('[data-testid="shelf-show-sub-type"]');
    expect(document.activeElement).toBe(sub.find("input").element);
    await sub.find("input").trigger("keydown", { key: "ArrowLeft" });
    expect(document.activeElement).toBe(rootRow(wrapper, "type").element);
    expect(wrapper.find(".shelf-show-sub-slot").exists()).toBe(false);
  });

  it("steps the root rows with the arrow keys, wrapping at the ends", async () => {
    const { wrapper } = await mountPanel([adapter()]);
    await rootRow(wrapper, "type").trigger("keydown", { key: "ArrowUp" });
    expect(document.activeElement).toBe(rootRow(wrapper, "hidden").element);
    await rootRow(wrapper, "hidden").trigger("keydown", {
      key: "ArrowDown",
    });
    expect(document.activeElement).toBe(rootRow(wrapper, "type").element);
  });
});
