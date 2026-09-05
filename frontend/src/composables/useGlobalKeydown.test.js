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
function mountShelfMarker() {
  const el = document.createElement("div");
  el.className = "shelf";
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

  it("declines while the model shelf is the mounted destination", () => {
    mountShelfMarker();
    press("z");
    press("y");
    press("z", { shiftKey: true });
    expect(store.undo).not.toHaveBeenCalled();
    expect(store.redo).not.toHaveBeenCalled();
  });

  it("says why, once, however many times the chord is pressed", () => {
    mountShelfMarker();
    press("z");
    press("z");
    press("y");
    // One card, not three: the coalescing key is what makes a repeated press
    // an update rather than a wall (notice spec §9.1).
    expect(notices.notices).toHaveLength(1);
    expect(notices.notices[0].key).toBe(SHELF_NO_UNDO_KEY);
    expect(notices.notices[0].text).toContain("aren't undoable");
  });

  it("acts again once the shelf unmounts", () => {
    const el = mountShelfMarker();
    press("z");
    expect(store.undo).not.toHaveBeenCalled();
    el.remove();
    press("z");
    expect(store.undo).toHaveBeenCalledTimes(1);
  });
});
