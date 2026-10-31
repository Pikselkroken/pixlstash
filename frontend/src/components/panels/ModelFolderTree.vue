<template>
  <!-- The Folders tree (#2203): drives, registered folders and their subfolders,
       then their subfolders, one level per expand. Flat rows carrying
       `aria-level`, which is the tree pattern without nested groups: the
       roving focus walks one list, and a row never re-mounts on expand. -->
  <div
    ref="treeEl"
    class="mft"
    role="tree"
    :aria-label="label"
    :aria-multiselectable="mode === 'browse' ? 'true' : undefined"
    data-testid="model-folder-tree"
    @keydown="onKeydown"
  >
    <!-- A destination for the dragged models above the view: an arrow pinned
         to the top edge, in that row's glyph column. It sits in the shelf
         drag's edge-scroll band, so holding the pointer on it scrolls there;
         it takes no pointer, so the row under it still takes the drop. -->
    <div
      v-if="offview.up != null"
      class="mft-edge mft-edge--up"
      role="presentation"
      aria-hidden="true"
    >
      <span
        class="mft-offview"
        data-testid="model-folder-offview"
        :style="{ '--at': `${offview.up}px` }"
        ><v-icon size="14">mdi-arrow-up</v-icon></span
      >
    </div>
    <template v-for="node in nodes" :key="node.key">
      <div
        class="ps-row mft-row model-drop-target"
        :class="[
          {
            'mft-row--drive': node.kind === 'drive',
            'mft-row--two': Boolean(node.origin) || node.typeLine?.length > 0,
            'mft-row--welcome': welcomes.has(node.key),
            'mft-row--verbs': menuFor(node).length > 0,
            active: isSelected(node),
            'mft-row--picked': picked.has(node.key),
            'mft-row--off': node.disabled,
            'mft-row--lifted': dragKey === node.key,
          },
          dropClassFor(node.key),
        ]"
        :style="{ '--depth': node.level - 1 }"
        role="treeitem"
        :aria-level="node.level"
        :aria-expanded="node.expandable ? String(node.expanded) : undefined"
        :aria-selected="
          node.kind === 'drive'
            ? undefined
            : String(isSelected(node) || picked.has(node.key))
        "
        :aria-disabled="node.disabled ? 'true' : undefined"
        :aria-label="node.aria"
        :tabindex="node.key === tabStopKey ? 0 : -1"
        :data-key="node.key"
        :ref="(el) => setRowEl(node.key, el)"
        :data-folder-id="node.folderId"
        :data-path="node.path"
        :draggable="canDrag(node) ? 'true' : undefined"
        :data-model-copies-drop="mode === 'browse' ? '' : undefined"
        @click="onRowClick($event, node)"
        @contextmenu="onContextMenu($event, node)"
        @focus="focusedKey = node.key"
        @dragstart="onDragStart($event, node)"
        @dragend="onDragEnd"
        @dragover="onDragOver($event, node)"
        @dragleave="onDragLeave($event, node)"
        @drop="onDrop($event, node)"
        @model-copies-over="onModelOver($event, node)"
        @model-copies-leave="onModelLeave(node)"
        @model-copies-drop="onModelDrop($event, node)"
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
          <!-- The lock is the glyph, so nothing after the name changes width
               with it and the counts stay one column. -->
          <v-icon
            size="16"
            class="mft-glyph"
            aria-hidden="true"
            :class="{ 'mft-glyph--in': welcomes.has(node.key) }"
            :data-testid="node.locked ? 'model-folder-lock' : undefined"
            >{{
              node.locked
                ? "mdi-folder-lock-outline"
                : welcomes.has(node.key)
                  ? "mdi-folder-download-outline"
                  : node.expanded
                    ? "mdi-folder-open-outline"
                    : "mdi-folder-outline"
            }}</v-icon
          >
          <!-- A registered folder is two lines: its name, and where it is from
               the drive above, so namesakes are told apart by a line every root
               already carries. Subfolders take theirs from the row above. -->
          <span v-if="node.origin" class="mft-stack">
            <Tooltip :text="node.fullPath" activator="parent" />
            <span
              class="mft-label"
              :class="{ 'mft-welcome': nameWelcomes(node) }"
              >{{ node.name }}</span
            >
            <span class="mft-origin" data-testid="model-folder-origin"
              ><span class="mft-origin-head">{{ node.origin.head }}</span
              ><span class="mft-origin-tail">{{ node.origin.tail }}</span></span
            >
          </span>
          <!-- A folder whose name does not say what it holds says it on a
               second line, in the shelf's Type words; while a model of that
               Type is dragged, the words are what turn green. -->
          <span v-else-if="node.typeLine.length" class="mft-stack">
            <span
              class="mft-label"
              :class="{ 'mft-welcome': nameWelcomes(node) }"
              >{{ node.name }}</span
            >
            <span
              class="mft-origin"
              :class="{ 'mft-origin--welcome': welcomes.has(node.key) }"
              data-testid="model-folder-type"
              ><template v-for="(type, index) in node.typeLine" :key="type.key"
                >{{ index ? ", " : ""
                }}<span
                  :class="{
                    'mft-welcome':
                      welcomes.has(node.key) && dragKinds.has(type.key),
                  }"
                  >{{ type.label }}</span
                ></template
              ></span
            >
          </span>
          <span
            v-else
            class="mft-label"
            :class="{ 'mft-welcome': nameWelcomes(node) }"
            >{{ node.name }}</span
          >
          <!-- What ComfyUI loads from this very folder (#2205), in a slot that
               keeps its width when empty. -->
          <span class="mft-marks">
            <span
              v-for="type in node.marks"
              :key="type.key"
              class="mft-mark"
              data-testid="comfyui-mark"
              ><Tooltip
                :text="comfyuiLoadsTitle(type)"
                activator="parent" /><TypeMark
                :mark="type.mark"
                :tint="type.tint"
            /></span>
          </span>
          <span class="mft-count">{{ node.count }}</span>
          <!-- The row's own menu, for a pointer. Hidden until hover, when it
               takes the count's place rather than a width of its own,
               so a locked row's count stays in the same column. Out of the tab
               order: the row is the one stop, and the menu is Shift+F10 or the
               Menu key on it. -->
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
    <div
      v-if="offview.down != null"
      class="mft-edge mft-edge--down"
      role="presentation"
      aria-hidden="true"
    >
      <span
        class="mft-offview"
        data-testid="model-folder-offview"
        :style="{ '--at': `${offview.down}px` }"
        ><v-icon size="14">mdi-arrow-down</v-icon></span
      >
    </div>

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
import { computed, nextTick, onBeforeUnmount, ref, toRef, watch } from "vue";
import { VIcon, VMenu } from "vuetify/components";

import {
  DOWNLOADS_KIND,
  SOURCE_KIND,
  openModelFolder,
} from "../../api/modelFolders";
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
import {
  startFolderRemove,
  startFoldersRemove,
} from "../../composables/useModelFolderRemove";
import { errorMessage } from "../../utils/apiError";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { driveFullness } from "../../utils/libraryChrome";
import {
  allAlreadyIn,
  comfyuiLoadsTitle,
  comfyuiMarks,
  formatModelSize,
  modelDestinations,
  unsaidTypes,
} from "../../utils/modelShelf";
import { isApplePlatform } from "../../utils/shortcutHints";
import { folderOrigin } from "../../utils/folderPath";
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
 * drop asks for a move to that folder. While they are dragged, the folders
 * their Type belongs in welcome them: the glyph gains an arrow in and the Type
 * words turn green. A collapsed folder with one inside is marked the same way,
 * opens when the pointer rests on it, and a drop on it files into that one.
 *
 * Also in `browse`, subfolders can be picked several at a time, Ctrl (Cmd)
 * click to add or drop one, Shift click or Shift+arrows for a run, Ctrl+Space
 * on the focused row, Escape to let go. The menu or Del on a picked row then
 * removes them all, one dialog per folder that holds files. Picking is not
 * opening: the folder open on the shelf stays what it was.
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
/**
 * Expanded folder keys. Drives start open, so they are tracked closed. The
 * sidebar's live in the store and survive a trip to another view; a picker's
 * are its own.
 */
const browsing = props.mode === "browse";
const expanded = browsing
  ? toRef(foldersStore, "treeExpanded")
  : ref(new Set());
const closedDrives = browsing
  ? toRef(foldersStore, "treeClosedDrives")
  : ref(new Set());
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
const PASTE_KEY = `${isApplePlatform() ? "⌘" : "Ctrl"} V`;

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
        mountPoint: device?.mount_point ?? "",
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
  // What opening the row shows: the models directly in it, or everything
  // under a locked folder, which the shelf cannot browse into.
  const n =
    entry.count ??
    (entry.locked
      ? listing?.listing?.model_count
      : listing?.listing?.direct_model_count);
  const refused =
    props.mode === "pick" && (entry.locked || props.disabled?.(entry));
  const marks = comfyuiMarks(
    entry.folderId,
    entry.path,
    foldersStore.comfyuiReads,
  );
  // A registered folder's second line is where it is; a subfolder's says what
  // it holds when its name does not.
  const typeLine = entry.origin
    ? []
    : unsaidTypes(entry.name, kindsAt(entry.folderId, entry.path));
  out.push({
    ...entry,
    key,
    expandable,
    expanded: expandable && open,
    disabled: Boolean(refused),
    count: count(n),
    marks,
    typeLine,
    aria: [
      entry.name,
      entry.origin ? entry.origin.head + entry.origin.tail : "",
      typeLine.map((type) => type.label).join(", "),
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
      count: dir.direct_model_count,
    });
  }
}

