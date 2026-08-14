// The `Show` panel's three claims: the null base model is a labelled option,
// a partial kind selection says so on the parent checkbox rather than lying in
// either direction, and unchecking the parent disables its children (which is
// also what takes them out of the tab order) without clearing them.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
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

const globalOpts = { global: { stubs: { "v-icon": true } } };

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

async function mountPanel(rows) {
  listAdapters.mockResolvedValue(rows);
  listCheckpoints.mockResolvedValue([]);
  const store = useModelShelfStore();
  await store.fetchRows();
  const wrapper = mount(ShelfShowPanel, globalOpts);
  await wrapper.vm.$nextTick();
  return { wrapper, store };
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  listAdapters.mockReset();
  listCheckpoints.mockReset();
});

describe("ShelfShowPanel", () => {
  it("offers the null base model as 'Not set' rather than omitting it", async () => {
    const { wrapper } = await mountPanel([
      adapter({ id: 1 }),
      adapter({ id: 2, base_model: null }),
    ]);
    expect(wrapper.text()).toContain("Not set");
    // ...and never leaks the wire sentinel into the UI.
    expect(wrapper.text()).not.toContain("UNASSIGNED");
  });

  it("says 'some' on the parent when some kinds are ticked", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1, kind: "lora" }),
      adapter({ id: 2, kind: "lokr" }),
    ]);
    const parent = wrapper.find('input[type="checkbox"]');
    expect(parent.element.indeterminate).toBe(false);
    store.setFilters({ adapterKinds: ["lokr"] });
    await wrapper.vm.$nextTick();
    expect(parent.element.indeterminate).toBe(true);
  });

  it("disables the kinds when the parent is off, keeping the selection", async () => {
    const { wrapper, store } = await mountPanel([
      adapter({ id: 1, kind: "lora" }),
    ]);
    store.setFilters({ adapterKinds: ["lora"] });
    // With the refetch the component really performs: without it the rows
    // survived in the test and the disabled state it asserts was unreachable
    // in the app, where the narrowed fetch unmounted the whole nested block.
    await store.setFilters({ adapters: false }, { refetch: true });
    await wrapper.vm.$nextTick();
    const kindBox = wrapper.find(".shelf-show-nested input");
    expect(kindBox.exists()).toBe(true);
    expect(kindBox.attributes("disabled")).toBeDefined();
    expect(kindBox.element.checked).toBe(true);
  });

  // The capability boxes hang under Engines the way the kinds hang under
  // Adapters. Mocked per request kind, because the store asks the SAME route
  // twice and a single `mockResolvedValue` would answer both with these rows.
  async function mountWithEngines(engineRows) {
    listAdapters.mockImplementation((opts) =>
      Promise.resolve(opts?.fileKind === "engine" ? engineRows : []),
    );
    listCheckpoints.mockResolvedValue([]);
    const store = useModelShelfStore();
    await store.fetchRows();
    const wrapper = mount(ShelfShowPanel, globalOpts);
    await wrapper.vm.$nextTick();
    return { wrapper, store };
  }

  it("names each capability in the screen's words, not the database's", async () => {
    const { wrapper } = await mountWithEngines([
      adapter({
        id: 900,
        file_kind: "engine",
        kind: "scorer",
        capabilities: ["scorer", "search"],
      }),
    ]);
    expect(wrapper.text()).toContain("Quality score");
    expect(wrapper.text()).toContain("Search");
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
    await wrapper.vm.$nextTick();

    const boxes = wrapper.findAll(".shelf-show-nested input");
    expect(boxes.length).toBe(2);
    expect(boxes[1].attributes("disabled")).toBeDefined();
    expect(boxes[1].element.checked).toBe(true);
  });
});
