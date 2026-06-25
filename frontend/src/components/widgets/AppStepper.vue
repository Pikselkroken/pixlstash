<template>
  <div class="app-stepper" :style="{ width: width + 'px' }">
    <button
      type="button"
      class="app-stepper__btn"
      title="Decrease"
      :disabled="disabled"
      @click="bump(-step)"
    >
      <v-icon size="17">mdi-minus</v-icon>
    </button>
    <input
      class="app-stepper__input"
      type="text"
      inputmode="numeric"
      :value="modelValue"
      :disabled="disabled"
      @input="onInput"
      @blur="onBlur"
    />
    <button
      type="button"
      class="app-stepper__btn"
      title="Increase"
      :disabled="disabled"
      @click="bump(step)"
    >
      <v-icon size="17">mdi-plus</v-icon>
    </button>
  </div>
</template>

<script setup>
import { VIcon } from "vuetify/components";

const props = defineProps({
  modelValue: { type: [String, Number], default: "" },
  min: { type: Number, default: 0 },
  max: { type: Number, default: 65535 },
  step: { type: Number, default: 1 },
  disabled: { type: Boolean, default: false },
  width: { type: Number, default: 116 },
});

const emit = defineEmits(["update:modelValue"]);

function clamp(v) {
  return Math.max(props.min, Math.min(props.max, v));
}

function bump(delta) {
  if (props.disabled) return;
  const n = parseInt(props.modelValue, 10) || 0;
  emit("update:modelValue", String(clamp(n + delta)));
}

function onInput(e) {
  // Keep digits only while typing; clamp on blur so partial entry is allowed.
  emit("update:modelValue", e.target.value.replace(/[^0-9]/g, ""));
}

function onBlur(e) {
  const parsed = parseInt(e.target.value, 10);
  if (Number.isNaN(parsed)) {
    emit("update:modelValue", String(props.min));
  } else {
    emit("update:modelValue", String(clamp(parsed)));
  }
}
</script>

<style scoped>
.app-stepper {
  display: inline-flex;
  align-items: center;
  height: 26px;
  overflow: hidden;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-input-background));
}

.app-stepper__btn {
  width: 30px;
  height: 26px;
  flex-shrink: 0;
  border: none;
  padding: 0;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  transition: background var(--dur-1) var(--ease-standard);
}

.app-stepper__btn:hover:not(:disabled) {
  background: var(--hover-wash);
}

.app-stepper__btn:disabled {
  color: rgba(var(--v-theme-on-surface), 0.5);
  cursor: default;
}

.app-stepper__input {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: none;
  outline: none;
  text-align: center;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
  border-left: 1px solid rgb(var(--v-theme-border));
  border-right: 1px solid rgb(var(--v-theme-border));
}

.app-stepper__input:disabled {
  color: rgba(var(--v-theme-on-surface), 0.5);
}
</style>
