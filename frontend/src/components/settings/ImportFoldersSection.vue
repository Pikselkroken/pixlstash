<template>
  <div class="ifs">
    <div class="ifs-head">
      <h3 class="ifs-title">Import folders</h3>
      <AppButton
        class="ifs-add"
        size="sm"
        variant="primary"
        icon-left="folder-plus-outline"
        @click="openEditor(null)"
      >
        Add import folder…
      </AppButton>
    </div>
    <p class="ifs-lead">
      While PixlStash runs it checks these folders and imports anything new into
      this library. Every folder here is watched, and what it brings in waits in
      the Inbox until you have looked at it.
    </p>

    <p v-if="!store.loaded" class="ifs-state">Reading the import folders…</p>
    <p v-else-if="!store.folders.length" class="ifs-state">
      No import folders yet.
    </p>

    <!-- A table of four facts per folder, then its menu. Nothing is selected
         here, so it is a plain list of rows rather than a grid widget. -->
    <div v-else class="ifs-well">
      <div class="ifs-row ifs-row--head" aria-hidden="true">
        <span>Folder</span>
        <span>After import</span>
        <span>Latest</span>
        <span class="ifs-num">Imported</span>
        <span></span>
      </div>
      <ul class="ifs-list" role="list">
        <li v-for="folder in store.folders" :key="folder.id" class="ifs-row">
          <span class="ifs-folder">
            <span class="ifs-name">{{ folder.label || folder.folder }}</span>
            <FolderPath :path="folder.folder" />
          </span>
          <span class="ifs-meta">{{
            folder.delete_after_import ? "Delete original" : "Keep original"
          }}</span>
          <span
            :class="['ifs-meta', { 'ifs-missing': folder.missing }]"
            :role="folder.missing ? 'status' : undefined"
            >{{ latestLabel(folder) }}</span
          >
          <span class="ifs-meta ifs-num">{{
            Number(folder.picture_count || 0).toLocaleString()
          }}</span>
          <v-menu location="bottom end" origin="top end" :offset="6">
            <template #activator="{ props: menuProps }">
              <AppButton
                v-bind="menuProps"
                icon-only
                variant="ghost"
                icon-left="dots-vertical"
                aria-haspopup="menu"
                :aria-label="`More for ${folder.label || folder.folder}`"
              />
            </template>
            <div
              class="ctx-menu"
              role="menu"
              tabindex="-1"
              @keydown="onMenuKeydown"
            >
              <button
                class="ctx-item"
                type="button"
                role="menuitem"
                @click="openEditor(folder)"
              >
                <v-icon class="ctx-icon">mdi-pencil-outline</v-icon>
                <span class="ctx-label-text">Edit…</span>
              </button>
              <button
                class="ctx-item"
                type="button"
                role="menuitem"
                @click="removeFolder(folder)"
              >
                <v-icon class="ctx-icon">mdi-folder-off-outline</v-icon>
                <span class="ctx-label-text">Remove</span>
              </button>
            </div>
          </v-menu>
        </li>
      </ul>
    </div>

    <!-- Add and edit share one form. The folder itself is fixed once added:
         a different folder is a different import folder. -->
    <AppDialog
      :open="editorOpen"
      :title="editorFolder ? `Edit ${editorFolder.label || editorFolder.folder}` : 'Add import folder'"
      size="sm"
      @close="closeEditor"
      @accept="saveEditor"
    >
      <div class="ifs-form">
        <div v-if="!editorFolder" class="ifs-form__path">
          <AppInput
            v-model="form.folder"
            label="Folder"
            mono
            placeholder="/home/me/ComfyUI/output"
            :disabled="saving"
          />
          <AppButton
            size="sm"
            variant="secondary"
            :disabled="saving"
            @click="browseOpen = true"
          >
            Browse…
          </AppButton>
        </div>
        <FolderPath v-else :path="editorFolder.folder" />
        <AppInput
          v-if="inDocker"
          v-model="form.hostPath"
          label="Folder on the host"
          mono
          placeholder="/home/me/ComfyUI/output"
          :disabled="saving"
        />
        <AppInput
          v-model="form.label"
          label="Name"
          :placeholder="defaultLabel"
          :disabled="saving"
        />
        <Segmented
          v-model="form.deleteAfterImport"
          :options="AFTER_IMPORT_OPTIONS"
          aria-label="After import"
        />
        <p v-if="saveError" class="ifs-form__error" role="alert">
          {{ saveError }}
        </p>
      </div>
      <template #footer>
        <AppButton size="sm" variant="secondary" @click="closeEditor">
          Cancel
        </AppButton>
        <AppButton
          size="sm"
          variant="primary"
          :loading="saving"
          :disabled="!editorFolder && !form.folder.trim()"
          @click="saveEditor"
        >
          {{ editorFolder ? "Save" : "Add" }}
        </AppButton>
      </template>
    </AppDialog>
    <FolderBrowser
      :open="browseOpen"
      :registered-paths="store.folders.map((f) => f.folder)"
      already-registered-label="Already an import folder"
      @select="onPicked"
      @close="browseOpen = false"
    />
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";
import Segmented from "../widgets/Segmented.vue";
import FolderBrowser from "../editors/FolderBrowser.vue";
import FolderPath from "./FolderPath.vue";
import {
  createFolder,
  deleteFolder,
  listReferenceFolders,
  patchFolder,
} from "../../api/folders";
import { useImportFoldersStore } from "../../stores/useImportFoldersStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { relativeDate } from "../../utils/snapshots";

