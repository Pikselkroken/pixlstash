<template>
  <div class="mrr">
    <!-- With the run's page open the rail says what the run was made of; the
         page already says how it went, larger (Training and Samples). -->
    <ModelsRailSection v-if="open" section-key="setup" title="Set-up">
      <dl class="mrs-facts">
        <div v-for="fact in setup" :key="fact.label" class="mrs-fact">
          <dt>{{ fact.label }}</dt>
          <dd :class="fact.class">{{ fact.value }}</dd>
        </div>
      </dl>
    </ModelsRailSection>

    <ModelsRailSection
      v-if="open"
      section-key="trained"
      title="Trained on"
      :count="pictures.length || null"
    >
      <template v-if="live?.dataset_snapshot_id != null" #end>
        <!-- The frozen dataset (#2123): what the LoRA was trained on,
             whatever has happened to the pictures since. -->
        <button
          class="mrs-link-btn"
          type="button"
          :disabled="exporting"
          data-testid="mrr-export"
          @click="exportDataset"
        >
          Export
        </button>
      </template>
      <span v-if="subjectName" class="mrs-name">{{ subjectName }}</span>
      <div v-if="pictures.length" class="mrr-pics" data-testid="mrr-pictures">
        <img
          v-for="id in shownPictures"
          :key="id"
          :src="pictureThumbnailUrl(id)"
          alt="Training picture"
          loading="lazy"
          role="button"
          tabindex="0"
          @click="openPicture(id)"
          @keydown.enter="openPicture(id)"
          @keydown.space.prevent="openPicture(id)"
        />
        <span v-if="morePictures" class="mrs-hint mrr-more"
          >+{{ morePictures.toLocaleString() }} more</span
        >
      </div>
      <p v-else class="mrs-hint">The pictures were not recorded.</p>
    </ModelsRailSection>

    <ModelsRailSection
      v-if="!open"
      section-key="progress"
      title="Training"
      :count="status"
    >
      <template v-if="canStop" #end>
        <button
          class="mrs-link-btn mrr-stop"
          type="button"
          :disabled="stopping"
          data-testid="mrr-stop"
          @click="stop"
        >
          {{ current.job.status === "completed" ? "Skip previews" : "Stop" }}
        </button>
      </template>
      <span
        v-if="percent != null"
        class="mrr-bar"
        :class="{ 'mrr-bar--live': card.live }"
        role="progressbar"
        aria-label="Training progress"
        :aria-valuenow="percent"
        aria-valuemin="0"
        aria-valuemax="100"
        ><span :style="{ width: `${percent}%` }"></span
      ></span>
      <dl class="mrs-facts">
        <div class="mrs-fact">
          <dt>Steps</dt>
          <dd data-testid="mrr-steps">{{ steps }}</dd>
        </div>
        <div v-if="remaining" class="mrs-fact">
          <dt>Time left</dt>
          <dd>{{ remaining }}</dd>
        </div>
        <div v-if="loss" class="mrs-fact">
          <dt>Loss</dt>
          <dd>{{ loss }} <span class="mrs-hint">smoothed</span></dd>
        </div>
      </dl>
      <LoraLossChart
        v-if="curve.length > 1"
        ref="chartRef"
        :curve="curve"
        compact
        :height="40"
      />
      <template v-if="!current.live && reason.short">
        <p class="mrs-hint mrr-reason" data-testid="mrr-reason">
          {{ reason.short }}
        </p>
        <!-- The trainer's own text, for pasting into an issue. -->
        <pre
          v-if="reason.full && showFull"
          class="mrs-hint mrr-full"
          data-testid="mrr-full"
          >{{ reason.full }}</pre
        >
        <button
          v-if="reason.full"
          class="mrs-link-btn mrr-full-toggle"
          type="button"
          :aria-expanded="String(showFull)"
          data-testid="mrr-full-toggle"
          @click="showFull = !showFull"
        >
          {{ showFull ? "Hide full message" : "Full message" }}
        </button>
      </template>
      <p v-if="!card.job" class="mrs-hint">
        Trained outside PixlStash, so its loss was not recorded.
      </p>
    </ModelsRailSection>

    <ModelsRailSection
      v-if="!open"
      section-key="samples"
      title="Samples"
      :count="sampleStep"
    >
      <template v-if="card.job || card.run" #end>
        <button
          class="mrs-link-btn"
          type="button"
          data-testid="mrr-open"
          @click="openRun"
        >
          Open run
        </button>
      </template>
      <div
        v-if="grid.steps.length"
        class="mrr-grid"
        :style="{ '--mrr-cols': grid.steps.length }"
        data-testid="mrr-samples"
      >
        <span></span>
        <span v-for="step in grid.steps" :key="step" class="mrs-hint mrr-col">{{
          step.toLocaleString()
        }}</span>
        <template v-for="(row, r) in grid.rows" :key="r">
          <span class="mrs-hint mrr-prompt" :title="row.prompt"
            >{{ row.prompt
            }}<span v-if="row.noTrigger" class="mrr-tag"> · no trigger</span></span
          >
          <span v-for="(file, c) in row.cells" :key="c" class="mrr-cell">
            <!-- Only with an address: a live job can name a sample before its
                 folder resolves, and an empty src draws a broken picture. The
                 cell stays, so the grid keeps its shape (as TrainingRunLive). -->
            <img
              v-if="file && sampleUrl(file)"
              :src="sampleUrl(file)"
              :alt="`${row.prompt}, step ${grid.steps[c].toLocaleString()}`"
              loading="lazy"
              role="button"
              tabindex="0"
              @click="openSample(row, c, file)"
              @keydown.enter="openSample(row, c, file)"
              @keydown.space.prevent="openSample(row, c, file)"
            />
          </span>
        </template>
      </div>
      <p v-else class="mrs-hint">No samples drawn yet.</p>
    </ModelsRailSection>

    <ModelsRailSection
      section-key="ckpts"
      title="Checkpoints"
      :count="checkpoints.length"
    >
      <!-- What the checkpoints became: the LoRA on the shelf, opened there. -->
      <template v-if="modelId != null" #end>
        <button
          class="mrs-link-btn"
          type="button"
          data-testid="mrr-model"
          @click="goToModel"
        >
          Go to model
        </button>
      </template>
      <div v-for="cp in checkpoints" :key="cp.filename" class="mrs-row">
        <span class="mrs-num mrr-step">{{
          cp.step == null ? "final" : cp.step.toLocaleString()
        }}</span>
        <span class="mrs-name mrr-mono" :title="cp.filename">{{ cp.filename }}</span>
        <span class="mrs-num">{{ formatModelSize(cp.size) || "–" }}</span>
      </div>
      <p v-if="!checkpoints.length" class="mrs-hint">No checkpoint saved yet.</p>
      <!-- In the grid the Import bar does this; on the run page it is the one
           thing a run that never reached the shelf needs. -->
      <template v-if="open && shelvable">
        <AppButton
          class="mrr-shelve"
          size="sm"
          :loading="shelving"
          data-testid="mrr-shelve"
          @click="shelve"
        >
          {{ shelveLabel }}
        </AppButton>
        <p v-if="runsStore.source?.delete_after_import" class="mrs-hint">
          This folder removes what it imports, so the file leaves the run.
        </p>
      </template>
    </ModelsRailSection>

    <ModelsRailSection v-if="!open" section-key="setup" title="Set-up">
      <dl class="mrs-facts">
        <div v-for="fact in setup" :key="fact.label" class="mrs-fact">
          <dt>{{ fact.label }}</dt>
          <dd :class="fact.class">{{ fact.value }}</dd>
        </div>
      </dl>
    </ModelsRailSection>
    <TrainingSampleDialog :sample="openedSample" @close="openedSample = null" />
  </div>
</template>

<script setup>
/**
 * One training run, in the Models rail (#2169). Picked in the runs grid, where
 * nothing else on screen describes it, the rail is its whole summary: how it is
 * going (with Stop while it trains), its samples as prompts × steps, the
 * checkpoints it saved and what it was set up with.
 *
 * With the run's own page open the two split by question: the page says how it
 * went (failure, loss, samples) and the rail what it was made of (set-up, the
 * pictures it learned from, the checkpoints it left), so nothing is said twice.
 *
 * A run PixlStash trains is read from its live view, polled while it trains; a
 * run trained elsewhere is described from the folder listing alone.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { importRun, runSampleUrl } from "../../api/modelImports";
import { pictureThumbnailUrl } from "../../api/pictures";
import { exportTrainingSnapshot, getTrainingJobLive } from "../../api/training";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
import { errorDetail } from "../../utils/apiError";
import { saveFileAs } from "../../utils/downloadFile";
import { formatModelSize } from "../../utils/modelShelf";
import {
  baseOf,
  defaultDestination,
  failureText,
  importDestinations,
  jobStatus,
  lastCheckpoint,
  progressOf,
  resultModelId,
  sampleGrid,
  stepsOf,
  timeLeft,
} from "../../utils/trainingRuns.js";
import AppButton from "../widgets/AppButton.vue";
import LoraLossChart from "../widgets/LoraLossChart.vue";
import TrainingSampleDialog from "../widgets/TrainingSampleDialog.vue";
import ModelsRailSection from "./ModelsRailSection.vue";

const props = defineProps({
  /** The picked card, as `useTrainingRunsStore` draws it. */
  card: { type: Object, required: true },
  /** The run's own page is open beside the rail. */
  open: { type: Boolean, default: false },
});

