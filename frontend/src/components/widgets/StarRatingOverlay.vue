<template>
  <div
    class="star-overlay"
    :class="{
      'star-overlay--compact': compact,
      'star-overlay--number': numberMode,
      'star-overlay--fav-on': favouritable && favourite,
    }"
  >
    <template v-if="numberMode">
      <!-- A wrapper per star, so the tip takes the parent form and is built on
           first hover: this renders on every grid tile. -->
      <span class="star-slot">
        <Tooltip
          :text="
            dScore > 0 ? `Rated ${dScore} - click to change` : 'Click to rate'
          "
          activator="parent"
          :describe="false"
        />
        <v-icon
          :icon="STAR"
          :size="iconSize"
          :color="
            dScore > 0
              ? 'rgba(var(--v-theme-accent))'
              : 'rgba(var(--v-theme-on-background), 0.4)'
          "
          :aria-label="
            dScore > 0 ? `Rated ${dScore} - click to change` : 'Click to rate'
          "
          style="cursor: pointer; vertical-align: middle; display: flex"
          @click.stop="cycleRating()"
        />
      </span>
      <span
        class="star-number-label"
        :style="{ opacity: dScore > 0 ? 1 : 0 }"
        >{{ dScore > 0 ? dScore : 1 }}</span
      >
    </template>
    <!-- The stars and the hairline after them in one box, so a host can
         collapse the strip to its heart (a favourite at rest on a grid tile). -->
    <span v-else class="star-overlay__stars">
      <template v-if="stars">
      <span v-for="n in max" :key="n" class="star-slot">
        <Tooltip
          :text="`Set rating ${n} (${n})`"
          activator="parent"
          :describe="false"
        />
        <v-icon
          :icon="STAR"
          :size="iconSize"
          :color="
            n <= dScore
              ? 'rgba(var(--v-theme-accent))'
              : 'rgba(var(--v-theme-on-background), 0.6)'
          "
          :aria-label="`Set rating ${n} (${n})`"
          style="cursor: pointer"
          @click.stop="handleClick(n)"
        />
      </span>
      <span
        v-if="favouritable"
        class="star-overlay__rule"
        aria-hidden="true"
      ></span>
      </template>
    </span>
    <!-- The favourite heart ends the strip. Red is the ON state only; off is
         the light ink outline the stars use. -->
    <span v-if="favouritable && !numberMode" class="star-slot">
      <Tooltip
        :text="favourite ? 'Unfavourite' : 'Favourite'"
        shortcut="H"
        activator="parent"
        :describe="false"
      />
      <v-icon
        :icon="favourite ? HEART : HEART_OUTLINE"
        :size="iconSize"
        class="star-overlay__heart"
        :class="{ 'star-overlay__heart--on': favourite }"
        role="button"
        :aria-label="favourite ? 'Unfavourite' : 'Favourite'"
        :aria-pressed="favourite ? 'true' : 'false'"
        style="cursor: pointer"
        @click.stop="emit('toggle-favourite')"
      />
    </span>
  </div>
</template>

<script setup>
import { computed } from "vue";
import Tooltip from "./Tooltip.vue";

// Paths, not font glyphs: a font glyph's baseline snaps to the device pixel
// grid, which at a fractional display scale sits these up to 2px high in their
// tight pill while the hairline beside them stays centred. MDI paths.
const STAR =
  "svg:M12,17.27L18.18,21L16.54,13.97L22,9.24L14.81,8.62L12,2L9.19,8.62L2,9.24L7.45,13.97L5.82,21L12,17.27Z";
const HEART =
  "svg:M12,21.35L10.55,20.03C5.4,15.36 2,12.27 2,8.5C2,5.41 4.42,3 7.5,3C9.24,3 10.91,3.81 12,4.88C13.09,3.81 14.76,3 16.5,3C19.58,3 22,5.41 22,8.5C22,12.27 18.6,15.36 13.45,20.03L12,21.35Z";
const HEART_OUTLINE =
  "svg:M12.1,18.55L12,18.65L11.89,18.55C7.14,14.24 4,11.39 4,8.5C4,6.5 5.5,5 7.5,5C9.04,5 10.54,6 11.07,7.36H12.93C13.46,6 14.96,5 16.5,5C18.5,5 20,6.5 20,8.5C20,11.39 16.86,14.24 12.1,18.55M16.5,3C14.76,3 13.09,3.81 12,5.08C10.91,3.81 9.24,3 7.5,3C4.42,3 2,5.41 2,8.5C2,12.27 5.4,15.36 10.55,20.03L12,21.35L13.45,20.03C18.6,15.36 22,12.27 22,8.5C22,5.41 19.58,3 16.5,3Z";

const props = defineProps({
  score: { type: Number, default: 0 },
  max: { type: Number, default: 5 },
  iconSize: { type: [Number, String], default: "large" },
  compact: { type: Boolean, default: false },
  numberMode: { type: Boolean, default: false },
  /** Draw the stars. Off, a favouritable strip is its heart alone. */
  stars: { type: Boolean, default: true },
  /** End the strip with the favourite heart. */
  favouritable: { type: Boolean, default: false },
  /** Is this picture a favourite now? */
  favourite: { type: Boolean, default: false },
});

const emit = defineEmits(["set-score", "toggle-favourite"]);

const dScore = computed(() => Math.max(0, props.score || 0));

function handleClick(n) {
  emit("set-score", n);
}

function cycleRating() {
  const next = dScore.value >= props.max ? 0 : dScore.value + 1;
  emit("set-score", next);
}
</script>

<style scoped>
.star-overlay {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 0;
  box-shadow: none;
}

/* Shrink-wraps its star. A real box, not `display: contents`: the tip anchors
   to this element's rect, and a contents box has none. */
.star-slot {
  display: inline-flex;
  align-items: center;
}

.star-overlay--compact {
  z-index: var(--z-raised);
  font-size: 0.6em;
  gap: 0;
}

.star-overlay--compact .v-icon {
  width: 1em;
  height: 1em;
}

/* Hover marks the star a click would set. Every mount sits over a photo or the
   always-dark lightbox, so the wash is the dark-surface ink in both themes. */
.star-overlay .v-icon:hover {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
  border-radius: var(--radius-sm);
}

.star-overlay__stars {
  display: inline-flex;
  align-items: center;
}

/* The hairline between the score and the heart: the strip says two things. */
.star-overlay__rule {
  flex: none;
  width: 1px;
  height: 1em;
  margin: 0 var(--space-2);
  background: rgba(var(--v-theme-on-dark-surface), 0.35);
}

.star-overlay__heart {
  color: rgba(var(--v-theme-on-dark-surface), 0.75);
}

/* Every mount is over a photo or the always-dark lightbox, so the red is the
   dark-surface one in both themes. */
.star-overlay__heart--on {
  color: rgb(var(--v-theme-dark-surface-favourite));
}

.star-overlay--number {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  cursor: pointer;
  line-height: 1;
}

.star-overlay--number :deep(.v-icon) {
  font-size: inherit;
  width: 1em;
  height: 1em;
  display: flex;
  align-items: center;
  justify-content: center;
}

.star-number-label {
  font-size: 0.9em;
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-accent));
  line-height: 1;
  display: flex;
  align-items: center;
}
</style>
