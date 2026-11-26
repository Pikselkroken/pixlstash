<template>
  <!-- A clip's eleven frames, first to last, in the lightbox's filmstrip band
       (#2147), with its audio's waveform under them on the same axis (#2807).
       Each tile seeks the video to its moment; hover or focus lifts the whole
       frame above the tile. -->
  <div
    v-if="!failed"
    class="storyboard"
    :class="{ 'storyboard--loaded': aspect }"
    :style="spriteStyle"
    role="group"
    aria-label="Clip frames"
  >
    <!-- The sprite's own size gives the frame's shape, so the tiles crop it
         and the preview shows it whole without reading the picture's
         stored dimensions. A 404 hides the strip. -->
    <img
      :key="src"
      class="storyboard-probe"
      :src="src"
      alt=""
      aria-hidden="true"
      @load="onLoad"
      @error="onError"
    />
    <div class="storyboard-tiles">
      <button
        v-for="i in FRAMES"
        :key="i"
        type="button"
        class="storyboard-tile"
        :class="{ 'storyboard-tile--current': i - 1 === currentIndex }"
        :aria-current="i - 1 === currentIndex ? 'true' : undefined"
        :aria-disabled="seekable ? undefined : 'true'"
        :aria-label="seekable ? `Seek to ${clock(timeOf(i - 1))}` : `Frame ${i} of ${FRAMES}`"
        :tabindex="seekable ? 0 : -1"
        draggable="false"
        @click.stop="onTileClick(i - 1)"
        @mouseenter="previewIndex = i - 1"
        @mouseleave="previewIndex = null"
        @focus="previewIndex = i - 1"
        @blur="previewIndex = null"
      >
        <span
          v-if="aspect"
          class="storyboard-frame"
          :style="{ '--tile': i - 1 }"
        ></span>
      </button>
    </div>
    <!-- Inset half a tile at each end, so its axis meets the tile centres
         and the one playhead crosses both. No lane for a silent clip. -->
    <div
      v-if="bars"
      class="storyboard-lane"
      role="slider"
      aria-label="Position in the clip"
      aria-valuemin="0"
      :aria-valuemax="Math.round(axis)"
      :aria-valuenow="Math.round(currentTime)"
      :aria-valuetext="clock(currentTime)"
      :aria-disabled="seekable ? undefined : 'true'"
      :tabindex="seekable ? 0 : -1"
      @click.stop="onLaneClick"
      @keydown="onLaneKeydown"
    >
      <svg
        class="storyboard-wave"
        :style="{ width: `${span * 100}%` }"
        :viewBox="`0 0 ${BARS} 100`"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <rect
          v-for="(peak, i) in bars"
          :key="i"
          :class="{ played: playhead !== null && ((i + 0.5) / BARS) * span <= playhead }"
          :x="i + 0.18"
          :y="50 - peak / 2"
          width="0.64"
          :height="peak"
          rx="0.3"
        />
      </svg>
    </div>
    <span
      v-if="playhead !== null"
      class="storyboard-playhead"
      :style="{ '--at': playhead }"
      aria-hidden="true"
    ></span>
    <div
      v-if="aspect && previewIndex !== null"
      class="storyboard-preview"
      :style="{ '--tile': previewIndex }"
      aria-hidden="true"
    >
      <span class="storyboard-preview-frame"></span>
      <span class="storyboard-preview-caption">
        <span v-if="seekable">Seek to {{ clock(timeOf(previewIndex)) }}</span>
        <span class="storyboard-preview-count num"
          >{{ previewIndex + 1 }} / {{ FRAMES }}</span
        >
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue";

// The backend's STORYBOARD_FRAMES: tile i is i / (FRAMES - 1) of the clip.
const FRAMES = 11;
// The waveform's 512 peaks, drawn as this many bars (each the loudest of its
// share), as the design draws them.
const BARS = 128;

const props = defineProps({
  // pictureStoryboardUrl(id, { version }).
  src: { type: String, required: true },
  // The video's length in seconds; null until it plays (or when it cannot),
  // and then the tiles show frames but do not seek.
  duration: { type: Number, default: null },
  // The lightbox's <video>, or null while there is none. The strip follows
  // its time itself, so playback re-renders only the strip.
  media: { type: Object, default: null },
  // GET /pictures/waveforms/{id}.json, `{ duration, peaks }`, or null: no
  // lane then (a silent clip, no ffmpeg, not fetched yet).
  waveform: { type: Object, default: null },
  // The clip's length when the browser cannot report one (a refused original,
  // from its copy's status), so the lane still has an axis. It never seeks.
  clipLength: { type: Number, default: null },
});

const emit = defineEmits(["unavailable"]);

const currentTime = ref(0);
function onTimeUpdate(event) {
  currentTime.value = event.target.currentTime;
}
watch(
  () => props.media,
  (media, previous) => {
    previous?.removeEventListener?.("timeupdate", onTimeUpdate);
    media?.addEventListener?.("timeupdate", onTimeUpdate);
    currentTime.value = media?.currentTime || 0;
  },
  { immediate: true },
);
onBeforeUnmount(() =>
  props.media?.removeEventListener?.("timeupdate", onTimeUpdate),
);

const aspect = ref(null);
const failed = ref(false);
const previewIndex = ref(null);

watch(
  () => props.src,
  () => {
    aspect.value = null;
    failed.value = false;
    previewIndex.value = null;
    currentTime.value = 0;
  },
);

function onLoad(event) {
  const { naturalWidth, naturalHeight } = event.target;
  if (naturalWidth && naturalHeight) {
    aspect.value = naturalWidth / FRAMES / naturalHeight;
  }
}

function onError() {
  failed.value = true;
  emit("unavailable");
}

// Set once the sprite has loaded: the tiles and the preview draw from it.
const spriteStyle = computed(() =>
  aspect.value
    ? { "--frame-aspect": aspect.value, "--sprite": `url("${props.src}")` }
    : null,
);

const seekable = computed(() => props.duration > 0);

function timeOf(index) {
  return (props.duration * index) / (FRAMES - 1);
}

const currentIndex = computed(() => {
  if (!seekable.value) return null;
  const at = Math.round((currentTime.value / props.duration) * (FRAMES - 1));
  return Math.min(FRAMES - 1, Math.max(0, at));
});

// 0..1 along the axis that runs from the first tile's centre to the last's.
const playhead = computed(() => {
  if (!seekable.value) return null;
  return Math.min(1, Math.max(0, currentTime.value / props.duration));
});

// The lane's axis is the video's length; the audio may be shorter, and then
// its peaks span only its share of the lane. Until the video reports its
// length the audio's stands in, so the band does not grow a beat late.
const axis = computed(
  () => props.duration || props.clipLength || props.waveform?.duration || 0,
);
const span = computed(() =>
  Math.min(1, (props.waveform?.duration || 0) / axis.value),
);

// Bar heights in the lane's 0..100 viewBox; null when there is nothing to draw.
const bars = computed(() => {
  const peaks = props.waveform?.peaks;
  if (!peaks?.length || !(axis.value > 0) || !(span.value > 0)) return null;
  const per = peaks.length / BARS;
  return Array.from({ length: BARS }, (_, i) => {
    const share = peaks.slice(Math.floor(i * per), Math.ceil((i + 1) * per));
    return Math.max(2, Math.min(1, Math.max(0, ...share)) * 100);
  });
});

// The <video> is the lightbox's; seeking it is what the strip is for.
function seekTo(seconds) {
  const video = props.media;
  if (!seekable.value || !video) return;
  video.currentTime = Math.min(props.duration, Math.max(0, seconds));
}

function onLaneClick(event) {
  const rect = event.currentTarget.getBoundingClientRect();
  if (!rect.width) return;
  seekTo(((event.clientX - rect.left) / rect.width) * props.duration);
}

// A second a press; Home and End jump to the ends. Handled keys stop here, so
// the lightbox's own arrows (step through the set) do not also fire.
const LANE_KEYS = {
  ArrowLeft: (t) => t - 1,
  ArrowDown: (t) => t - 1,
  ArrowRight: (t) => t + 1,
  ArrowUp: (t) => t + 1,
  Home: () => 0,
  End: () => Infinity,
};
function onLaneKeydown(event) {
  const step = LANE_KEYS[event.key];
  if (!step || !seekable.value) return;
  event.preventDefault();
  event.stopPropagation();
  seekTo(step(props.media?.currentTime || 0));
}

function onTileClick(index) {
  seekTo(timeOf(index));
}

function clock(seconds) {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}
</script>

<style scoped>
/* Dark in both themes, as the lightbox is: the selection and hover washes
   take the dark surface's values here. */
.storyboard {
  --active-bar: rgb(var(--v-theme-dark-surface-primary));
  --active-wash: rgba(var(--v-theme-dark-surface-primary), 0.2);
  --hover-wash: rgba(var(--v-theme-on-dark-surface), 0.16);
  --tile-gap: var(--space-1);
  /* The tallest a tile gets. The sprite is 160px tall, sharp to 80 on a 2x
     screen; past that it is a little soft, the price of stills big enough
     to read. */
  --tile-h-max: 96px;
  /* One tile's width, from the strip's: 11 tiles and 10 gaps. */
  --tile-w: calc((100% - 10 * var(--tile-gap)) / 11);
  /* The preview's frame: 192px wide at most, 144px tall at most; the panel
     is never narrower than its caption needs. */
  --preview-frame-w: min(192px, calc(144px * var(--frame-aspect, 1)));
  --preview-w: max(var(--preview-frame-w), 128px);

  /* Every tile takes the frame's own shape: the strip is as wide as eleven
     tiles of the tallest height, or the band's width when that is less, and
     the tiles' height follows from their width. Centred in the band. */
  position: relative;
  flex: 0 1 auto;
  width: min(
    100%,
    calc(
      11 * var(--tile-h-max) * var(--frame-aspect, 1.7778) + 10 *
        var(--tile-gap)
    )
  );
  min-width: 0;
  margin-inline: auto;
}

.storyboard-probe {
  position: absolute;
  width: 0;
  height: 0;
  opacity: 0;
  pointer-events: none;
}

.storyboard-tiles {
  display: flex;
  align-items: flex-start;
  gap: var(--tile-gap);
}

/* 16px under the tiles, inset half a tile at each end. The band grows to
   hold it (LightboxFilmstrip). */
.storyboard-lane {
  height: var(--space-5);
  margin: var(--space-2) calc(var(--tile-w) / 2) 0;
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.storyboard-lane[aria-disabled="true"] {
  cursor: default;
}

.storyboard-wave {
  display: block;
  height: 100%;
}

/* What has played at full strength, the rest at 40%. */
.storyboard-wave rect {
  fill: rgb(var(--v-theme-dark-surface-waveform));
  opacity: 0.4;
}

.storyboard-wave rect.played {
  opacity: 1;
}

.storyboard-tile {
  position: relative;
  flex: 1 1 0;
  min-width: 0;
  aspect-ratio: var(--frame-aspect, 1.7778);
  padding: 0;
  overflow: hidden;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  cursor: pointer;
}

.storyboard-tile[aria-disabled="true"] {
  cursor: default;
}

/* The frame in its own shape, as wide as the tile; the tile has that shape
   too, so at most its 1px border is cut. */
.storyboard-frame {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 100%;
  aspect-ratio: var(--frame-aspect);
  transform: translate(-50%, -50%);
  background-image: var(--sprite);
  background-size: 1100% 100%;
  background-position: calc(var(--tile) * 10%) 0;
  pointer-events: none;
}

.storyboard-tile::after {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  pointer-events: none;
}

.storyboard-tile:hover::after {
  background: var(--hover-wash);
}

/* On the overlay, so the ring is drawn over the frame rather than under it. */
.storyboard-tile--current::after,
.storyboard-tile--current:hover::after {
  background: var(--active-wash);
  box-shadow: inset 0 0 0 var(--focus-width) var(--active-bar);
}

/* One playhead, from the first tile's centre to the last's, so it crosses a
   tile at that tile's moment. */
.storyboard-playhead {
  position: absolute;
  top: calc(-1 * var(--space-1));
  bottom: calc(-1 * var(--space-1));
  left: calc(var(--tile-w) / 2 + var(--at) * (100% - var(--tile-w)));
  width: 2px;
  margin-left: -1px;
  border-radius: 1px;
  background: rgb(var(--v-theme-on-dark-surface));
  pointer-events: none;
}

.storyboard-playhead::before {
  content: "";
  position: absolute;
  top: calc(-1 * var(--space-2));
  left: -3px;
  width: var(--space-3);
  height: var(--space-3);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-on-dark-surface));
}

