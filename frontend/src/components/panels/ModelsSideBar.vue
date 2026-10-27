<template>
  <!-- The Models workspace's own sidebar (#2168): the model-folder slot, the
       places, the Type and Base model rows, Training, and the amber Add band.
       It replaces the library's sidebar on /models, as the shelf replaces the
       grid: nothing in the library's navigation is about files on this disk. -->
  <aside
    class="msb"
    :style="{ width: `${width}px` }"
    aria-label="Model navigation"
    data-testid="models-sidebar"
  >
    <!-- The slot the library sidebar gives the project: which model folder,
         drawn like the title bar's library picker. -->
    <v-menu v-model="folderMenuOpen" location="bottom start" :offset="4">
      <template #activator="{ props: menuProps }">
        <button
          v-bind="withRef(menuProps, (el) => (folderTrigger = el))"
          type="button"
          class="msb-slot"
          :class="{ 'msb-slot--open': folderMenuOpen }"
          aria-haspopup="menu"
          :aria-label="slotLabel"
          data-testid="models-folder-slot"
        >
          <span class="msb-slot-mark" aria-hidden="true"
            ><v-icon size="14">mdi-folder-outline</v-icon></span
          >
          <span class="msb-slot-id">
            <span class="msb-slot-name">{{ scoped.name }}</span>
            <span
              v-if="scoped.drive"
              class="msb-bar"
              :data-state="scoped.drive.state"
              aria-hidden="true"
              ><span :style="{ width: `${scoped.drive.used}%` }"></span
            ></span>
          </span>
          <span class="msb-count">{{ scoped.size }}</span>
          <v-icon size="14" class="msb-slot-chev" aria-hidden="true"
            >mdi-unfold-more-horizontal</v-icon
          >
        </button>
      </template>
      <div
        class="ctx-menu msb-menu"
        role="menu"
        aria-label="Model folders"
        tabindex="-1"
        data-testid="models-folder-menu"
        @keydown="onMenuKeydown"
      >
        <span class="section-label msb-menu-label" role="none"
          >Limit to folder</span
        >
        <button
          v-for="option in folderOptions"
          :key="option.id ?? 'all'"
          type="button"
          class="ctx-item msb-folder"
          role="menuitemradio"
          :aria-checked="option.checked ? 'true' : 'false'"
          :aria-label="option.label"
          :data-folder-id="option.id ?? 'all'"
          @click="pickFolder(option.id)"
        >
          <v-icon v-if="option.checked" class="ctx-icon" aria-hidden="true"
            >mdi-check</v-icon
          >
          <span v-else class="ctx-icon" aria-hidden="true"></span>
          <span class="ctx-label-text msb-folder-id">
            <span class="msb-folder-name">{{ option.name }}</span>
            <span class="msb-folder-path">{{ option.path }}</span>
          </span>
          <span class="ctx-meta msb-folder-meta">
            {{ option.size }}
            <template v-if="option.drive">
              ·
              <span class="msb-free" :data-state="option.drive.state"
                >{{ formatModelSize(option.drive.free) }} free</span
              >
              <span
                class="msb-bar msb-bar--mini"
                :data-state="option.drive.state"
                aria-hidden="true"
                ><span :style="{ width: `${option.drive.used}%` }"></span
              ></span>
            </template>
          </span>
        </button>
        <div class="ctx-sep" role="separator"></div>
        <button
          type="button"
          class="ctx-item"
          role="menuitem"
          @click="ask('add-folder')"
        >
          <span class="ctx-icon" aria-hidden="true"></span>
          <span class="ctx-label-text">Add folder…</span>
        </button>
        <button
          type="button"
          class="ctx-item"
          role="menuitem"
          @click="ask('manage-folders')"
        >
          <span class="ctx-icon" aria-hidden="true"></span>
          <span class="ctx-label-text">Manage model folders…</span>
        </button>
      </div>
    </v-menu>

    <nav class="msb-scroll" aria-label="Model places">
      <button
        v-for="row in placeRows"
        :key="row.id"
        type="button"
        class="ps-row msb-row"
        :class="{ active: row.active, 'msb-row--off': row.disabled }"
        :aria-current="row.active ? 'page' : undefined"
        :aria-disabled="row.disabled ? 'true' : undefined"
        :data-place="row.id"
        @click="pickPlace(row)"
      >
        <Tooltip v-if="row.reason" :text="row.reason" activator="parent" />
        <v-icon size="16" class="ps-row-glyph" aria-hidden="true">{{
          row.icon
        }}</v-icon>
        <span class="msb-label">{{ row.label }}</span>
        <span class="msb-count">{{ row.count }}</span>
      </button>

      <h3 class="section-label msb-head">Type</h3>
      <button
        v-for="row in typeRows"
        :key="row.key"
        type="button"
        class="ps-row msb-row"
        :class="{ active: row.active }"
        :aria-current="row.active ? 'page' : undefined"
        :aria-pressed="row.active ? 'true' : 'false'"
        :data-type="row.key"
        @click="store.setSidebarFilter('type', row.key)"
      >
        <TypeMark class="ps-row-glyph" :mark="row.mark" :tint="row.tint" />
        <span class="msb-label">{{ row.label }}</span>
        <span class="msb-count">{{ row.count }}</span>
      </button>

      <template v-if="baseRows.length">
        <h3 class="section-label msb-head">Base model</h3>
        <button
          v-for="row in baseRows"
          :key="row.key"
          type="button"
          class="ps-row msb-row"
          :class="{ active: row.active }"
          :aria-current="row.active ? 'page' : undefined"
          :aria-pressed="row.active ? 'true' : 'false'"
          :data-base="row.key"
          @click="store.setSidebarFilter('base', row.key)"
        >
          <v-icon size="16" class="ps-row-glyph" aria-hidden="true"
            >mdi-layers-outline</v-icon
          >
          <span class="msb-label">{{ row.label }}</span>
          <span class="msb-count">{{ row.count }}</span>
        </button>
      </template>

      <!-- Training is a place of its own; issue #2169 builds it out. -->
      <h3 class="section-label msb-head">Training</h3>
      <button
        type="button"
        class="ps-row msb-row"
        :class="{ active: onRuns }"
        :aria-current="onRuns ? 'page' : undefined"
        data-place="runs"
        @click="openRuns"
      >
        <v-icon size="16" class="ps-row-glyph" aria-hidden="true"
          >mdi-chart-line-variant</v-icon
        >
        <span class="msb-label">Training runs</span>
        <span
          v-if="trainingNow"
          class="msb-live"
          data-testid="models-sidebar-live-dot"
          aria-hidden="true"
        ></span>
        <span v-if="trainingNow" class="visually-hidden">, training now</span>
      </button>
    </nav>

    <!-- The one amber action on this screen. -->
    <div class="msb-band">
      <v-menu v-model="addMenuOpen" location="top start" :offset="4">
        <template #activator="{ props: menuProps }">
          <AppButton
            v-bind="withRef(menuProps, (el) => (addTrigger = el))"
            variant="primary"
            block
            icon-left="plus"
            aria-haspopup="menu"
            data-testid="models-add"
            >Add models<v-icon size="16" aria-hidden="true"
              >mdi-menu-down</v-icon
            ></AppButton
          >
        </template>
        <div
          class="ctx-menu"
          role="menu"
          aria-label="Add models"
          tabindex="-1"
          @keydown="onMenuKeydown"
        >
          <button
            type="button"
            class="ctx-item"
            role="menuitem"
            @click="ask('add-folder', addTrigger)"
          >
            <v-icon class="ctx-icon">mdi-folder-plus-outline</v-icon>
            <span class="ctx-label-text">Add folder…</span>
          </button>
          <button
            type="button"
            class="ctx-item"
            role="menuitem"
            @click="ask('add-file', addTrigger)"
          >
            <v-icon class="ctx-icon">mdi-file-plus-outline</v-icon>
            <span class="ctx-label-text">Add file…</span>
          </button>
          <!-- Only while unset: ai-toolkit writes every run under one root, so
               setting it is a once-ever act (as it was on the toolbar's Add). -->
          <template v-if="!foldersStore.sourceFolder">
            <div class="ctx-sep" role="separator"></div>
            <button
              type="button"
              class="ctx-item"
              role="menuitem"
              @click="ask('set-source', addTrigger)"
            >
              <AiToolkitIcon class="ctx-icon" />
              <span class="ctx-label-text">Set ai-toolkit output folder…</span>
            </button>
          </template>
        </div>
      </v-menu>
    </div>
  </aside>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon, VMenu } from "vuetify/components";

