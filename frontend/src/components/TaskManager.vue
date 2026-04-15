<template>
  <div class="task-manager-shell">
    <div class="task-manager-window">
      <v-btn icon size="36px" class="task-manager-close" @click="emit('close')">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="task-manager-card">
        <div class="task-manager-header">
          <div class="task-manager-header-main">
            <div class="task-manager-title">Worker Task Manager</div>
            <div class="task-manager-subtitle">
              Last {{ windowSeconds / 60 }} minutes. Rates are pictures per
              second.
            </div>
          </div>
          <div v-if="systemItems.length" class="task-manager-header-system">
            <div
              v-for="item in systemItems"
              :key="item.label"
              class="task-manager-system-item"
            >
              <span class="task-manager-system-label">{{ item.label }}</span>
              <span class="task-manager-system-value">
                <template
                  v-for="(part, idx) in splitPercentSegments(item.value)"
                  :key="`${item.label}-${idx}`"
                >
                  <span
                    :class="{
                      'task-manager-system-value--emphasis': part.bold,
                    }"
                  >
                    {{ part.text }}
                  </span>
                </template>
              </span>
            </div>
          </div>
        </div>
        <div v-if="loading" class="task-manager-loading">Loading...</div>
        <div v-else>
          <div class="task-manager-grid">
            <div
              v-for="entry in workerEntries"
              :key="entry.key"
              class="task-manager-panel"
            >
              <div class="task-manager-panel-header">
                <div class="task-manager-metric">
                  {{ formatLabel(entry.key, entry.snapshot.label) }}
                </div>
                <div class="task-manager-progress">
                  {{ formatProgress(entry.snapshot) }}
                </div>
              </div>
              <div class="task-manager-panel-subheader">
                <span class="task-manager-rate">
                  {{ formatRate(getLatestRate(entry.key)) }}/s
                </span>
                <span class="task-manager-max">
                  Max {{ formatRate(getMaxRate(entry.key)) }}/s
                </span>
              </div>
              <div class="task-manager-canvas-wrap">
                <canvas
                  :ref="
                    (el) => registerCanvas(`${entry.key}-grid`, entry.key, el)
                  "
                  width="240"
                  height="60"
                  class="task-manager-canvas"
                ></canvas>
              </div>
              <div class="task-manager-status">
                <span
                  class="task-manager-status-dot"
                  :class="{
                    'task-manager-status-dot--running': entry.snapshot.running,
                  }"
                ></span>
                <span class="task-manager-status-text">
                  {{
                    entry.snapshot.running
                      ? "running"
                      : entry.snapshot.status || "idle"
                  }}
                </span>
              </div>
            </div>
            <div
              v-if="combinedSnapshot"
              class="task-manager-panel task-manager-panel--combined"
            >
              <div class="task-manager-panel-header">
                <div class="task-manager-metric">Total throughput</div>
                <div class="task-manager-progress">
                  {{ formatProgress(combinedSnapshot) }}
                </div>
              </div>
              <div class="task-manager-panel-subheader">
                <span class="task-manager-rate">
                  {{ formatRate(getLatestRate(combinedKey)) }}/s
                </span>
                <span class="task-manager-max">
                  Max {{ formatRate(getMaxRate(combinedKey)) }}/s
                </span>
              </div>
              <div class="task-manager-canvas-wrap">
                <canvas
                  :ref="
                    (el) =>
                      registerCanvas(`${combinedKey}-grid`, combinedKey, el)
                  "
                  width="240"
                  height="60"
                  class="task-manager-canvas"
                ></canvas>
              </div>
              <div class="task-manager-status">
                <span
                  class="task-manager-status-dot"
                  :class="{
                    'task-manager-status-dot--running':
                      combinedSnapshot.running,
                  }"
                ></span>
                <span class="task-manager-status-text">
                  {{ combinedSnapshot.running ? "running" : "idle" }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </v-card>
    </div>
  </div>
</template>

<script setup>
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from "vue";
import { apiClient } from "../utils/apiClient";

const props = defineProps({
  active: { type: Boolean, default: false },
  pollIntervalMs: { type: Number, default: 2000 },
  windowSeconds: { type: Number, default: 300 },
});

const emit = defineEmits(["close"]);

const loading = ref(false);
const workerSnapshots = ref({});
const series = ref({});
const systemUsage = ref(null);
const canvasRefs = new Map();
const canvasObservers = new Map();
const lastSnapshot = new Map();
const lastActiveAtByWorker = new Map();
const lastProgressAtByWorker = new Map();
let pollTimer = null;
const combinedKey = "__combined__";
const RATE_AVERAGE_WINDOW_SECONDS = 8;
const WORKER_REMOVE_GRACE_SECONDS = 10;
const nowSeconds = ref(Date.now() / 1000);

const labelMap = {
  quality_scored: "Quality",
  face_quality_scored: "Face quality",
  pictures_tagged: "Tags",
  descriptions_generated: "Descriptions",
  text_embeddings: "Text embeddings",
  image_embeddings: "Image embeddings",
  features_extracted: "Features extracted",
  likeness_pairs: "Likeness pairs",
  likeness_parameters: "Likeness params",
  watch_folder_import: "Folder import",
  comfyui_extraction: "ComfyUI backfill",
  tag_predictions_scored: "Tag Predictions",
  missing_file_purge: "File cleanup",
};

function seedSnapshotsIfEmpty() {
  if (Object.keys(workerSnapshots.value || {}).length) return;
  const nextSnapshots = {};
  const now = Date.now() / 1000;
  const nextSeries = {};
  const seedKeys = new Set([
    "QualityWorker",
    "WatchFolderWorker",
    "FeatureExtractionWorker",
    "ImageEmbeddingWorker",
    "DescriptionWorker",
    "TagWorker",
    "LikenessParameterWorker",
    "LikenessWorker",
    "EmbeddingWorker",
    "FaceQualityWorker",
    "TagPredictionTask",
    combinedKey,
  ]);
  for (const workerKey of seedKeys) {
    nextSnapshots[workerKey] = {
      label: "idle",
      current: 0,
      total: 0,
      remaining: 0,
      updated_at: null,
      status: "idle",
      running: false,
    };
    nextSeries[workerKey] = [
      {
        t: now,
        rate: 0,
        current: 0,
        total: 0,
        label: "idle",
        running: false,
      },
    ];
  }
  workerSnapshots.value = nextSnapshots;
  series.value = { ...nextSeries };
  nextTick(() => requestAnimationFrame(drawAll));
}

function getThemeRgb(name) {
  if (typeof window === "undefined") return null;
  const root = getComputedStyle(document.documentElement);
  const value = root.getPropertyValue(`--v-theme-${name}`).trim();
  return value || null;
}

function themeRgb(name, fallback = "0, 0, 0") {
  const value = getThemeRgb(name) || fallback;
  return `rgb(${value})`;
}

function themeRgba(name, alpha, fallback = "0, 0, 0") {
  const value = getThemeRgb(name) || fallback;
  return `rgba(${value}, ${alpha})`;
}

const workerEntries = computed(() => {
  const entries = Object.entries(workerSnapshots.value || {});
  const filtered = entries.filter(([key, snapshot]) => {
    if (!snapshot) return false;
    if (typeof snapshot.active === "boolean") {
      if (snapshot.active) return true;
    }
    const lastActiveAt = Number(lastActiveAtByWorker.get(key) || 0);
    const lastProgressAt = Number(lastProgressAtByWorker.get(key) || 0);
    const latestActivityAt = Math.max(lastActiveAt, lastProgressAt);
    return (
      latestActivityAt > 0 &&
      nowSeconds.value - latestActivityAt <= WORKER_REMOVE_GRACE_SECONDS
    );
  });
  return filtered.map(([key, snapshot]) => ({ key, snapshot }));
});

const combinedSnapshot = computed(() => {
  const snapshots = Object.values(workerSnapshots.value || {});
  if (!snapshots.length) return null;
  let current = 0;
  let total = 0;
  let running = false;
  for (const snap of snapshots) {
    current += Number(snap.current || 0);
    total += Number(snap.total || 0);
    if (snap.running) {
      running = true;
    }
  }
  return {
    label: "total_throughput",
    current,
    total,
    running,
    status: running ? "running" : "idle",
  };
});

const systemItems = computed(() => {
  const usage = systemUsage.value || {};
  const items = [];
  const cpuAllCores = Number.isFinite(usage.cpu_percent_all_cores)
    ? usage.cpu_percent_all_cores
    : usage.cpu_percent;
  const cpuOneCore = Number.isFinite(usage.cpu_percent_one_core)
    ? usage.cpu_percent_one_core
    : null;
  if (Number.isFinite(cpuAllCores)) {
    items.push({
      label: "CPU",
      value: Number.isFinite(cpuOneCore)
        ? `${formatPercent(cpuAllCores)} all cores (${formatPercent(cpuOneCore)} one core)`
        : formatPercent(cpuAllCores),
    });
  }
  if (Number.isFinite(usage.ram_used_gb)) {
    items.push({
      label: "RAM",
      value: formatUsage(
        usage.ram_used_gb,
        usage.ram_total_gb,
        usage.ram_percent,
      ),
    });
  }
  const vramValue = Number.isFinite(usage.vram_used_gb)
    ? formatUsage(usage.vram_used_gb, usage.vram_total_gb, usage.vram_percent)
    : "n/a";
  items.push({
    label: "VRAM",
    value: vramValue,
  });
  return items;
});

function registerCanvas(canvasKey, dataKey, el) {
  if (!el) return;
  canvasRefs.set(canvasKey, { el, dataKey });
  if (canvasObservers.has(canvasKey)) {
    canvasObservers.get(canvasKey).disconnect();
  }
  const observer = new ResizeObserver(() => {
    drawSparkline(el, series.value[dataKey] || []);
  });
  observer.observe(el);
  canvasObservers.set(canvasKey, observer);
  requestAnimationFrame(() => {
    drawSparkline(el, series.value[dataKey] || []);
  });
}

function startPolling() {
  if (pollTimer) return;
  fetchProgress();
  pollTimer = setInterval(fetchProgress, props.pollIntervalMs);
}

function stopPolling() {
  if (!pollTimer) return;
  clearInterval(pollTimer);
  pollTimer = null;
}

async function fetchProgress() {
  if (!Object.keys(workerSnapshots.value || {}).length) {
    loading.value = true;
  }
  try {
    const res = await apiClient.get("/workers/progress");
    const workers = res.data?.workers || {};
    systemUsage.value = res.data?.process || res.data?.system || null;
    const now = Date.now() / 1000;
    nowSeconds.value = now;
    const nextSeries = { ...series.value };
    workerSnapshots.value = workers;
    let combinedRate = 0;

    for (const [key, snapshot] of Object.entries(workers)) {
      const current = Number(snapshot.current || 0);
      const total = Number(snapshot.total || 0);
      const prev = lastSnapshot.get(key);
      let rate = 0;
      if (prev && now > prev.t) {
        const delta = current - prev.current;
        rate = delta > 0 ? delta / (now - prev.t) : 0;
      }
      if (rate > 0) {
        lastProgressAtByWorker.set(key, now);
      }
      const hasExplicitActive = typeof snapshot?.active === "boolean";
      const isActive = hasExplicitActive
        ? snapshot.active
        : Boolean(snapshot?.running) && rate > 0;
      if (isActive) {
        lastActiveAtByWorker.set(key, now);
      }
      combinedRate += rate;
      lastSnapshot.set(key, { current, t: now });

      const entry = {
        t: now,
        rate,
        current,
        total,
        label: snapshot.label,
        running: snapshot.running,
      };
      const existing = nextSeries[key] ? [...nextSeries[key]] : [];
      existing.push(entry);
      const cutoff = now - props.windowSeconds;
      nextSeries[key] = existing.filter((item) => item.t >= cutoff);
    }

    for (const key of Array.from(lastActiveAtByWorker.keys())) {
      if (!(key in workers)) {
        lastActiveAtByWorker.delete(key);
      }
    }
    for (const key of Array.from(lastProgressAtByWorker.keys())) {
      if (!(key in workers)) {
        lastProgressAtByWorker.delete(key);
      }
    }

    if (Object.keys(workers).length) {
      const combinedEntry = {
        t: now,
        rate: combinedRate,
        current: combinedSnapshot.value?.current || 0,
        total: combinedSnapshot.value?.total || 0,
        label: "total_throughput",
        running: combinedSnapshot.value?.running || false,
      };
      const combinedSeries = nextSeries[combinedKey]
        ? [...nextSeries[combinedKey]]
        : [];
      combinedSeries.push(combinedEntry);
      const cutoff = now - props.windowSeconds;
      nextSeries[combinedKey] = combinedSeries.filter(
        (item) => item.t >= cutoff,
      );
    }

    series.value = nextSeries;
    await nextTick();
    drawAll();
  } catch (err) {
    // keep last known samples
  } finally {
    loading.value = false;
  }
}

function drawAll() {
  for (const { el, dataKey } of canvasRefs.values()) {
    drawSparkline(el, series.value[dataKey] || []);
  }
}

function drawSparkline(canvas, samples) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const rect = canvas.getBoundingClientRect();
  const fallbackWidth =
    canvas.width || canvas.parentElement?.clientWidth || 240;
  const fallbackHeight =
    canvas.height || canvas.parentElement?.clientHeight || 60;
  const width = Math.max(4, Math.floor(rect.width || fallbackWidth));
  const height = Math.max(4, Math.floor(rect.height || fallbackHeight));
  const dpr = window.devicePixelRatio || 1;
  const targetWidth = Math.floor(width * dpr);
  const targetHeight = Math.floor(height * dpr);
  if (canvas.width !== targetWidth || canvas.height !== targetHeight) {
    canvas.width = targetWidth;
    canvas.height = targetHeight;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);

  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = themeRgba("surface", 0.2, "255, 255, 255");
  ctx.fillRect(0, 0, width, height);

  const plotSamples = samples.length ? samples : [{ rate: 0 }];
  const maxRate = Math.max(1, ...plotSamples.map((s) => s.rate || 0));
  const pad = 6;
  const plotWidth = width - pad * 2;
  const plotHeight = height - pad * 2;
  const step =
    plotSamples.length > 1 ? plotWidth / (plotSamples.length - 1) : 0;

  ctx.beginPath();
  plotSamples.forEach((sample, index) => {
    const x = pad + step * index;
    const y = pad + plotHeight * (1 - (sample.rate || 0) / maxRate);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.strokeStyle = themeRgba("tertiary", 0.85, "242, 229, 218");
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.lineTo(pad + step * (plotSamples.length - 1), pad + plotHeight);
  ctx.lineTo(pad, pad + plotHeight);
  ctx.closePath();
  ctx.fillStyle = themeRgba("tertiary", 0.18, "142, 166, 4");
  ctx.fill();
}

function formatLabel(key, label) {
  if (labelMap[label]) return labelMap[label];
  if (label && label !== "idle" && label !== "uninitialized") {
    return label.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
  return key.replace(/Worker$/, "");
}

function formatProgress(snapshot) {
  const current = Number(snapshot?.current || 0);
  const total = Number(snapshot?.total || 0);
  if (!total) return `${current}`;
  return `${current} / ${total}`;
}

function formatRate(value) {
  const rate = Number(value || 0);
  if (rate >= 10) return rate.toFixed(0);
  if (rate >= 1) return rate.toFixed(1);
  return rate.toFixed(2);
}

function formatPercent(value) {
  const percent = Number(value);
  if (!Number.isFinite(percent)) return "n/a";
  if (percent >= 10) return `${percent.toFixed(0)}%`;
  return `${percent.toFixed(1)}%`;
}

function formatGigabytes(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "n/a";
  return `${amount.toFixed(1)} GB`;
}

function formatUsage(used, total, percent) {
  const usedLabel = formatGigabytes(used);
  if (Number.isFinite(total)) {
    return `${usedLabel} / ${formatGigabytes(total)} (${formatPercent(percent)})`;
  }
  const percentLabel = formatPercent(percent);
  if (percentLabel !== "n/a") {
    return `${usedLabel} (${percentLabel})`;
  }
  return usedLabel;
}

function splitPercentSegments(value) {
  const text = String(value ?? "");
  const regex = /\d+(?:\.\d+)?%/g;
  const segments = [];
  let lastIndex = 0;

  for (const match of text.matchAll(regex)) {
    const start = match.index ?? 0;
    const matchedText = match[0] || "";
    if (start > lastIndex) {
      segments.push({ text: text.slice(lastIndex, start), bold: false });
    }
    segments.push({ text: matchedText, bold: true });
    lastIndex = start + matchedText.length;
  }

  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), bold: false });
  }

  return segments.length ? segments : [{ text, bold: false }];
}

