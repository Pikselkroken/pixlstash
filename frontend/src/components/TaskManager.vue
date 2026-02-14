<template>
  <div class="task-manager-shell">
    <div class="task-manager-window">
      <v-btn icon size="36px" class="task-manager-close" @click="emit('close')">
        <v-icon size="24px">mdi-close</v-icon>
      </v-btn>
      <v-card class="task-manager-card">
        <div class="task-manager-header">
          <div class="task-manager-title">Worker Task Manager</div>
        </div>
        <div class="task-manager-subtitle">
          Last {{ windowSeconds / 60 }} minutes. Rates are pictures per second.
        </div>
        <div v-if="loading" class="task-manager-loading">Loading...</div>
        <div v-else class="task-manager-tabs">
          <v-tabs
            v-model="activeTab"
            density="compact"
            class="task-manager-tablist"
          >
            <v-tab value="grid">Grid</v-tab>
            <v-tab value="graph">Graph</v-tab>
          </v-tabs>
          <v-window v-model="activeTab" class="task-manager-tab-window">
            <v-window-item value="grid">
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
                        (el) =>
                          registerCanvas(`${entry.key}-grid`, entry.key, el)
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
                          entry.snapshot.running,
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
              <div v-if="systemItems.length" class="task-manager-system">
                <div class="task-manager-system-header">PixlVault usage</div>
                <div class="task-manager-system-items">
                  <div
                    v-for="item in systemItems"
                    :key="item.label"
                    class="task-manager-system-item"
                  >
                    <span class="task-manager-system-label">{{
                      item.label
                    }}</span>
                    <span class="task-manager-system-value">{{
                      item.value
                    }}</span>
                  </div>
                </div>
              </div>
            </v-window-item>
            <v-window-item value="graph">
              <div class="task-manager-graph">
                <svg
                  class="task-manager-graph-lines"
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                >
                  <defs>
                    <marker
                      id="arrow"
                      viewBox="0 0 10 10"
                      refX="7"
                      refY="5"
                      markerWidth="4"
                      markerHeight="4"
                      orient="auto-start-reverse"
                    >
                      <path
                        d="M 0 0 L 10 5 L 0 10 z"
                        fill="rgba(var(--v-theme-on-surface), 0.45)"
                      />
                    </marker>
                  </defs>
                  <polyline
                    v-for="edge in graphEdges"
                    :key="edge.id"
                    :points="edge.points"
                    stroke="rgba(var(--v-theme-on-surface), 0.6)"
                    stroke-width="2"
                    marker-end="url(#arrow)"
                    fill="none"
                    vector-effect="non-scaling-stroke"
                    shape-rendering="crispEdges"
                  />
                </svg>
                <div
                  v-for="node in graphNodes"
                  :key="node.id"
                  class="task-manager-graph-node"
                  :class="{
                    'task-manager-graph-node--combined':
                      node.id === combinedKey,
                  }"
                  :style="{
                    left: `${node.x}%`,
                    top: `${node.y}%`,
                    width: `${GRAPH_NODE_WIDTH}%`,
                    height: `${GRAPH_NODE_HEIGHT}%`,
                  }"
                >
                  <div class="task-manager-graph-header">
                    <div class="task-manager-graph-title">{{ node.title }}</div>
                    <div class="task-manager-graph-progress">
                      {{ formatProgress(node.snapshot) }}
                    </div>
                  </div>
                  <div class="task-manager-graph-subheader">
                    <span class="task-manager-rate">
                      {{ formatRate(getLatestRate(node.workerKey)) }}/s
                    </span>
                    <span class="task-manager-max">
                      {{ formatRate(getMaxRate(node.workerKey)) }}/s
                    </span>
                  </div>
                  <div
                    class="task-manager-canvas-wrap task-manager-canvas-wrap--graph"
                  >
                    <canvas
                      :ref="
                        (el) =>
                          registerCanvas(`${node.id}-graph`, node.workerKey, el)
                      "
                      width="240"
                      height="48"
                      class="task-manager-canvas"
                    ></canvas>
                  </div>
                </div>
              </div>
            </v-window-item>
          </v-window>
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
let pollTimer = null;
const combinedKey = "__combined__";
const activeTab = ref("grid");
const GRAPH_NODE_WIDTH = 30;
const GRAPH_NODE_HEIGHT = 20;

