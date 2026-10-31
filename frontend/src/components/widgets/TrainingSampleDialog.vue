<template>
  <AppDialog
    :open="!!sample"
    :title="sample ? `Step ${Number(sample.step).toLocaleString()}` : ''"
    size="xl"
    @close="emit('close')"
  >
    <template v-if="sample">
      <img
        class="tsd-img"
        :src="sample.src"
        :alt="sample.prompt || `Sample, step ${sample.step}`"
        data-testid="training-sample-full"
      />
      <p v-if="sample.prompt" class="tsd-prompt">{{ sample.prompt }}</p>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * One training sample at full size. A sample is a file in the run's folder,
 * not a library picture, so the lightbox (which opens pictures by id) cannot
 * show it; this is what a click on one opens, in the run page and in
 * the Models rail.
 */
import { nextTick, watch } from "vue";

import AppDialog from "./AppDialog.vue";

const props = defineProps({
  /** `{ src, step, prompt }`, or null when closed. */
  sample: { type: Object, default: null },
});

const emit = defineEmits(["close"]);

// Opened from code, so no activator owns the focus; it goes back to the
// picture that opened it by hand when the dialog closes.
let returnFocus = null;
watch(
  () => props.sample,
  async (value, previous) => {
    if (value && !previous) {
      returnFocus =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
    } else if (!value && previous) {
      await nextTick();
      if (returnFocus?.isConnected) returnFocus.focus();
      returnFocus = null;
    }
  },
);
</script>

<style scoped>
.tsd-img {
  display: block;
  width: 100%;
  max-height: 75vh;
  object-fit: contain;
  border-radius: var(--radius-sm);
}

.tsd-prompt {
  margin: 0;
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
