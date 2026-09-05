// The toolbar history control - the persistent half of undo/redo.
//
// These tests pin what the CONTROL owns: enablement and its labels, that the
// buttons stay reachable when there is nothing to do (the design's "always
// tabbable" rule, which the native `disabled` attribute would break), the
// History popover's row states, and that the hover/focus preview and the
// undo-to-step call describe the same range.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { h } from "vue";
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

import UndoControl from "./UndoControl.vue";
import { isReadOnly as readOnlyRef } from "../../utils/apiClient";
import { getUndoState, listOperations } from "../../api/operations";
import { useOperationStore } from "../../stores/useOperationStore";

// Vuetify is not installed in the test app (importing it pulls raw .css), so
// `v-menu` is stubbed with the two behaviours this component relies on: the
// activator slot receives props carrying the toggle, and the default slot is
// rendered inline (rather than teleported) while the menu is open.
const VMenuStub = {
  name: "VMenu",
  props: {
    modelValue: { type: Boolean, default: false },
    location: { type: String, default: "" },
    origin: { type: String, default: "" },
  },
  emits: ["update:modelValue"],
  setup(props, { slots, emit }) {
    return () =>
      h(
        "div",
        {
          class: "v-menu-stub",
          "data-location": props.location,
          "data-origin": props.origin,
        },
        [
          slots.activator?.({
            props: {
              onClick: () => emit("update:modelValue", !props.modelValue),
            },
          }),
          props.modelValue ? slots.default?.() : null,
        ],
      );
  },
};

const globalOpts = {
  global: { stubs: { "v-icon": true, "v-menu": VMenuStub } },
};

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

function seed(store, rows, state = {}) {
  const undoState = {
    can_undo: rows.some((r) => r.status === "applied"),
    can_redo: rows.some((r) => r.status === "undone"),
    next_undo: rows.find((r) => r.status === "applied") ?? null,
    next_redo: rows.find((r) => r.status === "undone") ?? null,
  };
  store.operations = rows;
  store.canUndo = undoState.can_undo;
  store.canRedo = undoState.can_redo;
  store.nextUndo = undoState.next_undo;
  store.nextRedo = undoState.next_redo;
  Object.assign(store, state);
  // Opening the popover re-reads the log (Vuetify keeps menu content mounted,
  // so the component drives its read off the open flag). Serve the same rows,
  // or that read would overwrite the seed with an empty stack.
  listOperations.mockResolvedValue(rows);
  getUndoState.mockResolvedValue(undoState);
}

