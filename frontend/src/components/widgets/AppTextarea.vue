<template>
  <label class="app-textarea">
    <FieldLabel v-if="label">{{ label }}</FieldLabel>
    <textarea
      class="app-textarea__field"
      :value="modelValue"
      :placeholder="placeholder"
      :rows="rows"
      :disabled="disabled"
      @input="emit('update:modelValue', $event.target.value)"
    />
  </label>
</template>

<script setup>
import FieldLabel from "./FieldLabel.vue";

defineProps({
  modelValue: { type: String, default: "" },
  label: { type: String, default: "" },
  placeholder: { type: String, default: "" },
  rows: { type: [Number, String], default: 3 },
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue"]);
</script>

<style scoped>
.app-textarea {
  display: block;
}

.app-textarea__field {
  width: 100%;
  resize: vertical;
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
  line-height: var(--leading-snug);
  padding: var(--space-2) var(--space-3);
}

.app-textarea__field::placeholder {
  color: rgba(var(--v-theme-on-surface), 0.45);
}
</style>
