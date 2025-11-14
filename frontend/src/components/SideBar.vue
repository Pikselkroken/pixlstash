<script setup>
import { computed, ref, onMounted, watch } from "vue";
import ImageImporter from "./ImageImporter.vue";
import CharacterEditor from "./CharacterEditor.vue";
import PictureSetEditor from "./PictureSetEditor.vue";
import SearchBar from "./SearchBar.vue";
import unknownPerson from "../assets/unknown-person.png"; // Fallback avatar for characters without thumbnails

const props = defineProps({
  selectedCharacter: { type: [String, Number, null], default: null },
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  selectedSet: { type: [Number, null], default: null },
  searchQuery: { type: String, default: "" },
  selectedSort: { type: String, default: "" },
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
  search: true,
});
const dragOverCharacter = ref(null);
const nextCharacterNumber = ref(1);
const editingCharacterId = ref(null);
const editingCharacterName = ref("");

// --- Picture Sets State ---
const pictureSets = ref([]);

// --- Character Editor State ---
const characterEditorOpen = ref(false);
const characterEditorCharacter = ref(null);

const setEditorOpen = ref(false);
const setEditorSet = ref(null);

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

// Use reference_picture_set_id from categoryCounts (populated from backend /category/summary)
const referenceSetInfoByCharacter = computed(() => {
  const map = {};
  sortedCharacters.value.forEach((char) => {
    // Find the reference set for this character by matching name and description
    const set = pictureSets.value.find(
      (s) =>
        s.name === "reference_pictures" && s.description === String(char.id)
    );
    if (set) {
      map[char.id] = set;
    }
  });
  return map;
});

const editingNameModel = computed({
  get: () => editingCharacterName.value,
  set: (value) => emit("update:editing-character-name", value ?? ""),
});

