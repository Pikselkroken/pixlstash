<template>
  <div class="trl">
    <!-- Run status rides in the shelf's own toolbar (the design's first
         decision): no second header, so the board starts higher. Drawn in
         place where there is no such bar. Never toggled after mount: Vue
         cannot move a teleport to a target it did not find at mount. -->
    <Teleport defer to="#shelf-run-bar" :disabled="!hasBar">
      <div class="trl-bar">
        <AppBarButton
          icon="arrow-left"
          tooltip="All runs"
          aria-label="All runs"
          @click="emit('close')"
        />
        <span class="trl-name">{{ live?.name || "Training run" }}</span>
        <template v-if="live">
          <span class="trl-vsep" aria-hidden="true"></span>
          <span class="trl-status" role="status">
            <span
              v-if="live.total_steps"
              class="trl-tbar"
              aria-hidden="true"
            >
              <i :style="{ width: `${progressPercent}%` }"></i>
            </span>
            <b>{{ headline }}</b>
            <template v-if="subline">
              <span v-if="busy" class="trl-spin trl-spin--sm" aria-hidden="true"></span>
              <span>{{ subline }}</span>
            </template>
          </span>
          <span class="trl-spacer"></span>
          <AppButton
            v-if="canStop"
            size="sm"
            variant="outline"
            icon-left="stop"
            :loading="stopping"
            :disabled="live.status === 'stopping'"
            @click="stop"
          >
            {{ live.status === "stopping" ? "Stopping…" : "Stop training" }}
          </AppButton>
        </template>
      </div>
    </Teleport>

    <p v-if="error && !live" class="trl-note" role="alert">{{ error }}</p>
    <div v-else-if="!live" class="trl-note" role="status">
      <v-icon size="20" class="mdi-spin">mdi-loading</v-icon>
      <span>Reading the run…</span>
    </div>

    <template v-else>
      <!-- Notices: the glyph and the rail carry the hue, the sentence stays
           in ink. -->
      <div v-if="notice" class="trl-notice" :class="`trl-notice--${notice.kind}`">
        <v-icon size="18" class="trl-notice-glyph">{{ notice.icon }}</v-icon>
        <p>
          <b>{{ notice.title }}</b>
          {{ notice.text }}
        </p>
      </div>

      <div class="trl-body">
        <div class="trl-scroll">
          <div
            class="trl-board"
            :style="{ '--trl-cols': columns.length }"
            @mouseleave="hoverColumn = null"
          >
            <!-- Painted in DOM order: the prompt rows' tint first, then the
                 cover's olive frame and the hovered column's wash over it (so
                 selection wins), then the content. -->
            <template v-for="(group, g) in groups" :key="`z${group.key}`">
              <div
                v-for="(row, r) in group.rows"
                :key="`z${row.index}`"
                class="trl-zebra"
                :class="{ 'trl-zebra--on': (r + g) % 2 === 0 }"
                :style="{ gridRow: group.row + 1 + r }"
                aria-hidden="true"
              ></div>
            </template>
            <div
              v-if="coverIndex >= 0"
              class="trl-frame trl-frame--cover"
              :style="{ gridColumn: coverIndex + 2, gridRow: `1 / ${rowCount + 1}` }"
              aria-hidden="true"
            ></div>
            <div
              v-if="hoverColumn !== null && hoverColumn !== coverIndex"
              class="trl-frame trl-frame--hover"
              :style="{ gridColumn: hoverColumn + 2, gridRow: `1 / ${rowCount + 1}` }"
              aria-hidden="true"
            ></div>

            <!-- The timeline is the column header. -->
            <div class="trl-lab trl-lab--setup" :style="{ gridRow: 1 }">
              <span v-if="setupDone" class="trl-setup">
                <Tooltip :text="setupTooltip" activator="parent" />
                <v-icon size="14">mdi-check</v-icon>
                Set up in {{ clock(setupSeconds) }}
              </span>
              <span
                v-else-if="live.phase"
                class="trl-setup"
              >
                <span class="trl-spin trl-spin--sm" aria-hidden="true"></span>
                {{ PHASE_LABELS[live.phase] }}
              </span>
              <span v-else-if="live.status === 'error'" class="trl-setup trl-setup--err">
                <v-icon size="14">mdi-alert</v-icon>
                Stopped at {{ PHASE_LABELS[lastPhase] || "setup" }}
              </span>
            </div>
            <div
              class="trl-track"
              :style="{ gridRow: 1, gridColumn: `2 / span ${columns.length}` }"
              aria-hidden="true"
            >
              <i :style="{ width: `${fillPercent}%` }"></i>
            </div>
            <div
              v-for="(column, i) in columns"
              :key="`h${column}`"
              class="trl-step"
              :style="{ gridRow: 1, gridColumn: i + 2 }"
              @mouseenter="hoverColumn = i"
              @focusin="hoverColumn = i"
            >
              <span
                class="trl-stepn"
                :class="{ 'trl-stepn--fut': column > live.step }"
                >{{ column === 0 ? "Step 0" : column.toLocaleString() }}</span
              >
              <span class="trl-tick" :class="`trl-tick--${tickOf(i)}`" aria-hidden="true">
                <span v-if="tickOf(i) === 'now'" class="trl-spin"></span>
              </span>
              <span class="trl-stepsub">
                <span v-if="column === 0">before training</span>
                <span v-else-if="i === coverIndex" class="trl-badge-cover">
                  <v-icon size="12">mdi-star</v-icon>Cover
                </span>
                <span v-else-if="i === drawingIndex && live.previews">
                  previews {{ live.previews.drawing }} of {{ live.previews.total }}
                </span>
                <span v-else-if="i === drawingIndex && live.previews_deferred">
                  previews after training
                </span>
                <button
                  v-else-if="live.coverable.includes(column)"
                  type="button"
                  class="trl-use"
                  :disabled="choosing"
                  @click="useStep(column)"
                >
                  Use this step
                </button>
              </span>
            </div>
            <!-- The loss, on the same step axis. -->
            <div class="trl-lab" :style="{ gridRow: 2 }">
              <span class="section-label">Loss</span>
              <span class="trl-loss">
                <span class="trl-lv">{{ lossValue }}</span>
                <span v-if="lossValue !== 'Unknown'" class="trl-muted">smoothed</span>
                <Provenance :kind="live.loss_curve.length ? 'measured' : 'none'" />
              </span>
            </div>
            <div
              class="trl-chart"
              :style="{ gridRow: 2, gridColumn: `2 / span ${columns.length}` }"
            >
              <LoraLossChart
                ref="chartRef"
                :curve="live.loss_curve"
                :x-scale="stepFraction"
                :markers="markers"
                :height="92"
                smooth
                compact
              />
            </div>

            <template v-for="group in groups" :key="group.key">
              <div class="trl-gline" :style="{ gridRow: group.row }">
                <span class="section-label">{{ group.title }}</span>
                <span class="trl-hint">{{ group.hint }}</span>
              </div>
              <template v-for="(row, r) in group.rows" :key="`r${row.index}`">
                <div class="trl-lab trl-prompt" :style="{ gridRow: group.row + 1 + r }">
                  <template v-for="(part, p) in promptParts(row.prompt)" :key="p">
                    <span v-if="part.trigger" class="trl-trig">{{ part.text }}</span>
                    <template v-else>{{ part.text }}</template>
                  </template>
                  <span v-if="!row.prompt" class="trl-muted">Prompt {{ row.index + 1 }}</span>
                </div>
                <div
                  v-for="(cell, i) in row.cells"
                  :key="`c${row.index}-${i}`"
                  class="trl-cellwrap"
                  :style="{ gridRow: group.row + 1 + r, gridColumn: i + 2 }"
                  @mouseenter="hoverColumn = i"
                >
                  <img
                    v-if="cell.state === 'sample' && sampleUrl(cell)"
                    class="trl-cell"
                    :src="sampleUrl(cell)"
                    :alt="`${row.prompt || `Prompt ${row.index + 1}`}, step ${columns[i]}`"
                    loading="lazy"
                  />
                  <span v-else-if="cell.state === 'drawing'" class="trl-cell trl-cell--gen">
                    <span class="trl-spin"></span>
                    <template v-if="live.previews">
                      {{ live.previews.drawing }} of {{ live.previews.total }}
                    </template>
                  </span>
                  <span v-else-if="cell.state === 'queued'" class="trl-cell trl-cell--queued"
                    >next</span
                  >
                  <span v-else-if="cell.state === 'deferred'" class="trl-cell trl-cell--deferred"
                    >after training</span
                  >
                  <span
                    v-else
                    class="trl-cell trl-cell--future"
                    :class="{ 'trl-cell--missing': cell.state !== 'future' }"
                    >{{ cell.state === "future" ? "" : "No preview" }}</span
                  >
                </div>
              </template>
            </template>
          </div>
        </div>

        <!-- The pictures the columns are walking towards. -->
        <aside class="trl-strip" aria-label="Training pictures">
          <!-- Where the track ends: the final save and the import. -->
        <div class="trl-endph">
            <span
              v-for="end in endPhases"
              :key="end.key"
              :class="{ 'trl-endph--cur': end.state === 'now' }"
            >
              <v-icon v-if="end.state === 'done'" size="14">mdi-check</v-icon>
              <i v-else class="trl-ring" aria-hidden="true"></i>
              {{ end.label }}
            </span>
          </div>

          <span class="section-label">
            Trained on <span class="trl-n">· {{ pictures.length.toLocaleString() }}</span>
          </span>
          <span v-if="subjectName" class="trl-muted">{{ subjectName }}</span>
          <div class="trl-strip-grid">
            <img
              v-for="id in pictures.slice(0, STRIP_SHOWN)"
              :key="id"
              class="trl-strip-pic"
              :src="pictureThumbnailUrl(id)"
              alt=""
              loading="lazy"
            />
            <span v-if="pictures.length > STRIP_SHOWN" class="trl-strip-more">
              +{{ (pictures.length - STRIP_SHOWN).toLocaleString() }}<br />more
            </span>
          </div>
        </aside>
      </div>

      <!-- Memory and speed describe now, not a step: a status bar. -->
      <div class="trl-statusbar">
        <span class="trl-it">
          <span class="trl-lab2">GPU</span>
          <Meter :figure="gpu" />
          <span class="trl-v">{{ figureText(gpu) }}</span>
          <Provenance :kind="gpu.total_mb ? 'measured' : 'none'" />
        </span>
        <span class="trl-it">
          <span class="trl-lab2">RAM</span>
          <Meter :figure="ram" :over-mb="ramShortMb" />
          <span class="trl-v">{{ ramText }}</span>
          <Provenance
            :kind="
              ramShortMb
                ? live.telemetry.need.basis || 'estimated'
                : ram.total_mb
                  ? 'measured'
                  : 'none'
            "
          />
        </span>
        <span v-if="live.streamed" class="trl-it">
          <v-icon size="14">mdi-layers-outline</v-icon>
          <span class="trl-lab2">Model layers streamed from RAM</span>
          <span v-if="live.streamed.percent != null" class="trl-v"
            >{{ Math.round(live.streamed.percent * 100) }}%</span
          >
        </span>
        <span v-if="live.telemetry.paging" class="trl-it trl-warn">
          <v-icon size="14">mdi-alert</v-icon>
          <b>Disk paging detected</b>
        </span>
        <span class="trl-it">
          <template v-if="live.speed.seconds_per_step != null">
            <span class="trl-v">{{ seconds(live.speed.seconds_per_step) }}</span>
            <span class="trl-lab2">per update</span>
            <Provenance kind="measured" />
          </template>
          <template v-else>
            <span class="trl-lab2">Per update</span>
            <span class="trl-v">Unknown</span>
            <Provenance kind="none" />
          </template>
        </span>
        <span class="trl-spacer"></span>
        <span v-if="live.finished_while_closed" class="trl-it">
          <v-icon size="14">mdi-weather-night</v-icon>
          Finished at {{ timeOfDay(live.finished_while_closed.at) }}
        </span>
        <span v-else-if="remaining" class="trl-it">
          <span class="trl-v trl-hl">{{ remaining }}</span>
          <span class="trl-lab2">left</span>
          <Provenance kind="estimated" />
        </span>
        <span v-else-if="live.completed_at" class="trl-it trl-lab2">
          Finished at {{ timeOfDay(live.completed_at) }}
        </span>
      </div>
    </template>
  </div>
