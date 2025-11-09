<script setup>
import { computed } from "vue";
import SearchBar from "./SearchBar.vue";
import unknownPerson from "../assets/unknown-person.png"; // Fallback avatar for characters without thumbnails

const props = defineProps({
  sections: { type: Object, required: true },
  selectedCharacter: { type: [String, Number], required: true },
  allPicturesId: { type: String, required: true },
  unassignedPicturesId: { type: String, required: true },
  categoryCounts: { type: Object, required: true },
  sortedCharacters: { type: Array, required: true },
  error: { type: String, default: "" },
  dragOverCharacter: { type: [String, Number, null], default: null },
  characterThumbnails: { type: Object, required: true },
  editingCharacterId: { type: [String, Number, null], default: null },
  editingCharacterName: { type: String, default: "" },
  searchQuery: { type: String, default: "" },
  sortOptions: { type: Array, required: true },
  selectedSort: { type: String, default: "" },
});

const emit = defineEmits([
  "toggle-section",
  "select-character",
  "delete-character",
  "create-character",
  "start-editing-character",
  "save-editing-character",
  "cancel-editing-character",
  "update:editing-character-name",
  "open-character-editor",
  "drag-over-character",
  "drag-leave-character",
  "drop-on-character",
  "search-images",
  "update:selected-sort",
  "update:search-query",
]);

const editingNameModel = computed({
  get: () => props.editingCharacterName,
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

function toggleSection(section) {
  emit("toggle-section", section);
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

function startEditingCharacter(char) {
  emit("start-editing-character", char);
}

function saveEditingCharacter(char) {
  emit("save-editing-character", char);
}

function cancelEditingCharacter() {
  emit("cancel-editing-character");
}

function openCharacterEditor(char) {
  emit("open-character-editor", char);
}

function dragOverCharacter(id) {
  emit("drag-over-character", id);
}

function dragLeaveCharacter() {
  emit("drag-leave-character");
}

function dropOnCharacter(id, event) {
  emit("drop-on-character", { characterId: id, event });
}

function searchImages(query) {
  emit("search-images", query);
}
</script>

<template>
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
            { active: selectedCharacter === allPicturesId },
          ]"
          @click="selectCharacter(allPicturesId)"
        >
          <span class="sidebar-list-icon">
            <v-icon size="44">mdi-image-multiple</v-icon>
          </span>
          <span class="sidebar-list-label">All Pictures</span>
          <span class="sidebar-list-count">
            {{ categoryCounts[allPicturesId] ?? "" }}
          </span>
        </div>
        <div
          :class="[
            'sidebar-list-item',
            { active: selectedCharacter === unassignedPicturesId },
          ]"
          @click="selectCharacter(unassignedPicturesId)"
        >
          <span class="sidebar-list-icon">
            <v-icon size="44">mdi-help-circle-outline</v-icon>
          </span>
          <span class="sidebar-list-label">Unassigned Pictures</span>
          <span class="sidebar-list-count">
            {{ categoryCounts[unassignedPicturesId] ?? "" }}
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
            selectedCharacter &&
            selectedCharacter !== allPicturesId &&
            selectedCharacter !== unassignedPicturesId
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
        <div v-if="error" class="sidebar-error">{{ error }}</div>
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
            @dragover.prevent="dragOverCharacter(char.id)"
            @dragleave="dragLeaveCharacter"
            @drop.prevent="dropOnCharacter(char.id, $event)"
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
                <span @dblclick.stop="startEditingCharacter(char)">
                  {{ char.name.charAt(0).toUpperCase() + char.name.slice(1) }}
                </span>
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
          </div>
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
  background: #506168ff;
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
  margin: 0 0 2px 0;
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
  padding: 8px 16px;
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
    rgba(255, 165, 0, 0) 0%,
    rgba(255, 165, 0, 1) 100%
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
</style>
