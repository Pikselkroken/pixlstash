// The Guide (#2825): when the welcome shows, what "seen" writes, Start here,
// greying and search.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const sessionResets = vi.hoisted(() => []);
vi.mock("../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    isReadOnly: ref(false),
    onSessionReset: (fn) => {
      sessionResets.push(fn);
      return () => {};
    },
  };
});
vi.mock("../api/config", () => ({ patchUserConfig: vi.fn(async () => ({})) }));
vi.mock("../guide/needs", async (importOriginal) => ({
  ...(await importOriginal()),
  readNeeds: vi.fn(),
}));

import { patchUserConfig } from "../api/config";
import { readNeeds, firstUnmet } from "../guide/needs";
import { isReadOnly } from "../utils/apiClient";
import { GUIDE_CONCEPTS, matches } from "../guide/browse";
import {
  CONCEPT_BY_ID,
  EMPTY_LIBRARY_CONCEPTS,
  STARTUP_CONCEPTS,
  WELCOME_EDITION,
  startHereFor,
  useGuideStore,
} from "./useGuideStore";

const PICTURES = { picture: true, person: false };
const EMPTY = { picture: false, person: false };

function storeWith(config, met = PICTURES) {
  readNeeds.mockResolvedValue(met);
  const store = useGuideStore();
  store.hydrate(config);
  return store;
}

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  patchUserConfig.mockClear();
  readNeeds.mockReset();
});

describe("the welcome", () => {
  it("shows the six concepts on the first launch with pictures", async () => {
    const store = storeWith({ guide_welcome_seen: null });
    await store.evaluateWelcome({ libraryEmpty: false });
    expect(store.welcome).toEqual({
      concepts: STARTUP_CONCEPTS.map((c) => c.id),
      withNew: false,
    });
    expect(STARTUP_CONCEPTS.map((c) => c.id)).toEqual([
      "search",
      "similar-faces",
      "generate-comfyui",
      "edit-picture",
      "consistent-characters",
      "studio",
    ]);
  });

  it("does not show on a first launch with an empty library, and writes nothing", async () => {
    const store = storeWith({ guide_welcome_seen: null }, EMPTY);
    await store.evaluateWelcome({ libraryEmpty: true });
    expect(store.welcome).toBe(null);
    expect(patchUserConfig).not.toHaveBeenCalled();
  });

  it("does not show again for the edition already seen", async () => {
    const store = storeWith({ guide_welcome_seen: WELCOME_EDITION });
    await store.evaluateWelcome({ libraryEmpty: false });
    expect(store.welcome).toBe(null);
  });

  it("opens on New after a release adds a headline, even on an empty library", async () => {
    const store = storeWith({ guide_welcome_seen: "an-older-headline" }, EMPTY);
    await store.evaluateWelcome({ libraryEmpty: true });
    expect(store.welcome).toEqual({
      concepts: EMPTY_LIBRARY_CONCEPTS,
      withNew: true,
    });
  });

  it("is decided once per session", async () => {
    const store = storeWith({ guide_welcome_seen: null });
    await store.evaluateWelcome({ libraryEmpty: false });
    await store.closeWelcome();
    await store.evaluateWelcome({ libraryEmpty: false });
    expect(store.welcome).toBe(null);
  });

  it("is dropped when the session changes while the library is read", async () => {
    const store = storeWith({ guide_welcome_seen: null });
    let answer;
    readNeeds.mockReturnValue(new Promise((r) => (answer = r)));
    const pending = store.evaluateWelcome({ libraryEmpty: false });
    sessionResets.at(-1)();
    answer(PICTURES);
    await pending;
    expect(store.welcome).toBe(null);
  });

  it("a session change during the read lets the new session decide again", async () => {
    const store = storeWith({ guide_welcome_seen: null });
    readNeeds.mockReturnValueOnce(new Promise(() => {}));
    store.evaluateWelcome({ libraryEmpty: false });
    sessionResets.at(-1)();
    store.hydrate({ guide_welcome_seen: null });
    readNeeds.mockResolvedValue(PICTURES);
    await store.evaluateWelcome({ libraryEmpty: false });
    expect(store.welcome?.concepts).toHaveLength(6);
  });

  it("after a session change it waits for the new session's config", async () => {
    const store = storeWith({ guide_welcome_seen: WELCOME_EDITION });
    sessionResets.at(-1)();
    await store.evaluateWelcome({ libraryEmpty: false });
    expect(store.welcome).toBe(null);
    store.hydrate({ guide_welcome_seen: null });
    await store.evaluateWelcome({ libraryEmpty: false });
    expect(store.welcome?.concepts).toHaveLength(6);
  });

  it("closing records the edition in the owner's config", async () => {
    const store = storeWith({ guide_welcome_seen: null });
    await store.evaluateWelcome({ libraryEmpty: false });
    await store.closeWelcome();
    expect(store.welcome).toBe(null);
    expect(patchUserConfig).toHaveBeenCalledWith({
      guide_welcome_seen: WELCOME_EDITION,
    });
  });

  it("a read-only session neither shows it nor writes", async () => {
    isReadOnly.value = true;
    const store = storeWith({ guide_welcome_seen: null });
    await store.evaluateWelcome({ libraryEmpty: false });
    expect(store.welcome).toBe(null);
    await store.closeWelcome();
    expect(patchUserConfig).not.toHaveBeenCalled();
  });

  it("a replay is dropped when the session changes while the library is read", async () => {
    const store = storeWith({ guide_welcome_seen: WELCOME_EDITION });
    let answer;
    readNeeds.mockReturnValue(new Promise((r) => (answer = r)));
    const pending = store.replayWelcome();
    sessionResets.at(-1)();
    answer(PICTURES);
    await pending;
    expect(store.welcome).toBe(null);
  });

  it("Play the welcome again shows it with New, whatever was seen", async () => {
    const store = storeWith({ guide_welcome_seen: WELCOME_EDITION });
    await store.replayWelcome();
    expect(store.welcome?.withNew).toBe(true);
    expect(store.welcome?.concepts).toHaveLength(6);
  });
});

