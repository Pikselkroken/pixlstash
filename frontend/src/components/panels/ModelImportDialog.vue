<template>
  <AppDialog
    :open="open"
    title="Import from ai-toolkit"
    :subtitle="subtitle"
    :width="820"
    @close="emit('close')"
  >
    <label class="mid-field">
      <span class="mid-label">Look in</span>
      <select
        ref="firstFieldEl"
        v-model="sourceId"
        class="mid-select"
        :disabled="working"
      >
        <option v-for="folder in sources" :key="folder.id" :value="folder.id">
          {{ folder.path }}
        </option>
      </select>
    </label>

    <p v-if="!sources.length" class="mid-note" role="status">
      No ai-toolkit output folder is registered. Add one as a
      <strong>source</strong> folder and its runs will show up here.
    </p>
    <p v-else-if="loading" class="mid-note" role="status">Reading runs…</p>
    <p v-else-if="error" class="mid-note" role="alert">{{ error }}</p>
    <p v-else-if="!runs.length" class="mid-note" role="status">
      Nothing in that folder looks like a training run.
    </p>

    <!-- One card per run. `role="radiogroup"`, because an import takes ONE run:
         the destination, the step selection and the receipt are all per-run,
         and letting two be ticked would promise a batch nothing implements. -->
    <div
      v-else
      class="mid-grid"
      role="radiogroup"
      aria-label="Training runs"
      @keydown="onGridKeydown"
    >
      <div
        v-for="(run, index) in runs"
        :key="run.name"
        class="mid-card"
        :class="{ 'mid-card--picked': run.name === pickedName }"
        role="radio"
        :aria-checked="run.name === pickedName"
        :tabindex="run.name === (pickedName || runs[0].name) ? 0 : -1"
        :data-run-index="index"
        @click="pick(run)"
        @keydown.enter.prevent="pick(run)"
        @keydown.space.prevent="pick(run)"
      >
        <!-- The first prompt at the run's highest step: what it has learned so
             far, on a prompt that stays the same across runs so two cards are
             comparable (see `coverOf`). `loading="lazy"`, because a run carries
             up to 130 samples and only the visible cards need to fetch. -->
        <img
          v-if="coverOf(run)"
          class="mid-card-preview"
          :src="coverOf(run)"
          alt=""
          loading="lazy"
        />
        <div v-else class="mid-card-preview mid-card-preview--none">
          <v-icon size="20">mdi-image-off-outline</v-icon>
          <span>No previews</span>
        </div>

        <div class="mid-card-body">
          <span class="mid-card-name">{{ run.name }}</span>
          <span class="mid-card-meta">
            <span>{{ stepCount(run) }}</span>
            <span>{{ run.base_model || "Base model not recorded" }}</span>
            <span v-if="run.rank">rank {{ run.rank }}</span>
          </span>
          <!-- An unconfirmed cover is a fact about the run, not a warning about
               the user: ai-toolkit writes a bare final file at the end, so a run
               without one either is still training or was interrupted. The
               highest step is then the best available answer, not a certain
               one, and saying so is the difference between a surprise and a
               choice. -->
          <span v-if="!hasBareFinal(run)" class="mid-card-note">
            No final file yet, so the newest step is the cover.
          </span>
          <span v-if="run.config_error" class="mid-card-note">
            Could not read its config, so the base model and triggers are
            unknown. The steps still import.
          </span>
        </div>
      </div>
    </div>

    <!-- The step picker appears only once a run is picked: before that it would
         be a list of nothing, and after it is the only remaining decision. -->
    <fieldset v-if="picked" class="mid-field mid-steps">
      <legend class="mid-label">Take which checkpoints</legend>
      <label
        v-for="cp in picked.checkpoints"
        :key="cp.filename"
        class="mid-step"
      >
        <input
          v-model="chosenSteps"
          type="checkbox"
          :value="cp.step ?? null"
          :disabled="working"
        />
        <span>{{ cp.step === null ? "Final" : `Step ${cp.step}` }}</span>
        <span class="mid-step-size">{{ formatModelSize(cp.size) }}</span>
      </label>
    </fieldset>

    <label v-if="picked" class="mid-field">
      <span class="mid-label">Put them in</span>
      <select v-model="destinationId" class="mid-select" :disabled="working">
        <option
          v-for="folder in destinations"
          :key="folder.id"
          :value="folder.id"
        >
          {{ folder.path }}
        </option>
      </select>
    </label>

    <!-- The one thing about an import that cannot be undone, said before it
         starts rather than in the receipt. It follows the SOURCE folder's own
         setting, so it is a property of where the run lives and not a choice
         being made here. -->
    <p v-if="picked && deletesSource" class="mid-warning" role="status">
      This folder is set to remove a run after importing it, so
      {{ picked.name }} will be gone from disk once its files have landed.
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
// The ai-toolkit import card grid (shelf plan F6).
//
// Built on the promise the listing route makes: describing a run costs nothing
// and changes nothing, so the whole grid — names, steps, sizes, previews, what
// the config says it trained against — is drawn before the user commits to any
// of it. Nothing here hashes, copies or writes until Import is pressed.
//
// One run at a time, deliberately. The destination, the step selection and the
// receipt are all per-run, so a multi-select would promise a batch that
// `POST /model-imports` does not implement.

