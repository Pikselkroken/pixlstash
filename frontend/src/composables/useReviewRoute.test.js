import { describe, it, expect, beforeEach, vi } from "vitest";
import { reactive, watch, nextTick } from "vue";

import {
  parseReviewQuery,
  buildReviewQuery,
  resolveReviewView,
  useReviewRoute,
  REVIEW_BOARD,
} from "./useReviewRoute";

// --- Test doubles -------------------------------------------------------------

// A minimal history-backed router stand-in. `replace` overwrites the top entry
// (matching vue-router's replace semantics) so we can assert we never stack
// duplicate entries; `push`/`back` model real navigation for the back tests.
function makeRouter(initialQuery = {}) {
  const route = reactive({ query: { ...initialQuery } });
  const history = [{ ...initialQuery }];
  let cursor = 0;
  const router = {
    replaceCalls: 0,
    replace(to) {
      router.replaceCalls += 1;
      history[cursor] = { ...to.query };
      route.query = { ...to.query };
      return Promise.resolve();
    },
    push(to) {
      history.splice(cursor + 1);
      history.push({ ...to.query });
      cursor = history.length - 1;
      route.query = { ...to.query };
      return Promise.resolve();
    },
    back() {
      if (cursor === 0) return;
      cursor -= 1;
      route.query = { ...history[cursor] };
    },
    forward() {
      if (cursor >= history.length - 1) return;
      cursor += 1;
      route.query = { ...history[cursor] };
    },
    get entries() {
      return history;
    },
  };
  return { route, router };
}

function makeStore({ sessions = [], archived = [] } = {}) {
  const store = reactive({
    overlayOpen: false,
    view: { type: "board" },
    healthScope: { projectId: null, setId: null, characterId: null },
    pendingRestoreViewId: null,
    sessions,
    archived,
    setHealthScopeCalls: 0,
    showBoard() {
      store.view = { type: "board" };
    },
    openSession(id) {
      store.view = { type: "session", id };
    },
    openArchived(id) {
      store.view = { type: "archived", id };
    },
    setHealthScope(scope) {
      store.setHealthScopeCalls += 1;
      store.healthScope = {
        projectId: scope?.projectId ?? null,
        setId: scope?.setId ?? null,
        characterId: scope?.characterId ?? null,
      };
    },
  });
  return store;
}

// Simulates what ReviewSessionsOverlay.vue's onMounted → store.load() does:
// consume pendingRestoreViewId once the session lists exist.
function runLoad(store) {
  const id = store.pendingRestoreViewId;
  store.pendingRestoreViewId = null;
  store.view = { type: "board" };
  if (id != null) resolveReviewView(store, id);
}

// --- parseReviewQuery ---------------------------------------------------------

describe("parseReviewQuery", () => {
  it("treats an absent ?review as closed", () => {
    expect(parseReviewQuery({}).open).toBe(false);
    expect(parseReviewQuery({ overlay: "12" }).open).toBe(false);
  });

  it("opens on the board for every non-id value", () => {
    for (const raw of [
      "",
      "board",
      "true",
      "nonsense",
      "0",
      "-3",
      "1.5",
      null,
    ]) {
      const parsed = parseReviewQuery({ review: raw });
      expect(parsed.open, `review=${raw}`).toBe(true);
      expect(parsed.reviewId, `review=${raw}`).toBe(null);
    }
  });

  it("parses a numeric review id", () => {
    expect(parseReviewQuery({ review: "42" }).reviewId).toBe(42);
  });

  it("takes the last value of a repeated key", () => {
    expect(parseReviewQuery({ review: ["board", "7"] }).reviewId).toBe(7);
  });

  it("parses the scope dimensions", () => {
    expect(
      parseReviewQuery({
        review: "board",
        review_project: "3",
        review_set: "12",
        review_character: "9",
      }).scope,
    ).toEqual({ projectId: 3, setId: 12, characterId: 9 });
  });

  it("accepts UNASSIGNED for the character dimension", () => {
    expect(
      parseReviewQuery({ review: "board", review_character: "UNASSIGNED" })
        .scope.characterId,
    ).toBe("UNASSIGNED");
  });

  it("drops malformed scope dimensions instead of throwing", () => {
    const { scope } = parseReviewQuery({
      review: "board",
      review_project: "abc",
      review_set: "-1",
      review_character: "%%%",
    });
    expect(scope).toEqual({
      projectId: null,
      setId: null,
      characterId: null,
    });
  });
});

