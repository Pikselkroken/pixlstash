<template>
  <AppDialog
    :open="open"
    title="Move folder"
    :subtitle="source.name"
    @close="emit('close')"
    @accept="submit"
  >
    <!-- Step one: where it goes. The tree picks, and the folder itself and
         everything under it are refused. -->
    <div v-if="!facts" class="fmd-field">
      <span class="fmd-label" aria-hidden="true">Move it into</span>
      <div class="fmd-tree">
        <ModelFolderTree
          ref="treeRef"
          mode="pick"
          label="Move it into"
          :selected="destination"
          :disabled="refused"
          @select="pick"
        />
      </div>
      <ModelFolderNewRow
        v-if="destination"
        :folder-id="destination.folderId"
        :path="destination.path"
        :label="destinationLabel"
        :reason="gates.folderVerbReason({ id: destination.folderId })"
        @created="onCreated"
      />
    </div>

    <!-- Step two, only for a move across drives: what it will cost, before it
         starts. A move on one drive never gets here; it is a rename. -->
    <p v-else class="fmd-lead" data-testid="folder-move-facts">
      Move {{ source.name }} into {{ destinationTrail }} —
      {{ modelsWords }}, {{ formatModelSize(facts.bytes) }}. Another drive, so
      copied, checked, then removed. Frees {{ formatModelSize(facts.bytes) }} on
      {{ sourceDrive }}.
    </p>

    <p v-if="refusal" class="fmd-note" role="alert">{{ refusal }}</p>

    <template #footer>
      <AppButton variant="ghost" key-hint="esc" @click="emit('close')">
        Cancel
      </AppButton>
      <AppButton
        ref="moveButton"
        variant="primary"
        key-hint="enter"
        :loading="working"
        :disabled="!canSubmit"
        @click="submit"
      >
        {{ confirmLabel }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
// Moving a folder in the Models tree (#2206).
//
// Two steps, and only one of them is usually shown. The tree picks the place;
// the server's dry run then says whether it is a rename on one drive, which
// starts at once, or a copy across drives, which states its cost first. A drop
// that already knows it is a copy opens this on the second step, with its
// facts in `plan`.
//
// The dialog never moves anything itself: the move is the moves store's job,
// which outlives this dialog.

import { computed, nextTick, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import ModelFolderNewRow from "./ModelFolderNewRow.vue";
import ModelFolderTree from "./ModelFolderTree.vue";
import {
  askFolderMove,
  folderDropRefused,
} from "../../composables/useModelFolderMove";
import { destinationName } from "../../composables/useModelCopyDrop";
import { useModelFolderGates } from "../../composables/useModelFolderGates";
import { errorMessage } from "../../utils/apiError";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { formatModelSize } from "../../utils/modelShelf";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `{folderId, path, name}` of the directory being moved. */
  source: { type: Object, default: () => ({ folderId: 0, path: "", name: "" }) },
  /** `{folderId, path}` chosen before the dialog opened, from a drop. */
  initialDestination: { type: Object, default: null },
  /** The dry run's facts, when a drop already read them. Null from the menu. */
  plan: { type: Object, default: null },
});

const emit = defineEmits(["close", "started"]);

const folders = useModelFoldersStore();
const moves = useModelMovesStore();
const gates = useModelFolderGates();

/** `{folderId, path}` picked in the tree, or null. */
const destination = ref(null);
/** The dry run's facts once read; the dialog is on its second step while set. */
const facts = ref(null);
const working = ref(false);
const refusal = ref("");
const treeRef = ref(null);
const moveButton = ref(null);

/** Refused as a place for this folder: itself, its own, and the one it is in. */
function refused(node) {
  return folderDropRefused(props.source, node);
}

function pick(node) {
  destination.value = { folderId: node.folderId, path: node.path };
  refusal.value = "";
}

/** A folder made in the destination: reveal it, and pick it. */
function onCreated({ folderId, path }) {
  treeRef.value?.reveal(folderId, destination.value?.path ?? "", path);
  destination.value = { folderId, path };
}

const destinationLabel = computed(() =>
  destination.value
    ? destinationName(folders.folders, destination.value)
    : "",
);

/** "Drive B › models", the destination as the sentence reads it. */
const destinationTrail = computed(() => destinationLabel.value.replace(/\//g, " › "));

const modelsWords = computed(() => {
  const n = Number(facts.value?.models) || 0;
  const other = Number(facts.value?.other_files) || 0;
  const models = `${n.toLocaleString()} ${n === 1 ? "model" : "models"}`;
  if (!other) return models;
  return `${models} and ${other.toLocaleString()} other ${other === 1 ? "file" : "files"}`;
});

/** The drive the folder comes off, which is the one the move frees. */
const sourceDrive = computed(() => {
  const device = folders.deviceByFolderId.get(Number(props.source.folderId));
  return device?.label || device?.mount_point || "its drive";
});

const confirmLabel = computed(() =>
  moves.busy ? "A move is already running" : "Move",
);

const canSubmit = computed(
  () => !working.value && !moves.busy && destination.value != null,
);

// Each time it opens, start from what it was given. From a drop the destination
// and the facts are already known, and focus waits on Move so Enter confirms.
// From the menu nothing is chosen, and focus goes to the tree.
watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    working.value = false;
    refusal.value = "";
    facts.value = props.plan;
    const initial = props.initialDestination;
    destination.value = initial
      ? { folderId: Number(initial.folderId), path: initial.path ?? "" }
      : null;
    await nextTick();
    if (facts.value) moveButton.value?.$el?.focus?.();
    else treeRef.value?.$el?.querySelector('[tabindex="0"]')?.focus();
  },
  { immediate: true },
);

async function submit() {
  if (!canSubmit.value) return;
  working.value = true;
  refusal.value = "";
  try {
    if (facts.value) {
      await moves.moveFolder(props.source, destination.value);
      emit("started");
      emit("close");
      return;
    }
    const { plan, started } = await askFolderMove(
      props.source,
      destination.value,
    );
    if (started) {
      emit("started");
      emit("close");
      return;
    }
    // Across drives: the facts are read, and the next press is the move.
    facts.value = plan;
    await nextTick();
    moveButton.value?.$el?.focus?.();
  } catch (err) {
    refusal.value = errorMessage(err, "Could not move that folder.");
  } finally {
    working.value = false;
  }
}
</script>

<style scoped>
.fmd-field {
  display: block;
}

.fmd-label {
  display: block;
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The tree scrolls inside the dialog rather than growing it past the viewport:
   a machine with several drives lists many folders. */
.fmd-tree {
  max-height: 320px;
  overflow-y: auto;
  border-radius: var(--radius-sm);
  border: 1px solid rgb(var(--v-theme-divider));
}

.fmd-lead {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.fmd-note {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-error));
}
</style>