</template>

<script setup>
// One training run, watched as it happens (#2118): the Training runs tab's
// run page, the design's "step axis" direction. Everything is drawn against
// one x-axis of steps: the timeline is the column header, the loss curve
// sits on the same scale, and each checkpoint's samples hang under its point
// on the curve. Memory and speed are a status bar, because they describe now,
// not a step.
//
// Read from `GET /training/jobs/{id}/live` every POLL_MS while the run is
// live and the tab is visible. Every figure carries where it came from
// (Measured / Estimated / Not measured), and a figure that could not be read
// says "Unknown", never 0.

import {
  computed,
  defineComponent,
  h,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { VIcon } from "vuetify/components";

import AppBarButton from "../widgets/AppBarButton.vue";
import AppButton from "../widgets/AppButton.vue";
import LoraLossChart from "../widgets/LoraLossChart.vue";
import Tooltip from "../widgets/Tooltip.vue";
import {
  cancelTrainingJob,
  getTrainingJobLive,
  setTrainingJobCover,
} from "../../api/training";
import { runSampleUrl } from "../../api/modelImports";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useEntityNamesStore } from "../../stores/useEntityNamesStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { errorDetail } from "../../utils/apiError";

const props = defineProps({
  jobId: { type: Number, required: true },
});
const emit = defineEmits(["close"]);

