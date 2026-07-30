<script setup>
/**
 * Field-by-field comparison of one duplicate group.
 *
 * Usage:
 *   <DedupCompareDialog
 *     ref="compareRef"
 *     :open="compareOpen"
 *     :group="group"
 *     :cover-id="coverId"
 *     :excluded-ids="excludedIds"
 *     :busy="verdictInFlight"
 *     @close="compareOpen = false"
 *     @set-cover="onSetCover"
 *     @toggle-excluded="onToggleExcluded"
 *     @stack="onStack"
 *     @keep-separate="onKeepSeparate"
 *   />
 *
 * The whole decision is made here: the verdict buttons live in this dialog's
 * footer, so opening Compare is not a detour the user has to back out of before
 * they can act. The component is presentational for the queue state — it reads
 * props and emits intent — but it OWNS the zoom (the design system's
 * full-screen blink compare): which candidate is up, fit vs actual pixels, the
 * pan. The queue's keyboard model drives that state through the exposed API so
 * there is exactly one key owner.
 *
 * Layout, per the owner's calls (2026-07-29): the dialog takes the full grid
 * space (AppDialog `fullscreen`), the images dominate — down-scaled ORIGINALS,
 * not thumbnails, because the whole point of Compare is fine detail — and the
 * metadata sits under each picture as the design system's compact two-column
 * label-over-value grid instead of a tall row list.
 */
import { computed, nextTick, ref, watch } from "vue";
import {
  anchorZoomScroll,
  atFitFloor,
  formatZoomPercent,
  normalizeWheelDelta,
  zoomStepScale,
  ZOOM_EXIT_GESTURE_GAP_MS,
  ZOOM_EXIT_RESISTANCE,
  ZOOM_MAX_SCALE,
} from "../../utils/zoomMath";
import AppDialog from "./AppDialog.vue";
import AppButton from "./AppButton.vue";
import DedupWhyPills from "./DedupWhyPills.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { API_BASE_URL } from "../../utils/apiClient";
import {
  bestOf,
  candidateId,
  candidatePath,
  candidateSizeMb,
  candidateMegapixels,
  candidateSharpness,
  candidateSmartScore,
  confidenceLabel,
  shortenPath,
  showsPath,
} from "../../utils/dedup";
import { formatUserDate } from "../../utils/utils";

const props = defineProps({
  open: { type: Boolean, default: false },
  /**
   * The group under comparison:
   * `{ id, kind, confidence, why: [{label, against}], candidates: [...] }`.
   */
  group: { type: Object, default: null },
  /** The cover currently in force, as chosen by the parent. */
  coverId: { type: [Number, String], default: null },
  /** Candidates the user has left out of the stack. */
  excludedIds: { type: Array, default: () => [] },
  /** True while a verdict is in flight, which locks the verdict buttons. */
  busy: { type: Boolean, default: false },
  /**
   * True in a share or read-only session. Reading the comparison is still
   * useful there, so the dialog stays open to it; the verdict footer goes,
   * because a control that can never work is worse than an absent one.
   */
  readOnly: { type: Boolean, default: false },
});

const emit = defineEmits([
  "close",
  "set-cover",
  "toggle-excluded",
  "stack",
  "keep-separate",
]);

// The en dash placeholder for a value the picture does not carry. A blank cell
// reads as "still loading"; the dash says "there is nothing here".
const EMPTY = "–";

/** Formats the browser can decode in an <img>; anything else (RAW, video)
 * falls back to the thumbnail, which the server already rendered. */
const BROWSER_IMAGE_FORMATS = new Set([
  "jpg",
  "jpeg",
  "png",
  "webp",
  "gif",
  "bmp",
  "avif",
]);

const candidates = computed(() => props.group?.candidates ?? []);

const confidence = computed(() => confidenceLabel(props.group));

/** The per-column maxima that drive the best-value highlight. */
const bestMegapixels = computed(() =>
  bestOf(candidates.value, candidateMegapixels),
);
const bestFileSize = computed(() => bestOf(candidates.value, candidateSizeMb));
const bestScore = computed(() => bestOf(candidates.value, (c) => c.score));
const bestTagCount = computed(() =>
  bestOf(candidates.value, (c) => c.tag_count),
);
const bestSmartScore = computed(() =>
  bestOf(candidates.value, candidateSmartScore),
);

/**
 * Whether ANY copy carries a displayable smart score — the same group-level
 * decision the Location row makes, and for the same reason: the meta grid's
 * height comes off the image, so the row appears on every card or on none,
 * keeping the pictures registered against each other. Against a backend that
 * does not serve `smart_score` yet, no card shows the row.
 */
const anySmartScore = computed(() =>
  candidates.value.some((c) => candidateSmartScore(c) !== null),
);

/**
 * A candidate's smart score for display: the metadata panel's own precision
 * (`toFixed(2)`), the dash for a copy that has none while its siblings do.
 * @param {Object} candidate
 * @returns {string}
 */
function smartScoreText(candidate) {
  const value = candidateSmartScore(candidate);
  return value === null ? EMPTY : value.toFixed(2);
}

