<template>
  <div class="star-overlay" :class="{ 'star-overlay--compact': compact }">
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
  </div>
</template>

<script setup>
import { computed } from "vue";

const props = defineProps({
  score: { type: Number, default: 0 },
  max: { type: Number, default: 5 },
  iconSize: { type: [Number, String], default: "large" },
  compact: { type: Boolean, default: false },
});

const emit = defineEmits(["set-score"]);

const dScore = computed(() => Math.max(0, props.score || 0));

function handleClick(n) {
  emit("set-score", n);
}
</script>

<style scoped>
.star-overlay {
  display: flex;
  flex-direction: row;
  align-items: center;
  box-shadow: none;
}

.star-overlay--compact {
  z-index: 120;
  font-size: 0.65em;
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
</style>
