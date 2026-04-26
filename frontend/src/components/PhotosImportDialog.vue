<script setup>
import { computed, nextTick, ref, watch } from "vue";
import {
  extractSupportedImportFilesFromDataTransfer,
  isSupportedImportFile,
} from "../utils/media.js";
import { apiClient } from "../utils/apiClient.js";
import ProjectEditor from "./ProjectEditor.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  defaultProjectId: { type: [Number, null], default: null },
  backendUrl: { type: String, default: "" },
});

const emit = defineEmits(["update:open", "local-import", "project-created"]);

const dialogOpen = computed({
  get: () => props.open,
  set: (value) => emit("update:open", value),
});

const activeTab = ref("local");
const localInputRef = ref(null);
const localFiles = ref([]);
const dragActive = ref(false);
const importFolders = ref([]);
const importFoldersLoading = ref(false);
const importFoldersError = ref("");
const importFoldersLoaded = ref(false);

const projects = ref([]);
const selectedProjectId = ref(null);
const projectEditorOpen = ref(false);

const projectSelectItems = computed(() => [
  { title: "No project", value: null },
  ...[...projects.value]
    .sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    )
    .map((p) => ({ title: p.name, value: p.id })),
  { title: "+ New project\u2026", value: "__new__" },
]);

function handleProjectChange(newVal) {
  if (newVal === "__new__") {
    projectEditorOpen.value = true;
  } else {
    selectedProjectId.value = newVal;
  }
}

function onProjectSaved(newId) {
  projectEditorOpen.value = false;
  fetchProjects().then(() => {
    if (newId != null) selectedProjectId.value = newId;
  });
  if (newId != null) emit("project-created", newId);
}

watch(
  () => props.defaultProjectId,
  (val) => {
    selectedProjectId.value = val ?? null;
  },
  { immediate: true },
);

async function fetchProjects() {
  try {
    const res = await apiClient.get("/projects");
    projects.value = Array.isArray(res.data) ? res.data : [];
  } catch {
    projects.value = [];
  }
}

const dropMessage = computed(() => {
  const count = localFiles.value.length;
  if (!count) {
    return "Drop files here or select files with the Choose Files button.";
  }
  return `${count} file${count === 1 ? "" : "s"} ready to import.`;
});

function getSupportedFiles(fileList) {
  return Array.from(fileList || []).filter(isSupportedImportFile);
}

async function getSupportedFilesFromDataTransfer(dataTransfer) {
  if (!dataTransfer) return [];
  return extractSupportedImportFilesFromDataTransfer(dataTransfer);
}

function openLocalPicker() {
  if (localInputRef.value) {
    localInputRef.value.click();
  }
}

function handleLocalChange(event) {
  const files = getSupportedFiles(event?.target?.files);
  localFiles.value = files;
  if (files.length) {
    triggerLocalImport(files);
  }
}

function clearLocalSelection() {
  localFiles.value = [];
  if (localInputRef.value) {
    localInputRef.value.value = "";
  }
}

async function triggerLocalImport(files) {
  if (!files.length) return;
  emit("update:open", false);
  await nextTick();
  emit("local-import", { files, projectId: selectedProjectId.value });
  clearLocalSelection();
}

function handleLocalDragEnter(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  dragActive.value = true;
}

function handleLocalDragOver(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  if (event?.dataTransfer) {
    event.dataTransfer.dropEffect = "copy";
  }
  dragActive.value = true;
}

function handleLocalDragLeave(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  if (!event?.currentTarget?.contains(event.relatedTarget)) {
    dragActive.value = false;
  }
}

async function handleLocalDrop(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  dragActive.value = false;
  const files = await getSupportedFilesFromDataTransfer(event?.dataTransfer);
  if (!files.length) return;
  triggerLocalImport(files);
}

async function fetchWatchFolders({ force = false } = {}) {
  if (importFoldersLoading.value) return;
  if (importFoldersLoaded.value && !force) return;
  importFoldersLoading.value = true;
  importFoldersError.value = "";
  try {
    const response = await apiClient.get("/import-folders");
    importFolders.value = response?.data?.folders || [];
    importFoldersLoaded.value = true;
  } catch (error) {
    importFoldersError.value =
      "Unable to load monitored folders. Check server connection.";
    importFolders.value = [];
  } finally {
    importFoldersLoading.value = false;
  }
}

watch(
  [dialogOpen, activeTab],
  ([isOpen, tab]) => {
    if (isOpen && tab === "monitoring") {
      fetchWatchFolders({ force: !importFoldersLoaded.value });
    }
  },
  { immediate: false },
);

