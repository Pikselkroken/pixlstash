<template>
  <div class="trl">
    <!-- Run status rides in the shelf's own toolbar (the design's first
         decision): no second header, so the board starts higher. Drawn in
         place where there is no such bar. Never toggled after mount: Vue
         cannot move a teleport to a target it did not find at mount. -->
    <Teleport defer to="#shelf-run-bar" :disabled="!hasBar">
      <div class="trl-bar">
        <ViewBackButton
          label="Training runs"
          tooltip="Back to all runs (Esc)"
          @click="emit('close')"
        />
        <span class="trl-vsep" aria-hidden="true"></span>
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
            {{ stopLabel }}
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
        <div class="trl-notice-body">
          <p>
            <b>{{ notice.title }}</b>
            {{ notice.text }}
          </p>
          <!-- The trainer's own text, for pasting into an issue; the sentence
               above is the part someone can act on. -->
          <pre v-if="notice.full && showFull" class="trl-full" data-testid="trl-full">{{
            notice.full
          }}</pre>
        </div>
        <div v-if="notice.full" class="trl-notice-acts">
          <AppButton
            size="sm"
            variant="ghost"
            :icon-left="showFull ? 'chevron-up' : 'chevron-down'"
            :aria-expanded="String(showFull)"
            data-testid="trl-full-toggle"
            @click="showFull = !showFull"
          >
            Full message
          </AppButton>
          <AppButton
            size="sm"
            variant="ghost"
            icon-left="content-copy"
            data-testid="trl-full-copy"
            @click="copyFull(notice.full)"
          >
            Copy
          </AppButton>
        </div>
      </div>

      <!-- The page scrolls down only when the tips are opened on a window too
           short for them: the board is never squeezed to make room. -->
      <div ref="bodyRef" class="trl-body">
        <div
          ref="scrollRef"
          class="trl-scroll"
          :class="{ 'trl-scroll--moved': scrolledLeft }"
          @scroll="onBoardScroll"
        >
          <div
            ref="boardRef"
            class="trl-board"
            :style="{
              '--trl-cols': columns.length,
              '--trl-pitch': `${pitch}px`,
              '--trl-chart-h': `${chartHeight}px`,
            }"
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
              <!-- The prompts stay pinned while a long run scrolls sideways;
                   this brings the view back to the start. -->
              <AppButton
                v-if="scrolledLeft"
                class="trl-to-start"
                size="sm"
                variant="outline"
                icon-left="chevron-left"
                data-testid="trl-to-start"
                @click="scrollToStart"
              >
                Step 0
              </AppButton>
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
                <span v-else-if="i === failedIndex" class="trl-stepsub--err"
                  >failed here</span
                >
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
                <span v-else-if="dueIn(column)">{{ dueIn(column) }}</span>
              </span>
            </div>
            <!-- Where the track ends: the final save and the import. A run
                 that failed or was stopped never gets there, and one read from
                 its folder was not trained here. -->
            <div
              v-if="!fromFolder && !['error', 'stopped'].includes(live.status)"
              ref="endRef"
              class="trl-endph"
              :style="{ gridRow: 1, gridColumn: columns.length + 2 }"
            >
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
              ref="chartRowRef"
              class="trl-chart"
              :style="{ gridRow: 2, gridColumn: `2 / span ${columns.length}` }"
            >
              <LoraLossChart
                ref="chartRef"
                :curve="live.loss_curve"
                :x-scale="stepFraction"
                :markers="markers"
                :height="chartHeight"
                :empty-text="chartEmptyText"
                smooth
                compact
              />
            </div>

            <template v-for="(group, g) in groups" :key="group.key">
              <div class="trl-gline" :style="{ gridRow: group.row }">
                <span class="section-label">{{ group.title }}</span>
                <span class="trl-hint">{{ group.hint }}</span>
                <span v-if="group.badge" class="trl-mode" data-testid="trl-mode">{{
                  group.badge
                }}</span>
              </div>
              <template v-for="(row, r) in group.rows" :key="`r${row.index}`">
                <div
                  class="trl-lab trl-prompt"
                  :class="{ 'trl-lab--z': (r + g) % 2 === 0 }"
                  :style="{ gridRow: group.row + 1 + r }"
                >
                  <span>
                    <span v-if="roleOf(row)" class="trl-role" data-testid="trl-role">{{
                      roleOf(row).label
                    }}</span>
                    <template v-for="(part, p) in promptParts(row.prompt)" :key="p">
                      <span v-if="part.trigger" class="trl-trig">{{ part.text }}</span>
                      <template v-else>{{ part.text }}</template>
                    </template>
                    <span v-if="!row.prompt" class="trl-muted">Prompt {{ row.index + 1 }}</span>
                  </span>
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
                    role="button"
                    tabindex="0"
                    @click="openSample(row, i, cell)"
                    @keydown.enter="openSample(row, i, cell)"
                    @keydown.space.prevent="openSample(row, i, cell)"
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
                  <!-- A step the run never reached stays a quiet empty cell; only
                       one it passed without a picture says so. -->
                  <span v-else class="trl-cell trl-cell--future">{{
                    cell.state !== "future" && columns[i] <= live.step
                      ? "not drawn"
                      : columns[i] === 0 && settingUp
                        ? "after set-up"
                        : ""
                  }}</span>
                </div>
                <!-- What a control row should do, after its last column. -->
                <div
                  v-if="roleOf(row)"
                  class="trl-expect"
                  data-testid="trl-expect"
                  :style="{ gridRow: group.row + 1 + r, gridColumn: columns.length + 2 }"
                >
                  <span>{{ roleOf(row).expect }}</span>
                  <span class="trl-muted">{{ roleOf(row).detail }}</span>
                </div>
              </template>
            </template>
          </div>
        </div>

        <!-- What to look for: open by itself when it fits under the board,
             folded to its header when it does not, and left as the owner
             left it once they have toggled it. A run read from its folder
             was not watched and has no step to pick. -->
        <section v-if="!fromFolder" ref="tipsRef" class="trl-tips" aria-label="Tips">
          <SectionHeader
            label="Tips"
            :count="tipsOpen ? 'what to look for while it runs' : `${TIPS.length} tips on what to look for while it runs`"
            :collapsed="!tipsOpen"
            data-testid="trl-tips-toggle"
            @update:collapsed="toggleTips"
          />
          <!-- Folded by height, not v-if, so fit() can still measure the tips
               at the current width. -->
          <div class="trl-tips-fold" :class="{ 'trl-tips-fold--shut': !tipsOpen }">
            <div class="trl-tips-body" data-testid="trl-tips">
              <div v-for="tip in TIPS" :key="tip.title">
                <b>{{ tip.title }}</b>
                <p>{{ tip.text }}</p>
              </div>
            </div>
          </div>
        </section>
      </div>

      <!-- Memory and speed describe now, not a step: a status bar. A run
           read from its folder has none of it, and the notice says so. -->
      <div v-if="!fromFolder" class="trl-statusbar">
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
    <TrainingSampleDialog :sample="openedSample" @close="openedSample = null" />
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
  inject,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  unref,
  watch,
} from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "../widgets/AppButton.vue";
import LoraLossChart from "../widgets/LoraLossChart.vue";
import SectionHeader from "../widgets/SectionHeader.vue";
import Tooltip from "../widgets/Tooltip.vue";
import TrainingSampleDialog from "../widgets/TrainingSampleDialog.vue";
import ViewBackButton from "../widgets/ViewBackButton.vue";
import {
  cancelTrainingJob,
  getTrainingJobLive,
  setTrainingJobCover,
} from "../../api/training";
import { runSampleUrl } from "../../api/modelImports";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { errorDetail } from "../../utils/apiError";
import { copyText } from "../../utils/clipboard";
import { isTypingTarget } from "../../utils/dom.js";
import { pronounOf, roleText } from "../../utils/trainingPlan.js";
import {
  failureText,
  FOLDER_RUN_STATUS,
  folderRunLive,
  isBusyJob,
  isQueuedBehindRun,
  LIVE_STATUSES,
  stepsOf,
  WAITING_BEHIND_RUN,
} from "../../utils/trainingRuns.js";

