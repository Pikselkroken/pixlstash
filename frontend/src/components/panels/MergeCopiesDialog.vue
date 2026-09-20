<template>
  <AppDialog
    :open="open"
    title="Keep one copy"
    :subtitle="subtitle"
    :persistent="working"
    @close="close"
  >
    <p class="mcd-note">
      You have {{ copies.length }} copies of this model. Pick the one to keep;
      the others go to your {{ trash }}. The model itself stays on the shelf.
    </p>

    <!-- A radio group, and nothing is pre-selected: the wrong default here is a
         20 GB redownload, so the shelf does what it does everywhere else and
         picks nothing for the reader. -->
    <fieldset class="mcd-copies">
      <legend class="mcd-legend">Keep</legend>
      <label
        v-for="(copy, index) in copies"
        :key="keyOf(copy)"
        class="mcd-copy"
        :class="{ 'mcd-copy--going': keeper && keyOf(copy) !== keeper }"
      >
        <input
          :ref="(el) => index === 0 && (firstFieldEl = el)"
          v-model="keeper"
          type="radio"
          name="merge-keeper"
          :value="keyOf(copy)"
          :disabled="working"
        />
        <span class="mcd-path">{{ copyPath(copy) }}</span>
        <!-- What happens to THIS row, on the row. The consequence used to live
             only in the paragraph above, which is what a reader skips on a
             dialog they opened deliberately - and this is the gesture where the
             thing being skipped is which file gets deleted. -->
        <span v-if="keeper" class="mcd-fate">{{
          keyOf(copy) === keeper ? "kept" : goingTo
        }}</span>
      </label>
    </fieldset>

    <!-- ONE live region, not three `v-if` siblings. A region inserted with its
         text already in it is announced unreliably, and the sentence this dialog
         exists to deliver is the one that would be lost. -->
    <p class="mcd-status" :class="{ 'mcd-status--warn': warned }" role="status">
      {{ status }}
    </p>

    <template #footer>
      <AppButton variant="ghost" key-hint="esc" @click="close">
        Cancel
      </AppButton>
      <!-- `danger`, like every other confirm in the app that destroys a file.
           The argument for `primary` - the model survives and the copy is
           recoverable - is real and still loses: it left this footer
           pixel-identical to the Move dialog's, which destroys nothing. No
           `key-hint`: `AppDialog`'s Enter fires its `accept` emit, this dialog
           does not listen for one, and wiring it would put a delete one
           reflexive Enter away from a focused radio. -->
      <AppButton
        variant="danger"
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
// files were one model outlives the files.
//
// The dry run is not a nicety. It is how the ComfyUI warning arrives before the
// delete instead of after it, so it runs on every change of the keeper, the
// confirm waits for it, and a dry run that FAILS closes the confirm rather than
// leaving it open with nothing asked.

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
const plan = ref(null);
/** The keeper a dry run is currently out for, or null when none is. */
const pending = ref(null);
/** True when the last dry run did not answer at all. */
const planFailed = ref(false);
const firstFieldEl = ref(null);
/** Where focus was when this opened, so Cancel and Escape can give it back. */
let returnFocusTarget = null;

const trash = computed(() => plan.value?.trash_name || trashName());
const goingTo = computed(() => `goes to ${trash.value}`);

/** The copies that are on the disk - the only ones there is a choice between. */
const copies = computed(() =>
  (props.row?.locations || []).filter((loc) => loc?.state === "present"),
);

const subtitle = computed(() => props.row?.name || props.row?.filename || "");

const keyOf = (copy) => `${copy?.folder_id}:${copy?.relpath}`;

const chosen = computed(() =>
  copies.value.find((copy) => keyOf(copy) === keeper.value),
);

const checking = computed(() => pending.value !== null);

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
 * Two sentences because `keeper_advertised` is two situations: ComfyUI can be
 * pointed at the copy being kept, or it cannot and nothing can be substituted
 * there at all.
 */
const comfyuiWarning = computed(() => {
  const reads = plan.value?.comfyui_reads || [];
  if (!reads.length) return "";
  if (reads.some((item) => !item.keeper_advertised)) {
    return (
      "ComfyUI uses the copy you are removing and cannot see the one you are " +
      "keeping. Workflows that name this file will stop working until you " +
      "rescan in ComfyUI."
    );
  }
  return (
    "ComfyUI uses the copy you are removing. Runs you start from PixlStash " +
    "will use the copy you keep; one you open in ComfyUI itself will not."
  );
});

