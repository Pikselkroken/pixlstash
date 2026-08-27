// sessionReset.test.js - every Pinia store holding server-sourced data drops it
// the instant the auth context changes (issue #655).
//
// The completeness discipline in CLAUDE.md applies: this file is the executable
// half of the store matrix in docs/frontend_architecture.md §4. A store that
// caches server rows and is NOT listed here is the bug, which is why the last
// test in this file walks the store directory and fails on an unclassified one
// rather than trusting the list to be kept up to date by hand.
//
// Both directions are asserted, per CLAUDE.md's rule that over-blocking is its
// own regression: the cache is empty after the transition, AND it repopulates
// on the next read.

import { beforeEach, afterEach, describe, it, expect, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";

const { axiosInstance } = vi.hoisted(() => ({
  axiosInstance: {
    get: vi.fn().mockResolvedValue({ data: {} }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
  },
}));

// The real apiClient is the point of the test - it owns `notifySessionReset`,
// the chokepoint every store registers on. Only the transport is stubbed.
vi.mock("axios", () => ({ default: { create: () => axiosInstance } }));

const { getLockedMembers } = vi.hoisted(() => ({ getLockedMembers: vi.fn() }));
vi.mock("../api/pictureSets", async (importOriginal) => ({
  ...(await importOriginal()),
  getLockedMembers,
}));

const { listCharacters } = vi.hoisted(() => ({ listCharacters: vi.fn() }));
vi.mock("../api/characters", async (importOriginal) => ({
  ...(await importOriginal()),
  listCharacters,
}));

import { notifySessionReset } from "../utils/apiClient";
import { useLockedSetsStore } from "./useLockedSetsStore";
import { useEntityNamesStore } from "./useEntityNamesStore";
import { useEntityListsStore } from "./useEntityListsStore";
import { useProjectStore } from "./useProjectStore";
import { useSelectionStore } from "./useSelectionStore";
import { useSnapshotsStore } from "./useSnapshotsStore";
import { useDedupStore } from "./useDedupStore";
import { useReviewSessionsStore } from "./useReviewSessionsStore";
import { useOperationStore } from "./useOperationStore";
import { useLibrariesStore } from "./useLibrariesStore";
import { useModelShelfStore } from "./useModelShelfStore";
import { useModelFoldersStore } from "./useModelFoldersStore";
import { useFolderMappingStore } from "./useFolderMappingStore";
import { useModelMovesStore } from "./useModelMovesStore";
import { useMovesStore } from "./useMovesStore";

/**
 * The matrix. One row per store that holds server-sourced data: how to fill it
 * with a previous credential's rows, and what "dropped" means for it.
 */
const STORES = [
  {
    // The model rows are hub-side facts about this machine, but each one
    // carries the characters and sets in the ACTIVE LIBRARY that use it, so
    // the page is stale the moment the credential changes. The `Show`
    // selection is deliberately kept: it is the user's own preference and
    // holds no ids.
    name: "useModelShelfStore",
    use: useModelShelfStore,
    seed: (s) => {
      s.rows = [
        {
          id: 1,
          file_kind: "adapter",
          attachments: [{ entity_type: "character", entity_id: 9 }],
        },
      ];
    },
    isEmpty: (s) => s.rows.length === 0,
  },
  {
    // Absolute paths on the host machine, readable only by the owner. A share
    // or read-only session could never have asked for them, so none of it may
    // survive the transition, and any scan being waited on is abandoned with
    // it, because this session no longer has standing to poll for it.
    name: "useModelFoldersStore",
    use: useModelFoldersStore,
    seed: (s) => {
      s.folders = [
        { id: 1, path: "/home/g/loras", kind: "user", file_count: 91 },
      ];
      s.loaded = true;
    },
    isEmpty: (s) => s.folders.length === 0 && !s.loaded,
  },
  {
    // A move names registered folders by id and shifts files between absolute
    // host paths, so it is owner-only for the same reason the registry is. The
    // job itself carries on server-side - this session simply stops watching
    // it, because it no longer has standing to ask.
    name: "useModelMovesStore",
    use: useModelMovesStore,
    seed: (s) => {
      s.job = {
        status: "running",
        total: 4,
        done: 1,
        results: [],
      };
    },
    isEmpty: (s) => s.job === null && s.status === "idle",
  },
  {
    // v1.11 Phase 3: a host path and the read task id that maps it, same
    // owner-only reasoning as useModelFoldersStore above.
    name: "useFolderMappingStore",
    use: useFolderMappingStore,
    seed: (s) => {
      s.save({ taskId: "abc123", path: "/home/me/Pictures", label: "Pictures" });
    },
    isEmpty: (s) => s.pending === null,
  },
  {
    name: "useLockedSetsStore",
    use: useLockedSetsStore,
    seed: (s) => {
      s.sets = [{ id: 7, name: "Frozen set", picture_ids: [11, 12] }];
    },
    isEmpty: (s) => s.sets.length === 0 && !s.isLocked(11),
  },
  {
    name: "useEntityNamesStore",
    use: useEntityNamesStore,
    seed: (s) => {
      s.mergeCharacterNames([{ id: 1, name: "Ada" }]);
      s.mergeSetNames([{ id: 2, name: "Holiday" }]);
      s.mergeProjectNames([{ id: 3, name: "Client work" }]);
      s.mergeRefFolderLabels([{ id: 4, label: "refs" }]);
      s.mergeImportFolderLabels([{ id: 5, label: "inbox" }]);
    },
    isEmpty: (s) =>
      [
        s.characterNames,
        s.setNames,
        s.projectNames,
        s.refFolderLabels,
        s.importFolderLabels,
      ].every((map) => Object.keys(map).length === 0),
  },
  {
    name: "useEntityListsStore",
    use: useEntityListsStore,
    seed: (s) => {
      s.lists = { characters: [{ id: 1 }], sets: [{ id: 2 }], projects: [] };
    },
    isEmpty: (s) =>
      s.characters.length === 0 &&
      s.pictureSets.length === 0 &&
      s.projects.length === 0,
  },
  {
    name: "useProjectStore",
    use: useProjectStore,
    seed: (s) => {
      s.characterProjectIds = { 1: 9 };
      s.setProjectIds = { 2: 9 };
      s.selectedProjectId = 9;
      s.projectViewMode = "project";
    },
    isEmpty: (s) =>
      Object.keys(s.characterProjectIds).length === 0 &&
      Object.keys(s.setProjectIds).length === 0 &&
      s.selectedProjectId === null &&
      s.projectViewMode === "global",
  },
  {
    name: "useSelectionStore",
    use: useSelectionStore,
    seed: (s) => {
      s.selectedSetIds = [2];
      s.selectedSetNames = { 2: "Holiday" };
      s.selectedImageIds = [11, 12];
      s.selectedCharacterIds = [1];
    },
    isEmpty: (s) =>
      s.selectedSetIds.length === 0 &&
      Object.keys(s.selectedSetNames).length === 0 &&
      s.selectedImageIds.length === 0 &&
      s.selectedCharacterIds.length === 0,
  },
  {
    name: "useSnapshotsStore",
    use: useSnapshotsStore,
    seed: (s) => {
      s.snapshots = [{ id: 1, label: "Before the big import" }];
      s.activeJob = { kind: "RESTORE" };
    },
    isEmpty: (s) => s.snapshots.length === 0 && s.activeJob === null,
  },
  {
    name: "useDedupStore",
    use: useDedupStore,
    seed: (s) => {
      s.groups = [{ signature: "abc", candidates: [{ picture_id: 11 }] }];
      s.total = 1;
      s.openCount = 4;
      s.scopeCounts = { "set:2": 4 };
      s.mixedStacks = [{ id: 5 }];
    },
    isEmpty: (s) =>
      s.groups.length === 0 &&
      s.total === 0 &&
      s.openCount === 0 &&
      Object.keys(s.scopeCounts).length === 0 &&
      s.mixedStacks.length === 0,
  },
  {
    name: "useReviewSessionsStore",
    use: useReviewSessionsStore,
    seed: (s) => {
      s.sessions = [{ id: 1, tag: "blurry" }];
      s.archived = [{ id: 2, tag: "duplicate" }];
      s.healthRows = [{ tag: "blurry" }];
      s.details = { 1: { receipt: {} } };
    },
    isEmpty: (s) =>
      s.sessions.length === 0 &&
      s.archived.length === 0 &&
      s.healthRows.length === 0 &&
      Object.keys(s.details).length === 0,
  },
  {
    name: "useOperationStore",
    use: useOperationStore,
    seed: (s) => {
      s.operations = [{ id: 1, status: "applied", op_type: "pictures.score" }];
      s.canUndo = true;
    },
    isEmpty: (s) => s.operations.length === 0 && s.canUndo === false,
  },
  {
    name: "useLibrariesStore",
    use: useLibrariesStore,
    seed: (s) => {
      s.libraries = [{ uuid: "a", name: "Main", is_active: true }];
      s.canManage = true;
      s.cliHint = "pixlstash --library /srv/main";
      s.hasLoadedSuccessfully = true;
    },
    isEmpty: (s) =>
      s.libraries.length === 0 &&
      s.canManage === false &&
      s.cliHint === "" &&
      s.hasLoadedSuccessfully === false,
  },
  {
    // v1.11 Phase 5. Every row names a picture in the previous credential's
    // library, so it is exactly the class of server-sourced cache §655 is
    // about.
    name: "useMovesStore",
    use: useMovesStore,
    seed: (s) => {
      s.unambiguous = [{ review_id: 1, picture_id: 11 }];
      s.ambiguous = [{ review_id: 2, picture_id: 12 }];
      s.offLayout = [{ review_id: 3, picture_id: 13 }];
      s.loaded = true;
    },
    isEmpty: (s) =>
      s.unambiguous.length === 0 &&
      s.ambiguous.length === 0 &&
      s.offLayout.length === 0 &&
      s.loaded === false,
  },
];

beforeEach(() => {
  setActivePinia(createPinia());
  getLockedMembers.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("every store holding server data drops it on an auth-context change", () => {
  for (const { name, use, seed, isEmpty } of STORES) {
    it(`${name} is empty the instant the credential changes`, () => {
      const store = use();
      seed(store);
      expect(isEmpty(store)).toBe(false); // the seed actually seeded

      notifySessionReset("test");

      expect(isEmpty(store)).toBe(true);
    });
  }

  // One reset must reach EVERY registered store, not just the one under test.
  // A store that silently failed to register would still pass its own case
  // above if something else cleared it; this catches that.
  it("drops all of them from a single notification", () => {
    const instances = STORES.map(({ use, seed, isEmpty }) => {
      const store = use();
      seed(store);
      return { store, isEmpty };
    });

    notifySessionReset("test");

    for (const { store, isEmpty } of instances) {
      expect(isEmpty(store)).toBe(true);
    }
  });
});

// The other direction. Over-blocking is its own regression: a store that empties
// and then stays empty is as broken as one that never empties.
describe("the caches repopulate for the next credential", () => {
  it("useLockedSetsStore reads again after the reset", async () => {
    const store = useLockedSetsStore();
    getLockedMembers.mockResolvedValue({
      sets: [{ id: 7, name: "Frozen", picture_ids: [11] }],
    });
    await store.fetch();
    expect(store.isLocked(11)).toBe(true);

    notifySessionReset("test");
    expect(store.sets).toEqual([]);

    getLockedMembers.mockResolvedValue({
      sets: [{ id: 8, name: "The next one's set", picture_ids: [99] }],
    });
    await store.fetch();
    expect(store.isLocked(99)).toBe(true);
    expect(store.isLocked(11)).toBe(false);
  });

  it("useEntityNamesStore resolves names again after the reset", () => {
    const store = useEntityNamesStore();
    store.mergeCharacterNames([{ id: 1, name: "Ada" }]);
    notifySessionReset("test");
    store.mergeCharacterNames([{ id: 1, name: "Someone else" }]);
    expect(store.characterNames).toEqual({ 1: "Someone else" });
  });
});

// The window the epoch guards exist to close: a read that was already on the
// wire when the credential changed must be DISCARDED, not written into the new
// session's cache. Without the guard the store is empty for a few hundred
// milliseconds and then quietly refills with the previous credential's rows,
// which is the same leak with a delay on it.
describe("a response in flight across the reset is discarded", () => {
  it("useLockedSetsStore drops a read that started before the change", async () => {
    const store = useLockedSetsStore();
    let release;
    getLockedMembers.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({ sets: [{ id: 7, picture_ids: [11] }] });
      }),
    );

    const pending = store.fetch();
    notifySessionReset("test");
    release();
    await pending;

    expect(store.sets).toEqual([]);
    expect(store.isLocked(11)).toBe(false);
  });

  it("useEntityListsStore drops a list read that started before the change", async () => {
    const store = useEntityListsStore();
    let release;
    listCharacters.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve([{ id: 1, name: "Ada" }]);
      }),
    );

    const pending = store.refresh("characters");
    notifySessionReset("test");
    release();
    await pending;

    expect(store.characters).toEqual([]);
  });
});