import { BASE_MODEL_UNASSIGNED } from "../../api/modelShelf";
import { DOWNLOADS_KIND, SOURCE_KIND } from "../../api/modelFolders";
import {
  basename,
  useModelFoldersStore,
} from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { driveFullness } from "../../utils/libraryChrome";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { formatModelSize, SHELF_TYPES } from "../../utils/modelShelf";
import { withRef } from "../../utils/withRef.js";
import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import TypeMark from "../widgets/TypeMark.vue";

const store = useModelShelfStore();
const foldersStore = useModelFoldersStore();
const tasksStore = useTasksStore();
const userPrefsStore = useUserPrefsStore();
const route = useRoute();
const router = useRouter();

// The library sidebar's width, so switching workspace does not move the edge.
const width = computed(() => userPrefsStore.sidebarWidth ?? 240);

const folderMenuOpen = ref(false);
const addMenuOpen = ref(false);
// The triggers outlive their menus, so a dialog opened from one hands focus
// back to it. Not reactive: only `ask` reads them.
let folderTrigger = null;
let addTrigger = null;

onMounted(() => {
  // The counts need the sets ("In no set yet") and the folders the slot draws.
  store.loadWorkflowSets();
  foldersStore.refresh({ quiet: true });
  foldersStore.refreshDevices();
});

