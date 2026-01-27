<script setup>
import { computed, ref, onMounted, watch } from "vue";
import ImageImporter from "./ImageImporter.vue";
import CharacterEditor from "./CharacterEditor.vue";
import PictureSetEditor from "./PictureSetEditor.vue";
import SearchOverlay from "./SearchOverlay.vue";
import unknownPerson from "../assets/unknown-person.png"; // Fallback avatar for characters without thumbnails
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  collapsed: { type: Boolean, default: false },
  selectedCharacter: { type: [String, Number, null], default: null },
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  selectedSet: { type: [Number, null], default: null },
  searchQuery: { type: String, default: "" },
  selectedSort: { type: String, default: "" },
  selectedDescending: { type: Boolean, default: false },
  selectedSimilarityCharacter: { type: [String, Number, null], default: null },
  stackThreshold: { type: [String, Number, null], default: null },
  backendUrl: { type: String, required: true },
});

const emit = defineEmits([
  "select-character",
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
  "update:stack-threshold",
  "toggle-sidebar",
]);

const imageImporterRef = ref(null);
const uploadInputRef = ref(null);
const sortSelectRef = ref(null);

const dragOverSet = ref(null);

// --- Sorting State ---
const sortOptions = ref([]);

// --- Character & Sidebar State ---
const characters = ref([]);
const categoryCounts = ref({
  [props.allPicturesId]: 0,
  [props.unassignedPicturesId]: 0,
});

const flashCountsNextFetch = ref(false);
const countNewTags = ref({});
const knownCountIds = new Set();

const characterThumbnails = ref({});
const expandedCharacters = ref({});

// Ensure collapsedCharacters is reactive and initialized for all characters
const collapsedCharacters = ref({});

const dragOverCharacter = ref(null);
const nextCharacterNumber = ref(1);

// --- Picture Sets State ---
const pictureSets = ref([]);
const referencePictureSetsByCharacter = ref({});

// --- Character Editor State ---
const characterEditorOpen = ref(false);
const characterEditorCharacter = ref(null);

const setEditorOpen = ref(false);
const setEditorSet = ref(null);
const settingsDialogOpen = ref(false);
const settingsUsername = ref("");
const settingsHasPassword = ref(false);
const settingsLoading = ref(false);
const settingsError = ref("");
const settingsSuccess = ref("");
const currentPassword = ref("");
const newPassword = ref("");
const showNewPassword = ref(false);
const tokensLoading = ref(false);
const tokensError = ref("");
const tokens = ref([]);
const tokenDescription = ref("");
const newlyCreatedToken = ref("");
const tokenDialogOpen = ref(false);
const tokenDeleteDialogOpen = ref(false);
const tokenToDelete = ref(null);
const smartScorePenalizedTags = ref([]);
const smartScoreTagInput = ref("");
const smartScoreTagsLoading = ref(false);
const smartScoreTagsError = ref("");
const smartScoreTagsSuccess = ref("");
const settingsTab = ref("preferences");

async function fetchSettingsAuth() {
  settingsLoading.value = true;
  settingsError.value = "";
  try {
    const res = await apiClient.get("/users/me/auth");
    settingsUsername.value = res.data?.username || "";
    settingsHasPassword.value = Boolean(res.data?.has_password);
  } catch (e) {
    settingsError.value = "Failed to load account settings.";
  } finally {
    settingsLoading.value = false;
  }
}

function resetSettingsForm() {
  settingsError.value = "";
  settingsSuccess.value = "";
  currentPassword.value = "";
  newPassword.value = "";
  showNewPassword.value = false;
  tokensError.value = "";
  tokenDescription.value = "";
  newlyCreatedToken.value = "";
  tokenDialogOpen.value = false;
  tokenDeleteDialogOpen.value = false;
  tokenToDelete.value = null;
  smartScoreTagInput.value = "";
  smartScoreTagsError.value = "";
  smartScoreTagsSuccess.value = "";
}

function normalizeSmartScoreTags(tags) {
  const list = Array.isArray(tags) ? tags : [];
  const normalized = [];
  const seen = new Set();
  for (const tag of list) {
    if (tag == null) continue;
    const clean = String(tag).trim().toLowerCase();
    if (!clean || seen.has(clean)) continue;
    seen.add(clean);
    normalized.push(clean);
  }
  return normalized;
}

async function fetchSmartScoreSettings() {
  smartScoreTagsLoading.value = true;
  smartScoreTagsError.value = "";
  try {
    const res = await apiClient.get("/users/me/config");
    smartScorePenalizedTags.value = normalizeSmartScoreTags(
      res.data?.smart_score_penalized_tags,
    );
  } catch (e) {
    smartScoreTagsError.value = "Failed to load smart score settings.";
  } finally {
    smartScoreTagsLoading.value = false;
  }
}

async function saveSmartScoreTags(nextTags) {
  smartScoreTagsLoading.value = true;
  smartScoreTagsError.value = "";
  smartScoreTagsSuccess.value = "";
  try {
    const normalized = normalizeSmartScoreTags(nextTags);
    await apiClient.patch("/users/me/config", {
      smart_score_penalized_tags: normalized,
    });
    smartScorePenalizedTags.value = normalized;
    smartScoreTagsSuccess.value = "Saved.";
  } catch (e) {
    smartScoreTagsError.value =
      e?.response?.data?.detail || "Failed to update smart score tags.";
  } finally {
    smartScoreTagsLoading.value = false;
    if (smartScoreTagsSuccess.value) {
      setTimeout(() => {
        smartScoreTagsSuccess.value = "";
      }, 2000);
    }
  }
}

async function addSmartScoreTag() {
  const trimmed = smartScoreTagInput.value.trim().toLowerCase();
  if (!trimmed) return;
  const next = normalizeSmartScoreTags([
    ...smartScorePenalizedTags.value,
    trimmed,
  ]);
  smartScoreTagInput.value = "";
  await saveSmartScoreTags(next);
}