const POLL_MS = 2000;
/** A 3 × 3 grid; past nine, the last cell counts the rest. */
const PICTURES_SHOWN = 9;

const route = useRoute();
const router = useRouter();
const runsStore = useTrainingRunsStore();
const lists = useEntityListsStore();
const folders = useModelFoldersStore();
const shelf = useModelShelfStore();

const live = ref(null);
const stopping = ref(false);
const exporting = ref(false);
const shelving = ref(false);
/** The checkpoint this rail put on the shelf, so it is not offered twice. */
const shelved = ref("");
const chartRef = ref(null);

/** The card with the live view's step and status, which move faster. */
const current = computed(() =>
  live.value && props.card.job
    ? {
        ...props.card,
        live: runsStore.isLive(live.value),
        job: { ...props.card.job, ...live.value },
      }
    : props.card,
);

const status = computed(() => {
  const card = current.value;
  if (card.live) return jobStatus(card.job);
  if (card.job?.status === "stopped") return "stopped";
  if (card.job?.status === "error") return "failed";
  return "finished";
});

// Not while it is already stopping: a second cancel says nothing new. A
// trained run's cancel skips its remaining previews; once it is importing (or
// before the live view says which) there is nothing to stop.
const canStop = computed(() => {
  const job = current.value.job;
  if (!current.value.live || !job || job.status === "stopping") return false;
  return job.status !== "completed" || job.phase === "previews";
});
const reason = computed(() => failureText(current.value.job));
const showFull = ref(false);
const percent = computed(() => progressOf(current.value));
const steps = computed(() => stepsOf(current.value));
const remaining = computed(() =>
  current.value.live ? timeLeft(live.value?.speed) : "",
);
const curve = computed(() => live.value?.loss_curve ?? []);
// The chart's smoothed figure, the one the run page shows, so the two never
// give one run two losses. One point (no chart) smooths to itself.
const loss = computed(() => {
  const value =
    chartRef.value?.latestSmoothed ??
    Number(curve.value[curve.value.length - 1]?.loss);
  if (value == null || !Number.isFinite(value)) return "";
  return value < 0.01 ? value.toExponential(2) : value.toFixed(3);
});

