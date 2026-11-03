<template>
  <!-- Drawn inside the lightbox's transformed media, over exactly the
       displayed picture, so it follows zoom and pan like the face boxes.
       Decorative: the Edit tab's sliders and the toolbox are the controls. -->
  <div class="paint-layer" :style="layerStyle" aria-hidden="true">
    <!-- Extend: the new area as a checkerboard round the picture, the
         picture's old edge dashed, the new frame and its eight handles. The
         Edit tab's side fields are the accessible control. -->
    <template v-if="frame">
      <div class="paint-extend-new" :style="frame.style" />
      <div class="paint-extend-edge" />
      <div class="paint-extend-frame" :style="frame.style" />
      <span
        v-for="handle in frame.handles"
        :key="handle.sides"
        class="paint-extend-handle"
        :class="{ 'paint-extend-handle--hot': handle.sides === hot }"
        :data-sides="handle.sides"
        :style="handle.style"
      />
    </template>
    <img
      v-if="paint.showMask.value && paint.maskUrl.value"
      class="paint-bw"
      :src="paint.maskUrl.value"
      alt=""
    />
    <canvas v-show="!paint.showMask.value" ref="canvasRef" class="paint-ants" />
    <svg class="paint-draft" viewBox="0 0 1 1" preserveAspectRatio="none">
      <template v-if="crop">
        <path class="paint-crop-shade" :d="cropShade" fill-rule="evenodd" />
        <rect
          class="paint-crop-frame"
          :x="crop[0]"
          :y="crop[1]"
          :width="crop[2]"
          :height="crop[3]"
        />
        <line
          v-for="(line, index) in cropThirds"
          :key="index"
          class="paint-crop-third"
          v-bind="line"
        />
      </template>
      <template v-else-if="draft && draft.tool !== 'wand'">
        <rect
          v-if="draft.tool === 'rect'"
          class="paint-draft-shape"
          v-bind="draftBox"
        />
        <ellipse
          v-else-if="draft.tool === 'ellipse'"
          class="paint-draft-shape"
          :cx="draftBox.x + draftBox.width / 2"
          :cy="draftBox.y + draftBox.height / 2"
          :rx="draftBox.width / 2"
          :ry="draftBox.height / 2"
        />
        <polygon
          v-else-if="draft.tool === 'lasso'"
          class="paint-draft-shape"
          :points="draftPoints"
        />
        <polyline
          v-else
          class="paint-draft-stroke"
          :class="{ 'paint-draft-stroke--erase': draft.tool === 'eraser' }"
          :points="draftPoints"
          :style="{ strokeWidth: `${brushWidthPx}px` }"
        />
      </template>
    </svg>
  </div>
</template>

<script setup>
/**
 * The selection on the open picture, as the design draws it: an olive wash
 * with marching ants on the mask the server rendered, the gesture in progress
 * as a plain outline, and the crop frame with the cut-away part shaded.
 *
 * The wash and ants are computed from the server's PNG at display size, so
 * they show the REFINED edge, the one MASK receives. The eye swaps them for
 * the black-and-white mask itself.
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { wordLayerStyle } from "../../composables/usePictureText";

const props = defineProps({
  /** The `usePaintSelection` state. */
  paint: { type: Object, required: true },
  /** The lightbox's `lightboxDims`: display and natural size, offset. */
  dims: { type: Object, required: true },
  /** The CSS scale the layer is drawn at, so a handle stays 10 px on screen. */
  scale: { type: Number, default: 1 },
  /** The sides under the pointer with Extend in hand, e.g. `left,top`. */
  hot: { type: String, default: "" },
});

const canvasRef = ref(null);
const layerStyle = computed(() => wordLayerStyle(props.dims));

const draft = computed(() => props.paint.draft.value);

// Extend's frame, in percent of the picture: it runs from -left to 100+right.
const HANDLE_PX = 10;
const frame = computed(() => {
  if (!props.paint.extending?.value) return null;
  const { left, top, right, bottom } = props.paint.extension;
  const width = Math.max(1, props.dims.naturalWidth || 1);
  const height = Math.max(1, props.dims.naturalHeight || 1);
  const x0 = (-left / width) * 100;
  const x1 = 100 + (right / width) * 100;
  const y0 = (-top / height) * 100;
  const y1 = 100 + (bottom / height) * 100;
  const size = `${HANDLE_PX / (props.scale || 1)}px`;
  const columns = [
    [x0, "left"],
    [(x0 + x1) / 2, ""],
    [x1, "right"],
  ];
  const rows = [
    [y0, "top"],
    [(y0 + y1) / 2, ""],
    [y1, "bottom"],
  ];
  const handles = rows.flatMap(([y, row]) =>
    columns.flatMap(([x, column]) => {
      const sides = [column, row].filter(Boolean).join(",");
      if (!sides) return [];
      return [
        {
          sides,
          style: { left: `${x}%`, top: `${y}%`, width: size, height: size },
        },
      ];
    }),
  );
  return {
    style: {
      left: `${x0}%`,
      top: `${y0}%`,
      width: `${x1 - x0}%`,
      height: `${y1 - y0}%`,
      // The picture is cut out of the checkerboard, which lies under nothing.
      "--hole-x0": `${(-x0 / (x1 - x0)) * 100}%`,
      "--hole-y0": `${(-y0 / (y1 - y0)) * 100}%`,
      "--hole-x1": `${((100 - x0) / (x1 - x0)) * 100}%`,
      "--hole-y1": `${((100 - y0) / (y1 - y0)) * 100}%`,
      "--line": `${1.5 / (props.scale || 1)}px`,
    },
    handles,
  };
});
const crop = computed(() => {
  const current = draft.value;
  if (current?.tool === "crop") return boxOf(current);
  return props.paint.cropBox.value;
});

