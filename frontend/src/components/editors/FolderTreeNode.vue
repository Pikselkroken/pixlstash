<script setup>
import { defineProps, defineEmits } from "vue";

const props = defineProps({
  entry: { type: Object, required: true },
  rfId: { type: Number, required: true },
  depth: { type: Number, default: 1 },
  selectedFolderKey: { type: String, default: null },
  folderBrowseCache: { type: Object, required: true },
  expandedFolderIds: { type: Object, required: true }, // Set
  dropTargetKey: { type: String, default: null },
});

const emit = defineEmits([
  "select",
  "toggle",
  "drag-over",
  "drag-leave",
  "drop",
  "context",
  "metadata",
]);

function isExpanded() {
  return props.expandedFolderIds.has(props.entry.path);
}

function hasChildren() {
  const cached = props.folderBrowseCache[props.entry.path];
  if (isExpanded()) return true;
  if (!cached || cached.error) return false;
  return (cached.entries?.length ?? 0) > 0;
}

function childImageCount() {
  const cachedCount = Number(
    props.folderBrowseCache[props.entry.path]?.image_count,
  );
  if (Number.isFinite(cachedCount) && cachedCount >= 0) {
    return cachedCount;
  }
  const entryCount = Number(props.entry?.image_count);
  if (Number.isFinite(entryCount) && entryCount >= 0) {
    return entryCount;
  }
  return 0;
}
</script>

<template>
  <div>
    <div
      class="sidebar-folder-row sidebar-folder-child-row"
      :class="{
        active: selectedFolderKey === 'path-' + entry.path,
        droppable: dropTargetKey === 'path-' + entry.path,
      }"
      :title="`${entry.path} - drop dragged reference images here to move them`"
      @contextmenu.prevent="
        emit('context', { rfId, path: entry.path, label: entry.name, event: $event })
      "
      @dragover="emit('drag-over', { rfId, path: entry.path, event: $event })"
      @dragleave="emit('drag-leave', { rfId, path: entry.path, event: $event })"
      @drop="emit('drop', { rfId, path: entry.path, event: $event })"
      @click="
        emit('select', 'path-' + entry.path, {
          referenceFolderId: rfId,
          pathPrefix: entry.path,
          label: entry.name,
        })
      "
    >
      <v-icon
        size="16"
        class="sidebar-folder-chevron"
        :style="{ visibility: hasChildren() ? 'visible' : 'hidden' }"
        @click.stop="emit('toggle', entry.path)"
      >
        {{ isExpanded() ? "mdi-chevron-down" : "mdi-chevron-right" }}
      </v-icon>
      <v-icon size="16" class="sidebar-folder-icon">mdi-folder-outline</v-icon>
      <span class="sidebar-folder-label">{{ entry.name }}</span>
      <span class="sidebar-folder-actions" @click.stop>
        <button
          type="button"
          class="sidebar-folder-action-btn"
          title="Import TXT metadata"
          @click="
            emit('metadata', {
              action: 'import',
              rfId,
              path: entry.path,
              label: entry.name,
            })
          "
        >
          <v-icon size="13">mdi-file-import-outline</v-icon>
        </button>
        <button
          type="button"
          class="sidebar-folder-action-btn"
          title="Export TXT metadata"
          @click="
            emit('metadata', {
              action: 'export',
              rfId,
              path: entry.path,
              label: entry.name,
            })
          "
        >
          <v-icon size="13">mdi-file-export-outline</v-icon>
        </button>
      </span>
      <span
        v-if="folderBrowseCache[entry.path]?.loading || childImageCount() > 0"
        class="sidebar-folder-count-badge"
        title="Direct images in folder"
      >
        {{ folderBrowseCache[entry.path]?.loading ? "..." : childImageCount() }}
      </span>
    </div>

    <div v-if="isExpanded()" class="sidebar-folder-children">
      <div
        v-if="folderBrowseCache[entry.path]?.loading"
        class="sidebar-folder-loading-row"
      >
        <v-progress-circular indeterminate size="14" />
      </div>
      <template v-else>
        <FolderTreeNode
          v-for="child in folderBrowseCache[entry.path]?.entries ?? []"
          :key="child.path"
          :entry="child"
          :rf-id="rfId"
          :depth="depth + 1"
          :selected-folder-key="selectedFolderKey"
          :folder-browse-cache="folderBrowseCache"
          :expanded-folder-ids="expandedFolderIds"
          :drop-target-key="dropTargetKey"
          @select="(key, payload) => emit('select', key, payload)"
          @toggle="(path) => emit('toggle', path)"
          @drag-over="(payload) => emit('drag-over', payload)"
          @drag-leave="(payload) => emit('drag-leave', payload)"
          @drop="(payload) => emit('drop', payload)"
          @context="(payload) => emit('context', payload)"
          @metadata="(payload) => emit('metadata', payload)"
        />
        <div
          v-if="folderBrowseCache[entry.path]?.error"
          class="sidebar-folder-empty-row sidebar-folder-error-row"
        >
          <v-icon size="13">mdi-alert-circle-outline</v-icon> Cannot browse
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.sidebar-folder-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  cursor: pointer;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-sidebar-text), 0.85);
  user-select: none;
}

.sidebar-folder-row:hover {
  background: rgba(var(--v-theme-accent), 0.1);
}

.sidebar-folder-row.active {
  background: rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
  border-left: 3px solid rgb(var(--v-theme-primary));
}

.sidebar-folder-row.active:hover {
  background: linear-gradient(rgba(var(--v-theme-accent), 0.08), rgba(var(--v-theme-accent), 0.08)), rgba(var(--v-theme-primary), 0.18);
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-folder-children {
  padding-left: var(--space-2);
  border-left: 1px dashed rgba(var(--v-theme-border), 0.35);
  margin-left: var(--space-4);
}

.sidebar-folder-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.sidebar-folder-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.15s;
}

.sidebar-folder-row:hover .sidebar-folder-actions,
.sidebar-folder-row:focus-within .sidebar-folder-actions,
.sidebar-folder-row.active .sidebar-folder-actions {
  opacity: 1;
  pointer-events: auto;
}

.sidebar-folder-action-btn {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: inherit;
  cursor: pointer;
  opacity: 0.72;
}

.sidebar-folder-action-btn:hover {
  background: rgba(var(--v-theme-accent), 0.18);
  opacity: 1;
}

.sidebar-folder-chevron,
.sidebar-folder-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.sidebar-folder-status-badge {
  flex-shrink: 0;
  margin-left: var(--space-1);
  opacity: 0.75;
}

.sidebar-folder-count-badge {
  flex-shrink: 0;
  margin-left: var(--space-2);
  min-width: 22px;
  text-align: right;
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-sidebar-text), 0.6);
}

.sidebar-folder-row.active .sidebar-folder-count-badge {
  color: rgba(var(--v-theme-on-primary), 0.9);
}

.sidebar-folder-row.droppable {
  filter: brightness(1.2);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-folder-status--active {
  color: rgba(var(--v-theme-sidebar-text), 0.4);
  cursor: pointer;
  border-radius: var(--radius-sm);
  transition:
    color 0.15s,
    opacity 0.15s;
}

.sidebar-folder-status--active:hover {
  color: rgb(var(--v-theme-sidebar-text));
  opacity: 1;
}

.sidebar-folder-loading-row {
  display: flex;
  justify-content: center;
  padding: var(--space-3);
}

.sidebar-folder-empty-row {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-sidebar-text), 0.45);
  font-style: italic;
}

.sidebar-folder-error-row {
  color: rgba(var(--v-theme-error, 244, 67, 54), 0.8);
}
</style>