const props = defineProps({
  open: { type: Boolean, default: false },
});

const AFTER_IMPORT_OPTIONS = [
  { id: false, label: "Keep original" },
  { id: true, label: "Delete original" },
];

const store = useImportFoldersStore();
const noticeStore = useNoticeStore();

const editorOpen = ref(false);
const editorFolder = ref(null); // null = add, a folder = edit
const browseOpen = ref(false);
const saving = ref(false);
const saveError = ref("");
const form = reactive({
  folder: "",
  hostPath: "",
  label: "",
  deleteAfterImport: false,
});
// In Docker the folder is a path inside the container, and the server also
// needs the host folder mounted there.
const inDocker = ref(false);

// What the folder is called when no name is given: its last component.
const defaultLabel = computed(() => {
  const path = editorFolder.value?.folder ?? form.folder;
  return (
    String(path || "")
      .replace(/[/\\]+$/, "")
      .split(/[/\\]/)
      .pop() || "Name"
  );
});

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    store.refresh();
    try {
      inDocker.value = Boolean((await listReferenceFolders())?.in_docker);
    } catch (err) {
      console.warn("Could not tell whether the server runs in Docker", err);
    }
  },
  { immediate: true },
);

function latestLabel(folder) {
  if (folder.missing) return "Folder not found";
  if (!folder.last_imported_at) return "Nothing yet";
  return relativeDate(folder.last_imported_at);
}

function openEditor(folder) {
  editorFolder.value = folder;
  form.folder = "";
  form.hostPath = folder?.host_path ?? "";
  form.label = folder?.label ?? "";
  form.deleteAfterImport = Boolean(folder?.delete_after_import);
  saveError.value = "";
  editorOpen.value = true;
}

function closeEditor() {
  if (saving.value) return;
  editorOpen.value = false;
  editorFolder.value = null;
}

function onPicked(path) {
  browseOpen.value = false;
  if (path) form.folder = path;
}

async function saveEditor() {
  if (saving.value) return;
  if (!editorFolder.value && !form.folder.trim()) return;
  saving.value = true;
  saveError.value = "";
  const body = {
    label: form.label.trim() || defaultLabel.value,
    delete_after_import: form.deleteAfterImport,
  };
  if (inDocker.value) body.host_path = form.hostPath.trim() || null;
  try {
    if (editorFolder.value) {
      await patchFolder("import", editorFolder.value.id, body);
    } else {
      await createFolder("import", { ...body, folder: form.folder.trim() });
    }
    saving.value = false;
    closeEditor();
    await store.refresh();
  } catch (err) {
    console.error("Failed to save import folder", form.folder, err);
    saveError.value = errorDetail(err) || "Couldn't save that import folder.";
    saving.value = false;
  }
}

async function removeFolder(folder) {
  const name = folder.label || folder.folder;
  if (
    !window.confirm(
      `Stop watching "${name}"? What it already imported stays in the library.`,
    )
  )
    return;
  try {
    await deleteFolder("import", folder.id);
    await store.refresh();
  } catch (err) {
    console.error("Failed to remove import folder", folder.folder, err);
    noticeStore.error(
      `Couldn't remove that import folder. ${errorDetail(err) || ""}`.trim(),
      { key: "import-folder-remove" },
    );
  }
}
</script>

<style scoped>
.ifs {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.ifs-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.ifs-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}

.ifs-add {
  margin-left: auto;
}

.ifs-lead,
.ifs-state {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.ifs-well {
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-background));
  overflow: hidden;
}

.ifs-list {
  list-style: none;
  padding: 0;
  margin: 0;
}

/* Folder, then three facts in columns that line up down the table, then ⋮. */
.ifs-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 8em 8em 6em 28px;
  column-gap: var(--space-3);
  align-items: center;
  min-height: 48px;
  padding: var(--space-2) var(--space-2) var(--space-2) var(--space-4);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.ifs-list .ifs-row:last-child {
  border-bottom: 0;
}

.ifs-list .ifs-row:hover {
  background: var(--hover-wash);
}

.ifs-row--head {
  min-height: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.ifs-folder {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.ifs-name {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ifs-folder :deep(.fp) {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.ifs-meta {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
}

.ifs-num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.ifs-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.ifs-form__path {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}

.ifs-form__path > :first-child {
  flex: 1;
  min-width: 0;
}

.ifs-form__error {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-error));
}

/* Status words on a surface take the surface status token, with the words
   themselves carrying the meaning. */
.ifs-missing {
  color: rgb(var(--v-theme-surface-error));
}
</style>
