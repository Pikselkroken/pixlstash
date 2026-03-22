<template>
  <div class="pf-wrapper">
    <!-- Header bar: always visible, also acts as drop target when collapsed -->
    <div
      class="pf-header"
      :class="{ 'pf-drag-active': dragOver && !expanded }"
      @click="toggleExpanded"
      @dragover.prevent="onDragOver"
      @dragleave="onDragLeave"
      @drop.prevent="onDrop"
    >
      <v-icon size="15" class="pf-header-icon">mdi-folder-multiple-outline</v-icon>
      <span class="pf-title">Project Files</span>
      <span v-if="files.length > 0" class="pf-count">{{ files.length }}</span>
      <span class="pf-spacer"></span>
      <v-icon size="14" class="pf-chevron">
        {{ expanded ? "mdi-chevron-up" : "mdi-chevron-down" }}
      </v-icon>
    </div>

    <!-- Expanded panel -->
    <div
      v-if="expanded"
      class="pf-panel"
      :class="{ 'pf-drag-active': dragOver }"
      @dragover.prevent="onDragOver"
      @dragleave="onDragLeave"
      @drop.prevent="onDrop"
    >
      <!-- Drag overlay -->
      <div v-if="dragOver" class="pf-drop-overlay">
        <v-icon size="36">mdi-upload-outline</v-icon>
        <span>Drop files or URLs here</span>
      </div>

      <!-- Empty state -->
      <div v-else-if="files.length === 0 && !uploading" class="pf-empty">
        <v-icon size="30" class="pf-empty-icon">mdi-upload-outline</v-icon>
        <span>Drag files or URLs here to add</span>
        <button class="pf-add-url-btn pf-add-url-btn--empty" @click.stop="showUrlForm = !showUrlForm">
          <v-icon size="13">mdi-link-plus</v-icon>
          Add a URL
        </button>
      </div>

      <!-- Uploading indicator -->
      <div v-else-if="uploading" class="pf-uploading">
        <v-progress-circular indeterminate size="20" width="2" />
        <span>Uploading…</span>
      </div>

      <!-- File grid -->
      <div v-if="files.length > 0 && !dragOver" class="pf-grid">
        <div
          v-for="file in files"
          :key="file.id"
          class="pf-file-card"
          :class="{ 'pf-url-card': file.url }"
          :title="file.url || file.original_filename"
          @click="openFile(file)"
        >
          <button
            class="pf-file-delete"
            title="Remove"
            @click.stop="deleteFile(file)"
          >
            <v-icon size="13">mdi-close</v-icon>
          </button>
          <v-icon size="34" class="pf-file-icon">{{ fileIcon(file) }}</v-icon>
          <div class="pf-file-name">{{ file.url ? urlLabel(file) : file.original_filename }}</div>
          <div v-if="!file.url" class="pf-file-meta">
            {{ formatBytes(file.file_size) }}<br />{{ formatDate(file.created_at) }}
          </div>
        </div>
      </div>

      <!-- Drop hint + add URL when files already exist -->
      <div v-if="files.length > 0 && !dragOver" class="pf-drop-hint">
        <v-icon size="12">mdi-upload-outline</v-icon>
        Drag more files or URLs here
        <span class="pf-hint-sep">·</span>
        <button class="pf-add-url-btn" @click.stop="showUrlForm = !showUrlForm">
          <v-icon size="12">mdi-link-plus</v-icon>
          Add URL
        </button>
      </div>

      <!-- Add URL form -->
      <div v-if="showUrlForm && !dragOver" class="pf-url-form">
        <input
          ref="urlInputEl"
          v-model="urlInput"
          class="pf-url-input"
          placeholder="https://..."
          @keydown.enter="addUrl"
          @keydown.escape="showUrlForm = false"
        />
        <input
          v-model="urlTitle"
          class="pf-url-input"
          placeholder="Label (optional)"
          @keydown.enter="addUrl"
          @keydown.escape="showUrlForm = false"
        />
        <div class="pf-url-form-actions">
          <button class="pf-url-save" @click="addUrl" :disabled="!urlInput.trim()">Add</button>
          <button class="pf-url-cancel" @click="showUrlForm = false">Cancel</button>
        </div>
      </div>

      <!-- Error -->
      <div v-if="uploadError" class="pf-error">{{ uploadError }}</div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, nextTick } from "vue";
