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
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const emit = defineEmits(["rename", "set-base-model", "set-kind", "forget"]);

const store = useModelShelfStore();

const countLabel = computed(() => {
  const n = store.selectedRows.length;
  return `${n.toLocaleString()} ${n === 1 ? "model" : "models"} selected`;
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

function onAttach(payload, attach) {
  return store.setAttachment({ ...payload, attach });
}

function clear() {
  store.clearSelection();
}

defineExpose({ forgettable, assignable, membership });
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
