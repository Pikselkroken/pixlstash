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

  it("puts back only what the refused write changed", async () => {
    const store = useFavouritesStore();
    let refuse;
    setFavourites.mockImplementationOnce(
      () => new Promise((_, reject) => (refuse = reject)),
    );
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const failing = store.setFavourite("model", ["sha-a"], true);
    setFavourites.mockResolvedValueOnce(answer({ set: ["hand:1"], model: ["sha-a"] }));
    await store.setFavourite("set", ["hand:1"], true);
    refuse(new Error("offline"));
    await failing;
    expect(store.isFavourite("model", "sha-a")).toBe(false);
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
