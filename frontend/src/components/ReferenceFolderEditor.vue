<template>
  <v-dialog :model-value="open" max-width="560" @click:outside="emit('close')">
    <div class="editor-shell">
      <v-btn icon size="36px" class="close-icon" @click="emit('close')">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="editor-card">
        <v-card-title class="editor-header">
          {{ folder ? "Edit Reference Folder" : "Add Reference Folder" }}
        </v-card-title>
        <v-card-text class="editor-body">
          <!-- Path (create mode only) -->
          <div v-if="!folder && !props.inDocker" class="editor-path-row">
            <v-text-field
              ref="pathInputRef"
              v-model="localPath"
              label="Folder path *"
              :placeholder="
                props.inDocker ? '/data/ref/pictures' : '/path/to/folder'
              "
              density="comfortable"
              variant="filled"
              hide-details
              @keydown.enter="save"
            />
            <v-btn
              v-if="!props.inDocker"
              variant="outlined"
              size="small"
              icon
              class="editor-browse-btn"
              title="Browse for folder"
              @click="openBrowse"
            >
              <v-icon size="18">mdi-folder-open-outline</v-icon>
            </v-btn>
          </div>

          <!-- Docker helper (create mode only) -->
          <div
            v-else-if="!folder && props.inDocker"
            class="editor-docker-helper"
          >
            <v-text-field
              ref="pathInputRef"
              v-model="localMountName"
              label="Folder name *"
              placeholder="pictures"
              density="comfortable"
              variant="filled"
              hide-details
              @keydown.enter="save"
            />
            <v-text-field
              v-model="localHostPath"
              label="Local folder (host path)"
              placeholder="/home/you/Pictures"
              density="comfortable"
              variant="filled"
              hide-details
            />
            <div class="editor-docker-path-row">
              <div class="editor-docker-path-label">Container path</div>
              <div
                class="editor-docker-path-value"
                :title="dockerSuggestedPath"
              >
                {{ dockerSuggestedPath }}
              </div>
              <v-btn
                variant="outlined"
                size="small"
                icon
                class="editor-copy-btn"
                title="Copy container path"
                @click="
                  copyToClipboard(dockerSuggestedPath, 'Container path copied.')
                "
              >
                <v-icon size="16">mdi-content-copy</v-icon>
              </v-btn>
            </div>
            <div class="editor-docker-instructions">
              <div class="editor-docker-title">Docker setup</div>
              <ol>
                <li>Add a mount to your docker run command:</li>
              </ol>
              <div class="editor-docker-snippet-wrap">
                <code class="editor-docker-snippet">{{
                  dockerMountSnippet
                }}</code>
                <v-btn
                  variant="outlined"
                  size="small"
                  icon
                  class="editor-copy-btn"
                  title="Copy mount snippet"
                  @click="
                    copyToClipboard(dockerMountSnippet, 'Mount snippet copied.')
                  "
                >
                  <v-icon size="16">mdi-content-copy</v-icon>
                </v-btn>
              </div>
              <div class="editor-docker-note">
                If restart fails because the container name already exists,
                remove the old container first:
              </div>
              <div class="editor-docker-note editor-docker-note--muted">
                Replace <code>pixlstash-gpu</code> below if your container uses
                a different name.
              </div>
              <div class="editor-docker-snippet-wrap">
                <code class="editor-docker-snippet">{{
                  dockerRemoveContainerSnippet
                }}</code>
                <v-btn
                  variant="outlined"
                  size="small"
                  icon
                  class="editor-copy-btn"
                  title="Copy remove-container command"
                  @click="
                    copyToClipboard(
                      dockerRemoveContainerSnippet,
                      'Remove-container command copied.',
                    )
                  "
                >
                  <v-icon size="16">mdi-content-copy</v-icon>
                </v-btn>
              </div>
              <div class="editor-docker-note">
                Full restart command (uses your local folder mapping):
              </div>
              <div class="editor-docker-snippet-wrap">
                <code class="editor-docker-snippet">{{
                  dockerRestartCommandSnippet
                }}</code>
                <v-btn
                  variant="outlined"
                  size="small"
                  icon
                  class="editor-copy-btn"
                  title="Copy full restart command"
                  @click="
                    copyToClipboard(
                      dockerRestartCommandSnippet,
                      'Restart command copied.',
                    )
                  "
                >
                  <v-icon size="16">mdi-content-copy</v-icon>
                </v-btn>
              </div>
              <ol start="2">
                <li>Restart the container.</li>
                <li>
                  Add this folder in PixlStash using the container path above.
                </li>
              </ol>
            </div>
            <div v-if="copyStatus" class="editor-copy-status">
              {{ copyStatus }}
            </div>
          </div>

          <!-- Path display (edit mode) -->
          <div v-else class="editor-path-display">
            <v-icon size="16" class="editor-path-icon"
              >mdi-folder-network-outline</v-icon
            >
            <span class="editor-path-text" :title="folder.folder">{{
              folder.folder
            }}</span>
          </div>

          <!-- Label -->
          <v-text-field
            v-model="localLabel"
            label="Display label"
            placeholder="Leave blank to use folder name"
            density="comfortable"
            variant="filled"
            hide-details
            @keydown.enter="save"
          />

          <!-- Sync captions (edit mode only) -->
          <div v-if="folder" class="editor-toggle-row">
            <v-checkbox
              v-model="localSyncCaptions"
              label="Sync caption files"
              density="compact"
              hide-details
            />
            <div class="editor-toggle-desc">
              Tag changes made in PixlStash are written back to a
              <code>.txt</code> sidecar file next to each image.
            </div>
          </div>

          <!-- Allow delete (edit mode only) -->
          <div v-if="folder" class="editor-toggle-row">
            <v-checkbox
              v-model="localAllowDelete"
              label="Allow deleting source files from PixlStash"
              density="compact"
              hide-details
              color="error"
            />
            <div
              v-if="localAllowDelete"
              class="editor-toggle-desc editor-toggle-desc--warning"
            >
              Warning: enabling this allows PixlStash to permanently delete
              files from your disk.
            </div>
          </div>

          <!-- Error -->
          <div v-if="saveError" class="editor-error">{{ saveError }}</div>

          <!-- Delete confirmation inline -->
          <div v-if="confirmingDelete" class="editor-delete-confirm">
            <v-icon size="18" color="error" style="flex-shrink: 0"
              >mdi-alert-circle-outline</v-icon
            >
            <div class="editor-delete-confirm-text">
              Remove <strong>{{ folder?.label || folder?.folder }}</strong> from
              PixlStash? The original files on disk will not be deleted.
            </div>
          </div>
        </v-card-text>
        <v-card-actions class="editor-footer">
          <v-btn
            v-if="folder && !confirmingDelete"
            variant="outlined"
            color="error"
            size="small"
            class="editor-delete-btn"
            :loading="deleteLoading"
            @click="confirmingDelete = true"
          >
            Remove
          </v-btn>
          <v-btn
            v-if="confirmingDelete"
            variant="flat"
            color="error"
            size="small"
            class="editor-delete-btn"
            :loading="deleteLoading"
            @click="doDelete"
          >
            Confirm Remove
          </v-btn>
          <v-btn
            v-if="confirmingDelete"
            variant="text"
            size="small"
            @click="confirmingDelete = false"
          >
            Cancel
          </v-btn>
          <v-spacer></v-spacer>
          <template v-if="!confirmingDelete">
            <v-btn class="btn-cancel" @click="emit('close')">Cancel</v-btn>
            <v-btn
              class="btn-save"
              :loading="saveLoading"
              :disabled="!isValid"
              @click="save"
            >
              {{ folder ? "Save" : "Add Folder" }}
            </v-btn>
          </template>
        </v-card-actions>
      </v-card>
    </div>
  </v-dialog>

  <!-- Browse dialog (non-Docker only) -->
  <v-dialog v-model="browseOpen" max-width="720">
    <v-card class="editor-card">
      <v-card-title class="editor-header">Browse for Folder</v-card-title>
      <v-card-text style="padding: 0">
        <div class="browse-path-bar">
          <v-icon size="16" style="opacity: 0.6; margin-right: 4px"
            >mdi-folder</v-icon
          >
          <span class="browse-path-text">{{ browsePath }}</span>
          <v-checkbox
            v-model="browseShowHidden"
            label="Show hidden"
            density="compact"
            hide-details
            class="browse-hidden-toggle"
          />
        </div>
        <div class="browse-entries">
          <div v-if="browseLoading" class="browse-loading">
            <v-progress-circular indeterminate size="24" />
          </div>
          <div v-else-if="browseError" class="browse-error">
            {{ browseError }}
          </div>
          <template v-else>
            <div
              v-if="browsePath && browsePath !== '/'"
              class="browse-entry browse-entry--up"
              @click="browseUp"
            >
              <v-icon size="16">mdi-arrow-up</v-icon>
              <span class="browse-entry-name">..</span>
            </div>
            <div
              v-for="entry in browseEntries"
              :key="entry.path"
              class="browse-entry"
              :class="{
                'browse-entry--disabled': !!browseEntryDisabledReason(
                  entry.path,
                ),
                'browse-entry--selected': browseSelected === entry.path,
              }"
              :title="browseEntryDisabledReason(entry.path) || entry.path"
              @click="browseEntryClick(entry)"
            >
              <v-icon size="16">{{
                entry.is_file ? "mdi-file-outline" : "mdi-folder"
              }}</v-icon>
              <span class="browse-entry-name">{{ entry.name }}</span>
              <span
                v-if="browseEntryDisabledReason(entry.path)"
                class="browse-entry-reason"
              >
                {{ browseEntryDisabledReason(entry.path) }}
              </span>
            </div>
          </template>
        </div>
      </v-card-text>
      <v-card-actions class="editor-footer">
        <v-spacer></v-spacer>
        <v-btn variant="text" @click="browseOpen = false">Cancel</v-btn>
        <v-btn
          variant="flat"
          color="primary"
          :disabled="!browsePath"
          @click="selectBrowsedPath"
        >
          Select "{{ browseSelectedName }}"
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, ref, watch, nextTick } from "vue";
import {
  VBtn,
  VCard,
  VCardActions,
  VCardText,
  VCardTitle,
  VCheckbox,
  VDialog,
  VIcon,
  VSpacer,
  VTextField,
} from "vuetify/components";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** null → create mode; an RF object → edit mode */
  folder: { type: Object, default: null },
  inDocker: { type: Boolean, default: false },
  /** Existing RF folder paths — used to disable already-registered entries in browse */
  registeredPaths: { type: Array, default: () => [] },
  imageRoot: { type: String, default: null },
});