/* The whole frame, lifted above its tile and kept inside the strip. */
.storyboard-preview {
  --preview-half: calc(var(--preview-w) / 2 + var(--space-2) + 1px);

  position: absolute;
  bottom: calc(100% + var(--space-3));
  left: clamp(
    var(--preview-half),
    calc(var(--tile-w) / 2 + var(--tile) * (var(--tile-w) + var(--tile-gap))),
    calc(100% - var(--preview-half))
  );
  transform: translateX(-50%);
  z-index: var(--z-dropdown);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  width: var(--preview-w);
  padding: var(--space-2);
  background: rgb(var(--v-theme-dark-surface));
  color: rgb(var(--v-theme-on-dark-surface));
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  border-radius: var(--radius-md);
  box-shadow: var(--elevation-3);
  pointer-events: none;
}

.storyboard-preview-frame {
  display: block;
  width: var(--preview-frame-w);
  aspect-ratio: var(--frame-aspect);
  border-radius: var(--radius-sm);
  background-image: var(--sprite);
  background-size: 1100% 100%;
  background-position: calc(var(--tile) * 10%) 0;
}

.storyboard-preview-caption {
  align-self: stretch;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 0 var(--space-2) var(--space-1);
  font-size: var(--text-xs);
  white-space: nowrap;
}

.storyboard-preview-count {
  margin-left: auto;
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}
</style>