async function removeSmartScoreTag(tag) {
  const next = normalizeSmartScoreTags(
    smartScorePenalizedTags.value.filter((t) => t !== tag),
  );
  await saveSmartScoreTags(next);
}

function formatTokenTimestamp(value) {
  if (!value) return "Never used";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Never used";
  return date.toLocaleString();
}

async function fetchUserTokens() {
  tokensLoading.value = true;
  tokensError.value = "";
  try {
    const res = await apiClient.get("/users/me/token");
    tokens.value = Array.isArray(res.data) ? res.data : [];
  } catch (e) {
    tokensError.value = "Failed to load tokens.";
  } finally {
    tokensLoading.value = false;
  }
}

async function createUserToken() {
  tokensError.value = "";
  const description = tokenDescription.value.trim() || null;
  tokensLoading.value = true;
  try {
    const res = await apiClient.post("/users/me/token", { description });
    newlyCreatedToken.value = res.data?.token || "";
    tokenDialogOpen.value = Boolean(newlyCreatedToken.value);
    tokenDescription.value = "";
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = e?.response?.data?.detail || "Failed to create token.";
  } finally {
    tokensLoading.value = false;
  }
}

function confirmDeleteToken(token) {
  tokenToDelete.value = token;
  tokenDeleteDialogOpen.value = true;
}

async function deleteUserToken() {
  if (!tokenToDelete.value) {
    tokenDeleteDialogOpen.value = false;
    return;
  }
  tokensLoading.value = true;
  tokensError.value = "";
  try {
    await apiClient.delete(`/users/me/token/${tokenToDelete.value.id}`);
    tokenDeleteDialogOpen.value = false;
    tokenToDelete.value = null;
    await fetchUserTokens();
  } catch (e) {
    tokensError.value = e?.response?.data?.detail || "Failed to delete token.";
  } finally {
    tokensLoading.value = false;
  }
}

async function submitPasswordChange() {
  settingsError.value = "";
  settingsSuccess.value = "";
  if (!newPassword.value || newPassword.value.trim().length < 8) {
    settingsError.value = "New password must be at least 8 characters long.";
    return;
  }
  if (settingsHasPassword.value && !currentPassword.value) {
    settingsError.value = "Current password is required.";
    return;
  }
  settingsLoading.value = true;
  try {
    const newPasswordValue = newPassword.value.trim();
    await apiClient.post("/users/me/auth", {
      current_password: currentPassword.value || null,
      new_password: newPasswordValue,
    });
    settingsSuccess.value = "Password updated.";
    currentPassword.value = "";
    newPassword.value = "";
    settingsHasPassword.value = true;
    if (
      typeof window !== "undefined" &&
      "credentials" in navigator &&
      "PasswordCredential" in window &&
      settingsUsername.value &&
      newPasswordValue
    ) {
      try {
        const credential = new PasswordCredential({
          id: settingsUsername.value,
          name: settingsUsername.value,
          password: newPasswordValue,
        });
        await navigator.credentials.store(credential);
      } catch (credentialError) {
        console.debug("Credential store failed:", credentialError);
      }
    }
  } catch (e) {
    settingsError.value =
      e?.response?.data?.detail || "Failed to update password.";
  } finally {
    settingsLoading.value = false;
  }
}

const sidebarNotice = ref(null);
const sidebarNoticeSetId = ref(null);
let sidebarNoticeTimeout = null;

function createSet() {
  setEditorSet.value = null;
  setEditorOpen.value = true;
}

const sidebarError = ref(null);

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
    props.selectedCharacter !== props.unassignedPicturesId
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
const STACKS_SORT_KEY = "PICTURE_STACKS";
const DATE_SORT_KEY = "DATE";

const similarityCharacterOptions = computed(() => {
  let options = sortedCharacters.value.map((c) => ({
    text: c.name,
    value: c.id,
  }));
  return options;
});

const similarityCharacterModel = computed({
  get: () => props.selectedSimilarityCharacter,
  set: (value) => emit("update:similarity-character", value ?? null),
});

const stackThresholdOptions = [
  { label: "Very Loose", value: "0.90" },
  { label: "Loose", value: "0.92" },
  { label: "Medium", value: "0.94" },
  { label: "Strict", value: "0.96" },
  { label: "Very Strict", value: "0.98" },
];

const stackThresholdModel = computed({
  get: () => {
    if (props.stackThreshold == null || props.stackThreshold === "") {
      return "0.94";
    }
    const parsed = parseFloat(String(props.stackThreshold));
    if (!Number.isFinite(parsed) || parsed <= 0) {
      return "0.94";
    }
    return String(props.stackThreshold);
  },
  set: (value) => emit("update:stack-threshold", value),
});

const reactiveSelectedDescending = ref(props.selectedDescending);

watch(
  () => props.selectedDescending,
  (newValue, oldValue) => {
    console.log(
      "[SideBar.vue] Prop selectedDescending changed from",
      oldValue,
      "to",
      newValue,
    );
    reactiveSelectedDescending.value = newValue;
  },
);