describe("reading the library", () => {
  it("a read orphaned by a session change does not free the newer read's slot", async () => {
    const store = useGuideStore();
    let finishOld;
    readNeeds.mockReturnValueOnce(new Promise((r) => (finishOld = r)));
    store.refresh();
    sessionResets.at(-1)();
    readNeeds.mockReturnValueOnce(new Promise(() => {}));
    store.refresh();
    finishOld(PICTURES);
    await Promise.resolve();
    await Promise.resolve();
    store.refresh();
    expect(readNeeds).toHaveBeenCalledTimes(2);
  });
});

describe("Start here and greying", () => {
  it("follows the catalogue's three rules", () => {
    expect(startHereFor({ picture: false })).toEqual([
      "import-pictures",
      "generate-comfyui",
    ]);
    expect(startHereFor({ picture: true, person: false })).toEqual([
      "people",
      "similar-faces",
    ]);
    expect(startHereFor({ picture: true, person: true })).toEqual([
      "consistent-characters",
      "studio",
    ]);
  });

  it("an empty library leaves exactly the catalogue's seven concepts lit", async () => {
    const store = storeWith({}, {
      picture: false,
      pictures2: false,
      face: false,
      person: false,
      personWithPictures: false,
      recipe: false,
      ownWorkflow: false,
    });
    await store.refresh();
    const lit = GUIDE_CONCEPTS.filter((c) => store.stateOf(c) !== "grey").map(
      (c) => c.id,
    );
    expect(lit.sort()).toEqual(
      [
        "api",
        "generate-comfyui",
        "import-pictures",
        "libraries",
        "models",
        "people",
      ].sort(),
    );
    expect(store.stateOf(CONCEPT_BY_ID.new)).not.toBe("grey");
    expect(store.stateOf(CONCEPT_BY_ID["import-pictures"])).toBe("start");
  });

  it("a need that could not be read counts as met; an any-of need takes one", () => {
    expect(firstUnmet(["picture"], {})).toBe(null);
    expect(firstUnmet(["picture"], { picture: false })).toBe("picture");
    expect(firstUnmet([["set", "person"]], { set: false, person: true })).toBe(
      null,
    );
    expect(firstUnmet([["set", "person"]], { set: false, person: false })).toBe(
      "set",
    );
  });
});

describe("search", () => {
  it("matches names, task names and search terms from the start of a word", () => {
    const hits = (q) =>
      GUIDE_CONCEPTS.filter((c) => matches(c, q)).map((c) => c.id);
    expect(hits("dedupe")).toContain("stacks-duplicates");
    expect(hits("COLOR")).toContain("search");
    expect(hits("txt2img")).toContain("generate-comfyui");
    expect(hits("suggest more")).toContain("similar-faces");
    expect(hits("upe")).not.toContain("stacks-duplicates");
    expect(hits("zzzz")).toEqual([]);
  });
});