const emit = defineEmits(["close", "saved", "deleted"]);

// --- Form state ---
const localPath = ref("");
const localLabel = ref("");
const localMountName = ref("pictures");
const localHostPath = ref("");
const localSyncCaptions = ref(false);
const localAllowDelete = ref(false);
const saveError = ref("");
const saveLoading = ref(false);
const deleteLoading = ref(false);
const confirmingDelete = ref(false);
const copyStatus = ref("");
let copyStatusTimer = null;
const pathInputRef = ref(null);

const isValid = computed(() => {
  if (props.folder) return true; // edit mode always valid
  if (props.inDocker) {
    return localMountName.value.trim().length > 0;
  }
  return localPath.value.trim().length > 0;
});

const normalizedRegisteredPaths = computed(() => {
  return new Set(
    (props.registeredPaths || []).map((path) =>
      String(path || "").replace(/\/+$/, ""),
    ),
  );
});

const dockerSuggestedPath = computed(() => {
  const raw = String(localMountName.value || "")
    .trim()
    .toLowerCase();
  const slug =
    raw
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+/, "")
      .replace(/-+$/, "") || "folder";
  const base = `/data/ref/${slug}`;
  if (!normalizedRegisteredPaths.value.has(base)) return base;
  let index = 2;
  while (normalizedRegisteredPaths.value.has(`${base}-${index}`)) {
    index += 1;
  }
  return `${base}-${index}`;
});

