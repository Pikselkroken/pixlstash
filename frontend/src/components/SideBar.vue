<script setup>
import { computed, ref, onMounted, watch } from "vue";
import ImageImporter from "./ImageImporter.vue";
import CharacterEditor from "./CharacterEditor.vue";
import PictureSetEditor from "./PictureSetEditor.vue";
import SearchBar from "./SearchBar.vue";
import unknownPerson from "../assets/unknown-person.png"; // Fallback avatar for characters without thumbnails
import { apiClient } from '../utils/apiClient';

const props = defineProps({
  selectedCharacter: { type: [String, Number, null], default: null },
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  selectedSet: { type: [Number, null], default: null },
  searchQuery: { type: String, default: "" },
  selectedSort: { type: String, default: "" },
  selectedDescending: { type: Boolean, default: false },
  backendUrl: { type: String, required: true },
});

const emit = defineEmits([
  "select-character",
  "update:selected-sort",
  "update:search-query",
  "select-set",
  "switch-to-likeness",
  "import-finished",
  "set-error",
  "set-loading",
  "images-assigned-to-character",
  "images-moved",
  "search-images",
  "update:similarity-character",
]);

const dragOverSet = ref(null);

// --- Sorting State ---
const sortOptions = ref([]);

// --- Character & Sidebar State ---
const characters = ref([]);
const categoryCounts = ref({
  [props.allPicturesId]: 0,
  [props.unassignedPicturesId]: 0,
});

const characterThumbnails = ref({});
const expandedCharacters = ref({});

// Ensure collapsedCharacters is reactive and initialized for all characters
const collapsedCharacters = ref({});

const sections = ref({
  pictures: true,
  people: true,
  sets: true,
  analysis: true,
  sort: true,
});
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

function createSet() {
  setEditorSet.value = null;
  setEditorOpen.value = true;
}

const sidebarError = ref(null);