const onRuns = computed(() => route.name === "models-runs");

const trainingNow = computed(() =>
  (tasksStore.resources?.leases ?? []).some(
    (lease) => lease.tenant === "aitoolkit",
  ),
);

// ── The folder slot ──────────────────────────────────────────────────────

/** The folders the shelf catalogues: not the ai-toolkit root nor Downloads. */
const shelfFolders = computed(() =>
  foldersStore.folders.filter(
    (folder) => folder.kind !== SOURCE_KIND && folder.kind !== DOWNLOADS_KIND,
  ),
);

function folderName(folder) {
  return folder.tool || basename(folder.path) || folder.path;
}

function folderEntry(folder) {
  const drive = driveFullness(
    foldersStore.deviceByFolderId.get(Number(folder.id)),
  );
  const size = formatModelSize(Number(folder.present_bytes) || 0);
  return {
    id: Number(folder.id),
    name: folderName(folder),
    path: folder.path,
    size,
    drive,
    checked: store.folderScope === Number(folder.id),
    label: [
      folderName(folder),
      size,
      drive
        ? `${formatModelSize(drive.free)} free, drive ${drive.used}% full`
        : "",
    ]
      .filter(Boolean)
      .join(", "),
  };
}

const allFoldersEntry = computed(() => {
  const bytes = shelfFolders.value.reduce(
    (sum, folder) => sum + (Number(folder.present_bytes) || 0),
    0,
  );
  const drives = new Set(
    shelfFolders.value
      .map((folder) => foldersStore.deviceByFolderId.get(Number(folder.id)))
      .filter((device) => device?.device_id != null)
      .map((device) => device.device_id),
  ).size;
  const n = shelfFolders.value.length;
  const path = [
    n === 1 ? "1 folder" : `${n} folders`,
    drives ? (drives === 1 ? "1 drive" : `${drives} drives`) : "",
  ]
    .filter(Boolean)
    .join(", ");
  const size = formatModelSize(bytes);
  return {
    id: null,
    name: "All folders",
    path,
    size,
    drive: null,
    checked: store.folderScope == null,
    label: `All folders, ${path}, ${size}`,
  };
});

