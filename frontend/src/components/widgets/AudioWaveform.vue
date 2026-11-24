<template>
  <!-- A clip's sound as plain bars, in the lane under the filmstrip's frames
       (#2807). The lane's width is the video's length; the audio covers
       the share of it its own length is of the video's. Click or the arrow
       keys seek. -->
  <div
    class="audio-waveform"
    role="slider"
    aria-label="Clip sound"
    :tabindex="seekable ? 0 : -1"
    :aria-disabled="seekable ? undefined : 'true'"
    :aria-valuemin="0"
    :aria-valuemax="seekable ? videoDuration : 0"
    :aria-valuenow="seekable ? currentTime : 0"
    :aria-valuetext="clock(seekable ? currentTime : 0)"
    @click="onClick"
    @keydown="onKeydown"
  >
    <svg
      class="audio-waveform-svg"
      viewBox="0 0 1 16"
      preserveAspectRatio="none"
      aria-hidden="true"
      focusable="false"
    >
      <path class="audio-waveform-rest" :d="barsPath(false)" />
      <path class="audio-waveform-played" :d="barsPath(true)" />
    </svg>
  </div>
</template>

<script setup>
import { computed } from "vue";

// Seconds moved by one press of an arrow key.
// ponytail: a fixed step; a finer one with Shift if clips get long enough to need it.
const SEEK_STEP = 5;
// A bar's width as a share of its pitch.
const BAR_FILL = 0.6;
const LANE_H = 16;

const props = defineProps({
  // 512 peaks in 0..1, from GET /pictures/waveforms/{id}.json.
  peaks: { type: Array, required: true },
  // The audio's length in seconds.
  duration: { type: Number, required: true },
  // The video's length in seconds; null until the browser knows it. Then
  // the peaks run across the whole lane with no playhead and no seeking.
  videoDuration: { type: Number, default: null },
  // The video's position in seconds.
  currentTime: { type: Number, default: 0 },
});

const emit = defineEmits(["seek"]);

const seekable = computed(() => props.videoDuration > 0);

// The share of the lane the audio covers, from the left.
const span = computed(() =>
  seekable.value ? Math.min(1, props.duration / props.videoDuration) : 1,
);

// How far the playhead has got along the lane, 0..1. Without a video length
// nothing is unplayed, so every bar is drawn in full colour.
const progress = computed(() => {
  if (!seekable.value) return 1;
  return Math.min(1, Math.max(0, props.currentTime / props.videoDuration));
});

// One path of bars, either the played ones or the rest, in the lane's own
// units (1 wide, 16 tall) so `preserveAspectRatio="none"` stretches them.
function barsPath(played) {
  const count = props.peaks.length;
  const pitch = span.value / count;
  const width = pitch * BAR_FILL;
  let d = "";
  props.peaks.forEach((peak, i) => {
    const x = i * pitch;
    if (x < progress.value !== played) return;
    const h = Math.max(1, Math.min(1, Number(peak) || 0) * LANE_H);
    d += `M${x} ${LANE_H - h}h${width}v${h}h${-width}z`;
  });
  return d;
}

function onClick(event) {
  if (!seekable.value) return;
  const rect = event.currentTarget.getBoundingClientRect();
  if (!rect.width) return;
  const share = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  emit("seek", share * props.videoDuration);
}

function onKeydown(event) {
  if (!seekable.value) return;
  let step = 0;
  if (event.key === "ArrowLeft") step = -SEEK_STEP;
  else if (event.key === "ArrowRight") step = SEEK_STEP;
  if (!step) return;
  // The arrows seek the clip here; they must not also step the lightbox.
  event.preventDefault();
  event.stopPropagation();
  emit(
    "seek",
    Math.min(props.videoDuration, Math.max(0, props.currentTime + step)),
  );
}

function clock(seconds) {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
</script>

<style scoped>
/* The lane sits under the frames, inset half a tile at each end so its axis
   meets the tile centres. `--tile-w` is inherited from the storyboard. */
.audio-waveform {
  height: 16px;
  margin: var(--space-2) calc(var(--tile-w, 0px) / 2) 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.audio-waveform[aria-disabled="true"] {
  cursor: default;
}

.audio-waveform:focus-visible {
  outline: none;
  box-shadow: inset 0 0 0 var(--focus-width)
    rgb(var(--v-theme-dark-surface-primary));
}

.audio-waveform-svg {
  display: block;
  width: 100%;
  height: 100%;
}

/* Played bars in full colour; the rest dimmed. Lifted teal, the token the
   lightbox's dark surface reads (docs/design/design-tokens.css). */
.audio-waveform-played {
  fill: rgb(var(--v-theme-dark-surface-tertiary));
}

.audio-waveform-rest {
  fill: rgb(var(--v-theme-dark-surface-tertiary));
  opacity: 0.5;
}
</style>
