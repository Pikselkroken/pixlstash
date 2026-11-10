// The Sort panel: Sort by, and the list's Group by section (#2168).
//
// The Group section is the only grouping control left on the toolbar's Sort
// button. It writes the list's own grouping, and is not offered on the Sets
// place, whose grid groups itself. Folder is not an option at all: it is the
// sidebar's folder slot.

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import ShelfSortPanel from "./ShelfSortPanel.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = { global: { stubs: { "v-icon": true } } };

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
});

const groupSection = (wrapper) => wrapper.find(".shelf-group-section");
const groupRows = (wrapper) =>
  wrapper.find('[aria-label="Group by"]').findAll('[role="radio"]');

describe("the Group by section", () => {
  it("is offered on the list places and hidden on the Sets place", async () => {
    const wrapper = mount(ShelfSortPanel, globalOpts);
    const store = useModelShelfStore();

    store.setPlace("all");
    await wrapper.vm.$nextTick();
    expect(groupSection(wrapper).exists()).toBe(true);

    store.setPlace("sets");
    await wrapper.vm.$nextTick();
    expect(groupSection(wrapper).exists()).toBe(false);
  });

  it("offers Type, Base model, Feature and None, and not Folder", async () => {
    const wrapper = mount(ShelfSortPanel, globalOpts);
    useModelShelfStore().setPlace("all");
    await wrapper.vm.$nextTick();
    expect(groupRows(wrapper).map((row) => row.text())).toEqual([
      "Type",
      "Base model",
      "Feature",
      "None",
    ]);
  });

  it("writes the list's grouping, and remembers it as the list's", async () => {
    const wrapper = mount(ShelfSortPanel, globalOpts);
    const store = useModelShelfStore();
    store.setPlace("all");
    await wrapper.vm.$nextTick();

    await groupRows(wrapper)
      .find((row) => row.text() === "Base model")
      .trigger("click");
    expect(store.view.groupBy).toBe("base_model");
    expect(store.view.listGroupBy).toBe("base_model");
  });

  it("has no Folder layout sub-choice any more", async () => {
    const wrapper = mount(ShelfSortPanel, globalOpts);
    useModelShelfStore().setPlace("all");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[aria-label="Folders laid out"]').exists()).toBe(
      false,
    );
  });
});

describe("favourites first (#1578)", () => {
  it("is on by default, and unticking it writes the view and says why", async () => {
    const wrapper = mount(ShelfSortPanel, globalOpts);
    const store = useModelShelfStore();
    expect(store.view.favouritesFirst).toBe(true);
    const box = wrapper.find(".shelf-sort-favourites input[type='checkbox']");
    expect(box.element.checked).toBe(true);
    await box.setValue(false);
    expect(store.view.favouritesFirst).toBe(false);
    expect(wrapper.text()).toContain("Inside each group, favourites lead");
  });

  it("is remembered in the view across a reload", async () => {
    const first = mount(ShelfSortPanel, globalOpts);
    useModelShelfStore().setView({ favouritesFirst: false });
    first.unmount();
    setActivePinia(createPinia());
    expect(useModelShelfStore().view.favouritesFirst).toBe(false);
  });
});
