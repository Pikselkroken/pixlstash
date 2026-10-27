<template>
  <!-- The Folders tree (#2203): drives, then the folders registered on each,
       then their subfolders, one level per expand. Flat rows carrying
       `aria-level`, which is the tree pattern without nested groups: the
       roving focus walks one list, and a row never re-mounts on expand. -->
  <div
    class="mft"
    role="tree"
    :aria-label="label"
    data-testid="model-folder-tree"
    @keydown="onKeydown"
  >
    <div
      v-for="node in nodes"
      :key="node.key"
      class="ps-row mft-row model-drop-target"
      :class="[
        {
          'mft-row--drive': node.kind === 'drive',
          active: isSelected(node),
          'mft-row--off': node.disabled,
        },
        drop.dropClass(node.key),
      ]"
      :style="{ '--depth': node.level - 1 }"
      role="treeitem"
      :aria-level="node.level"
      :aria-expanded="node.expandable ? String(node.expanded) : undefined"
      :aria-selected="node.kind === 'drive' ? undefined : String(isSelected(node))"
      :aria-disabled="node.disabled ? 'true' : undefined"
      :aria-label="node.aria"
      :tabindex="node.key === tabStopKey ? 0 : -1"
      :data-key="node.key"
      :ref="(el) => setRowEl(node.key, el)"
      :data-folder-id="node.folderId"
      :data-path="node.path"
      @click="activate(node)"
      @focus="focusedKey = node.key"
      @dragover="onDragOver($event, node)"
      @dragleave="drop.onDragLeave($event, node.key)"
      @drop="onDrop($event, node)"
    >
      <span
        class="ps-row-glyph mft-twist"
        :class="{ 'ps-row-glyph--empty': !node.expandable }"
        aria-hidden="true"
        @click.stop="toggle(node)"
        ><v-icon size="16">{{
          node.expanded ? "mdi-chevron-down" : "mdi-chevron-right"
        }}</v-icon></span
      >
      <template v-if="node.kind === 'drive'">
        <span class="mft-drive">
          <span class="mft-line">
            <v-icon size="16" class="mft-glyph" aria-hidden="true"
              >mdi-harddisk</v-icon
            >
            <span class="mft-label">{{ node.name }}</span>
            <span
              v-if="node.drive"
              class="mft-free"
              :class="{ 'mft-free--full': node.full }"
              >{{ formatModelSize(node.drive.free) }} free</span
            >
          </span>
          <span
            v-if="node.drive"
            class="mft-bar"
            :class="{ 'mft-bar--full': node.full }"
            aria-hidden="true"
            ><span :style="{ width: `${node.drive.used}%` }"></span
          ></span>
        </span>
      </template>
      <template v-else>
        <v-icon size="16" class="mft-glyph" aria-hidden="true">{{
          node.expanded ? "mdi-folder-open-outline" : "mdi-folder-outline"
        }}</v-icon>
        <span class="mft-label">{{ node.name }}</span>
        <v-icon
          v-if="node.locked"
          size="14"
          class="mft-lock"
          aria-hidden="true"
          data-testid="model-folder-lock"
          >mdi-lock-outline</v-icon
        >
        <span class="mft-count">{{ node.count }}</span>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import { DOWNLOADS_KIND, SOURCE_KIND } from "../../api/modelFolders";
import { useModelCopyDrop } from "../../composables/useModelCopyDrop";
import {
  basename,
  useModelFoldersStore,
} from "../../stores/useModelFoldersStore";
import { driveFullness } from "../../utils/libraryChrome";
import { formatModelSize } from "../../utils/modelShelf";

/**
 * Browse or pick a model folder.
 *
 * `browse` (the sidebar) opens what is clicked, a locked folder included: it
 * lists everything under it. `pick` (a destination, for moving models) cannot
 * choose a locked folder, nor any row `disabled` refuses, and reads the same
 * `selected` back so the parent owns the choice.
 *
 * In `browse` the rows also take model rows dragged off the shelf (#2204): a
 * drop asks for the Move dialog, pre-chosen to that folder.
 */
const props = defineProps({
  mode: {
    type: String,
    default: "browse",
    validator: (value) => ["browse", "pick"].includes(value),
  },
  /** `{folderId, path}` of the open or picked folder, or null. */
  selected: { type: Object, default: null },
  /** Pick mode: `(node) => boolean`, true to refuse a row. */
  disabled: { type: Function, default: null },
  label: { type: String, default: "Model folders" },
});

/** `{folderId, path, locked}` of the row chosen. */
const emit = defineEmits(["select"]);

const foldersStore = useModelFoldersStore();
const focusedKey = ref(null);
/** Expanded folder keys. Drives start open, so they are tracked closed. */
const expanded = ref(new Set());
const closedDrives = ref(new Set());

const isLocked = (folder) => folder.movable !== "per_item";
const folderName = (folder) =>
  folder.tool || basename(folder.path) || folder.path;
