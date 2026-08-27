<template>
  <div class="tbm tb-import-panel">
    <span class="tbm-caret tbm-caret--icon-center-end"></span>
    <div class="tbm-header tb-import-header">
      <v-icon size="18" class="tbm-header-icon"
        >mdi-cloud-upload-outline</v-icon
      >
      <label :for="projectSelectId" class="tbm-title">Import photos to</label>
      <span class="tbm-spacer"></span>
      <div class="tbm-select-wrap tb-import-project">
        <select
          :id="projectSelectId"
          :value="selectedProjectId ?? '__none__'"
          class="tbm-select"
          :class="{ 'tb-import-project--none': selectedProjectId == null }"
          :disabled="projectsLoading"
          :aria-busy="projectsLoading ? 'true' : undefined"
          :aria-describedby="
            projectsLoading || projectLoadError ? projectStatusId : undefined
          "
          @change="onProjectChange($event.target.value)"
        >
          <option value="__none__">No project</option>
          <option v-for="p in projects" :key="p.id" :value="String(p.id)">
            {{ p.name }}
          </option>
          <option value="__new__">+ New project…</option>
        </select>
        <v-icon size="16" class="tbm-select-chevron">mdi-chevron-down</v-icon>
      </div>
    </div>

    <div
      v-if="projectsLoading"
      :id="projectStatusId"
      class="tb-import-project-status"
      role="status"
    >
      Loading projects…
    </div>
    <div
      v-else-if="projectLoadError"
      :id="projectStatusId"
      class="tb-import-project-status tb-import-project-status--error"
      role="alert"
    >
      <span>Projects couldn’t load. Importing without one still works.</span>
      <button type="button" class="tb-import-retry" @click="fetchProjects">
        Retry
      </button>
    </div>

    <div class="tb-import-tabs" role="tablist">
      <button
        v-for="t in IMPORT_TABS"
        :id="tabId(t.id)"
        :key="t.id"
        class="tb-import-tab"
        :class="{ 'tb-import-tab--active': activeTab === t.id }"
        type="button"
        role="tab"
        :aria-selected="activeTab === t.id"
        :aria-controls="panelId(t.id)"
        :tabindex="activeTab === t.id ? 0 : -1"
        @click="activeTab = t.id"
        @keydown="onTabKeydown($event, t.id)"
      >
        <v-icon size="16" aria-hidden="true">{{ t.icon }}</v-icon>
        {{ t.label }}
      </button>
    </div>

    <!-- Local -->
    <div
      v-if="activeTab === 'local'"
      :id="panelId('local')"
      class="tbm-section"
      role="tabpanel"
      :aria-labelledby="tabId('local')"
    >
      <span class="tbm-label">Local files</span>
      <div
        class="tb-import-dropzone"
        :class="{ 'is-dragging': dragActive }"
        @dragenter.stop.prevent="onDragEnter"
        @dragover.stop.prevent="onDragOver"
        @dragleave.stop.prevent="onDragLeave"
        @drop.stop.prevent="onDrop"
      >
        <v-icon size="30" class="tb-import-dropzone-icon"
          >mdi-tray-arrow-up</v-icon
        >
        <div class="tb-import-dropzone-text" aria-live="polite">
          {{ dropMessage }}
        </div>
      </div>
      <input
        ref="localInputRef"
        class="tb-import-file-input"
        type="file"
        multiple
        :accept="IMPORT_FILE_ACCEPT"
        @change="onLocalChange"
      />
      <div class="tb-import-actions">
        <button
          class="tbm-action tbm-action--outline"
          type="button"
          @click="openLocalPicker"
        >
          <v-icon size="16">mdi-file-plus-outline</v-icon>
          Choose files
        </button>
      </div>
    </div>

    <!-- Folder watch -->
    <div
      v-else-if="activeTab === 'watch'"
      :id="panelId('watch')"
      class="tbm-section tb-import-guidance"
      role="tabpanel"
      :aria-labelledby="tabId('watch')"
    >
      <span class="tbm-label">Automatic folder monitoring</span>
      <p class="tb-import-note">Import folders are managed in PixlStash.</p>
      <ol>
        <li>Open the sidebar <strong>Folders</strong> tab.</li>
        <li>
          Click <strong>Add folder</strong> and choose
          <strong>Import folder</strong>.
        </li>
        <li>
          Set <strong>Delete source files after import</strong> for one-way
          ingest.
        </li>
      </ol>
    </div>

    <!-- Cloud suppliers: manual-zip guidance (no live connection today) -->
    <div
      v-else
      :id="panelId(activeTab)"
      class="tbm-section tb-import-guidance"
      role="tabpanel"
      :aria-labelledby="tabId(activeTab)"
    >
      <span class="tbm-label">{{ activeCloud.title }}</span>
      <ol>
        <li v-for="(step, i) in activeCloud.steps" :key="i" v-html="step"></li>
      </ol>
      <p class="tb-import-note">{{ activeCloud.note }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, useId, watch } from "vue";