const POLL_MS = 2000;
const STRIP_SHOWN = 9;

const PHASE_LABELS = {
  waiting_gpu: "Waiting for the GPU",
  checking: "Checking memory and speed",
  loading: "Loading",
  caching: "Caching captions",
  training: "Training",
  previews: "Generating previews",
  saving: "Saving",
  importing: "Importing to the shelf",
};
const SETUP = ["waiting_gpu", "checking", "loading", "caching"];

const names = useEntityNamesStore();

const live = ref(null);
const error = ref("");
// The shelf renders its bar's slot before this page, so it is in the
// document by the time this runs.
const hasBar = Boolean(document.getElementById("shelf-run-bar"));
const hoverColumn = ref(null);
const choosing = ref(false);
const stopping = ref(false);
const chartRef = ref(null);

// ── The shape-coded provenance mark and the meter ────────────────────────────

/** Measured (filled dot), Estimated (dashed ring) or Not measured (a dash). */
const Provenance = defineComponent({
  props: { kind: { type: String, required: true } },
  setup(p) {
    const label = { measured: "Measured", estimated: "Estimated", none: "Not measured" };
    return () =>
      h("span", { class: ["trl-prov", `trl-prov--${p.kind}`] }, label[p.kind]);
  },
});

/**
 * Teal for this run's share, grey for everything else in use, hatched red for
 * what it is short of; an unknown figure is a dashed empty track.
 */