function boxOf(current) {
  const x = Math.min(current.start.x, current.end.x);
  const y = Math.min(current.start.y, current.end.y);
  return [
    x,
    y,
    Math.abs(current.end.x - current.start.x),
    Math.abs(current.end.y - current.start.y),
  ];
}

const draftBox = computed(() => {
  const [x, y, width, height] = draft.value?.start
    ? boxOf(draft.value)
    : [0, 0, 0, 0];
  return { x, y, width, height };
});

const draftPoints = computed(() =>
  (draft.value?.points || []).map((p) => `${p.x},${p.y}`).join(" "),
);

// The brush is sized in the picture's pixels; the stroke is drawn in the
// layer's, which is the picture at display size.
const brushWidthPx = computed(() => {
  const { width = 1, naturalWidth = 1 } = props.dims;
  return (2 * props.paint.brushSize.value * width) / Math.max(1, naturalWidth);
});

const cropShade = computed(() => {
  const [x, y, w, h] = crop.value;
  return `M0 0H1V1H0Z M${x} ${y}H${x + w}V${y + h}H${x}Z`;
});

const cropThirds = computed(() => {
  const [x, y, w, h] = crop.value;
  return [1, 2].flatMap((n) => [
    { x1: x + (w * n) / 3, y1: y, x2: x + (w * n) / 3, y2: y + h },
    { x1: x, y1: y + (h * n) / 3, x2: x + w, y2: y + (h * n) / 3 },
  ]);
});

// ── the wash and the ants ────────────────────────────────────────────────
// `base` is the wash at display size; `edges` the pixels on the mask's edge.
let base = null;
let edges = null;
let phase = 0;
let antsTimer = null;
let loadToken = 0;

const ANTS_INTERVAL_MS = 150;
// A resize (a window dragged, the inspector sliding) changes the display size
// every frame; the canvas stretches meanwhile and is rebuilt once it settles.
const RESIZE_SETTLE_MS = 150;
let resizeTimer = null;
const ANT_LENGTH = 4;
// The design's wash: the selected colour at a third, so the picture still reads.
const WASH_ALPHA = 0.34;

function washColour(canvas) {
  const value = getComputedStyle(canvas).getPropertyValue("--active-bar");
  const match = value.match(/(\d+)\D+(\d+)\D+(\d+)/);
  if (match) return match.slice(1, 4).map(Number);
  // Both themes define it (style.css); unread, the wash takes the ants' white.
  console.warn(`Selection wash: could not read --active-bar (${value})`);
  return [255, 255, 255];
}

function paintFrame() {
  const canvas = canvasRef.value;
  if (!canvas || !base) return;
  const frame = new ImageData(new Uint8ClampedArray(base.data), base.width);
  const pixels = frame.data;
  for (let i = 0; i < edges.length; i += 2) {
    const x = edges[i];
    const y = edges[i + 1];
    const ink = ((x + y + phase) / ANT_LENGTH) & 1 ? 0 : 255;
    const at = (y * base.width + x) * 4;
    pixels[at] = ink;
    pixels[at + 1] = ink;
    pixels[at + 2] = ink;
    pixels[at + 3] = 255;
  }
  canvas.getContext("2d", { willReadFrequently: true }).putImageData(frame, 0, 0);
}

function stopAnts() {
  clearInterval(antsTimer);
  antsTimer = null;
}

