// One undo vocabulary inside a review session (owner ruling, 2026-07-29).
//
// Ctrl+Z used to be dead here: the review overlay's capture-phase handler bailed
// on any ctrl/meta/alt, and App's global handler skips the whole overlay, so the
// chord reached nothing at all while `U` quietly did the work. These tests pin
// the three claims that fix makes:
//
//   • Ctrl+Z and `U` are the SAME request, through one gate.
//   • That gate answers all three outcomes - done, blocked by a locked set, and
//     nothing to undo - because a shortcut that silently does nothing is
//     indistinguishable from a broken one.
//   • It is the REVIEW's undo, never the app-wide operation stack. A review
//     decision also flips its suggestion row's status and writes the human-label
//     ledger, neither of which the operation log captures, so the two stacks
//     stay separate and the boundary is stated rather than crossed.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick, h } from "vue";

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: { value: false },
}));

import ReviewSessionView from "./ReviewSessionView.vue";
import ReviewSessionsOverlay from "../views/ReviewSessionsOverlay.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";


const VIcon = {
  name: "v-icon",
  setup:
    (_props, { slots }) =>
    () =>
      h("i", { class: "v-icon" }, slots.default?.()),
};

const globalOpts = {
  stubs: { "v-icon": VIcon },
  provide: {
    "rs-backend-url": "http://backend.test",
    "rs-open-zoom": () => {},
    "rs-open-tag-apply": () => {},
  },
};

const session = {
  id: "sess1",
  tag: "shirt",
  stats: { found: 3, scanned: 100, prev_reviewed: 0 },
  progress: { done: 1, pending: 2, skipped: 0 },
  created_at: null,
  stale: false,
};

function binaryItem(overrides = {}) {
  return {
    id: 1,
    kind: "binary",
    direction: "remove",
    tag: "shirt",
    picture_id: 10,
    picture_ext: "jpg",
    confidence: 0.82,
    neighbors: [],
    ...overrides,
  };
}

/** Seed an open session whose undo stack already carries one decision. */
function seedWithHistory(store, { pictureId = 10 } = {}) {
  store.sessions = [session];
  store.view = { type: "session", id: "sess1" };
  store.queues = {
    sess1: { items: [binaryItem({ id: 2 })], loading: false, error: null },
  };
  store.undoStacks = {
    sess1: [
      {
        item: binaryItem({ id: 1, picture_id: pictureId }),
        action: "remove_tag",
        delta: { removed: 1 },
        votes: [],
      },
    ],
  };
  return store;
}

function seedEmpty(store) {
  store.sessions = [session];
  store.view = { type: "session", id: "sess1" };
  store.queues = {
    sess1: { items: [binaryItem({ id: 2 })], loading: false, error: null },
  };
  store.undoStacks = { sess1: [] };
  return store;
}

