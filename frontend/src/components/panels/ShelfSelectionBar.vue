<template>
  <!-- `role="toolbar"` and an accessible name, because this is a row of related
       controls that appears and disappears: without the name a screen reader
       announces four unrelated buttons arriving from nowhere. -->
  <div
    v-if="store.selectedRows.length"
    class="shelf-selbar"
    role="toolbar"
    aria-label="Selected models"
  >
    <span class="shelf-selbar-count">{{ countLabel }}</span>

    <button class="bar-btn shelf-selbar-clear" type="button" @click="clear">
      Clear
    </button>

    <span class="shelf-selbar-spacer"></span>

    <!-- Rename is the one verb that is single-row by nature: a name is a fact
         about one file, and the server refuses it for more than one id. Shown
         and disabled rather than hidden, so the row of verbs does not reflow
         under the pointer as the selection grows. -->
    <AppButton
      size="sm"
      variant="secondary"
      icon-left="rename-outline"
      :disabled="store.selectedRows.length !== 1"
      :title="renameTitle"
      @click="emit('rename')"
    >
      Rename
    </AppButton>

    <AppButton
      size="sm"
      variant="secondary"
      icon-left="cube-outline"
      @click="emit('set-base-model')"
    >
      Set base model
    </AppButton>

    <AppButton
      size="sm"
      variant="secondary"
      icon-left="shape-outline"
      @click="emit('set-kind')"
    >
      Set kind
    </AppButton>

    <!-- Stack, the manual half of grouping. The toolbar's sweep proposes only
         what a training step spells out; a run whose files the detector cannot
         read as one has no other way to be said. Gated on every rule the route
         enforces, with the failing one in the tooltip. -->
    <AppButton
      size="sm"
      variant="secondary"
      icon-left="layers-outline"
      :disabled="!stackable"
      :title="stackTitle"
      @click="emit('stack')"
    >
      Stack these
    </AppButton>

    <!-- Assign, the fifth verb, as two pickers rather than a button: it names
         an entity, and the shelf uses the same picker the grid does so the
         search, the tri-state and the keyboard model are learned once. Both are
         host-driven — the rows already carry their `attachments`, so nothing is
         fetched, and the writes are the store's because the route replaces one
         adapter's whole set. -->
    <AddToEntityControl
      type="character"
      label="Assign to person"
      :subject-ids="assignableIds"
      :membership="membership.character"
      :disabled="!assignable.length"
      :title="assignTitle"
      @attach="onAttach($event, true)"
      @detach="onAttach($event, false)"
    />

    <AddToEntityControl
      type="set"
      label="Assign to set"
      :subject-ids="assignableIds"
      :membership="membership.set"
      :disabled="!assignable.length"
      :title="assignTitle"
      @attach="onAttach($event, true)"
      @detach="onAttach($event, false)"
    />

    <!-- An icon answers "which one is this?", so it is single-row by nature:
         giving forty rows one mark would remove the only thing telling them
         apart. Shown and disabled rather than hidden, like Rename. -->
    <AppButton
      size="sm"
      variant="secondary"
      icon-left="image-outline"
      :disabled="store.selectedRows.length !== 1"
      :title="iconTitle"
      @click="emit('set-icon')"
    >
      Set icon
    </AppButton>

    <AppButton
      v-if="withIcons.length"
      size="sm"
      variant="secondary"
      icon-left="image-off-outline"
      :title="clearIconTitle"
      @click="emit('clear-icons')"
    >
      Clear icon
    </AppButton>

    <!-- Move is the keyboard path to what a drag does. The shelf's definition
         of done requires every verb to be reachable without a pointer, and a
         drag is not; it is also where the move is stated in files, bytes and
         rename-versus-copy before a 438 GB operation starts. -->
    <AppButton
      size="sm"
      variant="secondary"
      icon-left="folder-move-outline"
      :disabled="!movable.length || moves.busy"
      :title="moveTitle"
      @click="emit('move')"
    >
      Move
    </AppButton>

    <!-- Forget is gated on the rows' STATE, not on how many are selected: it is
         offered only when every selected model has already lost its files.
         Disabled with the reason in the tooltip rather than hidden, or the
         reader learns nothing about why the verb they came for is absent. -->
    <AppButton
      size="sm"
      variant="danger"
      icon-left="delete-outline"
      :disabled="!forgettable.length"
      :title="forgetTitle"
      @click="emit('forget')"
    >
      Forget
    </AppButton>
  </div>
</template>

<script setup>
// The verb layer's control surface (shelf plan F3).
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

import { computed } from "vue";

import AddToEntityControl from "../widgets/AddToEntityControl.vue";
import AppButton from "../widgets/AppButton.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { formatModelSize, movableCopies } from "../../utils/modelShelf";

const emit = defineEmits([
  "rename",
  "set-base-model",
  "set-kind",
  "stack",
  "set-icon",
  "clear-icons",
  "move",
  "forget",
]);