const descendingModel = computed({
  get: () => {
    console.log(
      "[SideBar.vue] descendingModel.get() called. Current value:",
      reactiveSelectedDescending.value,
    );
    return reactiveSelectedDescending.value;
  },
  set: (value) => {
    console.log(
      "[SideBar.vue] descendingModel.set() called. New value:",
      value,
    );
    reactiveSelectedDescending.value = value;
    emit("update:selected-sort", { sort: sortModel.value, descending: value });
    console.log(
      "[SideBar.vue] descendingModel.set() completed. Updated reactiveSelectedDescending:",
      reactiveSelectedDescending.value,
    );
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

function handleSortChange() {
  sortSelectRef.value?.blur();
}

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
  console.log("Closing set editor");
  setEditorOpen.value = false;
  setEditorSet.value = null;
}

function selectCharacter(id) {
  clearCountNew(id);
  emit("select-set", null);
  emit("select-character", id);
}

function searchImages(query) {
  emit("search-images", query);
}

function selectSet(setId) {
  emit("select-character", null);
  emit("select-set", setId);
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

function handleImportFinished() {
  emit("import-finished");
}

function openUploadDialog() {
  if (uploadInputRef.value) {
    uploadInputRef.value.click();
  }
}

function handleUploadInputChange(event) {
  const files = Array.from(event?.target?.files || []);
  if (!files.length) return;
  imageImporterRef.value?.startImport(files);
  event.target.value = "";
}

function setLoading(isLoading) {
  emit("set-loading", isLoading);
}

function setError(message) {
  sidebarError.value = message;
  emit("set-error", message);
}

function showNotice(message, setId = null, duration = 4000) {
  if (sidebarNoticeTimeout) {
    clearTimeout(sidebarNoticeTimeout);
    sidebarNoticeTimeout = null;
  }
  sidebarNotice.value = message;
  sidebarNoticeSetId.value = setId;
  sidebarNoticeTimeout = setTimeout(() => {
    sidebarNotice.value = null;
    sidebarNoticeSetId.value = null;
    sidebarNoticeTimeout = null;
  }, duration);
}

function dragOverSetItem(setId) {
  dragOverSet.value = setId;
}

function dragLeaveSetItem() {
  dragOverSet.value = null;
}

// Watch sortedCharacters and initialize collapse state for all characters
watch(
  () => sortedCharacters.value,
  (chars) => {
    chars.forEach((char) => {
      if (!(char.id in collapsedCharacters.value)) {
        collapsedCharacters.value[char.id] = true;
      }
    });
  },
  { immediate: true },
);

function toggleCharacterCollapse(charId) {
  collapsedCharacters.value[charId] = !collapsedCharacters.value[charId];
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
  if (shouldFlash && prevValue !== value) {
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
    console.log("characters", characters.value);
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
  console.log("Refreshing sidebar");
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
    console.log("Fetched sort options:", options);

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
  } catch (e) {
    console.error("Error fetching sort options:", e);
    sortOptions.value = [];
  }
}

// Ensure sortedCharacters is fetched before fetchSortOptions
async function fetchSortedCharactersAndSortOptions() {
  try {
    await fetchCharacters(); // Fetch characters first
    await fetchSortOptions(); // Then fetch sort options
  } catch (e) {
    console.error("Error fetching sorted characters and sort options:", e);
  }
}

// --- Picture Sets ---
async function fetchPictureSets() {
  try {
    const res = await apiClient.get(`${props.backendUrl}/picture_sets`);

    const sets = await res.data; // Axios responses use `data` for the payload
    pictureSets.value = Array.isArray(sets) ? [...sets] : [];
    console.log("Found picture sets:", pictureSets.value);
    referencePictureSetsByCharacter.value = pictureSets.value.reduce(
      (acc, set) => {
        if (set.reference_character) {
          acc[set.reference_character.id] = set;
        }
        return acc;
      },
      {},
    );
  } catch (e) {
    console.error("Error fetching picture sets:", e);
    pictureSets.value = [...pictureSets.value]; // force reactivity on error
  }
}

function handleCreateSet() {
  openSetEditor(null);
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
    console.log("No images found in drag data");
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

    console.log(
      `Added ${draggedIds.length} image(s) to set "${targetSet.name}"`,
    );
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    if (typeof detail === "string" && detail.includes("already in set")) {
      showNotice("Picture already in set", setId);
      return;
    }
    setError("Failed to add images to set: " + detail);
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
  // Accept faceIds or imageIds from drag event
  let faceIds = [];
  let imageIds = [];
  let dragType = null;
  try {
    const rawDataStr = event.dataTransfer.getData("application/json");
    console.log("[DROP] raw drag data string:", rawDataStr);
    const data = JSON.parse(rawDataStr);
    console.log("onCharacterDrop data:", data);
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
    console.error("Could not parse drag data:", e);
    return;
  }

  if (dragType === "face-bbox" && faceIds.length > 0) {
    // Assign faces to character
    try {
      const body = { face_ids: faceIds };
      console.log("Assigning faces to character:", characterId, body);
      const res = await apiClient.post(
        `${props.backendUrl}/characters/${characterId}/faces`,
        body,
      );
      await fetchSidebarData();
      await fetchCharacterThumbnail(characterId);
      emit("faces-assigned-to-character", { characterId, faceIds });
      console.log(
        `Assigned ${faceIds.length} face(s) to character ${characterId}`,
      );
    } catch (e) {
      alert("Failed to assign faces to character: " + (e.message || e));
    }
    return;
  }

  if (imageIds.length === 0) {
    console.log("No images found in drag data");
    return;
  }

  try {
    // Fallback: assign images to character
    const body = { picture_ids: imageIds };
    console.log("Assigning images to character:", characterId, body);
    const res = await apiClient.post(
      `${props.backendUrl}/characters/${characterId}/faces`,
      body,
    );
    await fetchSidebarData();
    await fetchCharacterThumbnail(characterId);
    //emit("faces-assigned-to-character", { characterId, imageIds });
    console.log(
      `Assigned ${imageIds.length} image(s) to character ${characterId}`,
    );
    emit("images-assigned-to-character", { characterId, imageIds });
  } catch (e) {
    alert("Failed to assign images to character: " + (e.message || e));
  }
}

// Batched face removal
async function removeFacesFromCharacter(characterId, faceIds) {
  try {
    const res = await apiClient.delete(
      `${props.backendUrl}/characters/${characterId}/faces`,
      {
        data: { face_ids: faceIds },
      },
    );
    await fetchSidebarData();
    await fetchCharacterThumbnail(characterId);
    emit("faces-removed-from-character", { characterId, faceIds });
    console.log(
      `Removed ${faceIds.length} face(s) from character ${characterId}`,
    );
  } catch (e) {
    alert("Failed to remove faces from character: " + (e.message || e));
  }
}

function handleDropOnCharacter(payload) {
  dragOverCharacter.value = null;
  if (!payload || !payload.characterId) return;
  onCharacterDrop(payload.characterId, payload.event);
}

// --- Character Management ---
function addNewCharacter() {
  // Open character editor with empty character to create new one
  let num = nextCharacterNumber.value;
  let name;
  const existingNames = new Set(characters.value.map((c) => c.name));
  do {
    name = `Character ${num}`;
    num++;
  } while (existingNames.has(name));
  nextCharacterNumber.value = num;

  // Open editor with default values
  openCharacterEditor({
    id: null,
    name: name,
    description: "",
    extra_metadata: "",
  });
}

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

async function pictureSetSaved(setData) {
  // If setData is a new set (no id in pictureSets), add it
  if (
    setData &&
    setData.id &&
    !pictureSets.value.some((s) => s.id === setData.id)
  ) {
    pictureSets.value.push(setData);
    pictureSets.value = [...pictureSets.value]; // force reactivity
    emit("select-set", setData.id);
  }
  await fetchPictureSets();
  pictureSets.value = [...pictureSets.value]; // force reactivity
  await fetchSidebarData();
  closeSetEditor();
}

onMounted(() => {
  fetchSortedCharactersAndSortOptions(); // Ensure proper order of fetching
  fetchPictureSets();
  console.log(
    "[SideBar.vue] Initial descendingModel value:",
    descendingModel.value,
  );
});

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
  () => settingsDialogOpen.value,
  (isOpen) => {
    if (isOpen) {
      resetSettingsForm();
      settingsTab.value = "preferences";
      fetchSettingsAuth();
      fetchUserTokens();
      fetchSmartScoreSettings();
    }
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

defineExpose({ refreshSidebar });
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
  <input
    ref="uploadInputRef"
    class="sidebar-upload-input"
    type="file"
    accept="image/*,video/*"
    multiple
    @change="handleUploadInputChange"
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
  <v-dialog
    v-model="settingsDialogOpen"
    width="620"
    @click:outside="settingsDialogOpen = false"
  >
    <div class="settings-dialog-shell">
      <v-btn
        icon
        size="36px"
        class="settings-dialog-close"
        @click="settingsDialogOpen = false"
      >
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="settings-dialog-card">
        <v-card-title class="settings-dialog-title">Settings</v-card-title>
        <v-tabs
          v-model="settingsTab"
          density="comfortable"
          class="settings-tabs"
        >
          <v-tab value="preferences">User Preferences</v-tab>
          <v-tab value="account">Account Settings</v-tab>
        </v-tabs>
        <v-card-text class="settings-dialog-body">
          <v-window v-model="settingsTab" class="settings-tab-body">
            <v-window-item value="preferences">
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">Smart Score</div>
                <div class="settings-section-desc">
                  Tags listed here reduce Smart Score when present on a picture.
                </div>
                <div class="settings-form">
                  <v-text-field
                    v-model="smartScoreTagInput"
                    label="Add penalized tag"
                    density="comfortable"
                    variant="filled"
                    :disabled="smartScoreTagsLoading"
                    @keydown.enter.prevent="addSmartScoreTag"
                  />
                  <v-btn
                    variant="outlined"
                    color="primary"
                    class="settings-action-btn"
                    :loading="smartScoreTagsLoading"
                    :disabled="smartScoreTagsLoading"
                    @click="addSmartScoreTag"
                  >
                    Add Tag
                  </v-btn>
                  <div v-if="smartScoreTagsError" class="settings-error">
                    {{ smartScoreTagsError }}
                  </div>
                  <div v-if="smartScoreTagsSuccess" class="settings-success">
                    {{ smartScoreTagsSuccess }}
                  </div>
                  <div class="settings-tag-list">
                    <div
                      v-for="tag in smartScorePenalizedTags"
                      :key="tag"
                      class="settings-tag-chip"
                    >
                      <span class="settings-tag-label">{{ tag }}</span>
                      <v-btn
                        icon
                        variant="text"
                        class="settings-tag-delete"
                        :disabled="smartScoreTagsLoading"
                        @click="removeSmartScoreTag(tag)"
                      >
                        <v-icon size="16">mdi-close</v-icon>
                      </v-btn>
                    </div>
                    <div
                      v-if="
                        !smartScoreTagsLoading &&
                        !smartScorePenalizedTags.length
                      "
                      class="settings-token-empty"
                    >
                      No penalized tags yet.
                    </div>
                  </div>
                </div>
              </div>
            </v-window-item>
            <v-window-item value="account">
              <div class="settings-section">
                <div class="settings-section-title">Account</div>
                <div class="settings-section-desc">
                  Change your password or manage sign-in options.
                </div>
                <div class="settings-account-meta">
                  <span class="settings-account-label">Username</span>
                  <span class="settings-account-value">
                    {{ settingsUsername || "Not set" }}
                  </span>
                </div>
                <div class="settings-form">
                  <input
                    v-if="settingsUsername"
                    type="text"
                    name="username"
                    :value="settingsUsername"
                    autocomplete="username"
                    style="
                      position: absolute;
                      opacity: 0;
                      height: 0;
                      width: 0;
                      pointer-events: none;
                    "
                    tabindex="-1"
                  />
                  <v-text-field
                    v-if="settingsHasPassword"
                    v-model="currentPassword"
                    label="Current password"
                    type="password"
                    density="comfortable"
                    variant="filled"
                    autocomplete="current-password"
                    name="current-password"
                  />
                  <v-text-field
                    v-model="newPassword"
                    label="New password"
                    :type="showNewPassword ? 'text' : 'password'"
                    density="comfortable"
                    variant="filled"
                    autocomplete="new-password"
                    name="new-password"
                    :append-inner-icon="
                      showNewPassword ? 'mdi-eye-off' : 'mdi-eye'
                    "
                    @click:append-inner="showNewPassword = !showNewPassword"
                  />
                  <div v-if="settingsError" class="settings-error">
                    {{ settingsError }}
                  </div>
                  <div v-if="settingsSuccess" class="settings-success">
                    {{ settingsSuccess }}
                  </div>
                  <v-btn
                    variant="outlined"
                    color="primary"
                    class="settings-action-btn"
                    :loading="settingsLoading"
                    :disabled="settingsLoading"
                    @click="submitPasswordChange"
                  >
                    Update Password
                  </v-btn>
                </div>
              </div>
              <v-divider class="settings-section-divider" />
              <div class="settings-section">
                <div class="settings-section-title">API Tokens</div>
                <div class="settings-section-desc">
                  Manage tokens for authenticated API access.
                </div>
                <div class="settings-tokens">
                  <v-text-field
                    v-model="tokenDescription"
                    label="Token description"
                    density="comfortable"
                    variant="filled"
                    :disabled="tokensLoading"
                  />
                  <v-btn
                    variant="outlined"
                    color="primary"
                    class="settings-action-btn"
                    :loading="tokensLoading"
                    :disabled="tokensLoading"
                    @click="createUserToken"
                  >
                    Create Token
                  </v-btn>
                  <div v-if="tokensError" class="settings-error">
                    {{ tokensError }}
                  </div>
                  <div v-if="tokensLoading" class="settings-token-loading">
                    Loading tokens...
                  </div>
                  <div v-else class="settings-token-list">
                    <div
                      v-for="token in tokens"
                      :key="token.id"
                      class="settings-token-row"
                    >
                      <div class="settings-token-meta">
                        <div class="settings-token-desc">
                          {{ token.description || "Untitled token" }}
                        </div>
                        <div class="settings-token-sub">
                          <span
                            >Created:
                            {{ formatTokenTimestamp(token.created_at) }}</span
                          >
                          <span
                            >Last used:
                            {{ formatTokenTimestamp(token.last_used_at) }}</span
                          >
                        </div>
                      </div>
                      <v-btn
                        icon
                        variant="text"
                        class="settings-token-delete"
                        :disabled="tokensLoading"
                        @click="confirmDeleteToken(token)"
                      >
                        <v-icon size="20">mdi-trash-can-outline</v-icon>
                      </v-btn>
                    </div>
                    <div v-if="!tokens.length" class="settings-token-empty">
                      No tokens yet.
                    </div>
                  </div>
                </div>
              </div>
            </v-window-item>
          </v-window>
        </v-card-text>
      </v-card>
    </div>
  </v-dialog>

  <v-dialog v-model="tokenDialogOpen" width="520">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title">New API Token</v-card-title>
      <v-card-text class="settings-dialog-body">
        <div class="settings-token-warning">
          Copy this token now. You won’t be able to see it again.
        </div>
        <div class="settings-token-value">{{ newlyCreatedToken }}</div>
      </v-card-text>
      <v-card-actions class="settings-dialog-actions">
        <v-spacer />
        <v-btn
          variant="outlined"
          color="primary"
          @click="tokenDialogOpen = false"
        >
          Close
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <v-dialog v-model="tokenDeleteDialogOpen" width="420">
    <v-card class="settings-token-dialog">
      <v-card-title class="settings-dialog-title">Delete token?</v-card-title>
      <v-card-text class="settings-dialog-body">
        This will permanently revoke the selected token.
      </v-card-text>
      <v-card-actions class="settings-dialog-actions">
        <v-spacer />
        <v-btn variant="text" @click="tokenDeleteDialogOpen = false">
          Cancel
        </v-btn>
        <v-btn
          color="error"
          variant="outlined"
          :loading="tokensLoading"
          @click="deleteUserToken"
        >
          Delete
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>

  <aside class="sidebar" :class="{ 'sidebar-collapsed': props.collapsed }">
    <div class="sidebar-brand">
      <div class="sidebar-brand-left">
        <img
          v-if="!props.collapsed"
          src="/Logo.png"
          alt="PixlVault logo"
          class="sidebar-brand-logo"
        />
        <span v-if="!props.collapsed" class="sidebar-brand-title"
          >PixlVault</span
        >
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
          @click="selectCharacter(props.allPicturesId)"
        >
          <v-icon>mdi-image-multiple</v-icon>
        </div>
        <div
          :class="[
            'sidebar-collapsed-item',
            { active: props.selectedCharacter === props.unassignedPicturesId },
          ]"
          title="Unassigned Pictures"
          @click="selectCharacter(props.unassignedPicturesId)"
        >
          <v-icon>mdi-help-circle-outline</v-icon>
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
          :title="char.name || 'Character'"
          @click="selectCharacter(char.id)"
          @dragover.prevent="handleDragOverCharacter(char.id)"
          @dragleave="handleDragLeaveCharacter"
          @drop.prevent="
            handleDropOnCharacter({ characterId: char.id, event: $event })
          "
        >
          <img :src="characterThumbnails[char.id] || unknownPerson" alt="" />
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
          @click="selectSet(pset.id)"
          @dragover.prevent="dragOverSetItem(pset.id)"
          @dragleave="dragLeaveSetItem"
          @drop.prevent="handleDropOnSet(pset.id, $event)"
        >
          <v-icon>mdi-layers</v-icon>
        </div>
        <div class="sidebar-collapsed-divider"></div>
        <div
          class="sidebar-collapsed-item sidebar-settings-item"
          title="Settings"
          @click="settingsDialogOpen = true"
        >
          <v-icon>mdi-cog</v-icon>
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
            @click.stop="openUploadDialog"
            title="Upload pictures"
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
        @click="selectCharacter(props.allPicturesId)"
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
        @click="selectCharacter(props.unassignedPicturesId)"
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
              props.selectedCharacter !== props.unassignedPicturesId
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
      <div v-if="sidebarError" class="sidebar-error">
        {{ sidebarError.value }}
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
              droppable: dragOverCharacter === char.id,
            },
          ]"
          @click="selectCharacter(char.id)"
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
              class="sidebar-character-thumb"
            />
          </span>
          <span class="sidebar-list-label">
            <v-tooltip location="top">
              <template #activator="{ props }">
                <span v-bind="props" class="sidebar-list-label-text">
                  {{ char.name.charAt(0).toUpperCase() + char.name.slice(1) }}
                </span>
              </template>
              <span>{{ char.name }}</span>
            </v-tooltip>
          </span>
          <span class="sidebar-character-actions">
            <v-icon
              class="sidebar-character-toggle"
              size="18"
              :title="
                collapsedCharacters[char.id]
                  ? 'Show reference pictures'
                  : 'Hide reference pictures'
              "
              @click.stop="toggleCharacterCollapse(char.id)"
            >
              {{
                collapsedCharacters[char.id]
                  ? "mdi-chevron-right"
                  : "mdi-chevron-down"
              }}
            </v-icon>
            <span class="sidebar-list-count">
              <span v-if="isCountNew(char.id)" class="sidebar-new-tag">
                new
              </span>
              {{ categoryCounts[char.id] ?? "" }}
            </span>
          </span>
        </div>
        <transition name="fade">
          <div
            v-show="!collapsedCharacters[char.id]"
            class="sidebar-character-details"
          >
            <div class="sidebar-reference-pictures">
              <template v-if="referencePictureSetsByCharacter[char.id]">
                <div
                  :class="[
                    'sidebar-list-item',
                    'sidebar-reference-set',
                    {
                      active:
                        selectedSet ===
                        referencePictureSetsByCharacter[char.id].id,
                      droppable:
                        dragOverSet ===
                        referencePictureSetsByCharacter[char.id].id,
                    },
                  ]"
                  @click="
                    selectSet(referencePictureSetsByCharacter[char.id].id)
                  "
                  @dragover.prevent="
                    dragOverSetItem(referencePictureSetsByCharacter[char.id].id)
                  "
                  @dragleave="dragLeaveSetItem"
                  @drop.prevent="
                    handleDropOnSet(
                      referencePictureSetsByCharacter[char.id].id,
                      $event,
                    )
                  "
                >
                  <v-icon size="22" class="sidebar-reference-icon"
                    >mdi-layers</v-icon
                  >
                  <span class="sidebar-list-label">Reference Pictures</span>
                  <span class="sidebar-list-count">
                    {{
                      referencePictureSetsByCharacter[char.id]?.picture_count ??
                      ""
                    }}
                  </span>
                  <span
                    v-if="
                      sidebarNotice &&
                      sidebarNoticeSetId ===
                        referencePictureSetsByCharacter[char.id].id
                    "
                    class="sidebar-inline-notice"
                  >
                    {{ sidebarNotice }}
                  </span>
                </div>
              </template>
              <template v-else>
                <span
                  style="
                    color: rgb(var(--v-theme-on-accent));
                    font-size: 0.9em;
                    padding-left: 32px;
                  "
                  >No reference set found for this character</span
                >
              </template>
            </div>
          </div>
        </transition>
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
          @click="selectSet(pset.id)"
          @dragover.prevent="dragOverSetItem(pset.id)"
          @dragleave="dragLeaveSetItem"
          @drop.prevent="handleDropOnSet(pset.id, $event)"
        >
          <span class="sidebar-list-icon">
            <v-icon size="44">mdi-layers</v-icon>
          </span>
          <span class="sidebar-list-label">
            <v-tooltip location="top">
              <template #activator="{ props }">
                <span v-bind="props" class="sidebar-list-label-text">
                  {{ pset.name }}
                </span>
              </template>
              <span>{{ pset.name }}</span>
            </v-tooltip>
          </span>
          <span class="sidebar-list-count">
            {{ pset.picture_count ?? 0 }}
          </span>
          <span
            v-if="sidebarNotice && sidebarNoticeSetId === pset.id"
            class="sidebar-inline-notice"
          >
            {{ sidebarNotice }}
          </span>
        </div>
      </template>

      <div class="sidebar-section-header">
        Sort by
        <span style="flex: 1 1 auto"></span>
      </div>

      <div
        class="sidebar-searchbar-wrapper"
        style="
          display: flex;
          flex-direction: column;
          gap: 2px;
          align-items: stretch;
        "
      >
        <div style="display: flex; align-items: center; gap: 8px">
          <div style="flex: 1; min-width: 0; position: relative">
            <select
              ref="sortSelectRef"
              v-model="sortModel"
              class="sidebar-native-select"
              @change="handleSortChange"
            >
              <option
                v-for="opt in sortOptions"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </option>
            </select>
            <span class="sidebar-native-select-chevron">
              <v-icon size="18">mdi-menu-down</v-icon>
            </span>
          </div>
          <v-btn
            icon
            class="sidebar-sort-direction-btn"
            variant="plain"
            size="small"
            :color="null"
            :title="descendingModel ? 'Descending' : 'Ascending'"
            @click="descendingModel = !descendingModel"
            style="margin-left: 2px; margin-right: 2px"
          >
            <v-icon size="18">
              {{
                descendingModel ? "mdi-sort-ascending" : "mdi-sort-descending"
              }}
            </v-icon>
          </v-btn>
        </div>
        <div v-if="sortModel === SIMILARITY_SORT_KEY">
          <div class="sidebar-section-header">
            Similarity Character
            <span style="flex: 1 1 auto"></span>
          </div>
          <div style="display: flex; align-items: center; gap: 8px">
            <div style="flex: 1 1 0; min-width: 0; position: relative">
              <select
                v-model="similarityCharacterModel"
                class="sidebar-native-select"
              >
                <option
                  v-for="opt in similarityCharacterOptions"
                  :key="opt.value"
                  :value="opt.value"
                >
                  {{ opt.text }}
                </option>
              </select>
              <span class="sidebar-native-select-chevron">
                <v-icon size="18">mdi-menu-down</v-icon>
              </span>
            </div>
            <div style="width: 34px"></div>
          </div>
        </div>
        <div v-if="sortModel === STACKS_SORT_KEY">
          <div class="sidebar-section-header">
            Stack Strictness
            <span style="flex: 1 1 auto"></span>
          </div>
          <div style="display: flex; align-items: center; gap: 8px">
            <div style="flex: 1 1 0; min-width: 0; position: relative">
              <select
                v-model="stackThresholdModel"
                class="sidebar-native-select"
              >
                <option
                  v-for="opt in stackThresholdOptions"
                  :key="opt.value"
                  :value="opt.value"
                >
                  {{ opt.label }}
                </option>
              </select>
              <span class="sidebar-native-select-chevron">
                <v-icon size="18">mdi-menu-down</v-icon>
              </span>
            </div>
            <div style="width: 34px"></div>
          </div>
        </div>
      </div>
      <div class="sidebar-footer-spacer"></div>
      <div
        class="sidebar-list-item sidebar-settings-item"
        @click="settingsDialogOpen = true"
      >
        <span class="sidebar-list-icon">
          <v-icon size="44">mdi-cog</v-icon>
        </span>
        <span class="sidebar-list-label">Settings</span>
      </div>
    </template>
  </aside>