/** Mount with the popover already open, so the list is in the DOM. */
async function mountOpen(rows) {
  const store = useOperationStore();
  seed(store, rows);
  const wrapper = mount(UndoControl, globalOpts);
  await wrapper.find(".uc-btn--chevron").trigger("click");
  await wrapper.vm.$nextTick();
  await wrapper.vm.$nextTick();
  return { store, wrapper };
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("UndoControl - enablement", () => {
  it("marks both buttons unavailable when there is no history", () => {
    useOperationStore();
    const wrapper = mount(UndoControl, globalOpts);
    expect(wrapper.find(".uc-btn--undo").attributes("aria-disabled")).toBe(
      "true",
    );
    expect(wrapper.find(".uc-btn--redo").attributes("aria-disabled")).toBe(
      "true",
    );
  });

  it("keeps the buttons focusable and tooltip-bearing when unavailable", () => {
    useOperationStore();
    const wrapper = mount(UndoControl, globalOpts);
    const undo = wrapper.find(".uc-btn--undo");
    // `aria-disabled`, never the native attribute: a `disabled` button is not
    // in the tab order and shows no tooltip, and the design requires both.
    expect(undo.attributes("disabled")).toBeUndefined();
    expect(undo.attributes("title")).toContain("Nothing to undo");
    expect(wrapper.find(".uc-btn--redo").attributes("title")).toContain(
      "Nothing to redo",
    );
  });

  it("says when the undo target came from somewhere else", () => {
    const store = useOperationStore();
    seed(store, [op({ origin_client_id: "another-tab" })]);
    const wrapper = mount(UndoControl, globalOpts);
    // External operations update the stack silently, so the control has to say
    // so before it reverts something this user never did.
    expect(wrapper.find(".uc-btn--undo").attributes("title")).toContain(
      "Changed elsewhere:",
    );
  });

  it("names the exact step and its shortcut once there is history", () => {
    const store = useOperationStore();
    seed(store, [op()]);
    const wrapper = mount(UndoControl, globalOpts);

    const title = wrapper.find(".uc-btn--undo").attributes("title");
    expect(title).toContain("Added tag 'portrait' · 12");
    expect(title).toMatch(/Ctrl\+Z|⌘\+Z/);
    expect(wrapper.find(".uc-btn--undo").attributes("aria-disabled")).toBe(
      "false",
    );
  });

  it("does not call the server when the button is unavailable", async () => {
    const store = useOperationStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const wrapper = mount(UndoControl, globalOpts);

    await wrapper.find(".uc-btn--undo").trigger("click");
    expect(undo).not.toHaveBeenCalled();
  });

  it("undoes and redoes when they are available", async () => {
    const store = useOperationStore();
    seed(store, [op(), op({ id: 9, status: "undone" })]);
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const redo = vi.spyOn(store, "redo").mockResolvedValue(null);
    const wrapper = mount(UndoControl, globalOpts);

    await wrapper.find(".uc-btn--undo").trigger("click");
    await wrapper.find(".uc-btn--redo").trigger("click");
    expect(undo).toHaveBeenCalledTimes(1);
    expect(redo).toHaveBeenCalledTimes(1);
  });
});

// A share-token session keeps the control visible and inert rather than
// unmounting it: the read-only demo has to show that undo exists. Nothing is
// softened by an empty stack here - /operations* is owner-only, so the store
// never read one, and the affordances must say that instead of implying the
// library has no history.
describe("UndoControl - a read-only session", () => {
  beforeEach(() => {
    readOnlyRef.value = true;
  });
  afterEach(() => {
    readOnlyRef.value = false;
  });

  it("inerts both buttons and says why, even with a stack already in hand", () => {
    const store = useOperationStore();
    seed(store, [op()]);
    const wrapper = mount(UndoControl, globalOpts);

    const undo = wrapper.find(".uc-btn--undo");
    const redo = wrapper.find(".uc-btn--redo");
    expect(undo.attributes("aria-disabled")).toBe("true");
    expect(redo.attributes("aria-disabled")).toBe("true");
    expect(undo.attributes("title")).toBe(
      "Undo is only available in your own library",
    );
    expect(redo.attributes("title")).toBe(
      "Redo is only available in your own library",
    );
    // Still tabbable and still tooltip-bearing, the same rule as "nothing to
    // undo": the tooltip is the only place the reason is stated.
    expect(undo.attributes("disabled")).toBeUndefined();
  });

  it("never calls the server", async () => {
    const store = useOperationStore();
    seed(store, [op(), op({ id: 9, status: "undone" })]);
    const undo = vi.spyOn(store, "undo").mockResolvedValue(null);
    const redo = vi.spyOn(store, "redo").mockResolvedValue(null);
    const wrapper = mount(UndoControl, globalOpts);

    await wrapper.find(".uc-btn--undo").trigger("click");
    await wrapper.find(".uc-btn--redo").trigger("click");
    expect(undo).not.toHaveBeenCalled();
    expect(redo).not.toHaveBeenCalled();
  });

  it("explains the History popover instead of counting steps", async () => {
    const { wrapper } = await mountOpen([]);
    expect(wrapper.find(".uc-empty").text()).toContain(
      "History is only available in your own library",
    );
    // A tally would be a claim about the library, not a count of what is shown.
    expect(wrapper.find(".uc-count").exists()).toBe(false);
    // The footer teaches a gesture that has no list to act on here.
    expect(wrapper.find(".tbm-footer").exists()).toBe(false);
  });
});

describe("UndoControl - the History popover", () => {
  it("anchors its viewport-clamped panel and caret to the History button", async () => {
    const { wrapper } = await mountOpen([op({ id: 13 })]);
    const menu = wrapper.find(".v-menu-stub");

    // History lives at the toolbar's right edge. End anchoring lets Vuetify
    // keep the wide panel on-screen without leaving the caret at panel-left.
    expect(menu.attributes("data-location")).toBe("bottom end");
    expect(menu.attributes("data-origin")).toBe("top end");
    expect(wrapper.find(".tbm-caret").classes()).toContain(
      "tbm-caret--icon-center-end",
    );
  });

  it("lists the stack newest first with the redo side above it", async () => {
    const { wrapper } = await mountOpen([
      op({ id: 13, status: "undone", summary: "Scored" }),
      op({ id: 12, summary: "Assigned Walter" }),
      op({ id: 11, summary: "Added tag 'studio'" }),
    ]);

    const rows = wrapper.findAll(".uc-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].classes()).toContain("uc-row--future");
    expect(rows[1].text()).toContain("Assigned Walter");
    expect(rows[2].text()).toContain("Added tag 'studio'");
  });

  it("shows undone steps struck through and inert", async () => {
    const { wrapper } = await mountOpen([op({ id: 13, status: "undone" })]);
    const row = wrapper.find(".uc-row--future");
    // Not clickable: the redo side is reached with the Redo button, not by
    // clicking forward through the list.
    expect(row.attributes("disabled")).toBeDefined();
  });

  it("counts the steps in the header and prompts in the footer", async () => {
    const { wrapper } = await mountOpen([op({ id: 12 }), op({ id: 11 })]);
    expect(wrapper.find(".uc-count").text()).toBe("2 steps");
    expect(wrapper.find(".tbm-footer").text()).toContain(
      "Choose a step to undo back to it",
    );
  });

  it("says so when the stack is empty rather than showing a blank panel", async () => {
    const { wrapper } = await mountOpen([]);
    expect(wrapper.find(".uc-empty").exists()).toBe(true);
    expect(wrapper.find(".uc-count").text()).toBe("0 steps");
  });
});

describe("UndoControl - the hover preview", () => {
  it("highlights every step that would be undone, not only the hovered one", async () => {
    const { wrapper } = await mountOpen([
      op({ id: 13 }),
      op({ id: 12 }),
      op({ id: 11 }),
    ]);

    await wrapper.findAll(".uc-row")[1].trigger("mouseenter");
    const flagged = wrapper
      .findAll(".uc-row")
      .map((r) => r.classes().includes("uc-row--willundo"));
    expect(flagged).toEqual([true, true, false]);
    expect(wrapper.find(".tbm-footer").text()).toContain("Undo 2 steps");
  });

  it("previews on focus too, so the keyboard sees the same range", async () => {
    const { wrapper } = await mountOpen([op({ id: 13 }), op({ id: 12 })]);

    await wrapper.findAll(".uc-row")[1].trigger("focus");
    expect(wrapper.find(".tbm-footer").text()).toContain("Undo 2 steps");
    // Singular for one step: "Undo 1 steps" is the kind of copy that gets read.
    await wrapper.findAll(".uc-row")[0].trigger("focus");
    expect(wrapper.find(".tbm-footer").text()).toContain("Undo 1 step");
  });

  it("clears the preview only when the pointer leaves the whole panel", async () => {
    const { wrapper } = await mountOpen([op({ id: 13 }), op({ id: 12 })]);
    await wrapper.findAll(".uc-row")[1].trigger("mouseenter");
    // Moving from a row toward the footer must not wipe the readout the user
    // is moving to read, so the reset lives on the panel, not on the list.
    await wrapper.find(".uc-list").trigger("mouseleave");
    expect(wrapper.findAll(".uc-row--willundo")).toHaveLength(2);

    await wrapper.find(".uc-panel").trigger("mouseleave");
    expect(wrapper.findAll(".uc-row--willundo")).toHaveLength(0);
  });

  it("keeps the preview pinned to the step, not to its position", async () => {
    const { store, wrapper } = await mountOpen([
      op({ id: 13 }),
      op({ id: 12 }),
      op({ id: 11 }),
    ]);
    await wrapper.findAll(".uc-row")[1].trigger("mouseenter");
    expect(wrapper.find(".tbm-footer").text()).toContain("Undo 2 steps");

    // Another tab records a step while the popover is open. A positional index
    // would now describe a different range than the user is looking at.
    store.operations = [op({ id: 14 }), ...store.operations];
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".tbm-footer").text()).toContain("Undo 3 steps");
    expect(wrapper.findAll(".uc-row--willundo")).toHaveLength(3);
  });
});

