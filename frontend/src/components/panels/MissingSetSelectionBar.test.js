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
      "v-menu": {
        props: ["modelValue"],
        emits: ["update:modelValue"],
        template: "<div><slot /></div>",
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
    noSet: [],
    handMade: [],
  };
  store.selectAllMissing();
  return store;
}

const items = (wrapper) => wrapper.findAll(".ctx-item");
const byLabel = (wrapper, label) =>
  items(wrapper).find((b) => b.text().startsWith(label));

describe("MissingSetSelectionBar", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("leads with Replace and keeps the file verbs, disabled", async () => {
    selectMissing(["gone.sft"]);
    const wrapper = mount(MissingSetSelectionBar, globalOpts);
    expect(items(wrapper)[0].text()).toBe("Replace missing model…");
    expect(items(wrapper)[0].attributes("disabled")).toBeUndefined();
    for (const label of ["Rename", "Set base model…", "Move to…", "Works with…", "Delete"]) {
      expect(byLabel(wrapper, label).attributes("disabled"), label).toBeDefined();
    }
    await items(wrapper)[0].trigger("click");
    expect(wrapper.emitted("replace")).toHaveLength(1);
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
