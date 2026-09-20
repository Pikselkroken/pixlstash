<template>
  <AppDialog
    :open="open"
    title="Keep one copy"
    :subtitle="subtitle"
    @close="emit('close')"
  >
    <p class="mcd-note">
      The same file is on this machine
      {{ copies.length }} times. Choose the one to keep; the others go to your
      {{ trash }}. The model stays on the shelf with everything you recorded
      about it, and anything you run through PixlStash keeps working.
    </p>

    <!-- A radio group, and nothing is pre-selected: the wrong default here is a
         20 GB redownload, so the shelf does what it does everywhere else and
         picks nothing for the reader. -->
    <fieldset class="mcd-copies">
      <legend class="mcd-legend">Keep</legend>
      <label
        v-for="(copy, index) in copies"
        :key="`${copy.folder_id}:${copy.relpath}`"
        class="mcd-copy"
      >
        <input
          :ref="(el) => index === 0 && (firstFieldEl = el)"
          v-model="keeper"
          type="radio"
          name="merge-keeper"
          :value="`${copy.folder_id}:${copy.relpath}`"
          :disabled="working"
        />
        <span class="mcd-path">{{ copyPath(copy) }}</span>
      </label>
    </fieldset>

    <p v-if="checking" class="mcd-note" role="status">
      Checking what your ComfyUI reads…
    </p>

    <!-- The warning that has to arrive BEFORE the bytes go. It is the delete's
         side of the rule the submit-time swap obeys: ComfyUI's own file list is
         what says whether that install can load the copy being removed. -->
    <p v-else-if="comfyuiWarning" class="mcd-warning" role="status">
      {{ comfyuiWarning }}
    </p>

    <p v-if="refusal" class="mcd-warning" role="status">{{ refusal }}</p>

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
        Move the others to {{ trash }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
// Keep one copy of a duplicated model and remove the rest (#1439).
//
// The shelf already surfaces duplicates - `Show → Copies → Only duplicates`, and
// a `copies` count on the row - and this is the verb that acts on one. It is a
// per-copy delete, and the only one: `POST /model-files/delete` removes EVERY
// copy and then the row.
//
// Two things make it safe rather than merely convenient, and both are the
// server's. The request names the copy to KEEP, so nothing here can empty a
// model; and the removed copies keep their shelf rows, so the record of which
// files were one model outlives the files - a recipe naming the copy that went
// still resolves, and a run through PixlStash is substituted onto the copy that
// is left.
//
// The dry run is not a nicety. It is how the ComfyUI warning arrives before the
// delete instead of after it, so it runs on every change of the keeper and the
// confirm waits for it.

import { computed, nextTick, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { mergeModelCopies } from "../../api/modelFiles";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { trashName } from "../../utils/modelShelf";

const props = defineProps({
  open: { type: Boolean, default: false },
  /**
   * The shelf row, with its whole `locations` array.
   *
   * Taken from `selectedRows` rather than from a folder-grouped draw: that one
   * is narrowed to the copy its folder holds, and a dialog offering one choice
   * out of two would be the one screen that cannot do its job.
   */
  row: { type: Object, default: null },
});
const emit = defineEmits(["close"]);

const store = useModelShelfStore();

const keeper = ref(null);
const working = ref(false);
const checking = ref(false);
const plan = ref(null);
const firstFieldEl = ref(null);

const trash = computed(() => plan.value?.trash_name || trashName());

/** The copies that are actually on the disk - the only ones there is a choice between. */
const copies = computed(() =>
  (props.row?.locations || []).filter((loc) => loc?.state === "present"),
);

const subtitle = computed(() => props.row?.name || props.row?.filename || "");

const chosen = computed(() =>
  copies.value.find(
    (copy) => `${copy.folder_id}:${copy.relpath}` === keeper.value,
  ),
);

const canSubmit = computed(
  () => !working.value && !checking.value && !!chosen.value,
);

/** One copy's path, joined the way the shelf's tooltips join one. */
function copyPath(copy) {
  const folder = String(copy?.folder_path || "");
  const relpath = String(copy?.relpath || "");
  const windows = folder.includes("\\") && !folder.includes("/");
  const sep = windows ? "\\" : "/";
  const tail = windows ? relpath.replace(/\//g, "\\") : relpath;
  return `${folder.replace(/[/\\]+$/, "")}${sep}${tail.replace(/^[/\\]+/, "")}`;
}

/**
 * What the reader has to know before agreeing, in ComfyUI's own words.
 *
 * Two different sentences, because they are two different situations. If that
 * ComfyUI also lists the copy being kept, PixlStash can put the run on it and
 * only a graph queued inside ComfyUI itself breaks. If it does not, nothing can
 * be substituted and every graph naming the file stops working there.
 */
const comfyuiWarning = computed(() => {
  const reads = plan.value?.comfyui_reads || [];
  if (!reads.length) return "";
  if (reads.some((item) => !item.keeper_advertised)) {
    return (
      "Your ComfyUI reads the copy you are removing and not the one you are " +
      "keeping, so PixlStash cannot put a run on it either. Any workflow " +
      "naming that file will stop working there until you rescan or move it."
    );
  }
  return (
    "Your ComfyUI reads the copy you are removing. Workflows you run through " +
    "PixlStash are put on the copy you keep; one you open in ComfyUI and queue " +
    "there still names the file that went."
  );
});

/** The server's refusal, said before the press rather than after it. */
const refusal = computed(() => {
  const reason = plan.value?.refused?.[0]?.reason;
  if (!reason) return "";
  if (reason === "keeper_not_present") {
    return "That copy is not on the disk any more. Rescan the folder, or keep another one.";
  }
  if (reason === "not_a_user_folder") {
    return "One of the other copies is in a folder PixlStash keeps for itself and will not remove. Keep that one instead.";
  }
  if (reason === "unreachable_copy") {
    return "Another copy is on a drive that is not plugged in, so nothing will be removed.";
  }
  if (reason === "not_a_duplicate") {
    return "There is only one copy left, so there is nothing to merge.";
  }
  return "This cannot be merged right now; the shelf will say why if you try.";
});

/**
 * Ask the server what it would do, removing nothing.
 *
 * Re-run on every change of the keeper, because both answers depend on which
 * copy stays: the refusal is about the copies that would go, and so is the
 * ComfyUI warning.
 */
async function check() {
  const copy = chosen.value;
  plan.value = null;
  if (!copy) return;
  checking.value = true;
  try {
    plan.value = await mergeModelCopies(
      [
        {
          model_id: props.row.id,
          folder_id: copy.folder_id,
          relpath: copy.relpath,
        },
      ],
      { dryRun: true },
    );
  } catch (err) {
    // Not a notice: the dialog is open and the press is still ahead, so the
    // server's own answer to the real call is the one the reader should see.
    console.warn("[MergeCopiesDialog] could not plan the merge", err);
  } finally {
    checking.value = false;
  }
}

watch(keeper, check);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    working.value = false;
    checking.value = false;
    plan.value = null;
    // Nothing pre-selected, every time it opens.
    keeper.value = null;
    await nextTick();
    firstFieldEl.value?.focus();
  },
  { immediate: true },
);

async function submit() {
  if (!canSubmit.value) return;
  const copy = chosen.value;
  working.value = true;
  const done = await store.mergeCopies([
    {
      model_id: props.row.id,
      folder_id: copy.folder_id,
      relpath: copy.relpath,
    },
  ]);
  working.value = false;
  if (done) emit("close");
}
</script>

<style scoped>
.mcd-note {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}

/* The hue drawn as TEXT, which is `surface-warning` and never the fill: the
   fill is 2.1:1 on a light canvas (`frontend/src/main.js`). */
.mcd-warning {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-surface-warning));
}

.mcd-copies {
  margin: 0;
  padding: 0;
  border: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.mcd-legend {
  padding: 0;
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mcd-copy {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.25);
  background: rgb(var(--v-theme-surface));
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
  cursor: pointer;
}

.mcd-copy:hover {
  background: var(--hover-wash);
}

.mcd-copy:focus-within {
  box-shadow: var(--focus-ring-inset);
}

.mcd-path {
  overflow-wrap: anywhere;
}
</style>
