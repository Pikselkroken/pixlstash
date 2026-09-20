<template>
  <!-- The floating pill, the SAME object the photo grid docks over its tiles:
       bottom-centre, panel surface, pill radius, elevation-4. Nine labelled
       buttons in a docked bar was a sentence to re-read on every selection;
       a row of icons is a chord you learn once, and the words are never gone -
       hover has them and right-click has all of them (#904). The last of them
       is the only one that destroys bytes, which is why it is the only one in
       the error colour (#933). -->
  <div
    v-if="store.selectedRows.length"
    class="selbar"
    role="toolbar"
    aria-label="Selected models"
  >
    <!-- The count is a control, not a caption: it is where "I meant all of
         them" and "never mind" live, which is why it carries a chevron. -->
    <v-menu
      v-model="countMenuOpen"
      location="top"
      origin="bottom center"
      :offset="8"
    >
      <template #activator="{ props: menuProps }">
        <button
          v-bind="menuProps"
          class="selbar-count"
          type="button"
          aria-haspopup="menu"
          :aria-expanded="countMenuOpen"
        >
          <Tooltip :text="countTitle" activator="parent" />
          <v-icon size="16">mdi-cube-outline</v-icon>
          <span>{{ store.selectedRows.length.toLocaleString() }}</span>
          <span v-if="selectedSize" class="selbar-size"
            >· {{ selectedSize }}</span
          >
          <v-icon size="16" class="selbar-chevron">mdi-menu-down</v-icon>
        </button>
      </template>
      <div
        class="ctx-menu shelf-menu"
        role="menu"
        tabindex="-1"
        @keydown="onMenuKeydown"
      >
        <button
          class="ctx-item"
          type="button"
          role="menuitem"
          @click="store.selectVisible()"
        >
          <v-icon class="ctx-icon">mdi-select-all</v-icon>
          <span class="ctx-label-text">Select all shown</span>
          <span class="ctx-shortcut">{{ selectAllHint }}</span>
        </button>
        <button class="ctx-item" type="button" role="menuitem" @click="clear">
          <v-icon class="ctx-icon">mdi-close</v-icon>
          <span class="ctx-label-text">Clear selection</span>
          <span class="ctx-shortcut">Esc</span>
        </button>
      </div>
    </v-menu>

    <span class="selbar-sep"></span>

    <!-- Assign is one button and one popover, because "person or set" is one
         question: which thing does this adapter belong to. The two pickers
         inside it are the shared `AddToEntityControl` the grid uses, so the
         search, the tri-state and the keyboard model are learned once.

         Every verb below carries BOTH `aria-label` and a tooltip, and they say
         different things on purpose. The label is the verb and never changes,
         so a screen reader hears a stable name and voice control has something
         to say; the tooltip is the REFUSAL - "Only files that are actually on
         this machine can be moved" - and changes with the selection. A tooltip
         alone is not an accessible name a reader can rely on. -->
    <v-menu
      v-model="assignMenuOpen"
      :close-on-content-click="false"
      location="top"
      origin="bottom center"
      :offset="8"
    >
      <template #activator="{ props: menuProps }">
        <AppBarButton
          shape="round"
          icon="account-plus-outline"
          v-bind="menuProps"
          data-verb="assign"
          aria-label="Assign to person or set"
          :disabled="!assignable.length"
          :tooltip="assignTitle || 'Assign to person or set'"
        />
      </template>
      <div class="ctx-menu shelf-menu shelf-menu--assign">
        <Tooltip :text="assignTitle || ''">
          <template #activator="{ props: tipProps }">
            <AddToEntityControl
              v-bind="tipProps"
              type="character"
              label="Assign to person"
              float-menu
              :subject-ids="assignableIds"
              :membership="membership.character"
              :disabled="!assignable.length"
              @attach="onAttach($event, true)"
              @detach="onAttach($event, false)"
            />
          </template>
        </Tooltip>
        <Tooltip :text="assignTitle || ''">
          <template #activator="{ props: tipProps }">
            <AddToEntityControl
              v-bind="tipProps"
              type="set"
              label="Assign to set"
              float-menu
              :subject-ids="assignableIds"
              :membership="membership.set"
              :disabled="!assignable.length"
              @attach="onAttach($event, true)"
              @detach="onAttach($event, false)"
            />
          </template>
        </Tooltip>
      </div>
    </v-menu>

    <AppBarButton
      shape="round"
      icon="layers-outline"
      data-verb="stack"
      :aria-label="
        stackFuses ? 'Fuse these into one stack' : 'Stack these into one stack'
      "
      :disabled="!stackable"
      :tooltip="stackTitle"
      @click="emit('stack')"
    />

    <AppBarButton
      shape="round"
      icon="layers-off-outline"
      data-verb="unstack"
      aria-label="Break this stack up"
      :disabled="!unstackable"
      :tooltip="unstackTitle"
      @click="emit('unstack')"
    />

    <AppBarButton
      shape="round"
      icon="folder-move-outline"
      data-verb="move"
      aria-label="Move to another folder"
      :disabled="!movable.length || moves.busy"
      :tooltip="moveTitle"
      @click="emit('move')"
    />

    <!-- Rename rides along DISABLED rather than disappearing: a row of buttons
         that reflows as the selection grows is a row you have to re-read, and a
         disabled button with its reason in the tooltip teaches where the verb
         lives. -->
    <AppBarButton
      shape="round"
      icon="pencil-outline"
      data-verb="rename"
      aria-label="Rename"
      :disabled="!single"
      :tooltip="renameTitle"
      @click="emit('rename')"
    />

    <AppBarButton
      shape="round"
      icon="image-outline"
      data-verb="set-icon"
      aria-label="Set thumbnail"
      :tooltip="iconTitle"
      @click="emit('set-icon')"
    />

    <span class="selbar-sep"></span>

    <AppBarButton
      shape="round"
      icon="playlist-remove"
      data-verb="forget"
      aria-label="Remove from shelf"
      :disabled="!forgettable.length"
      :tooltip="forgetTitle"
      @click="emit('forget')"
    />

    <!-- The one verb that destroys bytes, so it is the one verb drawn in the
         error colour. `aria-label` follows the modifier rather than staying
         fixed like the others': a screen-reader user holding Shift is being
         offered a different, unrecoverable operation, and a stable name would
         be the one place that did not say so. -->
    <AppBarButton
      shape="round"
      danger
      :icon="shiftHeld ? 'delete-forever-outline' : 'delete-outline'"
      data-verb="delete"
      :aria-label="deleteLabel"
      :disabled="!deletable.length"
      :tooltip="deleteTitle"
      @click="emit('delete', $event.shiftKey)"
    />

    <v-menu
      v-model="moreMenuOpen"
      :close-on-content-click="false"
      location="top end"
      origin="bottom end"
      :offset="8"
    >
      <template #activator="{ props: menuProps }">
        <AppBarButton
          shape="round"
          icon="dots-horizontal"
          v-bind="menuProps"
          data-verb="more"
          aria-label="More actions"
          aria-haspopup="menu"
          :aria-expanded="moreMenuOpen"
          tooltip="More…"
        />
      </template>
      <VerbMenu :single="false" v-bind="verbHandlers" />
    </v-menu>
  </div>

  <!-- The row context menu: the FULL inventory, and the thing every other
       surface is a shortcut into. Anchored to the pointer rather than to the
       row, which is what a context menu is; the view opens it through
       `openContextMenu` after it has made the row the selection. -->
  <v-menu
    v-model="contextOpen"
    :target="contextAt"
    :close-on-content-click="false"
    location="bottom end"
    origin="top start"
    :offset="2"
  >
    <VerbMenu :single="single" v-bind="verbHandlers" />
  </v-menu>
</template>

<script setup>
// The verb layer's control surface (shelf plan F3, redrawn to the resolved
// design in #904).
//
// It carries no verb logic of its own: every button emits and `ModelShelf.vue`
// runs the confirmation and the call. That keeps the two confirmations in one
// place instead of half here and half there, and it is what lets this component
// be mounted in a test with nothing but a store.
//
// Assign is the exception, and only because it is not a button: it is the
// shared `AddToEntityControl`, which owns its own menu and emits the entity it
// was pointed at. Handing that emit up unchanged and back down again would buy
// nothing, so this one calls the store directly.
//
// **Three surfaces, one set of gates.** The pill, its `⋯` menu and the row
// context menu all offer the same verbs under the same refusals, so they are
// one component rather than three that have to be kept in step. That is the
// whole reason the context menu lives here and not in the view: every tooltip
// below is a refusal sentence, and a second copy of them would drift.

import { computed, h, onMounted, onUnmounted, ref } from "vue";

import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import AppBarButton from "../widgets/AppBarButton.vue";
import Tooltip from "../widgets/Tooltip.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { formatKeyHint, selectAllKeyHint } from "../../utils/shortcutHints";
import {
  deletableModels,
  formatModelSize,
  movableCopies,
  trashName,
} from "../../utils/modelShelf";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";

const emit = defineEmits([
  "rename",
  "set-base-model",
  "set-kind",
  "stack",
  "unstack",
  "make-cover",
  "remove-from-stack",
  "set-icon",
  "clear-icons",
  "move",
  "merge-copies",
  "open-location",
  "forget",
  "delete",
]);

const store = useModelShelfStore();
const folders = useModelFoldersStore();
const moves = useModelMovesStore();

// The keycap beside "Select all shown", so the chord is taught where the button
// is - the same job the `Esc` cap next to Clear does. Read once at setup: the
// platform cannot change under a mounted component.
const selectAllHint = formatKeyHint(selectAllKeyHint());

const countMenuOpen = ref(false);
const assignMenuOpen = ref(false);
const moreMenuOpen = ref(false);
const contextOpen = ref(false);
/** `[x, y]` in client coordinates - what v-menu's `target` takes. */
const contextAt = ref([0, 0]);

/**
 * Open the context menu at a pointer.
 *
 * Called by the view, which has already made the right-clicked row the
 * selection - the file-manager rule: right-clicking a row that is not selected
 * selects it, right-clicking one that is leaves the selection alone.
 *
 * @param {number} x - clientX.
 * @param {number} y - clientY.
 */
function openContextMenu(x, y) {
  contextAt.value = [x, y];
  contextOpen.value = true;
}

const single = computed(() => store.selectedRows.length === 1);

/**
 * What the selection weighs, stack members included.
 *
 * Summed off `members` rather than the payload's `total_size` for the reason
 * {@link collapseStacks} counts what is shown: a filter can hide part of a run,
 * and a figure covering rows the reader cannot reach would not describe the
 * selection they made. A row that stands alone carries no `members`.
 */
const selectedBytes = computed(() =>
  store.selectedRows.reduce(
    (total, row) =>
      total +
      (row.members
        ? row.members.reduce((sum, m) => sum + (Number(m.file_size) || 0), 0)
        : Number(row.file_size) || 0),
    0,
  ),
);

/**
 * What the selection weighs, or `""` when nothing in it has a recorded size.
 *
 * Dropped rather than shown as `0 B`, because a shelf that has not been hashed
 * yet would otherwise claim the selection is empty. The size is what makes a
 * bulk verb reviewable before it runs: "these 40" says nothing about what is
 * being reclaimed and "12.4 GB" does.
 */
const selectedSize = computed(() =>
  selectedBytes.value ? formatModelSize(selectedBytes.value) : "",
);

const countTitle = computed(() => {
  const n = store.selectedRows.length;
  const head = `${n.toLocaleString()} ${n === 1 ? "model" : "models"} selected`;
  return selectedSize.value ? `${head} · ${selectedSize.value}` : head;
});

/**
 * The selected models that have already lost every copy.
 *
 * `missing` is a fact (the folder was readable and the file was not in it);
 * `present` and `unreachable` both mean the bytes may still be out there, and
 * the second is the dangerous one - an unplugged drive must never be read as a
 * deletion. The server enforces exactly this; the bar only stops the owner
 * pressing a button that would come back refused.
 */
const forgettable = computed(() =>
  store.selectedRows.filter(
    (row) => row.locState === "missing" || row.locState === "forgotten",
  ),
);

const renameTitle = computed(() =>
  single.value ? "Rename this model" : "Select one model to rename it",
);

// Counted off `selectedModelIds` rather than the rows, the way the write is: a
// ticked run is one row and twelve models.
const iconTitle = computed(() =>
  store.selectedModelIds.length === 1
    ? "Give this model a picture"
    : `Give the same picture to all ${store.selectedModelIds.length} models`,
);

const withIcons = computed(() =>
  store.selectedRows.filter((row) => row.icon_sha256),
);

const clearIconTitle = computed(() =>
  withIcons.value.length === 1
    ? "Go back to the generated mark"
    : `Clear the ${withIcons.value.length} pictures in this selection`,
);

/**
 * Is Shift down right now?
 *
 * Cosmetic ONLY: it decides which word the delete verb shows, never what the
 * verb does. What it does comes off the triggering event's own `shiftKey`, so a
 * key state this ref missed - the window lost focus mid-press, the menu was
 * opened from a keyboard shortcut - can make the label a moment stale and can
 * never make a trash into an unlink. The confirmation names the operation
 * either way, and that is the gate.
 *
 * On the window because the label has to change while the pointer sits over the
 * button, which is not an element the key is delivered to.
 */
const shiftHeld = ref(false);

function trackShift(event) {
  shiftHeld.value = Boolean(event.shiftKey);
}

// A blur with Shift down never fires the keyup, which would otherwise leave the
// pill offering `Permanently delete` for the rest of the session.
function dropShift() {
  shiftHeld.value = false;
}

onMounted(() => {
  window.addEventListener("keydown", trackShift);
  window.addEventListener("keyup", trackShift);
  window.addEventListener("blur", dropShift);
});
onUnmounted(() => {
  window.removeEventListener("keydown", trackShift);
  window.removeEventListener("keyup", trackShift);
  window.removeEventListener("blur", dropShift);
});

/** `model_folder.id` to the folder row, for the delete and move folder rules. */
const foldersById = computed(
  () => new Map(folders.folders.map((folder) => [Number(folder.id), folder])),
);

/**
 * The selected models a delete would actually act on.
 *
 * The same gates the route enforces, so the button is never offered where it
 * could only come back refused. Unlike Forget, this one acts on files that are
 * THERE - which is why the tooltip says where they are going.
 */
const deletable = computed(() =>
  deletableModels(store.selectedRows, foldersById.value),
);

/** `Move to Trash` / `Move to Recycle Bin`, or `Permanently delete` on Shift. */
const deleteLabel = computed(() =>
  shiftHeld.value ? "Permanently delete" : `Move to ${trashName()}`,
);

const deleteTitle = computed(() => {
  if (!deletable.value.length) {
    return (
      "Only models in your own folders can be deleted. PixlStash's own " +
      "engines, and anything on a drive that is not plugged in, are left alone"
    );
  }
  const n = deletable.value.length;
  const subject = n === store.selectedRows.length ? "these" : `${n} of these`;
  return shiftHeld.value
    ? `Delete ${subject} from disk. There is no undo`
    : `Move ${subject} to your ${trashName()}. Hold Shift to delete permanently`;
});

const forgetTitle = computed(() => {
  if (!forgettable.value.length) {
    return "Only models whose files are gone can be removed from the shelf";
  }
  if (forgettable.value.length === store.selectedRows.length) {
    return "Forget these models and everything recorded about them";
  }
  return `Forget the ${forgettable.value.length} whose files are gone`;
});

/**
 * The selected models an entity can actually be attached to.
 *
 * Two gates, and they are different refusals. A CHECKPOINT is refused on
 * meaning: "this character uses this LoRA" is not a thing you say about a base
 * model, and the route 400s. A row with no `sha256` is refused on addressing:
 * the attachment table is keyed by the interop hash and a 24 GB file the hash
 * worker has not reached yet has none, so there is nothing to write against -
 * it becomes assignable on its own once the hash lands.
 *
 * Gated the same way Forget is, and for the same reason: the verb acts on the
 * subset it can act on, and the tooltip says how many that is. Passing the
 * whole selection instead would compute the tri-state across rows that can
 * never be attached, so a fully-assigned person would still read as partial.
 */
const assignable = computed(() =>
  store.selectedRows.filter(
    (row) => row.file_kind !== "checkpoint" && row.sha256,
  ),
);

const assignableIds = computed(() => assignable.value.map((row) => row.id));

/**
 * `entity id -> Set of model ids`, per entity type, straight off the rows.
 *
 * The picker's own readers ask which PICTURES are in each entity, which is not
 * a question that has an answer here. Supplying this map is what switches it
 * into host-driven mode, and it costs no request: `attachments` come back on
 * the list, so the answer is already in hand before the menu opens.
 */
const membership = computed(() => {
  const byType = { character: {}, set: {} };
  for (const row of assignable.value) {
    for (const att of row.attachments ?? []) {
      const bucket = byType[att.entity_type];
      // A type the server adds later is skipped rather than crashing the bar.
      if (!bucket) continue;
      const key = String(att.entity_id);
      (bucket[key] ??= new Set()).add(String(row.id));
    }
  }
  return byType;
});

const assignTitle = computed(() => {
  const total = store.selectedRows.length;
  if (!assignable.value.length) {
    return total
      ? "Checkpoints cannot be assigned, and an unhashed file has no hash to assign by"
      : undefined;
  }
  if (assignable.value.length === total) return undefined;
  return `Applies to the ${assignable.value.length} of ${total} that can be assigned`;
});

/**
 * The copies in the selection a move could pick up.
 *
 * Gated per COPY and not per model, so a model with one file on an unplugged
 * NAS and another on this disk IS movable - its present copy is. What the
 * button acts on and what the tooltip counts are the same list, and the view
 * recomputes it for the dialog rather than this being handed up, because a drop
 * onto a folder header has to reach the same list without a selection.
 */
const movable = computed(
  () => movableCopies(store.selectedRows, foldersById.value).items,
);

/**
 * Whether the selection is one model with the same bytes on the disk twice.
 *
 * `copies` is the row's own count of `present` locations, carried by the store
 * because the folder axis narrows a draw to one of them. One row only: the keeper
 * is a per-model choice, and a batch would be a table of radio groups rather than
 * a dialog.
 */
const mergeable = computed(
  () => single.value && (store.selectedRows[0]?.copies ?? 0) > 1,
);

const mergeTitle = computed(() => {
  if (!single.value) return "Choose one model to merge the copies of";
  const copies = store.selectedRows[0]?.copies ?? 0;
  if (copies < 2) {
    return "There is only one copy of this model on the disk";
  }
  return `Keep one of the ${copies} copies on the disk and remove the rest`;
});

const moveTitle = computed(() => {
  if (moves.busy) return "A move is already running. One at a time, one disk.";
  if (!movable.value.length) {
    return "Only files that are actually on this machine can be moved";
  }
  // Counted in COPIES, which is what moves, and named as files rather than
  // models so the number cannot be read against the selection count beside it.
  const n = movable.value.length;
  return `Move ${n.toLocaleString()} ${n === 1 ? "file" : "files"} into another folder`;
});

/**
 * Why this selection cannot become one stack, or `""` when it can.
 *
 * Stack is the manual counterpart to the toolbar's detection sweep, which
 * proposes only files whose names it can explain: a subject the detector cannot
 * name is otherwise ungroupable, and there is no other way to say "these are
 * one thing".
 *
 * **An already-stacked row is no longer a refusal.** Stacking two stacks fuses
 * them - the route absorbs their stacks whole and removes the emptied rows -
 * so the gate that used to read "something here is already part of a run" was
 * blocking the operation this bar now exists to offer.
 *
 * Every other gate the route enforces (`services/stack_detector.apply_stack`)
 * is checked here, so the button is never offered where it could only come back
 * refused: two or more models, adapters only, each with a copy actually
 * present, and ONE folder holding all of them - a stack is files that sit
 * together, and stacking across folders would invent one and put its members on
 * two drives.
 *
 * The gate and its sentence are ONE computed rather than a boolean beside a
 * message that has to be kept in step with it. Written as two, the tooltip
 * named the shared-folder rule for every refusal the boolean made after the
 * cheap checks - so a selection blocked by an unplugged drive was told its files
 * were in different folders, which is a different fact and sends the reader to
 * fix the wrong thing.
 */
const stackRefusal = computed(() => {
  const rows = store.selectedRows;
  if (rows.length < 2) {
    return "Select two or more files, or stacks, to group them";
  }
  if (rows.some((row) => row.file_kind !== "adapter")) {
    return "Only adapters can be stacked";
  }
  let shared = null;
  for (const row of rows) {
    const here = (row.locations || [])
      .filter((loc) => loc.state === "present")
      .map((loc) => Number(loc.folder_id));
    // Its own refusal, and not the folder one: `missing` and `unreachable` mean
    // there is no file here to group, which the reader fixes by plugging a
    // drive in rather than by moving anything.
    if (!here.length) {
      return "Only files that are actually on this machine can be grouped";
    }
    shared =
      shared === null
        ? new Set(here)
        : new Set(here.filter((id) => shared.has(id)));
    if (!shared.size) {
      return "A stack is files that sit together, so they must all be in one folder";
    }
  }
  return "";
});

const stackable = computed(() => !stackRefusal.value);

/** Whether pressing Stack would fuse existing stacks rather than build one. */
const stackFuses = computed(() =>
  store.selectedRows.some((row) => row.stack_id != null),
);

const stackTitle = computed(() => {
  if (stackRefusal.value) return stackRefusal.value;
  const n = store.selectedRows.length.toLocaleString();
  // The verb says which of the two things it is about to do. Fusing takes rows
  // that are already grouped and merges them, which is a different sentence
  // from collapsing loose files, and the reader is entitled to know which.
  return stackFuses.value
    ? `Fuse these ${n} rows into one stack`
    : `Group these ${n} files into one stack`;
});

/**
 * The selection's rows that are ONE member of a run rather than a whole run.
 *
 * The two are told apart by `members`: {@link collapseStacks} gives every
 * stacked row in `visibleRows` that array, and a member picked out of an
 * expanded strip is a raw shelf row without it. That distinction is the whole
 * basis of the two verbs below - both act *inside* a run, which is exactly what
 * selecting the collapsed row cannot express.
 */
const selectedMembers = computed(() =>
  store.selectedRows.filter((row) => row.stack_id != null && !row.members),
);

/**
 * Why this selection cannot be ungrouped, or `""` when it can.
 *
 * The undo the shelf never had, and the reason the grouping dialog could once
 * only warn that nothing takes a stack back. **Whole runs only**, and that has
 * to be checked rather than assumed now that a single member can be selected:
 * `stack_id != null` is true of a member too, so on its own it would let a
 * reader who picked one checkpoint break up the whole run of six. Taking one
 * file out is the verb below, and this one says so.
 */
const unstackRefusal = computed(() => {
  const rows = store.selectedRows;
  if (!rows.length) return "Select a stack to break it up";
  if (rows.some((row) => row.stack_id == null)) {
    return "Something here is not part of a stack";
  }
  if (selectedMembers.value.length) {
    return "That is one file of a run - select the run itself to break it up";
  }
  return "";
});

const unstackable = computed(() => !unstackRefusal.value);

/** The distinct stacks the selection covers, which is what Ungroup acts on. */
const selectedStackIds = computed(() => [
  ...new Set(
    store.selectedRows
      .map((row) => row.stack_id)
      .filter((id) => id != null)
      .map(Number),
  ),
]);

const unstackTitle = computed(() => {
  if (unstackRefusal.value) return unstackRefusal.value;
  const n = selectedStackIds.value.length;
  return n === 1
    ? "Break this stack up, leaving its files on the shelf"
    : `Break these ${n.toLocaleString()} stacks up, leaving their files on the shelf`;
});

/**
 * Why this selection cannot be made the cover, or `""` when it can.
 *
 * One file, and one that is not already the face of its run. The cover is what
 * the shelf draws for the whole stack - its name, its kind, its base - and the
 * filenames pick it when the stack is built; this is the owner saying the
 * heuristic chose the wrong checkpoint.
 */
const coverRefusal = computed(() => {
  const members = selectedMembers.value;
  if (store.selectedRows.length !== 1 || members.length !== 1) {
    return "Open a run and pick one file inside it - a run has one cover";
  }
  if (members[0].stack_position === 0) {
    return "This file already stands for its run";
  }
  return "";
});

const coverable = computed(() => !coverRefusal.value);

const coverTitle = computed(
  () => coverRefusal.value || "Draw the run from this file instead",
);

/**
 * Why these files cannot be taken out of their runs, or `""` when they can.
 *
 * The single-file counterpart to Ungroup, and gated the opposite way round: a
 * whole run selected is Ungroup's business, and a loose row is in no run to
 * leave.
 */
const releaseRefusal = computed(() => {
  const rows = store.selectedRows;
  if (!rows.length) return "Open a run and pick a file inside it";
  if (rows.some((row) => row.stack_id == null)) {
    return "Something here is not part of a run";
  }
  if (rows.length !== selectedMembers.value.length) {
    return "That is a whole run - use Ungroup to break it up";
  }
  return "";
});

const releasable = computed(() => !releaseRefusal.value);

const releaseTitle = computed(() => {
  if (releaseRefusal.value) return releaseRefusal.value;
  const n = selectedMembers.value.length;
  return n === 1
    ? "Take this file out of its run, leaving it on the shelf"
    : `Take these ${n.toLocaleString()} files out of their runs, leaving them on the shelf`;
});

/**
 * Can the host be asked to show this row's folder? (#933)
 *
 * Gated on a `present` copy, which is the RECORDED half of the route's own
 * gate: a `missing` row names a folder with nothing of ours in it and an
 * `unreachable` one names a drive that is not plugged in, so either would come
 * back 409. The other half is `os.path.isfile`, which only the server can
 * answer, so a row whose file went between the list and the press is a 409 the
 * view has its own sentence for.
 *
 * A collapsed stack spreads its COVER, so this reads the cover's copies and the
 * cover's folder is what opens: one press, one window, on the file that was
 * right-clicked. A run whose cover is gone while a step of it is not is
 * therefore refused rather than opening some other member's folder, which would
 * answer a different question from the one the row asked.
 *
 * Single selection only, like Rename: forty rows would be forty windows.
 */
const openable = computed(
  () =>
    single.value &&
    (store.selectedRows[0]?.locations ?? []).some(
      (loc) => loc.state === "present",
    ),
);

const openTitle = computed(() =>
  openable.value
    ? "Show this file where it lives, on the machine running PixlStash"
    : "There is no copy of this on that machine right now",
);

/**
 * Put the selection's filenames on the clipboard.
 *
 * The other verb in the design's context menu the shelf can answer without a
 * new route: the names are already on the rows. It is also the answer to "I need
 * this in a ComfyUI node", which is why it is worth a line at all.
 *
 * A notice rather than silence, because a clipboard write has no visible
 * result: pressing it twice because nothing happened is how a reader ends up
 * unsure whether it worked at all.
 */
async function copyFilenames() {
  const names = store.selectedRows.map((row) => row.filename).filter(Boolean);
  const notices = useNoticeStore();
  if (!names.length) return;
  try {
    await navigator.clipboard.writeText(names.join("\n"));
    notices.push({
      level: "success",
      text:
        names.length === 1
          ? `Copied ${names[0]}.`
          : `Copied ${names.length} filenames.`,
    });
  } catch (err) {
    // Denied permission, an insecure origin, or a browser that has no
    // clipboard API at all. Named rather than swallowed: the reader pressed a
    // verb and nothing on screen would otherwise say it did not run.
    notices.push({
      level: "error",
      text: `Could not reach the clipboard: ${err?.message || err}`,
    });
  }
}

function clear() {
  store.clearSelection();
}

/**
 * One write per row, for the entity the picker was pointed at.
 *
 * The route replaces one adapter's whole attachment set, so the store owns the
 * read-modify-write; the picker only says which entity, and which way.
 */
function onAttach(payload, attach) {
  return store.setAttachment({ ...payload, attach });
}

/**
 * Everything `VerbMenu` needs, in one object, so the two mounts of it cannot
 * drift apart. Passed with `v-bind` rather than listed twice in the template.
 */
const verbHandlers = computed(() => ({
  rows: store.selectedRows,
  renameTitle: renameTitle.value,
  iconTitle: iconTitle.value,
  clearIconTitle: clearIconTitle.value,
  hasIcons: withIcons.value.length > 0,
  assignTitle: assignTitle.value,
  assignableIds: assignableIds.value,
  assignable: assignable.value.length > 0,
  membership: membership.value,
  stackable: stackable.value,
  stackTitle: stackTitle.value,
  unstackable: unstackable.value,
  unstackTitle: unstackTitle.value,
  coverable: coverable.value,
  coverTitle: coverTitle.value,
  releasable: releasable.value,
  releaseTitle: releaseTitle.value,
  releaseLabel:
    selectedMembers.value.length > 1
      ? "Take out of their runs"
      : "Take out of this run",
  movable: movable.value.length > 0 && !moves.busy,
  moveTitle: moveTitle.value,
  mergeable: mergeable.value,
  mergeTitle: mergeTitle.value,
  openable: openable.value,
  openTitle: openTitle.value,
  forgettable: forgettable.value.length > 0,
  forgetTitle: forgetTitle.value,
  deletable: deletable.value.length > 0,
  deleteLabel: deleteLabel.value,
  deleteTitle: deleteTitle.value,
  onVerb: (verb, event) => {
    contextOpen.value = false;
    moreMenuOpen.value = false;
    if (verb === "copy-filenames") copyFilenames();
    // The gesture decides, not the tracked key state: `shiftHeld` drives the
    // label and nothing else, so a stale one can never turn a trash into an
    // unlink.
    else if (verb === "delete") emit("delete", Boolean(event?.shiftKey));
    else emit(verb);
  },
  onAttach,
}));

/**
 * The verb list itself, as a render function rather than a second `.vue` file.
 *
 * It is drawn twice - once under `⋯` and once at the pointer - from one array,
 * and it is the ONE place the design's ordering lives: rename and set icon
 * first because they are about this row, then the properties, then the two that
 * move files, then removal. A separate component would need every gate above
 * threaded through it as props anyway, which is exactly what `verbHandlers` is.
 */
const VerbMenu = (props) => {
  const item = (icon, label, { on, disabled, title, kbd, danger } = {}) =>
    h(
      "button",
      {
        class: [
          "ctx-item",
          disabled && "ctx-item--disabled",
          danger && "ctx-item--danger",
        ],
        type: "button",
        role: "menuitem",
        disabled,
        onClick: on,
      },
      [
        title ? h(Tooltip, { text: title, activator: "parent" }) : null,
        h("i", {
          class: `v-icon mdi ${icon} ctx-icon`,
          "aria-hidden": "true",
        }),
        h("span", { class: "ctx-label-text" }, label),
        kbd ? h("span", { class: "ctx-shortcut" }, kbd) : null,
      ],
    );
  const sep = () => h("div", { class: "ctx-sep" });
  // No `floatMenu`: it teleports the panel below the row, which a flyout cannot
  // use, and the picker now refuses the pair anyway.
  const assign = (type, label) =>
    h(
      Tooltip,
      { text: props.assignTitle || "" },
      {
        activator: ({ props: tipProps }) =>
          h(AddToEntityControl, {
            ...tipProps,
            type,
            label,
            placement: "right",
            subjectIds: props.assignableIds,
            membership: props.membership[type],
            disabled: !props.assignable,
            onAttach: (entity) => props.onAttach(entity, true),
            onDetach: (entity) => props.onAttach(entity, false),
          }),
      },
    );

  return h("div", { class: "ctx-menu shelf-menu", role: "menu" }, [
    props.single
      ? item("mdi-pencil-outline", "Rename", {
          on: () => props.onVerb("rename"),
          title: props.renameTitle,
          kbd: "F2",
        })
      : null,
    item("mdi-image-outline", "Set thumbnail…", {
      on: () => props.onVerb("set-icon"),
      title: props.iconTitle,
    }),
    props.hasIcons
      ? item("mdi-image-off-outline", "Clear thumbnail", {
          on: () => props.onVerb("clear-icons"),
          title: props.clearIconTitle,
        })
      : null,
    sep(),
    item("mdi-cube-outline", "Set base model…", {
      on: () => props.onVerb("set-base-model"),
    }),
    item("mdi-shape-outline", "Set kind…", {
      on: () => props.onVerb("set-kind"),
    }),
    assign("character", "Assign to person"),
    assign("set", "Assign to set"),
    item(
      "mdi-layers-outline",
      props.single ? "Stack with selection" : "Stack these",
      {
        on: () => props.onVerb("stack"),
        disabled: !props.stackable,
        title: props.stackTitle,
      },
    ),
    item("mdi-layers-off-outline", "Ungroup", {
      on: () => props.onVerb("unstack"),
      disabled: !props.unstackable,
      title: props.unstackTitle,
    }),
    // The two verbs that act INSIDE a run. Always listed, and disabled with
    // their reason on a selection that is not a run's member: this is where a
    // reader who has never opened a stack finds out that opening one is a
    // gesture, which a hidden item could never tell them.
    item("mdi-arrow-collapse-up", "Make this the cover", {
      on: () => props.onVerb("make-cover"),
      disabled: !props.coverable,
      title: props.coverTitle,
    }),
    item("mdi-layers-minus", props.releaseLabel, {
      on: () => props.onVerb("remove-from-stack"),
      disabled: !props.releasable,
      title: props.releaseTitle,
    }),
    item("mdi-folder-move-outline", "Move to…", {
      on: () => props.onVerb("move"),
      disabled: !props.movable,
      title: props.moveTitle,
    }),
    // Listed always and disabled with its reason, like the two run verbs above:
    // a reader whose shelf has no duplicates on screen is the one who most needs
    // to learn that the verb exists and what it acts on. NOT the danger
    // treatment - it removes a redundant copy and keeps the model, which is the
    // opposite of what the red below is for.
    item("mdi-call-merge", "Keep one copy…", {
      on: () => props.onVerb("merge-copies"),
      disabled: !props.mergeable,
      title: props.mergeTitle,
    }),
    sep(),
    // The two verbs that answer "where is this file, actually" - the first
    // asks the server's own desktop, the second answers on this machine.
    // Single selection only for the opener: it is one window per press.
    props.single
      ? item("mdi-folder-open-outline", "Open in file manager", {
          on: () => props.onVerb("open-location"),
          disabled: !props.openable,
          title: props.openTitle,
        })
      : null,
    item(
      "mdi-content-copy",
      props.single ? "Copy filename" : "Copy filenames",
      { on: () => props.onVerb("copy-filenames") },
    ),
    sep(),
    // NOT the danger treatment: removing a model whose file is already gone
    // destroys a row, not bytes. The one below it does destroy bytes, which is
    // what the red is for.
    item("mdi-playlist-remove", "Remove from shelf", {
      on: () => props.onVerb("forget"),
      disabled: !props.forgettable,
      title: props.forgetTitle,
    }),
    // The label IS the operation here, and it changes under Shift - the
    // file-manager gesture, spelled the way Windows Explorer spells it. The
    // event is handed on rather than the tracked key state, so what runs is
    // what the reader's hand was doing at the moment they pressed.
    item(
      props.deleteLabel === "Permanently delete"
        ? "mdi-delete-forever-outline"
        : "mdi-delete-outline",
      props.deleteLabel,
      {
        on: (event) => props.onVerb("delete", event),
        disabled: !props.deletable,
        title: props.deleteTitle,
        kbd: "Del",
        danger: true,
      },
    ),
  ]);
};

// The opener the view calls, and the gates the suite asserts directly rather
// than through seven `title` strings. Last in the file because `defineExpose`
// evaluates its argument where it stands.
defineExpose({
  openContextMenu,
  deletable,
  forgettable,
  assignable,
  membership,
  movable,
  mergeable,
  openable,
  selectedBytes,
  stackable,
  stackFuses,
  unstackable,
  coverable,
  releasable,
  selectedMembers,
  selectedStackIds,
  withIcons,
});
</script>

<style>
/* The rows are the global `.ctx-*` menu (styles/context-menu.css). What is left
   here is sizing, and it is global because the verb menu is a render function,
   which scoped CSS cannot reach. */
.shelf-menu {
  min-width: 210px;
}

/* The Assign popover's two pickers float their listbox (`float-menu`), so they
   are not in flyout mode and keep their toolbar-button skin; drawn here as the
   menu's rows. `.ate` is repeated past the scoped `.ate-btn[data-v-…]`. */
.shelf-menu--assign .ate {
  width: 100%;
}

.shelf-menu--assign .ate .ate-btn {
  width: 100%;
  min-height: var(--control-h-bar);
  gap: var(--space-3);
  padding: 0 var(--space-4);
  border: 0;
  border-radius: 0;
  background: transparent;
}

.shelf-menu--assign .ate .ate-btn:hover:not(:disabled) {
  background: var(--hover-wash);
}

.shelf-menu--assign .ate .ate-btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring-inset);
}
</style>
