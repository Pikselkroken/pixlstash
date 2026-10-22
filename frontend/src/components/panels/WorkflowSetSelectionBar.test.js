// The set pill's context menu is opened from code at a point (#1520), so
// v-menu has no activator to hand focus back to. It must return it itself.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import WorkflowSetSelectionBar from "./WorkflowSetSelectionBar.vue";
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

describe("WorkflowSetSelectionBar", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("puts focus back on the card that opened the menu when it closes", async () => {
    const card = document.createElement("div");
    card.tabIndex = 0;
    document.body.appendChild(card);
    const wrapper = mount(WorkflowSetSelectionBar, {
      ...globalOpts,
      attachTo: document.body,
    });

    wrapper.vm.openContextMenu(10, 20, card);
    await wrapper.vm.$nextTick();
    document.activeElement?.blur?.();
    // A menu row pressed: the menu closes.
    await wrapper.find(".ctx-item").trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(document.activeElement).toBe(card);
    wrapper.unmount();
    card.remove();
  });

  it("offers Duplicate set for one selected set only", async () => {
    const store = useModelShelfStore();
    const set = (id) => ({ id, name: `Set ${id}`, members: [] });
    store.workflowSets.handMade = [set(10), set(11)];
    store.selectSet(10);
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    const button = () => wrapper.find('[data-verb="duplicate-set"]');
    await button().trigger("click");
    const item = wrapper
      .findAll(".ctx-item")
      .find((row) => row.text().includes("Duplicate set"));
    await item.trigger("click");
    expect(wrapper.emitted("duplicate")).toHaveLength(2);

    store.selectSet(11, { ctrl: true });
    await wrapper.vm.$nextTick();
    expect(button().attributes("disabled")).toBeDefined();
    expect(wrapper.text()).not.toContain("Duplicate set");
  });

  /** A set on checkpoint `sha`, holding `extra` besides it. */
  const onCheckpoint = (id, sha, extra = []) => ({
    id,
    name: `Set ${id}`,
    members: [
      { sha256: sha, slot: "checkpoint", name: `ckpt ${sha}`, on_shelf: true, id: 1 },
      ...extra,
    ],
  });

  it("merges selected sets on one checkpoint into the one last clicked (#1972)", async () => {
    const store = useModelShelfStore();
    const sets = [onCheckpoint(10, "x"), onCheckpoint(11, "x"), onCheckpoint(12, "y")];
    store.workflowSets.handMade = sets;
    store.selectSet(10);
    store.selectSet(11, { ctrl: true });
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".selbar-count").text()).toBe("2 sets · ckpt x");
    const pill = wrapper.find('.selbar [data-verb="merge-sets"]');
    expect(pill.attributes("disabled")).toBeUndefined();
    expect(pill.attributes("aria-label")).toBe("Merge 2 identical sets into “Set 11”");
    await pill.trigger("click");
    const [[payload]] = wrapper.emitted("merge");
    expect(payload.into.id).toBe(11);
    expect(payload.others.map((set) => set.id)).toEqual([10]);

    // A set on another checkpoint: still there, disabled, saying why.
    store.selectSet(12, { ctrl: true });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".selbar-count").text()).toBe("3 sets · 2 checkpoints");
    const row = wrapper.find('.ctx-item[data-verb="merge-sets"]');
    expect(row.attributes("disabled")).toBeDefined();
    expect(wrapper.find('.selbar [data-verb="merge-sets"]').attributes("disabled")).toBeDefined();
  });

  it("offers one set the others on its checkpoint, and Reset when it has changes", async () => {
    const store = useModelShelfStore();
    const changed = onCheckpoint(10, "x", [
      { sha256: "d", slot: "lora", name: "mine", origin: "added", on_shelf: true, id: 4 },
    ]);
    store.workflowSets.handMade = [changed, onCheckpoint(11, "x"), onCheckpoint(12, "y")];
    store.selectSet(10);
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll('.ctx-item[data-verb="merge-sets"]');
    expect(rows.map((r) => r.text())).toEqual(["Merge with “Set 11”"]);
    await rows[0].trigger("click");
    const [[payload]] = wrapper.emitted("merge");
    expect([payload.into.id, payload.others.map((set) => set.id)]).toEqual([10, [11]]);
    await wrapper.find('.ctx-item[data-verb="reset-set"]').trigger("click");
    expect(wrapper.emitted("reset")).toHaveLength(1);

    store.selectSet(12);
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("No other set uses ckpt y");
    // Nothing changed on it: nothing to reset.
    expect(wrapper.find('.ctx-item[data-verb="reset-set"]').exists()).toBe(false);
  });

  /** One selected hand-made set, as the grid hands `sets` to the bar. */
  function selectedSetBar(sets) {
    const store = useModelShelfStore();
    store.workflowSets.handMade = [{ id: 10, name: "Kit", members: [] }];
    store.selectSet(10);
    return mount(WorkflowSetSelectionBar, { ...globalOpts, props: { sets } });
  }
  const hiddenable = (hidden) => [
    { key: "hand:10", hidden, card: { name: "Kit" } },
  ];

  it("offers Hide in the pill and the menu, and emits hide-sets", async () => {
    const wrapper = selectedSetBar(hiddenable(false));
    await wrapper.vm.$nextTick();

    const pill = wrapper.find('.selbar [data-verb="hide-sets"]');
    expect(pill.attributes("aria-label")).toBe("Hide workflow set");
    // Between Duplicate set and Delete set: Delete stays last.
    const verbs = wrapper
      .findAll(".selbar [data-verb]")
      .map((b) => b.attributes("data-verb"));
    expect(verbs).toEqual([
      "rename-set",
      "duplicate-set",
      "hide-sets",
      "delete-set",
    ]);

    await pill.trigger("click");
    expect(wrapper.emitted("hide-sets")).toHaveLength(1);

    const row = wrapper.find('.ctx-item[data-verb="hide-sets"]');
    expect(row.text()).toBe("Hide workflow set");
    await row.trigger("click");
    expect(wrapper.emitted("hide-sets")).toHaveLength(2);
  });

  it("reads Show again when every selected set is hidden", async () => {
    const wrapper = selectedSetBar(hiddenable(true));
    await wrapper.vm.$nextTick();

    expect(wrapper.find('.selbar [data-verb="hide-sets"]').attributes("aria-label")).toBe(
      "Show workflow set again",
    );
    expect(wrapper.find('.ctx-item[data-verb="hide-sets"]').text()).toBe(
      "Show workflow set again",
    );
  });

  it("offers no Hide row when no selected set is hideable", async () => {
    const wrapper = selectedSetBar([]);
    await wrapper.vm.$nextTick();

    expect(wrapper.find('[data-verb="hide-sets"]').exists()).toBe(false);
    expect(wrapper.find('.ctx-item[data-verb="hide-sets"]').exists()).toBe(false);
    // The rest of the row is still there.
    expect(wrapper.find('[data-verb="delete-set"]').exists()).toBe(true);
  });
});
