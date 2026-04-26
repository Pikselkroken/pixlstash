<script setup>
import { defineProps, defineEmits } from "vue";

const props = defineProps({
  entry: { type: Object, required: true },
  rfId: { type: Number, required: true },
  depth: { type: Number, default: 1 },
  selectedFolderKey: { type: String, default: null },
  folderBrowseCache: { type: Object, required: true },
  expandedFolderIds: { type: Object, required: true }, // Set
});

const emit = defineEmits(["select", "toggle"]);

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
      :class="{ active: selectedFolderKey === 'path-' + entry.path }"
      :title="entry.path"
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
          @select="(key, payload) => emit('select', key, payload)"
          @toggle="(path) => emit('toggle', path)"
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
  gap: 4px;
  padding: 4px 8px;
  cursor: pointer;
  font-size: 0.82rem;
  color: rgba(var(--v-theme-sidebar-text), 0.85);
  user-select: none;
}

.sidebar-folder-row:hover {
  background: rgba(var(--v-theme-accent), 0.1);
}

.sidebar-folder-row.active {
  background: rgba(var(--v-theme-primary), 0.6);
  color: rgb(var(--v-theme-on-primary));
}

.sidebar-folder-children {
  padding-left: 4px;
  border-left: 1px dashed rgba(var(--v-theme-border), 0.35);
  margin-left: 11px;
}

.sidebar-folder-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}

.sidebar-folder-chevron,
.sidebar-folder-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.sidebar-folder-status-badge {
  flex-shrink: 0;
  margin-left: 2px;
  opacity: 0.75;
}

.sidebar-folder-count-badge {
  flex-shrink: 0;
  margin-left: 4px;
  min-width: 22px;
  text-align: right;
  font-size: 0.74rem;
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-sidebar-text), 0.6);
}

.sidebar-folder-row.active .sidebar-folder-count-badge {
  color: rgba(var(--v-theme-on-primary), 0.9);
}

.sidebar-folder-status--active {
  color: rgba(var(--v-theme-sidebar-text), 0.4);
  cursor: pointer;
  border-radius: 3px;
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
  padding: 8px;
}

.sidebar-folder-empty-row {
  padding: 4px 8px;
  font-size: 0.78rem;
  color: rgba(var(--v-theme-sidebar-text), 0.45);
  font-style: italic;
}

.sidebar-folder-error-row {
  color: rgba(var(--v-theme-error, 244, 67, 54), 0.8);
}
</style>