function mountSession() {
  return mount(ReviewSessionView, { props: { session }, global: globalOpts });
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("review undo - one gate for `U` and Ctrl+Z", () => {
  it("runs the review's own undo for both keys", async () => {
    const store = seedWithHistory(useReviewSessionsStore());
    const undo = vi.spyOn(store, "undo").mockResolvedValue(undefined);
    const w = mountSession();
    await nextTick();

    expect(w.vm.handleKey("u")).toBe(true);
    expect(w.vm.handleKey("undo")).toBe(true);
    expect(undo).toHaveBeenCalledTimes(2);
  });

  it("undoes even with no card in front of you", async () => {
    // The queue has run dry (`current` is null). Undo does not act on the card
    // on screen, it puts the LAST one back, which is exactly when you want it.
    const store = seedWithHistory(useReviewSessionsStore());
    store.queues = { sess1: { items: [], loading: false, error: null } };
    const undo = vi.spyOn(store, "undo").mockResolvedValue(undefined);
    const w = mountSession();
    await nextTick();

    expect(w.vm.handleKey("undo")).toBe(true);
    expect(undo).toHaveBeenCalledTimes(1);
  });

  it("announces the success after the card has landed, not before", async () => {
    // The `current` watcher clears the live region on every card change, and a
    // successful undo IS a card change. Announcing first means the watcher wipes
    // it and the success is silent.
    const store = seedWithHistory(useReviewSessionsStore());
    vi.spyOn(store, "undo").mockImplementation(async () => {
      store.queues = {
        sess1: {
          items: [binaryItem({ id: 1 }), binaryItem({ id: 2 })],
          loading: false,
          error: null,
        },
      };
    });
    const w = mountSession();
    await nextTick();

    w.vm.handleKey("undo");
    await flushPromises();
    await nextTick();

    expect(w.find('[aria-live="assertive"]').text()).toBe(
      "Undone. The last decision is back in the queue.",
    );
  });
});

describe("review undo - the three outcomes are all answered", () => {
  it("says so, and never touches the app-wide stack, when there is nothing to undo", async () => {
    const store = seedEmpty(useReviewSessionsStore());
    const notices = useNoticeStore();
    const undo = vi.spyOn(store, "undo").mockResolvedValue(undefined);
    const w = mountSession();
    await nextTick();

    // Consumed: the press was ANSWERED, not swallowed.
    expect(w.vm.handleKey("undo")).toBe(true);
    expect(undo).not.toHaveBeenCalled();
    const text = notices.notices.map((n) => n.text).join(" ");
    expect(text).toContain("Nothing to undo in this review");
    // The boundary is stated rather than crossed: the app-wide history is still
    // there, it is just not what this key reaches.
    expect(text).toContain("toolbar");
  });

  it("announces the locked-set reason and issues no request", async () => {
    const store = seedWithHistory(useReviewSessionsStore(), { pictureId: 77 });
    useLockedSetsStore().sets = [
      { id: 99, name: "Holiday 2019", picture_ids: [77] },
    ];
    const undo = vi.spyOn(store, "undo").mockResolvedValue(undefined);
    const w = mountSession();
    await nextTick();

    expect(w.vm.handleKey("undo")).toBe(true);
    expect(undo).not.toHaveBeenCalled();
    await nextTick();
    expect(w.find('[aria-live="assertive"]').text()).toContain("Holiday 2019");
  });
});

// ── The overlay's keyboard routing ──────────────────────────────────────────
// The chord has to be caught BEFORE the modifier bail that made it dead, and
// the bail has to survive for everything else.

const SessionStub = {
  name: "ReviewSessionView",
  props: { session: { type: Object, required: true } },
  setup(_props, { expose }) {
    const handleKey = vi.fn(() => true);
    expose({ handleKey });
    SessionStub.lastHandleKey = handleKey;
    return () => h("div", { class: "session-stub" });
  },
};

const Blank = { name: "Blank", render: () => h("div") };

const overlayOpts = {
  global: {
    stubs: {
      "v-icon": VIcon,
      ReviewSessionView: SessionStub,
      ReviewRail: Blank,
      TagHealthBoard: Blank,
      ReviewArchivedReceipt: Blank,
      NewReviewDialog: Blank,
      TbTagPanel: Blank,
    },
  },
  attachTo: document.body,
};

async function mountOverlayWithSession() {
  const store = useReviewSessionsStore();
  const w = mount(ReviewSessionsOverlay, overlayOpts);
  await flushPromises();
  // `load()` resets the view to the board on mount, so the session is seeded
  // afterwards rather than before.
  seedWithHistory(store);
  await nextTick();
  return { store, w };
}

function press(key, init = {}) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  window.dispatchEvent(event);
  return event;
}

describe("ReviewSessionsOverlay - Ctrl+Z is no longer dead", () => {
  it("routes the chord to the review's own undo and consumes it", async () => {
    const { w } = await mountOverlayWithSession();

    const event = press("z", { ctrlKey: true });
    expect(SessionStub.lastHandleKey).toHaveBeenCalledWith("undo");
    expect(event.defaultPrevented).toBe(true);
    w.unmount();
  });

  it("accepts Meta+Z too, so the binding is not platform-specific", async () => {
    const { w } = await mountOverlayWithSession();

    press("z", { metaKey: true });
    expect(SessionStub.lastHandleKey).toHaveBeenCalledWith("undo");
    w.unmount();
  });

  it("does not walk the stack on a held key", async () => {
    const { w } = await mountOverlayWithSession();

    press("z", { ctrlKey: true, repeat: true });
    expect(SessionStub.lastHandleKey).not.toHaveBeenCalled();
    w.unmount();
  });

  it("leaves the other chords alone", async () => {
    const { w } = await mountOverlayWithSession();

    for (const key of ["f", "a", "c", "r"]) {
      const event = press(key, { ctrlKey: true });
      expect(event.defaultPrevented).toBe(false);
    }
    expect(SessionStub.lastHandleKey).not.toHaveBeenCalled();
    w.unmount();
  });

  it("answers redo instead of leaving it dead or reaching the app-wide stack", async () => {
    const notices = useNoticeStore();
    const { w } = await mountOverlayWithSession();

    const event = press("y", { ctrlKey: true });
    expect(event.defaultPrevented).toBe(true);
    expect(SessionStub.lastHandleKey).not.toHaveBeenCalled();
    expect(notices.notices.map((n) => n.text).join(" ")).toContain(
      "Nothing to redo in this review",
    );
    w.unmount();
  });

  it("answers the chord on the board, where there is no review to undo in", async () => {
    const notices = useNoticeStore();
    const w = mount(ReviewSessionsOverlay, overlayOpts);
    await flushPromises();

    press("z", { ctrlKey: true });
    expect(notices.notices.map((n) => n.text).join(" ")).toContain(
      "Nothing to undo here",
    );
    w.unmount();
  });

  it("teaches both keys, and the scope, in the cheat-sheet", async () => {
    const { w } = await mountOverlayWithSession();

    press("?");
    await nextTick();
    const sheet = w.find(".rs-keys");
    expect(sheet.exists()).toBe(true);
    expect(sheet.text()).toMatch(/U \/ (Ctrl\+Z|⌘\+Z)/);
    expect(sheet.text()).toContain("Undo the last decision in this review");
    w.unmount();
  });
});