const dockerMountSnippet = computed(() => {
  const hostPath =
    String(localHostPath.value || "").trim() || "/absolute/host/path";
  return `-v ${hostPath}:${dockerSuggestedPath.value}`;
});

const dockerRemoveContainerSnippet = "docker rm -f pixlstash-gpu";

const dockerRestartCommandSnippet = computed(() => {
  return [
    "docker rm -f pixlstash-gpu 2>/dev/null || true &&",
    "docker run -d --runtime nvidia --user $(id -u):$(id -g)",
    "-e HOME=/home/pixlstash",
    "-e NVIDIA_VISIBLE_DEVICES=all",
    "-e NVIDIA_DRIVER_CAPABILITIES=compute,utility",
    "-e PIXLSTASH_HOST=0.0.0.0",
    "-p 9537:9537",
    "-v ~/Pictures/pixlstash:/home/pixlstash",
    dockerMountSnippet.value,
    "--name pixlstash-gpu",
    "ghcr.io/pikselkroken/pixlstash:1.1.0rc5-gpu",
  ].join(" ");
});

// Sync from props when dialog opens or folder changes
watch(
  [() => props.open, () => props.folder],
  ([isOpen]) => {
    if (isOpen) {
      confirmingDelete.value = false;
      saveError.value = "";
      if (props.folder) {
        localLabel.value = props.folder.label || "";
        localSyncCaptions.value = Boolean(props.folder.sync_captions);
        localAllowDelete.value = Boolean(props.folder.allow_delete_file);
      } else {
        localPath.value = props.inDocker ? dockerSuggestedPath.value : "";
        localLabel.value = "";
        localMountName.value = "pictures";
        localHostPath.value = "";
        localSyncCaptions.value = false;
        localAllowDelete.value = false;
        copyStatus.value = "";
        nextTick(() => {
          pathInputRef.value?.$el?.querySelector("input")?.focus();
        });
      }
    }
  },
  { immediate: true },
);

