<template>
  <div
    ref="rootRef"
    class="add-to-character"
    :class="{ open: menuOpen, disabled }"
  >
    <button
      class="add-to-character-btn"
      type="button"
      :disabled="disabled"
      :aria-expanded="menuOpen"
      aria-haspopup="true"
      aria-label="Set character"
      title="Set character"
      @click.stop="toggleMenu"
    >
      <v-icon size="18">mdi-account-plus</v-icon>
      <span class="add-to-character-label">{{ label }}</span>
      <v-icon size="16">mdi-chevron-down</v-icon>
    </button>

    <div class="add-to-character-menu" role="menu">
      <div class="add-to-character-search">
        <v-icon size="14">mdi-magnify</v-icon>
        <input
          ref="searchInputRef"
          v-model="searchQuery"
          type="text"
          placeholder="Search characters..."
          @keydown.escape.stop.prevent="closeMenu"
        />
      </div>

      <div v-if="isLoading" class="add-to-character-empty">
        Loading characters...
      </div>
      <div
        v-else-if="filteredCharacters.length === 0"
        class="add-to-character-empty"
      >
        No characters found
      </div>
      <button
        v-for="character in filteredCharacters"
        :key="character.id"
        :class="[
          'add-to-character-item',
          { 'add-to-character-item--disabled': isCharacterDisabled(character) },
        ]"
        type="button"
        role="menuitem"
        :disabled="isCharacterDisabled(character)"
        @click.stop="addToCharacter(character)"
      >
        <span class="add-to-character-item-name">{{ character.name }}</span>
      </button>

      <div v-if="statusMessage" class="add-to-character-status">
        {{ statusMessage }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  backendUrl: { type: String, required: true },
  pictureIds: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: "Character" },
});

const emit = defineEmits(["added"]);

const rootRef = ref(null);
const searchInputRef = ref(null);
const menuOpen = ref(false);
const searchQuery = ref("");
const isLoading = ref(false);
const characters = ref([]);
const statusMessage = ref("");
const characterMembersById = ref({});
let statusTimer = null;

const normalisedPictureIds = computed(() =>
  (Array.isArray(props.pictureIds) ? props.pictureIds : [])
    .map((id) => String(id))
    .filter(Boolean),
);

const baseUrl = computed(() =>
  props.backendUrl ? String(props.backendUrl).replace(/\/$/, "") : "",
);

function resolveUrl(path) {
  return baseUrl.value ? `${baseUrl.value}${path}` : path;
}

const filteredCharacters = computed(() => {
  const needle = searchQuery.value.trim().toLowerCase();
  if (!needle) return characters.value;
  return characters.value.filter((char) =>
    String(char?.name || "")
      .toLowerCase()
      .includes(needle),
  );
});

function isCharacterDisabled(character) {
  const ids = normalisedPictureIds.value;
  if (!ids.length) return true;
  const members = characterMembersById.value?.[character.id];
  if (!members || members.size === 0) return false;
  return ids.every((id) => members.has(String(id)));
}

function toggleMenu() {
  if (props.disabled) return;
  menuOpen.value = !menuOpen.value;
  if (menuOpen.value) {
    openMenu();
  } else {
    closeMenu();
  }
}

function openMenu() {
  menuOpen.value = true;
  fetchCharacters();
  nextTick(() => searchInputRef.value?.focus());
  document.addEventListener("mousedown", handleOutsideClick);
}

function closeMenu() {
  menuOpen.value = false;
  searchQuery.value = "";
  document.removeEventListener("mousedown", handleOutsideClick);
}

function handleOutsideClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) return;
  if (!rootRef.value || rootRef.value.contains(target)) return;
  closeMenu();
}

async function fetchCharacters() {
  if (!props.backendUrl || isLoading.value) return;
  isLoading.value = true;
  try {
    const res = await apiClient.get(resolveUrl("/characters"));
    const data = await res.data;
    characters.value = Array.isArray(data) ? data : [];
    await fetchCharacterMembers(characters.value);
  } catch (e) {
    characters.value = [];
    characterMembersById.value = {};
  } finally {
    isLoading.value = false;
  }
}

