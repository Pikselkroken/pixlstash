// The shared character / picture-set / project list cache (issue #646).
//
// Two properties are under test and they pull in opposite directions:
//
//   * SPEED — a context-menu flyout must render from cache and revalidate in
//     the background, never block on the network.
//   * SAFETY — these are `SCOPED_LIST` routes, so the cached CONTENT is an
//     authorization decision. It must never outlive the credential that
//     produced it, and it must never be written from a WebSocket payload.

import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";

vi.mock("../api/characters", () => ({ listCharacters: vi.fn() }));
vi.mock("../api/pictureSets", () => ({ listPictureSets: vi.fn() }));
vi.mock("../api/projects", () => ({ listProjects: vi.fn() }));

// A working stand-in for the real auth-context chokepoint: the same register /
// notify pair apiClient exports, so the reset wiring is exercised for real.
const sessionResetHandlers = new Set();
vi.mock("../utils/apiClient", () => ({
  isReadOnly: ref(false),
  sessionContext: ref(null),
  onSessionReset: (handler) => {
    sessionResetHandlers.add(handler);
    return () => sessionResetHandlers.delete(handler);
  },
}));

import { listCharacters } from "../api/characters";
import { listPictureSets } from "../api/pictureSets";
import { listProjects } from "../api/projects";
import { isReadOnly, sessionContext } from "../utils/apiClient";
import { useEntityListsStore, ENTITY_KINDS } from "./useEntityListsStore";

/** Fire the auth-context transition every registered store listens for. */
function transitionAuthContext() {
  for (const handler of sessionResetHandlers) handler();
}

/** A promise whose resolution this test controls. */
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const CHARACTERS = [{ id: 1, name: "Ada" }];
const SETS = [{ id: 7, name: "Portraits", picture_count: 12 }];
const PROJECTS = [{ id: 3, name: "Book" }];

