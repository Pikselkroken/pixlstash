<template>
  <div
    class="star-overlay"
    :class="{
      'star-overlay--compact': compact,
      'star-overlay--number': numberMode,
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
    <template v-else>
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
    </template>
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
});

const emit = defineEmits(["set-score"]);

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
  z-index: 120;
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
