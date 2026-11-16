// The person's "Generate character sheets…": the sheet step on its own. The
// step is stubbed; what is tested is the hosting: a draw left open comes back,
// a cut closes the dialog, Cancel stops the runs and drops the draw, and a cut
// in flight cannot be closed out of.

import { describe, expect, it, vi } from "vitest";
import { defineComponent, ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const stepState = vi.hoisted(() => ({
  working: false,
  zooming: false,
  savedSeen: [],
  stopAll: null,
}));
vi.mock("./CharacterSheetStep.vue", () => ({
  default: defineComponent({
    props: { character: Object, pictureCount: Number, saved: Object },
    emits: ["cut", "done", "faces"],
    setup(props, { expose }) {
      stepState.savedSeen.push(props.saved);
      expose({
        working: ref(stepState.working),
        zooming: ref(stepState.zooming),
        snapshot: ref({ phase: "pick", order: [41, 42] }),
        footer: ref({ lead: "", primary: { label: "Keep", enabled: true, run() {} } }),
        error: ref(""),
        workflowOptions: ref([]),
        stopAll: () => stepState.stopAll?.(),
      });
      return () => null;
    },
  }),
}));
vi.mock("./FaceClustersDialog.vue", () => ({
  default: defineComponent({ props: ["open"], render: () => null }),
}));

import CharacterSheetsDialog from "./CharacterSheetsDialog.vue";
import CharacterSheetStep from "./CharacterSheetStep.vue";

function mountDialog(character) {
  return mount(CharacterSheetsDialog, {
    props: { open: true, character },
    global: { stubs: { teleport: true } },
    attachTo: document.body,
  });
}

const cancelButton = (wrapper) => wrapper.find('[data-testid="csd-cancel"]');

const closeButton = (wrapper) =>
  wrapper.find('[aria-label="Close"], .app-dialog__actions button');

describe("CharacterSheetsDialog", () => {
  it("keeps a draw that is closed, and hands it back on reopening", async () => {
    stepState.working = false;
    const wrapper = mountDialog({ id: 3, name: "Wren", image_count: 12 });
    await flushPromises();
    expect(stepState.savedSeen.at(-1)).toBeNull();
    expect(wrapper.findComponent(CharacterSheetStep).props("pictureCount")).toBe(12);
    await closeButton(wrapper).trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();

    const again = mountDialog({ id: 3, name: "Wren" });
    await flushPromises();
    expect(stepState.savedSeen.at(-1)).toEqual({ phase: "pick", order: [41, 42] });
    again.unmount();

    // A deleted person's id reused by a new one: the old draw is not theirs.
    const stranger = mountDialog({ id: 3, name: "Someone else" });
    await flushPromises();
    expect(stepState.savedSeen.at(-1)).toBeNull();
    stranger.unmount();
  });

  it("closes once the sheets are cut, and forgets the draw", async () => {
    stepState.working = false;
    const wrapper = mountDialog({ id: 5, name: "Ash" });
    await flushPromises();
    await closeButton(wrapper).trigger("click");
    wrapper.unmount();

    const again = mountDialog({ id: 5, name: "Ash" });
    await flushPromises();
    again.findComponent(CharacterSheetStep).vm.$emit("cut", [7, 8]);
    expect(again.emitted("done")[0][0]).toEqual({
      character: { id: 5, name: "Ash" },
      pictureIds: [7, 8],
    });
    expect(again.emitted("close")).toHaveLength(1);
    again.unmount();

    const third = mountDialog({ id: 5, name: "Ash" });
    await flushPromises();
    expect(stepState.savedSeen.at(-1)).toBeNull();
    third.unmount();
  });

  it("cancels: stops what is still drawing and forgets the draw", async () => {
    stepState.working = false;
    stepState.stopAll = vi.fn();
    const wrapper = mountDialog({ id: 6, name: "Rue" });
    await flushPromises();
    await closeButton(wrapper).trigger("click");
    wrapper.unmount();

    const again = mountDialog({ id: 6, name: "Rue" });
    await flushPromises();
    expect(stepState.savedSeen.at(-1)).toEqual({ phase: "pick", order: [41, 42] });
    await cancelButton(again).trigger("click");
    expect(stepState.stopAll).toHaveBeenCalledTimes(1);
    expect(again.emitted("close")).toHaveLength(1);
    again.unmount();

    const third = mountDialog({ id: 6, name: "Rue" });
    await flushPromises();
    expect(stepState.savedSeen.at(-1)).toBeNull();
    third.unmount();
    stepState.stopAll = null;
  });

  it.each([
    ["a cut is in flight", { working: true, zooming: false }],
    ["a sheet is zoomed", { working: false, zooming: true }],
  ])("does not close while %s", async (_, state) => {
    Object.assign(stepState, state);
    const wrapper = mountDialog({ id: 9, name: "Kit" });
    await flushPromises();
    await closeButton(wrapper).trigger("click");
    await cancelButton(wrapper).trigger("click");
    expect(wrapper.emitted("close")).toBeUndefined();
    wrapper.unmount();
    Object.assign(stepState, { working: false, zooming: false });
  });
});