function getMaxRate(key) {
  const samples = series.value[key] || [];
  if (!samples.length) return 0;
  return Math.max(...samples.map((s) => s.rate || 0));
}

function getLatestRate(key) {
  const samples = series.value[key] || [];
  if (!samples.length) return 0;
  const latest = samples[samples.length - 1];
  const latestTime = Number(latest?.t || 0);
  if (!latestTime) return Number(latest?.rate || 0);
  const cutoff = latestTime - RATE_AVERAGE_WINDOW_SECONDS;
  const windowSamples = samples.filter(
    (sample) => Number(sample?.t || 0) >= cutoff,
  );
  if (!windowSamples.length) return Number(latest?.rate || 0);
  const sum = windowSamples.reduce(
    (acc, sample) => acc + Number(sample?.rate || 0),
    0,
  );
  return sum / windowSamples.length;
}

watch(
  () => props.active,
  (value) => {
    if (value) {
      seedSnapshotsIfEmpty();
      startPolling();
      nextTick(() => requestAnimationFrame(drawAll));
    } else {
      stopPolling();
    }
  },
  { immediate: true },
);

onMounted(() => {
  nextTick(() => requestAnimationFrame(drawAll));
});

onBeforeUnmount(() => {
  stopPolling();
  for (const observer of canvasObservers.values()) {
    observer.disconnect();
  }
  canvasObservers.clear();
});
</script>

