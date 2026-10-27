<template>
  <AppDialog
    :open="open"
    title="Move files"
    :subtitle="subtitle"
    @close="emit('close')"
    @accept="submit"
  >
    <div class="smd-field">
      <span class="smd-label" aria-hidden="true">Move them to</span>
      <!-- The Folders tree, picking (#2204): any subfolder is a destination,
           and each drive shows its free space. Locked folders and the folder
           every file is already in are refused. -->
      <div ref="treeBoxEl" class="smd-tree">
        <ModelFolderTree
          mode="pick"
          label="Move them to"
          :selected="destination"
          :disabled="refused"
          @select="pick"
        />
      </div>
    </div>

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
import ModelFolderTree from "./ModelFolderTree.vue";
import { DOWNLOADS_KIND, SOURCE_KIND } from "../../api/modelFolders";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import {
  allAlreadyIn,
  formatModelSize,
  inFolderScopeOf,
} from "../../utils/modelShelf";

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
   * `{folderId, path}` chosen before the dialog opened: the folder a drop or
   * a paste named. Null from *Move to…*, which starts with nothing chosen so
   * the destination is always the reader's own pick.
   */
  initialDestination: { type: Object, default: null },
});
/** `started` once the server has accepted the move. */
const emit = defineEmits(["close", "started"]);

const folders = useModelFoldersStore();
const moves = useModelMovesStore();

/** `{folderId, path}` picked in the tree, or null. */
const destination = ref(null);
const working = ref(false);
const treeBoxEl = ref(null);
const moveButton = ref(null);

const itemCount = computed(() => props.items.length);

/**
 * The folders a move may be sent to: the ones the tree lists (not an
 * ai-toolkit output root nor Downloads) that are not locked. The server
 * refuses the rest too.
 */
const destinations = computed(() =>
  folders.folders.filter(
    (folder) =>
      folder.kind !== SOURCE_KIND &&
      folder.kind !== DOWNLOADS_KIND &&
      folder.movable === "per_item",
  ),
);

/** A tree row refused as a destination: every file is already in it. */
function refused(entry) {
  return allAlreadyIn(props.items, entry);
}

function pick(node) {
  destination.value = { folderId: node.folderId, path: node.path };
}

const subtitle = computed(
  () =>
    `${itemCount.value.toLocaleString()} ${itemCount.value === 1 ? "file" : "files"}`,
);

const confirmLabel = computed(() =>
  moves.busy ? "A move is already running" : "Move",
);

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
  const target = byId?.get(Number(destination.value?.folderId));
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

/** How many of the chosen copies are already directly in the destination. */
const alreadyThere = computed(() => {
  if (!destination.value) return "";
  const already = props.items.filter((item) =>
    inFolderScopeOf(item, destination.value),
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
    destination.value != null &&
    itemCount.value > 0,
);

// Seed the destination on open: the folder a drop or a paste named, else
// nothing, so *Move to…* never sends files somewhere the reader did not pick.
// (It used to pre-select the managed store, which takes no moves now.) With a
// destination already chosen, focus waits on Move and Enter confirms; without
// one it goes to the tree.
watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    working.value = false;
    const initial = props.initialDestination;
    destination.value = initial
      ? { folderId: Number(initial.folderId), path: initial.path ?? "" }
      : null;
    await nextTick();
    if (destination.value) moveButton.value?.$el?.focus?.();
    else treeBoxEl.value?.querySelector('[tabindex="0"]')?.focus();
  },
  { immediate: true },
);

async function submit() {
  if (!canSubmit.value) return;
  working.value = true;
  const { folderId, path } = destination.value;
  const started = await moves.start(folderId, props.items, path);
  working.value = false;
  // The selection is deliberately KEPT. A move changes where the files are and
  // not what the models are, so under `Group by: folder` the selected rows
  // reappear under the destination - which is the answer to "where did they
  // go", and it leaves the reader able to follow with another verb.
  if (started) {
    emit("started");
    emit("close");
  }
}
</script>

<style scoped>
.smd-field {
  display: block;
}

.smd-label {
  display: block;
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface-variant));
}

/* The tree scrolls inside the dialog rather than growing it past the
   viewport: a machine with several drives lists many folders. */
.smd-tree {
  max-height: 320px;
  overflow-y: auto;
  border-radius: var(--radius-sm);
  border: 1px solid rgb(var(--v-theme-divider));
}

.smd-facts {
  display: flex;
  gap: var(--space-5);
  margin: 0;
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
