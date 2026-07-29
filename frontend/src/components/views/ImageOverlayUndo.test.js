// Ctrl+Z inside the lightbox.
//
// This was dead, and not for the reason the code claimed. App's guard checks
// for a Vuetify scrim, which `.image-overlay` does not render; what actually
// stopped it is ImageOverlay's own `stopImmediatePropagation()` on a listener
// registered BEFORE App's (a child mounts first). So the binding has to live in
// the overlay's own handler, which is what these tests drive: they dispatch on
// `window`, exactly as a real keypress arrives.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const getMock = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/workflow")) {
    const e = new Error("no workflow");
    e.response = { status: 404 };
    throw e;
  }
  return { data: [] };
});

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    apiClient: { get: (...a) => getMock(...a), post: vi.fn(), delete: vi.fn() },
    appendShareToken: (u) => u,
    isReadOnly: ref(false),
    setRequestClientId: vi.fn(),
  };
});

vi.mock("../../api/operations", () => ({
  listOperations: vi.fn().mockResolvedValue([]),
  getUndoState: vi.fn().mockResolvedValue({ can_undo: true, can_redo: true }),
  undoLastOperation: vi.fn().mockResolvedValue({ operations: [] }),
  redoOperation: vi.fn().mockResolvedValue({ operations: [] }),
  undoOperation: vi.fn().mockResolvedValue({ operations: [] }),
  undoBatch: vi.fn().mockResolvedValue({ operations: [] }),
}));

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

import { useOperationStore } from "../../stores/useOperationStore";

// The receipt is deliberately NOT stubbed: the Escape guard and the hint
// suppression are contracts between the two components.
const STUBS = {
  OverlayTagsPanel: true,
  OverlayFilmstrip: true,
  OverlayDescriptionPanel: true,
  OverlayMetadataPanel: true,
  AddToEntityControl: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  "v-icon": true,
  "v-menu": true,
  "v-tooltip": true,
};

const flush = () => new Promise((r) => setTimeout(r, 0));

async function openOverlay() {
  const { default: ImageOverlay } = await import("./ImageOverlay.vue");
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: 7,
      allImages: [{ id: 7, tags: [] }],
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
    },
    global: { stubs: STUBS },
    attachTo: document.body,
  });
  await wrapper.setProps({ open: true });
  await flush();
  await flush();
  return wrapper;
}

function press(key, init = {}) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  (init.target ?? window).dispatchEvent(event);
  return event;
}

function op(overrides = {}) {
  return {
    id: 10,
    batch_id: null,
    created_at: "2026-07-29T12:00:00",
    op_type: "pictures.tags.add",
    target_count: 1,
    origin_client_id: "me",
    undoable: true,
    status: "applied",
    summary: "Added tag 'portrait'",
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("ImageOverlay — the undo binding", () => {
  it("undoes on Ctrl+Z while the lightbox is open", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const wrapper = await openOverlay();

    const event = press("z", { ctrlKey: true });
    expect(undo).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
    wrapper.unmount();
  });

  it("accepts Meta+Z, so the binding is not platform-specific", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const wrapper = await openOverlay();

    press("z", { metaKey: true });
    expect(undo).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("redoes on Ctrl+Y and on Ctrl+Shift+Z", async () => {
    const store = useOperationStore();
    const redo = vi.spyOn(store, "redo").mockResolvedValue(null);
    const wrapper = await openOverlay();

    press("y", { ctrlKey: true });
    press("Z", { ctrlKey: true, shiftKey: true });
    expect(redo).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it("does not walk the stack on a held key", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const wrapper = await openOverlay();

    press("z", { ctrlKey: true, repeat: true });
    expect(undo).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("leaves a text field its own native undo", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const wrapper = await openOverlay();

    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    press("z", { ctrlKey: true, target: input });
    expect(undo).not.toHaveBeenCalled();

    input.remove();
    wrapper.unmount();
  });

  it("still zooms on a bare z", async () => {
    // The regression the base lane fixed: a modifier-blind `z` made Ctrl+Z zoom
    // instead of undo. Both halves have to keep working.
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const wrapper = await openOverlay();

    expect(wrapper.find(".zoom-hud").classes()).toContain("hidden");
    press("z");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".zoom-hud").classes()).not.toContain("hidden");
    expect(undo).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("still undoes with the chrome hidden", async () => {
    // The narration is a transient HUD like the progress cards and the swipe
    // hint, none of which hide with the chrome, so undo stays reachable on a
    // bare image and still reports itself.
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const wrapper = await openOverlay();

    press(" ");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".overlay-shell").classes()).toContain("chrome-hidden");

    press("z", { ctrlKey: true });
    expect(undo).toHaveBeenCalledTimes(1);
    // …and the keystroke did not drag the chrome back.
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".overlay-shell").classes()).toContain("chrome-hidden");
    wrapper.unmount();
  });
});

describe("ImageOverlay — the narration on this surface", () => {
  it("renders the receipt in the lightbox's own chrome", async () => {
    const store = useOperationStore();
    const wrapper = await openOverlay();
    store.showReceipt(store.buildReceipt(op(), "did"));
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".overlay-receipt").exists()).toBe(true);
    expect(wrapper.find(".overlay-receipt .r-text").text()).toBe(
      "Added tag 'portrait'",
    );
    wrapper.unmount();
  });

  it("stands the chrome hint down while a receipt is up, and back after", async () => {
    const store = useOperationStore();
    const wrapper = await openOverlay();
    press(" ");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".overlay-chrome-hint").exists()).toBe(true);

    store.showReceipt(store.buildReceipt(op(), "did"));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".overlay-chrome-hint").exists()).toBe(false);

    store.dismissReceipt();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".overlay-chrome-hint").exists()).toBe(true);
    wrapper.unmount();
  });

  it("keeps the receipt through arrow navigation", async () => {
    // The receipt narrates an OPERATION, not a picture. Dismissing it on
    // navigation would remove the undo affordance exactly as the user walks
    // away from the mistake.
    const store = useOperationStore();
    const wrapper = await openOverlay();
    store.showReceipt(store.buildReceipt(op(), "did"));
    await wrapper.vm.$nextTick();

    press("ArrowRight");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".overlay-receipt").exists()).toBe(true);
    wrapper.unmount();
  });

  it("gives a keyboard user inside the pill an exit that is not closing the lightbox", async () => {
    const store = useOperationStore();
    const wrapper = await openOverlay();
    store.showReceipt(store.buildReceipt(op(), "did"));
    await wrapper.vm.$nextTick();

    wrapper.find(".overlay-receipt .r-btn").element.focus();
    press("Escape");
    await wrapper.vm.$nextTick();

    expect(store.receipt).toBeNull();
    expect(wrapper.emitted("close")).toBeFalsy();
    wrapper.unmount();
  });

  it("closes the lightbox on Escape from anywhere else, receipt or not", async () => {
    const store = useOperationStore();
    const wrapper = await openOverlay();
    store.showReceipt(store.buildReceipt(op(), "did"));
    await wrapper.vm.$nextTick();

    press("Escape");
    await wrapper.vm.$nextTick();
    expect(wrapper.emitted("close")).toBeTruthy();
    // The receipt is NOT dismissed on close: the same one, with its remaining
    // dwell, is handed back to the grid pill already mounted underneath.
    expect(store.receipt).not.toBeNull();
    wrapper.unmount();
  });
});
