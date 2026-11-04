// ReviewsInspector - the Tags view's right rail: Open | Archived | Stickers.
// It replaced the board's footer (archived receipts, their clear, the sticker
// shelf) and lists the open reviews with their scope and the one way to end
// them, "End review…" (the shared ReviewEndDialog).
//
// Removing an archived receipt unmounts the row the user just activated. With
// no focus move, focus falls to <body>, so these tests pin the recovery target:
// the next row's delete button, or the pane's title when nothing is left.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { nextTick, h } from "vue";

// isReadOnly is a real ref: the template unwraps it, and a plain object there
// would read as truthy and disable every End review….
vi.mock("../../utils/apiClient", async () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: (await import("vue")).ref(false),
}));

const confirmMock = vi.fn();
vi.mock("../../composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

import ReviewsInspector from "./ReviewsInspector.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useEntityListsStore } from "../../stores/useEntityListsStore";

const VIcon = {
  name: "v-icon",
  setup: (_props, { slots }) => () => h("i", { class: "v-icon" }, slots.default?.()),
};

// The one tooltip surface, reduced to what a test can read: the tip's text as
// `data-tip`, on the activator element or on a marker inside the parent.
const TooltipStub = {
  name: "Tooltip",
  props: ["text", "shortcut", "location", "disabled", "describe", "activator"],
  template: `<slot
      v-if="$slots.activator"
      name="activator"
      :props="{ 'data-tip': text || undefined }"
    /><span v-else-if="text" class="tip" :data-tip="text" />`,
};

// The row menu with its overlay flattened: the activator and the items are
// both in the DOM, so a test clicks an item directly.
const VMenuStub = {
  name: "v-menu",
  template: `<div class="vmenu-stub"><slot name="activator" :props="{}" /><slot /></div>`,
};

// The end dialog has its own suite (ReviewEndDialog.test.js); here only that
// the inspector asks it about the right review.
const endOpen = vi.fn();
const EndDialogStub = {
  name: "ReviewEndDialog",
  setup(_props, { expose }) {
    expose({ open: endOpen });
    return () => h("div", { class: "end-dialog-stub" });
  },
};

const globalOpts = {
  stubs: {
    Tooltip: TooltipStub,
    "v-icon": VIcon,
    "v-menu": VMenuStub,
    ReviewEndDialog: EndDialogStub,
  },
};

const DAY = 24 * 60 * 60 * 1000;

function archivedRow(id, tag, { daysAgo = 1, done = 4 } = {}) {
  return {
    id,
    tag,
    stats: { found: 9 },
    progress: { done },
    created_at: new Date(Date.now() - daysAgo * DAY).toISOString(),
  };
}

function seedStore({ archived = [], sessions = [] } = {}) {
  const store = useReviewSessionsStore();
  store.sessions = sessions;
  // The scope names come from the shared entity lists.
  const lists = useEntityListsStore();
  lists.lists = { ...lists.lists, sets: [{ id: 3, name: "Hands" }] };
  store.archived = archived;
  return store;
}

function mountInspector() {
  return mount(ReviewsInspector, { global: globalOpts, attachTo: document.body });
}

async function showTab(w, label) {
  const tab = w
    .findAll("button.inspector-tab")
    .find((t) => t.text().startsWith(label));
  await tab.trigger("click");
}

// Store calls are async; the component awaits them, then awaits nextTick
// before focusing. Flush both plus the render.
async function flush() {
  for (let i = 0; i < 4; i += 1) {
    await Promise.resolve();
    await nextTick();
  }
}

beforeEach(() => {
  setActivePinia(createPinia());
  confirmMock.mockReset();
  endOpen.mockReset();
  window.localStorage.clear();
});

describe("ReviewsInspector tabs", () => {
  it("names itself Reviews and counts Open and Archived; no Stickers tab while gamify is off", () => {
    const store = seedStore({
      archived: [archivedRow(1, "shirt")],
      sessions: [{ id: 9, tag: "hat", scope: {}, stats: { found: 5 }, progress: { done: 0 } }],
    });
    store.gamify = false;
    const w = mountInspector();

    expect(w.find(".rvi-title").text()).toBe("Reviews");
    const tabs = w
      .findAll("button.inspector-tab")
      .map((t) => t.text().replace(/\s+/g, ""));
    expect(tabs).toEqual(["Open1", "Archived1"]);
    w.unmount();
  });

  it("adds a Stickers tab while Pretend this is fun is on, showing the collection", async () => {
    const store = seedStore();
    store.gamify = true;
    store.stickers = [{ id: 1, icon: "mdi-star", color: "#46707a", label: "First" }];
    const w = mountInspector();

    await showTab(w, "Stickers");
    expect(w.findAll(".rvi-stickers .rs-sticker")).toHaveLength(1);
    w.unmount();
  });

  it("the close button closes the rail and the choice is kept", async () => {
    seedStore();
    const w = mountInspector();
    await w.find('[data-testid="rvi-close"]').trigger("click");
    expect(window.localStorage.getItem("pixlstash:reviewsRailOpen")).toBe("false");
    w.unmount();
  });
});

describe("ReviewsInspector Open tab", () => {
  it("lists each open review with its progress and scope", () => {
    seedStore({
      sessions: [
        { id: 9, tag: "malformed hand", scope: { set_id: 3 }, stats: { found: 40 }, progress: { done: 12 } },
        { id: 10, tag: "sunglasses", scope: {}, stats: { found: 5 }, progress: { done: 0 } },
      ],
    });
    const w = mountInspector();

    const rows = w.findAll(".rvi-open");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("12 of 40 · Set: Hands");
    expect(rows[1].text()).toContain("0 of 5 · Whole library");
    w.unmount();
  });

  it("End review… asks the end dialog about that review; Continue opens it", async () => {
    const store = seedStore({
      sessions: [{ id: 9, tag: "hat", scope: {}, stats: { found: 5 }, progress: { done: 2 } }],
    });
    const openSession = vi.spyOn(store, "openSession").mockImplementation(() => {});
    const w = mountInspector();

    const item = (label) =>
      w.findAll(".rvi-open .ctx-item").find((b) => b.text().includes(label));
    expect(item("Abort")).toBeUndefined();
    expect(item("Archive")).toBeUndefined();

    await item("End review").trigger("click");
    expect(endOpen).toHaveBeenCalledWith(9);

    await item("Continue review").trigger("click");
    expect(openSession).toHaveBeenCalledWith(9);

    // The review on screen has nothing to continue.
    store.view = { type: "session", id: 9 };
    await nextTick();
    expect(item("Continue review")).toBeUndefined();
    expect(item("End review")).toBeDefined();
    w.unmount();
  });
});

describe("ReviewsInspector Archived tab", () => {
  it("groups receipts by week and says what each kept", async () => {
    seedStore({
      archived: [
        archivedRow(1, "shirt", { daysAgo: 1, done: 8 }),
        archivedRow(2, "hat", { daysAgo: 30, done: 0 }),
      ],
    });
    const w = mountInspector();
    await showTab(w, "Archived");

    expect(w.findAll(".rvi-sec").map((s) => s.text())).toEqual(["This week", "Earlier"]);
    const rows = w.findAll(".rvi-archived");
    expect(rows[0].text()).toContain("shirt");
    expect(rows[0].text()).toContain("8 kept");
    expect(rows[1].text()).toContain("0 kept");
    expect(w.text()).not.toContain("reviewed ·");
    w.unmount();
  });

  it("moves focus to the next row's delete button after a per-item delete", async () => {
    const store = seedStore({ archived: [archivedRow(1, "shirt"), archivedRow(2, "hat")] });
    vi.spyOn(store, "deleteArchived").mockImplementation(async (id) => {
      store.archived = store.archived.filter((a) => a.id !== id);
    });
    const w = mountInspector();
    await showTab(w, "Archived");

    await w.findAll(".rvi-del")[0].trigger("click");
    await flush();

    const remaining = w.findAll(".rvi-del");
    expect(remaining).toHaveLength(1);
    expect(document.activeElement).toBe(remaining[0].element);
    w.unmount();
  });

  it("moves focus to the pane's title when the last receipt is deleted", async () => {
    const store = seedStore({ archived: [archivedRow(1, "shirt")] });
    vi.spyOn(store, "deleteArchived").mockImplementation(async () => {
      store.archived = [];
    });
    const w = mountInspector();
    await showTab(w, "Archived");

    await w.find(".rvi-del").trigger("click");
    await flush();

    expect(w.find(".rvi-del").exists()).toBe(false);
    expect(document.activeElement).toBe(w.find(".rvi-title").element);
    w.unmount();
  });

  it("names the per-item delete by its tag", async () => {
    seedStore({ archived: [archivedRow(1, "shirt")] });
    const w = mountInspector();
    await showTab(w, "Archived");

    expect(w.find(".rvi-del").attributes("aria-label")).toBe(
      "Delete the archived review for shirt",
    );
    expect(w.find(".rvi-archived .rvi-tag").attributes("title")).toBe("shirt");
    w.unmount();
  });

  it("Clear archived… clears only once confirmed", async () => {
    const store = seedStore({ archived: [archivedRow(1, "shirt")] });
    const clear = vi.spyOn(store, "clearArchived").mockResolvedValue();
    const w = mountInspector();
    await showTab(w, "Archived");

    const btn = () => w.findAll(".rvi-foot button").find((b) => b.text().includes("Clear archived"));
    confirmMock.mockResolvedValueOnce(false);
    await btn().trigger("click");
    await flush();
    expect(confirmMock).toHaveBeenCalledTimes(1);
    expect(confirmMock.mock.calls[0][0].danger).toBe(true);
    expect(clear).not.toHaveBeenCalled();

    confirmMock.mockResolvedValueOnce(true);
    await btn().trigger("click");
    await flush();
    expect(clear).toHaveBeenCalledTimes(1);
    w.unmount();
  });

  it("offers no clear on the Open tab", () => {
    seedStore({ archived: [archivedRow(1, "shirt")] });
    const w = mountInspector();
    expect(w.find(".rvi-foot").exists()).toBe(false);
    w.unmount();
  });
});
