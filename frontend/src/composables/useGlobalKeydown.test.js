// useGlobalKeydown - where the undo chord is allowed to act.
//
// The handler's own invariant is that every undo raises a receipt, which is why
// it already declines behind a modal scrim. The model shelf is the same rule
// reached from the other side: it mounts no `ActionReceipt` and no
// `UndoControl`, so an undo fired there would revert a library action taken on
// a screen the reader has left, with nothing on this one to say so. These tests
// pin the positive control beside the negative - over-declining would be its
// own regression.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { ref } from "vue";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import { SHELF_NO_UNDO_KEY, useGlobalKeydown } from "./useGlobalKeydown";
import { useNoticeStore } from "../stores/useNoticeStore";
import { useOperationStore } from "../stores/useOperationStore";

/** A host component whose only job is to install the listener. */
const Host = {
  setup() {
    useGlobalKeydown({
      gridContainer: ref(null),
      sidebarRef: ref(null),
      shortcutsDialogOpen: ref(false),
    });
    return () => null;
  },
};

let wrapper;
let store;
let notices;

function press(key, init = {}) {
  window.dispatchEvent(
    new KeyboardEvent("keydown", {
      key,
      ctrlKey: true,
      bubbles: true,
      ...init,
    }),
  );
}

/** Mount the shelf's root signal - the class `ModelShelf.vue` renders. */
function mountShelfMarker(className = "shelf") {
  const el = document.createElement("div");
  el.className = className;
  document.body.appendChild(el);
  return el;
}

beforeEach(() => {
  setActivePinia(createPinia());
  store = useOperationStore();
  notices = useNoticeStore();
  vi.spyOn(store, "undo").mockResolvedValue(undefined);
  vi.spyOn(store, "redo").mockResolvedValue(undefined);
  wrapper = mount(Host, { attachTo: document.body });
});

afterEach(() => {
  wrapper?.unmount();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("the undo chord", () => {
  it("undoes and redoes from a narrating view, silently", () => {
    press("z");
    expect(store.undo).toHaveBeenCalledTimes(1);
    press("y");
    expect(store.redo).toHaveBeenCalledTimes(1);
    press("z", { shiftKey: true });
    expect(store.redo).toHaveBeenCalledTimes(2);
    // The receipt narrates a real undo; a notice here would be a second voice.
    expect(notices.notices).toHaveLength(0);
  });

  it("takes a live local receipt's action on the shelf ahead of the log", async () => {
    mountShelfMarker();
    const undo = vi.fn().mockResolvedValue(true);
    const redo = vi.fn().mockResolvedValue({});
    store.setLocalReceiptHost(true);
    store.showLocalReceipt({ summary: "Added", icon: "mdi-plus", undo, redo });
    press("y");
    expect(undo).not.toHaveBeenCalled();
    // The wrong direction says which way works, never "not undoable".
    expect(notices.notices.at(-1).text).toContain(
      "Undo takes that change back",
    );
    expect(notices.notices.at(-1).key).toBe(SHELF_NO_UNDO_KEY);
    press("z");
    await vi.waitFor(() => expect(store.receipt?.mode).toBe("undone"));
    expect(undo).toHaveBeenCalledTimes(1);
    press("y");
    await vi.waitFor(() => expect(redo).toHaveBeenCalledTimes(1));
    expect(store.undo).not.toHaveBeenCalled();
    expect(store.redo).not.toHaveBeenCalled();
  });

  // Every grid-replacing destination now carries UndoControl (in
  // TbGlobalActions) and an ActionReceipt, so the chord acts there as it does
  // on the grid. They used to decline (#1415) because they had neither.
  it.each([
    ["the model shelf", "shelf"],
    ["the Workflows grid", "wfv"],
    ["Moves", "mv"],
    ["Insights", "ins"],
  ])("undoes and redoes on %s, silently", (_name, className) => {
    mountShelfMarker(className);
    press("z");
    expect(store.undo).toHaveBeenCalledTimes(1);
    press("z", { shiftKey: true });
    expect(store.redo).toHaveBeenCalledTimes(1);
    expect(notices.notices).toHaveLength(0);
  });
});

describe("Escape and the sidebar's Find in library (#2448)", () => {
  let sidebar;
  let host;

  beforeEach(() => {
    sidebar = { clearFind: vi.fn(() => true) };
    const sidebarRef = ref(sidebar);
    host = mount(
      {
        setup() {
          useGlobalKeydown({
            gridContainer: ref(null),
            sidebarRef,
            shortcutsDialogOpen: ref(false),
          });
          return () => null;
        },
      },
      { attachTo: document.body },
    );
  });

  afterEach(() => host.unmount());

  it("clears Find from anywhere outside a text field", () => {
    press("Escape", { ctrlKey: false });
    expect(sidebar.clearFind).toHaveBeenCalled();
  });

  it("leaves it alone while typing elsewhere or under a dialog", () => {
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(sidebar.clearFind).not.toHaveBeenCalled();

    const overlay = document.createElement("div");
    overlay.className = "v-overlay--active";
    overlay.innerHTML = '<div class="v-overlay__scrim"></div>';
    document.body.appendChild(overlay);
    press("Escape", { ctrlKey: false });
    expect(sidebar.clearFind).not.toHaveBeenCalled();
  });
});
