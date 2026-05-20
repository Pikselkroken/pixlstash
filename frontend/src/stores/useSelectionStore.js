import { computed, ref } from "vue";
import { defineStore } from "pinia";

const ALL_PICTURES_ID = "ALL";
const UNASSIGNED_PICTURES_ID = "UNASSIGNED";
const SCRAPHEAP_PICTURES_ID = "SCRAPHEAP";

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

export function saveMultiMode(key, val) {
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

export function saveBaseId(key, val) {
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
  };
});