const grid = computed(() =>
  sampleGrid({ live: live.value, run: props.card.run }),
);
const sampleStep = computed(() => {
  const last = grid.value.steps[grid.value.steps.length - 1];
  return last == null ? "" : `step ${last.toLocaleString()}`;
});

function sampleUrl(filename) {
  const folder = live.value?.run_folder;
  if (folder?.folder_id != null) {
    return runSampleUrl(folder.folder_id, folder.run_name, filename);
  }
  const sourceId = runsStore.source?.id;
  if (sourceId == null || !props.card.run) return "";
  return runSampleUrl(sourceId, props.card.run.name, filename);
}

/** The sample a click opened at full size, or null. */
const openedSample = ref(null);

function openSample(row, column, file) {
  openedSample.value = {
    src: sampleUrl(file),
    step: grid.value.steps[column],
    prompt: row.prompt,
  };
}

const checkpoints = computed(() => props.card.run?.checkpoints ?? []);

/** The shelf model the run made, which "Go to model" opens the shelf on. */
const modelId = computed(() => resultModelId(props.card));

function goToModel() {
  router.push({ name: "models", query: { model: String(modelId.value) } });
}

const subjectName = computed(() => {
  const job = props.card.job;
  if (!job) return "";
  const name =
    job.character_id != null
      ? lists.characters.find((c) => Number(c.id) === job.character_id)?.name
      : lists.pictureSets.find((s) => Number(s.id) === job.set_id)?.name;
  return name || "";
});

