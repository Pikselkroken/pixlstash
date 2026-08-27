<template>
  <div class="rs-pair">
    <!-- Banner: the only context where Both / Neither / Left / Right mean
         anything - two images that are versions of ONE shot. The question is
         merged in so the decision bar stays buttons-only. -->
    <div class="rs-pair-banner">
      <v-icon size="24" class="rs-pair-banner-icon"
        >mdi-content-duplicate</v-icon
      >
      <div class="rs-pair-banner-text">
        <span class="rs-pair-banner-title"
          >These two are versions of the same shot{{ reasonText }} - their
          labels disagree.</span
        >
        <span class="rs-pair-banner-sub"
          >Versions should carry the same tags. Which really has “{{
            item.tag
          }}”?</span
        >
      </div>
      <span v-if="item._isNew" class="rs-pair-new">NEW - from refresh</span>
    </div>

    <div class="rs-pair-body">
      <figure
        v-for="pane in panes"
        :key="pane.id"
        class="rs-pair-pane"
        :class="{ 'rs-pair-pane--tagged': pane.tagged }"
      >
        <figcaption class="rs-pair-head">
          <span class="rs-pair-id">#{{ pane.id }}</span>
          <span
            class="rs-pair-state"
            :class="{ 'rs-pair-state--tagged': pane.tagged }"
          >
            <v-icon v-if="pane.tagged" size="12">mdi-tag</v-icon>
            {{ pane.tagged ? `tagged “${item.tag}”` : "not tagged" }}
          </span>
          <span class="rs-pair-conf">{{ confText(pane.conf) }}</span>
        </figcaption>
        <div class="rs-pair-imgwrap">
          <img
            class="rs-pair-img"
            :src="imgSrc(pane.id, pane.ext)"
            :alt="`picture ${pane.id}`"
            title="Click to zoom"
            @click="openZoom(pane.id, pane.ext)"
            @error="onImgError($event, pane.id)"
          />
          <!-- On a PAIR card a locked pane is not an inert reference: both of
               its pictures are written by some corner (fix-twin / swap), so a
               lock here BLOCKS decisions. The badge is the "which half" answer
               and carries the blocking wording; the decision bar carries the
               remedy. (The backend degrades a locked-twin pair to a binary card
               at read time, so this only shows on a client-cached card that
               predates the lock.) -->
          <span
            v-if="paneLockNames(pane.id).length"
            class="rs-lock-badge"
            :title="blockingPaneTitle(paneLockNames(pane.id))"
          >
            <v-icon size="14">mdi-lock-outline</v-icon>
          </span>
          <button
            v-if="pane.tagged"
            class="rs-manual-tag"
            type="button"
            title="Tag manually (T)"
            @click.stop="openTagApply()"
          >
            <v-icon size="16">mdi-tag-plus-outline</v-icon>
          </button>
        </div>
      </figure>
    </div>
  </div>
</template>

<script setup>
// Pair card: ONLY for true versions of one shot (same PictureStack or
// dhash-near). LEFT is always the tagged side, RIGHT the untagged side -
// which picture id is which depends on the suggestion direction (same
// convention as the old overlay and the store's pairSides()).
import { computed, inject } from "vue";
import { pairSides } from "../../stores/useReviewSessionsStore";
import { useLockedSetsStore } from "../../stores/useLockedSetsStore";
import { blockingPaneTitle, lockedSetNamesOf } from "./lockedSetCopy";

const props = defineProps({
  item: { type: Object, required: true },
});

const lockedSetsStore = useLockedSetsStore();
const backendUrl = inject("rs-backend-url", "");
const openZoomInject = inject("rs-open-zoom", () => {});
const openTagApply = inject("rs-open-tag-apply", () => {});

// Locking set names for a pane. The payload ships them inline (`locked_sets` /
// `twin_locked_sets`); useLockedSetsStore is the fallback for a card cached
// before the set was locked.
function paneLockNames(id) {
  const item = props.item;
  const payload =
    id === item.picture_id
      ? item.locked_sets
      : id === item.twin_picture_id
        ? item.twin_locked_sets
        : null;
  const names = lockedSetNamesOf(payload);
  if (names.length) return names;
  return lockedSetsStore.isLocked(id) ? lockedSetsStore.lockedSetNames(id) : [];
}

