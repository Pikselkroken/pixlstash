<script setup>
import {
  computed,
  ref,
  onBeforeUnmount,
  onMounted,
  watch,
  nextTick,
} from "vue";
import ImageImporter from "./ImageImporter.vue";
import CharacterEditor from "./CharacterEditor.vue";
import PictureSetEditor from "./PictureSetEditor.vue";
import TaskManager from "./TaskManager.vue";
import UserSettingsDialog from "./UserSettingsDialog.vue";
import unknownPerson from "../assets/unknown-person.png"; // Fallback avatar for characters without thumbnails
import { apiClient } from "../utils/apiClient";

const appVersion = __APP_VERSION__;

const latestVersion = ref(null);
const latestVersionUrl = ref(null);
const updateAvailable = computed(
  () => latestVersion.value && latestVersion.value !== appVersion,
);

const props = defineProps({
  collapsed: { type: Boolean, default: false },
  selectedCharacter: { type: [String, Number, null], default: null },
  selectedReferenceCharacter: { type: [String, Number, null], default: null },
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  scrapheapPicturesId: { type: String, required: true },
  selectedSet: { type: [Number, null], default: null },
  searchQuery: { type: String, default: "" },
  selectedSort: { type: String, default: "" },
  selectedDescending: { type: Boolean, default: false },
  selectedSimilarityCharacter: { type: [String, Number, null], default: null },
  backendUrl: { type: String, required: true },
  sidebarThumbnailSize: { type: Number, default: 48 },
  dateFormat: { type: String, default: "locale" },
  themeMode: { type: String, default: "light" },
});

const emit = defineEmits([
  "select-character",
  "select-reference-pictures",
  "update:selected-sort",
  "update:search-query",
  "select-set",
  "import-finished",
  "set-error",
  "set-loading",
  "images-assigned-to-character",
  "faces-assigned-to-character",
  "images-moved",
  "search-images",
  "update:similarity-character",
  "update:similarity-options",
  "update:sidebar-thumbnail-size",
  "update:date-format",
  "update:theme-mode",
  "toggle-sidebar",
  "update:sort-options",
  "update:hidden-tags",
  "update:apply-tag-filter",
  "open-import-dialog",
]);

const imageImporterRef = ref(null);
const sidebarRootRef = ref(null);
const labelOverflow = ref({});
const labelRefs = new Map();
const labelObservers = new Map();

const dragOverSet = ref(null);

// --- Sorting State ---
const sortOptions = ref([]);

// --- Character & Sidebar State ---
const characters = ref([]);
const categoryCounts = ref({
  [props.allPicturesId]: 0,
  [props.unassignedPicturesId]: 0,
  [props.scrapheapPicturesId]: 0,
});

const flashCountsNextFetch = ref(false);
const countNewTags = ref({});
const knownCountIds = new Set();

const characterThumbnails = ref({});
const setThumbnails = ref({});
const expandedCharacters = ref({});

const dragOverCharacter = ref(null);
const nextCharacterNumber = ref(1);

// --- Picture Sets State ---
const pictureSets = ref([]);

// --- Character Editor State ---
const characterEditorOpen = ref(false);
const characterEditorCharacter = ref(null);

const setEditorOpen = ref(false);
const setEditorSet = ref(null);
const settingsDialogOpen = ref(false);
const taskManagerOpen = ref(false);
function updateLabelOverflow(key, el = null) {
  const element = el || labelRefs.get(key);
  if (!element) return;
  const width = element.clientWidth;
  const isOverflowing = width > 0 && element.scrollWidth > width + 1;
  if (labelOverflow.value[key] !== isOverflowing) {
    labelOverflow.value = { ...labelOverflow.value, [key]: isOverflowing };
  }
}

function registerLabelRef(key, el) {
  const existingObserver = labelObservers.get(key);
  if (existingObserver) {
    existingObserver.disconnect();
    labelObservers.delete(key);
  }

  if (!el) {
    labelRefs.delete(key);
    if (labelOverflow.value[key] !== undefined) {
      const next = { ...labelOverflow.value };
      delete next[key];
      labelOverflow.value = next;
    }
    return;
  }

  labelRefs.set(key, el);
  const observer = new ResizeObserver(() => updateLabelOverflow(key, el));
  observer.observe(el);
  labelObservers.set(key, observer);
  requestAnimationFrame(() => updateLabelOverflow(key, el));
}

function labelNeedsTooltip(key) {
  return Boolean(labelOverflow.value[key]);
}

function refreshLabelOverflows() {
  for (const [key, el] of labelRefs.entries()) {
    updateLabelOverflow(key, el);
  }
}

function mergeTooltipRef(refProps, key) {
  return (el) => {
    if (refProps?.ref) {
      if (typeof refProps.ref === "function") {
        refProps.ref(el);
      } else {
        refProps.ref.value = el;
      }
    }
    registerLabelRef(key, el);
  };
}

const sidebarNotice = ref(null);
const sidebarNoticeTargetId = ref(null);
const sidebarNoticeTargetType = ref("set");
const sidebarNoticePosition = ref(null);
const setItemRefs = ref(new Map());
const characterItemRefs = ref(new Map());
let sidebarNoticeTimeout = null;
const sidebarError = ref(null);
const sidebarErrorTargetId = ref(null);
const sidebarErrorTargetType = ref("set");
const sidebarErrorPosition = ref(null);
let sidebarErrorTimeout = null;

function registerSetRef(setId, el) {
  if (!setId) return;
  if (el) {
    setItemRefs.value.set(setId, el);
  } else {
    setItemRefs.value.delete(setId);
  }
}

function registerCharacterRef(characterId, el) {
  if (!characterId) return;
  if (el) {
    characterItemRefs.value.set(characterId, el);
  } else {
    characterItemRefs.value.delete(characterId);
  }
}

function updateSidebarNoticePosition() {
  if (!sidebarNotice.value || !sidebarNoticeTargetId.value) {
    sidebarNoticePosition.value = null;
    return;
  }
  const targetMap =
    sidebarNoticeTargetType.value === "character"
      ? characterItemRefs.value
      : setItemRefs.value;
  const target = targetMap.get(sidebarNoticeTargetId.value);
  if (!target) return;
  const rect = target.getBoundingClientRect();
  sidebarNoticePosition.value = {
    top: rect.top + rect.height / 2,
    left: rect.right + 12,
  };
}

function updateSidebarErrorPosition() {
  if (!sidebarError.value || !sidebarErrorTargetId.value) {
    sidebarErrorPosition.value = null;
    return;
  }
  const targetMap =
    sidebarErrorTargetType.value === "character"
      ? characterItemRefs.value
      : setItemRefs.value;
  const target = targetMap.get(sidebarErrorTargetId.value);
  if (!target) return;
  const rect = target.getBoundingClientRect();
  const sidebarRect = sidebarRootRef.value
    ? sidebarRootRef.value.getBoundingClientRect()
    : null;
  const baseLeft = sidebarRect ? sidebarRect.right + 12 : rect.right + 12;
  sidebarErrorPosition.value = {
    top: rect.top + rect.height / 2,
    left: baseLeft,
  };
}

function createSet() {
  setEditorSet.value = null;
  setEditorOpen.value = true;
}

const sortedCharacters = computed(() => {
  return [...characters.value]
    .filter((c) => c && typeof c.name === "string" && c.name.trim() !== "")
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    );
});

const selectedCharacterObj = computed(() => {
  if (
    props.selectedCharacter &&
    props.selectedCharacter !== props.allPicturesId &&
    props.selectedCharacter !== props.unassignedPicturesId &&
    props.selectedCharacter !== props.scrapheapPicturesId
  ) {
    const char =
      characters.value.find((c) => c.id === props.selectedCharacter) || null;
    if (char && typeof char.name === "string" && char.name.length > 0) {
      return {
        ...char,
        name: char.name.charAt(0).toUpperCase() + char.name.slice(1),
      };
    }
    return char;
  }
  return null;
});

