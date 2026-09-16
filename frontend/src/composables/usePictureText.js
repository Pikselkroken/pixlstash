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
  const readAgainBusy = ref(false);
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
    readAgainBusy.value = true;
    try {
      await readPictureText(id);
      if (pictureId.value !== id) return;
      // Invalidate a load still in flight: it would land the old text.
      requestId += 1;
      text.value = { state: "pending", lines: [] };
      selectedWords.value = [];
    } catch (err) {
      console.error(`Failed to re-read the text of picture ${id}`, err);
      noticeStore.error(
        `Couldn't read the text again. ${errorDetail(err) || err?.message || "Please try again."}`,
        { key: "picture-text-read" },
      );
    } finally {
      readAgainBusy.value = false;
    }
  }

  function refresh() {
    return load(pictureId.value);
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
