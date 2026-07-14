<template>
  <div
    ref="rootRef"
    class="ate"
    :class="{
      open: menuOpen,
      disabled,
      'ate--readonly': readonly,
      'ate--flyout': placement === 'right',
      'ate--flip': flyoutFlipped,
      'ate--force-dark': forceDark,
    }"
    @mouseenter="onFlyoutMouseenter"
    @mouseleave="onFlyoutMouseleave"
  >
    <button
      class="ate-btn"
      type="button"
      :disabled="disabled"
      :aria-expanded="menuOpen"
      aria-haspopup="true"
      :aria-label="config.ariaLabel"
      @click.stop="toggleMenu"
    >
      <v-icon size="18">{{ config.icon }}</v-icon>
      <span class="ate-label">{{ effectiveLabel }}</span>
      <v-icon size="16" class="ate-chevron">{{
        placement === "right" ? "mdi-chevron-right" : "mdi-chevron-down"
      }}</v-icon>
    </button>

    <Teleport :disabled="true" to="body">
      <div
        ref="menuRef"
        class="ate-menu"
        role="menu"
        :style="menuStyle"
        :class="{
          open: menuOpen,
          flyout: placement === 'right',
          'force-dark': forceDark,
        }"
      >
        <div class="ate-search">
          <v-icon size="14">mdi-magnify</v-icon>
          <input
            ref="searchInputRef"
            v-model="searchQuery"
            type="text"
            :placeholder="config.searchPlaceholder"
            @keydown.escape.stop.prevent="closeMenu"
          />
        </div>

        <!-- Only the item list scrolls; the search box above and status below stay
             pinned. The list's max height is sized to the viewport in sizeMenu(), so
             a long list (or a flyout opened low on screen) scrolls instead of
             running off the bottom. -->
        <div class="ate-list">
          <div v-if="isLoading" class="ate-empty">{{ config.loadingText }}</div>
          <div v-else-if="filteredItems.length === 0" class="ate-empty">
            {{ config.emptyText }}
          </div>
          <button
            v-for="item in filteredItems"
            :key="item.key"
            :class="[
              'ate-item',
              {
                'ate-item--disabled': isItemDisabled(item),
                'ate-item--checked': getItemState(item) === 'checked',
              },
            ]"
            type="button"
            role="menuitem"
            :disabled="isItemDisabled(item)"
            @click.stop="toggleItem(item)"
          >
            <v-icon size="16" class="ate-item-check">
              {{
                getItemState(item) === "checked"
                  ? "mdi-checkbox-marked"
                  : getItemState(item) === "partial"
                    ? "mdi-minus-box-outline"
                    : "mdi-checkbox-blank-outline"
              }}
            </v-icon>
            <span class="ate-item-name">{{ item.name }}</span>
            <span v-if="isSet" class="ate-item-meta">
              <span
                v-if="isLastUsedItem(item)"
                class="ate-item-shortcut"
                title="Press A to add to this set"
                >A</span
              >
            </span>
          </button>
        </div>

        <div v-if="statusMessage" class="ate-status">
          {{ statusMessage }}
        </div>
      </div>
    </Teleport>

    <div
      v-if="isSet && statusMessage && !menuOpen"
      class="ate-shortcut-status"
      role="status"
      aria-live="polite"
    >
      {{ statusMessage }}
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";

const props = defineProps({
  type: { type: String, required: true }, // 'set' | 'project' | 'character'
  backendUrl: { type: String, required: true },
  pictureIds: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false },
  label: { type: String, default: null },
  includeDeletedMembers: { type: Boolean, default: false },
  expandStacks: { type: Boolean, default: true },
  placement: { type: String, default: "bottom" },
  forceDark: { type: Boolean, default: false },
});

const emit = defineEmits(["added", "removed", "selected"]);

// --- Type-derived helpers ---
const isSet = computed(() => props.type === "set");
const isProject = computed(() => props.type === "project");
const isCharacter = computed(() => props.type === "character");

