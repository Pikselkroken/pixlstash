<template>
  <div
    ref="rootRef"
    class="add-to-character"
    :class="{
      open: menuOpen,
      disabled,
      'add-to-character--flyout': placement === 'right',
      'add-to-character--force-dark': forceDark,
    }"
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
      <v-icon size="16" class="add-to-character-chevron">{{
        placement === "right" ? "mdi-chevron-right" : "mdi-chevron-down"
      }}</v-icon>
    </button>

    <Teleport :disabled="placement !== 'right'" to="body">
      <div
        ref="menuRef"
        class="add-to-character-menu"
        role="menu"
        :style="placement === 'right' ? flyoutMenuStyle : {}"
        :class="{
          open: menuOpen,
          flyout: placement === 'right',
          'force-dark': forceDark,
        }"
      >
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
            {
              'add-to-character-item--disabled': isCharacterDisabled(character),
              'add-to-character-item--checked':
                getCharacterState(character) === 'checked',
            },
          ]"
          type="button"
          role="menuitem"
          :disabled="isCharacterDisabled(character)"
          @click.stop="toggleCharacterMembership(character)"
        >
          <v-icon size="16" class="add-to-character-item-check">
            {{
              getCharacterState(character) === "checked"
                ? "mdi-checkbox-marked"
                : getCharacterState(character) === "partial"
                  ? "mdi-minus-box-outline"
                  : "mdi-checkbox-blank-outline"
            }}
          </v-icon>
          <span class="add-to-character-item-name">{{ character.name }}</span>
        </button>

        <div v-if="statusMessage" class="add-to-character-status">
          {{ statusMessage }}
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  backendUrl: { type: String, required: true },
  pictureIds: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: "Person" },
  placement: { type: String, default: "bottom" },
  forceDark: { type: Boolean, default: false },
});

const emit = defineEmits(["added", "removed"]);

const rootRef = ref(null);
const menuRef = ref(null);

const flyoutMenuStyle = ref({});

function positionFlyout() {
  if (props.placement !== "right" || !rootRef.value) return;
  const rect = rootRef.value.getBoundingClientRect();
  const menuW = 250;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const left =
    rect.right + 4 + menuW <= vw - 8
      ? rect.right + 4
      : Math.max(8, rect.left - menuW - 4);
  const top = Math.min(rect.top, vh - 32);
  flyoutMenuStyle.value = {
    position: "fixed",
    top: `${top}px`,
    left: `${left}px`,
    maxHeight: `${vh - top - 16}px`,
    overflowY: "auto",
  };
}
const searchInputRef = ref(null);
const menuOpen = ref(false);
const searchQuery = ref("");
const isLoading = ref(false);
const characters = ref([]);
const statusMessage = ref("");
const characterMembersById = ref({});
const picturesWithFaces = ref(new Set()); // picture IDs that have ≥1 face record
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

const sortedCharacters = computed(() =>
  [...characters.value].sort((a, b) =>
    String(a?.name || "").localeCompare(String(b?.name || "")),
  ),
);

const filteredCharacters = computed(() => {
  const needle = searchQuery.value.trim().toLowerCase();
  if (!needle) return sortedCharacters.value;
  return sortedCharacters.value.filter((char) =>
    String(char?.name || "")
      .toLowerCase()
      .includes(needle),
  );
});

function getCharacterState(character) {
  const ids = normalisedPictureIds.value;
  if (!ids.length) return "unchecked";
  const members = characterMembersById.value?.[character.id];

  // Only count pictures that have at least one face record; faceless pictures
  // can't be assigned via face detection so they don't affect check state.
  const facedIds = ids.filter((id) => picturesWithFaces.value.has(String(id)));
  if (!facedIds.length) return "unchecked";

  const matched = facedIds.filter((id) => members?.has(String(id))).length;
  if (matched === 0) return "unchecked";
  if (matched === facedIds.length) return "checked";
  return "partial";
}

function isCharacterDisabled(character) {
  return normalisedPictureIds.value.length === 0;
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
  if (props.placement === "right") positionFlyout();
  nextTick(() => searchInputRef.value?.focus());
  document.addEventListener("mousedown", handleOutsideClick);
  if (props.placement === "right") {
    window.addEventListener("resize", positionFlyout, { passive: true });
  }
}

function closeMenu() {
  menuOpen.value = false;
  searchQuery.value = "";
  searchInputRef.value?.blur();
  document.removeEventListener("mousedown", handleOutsideClick);
  window.removeEventListener("resize", positionFlyout);
}

function handleOutsideClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) return;
  if (rootRef.value?.contains(target)) return;
  if (menuRef.value?.contains(target)) return;
  closeMenu();
}

async function fetchCharacters() {
  if (!props.backendUrl || isLoading.value) return;
  isLoading.value = true;
  try {
    const [charsRes] = await Promise.all([
      apiClient.get(resolveUrl("/characters")),
      fetchCharacterMembers(),
    ]);
    characters.value = Array.isArray(charsRes.data) ? charsRes.data : [];
  } catch (e) {
    characters.value = [];
    characterMembersById.value = {};
  } finally {
    isLoading.value = false;
  }
}

async function fetchCharacterMembers() {
  const ids = normalisedPictureIds.value;
  if (!props.backendUrl || !ids.length) {
    characterMembersById.value = {};
    picturesWithFaces.value = new Set();
    return;
  }
  try {
    const res = await apiClient.post(resolveUrl("/characters/membership"), {
      picture_ids: ids,
    });
    const data = res.data ?? {};
    const assignments = data.character_assignments ?? {};
    const withFaces = data.pictures_with_faces ?? [];
    picturesWithFaces.value = new Set(withFaces.map(String));
    const next = {};
    Object.entries(assignments).forEach(([charId, picIds]) => {
      next[Number(charId)] = new Set(picIds.map(String));
    });
    characterMembersById.value = next;
  } catch (e) {
    characterMembersById.value = {};
    picturesWithFaces.value = new Set();
  }
}

