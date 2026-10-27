// The set pill's context menu is opened from code at a point (#1520), so
// v-menu has no activator to hand focus back to. It must return it itself.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

// The set pill's two ways into the Workflows screen: the router push that opens
// one workflow, and the picture arrival that shows several. Both are doubled
// so the test can assert the call, not the screen it lands on.
const { push, showWorkflowsPictures } = vi.hoisted(() => ({
  push: vi.fn(),
  showWorkflowsPictures: vi.fn(),
}));
vi.mock("vue-router", () => ({ useRouter: () => ({ push }) }));
vi.mock("../../composables/useWorkflowPictures", () => ({
  useWorkflowPictures: () => ({ showWorkflowsPictures }),
}));

import WorkflowSetSelectionBar from "./WorkflowSetSelectionBar.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      // Carries its text on the element it wraps, as the real one's tests do.
      Tooltip: {
        props: ["text", "activator"],
        template:
          '<span class="tip" :data-text="text"><slot name="activator" :props="{}" /></span>',
      },
      // Renders the activator too, as the real menu does once it is open: the
      // pill's ⋯ button is that activator.
      "v-menu": {
        props: ["modelValue"],
        emits: ["update:modelValue"],
        template: '<div><slot name="activator" :props="{}" /><slot /></div>',
      },
    },
  },
};

/** The tooltip text a control carries, whether the tip sits inside or around it. */
function tip(wrapper) {
  const inner = wrapper.find(".tip");
  if (inner.exists()) return inner.attributes("data-text");
  return wrapper.element.parentElement?.dataset.text;
}

/** The rows of the context menu, which opens at the pointer (outside the pill). */
const menuRows = (wrapper) =>
  wrapper
    .findAll(".ctx-item")
    .filter((row) => !row.element.closest(".selbar"));

/** A verb row under the pill's `⋯`, by its data-verb. */
const inMore = (wrapper, verb) =>
  wrapper.find(`.selbar .ctx-menu [data-verb="${verb}"]`);

/** The verbs drawn on the pill itself, in order: not the ones inside a menu. */
const pillVerbs = (wrapper) =>
  wrapper
    .findAll(".selbar [data-verb]")
    .filter((button) => !button.element.closest(".ctx-menu"))
    .map((button) => button.attributes("data-verb"));

/** A selected set's own group, as the store arranges it, with its workflows. */
function setWithWorkflows(store, id, workflows) {
  store.workflowSets.handMade = [{ id, name: `Set ${id}`, members: [] }];
  store.workflowSets.combinations = [
    {
      covered_by: [id],
      workflows,
      picture_count: workflows.reduce((n, w) => n + w.picture_count, 0),
      last_used_at: null,
      latest: [],
    },
  ];
}