const Meter = defineComponent({
  props: {
    figure: { type: Object, required: true },
    overMb: { type: Number, default: 0 },
  },
  setup(p) {
    return () => {
      const { total_mb: total, used_mb: used, ours_mb: ours } = p.figure;
      if (!total) {
        return h("span", { class: "trl-meter", "aria-hidden": "true" }, [
          h("span", { class: "trl-m-unknown" }),
        ]);
      }
      const pct = (mb) => `${Math.max(0, Math.min(100, (mb / total) * 100))}%`;
      const segments = [];
      if (ours != null) segments.push(h("span", { class: "trl-m-ours", style: { width: pct(ours) } }));
      segments.push(
        h("span", {
          class: "trl-m-other",
          style: { width: pct(Math.max(0, (used ?? 0) - (ours ?? 0))) },
        }),
      );
      if (p.overMb) segments.push(h("span", { class: "trl-m-over", style: { width: pct(p.overMb) } }));
      return h("span", { class: "trl-meter", "aria-hidden": "true" }, segments);
    };
  },
});

// ── Derived ────────────────────────────────────────────────────────────────

const columns = computed(() => live.value?.columns ?? []);

const LIVE_STATUSES = ["queued", "running", "stopping"];
const isLive = computed(
  () =>
    live.value != null &&
    (LIVE_STATUSES.includes(live.value.status) || live.value.phase === "importing"),
);
const canStop = computed(() => LIVE_STATUSES.includes(live.value?.status));
const busy = computed(() => ["previews", "saving", "importing"].includes(live.value?.phase));

/** A step's position on the column axis: column i's centre is i + 0.5. */
function stepPosition(step) {
  const cols = columns.value;
  if (!cols.length) return 0;
  if (step <= cols[0]) return 0.5;
  for (let i = 0; i < cols.length - 1; i += 1) {
    if (step <= cols[i + 1]) {
      return i + 0.5 + (step - cols[i]) / (cols[i + 1] - cols[i] || 1);
    }
  }
  return cols.length - 0.5;
}

/** The chart's injected x-scale: a step's fraction of the columns' width. */
const stepFraction = computed(() => {
  const n = columns.value.length || 1;
  return (step) => stepPosition(step) / n;
});

const fillPercent = computed(() => {
  if (!live.value || !live.value.step) return 0;
  return (stepPosition(live.value.step) / (columns.value.length || 1)) * 100;
});

const progressPercent = computed(() => {
  const total = live.value?.total_steps;
  return total ? Math.min(100, (live.value.step / total) * 100) : 0;
});

const coverIndex = computed(() =>
  live.value?.cover_step == null ? -1 : columns.value.indexOf(live.value.cover_step),
);

/** The column a preview round now drawing belongs to. */
const drawingIndex = computed(() => {
  const step = live.value?.step ?? 0;
  let index = -1;
  columns.value.forEach((column, i) => {
    if (column <= step) index = i;
  });
  return index;
});

function tickOf(i) {
  if (live.value?.phase === "previews" && i === drawingIndex.value) return "now";
  const rows = live.value?.rows ?? [];
  const drawn = rows.length > 0 && rows.every((row) => row.cells[i]?.state === "sample");
  if (drawn) return "done";
  if (live.value?.previews_deferred && columns.value[i] > 0) return "def";
  return "todo";
}

const markers = computed(() =>
  columns.value.map((step, i) => ({
    step,
    kind: i === coverIndex.value ? "cover" : "checkpoint",
  })),
);

const groups = computed(() => {
  const rows = live.value?.rows ?? [];
  const withWord = rows.filter((row) => row.with_trigger === true);
  const without = rows.filter((row) => row.with_trigger === false);
  // Rows whose prompt the run's config did not give: no claim either way.
  const unknown = rows.filter((row) => row.with_trigger == null);
  // Grid rows: 1 timeline, 2 loss, then each group's heading and its prompts.
  const out = [];
  let next = 3;
  if (withWord.length) {
    out.push({
      key: "with",
      title: "With the trigger word",
      hint: "what the LoRA learned",
      rows: withWord,
      row: next,
    });
    next += 1 + withWord.length;
  }
  if (without.length) {
    out.push({
      key: "without",
      title: "Without it",
      hint: "should not change from step 0",
      rows: without,
      row: next,
    });
    next += 1 + without.length;
  }
  if (unknown.length) {
    out.push({
      key: "unknown",
      title: "Previews",
      hint: "the run's prompts could not be read",
      rows: unknown,
      row: next,
    });
  }
  return out;
});

/** Every grid row: the timeline, the loss, each group's heading and prompts. */
const rowCount = computed(() =>
  groups.value.reduce((n, group) => n + 1 + group.rows.length, 2),
);

