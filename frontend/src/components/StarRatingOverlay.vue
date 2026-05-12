<template>
  <div
    class="star-overlay"
    :class="{ 'star-overlay--compact': compact, 'star-overlay--number': numberMode }"
  >
    <template v-if="numberMode">
      <v-icon
        :size="iconSize"
        :color="dScore > 0 ? 'rgba(var(--v-theme-accent))' : 'rgba(var(--v-theme-on-background), 0.25)'"
        :title="dScore > 0 ? `Rated ${dScore} — click to change` : 'Click to rate'"
        style="cursor: pointer; vertical-align: middle; display: flex;"
        @click.stop="cycleRating()"
        >mdi-star</v-icon
      >
      <span class="star-number-label" :style="{ opacity: dScore > 0 ? 1 : 0 }">{{ dScore > 0 ? dScore : 1 }}</span>
    </template>
    <template v-else>
      <v-icon
        v-for="n in max"
        :key="n"
        :size="iconSize"
        :color="
          n <= dScore
            ? 'rgba(var(--v-theme-accent))'
            : 'rgba(var(--v-theme-on-background), 0.2)'
        "
        :title="`Set rating ${n} (${n})`"
        style="cursor: pointer"
        @click.stop="handleClick(n)"
        >mdi-star</v-icon
      >
    </template>
  </div>
</template>

<script setup>
import { computed } from "vue";

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

.star-overlay--compact {
  z-index: 120;
  font-size: 0.6em;
  gap: 0;
}

.star-overlay--compact:hover {
  filter: brightness(1.25);
}

.star-overlay--compact .v-icon {
  width: 1em;
  height: 1em;
}

.star-overlay--compact .v-icon:hover {
  width: 1em;
  height: 1em;
  color: rgba(var(--v-theme-accent), 0.5);
}

.star-overlay--number {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  cursor: pointer;
  line-height: 1;
}

.star-overlay--number:hover {
  filter: brightness(1.3);
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
  font-weight: 700;
  color: rgba(var(--v-theme-accent));
  line-height: 1;
  display: flex;
  align-items: center;
}
</style>