const bestSharpness = computed(() =>
  bestOf(candidates.value, candidateSharpness),
);

/** Group-level, exactly like the Smart score column above. */
const anySharpness = computed(() =>
  candidates.value.some((c) => candidateSharpness(c) !== null),
);

/**
 * A candidate's sharpness for display. Three decimals, not two: the server
 * serialises the metric at 3dp on a typical 0–0.5 range, where two decimals
 * would flatten genuinely different copies into the same number.
 * @param {Object} candidate
 * @returns {string}
 */
function sharpnessText(candidate) {
  const value = candidateSharpness(candidate);
  return value === null ? EMPTY : value.toFixed(3);
}

/**
 * Whether ANY copy in this group carries a path worth showing.
 *
 * The Location row is a whole extra line in the meta grid, and the meta grid is
 * what the image area gives its leftover height to. With the row rendered per
 * candidate, one reference-folder copy next to a library-managed one produced
 * two cards whose pictures started and ended at different heights — the
 * comparison's whole premise is that the images are registered against each
 * other, so the row is a group-level decision: every card gets it, or none do.
 */
const anyPathShown = computed(() => candidates.value.some(showsPath));

/** How many pictures the Stack verdict would collapse. */
const stackCount = computed(
  () => candidates.value.length - props.excludedIds.length,
);

function isCover(candidate) {
  return props.coverId != null && candidateId(candidate) === props.coverId;
}

function isExcluded(candidate) {
  return props.excludedIds.includes(candidateId(candidate));
}

/**
 * The preview source: the down-scaled ORIGINAL for browser-decodable formats
 * (Compare exists to judge fine detail, which a grid-scale thumbnail cannot
 * carry), the server-rendered thumbnail for RAW and video.
 */
function previewUrl(candidate) {
  const format = String(candidate?.format || "").toLowerCase();
  if (BROWSER_IMAGE_FORMATS.has(format)) {
    return `${API_BASE_URL}/pictures/${candidateId(candidate)}.${format}`;
  }
  return pictureThumbnailUrl(candidateId(candidate), {
    version: candidate.thumbnail_version,
    baseUrl: API_BASE_URL,
  });
}

/**
 * Whether a value ties the column maximum, so it renders as the best one.
 *
 * A column whose maximum is 0 has nothing to win, so nothing is highlighted.
 */
function isBest(value, best) {
  const numeric = Number(value);
  return best > 0 && Number.isFinite(numeric) && numeric === best;
}

function resolutionText(candidate) {
  const width = Number(candidate?.width);
  const height = Number(candidate?.height);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return EMPTY;
  return `${width} x ${height}`;
}

function fileText(candidate) {
  const size = candidateSizeMb(candidate);
  const format = candidate?.format || EMPTY;
  if (!Number.isFinite(size)) return format;
  return `${size.toFixed(1)} MB, ${format}`;
}

function capturedText(candidate) {
  return candidate?.created_at
    ? formatUserDate(candidate.created_at, "iso")
    : EMPTY;
}

function starCount(candidate) {
  const score = Math.round(Number(candidate?.score));
  return Number.isFinite(score) && score > 0 ? score : 0;
}

function tagText(candidate) {
  const count = Number(candidate?.tag_count) || 0;
  return count > 0 ? `${count} tags` : "none";
}

// ── Zoom: the design system's full-screen blink compare ────────────────────
// One candidate fills the screen; flipping in place (arrows, 1-9) makes the
// differences jump out as motion. The magnification is a CONTINUOUS scale
// (owner requirement): the wheel means zoom for the whole gesture — wheel up
// over a candidate opens the zoom and continued wheeling keeps magnifying,
// anchored at the cursor (binding: the image point under the pointer stays
// stationary through every scale change) — and wheeling out three full
// notches of deliberate resistance past the fit floor leaves the zoom back
// to Compare. Fit and 100% (actual
// pixels) are SNAP STOPS on the continuum (the header buttons and the P key),
// drag pans at every overflowing level, and a flip keeps the scale and pan so
// the blink stays registered.

const zoomIndex = ref(null);
const zoomImgEl = ref(null);
const zoomScrollEl = ref(null);
let panState = null;

/** The current scale, 1 = actual pixels; null until the image has measured
 * (the un-measured state renders the classic fit look via CSS). */
const zoomScale = ref(null);
/** The floor of the continuum: the scale at which this image exactly fits. */
const zoomFitScale = ref(1);
/** The displayed image's natural pixel size, measured on load. */
const zoomNatural = ref(null);
/** Outward wheel delta accumulated while AT the fit floor; three full
 * notches of deliberate resistance close the zoom (the hysteresis — see
 * ZOOM_EXIT_RESISTANCE in utils/zoomMath.js). A pause longer than the
 * gesture gap starts the count over. */
let zoomCloseAccumulator = 0;
let zoomCloseLastOutTs = 0;

const zoomOpen = computed(() => props.open && zoomIndex.value != null);
const zoomCandidate = computed(() =>
  zoomOpen.value ? candidates.value[zoomIndex.value] : null,
);