import { VProgressCircular } from "vuetify/components";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  projectId: { type: Number, required: true },
  backendUrl: { type: String, required: true },
});

const expanded = ref(false);
const files = ref([]);
const dragOver = ref(false);
const uploading = ref(false);
const uploadError = ref(null);
const showUrlForm = ref(false);
const urlInput = ref("");
const urlTitle = ref("");
const urlInputEl = ref(null);

let dragLeaveTimer = null;

async function fetchFiles() {
  try {
    const resp = await apiClient.get(`/projects/${props.projectId}/attachments`);
    files.value = resp.data;
  } catch {
    files.value = [];
  }
}

function toggleExpanded() {
  expanded.value = !expanded.value;
  if (expanded.value && files.value.length === 0) {
    fetchFiles();
  }
  if (!expanded.value) {
    showUrlForm.value = false;
  }
}

function onDragOver(e) {
  clearTimeout(dragLeaveTimer);
  dragOver.value = true;
}

function onDragLeave() {
  // Small debounce to avoid flicker when moving between child elements
  dragLeaveTimer = setTimeout(() => {
    dragOver.value = false;
  }, 80);
}

async function onDrop(e) {
  dragOver.value = false;

  // Auto-expand when something is dropped onto the collapsed header
  if (!expanded.value) expanded.value = true;

  // --- URL drop (dragging a tab or link from a browser) ---
  const dt = e.dataTransfer;
  const uriList = dt?.getData("text/uri-list") || dt?.getData("text/plain") || "";
  const droppedUrls = uriList
    .split(/\r?\n/)
    .map((u) => u.trim())
    .filter((u) => u && !u.startsWith("#") && /^https?:\/\//.test(u));

  if (droppedUrls.length) {
    uploadError.value = null;
    try {
      for (const url of droppedUrls) {
        const resp = await apiClient.post(
          `/projects/${props.projectId}/attachments/url`,
          { url, title: url },
        );
        files.value.push(resp.data);
      }
    } catch (err) {
      uploadError.value = err?.response?.data?.detail ?? "Could not save URL.";
    }
    return;
  }

  // --- File drop ---
  const droppedFiles = Array.from(dt?.files ?? []);
  if (!droppedFiles.length) return;

  uploadError.value = null;
  uploading.value = true;
  try {
    for (const file of droppedFiles) {
      const formData = new FormData();
      formData.append("file", file);
      await apiClient.post(`/projects/${props.projectId}/attachments`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    }
    await fetchFiles();
  } catch (err) {
    uploadError.value =
      err?.response?.data?.detail ?? "Upload failed. Please try again.";
  } finally {
    uploading.value = false;
  }
}

function openFile(file) {
  if (file.url) {
    window.open(file.url, "_blank", "noopener,noreferrer");
  } else {
    downloadFile(file);
  }
}

function downloadFile(file) {
  const url = `${props.backendUrl}/projects/${props.projectId}/attachments/${file.id}`;
  const a = document.createElement("a");
  a.href = url;
  a.download = file.original_filename;
  a.click();
}

async function deleteFile(file) {
  if (!window.confirm(`Remove "${file.original_filename}"?`)) return;
  try {
    await apiClient.delete(
      `/projects/${props.projectId}/attachments/${file.id}`
    );
    files.value = files.value.filter((f) => f.id !== file.id);
  } catch {
    uploadError.value = "Could not delete file. Please try again.";
  }
}

async function addUrl() {
  const url = urlInput.value.trim();
  if (!url) return;
  const title = urlTitle.value.trim() || url;
  uploadError.value = null;
  try {
    const resp = await apiClient.post(
      `/projects/${props.projectId}/attachments/url`,
      { url, title },
    );
    files.value.push(resp.data);
    urlInput.value = "";
    urlTitle.value = "";
    showUrlForm.value = false;
  } catch (err) {
    uploadError.value = err?.response?.data?.detail ?? "Could not save URL.";
  }
}

function urlLabel(file) {
  const name = file.original_filename || "";
  // If no custom label was given (original_filename is the raw URL), strip the protocol
  if (name === file.url || /^https?:\/\//.test(name)) {
    return name.replace(/^https?:\/\//, "").replace(/\/$/, "");
  }
  return name;
}

function shortenUrl(url) {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 24);
  }
}

function fileIcon(file) {
  if (file.url) return "mdi-link-variant";
  const mimeType = file.mime_type;
  if (!mimeType) return "mdi-file-outline";
  if (mimeType.startsWith("image/")) return "mdi-file-image-outline";
  if (mimeType.startsWith("video/")) return "mdi-file-video-outline";
  if (mimeType.startsWith("audio/")) return "mdi-file-music-outline";
  if (mimeType === "application/pdf") return "mdi-file-pdf-box";
  if (
    mimeType.startsWith("text/") ||
    mimeType === "application/json" ||
    mimeType === "application/xml"
  )
    return "mdi-file-document-outline";
  if (
    mimeType.includes("zip") ||
    mimeType.includes("tar") ||
    mimeType.includes("rar") ||
    mimeType.includes("7z")
  )
    return "mdi-folder-zip-outline";
  return "mdi-file-outline";
}

function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function formatDate(dateStr) {
  if (!dateStr) return "";
  try {
    return new Date(dateStr).toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return dateStr;
  }
}

watch(
  () => showUrlForm.value,
  (val) => {
    if (val) {
      nextTick(() => urlInputEl.value?.focus());
    } else {
      urlInput.value = "";
      urlTitle.value = "";
    }
  },
);

watch(
  () => props.projectId,
  () => {
    files.value = [];
    uploadError.value = null;
    if (expanded.value) fetchFiles();
  }
);

onMounted(() => {
  fetchFiles();
});
</script>

<style scoped>
.pf-wrapper {
  margin: 4px 8px 8px;
  border-radius: 6px;
  overflow: visible;
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  background: rgba(var(--v-theme-surface), 0.2);
}

.pf-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 10px;
  cursor: pointer;
  border-radius: 6px;
  user-select: none;
  transition: background 0.15s;
  color: rgb(var(--v-theme-sidebar-text));
  font-size: 0.88rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  white-space: nowrap;
}