watch(dialogOpen, (isOpen) => {
  if (isOpen) {
    importFoldersLoaded.value = false;
    fetchProjects();
  }
});
</script>

<template>
  <v-dialog v-model="dialogOpen" width="980">
    <div class="google-photos-shell">
      <v-btn
        icon
        size="36px"
        class="google-photos-close"
        @click="dialogOpen = false"
      >
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="google-photos-card">
        <v-card-title class="google-photos-title">
          <span class="import-title-text">Import photos to</span>
          <v-select
            :model-value="selectedProjectId"
            :items="projectSelectItems"
            item-title="title"
            item-value="value"
            variant="outlined"
            density="compact"
            hide-details
            single-line
            class="import-project-select"
            color="primary"
            @update:model-value="handleProjectChange"
          />
        </v-card-title>
        <v-card-text class="google-photos-body">
          <ProjectEditor
            :open="projectEditorOpen"
            :project="null"
            :backend-url="props.backendUrl"
            @close="projectEditorOpen = false"
            @saved="onProjectSaved"
          />
          <v-tabs v-model="activeTab" class="photo-import-tabs">
            <v-tab value="local">Local import</v-tab>
            <v-tab value="monitoring">Automatic Folder Monitoring</v-tab>
            <v-tab value="google">Google Photos</v-tab>
            <v-tab value="icloud">iCloud Photos</v-tab>
            <v-tab value="flickr">Flickr</v-tab>
          </v-tabs>
          <v-window v-model="activeTab" class="photo-import-window">
            <v-window-item value="local">
              <div class="google-photos-instructions">
                <div class="google-photos-section-title">Local files</div>
                <p class="google-photos-note">
                  Select images, videos, or ZIP archives to import.
                </p>
                <div
                  class="local-import-dropzone"
                  :class="{ 'is-dragging': dragActive }"
                  @dragenter.stop.prevent="handleLocalDragEnter"
                  @dragover.stop.prevent="handleLocalDragOver"
                  @dragleave.stop.prevent="handleLocalDragLeave"
                  @drop.stop.prevent="handleLocalDrop"
                >
                  <div class="local-import-dropzone-text">
                    {{ dropMessage }}
                  </div>
                </div>
                <div class="local-import-controls">
                  <input
                    ref="localInputRef"
                    class="local-import-input"
                    type="file"
                    multiple
                    accept="image/*,video/*,.zip,application/zip,application/x-zip-compressed,.txt,text/plain"
                    @change="handleLocalChange"
                  />
                  <v-btn variant="outlined" @click="openLocalPicker">
                    Choose Files
                  </v-btn>
                </div>
              </div>
            </v-window-item>
            <v-window-item value="monitoring">
              <div class="google-photos-instructions">
                <div class="google-photos-section-title">
                  Automatic Folder Monitoring
                </div>
                <p class="google-photos-note">
                  Import folders are managed directly in PixlStash.
                </p>
                <ol>
                  <li>Open the sidebar <strong>Folders</strong> tab.</li>
                  <li>
                    Click <strong>Add folder</strong> and choose
                    <strong>Import folder</strong>.
                  </li>
                  <li>
                    Set <strong>Delete source files after import</strong> if you
                    want one-way ingest behavior.
                  </li>
                </ol>
              </div>
            </v-window-item>
            <v-window-item value="google">
              <div class="google-photos-instructions">
                <div class="google-photos-section-title">
                  Google Takeout export
                </div>
                <ol>
                  <li>
                    Go to
                    <a
                      href="https://takeout.google.com/"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Google Takeout
                    </a>
                    and select Google Photos.
                  </li>
                  <li>Download the zip archive.</li>
                  <li>Drag the zip file into PixlStash to import.</li>
                </ol>
                <div class="google-photos-note">
                  Importable right now: Takeout zip files or extracted folders.
                </div>
              </div>
            </v-window-item>
            <v-window-item value="icloud">
              <div class="google-photos-instructions">
                <div class="google-photos-section-title">
                  iCloud Photos export
                </div>
                <ol>
                  <li>
                    Web: open
                    <a
                      href="https://www.icloud.com/photos/"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      iCloud Photos
                    </a>
                    , select your library, and download.
                  </li>
                  <li>
                    Mac: Photos → Settings → iCloud → enable "Download
                    Originals" and export unmodified originals.
                  </li>
                  <li>
                    Windows: install iCloud for Windows, enable Photos, and use
                    the synced download folder.
                  </li>
                  <li>Download the zip files from iCloud.</li>
                  <li>Drag the zip file into PixlStash to import.</li>
                </ol>
                <div class="google-photos-note">
                  Importable right now: iCloud zip files or extracted folders.
                </div>
              </div>
            </v-window-item>
            <v-window-item value="flickr">
              <div class="google-photos-instructions">
                <div class="google-photos-section-title">Flickr export</div>
                <ol>
                  <li>
                    Open
                    <a
                      href="https://www.flickr.com/account/data"
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Flickr Data Download
                    </a>
                    and request your archive.
                  </li>
                  <li>When it arrives, download the zip archive.</li>
                  <li>Drag the zip file into PixlStash to import.</li>
                </ol>
                <div class="google-photos-note">
                  Importable right now: Flickr zip files or extracted folders.
                </div>
              </div>
            </v-window-item>
          </v-window>
        </v-card-text>
      </v-card>
    </div>
  </v-dialog>