/**
 * Where each Type belongs in the folders the tree lists. A locked folder takes
 * no file, so nothing in it is a destination.
 */
const destinations = computed(() =>
  modelDestinations(
    shelfFolders.value.filter((folder) => !isLocked(folder)),
    foldersStore.dirListings,
    foldersStore.comfyuiReads,
  ),
);

/** The Types that belong in exactly this directory. */
function kindsAt(folderId, path) {
  return new Set(
    destinations.value
      .filter((one) => one.folderId === folderId && one.path === path)
      .map((one) => one.kind),
  );
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
      // Named by its tool, else its own folder name, and never renamed when a
      // namesake arrives: the second line is what tells the two apart.
      pushFolderRows(out, {
        kind: "folder",
        folderId: Number(folder.id),
        path: "",
        name: folder.tool || basename(folder.path),
        origin: folderOrigin(folder.path, drive.mountPoint, {
          parent: !folder.tool,
        }),
        fullPath: String(folder.path || ""),
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

/** Every row with a menu offers it. It only looks, so nothing busy refuses it. */
const OPEN_ENTRY = {
  key: "open",
  label: "Open in file manager",
  icon: "mdi-folder-open-outline",
  reason: "",
};

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
  // Models cut on the shelf (Ctrl X or Cut) land here, through the Move
  // dialog. Listed only while something is cut, first: it is why the reader
  // opened this menu.
  const paste = shelf.cutCopies
    ? [
        {
          key: "paste",
          label: "Paste here",
          icon: "mdi-content-paste",
          shortcut: PASTE_KEY,
          reason: allAlreadyIn(shelf.cutCopies.items, node)
            ? "What you cut is already in this folder"
            : "",
        },
        SEPARATOR,
      ]
    : [];
  if (node.path === "") {
    return [
      ...paste,
      {
        key: "new",
        label: "New folder",
        icon: "mdi-folder-plus-outline",
        shortcut: "Ctrl Shift N",
        reason: busy,
      },
      OPEN_ENTRY,
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
    ...paste,
    {
      key: "new",
      label: "New folder",
      icon: "mdi-folder-plus-outline",
      shortcut: "Ctrl Shift N",
      reason: busy,
    },
    OPEN_ENTRY,
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
      reason: busy,
    },
  ];
}

const menuEntries = computed(() => {
  if (!menuNode.value) return [];
  return isManyPicked(menuNode.value)
    ? manyMenu.value
    : menuFor(menuNode.value);
});

/** Open the menu for a row at a point, if the row has one. */
function openMenuFor(node, x, y, trigger) {
  if (!menuFor(node).length) return false;
  // A row outside the picked ones speaks for itself alone, as in a file
  // manager: the pick lets go.
  if (!picked.value.has(node.key)) clearPicks();
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

/**
 * Run one entry, from the menu or from its key. `options.permanent` is Shift on
 * the Delete that asked, for an empty folder, which goes without a dialog.
 */
function act(key, node, options = {}) {
  if (key === "paste") shelf.pasteCut(dropDestination(node));
  else if (key === "new") startNew(node);
  else if (key === "rename") startRename(node);
  else if (key === "move") shelf.requestFolderMove(sourceOf(node));
  else if (key === "open") void openInFileManager(node);
  else if (key === "remove") void startFolderRemove(sourceOf(node), options);
  else if (key === "remove-many") {
    const sources = [...picked.value.values()];
    clearPicks();
    void startFoldersRemove(sources, options);
  }
  else if (key === "stop") {
    shelf.requestShell("manage-folders", rowEls.get(node.key) ?? null);
  }
}

/**
 * Show the row's directory in the server host's file manager. Nothing visible
 * happens here when it works, so each failure says which one it was: 403 is a
 * tree opened from another machine (the route is loopback-only), 400 or 404 a
 * directory that is gone or became a link since the tree was drawn, anything
 * else a server with no desktop to open it on.
 */
async function openInFileManager(node) {
  try {
    await openModelFolder(node.folderId, node.path);
  } catch (err) {
    const status = err?.response?.status;
    notices.push({
      level: "warning",
      text:
        status === 403
          ? "A file manager opens on the machine running PixlStash, so this only works when you are sitting at it."
          : status === 400 || status === 404
            ? "That folder is not where the tree last saw it."
            : "Couldn't open that folder - the machine running PixlStash has no desktop file manager.",
      key: "model-folder-open",
    });
    console.warn(
      `Failed to open model folder ${node.folderId} at ${JSON.stringify(node.path)}`,
      err,
    );
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
  // A paste may open the Move dialog, which returns focus to what held it:
  // the row, not the menu row about to go.
  if (entry.key === "paste") rowEls.get(node.key)?.focus();
  act(entry.key, node);
}

/**
 * A row's verb from the keyboard: F2, Ctrl+Shift+N, Shift+F10 or the Menu key.
 * Returns whether the key was the tree's, so the caller stops it there and the
 * shelf's window listeners never see it.
 */
function verbKey(event, node) {
  const chord = event.ctrlKey || event.metaKey;
  // Ctrl V pastes what the shelf cut into this folder. A locked folder offers
  // no menu, so it is asked directly and says why it refuses.
  if (
    chord &&
    !event.shiftKey &&
    !event.altKey &&
    String(event.key).toLowerCase() === "v" &&
    shelf.cutCopies &&
    node.kind !== "drive"
  ) {
    if (verbFor(node, "paste")) return true;
    shelf.pasteCut(dropDestination(node));
    return true;
  }
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
  // Delete, with or without Shift: a folder with files opens the dialog, whose
  // own button reads Shift; an empty one goes at once, permanently on Shift.
  // Never the shelf's file delete.
  if (event.key === "Delete" && !chord && !event.altKey) {
    return verbFor(node, isManyPicked(node) ? "remove-many" : "remove", {
      permanent: event.shiftKey,
    });
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
function verbFor(node, key, options = {}) {
  const entries = isManyPicked(node) ? manyMenu.value : menuFor(node);
  const entry = entries.find((one) => one.key === key);
  if (!entry) return false;
  if (entry.reason) {
    notices.push({ level: "info", text: entry.reason });
    return true;
  }
  act(key, node, options);
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

// ── Picking several (multi-select) ──────────────────────────────────────────

/**
 * Subfolders picked together for one verb, by row key, each with its move
 * source. Only rows that offer Remove can be picked: a registered root, a
 * locked folder and a drive have nothing a pick could do.
 */
const picked = ref(new Map());
/** The row a Shift run reaches from: the last one picked alone. */
const pickAnchor = ref(null);

function canPick(node) {
  return menuFor(node).some((entry) => entry.key === "remove");
}

function clearPicks() {
  if (picked.value.size) picked.value = new Map();
  pickAnchor.value = null;
}

/** True when this row is one of two or more picked, so verbs take them all. */
function isManyPicked(node) {
  return picked.value.size > 1 && picked.value.has(node.key);
}

function togglePick(node) {
  if (!canPick(node)) return;
  const next = new Map(picked.value);
  if (next.has(node.key)) next.delete(node.key);
  else next.set(node.key, sourceOf(node));
  picked.value = next;
  pickAnchor.value = node.key;
}

/**
 * Pick every pickable row from the anchor to this one, in reading order. With
 * no anchor yet the open folder is where the run starts, if it can be picked.
 */
function pickRun(node, { add = false } = {}) {
  const list = nodes.value;
  const open = list.find((one) => isSelected(one) && canPick(one));
  const from = pickAnchor.value ?? open?.key ?? node.key;
  const a = list.findIndex((one) => one.key === from);
  const b = list.findIndex((one) => one.key === node.key);
  if (a < 0 || b < 0) {
    togglePick(node);
    return;
  }
  const next = add ? new Map(picked.value) : new Map();
  for (const one of list.slice(Math.min(a, b), Math.max(a, b) + 1)) {
    if (canPick(one)) next.set(one.key, sourceOf(one));
  }
  picked.value = next;
  pickAnchor.value = from;
}

/** A plain click opens and lets the pick go; Ctrl and Shift pick instead. */
function onRowClick(event, node) {
  const chord = event.ctrlKey || event.metaKey;
  if (props.mode === "browse" && (chord || event.shiftKey)) {
    if (event.shiftKey) pickRun(node, { add: chord });
    else togglePick(node);
    return;
  }
  clearPicks();
  activate(node);
}

/** The menu for two or more picked folders: the one verb they share. */
const manyMenu = computed(() => {
  const n = picked.value.size;
  const busy =
    [...picked.value.values()]
      .map((source) => gates.folderVerbReason({ id: source.folderId }))
      .find(Boolean) ?? "";
  return [
    {
      key: "remove-many",
      label: `Remove ${n} folders…`,
      icon: "mdi-minus-circle-outline",
      shortcut: "Del",
      danger: true,
      reason: busy,
    },
  ];
});

// A picked row that is no longer drawn (its parent closed, or it went) leaves
// the pick, so a verb never reaches a folder the reader cannot see.
watch(nodes, (list) => {
  if (!picked.value.size) return;
  const shown = new Set(list.filter(canPick).map((one) => one.key));
  const next = new Map(
    [...picked.value].filter(([key]) => shown.has(key)),
  );
  if (next.size !== picked.value.size) picked.value = next;
  if (pickAnchor.value && !shown.has(pickAnchor.value)) pickAnchor.value = null;
});

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

/**
 * A rename accepted by the server, until its job lands: `{folderId, oldPath,
 * newPath}`. Row keys are paths, so the renamed row is a new row; the open
 * state and the focus follow it here rather than vanishing with the old key.
 */
const pendingRename = ref(null);
/** A row to focus as soon as it is drawn. */
const pendingFocusKey = ref(null);

function followRename(job) {
  const rename = pendingRename.value;
  if (!rename || job?.kind !== "folder" || job.status !== "finished") return;
  pendingRename.value = null;
  const old = nodeKey(rename.folderId, rename.oldPath);
  // A failure is reported by the move store, which holds its receipt in the
  // corner card; here only the focus goes back.
  if (job.results?.[0]?.status !== "moved") {
    if (rename.focus) focusEditorRow(old);
    return;
  }
  const fresh = nodeKey(rename.folderId, rename.newPath);
  const next = new Set();
  for (const key of expanded.value) {
    if (key !== old && !key.startsWith(`${old}/`)) {
      next.add(key);
      continue;
    }
    const moved = fresh + key.slice(old.length);
    next.add(moved);
    foldersStore.loadDirs(rename.folderId, moved.slice(`f:${rename.folderId}:`.length));
  }
  expanded.value = next;
  if (!rename.focus) return;
  focusedKey.value = fresh;
  focusWhenDrawn(fresh);
}

/**
 * Focus a row now if it is drawn, else as soon as it is. A row under a folder
 * whose listing is still loading only exists once that fetch lands.
 */
function focusWhenDrawn(key) {
  pendingFocusKey.value = key;
  nextTick(() => {
    if (pendingFocusKey.value !== key || !rowEls.has(key)) return;
    pendingFocusKey.value = null;
    rowEls.get(key).focus();
  });
}

watch(() => moves.job, followRename);

watch(
  nodes,
  () => {
    // A field whose row went (its parent collapsed) is not cancellable and
    // would keep every row from dragging, so it goes with its row.
    if (editor.value && !nodes.value.some((n) => n.key === editor.value.key)) {
      editor.value = null;
    }
    const key = pendingFocusKey.value;
    if (!key || !rowEls.has(key)) return;
    pendingFocusKey.value = null;
    rowEls.get(key).focus();
  },
  { flush: "post" },
);

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
      // The reader may have pressed Esc and opened another field while the
      // request ran: that field is theirs now, and so is the focus.
      if (editor.value !== open) return;
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
      // The rename happened either way, so its open state follows it; only
      // the field and the focus are left alone if the reader has moved on.
      const ours = editor.value === open;
      if (ours) editor.value = null;
      const parent = parentPath(open.path);
      pendingRename.value = {
        folderId: open.folderId,
        oldPath: open.path,
        newPath: parent ? `${parent}/${name}` : name,
        focus: ours,
      };
      followRename(moves.job);
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
  focusWhenDrawn(key);
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

/**
 * How many Types the drag holds, a file no Type row takes counted as one more:
 * a LoRA dragged with it has no single destination to route to.
 */
const dragTypeCount = computed(
  () => new Set(shelf.dragCopies?.kinds?.values() ?? []).size,
);

/** The Types of the models being dragged off the shelf. */
const dragKinds = computed(
  () =>
    new Set(
      [...(shelf.dragCopies?.kinds?.values() ?? [])].filter(Boolean),
    ),
);

/**
 * The rows that welcome the models being dragged, by key: `{route}`, where
 * `route` is the destination a drop on a collapsed parent files into, and null
 * on a destination itself. A folder every copy is already in is no welcome.
 *
 * A parent is marked only while it is collapsed, and only for a drag of one
 * Type, so its mark and where a drop on it lands never disagree: open, it is
 * an ordinary folder and the drop lands in it. Of several destinations inside
 * it, the one holding the most models takes the drop.
 */
const welcomes = computed(() => {
  const out = new Map();
  if (props.mode !== "browse" || !dragKinds.value.size) return out;
  const items = shelf.dragCopies.items ?? [];
  const fits = destinations.value.filter(
    (one) => dragKinds.value.has(one.kind) && !allAlreadyIn(items, one),
  );
  for (const node of nodes.value) {
    if (node.kind === "drive") continue;
    const mine = fits.filter((one) => one.folderId === node.folderId);
    if (mine.some((one) => one.path === node.path)) {
      out.set(node.key, { route: null });
      continue;
    }
    if (dragTypeCount.value > 1 || node.expanded) continue;
    const inside = mine.filter((one) =>
      node.path === ""
        ? one.path !== ""
        : one.path.startsWith(`${node.path}/`),
    );
    if (!inside.length) continue;
    out.set(node.key, {
      route: inside.reduce((best, one) => (one.count > best.count ? one : best)),
    });
  }
  return out;
});

/** The name turns green when no Type word on the row is the one dragged. */
function nameWelcomes(node) {
  return (
    welcomes.value.has(node.key) &&
    !node.typeLine?.some((type) => dragKinds.value.has(type.key))
  );
}

/** Where a model drop on this row lands: a marked parent routes it inside. */
function modelDropDestination(node) {
  const route = welcomes.value.get(node.key)?.route;
  if (!route) return dropDestination(node);
  return { folderId: route.folderId, path: route.path, locked: false };
}

function onDragOver(event, node) {
  if (props.mode !== "browse") return;
  folderDrop.onDragOver(event, node.key, dropDestination(node));
}

function onDragLeave(event, node) {
  folderDrop.onDragLeave(event, node.key);
}

function onDrop(event, node) {
  if (props.mode !== "browse") return;
  folderDrop.onDrop(event, dropDestination(node));
}

// Model rows dragged off the shelf arrive as the shelf's pointer-drag events
// (`model-copies-over`, `-leave`, `-drop`), not native drag events.

function onModelOver(event, node) {
  drop.onDragOver(event, node.key, modelDropDestination(node));
  dwellOn(node);
}

function onModelLeave(node) {
  drop.onDragLeave(node.key);
  if (dwell?.key === node.key) stopDwell();
}

function onModelDrop(event, node) {
  stopDwell();
  const destination = modelDropDestination(node);
  if (shelf.dragCopies) landedAt = destination;
  drop.onDrop(event, destination);
}

// ── Opening a marked parent on the way to a destination ─────────────────────

/** How long the pointer rests on a marked, collapsed parent to open it. */
const DWELL_MS = 600;
/** `{key, timer}` of the parent the pointer rests on, or null. */
let dwell = null;
/** Rows a dwell opened during this drag, closed again when it ends. */
const dragOpened = new Set();
/** Where this drag's models were dropped, whose way down stays open. */
let landedAt = null;

function stopDwell() {
  if (dwell) clearTimeout(dwell.timer);
  dwell = null;
}

function dwellOn(node) {
  if (!welcomes.value.get(node.key)?.route) {
    stopDwell();
    return;
  }
  if (dwell?.key === node.key) return;
  stopDwell();
  dwell = {
    key: node.key,
    timer: setTimeout(() => {
      dwell = null;
      const now = nodes.value.find((one) => one.key === node.key);
      // The drag or the tree may have changed under the still pointer: open
      // only a row that is still a marked parent, as its mark promised.
      if (!now || now.expanded || !welcomes.value.get(now.key)?.route) return;
      setExpanded(now, true);
      dragOpened.add(now.key);
    }, DWELL_MS),
  };
}

/** Whether a row is the landed folder or one on the way down to it. */
function leadsTo(key, landed) {
  const prefix = `f:${Number(landed.folderId)}:`;
  if (!key.startsWith(prefix)) return false;
  const path = key.slice(prefix.length);
  return (
    path === "" || landed.path === path || landed.path.startsWith(`${path}/`)
  );
}

// The drag is over when the shelf lets go of its copies: on a drop, on Esc and
// on a release anywhere else. What a dwell opened folds back, except the way
// to where the models went.
watch(
  () => shelf.dragCopies,
  (now, before) => {
    if (now || !before) return;
    stopDwell();
    const landed = landedAt;
    landedAt = null;
    if (!dragOpened.size) return;
    const next = new Set(expanded.value);
    for (const key of dragOpened) {
      if (!landed || !leadsTo(key, landed)) next.delete(key);
    }
    dragOpened.clear();
    expanded.value = next;
  },
);

// ── Destinations out of view ────────────────────────────────────────────────

const treeEl = ref(null);
/** The glyph column each arrow centres on, per edge, or null for no arrow. */
const offview = ref({ up: null, down: null });

/** The nearest ancestor that scrolls the tree, or null. */
function scrollerOf(el) {
  for (let at = el?.parentElement; at; at = at.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(at).overflowY)) return at;
  }
  return null;
}

/**
 * Which edges have a welcoming row beyond them, each arrow lined up with the
 * nearest such row's glyph.
 */
function measureOffview() {
  const scroller = treeEl.value && scrollerOf(treeEl.value);
  if (!welcomes.value.size || !scroller) {
    if (offview.value.up != null || offview.value.down != null) {
      offview.value = { up: null, down: null };
    }
    return;
  }
  const view = scroller.getBoundingClientRect();
  const tree = treeEl.value.getBoundingClientRect();
  let up = null;
  let down = null;
  for (const key of welcomes.value.keys()) {
    const row = rowEls.get(key);
    if (!row) continue;
    const at = row.getBoundingClientRect();
    const glyph = row.querySelector(".mft-glyph")?.getBoundingClientRect() ?? at;
    const left = glyph.left - tree.left + glyph.width / 2;
    if (at.bottom <= view.top && (!up || at.bottom > up.edge)) {
      up = { left, edge: at.bottom };
    } else if (at.top >= view.bottom && (!down || at.top < down.edge)) {
      down = { left, edge: at.top };
    }
  }
  offview.value = { up: up?.left ?? null, down: down?.left ?? null };
}

watch(welcomes, measureOffview, { flush: "post" });

// Scrolling does not bubble; captured on the document, only while a drag of
// models is on.
watch(
  () => props.mode === "browse" && dragKinds.value.size > 0,
  (dragging) => {
    if (dragging) document.addEventListener("scroll", measureOffview, true);
    else document.removeEventListener("scroll", measureOffview, true);
  },
  // A tree drawn while a drag is already on arms the listener at once.
  { immediate: true },
);

onBeforeUnmount(() => {
  stopDwell();
  document.removeEventListener("scroll", measureOffview, true);
});

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
  // shelf's window listeners (Delete, Escape). Chrome keeps Ctrl+Shift+N for
  // itself whatever the page does.
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
  if (props.mode === "browse" && pickKey(event, node, list, at)) {
    event.preventDefault();
    event.stopPropagation();
    return;
  }
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
 * The picking keys: Shift+Up/Down extends a run, Ctrl+Space picks or drops the
 * focused row, Escape lets the pick go (and is only the tree's while there is
 * one). Returns whether the key was taken.
 */
function pickKey(event, node, list, at) {
  const chord = event.ctrlKey || event.metaKey;
  if (
    event.shiftKey &&
    !chord &&
    !event.altKey &&
    (event.key === "ArrowDown" || event.key === "ArrowUp")
  ) {
    const next = list[event.key === "ArrowDown" ? at + 1 : at - 1];
    if (!next) return true;
    if (!pickAnchor.value && canPick(node)) pickAnchor.value = node.key;
    focusNode(next);
    if (pickAnchor.value) pickRun(next);
    return true;
  }
  if (chord && !event.shiftKey && !event.altKey && event.key === " ") {
    togglePick(node);
    return true;
  }
  if (event.key === "Escape" && picked.value.size) {
    clearPicks();
    return true;
  }
  return false;
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
  position: relative;
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

/* A picked row wears the shelf's selected-row look: the wash and the inset
   rail. The open folder keeps its own, so the two never read as one. */
.mft-row--picked {
  background: var(--active-wash);
  box-shadow: var(--selection-edge);
}

.mft-row--picked:focus-visible {
  box-shadow: var(--selection-edge), var(--focus-ring-inset);
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

/* A folder the dragged models belong in: the glyph (an arrow into the folder)
   and its Type words turn green, and nothing else changes. Green says
   "belongs here"; the blue drop ring still says "drops here now". */
.mft-glyph--in {
  color: rgb(var(--v-theme-surface-success));
  opacity: 1;
}

.mft-welcome {
  color: rgb(var(--v-theme-surface-success));
  font-weight: var(--weight-semibold);
}

.mft-origin--welcome {
  opacity: 1;
}

/* The off-view arrow's edge: no height of its own, pinned to the top or the
   bottom of the scrolling sidebar while the tree runs past it. */
.mft-edge {
  position: sticky;
  z-index: var(--z-sticky);
  height: 0;
}

.mft-edge--up {
  top: 0;
}

.mft-edge--down {
  bottom: 0;
}

.mft-offview {
  position: absolute;
  left: calc(var(--at) - (var(--gutter-glyph) + var(--space-2)) / 2);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: calc(var(--gutter-glyph) + var(--space-2));
  height: calc(var(--gutter-glyph) + var(--space-2));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-sidebar));
  color: rgb(var(--v-theme-surface-success));
  pointer-events: none;
}

.mft-edge--up .mft-offview {
  top: var(--space-2);
}

.mft-edge--down .mft-offview {
  bottom: var(--space-2);
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

/* A registered folder's two lines: 40px, its name over where it is. */
.mft-row--two {
  min-height: calc(var(--control-h-bar) + var(--space-3));
  padding-top: var(--space-1);
  padding-bottom: var(--space-1);
}

.mft-stack {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: var(--leading-tight);
}

.mft-stack .mft-label {
  flex: none;
}

/* The path gives way in the middle: the head shrinks to an ellipsis while the
   last segment, the part that differs, keeps its width. */
.mft-origin {
  display: flex;
  min-width: 0;
  font-size: var(--text-2xs);
  font-weight: var(--weight-regular);
  white-space: nowrap;
  opacity: var(--opacity-text-secondary);
}

/* Shrinks first, but never below the width of its ellipsis. */
.mft-origin-head {
  flex: 0 1 auto;
  min-width: 2ch;
  overflow: hidden;
  text-overflow: ellipsis;
}

.mft-origin-head:empty {
  min-width: 0;
}

.mft-origin-tail {
  flex: 0 0 auto;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The ComfyUI type marks, in a slot one mark wide whether or not it holds one. */
.mft-marks {
  flex-shrink: 0;
  display: inline-flex;
  gap: var(--space-1);
  min-width: var(--gutter-glyph);
  margin-left: var(--space-2);
}

.mft-mark {
  display: inline-flex;
}

/* One right-aligned column of counts, muted at rest and full strength on the
   hovered or open row. */
.mft-count {
  min-width: calc(var(--space-6) + var(--space-3));
  text-align: right;
}

.mft-row:hover .mft-count,
.mft-row.active .mft-count {
  opacity: 1;
}

.mft-row--verbs:hover .mft-count {
  visibility: hidden;
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

/* The row's ⋯: a 24px control (`--control-h-sm`) for a pointer, shown on hover
   over the count column at the row's right edge. Not on keyboard focus: there
   it would only hide the count, and the menu is Shift+F10. */
.mft-more {
  position: absolute;
  top: 50%;
  right: var(--sidebar-right-edge, var(--space-3));
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  cursor: pointer;
  opacity: 0;
}

.mft-row:hover .mft-more,
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