describe("useEntityListsStore", () => {
  beforeEach(() => {
    sessionResetHandlers.clear();
    setActivePinia(createPinia());
    isReadOnly.value = false;
    sessionContext.value = null;
    listCharacters.mockReset().mockResolvedValue(CHARACTERS);
    listPictureSets.mockReset().mockResolvedValue(SETS);
    listProjects.mockReset().mockResolvedValue(PROJECTS);
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  // ── Stale-while-revalidate ────────────────────────────────────────────────

  it("keeps serving the cached list while a revalidation is in flight", async () => {
    const store = useEntityListsStore();
    await store.refresh("characters");
    expect(store.characters).toEqual(CHARACTERS);

    const pendingRead = deferred();
    listCharacters.mockReturnValueOnce(pendingRead.promise);
    const revalidation = store.refresh("characters");

    // This is the whole point: the second open renders the old list at once.
    expect(store.characters).toEqual(CHARACTERS);
    expect(store.isLoading("characters")).toBe(false);

    pendingRead.resolve([{ id: 2, name: "Grace" }]);
    await revalidation;
    expect(store.characters).toEqual([{ id: 2, name: "Grace" }]);
  });

  it("collapses concurrent reads of one list into a single request", async () => {
    const store = useEntityListsStore();
    const results = await Promise.all([
      store.refresh("sets"),
      store.refresh("sets"),
      store.refresh("sets"),
    ]);
    expect(listPictureSets).toHaveBeenCalledTimes(1);
    for (const rows of results) expect(rows).toEqual(SETS);
  });

  it("reports loading only while there is nothing to show yet", async () => {
    const store = useEntityListsStore();
    const pendingRead = deferred();
    listCharacters.mockReturnValueOnce(pendingRead.promise);

    const first = store.refresh("characters");
    expect(store.isLoading("characters")).toBe(true); // cold: no cache
    pendingRead.resolve(CHARACTERS);
    await first;
    expect(store.isLoading("characters")).toBe(false);

    const secondRead = deferred();
    listCharacters.mockReturnValueOnce(secondRead.promise);
    const second = store.refresh("characters");
    expect(store.isLoading("characters")).toBe(false); // warm: never "loading"
    secondRead.resolve(CHARACTERS);
    await second;
  });

  it("keeps the last good list when a read fails, and logs the failure", async () => {
    const store = useEntityListsStore();
    await store.refresh("sets");
    listPictureSets.mockRejectedValueOnce(new Error("network down"));

    await store.refresh("sets");

    expect(store.pictureSets).toEqual(SETS); // stale beats empty
    expect(console.warn).toHaveBeenCalledWith(
      expect.stringContaining("failed to fetch sets"),
      expect.any(Error),
    );
  });

  it("treats a non-array response as an empty list and logs it", async () => {
    const store = useEntityListsStore();
    listCharacters.mockResolvedValueOnce({ detail: "nope" });
    await store.refresh("characters");
    expect(store.characters).toEqual([]);
    expect(console.warn).toHaveBeenCalledWith(
      expect.stringContaining("unexpected characters response"),
      { detail: "nope" },
    );
  });

  it("separates a never-fetched list from a genuinely empty one", async () => {
    const store = useEntityListsStore();
    expect(store.has("projects")).toBe(false);
    listProjects.mockResolvedValueOnce([]);
    await store.refresh("projects");
    expect(store.has("projects")).toBe(true);
    expect(store.projects).toEqual([]);
  });

  // ── Invalidation is refetch-only (C2) ─────────────────────────────────────

  it("refetches every list on invalidate, taking contents only from the server", async () => {
    const store = useEntityListsStore();
    await store.invalidate();
    expect(listCharacters).toHaveBeenCalledTimes(1);
    expect(listPictureSets).toHaveBeenCalledTimes(1);
    expect(listProjects).toHaveBeenCalledTimes(1);
    expect(store.characters).toEqual(CHARACTERS);

    // A `characters_changed` ws event says "ask again" and nothing more: the
    // payload is never written in, so the store shows the server's answer.
    listCharacters.mockResolvedValueOnce([{ id: 9, name: "From server" }]);
    await store.invalidate(["characters"]);
    expect(store.characters).toEqual([{ id: 9, name: "From server" }]);
  });

  it("leaves the previous list on screen while a ws-driven refetch runs", async () => {
    const store = useEntityListsStore();
    await store.refresh("characters");
    const pendingRead = deferred();
    listCharacters.mockReturnValueOnce(pendingRead.promise);

    const running = store.invalidate(["characters"]);
    expect(store.characters).toEqual(CHARACTERS); // never blanked
    pendingRead.resolve([]);
    await running;
    expect(store.characters).toEqual([]);
  });

  it("refetches only the kind a local mutation touched", async () => {
    const store = useEntityListsStore();
    await store.invalidate();
    listCharacters.mockClear();
    listPictureSets.mockClear();

    // What AddToEntityControl does after adding pictures to a set, and after a
    // 404 says the cached set is gone.
    await store.invalidate(["sets"]);

    expect(listPictureSets).toHaveBeenCalledTimes(1);
    expect(listCharacters).not.toHaveBeenCalled();
  });

  it("ignores an unknown kind instead of firing an undefined request", async () => {
    const store = useEntityListsStore();
    await expect(store.refresh("everything")).resolves.toEqual([]);
    expect(console.warn).toHaveBeenCalledWith(
      expect.stringContaining("unknown kind"),
    );
  });

  // ── Auth-context reset (C1) ───────────────────────────────────────────────

  it("is empty the instant the auth context changes, before any refetch", async () => {
    const store = useEntityListsStore();
    await store.invalidate();
    expect(store.characters).toEqual(CHARACTERS);
    listCharacters.mockClear();
    listPictureSets.mockClear();
    listProjects.mockClear();

    transitionAuthContext(); // logout / login / share-token entry / vault switch

    // Synchronously empty — not "empty once a refetch comes back".
    expect(store.characters).toEqual([]);
    expect(store.pictureSets).toEqual([]);
    expect(store.projects).toEqual([]);
    for (const kind of ENTITY_KINDS) expect(store.has(kind)).toBe(false);
    expect(listCharacters).not.toHaveBeenCalled();
    expect(listPictureSets).not.toHaveBeenCalled();
    expect(listProjects).not.toHaveBeenCalled();
  });

  it("discards a response that was in flight across the transition", async () => {
    const store = useEntityListsStore();
    const previousSession = deferred();
    listCharacters.mockReturnValueOnce(previousSession.promise);
    const read = store.refresh("characters");

    transitionAuthContext();
    previousSession.resolve([{ id: 1, name: "Other credential's character" }]);
    await read;

    // The previous credential's rows must never land in the new session.
    expect(store.characters).toEqual([]);
    expect(store.has("characters")).toBe(false);
  });

  it("lets a fresh read repopulate after a transition", async () => {
    const store = useEntityListsStore();
    await store.refresh("characters");
    transitionAuthContext();
    listCharacters.mockResolvedValueOnce([{ id: 4, name: "New session" }]);
    await store.refresh("characters");
    expect(store.characters).toEqual([{ id: 4, name: "New session" }]);
  });

  it("never persists a scope-filtered list to browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const store = useEntityListsStore();
    await store.invalidate();
    await store.invalidate(["sets"]);
    transitionAuthContext();
    expect(setItem).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("entityLists")).toBeNull();
    expect(window.sessionStorage.getItem("entityLists")).toBeNull();
  });

  // ── Scope-aware fetching ──────────────────────────────────────────────────

  it("declines the projects read for a token scoped to a non-project resource", async () => {
    isReadOnly.value = true;
    sessionContext.value = { scope: "READ", resource_type: "character" };
    const store = useEntityListsStore();

    await store.refresh("projects");

    expect(listProjects).not.toHaveBeenCalled();
    expect(store.projects).toEqual([]);
    expect(store.has("projects")).toBe(true); // answered, not merely unasked
  });

  it("still reads projects for a project-scoped token", async () => {
    isReadOnly.value = true;
    sessionContext.value = { scope: "READ", resource_type: "project" };
    const store = useEntityListsStore();
    await store.refresh("projects");
    expect(listProjects).toHaveBeenCalledTimes(1);
    expect(store.projects).toEqual(PROJECTS);
  });
});
