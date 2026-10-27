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
          >mdi-star</v-icon
        >
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
          :size="iconSize"
          :color="
            n <= dScore
              ? 'rgba(var(--v-theme-accent))'
              : 'rgba(var(--v-theme-on-background), 0.6)'
          "
          :aria-label="`Set rating ${n} (${n})`"
          style="cursor: pointer"
          @click.stop="handleClick(n)"
          >mdi-star</v-icon
        >
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
        :size="iconSize"
        class="star-overlay__heart"
        :class="{ 'star-overlay__heart--on': favourite }"
        role="button"
        :aria-label="favourite ? 'Unfavourite' : 'Favourite'"
        :aria-pressed="favourite ? 'true' : 'false'"
        style="cursor: pointer"
        @click.stop="emit('toggle-favourite')"
        >{{ favourite ? "mdi-heart" : "mdi-heart-outline" }}</v-icon
      >
    </span>
  </div>
</template>

<script setup>
import { computed } from "vue";
import Tooltip from "./Tooltip.vue";

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