const pictures = computed(() => live.value?.dataset_picture_ids ?? []);
const morePictures = computed(() =>
  pictures.value.length > PICTURES_SHOWN
    ? pictures.value.length - (PICTURES_SHOWN - 1)
    : 0,
);
const shownPictures = computed(() =>
  pictures.value.slice(0, morePictures.value ? PICTURES_SHOWN - 1 : PICTURES_SHOWN),
);

const trainedOn = computed(() => {
  if (!props.card.job) return "–";
  const n = pictures.value.length;
  return [subjectName.value || "–", n ? `${n.toLocaleString()} pictures` : ""]
    .filter(Boolean)
    .join(" · ");
});

const trigger = computed(
  () =>
    props.card.job?.trigger_word || (props.card.run?.trigger_words || []).join(", "),
);

const rankLr = computed(() => {
  const run = props.card.run;
  const rank = run?.rank ?? "–";
  const lr = run?.learning_rate;
  return `${rank} · ${lr != null ? lr : "–"}`;
});

const folder = computed(() => {
  const root = runsStore.source?.path;
  const name = props.card.run?.name || props.card.job?.run_name;
  if (!root || !name) return "–";
  return `${String(root).replace(/[\\/]+$/, "")}/${name}`;
});

/** "1,500 steps, a sample every 250", from the live view's columns. */
const plan = computed(() => {
  const total = live.value?.total_steps;
  if (!total) return "";
  const steps = `${total.toLocaleString()} steps`;
  // The columns are step 0, every preview step and the last. Without the
  // run's config they are only the steps its samples name, which need not be
  // even, so the interval is said only when every gap keeps to it (the last
  // step may fall short of the pitch).
  const columns = live.value.columns ?? [];
  const every = columns[1];
  const even =
    columns[0] === 0 &&
    every > 0 &&
    columns.every(
      (step, i) => i === 0 || step === total || step - columns[i - 1] === every,
    );
  return even ? `${steps}, a sample every ${every.toLocaleString()}` : steps;
});

/** The set-up facts; in the grid they also say what it trained on. */
const setup = computed(() =>
  [
    !props.open && { label: "Trained on", value: trainedOn.value },
    { label: "Base", value: baseOf(props.card), class: "mrr-wrap" },
    { label: "Trigger word", value: trigger.value || "–", class: "mrr-mono" },
    { label: "Rank · LR", value: rankLr.value },
    props.open && plan.value && { label: "Plan", value: plan.value },
    { label: "Folder", value: folder.value, class: "mrr-mono mrr-wrap" },
  ].filter(Boolean),
);

