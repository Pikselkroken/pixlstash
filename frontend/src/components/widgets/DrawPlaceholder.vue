<template>
  <!-- Where a picture or an item being drawn will be: its frame, what it is,
       and the draw's bar (DrawProgress), so the place it lands is already on
       the screen. A click opens the draw. -->
  <div class="draw-placeholder" :data-testid="testid">
    <!-- The frame takes a click anywhere; the bar inside is the button a
         keyboard reaches (a button cannot hold another). -->
    <div
      class="draw-placeholder__frame"
      :style="aspect ? { '--tile-aspect': aspect } : undefined"
      @click="emit('open')"
    >
      <v-icon size="28" class="draw-placeholder__icon" aria-hidden="true">{{ icon }}</v-icon>
      <span class="draw-placeholder__label">{{ label }}</span>
      <DrawProgress
        class="draw-placeholder__progress"
        :job="{ ...job, name: label }"
        @open="emit('open')"
      />
    </div>
  </div>
</template>

<script setup>
import { VIcon } from "vuetify/components";
import DrawProgress from "./DrawProgress.vue";

defineProps({
  /** `{ status, fraction, ready }`: the draw it waits on. */
  job: { type: Object, required: true },
  label: { type: String, required: true },
  icon: { type: String, default: "mdi-image-outline" },
  /** Width over height of what will land here; 16:9 when unknown. */
  aspect: { type: Number, default: null },
  testid: { type: String, default: undefined },
});
const emit = defineEmits(["open"]);
</script>

<style scoped>
.draw-placeholder {
  min-width: 0;
}

/* A dashed frame on the surface: a slot, not a picture yet. */
.draw-placeholder__frame {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: 100%;
  aspect-ratio: var(--tile-aspect, 16 / 9);
  padding: var(--space-3);
  border: 1px dashed rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  color: rgba(var(--v-theme-on-surface), 0.6);
  font: inherit;
  cursor: pointer;
}

.draw-placeholder__frame:hover {
  background: var(--hover-wash);
}

.draw-placeholder__label {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
}

.draw-placeholder__progress {
  width: min(80%, 240px);
}

@media (prefers-reduced-motion: no-preference) {
  .draw-placeholder__icon {
    animation: draw-placeholder-breathe 2.4s ease-in-out infinite;
  }
}

@keyframes draw-placeholder-breathe {
  50% {
    opacity: 0.4;
  }
}
</style>