const nodeKey = (folderId, path) => `f:${folderId}:${path}`;
const count = (n) => (n == null ? "" : Number(n).toLocaleString());

/** The folders the shelf catalogues: not ai-toolkit's root nor Downloads. */
const shelfFolders = computed(() =>
  foldersStore.folders.filter(
    (folder) => folder.kind !== SOURCE_KIND && folder.kind !== DOWNLOADS_KIND,
  ),
);

/**
 * The drives, in the server's order, with their folders. Without capacities
 * (the route failed, or has not answered) the folders sit under one plain
 * heading rather than vanishing.
 */
const drives = computed(() => {
  const byKey = new Map();
  for (const folder of shelfFolders.value) {
    const device = foldersStore.deviceByFolderId.get(Number(folder.id));
    const key = device
      ? `drive:${device.device_id ?? device.mount_point}`
      : "drive:none";
    if (!byKey.has(key)) {
      byKey.set(key, {
        key,
        name: device ? device.label || device.mount_point : "Folders",
        drive: driveFullness(device),
        folders: [],
      });
    }
    byKey.get(key).folders.push(folder);
  }
  return [...byKey.values()];
});

function listingOf(folderId, path) {
  return foldersStore.dirListings.get(foldersStore.dirsKey(folderId, path));
}

/** A folder or subfolder row, and its open children after it. */
function pushFolderRows(out, entry) {
  const key = nodeKey(entry.folderId, entry.path);
  const listing = listingOf(entry.folderId, entry.path);
  const open = expanded.value.has(key);
  const expandable =
    !entry.locked &&
    (listing?.listing
      ? listing.listing.dirs.length > 0
      : (entry.hasDirs ?? !listing?.error));
  const n = entry.count ?? listing?.listing?.model_count;
  const refused = props.mode === "pick" && (entry.locked || props.disabled?.(entry));
  out.push({
    ...entry,
    key,
    expandable,
    expanded: expandable && open,
    disabled: Boolean(refused),
    count: count(n),
    aria: [
      entry.name,
      n == null ? "" : `${count(n)} ${Number(n) === 1 ? "model" : "models"}`,
      entry.locked ? "locked" : "",
    ]
      .filter(Boolean)
      .join(", "),
  });
  if (!(expandable && open)) return;
  for (const dir of listing?.listing?.dirs ?? []) {
    pushFolderRows(out, {
      kind: "dir",
      folderId: entry.folderId,
      path: dir.path,
      name: dir.name,
      level: entry.level + 1,
      locked: false,
      hasDirs: dir.has_dirs,
      count: dir.model_count,
    });
  }
}

/** Every visible row, in reading order: what the arrows walk. */
const nodes = computed(() => {
  const out = [];
  for (const drive of drives.value) {
    const open = !closedDrives.value.has(drive.key);
    const full = Boolean(drive.drive && drive.drive.used >= 90);
    out.push({
      kind: "drive",
      key: drive.key,
      name: drive.name,
      drive: drive.drive,
      full,
      level: 1,
      expandable: true,
      expanded: open,
      disabled: false,
      aria: [
        drive.name,
        drive.drive
          ? `${formatModelSize(drive.drive.free)} free, ${drive.drive.used}% full`
          : "",
      ]
        .filter(Boolean)
        .join(", "),
    });
    if (!open) continue;
    for (const folder of drive.folders) {
      pushFolderRows(out, {
        kind: "folder",
        folderId: Number(folder.id),
        path: "",
        name: folderName(folder),
        level: 2,
        locked: isLocked(folder),
      });
    }
  }
  return out;
});

function isSelected(node) {
  return (
    node.kind !== "drive" &&
    props.selected != null &&
    Number(props.selected.folderId) === node.folderId &&
    (props.selected.path ?? "") === node.path
  );
}

/** The one row in the tab order: the focused one, else the open one. */
const tabStopKey = computed(() => {
  const keys = nodes.value.map((node) => node.key);
  if (keys.includes(focusedKey.value)) return focusedKey.value;
  const open = nodes.value.find(isSelected);
  return open?.key ?? keys[0] ?? null;
});

function setExpanded(node, open) {
  if (node.kind === "drive") {
    const next = new Set(closedDrives.value);
    if (open) next.delete(node.key);
    else next.add(node.key);
    closedDrives.value = next;
    return;
  }
  if (!node.expandable) return;
  const next = new Set(expanded.value);
  if (open) {
    next.add(node.key);
    foldersStore.loadDirs(node.folderId, node.path);
  } else {
    next.delete(node.key);
  }
  expanded.value = next;
}

function toggle(node) {
  if (node.expandable) setExpanded(node, !node.expanded);
}

function activate(node) {
  if (node.kind === "drive") {
    toggle(node);
    return;
  }
  if (node.disabled) return;
  emit("select", {
    folderId: node.folderId,
    path: node.path,
    locked: node.locked,
  });
}