const zoomMetaText = computed(() => {
  const c = zoomCandidate.value;
  if (!c) return "";
  const parts = [`#${candidateId(c)}`, resolutionText(c), fileText(c)];
  const stars = starCount(c);
  if (stars) parts.push("★".repeat(stars));
  const smart = candidateSmartScore(c);
  if (smart !== null) parts.push(`Smart score ${smart.toFixed(2)}`);
  return parts.filter((p) => p !== EMPTY).join(" · ");
});

/** The cover's index, where Z lands when no candidate was named. */
function coverIndex() {
  const index = candidates.value.findIndex((c) => isCover(c));
  return index >= 0 ? index : 0;
}

function openZoom(index = null) {
  if (!candidates.value.length) return;
  const target = index == null ? coverIndex() : index;
  zoomIndex.value = Math.max(0, Math.min(candidates.value.length - 1, target));
}

function closeZoom() {
  zoomIndex.value = null;
  zoomScale.value = null;
  zoomNatural.value = null;
  zoomCloseAccumulator = 0;
  zoomCloseLastOutTs = 0;
  panState = null;
}

/** Flip forward/back, wrapping — a blink loop, not a bounded carousel. The
 * scale and pan are deliberately NOT reset: flipping at identical
 * magnification is what makes differences read as motion (the new image's
 * own fit floor re-clamps on load). */
function flipZoom(delta) {
  if (!zoomOpen.value) return;
  const n = candidates.value.length;
  zoomIndex.value = (zoomIndex.value + delta + n) % n;
}

function zoomTo(index) {
  if (!zoomOpen.value) return;
  if (index >= 0 && index < candidates.value.length) zoomIndex.value = index;
}

/** Whether the current scale sits on a snap stop (1% slack). */
function nearScale(target) {
  const scale = zoomScale.value;
  return scale !== null && Math.abs(scale - target) <= target * 0.01;
}

const zoomAtFit = computed(() => nearScale(zoomFitScale.value));
const zoomAtActual = computed(() => nearScale(1));

/** The readout: percentage of ACTUAL pixels, the photo-tool convention.
 * Visibility of status — it is also what makes the blink guarantee (same
 * magnification across flips) verifiable by eye. */
const zoomPercent = computed(() =>
  zoomScale.value === null ? null : formatZoomPercent(zoomScale.value),
);

/**
 * Snap to a stop on the continuum, anchored at the viewport centre (a
 * keypress or button has no cursor to anchor on; centre is the convention).
 * @param {number} target
 */
function snapZoomTo(target) {
  if (!zoomOpen.value || zoomScale.value === null) return;
  const el = zoomScrollEl.value;
  applyZoomScale(
    Math.max(zoomFitScale.value, Math.min(ZOOM_MAX_SCALE, target)),
    el ? { x: el.clientWidth / 2, y: el.clientHeight / 2 } : { x: 0, y: 0 },
  );
}

/** P: flip between the two snap stops — fit and 100% — exactly the two
 * states the old toggle had, now as points on the continuum. */
function toggleZoomPixels() {
  if (!zoomOpen.value) return;
  snapZoomTo(zoomAtActual.value ? zoomFitScale.value : 1);
}

/** Measure the displayed image once it loads: its natural size fixes the fit
 * floor, and the first measurement lands the scale ON that floor (the zoom
 * opens at fit; a flip instead re-clamps the kept scale to the new floor). */
function onZoomImgLoad() {
  const img = zoomImgEl.value;
  const el = zoomScrollEl.value;
  const c = zoomCandidate.value;
  const naturalW = img?.naturalWidth || Number(c?.width) || 0;
  const naturalH = img?.naturalHeight || Number(c?.height) || 0;
  if (!naturalW || !naturalH) return;
  zoomNatural.value = { w: naturalW, h: naturalH };
  const cw = el?.clientWidth || 0;
  const ch = el?.clientHeight || 0;
  // No measurable viewport (or a zero-layout test environment): the floor
  // falls back to actual pixels so the continuum still behaves.
  zoomFitScale.value = cw && ch ? Math.min(cw / naturalW, ch / naturalH) : 1;
  if (zoomScale.value === null) {
    zoomScale.value = zoomFitScale.value;
  } else {
    zoomScale.value = Math.max(
      zoomFitScale.value,
      Math.min(ZOOM_MAX_SCALE, zoomScale.value),
    );
  }
}

/** The explicit pixel size the scale implies; empty until measured, which
 * leaves the classic CSS fit rendering in charge. */
const zoomImgStyle = computed(() => {
  if (zoomScale.value === null || !zoomNatural.value) return undefined;
  return {
    width: `${zoomNatural.value.w * zoomScale.value}px`,
    height: `${zoomNatural.value.h * zoomScale.value}px`,
    maxWidth: "none",
    maxHeight: "none",
  };
});

/** Whether the scaled image overflows the viewport (pan has meaning). */
const zoomOverflowing = computed(() => {
  const el = zoomScrollEl.value;
  if (!el || zoomScale.value === null || !zoomNatural.value) return false;
  return (
    zoomNatural.value.w * zoomScale.value > el.clientWidth ||
    zoomNatural.value.h * zoomScale.value > el.clientHeight
  );
});

