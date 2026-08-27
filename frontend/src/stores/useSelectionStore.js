// useSelectionStore.js - what the sidebar currently has selected.
//
// ── Security (issue #655, found by the store matrix) ─────────────────────────
// Most of this is UI state, but `selectedSetNames` holds server-resolved set
// NAMES and the four id lists hold server object ids, all read under one
// credential. Left in place across an auth-context change the consequence is
// over-blocking rather than leakage (the next credential's requests for those
// ids 403), but over-blocking is its own regression, so the selection is
// dropped through the same `onSessionReset` chokepoint as the caches.
//
// The three sessionStorage-backed values (`characterMultiMode`, `setMultiMode`,
// `setDifferenceBaseId`) are view preferences that survive a reload by design.
// Only `setDifferenceBaseId` names a server object, so it alone is cleared -
// through its own setter, so the stored copy goes with it rather than being
// left behind to reappear on the next reload.

import { computed, onScopeDispose, ref } from "vue";
import { defineStore } from "pinia";
import { onSessionReset } from "../utils/apiClient";

const ALL_PICTURES_ID = "ALL";

function loadMultiMode(key, fallback) {
  try {
    const v = window.sessionStorage?.getItem(key);
    return ["union", "intersection", "difference", "xor"].includes(v)
      ? v
      : fallback;
  } catch {
    return fallback;
  }
}

function saveMultiMode(key, val) {
  try {
    window.sessionStorage?.setItem(key, val);
  } catch {
    // ignore
  }
}

function loadBaseId(key) {
  try {
    const v = window.sessionStorage?.getItem(key);
    if (!v) return null;
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

function saveBaseId(key, val) {
  try {
    window.sessionStorage?.setItem(key, val != null ? String(val) : "");
  } catch {
    // ignore
  }
}

export const useSelectionStore = defineStore("selection", () => {
  const selectedCharacter = ref(ALL_PICTURES_ID);
  const selectedCharacterIds = ref([]);
  const selectedSet = ref(null);
  const selectedSetIds = ref([]);
  const selectedSetNames = ref({});
  const selectedFolderFilter = ref(null);
  const selectedImageIds = ref([]);
  const characterMultiMode = ref(
    loadMultiMode("pixlstash:characterMultiMode", "union"),
  );
  const setMultiMode = ref(
    loadMultiMode("pixlstash:setMultiMode", "intersection"),
  );
  const setDifferenceBaseId = ref(
    loadBaseId("pixlstash:setDifferenceBaseId"),
  );
  const lastSelectedCharacterLabel = ref("All Pictures");
  const lastSelectedSetLabel = ref("Picture Set");

  const isAllPicturesActive = computed(
    () =>
      !selectedSetIds.value.length &&
      selectedCharacter.value === ALL_PICTURES_ID,
  );

  function setCharacterMultiMode(val) {
    characterMultiMode.value = val;
    saveMultiMode("pixlstash:characterMultiMode", val);
  }

  function setSetMultiMode(val) {
    setMultiMode.value = val;
    saveMultiMode("pixlstash:setMultiMode", val);
  }

  function setSetDifferenceBaseId(val) {
    setDifferenceBaseId.value = val;
    saveBaseId("pixlstash:setDifferenceBaseId", val);
  }

  /** Drop the previous credential's selection. */
  function reset() {
    selectedCharacter.value = ALL_PICTURES_ID;
    selectedCharacterIds.value = [];
    selectedSet.value = null;
    selectedSetIds.value = [];
    selectedSetNames.value = {};
    selectedFolderFilter.value = null;
    selectedImageIds.value = [];
    lastSelectedCharacterLabel.value = "All Pictures";
    lastSelectedSetLabel.value = "Picture Set";
    setSetDifferenceBaseId(null);
  }

  const unsubscribeSessionReset = onSessionReset(reset);
  onScopeDispose(() => unsubscribeSessionReset());

  return {
    selectedCharacter,
    selectedCharacterIds,
    selectedSet,
    selectedSetIds,
    selectedSetNames,
    selectedFolderFilter,
    selectedImageIds,
    characterMultiMode,
    setMultiMode,
    setDifferenceBaseId,
    lastSelectedCharacterLabel,
    lastSelectedSetLabel,
    isAllPicturesActive,
    setCharacterMultiMode,
    setSetMultiMode,
    setSetDifferenceBaseId,
    reset,
  };
});
