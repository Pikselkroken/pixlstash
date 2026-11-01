<template>
  <!-- The Models workspace's own sidebar (#2168): the Model folders slot, the
       places and Training (fixed), the Folders tree (#2203, the one part that
       scrolls), and the amber Add band.
       It replaces the library's sidebar on /models, as the shelf replaces the
       grid: nothing in the library's navigation is about files on this disk. -->
  <aside
    class="msb sidebar-panel"
    :class="{ 'sidebar--narrow': narrow }"
    :style="{ width: `${width}px` }"
    aria-label="Model navigation"
    data-testid="models-sidebar"
  >
    <SidebarResizeHandle />
    <!-- The slot the library sidebar gives the project, on the toolbar's line
         at the toolbar's height (#2203): one button that opens Manage model
         folders. The folders themselves are the tree below, which also shows
         the drives, so the slot carries no count of either. -->
    <button
      type="button"
      class="msb-slot"
      aria-haspopup="dialog"
      aria-label="Manage model folders"
      data-testid="models-folder-slot"
      @click="ask('manage-folders', $event.currentTarget)"
    >
      <span class="msb-slot-mark" aria-hidden="true"
        ><v-icon size="12">mdi-folder-outline</v-icon></span
      >
      <span class="msb-slot-name">Model folders</span>
      <v-icon size="16" class="msb-slot-chev" aria-hidden="true"
        >mdi-cog-outline</v-icon
      >
    </button>

    <!-- Places and Training are one fixed block, and only the Folders tree
         scrolls under it, so no expansion can push Training out of sight. -->
    <nav class="msb-nav" aria-label="Model places">
      <div class="msb-fixed sidebar-nav-list">
        <button
          v-for="row in placeRows"
          :key="row.id"
          type="button"
          class="sidebar-list-item"
          :class="{
            active: row.active,
            'sidebar-list-item--unavailable': row.disabled,
          }"
          :aria-current="row.active ? 'page' : undefined"
          :aria-disabled="row.disabled ? 'true' : undefined"
          :data-place="row.id"
          @click="pickPlace(row)"
        >
          <Tooltip v-if="row.reason" :text="row.reason" activator="parent" />
          <span class="sidebar-list-icon" aria-hidden="true"
            ><v-icon size="16">{{ row.icon }}</v-icon></span
          >
          <span class="sidebar-list-label sidebar-list-label-text">{{
            row.label
          }}</span>
          <span class="sidebar-list-count">{{ row.count }}</span>
        </button>

        <!-- Training (#2169): its place, and the door to a new run in words. -->
        <div class="sidebar-section-header">
          <h3 class="sidebar-section-title msb-head-title">Training</h3>
        </div>
        <button
          type="button"
          class="sidebar-list-item"
          :class="{ active: onRuns, 'msb-row--two': runsStatus }"
          :aria-current="onRuns ? 'page' : undefined"
          data-place="runs"
          @click="openRuns"
        >
          <span class="sidebar-list-icon" aria-hidden="true"
            ><v-icon size="16">mdi-chart-line-variant</v-icon></span
          >
          <!-- Two lines, as a registered folder is: the name, and what the
               runs are doing, so the row answers "is something training?". -->
          <span class="msb-stack">
            <span class="msb-line">
              <span class="sidebar-list-label sidebar-list-label-text"
                >Training runs</span
              >
              <span
                v-if="trainingNow"
                class="msb-live"
                data-testid="models-sidebar-live-dot"
                aria-hidden="true"
              ></span>
              <span v-if="trainingNow" class="visually-hidden">, training now</span>
            </span>
            <span
              v-if="runsStatus"
              class="msb-sub"
              data-testid="models-sidebar-runs-status"
              >{{ runsStatus }}</span
            >
          </span>
          <span class="sidebar-list-count" data-testid="models-sidebar-runs-count">{{
            count(runsStore.count)
          }}</span>
        </button>

        <button
          type="button"
          class="sidebar-list-item"
          aria-haspopup="dialog"
          data-testid="models-sidebar-new-run"
          @click="training.openToPick()"
        >
          <span class="sidebar-list-icon" aria-hidden="true"
            ><v-icon size="16">mdi-school-outline</v-icon></span
          >
          <span class="sidebar-list-label sidebar-list-label-text"
            >Train a LoRA…</span
          >
        </button>
      </div>

      <div class="msb-scroll msb-folders sidebar-nav-list">
        <!-- The Folders heading's + makes a folder in the open one (#2206). -->
        <div class="sidebar-section-header">
          <h3 class="sidebar-section-title msb-head-title">Folders</h3>
          <span class="sidebar-header-actions">
          <button
            type="button"
            class="sidebar-section-add"
            :aria-label="newFolderReason ? 'New folder, unavailable' : 'New folder'"
            :aria-disabled="newFolderReason ? 'true' : undefined"
            ref="newFolderButton"
            data-testid="models-sidebar-new-folder"
            @click="openNewFolder"
          >
            <Tooltip
              :text="newFolderReason || 'New folder in the open folder'"
              activator="parent"
              :describe="false"
            />
            <v-icon size="16" aria-hidden="true">mdi-folder-plus-outline</v-icon>
          </button>
          </span>
        </div>
        <div v-if="newFolder" ref="newFolderRow" class="msb-new">
          <ModelFolderNameField
            :model-value="newFolder.name"
            :label="`New folder in ${newFolderScopeName}`"
            :error="newFolder.error"
            @update:model-value="(value) => (newFolder.name = value)"
            @submit="commitNewFolder"
            @cancel="cancelNewFolder"
          />
        </div>
        <ModelFolderTree
          ref="tree"
          :selected="onRuns || store.place !== 'folder' ? null : store.folderScope"
          @select="openFolder"
        />
      </div>
    </nav>

    <!-- The one amber action on this screen. -->
    <div class="sidebar-footer-band">
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
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { VIcon, VMenu } from "vuetify/components";

import { DOWNLOADS_KIND, SOURCE_KIND } from "../../api/modelFolders";
import { useModelFolderGates } from "../../composables/useModelFolderGates";
import { errorMessage } from "../../utils/apiError";
import {
  basename,
  useModelFoldersStore,
} from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
import { useTrainingStore } from "../../stores/useTrainingStore";
import { useSidebarWidth } from "../../composables/useSidebarWidth";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { relativeDate } from "../../utils/snapshots.js";
import { withRef } from "../../utils/withRef.js";
import AiToolkitIcon from "../widgets/AiToolkitIcon.vue";
import AppButton from "../widgets/AppButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import ModelFolderNameField from "./ModelFolderNameField.vue";
import ModelFolderTree from "./ModelFolderTree.vue";
import SidebarResizeHandle from "./SidebarResizeHandle.vue";

const store = useModelShelfStore();
const foldersStore = useModelFoldersStore();
const gates = useModelFolderGates();
const tasksStore = useTasksStore();
const runsStore = useTrainingRunsStore();
const training = useTrainingStore();
const route = useRoute();
const router = useRouter();

// The width every workspace's sidebar shares, so a switch does not move the edge.
const { width, narrow } = useSidebarWidth();

const addMenuOpen = ref(false);
// The trigger outlives its menu, so a dialog opened from it hands focus back
// to it. Not reactive: only `ask` reads it.
let addTrigger = null;

onMounted(() => {
  // The counts need the sets ("In no set yet"); the tree needs the folders,
  // their drives and a fresh read of each listing it shows. Re-read in place,
  // not dropped: the open rows draw whole from the last visit's listings
  // rather than opening one level at a time.
  store.loadWorkflowSets();
  foldersStore.reloadDirs();
  foldersStore.refresh({ quiet: true });
  foldersStore.refreshDevices();
  // The Training runs row counts them wherever the workspace is.
  runsStore.ensureLoaded();
});

const onRuns = computed(() => route.name === "models-runs");

// The shelf refetches after every scan, move, added file and edit: the folder
// listings open in the tree are re-read with it, so a counted or created
// folder does not wait for the next visit.
watch(
  () => store.rows,
  () => foldersStore.reloadDirs(),
);

// The leases, not the runs store: the tasks poll runs on every screen, and the
// store's job list is only re-read while the runs view is up, so it would keep
// the dot lit after a run ended elsewhere.
// Held or waiting lights it; the one holding the card names the second line.
const trainingLease = computed(() => {
  const leases = (tasksStore.resources?.leases ?? []).filter(
    (lease) => lease.tenant === "aitoolkit",
  );
  return leases.find((lease) => lease.state === "granted") ?? leases[0] ?? null;
});
const trainingNow = computed(() => trainingLease.value != null);

// The lease is labelled for the Tasks tab, so the run's own name comes from
// its job: one started since the jobs were read is read again for it, once.
// The tasks poll replaces the lease every few seconds, so the watch is on the
// id string, which a poll does not change.
watch(
  () => (runsStore.jobsLoaded ? (trainingLease.value?.job_id ?? "") : ""),
  (jobId) => {
    if (!jobId || runsStore.jobs.some((job) => job.uuid === jobId)) return;
    runsStore.loadJobs();
  },
  { immediate: true },
);

/**
 * The runs row's second line: "aria_v4 · 41%" training, "aria_v4 · waiting"
 * queued for the card, "aria_v4 · checking" probing before it trains, else
 * "Last run 2h ago".
 */
const runsStatus = computed(() => {
  const lease = trainingLease.value;
  if (lease) {
    const job = runsStore.jobs.find((one) => one.uuid === lease.job_id);
    const name = job?.name || "A run";
    if (lease.state !== "granted") return `${name} · waiting`;
    // A short pass around training ("checking", "previews"): its step and
    // total are the pass's own, so the 6-step probe would read 100%.
    const pass = lease.progress?.shown_as;
    if (pass) return `${name} · ${pass}`;
    const total = Number(lease.progress?.total);
    if (!(total > 0)) return name;
    const done = Number(lease.progress.step) || 0;
    return `${name} · ${Math.min(100, Math.round((done / total) * 100))}%`;
  }
  const whenOf = (card) =>
    card.run?.modified_at != null
      ? card.run.modified_at * 1000
      : utcMs(card.job?.created_at ?? "");
  const newest = Math.max(0, ...runsStore.cards.map(whenOf).filter(Boolean));
  if (!newest) return "";
  return `Last run ${relativeDate(new Date(newest).toISOString())}`;
});

/** An ISO time in ms, a naive one read as UTC as relativeDate reads it; 0 if none. */
function utcMs(at) {
  const naive = at.includes("T") && !/(Z|[+-]\d{2}:\d{2})$/.test(at);
  return Date.parse(naive ? `${at}Z` : at) || 0;
}

// ── The Model folders slot and the tree ──────────────────────────────────

/** The folders the shelf catalogues: not the ai-toolkit root nor Downloads. */
const shelfFolders = computed(() =>
  foldersStore.folders.filter(
    (folder) => folder.kind !== SOURCE_KIND && folder.kind !== DOWNLOADS_KIND,
  ),
);

// ── New folder (#2206) ──────────────────────────────────────────────────

/** The tree, to reveal a folder the field below just made. */
const tree = ref(null);
const newFolderButton = ref(null);
const newFolderRow = ref(null);
/** The field under the heading: `{folderId, path, name, error, busy}`, or null. */
const newFolder = ref(null);

/** The folder a new one is made in: the open folder, not while on Training runs. */
const openScope = computed(() => (onRuns.value ? null : store.folderScope));

/** Why the heading's + cannot make a folder right now, or "". */
const newFolderReason = computed(() => {
  const scope = openScope.value;
  if (!scope) {
    return "Open a folder first. A new folder is made inside the one open.";
  }
  if (scope.locked) {
    return "This folder is locked, so nothing can be made inside it.";
  }
  return gates.folderVerbReason({ id: scope.folderId });
});

/** The open folder's name, for the field's label. */
const newFolderScopeName = computed(() => {
  const scope = openScope.value;
  if (!scope) return "";
  if (scope.path) return basename(scope.path);
  return foldersStore.labelByFolderId.get(scope.folderId) || "this folder";
});

function openNewFolder() {
  const scope = openScope.value;
  if (newFolderReason.value) {
    useNoticeStore().push({ level: "info", text: newFolderReason.value });
    return;
  }
  // A second click keeps what was typed and goes back to it.
  if (newFolder.value) {
    newFolderRow.value?.querySelector("input")?.focus();
    return;
  }
  newFolder.value = {
    folderId: scope.folderId,
    path: scope.path ?? "",
    name: "",
    error: "",
    busy: false,
  };
}

/** Close the heading's field and give the focus back to its `+`. */
function cancelNewFolder() {
  newFolder.value = null;
  nextTick(() => newFolderButton.value?.focus());
}

async function commitNewFolder() {
  const open = newFolder.value;
  if (!open || open.busy) return;
  open.busy = true;
  open.error = "";
  try {
    const created = await foldersStore.createDir(
      open.folderId,
      open.path,
      open.name,
    );
    // Esc while the request ran closed this field; the focus is not ours.
    if (newFolder.value !== open) return;
    newFolder.value = null;
    tree.value?.reveal(open.folderId, open.path, created.path);
  } catch (err) {
    open.busy = false;
    open.error = errorMessage(err, "Could not create that folder.");
  }
}

// The field belongs to the folder it was opened in: moving the open folder
// closes it, and so does Training runs, which hides the open folder, rather
// than making a folder somewhere the reader is not looking.
watch(
  () => openScope.value,
  () => {
    newFolder.value = null;
  },
);

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

// All models first, as All media and All workflows lead their sidebars.
const PLACES = [
  { id: "all", label: "All models", icon: "mdi-format-list-bulleted" },
  { id: "favourites", label: "Favourites", icon: "mdi-heart-outline" },
  { id: "recent", label: "Recently added", icon: "mdi-clock-outline" },
  { id: "sets", label: "Sets", icon: "mdi-view-grid-outline" },
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

<style scoped src="./SidebarNav.css"></style>
<style scoped>
.msb {
  flex-shrink: 0;
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
  padding: 0 var(--space-4);
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

/* The neutral 20px tile the Media and Workflows slots wear. */
.msb-slot-mark {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-sidebar-text), 0.12);
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

/* The places and Training over the Folders pane, between the slot and the
   band. No top padding: the first row meets the slot's rule, as the Media
   list's does. */
.msb-nav {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.msb-fixed {
  flex-shrink: 0;
}

/* The one region that scrolls, so no expansion moves Training. The tree's
   rows take the nav rows' type size from here. The list is the shared nav
   list's flex column, whose children never shrink (SidebarNav.css), so an
   expanded folder scrolls rather than squeezes. */
.msb-scroll {
  font-size: var(--text-base);
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  margin-top: var(--space-3);
  padding: 0 0 var(--space-2);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

/* The FOLDERS label stays at the top of its pane while the tree scrolls. */
.msb-folders > .sidebar-section-header {
  position: sticky;
  top: 0;
  z-index: var(--z-sticky);
  background: rgb(var(--v-theme-sidebar));
}

/* A heading for the outline; the head's own type, not the h3's. */
.msb-head-title {
  margin: 0;
  font: inherit;
  letter-spacing: inherit;
}

/* The field a folder is named in, under the Folders heading: at the rows'
   label column. */
.msb-new {
  padding: 0 var(--space-3) var(--space-3)
    calc(var(--space-3) + var(--rail-w));
}

/* The runs row's two lines: 40px, as a registered folder's are. */
.sidebar-nav-list .sidebar-list-item.msb-row--two {
  height: calc(var(--control-h-bar) + var(--space-3));
}

.msb-stack {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: var(--leading-tight);
}

.msb-line {
  display: flex;
  align-items: center;
  min-width: 0;
}

.msb-line .sidebar-list-label-text {
  flex: 0 1 auto;
  width: auto;
}

.msb-sub {
  font-size: var(--text-2xs);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  opacity: var(--opacity-text-secondary);
}

.msb-live {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  margin-left: var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
}
</style>
