// OverlayDescriptionPanel - ending an edit returns the keyboard to the overlay.
//
// The regression this pins: after Enter saved the description (or Escape
// cancelled the edit), the textarea kept DOM focus, so the overlay's Ctrl+Z -
// which rightly defers to typing targets - stayed dead until a click. Ending
// an edit must blur the field and tell the parent, which refocuses its canvas.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const patchPicture = vi.fn();
const resetPictureDescription = vi.fn();
const listTaggers = vi.fn();

vi.mock("../../api/pictures", () => ({
  patchPicture: (...a) => patchPicture(...a),
  resetPictureDescription: (...a) => resetPictureDescription(...a),
}));

vi.mock("../../api/taggers", () => ({
  listTaggers: (...a) => listTaggers(...a),
}));

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return { isReadOnly: ref(false) };
});

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VProgressCircular: { name: "v-progress-circular", template: "<i></i>" },
}));

import OverlayDescriptionPanel from "./OverlayDescriptionPanel.vue";

const IMAGE = { id: 7, description: "a quiet harbour" };

function mountPanel(props = {}) {
  return mount(OverlayDescriptionPanel, {
    props: { image: IMAGE, backendUrl: "http://b.test", ...props },
    attachTo: document.body,
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  patchPicture.mockResolvedValue({});
  listTaggers.mockResolvedValue([]);
});

describe("OverlayDescriptionPanel - ending an edit", () => {
  it("saves on Enter, blurs the field and signals editing-finished", async () => {
    const w = mountPanel();
    const area = w.find("textarea");
    await area.trigger("focus");
    expect(w.vm.isEditingDescription).toBe(true);

    await area.setValue("a louder harbour");
    await area.trigger("keydown.enter");
    await Promise.resolve();
    await w.vm.$nextTick();

    expect(patchPicture).toHaveBeenCalledWith(
      7,
      { description: "a louder harbour" },
    );
    expect(w.emitted("update-description")).toBeTruthy();
    expect(w.emitted("editing-finished")).toBeTruthy();
    expect(document.activeElement).not.toBe(area.element);
  });

  it("cancel blurs the field and signals editing-finished too", async () => {
    const w = mountPanel();
    const area = w.find("textarea");
    await area.trigger("focus");
    w.vm.cancelEditDescription();
    await w.vm.$nextTick();

    expect(w.emitted("editing-finished")).toBeTruthy();
    expect(w.vm.isEditingDescription).toBe(false);
    expect(document.activeElement).not.toBe(area.element);
  });

  it("adopts an external description change while not editing", async () => {
    // This is the panel half of undo/redo reaching an open overlay: the
    // parent refetches metadata and the prop moves; the field must follow.
    const w = mountPanel();
    await w.setProps({ image: { id: 7, description: "restored text" } });
    await w.vm.$nextTick();
    expect(w.find("textarea").element.value).toBe("restored text");
  });
});
