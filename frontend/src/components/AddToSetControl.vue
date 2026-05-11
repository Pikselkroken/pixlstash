<template>
  <div
    ref="rootRef"
    class="add-to-set"
    :class="{
      open: menuOpen,
      disabled,
      'add-to-set--flyout': placement === 'right',
      'add-to-set--force-dark': forceDark,
    }"
  >
    <button
      class="add-to-set-btn"
      type="button"
      :disabled="disabled"
      :aria-expanded="menuOpen"
      aria-haspopup="true"
      aria-label="Set"
      title="Set"
      @click.stop="toggleMenu"
    >
      <v-icon size="18">mdi-folder-plus</v-icon>
      <span class="add-to-set-label">{{ label }}</span>
      <v-icon size="16" class="add-to-set-chevron">{{
        placement === "right" ? "mdi-chevron-right" : "mdi-chevron-down"
      }}</v-icon>
    </button>

    <Teleport :disabled="placement !== 'right'" to="body">
      <div
        ref="menuRef"
        class="add-to-set-menu"
        role="menu"
        :style="placement === 'right' ? flyoutMenuStyle : {}"
        :class="{
          open: menuOpen,
          flyout: placement === 'right',
          'force-dark': forceDark,
        }"
      >
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
            {
              'add-to-set-item--disabled': isSetDisabled(set),
              'add-to-set-item--checked': getSetState(set) === 'checked',
            },
          ]"
          type="button"
          role="menuitem"
          :disabled="isSetDisabled(set)"
          @click.stop="toggleSetMembership(set)"
        >
          <v-icon size="16" class="add-to-set-item-check">
            {{
              getSetState(set) === "checked"
                ? "mdi-checkbox-marked"
                : getSetState(set) === "partial"
                  ? "mdi-minus-box-outline"
                  : "mdi-checkbox-blank-outline"
            }}
          </v-icon>
          <span class="add-to-set-item-name">{{ set.name }}</span>
          <span class="add-to-set-item-meta">
            <span
              v-if="set.picture_count != null"
              class="add-to-set-item-count"
            >
              {{ set.picture_count }}
            </span>
            <span
              v-if="isLastUsedSet(set)"
              class="add-to-set-item-shortcut"
              title="Press A to add to this set"
              >A</span
            >
          </span>
        </button>

        <div v-if="statusMessage" class="add-to-set-status">
          {{ statusMessage }}
        </div>
      </div>
    </Teleport>

    <div
      v-if="statusMessage && !menuOpen"
      class="add-to-set-shortcut-status"
      role="status"
      aria-live="polite"
    >
      {{ statusMessage }}
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { apiClient } from "../utils/apiClient";

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

// Persists the last set the user added a picture to, shared across all
// AddToSetControl instances for the lifetime of the page.
const lastUsedSet = ref(null); // { id, name }

const props = defineProps({
  backendUrl: { type: String, required: true },
  pictureIds: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: "Set" },
  includeDeletedMembers: { type: Boolean, default: false },
  placement: { type: String, default: "bottom" },
  forceDark: { type: Boolean, default: false },
});

const emit = defineEmits(["added"]);

const rootRef = ref(null);
const menuRef = ref(null);
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

const normalisedPictureIds = computed(() =>
  (Array.isArray(props.pictureIds) ? props.pictureIds : [])
    .map((id) => String(id))
    .filter(Boolean),
);

const normalisedIdsKey = computed(() => normalisedPictureIds.value.join("|"));
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
  const ids = normalisedPictureIds.value;
  if (!ids.length) return true;
  const members = setMembersById.value?.[set.id];
  return !members;
}

function getSetState(set) {
  const ids = normalisedPictureIds.value;
  const members = setMembersById.value?.[set.id];
  if (!ids.length || !members || members.size === 0) return "unchecked";
  const matched = ids.filter((id) => members.has(String(id))).length;
  if (matched === 0) return "unchecked";
  if (matched === ids.length) return "checked";
  return "partial";
}

