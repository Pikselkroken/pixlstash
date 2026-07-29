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
import { computed, ref, watch } from "vue";
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
// differences jump out as motion. Fit keeps every candidate registered in the
// same box; Actual pixels is 1:1 with drag-to-pan, so resolution differences
// show as size jumps.

const zoomIndex = ref(null);
const zoomActualPixels = ref(false);
const zoomImgEl = ref(null);
const zoomScrollEl = ref(null);
let panState = null;

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
  panState = null;
}

/** Flip forward/back, wrapping — a blink loop, not a bounded carousel. */
function flipZoom(delta) {
  if (!zoomOpen.value) return;
  const n = candidates.value.length;
  zoomIndex.value = (zoomIndex.value + delta + n) % n;
}

function zoomTo(index) {
  if (!zoomOpen.value) return;
  if (index >= 0 && index < candidates.value.length) zoomIndex.value = index;
}

function toggleZoomPixels() {
  if (!zoomOpen.value) return;
  zoomActualPixels.value = !zoomActualPixels.value;
}

// The queue's keyboard model is the single key owner; it drives the zoom
// through this surface instead of the dialog competing for the keydown.
defineExpose({
  isZoomOpen: () => zoomOpen.value,
  openZoom,
  closeZoom,
  flipZoom,
  zoomTo,
  toggleZoomPixels,
});

// A new group, or a closed dialog, always starts un-zoomed at Fit: a zoom held
// over from another group would open on the wrong picture.
watch(
  () => [props.open, props.group?.signature],
  () => {
    closeZoom();
    zoomActualPixels.value = false;
  },
);

// ── Actual-pixels panning ───────────────────────────────────────────────────

function onZoomPointerDown(event) {
  if (!zoomActualPixels.value || event.button !== 0) return;
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
  <AppDialog :open="open" title="Compare group" fullscreen @close="emit('close')">
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
          <span class="dc-thumb">
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
      <AppButton variant="ghost" key-hint="esc" @click="emit('close')"
        >Close</AppButton
      >
      <AppButton
        v-if="!readOnly"
        variant="secondary"
        icon-left="call-split"
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
        <div class="dc-zv-mode">
          <button
            type="button"
            :class="{ 'dc-zv-on': !zoomActualPixels }"
            title="Scale every candidate into the same box — keeps the blink registered"
            @click="zoomActualPixels = false"
          >
            <v-icon size="15">mdi-fit-to-screen-outline</v-icon>
            Fit
          </button>
          <button
            type="button"
            :class="{ 'dc-zv-on': zoomActualPixels }"
            title="1:1 — resolution differences show as size jumps (P)"
            @click="zoomActualPixels = true"
          >
            <v-icon size="15">mdi-magnify-scan</v-icon>
            Actual pixels
          </button>
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
        :class="{ 'dc-zv-img--px': zoomActualPixels }"
        @mousedown="onZoomPointerDown"
        @mousemove="onZoomPointerMove"
        @mouseup="onZoomPointerUp"
        @mouseleave="onZoomPointerUp"
        @click="onZoomClick"
        @contextmenu.prevent="onZoomContextMenu"
      >
        <!-- draggable=false is load-bearing: the browser's native image drag
             starts on the same gesture as the actual-pixels pan and wins the
             race, leaving the pan dead and a ghost image under the cursor. -->
        <img
          v-if="zoomCandidate"
          ref="zoomImgEl"
          :src="previewUrl(zoomCandidate)"
          alt=""
          draggable="false"
          @dragstart.prevent
        />
      </div>
      <div class="dc-zv-foot" aria-hidden="true">
        <span><kbd>←</kbd><kbd>→</kbd> or <kbd>1</kbd>–<kbd>9</kbd> flip in place — differences jump out as motion</span>
        <span><kbd>P</kbd> actual pixels</span>
        <span><kbd>Enter</kbd> stack</span>
        <span><kbd>S</kbd> keep separate</span>
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

/* Fit: the same box for every candidate, so the blink stays registered. */
.dc-zv-img {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}

.dc-zv-img img {
  width: auto;
  height: 100%;
  max-width: 100%;
  object-fit: contain;
}

/* Actual pixels: 1:1, panned by dragging. */
.dc-zv-img--px {
  display: block;
  overflow: auto;
  cursor: grab;
  scrollbar-width: thin;
}

.dc-zv-img--px img {
  max-width: none;
  max-height: none;
  width: auto;
  height: auto;
  display: block;
  margin: auto;
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
