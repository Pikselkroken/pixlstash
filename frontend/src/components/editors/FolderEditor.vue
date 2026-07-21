<template>
  <v-dialog :model-value="open" max-width="560" @click:outside="emit('close')">
    <div class="editor-shell">
      <v-btn icon size="36px" class="close-icon" @click="emit('close')">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="editor-card">
        <v-card-title class="editor-header">
          {{
            isEditMode
              ? `Edit ${isImport ? "Import" : "Reference"} Folder`
              : `Add ${isImport ? "Import" : "Reference"} Folder`
          }}
        </v-card-title>
        <v-card-text class="editor-body">
          <!-- Path input (create mode, non-Docker) -->
          <div v-if="!isEditMode && !props.inDocker" class="editor-path-row">
            <v-text-field
              ref="pathInputRef"
              v-model="localPath"
              label="Folder path *"
              placeholder="/path/to/folder"
              density="comfortable"
              variant="filled"
              hide-details
              @keydown.enter="save"
            />
            <v-btn
              variant="outlined"
              size="small"
              icon
              class="editor-browse-btn"
              title="Browse for folder"
              @click="browseOpen = true"
            >
              <v-icon size="18">mdi-folder-open-outline</v-icon>
            </v-btn>
          </div>

          <!-- Docker helper (create mode) -->
          <div
            v-else-if="!isEditMode && props.inDocker"
            class="editor-docker-helper"
          >
            <v-text-field
              ref="pathInputRef"
              v-model="localHostPath"
              label="Local folder (host path)"
              :placeholder="
                isImport ? '/home/you/Pictures/import' : '/home/you/Pictures'
              "
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
              <div
                class="editor-docker-path-value"
                :title="dockerSuggestedPath"
              >
                {{ dockerSuggestedPath }}
              </div>
              <v-btn
                v-if="!isImport"
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

            <!-- Import: flat docker instructions -->
            <template v-if="isImport">
              <div class="editor-docker-format-row">
                <div class="editor-docker-title">Docker mount line</div>
                <v-btn-toggle
                  v-model="shellFormat"
                  mandatory
                  density="compact"
                  class="editor-docker-shell-btns"
                >
                  <v-btn value="linux" size="small">Linux / Mac</v-btn>
                  <v-btn value="windows" size="small">Windows</v-btn>
                </v-btn-toggle>
              </div>
              <div class="editor-docker-note">
                Add this <code>-v</code> mount to your
                <code>docker run</code> command:
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
                This removes the old container, not the image.
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
                v-if="hasExistingMounts"
                class="editor-docker-note editor-docker-note--muted"
              >
                Existing reference and import folder mounts are included.
              </div>
              <div class="editor-docker-snippet-wrap">
                <code
                  class="editor-docker-snippet editor-docker-snippet--full"
                  >{{ dockerRestartCommandSnippet }}</code
                >
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
            </template>

            <!-- Reference: boxed docker instructions with numbered steps -->
            <div v-else class="editor-docker-instructions">
              <div class="editor-docker-format-row">
                <div class="editor-docker-title">Docker setup</div>
                <v-btn-toggle
                  v-model="shellFormat"
                  mandatory
                  density="compact"
                  class="editor-docker-shell-btns"
                >
                  <v-btn value="linux" size="small">Linux / Mac</v-btn>
                  <v-btn value="windows" size="small">Windows</v-btn>
                </v-btn-toggle>
              </div>
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
                This removes the old container, not the image.
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
                v-if="hasExistingMounts"
                class="editor-docker-note editor-docker-note--muted"
              >
                Existing reference and import folder mounts are included from
                configured container paths and use stored host paths when
                available. Replace any remaining
                <code>/absolute/host/path/for-*</code> placeholder values.
              </div>
              <div class="editor-docker-snippet-wrap">
                <code
                  class="editor-docker-snippet editor-docker-snippet--full"
                  >{{ dockerRestartCommandSnippet }}</code
                >
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

          <!-- Reference path input (edit mode, non-Docker) -->
          <div
            v-else-if="isEditMode && !isImport && !props.inDocker"
            class="editor-path-display editor-path-display--with-action"
          >
            <v-icon size="16" class="editor-path-icon"
              >mdi-folder-network-outline</v-icon
            >
            <span class="editor-path-text" :title="activeFolder?.folder">{{
              activeFolder?.folder
            }}</span>
            <v-btn
              variant="outlined"
              size="small"
              icon
              class="editor-relocate-btn"
              title="Relocate folder and move files"
              @click="
                emit('relocate', activeFolder);
                emit('close');
              "
            >
              <v-icon size="18">mdi-folder-move-outline</v-icon>
            </v-btn>
          </div>

          <!-- Reference path inputs (edit mode, Docker) -->
          <div
            v-else-if="isEditMode && !isImport && props.inDocker"
            class="editor-docker-helper"
          >
            <v-text-field
              ref="pathInputRef"
              v-model="localHostPath"
              label="Local folder (host path)"
              placeholder="/home/you/Pictures"
              density="comfortable"
              variant="filled"
              hide-details
              @keydown.enter="save"
            />
            <v-text-field
              v-model="localPath"
              label="Container path"
              placeholder="/data/ref/pictures-001"
              density="comfortable"
              variant="filled"
              hide-details
              @keydown.enter="save"
            />
          </div>

          <!-- Path display (edit mode for import folders) -->
          <div v-else class="editor-path-display">
            <v-icon size="16" class="editor-path-icon">{{
              isImport ? "mdi-folder-import" : "mdi-folder-network-outline"
            }}</v-icon>
            <span class="editor-path-text" :title="activeFolder?.folder">{{
              activeFolder?.folder
            }}</span>
          </div>

          <!-- Docker restart helper (edit mode) -->
          <div
            v-if="isEditMode && props.inDocker"
            class="editor-docker-instructions"
          >
            <div class="editor-docker-format-row">
              <div class="editor-docker-title">Docker restart command</div>
              <v-btn-toggle
                v-model="shellFormat"
                mandatory
                density="compact"
                class="editor-docker-shell-btns"
              >
                <v-btn value="linux" size="small">Linux / Mac</v-btn>
                <v-btn value="windows" size="small">Windows</v-btn>
              </v-btn-toggle>
            </div>
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
              v-if="hasExistingMounts"
              class="editor-docker-note editor-docker-note--muted"
            >
              <template v-if="isImport">
                Existing reference and import folder mounts are included.
              </template>
              <template v-else>
                Stored host paths are used when available. Replace any remaining
                <code>/absolute/host/path/for-*</code> placeholder values.
              </template>
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

          <!-- Display label -->
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

          <!-- Import-only: delete after import toggle -->
          <div v-if="isImport" class="editor-toggle-row">
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

          <!-- Reference-only: caption file sync (create + edit) -->
          <div v-if="!isImport" class="editor-sync-section">
            <button
              type="button"
              class="editor-sync-header"
              @click="syncSectionOpen = !syncSectionOpen"
            >
              <v-icon size="18">{{
                syncSectionOpen ? "mdi-chevron-down" : "mdi-chevron-right"
              }}</v-icon>
              <span>Synchronise caption files?</span>
              <span class="editor-sync-summary">{{ syncSummary }}</span>
            </button>
            <v-expand-transition>
              <div v-show="syncSectionOpen" class="editor-sync-body">
                <div class="editor-sync-intro">
                  Keep <code>.txt</code> sidecar files next to each image in sync
                  with PixlStash. Tags and descriptions are stored in separate
                  files: changes made outside PixlStash are read back in, and
                  changes made here are written out (the files are created when
                  they don't exist yet).
                </div>

                <!-- Tags -->
                <div class="editor-toggle-row">
                  <v-checkbox
                    v-model="localSyncTags"
                    label="Sync tags"
                    density="compact"
                    hide-details
                  />
                  <div v-if="localSyncTags" class="editor-sync-suffix">
                    <v-text-field
                      v-model="localTagsSuffix"
                      label="Tags filename suffix"
                      :placeholder="DEFAULT_TAGS_SUFFIX"
                      density="compact"
                      variant="filled"
                      hide-details
                    />
                    <div class="editor-toggle-desc">
                      e.g. <code>{{ tagsExample }}</code>
                    </div>
                  </div>
                </div>

                <!-- Descriptions -->
                <div class="editor-toggle-row">
                  <v-checkbox
                    v-model="localSyncDescriptions"
                    label="Sync descriptions"
                    density="compact"
                    hide-details
                  />
                  <div v-if="localSyncDescriptions" class="editor-sync-suffix">
                    <v-text-field
                      v-model="localDescriptionSuffix"
                      label="Description filename suffix"
                      :placeholder="DEFAULT_DESCRIPTION_SUFFIX"
                      density="compact"
                      variant="filled"
                      hide-details
                    />
                    <div class="editor-toggle-desc">
                      e.g. <code>{{ descriptionExample }}</code>
                    </div>
                  </div>
                </div>

                <div v-if="detectInfo" class="editor-sync-detected">
                  {{ detectInfo }}
                </div>
              </div>
            </v-expand-transition>
          </div>

          <!-- Reference-only: allow delete (edit mode) -->
          <div v-if="!isImport && isEditMode" class="editor-toggle-row">
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

          <div v-if="saveError" class="editor-error">{{ saveError }}</div>

          <div
            v-if="isEditMode && props.inDocker && copyStatus"
            class="editor-copy-status"
          >
            {{ copyStatus }}
          </div>

          <div v-if="confirmingDelete" class="editor-delete-confirm">
            <v-icon size="18" color="error" style="flex-shrink: 0">
              mdi-alert-circle-outline
            </v-icon>
            <div class="editor-delete-confirm-text">
              <template v-if="isImport">
                Remove
                <strong>{{
                  activeFolder?.label || activeFolder?.folder
                }}</strong>
                from automatic import monitoring?
              </template>
              <template v-else>
                Remove
                <strong>{{
                  activeFolder?.label || activeFolder?.folder
                }}</strong>
                from PixlStash? The original files on disk will not be deleted.
              </template>
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

  <FolderBrowser
    :open="browseOpen"
    :registered-paths="props.registeredPaths"
    :image-root="props.imageRoot"
    :already-registered-label="`Already a${isImport ? 'n import' : ' reference'} folder`"
    @select="
      (path) => {
        localPath = path;
      }
    "
    @close="browseOpen = false"
  />
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { apiClient } from "../../utils/apiClient";
import { copyText } from "../../utils/clipboard";
import {
  buildDockerRestartCommand,
  buildDockerVolumeFlag,
  deriveLabelFromHostPath,
  hostPathPlaceholderFor,
  inferImportMount,
  inferReferenceMount,
  normalizeFolderPath,
} from "../../utils/dockerHelpers";
import FolderBrowser from "./FolderBrowser.vue";

const appVersion = __APP_VERSION__;

// Default sidecar filename suffixes used when no convention is detected or
// inherited (kept in sync with the backend defaults in caption_file_utils.py).
const DEFAULT_TAGS_SUFFIX = "_tags.txt";
const DEFAULT_DESCRIPTION_SUFFIX = "_description.txt";

const props = defineProps({
  /** "import" or "reference" — determines API endpoints, labels, and form fields */
  type: { type: String, required: true },
  open: { type: Boolean, default: false },
  /** null → create mode; a folder object → edit mode */
  folder: { type: Object, default: null },
  inDocker: { type: Boolean, default: false },
  /** "cpu" for the CPU-only Docker image; any other value uses the GPU image. */
  dockerVariant: { type: String, default: "gpu" },
  /** Registered paths for this folder type — used to disable already-registered entries in browse */
  registeredPaths: { type: Array, default: () => [] },
  /** Registered folder objects for this folder type — used for Docker command generation */
  registeredFolders: { type: Array, default: () => [] },
  /** Registered folder objects for the other folder type — used to include their mounts in restart commands */
  registeredSiblingFolders: { type: Array, default: () => [] },
  imageRoot: { type: String, default: null },
});

const emit = defineEmits(["close", "saved", "deleted", "relocate"]);

// --- Type-derived helpers ---

const isImport = computed(() => props.type === "import");
const apiBase = computed(() =>
  isImport.value ? "/import-folders" : "/reference-folders",
);
const containerPrefix = computed(() =>
  isImport.value ? "/data/import/pictures-" : "/data/ref/pictures-",
);
const containerPathPattern = computed(() =>
  isImport.value
    ? /^\/data\/import\/pictures-(\d+)$/
    : /^\/data\/ref\/pictures-(\d+)$/,
);

// --- Form state ---

const localPath = ref("");
const localHostPath = ref("");
const localLabel = ref("");
const localLabelTouched = ref(false);
const suppressLabelTouch = ref(false);
const frozenEditFolder = ref(null);
const localDeleteAfterImport = ref(false);
const localSyncDescriptions = ref(false);
const localSyncTags = ref(false);
const localDescriptionSuffix = ref(DEFAULT_DESCRIPTION_SUFFIX);
const localTagsSuffix = ref(DEFAULT_TAGS_SUFFIX);
const syncSectionOpen = ref(false);
const detectInfo = ref("");
const localAllowDelete = ref(false);
const saveError = ref("");
const saveLoading = ref(false);
const deleteLoading = ref(false);
const confirmingDelete = ref(false);
const pathInputRef = ref(null);
const copyStatus = ref("");
const browseOpen = ref(false);

let copyStatusTimer = null;
const defaultShellFormat = navigator.userAgent.toLowerCase().includes("win")
  ? "windows"
  : "linux";
const shellFormat = ref(defaultShellFormat);

// --- Core computed ---

const activeFolder = computed(() => props.folder ?? frozenEditFolder.value);
const isEditMode = computed(() => Boolean(activeFolder.value));

const isValid = computed(() => {
  if (isEditMode.value) {
    if (!isImport.value && props.inDocker) {
      return (
        localPath.value.trim().length > 0 &&
        localHostPath.value.trim().length > 0
      );
    }
    if (!isImport.value) return localPath.value.trim().length > 0;
    return true;
  }
  if (props.inDocker) return localHostPath.value.trim().length > 0;
  return localPath.value.trim().length > 0;
});

const suggestedDisplayLabel = computed(() =>
  deriveLabelFromHostPath(localHostPath.value),
);

// The most recently added reference folder, used to inherit sync defaults when
// adding a new one (so a user who switched to e.g. ".txt"/".caption" keeps it).
const lastAddedReferenceFolder = computed(() => {
  if (isImport.value) return null;
  const folders = props.registeredFolders || [];
  let best = null;
  for (const folder of folders) {
    if (best === null || Number(folder.id) > Number(best.id)) best = folder;
  }
  return best;
});

const syncSummary = computed(() => {
  const parts = [];
  if (localSyncTags.value) parts.push("tags");
  if (localSyncDescriptions.value) parts.push("descriptions");
  if (!parts.length) return "Off";
  return parts.map((p) => p[0].toUpperCase() + p.slice(1)).join(" + ");
});

function sidecarExample(suffix, fallback) {
  return "image" + (String(suffix || "").trim() || fallback);
}
const tagsExample = computed(() =>
  sidecarExample(localTagsSuffix.value, DEFAULT_TAGS_SUFFIX),
);
const descriptionExample = computed(() =>
  sidecarExample(localDescriptionSuffix.value, DEFAULT_DESCRIPTION_SUFFIX),
);

function setLabelWithoutTouch(value) {
  suppressLabelTouch.value = true;
  localLabel.value = value;
  suppressLabelTouch.value = false;
}

// --- Docker: inferred mounts from same-type folders ---
// For import type: infer containerPath from folder ID when not canonical
const inferredOwnImportMounts = computed(() => {
  if (!isImport.value) return [];
  let fallback = 1;
  return (props.registeredFolders || [])
    .map((folder) => {
      const inferred = inferImportMount(
        folder,
        String(fallback).padStart(3, "0"),
      );
      fallback += 1;
      return inferred;
    })
    .filter(Boolean);
});

// For reference type: use stored containerPaths + stored/placeholder host paths
const normalizedOwnPaths = computed(() => {
  return new Set(
    (props.registeredPaths || []).map((p) => normalizeFolderPath(p)),
  );
});

const normalizedOwnPathList = computed(() =>
  Array.from(normalizedOwnPaths.value).filter(Boolean),
);

const ownHostPathByContainerPath = computed(() => {
  const map = new Map();
  for (const folder of props.registeredFolders || []) {
    const cp = normalizeFolderPath(folder?.folder);
    const hp = String(folder?.host_path || "").trim();
    if (cp && hp) map.set(cp, hp);
  }
  return map;
});

// --- Docker: sibling folder mounts (the other type) ---
const inferredSiblingMounts = computed(() => {
  const inferFn = isImport.value ? inferReferenceMount : inferImportMount;
  let fallback = 1;
  return (props.registeredSiblingFolders || [])
    .map((folder) => {
      const inferred = inferFn(folder, String(fallback).padStart(3, "0"));
      fallback += 1;
      return inferred;
    })
    .filter(Boolean);
});

// --- Docker: suggested container path for new folder ---
const ownContainerPathSet = computed(() => {
  if (isImport.value) {
    return new Set(
      inferredOwnImportMounts.value
        .map((m) => normalizeFolderPath(m.containerPath))
        .filter(Boolean),
    );
  }
  return normalizedOwnPaths.value;
});

const dockerSuggestedPath = computed(() => {
  const pattern = containerPathPattern.value;
  const prefix = containerPrefix.value;
  const usedIndices = new Set();
  for (const path of ownContainerPathSet.value) {
    const match = path.match(pattern);
    if (!match) continue;
    const parsed = Number(match[1]);
    if (Number.isFinite(parsed) && parsed > 0) usedIndices.add(parsed);
  }
  let index = 1;
  while (true) {
    const suffix = String(index).padStart(3, "0");
    const candidate = `${prefix}${suffix}`;
    if (!ownContainerPathSet.value.has(candidate) && !usedIndices.has(index)) {
      return candidate;
    }
    index += 1;
  }
});

// --- Docker: mount snippets ---

const dockerMountSnippet = computed(() => {
  const hostPath =
    String(localHostPath.value || "").trim() ||
    (isImport.value ? localPath.value : "/absolute/host/path");
  return buildDockerVolumeFlag(
    hostPath,
    dockerSuggestedPath.value,
    shellFormat.value,
  );
});

const dockerExistingOwnMountSnippets = computed(() => {
  const newPath = normalizeFolderPath(dockerSuggestedPath.value);
  if (isImport.value) {
    return inferredOwnImportMounts.value
      .filter((m) => normalizeFolderPath(m.containerPath) !== newPath)
      .map((m) =>
        buildDockerVolumeFlag(m.hostPath, m.containerPath, shellFormat.value),
      );
  }
  return normalizedOwnPathList.value
    .filter((p) => p !== newPath)
    .map((p) => {
      const hp =
        ownHostPathByContainerPath.value.get(p) || hostPathPlaceholderFor(p);
      return buildDockerVolumeFlag(hp, p, shellFormat.value);
    });
});

const dockerAllOwnMountSnippets = computed(() => {
  if (isImport.value) {
    return inferredOwnImportMounts.value.map((m) =>
      buildDockerVolumeFlag(m.hostPath, m.containerPath, shellFormat.value),
    );
  }
  return normalizedOwnPathList.value.map((p) => {
    const hp =
      ownHostPathByContainerPath.value.get(p) || hostPathPlaceholderFor(p);
    return buildDockerVolumeFlag(hp, p, shellFormat.value);
  });
});

const dockerAllSiblingMountSnippets = computed(() =>
  inferredSiblingMounts.value.map((m) =>
    buildDockerVolumeFlag(m.hostPath, m.containerPath, shellFormat.value),
  ),
);

const dockerAllRegisteredMountSnippets = computed(() =>
  dedupeMountSnippets([
    ...dockerAllOwnMountSnippets.value,
    ...dockerAllSiblingMountSnippets.value,
  ]),
);

function dedupeMountSnippets(mounts) {
  return Array.from(new Set(mounts));
}

// --- Docker: container info ---

const isGpuVariant = computed(
  () =>
    String(props.dockerVariant || "")
      .trim()
      .toLowerCase() !== "cpu",
);

const containerName = computed(() =>
  isGpuVariant.value ? "pixlstash-gpu" : "pixlstash-cpu",
);

const dockerRemoveContainerSnippet = computed(
  () => `docker rm -f ${containerName.value}`,
);

const dockerImageReference = computed(() => {
  const version = String(appVersion || "").trim();
  const suffix = isGpuVariant.value ? "gpu" : "cpu";
  if (!version) {
    return `ghcr.io/pikselkroken/pixlstash:latest-${suffix}`;
  }
  const baseVersion = version.replace(/-(gpu|cpu)$/i, "");
  return `ghcr.io/pikselkroken/pixlstash:${baseVersion}-${suffix}`;
});

const dockerRestartOpts = computed(() => ({
  containerName: containerName.value,
  imageReference: dockerImageReference.value,
  isGpu: isGpuVariant.value,
}));

// Create mode: all existing mounts + new mount
const dockerRestartCommandSnippet = computed(() => {
  let allMounts;
  if (isImport.value) {
    // reference mounts first, then existing import mounts, then new import mount
    allMounts = [
      ...dockerAllSiblingMountSnippets.value,
      ...dockerExistingOwnMountSnippets.value,
      dockerMountSnippet.value,
    ];
  } else {
    // existing ref mounts + new ref mount + all import mounts
    allMounts = [
      ...dockerExistingOwnMountSnippets.value,
      dockerMountSnippet.value,
      ...dockerAllSiblingMountSnippets.value,
    ];
  }
  return buildDockerRestartCommand(
    dedupeMountSnippets(allMounts),
    shellFormat.value,
    dockerRestartOpts.value,
  );
});

// Edit mode: all registered mounts
const dockerEditRestartCommandSnippet = computed(() =>
  buildDockerRestartCommand(
    dockerAllRegisteredMountSnippets.value,
    shellFormat.value,
    dockerRestartOpts.value,
  ),
);

const hasExistingMounts = computed(
  () =>
    (props.registeredFolders || []).length > 0 ||
    (props.registeredSiblingFolders || []).length > 0,
);

// --- Watchers ---

watch(
  [() => props.open, () => props.folder],
  ([isOpen, folder], [wasOpen]) => {
    if (folder) {
      frozenEditFolder.value = folder;
    } else if (isOpen && !wasOpen) {
      frozenEditFolder.value = null;
    }

    if (!isOpen) return;
    shellFormat.value = defaultShellFormat;
    confirmingDelete.value = false;
    saveError.value = "";
    detectInfo.value = "";
    const editingFolder = activeFolder.value;
    if (editingFolder) {
      localLabel.value = editingFolder.label || "";
      localPath.value = editingFolder.folder || "";
      localHostPath.value = String(editingFolder.host_path || "");
      localDeleteAfterImport.value = Boolean(editingFolder.delete_after_import);
      localSyncDescriptions.value = Boolean(editingFolder.sync_descriptions);
      localSyncTags.value = Boolean(editingFolder.sync_tags);
      localTagsSuffix.value = editingFolder.tags_suffix || DEFAULT_TAGS_SUFFIX;
      localDescriptionSuffix.value =
        editingFolder.description_suffix || DEFAULT_DESCRIPTION_SUFFIX;
      syncSectionOpen.value =
        localSyncTags.value || localSyncDescriptions.value;
      localAllowDelete.value = Boolean(editingFolder.allow_delete_file);
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
    // Seed sync defaults from the most recently added reference folder, falling
    // back to the hardcoded defaults. Folder-content detection (non-Docker) may
    // override these once a path is entered.
    const last = lastAddedReferenceFolder.value;
    localSyncTags.value = last ? Boolean(last.sync_tags) : false;
    localSyncDescriptions.value = last ? Boolean(last.sync_descriptions) : false;
    localTagsSuffix.value = (last && last.tags_suffix) || DEFAULT_TAGS_SUFFIX;
    localDescriptionSuffix.value =
      (last && last.description_suffix) || DEFAULT_DESCRIPTION_SUFFIX;
    syncSectionOpen.value = localSyncTags.value || localSyncDescriptions.value;
    localAllowDelete.value = false;
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

// --- Sidecar convention detection (reference folders, non-Docker create) ---

let detectTimer = null;

async function detectSidecars(path) {
  const trimmed = String(path || "").trim();
  if (!trimmed) return;
  try {
    const { data } = await apiClient.get("/reference-folders/detect-sidecars", {
      params: { path: trimmed },
    });
    // Ignore a stale response if the path changed while the request was in flight.
    if (String(localPath.value || "").trim() !== trimmed) return;
    const found = [];
    if (data?.found_tags) {
      localSyncTags.value = true;
      if (data.tags_suffix) localTagsSuffix.value = data.tags_suffix;
      found.push("tags");
    }
    if (data?.found_descriptions) {
      localSyncDescriptions.value = true;
      if (data.description_suffix)
        localDescriptionSuffix.value = data.description_suffix;
      found.push("descriptions");
    }
    if (found.length) {
      syncSectionOpen.value = true;
      detectInfo.value = `Found existing ${found.join(" and ")} sidecars in this folder.`;
    } else {
      detectInfo.value = "";
    }
  } catch {
    // Detection is best-effort — ignore errors (e.g. an inaccessible path).
  }
}

watch([() => props.open, localPath], ([isOpen]) => {
  if (!isOpen || isEditMode.value || isImport.value || props.inDocker) return;
  if (detectTimer) clearTimeout(detectTimer);
  const path = localPath.value;
  detectTimer = setTimeout(() => detectSidecars(path), 400);
});

function handleKeydown(event) {
  if (event.key === "Escape") emit("close");
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeydown);
    } else {
      document.removeEventListener("keydown", handleKeydown);
      if (copyStatusTimer) {
        clearTimeout(copyStatusTimer);
        copyStatusTimer = null;
      }
      if (detectTimer) {
        clearTimeout(detectTimer);
        detectTimer = null;
      }
      copyStatus.value = "";
    }
  },
);

// --- API actions ---

// Return the suffix to submit, or undefined to leave it unset. An unmodified
// default is left unset so the backend's first scan can auto-detect the real
// convention on disk (notably for Docker folders, where in-dialog detection
// can't reach the not-yet-mounted path).
function suffixForSubmit(value, def) {
  const trimmed = String(value || "").trim();
  if (!trimmed || trimmed === def) return undefined;
  return trimmed;
}

async function save() {
  if (!isValid.value) return;
  saveLoading.value = true;
  saveError.value = "";
  try {
    let savedResponse = null;
    const editingFolder = activeFolder.value;
    if (editingFolder) {
      const patchData = { label: localLabel.value.trim() || null };
      if (isImport.value) {
        patchData.delete_after_import = localDeleteAfterImport.value;
      } else {
        const editedPath = localPath.value.trim();
        if (editedPath && editedPath !== editingFolder.folder) {
          patchData.folder = editedPath;
        }
        if (props.inDocker) {
          patchData.host_path = localHostPath.value.trim() || null;
        }
        patchData.allow_delete_file = localAllowDelete.value;
        patchData.sync_descriptions = localSyncDescriptions.value;
        patchData.sync_tags = localSyncTags.value;
        patchData.tags_suffix =
          suffixForSubmit(localTagsSuffix.value, DEFAULT_TAGS_SUFFIX) ?? null;
        patchData.description_suffix =
          suffixForSubmit(
            localDescriptionSuffix.value,
            DEFAULT_DESCRIPTION_SUFFIX,
          ) ?? null;
      }
      savedResponse = await apiClient.patch(
        `${apiBase.value}/${editingFolder.id}`,
        patchData,
      );
    } else {
      const pathToSave = props.inDocker
        ? dockerSuggestedPath.value
        : localPath.value.trim();
      const hostPathToSave = String(localHostPath.value || "").trim();
      const createData = {
        folder: pathToSave,
        label: localLabel.value.trim() || undefined,
        host_path:
          props.inDocker && hostPathToSave.length > 0
            ? hostPathToSave
            : undefined,
      };
      if (isImport.value) {
        createData.delete_after_import = localDeleteAfterImport.value;
      } else {
        createData.sync_descriptions = localSyncDescriptions.value;
        createData.sync_tags = localSyncTags.value;
        createData.tags_suffix = suffixForSubmit(
          localTagsSuffix.value,
          DEFAULT_TAGS_SUFFIX,
        );
        createData.description_suffix = suffixForSubmit(
          localDescriptionSuffix.value,
          DEFAULT_DESCRIPTION_SUFFIX,
        );
      }
      savedResponse = await apiClient.post(apiBase.value, createData);
    }
    emit("saved", savedResponse?.data || null);
  } catch (error) {
    saveError.value =
      error?.response?.data?.detail || `Failed to save ${props.type} folder.`;
  } finally {
    saveLoading.value = false;
  }
}

async function doDelete() {
  const editingFolder = activeFolder.value;
  if (!editingFolder) return;
  deleteLoading.value = true;
  try {
    await apiClient.delete(`${apiBase.value}/${editingFolder.id}`);
    emit("deleted");
  } catch (error) {
    saveError.value =
      error?.response?.data?.detail || `Failed to remove ${props.type} folder.`;
    confirmingDelete.value = false;
  } finally {
    deleteLoading.value = false;
  }
}

async function copyToClipboard(value, successMessage) {
  const text = String(value || "").trim();
  if (!text) return;
  const copied = await copyText(text);
  copyStatus.value = copied
    ? successMessage
    : "Copy failed. Please select and copy manually.";
  if (copyStatusTimer) clearTimeout(copyStatusTimer);
  copyStatusTimer = setTimeout(() => {
    copyStatus.value = "";
    copyStatusTimer = null;
  }, 1800);
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
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  padding: var(--space-6) var(--space-6) var(--space-3);
}

.editor-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-6) var(--space-3);
}