function isLastUsedSet(set) {
  return Boolean(lastUsedSet.value && set?.id === lastUsedSet.value.id);
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
  if (props.placement === "right") positionFlyout();
  nextTick(() => searchInputRef.value?.focus());
  document.addEventListener("pointerdown", handleOutsideClick, true);
  if (props.placement === "right") {
    window.addEventListener("resize", positionFlyout, { passive: true });
  }
}

function closeMenu() {
  menuOpen.value = false;
  searchQuery.value = "";
  searchInputRef.value?.blur();
  document.removeEventListener("pointerdown", handleOutsideClick, true);
  window.removeEventListener("resize", positionFlyout);
}

function handleOutsideClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) return;
  if (rootRef.value?.contains(target)) return;
  if (menuRef.value?.contains(target)) return;
  closeMenu();
}

async function fetchSets(force = false) {
  if (!props.backendUrl || isLoading.value) return;
  const key = normalisedIdsKey.value;
  if (!force && key === lastFetchKey.value && sets.value.length) {
    return;
  }
  lastFetchKey.value = key;
  isLoading.value = true;
  try {
    const [setsRes] = await Promise.all([
      apiClient.get(resolveUrl("/picture_sets")),
      fetchSetMembers(),
    ]);
    const data = setsRes.data;
    const list = Array.isArray(data) ? data : [];
    const filtered = list.filter((set) => !set?.reference_character);
    sets.value = filtered;
    // Ensure every known set has an entry so isSetDisabled returns false.
    const current = setMembersById.value;
    filtered.forEach((set) => {
      if (!(set.id in current)) current[set.id] = new Set();
    });
    setMembersById.value = { ...current };
  } catch (e) {
    sets.value = [];
    setMembersById.value = {};
  } finally {
    isLoading.value = false;
  }
}

async function fetchSetMembers() {
  const ids = normalisedPictureIds.value;
  if (!props.backendUrl || !ids.length) {
    setMembersById.value = {};
    return;
  }
  try {
    const res = await apiClient.post(resolveUrl("/picture_sets/membership"), {
      picture_ids: ids,
      include_deleted: props.includeDeletedMembers ?? false,
    });
    const data = res.data ?? {};
    // data is { set_id: [picture_id, ...] }
    const next = {};
    Object.entries(data).forEach(([setId, memberIds]) => {
      next[Number(setId)] = new Set(
        (Array.isArray(memberIds) ? memberIds : []).map((id) => String(id)),
      );
    });
    setMembersById.value = next;
  } catch (e) {
    setMembersById.value = {};
  }
}

async function toggleSetMembership(set) {
  if (!set?.id) return;
  if (isSetDisabled(set)) return;
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const members = setMembersById.value?.[set.id];
  const setState = getSetState(set);
  const shouldRemove = setState === "checked";
  const idsToAdd = members ? ids.filter((id) => !members.has(String(id))) : ids;
  const idsToRemove = members
    ? ids.filter((id) => members.has(String(id)))
    : [];
  if (!shouldRemove && !idsToAdd.length) {
    statusMessage.value = "Already in set";
    return;
  }
  if (shouldRemove && !idsToRemove.length) {
    statusMessage.value = "Not in set";
    return;
  }
  statusMessage.value = shouldRemove ? "Removing..." : "Adding...";
  try {
    if (shouldRemove) {
      await Promise.all(
        idsToRemove.map((id) =>
          apiClient.delete(resolveUrl(`/picture_sets/${set.id}/members/${id}`)),
        ),
      );
      statusMessage.value = `Removed from ${set.name}`;
      emit("added", {
        setId: set.id,
        pictureIds: idsToRemove,
        action: "removed",
      });
      if (members) {
        idsToRemove.forEach((id) => members.delete(String(id)));
      }
      const removedSet = sets.value.find((s) => s.id === set.id);
      if (removedSet != null && removedSet.picture_count != null) {
        removedSet.picture_count = Math.max(
          0,
          removedSet.picture_count - idsToRemove.length,
        );
      }
    } else {
      await Promise.all(
        idsToAdd.map((id) =>
          apiClient.post(resolveUrl(`/picture_sets/${set.id}/members/${id}`)),
        ),
      );
      statusMessage.value = `Added to ${set.name}`;
      emit("added", {
        setId: set.id,
        pictureIds: idsToAdd,
        action: "added",
      });
      lastUsedSet.value = { id: set.id, name: set.name };
      if (members) {
        idsToAdd.forEach((id) => members.add(String(id)));
      }
      const addedSet = sets.value.find((s) => s.id === set.id);
      if (addedSet != null && addedSet.picture_count != null) {
        addedSet.picture_count = addedSet.picture_count + idsToAdd.length;
      }
    }
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    if (String(detail).includes("already in set")) {
      statusMessage.value = "Already in set";
    } else {
      statusMessage.value = shouldRemove ? "Failed to remove" : "Failed to add";
    }
  }
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => {
    statusMessage.value = "";
  }, 2000);
}

