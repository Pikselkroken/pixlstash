// Text in the picture (#1197): the lightbox's word selection, which tab a
// picture opens on, and when the Text tab exists at all.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { nextTick, ref } from "vue";
import { setActivePinia, createPinia } from "pinia";

const getPictureText = vi.fn();
const readPictureText = vi.fn();

vi.mock("../api/pictures", () => ({
  getPictureText: (...a) => getPictureText(...a),
  readPictureText: (...a) => readPictureText(...a),
}));

import {
  DESCRIPTION_TAB,
  TEXT_TAB,
  joinPictureText,
  nextWordSelection,
  usePictureText,
} from "./usePictureText";

const w = (text, matched = false) => ({
  text,
  box: [0.1, 0.1, 0.1, 0.05],
  matched,
});

const READ = {
  state: "read",
  lines: [[w("BAKERY"), w("No.4")], [w("2"), w("C0FFEE", true), w("7.00")]],
};
const PLAIN = { state: "read", lines: [[w("EXIT")]] };

/** Let the immediate load's promise settle. */
const settle = async () => {
  await Promise.resolve();
  await Promise.resolve();
  await nextTick();
};

function setup(query = "") {
  const pictureId = ref(1);
  const text = usePictureText({ pictureId, getSearchQuery: () => query });
  return { pictureId, text };
}

beforeEach(() => {
  setActivePinia(createPinia());
  getPictureText.mockReset();
  readPictureText.mockReset();
});

describe("nextWordSelection", () => {
  it("a click selects that word alone", () => {
    expect(nextWordSelection([], 3, false)).toEqual([3]);
    expect(nextWordSelection([1, 2], 3, false)).toEqual([3]);
  });

  it("clicking the only selected word clears", () => {
    expect(nextWordSelection([3], 3, false)).toEqual([]);
  });

  it("a click on one word of several selects just that word", () => {
    expect(nextWordSelection([1, 3], 3, false)).toEqual([3]);
  });

  it("shift-click adds and removes, kept in reading order", () => {
    expect(nextWordSelection([4], 1, true)).toEqual([1, 4]);
    expect(nextWordSelection([1, 4], 4, true)).toEqual([1]);
  });
});

describe("joinPictureText", () => {
  it("joins words by spaces and lines by newlines", () => {
    expect(joinPictureText(READ.lines)).toBe("BAKERY No.4\n2 C0FFEE 7.00");
  });
});

describe("usePictureText - which tab exists", () => {
  it("no text: Description only, and Text cannot be picked", async () => {
    getPictureText.mockResolvedValue({ state: "none", lines: [] });
    const { text } = setup();
    await settle();
    expect(text.hasText.value).toBe(false);
    text.pickTab(TEXT_TAB);
    expect(text.activeTab.value).toBe(DESCRIPTION_TAB);
  });

  it("pending: shows Description, and Text cannot be opened yet", async () => {
    getPictureText.mockResolvedValue({ state: "pending", lines: [] });
    const { text } = setup();
    await settle();
    expect(text.isPending.value).toBe(true);
    text.pickTab(TEXT_TAB);
    expect(text.activeTab.value).toBe(DESCRIPTION_TAB);
  });

  it("read with no words counts as no text", async () => {
    getPictureText.mockResolvedValue({ state: "read", lines: [[]] });
    const { text } = setup();
    await settle();
    expect(text.hasText.value).toBe(false);
  });
});

describe("usePictureText - landing", () => {
  it("sends the active search, and opens a matched picture on Text with the first match selected", async () => {
    getPictureText.mockResolvedValue(READ);
    const { text } = setup("coffee");
    await settle();
    expect(getPictureText).toHaveBeenCalledWith(1, { query: "coffee" });
    expect(text.activeTab.value).toBe(TEXT_TAB);
    expect(text.selectedWords.value).toEqual([3]);
  });

  it("omits the query when no text search is active", async () => {
    getPictureText.mockResolvedValue(PLAIN);
    setup("");
    await settle();
    expect(getPictureText).toHaveBeenCalledWith(1, {});
  });

  it("otherwise opens the last tab the user picked, picture to picture", async () => {
    getPictureText.mockResolvedValue(PLAIN);
    const { text, pictureId } = setup();
    await settle();
    expect(text.activeTab.value).toBe(DESCRIPTION_TAB);
    text.pickTab(TEXT_TAB);
    text.selectWord(0);

    pictureId.value = 2;
    await settle();
    expect(text.activeTab.value).toBe(TEXT_TAB);
    // A selection is about one picture's words.
    expect(text.selectedWords.value).toEqual([]);
  });

  it("a match landing does not overwrite the remembered tab", async () => {
    getPictureText.mockResolvedValueOnce(READ);
    const { text, pictureId } = setup("coffee");
    await settle();
    expect(text.activeTab.value).toBe(TEXT_TAB);

    getPictureText.mockResolvedValueOnce(PLAIN);
    pictureId.value = 2;
    await settle();
    expect(text.activeTab.value).toBe(DESCRIPTION_TAB);
  });

  it("read again goes pending, and the refreshed text opens the tab it was on", async () => {
    getPictureText.mockResolvedValue(PLAIN);
    readPictureText.mockResolvedValue({ state: "pending" });
    const { text } = setup();
    await settle();
    text.pickTab(TEXT_TAB);

    await text.readAgain();
    expect(readPictureText).toHaveBeenCalledWith(1);
    expect(text.isPending.value).toBe(true);
    expect(text.activeTab.value).toBe(DESCRIPTION_TAB);

    await text.refresh();
    expect(text.activeTab.value).toBe(TEXT_TAB);
  });
});