const phaseLog = computed(() => live.value?.phases ?? []);
const lastPhase = computed(() => phaseLog.value[phaseLog.value.length - 1]?.phase);
const setupDone = computed(
  () =>
    phaseLog.value.some((entry) => !SETUP.includes(entry.phase)) ||
    (live.value?.step ?? 0) > 0,
);
const setupSeconds = computed(() =>
  phaseLog.value
    .filter((entry) => SETUP.includes(entry.phase))
    .reduce((sum, entry) => sum + (entry.seconds || 0), 0),
);
const setupTooltip = computed(() =>
  phaseLog.value
    .filter((entry) => SETUP.includes(entry.phase))
    .map((entry) => `${PHASE_LABELS[entry.phase]}: ${clock(entry.seconds)}`)
    .join(" · "),
);

const endPhases = computed(() => {
  const phase = live.value?.phase;
  const finalDrawn = live.value?.imported || phase === "importing";
  return [
    {
      key: "saving",
      label: "Saving",
      state: finalDrawn ? "done" : phase === "saving" && drawingIndex.value === columns.value.length - 1 ? "now" : "todo",
    },
    {
      key: "importing",
      label: "Importing to the shelf",
      state: live.value?.imported ? "done" : phase === "importing" ? "now" : "todo",
    },
  ];
});

const headline = computed(() => {
  const run = live.value;
  if (!run) return "";
  const progress = run.total_steps
    ? `Training ${run.step.toLocaleString()} / ${run.total_steps.toLocaleString()}`
    : "Training";
  switch (run.status) {
    case "queued":
      return PHASE_LABELS.waiting_gpu;
    case "stopping":
      return `Stopping at step ${run.step.toLocaleString()}`;
    case "stopped":
      return `Stopped at step ${run.step.toLocaleString()}`;
    case "error":
      return "Failed";
    case "completed":
      return run.phase === "importing" ? PHASE_LABELS.importing : "Finished";
    default:
      return SETUP.includes(run.phase) ? PHASE_LABELS[run.phase] : progress;
  }
});

const subline = computed(() => {
  const run = live.value;
  if (!run) return "";
  if (run.phase === "previews" && run.previews) {
    return `Generating previews ${run.previews.drawing} of ${run.previews.total}`;
  }
  if (run.phase === "saving") return "Saving a checkpoint";
  if (run.status === "completed" && run.imported) return "On the shelf";
  return "";
});

const notice = computed(() => {
  const run = live.value;
  if (!run) return null;
  // A read that failed after the page drew: say so, or the board sits on
  // stale figures looking current.
  if (error.value) {
    return {
      kind: "err",
      icon: "mdi-alert",
      title: notFound ? "This run is gone." : "The run stopped updating.",
      text: error.value,
    };
  }
  if (run.waiting?.code === "ram") {
    return {
      kind: "err",
      icon: "mdi-alert",
      title: "Not enough system RAM to start.",
      text: `${run.waiting.text || ""} It starts by itself once enough is free; close other programs, or stop it.`,
    };
  }
  if (run.waiting) {
    return { kind: "info", icon: "mdi-information-outline", title: "Waiting for the GPU.", text: run.waiting.text || "" };
  }
  if (run.telemetry.paging) {
    return {
      kind: "warn",
      icon: "mdi-alert",
      title: "Disk paging detected.",
      text: "The machine is swapping memory to disk, which slows training down a lot. Closing other programs usually fixes it.",
    };
  }
  if (run.finished_while_closed) {
    return {
      kind: "ok",
      icon: "mdi-weather-night",
      title: "Finished while PixlStash was closed",
      text: `at ${timeOfDay(run.finished_while_closed.at)}. Loss and previews were read from the run folder; memory for that stretch was not recorded.`,
    };
  }
  if (run.status === "error") {
    return { kind: "err", icon: "mdi-alert", title: "The run failed.", text: run.error || "" };
  }
  return null;
});

const lossValue = computed(() => {
  const value = chartRef.value?.latestSmoothed;
  if (value == null || !Number.isFinite(value)) return "Unknown";
  return value < 0.01 ? value.toExponential(2) : value.toFixed(3);
});

const gpu = computed(() => live.value?.telemetry.gpu ?? {});
const ram = computed(() => live.value?.telemetry.ram ?? {});

/** How far a run waiting for RAM is short, from its profile's estimate. */
const ramShortMb = computed(() => {
  const run = live.value;
  const need = run?.telemetry.need.ram_mb;
  if (run?.waiting?.code !== "ram" || !need || !ram.value.total_mb || ram.value.used_mb == null) {
    return 0;
  }
  return Math.max(0, need - (ram.value.total_mb - ram.value.used_mb));
});

const ramText = computed(() => {
  if (ramShortMb.value && ram.value.used_mb != null) {
    const free = ram.value.total_mb - ram.value.used_mb;
    return `needs ${gb(live.value.telemetry.need.ram_mb)}, ${gb(free)} free`;
  }
  return figureText(ram.value);
});

const remaining = computed(() => {
  const { remaining_low_s: low, remaining_high_s: high } = live.value?.speed ?? {};
  if (low == null || high == null) return "";
  return span(low, high);
});

const pictures = computed(() => live.value?.dataset_picture_ids ?? []);
const subjectName = computed(() => {
  const run = live.value;
  if (!run) return "";
  if (run.character_id != null) return names.characterNames[run.character_id] || "";
  if (run.set_id != null) return names.setNames[run.set_id] || "";
  return "";
});

