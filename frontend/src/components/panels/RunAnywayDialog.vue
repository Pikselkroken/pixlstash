<template>
  <!-- The one dialog of the GPU queue (visual-language, Run anyway): it can
       crash a run, so it asks. The default button is Keep waiting, the first
       in the tab order, and is what Escape and the close button also do. -->
  <AppDialog
    :open="open"
    :title="title"
    size="md"
    @close="emit('close')"
  >
    <p class="ra-text" data-testid="ra-text">{{ text }}</p>

    <div
      class="ra-bar"
      role="img"
      :aria-label="barLabel"
      data-testid="ra-bar"
    >
      <span
        v-for="segment in bar.segments"
        :key="segment.key"
        class="ra-seg"
        :class="[`ra-seg--${segment.kind}`, { 'ra-seg--reserved': segment.reserved }]"
        :style="{ width: pct(segment.pct) }"
      ></span>
      <span
        class="ra-request"
        :style="{ left: pct(bar.startPct), width: pct(bar.needPct - bar.overPct) }"
        data-testid="ra-request"
      >
        We're asking for {{ formatGb(needMb) }}
      </span>
      <span
        v-if="bar.overMb > 0"
        class="ra-over"
        :style="{ width: pct(bar.overPct) }"
        data-testid="ra-over"
      >
        {{ formatGb(bar.overMb) }} over
      </span>
    </div>

    <ul class="ra-legend">
      <li v-for="segment in bar.segments" :key="segment.key">
        <span
          class="ra-swatch"
          :class="[`ra-seg--${segment.kind}`, { 'ra-seg--reserved': segment.reserved }]"
          aria-hidden="true"
        ></span>
        {{ segmentName(segment.kind) }}{{ segment.reserved ? " reserved" : "" }}
        {{ formatGb(segment.mb) }}
      </li>
    </ul>

    <template #footer>
      <AppButton
        variant="ghost"
        data-testid="ra-keep-waiting"
        @click="emit('close')"
      >
        Keep waiting
      </AppButton>
      <AppButton
        data-testid="ra-run-anyway"
        :disabled="busy"
        @click="emit('confirm')"
      >
        Run anyway
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
import { computed } from "vue";

import { formatGb, requestOnBar } from "../../utils/gpuMemory";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The waiting lease's `label`, e.g. "ComfyUI 'Klein inpaint'". */
  label: { type: String, default: "" },
  /** MB this run asks for. */
  needMb: { type: Number, default: 0 },
  /** The GPU the run waits on (`resources.devices` entry). */
  device: { type: Object, default: null },
  /** `resources.holders`. */
  holders: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "confirm"]);

const SEGMENT_NAMES = {
  pixlstash: "PixlStash",
  comfyui: "ComfyUI",
  aitoolkit: "Training",
  other: "Other apps",
  spare: "Kept spare",
};

const title = computed(() => `Run ${props.label || "this run"} anyway?`);

const bar = computed(() =>
  requestOnBar(props.device ?? { uuid: "", total_mb: 0 }, props.holders, props.needMb),
);

const text = computed(() => {
  if (bar.value.overMb > 0) {
    // Everything kept back: the spare margin and the reservations not yet used.
    const keptMb = bar.value.segments
      .filter((segment) => segment.kind === "spare" || segment.reserved)
      .reduce((sum, segment) => sum + segment.mb, 0);
    const kept = keptMb > 0 ? ` even counting the ${formatGb(keptMb)} PixlStash keeps spare` : "";
    return `That is more memory than this GPU has${kept}. This run or the other job can run out of GPU memory and fail.`;
  }
  return "Starting it now may run out of GPU memory, and this run or the other job could fail.";
});

const barLabel = computed(
  () =>
    `${formatGb(bar.value.total)} GPU, ${formatGb(bar.value.free)} free; this run asks for ${formatGb(props.needMb)}`,
);

function segmentName(kind) {
  return SEGMENT_NAMES[kind] ?? kind;
}

/** A share of the bar as a CSS width, clamped so a rounding error cannot overflow. */
function pct(value) {
  return `${Math.min(100, Math.max(0, Number(value) || 0)).toFixed(2)}%`;
}
</script>

<style scoped>
.ra-text {
  margin: 0 0 var(--space-5);
  font-size: var(--text-base);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* The GPU's physical memory. Segments fill from the left; the request is laid
   over the free part from where it starts, and sticks out past the end. */
.ra-bar {
  position: relative;
  display: flex;
  height: var(--space-6);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.2);
}

.ra-seg {
  display: block;
  height: 100%;
  flex: none;
}

.ra-request {
  position: absolute;
  top: calc(var(--space-1) * -1);
  bottom: calc(var(--space-1) * -1);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 var(--space-2);
  overflow: hidden;
  white-space: nowrap;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-on-surface), 0.12);
  outline: 1px dashed rgb(var(--v-theme-on-surface));
  outline-offset: -1px;
  border-radius: var(--radius-sm);
}

/* The part of the request beyond the card: the one red in the dialog. */
.ra-over {
  position: absolute;
  left: 100%;
  top: calc(var(--space-1) * -1);
  bottom: calc(var(--space-1) * -1);
  display: flex;
  align-items: center;
  padding-left: var(--space-2);
  white-space: nowrap;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-error));
  border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  background: rgba(var(--v-theme-error), 0.25);
  overflow: visible;
}

/* Tenant colours. Fill only; the words in the legend say which is which. */
.ra-seg--pixlstash {
  background: rgb(var(--tenant-pixlstash));
}
.ra-seg--comfyui {
  background: rgb(var(--tenant-comfyui));
}
.ra-seg--aitoolkit {
  background: rgb(var(--tenant-aitoolkit));
}
.ra-seg--pixlstash.ra-seg--reserved {
  background: rgba(var(--tenant-pixlstash), 0.4);
}
.ra-seg--comfyui.ra-seg--reserved {
  background: rgba(var(--tenant-comfyui), 0.4);
}
.ra-seg--aitoolkit.ra-seg--reserved {
  background: rgba(var(--tenant-aitoolkit), 0.4);
}
.ra-seg--other {
  background: var(--hatch-not-ours);
}
.ra-seg--spare {
  background: var(--spare-margin);
}

.ra-legend {
  list-style: none;
  margin: var(--space-4) 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-5);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.ra-legend li {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.ra-swatch {
  width: var(--space-3);
  height: var(--space-3);
  border-radius: var(--radius-sm);
  flex: none;
}
</style>
