// Component mount/update coverage for the review-session decision UI.
//
// These were absent (only the store was unit-tested), which is how BUG-RS-1 — a
// render crash on the Loading -> card transition — shipped past 189 unit + 61
// API tests. Each test MOUNTS a review component with representative props and
// asserts it renders AND survives an update without throwing.
//
// NOTE on BUG-RS-1 specifically: the crash was a v-if branch-key collision that
// only manifests with the PRODUCTION Vue compiler (a dev build wraps the later
// v-if branches in DEV_ROOT_FRAGMENTs with distinct auto-keys, so the explicit
// card key never collides). Vitest compiles SFCs in dev mode, so these mounts
// cannot reproduce that exact prod-only crash; they guard general render health
// and the null -> binary -> pair transition. The authoritative BUG-RS-1 guard
// is the production-build e2e spec (e2e/specs/review-session.spec.js).

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick, h } from "vue";

vi.mock("../../utils/apiClient", () => ({
  apiClient: {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
  },
  isReadOnly: { value: false },
}));

import ReviewBinaryCard from "./ReviewBinaryCard.vue";
import ReviewPairCard from "./ReviewPairCard.vue";
import ReviewDecisionBar from "./ReviewDecisionBar.vue";
import ReviewSessionView from "./ReviewSessionView.vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";

// jsdom has no ResizeObserver; ReviewBinaryCard observes its <img>.
globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// Stub Vuetify's <v-icon> so we can mount in isolation without registering
// Vuetify (it just renders its glyph text into an <i>).
const VIcon = {
  name: "v-icon",
  setup: (_props, { slots }) => () => h("i", { class: "v-icon" }, slots.default?.()),
};

const globalOpts = {
  stubs: { "v-icon": VIcon },
  provide: {
    "rs-backend-url": "http://backend.test",
    "rs-open-zoom": () => {},
    "rs-open-tag-apply": () => {},
  },
};

function binaryItem(overrides = {}) {
  return {
    id: 1, // deliberately 1: the value that collided with the Loading branch
    kind: "binary",
    direction: "remove",
    tag: "shirt",
    picture_id: 10,
    picture_ext: "jpg",
    confidence: 0.82,
    neighbors: [
      { picture_id: 11, has: true },
      { picture_id: 12, has: false },
      { picture_id: 13, has: true },
    ],
    ...overrides,
  };
}

function pairItem(overrides = {}) {
  return {
    id: 2,
    kind: "pair",
    direction: "remove",
    tag: "shirt",
    picture_id: 20,
    picture_ext: "jpg",
    confidence: 0.9,
    twin_picture_id: 21,
    twin_ext: "jpg",
    twin_confidence: 0.15,
    twin_sim: 0.98,
    ...overrides,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
});

describe("ReviewBinaryCard", () => {
  it("mounts a remove suggestion with neighbours and the tagger line", () => {
    const w = mount(ReviewBinaryCard, { props: { item: binaryItem() }, global: globalOpts });
    expect(w.find(".rs-bin").exists()).toBe(true);
    expect(w.find(".rs-bin-banner--remove").exists()).toBe(true);
    expect(w.text()).toContain("shirt");
    // 2 of 3 neighbours carry the tag.
    expect(w.text()).toContain("2 of 3");
  });

  it("mounts an add suggestion (opposite banner)", () => {
    const w = mount(ReviewBinaryCard, {
      props: { item: binaryItem({ direction: "add" }) },
      global: globalOpts,
    });
    expect(w.find(".rs-bin-banner--add").exists()).toBe(true);
  });

  it("shows the reason (not a 0/0 vote) for a zero-ground-truth fallback card", () => {
    const w = mount(ReviewBinaryCard, {
      props: {
        item: binaryItem({
          neighbors: [],
          reason: "model is confident (91%) and there is nothing to compare to",
        }),
      },
      global: globalOpts,
    });
    // The free-text reason renders...
    expect(w.text()).toContain("model is confident (91%)");
    // ...and no fabricated "0 of 0" neighbour vote or Similar column.
    expect(w.text()).not.toContain("0 of 0");
    expect(w.text()).not.toContain("similar images have it");
    expect(w.find(".rs-similar-closed").exists()).toBe(false);
    expect(w.find(".rs-similar").exists()).toBe(false);
  });

  it("still shows the neighbour vote when neighbours are present", () => {
    const w = mount(ReviewBinaryCard, {
      props: { item: binaryItem({ reason: "ignored when neighbours exist" }) },
      global: globalOpts,
    });
    expect(w.text()).toContain("2 of 3");
    expect(w.text()).not.toContain("ignored when neighbours exist");
  });

  it("survives an item update and a heatmap toggle without throwing", async () => {
    const store = useReviewSessionsStore();
    const w = mount(ReviewBinaryCard, { props: { item: binaryItem() }, global: globalOpts });
    store.setHeatmapEnabled(true);
    await w.setProps({ item: binaryItem({ confidence: 0.2 }) });
    await flushPromises();
    await nextTick();
    expect(w.find(".rs-bin").exists()).toBe(true);
  });
});