async function fetchCharacterMembers(list) {
  const ids = normalisedPictureIds.value;
  if (!props.backendUrl || !ids.length) {
    characterMembersById.value = {};
    return;
  }
  const pictureEntries = await Promise.all(
    ids.map(async (id) => {
      try {
        const res = await apiClient.get(resolveUrl(`/pictures/${id}/faces`));
        const data = await res.data;
        const faces = Array.isArray(data?.faces)
          ? data.faces
          : Array.isArray(data)
            ? data
            : [];
        return [String(id), faces];
      } catch (e) {
        return [String(id), []];
      }
    }),
  );

  const next = {};
  list.forEach((character) => {
    next[character.id] = new Set();
  });

  pictureEntries.forEach(([pictureId, faces]) => {
    faces.forEach((face) => {
      if (face?.character_id == null) return;
      const key = face.character_id;
      if (!next[key]) {
        next[key] = new Set();
      }
      next[key].add(String(pictureId));
    });
  });

  characterMembersById.value = next;
}

async function addToCharacter(character) {
  if (!character?.id) return;
  if (isCharacterDisabled(character)) return;
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const members = characterMembersById.value?.[character.id];
  const idsToAdd = members ? ids.filter((id) => !members.has(String(id))) : ids;
  if (!idsToAdd.length) {
    statusMessage.value = "Already assigned";
    return;
  }
  statusMessage.value = "Assigning...";
  try {
    await apiClient.post(resolveUrl(`/characters/${character.id}/faces`), {
      picture_ids: idsToAdd,
    });
    statusMessage.value = `Assigned to ${character.name}`;
    emit("added", { characterId: character.id, pictureIds: ids });
    if (members) {
      idsToAdd.forEach((id) => members.add(String(id)));
    }
    closeMenu();
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    statusMessage.value = detail ? String(detail) : "Failed to assign";
  }
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => {
    statusMessage.value = "";
  }, 2000);
}

onBeforeUnmount(() => {
  if (statusTimer) clearTimeout(statusTimer);
  document.removeEventListener("mousedown", handleOutsideClick);
});

watch(
  () => normalisedPictureIds.value,
  () => {
    if (menuOpen.value) {
      fetchCharacters();
    } else {
      characterMembersById.value = {};
    }
  },
);
</script>

<style scoped>
.add-to-character {
  position: relative;
  display: inline-flex;
}

.add-to-character-btn {
  border: none;
  background-color: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgba(var(--v-theme-on-dark-surface), 1);
  padding: 6px 14px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 1em;
  cursor: pointer;
}

.add-to-character-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.add-to-character-btn:hover {
  filter: brightness(1.75);
  border: none;
}

.add-to-character-label {
  white-space: nowrap;
}

.add-to-character-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  min-width: 220px;
  padding: 10px;
  border-radius: 10px;
  background-color: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgba(var(--v-theme-on-dark-surface), 1);

  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.35);
  opacity: 0;
  transform: translateY(-6px);
  pointer-events: none;
  transition:
    opacity 0.15s ease,
    transform 0.15s ease;
  z-index: 6;
}

.add-to-character.open .add-to-character-menu {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.add-to-character-search {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.72rem;
  color: rgba(255, 255, 255, 0.55);
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  margin-bottom: 8px;
}

.add-to-character-search input {
  background: transparent;
  border: none;
  color: #fff;
  width: 100%;
  font-size: 0.78rem;
  outline: none;
}

.add-to-character-item {
  width: 100%;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 0.78rem;
  background-color: transparent;
  color: rgba(var(--v-theme-on-dark-surface), 1);
  border: none;
  text-align: left;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
}

.add-to-character-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.add-to-character-item--disabled {
  opacity: 0.5;
  cursor: default;
  pointer-events: none;
}

.add-to-character-empty {
  padding: 6px 8px;
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.6);
}

.add-to-character-status {
  margin-top: 6px;
  padding: 6px 8px;
  font-size: 0.72rem;
  color: rgba(255, 255, 255, 0.7);
}
</style>
