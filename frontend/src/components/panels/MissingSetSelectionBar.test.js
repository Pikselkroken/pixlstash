// A set whose checkpoint is missing keeps the shelf's whole verb menu: Replace
// leads, Copy filename copies the missing name, and the verbs that need the
// checkpoint's shelf row are listed disabled rather than dropped.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import MissingSetSelectionBar from "./MissingSetSelectionBar.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      Tooltip: true,
      // Renders the activator too: the pill's ⋯ button is that activator.
      "v-menu": {
        props: ["modelValue"],
        emits: ["update:modelValue"],
        template: '<div><slot name="activator" :props="{}" /><slot /></div>',
      },
    },
  },
};

function selectMissing(names) {
  const store = useModelShelfStore();
  store.workflowSets = {
    combinations: names.map((name) => ({
      key: `+${name}`,
      models: [],
      missing: [{ name, workflow_ids: ["auto:a"] }],
      recipes: 1,
      history_runs: 0,
      picture_count: 1,
      covers: [],
    })),
    handMade: [],
  };
  store.selectAllMissing();
  return store;
}

/** The rows of the context menu, which opens at the pointer (outside the pill). */
const items = (wrapper) =>
  wrapper
    .findAll(".ctx-item")
    .filter((row) => !row.element.closest(".selbar"));
/** The rows under the pill's ⋯ button, which the pill holds. */
const moreItems = (wrapper) =>
  wrapper
    .findAll(".selbar .ctx-item");
const byLabel = (wrapper, label) =>
  moreItems(wrapper).find((b) => b.text().startsWith(label)) ??
  items(wrapper).find((b) => b.text().startsWith(label));

describe("MissingSetSelectionBar", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("labels Replace on the pill, and keeps the file verbs under ⋯, disabled", async () => {
    selectMissing(["gone.sft"]);
    const wrapper = mount(MissingSetSelectionBar, globalOpts);
    const replace = wrapper.find('[data-verb="replace-missing"]');
    expect(replace.text()).toBe("Replace…");
    expect(replace.attributes("disabled")).toBeUndefined();
    // Replace is not repeated under ⋯, and the card menu still leads with it.
    expect(moreItems(wrapper).map((b) => b.text()).join("|")).not.toContain("Replace");
    expect(items(wrapper)[0].text()).toBe("Replace missing model…");
    for (const label of ["Rename", "Set base model…", "Move to…", "Works with…", "Delete"]) {
      expect(byLabel(wrapper, label).attributes("disabled"), label).toBeDefined();
    }
    await replace.trigger("click");
    expect(wrapper.emitted("replace")).toHaveLength(1);
  });

  it("puts ⋯ on the pill when the card has more than Replace and Hide", () => {
    selectMissing(["gone.sft"]);
    const wrapper = mount(MissingSetSelectionBar, globalOpts);
    expect(wrapper.find('.selbar [data-verb="more"]').exists()).toBe(true);
    expect(wrapper.find('.selbar [data-verb="clear"]').exists()).toBe(true);
  });

  it("offers Hide on the pill and under Replace, over every selected card", async () => {
    const store = selectMissing(["a.sft", "b.sft"]);
    expect(store.selectedMissingGroups.map((g) => g.key)).toEqual([
      "missing:a.sft",
      "missing:b.sft",
    ]);
    // The view hands in every lit card, a model card held beside these too.
    const wrapper = mount(MissingSetSelectionBar, {
      ...globalOpts,
      props: { sets: [...store.selectedMissingGroups, { hidden: false }] },
    });
    expect(items(wrapper)[1].text()).toBe("Hide 3 workflow sets");
    expect(items(wrapper)[1].attributes("disabled")).toBeUndefined();
    await items(wrapper)[1].trigger("click");
    await wrapper.find('.selbar [data-verb="hide-sets"]').trigger("click");
    // Emits, like Replace: the view hands the store the selected cards.
    expect(wrapper.emitted("hide-sets")).toHaveLength(2);
    expect(wrapper.emitted("replace")).toBeUndefined();
  });

  it("has no Hide while the view names no card", () => {
    selectMissing(["a.sft"]);
    const wrapper = mount(MissingSetSelectionBar, globalOpts);
    expect(wrapper.find('[data-verb="hide-sets"]').exists()).toBe(false);
  });

  it("clears the selection from the × on the pill", async () => {
    const store = selectMissing(["a.sft"]);
    const wrapper = mount(MissingSetSelectionBar, globalOpts);
    await wrapper.find('[data-verb="clear"]').trigger("click");
    expect(store.selectedMissing).toEqual([]);
  });

  it("copies the missing files' names", async () => {
    const writeText = vi.fn().mockResolvedValue();
    Object.assign(navigator, { clipboard: { writeText } });
    selectMissing(["a.sft", "b.sft"]);
    const wrapper = mount(MissingSetSelectionBar, globalOpts);
    expect(items(wrapper)[0].text()).toBe("Replace missing models…");
    await byLabel(wrapper, "Copy filenames").trigger("click");
    expect(writeText).toHaveBeenCalledWith("a.sft\nb.sft");
  });
});
