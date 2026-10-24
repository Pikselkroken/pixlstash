<template>
  <span
    class="dlm"
    role="img"
    :aria-label="spoken"
    data-testid="downloading-mark"
    ><Tooltip :text="spoken" activator="parent" /><v-icon
      :size="size"
      aria-hidden="true"
      >mdi-download</v-icon
    ></span
  >
</template>

<script setup>
/**
 * A model PixlStash is still downloading (`POST /model-downloads`): the
 * download glyph, its arrow dropping, in the accent hue live work wears.
 *
 * Drawn where a model on its way is offered or held (a workflow's model
 * picker, a set's slot and its picker, a run's refusal), so the owner can put
 * it in before it lands and sees why it will not run yet. Under reduced motion
 * the global reset stills the arrow; the glyph and its words still say it.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";

import Tooltip from "./Tooltip.vue";

const props = defineProps({
  /** How far it is, 0-100; omitted where the size is not known yet. */
  percent: { type: Number, default: null },
  /** The icon sizes: 16 in a row, 18 beside text. */
  size: { type: Number, default: 16 },
});

const spoken = computed(() =>
  props.percent ? `Downloading, ${Math.round(props.percent)}%` : "Downloading",
);
</script>

<style scoped>
.dlm {
  display: inline-flex;
  align-items: center;
  vertical-align: middle;
  color: rgb(var(--v-theme-accent));
}

.dlm :deep(.v-icon) {
  animation: dlm-drop 1.4s var(--ease-standard) infinite;
}

@keyframes dlm-drop {
  0% {
    transform: translateY(-25%);
    opacity: 0;
  }
  30%,
  70% {
    transform: translateY(0);
    opacity: 1;
  }
  100% {
    transform: translateY(15%);
    opacity: 0.4;
  }
}
</style>