.pf-header:hover {
  background: rgba(var(--v-theme-surface), 0.4);
}

.pf-header.pf-drag-active {
  background: rgba(var(--v-theme-accent), 0.2);
  border-color: rgb(var(--v-theme-accent));
}

.pf-header-icon {
  color: rgb(var(--v-theme-sidebar-text)) !important;
  opacity: 0.7;
}

.pf-title {
  flex: 1;
}

.pf-count {
  background: rgba(var(--v-theme-accent), 0.25);
  color: rgb(var(--v-theme-accent));
  font-size: 0.72rem;
  font-weight: 700;
  padding: 0 5px;
  border-radius: 8px;
  min-width: 18px;
  text-align: center;
}

.pf-spacer {
  flex: 1;
}

.pf-chevron {
  opacity: 0.5;
  color: rgb(var(--v-theme-sidebar-text)) !important;
}

/* ---- Panel ---- */
.pf-panel {
  position: relative;
  border-top: 1px solid rgba(var(--v-theme-border), 0.3);
  padding: 8px;
  min-height: 72px;
  background: rgb(var(--v-theme-surface));
  border-radius: 0 0 5px 5px;
  box-shadow: inset 1px 1px 3px rgba(var(--v-theme-shadow), 0.12);
}

.pf-panel.pf-drag-active {
  background: rgba(var(--v-theme-accent), 0.06);
}

.pf-drop-overlay {
  position: absolute;
  inset: 0;
  z-index: 10;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background: rgba(var(--v-theme-accent), 0.15);
  border-radius: 0 0 6px 6px;
  color: rgb(var(--v-theme-accent));
  font-size: 0.85rem;
  font-weight: 600;
  border: 2px dashed rgb(var(--v-theme-accent));
  pointer-events: none;
}

.pf-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 72px;
  color: rgba(var(--v-theme-on-surface), 0.4);
  font-size: 0.8rem;
}

.pf-empty-icon {
  color: rgba(var(--v-theme-on-surface), 0.25) !important;
}

.pf-uploading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 72px;
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-size: 0.85rem;
}

/* ---- File grid ---- */
.pf-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(72px, 1fr));
  gap: 2px;
}

