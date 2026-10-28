<template>
  <!-- The floating pill, the SAME object the photo grid docks over its tiles:
       bottom-centre, panel surface, pill radius, elevation-4. Count first, the
       three verbs that are used most carry their words, the rest sit in `⋯`,
       and the one that removes a row from the shelf is the only icon in the
       error colour. Every verb is also on the row context menu, which is the
       full inventory and is unchanged. -->
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

         The verbs here carry their WORDS, so the visible text is the name; the
         tooltip is the REFUSAL ("Only files that are actually on this machine
         can be moved") and changes with the selection. -->
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
          :disabled="!assignable.length"
          :tooltip="assignTitle || 'Assign to person or set'"
        >
          Assign
        </AppBarButton>
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

    <!-- Move rides along DISABLED rather than disappearing, with its refusal. -->
    <AppBarButton
      shape="round"
      icon="folder-move-outline"
      data-verb="move"
      :disabled="!movable.length || moves.busy"
      :tooltip="moveTitle"
      @click="emit('move')"
    >
      Move to…
    </AppBarButton>

    <!-- Favourite leads the verbs (#1578): a heart and its H, the same verb the
         menus lead with. Disabled, with its reason, when nothing is keyed. -->
    <AppBarButton
      shape="round"
      :icon="favouriteOn ? 'heart' : 'heart-outline'"
      data-verb="favourite"
      :aria-label="favouriteLabel"
      aria-keyshortcuts="H"
      :disabled="!favouritable"
      :tooltip="favouriteTitle || `${favouriteLabel} (H)`"
      @click="store.toggleFavourites(favouriteTargets)"
    >
      {{ favouriteLabel }}
      <kbd class="selbar-kbd" aria-hidden="true">H</kbd>
    </AppBarButton>

    <!-- Every other verb, under the same gates and refusals as the context
         menu: stack, unstack, rename, set thumbnail, delete and the rest. -->
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
          v-bind="withRef(menuProps, (el) => (moreTrigger = el))"
          data-verb="more"
          aria-label="More actions"
          aria-haspopup="menu"
          :aria-expanded="moreMenuOpen"
          tooltip="More…"
        />
      </template>
      <VerbMenu :single="single" :pill="true" v-bind="verbHandlers" />
    </v-menu>

    <span class="selbar-sep"></span>

    <!-- Remove from shelf: the one verb drawn in the error colour, last. It
         removes a row, not bytes, but it is the verb that ends the selection's
         presence on the shelf, so it wears the destructive treatment. -->
    <AppBarButton
      shape="round"
      danger
      icon="delete-outline"
      data-verb="forget"
      aria-label="Remove from shelf"
      :disabled="!forgettable.length"
      :tooltip="forgetTitle"
      @click="emit('forget')"
    />

    <AppBarButton
      shape="round"
      icon="close"
      data-verb="clear"
      aria-label="Clear selection (Esc)"
      tooltip="Clear selection (Esc)"
      @click="clear"
    />
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
import { favouriteVerb } from "../../stores/useFavouritesStore";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { formatKeyHint, selectAllKeyHint } from "../../utils/shortcutHints";
import {
  deletableModels,
  formatModelSize,
  hideModelsVerb,
  lookupEligible,
  MAX_BASE_MODEL_LOOKUP,
  trashName,
} from "../../utils/modelShelf";
import { onMenuKeydown } from "../../utils/menuKeyboard.js";
import { withRef } from "../../utils/withRef.js";
import { hideSetsVerb } from "../../utils/workflowSets";

const props = defineProps({
  /**
   * The sets from pictures whose cards are selected, on the set grid: what
   * Hide acts on, missing-checkpoint cards included. Empty on the row list,
   * which draws no sets.
   */
  sets: { type: Array, default: () => [] },
  /**
   * The evidence cards picked on the set grid. Read by the favourite verb, so
   * it favourites a set and not the model the card is named after.
   */
  cardKeys: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "rename",
  "set-base-model",
  "lookup-base-model",
  "set-kind",
  "set-trigger-words",
  "stack",
  "unstack",
  "make-cover",
  "remove-from-stack",
  "set-icon",
  "clear-icons",
  "move",
  "merge-copies",
  "open-location",
  "works-with",
  "forget",
  "delete",
  "new-set",
  "clone-set",
  "remove-from-set",
  "hide-sets",
  "hide-models",
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
// The ⋯ button, so a verb picked from its menu hands focus back to it: the
// rows leave with the menu. Not reactive: only `onVerb` reads it.
let moreTrigger = null;
const contextOpen = ref(false);
/** The set-grid card the context menu was opened on, by group key, or "". */
const contextSetKey = ref("");
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
function openContextMenu(x, y, setKey = "") {
  contextSetKey.value = setKey;
  contextAt.value = [x, y];
  contextOpen.value = true;
}

const single = computed(() => store.selectedRows.length === 1);

// ── Favourites (#1578) ─────────────────────────────────────────────────────

/**
 * What the verb acts on: the picked cards, the rows, and the card the context
 * menu is open on - only while it is open, since nothing clears the key after.
 */
const favouriteTargets = computed(() =>
  store.favouriteTargets(
    [...props.cardKeys, contextOpen.value ? contextSetKey.value : ""].filter(Boolean),
  ),
);
const favouritable = computed(() => favouriteTargets.value.length > 0);
/** Unfavourite only when every keyed item already is one. */
const favouriteOn = computed(() =>
  store.allFavouriteTargets(favouriteTargets.value),
);
const favouriteLabel = computed(() => favouriteVerb(favouriteOn.value));
const favouriteTitle = computed(() =>
  favouritable.value ? "" : "Favourite once its hash is read",
);

// ── Hand-made workflow sets (#1520) ─────────────────────────────────────────

/**
 * "New workflow set with this checkpoint": one base model, hashed, since a set
 * keeps its members by hash. A checkpoint still being hashed is refused with
 * that reason rather than failing on the server.
 */
const newSetTitle = computed(() => {
  const row = store.selectedRows[0];
  if (!row || !["checkpoint", "unknown"].includes(row.file_kind)) {
    return "Only a checkpoint starts a set this way. The New workflow set tile on the set grid makes an empty one.";
  }
  if (!row.sha256) {
    // No hash and none on its way (a cached Hugging Face repo is a directory):
    // "try again in a moment" would be said of it for good.
    return row.hash_pending
      ? "This checkpoint is still being hashed, and a set keeps its models by hash. Try again in a moment."
      : "A set keeps its models by file hash, and this one has none.";
  }
  return "";
});

/**
 * "Clone set to edit": the set from pictures whose CARD the context menu was
 * opened on. Keyed by the card and not by the selected file, because the same
 * file is drawn in other trays too, where "this set" would be another one.
 * A set from pictures cannot be edited; its clone is the owner's and can.
 */
const clonableSet = computed(
  () =>
    (contextOpen.value &&
      store.setGroups?.find(
        (group) => group.key === contextSetKey.value && !group.head?.missing,
      )) ||
    null,
);

/**
 * Remove from set: offered while every selected file is a member of the OPEN
 * hand-made set's tray - the one place a reader can see which set they mean.
 */
const removableFromSet = computed(() => {
  const open = store.handMadeGroups?.find((g) => g.key === store.openSetKey);
  if (!open || !store.selectedRows.length) return false;
  const members = new Set(
    (open.set.members ?? []).filter((m) => m.on_shelf).map((m) => m.id),
  );
  return store.selectedModelIds.every((id) => members.has(id));
});

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
 *
 * Per MODEL, a ticked stack expanded into its members: the stack row carries
 * only the cover's id, hash and attachments, so assigning it off the row
 * reached the cover and left every other member unassigned.
 */
const assignable = computed(() =>
  store.selectedModels.filter(
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

/**
 * Whether the selection can be given trigger words: every model in it has to be
 * something a prompt calls up. `unknown` counts, as it does for assigning - an
 * unclassified file is as likely a LoRA as anything else. The item stays listed
 * and says why when it cannot, like the run verbs below it.
 */
const triggerable = computed(
  () =>
    store.selectedModels.length > 0 &&
    store.selectedModels.every(
      (row) => row.file_kind === "adapter" || row.file_kind === "unknown",
    ),
);

/**
 * The selected models Look up base model online would ask Civitai about.
 * Listed always, disabled with its reason when none would be, like Set trigger
 * words above; over the route's cap it says so rather than sending part.
 */
const lookupIds = computed(() =>
  store.selectedModels.filter(lookupEligible).map((row) => row.id),
);

const lookupTitle = computed(() => {
  const eligible = lookupIds.value.length;
  if (!eligible) {
    return "Only adapters and checkpoints with no base model, once hashed, can be looked up";
  }
  if (eligible > MAX_BASE_MODEL_LOOKUP) {
    return `Select at most ${MAX_BASE_MODEL_LOOKUP} models with no base model at a time`;
  }
  return "";
});

const assignTitle = computed(() => {
  const total = store.selectedModels.length;
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
 * onto a folder header has to reach the same list without a selection. In a
 * folder view, only the copy in the open folder counts (#2204), as in the
 * dialog.
 */
const movable = computed(
  () => store.movableSelection(foldersById.value).items,
);

/**
 * Whether the selection is one model with the same bytes on the disk twice.
 *
 * `copies` is the row's own count of `present` locations, carried by the store
 * because the folder axis narrows a draw to one of them. One row only: the keeper
 * is a per-model choice, and a batch would be a table of radio groups rather than
 * a dialog.
 */
const mergeable = computed(() => {
  const row = store.selectedRows[0];
  // A collapsed run satisfies `single` - it is one row - and carries the COVER's
  // `copies`, so without the `members` check the verb would be offered on a
  // whole run and act on one file of it while the pill counted several. `move`
  // has the same shape and may be settled convention there; this one deletes
  // bytes, so it asks for a model.
  return single.value && !row?.members && (row?.copies ?? 0) > 1;
});

const mergeTitle = computed(() => {
  const row = store.selectedRows[0];
  if (!single.value) return "Choose one model to merge the copies of";
  if (row?.members) return "Open the run and choose one of its models";
  const copies = row?.copies ?? 0;
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
 * The hashes the selection has, for Copy SHA-256.
 *
 * The hash is what sets and recipes are matched by, and the file line's
 * tooltip shows it - but a tooltip is never a fact's only home, and nobody can
 * select text out of one. A checkpoint not hashed yet has none to give.
 */
const selectedHashes = computed(() =>
  store.selectedRows.map((row) => row.sha256).filter(Boolean),
);

/**
 * Put one line per selected model on the clipboard: its filename, or its hash.
 *
 * Filenames are the other verb in the design's context menu the shelf can
 * answer without a new route: the names are already on the rows. It is also the
 * answer to "I need this in a ComfyUI node", which is why it is worth a line.
 *
 * A notice rather than silence, because a clipboard write has no visible
 * result: pressing it twice because nothing happened is how a reader ends up
 * unsure whether it worked at all.
 */
async function copyLines(names, plural) {
  const notices = useNoticeStore();
  if (!names.length) return;
  // A row with nothing to give (a checkpoint not hashed yet) is left out, and
  // the notice says so: a paste of two lines from three models must not read
  // as the whole selection.
  const total = store.selectedRows.length;
  const skipped = total - names.length;
  try {
    await navigator.clipboard.writeText(names.join("\n"));
    notices.push({
      level: "success",
      text:
        (names.length === 1
          ? `Copied ${names[0]}.`
          : `Copied ${names.length} ${plural}.`) +
        (skipped > 0 ? ` ${skipped} of ${total} selected had none.` : ""),
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
  hideSets: hideSetsVerb(props.sets),
  hideModels: hideModelsVerb(
    store.selectedModels.map((model) => model.id),
    store.hiddenModelIds,
  ),
  newSetTitle: newSetTitle.value,
  removableFromSet: removableFromSet.value,
  clonable: Boolean(clonableSet.value),
  renameTitle: renameTitle.value,
  iconTitle: iconTitle.value,
  clearIconTitle: clearIconTitle.value,
  hasIcons: withIcons.value.length > 0,
  assignTitle: assignTitle.value,
  assignableIds: assignableIds.value,
  assignable: assignable.value.length > 0,
  membership: membership.value,
  lookupable: lookupIds.value.length > 0 && !lookupTitle.value,
  lookupTitle: lookupTitle.value,
  triggerable: triggerable.value,
  triggerTitle: triggerable.value
    ? ""
    : "Only LoRAs and other adapters have trigger words",
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
  hashed: selectedHashes.value.length > 0,
  hashPending: store.selectedRows.some((row) => row.hash_pending),
  forgettable: forgettable.value.length > 0,
  forgetTitle: forgetTitle.value,
  deletable: deletable.value.length > 0,
  deleteLabel: deleteLabel.value,
  deleteTitle: deleteTitle.value,
  favouriteOn: favouriteOn.value,
  favouriteLabel: favouriteLabel.value,
  favouritable: favouritable.value,
  favouriteTitle: favouriteTitle.value,
  onVerb: (verb, event) => {
    // Read before the menu closes: it is the open menu's card.
    const cloned = clonableSet.value;
    const targets = favouriteTargets.value;
    const fromMore = moreMenuOpen.value;
    contextOpen.value = false;
    moreMenuOpen.value = false;
    if (fromMore) moreTrigger?.focus?.();
    if (verb === "copy-filenames")
      copyLines(
        store.selectedRows.map((row) => row.filename).filter(Boolean),
        "filenames",
      );
    else if (verb === "copy-sha256")
      copyLines(selectedHashes.value, "SHA-256 hashes");
    // The gesture decides, not the tracked key state: `shiftHeld` drives the
    // label and nothing else, so a stale one can never turn a trash into an
    // unlink.
    else if (verb === "delete") emit("delete", Boolean(event?.shiftKey));
    else if (verb === "favourite") store.toggleFavourites(targets);
    else if (verb === "clone-set") emit("clone-set", cloned);
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
const VerbItems = (props) => {
  // `verb` names the row for `data-verb`, so a test or a shortcut can find a
  // row by the operation it performs, the way it finds a pill button.
  const item = (icon, label, { verb, on, disabled, title, kbd, danger } = {}) =>
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
        "data-verb": verb,
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
  // `pill`: the `⋯` under the selection pill. Favourite, Assign, Move and
  // Remove from shelf are on the pill itself, so they are left out here; the
  // context menu, which has no pill beside it, lists all of them.
  const pill = Boolean(props.pill);

  return [
    // Favourite leads the whole menu (#1578): it touches no file, and it is the
    // one verb every surface shares. Unhashed models have no key and say why.
    pill
      ? null
      : item(
          props.favouriteOn ? "mdi-heart" : "mdi-heart-outline",
          props.favouriteLabel,
          {
            verb: "favourite",
            on: () => props.onVerb("favourite"),
            disabled: !props.favouritable,
            title: props.favouriteTitle,
            kbd: "H",
          },
        ),
    pill ? null : sep(),
    // Inside an open hand-made set's tray the set's own verb leads: it is what
    // the reader is working on, and it never touches a file.
    props.removableFromSet
      ? item("mdi-minus-circle-outline", "Remove from set", {
          verb: "remove-from-set",
          on: () => props.onVerb("remove-from-set"),
          title: "Takes it off this set only. The file stays on the shelf.",
        })
      : null,
    props.removableFromSet ? sep() : null,
    // Hide leads for the same reason, and to sit as far from the two removal
    // verbs at the foot as the menu allows: this one takes a card off the grid
    // and those take a row off the shelf or a file off the disk.
    props.hideSets
      ? item(props.hideSets.icon, props.hideSets.label, {
          verb: "hide-sets",
          on: () => props.onVerb("hide-sets"),
          title: props.hideSets.hide
            ? "No file is touched. Show them, under the grid, brings hidden sets back."
            : "",
        })
      : null,
    // The model's own Hide, worded apart from the set's just above. Beside it
    // for the reason that one leads: it touches no file, and the verbs that do
    // are at the foot.
    props.hideModels
      ? item(props.hideModels.icon, props.hideModels.label, {
          verb: "hide-models",
          on: () => props.onVerb("hide-models"),
          title: props.hideModels.title,
        })
      : null,
    props.hideSets || props.hideModels ? sep() : null,
    // Rename is on the pill's `⋯` too, disabled with its reason for a
    // selection of several, as the pill used to draw it; the context menu
    // leaves it out for several.
    props.single || pill
      ? item("mdi-pencil-outline", "Rename", {
          verb: "rename",
          on: () => props.onVerb("rename"),
          disabled: !props.single,
          title: props.renameTitle,
          kbd: "F2",
        })
      : null,
    item("mdi-image-outline", "Set thumbnail…", {
      verb: "set-icon",
      on: () => props.onVerb("set-icon"),
      title: props.iconTitle,
    }),
    props.hasIcons
      ? item("mdi-image-off-outline", "Clear thumbnail", {
          verb: "clear-icons",
          on: () => props.onVerb("clear-icons"),
          title: props.clearIconTitle,
        })
      : null,
    sep(),
    item("mdi-cube-outline", "Set base model…", {
      verb: "set-base-model",
      on: () => props.onVerb("set-base-model"),
    }),
    item("mdi-cloud-search-outline", "Look up base model online…", {
      verb: "lookup-base-model",
      on: () => props.onVerb("lookup-base-model"),
      disabled: !props.lookupable,
      title: props.lookupTitle,
    }),
    item("mdi-shape-outline", "Set kind…", {
      verb: "set-kind",
      on: () => props.onVerb("set-kind"),
    }),
    item("mdi-format-quote-close-outline", "Set trigger words…", {
      verb: "set-trigger-words",
      on: () => props.onVerb("set-trigger-words"),
      disabled: !props.triggerable,
      title: props.triggerTitle,
    }),
    pill ? null : assign("character", "Assign to person"),
    pill ? null : assign("set", "Assign to set"),
    item(
      "mdi-layers-outline",
      props.single ? "Stack with selection" : "Stack these",
      {
        verb: "stack",
        on: () => props.onVerb("stack"),
        disabled: !props.stackable,
        title: props.stackTitle,
      },
    ),
    item("mdi-layers-off-outline", "Ungroup", {
      verb: "unstack",
      on: () => props.onVerb("unstack"),
      disabled: !props.unstackable,
      title: props.unstackTitle,
    }),
    // The two verbs that act INSIDE a run. Always listed, and disabled with
    // their reason on a selection that is not a run's member: this is where a
    // reader who has never opened a stack finds out that opening one is a
    // gesture, which a hidden item could never tell them.
    item("mdi-arrow-collapse-up", "Make this the cover", {
      verb: "make-cover",
      on: () => props.onVerb("make-cover"),
      disabled: !props.coverable,
      title: props.coverTitle,
    }),
    item("mdi-layers-minus", props.releaseLabel, {
      verb: "remove-from-stack",
      on: () => props.onVerb("remove-from-stack"),
      disabled: !props.releasable,
      title: props.releaseTitle,
    }),
    pill
      ? null
      : item("mdi-folder-move-outline", "Move to…", {
          verb: "move",
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
      verb: "merge-copies",
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
          verb: "open-location",
          on: () => props.onVerb("open-location"),
          disabled: !props.openable,
          title: props.openTitle,
        })
      : null,
    // One model at a time, because the answer is about one file: "what has THIS
    // run with" has no reading across a selection of forty. The evidence is
    // co-occurrence, so the dialog it opens also has to say that a companion
    // missing from its list is untested rather than incompatible (#1438).
    props.single
      ? item("mdi-connection", "Works with…", {
          verb: "works-with",
          on: () => props.onVerb("works-with"),
        })
      : null,
    props.single
      ? item("mdi-layers-plus", "New workflow set with this checkpoint", {
          verb: "new-set",
          on: () => props.onVerb("new-set"),
          disabled: Boolean(props.newSetTitle),
          title: props.newSetTitle,
        })
      : null,
    props.clonable
      ? item("mdi-pencil-outline", "Edit set", {
          verb: "clone-set",
          on: () => props.onVerb("clone-set"),
          title:
            "Makes it a set of your own on this checkpoint. It keeps following the pictures and reads Automatic until you change it. No file is touched.",
        })
      : null,
    item(
      "mdi-content-copy",
      props.single ? "Copy filename" : "Copy filenames",
      { verb: "copy-filenames", on: () => props.onVerb("copy-filenames") },
    ),
    item("mdi-fingerprint", "Copy SHA-256", {
      verb: "copy-sha256",
      on: () => props.onVerb("copy-sha256"),
      disabled: !props.hashed,
      // "Yet" only where a hash is on its way: an engine or a cached Hugging
      // Face repo never gets one.
      title: props.hashed
        ? ""
        : props.hashPending
          ? "Not hashed yet"
          : "No file hash",
    }),
    sep(),
    // NOT the danger treatment: removing a model whose file is already gone
    // destroys a row, not bytes. The one below it does destroy bytes, which is
    // what the red is for. Off the pill's `⋯` because the pill has it as an icon.
    pill
      ? null
      : item("mdi-playlist-remove", "Remove from shelf", {
          verb: "forget",
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
        verb: "delete",
        on: (event) => props.onVerb("delete", event),
        disabled: !props.deletable,
        title: props.deleteTitle,
        kbd: "Del",
        danger: true,
      },
    ),
  ];
};

const VerbMenu = (props) =>
  h("div", { class: "ctx-menu shelf-menu", role: "menu" }, VerbItems(props));

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
/* The key-hint chip on a labelled pill verb (Favourite H). The same paint as
   AppButton's `.app-btn__key`: currentColor border, so it follows the verb. */
.selbar-kbd {
  margin-left: var(--space-1);
  padding: 0 var(--space-1);
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  line-height: 1.5;
  border: 1px solid currentColor;
  border-radius: var(--radius-sm);
  opacity: 0.55;
}

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
