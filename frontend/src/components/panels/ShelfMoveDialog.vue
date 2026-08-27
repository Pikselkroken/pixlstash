<template>
  <AppDialog
    :open="open"
    title="Move files"
    :subtitle="subtitle"
    :width="520"
    @close="emit('close')"
  >
    <label class="smd-field">
      <span class="smd-label">Move them to</span>
      <!-- A native select, not a combobox: the destination list is the
           registered folders, which is a handful and never grows to the
           thousands the entity pickers deal with. Native gets type-ahead,
           the platform's own keyboard model and a mobile picker for free. -->
      <select
        ref="firstFieldEl"
        v-model="destinationId"
        class="smd-select"
        :disabled="working"
      >
        <option
          v-for="folder in destinations"
          :key="folder.id"
          :value="folder.id"
        >
          {{ folderLabel(folder) }}
        </option>
      </select>
    </label>

    <p v-if="!destinations.length" class="smd-note" role="status">
      There is nowhere to move these. Register another model folder first.
    </p>

    <!-- What the move will actually cost, before it starts. The server plans
         the whole batch inside the POST and refuses it whole, so this is not
         the safety net; it is the difference between pressing Move on a rename
         and pressing it on 40 minutes of copying across a USB drive. -->
    <dl v-else class="smd-facts">
      <div class="smd-fact">
        <dt>Files</dt>
        <dd>{{ itemCount.toLocaleString() }}</dd>
      </div>
      <div class="smd-fact">
        <dt>Size</dt>
        <dd>{{ formatModelSize(totalBytes) }}</dd>
      </div>
      <div class="smd-fact">
        <dt>How</dt>
        <dd>{{ mechanism }}</dd>
      </div>
    </dl>

    <p v-if="alreadyThere" class="smd-note" role="status">
      {{ alreadyThere }}
    </p>

    <template #footer>
      <AppButton variant="ghost" key-hint="esc" @click="emit('close')">
        Cancel
      </AppButton>
      <AppButton
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
// The shelf's move destination picker (shelf plan F4).
//
// Drag and drop is the fast path and this is the one that always works: the
// definition of done requires the whole shelf to be operable from the keyboard,
// and a drag is not. It is also where the move gets stated in numbers before it
// starts, which a drop onto a folder header cannot do.
//
// The dialog never moves anything itself. `useModelMovesStore` owns the job,
// because the job outlives this dialog: the owner starts 400 files onto another
// drive and closes the panel, and the server keeps copying either way.

import { computed, nextTick, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { formatModelSize } from "../../utils/modelShelf";

const props = defineProps({
  open: { type: Boolean, default: false },
  /**
   * The copies to move, `{folder_id, relpath}` each.
   *
   * Supplied by the host rather than derived from the selection here, because
   * a drop onto a folder header moves the same copies without a selection
   * having to exist, and both paths deserve the same summary.
   */
  items: { type: Array, default: () => [] },
  /** Bytes the move will shift, for the summary. */
  totalBytes: { type: Number, default: 0 },
  /**
   * The destination a drop already chose, or null when a button opened this.
   *
   * Seeded into the select rather than skipping it: a drop still gets the
   * numbers and the Move press, so a 438 GB copy is never one slip of the
   * pointer away from starting, and the destination is still correctable
   * without starting the gesture again.
   */
  destinationFolderId: { type: Number, default: null },
});
const emit = defineEmits(["close"]);

const folders = useModelFoldersStore();
const moves = useModelMovesStore();

const destinationId = ref(null);
const working = ref(false);
const firstFieldEl = ref(null);

const itemCount = computed(() => props.items.length);

/**
 * The folders a move may be sent to.
 *
 * Two exclusions, and the server only enforces the first. A `source` folder is
 * an ai-toolkit output root: it is taken from, never written into, and
 * `ModelMover.plan` refuses it. An `external` folder is one PixlStash shares
 * with other software (the HuggingFace cache, insightface's store) - the server
 * would accept the write, and it is still not ours to put files in.
 */
const destinations = computed(() =>
  folders.folders.filter(
    (folder) => folder.kind !== "source" && folder.movable !== "external",
  ),
);

const subtitle = computed(
  () =>
    `${itemCount.value.toLocaleString()} ${itemCount.value === 1 ? "file" : "files"}`,
);

const confirmLabel = computed(() =>
  moves.busy ? "A move is already running" : "Move",
);

function folderLabel(folder) {
  return folder.kind === "managed"
    ? `${folder.path} (PixlStash's own store)`
    : folder.path;
}

/**
 * Whether this will be a rename or a real copy, which is the whole difference
 * between a second and forty minutes.
 *
 * Measured on the DRIVE, not on the path: two folders on one disk rename even
 * when their paths share nothing, and two paths that look alike can sit on
 * different mounts. `deviceByFolderId` carries the `st_dev` the backend
 * measured, so this asks the same question `model_mover.same_device` does.
 * Unmeasured devices fall through to the honest "may need copying".
 */
const mechanism = computed(() => {
  const byId = folders.deviceByFolderId;
  const target = byId?.get(Number(destinationId.value));
  // `device_id` is null when the drive could not be measured, and an unmeasured
  // drive must not be guessed as "same": that would promise instant on a move
  // that copies. Both directions fall through to the honest answer.
  if (!target?.device_id) return "May need copying, so it can take a while";
  const sources = new Set(
    props.items.map(
      (item) => byId?.get(Number(item.folder_id))?.device_id ?? null,
    ),
  );
  if (sources.size === 1 && sources.has(target.device_id)) {
    return "Renamed on the same drive, so it is instant";
  }
  return "Copied to another drive, verified, then removed";
});

/** How many of the chosen copies are already in the destination. */
const alreadyThere = computed(() => {
  const already = props.items.filter(
    (item) => item.folder_id === destinationId.value,
  ).length;
  if (!already) return "";
  if (already === itemCount.value) {
    return "Every one of these is already in that folder, so nothing will move.";
  }
  // Skipped rather than refused, on purpose: a mixed selection dropped onto a
  // folder should do the obvious thing rather than reject the whole gesture.
  return `${already.toLocaleString()} of these are already in that folder and will be left alone.`;
});

const canSubmit = computed(
  () =>
    !working.value &&
    !moves.busy &&
    destinationId.value != null &&
    itemCount.value > 0,
);

// Seed the destination on open. The managed store is the default because it is
// the one folder ruled to always exist, so it is the only choice that is never
// wrong on a fresh install.
watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    working.value = false;
    const dropped = destinations.value.find(
      (f) => f.id === props.destinationFolderId,
    );
    const managed = destinations.value.find((f) => f.kind === "managed");
    destinationId.value =
      dropped?.id ?? managed?.id ?? destinations.value[0]?.id ?? null;
    await nextTick();
    firstFieldEl.value?.focus();
  },
  { immediate: true },
);

async function submit() {
  if (!canSubmit.value) return;
  working.value = true;
  const started = await moves.start(destinationId.value, props.items);
  working.value = false;
  // The selection is deliberately KEPT. A move changes where the files are and
  // not what the models are, so under `Group by: folder` the selected rows
  // reappear under the destination - which is the answer to "where did they
  // go", and it leaves the reader able to follow with another verb.
  if (started) emit("close");
}
</script>

<style scoped>
.smd-field {
  display: block;
  margin-bottom: var(--space-4);
}

.smd-label {
  display: block;
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface-variant));
}

.smd-select {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.25);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
}

.smd-facts {
  display: flex;
  gap: var(--space-5);
  margin: 0 0 var(--space-3);
}

.smd-fact dt {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

.smd-fact dd {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.smd-note {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}
</style>