.editor-path-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.editor-path-row .v-text-field {
  flex: 1;
}

.editor-browse-btn {
  margin-top: var(--space-2);
  flex-shrink: 0;
}

.editor-docker-helper {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  margin-bottom: var(--space-1);
}

.editor-docker-path-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-surface-variant), 0.35);
}

.editor-docker-path-label {
  font-size: var(--text-2xs);
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
  font-family: var(--font-mono);
  font-size: var(--text-sm);
}

.editor-docker-instructions {
  border: 1px solid rgba(var(--v-theme-primary), 0.22);
  background: rgba(var(--v-theme-primary), 0.06);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  line-height: 1.35;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.editor-docker-instructions ol {
  margin: 0;
  padding-left: var(--space-5);
}

.editor-docker-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  margin-bottom: var(--space-2);
}

.editor-docker-format-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.editor-docker-format-row .editor-docker-title {
  margin-bottom: 0;
}

.editor-docker-shell-btns {
  flex-shrink: 0;
}

.editor-docker-note {
  font-size: var(--text-xs);
  opacity: 0.75;
  line-height: 1.32;
}

.editor-docker-note--muted {
  opacity: 0.65;
}

.editor-docker-snippet-wrap {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.editor-docker-snippet {
  flex: 1;
  display: block;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-dark-surface), 0.55);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-xs);
  white-space: nowrap;
  overflow: auto hidden;
}

