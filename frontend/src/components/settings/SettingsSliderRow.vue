<template>
  <div class="slider-row">
    <span class="slider-row__value">{{ modelValue }}{{ suffix }}</span>
    <div class="slider-row__track">
      <v-slider
        :model-value="modelValue"
        :min="min"
        :max="max"
        :step="step"
        hide-details
        density="compact"
        color="accent"
        track-color="rgba(var(--v-theme-on-surface), 0.2)"
        @update:model-value="emit('update:modelValue', $event)"
      />
    </div>
  </div>
</template>

<script setup>
import { VSlider } from "vuetify/components";

defineProps({
  modelValue: { type: Number, default: 0 },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 100 },
  step: { type: Number, default: 1 },
  suffix: { type: String, default: "" },
});

const emit = defineEmits(["update:modelValue"]);
</script>

<style scoped>
.slider-row {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  padding-right: var(--space-2);
}

.slider-row__value {
  min-width: 60px;
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.slider-row__track {
  flex: 1;
}
</style>