watch(
  [() => props.open, () => props.inDocker, dockerSuggestedPath],
  ([isOpen, inDocker]) => {
    if (!isOpen || props.folder || !inDocker) return;
    localPath.value = dockerSuggestedPath.value;
  },
  { immediate: true },
);

// Keyboard shortcut
function handleKeydown(event) {
  if (event.key === "Escape") emit("close");
}
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) document.addEventListener("keydown", handleKeydown);
    else document.removeEventListener("keydown", handleKeydown);
  },
);

async function save() {
  if (!isValid.value) return;
  saveLoading.value = true;
  saveError.value = "";
  try {
    if (props.folder) {
      // Edit mode
      await apiClient.patch(`/reference-folders/${props.folder.id}`, {
        label: localLabel.value.trim() || null,
        allow_delete_file: localAllowDelete.value,
        sync_captions: localSyncCaptions.value,
      });
    } else {
      // Create mode
      const pathToSave = props.inDocker
        ? dockerSuggestedPath.value
        : localPath.value.trim();
      await apiClient.post("/reference-folders", {
        folder: pathToSave,
        label: localLabel.value.trim() || undefined,
      });
    }
    emit("saved");
  } catch (e) {
    saveError.value =
      e?.response?.data?.detail || "Failed to save reference folder.";
  } finally {
    saveLoading.value = false;
  }
}