const selectedSetObj = computed(() => {
  if (!props.selectedSet) return null;
  return (
    pictureSets.value.find((pset) => pset.id === props.selectedSet) || null
  );
});

const nonReferenceSets = computed(() =>
  pictureSets.value.filter((pset) => !pset.reference_character),
);

// --- Similarity Character Dropdown State ---
const SIMILARITY_SORT_KEY = "CHARACTER_LIKENESS"; // Adjust if backend uses a different key
const DATE_SORT_KEY = "DATE";

const similarityCharacterOptions = computed(() => {
  let options = sortedCharacters.value.map((c) => ({
    text: c.name,
    value: c.id,
    thumbnail: characterThumbnails.value?.[c.id] || null,
  }));
  return options;
});

watch(
  similarityCharacterOptions,
  (options) => {
    emit("update:similarity-options", options);
  },
  { immediate: true },
);

const similarityCharacterModel = computed({
  get: () => props.selectedSimilarityCharacter,
  set: (value) => emit("update:similarity-character", value ?? null),
});

const sidebarThumbnailSizeModel = computed({
  get: () => props.sidebarThumbnailSize ?? 64,
  set: (value) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return;
    const clamped = Math.min(64, Math.max(32, parsed));
    const snapped = Math.round(clamped / 8) * 8;
    emit("update:sidebar-thumbnail-size", snapped);
  },
});

const dateFormatModel = computed({
  get: () => props.dateFormat ?? "locale",
  set: (value) => emit("update:date-format", value ?? "locale"),
});

const themeModeModel = computed({
  get: () => props.themeMode ?? "light",
  set: (value) => emit("update:theme-mode", value ?? "light"),
});

const sidebarThumbnailSizeLarge = computed(
  () => sidebarThumbnailSizeModel.value + 8,
);

const sidebarThumbStyle = computed(() => ({
  "--sidebar-thumb-size": `${sidebarThumbnailSizeModel.value}px`,
}));

const isSearchActive = computed(() => {
  const query = typeof props.searchQuery === "string" ? props.searchQuery : "";
  return query.trim().length > 0;
});

const reactiveSelectedDescending = ref(props.selectedDescending);

watch(
  () => props.selectedDescending,
  (newValue, oldValue) => {
    reactiveSelectedDescending.value = newValue;
  },
);

const descendingModel = computed({
  get: () => {
    return reactiveSelectedDescending.value;
  },
  set: (value) => {
    reactiveSelectedDescending.value = value;
    emit("update:selected-sort", { sort: sortModel.value, descending: value });
  },
});

const sortModel = computed({
  get: () => props.selectedSort,
  set: (value) =>
    emit("update:selected-sort", {
      sort: value != null ? String(value) : "",
      descending: descendingModel.value,
    }),
});

const searchModel = computed({
  get: () => props.searchQuery,
  set: (value) => emit("update:search-query", value ?? ""),
});

// --- Character Editor Dialog Functions ---
function openCharacterEditor(char = null) {
  characterEditorCharacter.value = char;
  characterEditorOpen.value = true;
}

function closeCharacterEditor() {
  characterEditorOpen.value = false;
  characterEditorCharacter.value = null;
}

// --- Picture Set Editor ---
function openSetEditor(set = null) {
  setEditorSet.value = set;
  setEditorOpen.value = true;
}

function closeSetEditor() {
  setEditorOpen.value = false;
  setEditorSet.value = null;
}

function openSettingsDialog() {
  settingsDialogOpen.value = true;
}

function selectCharacter(id, label = null) {
  clearCountNew(id);
  emit("select-set", null);
  emit("select-character", { id, label });
}

function selectReferencePictures(
  characterId,
  characterLabel = null,
  referenceLabel = null,
) {
  clearCountNew(characterId);
  emit("select-set", null);
  if (props.selectedReferenceCharacter === characterId) {
    emit("select-reference-pictures", null);
    emit("select-character", { id: characterId, label: characterLabel });
    return;
  }
  emit("select-reference-pictures", {
    id: characterId,
    label: referenceLabel || characterLabel,
  });
}

function selectSet(setId, label = null) {
  emit("select-character", null);
  emit("select-set", { id: setId, label });
}

async function deleteCharacter() {
  if (!props.selectedCharacter) return;
  if (!window.confirm("Delete this character?")) return;
  try {
    await apiClient.delete(`/characters/${props.selectedCharacter}`);

    // Remove the deleted character from the characters array
    characters.value = characters.value.filter(
      (char) => char.id !== props.selectedCharacter,
    );

    await fetchCharacters(); // Refresh sidebar
  } catch (e) {
    setError(e.message);
  }
}

function createCharacter() {
  // Find the next available unique name in the format "Character 0001"
  const existingNames = new Set(characters.value.map((c) => c.name));
  let num = 1;
  let name;
  do {
    name = `Character ${num.toString().padStart(4, "0")}`;
    num++;
  } while (existingNames.has(name));
  // Open the editor with default values
  openCharacterEditor({
    id: null,
    name: name,
    description: "",
    extra_metadata: "",
  });
}

const pendingImportTarget = ref(null);

function getImportedPictureIds(payload) {
  const results = Array.isArray(payload?.results) ? payload.results : [];
  return Array.from(
    new Set(
      results
        .map((entry) => entry?.picture_id)
        .filter((id) => id !== null && id !== undefined),
    ),
  );
}

async function associateImportedPictures(pictureIds, target) {
  if (!target || !pictureIds.length) return;
  if (target.type === "set") {
    await Promise.all(
      pictureIds.map((id) =>
        apiClient.post(
          `${props.backendUrl}/picture_sets/${target.id}/members/${id}`,
        ),
      ),
    );
    await fetchPictureSets();
    return;
  }
  if (target.type === "character") {
    await apiClient.post(`${props.backendUrl}/characters/${target.id}/faces`, {
      picture_ids: pictureIds,
    });
    await fetchSidebarData();
    await fetchCharacterThumbnail(target.id);
  }
}

async function handleImportFinished(payload) {
  emit("import-finished", payload);
  const target = pendingImportTarget.value;
  if (!target) return;
  pendingImportTarget.value = null;
  const pictureIds = getImportedPictureIds(payload);
  if (!pictureIds.length) return;
  try {
    await associateImportedPictures(pictureIds, target);
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    let targetName = "";
    if (target.type === "character") {
      targetName =
        characters.value.find((c) => c.id === target.id)?.name || "Character";
    } else if (target.type === "set") {
      targetName =
        pictureSets.value.find((s) => s.id === target.id)?.name || "Set";
    }
    const normalizedDetail = String(detail || "").toLowerCase();
    const prefix = normalizedDetail.includes("already")
      ? `Already associated with ${targetName}`
      : `Failed to associate imported pictures with ${targetName}`;
    setError(`${prefix}: ${detail}`, target.id, target.type);
  }
}

function openImportDialog() {
  emit("open-import-dialog");
}

function startLocalImport(files) {
  const list = Array.isArray(files) ? files : [];
  if (!list.length) return;
  imageImporterRef.value?.startImport(list);
}

function setLoading(isLoading) {
  emit("set-loading", isLoading);
}

function setError(message, targetId = null, targetType = "set") {
  sidebarError.value = message;
  sidebarErrorTargetId.value = targetId;
  sidebarErrorTargetType.value = targetType;
  nextTick(() => updateSidebarErrorPosition());
  emit("set-error", message);
  if (sidebarErrorTimeout) {
    clearTimeout(sidebarErrorTimeout);
    sidebarErrorTimeout = null;
  }
  sidebarErrorTimeout = setTimeout(() => {
    sidebarError.value = null;
    sidebarErrorTargetId.value = null;
    sidebarErrorPosition.value = null;
    sidebarErrorTimeout = null;
  }, 3500);
}

