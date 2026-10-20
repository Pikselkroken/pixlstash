<script setup>
/**
 * Small SVG-free canvas line chart of training loss vs step, with a built-in
 * text alternative (WCAG 1.1.1): an aria-label summary plus a toggle to a
 * compact data table. Reuses the canvas/DPR/theme-rgba drawing idiom from
 * StatsSidebar.tmDrawSparkline. Respects prefers-reduced-motion (the chart is
 * static anyway; no draw animation is used).
 *
 * Props:
 *   curve: Array<{ step:number, loss:number }>  the loss curve from a job.
 */
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from "vue";

const props = defineProps({
  curve: { type: Array, default: () => [] },
});

const canvasRef = ref(null);
const showTable = ref(false);
let resizeObserver = null;

const points = computed(() =>
  (props.curve || [])
    .map((p) => ({ step: Number(p.step), loss: Number(p.loss) }))
    .filter((p) => Number.isFinite(p.step) && Number.isFinite(p.loss)),
);

const hasData = computed(() => points.value.length >= 2);

const summary = computed(() => {
  const pts = points.value;
  if (!pts.length) return "No loss data recorded for this run.";
  const first = pts[0];
  const last = pts[pts.length - 1];
  return (
    `Training loss from ${formatLoss(first.loss)} at step ${first.step} ` +
    `to ${formatLoss(last.loss)} at step ${last.step} ` +
    `over ${pts.length} recorded points.`
  );
});

function formatLoss(v) {
  if (!Number.isFinite(v)) return "-";
  return v < 0.01 ? v.toExponential(2) : v.toFixed(3);
}

function themeRgb(name, fallback) {
  if (typeof window === "undefined") return fallback;
  return (
    getComputedStyle(document.documentElement)
      .getPropertyValue(`--v-theme-${name}`)
      .trim() || fallback
  );
}

function draw() {
  const canvas = canvasRef.value;
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(4, Math.floor(rect.width || 240));
  const height = Math.max(4, Math.floor(rect.height || 80));
  const tw = Math.floor(width * dpr);
  const th = Math.floor(height * dpr);
  if (canvas.width !== tw || canvas.height !== th) {
    canvas.width = tw;
    canvas.height = th;
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);

  const pts = points.value;
  if (pts.length < 2) return;

  const pad = 4;
  const plotW = width - pad * 2;
  const plotH = height - pad * 2;
  const steps = pts.map((p) => p.step);
  const losses = pts.map((p) => p.loss);
  const minStep = Math.min(...steps);
  const maxStep = Math.max(...steps);
  const minLoss = Math.min(...losses);
  const maxLoss = Math.max(...losses);
  const stepRange = maxStep - minStep || 1;
  const lossRange = maxLoss - minLoss || 1;

  const xOf = (step) => pad + ((step - minStep) / stepRange) * plotW;
  // Invert y: lower loss is higher on the chart.
  const yOf = (loss) => pad + (1 - (loss - minLoss) / lossRange) * plotH;

  const accent = themeRgb("primary", "142,166,4");

  // Line
  ctx.beginPath();
  pts.forEach((p, i) => {
    const x = xOf(p.step);
    const y = yOf(p.loss);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = `rgba(${accent}, 0.9)`;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // Soft fill under the line
  const last = pts[pts.length - 1];
  ctx.lineTo(xOf(last.step), pad + plotH);
  ctx.lineTo(pad, pad + plotH);
  ctx.closePath();
  ctx.fillStyle = `rgba(${accent}, 0.14)`;
  ctx.fill();
}

function scheduleDraw() {
  nextTick(() => requestAnimationFrame(draw));
}

onMounted(() => {
  if (canvasRef.value && typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(() => draw());
    resizeObserver.observe(canvasRef.value);
  }
  scheduleDraw();
});

onUnmounted(() => {
  if (resizeObserver) {
    resizeObserver.disconnect();
    resizeObserver = null;
  }
});

watch(() => props.curve, scheduleDraw, { deep: true });
</script>

<template>
  <div class="lora-loss-chart">
    <figure class="lora-loss-figure" role="group" :aria-label="summary">
      <canvas
        v-show="hasData"
        ref="canvasRef"
        class="lora-loss-canvas"
        aria-hidden="true"
      ></canvas>
      <p v-if="!hasData" class="lora-loss-empty">
        Not enough loss data to chart.
      </p>
      <figcaption class="lora-loss-caption">{{ summary }}</figcaption>
    </figure>

    <button
      v-if="points.length"
      type="button"
      class="lora-loss-table-toggle"
      :aria-expanded="showTable ? 'true' : 'false'"
      @click="showTable = !showTable"
    >
      <v-icon size="13">{{
        showTable ? "mdi-chevron-down" : "mdi-chevron-right"
      }}</v-icon>
      {{ showTable ? "Hide loss data table" : "Show loss data table" }}
    </button>

    <div v-if="showTable && points.length" class="lora-loss-table-wrap">
      <table class="lora-loss-table">
        <caption class="lora-loss-table-caption">
          Training loss by step
        </caption>
        <thead>
          <tr>
            <th scope="col">Step</th>
            <th scope="col">Loss</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(p, i) in points" :key="i">
            <td>{{ p.step }}</td>
            <td>{{ formatLoss(p.loss) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.lora-loss-chart {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.lora-loss-figure {
  margin: 0;
}

.lora-loss-canvas {
  display: block;
  width: 100%;
  height: 80px;
}

.lora-loss-empty {
  font-size: var(--text-xs);
  opacity: 0.6;
  margin: 0;
  padding: var(--space-3) 0;
}

/* The caption doubles as the visible text alternative; keep it readable
   (contrast >= 4.5:1) rather than hiding it from sighted users. */
.lora-loss-caption {
  font-size: var(--text-xs);
  opacity: 0.8;
  margin-top: var(--space-1);
}

.lora-loss-table-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  background: none;
  border: none;
  padding: var(--space-1) 0;
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-primary));
  cursor: pointer;
  align-self: flex-start;
}

.lora-loss-table-toggle:focus-visible {
  outline: var(--focus-width) solid rgb(var(--v-theme-primary));
  outline-offset: var(--focus-offset);
  border-radius: var(--radius-sm);
}

.lora-loss-table-wrap {
  max-height: 180px;
  overflow: auto;
  border: 1px solid rgba(var(--v-theme-on-surface), 0.12);
  border-radius: var(--radius-sm);
}

.lora-loss-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-xs);
}

.lora-loss-table-caption {
  text-align: left;
  padding: var(--space-2) var(--space-2);
  font-size: var(--text-2xs);
  opacity: 0.6;
}

.lora-loss-table th,
.lora-loss-table td {
  text-align: left;
  padding: var(--radius-sm) var(--space-3);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.lora-loss-table th {
  position: sticky;
  top: 0;
  background: rgb(var(--v-theme-surface));
}
</style>