const sortedCharacters = computed(() => {
  return [...characters.value]
    .filter((c) => c && typeof c.name === "string" && c.name.trim() !== "")
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" })
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

// --- Similarity Character Dropdown State ---
const SIMILARITY_SORT_KEY = "CHARACTER_LIKENESS"; // Adjust if backend uses a different key
const similarityCharacter = ref(null);

const similarityCharacterOptions = computed(() => {
  let options = sortedCharacters.value.map((c) => ({
    text: c.name,
    value: c.id,
  }));
  return options;
});

watch(similarityCharacter, (val) => {
  emit("update:similarity-character", val);
});

const reactiveSelectedDescending = ref(props.selectedDescending);

watch(
  () => props.selectedDescending,
  (newValue, oldValue) => {
    console.log(
      "[SideBar.vue] Prop selectedDescending changed from",
      oldValue,
      "to",
      newValue
    );
    reactiveSelectedDescending.value = newValue;
  }
);

const descendingModel = computed({
  get: () => {
    console.log(
      "[SideBar.vue] descendingModel.get() called. Current value:",
      reactiveSelectedDescending.value
    );
    return reactiveSelectedDescending.value;
  },
  set: (value) => {
    console.log(
      "[SideBar.vue] descendingModel.set() called. New value:",
      value
    );
    reactiveSelectedDescending.value = value;
    emit("update:selected-sort", { sort: sortModel.value, descending: value });
    console.log(
      "[SideBar.vue] descendingModel.set() completed. Updated reactiveSelectedDescending:",
      reactiveSelectedDescending.value
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

function toggleSection(section) {
  if (!section || !(section in sections.value)) return;
  sections.value[section] = !sections.value[section];
}

function selectCharacter(id) {
  emit("select-character", id);
}

function searchImages(query) {
  emit("search-images", query);
}

function selectSet(setId) {
  emit("select-set", setId);
}

async function deleteCharacter() {
  if (!props.selectedCharacter) return;
  if (!window.confirm("Delete this character?")) return;
  try {
    await apiClient.delete(`/characters/${props.selectedCharacter}`);

    // Remove the deleted character from the characters array
    characters.value = characters.value.filter(
      (char) => char.id !== props.selectedCharacter
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
    original_prompt: "",
    original_seed: null,
    loras: [],
  });
}

function handleImportFinished() {
  emit("import-finished");
}

function setLoading(isLoading) {
  emit("set-loading", isLoading);
}

function setError(message) {
  sidebarError.value = message;
  emit("set-error", message);
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
  { immediate: true }
);

function toggleCharacterCollapse(charId) {
  collapsedCharacters.value[charId] = !collapsedCharacters.value[charId];
}

// --- Sidebar & Character Data ---
async function fetchSidebarData() {
  // Fetch total image count for END key logic
  try {
    // All images summary
    const resAll = await apiClient.get(
      `${props.backendUrl}/characters/${props.allPicturesId}/summary`
    );
    const data = await resAll.data;
    categoryCounts.value[props.allPicturesId] = data.image_count;
  } catch (e) {
    console.warn("Error fetching all images summary:", e);
  }
  try {
    // Unassigned images summary
    const resUnassigned = await apiClient.get(
      `${props.backendUrl}/characters/${props.unassignedPicturesId}/summary`
    );
    const data = await resUnassigned.data;
    categoryCounts.value[props.unassignedPicturesId] = data.image_count;
  } catch (e) {
    console.warn("Error fetching unassigned images summary:", e);
  }
  await Promise.all(
    characters.value.map(async (char) => {
      try {
        const res = await apiClient.get(
          `${props.backendUrl}/characters/${char.id}/summary`
        );
        const data = await res.data;
        categoryCounts.value[char.id] = data.image_count;
        
      } catch {}
    })
  );
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

function refreshSidebar() {
  console.log("Refreshing sidebar");
  fetchCharacters();
  fetchPictureSets();
  fetchSidebarData();
}

async function fetchCharacterThumbnail(characterId) {
  try {
    const cacheBuster = Date.now();
    const thumbUrl = `/characters/${characterId}/thumbnail?cb=${cacheBuster}`;
    const res = await apiClient.get(thumbUrl, { responseType: 'blob' });

    // Create an object URL for the blob
    const blobUrl = URL.createObjectURL(res.data);
    characterThumbnails.value[characterId] = blobUrl;
  } catch (e) {
    console.error(`Failed to fetch thumbnail for character ${characterId}:`, e);
    characterThumbnails.value[characterId] = null;
  }
}

function toggleSidebarSection(section) {
  if (!section || !(section in sections.value)) return;
  sections.value[section] = !sections.value[section];
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
      {}
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
      `Delete picture set "${setToDelete.name}"? This will unassign all their images.`
    )
  )
    return;

  try {
    const res = await apiClient.delete(
      `${props.backendUrl}/picture_sets/${props.selectedSet}`
    );
    emit("select-set", null);
    await fetchPictureSets();
    await fetchSidebarData();
  } catch (e) {
    alert("Failed to delete set: " + (e.message || e));
  }
}

async function handleDropOnSet(setId, event) {
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
        `${props.backendUrl}/picture_sets/${setId}/members/${picId}`
      );
    });

    await Promise.all(addPromises);

    // Refresh the picture sets to update counts
    await fetchPictureSets();

    // Emit event to parent to remove images from grid
    emit("images-moved", { imageIds: draggedIds });

    console.log(
      `Added ${draggedIds.length} image(s) to set "${targetSet.name}"`
    );
  } catch (e) {
    alert("Failed to add images to set: " + (e.message || e));
  }
}

function handleDragOverCharacter(id) {
  dragOverCharacter.value = id;
}

function handleDragLeaveCharacter() {
  dragOverCharacter.value = null;
}

async function onCharacterDrop(characterId, event) {
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
        body
      );
      await fetchSidebarData();
      await fetchCharacterThumbnail(characterId);
      //emit("faces-assigned-to-character", { characterId, faceIds});
      console.log(
        `Assigned ${faceIds.length} face(s) to character ${characterId}`
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
      body
    );
    await fetchSidebarData();
    await fetchCharacterThumbnail(characterId);
    //emit("faces-assigned-to-character", { characterId, imageIds });
    console.log(
      `Assigned ${imageIds.length} image(s) to character ${characterId}`
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
      }
    );
    await fetchSidebarData();
    await fetchCharacterThumbnail(characterId);
    emit("faces-removed-from-character", { characterId, faceIds });
    console.log(
      `Removed ${faceIds.length} face(s) from character ${characterId}`
    );
  } catch (e) {
    alert("Failed to remove faces from character: " + (e.message || e));
  }
}

