<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { isSupportedImportFile } from "../utils/media.js";
import { apiClient } from "../utils/apiClient.js";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const emit = defineEmits(["update:open", "local-import"]);

const dialogOpen = computed({
  get: () => props.open,
  set: (value) => emit("update:open", value),
});

const activeTab = ref("local");
const localInputRef = ref(null);
const localFiles = ref([]);
const dragActive = ref(false);
const watchFolders = ref([]);
const watchFoldersPath = ref("");
const watchFoldersLoading = ref(false);
const watchFoldersError = ref("");
const watchFoldersLoaded = ref(false);
const watchFoldersOpening = ref(false);

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

function getSupportedFilesFromDataTransfer(dataTransfer) {
  if (!dataTransfer) return [];

  const directFiles = getSupportedFiles(dataTransfer.files);
  const fromItems = Array.from(dataTransfer.items || [])
    .filter((item) => item?.kind === "file")
    .map((item) => item.getAsFile?.())
    .filter(Boolean)
    .filter(isSupportedImportFile);

  const unique = new Map();
  for (const file of [...directFiles, ...fromItems]) {
    const key = `${file.name}::${file.size}::${file.lastModified}`;
    if (!unique.has(key)) {
      unique.set(key, file);
    }
  }

  return Array.from(unique.values());
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
  emit("local-import", files);
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

function handleLocalDrop(event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  dragActive.value = false;
  const files = getSupportedFilesFromDataTransfer(event?.dataTransfer);
  if (!files.length) return;
  triggerLocalImport(files);
}

async function fetchWatchFolders({ force = false } = {}) {
  if (watchFoldersLoading.value) return;
  if (watchFoldersLoaded.value && !force) return;
  watchFoldersLoading.value = true;
  watchFoldersError.value = "";
  try {
    const response = await apiClient.get("/server-config/watch-folders");
    watchFolders.value = response?.data?.watch_folders || [];
    watchFoldersPath.value = response?.data?.config_path || "";
    watchFoldersLoaded.value = true;
  } catch (error) {
    watchFoldersError.value =
      "Unable to load monitored folders. Check server connection.";
    watchFolders.value = [];
    watchFoldersPath.value = "";
  } finally {
    watchFoldersLoading.value = false;
  }
}

async function openServerConfigInOS() {
  if (watchFoldersOpening.value) return;
  watchFoldersOpening.value = true;
  watchFoldersError.value = "";
  try {
    await apiClient.post("/server-config/open");
  } catch (error) {
    watchFoldersError.value = "Unable to open the server-config.json file.";
  } finally {
    watchFoldersOpening.value = false;
  }
}

watch(
  [dialogOpen, activeTab],
  ([isOpen, tab]) => {
    if (isOpen && tab === "monitoring") {
      fetchWatchFolders({ force: !watchFoldersLoaded.value });
    }
  },
  { immediate: false },
);

watch(dialogOpen, (isOpen) => {
  if (isOpen) {
    watchFoldersLoaded.value = false;
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
        <v-card-title class="google-photos-title">Import photos</v-card-title>
        <v-card-text class="google-photos-body">
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
                    accept="image/*,video/*,.zip,application/zip,application/x-zip-compressed"
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
                  Add folders to the server-config.json file under
                  <strong>watch_folders</strong> to auto-import new files.
                </p>
                <ol>
                  <li>Open the server-config.json file on the server.</li>
                  <li>
                    Add an entry to <strong>watch_folders</strong> with the
                    folder path and optional flags.
                  </li>
                  <li>Restart the server to apply changes.</li>
                </ol>
                <div class="watch-folder-snippet">
                  <pre>
{
  "watch_folders": [
    { "folder": "/path/to/photos", "delete_after_import": false }
  ]
}</pre
                  >
                </div>
                <p class="google-photos-note">
                  Server config file (full path):
                  <span v-if="watchFoldersPath" class="watch-folder-path">
                    {{ watchFoldersPath }}
                  </span>
                  <v-btn
                    v-if="watchFoldersPath"
                    size="x-small"
                    color="primary"
                    variant="elevated"
                    class="watch-folder-open-btn"
                    :disabled="watchFoldersOpening"
                    @click="openServerConfigInOS"
                  >
                    Open with...
                  </v-btn>
                  <span v-else class="watch-folder-path">(unavailable)</span>
                </p>
                <div class="watch-folder-section">
                  <div class="google-photos-section-title">
                    Currently monitored folders
                  </div>
                  <div v-if="watchFoldersLoading" class="google-photos-note">
                    Loading monitored folders...
                  </div>
                  <div v-else-if="watchFoldersError" class="google-photos-note">
                    {{ watchFoldersError }}
                  </div>
                  <ul v-else-if="watchFolders.length" class="watch-folder-list">
                    <li v-for="folder in watchFolders" :key="folder">
                      {{ folder }}
                    </li>
                  </ul>
                  <div v-else class="google-photos-note">
                    No monitored folders yet.
                  </div>
                </div>
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
                  <li>Drag the zip file into PixlVault to import.</li>
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
                  <li>Drag the zip file into PixlVault to import.</li>
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
                  <li>Drag the zip file into PixlVault to import.</li>
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

.watch-folder-snippet {
  background: rgba(var(--v-theme-on-surface), 0.04);
  border-radius: 10px;
  border: 1px solid rgba(var(--v-theme-border), 0.35);
  padding: 10px 12px;
  font-size: 0.86rem;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.watch-folder-snippet pre {
  margin: 0;
  font-family:
    "SFMono-Regular", "Consolas", "Liberation Mono", "Menlo", monospace;
  white-space: pre-wrap;
}

.watch-folder-path {
  font-family:
    "SFMono-Regular", "Consolas", "Liberation Mono", "Menlo", monospace;
  font-size: 0.9rem;
}

.watch-folder-open-btn {
  margin-left: 8px;
  text-transform: none;
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
  gap: 4px;
  color: rgba(var(--v-theme-on-surface), 0.8);
  font-size: 0.92rem;
}
</style>
