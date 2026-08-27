<template>
  <div class="rs-bin">
    <!-- Why it's here + the question, merged into one compact banner so the
         decision bar below is buttons-only. -->
    <div
      class="rs-bin-banner"
      :class="isRemove ? 'rs-bin-banner--remove' : 'rs-bin-banner--add'"
    >
      <v-icon size="19" class="rs-bin-banner-icon">{{
        isRemove ? "mdi-tag-remove-outline" : "mdi-tag-plus-outline"
      }}</v-icon>
      <span class="rs-bin-banner-text">
        <!-- With neighbours, state the neighbour vote. With none (the zero-
             ground-truth fallback the backend flags with an empty `neighbors`
             list), a "0 of 0 similar images" sentence would be fabricated -
             show the backend's free-text `reason` instead. -->
        <template v-if="hasNeighbors">
          <template v-if="isRemove"
            >Tagged “{{ item.tag }}” - but only {{ nHas }} of
            {{ nTot }} similar images have it.</template
          >
          <template v-else
            >Not tagged “{{ item.tag }}” - but {{ nHas }} of
            {{ nTot }} similar images have it.</template
          >
          <span class="rs-bin-banner-conf"> · {{ taggerText }}</span>
        </template>
        <template v-else>{{ reasonText }}</template>
      </span>
      <strong class="rs-bin-question"
        >Should this have the tag “{{ item.tag }}”?</strong
      >
      <span class="rs-bin-banner-spacer"></span>
      <!-- Evidence-region toggle (H): only when there's a region to show. -->
      <button
        v-if="hasRegion"
        type="button"
        class="rs-region-toggle"
        :class="{ 'rs-region-toggle--on': store.heatmapEnabled }"
        :aria-pressed="store.heatmapEnabled"
        :title="
          store.heatmapEnabled
            ? `Hide the “${item.tag}” evidence region (H)`
            : `Show where “${item.tag}” is (H)`
        "
        @click.stop="store.setHeatmapEnabled(!store.heatmapEnabled)"
      >
        <v-icon size="15">mdi-image-filter-center-focus</v-icon>
        Tag location
      </button>
      <span v-if="item._isNew" class="rs-bin-new">NEW - from refresh</span>
    </div>

    <!-- Picture + collapsible similar column -->
    <div class="rs-bin-body">
      <figure class="rs-bin-figure">
        <img
          ref="imgRef"
          class="rs-bin-img"
          :src="imgSrc(item.picture_id, item.picture_ext)"
          :alt="`picture ${item.picture_id}`"
          title="Click to zoom"
          @click="openZoom(item.picture_id, item.picture_ext)"
          @load="onImgLoad"
          @error="onImgError($event, item.picture_id)"
        />
        <span class="rs-img-chip rs-img-chip--id">#{{ item.picture_id }}</span>
        <span class="rs-img-chip rs-img-chip--zoom">click to zoom</span>
        <button
          class="rs-manual-tag"
          type="button"
          title="Tag manually (T)"
          @click.stop="openTagApply()"
        >
          <v-icon size="16">mdi-tag-plus-outline</v-icon>
        </button>

        <!-- Evidence-region overlay, aligned to the image's rendered content
             box (object-fit: contain letterboxes it). Heatmap fills the rect
             (visual only); each box is a clickable hotspot that zooms in. -->
        <div v-if="regionVisible" class="rs-region" :style="regionLayerStyle">
          <img
            v-if="region.heatmap"
            class="rs-region-heatmap"
            :src="region.heatmap"
            alt=""
          />
          <button
            v-for="(b, i) in region.boxes"
            :key="i"
            type="button"
            class="rs-region-box"
            :style="boxStyle(b)"
            :title="`Zoom into this “${item.tag}” region`"
            @click.stop="zoomToRegion(b)"
          >
            <v-icon size="14">mdi-magnify-plus-outline</v-icon>
          </button>
        </div>
      </figure>

      <!-- Collapsible neighbour column: the scan's evidence, made visible.
           Thumbs are zoomable context only - they never demand a verdict. -->
      <button
        v-if="hasNeighbors && !similarOpen"
        class="rs-similar-closed"
        type="button"
        title="Show similar images"
        @click="setSimilarOpen(true)"
      >
        <v-icon size="18">mdi-chevron-left</v-icon>
        <span class="rs-similar-closed-label"
          >Similar · {{ nHas }}/{{ nTot }}</span
        >
        <v-icon size="16">mdi-image-multiple-outline</v-icon>
      </button>
      <div v-else-if="hasNeighbors" class="rs-similar">
        <div class="rs-similar-head">
          <span class="rs-similar-title">Similar images</span>
          <button
            class="rs-similar-hide"
            type="button"
            title="Hide"
            @click="setSimilarOpen(false)"
          >
            <v-icon size="18">mdi-chevron-right</v-icon>
          </button>
        </div>
        <div class="rs-similar-why">
          {{ nHas }} of {{ nTot }} have “{{ item.tag }}” - why this was flagged
        </div>
        <div class="rs-similar-grid">
          <button
            v-for="n in neighbors"
            :key="n.picture_id"
            class="rs-thumb"
            :class="{ 'rs-thumb--has': n.has }"
            type="button"
            :title="
              n.has
                ? `#${n.picture_id} - has “${item.tag}”`
                : `#${n.picture_id} - no “${item.tag}”`
            "
            @click="openZoom(n.picture_id)"
          >
            <img
              class="rs-thumb-img"
              :src="thumbSrc(n.picture_id)"
              :alt="`picture ${n.picture_id}`"
              loading="lazy"
            />
            <span
              v-if="lockedSetsStore.isLocked(n.picture_id)"
              class="rs-thumb-lock"
              :title="referenceTitle(n.picture_id)"
            >
              <v-icon size="11">mdi-lock-outline</v-icon>
            </span>
            <span class="rs-thumb-badge" :class="{ 'rs-thumb-badge--has': n.has }">
              <v-icon size="11">{{
                n.has ? "mdi-tag" : "mdi-tag-off-outline"
              }}</v-icon>
            </span>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