function handleDropOnCharacter(payload) {
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
    original_prompt: "",
    original_seed: null,
    loras: [],
  });
}

async function characterSaved() {
  if (characterEditorCharacter.value && !characterEditorCharacter.value.id) {
    characters.value.push(characterEditorCharacter.value);
    // New character was created, increment nextCharacterNumber
    nextCharacterNumber.value++;
  }
  await fetchCharacters(); // Refresh characters
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
    descendingModel.value
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
          (char) => char.id === similarityCharacter.value
        )
      ) {
        similarityCharacter.value =
          sortedCharacters.value.length > 0
            ? sortedCharacters.value[0].id
            : null; // Default to the first character or null
      }
    }
  }
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

  <aside class="sidebar">
    <div class="sidebar-section-header" @click="toggleSection('pictures')">
      <v-icon small style="margin-right: 8px">
        {{ sections.pictures ? "mdi-chevron-down" : "mdi-chevron-right" }}
      </v-icon>
      Pictures
    </div>
    <transition name="fade">
      <div v-show="sections.pictures">
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
            {{ categoryCounts[props.unassignedPicturesId] ?? "" }}
          </span>
        </div>
      </div>
    </transition>

    <div class="sidebar-section-header" @click="toggleSection('people')">
      <v-icon small style="margin-right: 8px">
        {{ sections.people ? "mdi-chevron-down" : "mdi-chevron-right" }}
      </v-icon>
      People
      <span class="sidebar-header-spacer"></span>
      <div class="sidebar-header-actions">
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
    <transition name="fade">
      <div v-show="sections.people">
        <div v-if="sidebarError" class="sidebar-error">
          {{ sidebarError.value }}
        </div>
        <div
          v-if="sortedCharacters.length === 0"
          class="sidebar-character-group"
        >
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
            <span style="display: flex; align-items: center">
              <v-icon
                small
                style="margin-right: 8px; cursor: pointer"
                @click.stop="toggleCharacterCollapse(char.id)"
              >
                {{
                  collapsedCharacters[char.id]
                    ? "mdi-chevron-right"
                    : "mdi-chevron-down"
                }}
              </v-icon>
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
            <button
              class="character-edit-btn"
              @click.stop="openCharacterEditor(char)"
              title="Edit character details"
            >
              <v-icon size="small">mdi-pencil</v-icon>
            </button>
            <span class="sidebar-list-count">
              {{ categoryCounts[char.id] ?? "" }}
            </span>
            <!-- Collapse icon moved to the left of thumbnail -->
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
                      dragOverSetItem(
                        referencePictureSetsByCharacter[char.id].id
                      )
                    "
                    @dragleave="dragLeaveSetItem"
                    @drop.prevent="
                      handleDropOnSet(
                        referencePictureSetsByCharacter[char.id].id,
                        $event
                      )
                    "
                  >
                    <v-icon size="22" class="sidebar-reference-icon"
                      >mdi-layers</v-icon
                    >
                    <span class="sidebar-list-label">Reference Pictures</span>
                  </div>
                </template>
                <template v-else>
                  <span
                    style="color: #888; font-size: 0.9em; padding-left: 32px"
                    >No reference set found for this character</span
                  >
                </template>
              </div>
            </div>
          </transition>
        </div>
      </div>
    </transition>

    <div class="sidebar-section-header" @click="toggleSection('sets')">
      <v-icon small style="margin-right: 8px">
        {{ sections.sets ? "mdi-chevron-down" : "mdi-chevron-right" }}
      </v-icon>
      Picture Sets
      <span class="sidebar-header-spacer"></span>
      <div class="sidebar-header-actions">
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
    <transition name="fade">
      <div v-show="sections.sets">
        <div v-if="pictureSets.length === 0" class="sidebar-list-item">
          No picture sets. Click the + button to create one.
        </div>
        <template
          v-for="(pset, idx) in pictureSets.filter(
            (pset) => pset.reference_character == null
          )"
          :key="pset.id"
        >
          <div
            :class="[
              'sidebar-list-item',
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
            <button
              class="character-edit-btn"
              @click.stop="openSetEditor(pset)"
              title="Edit picture set details"
            >
              <v-icon size="small">mdi-pencil</v-icon>
            </button>
            <span class="sidebar-list-count">
              {{ pset.picture_count ?? 0 }}
            </span>
          </div>
        </template>
      </div>
    </transition>

    <div class="sidebar-section-header" @click="toggleSection('sort')">
      <v-icon small style="margin-right: 8px">
        {{ sections.sort ? "mdi-chevron-down" : "mdi-chevron-right" }}
      </v-icon>
      Sorting
      <span style="flex: 1 1 auto"></span>
    </div>

    <transition name="fade">
      <div class="search-and-sort" v-show="sections.sort">
        <div class="sidebar-searchbar-wrapper">
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
            <v-select
              v-model="sortModel"
              :items="sortOptions"
              class="sidebar-sort-select"
              item-title="label"
              item-value="value"
              label="Sort by"
              dense
              hide-details
              style="flex: 1; min-width: 0"
            />
            <v-btn
              icon
              :title="descendingModel ? 'Make ascending' : 'Make descending'"
              @click="descendingModel = !descendingModel"
              style="margin-left: auto"
            >
              <v-icon>
                {{
                  descendingModel ? "mdi-sort-descending" : "mdi-sort-ascending"
                }}
              </v-icon>
            </v-btn>
          </div>
          <v-select
            v-if="sortModel === SIMILARITY_SORT_KEY"
            v-model="similarityCharacter"
            :items="similarityCharacterOptions"
            class="sidebar-sort-select"
            label="Similarity to"
            dense
            hide-details
            style="min-width: 0; margin-top: -4px"
            item-title="text"
            item-value="value"
          />
        </div>
      </div>
    </transition>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 280px;
  background: rgb(var(--v-theme-secondary));
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  min-height: 100vh;
  box-sizing: border-box;
}

