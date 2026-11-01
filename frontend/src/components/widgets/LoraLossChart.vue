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
 *   xScale: (step) => fraction in [0, 1] of the plot width. Injected by the
 *     Training runs view (#2118) so the curve shares the sample grid's step
 *     axis; omitted, the steps are spread linearly over the width.
 *   markers: Array<{ step:number, kind:"checkpoint"|"cover" }>  vertical
 *     rules at checkpoint steps; the cover's is olive with a diamond on the
 *     curve.
 *   smooth: draw the raw points faint and a smoothed line over them.
 *   height: the plot's height in px.
 *   compact: the caption is read out but not shown, and there is no table
 *     toggle, for a chart laid into a grid whose rows must stay aligned.
 *   emptyText: what stands in for the chart before it has two points.
 *
 * Drawn in teal, the chart hue, in the shelf's run band and the live view alike.
 */
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from "vue";

const props = defineProps({
  curve: { type: Array, default: () => [] },
  xScale: { type: Function, default: null },
  markers: { type: Array, default: () => [] },
  smooth: { type: Boolean, default: false },
  height: { type: Number, default: 80 },
  compact: { type: Boolean, default: false },
  emptyText: { type: String, default: "Not enough loss data to chart." },
});

// The weight of each new point in the smoothed line: a 20-point EMA.
const SMOOTHING = 2 / 21;

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

/** The exponential moving average of the losses, one value per point. */
function smoothed(pts) {
  let value = null;
  return pts.map((p) => {
    value = value === null ? p.loss : value + SMOOTHING * (p.loss - value);
    return value;
  });
}

/** The latest smoothed loss, for a caller's headline figure. */
const latestSmoothed = computed(() => {
  const values = smoothed(points.value);
  return values.length ? values[values.length - 1] : null;
});

defineExpose({ latestSmoothed });

function cssColor(canvas, name, fallback) {
  return getComputedStyle(canvas).getPropertyValue(name).trim() || fallback;
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
  // An injected scale owns the whole width: its fractions are column centres,
  // which must land under the columns, so there is no side padding to add.
  const padX = props.xScale ? 0 : pad;
  const plotW = width - padX * 2;
  const plotH = height - pad * 2;
  const steps = pts.map((p) => p.step);
  const losses = pts.map((p) => p.loss);
  const minStep = Math.min(...steps);
  const maxStep = Math.max(...steps);
  const minLoss = Math.min(...losses);
  const maxLoss = Math.max(...losses);
  const stepRange = maxStep - minStep || 1;
  const lossRange = maxLoss - minLoss || 1;

  const fraction = props.xScale || ((step) => (step - minStep) / stepRange);
  const xOf = (step) => padX + fraction(step) * plotW;
  // Invert y: lower loss is higher on the chart.
  const yOf = (loss) => pad + (1 - (loss - minLoss) / lossRange) * plotH;

  const line = themeRgb("surface-tertiary", "143,188,198");
  const dot = themeRgb("tertiary", "70,112,122");
  const ink = themeRgb("on-surface", "242,229,218");

  for (const marker of props.markers) {
    const x = xOf(marker.step);
    ctx.beginPath();
    ctx.moveTo(x, pad);
    ctx.lineTo(x, pad + plotH);
    if (marker.kind === "cover") {
      ctx.setLineDash([]);
      ctx.strokeStyle = cssColor(canvas, "--active-bar", `rgb(${ink})`);
    } else {
      ctx.setLineDash([2, 3]);
      ctx.strokeStyle = `rgba(${ink}, 0.16)`;
    }
    ctx.lineWidth = 1;
    ctx.stroke();
  }
  ctx.setLineDash([]);

  const values = props.smooth ? smoothed(pts) : losses;
  if (props.smooth) {
    ctx.fillStyle = `rgba(${dot}, 0.55)`;
    for (const p of pts) {
      ctx.beginPath();
      ctx.arc(xOf(p.step), yOf(p.loss), 1.3, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Line
  ctx.beginPath();
  pts.forEach((p, i) => {
    const x = xOf(p.step);
    const y = yOf(values[i]);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = `rgba(${line}, 0.95)`;
  ctx.lineWidth = props.smooth ? 2 : 1.5;
  ctx.lineJoin = "round";
  ctx.stroke();

  if (!props.smooth) {
    // Soft fill under the line
    const last = pts[pts.length - 1];
    ctx.lineTo(xOf(last.step), pad + plotH);
    ctx.lineTo(xOf(pts[0].step), pad + plotH);
    ctx.closePath();
    ctx.fillStyle = `rgba(${dot}, 0.18)`;
    ctx.fill();
  }

  // The cover's step on the curve: an olive diamond, the selected-item mark.
  const cover = props.markers.find((m) => m.kind === "cover");
  if (cover && cover.step >= minStep && cover.step <= maxStep) {
    // On the drawn line at the cover's step: interpolated between the two
    // points around it, so the diamond stays on its column's rule.
    const after = Math.max(1, pts.findIndex((p) => p.step >= cover.step));
    const a = pts[after - 1];
    const b = pts[after];
    const t = b.step === a.step ? 0 : (cover.step - a.step) / (b.step - a.step);
    const x = xOf(cover.step);
    const y = yOf(values[after - 1] + t * (values[after] - values[after - 1]));
    ctx.save();
    ctx.translate(x, y);
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = `rgb(${themeRgb("primary", "86,115,9")})`;
    ctx.strokeStyle = cssColor(canvas, "--active-bar", `rgb(${ink})`);
    ctx.lineWidth = 2;
    ctx.fillRect(-5, -5, 10, 10);
    ctx.strokeRect(-5, -5, 10, 10);
    ctx.restore();
  }
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

watch(
  () => [props.curve, props.markers, props.xScale, props.smooth, props.height],
  scheduleDraw,
  { deep: true },
);
</script>

<template>
  <div class="lora-loss-chart">
    <figure class="lora-loss-figure" role="group" :aria-label="summary">
      <canvas
        v-show="hasData"
        ref="canvasRef"
        class="lora-loss-canvas"
        :style="{ height: `${height}px` }"
        aria-hidden="true"
      ></canvas>
      <p v-if="!hasData" class="lora-loss-empty">{{ emptyText }}</p>
      <figcaption
        class="lora-loss-caption"
        :class="{ 'visually-hidden': compact }"
      >
        {{ summary }}
      </figcaption>
    </figure>

    <button
      v-if="points.length && !compact"
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
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid rgba(var(--v-theme-on-surface), 0.07);
}

.lora-loss-table th {
  position: sticky;
  top: 0;
  background: rgb(var(--v-theme-surface));
}
</style>