function showNotice(
  message,
  targetId = null,
  targetType = "set",
  duration = 4000,
) {
  if (sidebarNoticeTimeout) {
    clearTimeout(sidebarNoticeTimeout);
    sidebarNoticeTimeout = null;
  }
  sidebarNotice.value = message;
  sidebarNoticeTargetId.value = targetId;
  sidebarNoticeTargetType.value = targetType;
  nextTick(() => updateSidebarNoticePosition());
  sidebarNoticeTimeout = setTimeout(() => {
    sidebarNotice.value = null;
    sidebarNoticeTargetId.value = null;
    sidebarNoticePosition.value = null;
    sidebarNoticeTimeout = null;
  }, duration);
}

function dragOverSetItem(setId) {
  dragOverSet.value = setId;
}

function dragLeaveSetItem() {
  dragOverSet.value = null;
}

function isCountSelected(id) {
  if (!id) return false;
  return props.selectedCharacter === id;
}

function isCountNew(id) {
  return Boolean(id && countNewTags.value[id]);
}

function clearCountNew(id) {
  if (!id) return;
  countNewTags.value[id] = false;
}

function markCountNew(id) {
  if (!id) return;
  if (isCountSelected(id)) return;
  countNewTags.value[id] = true;
}

function setCategoryCount(id, value, shouldFlash) {
  if (!id) return;
  const prevValue = categoryCounts.value[id];
  categoryCounts.value[id] = value;
  if (!knownCountIds.has(id)) {
    knownCountIds.add(id);
    return;
  }
  if (shouldFlash && typeof value === "number" && value > prevValue) {
    markCountNew(id);
  }
}

