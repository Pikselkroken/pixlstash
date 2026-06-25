<template>
  <div class="tbm tb-import-panel">
    <span class="tbm-caret tbm-caret--start"></span>
    <div class="tbm-header tb-import-header">
      <v-icon size="18" class="tbm-header-icon">mdi-cloud-upload-outline</v-icon>
      <span class="tbm-title">Import photos to</span>
      <span class="tbm-spacer"></span>
      <div class="tbm-select-wrap tb-import-project">
        <select
          :value="selectedProjectId ?? '__none__'"
          class="tbm-select"
          :class="{ 'tb-import-project--none': selectedProjectId == null }"
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

    <div class="tb-import-tabs" role="tablist">
      <button
        v-for="t in IMPORT_TABS"
        :key="t.id"
        class="tb-import-tab"
        :class="{ 'tb-import-tab--active': activeTab === t.id }"
        type="button"
        role="tab"
        :aria-selected="activeTab === t.id"
        @click="activeTab = t.id"
      >
        <v-icon size="16">{{ t.icon }}</v-icon>
        {{ t.label }}
      </button>
    </div>

    <!-- Local -->
    <div v-if="activeTab === 'local'" class="tbm-section">
      <span class="tbm-label">Local files</span>
      <div
        class="tb-import-dropzone"
        :class="{ 'is-dragging': dragActive }"
        @dragenter.stop.prevent="onDragEnter"
        @dragover.stop.prevent="onDragOver"
        @dragleave.stop.prevent="onDragLeave"
        @drop.stop.prevent="onDrop"
      >
        <v-icon size="30" class="tb-import-dropzone-icon">mdi-tray-arrow-up</v-icon>
        <div class="tb-import-dropzone-text">{{ dropMessage }}</div>
      </div>
      <input
        ref="localInputRef"
        class="tb-import-file-input"
        type="file"
        multiple
        accept="image/*,video/*,.zip,application/zip,application/x-zip-compressed,.txt,text/plain"
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
    <div v-else-if="activeTab === 'watch'" class="tbm-section tb-import-guidance">
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
    <div v-else class="tbm-section tb-import-guidance">
      <span class="tbm-label">{{ activeCloud.title }}</span>
      <ol>
        <li v-for="(step, i) in activeCloud.steps" :key="i" v-html="step"></li>
      </ol>
      <p class="tb-import-note">{{ activeCloud.note }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";
import {
  extractSupportedImportFilesFromDataTransfer,
  isSupportedImportFile,
} from "../../utils/media.js";

const props = defineProps({
  backendUrl: { type: String, default: "" },
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

// Manual export/import guidance per cloud supplier — PixlStash does not connect
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
const localInputRef = ref(null);
const localFiles = ref([]);
const dragActive = ref(false);
const projects = ref([]);
const selectedProjectId = ref(props.defaultProjectId ?? null);

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
  try {
    const res = await apiClient.get("/projects");
    projects.value = Array.isArray(res.data) ? res.data : [];
  } catch (err) {
    console.warn("Failed to load projects for import menu", err);
    projects.value = [];
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

/* Project select reads in accent when nothing is chosen, like the design. */
.tb-import-project {
  width: 190px;
  flex-shrink: 0;
}
.tb-import-project .tbm-select {
  min-height: 34px;
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}
.tb-import-project--none {
  color: rgb(var(--v-theme-accent));
}

/* Tabs — accent underline on the active tab. */
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
  border: none;
  border-bottom: 2px solid transparent;
  background: transparent;
  color: rgba(var(--v-theme-on-panel), 0.6);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  white-space: nowrap;
  cursor: pointer;
  transition: color var(--dur-1) var(--ease-standard);
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
</style>
