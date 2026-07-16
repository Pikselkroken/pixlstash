<template>
  <div class="rs-pair rs-conf">
    <!-- Banner: plain-language framing, never "near-duplicate" / "train/eval
         split" / "leakage" / "component" (UX spec §5.2). Mirrors
         ReviewPairCard's banner chrome (same classes) — this is a new KIND of
         pair card, not a new UI paradigm. -->
    <div class="rs-pair-banner">
      <v-icon size="24" class="rs-pair-banner-icon">mdi-image-multiple-outline</v-icon>
      <div class="rs-pair-banner-text">
        <span class="rs-pair-banner-title">Two pictures, two jobs</span>
        <span class="rs-pair-banner-sub">
          PixlStash found that these look like the same shot. It keeps
          separate pictures for teaching the tagger and for checking its
          work, so it never grades the model on a picture it already
          studied. These are currently stuck in the middle because they'd
          normally end up on opposite sides.
        </span>
      </div>
    </div>

    <div class="rs-pair-body">
      <figure
        v-for="pane in panes"
        :key="pane.id"
        class="rs-pair-pane"
      >
        <figcaption class="rs-pair-head">
          <span class="rs-pair-id">#{{ pane.id }}</span>
        </figcaption>
        <div class="rs-pair-imgwrap">
          <img
            class="rs-pair-img"
            :src="imgSrc(pane.id)"
            :alt="`picture ${pane.id}`"
            title="Click to zoom"
            @click="openZoom(pane.id)"
          />
        </div>
      </figure>
    </div>

    <!-- Step 1: always shown. -->
    <div v-if="step === 1" class="rs-conf-step">
      <p class="rs-conf-question">Are these actually the same shot?</p>
      <div class="rs-conf-choices">
        <button
          class="rs-conf-btn rs-conf-btn--yes"
          type="button"
          :disabled="busy"
          @click="emit('same', true)"
        >
          Yes
        </button>
        <button
          class="rs-conf-btn"
          type="button"
          :disabled="busy"
          title="Marks both as unrelated for now — they’re set aside (not used for teaching or checking) until this is revisited."
          @click="emit('same', false)"
        >
          No
        </button>
      </div>
    </div>

    <!-- Step 2: only after "Yes" — progressive disclosure inside the card. -->
    <div v-else class="rs-conf-step">
      <p class="rs-conf-question">Keep both together for:</p>
      <div class="rs-conf-choices">
        <button
          class="rs-conf-btn"
          type="button"
          :disabled="busy"
          @click="emit('choose', 'TRAIN')"
        >
          Teaching the tagger
        </button>
        <button
          class="rs-conf-btn"
          type="button"
          :disabled="busy"
          @click="emit('choose', 'EVAL')"
        >
          Checking its work
        </button>
        <button
          class="rs-conf-btn rs-conf-btn--recommended"
          type="button"
          :disabled="busy"
          @click="emit('choose', 'NEITHER')"
        >
          Leave both out for now
          <span class="rs-conf-recommended-tag">recommended</span>
        </button>
      </div>
      <button class="rs-conf-back" type="button" @click="emit('back')">
        <v-icon size="13">mdi-arrow-left</v-icon> Back
      </button>
    </div>
  </div>
</template>

<script setup>
// A new *kind* of pair card, reusing ReviewPairCard's visual shape/chrome
// (same .rs-pair-* classes, same click-to-zoom wiring) for a different
// question: "which side of the fence do these two belong on" instead of
// "does this pair share a tag". See docs/reviews/
// tag-review-accuracy-freeze-conflicts-ux-spec.md §5.2.
import { computed, inject } from "vue";

const props = defineProps({
  group: { type: Object, required: true }, // { componentKey, members: [{picture_id, ...}] }
  step: { type: Number, default: 1 }, // 1 = "same shot?", 2 = "keep together for?"
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["same", "choose", "back"]);

const backendUrl = inject("rs-backend-url", "");
const openZoomInject = inject("rs-open-zoom", () => {});

// The conflict row carries no picture extension (GET /picture_splits/conflicts
// only returns id/split/component_key/assigned_at/conflict_detail) — the
// thumbnail is the only image source available without a second fetch.
const panes = computed(() =>
  (props.group?.members ?? []).map((m) => ({ id: m.picture_id })),
);

function imgSrc(id) {
  if (!backendUrl || id == null) return "";
  return `${backendUrl}/pictures/thumbnails/${id}.webp`;
}

function openZoom(id) {
  openZoomInject(imgSrc(id), null);
}
</script>

<style scoped>
/* Layout/chrome classes (.rs-pair-*) intentionally match ReviewPairCard.vue
   verbatim — same visual language, different content. Only the .rs-conf-*
   classes below are new. */
.rs-pair {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
  width: 100%;
  max-width: 1100px;
  margin: 0 auto;
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
    rgb(var(--v-theme-warning)) 11%,
    rgb(var(--v-theme-dark-surface))
  );
  border: 1px solid color-mix(in srgb, rgb(var(--v-theme-warning)) 38%, transparent);
  border-left: 4px solid rgb(var(--v-theme-warning));
}
.rs-pair-banner-icon {
  color: rgb(var(--v-theme-warning));
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
  /* 0.65, not 0.7 — matches ReviewPairCard.vue's identical class exactly;
     these two banners must read as the same chrome family. */
  color: rgba(var(--v-theme-on-dark-surface), 0.65);
  line-height: var(--leading-body);
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

.rs-conf-step {
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4) 0;
}
.rs-conf-question {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}
.rs-conf-choices {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: var(--space-3);
}
.rs-conf-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 38px;
  padding: 0 var(--space-5);
  cursor: pointer;
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: var(--weight-semibold);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-conf-btn:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.14);
}
.rs-conf-btn:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}
.rs-conf-btn:focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
}
.rs-conf-btn--yes {
  border-color: color-mix(in srgb, rgb(var(--v-theme-accent)) 60%, transparent);
  background: color-mix(in srgb, rgb(var(--v-theme-accent)) 16%, transparent);
  color: rgb(var(--v-theme-accent));
}
/* Recommended: a small label next to the option, not a different button
   style — the nudge toward the system's own fail-closed default must not
   read as the only clickable choice (no dark-pattern default). */
.rs-conf-btn--recommended {
  border-color: color-mix(in srgb, rgb(var(--v-theme-tertiary)) 55%, transparent);
}
.rs-conf-recommended-tag {
  padding: 1px 7px;
  border-radius: var(--radius-pill);
  font-size: 10px;
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: rgb(var(--v-theme-tertiary));
  background: color-mix(in srgb, rgb(var(--v-theme-tertiary)) 18%, transparent);
}
.rs-conf-back {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  border: none;
  background: none;
  cursor: pointer;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}
.rs-conf-back:hover {
  color: rgb(var(--v-theme-on-dark-surface));
}
.rs-conf-back:focus-visible {
  outline: 2px solid rgb(var(--v-theme-focus));
  outline-offset: 1px;
}
</style>