// Arithmetic completeness, not judgement (CLAUDE.md). The matrix above is only
// trustworthy if a NEW store cannot quietly skip it, so the directory is walked
// and every store must appear in exactly one of the two lists. A store added
// without a decision recorded here fails this test rather than being discovered
// by a later incident - the same shape as the backend's
// `test_all_routes_declare_access_policy` guardrail.
describe("the store matrix is complete", () => {
  // Stores that hold NO server-sourced data, with the reason each is exempt.
  // Purely local UI state, or client-side preferences that are the user's own
  // and carry no authorization decision in their content.
  const NO_SERVER_DATA = {
    "useExportStore.js": "export dialog form state",
    "useFilterStore.js": "filter form state; ids come from the route",
    "useGenStackPrefsStore.js": "localStorage view preference",
    "useGridStore.js": "grid layout and display toggles",
    "useNoticeStore.js": "transient toast queue",
    "useScrapheapRetentionStore.js":
      "server-level policy from server-config.json, identical for every " +
      "credential - no scope dimension, so nothing to leak between them",
    "useSearchStore.js": "the user's own query text and history",
    "useSidebarStore.js": "sidebar open/collapsed state",
    "useSortStore.js": "the active sort order",
    "useTasksStore.js":
      "server-wide worker progress, owner-only, re-polled every few seconds",
    "useUserPrefsStore.js":
      "the owner's own preferences, re-hydrated from /config on login; " +
      "clearing them mid-transition would flash the theme (see the issue)",
    "useViewStore.js": "which view is open",
    "useWsStore.js": "the per-tab client id and socket status",
  };

  it("classifies every store as either reset-on-session-change or no-server-data", () => {
    const modules = import.meta.glob("./use*.js");
    const files = Object.keys(modules)
      .map((path) => path.replace("./", ""))
      .filter((name) => !name.endsWith(".test.js"));

    // A glob that resolved to nothing would make every assertion below pass
    // vacuously, which is the one way this guardrail could silently stop
    // guarding anything.
    expect(files.length).toBeGreaterThanOrEqual(20);

    const covered = new Set(STORES.map(({ name }) => `${name}.js`));
    const exempt = new Set(Object.keys(NO_SERVER_DATA));

    // Likewise, a renamed store would silently drop out of `covered`.
    for (const name of covered) expect(files).toContain(name);

    const unclassified = files.filter(
      (name) => !covered.has(name) && !exempt.has(name),
    );

    expect(unclassified).toEqual([]);
    // And the two lists must not disagree about the same store.
    expect(files.filter((n) => covered.has(n) && exempt.has(n))).toEqual([]);
  });
});
