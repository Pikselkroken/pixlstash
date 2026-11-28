<template>
  <!-- How far a location draw going on in the background has got, under the
       place it is drawing (useLocationDrawStore). A click opens the draw. -->
  <button
    type="button"
    class="draw-progress"
    :class="{ 'draw-progress--ready': job.ready, 'draw-progress--slim': slim }"
    :aria-label="`${job.name}: ${job.status}. Open it.`"
    @click.stop="emit('open')"
  >
    <span class="draw-progress__track" aria-hidden="true">
      <span :style="{ width: `${Math.round((job.fraction ?? 0) * 100)}%` }"></span>
    </span>
    <span v-if="!slim" class="draw-progress__text">{{ job.status }}</span>
  </button>
</template>

<script setup>
defineProps({
  /** `{ name, status, fraction, ready }` from useLocationDrawStore. */
  job: { type: Object, required: true },
  /** The bar alone, for a sidebar row. */
  slim: { type: Boolean, default: false },
});
const emit = defineEmits(["open"]);
</script>

<style scoped>
.draw-progress {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  width: 100%;
  padding: var(--space-1) 0 0;
  border: 0;
  background: none;
  color: rgba(var(--v-theme-on-surface), 0.7);
  font: inherit;
  font-size: var(--text-xs);
  text-align: left;
  cursor: pointer;
}

.draw-progress:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--space-1);
}

.draw-progress__track {
  display: block;
  height: 4px;
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.12);
  overflow: hidden;
}

.draw-progress__track > span {
  display: block;
  height: 100%;
  background: rgb(var(--v-theme-primary));
  transition: width var(--dur-3) var(--ease-standard);
}

/* Done and waiting for the owner: the text says so in the accent. */
.draw-progress--ready .draw-progress__text {
  color: rgb(var(--v-theme-primary));
}

.draw-progress--slim {
  padding: 0;
}

.draw-progress--slim .draw-progress__track {
  height: 2px;
}
</style>