/**
 * Apply a new scale with the CURSOR ANCHOR (binding): the image point under
 * the pointer is computed before the change, the scale applied, and the
 * scroll re-solved so that point is back under the pointer — clamped at the
 * edges. The scroll is written after the DOM has adopted the new image size.
 *
 * @param {number} next
 * @param {{x: number, y: number}} cursor - relative to the scroll container.
 */
function applyZoomScale(next, cursor) {
  const el = zoomScrollEl.value;
  const natural = zoomNatural.value;
  const oldScale = zoomScale.value;
  zoomScale.value = next;
  if (!el || !natural || oldScale === null) return;
  const target = anchorZoomScroll({
    cursorX: cursor.x,
    cursorY: cursor.y,
    scrollLeft: el.scrollLeft,
    scrollTop: el.scrollTop,
    containerWidth: el.clientWidth,
    containerHeight: el.clientHeight,
    imageWidth: natural.w,
    imageHeight: natural.h,
    oldScale,
    newScale: next,
  });
  nextTick(() => {
    el.scrollLeft = target.left;
    el.scrollTop = target.top;
  });
}

/**
 * Escape peels ONE layer: with the zoom up, a close request (AppDialog's own
 * Escape handling on its subtree, Vuetify's ESC/scrim, the header X) closes
 * the ZOOM and keeps the dialog; the next one closes the dialog. The queue's
 * keyboard model already layers Escape this way when the event reaches it,
 * but a keydown with DOM focus inside the dialog subtree is claimed by
 * AppDialog first (it stopPropagation-s), so the dialog's close intent has to
 * respect the layer here too or that path closes both at once.
 */
function requestClose() {
  if (zoomOpen.value) {
    closeZoom();
    return;
  }
  emit("close");
}

// ── Wheel: the mouse's way into (and through) the zoom ─────────────────────
// Scrolling over a candidate's picture opens the blink compare on it, like
// the zoom button. Inside the zoom, the wheel flips candidates in Fit — the
// same in-place flip the arrow keys do, so differences read as motion. In
// Actual pixels the surface is a scroll-to-pan container, so the wheel stays
// native there and the flip gesture deliberately does not exist.

/**
 * Wheel over a candidate's picture: wheel UP (the zoom-in direction) opens
 * the zoom, and the SAME gesture's next ticks keep zooming in over the zoom
 * surface — one continuous motion, no meaning-switch. The opening event only
 * opens (at fit), so the first tick after it steps once and cannot
 * overshoot; a wheel DOWN over a thumbnail is already fully zoomed out and
 * deliberately does nothing (and is not hijacked).
 *
 * First-tick anchoring: the jump from a strip thumbnail to the full-screen
 * surface has no meaningful cursor geometry to preserve, so the zoom opens
 * at fit and anchors perfectly from the first in-tick on the zoom surface.
 *
 * @param {number} index
 * @param {WheelEvent} event
 */
function onThumbWheel(index, event) {
  if (event.deltaY >= 0) return;
  event.preventDefault();
  event.stopPropagation();
  openZoom(index);
}

/**
 * The wheel inside the zoom means ZOOM, continuously: up magnifies, down
 * shrinks toward the fit floor, and three further full notches AT the floor
 * (deliberate resistance) close back to Compare (the accumulator is the
 * hysteresis — see ZOOM_EXIT_RESISTANCE in utils/zoomMath.js). Always
 * preventDefault: the wheel must never scroll the page or the dialog behind
 * the zoom.
 *
 * @param {WheelEvent} event
 */
function onZoomWheel(event) {
  event.preventDefault();
  if (zoomScale.value === null) return;
  // Normalize line/page-mode wheels to pixels (shared zoom-family rule):
  // a raw line-mode deltaY of ±3 fed into pixel-tuned arithmetic zoomed
  // ~50× weaker per notch, and took ~40 notches at the floor to leave.
  const deltaY = normalizeWheelDelta(event, {
    pageHeightPx: zoomScrollEl.value?.clientHeight || undefined,
  });
  if (!deltaY) return;
  if (deltaY > 0 && atFitFloor(zoomScale.value, zoomFitScale.value)) {
    // The exit accumulates only AT the floor; a pause longer than the gesture
    // gap starts the count over, so a later gesture meets the full
    // resistance itself instead of inheriting stale part-way accumulation.
    const now = Date.now();
    if (now - zoomCloseLastOutTs > ZOOM_EXIT_GESTURE_GAP_MS) {
      zoomCloseAccumulator = 0;
    }
    zoomCloseLastOutTs = now;
    zoomCloseAccumulator += deltaY;
    if (zoomCloseAccumulator >= ZOOM_EXIT_RESISTANCE) closeZoom();
    return;
  }
  // Any zoom-in movement resets the exit accumulation.
  zoomCloseAccumulator = 0;
  const next = zoomStepScale(zoomScale.value, deltaY, zoomFitScale.value);
  if (next === zoomScale.value) return;
  const rect = zoomScrollEl.value?.getBoundingClientRect?.();
  applyZoomScale(next, {
    x: rect ? event.clientX - rect.left : 0,
    y: rect ? event.clientY - rect.top : 0,
  });
}

