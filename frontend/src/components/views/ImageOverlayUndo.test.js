// Ctrl+Z inside the lightbox.
//
// This was dead, and not for the reason the code claimed. App's guard checks
// for a Vuetify scrim, which `.image-overlay` does not render; what actually
// stopped it is ImageOverlay's own `stopImmediatePropagation()` on a listener
// registered BEFORE App's (a child mounts first). So the binding has to live in
// the overlay's own handler, which is what these tests drive: they dispatch on
// `window`, exactly as a real keypress arrives.

import { afterEach, describe, it, expect, beforeEach, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

// Imported statically, not lazily inside the tests. `vi.mock` is hoisted
// above every import, so a lazy import buys no mock ordering. It only moves
// the cost of compiling this 5.7k-line SFC (~7s on a loaded machine) inside
// the first test's 5s timeout, which is what made this file flake in the full
// suite while passing on its own.
import ImageOverlay from "./ImageOverlay.vue";

// A test that fails mid-way must not leave a mounted overlay behind: its
// window-level keydown listener would answer every later test in this file.
enableAutoUnmount(afterEach);

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
    API_BASE_URL: "/api/v1",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
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


import { useOperationStore } from "../../stores/useOperationStore";

// The receipt is deliberately NOT stubbed: the Escape guard and the hint
// suppression are contracts between the two components.
const STUBS = {
  OverlayTagsPanel: true,
  OverlayFilmstrip: true,
  OverlayDescriptionPanel: true,
  OverlayMetadataPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  "v-icon": true,
  "v-menu": true,
  "v-tooltip": true,
};

const flush = () => new Promise((r) => setTimeout(r, 0));

async function openOverlay() {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: 7,
      allImages: [{ id: 7, format: "jpg", tags: [] }],
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

describe("ImageOverlay - the undo binding", () => {
  it("undoes on Ctrl+Z while the lightbox is open", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    await openOverlay();

    const event = press("z", { ctrlKey: true });
    expect(undo).toHaveBeenCalledTimes(1);
    expect(event.defaultPrevented).toBe(true);
  });

  it("accepts Meta+Z, so the binding is not platform-specific", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    await openOverlay();

    press("z", { metaKey: true });
    expect(undo).toHaveBeenCalledTimes(1);
  });

  it("redoes on Ctrl+Y and on Ctrl+Shift+Z", async () => {
    const store = useOperationStore();
    const redo = vi.spyOn(store, "redo").mockResolvedValue(null);
    await openOverlay();

    press("y", { ctrlKey: true });
    press("Z", { ctrlKey: true, shiftKey: true });
    expect(redo).toHaveBeenCalledTimes(2);
  });

  it("does not walk the stack on a held key", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    await openOverlay();

    press("z", { ctrlKey: true, repeat: true });
    expect(undo).not.toHaveBeenCalled();
  });

  it("leaves a text field its own native undo", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    await openOverlay();

    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    press("z", { ctrlKey: true, target: input });
    expect(undo).not.toHaveBeenCalled();

    input.remove();
  });

  it("still zooms on a bare z", async () => {
    // The regression the base lane fixed: a modifier-blind `z` made Ctrl+Z zoom
    // instead of undo. Both halves have to keep working. (Updated for the
    // continuous-zoom rework: the zoom-hud is gone, the toolbar button's live
    // percent readout is the visible zoom state, and Z snaps fit ↔ 100%.)
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const wrapper = await openOverlay();

    // Give jsdom the geometry the zoom measures: 800×600 over 1600×1200 → fit 50%.
    const canvasEl = wrapper.find(".overlay-canvas").element;
    Object.defineProperty(canvasEl, "clientWidth", { value: 800 });
    Object.defineProperty(canvasEl, "clientHeight", { value: 600 });
    const img = wrapper.find(".overlay-img");
    Object.defineProperty(img.element, "naturalWidth", { value: 1600 });
    Object.defineProperty(img.element, "naturalHeight", { value: 1200 });
    Object.defineProperty(img.element, "clientWidth", { value: 800 });
    Object.defineProperty(img.element, "clientHeight", { value: 600 });
    await img.trigger("load");

    expect(wrapper.find(".zoom-btn-label").text()).toBe("50%");
    press("z");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".zoom-btn-label").text()).toBe("100%");
    expect(undo).not.toHaveBeenCalled();
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
  });
});

describe("ImageOverlay - the narration on this surface", () => {
  it("renders the receipt in the lightbox's own chrome", async () => {
    const store = useOperationStore();
    const wrapper = await openOverlay();
    store.showReceipt(store.buildReceipt(op(), "did"));
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".overlay-receipt").exists()).toBe(true);
    expect(wrapper.find(".overlay-receipt .r-text").text()).toBe(
      "Added tag 'portrait'",
    );
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
  });
});