const graphLayout = [
  { id: "quality", workerKey: "QualityWorker", x: 1, y: 1 },
  { id: "watch", workerKey: "WatchFolderWorker", x: 1, y: 39 },
  { id: "features", workerKey: "FeatureExtractionWorker", x: 1, y: 67 },
  { id: "image_embeddings", workerKey: "ImageEmbeddingWorker", x: 35, y: 14 },
  { id: "descriptions", workerKey: "DescriptionWorker", x: 35, y: 39 },
  { id: "tags", workerKey: "TagWorker", x: 35, y: 61 },
  {
    id: "likeness_params",
    workerKey: "LikenessParameterWorker",
    x: 69,
    y: 1,
  },
  { id: "likeness", workerKey: "LikenessWorker", x: 69, y: 25 },
  { id: "text_embeddings", workerKey: "EmbeddingWorker", x: 69, y: 48 },
  {
    id: "scrapheap",
    workerKey: "SmartScoreScrapheapWorker",
    x: 69,
    y: 72,
  },
];

const graphEdgesConfig = [
  { from: "watch", to: "features", route: "direct" },
  { from: "watch", to: "quality", route: "direct" },
  { from: "watch", to: "image_embeddings", route: "bus" },
  { from: "watch", to: "descriptions", route: "bus" },
  { from: "features", to: "tags", route: "bus" },
  { from: "quality", to: "likeness", route: "bus" },
  { from: "quality", to: "likeness_params", route: "lane", laneX: 46 },
  { from: "quality", to: "scrapheap", route: "bus" },
  { from: "descriptions", to: "text_embeddings" },
  { from: "tags", to: "scrapheap" },
  { from: "text_embeddings", to: "likeness", route: "direct" },
  { from: "image_embeddings", to: "likeness" },
  { from: "likeness_params", to: "likeness", route: "direct" },
];

const labelMap = {
  quality_scored: "Quality scored",
  face_quality_scored: "Face quality",
  pictures_tagged: "Pictures tagged",
  descriptions_generated: "Descriptions",
  text_embeddings: "Text embeddings",
  image_embeddings: "Image embeddings",
  features_extracted: "Features extracted",
  likeness_pairs: "Likeness pairs",
  likeness_parameters: "Likeness params",
  scrapheap_candidates: "Scrapheap candidates",
  watch_folder_import: "Watch folder import",
};