</template>

<style scoped>
.google-photos-shell {
  position: relative;
  padding: 16px;
}

.google-photos-close {
  position: absolute;
  top: 8px;
  right: 8px;
  z-index: 2;
}

.google-photos-card {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border-radius: 16px;
  min-height: 560px;
}

.google-photos-title {
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.import-title-text {
  white-space: nowrap;
}

.import-project-select {
  min-width: 150px;
  max-width: 260px;
  font-weight: 700;
}

.import-project-select :deep(.v-field__outline__start),
.import-project-select :deep(.v-field__outline__notch),
.import-project-select :deep(.v-field__outline__end) {
  border-color: rgba(var(--v-theme-primary), 0.5);
}

.import-project-select :deep(.v-field--focused .v-field__outline__start),
.import-project-select :deep(.v-field--focused .v-field__outline__notch),
.import-project-select :deep(.v-field--focused .v-field__outline__end) {
  border-color: rgb(var(--v-theme-primary));
}

.import-project-select :deep(.v-field__input) {
  color: rgb(var(--v-theme-primary));
  font-weight: 700;
  font-size: 1rem;
  min-height: unset;
  padding-top: 4px;
  padding-bottom: 4px;
}

.import-project-select :deep(.v-field) {
  background: rgba(var(--v-theme-primary), 0.1);
  border-radius: 6px;
}

.import-project-select :deep(.v-field:hover .v-field__outline__start),
.import-project-select :deep(.v-field:hover .v-field__outline__notch),
.import-project-select :deep(.v-field:hover .v-field__outline__end) {
  border-color: rgb(var(--v-theme-primary));
}

.import-project-select :deep(.v-select__selection-text) {
  color: rgb(var(--v-theme-primary));
  font-weight: 700;
}

.import-project-select :deep(.v-field__append-inner .v-icon) {
  color: rgb(var(--v-theme-primary));
  opacity: 0.8;
}

.google-photos-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.photo-import-tabs {
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.5);
}

.photo-import-window {
  margin-top: 8px;
}

.google-photos-section-title {
  font-weight: 600;
}

.google-photos-instructions {
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 12px;
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.google-photos-instructions ol {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 4px;
}

.google-photos-note {
  font-size: 0.92rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.local-import-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.local-import-input {
  display: none;
}

.local-import-dropzone {
  border: 2px dashed rgba(var(--v-theme-border), 0.6);
  border-radius: 12px;
  padding: 28px;
  min-height: 140px;
  text-align: center;
  background: rgba(var(--v-theme-on-surface), 0.03);
  transition:
    border-color 0.2s ease,
    background 0.2s ease;
}

.local-import-dropzone.is-dragging {
  border-color: rgba(var(--v-theme-primary), 0.7);
  background: rgba(var(--v-theme-primary), 0.08);
}

.local-import-dropzone-text {
  font-weight: 500;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.watch-folder-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.watch-folder-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 8px;
  color: rgba(var(--v-theme-on-surface), 0.8);
  font-size: 0.92rem;
}

.watch-folder-list-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.watch-folder-list-label {
  font-weight: 600;
}

.watch-folder-list-path {
  font-family:
    "SFMono-Regular", "Consolas", "Liberation Mono", "Menlo", monospace;
  font-size: 0.84rem;
  color: rgba(var(--v-theme-on-surface), 0.66);
}

.watch-folder-list-badge {
  display: inline-block;
  width: fit-content;
  padding: 1px 6px;
  border-radius: 999px;
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  background: rgba(var(--v-theme-warning), 0.16);
  color: rgb(var(--v-theme-warning));
}
</style>
