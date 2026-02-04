<template>
  <div ref="rootRef" class="add-to-set" :class="{ open: menuOpen, disabled }">
    <button
      class="add-to-set-btn"
      type="button"
      :disabled="disabled"
      :aria-expanded="menuOpen"
      aria-haspopup="true"
      aria-label="Add to set"
      title="Add to set"
      @click.stop="toggleMenu"
    >
      <v-icon size="18">mdi-folder-plus</v-icon>
      <span class="add-to-set-label">{{ label }}</span>
      <v-icon size="16">mdi-chevron-down</v-icon>
    </button>

    <div class="add-to-set-menu" role="menu">
      <div class="add-to-set-search">
        <v-icon size="14">mdi-magnify</v-icon>
        <input
          ref="searchInputRef"
          v-model="searchQuery"
          type="text"
          placeholder="Search sets..."
          @keydown.escape.stop.prevent="closeMenu"
        />
      </div>

      <div v-if="isLoading" class="add-to-set-empty">Loading sets...</div>
      <div v-else-if="filteredSets.length === 0" class="add-to-set-empty">
        No sets found
      </div>
      <button
        v-for="set in filteredSets"
        :key="set.id"
        :class="[
          'add-to-set-item',
          { 'add-to-set-item--disabled': isSetDisabled(set) },
        ]"
        type="button"
        role="menuitem"
        :disabled="isSetDisabled(set)"
        @click.stop="addToSet(set)"
      >
        <span class="add-to-set-item-name">{{ set.name }}</span>
        <span v-if="set.picture_count != null" class="add-to-set-item-count">
          {{ set.picture_count }}
        </span>
      </button>

      <div v-if="statusMessage" class="add-to-set-status">
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
  label: { type: String, default: "Add to set" },
});

const emit = defineEmits(["added"]);

const rootRef = ref(null);
const searchInputRef = ref(null);
const menuOpen = ref(false);
const searchQuery = ref("");
const isLoading = ref(false);
const sets = ref([]);
const statusMessage = ref("");
const setMembersById = ref({});
let statusTimer = null;

const baseUrl = computed(() =>
  props.backendUrl ? String(props.backendUrl).replace(/\/$/, "") : "",
);

function resolveUrl(path) {
  return baseUrl.value ? `${baseUrl.value}${path}` : path;
}

const normalizedPictureIds = computed(() =>
  (Array.isArray(props.pictureIds) ? props.pictureIds : [])
    .map((id) => String(id))
    .filter(Boolean),
);

const normalizedIdsKey = computed(() => normalizedPictureIds.value.join("|"));
const lastFetchKey = ref("");

const filteredSets = computed(() => {
  const needle = searchQuery.value.trim().toLowerCase();
  if (!needle) return sets.value;
  return sets.value.filter((set) =>
    String(set?.name || "")
      .toLowerCase()
      .includes(needle),
  );
});

function isSetDisabled(set) {
  const ids = normalizedPictureIds.value;
  if (!ids.length) return true;
  const members = setMembersById.value?.[set.id];
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
  fetchSets(true);
  nextTick(() => searchInputRef.value?.focus());
  document.addEventListener("pointerdown", handleOutsideClick, true);
}

function closeMenu() {
  menuOpen.value = false;
  searchQuery.value = "";
  document.removeEventListener("pointerdown", handleOutsideClick, true);
}

function handleOutsideClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) return;
  if (!rootRef.value || rootRef.value.contains(target)) return;
  closeMenu();
}

async function fetchSets(force = false) {
  if (!props.backendUrl || isLoading.value) return;
  const key = normalizedIdsKey.value;
  if (!force && key === lastFetchKey.value && sets.value.length) {
    return;
  }
  lastFetchKey.value = key;
  isLoading.value = true;
  try {
    const res = await apiClient.get(resolveUrl("/picture_sets"));
    const data = await res.data;
    const list = Array.isArray(data) ? data : [];
    const filtered = list.filter((set) => !set?.reference_character);
    sets.value = filtered;
    await fetchSetMembers(filtered);
  } catch (e) {
    sets.value = [];
    setMembersById.value = {};
  } finally {
    isLoading.value = false;
  }
}