function seedSnapshotsIfEmpty() {
  if (Object.keys(workerSnapshots.value || {}).length) return;
  const nextSnapshots = {};
  const now = Date.now() / 1000;
  const nextSeries = {};
  const seedKeys = new Set([
    ...graphLayout.map((node) => node.workerKey),
    "FaceQualityWorker",
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

const systemItems = computed(() => {
  const usage = systemUsage.value || {};
  const items = [];
  if (Number.isFinite(usage.cpu_percent)) {
    items.push({
      label: "CPU",
      value: formatPercent(usage.cpu_percent),
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
  if (Number.isFinite(usage.vram_used_gb)) {
    items.push({
      label: "VRAM",
      value: formatUsage(
        usage.vram_used_gb,
        usage.vram_total_gb,
        usage.vram_percent,
      ),
    });
  }
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

const graphNodes = computed(() =>
  graphLayout.map((node) => {
    if (node.workerKey === combinedKey) {
      return {
        ...node,
        title: "Total throughput",
        snapshot: combinedSnapshot.value || {
          current: 0,
          total: 0,
          running: false,
        },
      };
    }
    const snapshot = workerSnapshots.value[node.workerKey] || {
      label: "uninitialized",
      current: 0,
      total: 0,
      running: false,
    };
    return {
      ...node,
      snapshot,
      title: formatLabel(node.workerKey, snapshot.label),
    };
  }),
);

const graphEdges = computed(() => {
  const nodeMap = new Map(graphLayout.map((node) => [node.id, node]));
  const rects = graphLayout.map((node) => ({
    id: node.id,
    x: node.x,
    y: node.y,
    w: GRAPH_NODE_WIDTH,
    h: GRAPH_NODE_HEIGHT,
  }));

  const pointKey = (point) => `${Math.round(point[0])},${Math.round(point[1])}`;

  const segmentIntersectsRect = (a, b, rect) => {
    const [x1, y1] = a;
    const [x2, y2] = b;
    const minX = rect.x;
    const maxX = rect.x + rect.w;
    const minY = rect.y;
    const maxY = rect.y + rect.h;

    if (Math.max(x1, x2) < minX || Math.min(x1, x2) > maxX) return false;
    if (Math.max(y1, y2) < minY || Math.min(y1, y2) > maxY) return false;

    const dx = x2 - x1;
    const dy = y2 - y1;
    let t0 = 0;
    let t1 = 1;

    const clip = (p, q) => {
      if (p === 0) {
        return q >= 0;
      }
      const r = q / p;
      if (p < 0) {
        if (r > t1) return false;
        if (r > t0) t0 = r;
      } else {
        if (r < t0) return false;
        if (r < t1) t1 = r;
      }
      return true;
    };

    if (!clip(-dx, x1 - minX)) return false;
    if (!clip(dx, maxX - x1)) return false;
    if (!clip(-dy, y1 - minY)) return false;
    if (!clip(dy, maxY - y1)) return false;

    return t0 <= t1;
  };

  const routeEdge = (edge) => {
    const { from: fromId, to: toId, route, laneX } = edge;
    const from = nodeMap.get(fromId);
    const to = nodeMap.get(toId);
    if (!from || !to) return null;
    const fromCenterX = from.x + GRAPH_NODE_WIDTH / 2;
    const fromCenterY = from.y + GRAPH_NODE_HEIGHT / 2;
    const toCenterX = to.x + GRAPH_NODE_WIDTH / 2;
    const toCenterY = to.y + GRAPH_NODE_HEIGHT / 2;
    const dx = toCenterX - fromCenterX;
    const dy = toCenterY - fromCenterY;
    const horizontal = Math.abs(dx) >= Math.abs(dy);

    const start = horizontal
      ? [from.x + (dx >= 0 ? GRAPH_NODE_WIDTH : 0), fromCenterY]
      : [fromCenterX, from.y + (dy >= 0 ? GRAPH_NODE_HEIGHT : 0)];
    const end = horizontal
      ? [to.x + (dx >= 0 ? 0 : GRAPH_NODE_WIDTH), toCenterY]
      : [toCenterX, to.y + (dy >= 0 ? 0 : GRAPH_NODE_HEIGHT)];

    const segmentsClear = (points) => {
      for (let i = 0; i < points.length - 1; i += 1) {
        const a = points[i];
        const b = points[i + 1];
        for (const rect of rects) {
          if (rect.id === fromId || rect.id === toId) continue;
          if (segmentIntersectsRect(a, b, rect)) {
            return false;
          }
        }
      }
      return true;
    };

    const startX = start[0];
    const startY = start[1];
    const endX = end[0];
    const endY = end[1];
    const midX = (startX + endX) / 2;
    const midY = (startY + endY) / 2;
    const offset = horizontal
      ? dx >= 0
        ? GRAPH_NODE_WIDTH * 0.6
        : -GRAPH_NODE_WIDTH * 0.6
      : dy >= 0
        ? GRAPH_NODE_HEIGHT * 0.6
        : -GRAPH_NODE_HEIGHT * 0.6;

    const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
    const edgeMin = 2;
    const edgeMax = 98;
    const buildElbowX = (x) => {
      const clampedX = clamp(x, edgeMin, edgeMax);
      return [start, [clampedX, startY], [clampedX, endY], end];
    };
    const buildElbowY = (y) => {
      const clampedY = clamp(y, edgeMin, edgeMax);
      return [start, [startX, clampedY], [endX, clampedY], end];
    };
    const useX = horizontal || Math.abs(startX - endX) < 0.01;
    const useY = !horizontal || Math.abs(startY - endY) < 0.01;

    const leftColumnX = nodeMap.get("watch")?.x ?? from.x;
    const middleColumnX = nodeMap.get("image_embeddings")?.x ?? startX;
    const busX = Math.round(
      (leftColumnX + GRAPH_NODE_WIDTH + middleColumnX) / 2,
    );

    if (route === "direct") {
      return [start, end];
    }

    if (route === "bus") {
      const busStart = [from.x + GRAPH_NODE_WIDTH, fromCenterY];
      const busEnd = [to.x, toCenterY];
      return [busStart, [busX, busStart[1]], [busX, busEnd[1]], busEnd];
    }

    if (route === "lane") {
      const lane = clamp(laneX ?? (startX + endX) / 2, edgeMin, edgeMax);
      const forcedStart = [from.x + GRAPH_NODE_WIDTH, fromCenterY];
      const forcedEnd = [to.x, toCenterY];
      return [
        forcedStart,
        [lane, forcedStart[1]],
        [lane, forcedEnd[1]],
        forcedEnd,
      ];
    }

    const primary = useX ? buildElbowX(midX) : buildElbowY(midY);
    if (segmentsClear(primary)) {
      return primary;
    }

    const alternate = useX
      ? buildElbowX(startX + offset)
      : buildElbowY(startY + offset);
    if (segmentsClear(alternate)) {
      return alternate;
    }

    return primary;
  };

  return graphEdgesConfig
    .map((edge, index) => {
      const points = routeEdge(edge);
      if (!points) return null;
      const pointString = points.map((p) => pointKey(p)).join(" ");
      return {
        id: `${edge.from}-${edge.to}-${index}`,
        points: pointString,
      };
    })
    .filter(Boolean);
});

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
      seedSnapshotsIfEmpty();
      startPolling();
      nextTick(() => requestAnimationFrame(drawAll));
    } else {
      stopPolling();
    }
  },
  { immediate: true },
);

watch(
  () => activeTab.value,
  async () => {
    await nextTick();
    requestAnimationFrame(drawAll);
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
  width: 60vw;
}

.task-manager-shell {
  position: relative;
  padding: 16px;
  background: transparent;
}

.task-manager-window {
  position: relative;
  display: inline-block;
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

.task-manager-system {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.task-manager-system-header {
  font-size: 0.9rem;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.8);
}

.task-manager-system-items {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
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

.task-manager-loading {
  margin-top: 16px;
  font-size: 0.95rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.task-manager-tabs {
  margin-top: 8px;
}

.task-manager-tablist {
  border-bottom: 1px solid rgba(var(--v-theme-border), 0.5);
}

:deep(.task-manager-tablist .v-tab) {
  border: 1px solid transparent;
  box-shadow: none;
}

:deep(.task-manager-tablist .v-tab--selected) {
  border-color: rgba(var(--v-theme-on-surface), 0.2);
  box-shadow: none;
}

:deep(.task-manager-tablist .v-tab:focus-visible),
:deep(.task-manager-tablist .v-tab:focus),
:deep(.task-manager-tablist .v-tab--selected:focus-visible),
:deep(.task-manager-tablist .v-tab--selected:focus) {
  outline: none;
  box-shadow: none;
}

.task-manager-tab-window {
  margin-top: 12px;
  height: 55vh;
  min-height: 500px;
}

.task-manager-tab-window :deep(.v-window__container) {
  height: 100%;
}

.task-manager-tab-window :deep(.v-window-item) {
  height: 100%;
}

.task-manager-tab-window :deep(.v-window-item__content) {
  height: 100%;
}

.task-manager-grid {
  margin-top: 16px;
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 10px;
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

.task-manager-graph {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 14px;
  background: rgba(var(--v-theme-shadow), 0.12);
  border: 1px solid rgba(var(--v-theme-border), 0.4);
  overflow: hidden;
}

.task-manager-graph-lines {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.task-manager-graph-node {
  position: absolute;
  padding: 8px;
  border-radius: 10px;
  border: 1px solid rgba(var(--v-theme-border), 0.5);
  background: rgba(var(--v-theme-surface), 0.45);
  display: flex;
  flex-direction: column;
  gap: 4px;
  color: rgb(var(--v-theme-on-surface));
}

.task-manager-graph-node--combined {
  border-color: rgba(var(--v-theme-primary), 0.7);
  background: rgba(var(--v-theme-shadow), 0.32);
}

.task-manager-graph-header {
  display: flex;
  justify-content: space-between;
  gap: 6px;
  font-size: 0.85rem;
  font-weight: 600;
}

.task-manager-graph-title {
  font-size: 0.85rem;
  font-weight: 600;
}

.task-manager-graph-progress {
  font-size: 0.75rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.task-manager-graph-subheader {
  display: flex;
  justify-content: space-between;
  font-size: 0.75rem;
  color: rgba(var(--v-theme-on-surface), 0.7);
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
  height: 60px;
  background: rgba(var(--v-theme-shadow), 0.15);
  border-radius: 8px;
  overflow: hidden;
}

.task-manager-canvas-wrap--graph {
  height: 48px;
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
