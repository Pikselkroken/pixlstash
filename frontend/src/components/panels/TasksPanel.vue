<template>
  <!-- Deliberately multi-root: these are the inspector body's own flex
       children, exactly as they were when they lived in `StatsSidebar`. A
       wrapper div would make them one child and change the spacing in a rail
       this change is not supposed to touch. It also means `.tm-system-bar`'s
       `margin-top: auto` still resolves against the body: inert in the stats
       rail, which never gives `.inspector-body` a height, and a real pin to
       the bottom on `/workflows`, where the inspector's footer slot makes the
       body `flex: 1` (`.inspector-content--footed`). That difference is pre-existing and left alone. -->
  <div v-if="tasksStore.activeEntries.length === 0" class="tm-idle-msg">
    No active tasks
  </div>
  <div v-else class="tm-worker-list">
    <template v-for="entry in tasksStore.activeEntries" :key="entry.key">
      <!-- ComfyUI run: frontend-driven, shows a progress bar + abort -->
      <div v-if="entry.kind === 'comfyui'" class="tm-worker-row">
        <div class="tm-worker-row-top">
          <span class="tm-status-dot tm-status-dot--running"></span>
          <span class="tm-worker-label">{{ entry.run.label }}</span>
          <button
            v-if="entry.run.status !== 'completed'"
            class="tm-comfy-abort"
            type="button"
            aria-label="Abort ComfyUI run"
            @click="tasksStore.abortComfyuiRun(entry.key)"
          >
            <Tooltip
              text="Abort ComfyUI run"
              activator="parent"
              :describe="false"
            />
            ✕
          </button>
        </div>
        <div class="tm-comfy-bar">
          <div
            class="tm-comfy-fill"
            :style="{
              width: `${Math.min(100, Math.max(0, Math.round(entry.run.percent)))}%`,
            }"
          ></div>
        </div>
        <div class="tm-comfy-message">{{ entry.run.message }}</div>
      </div>
      <!-- Async import (#459): the two-phase dialog auto-hides at the safe
             transition and the import lands here as a determinate task row.
             data-import-task-row is the FLIP flight target the import dialog
             flies its count chip into. -->
      <div
        v-else-if="entry.kind === 'import'"
        class="tm-worker-row tm-import-row"
        :data-import-task-row="entry.key"
      >
        <div class="tm-worker-row-top">
          <span
            class="tm-status-dot"
            :class="{
              'tm-status-dot--running': entry.run.status === 'running',
            }"
          ></span>
          <span class="tm-worker-label">{{ entry.run.label }}</span>
          <span v-if="entry.run.total > 0" class="tm-worker-progress">
            {{ entry.run.current }} / {{ entry.run.total }}
          </span>
          <!-- Cancel is offered only while the import is genuinely
                 client-abortable (the pre-commit upload window, run.abortable).
                 A committed server-side import cannot be stopped from the
                 client, so no cancel control is shown for it. -->
          <button
            v-if="entry.run.abortable && !isReadOnly"
            class="tm-comfy-abort"
            type="button"
            aria-label="Cancel import"
            @click="tasksStore.abortImportRun(entry.key)"
          >
            <Tooltip
              text="Cancel import"
              activator="parent"
              :describe="false"
            />
            ✕
          </button>
        </div>
        <div class="tm-comfy-bar">
          <div
            class="tm-comfy-fill"
            :style="{
              width: `${Math.min(100, Math.max(0, Math.round(entry.run.percent)))}%`,
            }"
          ></div>
        </div>
        <div class="tm-comfy-message">{{ entry.run.message }}</div>
      </div>
      <!-- Backend worker: throughput sparkline + rate -->
      <div v-else class="tm-worker-row">
        <div class="tm-worker-row-top">
          <span
            class="tm-status-dot"
            :class="{
              'tm-status-dot--running':
                entry.snapshot.running || tmGetLatestRate(entry.key) > 0,
            }"
          ></span>
          <span class="tm-worker-label">{{
            tmFormatLabel(entry.key, entry.snapshot.label)
          }}</span>
          <span class="tm-worker-progress">{{
            tmFormatProgress(entry.snapshot)
          }}</span>
        </div>
        <div class="tm-worker-row-bottom">
          <canvas
            :ref="(el) => tmRegisterCanvas(entry.key, el)"
            class="tm-sparkline"
          ></canvas>
          <span class="tm-worker-rate">
            {{ tmFormatRate(tmGetLatestRate(entry.key)) }}/s
          </span>
        </div>
      </div>
    </template>
  </div>
  <div v-if="tmSystemItems.length" class="tm-system-bar">
    <div v-for="item in tmSystemItems" :key="item.label" class="tm-system-item">
      <span class="tm-system-label">{{ item.label }}</span>
      <span class="tm-system-value">{{ item.value }}</span>
    </div>
  </div>
