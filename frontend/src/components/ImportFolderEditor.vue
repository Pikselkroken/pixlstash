<template>
  <v-dialog :model-value="open" max-width="560" @click:outside="emit('close')">
    <div class="editor-shell">
      <v-btn icon size="36px" class="close-icon" @click="emit('close')">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="editor-card">
        <v-card-title class="editor-header">
          {{ isEditMode ? "Edit Import Folder" : "Add Import Folder" }}
        </v-card-title>
        <v-card-text class="editor-body">
          <div v-if="!isEditMode && !props.inDocker" class="editor-path-row">
            <v-text-field
              ref="pathInputRef"
              v-model="localPath"
              label="Folder path *"
              :placeholder="
                props.inDocker ? '/host/path/to/folder' : '/path/to/folder'
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

          <div
            v-else-if="!isEditMode && props.inDocker"
            class="editor-docker-helper"
          >
            <v-text-field
              ref="pathInputRef"
              v-model="localHostPath"
              label="Local folder (host path)"
              placeholder="/home/you/Pictures/import"
              density="comfortable"
              variant="filled"
              hide-details
              @keydown.enter="save"
            />

            <v-text-field
              v-model="localLabel"
              label="Display name"
              placeholder="Auto-filled from host path (editable)"
              density="comfortable"
              variant="filled"
              hide-details
              @keydown.enter="save"
            />

            <div class="editor-docker-path-row">
              <div class="editor-docker-path-label">Container path</div>
              <div class="editor-docker-path-value">{{ localPath }}</div>
            </div>

            <div class="editor-docker-title">Docker mount line</div>
            <div class="editor-docker-note">
              Add this <code>-v</code> mount to your <code>docker run</code>
              command:
            </div>
            <div class="editor-docker-snippet-wrap">
              <code class="editor-docker-snippet">{{
                dockerMountSnippet
              }}</code>
              <v-btn
                variant="outlined"
                size="small"
                icon
                class="editor-copy-btn"
                title="Copy mount line"
                @click="
                  copyToClipboard(dockerMountSnippet, 'Mount line copied.')
                "
              >
                <v-icon size="16">mdi-content-copy</v-icon>
              </v-btn>
            </div>

            <div class="editor-docker-title">Container restart helpers</div>
            <div class="editor-docker-note editor-docker-note--muted">
              Replace <code>pixlstash-gpu</code> below if your container uses a
              different name. This removes the old container, not the image.
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
            <div
              v-if="hasExistingFolderMounts"
              class="editor-docker-note editor-docker-note--muted"
            >
              Existing reference and import folder mounts are included.
            </div>
            <div class="editor-docker-snippet-wrap">
              <code class="editor-docker-snippet editor-docker-snippet--full">{{
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

            <div v-if="copyStatus" class="editor-copy-status">
              {{ copyStatus }}
            </div>
          </div>

          <div v-else class="editor-path-display">
            <v-icon size="16" class="editor-path-icon"
              >mdi-folder-import</v-icon
            >
            <span class="editor-path-text" :title="activeFolder?.folder">{{
              activeFolder?.folder
            }}</span>
          </div>

          <div
            v-if="isEditMode && props.inDocker"
            class="editor-docker-instructions"
          >
            <div class="editor-docker-title">Docker restart command</div>
            <div class="editor-docker-note">
              Copy this if you need to restart with the current folder mounts.
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
            <div
              v-if="hasExistingFolderMounts"
              class="editor-docker-note editor-docker-note--muted"
            >
              Existing reference and import folder mounts are included.
            </div>
            <div class="editor-docker-snippet-wrap">
              <code class="editor-docker-snippet editor-docker-snippet--full">{{
                dockerEditRestartCommandSnippet
              }}</code>
              <v-btn
                variant="outlined"
                size="small"
                icon
                class="editor-copy-btn"
                title="Copy full restart command"
                @click="
                  copyToClipboard(
                    dockerEditRestartCommandSnippet,
                    'Restart command copied.',
                  )
                "
              >
                <v-icon size="16">mdi-content-copy</v-icon>
              </v-btn>
            </div>
          </div>

          <v-text-field
            v-if="isEditMode || !props.inDocker"
            v-model="localLabel"
            label="Display label"
            placeholder="Leave blank to use folder name"
            density="comfortable"
            variant="filled"
            hide-details
            @keydown.enter="save"
          />

          <div class="editor-toggle-row">
            <v-checkbox
              v-model="localDeleteAfterImport"
              label="Delete source files after successful import"
              density="compact"
              hide-details
              color="warning"
            />
            <div v-if="localDeleteAfterImport" class="editor-toggle-desc">
              Imported files are removed from this folder after they are added
              to PixlStash.
            </div>
          </div>

          <div v-if="saveError" class="editor-error">{{ saveError }}</div>

          <div
            v-if="isEditMode && props.inDocker && copyStatus"
            class="editor-copy-status"
          >
            {{ copyStatus }}
          </div>

          <div v-if="confirmingDelete" class="editor-delete-confirm">
            <v-icon size="18" color="error" style="flex-shrink: 0"
              >mdi-alert-circle-outline</v-icon
            >
            <div class="editor-delete-confirm-text">
              Remove
              <strong>{{ activeFolder?.label || activeFolder?.folder }}</strong>
              from automatic import monitoring?
            </div>
          </div>
        </v-card-text>
        <v-card-actions class="editor-footer">
          <v-btn
            v-if="isEditMode && !confirmingDelete"
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
              {{ isEditMode ? "Save" : "Add Folder" }}
            </v-btn>
          </template>
        </v-card-actions>
      </v-card>
    </div>
  </v-dialog>

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
import { computed, nextTick, ref, watch } from "vue";
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

const appVersion = __APP_VERSION__;

const props = defineProps({
  open: { type: Boolean, default: false },
  folder: { type: Object, default: null },
  inDocker: { type: Boolean, default: false },
  registeredPaths: { type: Array, default: () => [] },
  registeredFolders: { type: Array, default: () => [] },
  registeredReferenceFolders: { type: Array, default: () => [] },
  imageRoot: { type: String, default: null },
});

const emit = defineEmits(["close", "saved", "deleted"]);

const localPath = ref("");
const localHostPath = ref("");
const localLabel = ref("");
const localLabelTouched = ref(false);
const suppressLabelTouch = ref(false);
const frozenEditFolder = ref(null);
const localDeleteAfterImport = ref(false);
const saveError = ref("");
const saveLoading = ref(false);
const deleteLoading = ref(false);
const confirmingDelete = ref(false);
const pathInputRef = ref(null);
const copyStatus = ref("");

let copyStatusTimer = null;

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

const activeFolder = computed(() => props.folder ?? frozenEditFolder.value);
const isEditMode = computed(() => Boolean(activeFolder.value));

function normalizeFolderPath(value) {
  return String(value || "")
    .trim()
    .replace(/\/+$/, "");
}

function padFolderIndex(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return String(Math.trunc(parsed)).padStart(3, "0");
}

function inferImportMount(folder, fallbackIndex = null) {
  const rawFolder = normalizeFolderPath(folder?.folder);
  if (!rawFolder) return null;

  const storedHost = String(folder?.host_path || "").trim();
  const hasCanonicalContainerPath = /^\/data\/import\/pictures-\d+$/.test(
    rawFolder,
  );

  let containerPath = rawFolder;
  let hostPath = storedHost;

  if (!hasCanonicalContainerPath) {
    if (!hostPath) {
      // Legacy rows stored host path in `folder`; keep it as source.
      hostPath = rawFolder;
    }
    const index = padFolderIndex(folder?.id) || fallbackIndex;
    if (index) {
      containerPath = `/data/import/pictures-${index}`;
    }
  }

  if (!hostPath) {
    hostPath = containerPath;
  }

  return { hostPath, containerPath };
}

function inferReferenceMount(folder, fallbackIndex = null) {
  const rawFolder = normalizeFolderPath(folder?.folder);
  if (!rawFolder) return null;

  const storedHost = String(folder?.host_path || "").trim();
  const hasCanonicalContainerPath = /^\/data\/ref\/pictures-\d+$/.test(rawFolder);

  let containerPath = rawFolder;
  let hostPath = storedHost;

  if (!hasCanonicalContainerPath) {
    if (!hostPath) {
      hostPath = rawFolder;
    }
    const index = padFolderIndex(folder?.id) || fallbackIndex;
    if (index) {
      containerPath = `/data/ref/pictures-${index}`;
    }
  }

  if (!hostPath) {
    hostPath = containerPath;
  }

  return { hostPath, containerPath };
}

const inferredImportMounts = computed(() => {
  let fallback = 1;
  return (props.registeredFolders || [])
    .map((folder) => {
      const inferred = inferImportMount(folder, String(fallback).padStart(3, "0"));
      fallback += 1;
      return inferred;
    })
    .filter(Boolean);
});

const inferredReferenceMounts = computed(() => {
  let fallback = 1;
  return (props.registeredReferenceFolders || [])
    .map((folder) => {
      const inferred = inferReferenceMount(
        folder,
        String(fallback).padStart(3, "0"),
      );
      fallback += 1;
      return inferred;
    })
    .filter(Boolean);
});

const importContainerPathSet = computed(() => {
  return new Set(
    inferredImportMounts.value
      .map((mount) => normalizeFolderPath(mount.containerPath))
      .filter(Boolean),
  );
});

const hasExistingFolderMounts = computed(() => {
  return (
    inferredReferenceMounts.value.length > 0 || inferredImportMounts.value.length > 0
  );
});

function shellSingleQuote(value) {
  return `'${String(value || "").replace(/'/g, `"'"'`)}'`;
}

function buildDockerVolumeFlag(hostPath, containerPath) {
  const source = String(hostPath || "").trim();
  const target = String(containerPath || "").trim();
  return `-v ${shellSingleQuote(`${source}:${target}`)}`;
}

function deriveLabelFromHostPath(value) {
  const normalized = String(value || "")
    .trim()
    .replace(/[\\/]+$/, "");
  if (!normalized) return "";

  const parts = normalized.split(/[\\/]/).filter(Boolean);
  if (parts.length === 0) return "";

  const leaf = parts[parts.length - 1];
  if (!leaf || /^[A-Za-z]:$/.test(leaf)) return "";
  return leaf;
}

const suggestedDisplayLabel = computed(() => {
  return deriveLabelFromHostPath(localHostPath.value);
});

function setLabelWithoutTouch(value) {
  suppressLabelTouch.value = true;
  localLabel.value = value;
  suppressLabelTouch.value = false;
}

const dockerSuggestedPath = computed(() => {
  const usedIndices = new Set();
  for (const path of importContainerPathSet.value) {
    const match = path.match(/^\/data\/import\/pictures-(\d+)$/);
    if (!match) continue;
    const parsed = Number(match[1]);
    if (Number.isFinite(parsed) && parsed > 0) {
      usedIndices.add(parsed);
    }
  }

  let index = 1;
  while (true) {
    const suffix = String(index).padStart(3, "0");
    const candidate = `/data/import/pictures-${suffix}`;
    if (!importContainerPathSet.value.has(candidate) && !usedIndices.has(index)) {
      return candidate;
    }
    index += 1;
  }
});

const dockerMountSnippet = computed(() => {
  const hostPath = String(localHostPath.value || "").trim() || localPath.value;
  return buildDockerVolumeFlag(hostPath, dockerSuggestedPath.value);
});

const dockerExistingImportMountSnippets = computed(() => {
  const newFolderPath = normalizeFolderPath(dockerSuggestedPath.value);
  return inferredImportMounts.value
    .filter((mount) => normalizeFolderPath(mount.containerPath) !== newFolderPath)
    .map((mount) => buildDockerVolumeFlag(mount.hostPath, mount.containerPath));
});

const dockerAllImportMountSnippets = computed(() => {
  return inferredImportMounts.value.map((mount) =>
    buildDockerVolumeFlag(mount.hostPath, mount.containerPath),
  );
});

const dockerAllReferenceMountSnippets = computed(() => {
  return inferredReferenceMounts.value.map((mount) =>
    buildDockerVolumeFlag(mount.hostPath, mount.containerPath),
  );
});

function dedupeMountSnippets(mounts) {
  return Array.from(new Set(mounts));
}

const dockerAllRegisteredMountSnippets = computed(() => {
  return dedupeMountSnippets([
    ...dockerAllReferenceMountSnippets.value,
    ...dockerAllImportMountSnippets.value,
  ]);
});

const dockerRemoveContainerSnippet = "docker rm -f pixlstash-gpu";

const dockerImageReference = computed(() => {
  const version = String(appVersion || "").trim();
  if (!version) {
    return "ghcr.io/pikselkroken/pixlstash:latest-gpu";
  }
  if (version.toLowerCase().endsWith("-gpu")) {
    return `ghcr.io/pikselkroken/pixlstash:${version}`;
  }
  return `ghcr.io/pikselkroken/pixlstash:${version}-gpu`;
});

function buildDockerRestartCommand(importMounts) {
  return [
    "docker rm -f pixlstash-gpu 2>/dev/null || true",
    "docker run -d \\",
    "  --runtime nvidia \\",
    "  --user $(id -u):$(id -g) \\",
    "  -e HOME=/home/pixlstash \\",
    "  -e NVIDIA_VISIBLE_DEVICES=all \\",
    "  -e NVIDIA_DRIVER_CAPABILITIES=compute,utility \\",
    "  -e PIXLSTASH_HOST=0.0.0.0 \\",
    "  -p 9537:9537 \\",
    "  -v ~/Pictures/pixlstash:/home/pixlstash \\",
    ...importMounts.map((mount) => `  ${mount} \\`),
    "  --name pixlstash-gpu \\",
    `  ${dockerImageReference.value}`,
  ].join("\n");
}

const dockerRestartCommandSnippet = computed(() => {
  const allMounts = [
    ...dockerAllReferenceMountSnippets.value,
    ...dockerExistingImportMountSnippets.value,
    dockerMountSnippet.value,
  ];
  return buildDockerRestartCommand(dedupeMountSnippets(allMounts));
});

const dockerEditRestartCommandSnippet = computed(() => {
  return buildDockerRestartCommand(dockerAllRegisteredMountSnippets.value);
});

const isValid = computed(() => {
  if (isEditMode.value) return true;
  if (props.inDocker) return localHostPath.value.trim().length > 0;
  return localPath.value.trim().length > 0;
});

watch(
  [() => props.open, () => props.folder],
  ([isOpen, folder], [wasOpen]) => {
    if (folder) {
      frozenEditFolder.value = folder;
    } else if (isOpen && !wasOpen) {
      frozenEditFolder.value = null;
    }

    if (!isOpen) return;
    confirmingDelete.value = false;
    saveError.value = "";
    const editingFolder = activeFolder.value;
    if (editingFolder) {
      localLabel.value = editingFolder.label || "";
      localHostPath.value = String(editingFolder.host_path || "");
      localDeleteAfterImport.value = Boolean(editingFolder.delete_after_import);
      return;
    }
    localPath.value = props.inDocker ? dockerSuggestedPath.value : "";
    localHostPath.value = "";
    localLabelTouched.value = false;
    if (props.inDocker) {
      setLabelWithoutTouch(suggestedDisplayLabel.value);
    } else {
      setLabelWithoutTouch("");
    }
    localDeleteAfterImport.value = false;
    copyStatus.value = "";
    nextTick(() => {
      pathInputRef.value?.$el?.querySelector("input")?.focus();
    });
  },
  { immediate: true },
);

watch(
  localLabel,
  () => {
    if (suppressLabelTouch.value) return;
    if (!props.open || isEditMode.value || !props.inDocker) return;
    localLabelTouched.value = true;
  },
  { flush: "sync" },
);

watch(
  [() => props.open, () => props.folder, () => props.inDocker, localHostPath],
  ([isOpen, , inDocker]) => {
    if (!isOpen || isEditMode.value || !inDocker) return;
    if (localLabelTouched.value) return;
    setLabelWithoutTouch(suggestedDisplayLabel.value);
  },
  { immediate: true },
);

watch(
  [() => props.open, () => props.inDocker, dockerSuggestedPath],
  ([isOpen, inDocker]) => {
    if (!isOpen || isEditMode.value || !inDocker) return;
    localPath.value = dockerSuggestedPath.value;
  },
  { immediate: true },
);

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
    const editingFolder = activeFolder.value;
    if (editingFolder) {
      await apiClient.patch(`/import-folders/${editingFolder.id}`, {
        label: localLabel.value.trim() || null,
        delete_after_import: localDeleteAfterImport.value,
      });
    } else {
      const pathToSave = props.inDocker
        ? dockerSuggestedPath.value
        : localPath.value.trim();
      const hostPathToSave = String(localHostPath.value || "").trim();
      await apiClient.post("/import-folders", {
        folder: pathToSave,
        label: localLabel.value.trim() || undefined,
        host_path:
          props.inDocker && hostPathToSave.length > 0
            ? hostPathToSave
            : undefined,
        delete_after_import: localDeleteAfterImport.value,
      });
    }
    emit("saved");
  } catch (error) {
    saveError.value =
      error?.response?.data?.detail || "Failed to save import folder.";
  } finally {
    saveLoading.value = false;
  }
}

async function copyToClipboard(value, successMessage) {
  const text = String(value || "").trim();
  if (!text) return;

  const fallbackCopy = () => {
    try {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(textarea);
      return ok;
    } catch {
      return false;
    }
  };

  let copied = false;
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      copied = true;
    }
  } catch {
    copied = false;
  }

  if (!copied) {
    copied = fallbackCopy();
  }

  if (copied) {
    copyStatus.value = successMessage;
  } else {
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
  const editingFolder = activeFolder.value;
  if (!editingFolder) return;
  deleteLoading.value = true;
  try {
    await apiClient.delete(`/import-folders/${editingFolder.id}`);
    emit("deleted");
  } catch (error) {
    saveError.value =
      error?.response?.data?.detail || "Failed to remove import folder.";
    confirmingDelete.value = false;
  } finally {
    deleteLoading.value = false;
  }
}

function browseEntryDisabledReason(entryPath) {
  const norm = entryPath.replace(/\/$/, "");
  if (props.imageRoot) {
    const root = props.imageRoot.replace(/\/$/, "");
    if (norm === root) return "PixlStash data folder";
  }
  for (const registered of props.registeredPaths) {
    if (norm === registered.replace(/\/$/, ""))
      return "Already an import folder";
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
  } catch (error) {
    browseError.value =
      error?.response?.data?.detail || "Cannot browse this directory.";
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

.editor-docker-helper,
.editor-docker-instructions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 2px;
}

.editor-docker-path-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border: 1px dashed rgba(var(--v-theme-border), 0.3);
  border-radius: 6px;
  background: rgba(var(--v-theme-surface), 0.45);
}

.editor-docker-path-label {
  font-size: 0.78rem;
  opacity: 0.72;
}

.editor-docker-path-value {
  font-family: monospace;
  font-size: 0.78rem;
  opacity: 0.95;
  text-align: right;
}

.editor-docker-title {
  font-size: 0.8rem;
  font-weight: 600;
  opacity: 0.86;
}

.editor-docker-note {
  font-size: 0.76rem;
  opacity: 0.75;
  line-height: 1.32;
}

.editor-docker-note--muted {
  opacity: 0.65;
}

.editor-docker-snippet-wrap {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.editor-docker-snippet {
  flex: 1;
  display: block;
  padding: 8px 10px;
  border-radius: 6px;
  background: rgba(var(--v-theme-surface-variant), 0.38);
  font-family: monospace;
  font-size: 0.72rem;
  line-height: 1.35;
  word-break: break-all;
  white-space: pre-wrap;
  overflow: auto hidden;
}

.editor-docker-snippet--full {
  white-space: pre-wrap;
  overflow: auto;
  line-height: 1.35;
}

.editor-copy-btn {
  flex-shrink: 0;
  margin-top: 1px;
}

.editor-copy-status {
  font-size: 0.76rem;
  color: rgb(var(--v-theme-accent));
  margin-top: 2px;
}

.editor-browse-btn {
  margin-top: 4px;
  flex-shrink: 0;
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
  opacity: 0.72;
  margin-top: -2px;
  padding-left: 6px;
}

.editor-error {
  font-size: 0.8rem;
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.08);
  border: 1px solid rgba(var(--v-theme-error), 0.22);
  border-radius: 6px;
  padding: 8px 10px;
}

.editor-delete-confirm {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  background: rgba(var(--v-theme-error), 0.08);
  border: 1px solid rgba(var(--v-theme-error), 0.24);
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 0.8rem;
}

.editor-delete-confirm-text {
  line-height: 1.25;
}

.editor-footer {
  padding: 12px 20px 18px;
}

.btn-cancel {
  margin-right: 6px;
}

.btn-save {
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.editor-delete-btn {
  margin-right: 4px;
}

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
