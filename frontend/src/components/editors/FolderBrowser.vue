<template>
  <v-dialog :model-value="open" max-width="720" @update:model-value="!$event && emit('close')">
    <v-card class="browser-card">
      <v-card-title class="browser-header">{{
        pickModelFile ? "Choose a model file" : "Browse for Folder"
      }}</v-card-title>
      <v-card-text style="padding: 0">
        <div class="browse-path-bar">
          <v-icon size="16" style="opacity: 0.6; margin-right: 4px">mdi-folder</v-icon>
          <span class="browse-path-text">{{ browsePath }}</span>
          <v-checkbox
            v-model="browseShowHidden"
            label="Show hidden"
            density="compact"
            hide-details
            class="browse-hidden-toggle"
          />
        </div>
        <div v-if="allowCreateFolder" class="browse-create-bar">
          <template v-if="creatingFolder">
            <v-text-field
              ref="createFolderInputRef"
              v-model="newFolderName"
              label="New folder name"
              density="compact"
              variant="filled"
              hide-details
              :error="Boolean(createFolderError)"
              @keydown.enter="createFolder"
              @keydown.esc="cancelCreateFolder"
            />
            <v-btn
              class="browse-create-btn"
              size="small"
              variant="flat"
              color="primary"
              :loading="createFolderLoading"
              :disabled="!newFolderName.trim()"
              @click="createFolder"
            >
              Create
            </v-btn>
            <v-btn size="small" variant="text" @click="cancelCreateFolder">
              Cancel
            </v-btn>
          </template>
          <v-btn
            v-else
            size="small"
            variant="outlined"
            prepend-icon="mdi-folder-plus-outline"
            :disabled="!browsePath"
            @click="startCreateFolder"
          >
            New folder
          </v-btn>
        </div>
        <div v-if="createFolderError" class="browse-create-error">
          {{ createFolderError }}
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
                'browse-entry--disabled': !!entryDisabledReason(entry.path),
                'browse-entry--picked': entry.path === pickedFile,
              }"
              :title="entryDisabledReason(entry.path) || entry.path"
              @click="entryClick(entry)"
            >
              <v-icon size="16">{{
                entry.is_file ? "mdi-file-outline" : "mdi-folder"
              }}</v-icon>
              <span class="browse-entry-name">{{ entry.name }}</span>
              <span
                v-if="entryDisabledReason(entry.path)"
                class="browse-entry-reason"
              >
                {{ entryDisabledReason(entry.path) }}
              </span>
            </div>
          </template>
        </div>
      </v-card-text>
      <v-card-actions class="browse-footer">
        <v-spacer></v-spacer>
        <v-btn variant="text" @click="emit('close')">Cancel</v-btn>
        <v-btn
          variant="flat"
          color="primary"
          :disabled="pickModelFile ? !pickedFile : !browsePath"
          @click="selectPath"
        >
          {{ selectedName ? `Select "${selectedName}"` : "Select" }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import {
  browseFilesystem,
  createFilesystemFolder,
} from "../../api/folders";
import { useSubmitGuard } from "../../composables/useSubmitGuard";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  open: { type: Boolean, default: false },
  registeredPaths: { type: Array, default: () => [] },
  imageRoot: { type: String, default: null },
  alreadyRegisteredLabel: { type: String, default: "Already registered" },
  initialPath: { type: String, default: null },
  allowCreateFolder: { type: Boolean, default: false },
  // Pick a model FILE rather than the directory being browsed (the shelf's
  // `Add file`). The same dialog rather than a second one: navigating the host
  // filesystem is the whole interaction either way, and only what a click on a
  // row means and what the footer selects differ.
  pickModelFile: { type: Boolean, default: false },
});

const emit = defineEmits(["select", "close"]);

const browsePath = ref("");
/** The file a click chose, in file mode. Cleared by navigating. */
const pickedFile = ref("");
const browseEntries = ref([]);
const browseLoading = ref(false);
const browseError = ref("");
const browseShowHidden = ref(false);
const creatingFolder = ref(false);
const newFolderName = ref("");
const createFolderError = ref("");
const createFolderInputRef = ref(null);

const selectedName = computed(() => {
  const path = props.pickModelFile ? pickedFile.value : browsePath.value;
  if (!path) return "";
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || "/";
});

function pathSeparator(path) {
  return String(path || "").includes("\\") ? "\\" : "/";
}

function parentPath(path) {
  const raw = String(path || "");
  if (!raw || raw === "/") return "/";
  const trimmed = raw.replace(/[\\/]+$/, "");
  if (/^[A-Za-z]:$/.test(trimmed)) return `${trimmed}\\`;
  const match = trimmed.match(/^(.*)[\\/][^\\/]+$/);
  if (!match) return "/";
  const parent = match[1];
  if (/^[A-Za-z]:$/.test(parent)) return `${parent}\\`;
  return parent || "/";
}

function joinChildPath(parent, child) {
  const sep = pathSeparator(parent);
  const base = String(parent || "").replace(/[\\/]+$/, "");
  if (!base) return `${sep}${child}`;
  if (/^[A-Za-z]:$/.test(base)) return `${base}\\${child}`;
  return `${base}${sep}${child}`;
}

function entryDisabledReason(entryPath) {
  const norm = entryPath.replace(/[\\/]+$/, "");
  if (props.imageRoot) {
    const root = props.imageRoot.replace(/[\\/]+$/, "");
    if (norm === root) return "PixlStash data folder";
  }
  for (const registered of props.registeredPaths) {
    if (norm === registered.replace(/[\\/]+$/, ""))
      return props.alreadyRegisteredLabel;
  }
  return null;
}