// The queue's keyboard model is the single key owner; it drives the zoom
// through this surface instead of the dialog competing for the keydown.
// `zoomLevel` is the imperative read the tests (and any future readout)
// use; the visible percentage renders from the same scale.
defineExpose({
  isZoomOpen: () => zoomOpen.value,
  openZoom,
  closeZoom,
  flipZoom,
  zoomTo,
  toggleZoomPixels,
  zoomLevel: () => zoomScale.value,
});

// A new group, or a closed dialog, always starts un-zoomed at Fit: a zoom held
// over from another group would open on the wrong picture.
watch(
  () => [props.open, props.group?.signature],
  () => {
    closeZoom();
  },
);

// ── Drag-to-pan, at every overflowing zoom level ───────────────────────────
// The wheel never pans (it zooms); the drag is the pan gesture wherever the
// scaled image overflows. At fit there is nothing to pan and the drag no-ops.

function onZoomPointerDown(event) {
  if (!zoomOpen.value || event.button !== 0) return;
  const el = zoomScrollEl.value;
  if (!el) return;
  panState = {
    x: event.clientX,
    y: event.clientY,
    left: el.scrollLeft,
    top: el.scrollTop,
    moved: false,
  };
}

function onZoomPointerMove(event) {
  if (!panState) return;
  const el = zoomScrollEl.value;
  if (!el) return;
  const dx = event.clientX - panState.x;
  const dy = event.clientY - panState.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) panState.moved = true;
  el.scrollLeft = panState.left - dx;
  el.scrollTop = panState.top - dy;
}

function onZoomPointerUp() {
  // Cleared on the next tick so the click handler can still tell a drag from
  // a pick.
  setTimeout(() => {
    panState = null;
  }, 0);
}

/** Click picks the cover — unless the click was the tail end of a pan. */
function onZoomClick() {
  if (panState?.moved) return;
  const c = zoomCandidate.value;
  if (c) emit("set-cover", candidateId(c));
}

function onZoomContextMenu() {
  const c = zoomCandidate.value;
  if (c) emit("toggle-excluded", candidateId(c));
}
</script>