// Either a job PixlStash trained, read from its live view, or a run trained
// elsewhere, drawn once from its folder listing entry (`run` + `folderId`).
const props = defineProps({
  jobId: { type: Number, default: null },
  run: { type: Object, default: null },
  folderId: { type: Number, default: null },
});
const emit = defineEmits(["close"]);

const POLL_MS = 2000;

const PHASE_LABELS = {
  waiting_gpu: "Waiting for the GPU",
  checking: "Checking memory and speed",
  loading: "Loading the model",
  caching: "Caching captions",
  training: "Training",
  previews: "Generating previews",
  saving: "Saving",
  importing: "Importing to the shelf",
};
const SETUP = ["waiting_gpu", "checking", "loading", "caching"];

const live = ref(null);
const error = ref("");
const fromFolder = computed(() => Boolean(props.run));
// The tab re-lists its folder when it is looked at again: follow it.
watch(
  () => [props.run, props.folderId],
  () => {
    if (props.run) live.value = folderRunLive(props.run, props.folderId);
  },
  { immediate: true },
);
// Whether the shelf has a bar slot for this page. Not read off the document:
// opened from another tab, the shelf mounts together with this page and its
// slot is not in the document yet, so a probe here drew the bar in the page.
// The deferred Teleport resolves the slot after the whole tree is in.
const hasBar = unref(inject("shelf-run-bar", false));
const hoverColumn = ref(null);
const choosing = ref(false);
const stopping = ref(false);
const showFull = ref(false);
const chartRef = ref(null);
const bodyRef = ref(null);
const scrollRef = ref(null);
const boardRef = ref(null);
const chartRowRef = ref(null);
const endRef = ref(null);
const tipsRef = ref(null);

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

