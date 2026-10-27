<template>
  <div class="mrr">
    <ModelsRailSection section-key="progress" title="Training" :count="status">
      <template v-if="canStop" #end>
        <button
          class="mrs-link-btn mrr-stop"
          type="button"
          :disabled="stopping"
          data-testid="mrr-stop"
          @click="stop"
        >
          Stop
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
          <dd>{{ loss }}</dd>
        </div>
      </dl>
      <LoraLossChart v-if="curve.length > 1" :curve="curve" compact :height="40" />
      <p v-if="!card.job" class="mrs-hint">
        Trained outside PixlStash, so its loss was not recorded.
      </p>
    </ModelsRailSection>

    <ModelsRailSection section-key="samples" title="Samples" :count="sampleStep">
      <template v-if="card.job" #end>
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
            <img v-if="file" :src="sampleUrl(file)" alt="" loading="lazy" />
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
      <div v-for="cp in checkpoints" :key="cp.filename" class="mrs-row">
        <span class="mrs-name mrr-mono" :title="cp.filename">{{ cp.filename }}</span>
        <span class="mrs-num">{{ formatModelSize(cp.size) || "–" }}</span>
      </div>
      <p v-if="!checkpoints.length" class="mrs-hint">No checkpoint saved yet.</p>
    </ModelsRailSection>

    <ModelsRailSection section-key="setup" title="Set-up">
      <dl class="mrs-facts">
        <div class="mrs-fact">
          <dt>Trained on</dt>
          <dd>{{ trainedOn }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Base</dt>
          <dd class="mrr-wrap">{{ baseOf(card) }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Trigger word</dt>
          <dd class="mrr-mono">{{ trigger || "–" }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Rank · LR</dt>
          <dd>{{ rankLr }}</dd>
        </div>
        <div class="mrs-fact">
          <dt>Folder</dt>
          <dd class="mrr-mono mrr-wrap">{{ folder }}</dd>
        </div>
      </dl>
    </ModelsRailSection>
  </div>
</template>

<script setup>
/**
 * One training run, in the Models rail (#2169): how it is going (with Stop
 * while it trains), its samples as prompts × steps, the checkpoints it saved
 * and what it was set up with. A run PixlStash trains is read from its live
 * view, polled while it trains; a run trained elsewhere is described from the
 * folder listing alone.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { runSampleUrl } from "../../api/modelImports";
import { cancelTrainingJob, getTrainingJobLive } from "../../api/training";
import { useEntityListsStore } from "../../stores/useEntityListsStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTrainingRunsStore } from "../../stores/useTrainingRunsStore";
import { errorDetail } from "../../utils/apiError";
import { formatModelSize } from "../../utils/modelShelf";
import {
  baseOf,
  jobStatus,
  progressOf,
  sampleGrid,
  stepsOf,
  timeLeft,
} from "../../utils/trainingRuns.js";
import LoraLossChart from "../widgets/LoraLossChart.vue";
import ModelsRailSection from "./ModelsRailSection.vue";

const props = defineProps({
  /** The picked card, as `useTrainingRunsStore` draws it. */
  card: { type: Object, required: true },
});

const POLL_MS = 2000;

const router = useRouter();
const runsStore = useTrainingRunsStore();
const lists = useEntityListsStore();

const live = ref(null);
const stopping = ref(false);

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

// Not while it is already stopping: a second cancel says nothing new.
const canStop = computed(
  () =>
    current.value.live &&
    current.value.job &&
    current.value.job.status !== "stopping",
);
const percent = computed(() => progressOf(current.value));
const steps = computed(() => stepsOf(current.value));
const remaining = computed(() =>
  current.value.live ? timeLeft(live.value?.speed) : "",
);
const curve = computed(() => live.value?.loss_curve ?? []);
const loss = computed(() => {
  const last = curve.value[curve.value.length - 1]?.loss;
  if (last == null || !Number.isFinite(Number(last))) return "";
  return Number(last).toFixed(3);
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

const checkpoints = computed(() => props.card.run?.checkpoints ?? []);

const trainedOn = computed(() => {
  const job = props.card.job;
  if (!job) return "–";
  const name =
    job.character_id != null
      ? lists.characters.find((c) => Number(c.id) === job.character_id)?.name
      : lists.pictureSets.find((s) => Number(s.id) === job.set_id)?.name;
  const n = live.value?.dataset_picture_ids?.length;
  return [name || "–", n ? `${n.toLocaleString()} pictures` : ""]
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

function openRun() {
  router.push({ name: "models-runs", query: { run: props.card.job.uuid } });
}

async function stop() {
  if (stopping.value) return;
  stopping.value = true;
  try {
    await cancelTrainingJob(props.card.job.id);
    await Promise.all([load(), runsStore.loadJobs()]);
  } catch (err) {
    useNoticeStore().push({
      level: "error",
      text: errorDetail(err) || "Could not stop the run.",
    });
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

watch(
  () => props.card.key,
  () => {
    generation += 1;
    clearTimeout(timer);
    live.value = null;
    load().then(schedule);
  },
);

onMounted(() => {
  lists.refresh("characters");
  lists.refresh("sets");
  load().then(schedule);
});

onBeforeUnmount(() => {
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
}

.mrr-mono {
  font-family: var(--font-mono);
}

.mrr-wrap {
  min-width: 0;
  overflow-wrap: anywhere;
}
</style>
