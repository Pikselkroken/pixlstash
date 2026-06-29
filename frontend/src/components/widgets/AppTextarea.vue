<template>
  <label class="app-textarea">
    <FieldLabel v-if="label">{{ label }}</FieldLabel>
    <textarea
      class="app-textarea__field"
      :class="{ 'app-textarea__field--focus': focused }"
      :value="modelValue"
      :placeholder="placeholder"
      :rows="rows"
      :disabled="disabled"
      @input="emit('update:modelValue', $event.target.value)"
      @focus="focused = true"
      @blur="focused = false"
    />
  </label>
</template>

<script setup>
import { ref } from "vue";
import FieldLabel from "./FieldLabel.vue";

defineProps({
  modelValue: { type: String, default: "" },
  label: { type: String, default: "" },
  placeholder: { type: String, default: "" },
  rows: { type: [Number, String], default: 3 },
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue"]);
const focused = ref(false);
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
  outline: none;
  transition: border-color var(--dur-1) var(--ease-standard);
}

.app-textarea__field--focus {
  border-color: rgb(var(--v-theme-accent));
}

.app-textarea__field::placeholder {
  color: rgba(var(--v-theme-on-surface), 0.45);
}
</style>