</template>

<script>
/**
 * The tab that shows this panel, so its word and its glyph are declared once
 * for every inspector that offers it. `tasksTabFor` lights it up while work is
 * running, and is what a host imports.
 */
const TASKS_TAB = {
  value: "tasks",
  label: "Tasks",
  icon: "mdi-timeline-clock-outline",
};

/**
 * The descriptor an inspector declares for its Tasks tab, lit while the store
 * has work. One function so the two hosts cannot drift apart on the wording or
 * on when the light comes on.
 */
export function tasksTabFor(tasksStore) {
  if (!tasksStore.hasActiveTasks) return TASKS_TAB;
  const n = tasksStore.activeCount;
  return {
    ...TASKS_TAB,
    busy: true,
    busyTooltip: `${n} active task${n === 1 ? "" : "s"}`,
  };
}
</script>

<script setup>
// The task manager: the body of the Tasks tab, wherever an inspector offers it.
//
// It started inside StatsSidebar and is a component of its own because the
// Workflows view's inspector offers the same tab: somebody browsing
// workflows or recipes has to be able to watch the work their last run started
// without leaving the screen to do it. The image overlay is the one inspector
// that does not offer it - it is a picture's pane, laid over the picture.
//
// The data (worker snapshots, rate series, active-state, ComfyUI runs) lives in
// the shared tasks store, which is the single poller of /workers/progress. This
// component owns only the view: canvas sparkline drawing and the label / number
// formatting - and, while it is mounted, the store's fast poll cadence.

import { computed, onMounted, onUnmounted, watch } from "vue";

import { useTasksStore } from "../../stores/useTasksStore";
import { isReadOnly } from "../../utils/apiClient";
import Tooltip from "../widgets/Tooltip.vue";

const tasksStore = useTasksStore();
const TM_WINDOW_SECONDS = 120; // sparkline x-axis span, seconds
const tmCanvasRefs = new Map(); // key → { el, observer }

// Mounted is exactly "the Tasks tab is on screen": the inspector unmounts its
// body when the rail is collapsed, so the fast cadence follows what is actually
// visible rather than which tab was last picked. The store keeps polling either
// way (that's what drives the app-wide activity light); this only changes how
// often.
onMounted(() => tasksStore.setTasksTabOpen(true));

onUnmounted(() => {
  tasksStore.setTasksTabOpen(false);
  for (const { observer } of tmCanvasRefs.values()) observer.disconnect();
  tmCanvasRefs.clear();
});

function tmRegisterCanvas(key, el) {
  const existing = tmCanvasRefs.get(key);
  if (existing) {
    existing.observer.disconnect();
    tmCanvasRefs.delete(key);
  }
  if (!el) return;
  const observer = new ResizeObserver(() => {
    tmDrawSparkline(el, tasksStore.series[key] || []);
  });
  observer.observe(el);
  tmCanvasRefs.set(key, { el, observer });
  requestAnimationFrame(() =>
    tmDrawSparkline(el, tasksStore.series[key] || []),
  );
}

function tmGetThemeRgb(name) {
  if (typeof window === "undefined") return null;
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(`--v-theme-${name}`)
      .trim() || null
  );
}

function tmThemeRgba(name, alpha, fallback = "0,0,0") {
  const v = tmGetThemeRgb(name) || fallback;
  return `rgba(${v}, ${alpha})`;
}

function tmFormatRate(value) {
  const rate = Number(value || 0);
  if (rate >= 10) return rate.toFixed(0);
  if (rate >= 1) return rate.toFixed(1);
  return rate.toFixed(2);
}

function tmDrawAll() {
  for (const [key, { el }] of tmCanvasRefs.entries()) {
    tmDrawSparkline(el, tasksStore.series[key] || []);
  }
}

// The store reassigns its `series` ref on every poll; redraw the sparklines
// whenever it does. tmDrawAll is a no-op when no canvases are mounted (no
// backend worker running), so this is cheap to leave always-on.
watch(
  () => tasksStore.series,
  () => requestAnimationFrame(tmDrawAll),
);

