// The text found in a picture (OCR, #1197), as the lightbox shows it: which
// sidebar tab is open, which words are selected, and when to re-read.
//
// The tab and the selection are shared between two surfaces that do not own
// each other (the sidebar's word list and the boxes drawn over the picture),
// so they live here and ImageOverlay hands them to both.

import { computed, ref, watch } from "vue";
import { getPictureText, readPictureText } from "../api/pictures";
import { useNoticeStore } from "../stores/useNoticeStore";
import { errorDetail } from "../utils/apiError";

export const DESCRIPTION_TAB = "description";
export const TEXT_TAB = "text";

const NO_TEXT = Object.freeze({ state: "none", lines: [] });

/**
 * Every word in reading order, numbered, with the line it sits on.
 * @param {Array<Array<Object>>} lines
 * @returns {Array<{index: number, line: number, text: string, box: number[], matched: boolean}>}
 */
export function flattenWords(lines) {
  const words = [];
  (Array.isArray(lines) ? lines : []).forEach((line, lineIdx) => {
    for (const word of Array.isArray(line) ? line : []) {
      words.push({
        index: words.length,
        line: lineIdx,
        text: String(word?.text ?? ""),
        box: Array.isArray(word?.box) ? word.box : null,
        matched: word?.matched === true,
      });
    }
  });
  return words;
}

/** The whole text: words joined by spaces, lines by newlines. */
export function joinPictureText(lines) {
  return (Array.isArray(lines) ? lines : [])
    .map((line) =>
      (Array.isArray(line) ? line : []).map((w) => w?.text ?? "").join(" "),
    )
    .join("\n");
}

/**
 * The selection after a word is clicked.
 *
 * A plain click selects that word alone, and clicking the only selected word
 * clears. A shift-click toggles the word in or out of the selection.
 *
 * @param {number[]} selected - current selection (word indices).
 * @param {number} index - the clicked word.
 * @param {boolean} additive - shift was held.
 * @returns {number[]} the next selection, in reading order.
 */
export function nextWordSelection(selected, index, additive) {
  const current = new Set(selected);
  if (additive) {
    if (current.has(index)) current.delete(index);
    else current.add(index);
    return [...current].sort((a, b) => a - b);
  }
  if (current.size === 1 && current.has(index)) return [];
  return [index];
}

/**
 * Where a word box sits in the word-box layer, in pixels.
 * @param {number[]|null} box - `[x, y, width, height]` as fractions of the picture.
 * @param {{width: number, height: number}} dims - the displayed picture's size.
 * @returns {Object} rect attributes, or `{}` for a missing or malformed box.
 */
export function wordBoxRect(box, dims) {
  if (!Array.isArray(box) || box.length !== 4) return {};
  return {
    x: box[0] * dims.width,
    y: box[1] * dims.height,
    width: box[2] * dims.width,
    height: box[3] * dims.height,
  };
}

/** The word-box layer's style: exactly over the displayed picture. */
export function wordLayerStyle(dims) {
  return {
    left: `${dims.offsetX || 0}px`,
    top: `${dims.offsetY || 0}px`,
    width: `${dims.width}px`,
    height: `${dims.height}px`,
  };
}

/**
 * @param {Object} deps
 * @param {import("vue").Ref<number|string|null>} deps.pictureId - the open picture.
 * @param {() => string} deps.getSearchQuery - the active text search, or "".
 */
export function usePictureText({ pictureId, getSearchQuery }) {
  const noticeStore = useNoticeStore();

  const text = ref(NO_TEXT);
  // The tab for the picture on screen, and the one the user last picked. The
  // second is carried from picture to picture for the overlay's lifetime and
  // deliberately never persisted.
  const chosenTab = ref(DESCRIPTION_TAB);
  let lastPickedTab = DESCRIPTION_TAB;
  const selectedWords = ref([]);
  // The picture a "Read again" is running for, until its new text lands.
  // Landing on another picture clears it, so the next one is never busy.
  const rereadingId = ref(null);
  const readAgainBusy = computed(() => rereadingId.value != null);
  let requestId = 0;

  const words = computed(() => flattenWords(text.value.lines));
  const hasText = computed(
    () => text.value.state === "read" && words.value.length > 0,
  );
  const isPending = computed(() => text.value.state === "pending");
  // A picture with no text always shows Description, whatever was picked.
  const activeTab = computed(() =>
    hasText.value ? chosenTab.value : DESCRIPTION_TAB,
  );
  const fullText = computed(() => joinPictureText(text.value.lines));

  function pickTab(tab) {
    if (tab === TEXT_TAB && !hasText.value) return;
    chosenTab.value = tab;
    lastPickedTab = tab;
  }

  function selectWord(index, additive = false) {
    selectedWords.value = nextWordSelection(
      selectedWords.value,
      index,
      additive,
    );
  }

  function clearSelection() {
    selectedWords.value = [];
  }

  /**
   * Read the picture's text. `landing` is a picture change: it resets the tab
   * to the remembered one, then lets a search match override it.
   */
  async function load(id, { landing = false } = {}) {
    const current = (requestId += 1);
    if (landing) {
      rereadingId.value = null;
      text.value = NO_TEXT;
      selectedWords.value = [];
      chosenTab.value = lastPickedTab;
    }
    if (id == null) return;
    const query = (getSearchQuery?.() || "").trim();
    let body;
    try {
      body = await getPictureText(id, query ? { query } : {});
    } catch (err) {
      if (current !== requestId) return;
      console.error(`Failed to read the text of picture ${id}`, err);
      text.value = NO_TEXT;
      return;
    }
    if (current !== requestId) return;
    const state = ["pending", "read"].includes(body?.state)
      ? body.state
      : "none";
    text.value = {
      state,
      lines: state === "read" && Array.isArray(body?.lines) ? body.lines : [],
    };
    const firstMatch = words.value.find((w) => w.matched);
    if (landing && firstMatch) {
      chosenTab.value = TEXT_TAB;
      selectedWords.value = [firstMatch.index];
    } else if (!landing) {
      // Indices mean nothing across a re-read.
      selectedWords.value = [];
    }
  }

  async function readAgain() {
    const id = pictureId.value;
    if (id == null || readAgainBusy.value) return;
    // The server keeps the old text until the new read lands, so the words stay
    // put and this flag is the busy state; `refresh` (the `ocr_text` frame)
    // clears it.
    rereadingId.value = id;
    selectedWords.value = [];
    try {
      await readPictureText(id);
    } catch (err) {
      if (rereadingId.value === id) rereadingId.value = null;
      console.error(`Failed to re-read the text of picture ${id}`, err);
      noticeStore.error(
        `Couldn't read the text again. ${errorDetail(err) || err?.message || "Please try again."}`,
        { key: "picture-text-read" },
      );
    }
  }

  /** Read the open picture's text again, e.g. because the server says it changed. */
  async function refresh() {
    const id = pictureId.value;
    await load(id);
    if (rereadingId.value === id) rereadingId.value = null;
  }

  watch(pictureId, (id) => load(id, { landing: true }), { immediate: true });

  return {
    text,
    words,
    hasText,
    isPending,
    activeTab,
    fullText,
    selectedWords,
    readAgainBusy,
    pickTab,
    selectWord,
    clearSelection,
    readAgain,
    refresh,
  };
}
