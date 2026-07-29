// The action receipt pill — the transient half of undo/redo.
//
// The store owns the timers and the API (covered in useOperationStore.test.js);
// these tests pin the contracts the PILL is responsible for: which state it
// renders, that the drain window it hands CSS matches the store's own timer,
// that hover and focus freeze that timer (WCAG 2.2.1), and that a receipt
// replaced in place remounts rather than mutating a live region under the
// screen reader.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    apiClient: { get: vi.fn(), post: vi.fn() },
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

import ActionReceipt from "./ActionReceipt.vue";
import { useOperationStore } from "../../stores/useOperationStore";

const globalOpts = { global: { stubs: { "v-icon": true } } };

function op(overrides = {}) {
  return {
    id: 10,
    batch_id: null,
    created_at: "2026-07-29T12:00:00",
    op_type: "pictures.tags.add",
    target_count: 12,
    origin_client_id: "me",
    undoable: true,
    status: "applied",
    summary: "Added tag 'portrait'",
    ...overrides,
  };
}

/** Mount the pill with one receipt already raised in the given mode. */
function mountWith(operation, mode = "did", steps = 1) {
  const store = useOperationStore();
  const wrapper = mount(ActionReceipt, globalOpts);
  store.showReceipt(store.buildReceipt(operation, mode, steps));
  return { store, wrapper };
}

beforeEach(() => {
  vi.useFakeTimers();
  setActivePinia(createPinia());
});

describe("ActionReceipt — states", () => {
  it("renders nothing while there is no receipt", () => {
    mount(ActionReceipt, globalOpts);
    expect(document.querySelector(".receipt")).toBeNull();
  });

  it("shows the summary, an Undo button and the shortcut hint by default", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-text").text()).toBe("Added tag 'portrait' · 12");
    expect(wrapper.find(".r-btn").text()).toContain("Undo");
    expect(wrapper.find(".kbdhint").exists()).toBe(true);
    // The hint is decorative: announcing "Ctrl Z" on every action would make
    // the live region unusable.
    expect(wrapper.find(".kbdhint").attributes("aria-hidden")).toBe("true");
  });

  it("is a polite live region so the outcome is announced, once", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();
    const pill = wrapper.find(".receipt");
    expect(pill.attributes("role")).toBe("status");
    expect(pill.attributes("aria-live")).toBe("polite");
  });

  it("shows the coalesced +N when the step carries batch siblings", async () => {
    const store = useOperationStore();
    store.operations = [
      op({ id: 12, batch_id: "b1" }),
      op({ id: 11, batch_id: "b1" }),
      op({ id: 10, batch_id: "b1" }),
    ];
    const wrapper = mount(ActionReceipt, globalOpts);
    store.showReceipt(
      store.buildReceipt(op({ id: 12, batch_id: "b1" }), "did"),
    );
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-more").text()).toBe("+2");
  });

  it("omits the +N for a lone step", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".r-more").exists()).toBe(false);
  });

  it("flips in place to the undone state and offers Redo", async () => {
    const { wrapper } = mountWith(op(), "undone");
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-text").text()).toBe(
      "Undone — Added tag 'portrait' · 12",
    );
    expect(wrapper.find(".r-btn").text()).toContain("Redo");
    // One pill, never a second stacked below it.
    expect(wrapper.findAll(".receipt")).toHaveLength(1);
  });

  it("says how far a multi-step undo went", async () => {
    const { wrapper } = mountWith(op(), "undone", 3);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".r-text").text()).toBe(
      "Undone 3 steps — Added tag 'portrait' · 12",
    );
  });

  it("states the limit instead of a dead button when the action is one-way", async () => {
    const { wrapper } = mountWith(op({ undoable: false }), "blocked");
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-limit").text()).toBe("Can't be undone");
    expect(wrapper.find(".r-btn").exists()).toBe(false);
  });
});

describe("ActionReceipt — the drain window", () => {
  it("hands CSS the same window the store's timer uses", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".receipt").attributes("style")).toContain(
      "--r-drain-dur: 5000ms",
    );
  });

  it("uses the longer window for a destructive action", async () => {
    const { wrapper } = mountWith(op({ op_type: "pictures.scrapheap.move" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".receipt").attributes("style")).toContain(
      "--r-drain-dur: 8000ms",
    );
  });
});

describe("ActionReceipt — pause on hover and focus (WCAG 2.2.1)", () => {
  it("freezes the countdown while the pointer is on the pill", async () => {
    const { store, wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();

    await wrapper.find(".receipt").trigger("mouseenter");
    vi.advanceTimersByTime(60000);
    expect(store.receipt).not.toBeNull();

    await wrapper.find(".receipt").trigger("mouseleave");
    vi.advanceTimersByTime(5000);
    expect(store.receipt).toBeNull();
  });

  it("freezes the countdown while focus is inside the pill", async () => {
    const { store, wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();

    await wrapper.find(".receipt").trigger("focusin");
    vi.advanceTimersByTime(60000);
    expect(store.receipt).not.toBeNull();

    await wrapper.find(".receipt").trigger("focusout");
    vi.advanceTimersByTime(5000);
    expect(store.receipt).toBeNull();
  });
});

describe("ActionReceipt — the action button", () => {
  it("undoes from the default state", async () => {
    const { store, wrapper } = mountWith(op());
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    await wrapper.vm.$nextTick();

    await wrapper.find(".r-btn").trigger("click");
    expect(undo).toHaveBeenCalledTimes(1);
  });

  it("redoes from the undone state", async () => {
    const { store, wrapper } = mountWith(op(), "undone");
    const redo = vi.spyOn(store, "redo").mockResolvedValue(null);
    await wrapper.vm.$nextTick();

    await wrapper.find(".r-btn").trigger("click");
    expect(redo).toHaveBeenCalledTimes(1);
  });
});

describe("ActionReceipt — placement", () => {
  it("lifts clear of the selection pill by the measured height it is given", async () => {
    const store = useOperationStore();
    const wrapper = mount(ActionReceipt, {
      ...globalOpts,
      props: { liftPx: 62 },
    });
    store.showReceipt(store.buildReceipt(op(), "did"));
    await wrapper.vm.$nextTick();

    // The lift is padding on the pointer-transparent wrapper, so the wrapper's
    // measured box is the FULL height this component occupies on the bottom
    // edge — which is what the anchor registry reports to the notice stack.
    expect(
      wrapper.find('[data-testid="action-receipt-slot"]').attributes("style"),
    ).toContain("padding-bottom: 62px");
  });

  it("sits flush on the bottom edge when nothing else is parked there", () => {
    const wrapper = mount(ActionReceipt, globalOpts);
    expect(
      wrapper.find('[data-testid="action-receipt-slot"]').attributes("style"),
    ).toContain("padding-bottom: 0px");
  });
});