<style scoped>
.task-manager-card {
  background: rgb(var(--v-theme-background));
  color: rgb(var(--v-theme-on-background));
  padding: 16px 18px 20px 18px;
  border-radius: 16px;
  width: min(92vw, 1600px);
  max-width: 100%;
  box-sizing: border-box;
  overflow-x: hidden;
}

.task-manager-shell {
  position: relative;
  padding: 16px;
  background: transparent;
}

.task-manager-window {
  position: relative;
  display: block;
  width: 100%;
}

.task-manager-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.task-manager-header-main {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.task-manager-header-system {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  max-width: 52%;
}

.task-manager-title {
  font-size: 1.2rem;
  font-weight: 700;
}

.task-manager-subtitle {
  color: rgba(var(--v-theme-on-surface), 0.65);
  font-size: 0.9rem;
}

.task-manager-system-item {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 6px 10px;
  border-radius: 8px;
  background: rgba(var(--v-theme-surface), 0.4);
  border: 1px solid rgba(var(--v-theme-border), 0.35);
  font-size: 0.85rem;
}

.task-manager-system-label {
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.task-manager-system-value {
  color: rgba(var(--v-theme-on-surface), 0.65);
}

.task-manager-system-value--emphasis {
  font-weight: 700;
  color: rgba(var(--v-theme-on-surface), 0.9);
}

.task-manager-loading {
  margin-top: 16px;
  font-size: 0.95rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.task-manager-grid {
  margin-top: 16px;
  display: grid;
  width: 100%;
  max-width: 100%;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

@media (max-width: 1700px) {
  .task-manager-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 1280px) {
  .task-manager-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 860px) {
  .task-manager-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

.task-manager-panel {
  background: rgba(var(--v-theme-surface), 0.45);
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  border-radius: 10px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.task-manager-panel--combined {
  background: rgba(var(--v-theme-surface), 0.45);
  border-color: rgba(var(--v-theme-primary), 0.6);
}

.task-manager-panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.task-manager-metric {
  font-weight: 600;
  font-size: 0.95rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 140px;
  line-height: 1.2;
}

.task-manager-progress {
  font-size: 0.85rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
  white-space: nowrap;
  overflow: visible;
  text-overflow: clip;
  min-height: 1.2em;
  line-height: 1.2;
  flex-shrink: 0;
}

.task-manager-panel-subheader {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 0.8rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.task-manager-canvas-wrap {
  width: 100%;
  height: 60px;
  background: rgba(var(--v-theme-shadow), 0.15);
  border-radius: 8px;
  overflow: hidden;
}

.task-manager-canvas {
  width: 100%;
  height: 100%;
  display: block;
}

.task-manager-status {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 0.8rem;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.task-manager-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: rgba(var(--v-theme-on-surface), 0.25);
}

.task-manager-status-dot--running {
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 6px rgba(var(--v-theme-primary), 0.6);
}

.task-manager-close {
  position: absolute;
  top: -16px;
  right: -16px;
  background-color: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  border: none;
  cursor: pointer;
  z-index: 2;
}

.task-manager-close:hover {
  background-color: rgb(var(--v-theme-accent));
}
</style>