// --- Sidebar & Character Data ---
async function fetchSidebarData() {
  const shouldFlash = flashCountsNextFetch.value;
  // Fetch total image count for END key logic
  try {
    // All images summary
    const resAll = await apiClient.get(
      `${props.backendUrl}/characters/${props.allPicturesId}/summary`,
    );
    const data = await resAll.data;
    setCategoryCount(props.allPicturesId, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching all images summary:", e);
  }
  try {
    // Unassigned images summary
    const resUnassigned = await apiClient.get(
      `${props.backendUrl}/characters/${props.unassignedPicturesId}/summary`,
    );
    const data = await resUnassigned.data;
    setCategoryCount(props.unassignedPicturesId, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching unassigned images summary:", e);
  }
  try {
    const resScrapheap = await apiClient.get(
      `${props.backendUrl}/characters/${props.scrapheapPicturesId}/summary`,
    );
    const data = await resScrapheap.data;
    setCategoryCount(props.scrapheapPicturesId, data.image_count, shouldFlash);
  } catch (e) {
    console.warn("Error fetching scrapheap images summary:", e);
  }
  await Promise.all(
    characters.value.map(async (char) => {
      try {
        const res = await apiClient.get(
          `${props.backendUrl}/characters/${char.id}/summary`,
        );
        const data = await res.data;
        setCategoryCount(char.id, data.image_count, shouldFlash);
      } catch {}
    }),
  );
  flashCountsNextFetch.value = false;
}

async function fetchCharacters() {
  setLoading(true);
  setError(null);
  try {
    const res = await apiClient.get(`${props.backendUrl}/characters`);
    const chars = await res.data;
    characters.value = chars;
    for (const char of chars) {
      fetchCharacterThumbnail(char.id);
    }
  } catch (e) {
    setError(e.message);
  } finally {
    setLoading(false);
  }
}

function refreshSidebar(options = {}) {
  if (options?.flashCounts) {
    flashCountsNextFetch.value = true;
  }
  fetchCharacters();
  fetchPictureSets();
  fetchSidebarData();
}

async function fetchCharacterThumbnail(characterId) {
  try {
    const cacheBuster = Date.now();
    const thumbUrl = `/characters/${characterId}/thumbnail?cb=${cacheBuster}`;
    const res = await apiClient.get(thumbUrl, { responseType: "blob" });

    // Create an object URL for the blob
    const blobUrl = URL.createObjectURL(res.data);
    characterThumbnails.value[characterId] = blobUrl;
  } catch (e) {
    console.error(`Failed to fetch thumbnail for character ${characterId}:`, e);
    characterThumbnails.value[characterId] = null;
  }
}

// --- Sorting & Pagination ---
async function fetchSortOptions() {
  try {
    const res = await apiClient.get(`${props.backendUrl}/sort_mechanisms`);

    const options = await res.data;

    // Filter out CHARACTER_LIKENESS if there are no characters
    const filteredOptions = options.filter((opt) => {
      if (opt.key === SIMILARITY_SORT_KEY) {
        return sortedCharacters.value.length > 0; // Only include if characters exist
      }
      return true;
    });

    // Map options to the desired format
    sortOptions.value = filteredOptions.map((opt) => ({
      label: opt.description,
      value: opt.key,
    }));

    // Reset sortModel if it is not in the available options
    if (!sortOptions.value.some((opt) => opt.value === sortModel.value)) {
      sortModel.value = sortOptions.value.length
        ? sortOptions.value[0].value
        : null;
    }
    emit("update:sort-options", sortOptions.value);
  } catch (e) {
    console.error("Error fetching sort options:", e);
    sortOptions.value = [];
    emit("update:sort-options", []);
  }
}

// --- Picture Sets ---
async function fetchPictureSets() {
  try {
    const res = await apiClient.get(`${props.backendUrl}/picture_sets`);

    const sets = await res.data; // Axios responses use `data` for the payload
    pictureSets.value = Array.isArray(sets) ? [...sets] : [];
    await updateSetThumbnails(pictureSets.value);
  } catch (e) {
    console.error("Error fetching picture sets:", e);
    pictureSets.value = [...pictureSets.value]; // force reactivity on error
  }
}

async function updateSetThumbnails(sets) {
  const nextMap = {};
  for (const set of sets || []) {
    const baseUrl = set?.thumbnail_url || null;
    if (!baseUrl) {
      nextMap[set.id] = null;
      continue;
    }
    const topIds = Array.isArray(set?.top_picture_ids)
      ? set.top_picture_ids
      : [];
    const versionKey = topIds.length
      ? topIds.join("-")
      : (set.picture_count ?? 0);
    const url = baseUrl.startsWith("http")
      ? baseUrl
      : `${props.backendUrl}${baseUrl}`;
    nextMap[set.id] = `${url}?v=${encodeURIComponent(versionKey)}`;
  }
  setThumbnails.value = nextMap;
}

function getSetThumbnail(setId) {
  return setThumbnails.value?.[setId] || null;
}

function hasSetThumbnail(pset) {
  if (!pset || !pset.id) return false;
  if (!pset.picture_count) return false;
  return Boolean(getSetThumbnail(pset.id));
}

function handleSetThumbnailError(setId) {
  if (!setId) return;
  setThumbnails.value = { ...setThumbnails.value, [setId]: null };
}

async function handleDeleteSet() {
  if (!props.selectedSet) return;

  const setToDelete = pictureSets.value.find((s) => s.id === props.selectedSet);
  if (!setToDelete) return;

  if (
    !window.confirm(
      `Delete picture set "${setToDelete.name}"? This will unassign all their images.`,
    )
  )
    return;

  try {
    const res = await apiClient.delete(
      `${props.backendUrl}/picture_sets/${props.selectedSet}`,
    );
    emit("select-set", null);
    await fetchPictureSets();
    await fetchSidebarData();
  } catch (e) {
    alert("Failed to delete set: " + (e.message || e));
  }
}

async function handleDropOnSet(setId, event) {
  dragOverSet.value = null;
  if (event?.dataTransfer?.files && event.dataTransfer.files.length > 0) {
    const files = Array.from(event.dataTransfer.files);
    pendingImportTarget.value = { type: "set", id: setId };
    imageImporterRef.value?.startImport(files);
    return;
  }
  // Get the dragged image IDs from the drag event
  let draggedIds = [];
  try {
    const data = JSON.parse(event.dataTransfer.getData("application/json"));
    if (data.imageIds && Array.isArray(data.imageIds)) {
      draggedIds = data.imageIds;
    }
  } catch (e) {
    console.error("Could not parse drag data:", e);
    return;
  }

  if (draggedIds.length === 0) {
    return;
  }

  const targetSet = pictureSets.value.find((s) => s.id === setId);
  if (!targetSet) return;

  try {
    // Add each image to the set
    const addPromises = draggedIds.map(async (picId) => {
      const res = await apiClient.post(
        `${props.backendUrl}/picture_sets/${setId}/members/${picId}`,
      );
    });

    await Promise.all(addPromises);

    // Refresh the picture sets to update counts
    await fetchPictureSets();

    // Emit event to parent to remove images from grid
    emit("images-moved", { imageIds: draggedIds });
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    if (typeof detail === "string" && detail.includes("already in set")) {
      showNotice("Picture already in set", setId);
      return;
    }
    setError("Failed to add images to set: " + detail, setId, "set");
  }
}

function handleDragOverCharacter(id) {
  dragOverCharacter.value = id;
}

function handleDragLeaveCharacter() {
  dragOverCharacter.value = null;
}

async function onCharacterDrop(characterId, event) {
  dragOverCharacter.value = null;
  if (event?.dataTransfer?.files && event.dataTransfer.files.length > 0) {
    const files = Array.from(event.dataTransfer.files);
    pendingImportTarget.value = { type: "character", id: characterId };
    imageImporterRef.value?.startImport(files);
    return;
  }
  // Accept faceIds or imageIds from drag event
  let faceIds = [];
  let imageIds = [];
  let dragType = null;
  try {
    const rawDataStr = event.dataTransfer.getData("application/json");
    const data = JSON.parse(rawDataStr);
    dragType = data.type || null;
    if (
      dragType === "face-bbox" &&
      data.faceIds &&
      Array.isArray(data.faceIds)
    ) {
      faceIds = data.faceIds;
    }
    if (data.imageIds && Array.isArray(data.imageIds)) {
      imageIds = data.imageIds;
    }
    emit("images-assigned-to-character", { characterId, imageIds });
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    console.error("Error parsing drag data:", detail);
    if (typeof detail === "string") {
      showNotice(detail, characterId, "character");
      return;
    }
    setError(
      "Failed to add images to set: " + detail,
      characterId,
      "character",
    );
    return;
  }

  if (dragType === "face-bbox" && faceIds.length > 0) {
    // Assign faces to character
    try {
      const body = { face_ids: faceIds };
      const res = await apiClient.post(
        `${props.backendUrl}/characters/${characterId}/faces`,
        body,
      );
      await fetchSidebarData();
      await fetchCharacterThumbnail(characterId);
      emit("faces-assigned-to-character", { characterId, faceIds });
    } catch (e) {
      alert("Failed to assign faces to character: " + (e.message || e));
    }
    return;
  }

  if (imageIds.length === 0) {
    return;
  }

  try {
    // Fallback: assign images to character
    const body = { picture_ids: imageIds };
    const res = await apiClient.post(
      `${props.backendUrl}/characters/${characterId}/faces`,
      body,
    );
    await fetchSidebarData();
    await fetchCharacterThumbnail(characterId);
    emit("images-assigned-to-character", { characterId, imageIds });
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    console.error("Error assignning character:", detail);
    if (typeof detail === "string") {
      showNotice(detail, characterId, "character");
      return;
    }
    setError(
      "Failed to add images to set: " + detail,
      characterId,
      "character",
    );
    return;
  }
}

function handleDropOnCharacter(payload) {
  dragOverCharacter.value = null;
  if (!payload || !payload.characterId) return;
  onCharacterDrop(payload.characterId, payload.event);
}

// --- Character Management ---
async function characterSaved() {
  if (characterEditorCharacter.value && !characterEditorCharacter.value.id) {
    characters.value.push(characterEditorCharacter.value);
    // New character was created, increment nextCharacterNumber
    nextCharacterNumber.value++;
  }
  await fetchCharacters(); // Refresh characters
  await fetchSortOptions(); // Ensure sort options include similarity when characters exist
  await fetchPictureSets(); // Refresh picture sets to include reference sets
  closeCharacterEditor();
}

onMounted(() => {
  // Check for a newer version on PyPI (fire-and-forget, never throws)
  apiClient
    .get("/version/latest")
    .then((resp) => {
      latestVersion.value = resp.data.latest_version;
      latestVersionUrl.value = resp.data.release_url;
    })
    .catch(() => {});

  const handleNoticeReflow = () => {
    updateSidebarNoticePosition();
    updateSidebarErrorPosition();
  };
  if (sidebarRootRef.value) {
    sidebarRootRef.value.addEventListener("scroll", handleNoticeReflow, {
      passive: true,
    });
  }
  window.addEventListener("resize", handleNoticeReflow);
  sidebarNoticeCleanup = () => {
    if (sidebarRootRef.value) {
      sidebarRootRef.value.removeEventListener("scroll", handleNoticeReflow);
    }
    window.removeEventListener("resize", handleNoticeReflow);
  };
});

let sidebarNoticeCleanup = null;
onBeforeUnmount(() => {
  if (sidebarNoticeCleanup) {
    sidebarNoticeCleanup();
    sidebarNoticeCleanup = null;
  }
  for (const observer of labelObservers.values()) {
    observer.disconnect();
  }
  labelObservers.clear();
  labelRefs.clear();
});

watch(
  [sortedCharacters, pictureSets],
  () => {
    nextTick(() => refreshLabelOverflows());
  },
  { deep: true },
);

// Ensure similarityCharacter is valid when switching to CHARACTER_LIKENESS
watch(
  () => sortModel.value,
  (newSort) => {
    if (newSort === SIMILARITY_SORT_KEY) {
      // Check if the current similarityCharacter is valid
      if (
        !sortedCharacters.value.some(
          (char) => char.id === similarityCharacterModel.value,
        )
      ) {
        similarityCharacterModel.value =
          sortedCharacters.value.length > 0
            ? sortedCharacters.value[0].id
            : null; // Default to the first character or null
      }
    }
  },
);

watch(
  () => sortedCharacters.value.length,
  () => {
    fetchSortOptions();
  },
);

watch(
  [() => sortedCharacters.value, () => props.selectedSort],
  ([chars, selectedSort]) => {
    const hasCharacters = Array.isArray(chars) && chars.length > 0;
    if (!hasCharacters && selectedSort === SIMILARITY_SORT_KEY) {
      sortModel.value = DATE_SORT_KEY;
      similarityCharacterModel.value = null;
      return;
    }

    if (hasCharacters && selectedSort === SIMILARITY_SORT_KEY) {
      if (!similarityCharacterModel.value) {
        similarityCharacterModel.value = chars[0].id;
      }
    }
  },
  { immediate: true },
);

watch(
  () => props.selectedCharacter,
  (nextId) => {
    clearCountNew(nextId);
  },
);

defineExpose({ refreshSidebar, openSettingsDialog, startLocalImport });
</script>

<template>
  <ImageImporter
    ref="imageImporterRef"
    :backend-url="props.backendUrl"
    :selected-character-id="props.selectedCharacter"
    :all-pictures-id="props.allPicturesId"
    :unassigned-pictures-id="props.unassignedPicturesId"
    @import-finished="handleImportFinished"
  />
  <CharacterEditor
    :open="characterEditorOpen"
    :character="characterEditorCharacter"
    :backendUrl="props.backendUrl"
    @close="closeCharacterEditor"
    @saved="characterSaved"
  />
  <PictureSetEditor
    :open="setEditorOpen"
    :set="setEditorSet"
    :backendUrl="props.backendUrl"
    @close="closeSetEditor"
    @refresh-sidebar="refreshSidebar"
  />
  <UserSettingsDialog
    v-model:open="settingsDialogOpen"
    v-model:sidebar-thumbnail-size="sidebarThumbnailSizeModel"
    v-model:date-format="dateFormatModel"
    v-model:theme-mode="themeModeModel"
    @update:hidden-tags="(value) => emit('update:hidden-tags', value)"
    @update:apply-tag-filter="(value) => emit('update:apply-tag-filter', value)"
  />

  <v-dialog v-model="taskManagerOpen" width="980">
    <TaskManager :active="taskManagerOpen" @close="taskManagerOpen = false" />
  </v-dialog>

  <aside
    ref="sidebarRootRef"
    class="sidebar"
    :class="{ 'sidebar-collapsed': props.collapsed }"
    :style="sidebarThumbStyle"
  >
    <div class="sidebar-brand">
      <div class="sidebar-brand-left">
        <a
          v-if="!props.collapsed"
          href="https://pixelurgy.github.io/pixlvault/"
          target="_blank"
          rel="noopener noreferrer"
          class="sidebar-brand-logo-link"
        >
          <img
            src="/Logo.png"
            alt="PixlVault logo"
            class="sidebar-brand-logo"
          />
        </a>
        <div v-if="!props.collapsed" class="sidebar-brand-text">
          <span class="sidebar-brand-title">PixlVault</span>
          <div class="sidebar-brand-subtitle-row">
            <span class="sidebar-brand-version">v{{ appVersion }}</span>
            <a
              v-if="updateAvailable"
              :href="latestVersionUrl"
              target="_blank"
              rel="noopener noreferrer"
              class="sidebar-update-available"
              >&#x2191; v{{ latestVersion }} available</a
            >
          </div>
        </div>
      </div>
      <v-btn
        icon
        class="sidebar-brand-toggle"
        :title="props.collapsed ? 'Show sidebar' : 'Hide sidebar'"
        @click.stop="emit('toggle-sidebar')"
      >
        <v-icon>{{
          props.collapsed ? "mdi-chevron-right" : "mdi-chevron-left"
        }}</v-icon>
      </v-btn>
    </div>
    <div class="sidebar-collapsed-divider"></div>
    <template v-if="props.collapsed">
      <div class="sidebar-collapsed-list">
        <div
          :class="[
            'sidebar-collapsed-item',
            { active: props.selectedCharacter === props.allPicturesId },
          ]"
          title="All Pictures"
          @click="selectCharacter(props.allPicturesId, 'All Pictures')"
        >
          <v-icon>mdi-image-multiple</v-icon>
        </div>
        <div
          :class="[
            'sidebar-collapsed-item',
            { active: props.selectedCharacter === props.unassignedPicturesId },
          ]"
          title="Unassigned Pictures"
          @click="
            selectCharacter(props.unassignedPicturesId, 'Unassigned Pictures')
          "
        >
          <v-icon>mdi-help-circle-outline</v-icon>
        </div>
        <div
          :class="[
            'sidebar-collapsed-item',
            { active: props.selectedCharacter === props.scrapheapPicturesId },
          ]"
          title="Scrapheap"
          @click="selectCharacter(props.scrapheapPicturesId, 'Scrapheap')"
        >
          <v-icon>mdi-trash-can-outline</v-icon>
        </div>
        <div class="sidebar-collapsed-divider"></div>
        <button
          v-for="char in sortedCharacters"
          :key="char.id"
          :class="[
            'sidebar-collapsed-thumb',
            {
              active: props.selectedCharacter === char.id,
              droppable: dragOverCharacter === char.id,
            },
          ]"
          :ref="(el) => registerCharacterRef(char.id, el)"
          :title="char.name || 'Character'"
          @click="selectCharacter(char.id, char.name || 'Character')"
          @dragover.prevent="handleDragOverCharacter(char.id)"
          @dragleave="handleDragLeaveCharacter"
          @drop.prevent="
            handleDropOnCharacter({ characterId: char.id, event: $event })
          "
        >
          <img
            :src="characterThumbnails[char.id] || unknownPerson"
            alt=""
            :width="sidebarThumbnailSizeModel"
            :height="sidebarThumbnailSizeModel"
            class="sidebar-character-thumb"
          />
        </button>
        <div
          v-if="nonReferenceSets.length"
          class="sidebar-collapsed-divider"
        ></div>
        <div
          v-for="pset in nonReferenceSets"
          :key="pset.id"
          :class="[
            'sidebar-collapsed-item',
            {
              active: props.selectedSet === pset.id,
              droppable: dragOverSet === pset.id,
            },
          ]"
          :title="pset.name || 'Picture Set'"
          @click="selectSet(pset.id, pset.name || 'Picture Set')"
          @dragover.prevent="dragOverSetItem(pset.id)"
          @dragleave="dragLeaveSetItem"
          @drop.prevent="handleDropOnSet(pset.id, $event)"
        >
          <img
            v-if="hasSetThumbnail(pset)"
            :src="getSetThumbnail(pset.id)"
            alt=""
            class="sidebar-set-thumb-image sidebar-set-thumb-image--collapsed"
            :width="sidebarThumbnailSizeModel"
            :height="sidebarThumbnailSizeModel"
            @error="handleSetThumbnailError(pset.id)"
          />
          <v-icon width="40" size="40" v-else>mdi-image-album</v-icon>
        </div>
        <div class="sidebar-collapsed-spacer"></div>
        <div
          class="sidebar-collapsed-item"
          title="Task Manager"
          @click.stop="taskManagerOpen = true"
        >
          <v-icon>mdi-timeline-clock-outline</v-icon>
        </div>
      </div>
    </template>
    <template v-else>
      <div class="sidebar-section-header">
        Pictures
        <span class="sidebar-header-spacer"></span>
        <div class="sidebar-header-actions">
          <v-icon
            class="upload-pictures-inline"
            @click.stop="openImportDialog"
            title="Import photos"
          >
            mdi-cloud-upload-outline
          </v-icon>
        </div>
      </div>
      <div
        :class="[
          'sidebar-list-item',
          { active: props.selectedCharacter === props.allPicturesId },
        ]"
        @click="selectCharacter(props.allPicturesId, 'All Pictures')"
      >
        <span class="sidebar-list-icon">
          <v-icon size="44">mdi-image-multiple</v-icon>
        </span>
        <span class="sidebar-list-label">All Pictures</span>
        <span class="sidebar-list-count">
          <span v-if="isCountNew(props.allPicturesId)" class="sidebar-new-tag">
            new
          </span>
          {{ categoryCounts[props.allPicturesId] ?? "" }}
        </span>
      </div>
      <div
        :class="[
          'sidebar-list-item',
          { active: selectedCharacter === props.unassignedPicturesId },
        ]"
        @click="
          selectCharacter(props.unassignedPicturesId, 'Unassigned Pictures')
        "
      >
        <span class="sidebar-list-icon">
          <v-icon size="44">mdi-help-circle-outline</v-icon>
        </span>
        <span class="sidebar-list-label">Unassigned Pictures</span>
        <span class="sidebar-list-count">
          <span
            v-if="isCountNew(props.unassignedPicturesId)"
            class="sidebar-new-tag"
          >
            new
          </span>
          {{ categoryCounts[props.unassignedPicturesId] ?? "" }}
        </span>
      </div>
      <div
        :class="[
          'sidebar-list-item',
          { active: selectedCharacter === props.scrapheapPicturesId },
        ]"
        @click="selectCharacter(props.scrapheapPicturesId, 'Scrapheap')"
      >
        <span class="sidebar-list-icon">
          <v-icon size="44">mdi-trash-can-outline</v-icon>
        </span>
        <span class="sidebar-list-label">Scrapheap</span>
        <span class="sidebar-list-count">
          <span
            v-if="isCountNew(props.scrapheapPicturesId)"
            class="sidebar-new-tag"
          >
            new
          </span>
          {{ categoryCounts[props.scrapheapPicturesId] ?? "" }}
        </span>
      </div>

      <div class="sidebar-section-header">
        People
        <span class="sidebar-header-spacer"></span>
        <div class="sidebar-header-actions">
          <v-icon
            v-if="selectedCharacterObj"
            class="edit-character-inline"
            @click.stop="openCharacterEditor(selectedCharacterObj)"
            title="Edit selected character"
          >
            mdi-pencil
          </v-icon>
          <v-icon
            v-if="
              props.selectedCharacter &&
              props.selectedCharacter !== props.allPicturesId &&
              props.selectedCharacter !== props.unassignedPicturesId &&
              props.selectedCharacter !== props.scrapheapPicturesId
            "
            class="delete-character-inline"
            color="white"
            @click.stop="deleteCharacter"
            title="Delete selected character"
          >
            mdi-trash-can-outline
          </v-icon>
          <v-icon
            class="add-character-inline"
            @click.stop="createCharacter"
            title="Add character"
          >
            mdi-plus
          </v-icon>
        </div>
      </div>
      <div
        v-if="sidebarError"
        class="sidebar-error-bubble"
        :style="
          sidebarErrorPosition
            ? {
                top: `${sidebarErrorPosition.top}px`,
                left: `${sidebarErrorPosition.left}px`,
              }
            : { top: '72px', left: '20px' }
        "
      >
        {{ sidebarError }}
      </div>
      <div v-if="sortedCharacters.length === 0" class="sidebar-character-group">
        <div class="sidebar-list-item">
          No characters found. Click the + button to add one.
        </div>
      </div>
      <div
        v-if="sortedCharacters.length > 0"
        v-for="char in sortedCharacters"
        :key="char.id"
        class="sidebar-character-group"
      >
        <div
          :class="[
            'sidebar-list-item',
            {
              active: selectedCharacter === char.id,
              'reference-active': props.selectedReferenceCharacter === char.id,
              droppable: dragOverCharacter === char.id,
            },
          ]"
          :ref="(el) => registerCharacterRef(char.id, el)"
          @click="selectCharacter(char.id, char.name || 'Character')"
          @dragover.prevent="handleDragOverCharacter(char.id)"
          @dragleave="handleDragLeaveCharacter"
          @drop.prevent="
            handleDropOnCharacter({ characterId: char.id, event: $event })
          "
        >
          <span class="sidebar-list-icon">
            <img
              :src="
                characterThumbnails[char.id]
                  ? characterThumbnails[char.id]
                  : unknownPerson
              "
              alt=""
              :width="sidebarThumbnailSizeModel"
              :height="sidebarThumbnailSizeModel"
              class="sidebar-character-thumb"
            />
          </span>
          <span class="sidebar-list-label">
            <v-tooltip
              location="top"
              :disabled="!labelNeedsTooltip(`char-${char.id}`)"
            >
              <template #activator="{ props }">
                <span
                  v-bind="props"
                  :ref="mergeTooltipRef(props, `char-${char.id}`)"
                  class="sidebar-list-label-text"
                >
                  {{ char.name.charAt(0).toUpperCase() + char.name.slice(1) }}
                </span>
              </template>
              <span>{{ char.name }}</span>
            </v-tooltip>
          </span>
          <span class="sidebar-character-actions">
            <v-icon
              :class="[
                'sidebar-character-reference',
                { active: props.selectedReferenceCharacter === char.id },
              ]"
              size="20"
              :title="'Reference pictures'"
              @click.stop="
                selectReferencePictures(
                  char.id,
                  char.name || 'Character',
                  `Reference: ${char.name || 'Character'}`,
                )
              "
            >
              mdi-image-multiple
            </v-icon>
            <span class="sidebar-list-count">
              <span v-if="isCountNew(char.id)" class="sidebar-new-tag">
                new
              </span>
              <span
                v-if="props.selectedReferenceCharacter === char.id"
                class="sidebar-reference-label"
              >
                Ref
              </span>
              <span v-else>
                {{ categoryCounts[char.id] ?? "" }}
              </span>
            </span>
          </span>
        </div>
      </div>

      <div class="sidebar-section-header">
        Picture Sets
        <span class="sidebar-header-spacer"></span>
        <div class="sidebar-header-actions">
          <v-icon
            v-if="selectedSetObj"
            class="edit-set-inline"
            @click.stop="openSetEditor(selectedSetObj)"
            title="Edit selected set"
          >
            mdi-pencil
          </v-icon>
          <v-icon
            v-if="selectedSet"
            class="delete-character-inline"
            color="white"
            @click.stop="handleDeleteSet"
            title="Delete selected set"
          >
            mdi-trash-can-outline
          </v-icon>
          <v-icon
            class="add-character-inline"
            @click.stop="createSet"
            title="Create new set"
          >
            mdi-plus
          </v-icon>
        </div>
      </div>
      <div v-if="pictureSets.length === 0" class="sidebar-list-item">
        No picture sets. Click the + button to create one.
      </div>
      <template
        v-for="(pset, idx) in pictureSets.filter(
          (pset) => pset.reference_character == null,
        )"
        :key="pset.id"
      >
        <div
          :class="[
            'sidebar-list-item',
            'sidebar-set-item',
            {
              active: selectedSet === pset.id,
              droppable: dragOverSet === pset.id,
            },
          ]"
          :ref="(el) => registerSetRef(pset.id, el)"
          @click="selectSet(pset.id, pset.name || 'Picture Set')"
          @dragover.prevent="dragOverSetItem(pset.id)"
          @dragleave="dragLeaveSetItem"
          @drop.prevent="handleDropOnSet(pset.id, $event)"
        >
          <span class="sidebar-list-icon">
            <img
              v-if="hasSetThumbnail(pset)"
              :src="getSetThumbnail(pset.id)"
              alt=""
              class="sidebar-set-thumb-image sidebar-set-thumb-image--large"
              :width="sidebarThumbnailSizeLarge"
              :height="sidebarThumbnailSizeLarge"
              @error="handleSetThumbnailError(pset.id)"
            />
            <v-icon v-else size="44">mdi-image-album</v-icon>
          </span>
          <span class="sidebar-list-label">
            <v-tooltip
              location="top"
              :disabled="!labelNeedsTooltip(`set-${pset.id}`)"
            >
              <template #activator="{ props }">
                <span
                  v-bind="props"
                  :ref="mergeTooltipRef(props, `set-${pset.id}`)"
                  class="sidebar-list-label-text"
                >
                  {{ pset.name }}
                </span>
              </template>
              <span>{{ pset.name }}</span>
            </v-tooltip>
          </span>
          <span class="sidebar-list-count">
            {{ pset.picture_count ?? 0 }}
          </span>
        </div>
      </template>

      <div
        class="sidebar-searchbar-wrapper"
        style="
          display: flex;
          flex-direction: column;
          gap: 2px;
          align-items: stretch;
        "
      ></div>
      <div class="sidebar-footer-spacer"></div>
      <div class="sidebar-footer">
        <div
          class="sidebar-list-item sidebar-footer-item"
          @click="taskManagerOpen = true"
        >
          <span class="sidebar-list-icon">
            <v-icon>mdi-timeline-clock-outline</v-icon>
          </span>
          <span class="sidebar-list-label">Task Manager</span>
        </div>
      </div>
    </template>
  </aside>
  <div
    v-if="sidebarNotice && sidebarNoticePosition"
    class="sidebar-inline-notice"
    :style="{
      top: `${sidebarNoticePosition.top}px`,
      left: `${sidebarNoticePosition.left}px`,
    }"
  >
    {{ sidebarNotice }}
  </div>
