import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const { listFavourites, setFavourites } = vi.hoisted(() => ({
  listFavourites: vi.fn(),
  setFavourites: vi.fn(),
}));
vi.mock("../api/favourites", () => ({ listFavourites, setFavourites }));

import { favouritesFirst, useFavouritesStore } from "./useFavouritesStore";
import { useNoticeStore } from "./useNoticeStore";

const answer = (over = {}) => ({ model: [], workflow: [], set: [], ...over });
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  setActivePinia(createPinia());
  listFavourites.mockReset();
  setFavourites.mockReset();
});

describe("favouritesFirst", () => {
  it("leads with favourites and keeps both halves in their order", () => {
    const fav = new Set(["c", "a"]);
    expect(favouritesFirst(["a", "b", "c", "d"], (x) => fav.has(x))).toEqual([
      "a",
      "c",
      "b",
      "d",
    ]);
  });

  it("returns the list unchanged with no favourites", () => {
    expect(favouritesFirst(["b", "a"], () => false)).toEqual(["b", "a"]);
  });
});

describe("useFavouritesStore", () => {
  it("reads once and answers per kind", async () => {
    listFavourites.mockResolvedValue(answer({ model: ["sha-a"], set: ["hand:1"] }));
    const store = useFavouritesStore();
    await store.ensureLoaded();
    await store.ensureLoaded();
    expect(listFavourites).toHaveBeenCalledTimes(1);
    expect(store.isFavourite("model", "sha-a")).toBe(true);
    expect(store.isFavourite("set", "sha-a")).toBe(false);
    expect(store.isFavourite("model", null)).toBe(false);
  });

  it("toggles a mixed selection on and an all-favourite one off", async () => {
    const store = useFavouritesStore();
    store.keys = answer({ workflow: ["w1"] });
    setFavourites.mockResolvedValue(answer({ workflow: ["w1", "w2"] }));
    await store.toggle("workflow", ["w1", "w2"]);
    expect(setFavourites).toHaveBeenLastCalledWith("workflow", ["w1", "w2"], true);
    expect(store.allFavourite("workflow", ["w1", "w2"])).toBe(true);

    setFavourites.mockResolvedValue(answer());
    await store.toggle("workflow", ["w1", "w2"]);
    expect(setFavourites).toHaveBeenLastCalledWith("workflow", ["w1", "w2"], false);
    expect(store.isFavourite("workflow", "w1")).toBe(false);
  });

  it("skips keyless items and writes nothing when none are left", async () => {
    const store = useFavouritesStore();
    await store.setFavourite("model", [null, ""], true);
    expect(setFavourites).not.toHaveBeenCalled();
    expect(store.allFavourite("model", [null])).toBe(false);
  });

  it("puts the previous marks back, re-reads, and says so when the write is refused", async () => {
    const store = useFavouritesStore();
    store.keys = answer({ model: ["sha-a"] });
    setFavourites.mockRejectedValue(new Error("offline"));
    listFavourites.mockResolvedValue(answer({ model: ["sha-a"] }));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await store.setFavourite("model", ["sha-b"], true);
    await flush();
    expect(store.isFavourite("model", "sha-b")).toBe(false);
    expect(store.isFavourite("model", "sha-a")).toBe(true);
    expect(listFavourites).toHaveBeenCalledTimes(1);
    expect(useNoticeStore().notices.map((n) => n.level)).toContain("error");
    warn.mockRestore();
  });

  it("an older refused write does not undo what a newer write saved", async () => {
    const store = useFavouritesStore();
    let refuse;
    setFavourites.mockImplementationOnce(
      () => new Promise((_, reject) => (refuse = reject)),
    );
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const older = store.setFavourite("model", ["sha-a"], true);
    setFavourites.mockResolvedValueOnce(answer({ model: ["sha-a"] }));
    await store.setFavourite("model", ["sha-a"], true);
    refuse(new Error("offline"));
    await older;
    expect(store.isFavourite("model", "sha-a")).toBe(true);
    expect(listFavourites).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("when the newest write is refused too, the server decides both", async () => {
    const store = useFavouritesStore();
    const refusals = [];
    setFavourites.mockImplementation(
      () => new Promise((_, reject) => refusals.push(reject)),
    );
    listFavourites.mockResolvedValue(answer({ set: ["hand:1"] }));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const first = store.setFavourite("model", ["sha-a"], true);
    const second = store.setFavourite("set", ["hand:2"], true);
    refusals[0](new Error("offline"));
    await first;
    expect(store.isFavourite("model", "sha-a")).toBe(true);
    refusals[1](new Error("offline"));
    await second;
    await flush();
    expect(store.isFavourite("model", "sha-a")).toBe(false);
    expect(store.isFavourite("set", "hand:2")).toBe(false);
    expect(store.isFavourite("set", "hand:1")).toBe(true);
    warn.mockRestore();
  });

  it("does not let an older answer land over a newer write", async () => {
    const store = useFavouritesStore();
    let first;
    setFavourites.mockImplementationOnce(
      () => new Promise((resolve) => (first = resolve)),
    );
    const older = store.setFavourite("workflow", ["w1"], true);
    setFavourites.mockResolvedValueOnce(answer());
    await store.setFavourite("workflow", ["w1"], false);
    first(answer({ workflow: ["w1"] }));
    await older;
    expect(store.isFavourite("workflow", "w1")).toBe(false);
  });
});