import { computed, nextTick, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { importRun, listRuns, runSampleUrl } from "../../api/modelImports";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";
import { formatModelSize, importReceipt } from "../../utils/modelShelf";

const props = defineProps({
  open: { type: Boolean, default: false },
});
const emit = defineEmits(["close"]);

const folders = useModelFoldersStore();
const shelf = useModelShelfStore();

const sourceId = ref(null);
const destinationId = ref(null);
const pickedName = ref("");
const chosenSteps = ref([]);
const runs = ref([]);
const loading = ref(false);
const working = ref(false);
const error = ref("");
const firstFieldEl = ref(null);

/** Registered ai-toolkit output roots: the only folders holding runs. */
const sources = computed(() =>
  folders.folders.filter((folder) => folder.kind === "source"),
);

/**
 * Where an import may land.
 *
 * The same two exclusions a move applies, and for the same reasons: a `source`
 * folder is taken from rather than written into (the server refuses it), and an
 * `external` folder is shared with other software.
 */
const destinations = computed(() =>
  folders.folders.filter(
    (folder) => folder.kind !== "source" && folder.movable !== "external",
  ),
);

const picked = computed(() =>
  runs.value.find((run) => run.name === pickedName.value),
);

const sourceFolder = computed(() =>
  sources.value.find((folder) => folder.id === sourceId.value),
);

const deletesSource = computed(() =>
  Boolean(sourceFolder.value?.delete_after_import),
);

const subtitle = computed(() =>
  runs.value.length
    ? `${runs.value.length.toLocaleString()} ${runs.value.length === 1 ? "run" : "runs"}`
    : "",
);

const confirmLabel = computed(() => {
  const n = chosenSteps.value.length;
  if (!picked.value || !n) return "Import";
  return `Import ${n.toLocaleString()} ${n === 1 ? "file" : "files"}`;
});

const canSubmit = computed(
  () =>
    !working.value &&
    Boolean(picked.value) &&
    chosenSteps.value.length > 0 &&
    destinationId.value != null,
);

/**
 * The run's cover: the FIRST prompt at its highest step.
 *
 * Highest step because that is what the run has learned so far. First prompt,
 * and deliberately not the last one rendered — `index` distinguishes *prompts*
 * within a step, not time, so every sample at the top step is equally "newest"
 * and a tie-break on recency has nothing to break. Choosing index 0 keeps the
 * cover on the same prompt for every run and at every step, which is what makes
 * two cards in this grid comparable and stops a card changing subject when a
 * later step renders more prompts.
 *
 * The reviewer of #878 read the old comment here — which claimed "the last
 * preview" — and reasonably called the tie-break a bug. The comment was the
 * bug; this is what the code should have said it does.
 */
function coverOf(run) {
  const samples = run.samples || [];
  if (!samples.length || sourceId.value == null) return "";
  const cover = samples.reduce((best, s) =>
    s.step > best.step || (s.step === best.step && s.index < best.index)
      ? s
      : best,
  );
  return runSampleUrl(sourceId.value, run.name, cover.filename);
}

/** True when the run wrote the bare final file that confirms it finished. */
function hasBareFinal(run) {
  return (run.checkpoints || []).some((cp) => cp.step === null);
}

function stepCount(run) {
  const n = (run.checkpoints || []).length;
  return `${n.toLocaleString()} ${n === 1 ? "checkpoint" : "checkpoints"}`;
}

/**
 * Pick a run, and tick every checkpoint in it.
 *
 * All of them, because importing part of a run is the exception: the steps
 * become one stack and the point of a stack is that the run is kept together.
 */
function pick(run) {
  pickedName.value = run.name;
  chosenSteps.value = (run.checkpoints || []).map((cp) => cp.step ?? null);
}

/** Arrow keys move between cards, the radiogroup pattern the role promises. */
function onGridKeydown(event) {
  const keys = ["ArrowDown", "ArrowRight", "ArrowUp", "ArrowLeft"];
  if (!keys.includes(event.key) || !runs.value.length) return;
  event.preventDefault();
  // Captured before the await: `currentTarget` is null by the time a queued
  // callback runs, so reading it inside `nextTick` would silently drop focus.
  const grid = event.currentTarget;
  const current = runs.value.findIndex((run) => run.name === pickedName.value);
  const step = event.key === "ArrowUp" || event.key === "ArrowLeft" ? -1 : 1;
  const from = current < 0 ? 0 : current;
  const next = (from + step + runs.value.length) % runs.value.length;
  pick(runs.value[next]);
  nextTick(() => {
    grid?.querySelector(`[data-run-index="${next}"]`)?.focus();
  });
}

async function loadRuns() {
  if (sourceId.value == null) {
    runs.value = [];
    return;
  }
  loading.value = true;
  error.value = "";
  pickedName.value = "";
  chosenSteps.value = [];
  try {
    runs.value = await listRuns(sourceId.value);
  } catch (err) {
    error.value = errorDetail(err) || "Could not read that folder.";
    runs.value = [];
  } finally {
    loading.value = false;
  }
}

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    working.value = false;
    sourceId.value = sources.value[0]?.id ?? null;
    const managed = destinations.value.find((f) => f.kind === "managed");
    destinationId.value = managed?.id ?? destinations.value[0]?.id ?? null;
    await loadRuns();
    await nextTick();
    firstFieldEl.value?.focus();
  },
  { immediate: true },
);

