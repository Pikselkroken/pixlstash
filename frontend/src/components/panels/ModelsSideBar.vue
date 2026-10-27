<template>
  <!-- The Models workspace's own sidebar (#2168): the Model folders slot, the
       places, the Folders tree (#2203), Training, and the amber Add band.
       It replaces the library's sidebar on /models, as the shelf replaces the
       grid: nothing in the library's navigation is about files on this disk. -->
  <aside
    class="msb"
    :style="{ width: `${width}px` }"
    aria-label="Model navigation"
    data-testid="models-sidebar"
  >
    <!-- The slot the library sidebar gives the project, on the toolbar's line
         at the toolbar's height (#2203): one button that opens Manage model
         folders. The folders themselves are the tree below. -->
    <button
      type="button"
      class="msb-slot"
      aria-haspopup="dialog"
      :aria-label="`Model folders, ${slotSummary}. Manage model folders`"
      data-testid="models-folder-slot"
      @click="ask('manage-folders', $event.currentTarget)"
    >
      <span class="msb-slot-mark" aria-hidden="true"
        ><v-icon size="14">mdi-folder-outline</v-icon></span
      >
      <span class="msb-slot-name">Model folders</span>
      <span class="msb-count msb-slot-sum">{{ slotSummary }}</span>
      <v-icon size="14" class="msb-slot-chev" aria-hidden="true"
        >mdi-cog-outline</v-icon
      >
    </button>

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

      <h3 class="section-label msb-head">Folders</h3>
      <ModelFolderTree
        :selected="onRuns || store.place !== 'folder' ? null : store.folderScope"
        @select="openFolder"
      />

      <!-- Training, last (#2169): its place, and the door to a new run. -->
      <div class="msb-head msb-head--action">
        <h3 class="section-label msb-head-label">Training</h3>
        <button
          type="button"
          class="msb-add"
          aria-label="New training run…"
          aria-haspopup="dialog"
          data-testid="models-sidebar-new-run"
          @click="training.openToPick()"
        >
          <Tooltip text="New training run…" activator="parent" :describe="false" />
          <v-icon size="16" aria-hidden="true">mdi-plus</v-icon>
        </button>
      </div>
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
        <span class="msb-count" data-testid="models-sidebar-runs-count">{{
          count(runsStore.count)
        }}</span>
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

import { DOWNLOADS_KIND, SOURCE_KIND } from "../../api/modelFolders";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
import { useTrainingStore } from "../../stores/useTrainingStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { withRef } from "../../utils/withRef.js";
import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import ModelFolderTree from "./ModelFolderTree.vue";

const store = useModelShelfStore();
const foldersStore = useModelFoldersStore();
const tasksStore = useTasksStore();
const runsStore = useTrainingRunsStore();
const training = useTrainingStore();
const userPrefsStore = useUserPrefsStore();
const route = useRoute();
const router = useRouter();

// The library sidebar's width, so switching workspace does not move the edge.
const width = computed(() => userPrefsStore.sidebarWidth ?? 240);

const addMenuOpen = ref(false);
// The trigger outlives its menu, so a dialog opened from it hands focus back
// to it. Not reactive: only `ask` reads it.
let addTrigger = null;

onMounted(() => {
  // The counts need the sets ("In no set yet"); the tree needs the folders,
  // their drives and a fresh read of each folder's own level.
  store.loadWorkflowSets();
  foldersStore.clearDirs();
  foldersStore.refresh({ quiet: true });
  foldersStore.refreshDevices();
  // The Training runs row counts them wherever the workspace is.
  runsStore.ensureLoaded();
});

const onRuns = computed(() => route.name === "models-runs");

// The leases, not the runs store: the tasks poll runs on every screen, and the
// store's job list is only re-read while the runs view is up, so it would keep
// the dot lit after a run ended elsewhere.
const trainingNow = computed(() =>
  (tasksStore.resources?.leases ?? []).some(
    (lease) => lease.tenant === "aitoolkit",
  ),
);

// ── The Model folders slot and the tree ──────────────────────────────────

/** The folders the shelf catalogues: not the ai-toolkit root nor Downloads. */
const shelfFolders = computed(() =>
  foldersStore.folders.filter(
    (folder) => folder.kind !== SOURCE_KIND && folder.kind !== DOWNLOADS_KIND,
  ),
);

/** "2 drives · 7 folders": what the slot's button manages. */
const slotSummary = computed(() => {
  const drives = new Set(
    shelfFolders.value
      .map((folder) => foldersStore.deviceByFolderId.get(Number(folder.id)))
      .filter((device) => device?.device_id != null)
      .map((device) => device.device_id),
  ).size;
  const n = shelfFolders.value.length;
  return [
    drives ? `${drives} ${drives === 1 ? "drive" : "drives"}` : "",
    `${n} ${n === 1 ? "folder" : "folders"}`,
  ]
    .filter(Boolean)
    .join(" · ");
});

// An open folder that is no longer registered (forgotten in Manage model
// folders) closes, or the list would sit empty under a stale breadcrumb.
watch(
  () => [foldersStore.loaded, shelfFolders.value, store.folderScope],
  ([loaded, folders, scope]) => {
    if (!loaded || scope == null) return;
    if (!folders.some((folder) => Number(folder.id) === scope.folderId)) {
      store.setFolderScope(null);
    }
  },
  { immediate: true },
);

/** Open a folder from the tree, leaving Training runs for the list. */
function openFolder(scope) {
  store.setFolderScope(scope);
  if (onRuns.value) router.push({ name: "models" });
}

/**
 * Ask the shelf for one of its dialogs. The shelf owns them, and their
 * confirmations, because the toolbar's Add and Model folders used to.
 */
function ask(what, el = null) {
  addMenuOpen.value = false;
  const target = el?.el ?? el ?? null;
  // Back on the button first, so the dialog that opens next returns there.
  target?.focus?.();
  store.requestShell(what, target);
}

// ── Places ───────────────────────────────────────────────────────────────

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
      active: !onRuns.value && store.place === place.id,
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

.msb-slot:hover {
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

.msb-slot-name {
  flex: 1;
  min-width: 0;
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

.msb-slot-sum {
  margin-left: 0;
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

/* The Training head carries its + at the right edge, as the design draws it. */
.msb-head--action {
  display: flex;
  align-items: center;
  padding-top: var(--space-4);
  padding-bottom: var(--space-1);
  padding-right: var(--space-3);
}

.msb-head-label {
  flex: 1;
  margin: 0;
}

/* The library sidebar's section + (`sidebar-section-add`). */
.msb-add {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-sidebar-text));
  cursor: pointer;
}

.msb-add:hover {
  background: var(--hover-wash);
}

.msb-add:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
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