// Adds the current picture(s) directly to the last used set without opening
// the menu. Returns { success, setName } or { error }.
async function addToLastSet() {
  if (!lastUsedSet.value) return { error: "no-last-set" };
  const ids = normalisedPictureIds.value;
  if (!ids.length) return { error: "no-pictures" };
  const set = lastUsedSet.value;
  statusMessage.value = `Adding to ${set.name}...`;
  try {
    await Promise.all(
      ids.map((id) =>
        apiClient.post(resolveUrl(`/picture_sets/${set.id}/members/${id}`)),
      ),
    );
    statusMessage.value = `Added to ${set.name}`;
    // Update local membership cache if loaded.
    const members = setMembersById.value?.[set.id];
    if (members) ids.forEach((id) => members.add(String(id)));
    const cachedSet = sets.value.find((s) => s.id === set.id);
    if (cachedSet?.picture_count != null) cachedSet.picture_count += ids.length;
    emit("added", { setId: set.id, pictureIds: ids, action: "added" });
    if (statusTimer) clearTimeout(statusTimer);
    statusTimer = window.setTimeout(() => {
      statusMessage.value = "";
    }, 2000);
    return { success: true, setName: set.name };
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    if (String(detail).includes("already in set")) {
      statusMessage.value = `Already in ${set.name}`;
    } else {
      statusMessage.value = "Failed to add";
    }
    if (statusTimer) clearTimeout(statusTimer);
    statusTimer = window.setTimeout(() => {
      statusMessage.value = "";
    }, 2000);
    return { error: detail };
  }
}

onBeforeUnmount(() => {
  if (statusTimer) clearTimeout(statusTimer);
  document.removeEventListener("pointerdown", handleOutsideClick, true);
  window.removeEventListener("resize", positionFlyout);
});

watch(
  () => normalisedIdsKey.value,
  () => {
    if (menuOpen.value) {
      fetchSets();
    } else {
      setMembersById.value = {};
    }
  },
);

defineExpose({ addToLastSet, lastUsedSet });
</script>

<style scoped>
.add-to-set {
  position: relative;
  display: inline-flex;
}

.add-to-set-btn {
  background-color: rgba(var(--v-theme-surface), 0.85);
  color: rgb(var(--v-theme-on-surface));
  border: none;
  padding: 2px 8px;
  border-radius: 3px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 0.85rem;
  line-height: 1.4;
  cursor: pointer;
}