async function fetchSetMembers(list) {
  const ids = normalizedPictureIds.value;
  if (!props.backendUrl || !ids.length) {
    setMembersById.value = {};
    return;
  }
  const entries = await Promise.all(
    list.map(async (set) => {
      try {
        const res = await apiClient.get(
          resolveUrl(`/picture_sets/${set.id}/members`),
        );
        const data = await res.data;
        const pictureIds = Array.isArray(data?.picture_ids)
          ? data.picture_ids
          : Array.isArray(data)
            ? data
            : [];
        return [set.id, new Set(pictureIds.map((id) => String(id)))];
      } catch (e) {
        return [set.id, new Set()];
      }
    }),
  );
  const next = {};
  entries.forEach(([setId, members]) => {
    next[setId] = members;
  });
  setMembersById.value = next;
}

async function addToSet(set) {
  if (!set?.id) return;
  if (isSetDisabled(set)) return;
  const ids = normalizedPictureIds.value;
  if (!ids.length) return;
  const members = setMembersById.value?.[set.id];
  const idsToAdd = members ? ids.filter((id) => !members.has(String(id))) : ids;
  if (!idsToAdd.length) {
    statusMessage.value = "Already in set";
    return;
  }
  statusMessage.value = "Adding...";
  try {
    await Promise.all(
      idsToAdd.map((id) =>
        apiClient.post(resolveUrl(`/picture_sets/${set.id}/members/${id}`)),
      ),
    );
    statusMessage.value = `Added to ${set.name}`;
    emit("added", { setId: set.id, pictureIds: ids });
    if (members) {
      idsToAdd.forEach((id) => members.add(String(id)));
    }
    closeMenu();
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    if (String(detail).includes("already in set")) {
      statusMessage.value = "Already in set";
    } else {
      statusMessage.value = "Failed to add";
    }
  }
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => {
    statusMessage.value = "";
  }, 2000);
}

onBeforeUnmount(() => {
  if (statusTimer) clearTimeout(statusTimer);
  document.removeEventListener("pointerdown", handleOutsideClick, true);
});

watch(
  () => normalizedIdsKey.value,
  () => {
    if (menuOpen.value) {
      fetchSets();
    } else {
      setMembersById.value = {};
    }
  },
);
</script>

<style scoped>
.add-to-set {
  position: relative;
  display: inline-flex;
}

.add-to-set-btn {
  background-color: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgba(var(--v-theme-on-dark-surface), 1);
  border: none;
  padding: 6px 14px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 1em;
  cursor: pointer;
}

.add-to-set-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.add-to-set-btn:hover {
  filter: brightness(1.75);
  border: none;
}

.add-to-set-label {
  white-space: nowrap;
}

.add-to-set-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  min-width: 200px;
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

.add-to-set.open .add-to-set-menu {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.add-to-set-search {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.72rem;
  color: rgba(var(--v-theme-on-background), 0.7);
  background: rgba(var(--v-theme-surface), 0.1);
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  margin-bottom: 8px;
}

.add-to-set-search input {
  background: transparent;
  border: none;
  color: #fff;
  width: 100%;
  font-size: 0.78rem;
  outline: none;
}

.add-to-set-item {
  width: 100%;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 0.78rem;
  color: #fff;
  background: transparent;
  border: none;
  text-align: left;
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
}

.add-to-set-item:hover {
  background: rgba(255, 255, 255, 0.08);
}

.add-to-set-item--disabled {
  opacity: 0.5;
  cursor: default;
  pointer-events: none;
}

.add-to-set-item-count {
  font-size: 0.7rem;
  color: rgba(255, 255, 255, 0.6);
}

.add-to-set-empty {
  padding: 6px 8px;
  font-size: 0.75rem;
  color: rgba(255, 255, 255, 0.6);
}

.add-to-set-status {
  margin-top: 6px;
  padding: 6px 8px;
  font-size: 0.72rem;
  color: rgba(255, 255, 255, 0.7);
}
</style>