const sortModel = computed({
  get: () => props.selectedSort,
  set: (value) => emit("update:selected-sort", value ?? ""),
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

function toggleSection(section) {
  if (!section || !(section in sections.value)) return;
  sections.value[section] = !sections.value[section];
}

function selectCharacter(id) {
  emit("select-character", id);
}

function deleteCharacter() {
  emit("delete-character");
}

function createCharacter() {
  emit("create-character");
}

function searchImages(query) {
  emit("search-images", query);
}

function selectSet(setId) {
  emit("select-set", setId);
}

function createSet() {
  emit("create-set");
}

function deleteSet() {
  emit("delete-set");
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

function dropOnSetItem(setId, event) {
  dragOverSet.value = null;
  emit("drop-on-set", { setId, event });
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

() => sortedCharacters.value,
  (chars) => {
    // Initialize collapse state for all characters to true (collapsed by default)
    chars.forEach((char) => {
      if (!(char.id in collapsedCharacters.value)) {
        collapsedCharacters.value[char.id] = true;
      }
    });
  },
  { immediate: true };

function toggleCharacterCollapse(charId) {
  collapsedCharacters.value[charId] = !collapsedCharacters.value[charId];
}

// --- Sidebar & Character Data ---
async function fetchSidebarCounts() {
  // Fetch total image count for END key logic
  try {
    const resAll = await fetch(`${props.backendUrl}/category/summary`);
    if (resAll.ok) {
      const data = await resAll.json();
      totalImages.value = data.image_count || 0;
      categoryCounts.value[props.allPicturesId] = data.image_count;
    }
  } catch {}
  try {
    const resAll = await fetch(`${props.backendUrl}/category/summary`);
    if (resAll.ok) {
      const data = await resAll.json();
      categoryCounts.value[props.allPicturesId] = data.image_count;
    }
  } catch {}
  try {
    const resUnassigned = await fetch(
      `${props.backendUrl}/category/summary?primary_character_id=null`
    );
    if (resUnassigned.ok) {
      const data = await resUnassigned.json();
      categoryCounts.value[props.unassignedPicturesId] = data.image_count;
    }
  } catch {}
  await Promise.all(
    characters.value.map(async (char) => {
      try {
        const res = await fetch(
          `${
            props.backendUrl
          }/category/summary?primary_character_id=${encodeURIComponent(
            char.id
          )}`
        );
        if (res.ok) {
          const data = await res.json();
          categoryCounts.value[char.id] = data.image_count;
        }
      } catch {}
    })
  );
}

async function fetchCharacters() {
  setLoading(true);
  setError(null);
  try {
    const res = await fetch(`${props.backendUrl}/characters`);
    if (!res.ok) throw new Error("Failed to fetch characters");
    const chars = await res.json();
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
  fetchSidebarCounts();
}
defineExpose({ refreshSidebar });

async function fetchCharacterThumbnail(characterId) {
  try {
    const cacheBuster = Date.now();
    const thumbUrl = `${props.backendUrl}/face_thumbnail/${characterId}?cb=${cacheBuster}`;
    const res = await fetch(thumbUrl);
    if (res.ok && res.headers.get("content-type")?.includes("image/png")) {
      characterThumbnails.value[characterId] = thumbUrl;
    } else {
      characterThumbnails.value[characterId] = null;
    }
  } catch (e) {
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
    const res = await fetch(`${props.backendUrl}/sort_mechanisms`);
    if (!res.ok) throw new Error("Failed to fetch sort mechanisms");
    const options = await res.json();
    // Use backend-provided values directly
    sortOptions.value = options.map((opt) => ({
      label: opt.label,
      value: opt.id,
    }));
    if (!sortModel.value && sortOptions.value.length) {
      sortModel.value = sortOptions.value[0].value;
    }
  } catch (e) {
    // Fallback to hardcoded options only if backend fails
    sortOptions.value = [
      { label: "Date: Latest First", value: "created_at DESC" },
      { label: "Date: Oldest First", value: "created_at ASC" },
      { label: "Score: Highest First", value: "score DESC" },
      { label: "Score: Lowest First", value: "score ASC" },
      { label: "Search Likeness", value: "search_likeness" },
    ];
    if (!selectedSort.value) selectedSort.value = sortOptions.value[0].value;
  }
}

// --- Picture Sets ---
async function fetchPictureSets() {
  try {
    const res = await fetch(`${props.backendUrl}/picture_sets`);
    if (!res.ok) throw new Error("Failed to fetch picture sets");
    pictureSets.value = await res.json();
  } catch (e) {
    console.error("Error fetching picture sets:", e);
  }
}

function handleCreateSet() {
  openSetEditor(null);
}

async function handleDeleteSet() {
  if (!selectedSet.value) return;

  const setToDelete = pictureSets.value.find((s) => s.id === selectedSet.value);
  if (!setToDelete) return;

  if (!confirm(`Delete picture set "${setToDelete.name}"?`)) return;

  try {
    const res = await fetch(
      `${props.backendUrl}/picture_sets/${selectedSet.value}`,
      {
        method: "DELETE",
      }
    );

    if (!res.ok) throw new Error("Failed to delete set");

    selectedSet.value = null;
    await fetchPictureSets();
  } catch (e) {
    alert("Failed to delete set: " + (e.message || e));
  }
}

async function removeSelectedFromSet() {
  handleSwitchToGrid();
  if (!selectedSet.value || selectedImageIds.value.length === 0) return;

  const setToUpdate = pictureSets.value.find((s) => s.id === selectedSet.value);
  if (!setToUpdate) return;

  try {
    // Remove each selected image from the set
    const removePromises = selectedImageIds.value.map(async (picId) => {
      const res = await fetch(
        `${props.backendUrl}/picture_sets/${selectedSet.value}/pictures/${picId}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`Failed to remove image ${picId}`);
    });

    await Promise.all(removePromises);

    // No longer remove from local images array
    selectedImageIds.value = [];

    // Refresh the picture sets to update counts
    await fetchPictureSets();
  } catch (e) {
    alert("Failed to remove images from set: " + (e.message || e));
    // Refresh the view to ensure consistency
    handleSelectSet(selectedSet.value);
  }
}

async function removeSelectedFromCharacter() {
  //handleSwitchToGrid();
  if (!props.selectedCharacter || selectedImageIds.value.length === 0) return;
  if (
    props.selectedCharacter === props.allPicturesId ||
    props.selectedCharacter === props.unassignedPicturesId
  )
    return;

  const character = characters.value.find(
    (c) => c.id === props.selectedCharacter
  );
  if (!character) return;

  try {
    // Update each selected image to clear primary_character_id
    const updatePromises = selectedImageIds.value.map(async (picId) => {
      const res = await fetch(`${props.backendUrl}/pictures/${picId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ primary_character_id: null }),
      });
      if (!res.ok)
        throw new Error(`Failed to update image ${picId}: ${res.status}`);
    });

    await Promise.all(updatePromises);

    // No longer remove from local images array
    selectedImageIds.value = [];

    // Refresh character counts
    await fetchSidebarCounts();
  } catch (e) {
    console.error("Error removing images:", e);
    alert("Failed to remove images from character: " + (e.message || e));
  }
}

async function handleDropOnSet({ setId, event }) {
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
      const res = await fetch(
        `${props.backendUrl}/picture_sets/${setId}/pictures/${picId}`,
        { method: "POST" }
      );
      // 400 error might mean it's already in the set, which is ok
      if (!res.ok && res.status !== 400) {
        throw new Error(`Failed to add image ${picId}`);
      }
    });

    await Promise.all(addPromises);

    // Refresh the picture sets to update counts
    await fetchPictureSets();

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

function confirmDeleteCharacter() {
  const char = characters.value.find((c) => c.id === props.selectedCharacter);
  if (!char) return;
  if (
    window.confirm(
      `Delete character '${char.name}'? This will unassign all their images.`
    )
  ) {
    fetch(`${props.backendUrl}/characters/${char.id}`, { method: "DELETE" })
      .then(async (res) => {
        if (!res.ok) throw new Error("Failed to delete character");
        characters.value = characters.value.filter((c) => c.id !== char.id);
        selectCharacter(props.allPicturesId);
        images.value = [];
        await fetchCharacters();
      })
      .catch((e) => {
        alert("Failed to delete character: " + (e.message || e));
      });
  }
}

function updateEditingCharacterName(value) {
  editingCharacterName.value = typeof value === "string" ? value : "";
}

function startEditingCharacter(char) {
  editingCharacterId.value = char.id;
  editingCharacterName.value = char.name;
  nextTick(() => {
    const input = document.querySelector(".edit-character-input");
    if (input) {
      input.focus();
      input.select();
    }
  });
}

function saveEditingCharacter(char) {
  const newName = editingCharacterName.value.trim();
  if (newName && newName !== char.name) {
    fetch(`${props.backendUrl}/characters/${char.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName }),
    })
      .then(async (res) => {
        if (!res.ok) throw new Error("Failed to update character");
        const data = await res.json();
        if (data && data.character) {
          char.name = data.character.name;
        }
      })
      .catch((e) => {
        alert("Failed to update character: " + (e.message || e));
      });
  }
  editingCharacterId.value = null;
  editingCharacterName.value = "";
}

function cancelEditingCharacter() {
  editingCharacterId.value = null;
  editingCharacterName.value = "";
}

onMounted(() => {
  fetchSortOptions();
  fetchCharacters();
  fetchPictureSets();
});
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
  />
  <PictureSetEditor :open="setEditorOpen" :set="setEditorSet" />

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
              <template v-if="editingCharacterId === char.id">
                <input
                  v-model="editingNameModel"
                  class="edit-character-input"
                  @keydown.enter="saveEditingCharacter(char)"
                  @keydown.esc="cancelEditingCharacter"
                  @blur="saveEditingCharacter(char)"
                  style="
                    width: 90%;
                    font-size: 1em;
                    background: #fff;
                    color: #222;
                    border-radius: 4px;
                    border: 1px solid #bbb;
                    padding: 2px 6px;
                    outline: none;
                  "
                />
              </template>
              <template v-else>
                <v-tooltip location="top">
                  <template #activator="{ props }">
                    <span
                      v-bind="props"
                      @dblclick.stop="startEditingCharacter(char)"
                      class="sidebar-list-label-text"
                    >
                      {{
                        char.name.charAt(0).toUpperCase() + char.name.slice(1)
                      }}
                    </span>
                  </template>
                  <span>{{ char.name }}</span>
                </v-tooltip>
              </template>
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
                <template v-if="referenceSetInfoByCharacter[char.id]">
                  <div
                    :class="[
                      'sidebar-list-item',
                      'sidebar-reference-set',
                      {
                        active:
                          selectedSet ===
                          referenceSetInfoByCharacter[char.id].id,
                        droppable:
                          dragOverSet ===
                          referenceSetInfoByCharacter[char.id].id,
                      },
                    ]"
                    @click="selectSet(referenceSetInfoByCharacter[char.id].id)"
                    @dragover.prevent="
                      dragOverSetItem(referenceSetInfoByCharacter[char.id].id)
                    "
                    @dragleave="dragLeaveSetItem"
                    @drop.prevent="
                      dropOnSetItem(
                        referenceSetInfoByCharacter[char.id].id,
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
          @click.stop="deleteSet"
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
            (pset) => pset.name !== 'reference_pictures'
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
            @drop.prevent="dropOnSetItem(pset.id, $event)"
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

    <div class="sidebar-section-header" @click="toggleSection('analysis')">
      <v-icon small style="margin-right: 8px">
        {{ sections.analysis ? "mdi-chevron-down" : "mdi-chevron-right" }}
      </v-icon>
      Analysis
      <span style="flex: 1 1 auto"></span>
    </div>
    <transition name="fade">
      <div v-show="sections.analysis">
        <div class="sidebar-list-item" @click="$emit('switch-to-likeness')">
          <span class="sidebar-list-icon">
            <v-icon size="44">mdi-account-group</v-icon>
          </span>
          <span class="sidebar-list-label">Likeness View</span>
        </div>
      </div>
    </transition>

    <div class="sidebar-section-header" @click="toggleSection('search')">
      <v-icon small style="margin-right: 8px">
        {{ sections.search ? "mdi-chevron-down" : "mdi-chevron-right" }}
      </v-icon>
      Search &amp; Sorting
      <span style="flex: 1 1 auto"></span>
    </div>
    <transition name="fade">
      <div class="search-and-sort" v-show="sections.search">
        <div class="sidebar-searchbar-wrapper">
          <SearchBar
            v-model="searchModel"
            placeholder="Search images..."
            class="sidebar-searchbar"
            @search="searchImages"
          />
        </div>
        <div class="sidebar-searchbar-wrapper">
          <v-select
            v-model="sortModel"
            :items="sortOptions"
            class="sidebar-sort-select"
            item-title="label"
            item-value="value"
            label="Sort by"
            dense
            hide-details
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
  font-size: 1.2rem;
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
  min-height: 56px;
  padding: 2px 6px;
  cursor: pointer;
  border-radius: 0;
  margin-bottom: 0;
  font-size: 1em;
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
  width: 44px;
  height: 44px;
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
  max-width: 44px;
  max-height: 44px;
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
  font-size: 0.92em;
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

.sidebar-searchbar-wrapper {
  display: flex;
  justify-content: center;
  align-items: center;
  position: relative;
  width: 100%;
  padding: 4px;
}

.sidebar-searchbar {
  width: 100%;
  min-width: 0;
  position: relative;
  transition: max-width 0.3s cubic-bezier(0.4, 0, 0.2, 1),
    width 0.3s cubic-bezier(0.4, 0, 0.2, 1);
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