// --- buildReviewQuery ---------------------------------------------------------

describe("buildReviewQuery", () => {
  it("strips every review param when closed, preserving foreign params", () => {
    const next = buildReviewQuery(
      { overlay: "5", review: "7", review_set: "2" },
      { open: false },
    );
    expect(next).toEqual({ overlay: "5" });
  });

  it("encodes the board", () => {
    expect(
      buildReviewQuery({}, { open: true, view: { type: "board" } }),
    ).toEqual({ review: REVIEW_BOARD });
  });

  it("encodes a session and an archived receipt by id", () => {
    expect(
      buildReviewQuery({}, { open: true, view: { type: "session", id: 8 } }),
    ).toEqual({ review: "8" });
    expect(
      buildReviewQuery({}, { open: true, view: { type: "archived", id: 9 } }),
    ).toEqual({ review: "9" });
  });

  it("encodes only the set scope dimensions that are present", () => {
    expect(
      buildReviewQuery(
        {},
        {
          open: true,
          view: { type: "board" },
          scope: { projectId: null, setId: 4, characterId: "UNASSIGNED" },
        },
      ),
    ).toEqual({
      review: REVIEW_BOARD,
      review_set: "4",
      review_character: "UNASSIGNED",
    });
  });
});

// --- resolveReviewView --------------------------------------------------------

describe("resolveReviewView", () => {
  it("resolves an open session", () => {
    const store = makeStore({ sessions: [{ id: 5 }] });
    expect(resolveReviewView(store, 5)).toBe("session");
    expect(store.view).toEqual({ type: "session", id: 5 });
  });

  it("resolves an archived receipt", () => {
    const store = makeStore({ archived: [{ id: 6 }] });
    expect(resolveReviewView(store, 6)).toBe("archived");
    expect(store.view).toEqual({ type: "archived", id: 6 });
  });

  it("falls back to the board for an unknown id without throwing", () => {
    const store = makeStore({ sessions: [{ id: 1 }], archived: [{ id: 2 }] });
    expect(resolveReviewView(store, 999)).toBe("board");
    expect(store.view).toEqual({ type: "board" });
  });
});

// --- useReviewRoute: refresh restore -----------------------------------------