const panes = computed(() => {
  const item = props.item;
  const { leftPid, rightPid } = pairSides(item);
  const suspectFirst = item.direction === "remove";
  // The suspect carries `confidence` + `picture_ext`; the twin the twin_* pair.
  const suspect = {
    id: item.picture_id,
    ext: item.picture_ext,
    conf: item.confidence,
  };
  const twin = {
    id: item.twin_picture_id,
    ext: item.twin_ext,
    conf: item.twin_confidence,
  };
  const left = suspectFirst ? suspect : twin;
  const right = suspectFirst ? twin : suspect;
  return [
    { ...left, id: leftPid, tagged: true },
    { ...right, id: rightPid, tagged: false },
  ];
});

const reasonText = computed(() => {
  const sim = props.item.twin_sim;
  if (sim == null) return "";
  return sim >= 0.999 ? " (same stack)" : ` (${Math.round(sim * 100)}% similar)`;
});

// State the confidence direction explicitly so "97% sure" can never be read
// the wrong way (same rationale as the old overlay's confText).
function confText(conf) {
  if (conf == null) return "no tagger prediction";
  const pct = Math.round(conf * 100);
  return conf >= 0.5
    ? `${pct}% sure the tag is present`
    : `${100 - pct}% sure the tag is not present`;
}

function imgSrc(id, ext) {
  if (!backendUrl || id == null) return "";
  if (ext) return `${backendUrl}/pictures/${id}.${ext}`;
  return `${backendUrl}/pictures/thumbnails/${id}.webp`;
}

function onImgError(event, id) {
  const el = event?.target;
  if (!el || el.dataset.fellBack) return;
  el.dataset.fellBack = "1";
  el.src = `${backendUrl}/pictures/thumbnails/${id}.webp`;
}

function openZoom(id, ext) {
  openZoomInject(imgSrc(id, ext), null);
}
</script>

<style scoped>
.rs-pair {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
}

.rs-pair-banner {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--radius-md);
  background: color-mix(
    in srgb,
    rgb(var(--v-theme-tertiary)) 11%,
    rgb(var(--v-theme-dark-surface))
  );
  border: 1px solid
    color-mix(in srgb, rgb(var(--v-theme-tertiary)) 38%, transparent);
  border-left: 4px solid rgb(var(--v-theme-tertiary));
}
.rs-pair-banner-icon {
  color: rgb(var(--v-theme-tertiary));
  flex-shrink: 0;
}
.rs-pair-banner-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.rs-pair-banner-title {
  font-size: 15px;
  font-weight: var(--weight-semibold);
}
.rs-pair-banner-sub {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
}
.rs-pair-new {
  flex-shrink: 0;
  font-size: 10.5px;
  font-weight: var(--weight-bold);
  letter-spacing: 0.05em;
  padding: 3px 8px;
  border-radius: 999px;
  color: rgb(var(--v-theme-accent));
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 18%, transparent);
}

.rs-pair-body {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 16px;
}
.rs-pair-pane {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.14);
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-dark-surface), 0.04);
  margin: 0;
}
.rs-pair-pane--tagged {
  border-color: rgb(var(--v-theme-accent));
}
.rs-pair-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 13px;
  border-bottom: 1px solid rgba(var(--v-theme-on-dark-surface), 0.1);
}
.rs-pair-id {
  font-family: var(--font-mono, monospace);
  font-size: 12.5px;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-pair-state {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}
.rs-pair-state--tagged {
  color: rgb(var(--v-theme-accent));
}
.rs-pair-conf {
  margin-left: auto;
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-dark-surface), 0.55);
  white-space: nowrap;
}
.rs-pair-imgwrap {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
}
.rs-pair-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
  cursor: zoom-in;
}

/* Reference-only lock badge - translucent corner chip. Uses the shared
   `--scrim-photo` token (dark scrim over the photo) matching this card's tag
   chips, so mdi-lock-outline reads on any image. */
.rs-lock-badge {
  position: absolute;
  top: 8px;
  right: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  pointer-events: auto;
}

/* Visible manual-tag affordance (same flow as the T shortcut); applies to
   both pictures of the pair via the shared panel. */
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
</style>