async function copyToClipboard(value, successMessage) {
  const text = String(value || "").trim();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    copyStatus.value = successMessage;
  } catch {
    copyStatus.value = "Copy failed. Please select and copy manually.";
  }
  if (copyStatusTimer) {
    clearTimeout(copyStatusTimer);
  }
  copyStatusTimer = setTimeout(() => {
    copyStatus.value = "";
    copyStatusTimer = null;
  }, 1800);
}

async function doDelete() {
  if (!props.folder) return;
  deleteLoading.value = true;
  try {
    await apiClient.delete(`/reference-folders/${props.folder.id}`);
    emit("deleted");
  } catch (e) {
    saveError.value =
      e?.response?.data?.detail || "Failed to remove reference folder.";
    confirmingDelete.value = false;
  } finally {
    deleteLoading.value = false;
  }
}

// --- Browse dialog ---
const browseOpen = ref(false);
const browsePath = ref("");
const browseEntries = ref([]);
const browseLoading = ref(false);
const browseError = ref("");
const browseShowHidden = ref(false);
const browseSelected = ref("");

const browseSelectedName = computed(() => {
  if (!browsePath.value) return "";
  const parts = browsePath.value.replace(/\/$/, "").split("/");
  return parts[parts.length - 1] || "/";
});

function browseEntryDisabledReason(entryPath) {
  const norm = entryPath.replace(/\/$/, "");
  if (props.imageRoot) {
    const root = props.imageRoot.replace(/\/$/, "");
    if (norm === root) return "PixlStash data folder";
  }
  for (const registered of props.registeredPaths) {
    if (norm === registered.replace(/\/$/, ""))
      return "Already a reference folder";
  }
  return null;
}

async function openBrowse() {
  browseError.value = "";
  browseEntries.value = [];
  browsePath.value = "";
  browseShowHidden.value = false;
  browseSelected.value = "";
  browseOpen.value = true;
  await browseDir(null);
}

async function browseDir(path) {
  browseLoading.value = true;
  browseError.value = "";
  try {
    const res = await apiClient.get("/filesystem/browse", {
      params: { path: path ?? undefined, show_hidden: browseShowHidden.value },
    });
    browseEntries.value = res.data?.entries ?? [];
    browsePath.value = res.data?.path ?? path ?? "/";
    browseSelected.value = "";
  } catch (e) {
    browseError.value =
      e?.response?.data?.detail || "Cannot browse this directory.";
    browseEntries.value = [];
  } finally {
    browseLoading.value = false;
  }
}

watch(browseShowHidden, () => {
  if (browseOpen.value) browseDir(browsePath.value || null);
});

function browseEntryClick(entry) {
  if (entry.is_file) return;
  if (browseEntryDisabledReason(entry.path)) return;
  browseDir(entry.path);
}

function browseUp() {
  if (!browsePath.value || browsePath.value === "/") return;
  const parent =
    browsePath.value.replace(/\/$/, "").split("/").slice(0, -1).join("/") ||
    "/";
  browseDir(parent);
}

function selectBrowsedPath() {
  localPath.value = browsePath.value;
  browseOpen.value = false;
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) return;
    if (copyStatusTimer) {
      clearTimeout(copyStatusTimer);
      copyStatusTimer = null;
    }
    copyStatus.value = "";
  },
);
</script>

<style scoped>
.editor-shell {
  position: relative;
  width: 100%;
}

.editor-card {
  overflow: hidden;
}

.close-icon {
  position: absolute;
  top: -16px;
  right: -16px;
  background-color: rgb(var(--v-theme-primary));
  border: none;
  color: rgb(var(--v-theme-on-primary));
  cursor: pointer;
  z-index: 2;
}

.close-icon:hover {
  background-color: rgb(var(--v-theme-accent));
}

.editor-header {
  font-size: 1.1rem;
  font-weight: 600;
  padding: 20px 20px 8px;
}

.editor-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 20px 8px;
}

.editor-path-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.editor-path-row .v-text-field {
  flex: 1;
}

.editor-browse-btn {
  margin-top: 4px;
  flex-shrink: 0;
}