<template>
  <AppDialog :open="open" title="Compare group" fullscreen @close="requestClose">
    <!-- The confidence claim rides in the header, next to the close button, so
         it stays visible while the strip below scrolls. -->
    <template #header-right>
      <span class="dc-confidence">{{ confidence.label }}</span>
    </template>

    <!-- ── The candidate strip ─────────────────────────────────────────────
         One card per copy. The cards GROW into the freed width — the images
         are the point of this surface — and scroll sideways only once there
         are too many to fit; the comparison only works when the fields line
         up across cards. -->
    <div class="dc-strip">
      <div
        v-for="(candidate, index) in candidates"
        :key="candidateId(candidate)"
        class="dc-card"
        :class="{ 'dc-card--out': isExcluded(candidate) }"
        @contextmenu.prevent="emit('toggle-excluded', candidateId(candidate))"
      >
        <button
          type="button"
          class="dc-pick"
          :aria-pressed="isCover(candidate)"
          :title="'Make this the cover'"
          @click="emit('set-cover', candidateId(candidate))"
        >
          <!-- Wheel over the picture starts the zoom on it: the mouse's
               equivalent of the corner button, without the pixel hunt. -->
          <span class="dc-thumb" @wheel="onThumbWheel(index, $event)">
            <img
              class="dc-thumb-img"
              :src="previewUrl(candidate)"
              :alt="`Copy ${index + 1}`"
              loading="lazy"
              decoding="async"
              draggable="false"
              @dragstart.prevent
            />
            <span class="dc-index">{{ index + 1 }}</span>
            <span v-if="isCover(candidate)" class="dc-flag dc-flag--cover">
              Cover
            </span>
            <span v-if="isExcluded(candidate)" class="dc-flag dc-flag--out">
              Not in stack
            </span>
          </span>
        </button>
        <!-- The zoom trigger is a sibling of the pick button (a button inside
             a button is invalid markup), absolutely placed over the corner. -->
        <button
          type="button"
          class="dc-zoom"
          title="Zoom — full-screen blink compare (Z)"
          :aria-label="`Zoom copy ${index + 1}`"
          @click.stop="openZoom(index)"
        >
          <v-icon size="16">mdi-magnify-plus-outline</v-icon>
        </button>

        <!-- The design system's compact meta: two columns, label over value,
             so the numbers read first and the metadata never squeezes the
             image. -->
        <span class="dc-meta">
          <span class="dc-cell">
            <span class="dc-label">ID</span>
            <span class="dc-val">#{{ candidateId(candidate) }}</span>
          </span>
          <span class="dc-cell">
            <span class="dc-label">Resolution</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(
                  candidateMegapixels(candidate),
                  bestMegapixels,
                ),
              }"
              >{{ resolutionText(candidate) }}</span
            >
          </span>
          <span class="dc-cell">
            <span class="dc-label">File</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(candidateSizeMb(candidate), bestFileSize),
              }"
              >{{ fileText(candidate) }}</span
            >
          </span>
          <span class="dc-cell">
            <span class="dc-label">Captured</span>
            <span class="dc-val">{{ capturedText(candidate) }}</span>
          </span>
          <span class="dc-cell">
            <span class="dc-label">Score</span>
            <span
              class="dc-val"
              :class="{ 'dc-val--best': isBest(candidate.score, bestScore) }"
            >
              <template v-if="starCount(candidate)">
                <v-icon
                  v-for="star in starCount(candidate)"
                  :key="star"
                  size="13"
                  class="dc-star"
                  >mdi-star</v-icon
                >
              </template>
              <template v-else>{{ EMPTY }}</template>
            </span>
          </span>
          <!-- Group-level like the Location row: rendered on every card as
               soon as ONE copy has a smart score, so the cards keep the same
               shape and the pictures stay aligned. -->
          <span v-if="anySmartScore" class="dc-cell">
            <span class="dc-label">Smart score</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(
                  candidateSmartScore(candidate),
                  bestSmartScore,
                ),
              }"
              >{{ smartScoreText(candidate) }}</span
            >
          </span>
          <span v-if="anySharpness" class="dc-cell">
            <span class="dc-label">Sharpness</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(
                  candidateSharpness(candidate),
                  bestSharpness,
                ),
              }"
              >{{ sharpnessText(candidate) }}</span
            >
          </span>
          <span class="dc-cell">
            <span class="dc-label">Metadata</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(candidate.tag_count, bestTagCount),
              }"
              >{{ tagText(candidate) }}</span
            >
          </span>
          <span class="dc-cell">
            <span class="dc-label">In stack</span>
            <span class="dc-val dc-instack">
              <button
                type="button"
                class="dc-toggle"
                :aria-pressed="!isExcluded(candidate)"
                :title="
                  isExcluded(candidate)
                    ? 'Put this copy back in the stack'
                    : 'Leave this copy out of the stack'
                "
                @click.stop="emit('toggle-excluded', candidateId(candidate))"
              >
                <v-icon size="14">{{
                  isExcluded(candidate)
                    ? "mdi-checkbox-blank-outline"
                    : "mdi-checkbox-marked-outline"
                }}</v-icon>
              </button>
              <span>{{ isExcluded(candidate) ? "No" : "Yes" }}</span>
            </span>
          </span>
          <!-- The path is shown only for a reference-folder picture, where the
               user manages the files and needs to know which copy is which.
               Full width: paths do not fit half a column. The row appears on
               every card as soon as ONE copy has a path, so the cards stay the
               same shape and the pictures stay aligned. -->
          <span v-if="anyPathShown" class="dc-cell dc-cell--wide">
            <span class="dc-label">Location</span>
            <span
              v-if="showsPath(candidate)"
              class="dc-val dc-path"
              :title="candidatePath(candidate)"
            >
              <v-icon
                size="13"
                class="dc-path-icon"
                title="Reference folder, you manage these files yourself"
                >mdi-folder-eye-outline</v-icon
              >
              {{ shortenPath(candidatePath(candidate)) }}
            </span>
            <span
              v-else
              class="dc-val dc-path"
              title="Stored in your PixlStash library, not in a reference folder"
            >
              <v-icon size="13" class="dc-path-icon"
                >mdi-image-multiple-outline</v-icon
              >
              In your library
            </span>
          </span>
        </span>
      </div>
    </div>

    <!-- ── Why these were grouped ──────────────────────────────────────────
         The same pill component the queue row uses, so the evidence a user
         glanced at in the row is literally the same treatment they study here.
         Compare shows all of it, where the row shows only the first two. -->
    <DedupWhyPills class="dc-why" :why="group?.why" />

    <template #footer>
      <span v-if="!readOnly" class="dc-hint">
        Click a picture, or press its number, to make it the cover. Right-click,
        or press X, to leave it out. Z zooms. No file is ever deleted.
      </span>
      <AppButton variant="ghost" key-hint="esc" @click="requestClose"
        >Close</AppButton
      >
      <AppButton
        v-if="!readOnly"
        variant="secondary"
        icon-left="call-split"
        key-hint="k"
        :disabled="busy"
        title="Leave these as separate pictures. They stay in your library and stop being suggested."
        @click="emit('keep-separate')"
      >
        Keep separate
      </AppButton>
      <AppButton
        v-if="!readOnly"
        variant="primary"
        icon-left="layers-plus"
        key-hint="enter"
        :disabled="busy"
        title="Group these behind one cover. Every file stays on disk, and Ctrl+Z reverses it."
        @click="emit('stack')"
      >
        Stack {{ stackCount }}
      </AppButton>
    </template>
  </AppDialog>

  <!-- ── The blink compare ──────────────────────────────────────────────────
       Full screen, above the dialog: one candidate at a time, flipped in
       place so differences show as motion. Deliberately near-black chrome at
       fixed colors — this is a photo-judgement surface, same rationale as the
       lightbox. -->
  <Teleport to="body">
    <div v-if="zoomOpen" class="dc-zv" data-testid="dedup-zoom">
      <div class="dc-zv-top">
        <div class="dc-zv-flip" role="tablist" aria-label="Candidate">
          <button
            v-for="(candidate, index) in candidates"
            :key="candidateId(candidate)"
            type="button"
            :class="{ 'dc-zv-on': index === zoomIndex }"
            @click="zoomTo(index)"
          >
            {{ index + 1 }}
          </button>
        </div>
        <span
          v-if="zoomCandidate && isCover(zoomCandidate)"
          class="dc-flag dc-flag--zv"
          >Cover</span
        >
        <span
          v-if="zoomCandidate && isExcluded(zoomCandidate)"
          class="dc-flag dc-flag--zv"
          >Not in stack</span
        >
        <span class="dc-zv-meta">{{ zoomMetaText }}</span>
        <!-- Fit and 100% are SNAP STOPS on the wheel's continuum; the
             percentage readout is what makes "same magnification across the
             blink" verifiable by eye. -->
        <div class="dc-zv-mode">
          <button
            type="button"
            :class="{ 'dc-zv-on': zoomAtFit }"
            title="Snap to fit — every candidate in the same box, the blink registered"
            @click="snapZoomTo(zoomFitScale)"
          >
            <v-icon size="15">mdi-fit-to-screen-outline</v-icon>
            Fit
          </button>
          <button
            type="button"
            :class="{ 'dc-zv-on': zoomAtActual }"
            title="Snap to 1:1 — resolution differences show as size jumps (P)"
            @click="snapZoomTo(1)"
          >
            <v-icon size="15">mdi-magnify-scan</v-icon>
            Actual pixels
          </button>
          <span v-if="zoomPercent !== null" class="dc-zv-pct">{{
            zoomPercent
          }}</span>
        </div>
        <button
          type="button"
          class="dc-zv-close"
          title="Back to compare (Esc)"
          @click="closeZoom()"
        >
          <v-icon size="18">mdi-close</v-icon>
        </button>
      </div>
      <div
        ref="zoomScrollEl"
        class="dc-zv-img"
        :class="{ 'dc-zv-img--pannable': zoomOverflowing }"
        @wheel="onZoomWheel"
        @mousedown="onZoomPointerDown"
        @mousemove="onZoomPointerMove"
        @mouseup="onZoomPointerUp"
        @mouseleave="onZoomPointerUp"
        @click="onZoomClick"
        @contextmenu.prevent="onZoomContextMenu"
      >
        <!-- draggable=false is load-bearing: the browser's native image drag
             starts on the same gesture as the pan and wins the race, leaving
             the pan dead and a ghost image under the cursor. -->
        <img
          v-if="zoomCandidate"
          ref="zoomImgEl"
          :src="previewUrl(zoomCandidate)"
          :style="zoomImgStyle"
          alt=""
          draggable="false"
          @load="onZoomImgLoad"
          @dragstart.prevent
        />
      </div>
      <div class="dc-zv-foot" aria-hidden="true">
        <span><kbd>←</kbd><kbd>→</kbd> or <kbd>1</kbd>–<kbd>9</kbd> flip in place — differences jump out as motion</span>
        <span>Scroll zooms, drag pans — zoom out past Fit to leave</span>
        <span><kbd>P</kbd> Fit ↔ 100%</span>
        <span><kbd>Enter</kbd> stack</span>
        <span><kbd>K</kbd> keep separate</span>
        <span><kbd>Esc</kbd> back</span>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.dc-confidence {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

/* ── The strip ──────────────────────────────────────────────────────────────
   The cards grow to spend the fullscreen dialog's width on the images; a
   sideways scroll appears only when even the minimum card width overflows. */
.dc-strip {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: stretch;
  gap: var(--space-4);
  overflow-x: auto;
  scrollbar-gutter: stable;
  padding-bottom: var(--space-3);
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-surface), 0.4) transparent;
}