</template>

<style scoped>
.sidebar-native-select {
  background: rgba(var(--v-theme-surface), 0.3);
  color: rgb(var(--v-theme-on-surface));
  border-radius: 4px;
  min-height: 32px;
  height: 32px;
  font-size: 1em;
  box-shadow: 2px 2px 6px rgba(var(--v-theme-shadow), 0.2);
  margin-left: 6px;
  box-sizing: border-box;
  padding-left: 8px;
  padding-right: 8px;
  border: 1px solid rgba(var(--v-theme-border), 0.5);
  width: 230px;
  transition: border 0.15s;
}
.sidebar-native-select:focus {
  border: 1.5px solid rgb(var(--v-theme-accent));
}
.sidebar-native-select-chevron {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  pointer-events: none;
  color: rgb(var(--v-theme-on-surface));
  display: flex;
  align-items: center;
  height: 18px;
  z-index: 2;
}

.sidebar-search-result-label {
  display: flex;
  align-items: center;
  min-height: 32px;
  padding: 0 12px;
  margin-left: 6px;
  border-radius: 4px;
  background: rgba(var(--v-theme-surface), 0.2);
  color: rgba(var(--v-theme-on-surface), 0.7);
  border: 1px dashed rgba(var(--v-theme-border), 0.5);
  font-size: 0.95em;
}
/* Sidebar right edge for counts */
.sidebar {
  width: 280px;
  --sidebar-right-edge: 14px;
  --sidebar-header-action-right-edge: 0px;
  --sidebar-thumb-size: 36px;
  --sidebar-thumb-size-large: calc(var(--sidebar-thumb-size) + 8px);
  color: rgb(var(--v-theme-sidebar-text));
  background: rgb(var(--v-theme-sidebar));
  padding: 4px 0px 12px 0px;
  margin: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  min-height: 0;
  height: 100%;
  max-height: 100%;
  overflow-x: visible;
  overflow-y: auto;
  scrollbar-color: rgb(var(--v-theme-accent)) rgba(var(--v-theme-shadow), 0.15);
  box-sizing: border-box;
}

