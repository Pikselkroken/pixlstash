// ReviewRail - focus management around archived-review removal, and the
// accessible names of the two icon-only controls.
//
// Removing an archived receipt unmounts the row the user just activated (and,
// for the last one, the whole Archived section, which sits behind a v-if). With
// no focus move, focus falls to <body> and a keyboard user loses their place
// entirely. These tests pin the recovery target: the next row's delete button,
// or the rail's "New review" button when there is nothing left.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";
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

import ReviewRail from "./ReviewRail.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";

const VIcon = {
  name: "v-icon",
  setup: (_props, { slots }) => () => h("i", { class: "v-icon" }, slots.default?.()),
};

const globalOpts = { stubs: { "v-icon": VIcon } };

function archivedRow(id, tag) {
  return { id, tag, stats: { found: 4 }, created_at: "2026-07-01T00:00:00Z" };
}

function seedStore(archived) {
  const store = useReviewSessionsStore();
  store.sessions = [];
  store.sets = [];
  store.projects = [];
  store.characters = [];
  store.archived = archived;
  return store;
}

function mountRail() {
  return mount(ReviewRail, { global: globalOpts, attachTo: document.body });
}

// Both store calls are async; the component awaits them, then awaits nextTick
// before focusing. Flush both plus the render.
async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await nextTick();
  await nextTick();
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("ReviewRail archived focus management", () => {
  it("moves focus to the next row's delete button after a per-item delete", async () => {
    seedStore([archivedRow(1, "shirt"), archivedRow(2, "hat")]);
    const w = mountRail();

    await w.findAll(".rs-rail-archived-del")[0].trigger("click");
    await flush();

    const remaining = w.findAll(".rs-rail-archived-del");
    expect(remaining).toHaveLength(1);
    expect(document.activeElement).toBe(remaining[0].element);
    expect(document.activeElement).not.toBe(document.body);
    w.unmount();
  });

  it("moves focus to New review when the last archived row is deleted", async () => {
    seedStore([archivedRow(1, "shirt")]);
    const w = mountRail();

    await w.find(".rs-rail-archived-del").trigger("click");
    await flush();

    // The whole Archived section is behind v-if and has unmounted.
    expect(w.find(".rs-rail-archived-del").exists()).toBe(false);
    expect(document.activeElement).toBe(w.find(".rs-rail-new").element);
    w.unmount();
  });

  it("moves focus to New review after Clear all resolves", async () => {
    seedStore([archivedRow(1, "shirt"), archivedRow(2, "hat")]);
    const w = mountRail();

    // Two-click arm→confirm: the first click only arms, and must not move focus.
    await w.find(".rs-archived-clear").trigger("click");
    await nextTick();
    expect(w.find(".rs-archived-clear").text()).toContain("Sure?");
    expect(document.activeElement).not.toBe(w.find(".rs-rail-new").element);

    await w.find(".rs-archived-clear").trigger("click");
    await flush();

    expect(w.find(".rs-archived-clear").exists()).toBe(false);
    expect(document.activeElement).toBe(w.find(".rs-rail-new").element);
    w.unmount();
  });
});

describe("ReviewRail icon-only button labels", () => {
  it("gives the per-item delete an explicit aria-label naming its tag", () => {
    seedStore([archivedRow(1, "shirt")]);
    const w = mountRail();

    const del = w.find(".rs-rail-archived-del");
    expect(del.attributes("aria-label")).toBe(
      "Delete the archived review for shirt",
    );
    // The mouse affordance stays.
    expect(del.attributes("title")).toContain("shirt");
    w.unmount();
  });

  it("swaps the Clear all aria-label to a confirm phrasing once armed", async () => {
    seedStore([archivedRow(1, "shirt")]);
    const w = mountRail();

    expect(w.find(".rs-archived-clear").attributes("aria-label")).toBe(
      "Clear all archived reviews",
    );

    await w.find(".rs-archived-clear").trigger("click");
    await nextTick();

    expect(w.find(".rs-archived-clear").attributes("aria-label")).toBe(
      "Confirm: clear every archived review",
    );
    w.unmount();
  });
});

describe("ReviewRail truncated labels carry a native title", () => {
  it("titles the archived tag span with its full tag", () => {
    seedStore([archivedRow(1, "a-very-long-tag-name-that-truncates")]);
    const w = mountRail();

    expect(w.find(".rs-rail-archived-tag").attributes("title")).toBe(
      "a-very-long-tag-name-that-truncates",
    );
    w.unmount();
  });

  it("titles the session tag and scope spans", () => {
    const store = seedStore([]);
    store.sessions = [
      {
        id: 9,
        tag: "long-session-tag",
        scope: {},
        stats: { found: 3 },
        progress: { done: 1 },
      },
    ];
    const w = mountRail();

    expect(w.find(".rs-rail-session-tag").attributes("title")).toBe(
      "long-session-tag",
    );
    // Scope title matches the string actually rendered as its text.
    const scope = w.find(".rs-rail-session-scope");
    expect(scope.attributes("title")).toBe("Whole vault");
    expect(scope.text()).toBe("Whole vault");
    w.unmount();
  });
});