.dc-card {
  position: relative;
  flex: 1 0 300px;
  max-width: 640px;
  display: flex;
  flex-direction: column;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  box-shadow: var(--elevation-1);
  overflow: hidden;
}

/* An excluded copy stays fully readable, because it is still part of the
   comparison; it is just not going into the stack. */
.dc-card--out {
  border-style: dashed;
  border-color: rgba(var(--v-theme-on-surface), 0.35);
}

.dc-pick {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  width: 100%;
  text-align: left;
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.dc-pick:hover {
  background: var(--hover-wash);
}

.dc-pick[aria-pressed="true"] {
  background: var(--active-wash);
}

.dc-pick:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* The image takes every pixel the metadata does not strictly need. */
.dc-thumb {
  position: relative;
  display: block;
  flex: 1;
  min-height: 220px;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.dc-thumb-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

/* Count pill, per the badge rules: primary fill, never the amber accent. */
.dc-index {
  position: absolute;
  top: var(--space-2);
  left: var(--space-2);
  min-width: var(--badge-size);
  height: var(--badge-size);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

/* The flags sit on top of an arbitrary photo, so they take the photo scrim. */
.dc-flag {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.dc-flag--out {
  top: auto;
  bottom: var(--space-2);
}

/* The zoom trigger sits under the index pill, over the photo. */
.dc-zoom {
  position: absolute;
  top: calc(var(--space-2) + var(--badge-size) + var(--space-2));
  left: var(--space-2);
  z-index: var(--z-raised);
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: pointer;
}

.dc-zoom:hover {
  color: rgb(var(--v-theme-accent));
}

.dc-zoom:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* ── The compact meta grid: two columns, label over value ─────────────────── */
.dc-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2) var(--space-3);
  padding: var(--space-3) var(--space-4);
  align-content: start;
}

.dc-cell {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.dc-cell--wide {
  grid-column: 1 / -1;
}

/* The label is deliberately quiet: the values are what the user compares. */
.dc-label {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.dc-val {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* The best-value chip below draws a border. Reserving its vertical space on
     every value keeps the meta rows the same height from card to card, and the
     meta block is what the picture above it gives its leftover height to: a
     card that wins three columns must not end up with a shorter picture. */
  border-block: 1px solid transparent;
}

/* The winner of a column reads first: a quiet primary-tinted chip. */
.dc-val--best {
  font-weight: var(--weight-semibold);
  background: rgba(var(--v-theme-primary), 0.18);
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  border-radius: var(--radius-sm);
  padding: 0 var(--space-2);
  align-self: flex-start;
  max-width: 100%;
}

.dc-star {
  color: rgb(var(--v-theme-accent));
}

.dc-path {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.dc-path-icon {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.dc-instack {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.dc-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.7);
  cursor: pointer;
  transition:
    background var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard);
}

.dc-toggle:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.dc-toggle:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

/* ── Why pills ───────────────────────────────────────────────────────────── */
.dc-why {
  margin-top: var(--space-4);
  flex-shrink: 0;
}

.dc-hint {
  margin-right: auto;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* ── The blink compare ─────────────────────────────────────────────────────
   Fixed colors on purpose: like the lightbox, a photo-judgement surface gets
   near-black chrome in both themes. Sits above the modal dialog. */
.dc-zv {
  position: fixed;
  inset: 0;
  z-index: calc(var(--z-modal) + 100);
  display: flex;
  flex-direction: column;
  background: #0a0a0a;
}

.dc-zv-top {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  flex-shrink: 0;
}

.dc-zv-flip {
  display: flex;
  gap: var(--space-1);
}

.dc-zv-flip button {
  min-width: 30px;
  height: 30px;
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: var(--radius-sm);
  background: transparent;
  color: #fff;
  font-family: var(--font-ui);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.dc-zv-flip button:hover {
  background: rgba(255, 255, 255, 0.12);
}

.dc-zv .dc-zv-on {
  background: rgb(var(--v-theme-accent));
  border-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  font-weight: var(--weight-semibold);
}

.dc-flag--zv {
  position: static;
}

.dc-zv-meta {
  flex: 1;
  font-size: var(--text-xs);
  color: rgba(255, 255, 255, 0.72);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dc-zv-mode {
  display: flex;
  gap: var(--space-1);
  flex-shrink: 0;
}

.dc-zv-mode button {
  height: 30px;
  padding: 0 var(--space-3);
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: var(--radius-sm);
  background: transparent;
  color: #fff;
  font-family: var(--font-ui);
  font-size: var(--text-xs);
  cursor: pointer;
}

.dc-zv-mode button:hover {
  background: rgba(255, 255, 255, 0.12);
}

/* The live magnification, in the photo-tool convention (100% = 1:1). Same
   fixed chrome values as the meta line: this is the near-black judgement
   surface, deliberately un-themed. */
.dc-zv-pct {
  align-self: center;
  min-width: 5ch;
  text-align: right;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(255, 255, 255, 0.72);
}

.dc-zv-close {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgba(255, 255, 255, 0.72);
  cursor: pointer;
}

.dc-zv-close:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

/* One continuous zoom surface. `overflow: hidden` is deliberate: the wheel
   ZOOMS (never scrolls) and the drag is the only pan, so no scrollbar may
   appear and no native wheel-scroll may compete; the scroll offsets are
   driven programmatically by the cursor-anchor math. Flex + auto margins on
   the image: centred while it fits, full programmatic scroll range once it
   overflows (auto margins collapse to zero under negative free space). */
.dc-zv-img {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.dc-zv-img--pannable {
  cursor: grab;
}

/* Until the image has measured (its `load` fixes the fit floor), the classic
   fit rendering holds; the measured inline width/height then take over. */
.dc-zv-img img {
  display: block;
  margin: auto;
  flex-shrink: 0;
  width: auto;
  height: 100%;
  max-width: 100%;
  object-fit: contain;
}

.dc-zv-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-5);
  padding: var(--space-3);
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(255, 255, 255, 0.6);
}

.dc-zv-foot kbd {
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}
</style>
