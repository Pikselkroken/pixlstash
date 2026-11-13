<template>
  <!-- A large pop-out over whatever opened it, for looking closely at one
       picture: wheel zoom anchored at the cursor, drag to pan, double-click
       or Z for fit ↔ 100%. The zoom FEEL is the family's (useWheelZoom); this
       is only its frame. A StackLayer, so it clears the dialog it was opened
       from with no z-index of its own. -->
  <StackLayer :open="open">
    <template v-if="open">
      <div class="zp-scrim" @click="emit('close')" />
      <div
        ref="rootEl"
        class="zp"
        role="dialog"
        aria-modal="true"
        :aria-label="caption || alt"
        tabindex="-1"
        data-testid="zoom-popout"
        @keydown="onKey"
      >
        <div class="zp-bar">
          <AppBarButton
            v-if="hasPrev || hasNext"
            icon="chevron-left"
            :disabled="!hasPrev"
            tooltip="Previous"
            @click="emit('prev')"
          />
          <span class="zp-cap">{{ caption }}</span>
          <AppBarButton
            v-if="hasPrev || hasNext"
            icon="chevron-right"
            :disabled="!hasNext"
            tooltip="Next"
            @click="emit('next')"
          />
          <slot name="actions" />
          <span class="zp-sep" aria-hidden="true" />
          <AppBarButton icon="minus" tooltip="Zoom out" @click="step(0.8)" />
          <span class="zp-pct">{{ zoom.percentLabel.value }}</span>
          <AppBarButton icon="plus" tooltip="Zoom in" @click="step(1.25)" />
          <AppBarButton
            :icon="zoom.atFit.value ? 'magnify-plus-outline' : 'fit-to-screen-outline'"
            :tooltip="zoom.atFit.value ? '100% (Z)' : 'Fit (Z)'"
            @click="zoom.toggleSnap()"
          />
          <AppBarButton icon="close" tooltip="Close (Esc)" @click="emit('close')" />
        </div>
        <div
          ref="stageEl"
          class="zp-stage"
          :class="{ 'zp-stage--pan': zoom.aboveFit.value, 'zp-stage--drag': dragging }"
          @wheel.prevent="onWheel"
          @pointerdown="onDown"
          @pointermove="onMove"
          @pointerup="onUp"
          @pointercancel="onUp"
          @dblclick="zoom.toggleSnap(cursor($event))"
        >
          <!-- draggable=false: the browser's own image drag would win the
               race with the pan. -->
          <img
            :src="src"
            :alt="alt"
            :style="imgStyle"
            draggable="false"
            @load="onLoad"
          />
        </div>
        <span class="zp-sr" role="status">{{ zoom.announcement.value }}</span>
      </div>
    </template>
  </StackLayer>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import { useWheelZoom } from "../../composables/useWheelZoom";
import AppBarButton from "./AppBarButton.vue";
import StackLayer from "./StackLayer.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  src: { type: String, default: "" },
  alt: { type: String, default: "" },
  caption: { type: String, default: "" },
  hasPrev: { type: Boolean, default: false },
  hasNext: { type: Boolean, default: false },
  /** A wheel that opened the pop-out from a thumbnail: `{ deltaY, deltaMode,
   * fx, fy }`, fx/fy the cursor as a fraction of the thumbnail. Applied once
   * the picture is measured, so the gesture carries on into the pop-out. */
  entry: { type: Object, default: null },
});

const emit = defineEmits(["close", "prev", "next"]);

const zoom = useWheelZoom({ floorPolicy: "rest" });
const rootEl = ref(null);
const stageEl = ref(null);
const natural = ref({ width: 0, height: 0 });
const dragging = ref(false);
let last = null;
let pendingEntry = null;
let observer = null;

