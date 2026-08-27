// The action receipt pill - the transient half of undo/redo.
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
    API_BASE_URL: "/api/v1",
    onSessionReset: () => () => {},
    sessionContext: { value: null },
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

describe("ActionReceipt - states", () => {
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

  it("announces through one persistent region, not the pill itself", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();

    // The pill carries no live region: a region created at the same moment as
    // its content announces unreliably, and a burst would create one region
    // per action and queue a backlog of stale sentences.
    const pill = wrapper.find(".receipt");
    expect(pill.attributes("role")).toBeUndefined();
    expect(pill.attributes("aria-live")).toBeUndefined();

    const region = wrapper.find('[data-testid="action-receipt-announcement"]');
    expect(region.attributes("role")).toBe("status");
    expect(region.attributes("aria-live")).toBe("polite");
    expect(region.attributes("aria-atomic")).toBe("true");
  });

  it("speaks once for a burst rather than reading every step aloud", async () => {
    const { store, wrapper } = mountWith(op({ summary: "First" }));
    await wrapper.vm.$nextTick();
    const region = () =>
      wrapper.find('[data-testid="action-receipt-announcement"]').text();
    expect(region()).toBe("");

    store.showReceipt(store.buildReceipt(op({ summary: "Second" }), "did"));
    await wrapper.vm.$nextTick();
    vi.advanceTimersByTime(200);
    store.showReceipt(store.buildReceipt(op({ summary: "Third" }), "did"));
    await wrapper.vm.$nextTick();
    expect(region()).toBe("");

    vi.advanceTimersByTime(400);
    await wrapper.vm.$nextTick();
    expect(region()).toBe("Third \u00b7 12");
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
      "Undone: Added tag 'portrait' · 12",
    );
    expect(wrapper.find(".r-btn").text()).toContain("Redo");
    // One pill, never a second stacked below it.
    expect(wrapper.findAll(".receipt")).toHaveLength(1);
  });

  it("says how far a multi-step undo went", async () => {
    const { wrapper } = mountWith(op(), "undone", 3);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".r-text").text()).toBe(
      "Undone 3 steps: Added tag 'portrait' · 12",
    );
  });

  // What an action deliberately left alone rides the SAME pill as what it did,
  // as a second sentence, rather than a notice competing with it. Keep cover
  // only is the first consumer: it skips a whole stack when a locked set or a
  // character link would otherwise lose data.
  it("carries the action's second sentence on the same pill", async () => {
    const store = useOperationStore();
    const wrapper = mount(ActionReceipt, globalOpts);
    store.noteNextReceipt(
      "stack.keep_cover_only",
      "2 stacks skipped: held by a locked picture set.",
    );
    store.showReceipt(
      store.buildReceipt(
        op({
          op_type: "stack.keep_cover_only",
          summary: "Kept the cover of 3 stacks · 414 pictures to the Scrapheap",
          target_count: 1,
        }),
        "did",
      ),
    );
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-text").text()).toBe(
      "Kept the cover of 3 stacks · 414 pictures to the Scrapheap. " +
        "2 stacks skipped: held by a locked picture set.",
    );
  });

  // Once the work is taken back the note describes nothing that still stands.
  it("drops the second sentence when the pill flips to undone", async () => {
    const store = useOperationStore();
    const wrapper = mount(ActionReceipt, globalOpts);
    store.noteNextReceipt("stack.keep_cover_only", "2 stacks skipped.");
    store.showReceipt(
      store.buildReceipt(
        op({
          op_type: "stack.keep_cover_only",
          summary: "Kept the cover of 3 stacks",
          target_count: 1,
        }),
        "undone",
      ),
    );
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-text").text()).toBe(
      "Undone: Kept the cover of 3 stacks",
    );
  });

  it("states the limit instead of a dead button when the action is one-way", async () => {
    const { wrapper } = mountWith(op({ undoable: false }), "blocked");
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-limit").text()).toBe("Can't be undone");
    expect(wrapper.find(".r-btn").exists()).toBe(false);
  });
});

describe("ActionReceipt - the drain window", () => {
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

describe("ActionReceipt - pause on hover and focus (WCAG 2.2.1)", () => {
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

  it("releases a held pause when the surface unmounts mid-hover", async () => {
    // The regression this pins: a view change under the pointer unmounted the
    // pill while paused, no mouseleave ever fired, and the frozen receipt
    // survived in the store to resurface on whichever surface rendered next.
    const { store, wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();

    await wrapper.find(".receipt").trigger("mouseenter");
    wrapper.unmount();
    vi.advanceTimersByTime(60000);
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

describe("ActionReceipt - the action button", () => {
  it("keeps the keyboard on the button when the pill flips to Redo", async () => {
    // Attached to the document: `document.activeElement` only tracks a focus()
    // call on an element that is actually in the page.
    const store = useOperationStore();
    const wrapper = mount(ActionReceipt, {
      ...globalOpts,
      attachTo: document.body,
    });
    store.showReceipt(store.buildReceipt(op(), "did"));
    // The pill is REPLACED on flip; without focus restoration the user who
    // reached Undo by keyboard is dropped to <body> (WCAG 2.4.3).
    vi.spyOn(store, "undo").mockImplementation(async () => {
      store.showReceipt(store.buildReceipt(op(), "undone"));
      return {};
    });
    await wrapper.vm.$nextTick();

    const button = wrapper.find(".r-btn");
    button.element.focus();
    await button.trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".r-btn").text()).toContain("Redo");
    expect(document.activeElement).toBe(wrapper.find(".r-btn").element);
  });

  it("marks itself busy with aria-disabled, never the attribute", async () => {
    const { store, wrapper } = mountWith(op());
    store.busy = true;
    await wrapper.vm.$nextTick();
    const button = wrapper.find(".r-btn");
    // A `disabled` button loses focus to <body> the instant it is disabled.
    expect(button.attributes("disabled")).toBeUndefined();
    expect(button.attributes("aria-disabled")).toBe("true");
  });

  it("advertises its shortcut in a way that survives the hidden keycaps", async () => {
    const { wrapper } = mountWith(op());
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".r-btn").attributes("aria-keyshortcuts")).toMatch(
      /Control\+Z|Meta\+Z/,
    );
  });

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

describe("ActionReceipt - placement", () => {
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
    // edge - which is what the anchor registry reports to the notice stack.
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
