// Undo inside the lightbox (owner ruling, 2026-07-29).
//
// The lightbox has its own GUI, so it gets its own narration rather than the
// grid pill promoted above the modal layer. Two things had to be true for that
// to be honest, and these tests pin both:
//
//   1. Ctrl+Z actually reaches the operation stack while the lightbox is open.
//      It did not before: ImageOverlay registers its window listener in its own
//      `onMounted` and calls `stopImmediatePropagation()`, and a child mounts
//      before its parent, so App's global binding never saw the keystroke.
//   2. The result is narrated ON the lightbox, honouring the receipt contract
//      (timings, pause, one at a time, reduced motion) while adding the one
//      thing the grid does not need: how far past the visible picture the step
//      reached.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    apiClient: { get: vi.fn(), post: vi.fn() },
    appendShareToken: (u) => u,
    isReadOnly: ref(false),
    setRequestClientId: vi.fn(),
  };
});

vi.mock("../../api/operations", () => ({
  listOperations: vi.fn().mockResolvedValue([]),
  getUndoState: vi.fn().mockResolvedValue({ can_undo: false, can_redo: false }),
  undoLastOperation: vi.fn().mockResolvedValue({ operations: [] }),
  redoOperation: vi.fn().mockResolvedValue({ operations: [] }),
  undoOperation: vi.fn().mockResolvedValue({ operations: [] }),
  undoBatch: vi.fn().mockResolvedValue({ operations: [] }),
}));

import OverlayActionReceipt from "./OverlayActionReceipt.vue";
import ActionReceipt from "./ActionReceipt.vue";
import { useOperationStore } from "../../stores/useOperationStore";

const globalOpts = { global: { stubs: { "v-icon": true } } };

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

function mountWith(operation, mode = "did", steps = 1, props = {}) {
  const store = useOperationStore();
  const wrapper = mount(OverlayActionReceipt, { ...globalOpts, props });
  store.showReceipt(store.buildReceipt(operation, mode, steps));
  return { store, wrapper };
}

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
});

describe("OverlayActionReceipt - states", () => {
  it("renders nothing while there is no receipt", () => {
    const wrapper = mount(OverlayActionReceipt, globalOpts);
    expect(wrapper.find(".overlay-receipt").exists()).toBe(false);
  });

  it("shows the summary, an Undo button and the shortcut hint", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-text").text()).toBe("Added tag 'portrait'");
    expect(wrapper.find(".r-btn").text()).toContain("Undo");
    expect(wrapper.find(".kbdhint").attributes("aria-hidden")).toBe("true");
  });

  it("flips in place to the undone state and offers Redo", async () => {
    const { wrapper } = mountWith(op(), "undone");
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-text").text()).toBe("Undone: Added tag 'portrait'");
    expect(wrapper.find(".r-btn").text()).toContain("Redo");
    // One pill, never a second stacked beside it.
    expect(wrapper.findAll(".overlay-receipt")).toHaveLength(1);
  });

  it("states the limit instead of a dead button when the action is one-way", async () => {
    const { wrapper } = mountWith(op({ undoable: false }), "blocked");
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-limit").text()).toBe("Can't be undone");
    expect(wrapper.find(".r-btn").exists()).toBe(false);
  });

  it("names what the button will undo, not just the verb", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".r-btn").attributes("aria-label")).toBe(
      "Undo: Added tag 'portrait'",
    );
  });
});

describe("OverlayActionReceipt - the scope clause", () => {
  // The lightbox shows one picture while Ctrl+Z can revert an action across
  // thousands. The clause is derived from the COUNT only, so navigating to the
  // next picture cannot falsify it.
  it("says how far past the visible picture a bulk step reached", async () => {
    const { wrapper } = mountWith(op({ target_count: 2700 }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".r-scope").text()).toBe(
      "Across 2,700 pictures, not just this one",
    );
  });

  it("says so in the past tense once the step has been reverted", async () => {
    const { wrapper } = mountWith(op({ target_count: 2700 }), "undone");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".r-scope").text()).toBe(
      "Reverted across 2,700 pictures, not just this one",
    );
  });

  it("stays silent on a single-picture step", async () => {
    const { wrapper } = mountWith(op({ target_count: 1 }));
    await wrapper.vm.$nextTick();
    // "Just this picture" would be noise on the common case, and false the
    // moment the user navigates away.
    expect(wrapper.find(".r-scope").exists()).toBe(false);
  });

  it("never claims to be about the picture on screen", async () => {
    const { wrapper } = mountWith(op({ target_count: 2700 }));
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).not.toMatch(/this picture\b/);
    expect(wrapper.text()).not.toMatch(/this image\b/);
  });
});