const config = computed(() => {
  if (isSet.value) {
    return {
      icon: "mdi-folder-plus",
      ariaLabel: "Set",
      searchPlaceholder: "Search sets...",
      loadingText: "Loading sets...",
      emptyText: "No sets found",
    };
  }
  if (isProject.value) {
    return {
      icon: "mdi-briefcase-edit-outline",
      ariaLabel: "Set project",
      searchPlaceholder: "Search projects...",
      loadingText: "Loading projects...",
      emptyText: "No projects found",
    };
  }
  return {
    icon: "mdi-account-plus",
    ariaLabel: "Set character",
    searchPlaceholder: "Search characters...",
    loadingText: "Loading characters...",
    emptyText: "No characters found",
  };
});

const effectiveLabel = computed(() => {
  if (props.label !== null) return props.label;
  if (isSet.value) return "Set";
  if (isProject.value) return "Project";
  return "Person";
});

// --- Core state ---
const rootRef = ref(null);
const menuRef = ref(null);
const searchInputRef = ref(null);
const menuOpen = ref(false);
const searchQuery = ref("");
const isLoading = ref(false);
const statusMessage = ref("");
const items = ref([]);
const membersById = ref({}); // key: item.key → Set<string> of picture IDs
const lastFetchKey = ref("");
let statusTimer = null;

// Set-only state
const lastUsedItem = ref(null); // { id, name }

// Character-only state
const picturesWithFaces = ref(new Set());

const flyoutFlipped = ref(false);
const flyoutClickedOpen = ref(false);

// Dynamic max-height so the menu never runs off the bottom of the screen: measure
// the menu's top in the viewport and cap its height to what's left below it. The
// inner .ate-list scrolls; the search box and status stay pinned. Recomputed on
// open and on resize/scroll (scroll uses capture, to catch a scrolling ancestor
// such as the sidebar when this is a flyout).
const menuStyle = ref({});
function sizeMenu() {
  nextTick(() => {
    const el = menuRef.value;
    if (!el) return;
    const top = el.getBoundingClientRect().top;
    const avail = window.innerHeight - top - 12;
    menuStyle.value = { maxHeight: `${Math.max(140, Math.round(avail))}px` };
  });
}

// --- URL helpers ---
const baseUrl = computed(() =>
  props.backendUrl ? String(props.backendUrl).replace(/\/$/, "") : "",
);

function resolveUrl(path) {
  return baseUrl.value ? `${baseUrl.value}${path}` : path;
}

// --- Normalised picture IDs ---
const normalisedPictureIds = computed(() =>
  (Array.isArray(props.pictureIds) ? props.pictureIds : [])
    .map((id) => String(id))
    .filter(Boolean),
);

const normalisedIdsKey = computed(() => normalisedPictureIds.value.join("|"));

// --- Filtered items list ---
const filteredItems = computed(() => {
  const needle = searchQuery.value.trim().toLowerCase();
  if (!needle) return items.value;
  return items.value.filter((item) =>
    String(item.name || "")
      .toLowerCase()
      .includes(needle),
  );
});

// --- Item state helpers ---
function isItemDisabled(item) {
  if (props.readonly) return true;
  if (!normalisedPictureIds.value.length) return true;
  if (isCharacter.value) return false;
  return !membersById.value?.[item.key];
}

function getItemState(item) {
  const ids = normalisedPictureIds.value;
  if (!ids.length) return "unchecked";
  const members = membersById.value?.[item.key];
  if (!members || members.size === 0) return "unchecked";

  const relevantIds = isCharacter.value
    ? ids.filter((id) => picturesWithFaces.value.has(String(id)))
    : ids;
  if (!relevantIds.length) return "unchecked";

  const matched = relevantIds.filter((id) => members.has(String(id))).length;
  if (matched === 0) return "unchecked";
  if (matched === relevantIds.length) return "checked";
  return "partial";
}

function isLastUsedItem(item) {
  return Boolean(
    isSet.value && lastUsedItem.value && item?.id === lastUsedItem.value.id,
  );
}