.sidebar.sidebar-collapsed {
  width: calc(var(--sidebar-thumb-size) + 20px);
  overflow-x: visible;
  overflow-y: hidden;
}

.sidebar.sidebar-collapsed .sidebar-brand {
  justify-content: center;
}

.sidebar.sidebar-collapsed .sidebar-brand-toggle:hover {
  justify-content: center;
  background-color: rgba(var(--v-theme-accent), 0.4);
}

.sidebar.sidebar-collapsed .sidebar-brand-left {
  display: none;
}

.sidebar-brand {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 2px 4px 2px 2px;
  margin-bottom: 8px;
}

.sidebar-brand-left {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 2px 2px 10px;
}

.sidebar-brand-logo {
  width: 40px;
  height: 40px;
  object-fit: contain;
  transition:
    filter 0.2s ease,
    transform 0.2s ease;
}

.sidebar-brand-logo-link {
  display: flex;
  align-items: center;
  border-radius: 6px;
  outline: none;
}

.sidebar-brand-logo-link:hover .sidebar-brand-logo {
  filter: drop-shadow(0 0 8px rgba(var(--v-theme-accent), 0.9))
    drop-shadow(0 0 16px rgba(var(--v-theme-accent), 0.5));
  transform: scale(1.08);
}

.sidebar-brand-toggle:hover {
  background-color: rgb(var(--v-theme-accent));
}

