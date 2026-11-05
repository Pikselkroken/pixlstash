import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

const isReadOnly = ref(false);
vi.mock("../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  get isReadOnly() {
    return isReadOnly;
  },
}));
vi.mock("../api/config", () => ({ patchUserConfig: vi.fn() }));

import { patchUserConfig } from "../api/config";
import { useEntityListsStore } from "./useEntityListsStore";
import { useNoticeStore } from "./useNoticeStore";
import { useSidebarStore } from "./useSidebarStore";
import {
  ALL_DONE_MS,
  STEP_IDS,
  releaseOf,
  useWhatsChangedStore,
} from "./useWhatsChangedStore";

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  patchUserConfig.mockReset();
  patchUserConfig.mockResolvedValue({});
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

/** A store hydrated from a config, then evaluated for a launch. */
async function launch({ cfg = {}, version = "2.0.0", libraryEmpty = false } = {}) {
  const store = useWhatsChangedStore();
  store.hydrate(cfg);
  const upgraded = await store.evaluate({ version, libraryEmpty });
  return { store, upgraded };
}

const upgradeState = () => ({ done: [], checklist: true, card: true });
const notices = () => useNoticeStore().notices;

describe("releaseOf", () => {
  it("reads major.minor, dev builds included", () => {
    expect(releaseOf("2.0.0.dev.3")).toEqual([2, 0]);
    expect(releaseOf("1.12.4")).toEqual([1, 12]);
    expect(releaseOf("")).toBeNull();
    expect(releaseOf(null)).toBeNull();
  });
});

describe("the upgrade test", () => {
  it("a library with pictures and no stored version is an upgrade", async () => {
    const sidebar = useSidebarStore();
    sidebar.statsOpen = false;
    sidebar.sidebarPinned = false;
    sidebar.sidebarDocked = true;
    const { store, upgraded } = await launch({ version: "2.0.0.dev.3" });
    // Both rails on screen: the checklist's and the card's.
    expect(sidebar.statsOpen).toBe(true);
    expect(sidebar.sidebarPinned).toBe(true);
    expect(sidebar.sidebarDocked).toBe(false);
    expect(upgraded).toBe(true);
    expect(store.checklistShown).toBe(true);
    expect(store.railShown).toBe(true);
    expect(store.card).toBe(true);
    expect(patchUserConfig).toHaveBeenCalledWith({
      last_seen_version: "2.0.0.dev.3",
      whats_changed: upgradeState(),
    });
  });

  it("an older stored version is an upgrade even on an empty library", async () => {
    const { store, upgraded } = await launch({
      cfg: { last_seen_version: "1.12.0" },
      libraryEmpty: true,
    });
    expect(upgraded).toBe(true);
    expect(store.checklistShown).toBe(true);
  });

  it("a new install stores the version and shows nothing", async () => {
    const sidebar = useSidebarStore();
    sidebar.statsOpen = false;
    const { store, upgraded } = await launch({ libraryEmpty: true });
    expect(sidebar.statsOpen).toBe(false);
    expect(upgraded).toBe(false);
    expect(store.checklistShown).toBe(false);
    expect(store.card).toBe(false);
    expect(patchUserConfig).toHaveBeenCalledWith({ last_seen_version: "2.0.0" });
  });

  it("a version already seen keeps the stored state and writes nothing", async () => {
    const { store, upgraded } = await launch({
      cfg: {
        last_seen_version: "2.0.0",
        whats_changed: { done: ["click", "bogus"], checklist: true, card: false },
      },
    });
    expect(upgraded).toBeFalsy();
    expect(patchUserConfig).not.toHaveBeenCalled();
    expect(store.done).toEqual(["click"]);
    expect(store.checklistShown).toBe(true);
    expect(store.card).toBe(false);
  });

  it("a config with nothing stored clears what an earlier read held", () => {
    const store = useWhatsChangedStore();
    store.hydrate({ whats_changed: { done: ["click"], checklist: true, card: true } });
    store.hydrate({ whats_changed: null });
    expect(store.done).toEqual([]);
    expect(store.checklist).toBe(false);
    expect(store.card).toBe(false);
  });

  it("a 1.x build, a read-only session or an unread config do nothing", async () => {
    expect((await launch({ version: "1.12.0" })).upgraded).toBeFalsy();
    setActivePinia(createPinia());
    isReadOnly.value = true;
    expect((await launch()).upgraded).toBeFalsy();
    isReadOnly.value = false;
    setActivePinia(createPinia());
    const store = useWhatsChangedStore();
    expect(await store.evaluate({ version: "2.0.0", libraryEmpty: false })).toBeFalsy();
    expect(patchUserConfig).not.toHaveBeenCalled();
  });

  it("decides once per session", async () => {
    const { store } = await launch();
    patchUserConfig.mockClear();
    expect(await store.evaluate({ version: "2.0.0", libraryEmpty: false })).toBeFalsy();
    expect(patchUserConfig).not.toHaveBeenCalled();
  });
});