async function toggleCharacterMembership(character) {
  if (!character?.id || isCharacterDisabled(character)) return;
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const state = getCharacterState(character);

  if (state === "checked") {
    statusMessage.value = "Removing...";
    try {
      await apiClient.delete(resolveUrl(`/characters/${character.id}/faces`), {
        data: { picture_ids: ids },
      });
      statusMessage.value = `Removed from ${character.name}`;
      emit("removed", { characterId: character.id, pictureIds: ids });
      const members = characterMembersById.value?.[character.id];
      if (members) ids.forEach((id) => members.delete(String(id)));
      closeMenu();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || String(e);
      statusMessage.value = detail ? String(detail) : "Failed to remove";
    }
  } else {
    const members = characterMembersById.value?.[character.id];
    const idsToAdd = members
      ? ids.filter((id) => !members.has(String(id)))
      : ids;
    if (!idsToAdd.length) return;
    statusMessage.value = "Assigning...";
    try {
      await apiClient.post(resolveUrl(`/characters/${character.id}/faces`), {
        picture_ids: idsToAdd,
      });
      statusMessage.value = `Assigned to ${character.name}`;
      emit("added", { characterId: character.id, pictureIds: ids });
      // Only update the optimistic member cache for pictures that actually have
      // faces — faceless pictures can't be reflected in characterMembersById.
      if (members) {
        idsToAdd
          .filter((id) => picturesWithFaces.value.has(String(id)))
          .forEach((id) => members.add(String(id)));
      }
      closeMenu();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || String(e);
      statusMessage.value = detail ? String(detail) : "Failed to assign";
    }
  }
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => {
    statusMessage.value = "";
  }, 2000);
}

onBeforeUnmount(() => {
  if (statusTimer) clearTimeout(statusTimer);
  document.removeEventListener("mousedown", handleOutsideClick);
  window.removeEventListener("resize", positionFlyout);
});

watch(
  () => normalisedPictureIds.value,
  () => {
    if (menuOpen.value) {
      fetchCharacters();
    } else {
      characterMembersById.value = {};
      picturesWithFaces.value = new Set();
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
  background-color: rgba(var(--v-theme-surface), 0.85);
  color: rgb(var(--v-theme-on-surface));
  padding: 2px 8px;
  border-radius: 3px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.85rem;
  line-height: 1.4;
  cursor: pointer;
}

.add-to-character--force-dark .add-to-character-btn {
  background-color: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgba(var(--v-theme-on-dark-surface), 1);
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
  background-color: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));

  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.35);
  opacity: 0;
  transform: translateY(-6px);
  pointer-events: none;
  transition:
    opacity 0.15s ease,
    transform 0.15s ease;
  z-index: 6;
}

.add-to-character-menu.force-dark {
  background-color: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgba(var(--v-theme-on-dark-surface), 1);

  .add-to-character-search {
    color: rgba(255, 255, 255, 0.55);
    background: rgba(255, 255, 255, 0.06);
  }

  .add-to-character-search input {
    color: #fff;
  }

  .add-to-character-item {
    color: rgba(var(--v-theme-on-dark-surface), 1);
  }

  .add-to-character-item:hover {
    background: rgba(255, 255, 255, 0.08);
  }

  .add-to-character-empty {
    color: rgba(255, 255, 255, 0.6);
  }

  .add-to-character-status {
    color: rgba(255, 255, 255, 0.7);
  }
}

.add-to-character.open .add-to-character-menu,
.add-to-character-menu.open {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.add-to-character-search {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.72rem;
  color: rgba(var(--v-theme-on-surface), 0.6);
  padding: 6px 8px;
  border-radius: 8px;
  background: rgba(var(--v-theme-on-surface), 0.06);
  margin-bottom: 8px;
}

.add-to-character-search input {
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-surface));
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
  color: rgb(var(--v-theme-on-surface));
  border: none;
  text-align: left;
  display: flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}

.add-to-character-item-check {
  color: rgba(var(--v-theme-on-surface), 0.7);
  flex-shrink: 0;
}

.add-to-character-item--checked .add-to-character-item-check {
  color: rgb(var(--v-theme-primary));
}

.add-to-character-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.add-to-character-item--disabled {
  opacity: 0.5;
  cursor: default;
  pointer-events: none;
}

.add-to-character-empty {
  padding: 6px 8px;
  font-size: 0.75rem;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.add-to-character-status {
  margin-top: 6px;
  padding: 6px 8px;
  font-size: 0.72rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

/* ── Flyout (right-placement) mode ──────────────────────────── */
.add-to-character--flyout {
  width: 100%;
  display: flex;
}

.add-to-character--flyout .add-to-character-btn {
  width: 100%;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  padding: 7px 14px;
  border-radius: 0;
  font-size: 13px;
  gap: 8px;
}

.add-to-character--flyout .add-to-character-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.add-to-character--flyout .add-to-character-chevron {
  margin-left: auto;
  opacity: 0.7;
}

.add-to-character--flyout .add-to-character-menu,
.add-to-character-menu.flyout {
  /* top/left set by JS positionFlyout() via inline style */
  transform: translateX(-6px);
  z-index: 2500;
}

.add-to-character--flyout.open .add-to-character-menu,
.add-to-character--flyout .add-to-character-menu.open,
.add-to-character-menu.flyout.open {
  transform: translateX(0);
}
</style>