import { listProjects } from "../../api/projects";
import { API_BASE_URL } from "../../utils/apiClient";
import {
  extractSupportedImportFilesFromDataTransfer,
  isSupportedImportFile,
  IMPORT_FILE_ACCEPT,
} from "../../utils/media.js";

const props = defineProps({
  backendUrl: { type: String, default: () => API_BASE_URL },
  open: { type: Boolean, default: false },
  defaultProjectId: { type: [Number, String, null], default: null },
});

const emit = defineEmits(["local-import", "open-full-import"]);

const IMPORT_TABS = [
  { id: "local", icon: "mdi-folder-outline", label: "Local" },
  { id: "watch", icon: "mdi-folder-sync-outline", label: "Folder watch" },
  { id: "google", icon: "mdi-google", label: "Google" },
  { id: "icloud", icon: "mdi-apple-icloud", label: "iCloud" },
  { id: "flickr", icon: "mdi-flickr", label: "Flickr" },
];

// Manual export/import guidance per cloud supplier - PixlStash does not connect
// to these services; users export a zip and drop it in. (See the connect-to
// suppliers plan for why there is no live connection.)
const CLOUD_GUIDANCE = {
  google: {
    title: "Google Takeout export",
    steps: [
      'Go to <a href="https://takeout.google.com/" target="_blank" rel="noopener noreferrer">Google Takeout</a> and select Google Photos.',
      "Download the zip archive.",
      "Drag the zip file into PixlStash to import.",
    ],
    note: "Importable right now: Takeout zip files or extracted folders.",
  },
  icloud: {
    title: "iCloud Photos export",
    steps: [
      'Web: open <a href="https://www.icloud.com/photos/" target="_blank" rel="noopener noreferrer">iCloud Photos</a>, select your library, and download.',
      'Mac: Photos → Settings → iCloud → enable "Download Originals" and export unmodified originals.',
      "Download the zip files from iCloud.",
      "Drag the zip file into PixlStash to import.",
    ],
    note: "Importable right now: iCloud zip files or extracted folders.",
  },
  flickr: {
    title: "Flickr export",
    steps: [
      'Open <a href="https://www.flickr.com/account/data" target="_blank" rel="noopener noreferrer">Flickr Data Download</a> and request your archive.',
      "When it arrives, download the zip archive.",
      "Drag the zip file into PixlStash to import.",
    ],
    note: "Importable right now: Flickr zip files or extracted folders.",
  },
};

const activeTab = ref("local");
const importPanelId = useId();
const projectSelectId = `${importPanelId}-project`;
const projectStatusId = `${projectSelectId}-status`;
const localInputRef = ref(null);
const localFiles = ref([]);
const dragActive = ref(false);
const projects = ref([]);
const projectsLoading = ref(false);
const projectLoadError = ref(false);
const selectedProjectId = ref(props.defaultProjectId ?? null);
let projectRequestId = 0;

function tabId(id) {
  return `${importPanelId}-tab-${id}`;
}

function panelId(id) {
  return `${importPanelId}-panel-${id}`;
}

function onTabKeydown(event, currentId) {
  const currentIndex = IMPORT_TABS.findIndex((tab) => tab.id === currentId);
  if (currentIndex < 0) return;

  let nextIndex = null;
  if (event.key === "ArrowRight" || event.key === "ArrowDown") {
    nextIndex = (currentIndex + 1) % IMPORT_TABS.length;
  } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
    nextIndex = (currentIndex - 1 + IMPORT_TABS.length) % IMPORT_TABS.length;
  } else if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = IMPORT_TABS.length - 1;
  }

  if (nextIndex === null) return;
  event.preventDefault();
  const nextTab = IMPORT_TABS[nextIndex];
  activeTab.value = nextTab.id;
  nextTick(() => document.getElementById(tabId(nextTab.id))?.focus());
}

const activeCloud = computed(
  () => CLOUD_GUIDANCE[activeTab.value] ?? CLOUD_GUIDANCE.google,
);

const dropMessage = computed(() => {
  const count = localFiles.value.length;
  if (!count) return "Drop images, videos, or ZIP archives here";
  return `${count} file${count === 1 ? "" : "s"} ready to import.`;
});

watch(
  () => props.defaultProjectId,
  (val) => {
    selectedProjectId.value = val ?? null;
  },
);

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      // Default to the currently-selected project (None when none is selected)
      // each time the menu opens, discarding any prior manual override.
      selectedProjectId.value = props.defaultProjectId ?? null;
      fetchProjects();
    }
  },
  { immediate: true },
);