describe("ticking", () => {
  it("ticks once, opens the step, and stores the list", async () => {
    const { store } = await launch();
    patchUserConfig.mockClear();
    store.tick("menu");
    store.tick("menu");
    expect(store.done).toEqual(["menu"]);
    expect(store.openStep).toBe("menu");
    expect(patchUserConfig).toHaveBeenCalledTimes(1);
    expect(patchUserConfig).toHaveBeenCalledWith({
      whats_changed: { done: ["menu"], checklist: true, card: true },
    });
  });

  it("ticks nothing while the checklist is not up", async () => {
    const { store } = await launch({ libraryEmpty: true });
    store.tick("click");
    expect(store.done).toEqual([]);
  });

  it("the last tick says so, then the tab sees itself out with Undo", async () => {
    const { store } = await launch();
    for (const id of STEP_IDS) store.tick(id);
    expect(store.allDone).toBe(true);
    expect(store.checklist).toBe(true);
    vi.advanceTimersByTime(ALL_DONE_MS);
    expect(store.checklist).toBe(false);
    expect(store.allDone).toBe(false);
    const toast = notices().find((n) => n.text.startsWith("Fine, I'm going."));
    expect(toast?.action?.label).toBe("Undo");
  });
});

describe("dismissing and coming back", () => {
  it("Get outta here hides the list, keeps the card, and Undo restores the ticks", async () => {
    const { store } = await launch();
    store.tick("click");
    store.dismissChecklist();
    expect(store.checklistShown).toBe(false);
    expect(store.railTab).toBe("details");
    expect(store.card).toBe(true);
    const toast = notices().find((n) => n.text.startsWith("Fine, I'm going."));
    expect(toast.text).toContain("It's in Settings, beside the version");
    toast.action.handler();
    expect(store.checklistShown).toBe(true);
    expect(store.railTab).toBe("changes");
    expect(store.done).toEqual(["click"]);
    expect(notices().some((n) => n.text === "Knew you'd be back.")).toBe(true);
  });

  it("the card goes on its own and leaves the checklist alone", async () => {
    const { store } = await launch();
    store.dismissCard();
    expect(store.card).toBe(false);
    expect(store.checklistShown).toBe(true);
    const toast = notices().find((n) => n.text.startsWith("Suit yourself."));
    toast.action.handler();
    expect(store.card).toBe(true);
  });

  it("Settings' What's new brings back both, rails open", async () => {
    const { store } = await launch();
    store.dismissChecklist();
    store.dismissCard();
    useSidebarStore().statsOpen = false;
    patchUserConfig.mockClear();
    store.reopen();
    expect(store.reopened).toBe(1);
    expect(store.railTab).toBe("changes");
    expect(useSidebarStore().statsOpen).toBe(true);
    expect(store.checklistShown).toBe(true);
    expect(store.card).toBe(true);
    expect(patchUserConfig).toHaveBeenCalledWith({
      whats_changed: upgradeState(),
    });
  });

  it("finishing the tour removes the card", async () => {
    const { store } = await launch();
    store.tourStep = 4;
    store.finishTour();
    expect(store.tourStep).toBe(0);
    expect(store.card).toBe(false);
  });
});

describe("the tour's subject", () => {
  function lists(characters, sets) {
    const entity = useEntityListsStore();
    entity.lists = { characters, sets, projects: [] };
  }

  it("is the person with the most pictures", () => {
    lists(
      [
        { id: 1, name: "Someone", image_count: 40 },
        { id: 2, name: "Example", image_count: 812 },
      ],
      [{ id: 9, name: "Foo", picture_count: 5000 }],
    );
    expect(useWhatsChangedStore().tourSubject).toEqual({
      type: "character",
      id: 2,
      name: "Example",
      count: 812,
    });
  });

  it("falls back to the largest set, and with neither there is no card", async () => {
    lists([{ id: 1, name: "Empty", image_count: 0 }], [
      { id: 9, name: "Foo", picture_count: 64 },
    ]);
    const store = useWhatsChangedStore();
    expect(store.tourSubject).toMatchObject({ type: "set", id: 9 });
    lists([], []);
    store.card = true;
    expect(store.tourSubject).toBeNull();
    expect(store.cardShown).toBe(false);
  });
});