// --- Menu open/close ---
function toggleMenu() {
  if (props.disabled) return;
  if (props.placement === "right") {
    // For flyout placement, click/keyboard can open (or close a click-opened menu).
    // Hover-opened menus are not toggled by click to avoid accidental dismissal.
    if (menuOpen.value && flyoutClickedOpen.value) {
      flyoutClickedOpen.value = false;
      closeMenu();
    } else if (!menuOpen.value) {
      flyoutClickedOpen.value = true;
      openMenu();
      document.addEventListener("pointerdown", handleOutsideClick, true);
    }
    return;
  }
  menuOpen.value = !menuOpen.value;
  if (menuOpen.value) {
    openMenu();
  } else {
    closeMenu();
  }
}

function openMenu() {
  menuOpen.value = true;
  fetchItems(true);
  sizeMenu();
  window.addEventListener("resize", sizeMenu);
  window.addEventListener("scroll", sizeMenu, true);
  if (props.placement !== "right") {
    nextTick(() => searchInputRef.value?.focus());
    document.addEventListener("pointerdown", handleOutsideClick, true);
  }
}

function closeMenu() {
  menuOpen.value = false;
  searchQuery.value = "";
  window.removeEventListener("resize", sizeMenu);
  window.removeEventListener("scroll", sizeMenu, true);
  if (flyoutClickedOpen.value) {
    document.removeEventListener("pointerdown", handleOutsideClick, true);
    flyoutClickedOpen.value = false;
  }
  if (props.placement !== "right") {
    searchInputRef.value?.blur();
    document.removeEventListener("pointerdown", handleOutsideClick, true);
  }
}

function onFlyoutMouseenter() {
  if (props.placement !== "right" || props.disabled) return;
  if (rootRef.value) {
    const rect = rootRef.value.getBoundingClientRect();
    flyoutFlipped.value = rect.right + 185 > window.innerWidth - 8;
  }
  if (menuOpen.value) return;
  openMenu();
}

function onFlyoutMouseleave() {
  if (props.placement !== "right") return;
  if (flyoutClickedOpen.value) return; // user clicked to open — keep it open
  closeMenu();
}

function handleOutsideClick(event) {
  const target = event?.target;
  if (!target || !(target instanceof HTMLElement)) return;
  if (rootRef.value?.contains(target)) return;
  if (menuRef.value?.contains(target)) return;
  closeMenu();
}

// --- Fetch ---
async function fetchItems(force = false) {
  if (!props.backendUrl || isLoading.value) return;
  const key = normalisedIdsKey.value;
  if (!force && key === lastFetchKey.value && items.value.length) return;
  lastFetchKey.value = key;
  isLoading.value = true;
  try {
    if (isSet.value) await fetchSetData();
    else if (isProject.value) await fetchProjectData();
    else await fetchCharacterData();
  } catch {
    items.value = [];
    membersById.value = {};
  } finally {
    isLoading.value = false;
  }
}

async function fetchSetData() {
  const [listRes] = await Promise.all([
    apiClient.get(resolveUrl("/picture_sets")),
    fetchSetMembers(),
  ]);
  const list = (Array.isArray(listRes.data) ? listRes.data : []).filter(
    (s) => !s?.reference_character,
  );
  items.value = list.map((s) => ({
    id: s.id,
    key: String(s.id),
    name: s.name,
    count: s.picture_count ?? null,
  }));
  // Ensure every known set has an entry so isItemDisabled returns false.
  const current = membersById.value;
  list.forEach((s) => {
    if (!(String(s.id) in current)) current[String(s.id)] = new Set();
  });
  membersById.value = { ...current };
}

