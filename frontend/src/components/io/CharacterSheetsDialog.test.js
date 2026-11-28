// The person's "Generate character sheets…": the sheet step on its own. The
// step is stubbed; what is tested is the hosting: a draw left open comes back,
// a cut closes the dialog, Cancel stops the runs and drops the draw, and a cut
// in flight cannot be closed out of.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
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
  snapshot: null,
}));
vi.mock("./CharacterSheetStep.vue", () => ({
  default: defineComponent({
    props: { kind: String, character: Object, pictureCount: Number, saved: Object },
    emits: ["cut", "done", "faces"],
    setup(props, { expose }) {
      stepState.savedSeen.push(props.saved);
      expose({
        working: ref(stepState.working),
        zooming: ref(stepState.zooming),
        snapshot: ref(stepState.snapshot ?? { phase: "pick", order: [41, 42] }),
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
import { useSheetDrawsStore } from "../../stores/useSheetDrawsStore";

function mountDialog(character, kind = "character") {
  return mount(CharacterSheetsDialog, {
    props: { open: true, character, kind },
    global: { stubs: { teleport: true } },
    attachTo: document.body,
  });
}

const cancelButton = (wrapper) => wrapper.find('[data-testid="csd-cancel"]');

const closeButton = (wrapper) =>
  wrapper.find('[aria-label="Close"], .app-dialog__actions button');

// The closed draws are kept in a store: one per test, so none carries over.
beforeEach(() => setActivePinia(createPinia()));

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

  it("keeps a closed item draw where the Studio shows a placeholder for it", async () => {
    stepState.working = false;
    const wrapper = mountDialog({ id: 3, name: "Wren" }, "outfit");
    await flushPromises();
    await closeButton(wrapper).trigger("click");
    const kept = useSheetDrawsStore().get("outfit:3");
    // No name typed: the kind's own, as the set would be called.
    expect(kept).toMatchObject({
      kind: "outfit",
      character: { id: 3, name: "Wren" },
      label: "New outfit",
    });
    await mountDialog({ id: 3, name: "Wren" }, "outfit").find('[data-testid="csd-cancel"]').trigger("click");
    expect(useSheetDrawsStore().get("outfit:3")).toBeNull();

    // A name typed for it is the placeholder's.
    stepState.snapshot = { phase: "drawing", form: { name: "  Raincoat " }, runs: [] };
    const named = mountDialog({ id: 4, name: "Ash" }, "outfit");
    await flushPromises();
    await closeButton(named).trigger("click");
    stepState.snapshot = null;
    expect(useSheetDrawsStore().get("outfit:4").label).toBe("Raincoat");
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

  it("draws a location with no person, and closes once its set is made", async () => {
    stepState.working = false;
    const wrapper = mountDialog(null, "location");
    await flushPromises();
    expect(wrapper.text()).toContain("Location sheets");
    const step = wrapper.findComponent(CharacterSheetStep);
    expect(step.props("kind")).toBe("location");
    // The cut alone does not close it: the set is made after.
    step.vm.$emit("cut", [7, 8, 9, 10]);
    expect(wrapper.emitted("close")).toBeUndefined();
    step.vm.$emit("done", [7, 8, 9, 10], { id: 4, name: "Pine trail" });
    expect(wrapper.emitted("done")[0][0]).toEqual({
      kind: "location",
      character: null,
      pictureIds: [7, 8, 9, 10],
      set: { id: 4, name: "Pine trail" },
    });
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("keeps a person's outfit draw apart from their character sheets", async () => {
    stepState.working = false;
    const outfit = mountDialog({ id: 11, name: "Mira" }, "outfit");
    await flushPromises();
    expect(outfit.text()).toContain("Outfit sheets for Mira");
    await closeButton(outfit).trigger("click");
    outfit.unmount();

    const sheets = mountDialog({ id: 11, name: "Mira" });
    await flushPromises();
    expect(stepState.savedSeen.at(-1)).toBeNull();
    // A character's step says done after its faces are read: the dialog
    // already reported its cut and does not report it again.
    sheets.findComponent(CharacterSheetStep).vm.$emit("done", [1]);
    expect(sheets.emitted("done")).toBeUndefined();
    sheets.unmount();

    const again = mountDialog({ id: 11, name: "Mira" }, "outfit");
    await flushPromises();
    expect(stepState.savedSeen.at(-1)).toEqual({ phase: "pick", order: [41, 42] });
    again.unmount();
  });
});