.sidebar-brand-title {
  font-family: "PressStart2P", monospace;
  font-size: 0.95em;
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-brand-version {
  font-size: 0.8rem;
  opacity: 0.6;
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-brand-text {
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 0;
}

.sidebar-brand-subtitle-row {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.sidebar-update-available {
  font-size: 0.8rem;
  color: rgba(var(--v-theme-accent), 0.8);
  text-decoration: none;
}

.sidebar-update-available:hover {
  text-decoration: underline;
}

.sidebar-brand-toggle {
  min-width: 36px;
  min-height: 36px;
  width: 36px;
  height: 36px;
  padding: 0;
  border-radius: 8px;
  background: transparent;
  border: none;
  box-shadow: none;
}

.sidebar-brand-toggle:focus,
.sidebar-brand-toggle:focus-visible,
.sidebar-brand-toggle:active {
  outline: none;
  box-shadow: none;
}

.sidebar-collapsed-list {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 4px 0 8px;
  overflow-y: auto;
  flex: 1 1 auto;
  min-height: 0;
}

.sidebar-collapsed-spacer {
  flex: 1 1 auto;
  width: 100%;
}

.sidebar-collapsed-item {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  cursor: pointer;
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-collapsed-item.active {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  box-shadow: inset 0 0 0 3px rgb(var(--v-theme-primary));
}

.sidebar-collapsed-item.droppable {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  box-shadow: inset 0 0 0 3px rgb(var(--v-theme-primary));
}

.sidebar-collapsed-item:hover {
  filter: brightness(1.1);
  background-color: rgba(var(--v-theme-accent), 0.4);
}

.sidebar-collapsed-thumb {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  border-radius: 8px;
  border: none;
  padding: 0;
  background: transparent;
  cursor: pointer;
  outline: none;
  box-shadow: none;
  transition:
    background 0.18s ease,
    box-shadow 0.18s ease,
    filter 0.18s ease;
}

.sidebar-collapsed-thumb img {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  object-fit: contain;
  border-radius: 8px;
  display: block;
  position: relative;
  z-index: 1;
}

.sidebar-collapsed-thumb::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: 8px;
  pointer-events: none;
  opacity: 0;
  z-index: 2;
  box-shadow: inset 0 0 0 3px transparent;
  transition:
    box-shadow 0.18s ease,
    opacity 0.18s ease;
}

.sidebar-collapsed-thumb .sidebar-character-thumb {
  filter: drop-shadow(0 2px 6px rgba(var(--v-theme-shadow), 0.35));
}

.sidebar-collapsed-thumb:focus,
.sidebar-collapsed-thumb:focus-visible,
.sidebar-collapsed-thumb:active,
.sidebar-collapsed-thumb img:focus,
.sidebar-collapsed-thumb img:focus-visible,
.sidebar-collapsed-thumb img:active {
  outline: none;
  box-shadow: none;
}

.sidebar-collapsed-thumb.active {
  background: rgb(var(--v-theme-primary));
}

.sidebar-collapsed-thumb.active::after {
  opacity: 1;
  box-shadow: inset 0 0 0 3px rgb(var(--v-theme-primary));
}

.sidebar-collapsed-thumb:hover {
  filter: brightness(1.1);
  background-color: rgba(var(--v-theme-accent), 0.4);
}

.sidebar-collapsed-thumb:hover::after {
  opacity: 1;
  box-shadow: inset 0 0 0 3px rgba(var(--v-theme-accent), 0.7);
}

.sidebar-collapsed-thumb.droppable {
  background: rgb(var(--v-theme-primary));
}

.sidebar-collapsed-thumb.droppable::after {
  opacity: 1;
  box-shadow: inset 0 0 0 3px rgb(var(--v-theme-primary));
}

.sidebar-collapsed-divider {
  width: 100%;
  height: 1px;
  margin-top: 1px;
  margin-bottom: 1px;
  background: rgba(var(--v-theme-background), 0.3);
}

@media (max-width: 900px) {
  .sidebar {
    height: 100dvh;
    max-height: 100dvh;
  }

  .sidebar.sidebar-collapsed {
    display: none;
  }
}

.sidebar::-webkit-scrollbar {
  width: 8px;
}

.sidebar::-webkit-scrollbar-thumb {
  background: rgb(var(--v-theme-accent));
  border-radius: 8px;
}

.sidebar::-webkit-scrollbar-track {
  background: rgba(var(--v-theme-shadow), 0.15);
}

.sidebar-section-header {
  position: relative;
  font-size: 1.1rem;
  font-weight: bold;
  min-height: 42px;
  padding: 2px 8px;
  padding-right: var(--sidebar-header-action-right-edge) !important;
  display: flex;
  align-items: center;
  color: rgb(var(--v-theme-sidebar-text));
}

.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}

.sidebar-list-item,
.sidebar-list-item.active {
  display: flex;
  align-items: center;
  min-height: max(52px, calc(var(--sidebar-thumb-size) + 8px));
  padding: 2px 6px;
  padding-right: var(--sidebar-right-edge) !important;
  cursor: pointer;
  border-radius: 0;
  margin-bottom: 0;
  font-size: 0.9em;
  font-weight: 500;
  background: transparent;
  color: rgb(var(--v-theme-sidebar-text));
  transition:
    background 0.18s,
    color 0.18s;
  width: 100%;
}

.sidebar-footer-spacer {
  flex: 1 1 auto;
}

.sidebar-footer {
  padding: 4px 0 0 0;
}

.sidebar-footer-item {
  margin-bottom: 0;
}

.sidebar-list-item.active {
  background: rgba(var(--v-theme-primary), 0.6);
  color: rgb(var(--v-theme-on-primary));
  border-right: 0;
  position: relative;
}

.sidebar-list-item.active .sidebar-list-count,
.sidebar-list-item.reference-active .sidebar-list-count {
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-list-item.reference-active {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-primary), 0.35);
}

.sidebar-list-item:hover {
  background: rgba(var(--v-theme-accent), 0.6);
}

.sidebar-list-item.droppable {
  filter: brightness(1.2);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-header-spacer {
  flex: 1 1 auto;
}

.sidebar-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 64px;
  justify-content: flex-end;
  margin-left: auto;
  padding-right: var(--sidebar-header-action-right-edge) !important;
}

.sidebar-header-actions .v-icon {
  min-width: 36px;
  min-height: 36px;
  justify-content: center;
  text-align: center;
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-list-icon {
  display: flex;
  align-items: center;
  margin-right: 6px;
  justify-content: center;
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  overflow: visible;
}

.sidebar-list-icon .v-icon,
.sidebar-collapsed-item .v-icon,
.sidebar-brand-toggle .v-icon {
  color: rgb(var(--v-theme-sidebar-text));
}

.sidebar-list-label {
  flex: 1;
  min-width: 0;
  text-align: left;
  padding-left: 4px;
}

.sidebar-list-label-text {
  display: block;
  width: 100%;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-character-thumb {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  object-fit: contain;
  border-radius: 6px;
  background: transparent;
  display: inline-block;
  filter: drop-shadow(0 2px 6px rgba(var(--v-theme-shadow), 0.35));
}

.sidebar-set-thumb-image {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  border-radius: 6px;
  object-fit: cover;
  background: transparent;
  border: none;
  box-shadow: none;
  display: block;
  box-sizing: border-box;
}

.sidebar-set-thumb-image--collapsed {
  width: var(--sidebar-thumb-size);
  height: var(--sidebar-thumb-size);
  margin: 0;
  border: none;
  box-shadow: none;
}

.sidebar-set-thumb-image--large {
  width: var(--sidebar-thumb-size-large);
  height: var(--sidebar-thumb-size-large);
  border-radius: 8px;
}

.sidebar-collapsed-item,
.sidebar-collapsed-thumb {
  position: relative;
  overflow: hidden;
}

.sidebar-character-group {
  display: flex;
  flex-direction: column;
  width: 100%;
}

.sidebar-error-bubble {
  position: fixed;
  top: 72px;
  left: 20px;
  transform: translateY(-50%);
  z-index: 1200;
  color: rgb(var(--v-theme-on-error));
  background: rgba(var(--v-theme-error), 0.8);
  padding: 10px 16px;
  border-radius: 14px;
  font-size: 0.9em;
  line-height: 1.3;
  box-shadow: 0 8px 20px rgba(var(--v-theme-shadow), 0.25);
  pointer-events: none;
  max-width: 360px;
  white-space: normal;
  word-break: break-word;
}

.sidebar-list-count {
  font-size: 0.9em;
  color: rgb(var(--v-theme-sidebar-text));
  min-width: 2.6em;
  width: 2.6em;
  text-align: right;
  margin: 0;
  font-weight: 400;
  opacity: 0.85;
  letter-spacing: 0.01em;
  align-self: center;
  display: inline-flex;
  justify-content: flex-end;
}

.sidebar-new-tag {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 0.65em;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 2px 2px;
  margin-right: 4px;
  border-radius: 4px;
  color: rgb(var(--v-theme-on-primary));
  background: rgba(var(--v-theme-primary), 0.7);
  position: relative;
  top: -2px;
}

.sidebar-character-actions {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  justify-content: flex-end;
}

.sidebar-character-actions .sidebar-list-count {
  margin: 0;
}

.sidebar-character-toggle {
  cursor: pointer;
  color: rgb(var(--v-theme-sidebar-text));
  opacity: 0.8;
  margin-right: 4px;
}

.sidebar-character-toggle:hover {
  opacity: 1;
  color: rgb(var(--v-theme-on-primary));
}

.add-character-inline {
  color: rgb(var(--v-theme-sidebar-text)) !important;
  font-size: 1.4rem;
  cursor: pointer;
  background: transparent;
  border-radius: 8px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.2s;
}

.add-character-inline:hover {
  background: rgb(var(--v-theme-accent));
}

.edit-character-inline,
.edit-set-inline {
  color: rgb(var(--v-theme-sidebar-text)) !important;
  font-size: 1.2rem;
  cursor: pointer;
  background: transparent;
  border-radius: 8px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 32px;
  transition:
    background 0.2s,
    color 0.2s;
}

.edit-character-inline:hover,
.edit-set-inline:hover {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary)) !important;
}

.upload-pictures-inline {
  color: rgb(var(--v-theme-sidebar-text)) !important;
  font-size: 1.2rem;
  cursor: pointer;
  background: transparent;
  border-radius: 8px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 32px;
  transition: background 0.2s;
}

.upload-pictures-inline:hover {
  background: rgb(var(--v-theme-accent));
}

.delete-character-inline {
  color: rgb(var(--v-theme-sidebar-text)) !important;
  font-size: 1.1rem;
  cursor: pointer;
  background: transparent;
  border-radius: 8px;
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 0 0 32px;
  transition:
    background 0.2s,
    color 0.2s;
}

.delete-character-inline:hover {
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error)) !important;
}