async function fetchSetMembers() {
  const ids = normalisedPictureIds.value;
  if (!props.backendUrl || !ids.length) {
    membersById.value = {};
    return;
  }
  try {
    const res = await apiClient.post(resolveUrl("/picture_sets/membership"), {
      picture_ids: ids,
      include_deleted: props.includeDeletedMembers ?? false,
    });
    const data = res.data ?? {};
    const next = {};
    Object.entries(data).forEach(([setId, memberIds]) => {
      next[String(setId)] = new Set(
        (Array.isArray(memberIds) ? memberIds : []).map(String),
      );
    });
    membersById.value = next;
  } catch {
    membersById.value = {};
  }
}

async function fetchProjectData() {
  const [listRes] = await Promise.all([
    apiClient.get(resolveUrl("/projects")),
    fetchProjectMembers(),
  ]);
  const data = Array.isArray(listRes.data) ? listRes.data : [];
  const projs = data
    .map((row) => ({
      id: Number(row?.id),
      name: String(row?.name || "").trim() || `Project ${row?.id}`,
    }))
    .filter((row) => Number.isFinite(row.id) && row.id > 0)
    .sort((a, b) => a.name.localeCompare(b.name));
  items.value = projs.map((p) => ({
    id: p.id,
    key: `project-${p.id}`,
    name: p.name,
    count: null,
  }));
  // Ensure every known project has an entry so isItemDisabled returns false.
  const current = membersById.value;
  if (!("unassigned" in current)) current["unassigned"] = new Set();
  projs.forEach((p) => {
    const k = `project-${p.id}`;
    if (!(k in current)) current[k] = new Set();
  });
  membersById.value = { ...current };
}

async function fetchProjectMembers() {
  const ids = normalisedPictureIds.value;
  if (!props.backendUrl || !ids.length) {
    membersById.value = {};
    return;
  }
  try {
    const res = await apiClient.post(resolveUrl("/projects/membership"), {
      picture_ids: ids,
    });
    const data = res.data ?? {};
    const assignments = data.project_assignments ?? {};
    const unassignedIds = data.unassigned_picture_ids ?? [];
    const next = { unassigned: new Set(unassignedIds.map(String)) };
    Object.entries(assignments).forEach(([projectId, picIds]) => {
      next[`project-${projectId}`] = new Set((picIds ?? []).map(String));
    });
    membersById.value = next;
  } catch {
    membersById.value = {};
  }
}

async function fetchCharacterData() {
  const [listRes] = await Promise.all([
    apiClient.get(resolveUrl("/characters")),
    fetchCharacterMembers(),
  ]);
  const list = Array.isArray(listRes.data) ? listRes.data : [];
  items.value = [...list]
    .sort((a, b) => String(a?.name || "").localeCompare(String(b?.name || "")))
    .map((c) => ({ id: c.id, key: String(c.id), name: c.name, count: null }));
}

async function fetchCharacterMembers() {
  const ids = normalisedPictureIds.value;
  if (!props.backendUrl || !ids.length) {
    membersById.value = {};
    picturesWithFaces.value = new Set();
    return;
  }
  try {
    const res = await apiClient.post(resolveUrl("/characters/membership"), {
      picture_ids: ids,
    });
    const data = res.data ?? {};
    picturesWithFaces.value = new Set(
      (data.pictures_with_faces ?? []).map(String),
    );
    const next = {};
    Object.entries(data.character_assignments ?? {}).forEach(
      ([charId, picIds]) => {
        next[String(charId)] = new Set(picIds.map(String));
      },
    );
    membersById.value = next;
  } catch {
    membersById.value = {};
    picturesWithFaces.value = new Set();
  }
}

// --- Toggle dispatch ---
async function toggleItem(item) {
  if (isItemDisabled(item)) return;
  if (isSet.value) await toggleSet(item);
  else if (isProject.value) toggleProject(item);
  else await toggleCharacter(item);
}