function measure() {
  const el = stageEl.value;
  if (!el || !natural.value.width) return;
  zoom.setMeasurements({
    containerWidth: el.clientWidth,
    containerHeight: el.clientHeight,
    naturalWidth: natural.value.width,
    naturalHeight: natural.value.height,
  });
  const e = pendingEntry;
  pendingEntry = null;
  if (!e || zoom.scale.value === null) return;
  // The thumbnail's point, where it sits in the fitted picture.
  const f = zoom.fitScale.value;
  zoom.wheelZoom(e, {
    x: el.clientWidth / 2 + (e.fx - 0.5) * natural.value.width * f,
    y: el.clientHeight / 2 + (e.fy - 0.5) * natural.value.height * f,
  });
}

function onLoad(event) {
  natural.value = {
    width: event.target.naturalWidth,
    height: event.target.naturalHeight,
  };
  measure();
}

watch(
  () => [props.open, props.src],
  async ([open]) => {
    zoom.reset();
    natural.value = { width: 0, height: 0 };
    pendingEntry = open ? props.entry : null;
    observer?.disconnect();
    observer = null;
    if (!open) return;
    await nextTick();
    rootEl.value?.focus();
    if (stageEl.value && typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(measure);
      observer.observe(stageEl.value);
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => observer?.disconnect());

const imgStyle = computed(() => {
  if (zoom.scale.value === null) return { visibility: "hidden" };
  const f = zoom.fitScale.value;
  const { x, y } = zoom.offset.value;
  return {
    width: `${natural.value.width * f}px`,
    height: `${natural.value.height * f}px`,
    transform: `translate(-50%, -50%) translate(${x}px, ${y}px) scale(${zoom.transformScale.value})`,
  };
});

function cursor(event) {
  const r = stageEl.value.getBoundingClientRect();
  return { x: event.clientX - r.left, y: event.clientY - r.top };
}

function onWheel(event) {
  zoom.wheelZoom(event, cursor(event));
}

function step(factor) {
  if (zoom.scale.value !== null) zoom.snapTo(zoom.scale.value * factor);
}

function onDown(event) {
  if (event.button !== 0 || !zoom.aboveFit.value) return;
  dragging.value = true;
  last = { x: event.clientX, y: event.clientY };
  event.currentTarget.setPointerCapture?.(event.pointerId);
}

function onMove(event) {
  if (!dragging.value) return;
  zoom.panBy(event.clientX - last.x, event.clientY - last.y);
  last = { x: event.clientX, y: event.clientY };
}

function onUp() {
  dragging.value = false;
}

function onKey(event) {
  const k = event.key;
  if (k === "Escape") emit("close");
  else if (k === "ArrowLeft" && props.hasPrev) emit("prev");
  else if (k === "ArrowRight" && props.hasNext) emit("next");
  else if (k === "z" || k === "Z") zoom.toggleSnap();
  else if (k === "+" || k === "=") step(1.25);
  else if (k === "-" || k === "_") step(0.8);
  else return;
  // Never reaches the dialog behind: Escape here closes only the pop-out.
  event.preventDefault();
  event.stopPropagation();
}
</script>

<style scoped>
.zp-scrim {
  position: fixed;
  inset: var(--titlebar-h) 0 0 0;
  background: rgba(var(--v-theme-scrim), 0.55);
}

.zp {
  position: fixed;
  /* Under the desktop title bar, so the window controls stay usable. */
  inset: calc(var(--titlebar-h) + var(--space-6)) var(--space-6) var(--space-6);
  display: flex;
  flex-direction: column;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-4);
  overflow: hidden;
  outline: none;
}

.zp-bar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4) var(--space-3) var(--space-5);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.zp-cap {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.zp-sep {
  width: 1px;
  height: 20px;
  margin: 0 var(--space-2);
  background: rgb(var(--v-theme-divider));
}

/* Reserved width: the bar does not jump as the readout changes. */
.zp-pct {
  min-width: 44px;
  text-align: center;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* A photo-judgement ground: dark in both themes, like the lightbox. */
.zp-stage {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  background: rgb(var(--v-theme-dark-surface));
  cursor: zoom-in;
  user-select: none;
  touch-action: none;
}

.zp-stage--pan {
  cursor: grab;
}

.zp-stage--drag {
  cursor: grabbing;
}

.zp-stage img {
  position: absolute;
  left: 50%;
  top: 50%;
  display: block;
  max-width: none;
}

.zp-sr {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