// ── Formatting ──────────────────────────────────────────────────────────────

function gb(mb) {
  return `${(mb / 1024).toFixed(1)} GB`;
}

function figureText(figure) {
  if (!figure.total_mb || figure.used_mb == null) return "Unknown";
  return `${(figure.used_mb / 1024).toFixed(1)} / ${gb(figure.total_mb)}`;
}

function seconds(value) {
  return value < 10 ? `${value.toFixed(1)} s` : `${Math.round(value)} s`;
}

/** 155 → "2:35"; an hour or more → "1:02:35". */
function clock(total) {
  const s = Math.max(0, Math.round(total || 0));
  const hh = Math.floor(s / 3600);
  const mm = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return hh ? `${hh}:${String(mm).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}

function minutes(value) {
  const m = Math.max(1, Math.round(value / 60));
  if (m < 60) return `${m} min`;
  const hh = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${hh} h ${rest} min` : `${hh} h`;
}

/** The time left as a range: "16–21 min", "1 h 40 min – 3 h". */
function span(low, high) {
  const a = minutes(low);
  const b = minutes(high);
  if (a === b) return `about ${a}`;
  if (high < 3600) return `${a.replace(" min", "")}–${b}`;
  return `${a} – ${b}`;
}

function timeOfDay(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** The prompt split around its trigger word, which is drawn as a token. */
function promptParts(prompt) {
  const word = (live.value?.trigger_word || "").trim();
  if (!prompt || !word) return [{ text: prompt || "", trigger: false }];
  const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return prompt
    .split(new RegExp(`((?<!\\w)${escaped}(?!\\w))`, "i"))
    .filter((text) => text !== "")
    .map((text) => ({ text, trigger: text.toLowerCase() === word.toLowerCase() }));
}

function sampleUrl(cell) {
  const folder = live.value?.run_folder;
  if (!folder || !cell.filename) return "";
  return runSampleUrl(folder.folder_id, folder.run_name, cell.filename);
}

// ── Actions ─────────────────────────────────────────────────────────────────

async function useStep(step) {
  choosing.value = true;
  try {
    await setTrainingJobCover(props.jobId, step);
    await load();
    schedule();
  } catch (err) {
    useNoticeStore().push({
      level: "error",
      text: errorDetail(err) || `Could not make step ${step} the cover.`,
    });
  } finally {
    choosing.value = false;
  }
}

async function stop() {
  stopping.value = true;
  try {
    await cancelTrainingJob(props.jobId);
    await load();
    schedule();
  } catch (err) {
    useNoticeStore().push({
      level: "error",
      text: errorDetail(err) || "Could not stop the run.",
    });
  } finally {
    stopping.value = false;
  }
}

// ── Polling ─────────────────────────────────────────────────────────────────

let timer = null;
let generation = 0;
// A run this library does not hold will not appear by asking again.
let notFound = false;

async function load() {
  const gen = ++generation;
  try {
    const body = await getTrainingJobLive(props.jobId);
    if (gen !== generation) return;
    live.value = body;
    error.value = "";
    notFound = false;
  } catch (err) {
    if (gen !== generation) return;
    notFound = err?.response?.status === 404;
    error.value = notFound
        ? "That training run is not in this library."
        : errorDetail(err) || "Could not read the run.";
  }
}

function schedule() {
  clearTimeout(timer);
  timer = null;
  if (document.visibilityState !== "visible" || notFound) return;
  if (live.value && !isLive.value) return;
  timer = setTimeout(async () => {
    await load();
    schedule();
  }, POLL_MS);
}

function onVisible() {
  if (document.visibilityState !== "visible") clearTimeout(timer);
  else if (!notFound) load().then(schedule);
}

watch(
  () => props.jobId,
  () => {
    // The old run's timer and any read in flight are dropped, or two poll
    // chains would run from here on.
    generation += 1;
    clearTimeout(timer);
    live.value = null;
    error.value = "";
    notFound = false;
    load().then(schedule);
  },
);

onMounted(() => {
  load().then(schedule);
  document.addEventListener("visibilitychange", onVisible);
});

onBeforeUnmount(() => {
  generation += 1;
  clearTimeout(timer);
  document.removeEventListener("visibilitychange", onVisible);
});
</script>

<style scoped>
/* The step axis: the label column, then one fixed pitch per checkpoint so a
   column's centre is exactly its step on the curve, then the end phases. */
.trl {
  --trl-label-w: 224px;
  --trl-pitch: 124px;
  --trl-cell-w: 104px;
  --trl-cell-h: 76px;
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.trl-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 1;
  min-width: 0;
}

.trl-name {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 24ch;
}

.trl-vsep {
  width: 1px;
  height: 22px;
  background: rgb(var(--v-theme-divider));
}

.trl-status {
  display: inline-flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  white-space: nowrap;
  min-width: 0;
}

.trl-status b {
  color: rgb(var(--v-theme-on-surface));
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.trl-tbar {
  width: 72px;
  height: 4px;
  border-radius: var(--radius-pill);
  background: var(--track-trough);
  overflow: hidden;
  display: inline-block;
}

.trl-tbar i {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-tertiary));
}