describe("UndoControl - the History popover stays reachable without its chevron", () => {
  // The shared toolbar collapse hides the chevron at the ≤480 container step
  // (jsdom does not evaluate container queries - the CSS being shared with
  // both bars is the coverage for the step itself). What must hold here is
  // the CONTRACT that replaces the chevron: the exposed openHistory() opens
  // the same popover, and the aria state on the (hidden) activator follows,
  // so the hosts' ⋯ "History…" row is a full substitute.
  it("openHistory() opens the popover with its rows and aria state", async () => {
    const store = useOperationStore();
    seed(store, [op({ id: 9 })]);
    const wrapper = mount(UndoControl, globalOpts);

    wrapper.vm.openHistory();
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".uc-btn--chevron").attributes("aria-expanded")).toBe(
      "true",
    );
    expect(wrapper.findAll(".uc-row")).toHaveLength(1);
    wrapper.unmount();
  });
});

describe("UndoControl - undo to a step", () => {
  it("undoes back to the clicked step and closes the popover", async () => {
    const { store, wrapper } = await mountOpen([
      op({ id: 13 }),
      op({ id: 12 }),
      op({ id: 11 }),
    ]);
    const undoTo = vi.spyOn(store, "undoTo").mockResolvedValue(2);

    await wrapper.findAll(".uc-row")[1].trigger("click");
    expect(undoTo).toHaveBeenCalledWith(12);
    expect(wrapper.find(".uc-btn--chevron").attributes("aria-expanded")).toBe(
      "false",
    );
  });

  it("does nothing when an undone row is activated", async () => {
    const { store, wrapper } = await mountOpen([
      op({ id: 13, status: "undone" }),
      op({ id: 12 }),
    ]);
    const undoTo = vi.spyOn(store, "undoTo").mockResolvedValue(0);

    await wrapper.find(".uc-row--future").trigger("click");
    expect(undoTo).not.toHaveBeenCalled();
  });
});