describe("useReviewRoute - refresh restore", () => {
  let store;

  beforeEach(() => {
    store = null;
  });

  it("does nothing when the URL carries no review param", async () => {
    const { route, router } = makeRouter({ overlay: "3" });
    store = makeStore();
    useReviewRoute(route, router, store, { watch });
    await nextTick();
    expect(store.overlayOpen).toBe(false);
    expect(router.replaceCalls).toBe(0);
    expect(route.query).toEqual({ overlay: "3" });
  });

  it("restores the overlay on the board", async () => {
    const { route, router } = makeRouter({ review: "board" });
    store = makeStore();
    useReviewRoute(route, router, store, { watch });
    expect(store.overlayOpen).toBe(true);
    runLoad(store);
    await nextTick();
    expect(store.view).toEqual({ type: "board" });
    // Already-correct URL must not be rewritten.
    expect(router.replaceCalls).toBe(0);
  });

  it("restores the board scope before the overlay opens", async () => {
    const { route, router } = makeRouter({
      review: "board",
      review_project: "3",
      review_set: "12",
      review_character: "UNASSIGNED",
    });
    store = makeStore();
    useReviewRoute(route, router, store, { watch });
    expect(store.healthScope).toEqual({
      projectId: 3,
      setId: 12,
      characterId: "UNASSIGNED",
    });
    // Seeded directly - no second /tag_health request via setHealthScope.
    expect(store.setHealthScopeCalls).toBe(0);
    expect(store.overlayOpen).toBe(true);
  });

  it("restores an open review session", async () => {
    const { route, router } = makeRouter({ review: "42" });
    store = makeStore({ sessions: [{ id: 42 }] });
    useReviewRoute(route, router, store, { watch });
    expect(store.pendingRestoreViewId).toBe(42);
    runLoad(store);
    await nextTick();
    expect(store.view).toEqual({ type: "session", id: 42 });
    expect(router.replaceCalls).toBe(0);
  });

  it("restores an archived receipt", async () => {
    const { route, router } = makeRouter({ review: "43" });
    store = makeStore({ archived: [{ id: 43 }] });
    useReviewRoute(route, router, store, { watch });
    runLoad(store);
    await nextTick();
    expect(store.view).toEqual({ type: "archived", id: 43 });
  });

  it("degrades a stale review id to an open overlay on the board and fixes the URL", async () => {
    const { route, router } = makeRouter({ review: "999" });
    store = makeStore({ sessions: [{ id: 1 }], archived: [{ id: 2 }] });
    useReviewRoute(route, router, store, { watch });
    expect(store.overlayOpen).toBe(true);
    runLoad(store);
    await nextTick();
    expect(store.view).toEqual({ type: "board" });
    // Self-heals: the lying id is replaced with `board`, not left in the URL.
    expect(route.query.review).toBe(REVIEW_BOARD);
  });

  it("opens the board for a malformed review value rather than half-opening", async () => {
    for (const raw of ["", "true", "%%%", "0"]) {
      const { route, router } = makeRouter({ review: raw });
      const s = makeStore();
      expect(() => useReviewRoute(route, router, s, { watch })).not.toThrow();
      expect(s.overlayOpen, `review=${raw}`).toBe(true);
      expect(s.pendingRestoreViewId).toBe(null);
      runLoad(s);
      await nextTick();
      expect(s.view).toEqual({ type: "board" });
    }
  });

  it("keeps a scope pointing at a deleted or locked set (the board owns that state)", async () => {
    // A locked set is a legitimate terminal state on the board, and a deleted
    // set simply yields no rows - neither is a routing error, so the scope is
    // restored verbatim and the board decides what to render.
    const { route, router } = makeRouter({ review: "board", review_set: "77" });
    store = makeStore();
    useReviewRoute(route, router, store, { watch });
    expect(store.healthScope.setId).toBe(77);
    expect(store.overlayOpen).toBe(true);
  });
});

// --- useReviewRoute: store → URL ---------------------------------------------

describe("useReviewRoute - writing the URL", () => {
  it("writes ?review=board on open and strips it on close", async () => {
    const { route, router } = makeRouter({});
    const store = makeStore();
    useReviewRoute(route, router, store, { watch });

    store.overlayOpen = true;
    await nextTick();
    expect(route.query.review).toBe(REVIEW_BOARD);

    store.overlayOpen = false;
    await nextTick();
    expect(route.query.review).toBeUndefined();
  });

  it("preserves the image-overlay param alongside the review param", async () => {
    const { route, router } = makeRouter({ overlay: "12" });
    const store = makeStore();
    useReviewRoute(route, router, store, { watch });
    store.overlayOpen = true;
    await nextTick();
    expect(route.query).toEqual({ overlay: "12", review: REVIEW_BOARD });
  });

  it("tracks the view and the scope", async () => {
    const { route, router } = makeRouter({});
    const store = makeStore({ sessions: [{ id: 8 }] });
    useReviewRoute(route, router, store, { watch });

    store.overlayOpen = true;
    await nextTick();
    store.openSession(8);
    await nextTick();
    expect(route.query.review).toBe("8");

    store.setHealthScope({ setId: 4 });
    await nextTick();
    expect(route.query.review_set).toBe("4");

    store.showBoard();
    await nextTick();
    expect(route.query.review).toBe(REVIEW_BOARD);
    expect(route.query.review_set).toBe("4");
  });

  it("uses replace only, and never navigates for an unchanged query", async () => {
    const { route, router } = makeRouter({});
    const store = makeStore();
    useReviewRoute(route, router, store, { watch });

    store.overlayOpen = true;
    await nextTick();
    const afterOpen = router.replaceCalls;
    expect(afterOpen).toBe(1);
    // One history entry total: replace never stacks.
    expect(router.entries).toHaveLength(1);

    // A no-op view change (already the board) must not navigate at all.
    store.showBoard();
    await nextTick();
    expect(router.replaceCalls).toBe(afterOpen);

    store.overlayOpen = false;
    await nextTick();
    expect(router.replaceCalls).toBe(afterOpen + 1);
    expect(router.entries).toHaveLength(1);
  });
});