.sidebar-sort {
  display: flex;
  flex-direction: column;
}

.sidebar-sort-select {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: 6px !important;
  min-height: 36px !important;
  height: 36px !important;
  font-size: 0.97em;
  box-shadow: none;
  margin-top: 0px;
  margin-bottom: 2px;
  align-items: center;
  padding-left: 6px;
  padding-right: 6px;
}

/* Remove extra height from v-input root for select */
.sidebar-sort-select .v-input__control,
.sidebar-sort-select .v-field {
  min-height: 32px !important;
  height: 32px !important;
  border-radius: 12px !important;
  box-shadow: none !important;
}

.sidebar-sort-select .v-field__input {
  min-height: 28px !important;
  height: 28px !important;
  padding-top: 2px !important;
  padding-bottom: 2px !important;
  align-items: center;
}

.sidebar-set-item {
  position: relative;
  overflow: visible;
}

.sidebar-character-reference {
  margin-right: 2px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  background: transparent;
  opacity: 0.65;
  transition:
    opacity 0.2s ease,
    color 0.2s ease;
}

.sidebar-character-reference:hover {
  opacity: 1;
  background: rgba(var(--v-theme-error), 0.16);
}

.sidebar-character-reference.active {
  opacity: 1;
  color: rgb(var(--v-theme-on-error));
  background: rgba(var(--v-theme-error), 0.22);
}

.sidebar-reference-label {
  display: inline-flex;
  align-items: right;
  justify-content: flex-end;
  padding: 2px 4px;
  margin-right: 0px;
  border-radius: 999px;
  color: rgb(var(--v-theme-on-error));
  background: rgb(var(--v-theme-error));
  font-weight: 700;
  font-size: 0.7em;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.sidebar-inline-notice {
  position: fixed;
  transform: translateY(-50%);
  background: rgba(var(--v-theme-secondary), 0.75);
  color: rgb(var(--v-theme-on-secondary));
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 0.9em;
  white-space: nowrap;
  pointer-events: none;
  z-index: 1000 !important;
}

@media (max-width: 900px) {
  .sidebar {
    width: 100%;
    min-height: 100%;
    height: 100%;
  }

  .sidebar-list-item,
  .sidebar-list-item.active {
    min-height: 56px;
    padding: 6px 10px;
  }

  .sidebar-section-header {
    min-height: 48px;
    padding: 6px 8px;
  }

  .sidebar-list-icon {
    width: var(--sidebar-thumb-size);
    height: var(--sidebar-thumb-size);
  }

  .sidebar-character-thumb {
    width: var(--sidebar-thumb-size);
    height: var(--sidebar-thumb-size);
  }

  .add-character-inline,
  .delete-character-inline,
  .edit-character-inline,
  .edit-set-inline {
    width: 44px;
    height: 44px;
  }

  .sidebar-header-actions .v-icon {
    min-width: 44px;
    min-height: 44px;
  }
}
</style>