async function toggleSet(item) {
  if (!item?.id) return;
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const members = membersById.value?.[item.key];
  const state = getItemState(item);
  const shouldRemove = state === "checked";
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
          apiClient.delete(
            resolveUrl(`/picture_sets/${item.id}/members/${id}`),
          ),
        ),
      );
      statusMessage.value = `Removed from ${item.name}`;
      emit("added", {
        setId: item.id,
        pictureIds: idsToRemove,
        action: "removed",
      });
      if (members) idsToRemove.forEach((id) => members.delete(String(id)));
    } else {
      await Promise.all(
        idsToAdd.map((id) =>
          apiClient.post(resolveUrl(`/picture_sets/${item.id}/members/${id}`)),
        ),
      );
      statusMessage.value = `Added to ${item.name}`;
      emit("added", { setId: item.id, pictureIds: idsToAdd, action: "added" });
      lastUsedItem.value = { id: item.id, name: item.name };
      if (members) idsToAdd.forEach((id) => members.add(String(id)));
    }
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    statusMessage.value = String(detail).includes("already in set")
      ? "Already in set"
      : shouldRemove
        ? "Failed to remove"
        : "Failed to add";
  }
  scheduleStatusClear();
}

function toggleProject(item) {
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const state = getItemState(item);
  if (item.id == null && state === "checked") {
    statusMessage.value = "Already unassigned";
    return;
  }
  const shouldRemove = item.id != null && state === "checked";
  const action = shouldRemove ? "removed" : "added";
  statusMessage.value = shouldRemove ? "Removing..." : "Adding...";
  emit("selected", {
    projectId: item.id ?? null,
    projectName: item.name,
    action,
    pictureIds: ids,
    expandStacks: props.expandStacks,
  });
  applyOptimisticProjectUpdate(item, action, ids);
  statusMessage.value =
    action === "removed"
      ? `Removed from ${item.name}`
      : item.id == null
        ? "Set to unassigned"
        : `Added to ${item.name}`;
  scheduleStatusClear(1600);
}

function applyOptimisticProjectUpdate(item, action, ids) {
  const next = { ...membersById.value };
  const unassignedKey = "unassigned";
  if (!(next[unassignedKey] instanceof Set)) next[unassignedKey] = new Set();
  for (const proj of items.value) {
    if (!(next[proj.key] instanceof Set)) next[proj.key] = new Set();
  }

  if (item.id == null && action === "added") {
    for (const proj of items.value) {
      ids.forEach((id) => next[proj.key].delete(String(id)));
    }
    ids.forEach((id) => next[unassignedKey].add(String(id)));
    membersById.value = next;
    return;
  }

  if (!(next[item.key] instanceof Set)) next[item.key] = new Set();

  if (action === "removed") {
    ids.forEach((id) => next[item.key].delete(String(id)));
    for (const id of ids) {
      const idStr = String(id);
      const stillAssigned = items.value.some(
        (proj) => next[proj.key] instanceof Set && next[proj.key].has(idStr),
      );
      if (!stillAssigned) next[unassignedKey].add(idStr);
    }
  } else {
    ids.forEach((id) => {
      next[item.key].add(String(id));
      next[unassignedKey].delete(String(id));
    });
  }
  membersById.value = next;
}