.trl-spacer {
  flex: 1;
}

.trl-note {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-5);
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}

/* ── notices: the glyph and the rail carry the hue ── */
.trl-notice {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: var(--space-3) var(--space-3) 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-border));
  font-size: var(--text-sm);
  max-width: 900px;
}

.trl-notice p {
  margin: 0;
}

.trl-notice b {
  font-weight: var(--weight-semibold);
}

.trl-notice-glyph {
  margin-top: var(--space-1);
}

.trl-notice--err {
  box-shadow: inset 3px 0 0 rgb(var(--v-theme-surface-error));
}
.trl-notice--err .trl-notice-glyph {
  color: rgb(var(--v-theme-surface-error));
}
.trl-notice--warn {
  box-shadow: inset 3px 0 0 rgb(var(--v-theme-surface-warning));
}
.trl-notice--warn .trl-notice-glyph {
  color: rgb(var(--v-theme-surface-warning));
}
.trl-notice--info {
  box-shadow: inset 3px 0 0 rgb(var(--v-theme-surface-info));
}
.trl-notice--info .trl-notice-glyph {
  color: rgb(var(--v-theme-surface-info));
}
.trl-notice--ok {
  box-shadow: inset 3px 0 0 rgb(var(--v-theme-surface-success));
}
.trl-notice--ok .trl-notice-glyph {
  color: rgb(var(--v-theme-surface-success));
}

/* ── the board ── */
.trl-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: var(--space-4);
  padding: var(--space-3) var(--space-3) 0;
}

/* Long runs scroll sideways rather than shrinking the samples, so cells stay
   comparable (the design's "not decided" item, settled on its own advice). */
.trl-scroll {
  flex: 1;
  min-width: 0;
  overflow: auto;
}

.trl-board {
  position: relative;
  display: grid;
  grid-template-columns: var(--trl-label-w) repeat(var(--trl-cols), var(--trl-pitch));
  grid-auto-rows: auto;
  row-gap: var(--space-1);
  width: max-content;
  padding-bottom: var(--space-5);
}

.trl-frame {
  justify-self: center;
  width: calc(var(--trl-cell-w) + var(--space-4));
  border-radius: var(--radius-md);
  pointer-events: none;
}

.trl-frame--cover {
  box-shadow: inset 0 0 0 var(--space-1) var(--active-bar);
  background: var(--active-wash);
}

.trl-frame--hover {
  background: var(--hover-wash);
}

.trl-lab {
  grid-column: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--space-1);
  padding: 0 var(--space-3) 0 var(--space-2);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.trl-setup {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  white-space: nowrap;
}

.trl-setup--err {
  white-space: normal;
  color: rgb(var(--v-theme-on-background));
  font-weight: var(--weight-semibold);
}

.trl-setup--err .v-icon {
  color: rgb(var(--v-theme-surface-error));
}

.trl-track {
  align-self: center;
  height: 4px;
  margin-top: var(--space-1);
  border-radius: var(--radius-pill);
  background: var(--track-trough);
  box-shadow: inset 0 0 0 1px var(--track-ring);
  overflow: hidden;
}

.trl-track i {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-tertiary));
}

.trl-step {
  position: relative;
  display: grid;
  grid-template-rows: auto 14px auto;
  justify-items: center;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) 0;
}

.trl-stepn {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.trl-stepn--fut {
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  font-weight: var(--weight-regular);
}

.trl-tick {
  width: 10px;
  height: 10px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
}

.trl-tick--done {
  background: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}

.trl-tick--todo {
  border: 1.5px solid rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  background: rgb(var(--v-theme-background));
}

.trl-tick--def {
  border: 1.5px dashed rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  background: rgb(var(--v-theme-background));
}

.trl-stepsub {
  min-height: 18px;
  display: flex;
  align-items: center;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  white-space: nowrap;
}

.trl-badge-cover {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-weight: var(--weight-semibold);
  line-height: 16px;
}

/* Shown on the column's hover and on keyboard focus; hidden by opacity, never
   by v-if, so the target does not pop into existence under the pointer. */
.trl-use {
  height: 18px;
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-background));
  font: inherit;
  font-size: var(--text-2xs);
  cursor: pointer;
  opacity: 0;
  transition: opacity var(--dur-1) var(--ease-standard);
}

.trl-step:hover .trl-use,
.trl-use:focus-visible {
  opacity: 1;
}

.trl-use:hover {
  background: var(--hover-wash);
}

.trl-endph {
  height: 62px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--space-1);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  white-space: nowrap;
}