describe("OverlayActionReceipt - the receipt contract", () => {
  it("hands CSS the same window the store's timer uses", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".overlay-receipt").attributes("style")).toContain(
      "--r-drain-dur: 5000ms",
    );
  });

  it("uses the longer window for a destructive action", async () => {
    const { wrapper } = mountWith(op({ op_type: "pictures.scrapheap.move" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".overlay-receipt").attributes("style")).toContain(
      "--r-drain-dur: 8000ms",
    );
  });

  it("freezes the countdown on hover and on focus (WCAG 2.2.1)", async () => {
    const { store, wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();

    await wrapper.find(".overlay-receipt").trigger("mouseenter");
    vi.advanceTimersByTime(60000);
    expect(store.receipt).not.toBeNull();

    await wrapper.find(".overlay-receipt").trigger("mouseleave");
    vi.advanceTimersByTime(5000);
    expect(store.receipt).toBeNull();
  });

  it("marks itself busy with aria-disabled, never the attribute", async () => {
    const { store, wrapper } = mountWith(op());
    store.busy = true;
    await wrapper.vm.$nextTick();
    const button = wrapper.find(".r-btn");
    expect(button.attributes("disabled")).toBeUndefined();
    expect(button.attributes("aria-disabled")).toBe("true");
  });

  it("undoes and redoes through the same store the grid uses", async () => {
    const { store, wrapper } = mountWith(op());
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    await wrapper.vm.$nextTick();
    await wrapper.find(".r-btn").trigger("click");
    expect(undo).toHaveBeenCalledTimes(1);

    const { store: s2, wrapper: w2 } = mountWith(op(), "undone");
    const redo = vi.spyOn(s2, "redo").mockResolvedValue(null);
    await w2.vm.$nextTick();
    await w2.find(".r-btn").trigger("click");
    expect(redo).toHaveBeenCalledTimes(1);
  });

  it("widens its band to the whole canvas while the chrome is hidden", async () => {
    const { wrapper } = mountWith(op(), "did", 1, { chromeHidden: true });
    await wrapper.vm.$nextTick();
    // The rail and the sidebar keep their width while hidden, so the band has
    // to collapse or the pill sits off-centre against a rail nobody can see.
    expect(
      wrapper
        .find('[data-testid="overlay-action-receipt-slot"]')
        .classes("chrome-hidden"),
    ).toBe(true);
  });
});

describe("the single live region", () => {
  it("is the grid's, and the lightbox adds no second one", async () => {
    // The lightbox does not `inert` or `aria-hidden` the grid, so the grid's
    // persistent region still speaks from underneath. A second one here would
    // be guaranteed double-speak.
    const store = useOperationStore();
    const grid = mount(ActionReceipt, {
      ...globalOpts,
      props: { pillHidden: true },
    });
    const lightbox = mount(OverlayActionReceipt, globalOpts);
    store.showReceipt(store.buildReceipt(op(), "did"));
    await grid.vm.$nextTick();
    await lightbox.vm.$nextTick();

    expect(lightbox.findAll('[role="status"]')).toHaveLength(0);
    expect(grid.findAll('[role="status"]')).toHaveLength(1);

    // …and the grid's PILL is out of the way, so the same receipt is not drawn
    // at two positions with the lightbox's backdrop between them.
    expect(grid.find(".receipt").exists()).toBe(false);
    expect(lightbox.find(".overlay-receipt").exists()).toBe(true);

    vi.advanceTimersByTime(400);
    await grid.vm.$nextTick();
    expect(
      grid.find('[data-testid="action-receipt-announcement"]').text(),
    ).toBe("Added tag 'portrait'");
  });
});