const drop = useModelCopyDrop();

/** Where a drop on this row lands: null for a drive, which is no place. */
function dropDestination(node) {
  if (node.kind === "drive") return null;
  return { folderId: node.folderId, path: node.path, locked: node.locked };
}

function onDragOver(event, node) {
  if (props.mode === "browse") {
    drop.onDragOver(event, node.key, dropDestination(node));
  }
}

function onDrop(event, node) {
  if (props.mode === "browse") drop.onDrop(event, dropDestination(node));
}

/** Row elements by key, for the arrows to move focus to. */
const rowEls = new Map();
function setRowEl(key, el) {
  if (el) rowEls.set(key, el);
  else rowEls.delete(key);
}

function focusNode(node) {
  if (!node) return;
  focusedKey.value = node.key;
  rowEls.get(node.key)?.focus();
}

function onKeydown(event) {
  const list = nodes.value;
  const at = list.findIndex((node) => node.key === tabStopKey.value);
  const node = list[at];
  if (!node) return;
  const parentOf = () =>
    list
      .slice(0, at)
      .reverse()
      .find((one) => one.level < node.level);
  switch (event.key) {
    case "ArrowDown":
      focusNode(list[at + 1]);
      break;
    case "ArrowUp":
      focusNode(list[at - 1]);
      break;
    case "Home":
      focusNode(list[0]);
      break;
    case "End":
      focusNode(list[list.length - 1]);
      break;
    case "ArrowRight":
      if (node.expandable && !node.expanded) setExpanded(node, true);
      else if (node.expanded && list[at + 1]?.level > node.level) {
        focusNode(list[at + 1]);
      }
      break;
    case "ArrowLeft":
      if (node.expanded) setExpanded(node, false);
      else focusNode(parentOf());
      break;
    case "Enter":
    case " ":
      activate(node);
      break;
    default:
      return;
  }
  event.preventDefault();
}

/**
 * Open the way down to the selected folder, so a subfolder opened from the
 * list or the breadcrumb is a lit row in the tree rather than a hidden one.
 */
watch(
  () => props.selected,
  (picked) => {
    if (!picked?.path) return;
    const id = Number(picked.folderId);
    const parts = picked.path.split("/");
    const next = new Set(expanded.value);
    for (let i = 0; i < parts.length; i += 1) {
      const path = parts.slice(0, i).join("/");
      next.add(nodeKey(id, path));
      foldersStore.loadDirs(id, path);
    }
    expanded.value = next;
  },
  { immediate: true },
);

// Every registered folder's own level, for its count and whether it has any
// subfolders to offer. One request per folder, and only once per visit.
watch(
  shelfFolders,
  (folders) => {
    for (const folder of folders) foldersStore.loadDirs(Number(folder.id), "");
  },
  { immediate: true },
);
</script>

<style scoped>
.mft {
  display: flex;
  flex-direction: column;
}

.mft-row {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: var(--control-h-bar);
  color: inherit;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.mft-row:hover {
  background: var(--hover-wash);
}

.mft-row.active {
  background: var(--active-wash);
  border-left-color: var(--active-bar);
  font-weight: var(--weight-medium);
}

.mft-row:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

.mft-row--off {
  cursor: default;
  opacity: var(--opacity-disabled);
}

.mft-row--off:hover {
  background: transparent;
}

.mft-row--drive {
  padding-top: var(--space-2);
  padding-bottom: var(--space-2);
  font-weight: var(--weight-semibold);
}

.mft-twist {
  cursor: pointer;
  opacity: var(--opacity-text-secondary);
}

.mft-glyph {
  flex-shrink: 0;
  margin-right: var(--space-3);
  opacity: var(--opacity-text-secondary);
}

.mft-drive {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.mft-line {
  display: flex;
  align-items: center;
  min-width: 0;
}

.mft-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mft-lock {
  flex-shrink: 0;
  margin-left: var(--space-2);
  opacity: var(--opacity-text-secondary);
}

.mft-count,
.mft-free {
  flex-shrink: 0;
  margin-left: var(--space-2);
  font-size: var(--text-xs);
  font-weight: var(--weight-regular);
  font-variant-numeric: tabular-nums;
  opacity: var(--opacity-text-secondary);
}

.mft-free--full {
  color: rgb(var(--v-theme-surface-warning));
  opacity: 1;
}

/* The drive meter, as the title bar's library picker draws it, under the
   drive's name; the warning colour from 90% full. */
.mft-bar {
  display: flex;
  height: 3px;
  margin-left: calc(var(--gutter-glyph) + var(--space-3));
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--drive-meter-track);
}

.mft-bar > span {
  display: block;
  background: rgb(var(--v-theme-surface-success));
}

.mft-bar--full > span {
  background: rgb(var(--v-theme-surface-warning));
}
</style>
