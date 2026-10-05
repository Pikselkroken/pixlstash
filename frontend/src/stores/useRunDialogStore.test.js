// useRunDialogStore.test.js - App.vue keys the Run popup on `runOpened`, so a
// popup opened over an open one mounts fresh instead of keeping its edits.

import { beforeEach, describe, it, expect } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { useRunDialogStore } from "./useRunDialogStore";

describe("useRunDialogStore", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("gives every open a new key, even over an open popup", () => {
    const store = useRunDialogStore();
    store.openRun({ kind: "picture", pictureIds: [1] });
    const first = store.runOpened;
    store.openRun({ kind: "picture", pictureIds: [2] });
    expect(store.runOpened).not.toBe(first);
    expect(store.source.pictureIds).toEqual([2]);
  });
});