describe("WorkflowSetSelectionBar", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    push.mockReset();
    showWorkflowsPictures.mockReset();
  });

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
    await menuRows(wrapper)[0].trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(document.activeElement).toBe(card);
    wrapper.unmount();
    card.remove();
  });

  it("offers Duplicate set under ⋯ for one selected set only", async () => {
    const store = useModelShelfStore();
    const set = (id) => ({ id, name: `Set ${id}`, members: [] });
    store.workflowSets.handMade = [set(10), set(11)];
    store.selectSet(10);
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    // Duplicate is not a pill verb: it is under ⋯.
    expect(pillVerbs(wrapper)).not.toContain("duplicate-set");
    await inMore(wrapper, "duplicate-set").trigger("click");
    expect(wrapper.emitted("duplicate")).toHaveLength(1);

    store.selectSet(11, { ctrl: true });
    await wrapper.vm.$nextTick();
    expect(inMore(wrapper, "duplicate-set").attributes("disabled")).toBeDefined();
    expect(tip(inMore(wrapper, "duplicate-set"))).toContain("one set at a time");
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

    expect(wrapper.find(".selbar-count").text()).toBe("2 selected");
    const merge = inMore(wrapper, "merge-sets");
    expect(merge.attributes("disabled")).toBeUndefined();
    expect(merge.text()).toBe("Merge 2 identical sets into “Set 11”");
    await merge.trigger("click");
    const [[payload]] = wrapper.emitted("merge");
    expect(payload.into.id).toBe(11);
    expect(payload.others.map((set) => set.id)).toEqual([10]);

    // A set on another checkpoint: still there, disabled, saying why.
    store.selectSet(12, { ctrl: true });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".selbar-count").text()).toBe("3 selected");
    expect(inMore(wrapper, "merge-sets").attributes("disabled")).toBeDefined();
    expect(tip(inMore(wrapper, "merge-sets"))).toContain("different checkpoints");
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

    // The partner row is under ⋯, and the context menu draws the same row.
    const rows = wrapper.findAll(".ctx-menu [data-verb=\"merge-sets\"]");
    expect(rows.map((r) => r.text())).toEqual([
      "Merge with “Set 11”",
      "Merge with “Set 11”",
    ]);
    await inMore(wrapper, "merge-sets").trigger("click");
    const [[payload]] = wrapper.emitted("merge");
    expect([payload.into.id, payload.others.map((set) => set.id)]).toEqual([10, [11]]);
    await inMore(wrapper, "reset-set").trigger("click");
    expect(wrapper.emitted("reset")).toHaveLength(1);

    store.selectSet(12);
    await wrapper.vm.$nextTick();
    expect(inMore(wrapper, "merge-sets").text()).toBe("No other set uses ckpt y");
    expect(inMore(wrapper, "merge-sets").attributes("disabled")).toBeDefined();
    // Nothing changed on it: nothing to reset.
    expect(inMore(wrapper, "reset-set").exists()).toBe(false);
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
    // Hide sits before Clear, and Delete is no longer on the pill at all.
    expect(pillVerbs(wrapper)).toEqual([
      "show-workflows",
      "show-pictures",
      "rename-set",
      "more",
      "hide-sets",
      "clear",
    ]);

    await pill.trigger("click");
    expect(wrapper.emitted("hide-sets")).toHaveLength(1);

    const row = menuRows(wrapper).find((r) => r.attributes("data-verb") === "hide-sets");
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
    expect(menuRows(wrapper).find((r) => r.attributes("data-verb") === "hide-sets").text()).toBe(
      "Show workflow set again",
    );
  });

  it("offers no Hide row when no selected set is hideable", async () => {
    const wrapper = selectedSetBar([]);
    await wrapper.vm.$nextTick();

    expect(wrapper.find('[data-verb="hide-sets"]').exists()).toBe(false);
    expect(wrapper.find('.ctx-item[data-verb="hide-sets"]').exists()).toBe(false);
    // The rest of the row is still there: Delete under ⋯, last.
    expect(inMore(wrapper, "delete-set").exists()).toBe(true);
  });

  it("labels Show workflows, Show pictures and Rename on the pill", () => {
    const store = useModelShelfStore();
    setWithWorkflows(store, 10, [{ id: "w1", name: "A", picture_count: 2 }]);
    store.selectSet(10);
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    expect(wrapper.find('[data-verb="show-workflows"]').text()).toBe("Show workflows");
    expect(wrapper.find('[data-verb="show-pictures"]').text()).toBe("Show pictures");
    expect(wrapper.find('[data-verb="rename-set"]').text()).toBe("Rename");
  });

  it("opens the workflow the set's pictures come from most", async () => {
    const store = useModelShelfStore();
    setWithWorkflows(store, 10, [
      { id: "w1", name: "A", picture_count: 2 },
      { id: "w2", name: "B", picture_count: 5 },
    ]);
    store.selectSet(10);
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    expect(wrapper.find('[data-verb="show-workflows"]').attributes("disabled")).toBeUndefined();
    await wrapper.find('[data-verb="show-workflows"]').trigger("click");
    expect(push).toHaveBeenCalledWith({ name: "workflows", query: { workflow: "w2" } });
  });

  it("refuses Show workflows, with the reason, for a set that ran in no workflow", async () => {
    const store = useModelShelfStore();
    setWithWorkflows(store, 10, []);
    store.selectSet(10);
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    const button = wrapper.find('[data-verb="show-workflows"]');
    expect(button.attributes("disabled")).toBeDefined();
    expect(tip(button)).toContain("has not run in any workflow");
    await button.trigger("click");
    expect(push).not.toHaveBeenCalled();
  });

  it("refuses Show workflows for two sets, since it opens one", async () => {
    const store = useModelShelfStore();
    setWithWorkflows(store, 10, [{ id: "w1", name: "A", picture_count: 2 }]);
    store.workflowSets.handMade.push({ id: 11, name: "Set 11", members: [] });
    store.selectSet(10);
    store.selectSet(11, { ctrl: true });
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    const button = wrapper.find('[data-verb="show-workflows"]');
    expect(button.attributes("disabled")).toBeDefined();
    expect(tip(button)).toContain("Select one set");
  });

  it("shows the pictures of every workflow the selected sets ran in, each once", async () => {
    const store = useModelShelfStore();
    const w1 = { id: "w1", name: "A", picture_count: 2 };
    const w2 = { id: "w2", name: "B", picture_count: 5 };
    const w3 = { id: "w3", name: "C", picture_count: 1 };
    store.workflowSets.handMade = [
      { id: 10, name: "Set 10", members: [] },
      { id: 11, name: "Set 11", members: [] },
    ];
    // The first combination is covered by both sets: it must count once.
    store.workflowSets.combinations = [
      { covered_by: [10, 11], workflows: [w1, w2], picture_count: 7, latest: [] },
      { covered_by: [11], workflows: [w3], picture_count: 1, latest: [] },
    ];
    store.selectSet(10);
    store.selectSet(11, { ctrl: true });
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    await wrapper.find('[data-verb="show-pictures"]').trigger("click");
    expect(showWorkflowsPictures).toHaveBeenCalledTimes(1);
    expect(showWorkflowsPictures.mock.calls[0][0].map((w) => w.id)).toEqual([
      "w2",
      "w1",
      "w3",
    ]);
    // Counted once: w2 is 5 pictures, not 10 from the shared combination.
    expect(showWorkflowsPictures.mock.calls[0][0].map((w) => w.picture_count)).toEqual([
      5,
      2,
      1,
    ]);
  });

  it("refuses Show pictures, with the reason, when no workflow ran with the set", async () => {
    const store = useModelShelfStore();
    setWithWorkflows(store, 10, []);
    store.selectSet(10);
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    const button = wrapper.find('[data-verb="show-pictures"]');
    expect(button.attributes("disabled")).toBeDefined();
    expect(tip(button)).toContain("no pictures yet");
  });

  it("clears the set selection from the × on the pill", async () => {
    const store = useModelShelfStore();
    store.workflowSets.handMade = [{ id: 10, name: "Set 10", members: [] }];
    store.selectSet(10);
    const wrapper = mount(WorkflowSetSelectionBar, globalOpts);
    await wrapper.find('[data-verb="clear"]').trigger("click");
    expect(store.selectedSets).toEqual([]);
  });
});
