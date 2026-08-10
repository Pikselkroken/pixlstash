// The Sort panel's two controls and the folder layout that hangs off one of
// them.
//
// The assertion worth having is that the layout choice is a SUB-choice: it was
// once shipped as `Sort: Drive | Folder`, which reordered nothing and grouped
// everything, and having a grouping control sit in the sort menu is why the
// absence of real sorting went unnoticed. It must appear under Folder and
// nowhere else.

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

function layoutSection(wrapper) {
  return wrapper.find('[aria-label="Folders laid out"]');
}

describe("the folder layout sub-choice", () => {
  it("is absent on every axis but Folder", () => {
    const wrapper = mount(ShelfSortPanel, globalOpts);
    const store = useModelShelfStore();

    expect(store.view.groupBy).toBe("none");
    expect(layoutSection(wrapper).exists()).toBe(false);

    store.setView({ groupBy: "base_model" });
    expect(layoutSection(wrapper).exists()).toBe(false);
  });

  it("appears under Folder and sets the layout when pressed", async () => {
    const wrapper = mount(ShelfSortPanel, globalOpts);
    const store = useModelShelfStore();
    store.setView({ groupBy: "folder" });
    await wrapper.vm.$nextTick();

    const section = layoutSection(wrapper);
    expect(section.exists()).toBe(true);
    const buttons = section.findAll("button");
    expect(buttons).toHaveLength(2);

    // Seeded to drive: the first question a shelf of 438 GB is asked is which
    // disk is filling up.
    expect(store.view.folderLayout).toBe("drive");
    expect(buttons[0].attributes("aria-pressed")).toBe("true");

    await buttons[1].trigger("click");
    expect(store.view.folderLayout).toBe("alpha");
    expect(buttons[1].attributes("aria-pressed")).toBe("true");
  });

  it("remembers the layout across a trip through another axis", async () => {
    // The choice is carried at all times and only READ under Folder, so
    // flipping to Base model and back must not silently reset it.
    const wrapper = mount(ShelfSortPanel, globalOpts);
    const store = useModelShelfStore();
    store.setView({ groupBy: "folder", folderLayout: "alpha" });
    store.setView({ groupBy: "base_model" });
    store.setView({ groupBy: "folder" });
    await wrapper.vm.$nextTick();

    expect(store.view.folderLayout).toBe("alpha");
    expect(
      layoutSection(wrapper).findAll("button")[1].attributes("aria-pressed"),
    ).toBe("true");
  });
});