// ── Put the last checkpoint on the shelf ────────────────────────────────────

const lastCp = computed(() => lastCheckpoint(props.card.run));
const destination = computed(() =>
  defaultDestination(importDestinations(folders.folders)),
);

/**
 * Whether that checkpoint is on the shelf already. Per checkpoint: an earlier
 * step taken from the grid leaves a later one still to take. `coverable` is
 * the steps on the shelf once anything is; a bare final is taken by every
 * automatic import, so one in an imported run is there.
 */
const lastOnShelf = computed(() => {
  const run = live.value;
  if (!run?.imported) return false;
  return lastCp.value?.step == null || (run.coverable ?? []).includes(lastCp.value.step);
});

// Only once the live view has said what is on the shelf, and never while it
// trains: its folder is the trainer's until it is done.
const shelvable = computed(() =>
  Boolean(
    lastCp.value &&
      lastCp.value.filename !== shelved.value &&
      destination.value &&
      runsStore.source?.id != null &&
      runsStore.liveKnown &&
      !current.value.live &&
      live.value &&
      !lastOnShelf.value,
  ),
);

const shelveLabel = computed(() =>
  lastCp.value?.step == null
    ? "Put the final checkpoint on the shelf"
    : `Put step ${lastCp.value.step.toLocaleString()} on the shelf`,
);

async function shelve() {
  const cp = lastCp.value;
  const runName = props.card.run?.name;
  if (shelving.value || !cp || !runName) return;
  // The same consent the grid's Import asks for, said before the file goes.
  if (
    runsStore.source?.delete_after_import &&
    !window.confirm(
      `This folder removes what it imports, so ${cp.filename} will leave the run once it is on the shelf. Put it on the shelf?`,
    )
  ) {
    return;
  }
  const notices = useNoticeStore();
  shelving.value = true;
  try {
    const report = await importRun({
      sourceFolderId: runsStore.source.id,
      runName,
      destinationFolderId: destination.value.id,
      steps: [cp.step],
    });
    if ((report?.files || []).some((f) => f.status === "failed")) {
      notices.push({ level: "warning", text: `Could not put ${cp.filename} on the shelf.` });
    } else {
      shelved.value = cp.filename;
      notices.push({ level: "success", text: `Put ${cp.filename} on the shelf.` });
    }
    // The shelf gained a row and the destination's figures moved; the run
    // folder may have lost the file.
    await Promise.all([
      shelf.fetchRows(),
      folders.refresh({ quiet: true }),
      runsStore.reload(),
    ]);
  } catch (err) {
    console.warn(`Could not import ${cp.filename} from training run ${runName}:`, err);
    notices.push({
      level: "error",
      text: errorDetail(err) || `Could not put ${cp.filename} on the shelf.`,
    });
  } finally {
    shelving.value = false;
  }
}

async function exportDataset() {
  exporting.value = true;
  try {
    const { blob, filename } = await exportTrainingSnapshot(props.card.job.id);
    await saveFileAs(blob, filename);
  } catch (err) {
    console.warn(`Could not export the dataset of training run ${props.card.job.id}:`, err);
    useNoticeStore().push({
      level: "error",
      text: errorDetail(err) || "Could not export the dataset.",
    });
  } finally {
    exporting.value = false;
  }
}

/**
 * Open a training picture in the lightbox. It lives in the library grid, which
 * the runs view replaces, so the way there is the picture route; `?from=` is
 * the full path, `?run=` included, so closing it comes back to this run.
 */
function openPicture(pictureId) {
  router.push({
    name: "all-pictures",
    query: { overlay: String(pictureId), from: route.fullPath },
  });
}