// --- useReviewRoute: back / forward -------------------------------------------

describe("useReviewRoute - back and forward", () => {
  it("closes the overlay when back leaves the review URL", async () => {
    const { route, router } = makeRouter({});
    const store = makeStore();
    useReviewRoute(route, router, store, { watch });

    // A real navigation happened first, then the overlay was opened (replace).
    router.push({ query: {} });
    await nextTick();
    store.overlayOpen = true;
    await nextTick();
    expect(route.query.review).toBe(REVIEW_BOARD);

    router.back();
    await nextTick();
    expect(store.overlayOpen).toBe(false);
  });

  it("reopens the overlay on forward", async () => {
    const { route, router } = makeRouter({});
    const store = makeStore();
    useReviewRoute(route, router, store, { watch });

    router.push({ query: {} });
    await nextTick();
    store.overlayOpen = true;
    await nextTick();
    router.back();
    await nextTick();
    expect(store.overlayOpen).toBe(false);

    router.forward();
    await nextTick();
    expect(store.overlayOpen).toBe(true);
  });

  it("reconciles the view when navigating back to a different review id", async () => {
    const { route, router } = makeRouter({ review: "board" });
    const store = makeStore({ sessions: [{ id: 8 }], archived: [{ id: 9 }] });
    useReviewRoute(route, router, store, { watch });
    runLoad(store);
    await nextTick();

    router.push({ query: { review: "9" } });
    await nextTick();
    expect(store.view).toEqual({ type: "archived", id: 9 });

    router.back();
    await nextTick();
    expect(store.view).toEqual({ type: "board" });
    expect(store.overlayOpen).toBe(true);
  });

  it("degrades to the board when back lands on a review id that no longer exists", async () => {
    const { route, router } = makeRouter({ review: "board" });
    const store = makeStore({ sessions: [{ id: 8 }] });
    useReviewRoute(route, router, store, { watch });
    runLoad(store);
    await nextTick();

    expect(() => router.push({ query: { review: "404" } })).not.toThrow();
    await nextTick();
    expect(store.view).toEqual({ type: "board" });
    expect(store.overlayOpen).toBe(true);
  });
});

// --- store integration: load() consumes pendingRestoreViewId -----------------

vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  sessionContext: { value: null },
  apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  isReadOnly: { value: false },
}));

describe("useReviewSessionsStore.load - URL restore", () => {
  it("resolves, then clears, pendingRestoreViewId", async () => {
    const { setActivePinia, createPinia } = await import("pinia");
    const { apiClient } = await import("../utils/apiClient");
    const { useReviewSessionsStore } =
      await import("../stores/useReviewSessionsStore");
    setActivePinia(createPinia());
    apiClient.get.mockImplementation((url, opts) => {
      if (url === "/reviews" && opts?.params?.status === "OPEN") {
        return Promise.resolve({ data: [{ id: 42, tag: "cat" }] });
      }
      return Promise.resolve({ data: [] });
    });
    apiClient.post.mockResolvedValue({ data: {} });

    const store = useReviewSessionsStore();
    store.pendingRestoreViewId = 42;
    await store.load();
    expect(store.view).toEqual({ type: "session", id: 42 });
    expect(store.pendingRestoreViewId).toBe(null);
  });

  it("falls back to the board for an unknown id", async () => {
    const { setActivePinia, createPinia } = await import("pinia");
    const { apiClient } = await import("../utils/apiClient");
    const { useReviewSessionsStore } =
      await import("../stores/useReviewSessionsStore");
    setActivePinia(createPinia());
    apiClient.get.mockResolvedValue({ data: [] });
    apiClient.post.mockResolvedValue({ data: {} });

    const store = useReviewSessionsStore();
    store.pendingRestoreViewId = 999;
    await store.load();
    expect(store.view).toEqual({ type: "board" });
    expect(store.pendingRestoreViewId).toBe(null);
  });
});