.add-to-set--force-dark .add-to-set-btn {
  background-color: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgba(var(--v-theme-on-dark-surface), 1);
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
  background-color: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));

  .add-to-set-item-check {
    color: rgba(var(--v-theme-on-surface), 0.7);
  }

  .add-to-set-item--checked .add-to-set-item-check {
    color: rgb(var(--v-theme-primary));
  }
  box-shadow: 0 10px 24px rgba(0, 0, 0, 0.35);
  opacity: 0;
  transform: translateY(-6px);
  pointer-events: none;
  transition:
    opacity 0.15s ease,
    transform 0.15s ease;
  z-index: 6;
}

.add-to-set-menu.force-dark {
  background-color: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgba(var(--v-theme-on-dark-surface), 1);

  .add-to-set-search {
    color: rgba(255, 255, 255, 0.55);
    background: rgba(255, 255, 255, 0.06);
  }

  .add-to-set-search input {
    color: #fff;
  }

  .add-to-set-item {
    color: rgba(var(--v-theme-on-dark-surface), 1);
  }

  .add-to-set-item:hover {
    background: rgba(255, 255, 255, 0.08);
  }

  .add-to-set-item-count {
    color: rgba(255, 255, 255, 0.6);
  }

  .add-to-set-item-shortcut {
    border-color: rgba(255, 255, 255, 0.32);
    color: rgba(255, 255, 255, 0.9);
    background: rgba(255, 255, 255, 0.08);
  }

  .add-to-set-empty {
    color: rgba(255, 255, 255, 0.6);
  }

  .add-to-set-status {
    color: rgba(255, 255, 255, 0.7);
  }
}

.add-to-set.open .add-to-set-menu,
.add-to-set-menu.open {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.add-to-set-search {
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

.add-to-set-search input {
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-surface));
  width: 100%;
  font-size: 0.78rem;
  outline: none;
}

.add-to-set-item {
  width: 100%;
  padding: 6px 8px;
  border-radius: 6px;
  font-size: 0.78rem;
  color: rgb(var(--v-theme-on-surface));
  background: transparent;
  border: none;
  text-align: left;
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 8px;
  cursor: pointer;
}

.add-to-set-item-name {
  flex: 1;
  min-width: 0;
}

.add-to-set-item-meta {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.add-to-set-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.add-to-set-item--disabled {
  opacity: 0.5;
  cursor: default;
  pointer-events: none;
}

.add-to-set-item-count {
  font-size: 0.7rem;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.add-to-set-item-shortcut {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.32);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 0.68rem;
  line-height: 1;
  color: rgba(var(--v-theme-on-surface), 0.9);
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.add-to-set-empty {
  padding: 6px 8px;
  font-size: 0.75rem;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.add-to-set-status {
  margin-top: 6px;
  padding: 6px 8px;
  font-size: 0.72rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.add-to-set-shortcut-status {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  max-width: 220px;
  padding: 6px 10px;
  border-radius: 8px;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: 0 8px 18px rgba(0, 0, 0, 0.35);
  font-size: 0.74rem;
  line-height: 1.2;
  z-index: 12;
  pointer-events: none;
}

.add-to-set--force-dark .add-to-set-shortcut-status {
  background: rgba(var(--v-theme-dark-surface), 0.92);
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

/* ── Flyout (right-placement) mode ──────────────────────────── */
.add-to-set--flyout {
  width: 100%;
  display: flex;
}

.add-to-set--flyout .add-to-set-btn {
  width: 100%;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  padding: 7px 14px;
  border-radius: 0;
  font-size: 13px;
  gap: 8px;
}

.add-to-set--flyout .add-to-set-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.add-to-set--flyout .add-to-set-chevron {
  margin-left: auto;
  opacity: 0.7;
}

.add-to-set--flyout .add-to-set-menu,
.add-to-set-menu.flyout {
  /* top/left set by JS positionFlyout() via inline style */
  transform: translateX(-6px);
  z-index: 2500;
}

.add-to-set--flyout.open .add-to-set-menu,
.add-to-set--flyout .add-to-set-menu.open,
.add-to-set-menu.flyout.open {
  transform: translateX(0);
}
</style>