.editor-docker-snippet--full {
  white-space: pre-wrap;
  overflow: auto;
  line-height: 1.35;
}

.editor-copy-btn {
  flex-shrink: 0;
  margin-top: var(--space-1);
}

.editor-copy-status {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-accent));
  margin-top: var(--space-1);
}

.editor-path-display {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: rgba(var(--v-theme-surface-variant), 0.4);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  opacity: 0.85;
}

.editor-path-display--with-action {
  opacity: 1;
}

.editor-path-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.editor-path-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
}

.editor-relocate-btn {
  flex-shrink: 0;
}

.editor-toggle-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.editor-toggle-desc {
  font-size: var(--text-xs);
  opacity: 0.65;
  padding-left: var(--space-7);
}

.editor-toggle-desc--warning {
  color: rgb(var(--v-theme-error));
  opacity: 1;
}

.editor-sync-section {
  border: 1px solid rgba(var(--v-theme-primary), 0.18);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.editor-sync-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: rgba(var(--v-theme-primary), 0.06);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  text-align: left;
  cursor: pointer;
  border: none;
  color: inherit;
}

.editor-sync-header:hover {
  background: rgba(var(--v-theme-primary), 0.1);
}

.editor-sync-summary {
  margin-left: auto;
  font-size: var(--text-xs);
  font-weight: var(--weight-medium);
  opacity: 0.7;
}

.editor-sync-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4);
}

.editor-sync-intro {
  font-size: var(--text-xs);
  opacity: 0.75;
  line-height: 1.4;
}

.editor-sync-suffix {
  padding-left: var(--space-7);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.editor-sync-detected {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-accent));
}

.editor-error {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-error));
  background: rgba(var(--v-theme-error), 0.08);
  border: 1px solid rgba(var(--v-theme-error), 0.22);
  border-radius: var(--radius-sm);
  padding: var(--space-3) var(--space-3);
}

.editor-delete-confirm {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: rgba(var(--v-theme-error), 0.08);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-error), 0.25);
  font-size: var(--text-sm);
}

.editor-delete-confirm-text {
  line-height: 1.4;
}

.editor-footer {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5) var(--space-5);
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
</style>