const folderOptions = computed(() => [
  allFoldersEntry.value,
  ...shelfFolders.value.map(folderEntry),
]);

/** What the slot names: the scoped folder, else All folders. */
const scoped = computed(
  () =>
    folderOptions.value.find((option) => option.checked) ??
    allFoldersEntry.value,
);

const slotLabel = computed(() => {
  const parts = [`Model folder: ${scoped.value.name}, ${scoped.value.size}`];
  const drive = scoped.value.drive;
  if (drive) {
    parts.push(
      `Its drive is ${drive.used}% full, ${drive.words}: ${formatModelSize(drive.free)} free of ${formatModelSize(drive.total)}`,
    );
  }
  parts.push("Choose a folder to limit the list to");
  return parts.join(". ");
});

// A scoped folder that is no longer registered (forgotten in Manage model
// folders) takes the scope with it, or the list would sit empty under a slot
// naming All folders.
watch(
  () => [foldersStore.loaded, shelfFolders.value, store.folderScope],
  ([loaded, folders, scope]) => {
    if (!loaded || scope == null) return;
    if (!folders.some((folder) => Number(folder.id) === scope)) {
      store.setFolderScope(null);
    }
  },
  { immediate: true },
);

function pickFolder(id) {
  folderMenuOpen.value = false;
  // The picked row leaves with the menu: hand focus back to the slot.
  folderTrigger?.focus?.();
  store.setFolderScope(id);
  if (id != null && onRuns.value) router.push({ name: "models" });
}

/**
 * Ask the shelf for one of its dialogs. The shelf owns them, and their
 * confirmations, because the toolbar's Add and Model folders used to.
 */
function ask(what, el = folderTrigger) {
  folderMenuOpen.value = false;
  addMenuOpen.value = false;
  const target = el?.el ?? el ?? null;
  // Back on the button first, so the dialog that opens next returns there.
  target?.focus?.();
  store.requestShell(what, target);
}

// ── Places, Type, Base model ─────────────────────────────────────────────

const PLACES = [
  { id: "sets", label: "Sets", icon: "mdi-view-grid-outline" },
  { id: "all", label: "All models", icon: "mdi-format-list-bulleted" },
  { id: "favourites", label: "Favourites", icon: "mdi-heart-outline" },
  { id: "recent", label: "Recently added", icon: "mdi-clock-outline" },
  { id: "loose", label: "In no set yet", icon: "mdi-plus-box-outline" },
];

const count = (n) => (n == null ? "" : n.toLocaleString());

const placeRows = computed(() =>
  PLACES.map((place) => {
    const n = store.placeCounts[place.id];
    const waiting = place.id === "loose" && !store.setsKnown;
    return {
      ...place,
      count: count(n),
      // Lit only while no Type or Base row is: one row lights at a time.
      active:
        !onRuns.value && store.place === place.id && !store.sidebarFilter,
      disabled: waiting,
      reason: waiting
        ? store.setsError
          ? "Your sets could not be read, so where each model is cannot be shown"
          : "Reading your sets…"
        : "",
    };
  }),
);

function pickPlace(row) {
  if (row.disabled) return;
  store.setPlace(row.id);
  if (onRuns.value) router.push({ name: "models" });
}

const isPicked = (axis, key) =>
  store.sidebarFilter?.axis === axis && store.sidebarFilter?.key === key;