const store = useModelShelfStore();
const folders = useModelFoldersStore();
const moves = useModelMovesStore();

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
 * The count and what it weighs, in the separator the app already uses.
 *
 * The size is what makes a bulk verb reviewable before it runs: "Forget these
 * 40" says nothing about what is being reclaimed and "12.4 GB" does. Dropped
 * rather than shown as `0 B` when nothing in the selection has a recorded size,
 * because a shelf that has not been hashed yet would otherwise claim the
 * selection is empty.
 */
const countLabel = computed(() => {
  const n = store.selectedRows.length;
  const head = `${n.toLocaleString()} ${n === 1 ? "model" : "models"} selected`;
  const size = formatModelSize(selectedBytes.value);
  return selectedBytes.value && size ? `${head} · ${size}` : head;
});

/**
 * The selected models that have already lost every copy.
 *
 * `missing` is a fact (the folder was readable and the file was not in it);
 * `present` and `unreachable` both mean the bytes may still be out there, and
 * the second is the dangerous one — an unplugged drive must never be read as a
 * deletion. The server enforces exactly this; the bar only stops the owner
 * pressing a button that would come back refused.
 */
const forgettable = computed(() =>
  store.selectedRows.filter(
    (row) => row.locState === "missing" || row.locState === "forgotten",
  ),
);

const renameTitle = computed(() =>
  store.selectedRows.length === 1
    ? "Rename this model"
    : "Select one model to rename it",
);

const forgetTitle = computed(() => {
  if (!forgettable.value.length) {
    return "Only models whose files are gone can be forgotten";
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
 * worker has not reached yet has none, so there is nothing to write against —
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

/** `model_folder.id` to the folder row, for `movableCopies`' folder rules. */
const foldersById = computed(
  () => new Map(folders.folders.map((folder) => [Number(folder.id), folder])),
);

/**
 * The copies in the selection a move could pick up.
 *
 * Gated per COPY and not per model, so a model with one file on an unplugged
 * NAS and another on this disk IS movable — its present copy is. What the
 * button acts on and what the tooltip counts are the same list, and the view
 * recomputes it for the dialog rather than this being handed up, because a drop
 * onto a folder header has to reach the same list without a selection.
 */
const movable = computed(
  () => movableCopies(store.selectedRows, foldersById.value).items,
);

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
 * Why this selection cannot become one training run, or `""` when it can.
 *
 * Stack is the manual counterpart to the toolbar's detection sweep, which
 * proposes only files differing by a training step: a run the detector cannot
 * name is otherwise ungroupable, and there is no other way to say "these are
 * one run".
 *
 * Every gate the route enforces (`services/stack_detector.apply_stack`) is
 * checked here, so the button is never offered where it could only come back
 * refused: two or more models, adapters only, none already in a stack, each
 * with a copy actually present, and ONE folder holding all of them — a run is
 * files that sit together, and stacking across folders would invent one and put
 * its members on two drives.
 *
 * The gate and its sentence are ONE computed rather than a boolean beside a
 * message that has to be kept in step with it. Written as two, the tooltip
 * named the shared-folder rule for every refusal the boolean made after the
 * cheap checks — so a selection blocked by an unplugged drive was told its files
 * were in different folders, which is a different fact and sends the reader to
 * fix the wrong thing.
 */
const stackRefusal = computed(() => {
  const rows = store.selectedRows;
  if (rows.length < 2) {
    return "Select the files of one training run to group them";
  }
  if (rows.some((row) => row.stack_id != null)) {
    return "Something here is already part of a run";
  }
  if (rows.some((row) => row.file_kind !== "adapter")) {
    return "Only adapters are training runs";
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
      return "A run is files that sit together, so they must all be in one folder";
    }
  }
  return "";
});

const stackable = computed(() => !stackRefusal.value);

const stackTitle = computed(
  () =>
    stackRefusal.value ||
    `Group these ${store.selectedRows.length.toLocaleString()} files into one run`,
);

/** The selected models that actually have an icon to clear. */
const withIcons = computed(() =>
  store.selectedRows.filter((row) => row.icon_sha256),
);

const iconTitle = computed(() =>
  store.selectedRows.length === 1
    ? "Give this model a mark of its own"
    : "Select one model to give it an icon",
);

const clearIconTitle = computed(() =>
  withIcons.value.length === 1
    ? "Clear this model's icon"
    : `Clear the icon on ${withIcons.value.length} models`,
);

function onAttach(payload, attach) {
  return store.setAttachment({ ...payload, attach });
}

function clear() {
  store.clearSelection();
}

defineExpose({
  forgettable,
  assignable,
  membership,
  movable,
  selectedBytes,
  stackable,
  withIcons,
});
</script>

<style scoped>
/* Sits between the toolbar and the list rather than floating over it: the list
   is what the selection was made in, and a floating bar would cover the rows a
   reader checks before pressing a verb. */
.shelf-selbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-4);
  margin-bottom: var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-primary), 0.1);
  border: 1px solid rgba(var(--v-theme-primary), 0.35);
}

.shelf-selbar-count {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-background));
  white-space: nowrap;
}

.shelf-selbar-clear {
  font-size: var(--text-sm);
}

.shelf-selbar-spacer {
  flex: 1 1 auto;
}
</style>
