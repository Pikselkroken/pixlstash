<template>
  <!-- The Folders tree (#2203): drives, registered folders and their subfolders,
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
    <template v-for="node in nodes" :key="node.key">
      <div
        class="ps-row mft-row model-drop-target"
        :class="[
          {
            'mft-row--drive': node.kind === 'drive',
            active: isSelected(node),
            'mft-row--off': node.disabled,
            'mft-row--lifted': dragKey === node.key,
          },
          dropClassFor(node.key),
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
        :draggable="canDrag(node) ? 'true' : undefined"
        @click="activate(node)"
        @contextmenu="onContextMenu($event, node)"
        @focus="focusedKey = node.key"
        @dragstart="onDragStart($event, node)"
        @dragend="onDragEnd"
        @dragover="onDragOver($event, node)"
        @dragleave="onDragLeave($event, node)"
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
          <!-- What ComfyUI loads from this very folder (#2205). -->
          <span
            v-for="type in node.marks"
            :key="type.key"
            class="mft-mark"
            data-testid="comfyui-mark"
            ><Tooltip :text="comfyuiLoadsTitle(type)" activator="parent" /><TypeMark
              :mark="type.mark"
              :tint="type.tint"
          /></span>
          <v-icon
            v-if="node.locked"
            size="14"
            class="mft-lock"
            aria-hidden="true"
            data-testid="model-folder-lock"
            >mdi-lock-outline</v-icon
          >
          <span class="mft-count">{{ node.count }}</span>
          <!-- The row's own menu, for a pointer. Hidden until hover or focus,
               and out of the tab order: the row is the one stop, and the menu
               is Shift+F10 or the Menu key on it. -->
          <button
            v-if="menuFor(node).length"
            type="button"
            class="mft-more"
            tabindex="-1"
            aria-hidden="true"
            aria-haspopup="menu"
            data-testid="model-folder-more"
            @click.stop="onMoreClick($event, node)"
          >
            <v-icon size="16">mdi-dots-horizontal</v-icon>
          </button>
        </template>
      </div>

      <!-- The name field, under the row it names: a new folder is listed as the
           first child of its parent, a rename sits on the row's own level. -->
      <div
        v-if="editorFor(node)"
        class="mft-editor"
        :class="{ 'mft-editor--rename': editorFor(node).mode === 'rename' }"
        :style="{
          '--depth':
            editorFor(node).mode === 'new' ? node.level : node.level - 1,
        }"
        role="presentation"
      >
        <ModelFolderNameField
          :model-value="editorFor(node).name"
          :label="
            editorFor(node).mode === 'new'
              ? `New folder in ${node.name}`
              : `Rename ${node.name}`
          "
          :hint="
            editorFor(node).mode === 'new'
              ? 'Enter creates it here · Esc cancels'
              : 'Enter renames · Esc cancels'
          "
          :error="editorFor(node).error"
          :select="editorFor(node).mode === 'rename'"
          @update:model-value="(value) => (editorFor(node).name = value)"
          @submit="commitEditor"
          @cancel="cancelEditor"
        />
      </div>
    </template>

    <!-- One menu for the tree, positioned where the right-click or the ⋯ was.
         The rows of it depend on the row it was opened from. -->
    <v-menu
      v-model="contextOpen"
      :target="contextAt"
      location="bottom end"
      origin="top start"
      :offset="2"
    >
      <div
        class="ctx-menu mft-menu"
        role="menu"
        tabindex="-1"
        data-testid="model-folder-menu"
        @keydown="onMenuKeydown"
      >
        <template v-for="(entry, index) in menuEntries" :key="index">
          <div v-if="entry === SEPARATOR" class="ctx-sep" role="separator"></div>
          <button
            v-else
            type="button"
            role="menuitem"
            class="ctx-item"
            :class="{ 'ctx-item--danger': entry.danger }"
            :data-verb="entry.key"
            v-bind="blockedAttrs(entry.reason)"
            @click="runEntry(entry)"
          >
            <v-icon v-if="entry.icon" class="ctx-icon" aria-hidden="true">{{
              entry.icon
            }}</v-icon>
            <span v-else class="ctx-icon" aria-hidden="true"></span>
            <span class="ctx-label-text">{{ entry.label }}</span>
            <span v-if="entry.shortcut" class="ctx-shortcut mft-key">{{
              entry.shortcut
            }}</span>
            <Tooltip v-if="entry.reason" :text="entry.reason" activator="parent" />
          </button>
        </template>
      </div>
    </v-menu>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import { DOWNLOADS_KIND, SOURCE_KIND } from "../../api/modelFolders";
import { useContextMenuAt } from "../../composables/useContextMenuAt";
import { useModelCopyDrop } from "../../composables/useModelCopyDrop";
import {
  FOLDER_TYPE,
  parentPath,
  useFolderDrop,
} from "../../composables/useModelFolderMove";
import {
  blockedAttrs,
  useModelFolderGates,
} from "../../composables/useModelFolderGates";
import { errorMessage } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { driveFullness } from "../../utils/libraryChrome";
import {
  comfyuiLoadsTitle,
  comfyuiMarks,
  formatModelSize,
} from "../../utils/modelShelf";
import {
  basename,
  useModelFoldersStore,
} from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import ModelFolderNameField from "./ModelFolderNameField.vue";
import Tooltip from "../widgets/Tooltip.vue";
import TypeMark from "../widgets/TypeMark.vue";

/**
 * Browse or pick a model folder.
 *
 * `browse` (the sidebar) opens what is clicked, a locked folder included: it
 * lists everything under it. Its folder rows also carry the folder verbs
 * (#2206): a menu by right-click, the ⋯ or Shift+F10; F2 renames; Ctrl+Shift+N
 * makes a folder inside; and a subfolder can be dragged onto another folder.
 * `pick` (a destination, for moving models or folders) cannot choose a locked
 * folder, nor any row `disabled` refuses, has no verbs, and reads the same
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
const shelf = useModelShelfStore();
const moves = useModelMovesStore();
const notices = useNoticeStore();
const gates = useModelFolderGates();
const focusedKey = ref(null);
/** Expanded folder keys. Drives start open, so they are tracked closed. */
const expanded = ref(new Set());
const closedDrives = ref(new Set());
/** The row being dragged, for its lifted look. */
const dragKey = ref(null);
/**
 * The inline name field open in the tree, or null: `{mode: 'new'|'rename',
 * key, folderId, path, name, error, busy}`. `key` is the row it sits under.
 */
const editor = ref(null);
/** The row whose menu is open, read by the menu's entries and actions. */
const menuNode = ref(null);
const { contextOpen, contextAt, openContextMenu } = useContextMenuAt();
const drop = useModelCopyDrop();
const folderDrop = useFolderDrop();

const SEPARATOR = "separator";
const REMOVE_REASON = "Removing a folder is not available yet.";

const isLocked = (folder) => folder.movable !== "per_item";
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

/** The registered folder a row belongs to, by its id. */
function registeredOf(folderId) {
  return foldersStore.folders.find((f) => Number(f.id) === Number(folderId));
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
  const refused =
    props.mode === "pick" && (entry.locked || props.disabled?.(entry));
  const marks = comfyuiMarks(
    entry.folderId,
    entry.path,
    foldersStore.comfyuiReads,
  );
  out.push({
    ...entry,
    key,
    expandable,
    expanded: expandable && open,
    disabled: Boolean(refused),
    count: count(n),
    marks,
    aria: [
      entry.name,
      n == null ? "" : `${count(n)} ${Number(n) === 1 ? "model" : "models"}`,
      entry.locked ? "locked" : "",
      ...marks.map(comfyuiLoadsTitle),
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
        name: foldersStore.labelByFolderId.get(Number(folder.id)) ?? "",
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

// ── Folder verbs (#2206) ────────────────────────────────────────────────────

/**
 * The menu a row offers, in the order the design draws it. Empty for a drive,
 * a locked folder (it shows its lock and nothing else), and every row in pick
 * mode. A registered root has no Move, Rename or Remove: it is a place the
 * owner registered, and leaving the registry is Stop watching.
 *
 * A reason is what the gate says right now; an entry carrying one is drawn
 * disabled with that reason, not left out, so the reader can see why.
 */
function menuFor(node) {
  if (props.mode !== "browse") return [];
  if (node.kind === "drive" || node.locked) return [];
  const busy = gates.folderVerbReason({ id: node.folderId });
  if (node.path === "") {
    return [
      {
        key: "new",
        label: "New folder",
        icon: "mdi-folder-plus-outline",
        shortcut: "Ctrl Shift N",
        reason: busy,
      },
      {
        key: "stop",
        label: "Stop watching…",
        icon: "",
        reason: registeredOf(node.folderId)
          ? gates.forgetReason(registeredOf(node.folderId))
          : "",
      },
    ];
  }
  return [
    {
      key: "new",
      label: "New folder",
      icon: "mdi-folder-plus-outline",
      shortcut: "Ctrl Shift N",
      reason: busy,
    },
    {
      key: "move",
      label: "Move folder to…",
      icon: "mdi-folder-move-outline",
      reason: busy,
    },
    { key: "rename", label: "Rename", icon: "", shortcut: "F2", reason: busy },
    SEPARATOR,
    {
      key: "remove",
      label: "Remove folder…",
      icon: "mdi-minus-circle-outline",
      shortcut: "Del",
      danger: true,
      reason: REMOVE_REASON,
    },
  ];
}

const menuEntries = computed(() =>
  menuNode.value ? menuFor(menuNode.value) : [],
);

/** Open the menu for a row at a point, if the row has one. */
function openMenuFor(node, x, y, trigger) {
  if (!menuFor(node).length) return false;
  menuNode.value = node;
  openContextMenu(x, y, trigger);
  return true;
}

function onContextMenu(event, node) {
  if (openMenuFor(node, event.clientX, event.clientY, event.currentTarget)) {
    event.preventDefault();
  }
}

function onMoreClick(event, node) {
  const trigger = event.currentTarget.closest(".mft-row") ?? event.currentTarget;
  const at = trigger.getBoundingClientRect();
  openMenuFor(node, at.right, at.bottom, trigger);
}

/** Run one entry, from the menu or from its key. */
function act(key, node) {
  if (key === "new") startNew(node);
  else if (key === "rename") startRename(node);
  else if (key === "move") shelf.requestFolderMove(sourceOf(node));
  else if (key === "stop") {
    shelf.requestShell("manage-folders", rowEls.get(node.key) ?? null);
  }
}

/** A menu entry that is refused says why, and runs nothing. */
function runEntry(entry) {
  const node = menuNode.value;
  if (!node) return;
  if (entry.reason) {
    notices.push({ level: "info", text: entry.reason });
    return;
  }
  contextOpen.value = false;
  act(entry.key, node);
}

/**
 * A row's verb from the keyboard: F2, Ctrl+Shift+N, Shift+F10 or the Menu key.
 * Returns whether the key was the tree's, so the caller stops it there and the
 * shelf's window listeners never see it.
 */
function verbKey(event, node) {
  const chord = event.ctrlKey || event.metaKey;
  if (
    chord &&
    event.shiftKey &&
    !event.altKey &&
    String(event.key).toLowerCase() === "n"
  ) {
    return verbFor(node, "new");
  }
  if (event.key === "F2" && !chord && !event.shiftKey && !event.altKey) {
    return verbFor(node, "rename");
  }
  if ((event.key === "F10" && event.shiftKey) || event.key === "ContextMenu") {
    const trigger = rowEls.get(node.key);
    if (!trigger) return false;
    const at = trigger.getBoundingClientRect();
    return openMenuFor(node, at.left + at.width / 2, at.bottom, trigger);
  }
  return false;
}

/**
 * Take a verb by its key, for a row that offers it. A row that offers it and is
 * refused says why in a notice; a row that does not offer it lets the key on.
 */
function verbFor(node, key) {
  const entry = menuFor(node).find((one) => one.key === key);
  if (!entry) return false;
  if (entry.reason) {
    notices.push({ level: "info", text: entry.reason });
    return true;
  }
  act(key, node);
  return true;
}

/** The directory a row names, as a move source. */
function sourceOf(node) {
  return {
    folderId: node.folderId,
    path: node.path,
    name: node.name,
  };
}

function canDrag(node) {
  return (
    props.mode === "browse" &&
    node.kind !== "drive" &&
    node.path !== "" &&
    !node.locked &&
    editor.value == null
  );
}

function onDragStart(event, node) {
  const source = sourceOf(node);
  shelf.dragFolder = source;
  dragKey.value = node.key;
  event.dataTransfer.setData(FOLDER_TYPE, JSON.stringify(source));
  event.dataTransfer.effectAllowed = "move";
}

function onDragEnd() {
  dragKey.value = null;
  shelf.dragFolder = null;
  shelf.dropTip = null;
}

// ── The name field ──────────────────────────────────────────────────────────

/** The field under this row, if it is the one being edited. */
function editorFor(node) {
  return editor.value && editor.value.key === node.key ? editor.value : null;
}

function startNew(node) {
  if (node.kind === "drive") return;
  // Opened first, so a folder listed as the new child has somewhere to show.
  if (node.expandable) setExpanded(node, true);
  editor.value = {
    mode: "new",
    key: node.key,
    folderId: node.folderId,
    path: node.path,
    name: "",
    error: "",
    busy: false,
  };
}

function startRename(node) {
  if (node.kind === "drive" || node.path === "") return;
  editor.value = {
    mode: "rename",
    key: node.key,
    folderId: node.folderId,
    path: node.path,
    name: node.name,
    error: "",
    busy: false,
  };
}

/** Put the focus back on the row the field was opened from. */
function focusEditorRow(key) {
  nextTick(() => rowEls.get(key)?.focus());
}

function cancelEditor() {
  const open = editor.value;
  editor.value = null;
  if (open) focusEditorRow(open.key);
}

/**
 * Apply the field. A refusal from the server stays under the field, which stays
 * open with what was typed; a success closes it and lands the focus on the
 * folder it made or renamed.
 */
async function commitEditor() {
  const open = editor.value;
  if (!open || open.busy) return;
  const name = open.name;
  if (open.mode === "rename" && name === basename(open.path)) {
    cancelEditor();
    return;
  }
  open.busy = true;
  open.error = "";
  try {
    if (open.mode === "new") {
      const created = await foldersStore.createDir(
        open.folderId,
        open.path,
        name,
      );
      editor.value = null;
      reveal(open.folderId, open.path, created.path);
    } else {
      await moves.moveFolder(
        { folderId: open.folderId, path: open.path },
        {
          folderId: open.folderId,
          path: parentPath(open.path),
        },
        { newName: name },
      );
      editor.value = null;
      focusEditorRow(open.key);
    }
  } catch (err) {
    open.busy = false;
    open.error = errorMessage(
      err,
      open.mode === "new"
        ? "Could not create that folder."
        : "Could not rename that folder.",
    );
  }
}

// ── Reveal ──────────────────────────────────────────────────────────────────

/**
 * Open the way down to `parentPath` (its parent included, so a folder made
 * inside it is listed), then take the focus to the folder at `focusPath`.
 *
 * The owner of a field that made a folder calls this on the tree that shows
 * it: the Folders heading's field on the sidebar's tree, the Move dialog's on
 * its own. Open state is the tree's, so the tree is the one that opens it.
 */
function reveal(folderId, parent, focusPath) {
  const id = Number(folderId);
  const drive = drives.value.find((d) =>
    d.folders.some((f) => Number(f.id) === id),
  );
  if (drive && closedDrives.value.has(drive.key)) {
    const next = new Set(closedDrives.value);
    next.delete(drive.key);
    closedDrives.value = next;
  }
  const parts = parent ? parent.split("/") : [];
  const next = new Set(expanded.value);
  for (let i = 0; i <= parts.length; i += 1) {
    const path = parts.slice(0, i).join("/");
    next.add(nodeKey(id, path));
    foldersStore.loadDirs(id, path);
  }
  expanded.value = next;
  const key = nodeKey(id, focusPath);
  focusedKey.value = key;
  nextTick(() => rowEls.get(key)?.focus());
}

// ── Drops ───────────────────────────────────────────────────────────────────

/** Where a drop on this row lands: null for a drive, which is no place. */
function dropDestination(node) {
  if (node.kind === "drive") return null;
  return { folderId: node.folderId, path: node.path, locked: node.locked };
}

function dropClassFor(key) {
  return drop.dropClass(key) ?? folderDrop.dropClass(key);
}

function onDragOver(event, node) {
  if (props.mode !== "browse") return;
  drop.onDragOver(event, node.key, dropDestination(node));
  folderDrop.onDragOver(event, node.key, dropDestination(node));
}

function onDragLeave(event, node) {
  drop.onDragLeave(event, node.key);
  folderDrop.onDragLeave(event, node.key);
}

function onDrop(event, node) {
  if (props.mode !== "browse") return;
  drop.onDrop(event, dropDestination(node));
  folderDrop.onDrop(event, dropDestination(node));
}

// ── Keys ────────────────────────────────────────────────────────────────────

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
  // Stopped here: a verb key on a row is the tree's, and must not reach the
  // shelf's window listeners (Delete, Escape) or the browser (Ctrl+Shift+N).
  if (props.mode === "browse" && verbKey(event, node)) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }
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

defineExpose({ reveal });
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

/* The dragged row dims while it is carried, so the drop target reads as the
   thing being moved over. */
.mft-row--lifted {
  opacity: 0.45;
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

.mft-mark {
  display: inline-flex;
  margin-left: var(--space-2);
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

/* The row's ⋯: a 24px control (`--control-h-sm`), shown on hover and when the
   row has the keyboard focus. */
.mft-more {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  margin-left: var(--space-1);
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  cursor: pointer;
  opacity: 0;
}

.mft-row:hover .mft-more,
.mft-row:focus-visible .mft-more,
.mft-more:focus-visible {
  opacity: var(--opacity-text-secondary);
}

.mft-more:hover {
  background: var(--hover-wash);
  opacity: 1;
}

.mft-more:focus-visible {
  box-shadow: var(--focus-ring-inset);
  outline: none;
}

/* The field, lined up with the label it names: past the row's glyph slot. */
.mft-editor {
  padding-top: var(--space-1);
  padding-bottom: var(--space-2);
  padding-right: var(--space-3);
  padding-left: calc(
    var(--row-pad, var(--space-3)) + var(--depth, 0) * var(--indent-step) +
      var(--gutter-glyph) + var(--space-2)
  );
}

.mft-menu {
  /* The owner's canvas width for a folder menu; no token exists for it yet. */
  width: 232px;
}

.mft-key {
  font-family: var(--font-mono);
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