.sidebar-section-header {
  position: relative;
  font-size: 1.0rem;
  font-weight: 800;
  padding: 2px;
  margin: 2px 0 2px 0;
  border-radius: 0;
  box-shadow: 0 1px 1px rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  cursor: pointer;
  user-select: none;
  background: #7f95aa;
  color: #fff;
  transition: background 0.2s, color 0.2s;
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
  padding: 2px 6px;
  cursor: pointer;
  border-radius: 0;
  margin-bottom: 0;
  font-size: 0.9em;
  font-weight: 500;
  background: transparent;
  color: #fff;
  transition: background 0.18s, color 0.18s;
  width: 100%;
}

.sidebar-list-item.active {
  background: #f0f0f055;
  color: #fff;
  border-right: 0;
  position: relative;
}

.sidebar-list-item.active::after {
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

.sidebar-list-item:hover {
  background: #6c7a8a;
  color: #fff;
}

.sidebar-list-item.droppable {
  background: #6c7a8a;
  box-shadow: inset 0 0 0 2px rgba(255, 255, 255, 0.35);
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
}

.sidebar-header-actions .v-icon {
  min-width: 32px;
  min-height: 32px;
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
}

.sidebar-character-thumb {
  max-width: 36px;
  max-height: 36px;
  object-fit: contain;
  border-radius: 6px;
  box-shadow: 0 0 0 #bbb;
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
  font-size: 0.8em;
  color: #b0b8c9;
  min-width: 2.5em;
  text-align: right;
  margin: 0 8px;
  font-weight: 400;
  opacity: 0.85;
  letter-spacing: 0.01em;
  align-self: center;
  display: inline-block;
}

.add-character-inline {
  color: #fff;
  font-size: 1.4rem;
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

.add-character-inline:hover {
  background: #3a5778;
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
  transition: background 0.2s, color 0.2s;
}

.delete-character-inline:hover {
  background: #ff5252;
}

.search-and-sort {
  display: flex;
  flex-direction: column;
}

.sidebar-sort-select {
  background: rgba(200, 200, 200, 0.6);
}

.character-edit-btn {
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.4);
  cursor: pointer;
  padding: 4px;
  margin-left: auto;
  margin-right: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: color 0.2s, background-color 0.2s;
}

.character-edit-btn:hover {
  color: rgba(255, 255, 255, 1);
  background-color: rgba(255, 255, 255, 0.1);
}
/* Reference set child entry styling */
.sidebar-reference-set {
  font-size: 0.88em;
  padding-left: 40px;
}

.sidebar-reference-set.active {
  background: #f0f0f055;
  color: #fff;
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
</style>
