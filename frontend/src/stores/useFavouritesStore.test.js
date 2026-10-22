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

  it("puts the previous marks back and says so when the write is refused", async () => {
    const store = useFavouritesStore();
    store.keys = answer({ model: ["sha-a"] });
    setFavourites.mockRejectedValue(new Error("offline"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    await store.setFavourite("model", ["sha-b"], true);
    expect(store.isFavourite("model", "sha-b")).toBe(false);
    expect(store.isFavourite("model", "sha-a")).toBe(true);
    expect(useNoticeStore().notices.map((n) => n.level)).toContain("error");
    warn.mockRestore();
  });
});