/** The server's refusal, said before the press rather than after it. */
const refusal = computed(() => {
  if (planFailed.value) {
    return "PixlStash could not check this. Try again in a moment.";
  }
  const reason = plan.value?.refused?.[0]?.reason;
  if (!reason) return "";
  if (reason === "keeper_not_present") {
    return "That copy is not on your disk any more. Rescan the folder, or keep another one.";
  }
  if (reason === "keeper_is_that_copy") {
    return "These two are one file — one is a shortcut to the other. There is nothing to reclaim.";
  }
  if (reason === "not_a_user_folder") {
    return "One of the other copies is in a folder PixlStash manages. Keep that one instead.";
  }
  if (reason === "unreachable_copy") {
    return "Another copy is on a drive that is not plugged in. Plug it in first.";
  }
  if (reason === "not_a_duplicate") {
    return "There is only one copy left. Nothing to merge.";
  }
  return "PixlStash will not merge this one right now.";
});

/** Whether the line below the copies is a warning rather than a note. */
const warned = computed(() => Boolean(refusal.value || comfyuiWarning.value));

/**
 * The one live region's text.
 *
 * A single persistent region rather than three `v-if` siblings: a region
 * inserted with its text already in it is announced unreliably, and the
 * sentence this dialog exists to deliver is the one that would be lost.
 */
const status = computed(() => {
  if (checking.value) return "Checking with ComfyUI…";
  if (refusal.value) return refusal.value;
  return comfyuiWarning.value;
});

/**
 * Whether the press may go ahead.
 *
 * The dry run has to have come back - and come back at all - because until it
 * has, neither the refusal nor the ComfyUI warning has been asked. A failed
 * check is therefore a closed confirm, not an open one: the warning is the whole
 * reason for planning first, and the real call reports it only in the receipt,
 * once the files have gone.
 */
const canSubmit = computed(
  () =>
    !working.value &&
    !checking.value &&
    !planFailed.value &&
    !!chosen.value &&
    !refusal.value,
);

/**
 * Ask the server what it would do, removing nothing.
 *
 * Re-run on every change of the keeper, because both answers depend on which
 * copy stays. **The response is matched to the keeper that asked for it**: a
 * radio group is arrow-keyed, so walking four copies dispatches four requests
 * and overlap is the ordinary keyboard path rather than an edge case. Without
 * the check the last response to land wins, and a stale clean plan can enable a
 * confirm the selected keeper's own check would have refused.
 */
async function check() {
  const copy = chosen.value;
  plan.value = null;
  planFailed.value = false;
  if (!copy) {
    pending.value = null;
    return;
  }
  const key = keeper.value;
  pending.value = key;
  try {
    const answer = await mergeModelCopies(
      [
        {
          model_id: props.row.id,
          folder_id: copy.folder_id,
          relpath: copy.relpath,
        },
      ],
      { dryRun: true },
    );
    if (key !== keeper.value) return;
    plan.value = answer;
  } catch (err) {
    if (key !== keeper.value) return;
    planFailed.value = true;
    console.warn("[MergeCopiesDialog] could not plan the merge", err);
  } finally {
    // Only the newest request may clear it, or an early completion would report
    // "asked and answered" while later ones are still out.
    if (key === keeper.value) pending.value = null;
  }
}

watch(keeper, check);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    working.value = false;
    pending.value = null;
    planFailed.value = false;
    plan.value = null;
    // Nothing pre-selected, every time it opens - and the previous model's
    // warning must not be on screen against this one.
    keeper.value = null;
    returnFocusTarget =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    await nextTick();
    firstFieldEl.value?.focus();
  },
  { immediate: true },
);

/**
 * Close, and give focus back.
 *
 * Vuetify restores focus only to an `activatorEl` and only through its own
 * Escape listener, which `AppDialog` pre-empts; this dialog is opened from a
 * context menu that is gone by now, so without this Cancel, Escape and success
 * all land focus on `<body>`.
 */
async function close() {
  const target = returnFocusTarget;
  returnFocusTarget = null;
  emit("close");
  await nextTick();
  target?.focus?.();
}

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
  if (done) await close();
}
</script>

<style scoped>
.mcd-note {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mcd-status {
  margin: 0;
  min-height: 1.5em;
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}

/* The hue drawn as TEXT, which is `surface-warning` and never the fill: the
   fill is 2.1:1 on a light canvas (`frontend/src/main.js`). */
.mcd-status--warn {
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
  flex: 1;
  overflow-wrap: anywhere;
}

/* What happens to this row, on the row. Muted, because the path is the thing
   being read; the word only has to be there when the eye arrives. */
.mcd-fate {
  flex: none;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mcd-copy--going .mcd-path {
  color: rgb(var(--v-theme-on-surface-variant));
}
</style>