async function toggleCharacter(item) {
  if (!item?.id) return;
  const ids = normalisedPictureIds.value;
  if (!ids.length) return;
  const state = getItemState(item);

  if (state === "checked") {
    statusMessage.value = "Removing...";
    try {
      await apiClient.delete(resolveUrl(`/characters/${item.id}/faces`), {
        data: { picture_ids: ids },
      });
      statusMessage.value = `Removed from ${item.name}`;
      emit("removed", { characterId: item.id, pictureIds: ids });
      const members = membersById.value?.[item.key];
      if (members) ids.forEach((id) => members.delete(String(id)));
      closeMenu();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || String(e);
      statusMessage.value = detail ? String(detail) : "Failed to remove";
    }
  } else {
    const members = membersById.value?.[item.key];
    const idsToAdd = members
      ? ids.filter((id) => !members.has(String(id)))
      : ids;
    if (!idsToAdd.length) return;
    statusMessage.value = "Assigning...";
    try {
      await apiClient.post(resolveUrl(`/characters/${item.id}/faces`), {
        picture_ids: idsToAdd,
      });
      statusMessage.value = `Assigned to ${item.name}`;
      emit("added", { characterId: item.id, pictureIds: ids });
      // Only update the optimistic member cache for pictures that actually have
      // faces — faceless pictures can't be reflected in the membership state.
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
  scheduleStatusClear();
}

function scheduleStatusClear(delay = 2000) {
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = window.setTimeout(() => {
    statusMessage.value = "";
  }, delay);
}

// Set-only: add to last used set without opening menu
async function addToLastSet() {
  if (!lastUsedItem.value) return { error: "no-last-set" };
  const ids = normalisedPictureIds.value;
  if (!ids.length) return { error: "no-pictures" };
  const item = lastUsedItem.value;
  statusMessage.value = `Adding to ${item.name}...`;
  try {
    await Promise.all(
      ids.map((id) =>
        apiClient.post(resolveUrl(`/picture_sets/${item.id}/members/${id}`)),
      ),
    );
    statusMessage.value = `Added to ${item.name}`;
    const members = membersById.value?.[String(item.id)];
    if (members) ids.forEach((id) => members.add(String(id)));
    emit("added", { setId: item.id, pictureIds: ids, action: "added" });
    scheduleStatusClear();
    return { success: true, setName: item.name };
  } catch (e) {
    const detail = e?.response?.data?.detail || e?.message || String(e);
    statusMessage.value = String(detail).includes("already in set")
      ? `Already in ${item.name}`
      : "Failed to add";
    scheduleStatusClear();
    return { error: detail };
  }
}

onBeforeUnmount(() => {
  if (statusTimer) clearTimeout(statusTimer);
  document.removeEventListener("pointerdown", handleOutsideClick, true);
  window.removeEventListener("resize", sizeMenu);
  window.removeEventListener("scroll", sizeMenu, true);
});

watch(
  () => normalisedIdsKey.value,
  () => {
    if (menuOpen.value) {
      fetchItems(true);
    } else {
      membersById.value = {};
      if (isCharacter.value) picturesWithFaces.value = new Set();
    }
  },
);

// Expose for external keyboard shortcut access (set type only)
defineExpose({ addToLastSet, lastUsedSet: lastUsedItem, closeMenu });
</script>

<style scoped>
.ate {
  position: relative;
  display: inline-flex;
}

.ate-btn {
  background-color: rgba(var(--v-theme-surface), 0.85);
  color: rgb(var(--v-theme-on-surface));
  border: none;
  padding: var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  line-height: 1.4;
  cursor: pointer;
}

.ate--force-dark .ate-btn {
  background-color: rgba(var(--v-theme-dark-surface), 0.6);
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

.ate-btn:disabled {
  opacity: 0.5;
  cursor: default;
}

.ate--readonly .ate-btn {
  opacity: 0.5;
}

.ate-btn:hover {
  filter: brightness(1.75);
  border: none;
}

.ate-label {
  white-space: nowrap;
}

.ate-menu {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  min-width: 220px;
  /* Flex column so the search box and status stay pinned while only the item list
     scrolls. The CSS cap is a fallback; sizeMenu() sets an exact viewport-aware
     max-height inline. overflow:hidden keeps the scroll area within the rounded card. */
  display: flex;
  flex-direction: column;
  max-height: 72vh;
  overflow: hidden;
  padding: var(--space-3);
  border-radius: var(--radius-md);
  background-color: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-2);
  opacity: 0;
  transform: translateY(-6px);
  pointer-events: none;
  transition:
    opacity var(--dur-1) var(--ease-standard),
    transform var(--dur-1) var(--ease-standard);
  z-index: 6;
}

/* The scrolling region: only the item list scrolls when the menu is height-capped.
   min-height:0 lets it shrink inside the flex column so overflow actually engages. */
.ate-list {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  overscroll-behavior: contain;
}

.ate-menu.force-dark {
  background-color: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

.ate-menu.force-dark .ate-search {
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  background: rgba(var(--v-theme-on-dark-surface), 0.06);
}

.ate-menu.force-dark .ate-search input {
  color: rgb(var(--v-theme-on-dark-surface));
}

.ate-menu.force-dark .ate-item {
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

.ate-menu.force-dark .ate-item:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}

.ate-menu.force-dark .ate-item-count {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.ate-menu.force-dark .ate-item-shortcut {
  border-color: rgba(var(--v-theme-on-dark-surface), 0.32);
  color: rgba(var(--v-theme-on-dark-surface), 0.9);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
}

.ate-menu.force-dark .ate-empty {
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.ate-menu.force-dark .ate-status {
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}

.ate.open .ate-menu,
.ate-menu.open {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}

.ate-search {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.06);
  margin-bottom: var(--space-3);
}

.ate-search input {
  background: transparent;
  border: none;
  color: rgb(var(--v-theme-on-surface));
  width: 100%;
  font-size: var(--text-xs);
  outline: none;
}

.ate-item {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface));
  background: transparent;
  border: none;
  text-align: left;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  cursor: pointer;
}

.ate-item-check {
  color: rgba(var(--v-theme-on-surface), 0.7);
  flex-shrink: 0;
}

.ate-item--checked .ate-item-check {
  color: rgb(var(--v-theme-primary));
}

.ate-item-name {
  flex: 1;
  min-width: 0;
}

.ate-item-meta {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.ate-item:hover {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.ate-item--disabled {
  opacity: 0.5;
  cursor: default;
  pointer-events: none;
}

.ate-item-count {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.ate-item-shortcut {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.32);
  border-radius: var(--radius-sm);
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-2xs);
  line-height: 1;
  color: rgba(var(--v-theme-on-surface), 0.9);
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.ate-empty {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.ate-status {
  flex: 0 0 auto;
  margin-top: var(--space-2);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.ate-shortcut-status {
  position: absolute;
  top: calc(100% + 8px);
  left: 0;
  max-width: 220px;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-3);
  font-size: var(--text-xs);
  line-height: 1.2;
  z-index: 12;
  pointer-events: none;
}

.ate--force-dark .ate-shortcut-status {
  background: rgba(var(--v-theme-dark-surface), 0.92);
  color: rgba(var(--v-theme-on-dark-surface), 1);
}

/* ── Flyout (right-placement) mode ──────────────────────────── */
.ate--flyout {
  width: 100%;
  display: flex;
}

.ate--flyout .ate-btn {
  width: 100%;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  padding: var(--space-2) var(--space-5);
  border-radius: 0;
  font-size: var(--text-sm);
  gap: var(--space-3);
}

.ate--flyout .ate-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.ate--flyout .ate-chevron {
  margin-left: auto;
  opacity: 0.7;
}

.ate--flyout .ate-menu,
.ate-menu.flyout {
  position: absolute;
  left: 100%;
  top: 0;
  min-width: 185px;
  max-width: 185px;
  padding: var(--space-2) 0;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.14);
  box-shadow: var(--elevation-3);
  transform: translateX(-4px);
  z-index: 2500;
}

/* Flip flyout menu leftward when near the right screen edge */
.ate--flip.ate--flyout .ate-menu,
.ate--flip .ate-menu.flyout {
  left: auto;
  right: 100%;
  transform: translateX(4px);
}

.ate--flip.ate--flyout.open .ate-menu,
.ate--flip.ate--flyout .ate-menu.open,
.ate--flip .ate-menu.flyout.open {
  transform: translateX(0);
}

.ate--flyout.open .ate-menu,
.ate--flyout .ate-menu.open,
.ate-menu.flyout.open {
  transform: translateX(0);
}

.ate--flyout .ate-item {
  border-radius: 0;
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
}

.ate--flyout .ate-item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ate--flyout .ate-search {
  margin: var(--space-2) var(--space-2) var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
}

.ate--flyout .ate-empty,
.ate--flyout .ate-status {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
}
</style>