// Binary card: ONE image, ONE question. The neighbour strip shows the scan's
// evidence (which of the k nearest neighbours carry the tag); the evidence-
// region overlay shows where the model saw the tag (Grad-CAM), toggled with H
// and persisted (same preference key as the old overlay).
import { computed, inject, onUnmounted, ref, watch } from "vue";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import {
  useLockedSetsStore,
  buildReferenceReason,
} from "../../stores/useLockedSetsStore";

const props = defineProps({
  item: { type: Object, required: true },
});

const store = useReviewSessionsStore();
const lockedSetsStore = useLockedSetsStore();
const backendUrl = inject("rs-backend-url", "");
const openZoomInject = inject("rs-open-zoom", () => {});
const openTagApply = inject("rs-open-tag-apply", () => {});

function referenceTitle(id) {
  return buildReferenceReason(lockedSetsStore.lockedSetNames(id));
}

const SIMILAR_PREF_KEY = "pixlstash:reviewSimilarOpen";

function readSimilarPref() {
  try {
    const raw = window.localStorage.getItem(SIMILAR_PREF_KEY);
    return raw === null ? true : raw === "1";
  } catch {
    return true;
  }
}

const similarOpen = ref(readSimilarPref());

function setSimilarOpen(v) {
  similarOpen.value = !!v;
  try {
    window.localStorage.setItem(SIMILAR_PREF_KEY, v ? "1" : "0");
  } catch {
    // Best-effort; the in-memory state still works this session.
  }
}

const isRemove = computed(() => props.item.direction === "remove");
const neighbors = computed(() =>
  Array.isArray(props.item.neighbors) ? props.item.neighbors : [],
);
const nHas = computed(() => neighbors.value.filter((n) => n.has).length);
const nTot = computed(() => neighbors.value.length);
const hasNeighbors = computed(() => neighbors.value.length > 0);

// The backend's free-text explanation for a zero-ground-truth fallback card
// (empty `neighbors`), e.g. "model is confident (NN%)…". Shown in place of the
// neighbour-vote sentence when there are no neighbours to vote.
const reasonText = computed(() => {
  const r = props.item.reason;
  return typeof r === "string" && r.trim() ? r.trim() : "";
});

// The tagger's confidence about the suspect picture, stated in plain text as a
// SEPARATE signal from the neighbour vote.
const taggerText = computed(() => {
  const conf = props.item.confidence;
  if (conf == null) return "no tagger prediction";
  const pct = Math.round(conf * 100);
  return conf >= 0.5
    ? `tagger: ${pct}% sure it is`
    : `tagger: ${100 - pct}% sure it isn’t`;
});

function imgSrc(id, ext) {
  if (!backendUrl || id == null) return "";
  if (ext) return `${backendUrl}/pictures/${id}.${ext}`;
  return `${backendUrl}/pictures/thumbnails/${id}.webp`;
}

