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
  // Whether the hovered row would REFUSE this payload. Without it the row lit
  // up with the accept highlight for a drag that never had preventDefault()
  // called on it, so releasing did nothing (#757 covered the root row only).
  dropRejected: { type: Boolean, default: false },
});

const emit = defineEmits([
  "select",
  "toggle",
  "drag-over",
  "drag-leave",
  "drop",
  "context",
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
        droppable: dropTargetKey === 'path-' + entry.path && !dropRejected,
        'not-droppable':
          dropTargetKey === 'path-' + entry.path && dropRejected,
      }"
      :style="{ '--depth': depth }"
      :title="`${entry.path} - drop dragged reference images here to move them`"
      @contextmenu.prevent="
        emit('context', {
          rfId,
          path: entry.path,
          label: entry.name,
          event: $event,
        })
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
        size="12"
        class="sidebar-row-glyph sidebar-folder-chevron"
        :class="{ 'sidebar-row-glyph--empty': !hasChildren() }"
        @click.stop="emit('toggle', entry.path)"
      >
        {{ isExpanded() ? "mdi-chevron-down" : "mdi-chevron-right" }}
      </v-icon>
      <v-icon size="16" class="sidebar-row-glyph sidebar-folder-icon"
        >mdi-folder-outline</v-icon
      >
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
          :drop-target-key="dropTargetKey"
          :drop-rejected="dropRejected"
          @select="(key, payload) => emit('select', key, payload)"
          @toggle="(path) => emit('toggle', path)"
          @drag-over="(payload) => emit('drag-over', payload)"
          @drag-leave="(payload) => emit('drag-leave', payload)"
          @drop="(payload) => emit('drop', payload)"
          @context="(payload) => emit('context', payload)"
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

<!-- The row itself is styled by the unscoped row system in SideBar.global.css.
     This component used to carry its own copy of .sidebar-folder-row and
     friends, which had drifted to an 8px inset instead of the sidebar's and
     was missing the base selection rail, so selecting a nested folder shifted
     its label 3px right. Do not re-add a local copy; see visual-language.md
     §5.1. Only rules with no counterpart in the shared system stay here. -->
<style scoped>
.sidebar-folder-status--active {
  color: rgb(var(--v-theme-sidebar-text));
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
  color: rgb(var(--v-theme-sidebar-text));
  font-style: italic;
}

.sidebar-folder-error-row {
  color: rgba(var(--v-theme-error, 244, 67, 54), 0.8);
}
</style>
