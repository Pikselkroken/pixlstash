<template>
  <label class="app-select">
    <FieldLabel v-if="label">{{ label }}</FieldLabel>
    <div class="app-select__wrap">
      <select
        class="app-select__field"
        :class="{ 'app-select__field--compact': compact }"
        :value="modelValue"
        :disabled="disabled"
        @change="emit('update:modelValue', $event.target.value)"
      >
        <option
          v-for="opt in normalizedOptions"
          :key="String(opt.value)"
          :value="opt.value"
        >
          {{ opt.label }}
        </option>
      </select>
      <v-icon size="18" class="app-select__chevron">mdi-chevron-down</v-icon>
    </div>
  </label>
</template>

<script setup>
import { computed } from "vue";
import { VIcon } from "vuetify/components";
import FieldLabel from "./FieldLabel.vue";

const props = defineProps({
  modelValue: { type: [String, Number, null], default: "" },
  label: { type: String, default: "" },
  // Array of strings OR { label, value } objects.
  options: { type: Array, default: () => [] },
  compact: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue"]);

const normalizedOptions = computed(() =>
  props.options.map((o) =>
    typeof o === "object" && o !== null ? o : { label: o, value: o },
  ),
);
</script>

<style scoped>
.app-select {
  display: block;
}

.app-select__wrap {
  position: relative;
}

.app-select__field {
  appearance: none;
  -webkit-appearance: none;
  width: 100%;
  height: 27px;
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  padding: 0 38px 0 var(--space-4);
  cursor: pointer;
  outline: none;
  transition: border-color var(--dur-1) var(--ease-standard);
}

.app-select__field:focus {
  border-color: rgb(var(--v-theme-accent));
}

.app-select__field--compact {
  height: 21px;
  font-size: var(--text-xs);
  padding: 0 24px 0 var(--space-3);
}

.app-select__chevron {
  position: absolute;
  right: var(--space-4);
  top: 50%;
  transform: translateY(-50%);
  color: rgba(var(--v-theme-on-surface), 0.5);
  pointer-events: none;
}
</style>