.editor-docker-helper {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.editor-docker-path-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(var(--v-theme-surface-variant), 0.35);
}

.editor-docker-path-label {
  font-size: 0.74rem;
  opacity: 0.7;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}

.editor-docker-path-value {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
  font-size: 0.82rem;
}

.editor-copy-btn {
  flex-shrink: 0;
}

.editor-docker-instructions {
  border: 1px solid rgba(var(--v-theme-primary), 0.22);
  background: rgba(var(--v-theme-primary), 0.06);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 0.8rem;
  line-height: 1.35;
}

.editor-docker-title {
  font-size: 0.82rem;
  font-weight: 600;
  margin-bottom: 6px;
}

.editor-docker-note {
  margin-top: 2px;
}

.editor-docker-note--muted {
  opacity: 0.78;
}

.editor-docker-instructions ol {
  margin: 0;
  padding-left: 16px;
}

.editor-docker-snippet-wrap {
  margin: 6px 0;
  display: flex;
  align-items: center;
  gap: 8px;
}

.editor-docker-snippet {
  flex: 1;
  display: block;
  padding: 6px 8px;
  border-radius: 6px;
  background: rgba(var(--v-theme-dark-surface), 0.55);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: 0.75rem;
  white-space: nowrap;
  overflow: auto hidden;
}

.editor-copy-status {
  font-size: 0.76rem;
  color: rgb(var(--v-theme-accent));
}

.editor-path-display {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: rgba(var(--v-theme-surface-variant), 0.4);
  border-radius: 6px;
  font-size: 0.82rem;
  opacity: 0.85;
}

.editor-path-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.editor-path-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
}

.editor-toggle-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.editor-toggle-desc {
  font-size: 0.76rem;
  opacity: 0.65;
  padding-left: 36px;
}

.editor-toggle-desc--warning {
  color: rgb(var(--v-theme-error));
  opacity: 1;
}

.editor-error {
  color: rgb(var(--v-theme-error));
  font-size: 0.85rem;
  padding: 4px 0;
}

.editor-delete-confirm {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  background: rgba(var(--v-theme-error), 0.08);
  border-radius: 6px;
  border: 1px solid rgba(var(--v-theme-error), 0.25);
  font-size: 0.86rem;
}

.editor-delete-confirm-text {
  line-height: 1.4;
}

.editor-footer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px 16px;
}

.editor-delete-btn {
  flex-shrink: 0;
}

.btn-cancel {
  background: rgb(var(--v-theme-cancel-button));
  color: rgb(var(--v-theme-cancel-button-text));
  transition: filter 0.2s;
}

.btn-cancel:hover {
  filter: brightness(1.2);
}

.btn-save {
  background: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  transition: filter 0.2s;
}

/* Browse dialog */
.browse-path-bar {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.2);
  gap: 4px;
  font-size: 0.82rem;
}

.browse-path-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: monospace;
  opacity: 0.9;
}

.browse-hidden-toggle {
  flex-shrink: 0;
  font-size: 0.78rem;
}

.browse-entries {
  max-height: 360px;
  overflow-y: auto;
  padding: 4px 0;
}

.browse-loading {
  display: flex;
  justify-content: center;
  padding: 24px;
}

.browse-error {
  color: rgb(var(--v-theme-error));
  padding: 12px 16px;
  font-size: 0.85rem;
}

.browse-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 16px;
  cursor: pointer;
  font-size: 0.88rem;
  transition: background 0.15s;
}

.browse-entry:hover:not(.browse-entry--disabled) {
  background: rgba(var(--v-theme-primary), 0.06);
}

.browse-entry--up {
  opacity: 0.7;
  font-style: italic;
}

.browse-entry--disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.browse-entry--selected {
  background: rgba(var(--v-theme-primary), 0.1);
}

.browse-entry-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.browse-entry-reason {
  font-size: 0.74rem;
  opacity: 0.6;
  font-style: italic;
  flex-shrink: 0;
}
</style>