</template>

<style scoped>
.sidebar-native-select {
  background: rgba(var(--v-theme-surface), 0.3);
  color: rgb(var(--v-theme-on-surface));
  border-radius: 4px;
  min-height: 32px;
  height: 32px;
  font-size: 1em;
  box-shadow: 2px 2px 6px rgba(0, 0, 0, 0.2);
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
/* Sidebar right edge for counts */
.sidebar {
  width: 280px;
  --sidebar-right-edge: 14px;
  --sidebar-header-action-right-edge: 0px;
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
  overflow-y: auto;
  scrollbar-color: rgb(var(--v-theme-accent)) rgba(0, 0, 0, 0.15);
  box-sizing: border-box;
}

.sidebar.sidebar-collapsed {
  width: 56px;
  overflow-y: hidden;
}

.sidebar.sidebar-collapsed .sidebar-brand {
  justify-content: center;
}

.sidebar.sidebar-collapsed .sidebar-brand:hover .v-btn {
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
  width: 36px;
  height: 36px;
  object-fit: contain;
}

.sidebar-brand:hover .v-btn {
  background-color: rgb(var(--v-theme-accent));
}

.sidebar-brand-title {
  font-family: "PressStart2P", monospace;
  font-size: 0.95em;
  color: rgb(var(--v-theme-on-primary));
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

.sidebar-collapsed-list {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 6px 0 12px;
  overflow-y: auto;
}

.sidebar-collapsed-spacer {
  flex: 1 1 auto;
  width: 100%;
}

.sidebar-collapsed-item {
  width: 36px;
  height: 36px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  cursor: pointer;
  color: rgb(var(--v-theme-on-surface));
}

.sidebar-collapsed-item.active {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}

.sidebar-collapsed-item.droppable {
  background: rgb(var(--v-theme-primary));
}

.sidebar-collapsed-item:hover {
  filter: brightness(1.1);
  background-color: rgba(var(--v-theme-accent), 0.4);
}

.sidebar-collapsed-thumb {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  border: none;
  padding: 0;
  background: transparent;
  cursor: pointer;
}

.sidebar-collapsed-thumb img {
  width: 36px;
  height: 36px;
  object-fit: cover;
  border-radius: 8px;
  display: block;
}

.sidebar-collapsed-thumb.active img {
  outline: 4px solid rgb(var(--v-theme-accent));
}

.sidebar-collapsed-thumb:hover {
  filter: brightness(1.4);
  outline: 4px solid rgba(var(--v-theme-accent), 0.4);
}

.sidebar-collapsed-thumb.droppable img {
  outline: 4px solid rgb(var(--v-theme-primary));
}

.sidebar-collapsed-divider {
  width: 100%;
  height: 1px;
  margin-top: 2px;
  margin-bottom: 2px;
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
  background: rgba(0, 0, 0, 0.15);
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
  color: rgb(var(--v-theme-on-surface));
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
  min-height: 48px;
  padding: 2px 8px;
  padding-right: var(--sidebar-right-edge) !important;
  cursor: pointer;
  border-radius: 0;
  margin-bottom: 0;
  font-size: 0.9em;
  font-weight: 500;
  background: transparent;
  color: #fff;
  transition:
    background 0.18s,
    color 0.18s;
  width: 100%;
}

.sidebar-footer-spacer {
  flex: 1 1 auto;
}

.sidebar-settings-item {
  margin-top: 6px;
}

.settings-dialog-card {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: 12px;
}

.settings-dialog-shell {
  position: relative;
  width: 100%;
}

.settings-dialog-close {
  position: absolute;
  top: -16px;
  right: -16px;
  background-color: rgb(var(--v-theme-primary));
  border: none;
  color: rgb(var(--v-theme-on-primary));
  cursor: pointer;
  z-index: 2;
}

.settings-dialog-close:hover {
  background-color: rgb(var(--v-theme-accent));
}

.settings-dialog-title {
  font-weight: 700;
  font-size: 1.2rem;
}

.settings-tabs {
  margin-top: 4px;
}

.settings-tab-body {
  padding-top: 6px;
}

.settings-dialog-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  line-height: 1;
}

.settings-section {
  display: flex;
  line-height: 1;
  flex-direction: column;
  gap: 6px;
}
.settings-section-title {
  font-weight: 600;
}

.settings-section-desc {
  font-size: 0.92em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-account-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0 2px;
}

.settings-account-label {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.settings-account-value {
  font-weight: 600;
}

.settings-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.settings-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.9em;
}

.settings-success {
  color: rgb(var(--v-theme-accent));
  font-size: 0.9em;
}

.settings-action-btn {
  align-self: flex-start;
  background-color: rgb(var(--v-theme-primary)) !important;
  color: rgb(var(--v-theme-on-primary)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}
.settings-action-btn:hover {
  background-color: rgb(var(--v-theme-accent)) !important;
  border: 1px rgb(var(--v-theme-on-primary)) !important;
}

.settings-tokens {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.settings-token-loading {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-token-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 220px;
  overflow-y: auto;
  padding-right: 4px;
}

.settings-token-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  background: rgba(var(--v-theme-surface), 0.2);
}

.settings-token-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.settings-token-desc {
  font-weight: 600;
}

.settings-token-sub {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 0.8em;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.settings-token-delete {
  color: rgba(var(--v-theme-error), 0.9);
}

.settings-token-empty {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.settings-tag-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.settings-tag-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 4px 4px 6px;
  border-radius: 6px;
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.settings-tag-label {
  font-size: 1em;
}

.settings-tag-delete {
  color: rgba(var(--v-theme-on-primary), 0.9);
  min-width: 0;
  height: 16px;
  width: 16px;
  padding: 0;
}

.settings-tag-delete:hover {
  color: rgba(var(--v-theme-error), 0.9);
  min-width: 0;
  height: 16px;
  width: 16px;
  padding: 0;
}

.settings-token-dialog {
  padding-bottom: 8px;
}

.settings-token-warning {
  font-size: 0.9em;
  color: rgba(var(--v-theme-on-surface), 0.7);
  margin-bottom: 8px;
}

.settings-token-value {
  word-break: break-all;
  font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
  background: rgba(var(--v-theme-surface), 0.2);
  border-radius: 8px;
  padding: 10px 12px;
}

.settings-section-divider {
  margin: 4px 0 8px;
}

.settings-privacy-note {
  font-size: 0.85em;
  color: rgba(var(--v-theme-on-surface), 0.6);
  margin-top: 4px;
}

.settings-dialog-actions {
  padding-top: 0;
}

.sidebar-list-item.active {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  border-right: 0;
  position: relative;
}

.sidebar-list-item:hover {
  filter: brightness(1.1);
  background: rgba(var(--v-theme-accent), 0.2);
}

.sidebar-list-item.droppable {
  background: rgb(var(--v-theme-primary));
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
}

.sidebar-list-icon {
  display: flex;
  align-items: center;
  margin-right: 12px;
  justify-content: center;
  width: 36px;
  height: 36px;
}

.sidebar-list-label {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: left;
  padding-left: 6px;
}

.sidebar-character-thumb {
  width: 36px;
  height: 36px;
  object-fit: contain;
  border-radius: 6px;
  background: transparent;
  display: inline-block;
}

.sidebar-character-group {
  display: flex;
  flex-direction: column;
  width: 100%;
}

.sidebar-error {
  color: #ffcccc;
  background: rgba(0, 0, 0, 0.25);
  padding: 6px 12px;
  border-radius: 6px;
  margin: 8px 12px;
  font-size: 0.95em;
}

.sidebar-list-count {
  font-size: 0.9em;
  color: rgb(var(--v-theme-on-surface));
  min-width: 2.5em;
  text-align: right;
  margin: 0;
  font-weight: 400;
  opacity: 0.85;
  letter-spacing: 0.01em;
  align-self: center;
  display: inline-block;
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
  color: #ffffff;
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
  color: rgb(var(--v-theme-on-surface));
  opacity: 0.8;
  margin-right: 4px;
}

.sidebar-character-toggle:hover {
  opacity: 1;
  color: rgb(var(--v-theme-on-primary));
}

.add-character-inline {
  color: rgb(var(--v-theme-on-surface)) !important;
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
  color: rgb(var(--v-theme-on-surface)) !important;
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
  color: rgb(var(--v-theme-on-surface)) !important;
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

.sidebar-upload-input {
  display: none;
}

.delete-character-inline {
  color: #fff !important;
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
  background: #ff5252;
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

/* Reference set child entry styling */
.sidebar-reference-set {
  font-size: 0.88em;
  padding-left: 40px;
  position: relative;
  overflow: visible;
}

.sidebar-set-item {
  position: relative;
  overflow: visible;
}

.sidebar-reference-set.active {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  position: relative;
  padding-left: 40px;
}

.sidebar-reference-set.active::after {
  content: "";
  position: absolute;
  top: 0;
  right: 0;
  width: 20px;
  height: 100%;
  background: linear-gradient(
    to right,
    rgba(255, 165, 0, 0) 30%,
    rgba(255, 165, 0, 1) 90%
  );
  pointer-events: none;
  z-index: 2;
}

.sidebar-reference-set .sidebar-list-label {
  font-size: 0.92em;
  font-weight: 400;
}

.sidebar-reference-icon {
  margin-right: 4px;
}

.sidebar-inline-notice {
  position: absolute;
  top: 50%;
  right: -12px;
  transform: translate(100%, -50%);
  background: rgba(var(--v-theme-secondary), 0.75);
  color: rgb(var(--v-theme-on-secondary));
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 0.9em;
  white-space: nowrap;
  pointer-events: none;
  z-index: 100 !important;
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
    width: 44px;
    height: 44px;
  }

  .sidebar-character-thumb {
    max-width: 44px;
    max-height: 44px;
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
