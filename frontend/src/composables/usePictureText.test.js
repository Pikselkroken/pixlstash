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
  wordBoxRect,
  wordLayerStyle,
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

  it("read again stays on Text, busy, until the refreshed text lands", async () => {
    getPictureText.mockResolvedValue(PLAIN);
    readPictureText.mockResolvedValue({ state: "pending" });
    const { text } = setup();
    await settle();
    text.pickTab(TEXT_TAB);
    text.selectWord(0);

    await text.readAgain();
    expect(readPictureText).toHaveBeenCalledWith(1);
    // The server keeps the old text meanwhile: the user is not bounced to
    // Description, and the busy flag is what says a read is running.
    expect(text.readAgainBusy.value).toBe(true);
    expect(text.activeTab.value).toBe(TEXT_TAB);
    expect(text.selectedWords.value).toEqual([]);

    await text.refresh();
    expect(text.readAgainBusy.value).toBe(false);
    expect(text.activeTab.value).toBe(TEXT_TAB);
  });

  it("a failed read again is not left busy", async () => {
    getPictureText.mockResolvedValue(PLAIN);
    readPictureText.mockRejectedValue(new Error("boom"));
    const { text } = setup();
    await settle();
    await text.readAgain();
    expect(text.readAgainBusy.value).toBe(false);
  });

  it("read again is busy for its own picture only", async () => {
    getPictureText.mockResolvedValue(PLAIN);
    let finishPost;
    readPictureText.mockReturnValue(new Promise((r) => (finishPost = r)));
    const { text, pictureId } = setup();
    await settle();

    const pending = text.readAgain();
    expect(text.readAgainBusy.value).toBe(true);
    pictureId.value = 2;
    await settle();
    expect(text.readAgainBusy.value).toBe(false);
    finishPost({});
    await pending;
    expect(text.readAgainBusy.value).toBe(false);
  });
});

describe("word box geometry", () => {
  const DIMS = { width: 400, height: 200, offsetX: 12, offsetY: 30 };

  it("turns fractions of the picture into pixels", () => {
    expect(wordBoxRect([0.25, 0.5, 0.1, 0.05], DIMS)).toEqual({
      x: 100,
      y: 100,
      width: 40,
      height: 10,
    });
  });

  it("draws nothing for a missing or malformed box", () => {
    expect(wordBoxRect(null, DIMS)).toEqual({});
    expect(wordBoxRect([0.1, 0.1, 0.1], DIMS)).toEqual({});
  });

  it("lays the layer exactly over the displayed picture", () => {
    expect(wordLayerStyle(DIMS)).toEqual({
      left: "12px",
      top: "30px",
      width: "400px",
      height: "200px",
    });
    expect(wordLayerStyle({ width: 5, height: 6 })).toMatchObject({
      left: "0px",
      top: "0px",
    });
  });
});