// Changing the folder reloads its runs. Not debounced: it is a select, so one
// change is one deliberate choice rather than a stream of them.
watch(sourceId, (id, previous) => {
  if (props.open && id !== previous) loadRuns();
});

async function submit() {
  if (!canSubmit.value) return;
  const notices = useNoticeStore();
  working.value = true;
  try {
    const report = await importRun({
      sourceFolderId: sourceId.value,
      runName: picked.value.name,
      destinationFolderId: destinationId.value,
      steps: chosenSteps.value,
    });
    const failed = (report?.files || []).some((f) => f.status === "failed");
    notices.push({
      level: failed ? "warning" : "success",
      text: importReceipt(report),
    });
    // Both stores: the shelf gained rows, and the destination folder's file
    // count and `shelf_bytes` moved with them, so the drive bands are stale too.
    await Promise.all([shelf.fetchRows(), folders.refresh({ quiet: true })]);
    emit("close");
  } catch (err) {
    notices.push({
      level: "error",
      text: errorDetail(err) || "Could not import that run.",
    });
  } finally {
    working.value = false;
  }
}
</script>

<style scoped>
.mid-field {
  display: block;
  margin-bottom: var(--space-4);
  border: none;
  padding: 0;
}

.mid-label {
  display: block;
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mid-select {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.25);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  font-size: var(--text-sm);
}

/* auto-fill rather than auto-fit: a single run keeps a card's width instead of
   stretching one preview across the whole dialog. */
.mid-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-3);
  max-height: 42vh;
  overflow-y: auto;
  margin-bottom: var(--space-4);
}

.mid-card {
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-md);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.15);
  overflow: hidden;
  cursor: pointer;
  background: rgb(var(--v-theme-surface));
}

.mid-card:hover {
  background: var(--hover-wash);
}

/* The pick reads as a ring, not a fill: the card's own preview is the content,
   and washing it would change the image the choice is being made on. */
.mid-card--picked {
  border-color: rgba(var(--v-theme-primary), 0.9);
  box-shadow: inset 0 0 0 1px rgba(var(--v-theme-primary), 0.9);
}

.mid-card-preview {
  width: 100%;
  aspect-ratio: 1;
  object-fit: cover;
  display: block;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.mid-card-preview--none {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mid-card-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
}

.mid-card-name {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgb(var(--v-theme-on-surface));
  word-break: break-word;
}

.mid-card-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mid-card-note {
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mid-steps {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.mid-step {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.mid-step-size {
  color: rgb(var(--v-theme-on-surface-variant));
  font-size: var(--text-xs);
}

.mid-note,
.mid-warning {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface-variant));
}

.mid-warning {
  color: rgb(var(--v-theme-warning));
}
</style>