/** A run trained elsewhere has no job, so its page is drawn from its folder. */
function openRun() {
  const query = props.card.job
    ? { run: props.card.job.uuid }
    : { "folder-run": props.card.run.name };
  router.push({ name: "models-runs", query });
}

async function stop() {
  if (stopping.value) return;
  stopping.value = true;
  try {
    if (await runsStore.stopJob(current.value.job, props.card.name)) await load();
  } finally {
    stopping.value = false;
  }
}

// ── The live view, polled while the run trains ──────────────────────────────

let timer = null;
let generation = 0;
let disposed = false;

async function load() {
  const job = props.card.job;
  if (!job) return;
  const gen = ++generation;
  try {
    const body = await getTrainingJobLive(job.id);
    if (gen === generation) live.value = body;
  } catch (err) {
    // The folder listing still describes the run; only the live figures go.
    if (gen === generation) live.value = null;
    console.warn(`Could not read the live view of training run ${job.id}:`, err);
  }
}

function schedule() {
  clearTimeout(timer);
  timer = null;
  // A read in flight at unmount must not re-arm a poll nobody owns.
  if (disposed) return;
  if (!current.value.live || document.visibilityState !== "visible") return;
  timer = setTimeout(async () => {
    await load();
    schedule();
  }, POLL_MS);
}

// `schedule` lets the chain end while the tab is hidden; looking at it again
// brings the figures up to date and starts it again.
function onVisible() {
  if (document.visibilityState === "visible" && current.value.live) {
    load().then(schedule);
  }
}

watch(
  () => props.card.key,
  () => {
    generation += 1;
    clearTimeout(timer);
    live.value = null;
    showFull.value = false;
    load().then(schedule);
  },
);

onMounted(() => {
  lists.refresh("characters");
  lists.refresh("sets");
  load().then(schedule);
  document.addEventListener("visibilitychange", onVisible);
});

onBeforeUnmount(() => {
  document.removeEventListener("visibilitychange", onVisible);
  disposed = true;
  generation += 1;
  clearTimeout(timer);
});
</script>

<style scoped>
.mrr {
  display: flex;
  flex-direction: column;
}

.mrr-bar {
  display: block;
  height: 4px;
  overflow: hidden;
  border-radius: var(--radius-pill);
  background: var(--track-trough);
}

.mrr-bar > span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-tertiary));
}

.mrr-bar--live > span {
  background: rgb(var(--v-theme-accent));
}

.mrr-stop {
  color: rgb(var(--v-theme-error));
}

/* An unsummarised trainer paragraph breaks anywhere rather than overflow. */
.mrr-reason {
  overflow-wrap: anywhere;
}

.mrr-full {
  max-height: 240px;
  overflow: auto;
  font-family: var(--font-mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.mrr-full-toggle {
  align-self: flex-start;
  padding: 0;
}

.mrr-grid {
  display: grid;
  grid-template-columns: var(--space-9) repeat(var(--mrr-cols), minmax(0, 1fr));
  gap: var(--space-1);
  align-items: center;
}

.mrr-col {
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.mrr-prompt {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mrr-tag {
  color: rgb(var(--v-theme-on-surface));
}

.mrr-cell {
  aspect-ratio: 3 / 4;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.mrr-cell img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  cursor: zoom-in;
}

.mrr-step {
  min-width: var(--space-7);
}

.mrr-shelve {
  align-self: flex-start;
  margin-top: var(--space-2);
}

.mrr-pics {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-2);
}

.mrr-pics img,
.mrr-more {
  aspect-ratio: 3 / 4;
  width: 100%;
  border-radius: var(--radius-sm);
}

.mrr-pics img {
  cursor: zoom-in;
  display: block;
  object-fit: cover;
  object-position: center 18%;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.mrr-more {
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed rgb(var(--v-theme-border));
}

.mrr-mono {
  font-family: var(--font-mono);
}

.mrr-wrap {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