describe("ReviewPairCard", () => {
  it("mounts a pair suggestion with both panes", () => {
    const w = mount(ReviewPairCard, { props: { item: pairItem() }, global: globalOpts });
    expect(w.find(".rs-pair").exists()).toBe(true);
    expect(w.findAll(".rs-pair-pane")).toHaveLength(2);
    expect(w.text()).toContain("shirt");
  });

  it("survives a direction flip without throwing", async () => {
    const w = mount(ReviewPairCard, { props: { item: pairItem() }, global: globalOpts });
    await w.setProps({ item: pairItem({ direction: "add" }) });
    await nextTick();
    expect(w.findAll(".rs-pair-pane")).toHaveLength(2);
  });
});

describe("ReviewDecisionBar", () => {
  it("renders the binary buttons for a binary card", () => {
    const w = mount(ReviewDecisionBar, {
      props: { kind: "binary", direction: "remove", canUndo: false, gamify: false, hold: false },
      global: globalOpts,
    });
    expect(w.find(".rs-decide-btn--yes").exists()).toBe(true);
    expect(w.find(".rs-decide-btn--no").exists()).toBe(true);
  });

  it("survives a kind flip binary -> pair (Both/Neither/Left/Right appear)", async () => {
    const w = mount(ReviewDecisionBar, {
      props: { kind: "binary", direction: "remove", canUndo: false, gamify: false, hold: false },
      global: globalOpts,
    });
    await w.setProps({ kind: "pair" });
    await nextTick();
    expect(w.text()).toContain("Both");
    expect(w.text()).toContain("Neither");
    expect(w.text()).toContain("Left only");
    expect(w.text()).toContain("Right only");
  });

  it("emits answer on Yes/No click", async () => {
    const w = mount(ReviewDecisionBar, {
      props: { kind: "binary", direction: "remove", canUndo: false, gamify: false, hold: false },
      global: globalOpts,
    });
    await w.find(".rs-decide-btn--yes").trigger("click");
    expect(w.emitted("answer")?.[0]).toEqual(["yes"]);
  });
});

describe("ReviewSessionView", () => {
  const session = {
    id: "sess1",
    tag: "shirt",
    stats: { found: 3, scanned: 100, prev_reviewed: 0 },
    created_at: null,
    stale: false,
  };

  function seed(store, queue) {
    store.sessions = [session];
    store.view = { type: "session", id: "sess1" };
    store.queues = { sess1: queue };
  }

  it("shows Loading while the queue is loading and empty", async () => {
    const store = useReviewSessionsStore();
    seed(store, { items: [], loading: true, error: null });
    const w = mount(ReviewSessionView, { props: { session }, global: globalOpts });
    await nextTick();
    expect(w.text()).toContain("Loading");
    expect(w.find(".rs-card").exists()).toBe(false);
  });

  // The direct BUG-RS-1 scenario: Loading -> a binary card whose id is 1 (the
  // value that used to collide with the Loading branch's key). Asserts the card
  // renders and the decision bar appears.
  it("transitions Loading -> binary card (id=1) and renders the card + decision bar", async () => {
    const store = useReviewSessionsStore();
    seed(store, { items: [], loading: true, error: null });
    const w = mount(ReviewSessionView, { props: { session }, global: globalOpts });
    await nextTick();

    store.queues = { sess1: { items: [binaryItem({ id: 1 })], loading: false, error: null } };
    await flushPromises();
    await nextTick();

    expect(w.find(".rs-card").exists()).toBe(true);
    expect(w.find(".rs-bin").exists()).toBe(true);
    expect(w.find(".rs-decide").exists()).toBe(true);
    expect(w.text()).not.toContain("Loading");
  });

  it("advances binary -> pair card without throwing", async () => {
    const store = useReviewSessionsStore();
    seed(store, { items: [binaryItem({ id: 1 })], loading: false, error: null });
    const w = mount(ReviewSessionView, { props: { session }, global: globalOpts });
    await nextTick();
    expect(w.find(".rs-bin").exists()).toBe(true);

    store.queues = { sess1: { items: [pairItem({ id: 2 })], loading: false, error: null } };
    await flushPromises();
    await nextTick();

    expect(w.find(".rs-pair").exists()).toBe(true);
    expect(w.findAll(".rs-pair-pane")).toHaveLength(2);
  });

  it("shows the completion state when the queue empties", async () => {
    const store = useReviewSessionsStore();
    seed(store, { items: [binaryItem({ id: 1 })], loading: false, error: null });
    const w = mount(ReviewSessionView, { props: { session }, global: globalOpts });
    await nextTick();

    store.queues = { sess1: { items: [], loading: false, error: null } };
    await flushPromises();
    await nextTick();

    expect(w.find(".rs-card").exists()).toBe(false);
    expect(w.find(".rs-state--done").exists()).toBe(true);
  });
});