const typeRows = computed(() =>
  SHELF_TYPES.map((type) => ({
    ...type,
    count: count(store.typeCounts[type.key] ?? 0),
    active: !onRuns.value && isPicked("type", type.key),
  })),
);

const baseRows = computed(() =>
  store.baseCounts.map(({ key, n }) => ({
    key,
    label: key === BASE_MODEL_UNASSIGNED ? "Base model not set" : key,
    count: count(n),
    active: !onRuns.value && isPicked("base", key),
  })),
);

function openRuns() {
  if (!onRuns.value) router.push({ name: "models-runs" });
}
</script>

<style scoped>
.msb {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  box-sizing: border-box;
  background: rgb(var(--v-theme-sidebar));
  color: rgb(var(--v-theme-sidebar-text));
  border-right: 1px solid rgb(var(--v-theme-divider));
  font-size: var(--text-sm);
}

/* The header slot, the height the Media sidebar's scope slot takes, so a
   workspace switch moves neither edge. */
.msb-slot {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  height: var(--sidebar-slot-h);
  box-sizing: border-box;
  padding: 0 var(--space-4) 0 var(--space-4);
  border: 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.msb-slot:hover,
.msb-slot--open {
  background: var(--hover-wash);
}

.msb-slot:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

.msb-slot-mark {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--gutter-glyph);
  height: var(--gutter-glyph);
}

.msb-slot-id {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.msb-slot-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  line-height: 1.2;
}

.msb-slot-chev {
  flex-shrink: 0;
  opacity: var(--opacity-text-secondary);
}

/* The drive meter, as the title bar's library picker draws it. */
.msb-bar {
  display: flex;
  height: 3px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--track-trough);
}

.msb-bar > span {
  display: block;
}

.msb-bar--mini {
  width: 28px;
  flex-shrink: 0;
}

.msb-bar[data-state="success"] > span {
  background: rgb(var(--v-theme-surface-success));
}

.msb-bar[data-state="warning"] > span {
  background: rgb(var(--v-theme-surface-warning));
}

.msb-bar[data-state="error"] > span {
  background: rgb(var(--v-theme-surface-error));
}

.msb-free[data-state="warning"] {
  color: rgb(var(--v-theme-surface-warning));
}

.msb-free[data-state="error"] {
  color: rgb(var(--v-theme-surface-error));
}

.msb-menu {
  width: 300px;
}

.msb-menu-label {
  display: block;
  padding: var(--space-2) var(--space-3) var(--space-1);
}

.msb-folder {
  min-height: calc(var(--control-h-bar) + var(--space-4));
}

.msb-folder-id {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.msb-folder-name,
.msb-folder-path {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msb-folder-path {
  font-size: var(--text-2xs);
  opacity: var(--opacity-text-secondary);
}

.msb-folder-meta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  white-space: nowrap;
}

.msb-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-2) 0;
  display: flex;
  flex-direction: column;
}

.msb-head {
  margin: 0;
  padding: var(--space-5) var(--space-4) var(--space-2) var(--space-5);
}

.msb-row {
  display: flex;
  align-items: center;
  width: 100%;
  height: var(--control-h-bar);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  border-top: 0;
  border-right: 0;
  border-bottom: 0;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.msb-row:hover {
  background: var(--hover-wash);
}

.msb-row.active {
  background: var(--active-wash);
  border-left-color: var(--active-bar);
  font-weight: var(--weight-medium);
}

.msb-row:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

.msb-row--off {
  cursor: default;
  opacity: var(--opacity-disabled);
}

.msb-row--off:hover {
  background: transparent;
}

.msb-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.msb-count {
  margin-left: var(--space-2);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  opacity: var(--opacity-text-secondary);
}

.msb-live {
  width: 8px;
  height: 8px;
  margin-left: var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
}

/* The footer band, with the screen's one amber button, at the Media
   sidebar's band height. */
.msb-band {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  height: var(--sidebar-band-h);
  box-sizing: border-box;
  padding: 0 var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-divider));
}
</style>