.trl-endph span {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.trl-endph--cur {
  color: rgb(var(--v-theme-on-background));
  font-weight: var(--weight-semibold);
}

.trl-ring {
  width: 9px;
  height: 9px;
  border-radius: var(--radius-pill);
  border: 1.5px solid currentColor;
  display: inline-block;
}

.trl-loss {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.trl-lv {
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.trl-chart {
  align-self: center;
  padding: var(--space-2) 0;
}

.trl-gline {
  grid-column: 1 / -1;
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-2) var(--space-1);
}

.trl-hint,
.trl-muted {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}

/* Prompt rows alternate with the table group header's 4.5% ink tint; the
   cover's olive wash sits over it, so selection still wins. */
.trl-zebra {
  grid-column: 1 / -1;
  border-radius: var(--radius-sm);
}

.trl-zebra--on {
  background: color-mix(in srgb, rgb(var(--v-theme-on-background)) 4.5%, transparent);
}

.trl-prompt {
  display: block;
  align-self: center;
  font-size: var(--text-sm);
}

.trl-trig {
  font-family: var(--font-mono);
  padding: 0 3px;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-surface));
  border: 1px solid rgb(var(--v-theme-border));
}

.trl-cellwrap {
  display: flex;
  justify-content: center;
  align-items: center;
  padding: var(--space-1) 0;
}

.trl-cell {
  width: var(--trl-cell-w);
  height: var(--trl-cell-h);
  border-radius: var(--radius-sm);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
  text-align: center;
}

img.trl-cell {
  display: block;
  object-fit: cover;
  object-position: center 18%;
  background: rgb(var(--v-theme-surface));
}

.trl-cell--gen,
.trl-cell--queued {
  background: rgb(var(--v-theme-surface));
}

.trl-cell--future,
.trl-cell--deferred {
  border: 1px dashed rgb(var(--v-theme-border));
}

.trl-cell--deferred {
  background: repeating-linear-gradient(
    135deg,
    rgba(var(--v-theme-on-background), 0.04) 0 6px,
    transparent 6px 12px
  );
}

.trl-spin {
  width: 16px;
  height: 16px;
  border-radius: var(--radius-pill);
  border: 2px solid rgba(var(--v-theme-on-background), 0.25);
  border-top-color: rgb(var(--v-theme-on-background));
  animation: trl-spin 1s linear infinite;
  display: inline-block;
}

.trl-spin--sm {
  width: 12px;
  height: 12px;
}

@keyframes trl-spin {
  to {
    transform: rotate(360deg);
  }
}

/* Slowed, not stopped: a frozen ring reads as a bug. */
@media (prefers-reduced-motion: reduce) {
  .trl-spin {
    animation-duration: 2s !important;
    animation-iteration-count: infinite !important;
  }
}

/* ── the training pictures ── */
.trl-strip {
  flex: none;
  width: 140px;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--text-xs);
}

.trl-n {
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}

.trl-strip-grid {
  display: grid;
  grid-template-columns: repeat(2, 66px);
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.trl-strip-pic,
.trl-strip-more {
  width: 66px;
  height: 110px;
  border-radius: var(--radius-sm);
}

.trl-strip-pic {
  object-fit: cover;
  object-position: center 18%;
  background: rgb(var(--v-theme-surface));
}

.trl-strip-more {
  border: 1px dashed rgb(var(--v-theme-border));
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  color: rgba(var(--v-theme-on-background), var(--opacity-text-secondary));
}

/* ── the status bar ── */
.trl-statusbar {
  flex: none;
  height: var(--control-h-bar);
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding: 0 var(--space-5);
  border-top: 1px solid rgb(var(--v-theme-divider));
  background: rgb(var(--v-theme-toolbar));
  color: rgb(var(--v-theme-toolbar-text));
  font-size: var(--text-xs);
  white-space: nowrap;
  overflow-x: auto;
}

.trl-it {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.trl-v {
  font-variant-numeric: tabular-nums;
}

.trl-hl {
  font-weight: var(--weight-semibold);
}

.trl-lab2 {
  opacity: var(--opacity-text-secondary);
}

.trl-warn .v-icon {
  color: rgb(var(--v-theme-surface-warning));
}

.trl-warn b {
  font-weight: var(--weight-semibold);
}

/* Provenance is carried by shape, not hue. */
:deep(.trl-prov) {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-2xs);
  opacity: var(--opacity-text-secondary);
  white-space: nowrap;
}

:deep(.trl-prov)::before {
  content: "";
  width: 7px;
  height: 7px;
  border-radius: var(--radius-pill);
  flex: none;
}

:deep(.trl-prov--measured)::before {
  background: currentColor;
}

:deep(.trl-prov--estimated)::before {
  border: 1.5px dashed currentColor;
}

:deep(.trl-prov--none)::before {
  width: 8px;
  height: 2px;
  border-radius: var(--radius-pill);
  background: currentColor;
}

:deep(.trl-meter) {
  width: 72px;
  height: 8px;
  display: flex;
  gap: var(--space-1);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgb(var(--v-theme-surface));
}

:deep(.trl-m-ours) {
  background: rgb(var(--v-theme-tertiary));
}

:deep(.trl-m-other) {
  background: rgba(var(--v-theme-on-surface), 0.3);
}

:deep(.trl-m-over) {
  background: repeating-linear-gradient(
    135deg,
    rgb(var(--v-theme-error)) 0 3px,
    transparent 3px 6px
  );
}

:deep(.trl-m-unknown) {
  flex: 1;
  background: repeating-linear-gradient(
    90deg,
    rgba(var(--v-theme-on-surface), 0.18) 0 2px,
    transparent 2px 6px
  );
}
</style>