async function fetchProjects() {
  const requestId = ++projectRequestId;
  projectsLoading.value = true;
  projectLoadError.value = false;
  try {
    const rows = await listProjects();
    if (requestId !== projectRequestId) return;
    projects.value = Array.isArray(rows) ? rows : [];
  } catch (err) {
    if (requestId !== projectRequestId) return;
    console.warn("Failed to load projects for import menu", err);
    projects.value = [];
    projectLoadError.value = true;
  } finally {
    if (requestId === projectRequestId) projectsLoading.value = false;
  }
}

function onProjectChange(value) {
  if (value === "__new__") {
    // Project creation lives in the full import dialog (with the project editor).
    emit("open-full-import");
    return;
  }
  selectedProjectId.value = value === "__none__" ? null : Number(value);
}

function openLocalPicker() {
  localInputRef.value?.click();
}

function onLocalChange(event) {
  const files = Array.from(event?.target?.files || []).filter(
    isSupportedImportFile,
  );
  if (files.length) triggerLocalImport(files);
}

async function onDrop(event) {
  dragActive.value = false;
  const files = await extractSupportedImportFilesFromDataTransfer(
    event?.dataTransfer,
  );
  if (files.length) triggerLocalImport(files);
}

function onDragEnter() {
  dragActive.value = true;
}
function onDragOver(event) {
  if (event?.dataTransfer) event.dataTransfer.dropEffect = "copy";
  dragActive.value = true;
}
function onDragLeave(event) {
  if (!event?.currentTarget?.contains(event.relatedTarget)) {
    dragActive.value = false;
  }
}

function triggerLocalImport(files) {
  if (!files.length) return;
  emit("local-import", { files, projectId: selectedProjectId.value });
  localFiles.value = [];
  if (localInputRef.value) localInputRef.value.value = "";
}
</script>

<style scoped>
.tb-import-panel {
  width: 520px;
  max-width: 94vw;
}

.tb-import-header {
  gap: var(--space-3);
}

.tb-import-header .tbm-title {
  min-width: 0;
  overflow-wrap: anywhere;
}

/* Project select reads in accent when nothing is chosen, like the design. */
.tb-import-project {
  width: 190px;
  min-width: 0;
  max-width: 48%;
  flex-shrink: 0;
}
.tb-import-project .tbm-select {
  width: 100%;
  min-height: 34px;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  text-overflow: ellipsis;
}
.tb-import-project--none {
  color: rgb(var(--v-theme-accent));
}

.tb-import-project-status {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: 0 var(--space-4) var(--space-3);
  color: rgba(
    var(--v-theme-on-panel),
    var(--opacity-text-secondary)
  );
  font-size: var(--text-xs);
  overflow-wrap: anywhere;
}

.tb-import-project-status--error {
  color: rgb(var(--v-theme-error));
}

.tb-import-retry {
  flex-shrink: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  color: inherit;
  font: inherit;
  font-weight: var(--weight-semibold);
}

.tb-import-retry:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* Tabs - accent underline on the active tab. */
.tb-import-tabs {
  display: flex;
  gap: var(--space-6);
  padding: 0 var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}
.tb-import-tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-1);
  margin-bottom: -1px;
  border-bottom: 2px solid transparent;
  color: rgba(var(--v-theme-on-panel), 0.6);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  white-space: nowrap;
  transition: color var(--dur-1) var(--ease-standard);
}
.tb-import-tab:focus-visible {
  outline: none;
  box-shadow: inset var(--focus-ring);
}
.tb-import-tab:hover {
  color: rgb(var(--v-theme-on-panel));
}
.tb-import-tab--active {
  color: rgb(var(--v-theme-accent));
  border-bottom-color: rgb(var(--v-theme-accent));
  font-weight: var(--weight-semibold);
}

/* Local dropzone */
.tb-import-dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-7) var(--space-5);
  text-align: center;
  background: rgb(var(--v-theme-input-background));
  border: 1.5px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  transition:
    border-color var(--dur-1) var(--ease-standard),
    background var(--dur-1) var(--ease-standard);
}
.tb-import-dropzone.is-dragging {
  border-color: rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.08);
}
.tb-import-dropzone-icon {
  color: rgba(var(--v-theme-on-panel), 0.5);
}
.tb-import-dropzone-text {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-panel), 0.6);
}
.tb-import-file-input {
  display: none;
}
.tb-import-actions {
  margin-top: var(--space-4);
}

/* Cloud / folder-watch guidance */
.tb-import-guidance ol {
  margin: 0;
  padding-left: var(--space-5);
  display: grid;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-panel));
}
.tb-import-guidance ol :deep(a) {
  color: rgb(var(--v-theme-accent));
}
.tb-import-note {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-panel), 0.6);
  margin-top: var(--space-3);
}

@media (max-width: 480px) {
  .tb-import-header {
    flex-wrap: wrap;
  }

  .tb-import-header .tbm-spacer {
    display: none;
  }

  .tb-import-project {
    flex-basis: 100%;
    width: 100%;
    max-width: none;
  }
}
</style>