function tmDrawSparkline(canvas, samples) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(
    4,
    Math.floor(rect.width || canvas.parentElement?.clientWidth || 200),
  );
  const height = Math.max(4, Math.floor(rect.height || 28));
  const tw = Math.floor(width * dpr);
  const th = Math.floor(height * dpr);
  if (canvas.width !== tw || canvas.height !== th) {
    canvas.width = tw;
    canvas.height = th;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);

  const plotSamples = samples.length
    ? samples
    : [{ rate: 0, t: Date.now() / 1000 }];
  const maxRate = Math.max(1, ...plotSamples.map((s) => s.rate || 0));
  const pad = 2;
  const plotW = width - pad * 2;
  const plotH = height - pad * 2;
  const tNow = Date.now() / 1000;
  const tMin = tNow - TM_WINDOW_SECONDS;
  const tRange = tNow - tMin;
  const tToX = (t) => pad + ((t - tMin) / tRange) * plotW;

  ctx.beginPath();
  plotSamples.forEach((s, i) => {
    const x = tToX(s.t ?? tNow);
    const y = pad + plotH * (1 - (s.rate || 0) / maxRate);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = tmThemeRgba("tertiary", 0.85, "142,166,4");
  ctx.lineWidth = 1.5;
  ctx.stroke();

  const last = plotSamples[plotSamples.length - 1];
  ctx.lineTo(tToX(last.t ?? tNow), pad + plotH);
  ctx.lineTo(pad, pad + plotH);
  ctx.closePath();
  ctx.fillStyle = tmThemeRgba("tertiary", 0.18, "142,166,4");
  ctx.fill();
}

const tmLabelMap = {
  quality_scored: "Quality",
  pictures_tagged: "Tags",
  descriptions_generated: "Descriptions",
  text_embeddings: "Text embeddings",
  image_embeddings: "Image embeddings",
  faces_extracted: "Faces extracted",
  likeness_pairs: "Likeness pairs",
  likeness_parameters: "Likeness params",
  watch_folder_import: "Folder import",
  comfyui_extraction: "ComfyUI backfill",
  tag_predictions_scored: "Tag Predictions",
  missing_file_purge: "File cleanup",
  snapshot_identity_scrub: "Snapshot cleanup",
  planner_managed: "Planner task",
  checkpoints_hashed: "Checkpoint Hash",
  workflows_carded: "Workflow cards",
  text_score: "Text score",
  text_read: "Text in pictures",
  object_detection: "Object detection",
  comfyui_workflow_pull: "ComfyUI workflow pull",
};

const tmWorkerLabelMap = {
  ReferenceFolderScanTask: "Reference folder scan",
  SourceFaceLikenessTask: "Source face likeness",
  SmartScoreTask: "Smart score",
};

function tmToTitleWords(value) {
  return String(value || "")
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim();
}

function tmFallbackWorkerLabel(key) {
  if (tmWorkerLabelMap[key]) return tmWorkerLabelMap[key];
  return tmToTitleWords(String(key || "").replace(/(Worker|Task)$/, ""));
}

function tmFormatLabel(key, label) {
  if (tmLabelMap[label]) {
    if (label === "planner_managed") return tmFallbackWorkerLabel(key);
    return tmLabelMap[label];
  }
  if (label && label !== "idle" && label !== "uninitialized") {
    return tmToTitleWords(label);
  }
  return tmFallbackWorkerLabel(key);
}

function tmFormatProgress(snapshot) {
  const current = Number(snapshot?.current || 0);
  const total = Number(snapshot?.total || 0);
  if (!total) return `${current}`;
  return `${current} / ${total}`;
}

function tmFormatPercent(value) {
  const percent = Number(value);
  if (!Number.isFinite(percent)) return "n/a";
  if (percent >= 10) return `${percent.toFixed(0)}%`;
  return `${percent.toFixed(1)}%`;
}

function tmFormatGigabytes(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "n/a";
  return `${amount.toFixed(1)} GB`;
}

function tmFormatUsage(used, total, percent) {
  const usedLabel = tmFormatGigabytes(used);
  if (Number.isFinite(total)) {
    return `${usedLabel} / ${tmFormatGigabytes(total)} (${tmFormatPercent(percent)})`;
  }
  const percentLabel = tmFormatPercent(percent);
  if (percentLabel !== "n/a") return `${usedLabel} (${percentLabel})`;
  return usedLabel;
}

function tmGetLatestRate(key) {
  return tasksStore.getLatestRate(key);
}

const tmSystemItems = computed(() => {
  const usage = tasksStore.systemUsage || {};
  const items = [];
  const cpuAllCores = Number.isFinite(usage.cpu_percent_all_cores)
    ? usage.cpu_percent_all_cores
    : usage.cpu_percent;
  if (Number.isFinite(cpuAllCores)) {
    items.push({ label: "CPU", value: tmFormatPercent(cpuAllCores) });
  }
  if (Number.isFinite(usage.ram_used_gb)) {
    items.push({
      label: "RAM",
      value: tmFormatUsage(
        usage.ram_used_gb,
        usage.ram_total_gb,
        usage.ram_percent,
      ),
    });
  }
  items.push({
    label: "VRAM",
    value: Number.isFinite(usage.vram_used_gb)
      ? tmFormatUsage(
          usage.vram_used_gb,
          usage.vram_total_gb,
          usage.vram_percent,
        )
      : "n/a",
  });
  return items;
});
</script>

<style scoped>
.tm-idle-msg {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.4);
  font-style: italic;
  padding: var(--space-4) 0 var(--space-2);
}