function thumbSrc(id) {
  return backendUrl ? `${backendUrl}/pictures/thumbnails/${id}.webp` : "";
}

function onImgError(event, id) {
  // Full-res failed (e.g. unusual extension) - fall back to the thumbnail once.
  const el = event?.target;
  if (!el || el.dataset.fellBack) return;
  el.dataset.fellBack = "1";
  el.src = thumbSrc(id);
}

function openZoom(id, ext, box = null) {
  openZoomInject(imgSrc(id, ext), box);
}

// --- Evidence-region overlay (heatmap + boxes) -------------------------------
//
// Coordinates are normalised to the FULL image; the <img> uses object-fit:
// contain so the picture is letterboxed. Align the overlay layer to the
// rendered contain-rect (natural size vs client size, kept live with a
// ResizeObserver).
const imgRef = ref(null);
const natW = ref(0);
const natH = ref(0);
const boxW = ref(0);
const boxH = ref(0);
let ro = null;

function measure() {
  const el = imgRef.value;
  if (!el) return;
  boxW.value = el.clientWidth;
  boxH.value = el.clientHeight;
}

function onImgLoad(event) {
  natW.value = event.target.naturalWidth || 0;
  natH.value = event.target.naturalHeight || 0;
  measure();
}

watch(imgRef, (el, prev) => {
  if (ro && prev) ro.unobserve(prev);
  if (el) {
    if (!ro) ro = new ResizeObserver(() => measure());
    ro.observe(el);
    measure();
  }
});

onUnmounted(() => {
  ro?.disconnect();
  ro = null;
});

const renderedRect = computed(() => {
  if (!natW.value || !natH.value || !boxW.value || !boxH.value) return null;
  const scale = Math.min(boxW.value / natW.value, boxH.value / natH.value);
  const dw = natW.value * scale;
  const dh = natH.value * scale;
  return {
    left: (boxW.value - dw) / 2,
    top: (boxH.value - dh) / 2,
    width: dw,
    height: dh,
  };
});

// Fetch the region for each card; the store caches per (id, tag) and caches
// misses (tags outside the tagger vocabulary just show no overlay).
watch(
  () => props.item.picture_id,
  () => {
    if (props.item.picture_id != null && props.item.tag) {
      store.fetchAnomalyRegion(props.item.picture_id, props.item.tag);
    }
  },
  { immediate: true },
);

// Only a SHOWABLE region counts (a real box, not diffuse).
const region = computed(() => {
  const r = store.anomalyRegionFor(props.item.picture_id, props.item.tag);
  if (!r || r.diffuse || !Array.isArray(r.boxes) || !r.boxes.length)
    return null;
  return r;
});

const hasRegion = computed(() => !!region.value);
const regionVisible = computed(() => hasRegion.value && store.heatmapEnabled);

const regionLayerStyle = computed(() => {
  const r = renderedRect.value;
  if (!r) return { display: "none" };
  return {
    left: `${r.left}px`,
    top: `${r.top}px`,
    width: `${r.width}px`,
    height: `${r.height}px`,
  };
});

function boxStyle(box) {
  if (!Array.isArray(box) || box.length !== 4) return {};
  const [x, y, w, h] = box;
  return {
    left: `${x * 100}%`,
    top: `${y * 100}%`,
    width: `${w * 100}%`,
    height: `${h * 100}%`,
  };
}

function zoomToRegion(box) {
  openZoom(props.item.picture_id, props.item.picture_ext, box);
}
</script>

<style scoped>
.rs-bin {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  width: 100%;
}

.rs-bin-banner {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 12px;
  border-radius: var(--radius-sm);
  flex-wrap: wrap;
}
.rs-bin-banner--remove {
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-dark-surface-error)) 11%,
    rgb(var(--v-theme-dark-surface))
  );
  border-left: 3px solid rgb(var(--v-theme-dark-surface-error));
}
.rs-bin-banner--remove .rs-bin-banner-icon {
  color: rgb(var(--v-theme-dark-surface-error));
}
.rs-bin-banner--add {
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-primary)) 11%,
    rgb(var(--v-theme-dark-surface))
  );
  border-left: 3px solid rgb(var(--v-theme-primary));
}
.rs-bin-banner--add .rs-bin-banner-icon {
  color: rgb(var(--v-theme-primary));
}
.rs-bin-banner-text {
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
}
.rs-bin-banner-conf {
  font-size: var(--text-2xs);
  font-weight: var(--weight-regular);
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
}
.rs-bin-question {
  font-size: var(--text-sm);
}
.rs-bin-banner-spacer {
  flex: 1;
}
.rs-bin-new {
  flex-shrink: 0;
  font-size: 10.5px;
  font-weight: var(--weight-bold);
  letter-spacing: 0.05em;
  padding: 3px 8px;
  border-radius: 999px;
  color: rgb(var(--v-theme-accent));
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 18%, transparent);
}

