<template>
  <v-card class="task-manager-card">
    <div class="task-manager-header">
      <div class="task-manager-title">Worker Task Manager</div>
      <v-btn
        icon
        variant="text"
        class="task-manager-close"
        @click="emit('close')"
      >
        <v-icon size="20">mdi-close</v-icon>
      </v-btn>
    </div>
    <div class="task-manager-subtitle">
      Last {{ windowSeconds / 60 }} minutes. Rates are pictures per second.
    </div>
    <div v-if="loading" class="task-manager-loading">Loading...</div>
    <div v-else class="task-manager-grid">
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
            :ref="(el) => registerCanvas(entry.key, el)"
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
            :ref="(el) => registerCanvas(combinedKey, el)"
            class="task-manager-canvas"
          ></canvas>
        </div>
        <div class="task-manager-status">
          <span
            class="task-manager-status-dot"
            :class="{
              'task-manager-status-dot--running': combinedSnapshot.running,
            }"
          ></span>
          <span class="task-manager-status-text">
            {{ combinedSnapshot.running ? "running" : "idle" }}
          </span>
        </div>
      </div>
    </div>
  </v-card>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
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
const canvasRefs = new Map();
const lastSnapshot = new Map();
let pollTimer = null;
const combinedKey = "__combined__";

const labelMap = {
  quality_scored: "Quality scored",
  face_quality_scored: "Face quality scored",
  pictures_tagged: "Pictures tagged",
  descriptions_generated: "Descriptions generated",
  text_embeddings: "Text embeddings",
  image_embeddings: "Image embeddings",
  features_extracted: "Features extracted",
  likeness_pairs: "Likeness pairs",
  likeness_parameters: "Likeness parameters",
  scrapheap_candidates: "Scrapheap candidates",
  watch_folder_import: "Watch folder import",
};

const workerEntries = computed(() => {
  const entries = Object.entries(workerSnapshots.value || {});
  const filtered = entries.filter(([, snapshot]) => {
    if (!snapshot) return false;
    if (snapshot.label === "uninitialized" && !snapshot.running) return false;
    return true;
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

function registerCanvas(key, el) {
  if (!el) return;
  canvasRefs.set(key, el);
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
    const now = Date.now() / 1000;
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
  for (const key of Object.keys(series.value || {})) {
    const canvas = canvasRefs.get(key);
    if (!canvas) continue;
    drawSparkline(canvas, series.value[key] || []);
  }
}

function drawSparkline(canvas, samples) {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const rect = canvas.getBoundingClientRect();
  const width = Math.max(1, Math.floor(rect.width));
  const height = Math.max(1, Math.floor(rect.height));
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
  ctx.fillStyle = "rgba(255, 255, 255, 0.04)";
  ctx.fillRect(0, 0, width, height);

  if (!samples.length) {
    const pad = 6;
    const y = height - pad;
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(width - pad, y);
    ctx.strokeStyle = "rgba(242, 229, 218, 0.45)";
    ctx.lineWidth = 1.2;
    ctx.stroke();
    return;
  }

  const maxRate = Math.max(1, ...samples.map((s) => s.rate || 0));
  const pad = 6;
  const plotWidth = width - pad * 2;
  const plotHeight = height - pad * 2;
  const step = samples.length > 1 ? plotWidth / (samples.length - 1) : 0;

  ctx.beginPath();
  samples.forEach((sample, index) => {
    const x = pad + step * index;
    const y = pad + plotHeight * (1 - (sample.rate || 0) / maxRate);
    if (index === 0) {
      ctx.moveTo(x, y);
    } else {
      ctx.lineTo(x, y);
    }
  });
  ctx.strokeStyle = "rgba(242, 229, 218, 0.85)";
  ctx.lineWidth = 1.5;
  ctx.stroke();

  ctx.lineTo(pad + step * (samples.length - 1), pad + plotHeight);
  ctx.lineTo(pad, pad + plotHeight);
  ctx.closePath();
  ctx.fillStyle = "rgba(142, 166, 4, 0.18)";
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

function getMaxRate(key) {
  const samples = series.value[key] || [];
  if (!samples.length) return 0;
  return Math.max(...samples.map((s) => s.rate || 0));
}

function getLatestRate(key) {
  const samples = series.value[key] || [];
  if (!samples.length) return 0;
  return samples[samples.length - 1].rate || 0;
}

watch(
  () => props.active,
  (value) => {
    if (value) {
      startPolling();
    } else {
      stopPolling();
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  stopPolling();
});
</script>

<style scoped>
.task-manager-card {
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  padding: 16px 18px 20px 18px;
  border-radius: 16px;
}

.task-manager-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.task-manager-title {
  font-size: 1.2rem;
  font-weight: 700;
}

.task-manager-subtitle {
  margin-top: 4px;
  color: rgba(var(--v-theme-on-surface), 0.65);
  font-size: 0.9rem;
}

.task-manager-loading {
  margin-top: 16px;
  font-size: 0.95rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.task-manager-grid {
  margin-top: 16px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.task-manager-panel {
  background: rgba(0, 0, 0, 0.15);
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  border-radius: 12px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.task-manager-panel--combined {
  background: rgba(0, 0, 0, 0.22);
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
}

.task-manager-progress {
  font-size: 0.85rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
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
  height: 70px;
  background: rgba(0, 0, 0, 0.15);
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
  background: rgba(255, 255, 255, 0.25);
}

.task-manager-status-dot--running {
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 6px rgba(142, 166, 4, 0.6);
}

.task-manager-close {
  min-width: 32px;
  min-height: 32px;
  width: 32px;
  height: 32px;
}
</style>