.pf-file-card {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 8px 4px 6px;
  border-radius: 2px;
  cursor: pointer;
  background: transparent;
  border: 1px solid transparent;
  transition: background 0.1s, border-color 0.1s;
  overflow: hidden;
  text-align: center;
}

.pf-file-card:hover {
  background: rgba(var(--v-theme-accent), 0.15);
  border-color: rgba(var(--v-theme-accent), 0.4);
}

.pf-file-card:hover .pf-file-delete {
  opacity: 0.45;
}

.pf-file-delete {
  position: absolute;
  top: 2px;
  right: 2px;
  background: rgba(var(--v-theme-surface), 0.6);
  border: none;
  border-radius: 50%;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.12s, background 0.12s;
  color: rgba(var(--v-theme-on-surface), 0.5);
  padding: 0;
}

.pf-file-delete:hover {
  background: rgba(220, 80, 60, 0.8);
  color: #fff;
  opacity: 1 !important;
}

.pf-file-icon {
  color: rgba(var(--v-theme-on-surface), 0.65) !important;
  margin-bottom: 4px;
}

.pf-file-name {
  font-size: 0.7rem;
  line-height: 1.2;
  word-break: break-all;
  max-width: 100%;
  overflow: hidden;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  color: rgb(var(--v-theme-on-surface));
}

.pf-file-meta {
  font-size: 0.65rem;
  color: rgba(var(--v-theme-on-surface), 0.4);
  margin-top: 3px;
  line-height: 1.3;
}

/* ---- Footer hints ---- */
.pf-drop-hint {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  font-size: 0.72rem;
  color: rgba(var(--v-theme-on-surface), 0.3);
  margin-top: 6px;
}

.pf-error {
  font-size: 0.78rem;
  color: rgb(200, 50, 40);
  text-align: center;
  margin-top: 6px;
  padding: 4px 8px;
  background: rgba(200, 50, 40, 0.08);
  border-radius: 4px;
}

/* ---- URL cards ---- */
.pf-url-card .pf-file-icon {
  color: rgba(var(--v-theme-accent), 0.75) !important;
}

.pf-url-meta {
  font-size: 0.65rem;
  color: rgba(var(--v-theme-accent), 0.6);
  margin-top: 3px;
  line-height: 1.3;
  word-break: break-all;
  max-width: 100%;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* ---- Add URL button (inline hint) ---- */
.pf-hint-sep {
  opacity: 0.4;
}

.pf-add-url-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  background: none;
  border: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: 0.72rem;
  padding: 0;
  transition: color 0.15s;
}

.pf-add-url-btn:hover {
  color: rgba(var(--v-theme-accent), 0.9);
}

.pf-add-url-btn--empty {
  font-size: 0.8rem;
  color: rgba(var(--v-theme-on-surface), 0.35);
  margin-top: 4px;
}

/* ---- URL form ---- */
.pf-url-form {
  display: flex;
  flex-direction: column;
  gap: 5px;
  padding: 6px 4px 2px;
}

.pf-url-input {
  width: 100%;
  background: rgba(var(--v-theme-on-surface), 0.06);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  border-radius: 4px;
  padding: 4px 7px;
  font-size: 0.78rem;
  color: rgb(var(--v-theme-on-surface));
  outline: none;
  box-sizing: border-box;
  transition: border-color 0.15s;
}

.pf-url-input:focus {
  border-color: rgba(var(--v-theme-accent), 0.6);
}

.pf-url-form-actions {
  display: flex;
  gap: 5px;
  justify-content: flex-end;
}

.pf-url-save,
.pf-url-cancel {
  font-size: 0.75rem;
  padding: 3px 10px;
  border-radius: 4px;
  border: none;
  cursor: pointer;
  transition: background 0.12s, opacity 0.12s;
}

.pf-url-save {
  background: rgba(var(--v-theme-accent), 0.85);
  color: #fff;
}

.pf-url-save:hover:not(:disabled) {
  background: rgb(var(--v-theme-accent));
}

.pf-url-save:disabled {
  opacity: 0.4;
  cursor: default;
}

.pf-url-cancel {
  background: rgba(var(--v-theme-on-surface), 0.08);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.pf-url-cancel:hover {
  background: rgba(var(--v-theme-on-surface), 0.14);
}
</style>