.rs-region-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  height: 26px;
  padding: 0 9px;
  border-radius: var(--radius-sm);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgba(var(--v-theme-on-dark-surface), 0.8);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  white-space: nowrap;
}
.rs-region-toggle--on {
  border-color: rgb(var(--v-theme-accent));
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 15%, transparent);
  color: rgb(var(--v-theme-accent));
}

.rs-bin-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 10px;
}
.rs-bin-figure {
  flex: 1;
  min-width: 0;
  position: relative;
  display: flex;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: rgba(var(--v-theme-on-dark-surface), 0.04);
  margin: 0;
}
.rs-bin-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  cursor: zoom-in;
}
/* Chip scrims at 0.65 so the white text always clears WCAG contrast. */
.rs-img-chip {
  position: absolute;
  top: 8px;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
  pointer-events: none;
}
.rs-img-chip--id {
  left: 8px;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
}
.rs-img-chip--zoom {
  right: 8px;
  font-size: 11.5px;
  color: rgba(255, 255, 255, 0.9);
}

/* Visible manual-tag affordance (same flow as the T shortcut). */
.rs-manual-tag {
  position: absolute;
  bottom: 8px;
  left: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border: 1px solid rgba(255, 255, 255, 0.35);
  border-radius: var(--radius-sm);
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
}
.rs-manual-tag:hover {
  background: rgba(0, 0, 0, 0.85);
}

.rs-region {
  position: absolute;
  pointer-events: none;
}
.rs-region-heatmap {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0.55;
  pointer-events: none;
}
.rs-region-box {
  position: absolute;
  display: flex;
  align-items: flex-start;
  justify-content: flex-end;
  padding: 2px;
  border: 2px dashed
    color-mix(in srgb, rgb(var(--v-theme-accent)) 80%, white);
  border-radius: 4px;
  color: #fff;
  cursor: zoom-in;
  pointer-events: auto;
}

.rs-similar-closed {
  flex-shrink: 0;
  width: 34px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 10px 0;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-dark-surface), 0.05);
  color: rgba(var(--v-theme-on-dark-surface), 0.7);
}
.rs-similar-closed-label {
  writing-mode: vertical-rl;
  font-size: 11px;
  font-weight: var(--weight-bold);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.rs-similar {
  flex-shrink: 0;
  width: 176px;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-dark-surface), 0.05);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  overflow: hidden;
}
.rs-similar-head {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.1);
}
.rs-similar-title {
  flex: 1;
  font-size: 11px;
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-similar-hide {
  display: inline-flex;
  padding: 2px;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-similar-why {
  flex-shrink: 0;
  padding: 7px 10px;
  font-size: 11.5px;
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.1);
  line-height: 1.4;
}
.rs-similar-grid {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 7px;
  align-content: start;
}

.rs-thumb {
  position: relative;
  aspect-ratio: 1;
  border-radius: var(--radius-sm);
  overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  padding: 0;
  cursor: zoom-in;
  background: rgba(var(--v-theme-on-dark-surface), 0.05);
}
.rs-thumb--has {
  border-color: color-mix(
    in srgb,
    rgb(var(--v-theme-primary)) 55%,
    rgba(var(--v-theme-on-dark-surface), 0.14)
  );
}
.rs-thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
/* Reference-only lock on a neighbour that lives in a locked set - small corner
   chip using the shared `--scrim-photo` token, matching this card's tag chips. */
.rs-thumb-lock {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 17px;
  height: 17px;
  border-radius: var(--radius-sm);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-thumb-badge {
  position: absolute;
  bottom: 3px;
  right: 3px;
  width: 17px;
  height: 17px;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.65);
  /* tag-off glyph at ~0.8 white so absence is legible, not colour-only. */
  color: rgba(255, 255, 255, 0.8);
}
.rs-thumb-badge--has {
  color: rgb(var(--v-theme-primary));
}
</style>