describe("UndoControl - keyboard reachability", () => {
  it("renders every control as a real button in the natural tab order", async () => {
    const { wrapper } = await mountOpen([op({ id: 13 }), op({ id: 12 })]);
    const buttons = wrapper.findAll("button");
    // undo + redo + chevron + 2 history rows, all real buttons, none of which
    // carries a tabindex that would pull it out of document order.
    expect(buttons.length).toBe(5);
    for (const button of buttons) {
      expect(button.attributes("type")).toBe("button");
      expect(button.attributes("tabindex")).toBeUndefined();
    }
  });

  it("labels each history row with what activating it would do, and how far", async () => {
    const { wrapper } = await mountOpen([op({ id: 13 }), op({ id: 12 })]);
    const labels = wrapper
      .findAll(".uc-row")
      .map((r) => r.attributes("aria-label"));
    // The step count rides the row rather than a chatty live region in the
    // footer, which would read one sentence per row swept.
    expect(labels[0]).toBe("Undo back to: Added tag 'portrait' · 12 (1 step)");
    expect(labels[1]).toBe("Undo back to: Added tag 'portrait' · 12 (2 steps)");
  });

  it("activates a row from the keyboard", async () => {
    const { store, wrapper } = await mountOpen([
      op({ id: 13 }),
      op({ id: 12 }),
    ]);
    const undoTo = vi.spyOn(store, "undoTo").mockResolvedValue(1);
    // Vuetify's menu preventDefaults Enter on its content, which suppresses
    // the synthesized click, so the row has to handle Enter itself.
    await wrapper.findAll(".uc-row")[0].trigger("keydown.enter");
    expect(undoTo).toHaveBeenCalledWith(13);
  });

  it("labels the popover as a dialog, not as a menu it does not implement", async () => {
    const { wrapper } = await mountOpen([op({ id: 13 })]);
    // No roving arrow-key navigation is implemented, so claiming role=menu
    // would promise a keyboard contract that is not owed and not honoured.
    const panel = wrapper.find(".uc-panel");
    expect(panel.attributes("role")).toBe("dialog");
    expect(panel.attributes("aria-label")).toBe("History");
    expect(wrapper.find(".uc-btn--chevron").attributes("aria-expanded")).toBe(
      "true",
    );
  });

  it("returns focus to the chevron when the popover closes on a pick", async () => {
    const store = useOperationStore();
    seed(store, [op({ id: 13 }), op({ id: 12 })]);
    const wrapper = mount(UndoControl, {
      ...globalOpts,
      attachTo: document.body,
    });
    vi.spyOn(store, "undoTo").mockResolvedValue(1);
    await wrapper.find(".uc-btn--chevron").trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    await wrapper.findAll(".uc-row")[0].trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    // Vuetify restores focus on Esc and Tab-past-last, but not on a
    // programmatic close: without this the row is unmounted under the keyboard
    // and focus lands on <body>.
    expect(document.activeElement).toBe(
      wrapper.find(".uc-btn--chevron").element,
    );
  });
});