.tm-worker-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  width: 100%;
}

.tm-worker-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.04);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.07);
  min-width: 0;
}

.tm-worker-row-top {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.tm-worker-row-bottom {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.tm-sparkline {
  flex: 1;
  height: 28px;
  min-width: 0;
  display: block;
  border-radius: var(--radius-sm);
}

.tm-worker-rate {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.45);
  white-space: nowrap;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  min-width: 38px;
  text-align: right;
}

.tm-status-dot {
  /* Sized as the attention dot (visual-language §12); amber only while running. */
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  flex-shrink: 0;
  background: rgba(var(--v-theme-on-surface), 0.2);
}

.tm-status-dot--running {
  background: rgb(var(--v-theme-accent));
  box-shadow: 0 0 5px rgba(var(--v-theme-accent), 0.55);
  animation: tm-dot-pulse 1.4s ease-in-out infinite;
}

/* ── ComfyUI run rows ──────────────────────────────────────────────────────── */
.tm-comfy-bar {
  height: 6px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.tm-comfy-fill {
  height: 100%;
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  transition: width 0.25s ease;
}

.tm-comfy-message {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.5);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tm-comfy-abort {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), 0.45);
  font-size: var(--text-xs);
  line-height: 1;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
}
.tm-comfy-abort:hover {
  color: rgb(var(--v-theme-surface-error));
  background: rgba(var(--v-theme-error), 0.12);
}

@keyframes tm-dot-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.78);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tm-status-dot--running {
    animation: none;
  }
}

/* Landing punctuation for the async import (#459): a one-shot spring-pop + accent
   glow-and-settle when the FLIP flight from the import dialog lands the chip on
   this row (mirrors the gridNewPulse landing-pulse pattern, visual-language §10).
   One-shot `both` so it plays once on the row's first render, then rests. */
.tm-import-row {
  animation:
    tm-import-pop var(--dur-4) var(--ease-spring) both,
    tm-import-glow 2.2s ease-out both;
}

@keyframes tm-import-pop {
  0% {
    transform: scale(0.92);
  }
  100% {
    transform: scale(1);
  }
}

@keyframes tm-import-glow {
  0% {
    box-shadow: 0 0 0 0 rgba(var(--v-theme-accent), 0.5);
    border-color: rgba(var(--v-theme-accent), 0.6);
  }
  35% {
    box-shadow: 0 0 0 4px rgba(var(--v-theme-accent), 0.18);
    border-color: rgba(var(--v-theme-accent), 0.45);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(var(--v-theme-accent), 0);
    border-color: rgba(var(--v-theme-on-surface), 0.07);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tm-import-row {
    animation: none;
  }
}

.tm-worker-label {
  flex: 1;
  font-size: var(--text-2xs);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: rgba(var(--v-theme-on-surface), 0.85);
}

.tm-worker-progress {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.5);
  white-space: nowrap;
  flex-shrink: 0;
}

.tm-system-bar {
  margin-top: auto;
  padding-top: var(--space-3);
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.08);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  width: 100%;
}

.tm-system-item {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-3);
  font-size: var(--text-2xs);
}

.tm-system-label {
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  text-transform: uppercase;
  font-size: var(--text-2xs);
  flex-shrink: 0;
}

.tm-system-value {
  color: rgba(var(--v-theme-on-surface), 0.75);
  text-align: right;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