async function browseDir(path) {
  browseLoading.value = true;
  browseError.value = "";
  // A choice belongs to the directory it was made in: keeping it across a
  // navigation would leave the footer offering a file the list no longer shows.
  pickedFile.value = "";
  try {
    const listing = await browseFilesystem(path, {
      showHidden: browseShowHidden.value,
      includeModelFiles: props.pickModelFile,
    });
    browseEntries.value = listing?.entries ?? [];
    browsePath.value = listing?.path ?? path ?? "/";
  } catch (error) {
    browseError.value = errorDetail(error) || "Cannot browse this directory.";
    browseEntries.value = [];
  } finally {
    browseLoading.value = false;
  }
}

watch(browseShowHidden, () => {
  if (props.open) browseDir(browsePath.value || null);
});

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) return;
    browseError.value = "";
    browseEntries.value = [];
    browsePath.value = "";
    pickedFile.value = "";
    browseShowHidden.value = false;
    creatingFolder.value = false;
    newFolderName.value = "";
    createFolderError.value = "";
    browseDir(props.initialPath || null);
  },
);

function entryClick(entry) {
  if (entry.is_file) {
    // Selecting rather than confirming: a double-click is not the only way in
    // (the footer button is), and a single click that started a copy would be
    // one slip of the pointer away from writing a file nobody chose.
    if (props.pickModelFile) pickedFile.value = entry.path;
    return;
  }
  if (entryDisabledReason(entry.path)) return;
  browseDir(entry.path);
}

function browseUp() {
  if (!browsePath.value || browsePath.value === "/") return;
  browseDir(parentPath(browsePath.value));
}

function selectPath() {
  const chosen = props.pickModelFile ? pickedFile.value : browsePath.value;
  if (!chosen) return;
  emit("select", chosen);
  emit("close");
}

function startCreateFolder() {
  creatingFolder.value = true;
  newFolderName.value = "";
  createFolderError.value = "";
  requestAnimationFrame(() => {
    createFolderInputRef.value?.$el?.querySelector("input")?.focus();
  });
}

function cancelCreateFolder() {
  creatingFolder.value = false;
  newFolderName.value = "";
  createFolderError.value = "";
}

async function submitNewFolder() {
  const name = newFolderName.value.trim();
  if (!name) return;
  if (name === "." || name === ".." || /[\\/]/.test(name)) {
    createFolderError.value = "Use a plain folder name.";
    return;
  }
  createFolderError.value = "";
  try {
    const target = joinChildPath(browsePath.value, name);
    const created = await createFilesystemFolder(target);
    creatingFolder.value = false;
    newFolderName.value = "";
    await browseDir(created?.path || target);
  } catch (error) {
    createFolderError.value = errorDetail(error) || "Could not create folder.";
  }
}

// The Create button already wore `createFolderLoading`, but the name field
// submits on Enter and stays enabled mid-flight, so a held Enter could ask the
// filesystem for the same directory twice (#647).
const { pending: createFolderLoading, run: createFolder } =
  useSubmitGuard(submitNewFolder);
</script>

<style scoped>
.browser-card {
  overflow: hidden;
}

.browser-header {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  padding: var(--space-6) var(--space-6) var(--space-3);
}

.browse-path-bar {
  display: flex;
  align-items: center;
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.2);
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.browse-path-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  opacity: 0.9;
}

.browse-hidden-toggle {
  flex-shrink: 0;
  font-size: var(--text-xs);
}

.browse-create-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.16);
}

.browse-create-bar .v-text-field {
  flex: 1;
}

.browse-create-error {
  color: rgb(var(--v-theme-error));
  padding: 6px 16px 0;
  font-size: 0.8rem;
}

/* Pending is not disabled (visual-language.md §11): the shared AppButton keeps
   its label legible while busy, but this is a stock Vuetify v-btn, whose own
   `--loading` styling sets `.v-btn__content { opacity: 0 }` - blanking "Create"
   entirely instead of just dimming it. Restore it so this button matches the
   pending contract the rest of #647's forms carry. */
.browse-create-btn :deep(.v-btn--loading .v-btn__content),
.browse-create-btn :deep(.v-btn--loading .v-btn__prepend),
.browse-create-btn :deep(.v-btn--loading .v-btn__append) {
  opacity: 1;
}

/* The spinner keeps spinning under reduced motion, the same fix AppButton takes
   for its own mdi-spin icon: the global reset in design-tokens.css zeroes every
   element's animation, which would freeze Vuetify's indeterminate spinner into a
   static ring that reads as a rendering fault rather than "working". */
@media (prefers-reduced-motion: reduce) {
  .browse-create-btn :deep(.v-progress-circular--indeterminate > svg),
  .browse-create-btn
    :deep(.v-progress-circular--indeterminate .v-progress-circular__overlay) {
    animation-duration: 1.4s !important;
    animation-iteration-count: infinite !important;
  }
}

.browse-entries {
  max-height: 360px;
  overflow-y: auto;
  padding: var(--space-2) 0;
}

.browse-loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.browse-error {
  color: rgb(var(--v-theme-error));
  padding: var(--space-4) var(--space-5);
  font-size: var(--text-sm);
}

.browse-entry {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-5);
  cursor: pointer;
  font-size: var(--text-base);
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

/* The picked file in file mode. A wash plus a left bar, the same pair the shelf
   marks a selected row with, so selection reads the same way in both places. */
.browse-entry--picked {
  background: rgba(var(--v-theme-primary), 0.12);
  box-shadow: inset 3px 0 0 rgb(var(--v-theme-primary));
}

.browse-entry-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.browse-entry-reason {
  font-size: var(--text-2xs);
  opacity: var(--opacity-text-secondary);
  font-style: italic;
  flex-shrink: 0;
}

.browse-footer {
  padding: var(--space-4) var(--space-6) var(--space-5);
}
</style>