async function rebuild() {
  clearTimeout(resizeTimer);
  const canvas = canvasRef.value;
  const url = props.paint.maskUrl.value;
  const token = ++loadToken;
  stopAnts();
  base = null;
  if (!canvas) return;
  const width = Math.max(1, Math.round(props.dims.width));
  const height = Math.max(1, Math.round(props.dims.height));
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d", { willReadFrequently: true });
  context.clearRect(0, 0, width, height);
  if (!url) return;
  const image = new Image();
  image.src = url;
  try {
    await image.decode();
  } catch (err) {
    console.warn("Could not draw the selection's mask:", err);
    return;
  }
  if (token !== loadToken) return;
  // Read on a canvas of its own: the black-and-white mask never reaches the
  // screen, so nothing short of the finished wash is ever shown.
  const reader = document.createElement("canvas");
  reader.width = width;
  reader.height = height;
  const readContext = reader.getContext("2d", { willReadFrequently: true });
  readContext.drawImage(image, 0, 0, width, height);
  const mask = readContext.getImageData(0, 0, width, height).data;
  const [r, g, b] = washColour(canvas);
  base = context.createImageData(width, height);
  const found = [];
  const on = (x, y) => mask[(y * width + x) * 4] >= 128;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const at = (y * width + x) * 4;
      const level = mask[at];
      if (level) {
        base.data[at] = r;
        base.data[at + 1] = g;
        base.data[at + 2] = b;
        base.data[at + 3] = Math.round(level * WASH_ALPHA);
      }
      if (
        level >= 128 &&
        (x === 0 ||
          y === 0 ||
          x === width - 1 ||
          y === height - 1 ||
          !on(x - 1, y) ||
          !on(x + 1, y) ||
          !on(x, y - 1) ||
          !on(x, y + 1))
      ) {
        found.push(x, y);
      }
    }
  }
  edges = found;
  paintFrame();
  antsTimer = setInterval(() => {
    phase = (phase + 1) % (ANT_LENGTH * 2);
    paintFrame();
  }, ANTS_INTERVAL_MS);
}

watch(
  () => props.paint.maskUrl.value,
  () => void rebuild(),
);

watch([() => Math.round(props.dims.width), () => Math.round(props.dims.height)], () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => void rebuild(), RESIZE_SETTLE_MS);
});

// The ants stop while the black-and-white view hides them, and come back
// with it off.
watch(
  () => props.paint.showMask.value,
  (bw) => {
    if (bw) stopAnts();
    else void rebuild();
  },
);

onBeforeUnmount(() => {
  clearTimeout(resizeTimer);
  loadToken += 1;
  stopAnts();
});
</script>

<style scoped>
.paint-layer {
  position: absolute;
  pointer-events: none;
}

/* Extend. The checkerboard is the dark surface's ink at two strengths, the
   picture cut out of it; lines and handles keep their screen size at any
   scale (`--line`, the handle's own size). */
.paint-extend-new,
.paint-extend-frame,
.paint-extend-edge {
  position: absolute;
  box-sizing: border-box;
}

.paint-extend-new {
  background-color: rgba(var(--v-theme-on-dark-surface), 0.12);
  background-image:
    linear-gradient(45deg, rgba(var(--v-theme-on-dark-surface), 0.08) 25%, transparent 25%, transparent 75%, rgba(var(--v-theme-on-dark-surface), 0.08) 75%),
    linear-gradient(45deg, rgba(var(--v-theme-on-dark-surface), 0.08) 25%, transparent 25%, transparent 75%, rgba(var(--v-theme-on-dark-surface), 0.08) 75%);
  background-size: calc(2 * var(--space-3)) calc(2 * var(--space-3));
  background-position:
    0 0,
    var(--space-3) var(--space-3);
  clip-path: polygon(
    evenodd,
    0 0,
    100% 0,
    100% 100%,
    0 100%,
    0 0,
    var(--hole-x0) var(--hole-y0),
    var(--hole-x1) var(--hole-y0),
    var(--hole-x1) var(--hole-y1),
    var(--hole-x0) var(--hole-y1),
    var(--hole-x0) var(--hole-y0)
  );
}

.paint-extend-edge {
  inset: 0;
  border: 1px dashed rgba(var(--v-theme-on-dark-surface), 0.55);
}

.paint-extend-frame {
  border: var(--line) solid rgb(var(--v-theme-on-dark-surface));
}

.paint-extend-handle {
  position: absolute;
  transform: translate(-50%, -50%);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-on-dark-surface));
  box-shadow: 0 0 0 1px rgb(var(--v-theme-shadow));
}

.paint-extend-handle--hot {
  background: var(--active-bar);
}

.paint-ants,
.paint-bw,
.paint-draft {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.paint-draft {
  overflow: visible;
}

.paint-draft-shape,
.paint-crop-frame {
  fill: rgba(var(--v-theme-on-dark-surface), 0.1);
  stroke: rgb(var(--v-theme-on-dark-surface));
  stroke-width: 1.5px;
  stroke-dasharray: 5 4;
  vector-effect: non-scaling-stroke;
}

.paint-crop-frame {
  fill: none;
  stroke-dasharray: none;
}

.paint-crop-shade {
  fill: var(--scrim-photo-strong);
}

.paint-crop-third {
  stroke: rgba(var(--v-theme-on-dark-surface), 0.5);
  stroke-width: 1px;
  vector-effect: non-scaling-stroke;
}

.paint-draft-stroke {
  fill: none;
  stroke: var(--active-bar);
  stroke-opacity: 0.5;
  stroke-linecap: round;
  stroke-linejoin: round;
  vector-effect: non-scaling-stroke;
}

.paint-draft-stroke--erase {
  stroke: rgb(var(--v-theme-on-dark-surface));
}
</style>
