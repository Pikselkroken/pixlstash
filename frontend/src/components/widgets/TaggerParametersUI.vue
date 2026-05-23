<script setup>
/**
 * Schema-driven parameter form for tagger plugins.
 *
 * Supported field types:
 *   number / integer — numeric input with optional min/max/step
 *   bool             — checkbox
 *   select           — dropdown (enum of {value, label} objects or plain strings)
 *   string           — single-line text input
 *   textarea         — multi-line text input
 *   csv-int          — comma-separated integers (stored as string, validated)
 */
import { reactive, watch } from "vue";

const props = defineProps({
  /** Array of parameter schema fields from TaggerPlugin.parameter_schema(). */
  schema: { type: Array, default: () => [] },
  /** Current param values (v-model). */
  modelValue: { type: Object, default: () => ({}) },
});

const emit = defineEmits(["update:modelValue"]);

const form = reactive({});

function applyValues(source) {
  for (const field of props.schema) {
    const raw = source?.[field.name];
    form[field.name] = raw !== undefined ? raw : (field.default ?? null);
  }
}

applyValues(props.modelValue);

watch(
  () => props.modelValue,
  (next) => applyValues(next),
  { deep: true },
);

watch(form, (next) => emit("update:modelValue", { ...next }), { deep: true });

function enumLabel(field, value) {
  if (!Array.isArray(field.enum)) return value;
  const entry = field.enum.find((e) =>
    typeof e === "object" ? e.value === value : e === value,
  );
  if (!entry) return value;
  return typeof entry === "object" ? (entry.label ?? entry.value) : entry;
}

function enumOptions(field) {
  if (!Array.isArray(field.enum)) return [];
  return field.enum.map((e) =>
    typeof e === "object" ? e : { value: e, label: e },
  );
}
</script>

<template>
  <div class="tagger-params-root">
    <div v-if="!schema.length" class="tagger-params-empty">
      This plugin has no configurable parameters.
    </div>
    <div v-for="field in schema" :key="field.name" class="tagger-params-field">
      <label class="tagger-params-label">{{ field.label || field.name }}</label>

      <!-- select -->
      <select
        v-if="field.type === 'select' && Array.isArray(field.enum)"
        v-model="form[field.name]"
        class="tagger-params-input"
      >
        <option
          v-for="opt in enumOptions(field)"
          :key="opt.value"
          :value="opt.value"
        >
          {{ opt.label }}
        </option>
      </select>

      <!-- number / integer -->
      <input
        v-else-if="field.type === 'number' || field.type === 'integer'"
        v-model.number="form[field.name]"
        type="number"
        class="tagger-params-input"
        :min="field.min ?? undefined"
        :max="field.max ?? undefined"
        :step="field.step ?? (field.type === 'integer' ? 1 : undefined)"
      />

      <!-- bool -->
      <label
        v-else-if="field.type === 'bool' || field.type === 'boolean'"
        class="tagger-params-checkbox-row"
      >
        <input v-model="form[field.name]" type="checkbox" />
        <span>Enabled</span>
      </label>

      <!-- textarea -->
      <textarea
        v-else-if="field.type === 'textarea'"
        v-model="form[field.name]"
        class="tagger-params-input tagger-params-textarea"
        rows="3"
      />

      <!-- csv-int -->
      <input
        v-else-if="field.type === 'csv-int'"
        v-model="form[field.name]"
        type="text"
        class="tagger-params-input"
        placeholder="e.g. 1, 2, 3"
      />

      <!-- string (default) -->
      <input
        v-else
        v-model="form[field.name]"
        type="text"
        class="tagger-params-input"
      />

      <div v-if="field.description" class="tagger-params-help">
        {{ field.description }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.tagger-params-root {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.tagger-params-empty {
  font-size: 13px;
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.tagger-params-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tagger-params-label {
  font-size: 12px;
  font-weight: 600;
  color: rgba(var(--v-theme-on-surface), 0.75);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.tagger-params-input {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.18);
  border-radius: 4px;
  padding: 6px 8px;
  font-size: 13px;
  color: rgb(var(--v-theme-on-surface));
  outline: none;
  width: 100%;
  box-sizing: border-box;
}

.tagger-params-input:focus {
  border-color: rgb(var(--v-theme-primary));
}

.tagger-params-textarea {
  resize: vertical;
  min-height: 60px;
}

.tagger-params-checkbox-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  cursor: pointer;
}

.tagger-params-help {
  font-size: 11px;
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin-top: 2px;
}
</style>