const isLive = computed(() => isBusyJob(live.value));
// While it draws its deferred previews, the cancel skips the rest of them and
// imports the run as it is; once it is importing there is nothing to stop.
const canStop = computed(
  () => LIVE_STATUSES.includes(live.value?.status) || live.value?.phase === "previews",
);
const busy = computed(() => ["previews", "saving", "importing"].includes(live.value?.phase));
// Before training starts: the corner names the step, step 0 says it comes next.
const settingUp = computed(
  () =>
    !setupDone.value &&
    (SETUP.includes(live.value?.phase) || live.value?.status === "queued"),
);

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

/**
 * The column a failed run broke at: the first at or past its step that it did
 * not finish drawing. Not while it was still setting up (step 0): the set-up
 * label says that.
 */
const failedIndex = computed(() => {
  const run = live.value;
  if (run?.status !== "error" || !run.step) return -1;
  return columns.value.findIndex(
    (column, i) => column >= run.step && tickOf(i) !== "done",
  );
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
    // A person's run says which control is a lookalike and which a stranger,
    // and whether it kept the words that describe them generic.
    const kept = live.value?.preservation;
    const roles = kept && without.some((row) => row.role);
    out.push({
      key: "without",
      title: "Without it",
      hint: roles
        ? `one person like ${pronounOf(kept.description)}, one who is not`
        : "should change little from step 0",
      badge: roles
        ? kept.on
          ? `“${kept.description}” kept generic`
          : "words left free"
        : null,
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

/** A control row's label and what it should do; null for any other row. */
function roleOf(row) {
  const kept = live.value?.preservation;
  if (!kept || !row.role) return null;
  return roleText(row.role, { kept: kept.on, description: kept.description });
}

/** Every grid row: the timeline, the loss, each group's heading and prompts. */
const rowCount = computed(() =>
  groups.value.reduce((n, group) => n + 1 + group.rows.length, 2),
);

const phaseLog = computed(() => live.value?.phases ?? []);
const lastPhase = computed(() => phaseLog.value[phaseLog.value.length - 1]?.phase);
// A run read from its folder kept no set-up timings to claim.
const setupDone = computed(
  () =>
    !fromFolder.value &&
    (phaseLog.value.some((entry) => !SETUP.includes(entry.phase)) ||
      (live.value?.step ?? 0) > 0),
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
      // A probe runs while the row is still queued, with the GPU in hand.
      if (run.phase === "checking") return PHASE_LABELS.checking;
      return isQueuedBehindRun(run.waiting) ? WAITING_BEHIND_RUN : PHASE_LABELS.waiting_gpu;
    case "stopping":
      return `Stopping at step ${run.step.toLocaleString()}`;
    case "stopped":
      return `Stopped at step ${run.step.toLocaleString()}`;
    case "error":
      return "Failed";
    case "completed":
      if (run.completed_at) return "Finished";
      return PHASE_LABELS[run.phase] ?? PHASE_LABELS.importing;
    case FOLDER_RUN_STATUS:
      return stepsOf({ run: props.run, job: null, live: false });
    default:
      // The board's corner names the set-up step; the bar only says which part.
      return SETUP.includes(run.phase) ? "Setting up" : progress;
  }
});

const stopLabel = computed(() => {
  if (live.value?.status === "stopping") return "Stopping…";
  return live.value?.status === "completed" ? "Skip previews" : "Stop training";
});

const subline = computed(() => {
  const run = live.value;
  if (!run) return "";
  if (run.phase === "previews" && run.previews) {
    return `Generating previews ${run.previews.drawing} of ${run.previews.total}`;
  }
  if (run.phase === "saving") return "Saving a checkpoint";
  if (run.downloading) return "Downloading the model, once per model";
  if (run.status === "completed" && run.imported) return "On the shelf";
  return "";
});

const notice = computed(() => {
  const run = live.value;
  if (!run) return null;
  if (fromFolder.value) {
    return {
      kind: "info",
      icon: "mdi-information-outline",
      title: "Trained outside PixlStash.",
      text: "This page is read from the run's folder: its checkpoints, previews and settings. PixlStash did not watch it train, so there is no loss curve, no memory or speed figures, and no record of the pictures it was trained on.",
    };
  }
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
    const title = isQueuedBehindRun(run.waiting) ? `${WAITING_BEHIND_RUN}.` : "Waiting for the GPU.";
    return { kind: "info", icon: "mdi-information-outline", title, text: run.waiting.text || "" };
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
  // One sentence, written by the backend from the trainer's text, which sits
  // behind Full message when it says more.
  if (run.status === "error") {
    const { short, full } = failureText(run);
    return { kind: "err", icon: "mdi-alert", title: short || "The run failed.", text: "", full };
  }
  return null;
});

// Undefined keeps the chart's own sentence for a run with no curve to show.
const chartEmptyText = computed(() =>
  isLive.value && !live.value?.step
    ? "The curve starts with the first training step."
    : undefined,
);

/**
 * When a future column is due, from the same estimate as the status bar's
 * "left", spread over the steps still to train: "~6 min".
 */
function dueIn(column) {
  const run = live.value;
  const { remaining_low_s: low, remaining_high_s: high } = run?.speed ?? {};
  if (!run || !isLive.value || low == null || high == null) return "";
  const left = (run.total_steps ?? 0) - run.step;
  if (column <= run.step || left <= 0) return "";
  return `~${minutes(((low + high) / 2) * ((column - run.step) / left))}`;
}

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

/** The sample a click opened at full size, or null. */
const openedSample = ref(null);

function openSample(row, column, cell) {
  openedSample.value = {
    src: sampleUrl(cell),
    step: columns.value[column],
    prompt: row.prompt || `Prompt ${row.index + 1}`,
  };
}

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
  // The same confirmation as Settings' Cancel: a stopped run cannot resume.
  // Skip previews (a trained run) keeps the model, so it does not ask.
  if (
    live.value?.status !== "completed" &&
    !window.confirm(
      `Stop training "${live.value?.name}"? The run will stop and cannot be resumed.`,
    )
  ) {
    return;
  }
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

async function copyFull(text) {
  const copied = await copyText(text);
  if (!copied) console.warn(`Could not copy training run ${props.jobId}'s message`);
  useNoticeStore().push(
    copied
      ? { level: "success", text: "Copied the full message." }
      : { level: "error", text: "Could not copy the message." },
  );
}

// ── Fitting the board to the window ─────────────────────────────────────────

// The pitch, and the samples with it, come from the width the board has;
// past PITCH_MIN the board scrolls sideways instead. The loss chart takes the
// height the board and the tips leave, between CHART_MIN and CHART_MAX.
const LABEL_W = 224;
const PITCH_MIN = 124;
const PITCH_MAX = 240;
const CHART_MIN = 92;
const CHART_MAX = 280;
// The cells keep 104 x 76 at the pitch less this gutter (the CSS's
// --trl-cell-w and --trl-cell-h say the same).
const CELL_GUTTER = 20;
const cellHeightAt = (p) => ((p - CELL_GUTTER) * 76) / 104;
const pitch = ref(PITCH_MIN);
const chartHeight = ref(CHART_MIN);

const TIPS = [
  {
    title: "With the trigger word",
    text: "The subject should show up over the first checkpoints. Step 0 is the base model on its own: the before picture.",
  },
  {
    title: "Without it",
    text: "These rows should stay close to step 0. If they drift toward the subject, the LoRA is leaking into everything and an earlier step is the better one.",
  },
  {
    title: "Loss",
    text: "Noisy is normal. Read the smoothed line's trend, not single dips. A flat curve does not mean training can stop.",
  },
  {
    title: "Pick the step",
    text: "The Use this step button under a column makes that step the cover: the checkpoint the set or character uses. With none picked, it is the last step.",
  },
];

// "open" or "shut" once toggled by hand; until then, whether they fit.
const TIPS_KEY = "pixlstash.trainingRunTips";
const tipsChoice = ref(readTipsChoice());
const tipsFit = ref(true);
const tipsOpen = computed(() =>
  tipsChoice.value ? tipsChoice.value === "open" : tipsFit.value,
);
// The open tips' height, measured folded or not, to judge whether they fit.
let tipsBodyHeight = 0;

function readTipsChoice() {
  try {
    const value = window.localStorage?.getItem(TIPS_KEY);
    return value === "open" || value === "shut" ? value : null;
  } catch (err) {
    console.warn("Could not read whether the training run tips were left open", err);
    return null;
  }
}

function toggleTips(collapsed) {
  tipsChoice.value = collapsed ? "shut" : "open";
  try {
    window.localStorage?.setItem(TIPS_KEY, tipsChoice.value);
  } catch (err) {
    console.warn("Could not remember the training run tips' state; it resets next time", err);
  }
  if (!collapsed) {
    nextTick(() => tipsRef.value?.scrollIntoView?.({ block: "nearest" }));
  }
}

function fit() {
  const body = bodyRef.value;
  const scroll = scrollRef.value;
  const board = boardRef.value;
  const row = chartRowRef.value;
  if (!body || !scroll || !board || !row) return;
  const n = columns.value.length || 1;
  const pad = getComputedStyle(scroll);
  const tipsFold = tipsRef.value?.querySelector(".trl-tips-fold");
  tipsBodyHeight = tipsRef.value?.querySelector(".trl-tips-body")?.offsetHeight ?? 0;
  const tipsHead = (tipsRef.value?.offsetHeight ?? 0) - (tipsFold?.offsetHeight ?? 0);
  // The scroller's own padding and its sideways scrollbar.
  const frame =
    scroll.offsetHeight -
    scroll.clientHeight +
    parseFloat(pad.paddingTop) +
    parseFloat(pad.paddingBottom);
  // Everything in the page but the chart, with the tips folded.
  const rest =
    board.offsetHeight - chartHeight.value + frame + tipsHead;

  const width =
    scroll.clientWidth -
    parseFloat(pad.paddingLeft) -
    parseFloat(pad.paddingRight) -
    LABEL_W -
    (endRef.value?.offsetWidth ?? 0);
  // Nor so tall that the prompt rows push the page past the window: a wide,
  // short window would otherwise scroll down.
  const rows = live.value?.rows?.length ?? 0;
  const cellsNow = rows * cellHeightAt(pitch.value);
  const tallest = rows
    ? Math.floor(((body.clientHeight - (rest - cellsNow) - CHART_MIN) / rows) * (104 / 76)) +
      CELL_GUTTER
    : PITCH_MAX;
  pitch.value = Math.max(
    PITCH_MIN,
    Math.min(PITCH_MAX, Math.floor(width / n), tallest),
  );

  // The height left for the chart with the tips folded.
  const room = body.clientHeight - rest;
  tipsFit.value = room - tipsBodyHeight >= CHART_MIN;
  const left = room - (tipsOpen.value ? tipsBodyHeight : 0);
  chartHeight.value = Math.max(CHART_MIN, Math.min(CHART_MAX, left));
}

// The tips are watched too: opening them by themselves resizes nothing else,
// and the chart must give back their height.
let resizer = null;
watch([bodyRef, scrollRef, boardRef, tipsRef], ([body, scroll, board, tips]) => {
  resizer?.disconnect();
  resizer = null;
  if (!body || !scroll || !board || typeof ResizeObserver === "undefined") return;
  resizer = new ResizeObserver(() => fit());
  [body, scroll, board, tips].filter(Boolean).forEach((el) => resizer.observe(el));
});

// A long run scrolls sideways under the pinned prompts. The view follows the
// newest checkpoint until the owner scrolls it themselves, and again once they
// scroll back to the end.
const scrolledLeft = ref(false);
let following = true;
let placedAt = null;

function onBoardScroll() {
  const el = scrollRef.value;
  if (!el) return;
  scrolledLeft.value = el.scrollLeft > 0;
  if (placedAt !== null && Math.abs(el.scrollLeft - placedAt) <= 1) return;
  placedAt = null;
  following = el.scrollLeft + el.clientWidth >= el.scrollWidth - 1;
}

function followNewest() {
  const el = scrollRef.value;
  if (!el || !following || el.scrollWidth <= el.clientWidth) return;
  // The newest checkpoint and the one after it, at the right edge.
  // The board starts after the scroller's own left padding.
  const shown = Math.min(columns.value.length, drawingIndex.value + 2);
  const padLeft = parseFloat(getComputedStyle(el).paddingLeft) || 0;
  const target = Math.max(0, padLeft + LABEL_W + shown * pitch.value - el.clientWidth);
  if (Math.abs(el.scrollLeft - target) <= 1) return;
  placedAt = Math.min(target, el.scrollWidth - el.clientWidth);
  el.scrollLeft = target;
}

function scrollToStart() {
  following = false;
  placedAt = null;
  scrollRef.value?.scrollTo({ left: 0, behavior: "smooth" });
}

watch([drawingIndex, pitch, () => columns.value.length], () => nextTick(followNewest));

// ── Polling ─────────────────────────────────────────────────────────────────

let timer = null;
let generation = 0;
// A run this library does not hold will not appear by asking again.
let notFound = false;
// A read in flight at unmount still reaches schedule(); it must not re-arm.
let disposed = false;

async function load() {
  // A run read from its folder has no job to ask about.
  if (props.jobId == null) return;
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
  if (disposed || document.visibilityState !== "visible" || notFound) return;
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
    showFull.value = false;
    notFound = false;
    // Another run's board starts at its newest checkpoint again.
    scrolledLeft.value = false;
    following = true;
    placedAt = null;
    load().then(schedule);
  },
);

const sidebarStore = useSidebarStore();

/**
 * Escape leaves the run, as the back button's keycap says, unless something
 * in front of the page owns the key: a dialog or menu (the sample viewer is
 * one), the lightbox, the review overlay, the auto-hide sidebar, a text
 * field, a focused row in the Models rail.
 *
 * Capture phase, so this reads that state BEFORE the owners act on the key:
 * the app's handler hides the sidebar on the same press and does not stop
 * it, and it registered first, so in the bubble phase the sidebar would
 * already read as hidden and the one press would also leave the run.
 */
function onKeydown(event) {
  if (event.key !== "Escape") return;
  if (isTypingTarget(event.target)) return;
  if (event.target?.closest?.(".mrail")) return;
  if (sidebarStore.sidebarOverlay && sidebarStore.sidebarVisible) return;
  if (
    document.querySelector(
      ".v-overlay--active:not(.v-tooltip), .lightbox, .rs-overlay",
    )
  ) {
    return;
  }
  emit("close");
}

onMounted(() => {
  load().then(schedule);
  document.addEventListener("visibilitychange", onVisible);
  window.addEventListener("keydown", onKeydown, true);
});

onBeforeUnmount(() => {
  disposed = true;
  generation += 1;
  clearTimeout(timer);
  document.removeEventListener("visibilitychange", onVisible);
  window.removeEventListener("keydown", onKeydown, true);
  resizer?.disconnect();
});
</script>

<style scoped>
/* The step axis: the label column, then one fixed pitch per checkpoint so a
   column's centre is exactly its step on the curve, then the end phases. */
.trl {
  --trl-label-w: 224px;
  --trl-pitch: 124px;
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

.trl-notice-body {
  flex: 1;
  min-width: 0;
}

.trl-notice p {
  margin: 0;
}

.trl-notice-acts {
  display: flex;
  flex: none;
  gap: var(--space-2);
  margin: calc(-1 * var(--space-2)) calc(-1 * var(--space-2)) 0 0;
}

.trl-full {
  margin: var(--space-3) 0 0;
  max-height: 240px;
  overflow: auto;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
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
  flex-direction: column;
  overflow-y: auto;
}

/* Long runs scroll sideways rather than shrinking the samples, so cells stay
   comparable (the design's "not decided" item, settled on its own advice).
   It fills the height the tips leave, so the tips sit at the bottom. */
.trl-scroll {
  flex: 1 0 auto;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  padding: var(--space-3) var(--space-3) 0;
}

/* The pitch is set on the board from the window (fit()); the samples grow
   with it at today's 104 x 76 shape, keeping the same gutter between columns.
   Declared here, not on .trl, so they read the board's pitch. */
.trl-board {
  --trl-cell-w: calc(var(--trl-pitch) - var(--space-4) - var(--space-3));
  --trl-cell-h: calc(var(--trl-cell-w) * 76 / 104);
  position: relative;
  display: grid;
  grid-template-columns: var(--trl-label-w) repeat(var(--trl-cols), var(--trl-pitch)) auto;
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

/* The labels stay pinned while a long run scrolls sideways: opaque, over the
   columns passing under them, with a divider once the view has moved. */
.trl-lab {
  grid-column: 1;
  position: sticky;
  left: calc(-1 * var(--space-3));
  z-index: var(--z-sticky);
  background: rgb(var(--v-theme-background));
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: var(--space-1);
  padding: 0 var(--space-3) 0 var(--space-2);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.trl-lab--z {
  background:
    linear-gradient(
      color-mix(in srgb, rgb(var(--v-theme-on-background)) 4.5%, transparent),
      color-mix(in srgb, rgb(var(--v-theme-on-background)) 4.5%, transparent)
    ),
    rgb(var(--v-theme-background));
}

.trl-scroll--moved .trl-lab {
  box-shadow: inset -1px 0 0 rgb(var(--v-theme-divider));
}

.trl-to-start {
  align-self: flex-start;
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

.trl-stepsub--err {
  color: rgb(var(--v-theme-surface-error));
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
  padding-left: var(--space-4);
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

/* Exactly the chart's height, so fit() can tell the chart from the rest. */
.trl-chart {
  align-self: center;
  box-sizing: content-box;
  height: var(--trl-chart-h);
  padding: var(--space-2) 0;
  display: flex;
  flex-direction: column;
  justify-content: center;
}

.trl-chart :deep(.lora-loss-empty) {
  text-align: center;
}

.trl-gline {
  grid-column: 1 / -1;
  justify-self: start;
  position: sticky;
  left: calc(-1 * var(--space-3));
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

/* Full row height, so the pinned label's own background covers its row. */
.trl-prompt {
  font-size: var(--text-sm);
}

/* The run's mode and a control row's role: neutral count badges. */
.trl-mode,
.trl-role {
  padding: 0 var(--space-3);
  border-radius: var(--radius-pill);
  background: color-mix(in srgb, rgb(var(--v-theme-on-background)) 12%, transparent);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  line-height: var(--badge-size);
  white-space: nowrap;
}

.trl-role {
  display: inline-block;
  margin-right: var(--space-2);
}

.trl-expect {
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding-left: var(--space-4);
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  white-space: nowrap;
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
  cursor: zoom-in;
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

/* ── the tips ── */
.trl-tips {
  flex: none;
  padding: 0 var(--space-4);
  border-top: 1px solid rgb(var(--v-theme-divider));
}

/* Folded: no height and out of the accessibility tree, still laid out. */
.trl-tips-fold--shut {
  height: 0;
  overflow: hidden;
  visibility: hidden;
}

.trl-tips-body {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: var(--space-4) var(--space-6);
  padding: 0 0 var(--space-5) var(--space-6);
  font-size: var(--text-sm);
}

.trl-tips-body b {
  font-weight: var(--weight-semibold);
}

.trl-tips-body p {
  margin: var(--space-1) 0 0;
  line-height: var(--leading-snug);
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
