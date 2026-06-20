<script setup>
/**
 * Schema-driven parameter form for tagger plugins.
 *
 * Supported field types:
 *   number / integer — numeric input with optional min/max/step
 *   bool             — checkbox
 *   select           — dropdown (field.enum or field.options: {value, label} objects or plain strings)
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
  const source = Array.isArray(field.enum)
    ? field.enum
    : Array.isArray(field.options)
      ? field.options
      : null;
  if (!source) return value;
  const entry = source.find((e) =>
    typeof e === "object" ? e.value === value : e === value,
  );
  if (!entry) return value;
  return typeof entry === "object" ? (entry.label ?? entry.value) : entry;
}

function enumOptions(field) {
  const source = Array.isArray(field.enum)
    ? field.enum
    : Array.isArray(field.options)
      ? field.options
      : [];
  return source.map((e) =>
    typeof e === "object" ? e : { value: e, label: e },
  );
}

/**
 * Scaled numeric fields are stored in one unit (e.g. a 0–1 fraction) but
 * displayed and edited in another (e.g. percentage points) via `field.scale`.
 * Rounding strips the floating-point noise that x100 / ÷100 introduces.
 */
function roundClean(n) {
  return Math.round(n * 1e9) / 1e9;
}

function toDisplay(field, stored) {
  if (stored === null || stored === undefined || stored === "") return "";
  return roundClean(Number(stored) * (field.scale ?? 1));
}

function scaleBound(field, value) {
  if (value === null || value === undefined) return undefined;
  return roundClean(Number(value) * (field.scale ?? 1));
}

function setScaled(field, raw) {
  if (raw === "" || raw === null) {
    form[field.name] = null;
    return;
  }
  const parsed = Number(raw);
  if (Number.isNaN(parsed)) return;
  form[field.name] = roundClean(parsed / (field.scale ?? 1));
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
        v-if="
          field.type === 'select' &&
          (Array.isArray(field.enum) || Array.isArray(field.options))
        "
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

      <!-- scaled number (stored unit differs from displayed unit) -->
      <div
        v-else-if="
          (field.type === 'number' || field.type === 'integer') && field.scale
        "
        class="tagger-params-scaled-row"
      >
        <input
          :value="toDisplay(field, form[field.name])"
          type="number"
          class="tagger-params-input"
          :min="scaleBound(field, field.min)"
          :max="scaleBound(field, field.max)"
          :step="
            scaleBound(field, field.step) ??
            (field.type === 'integer' ? 1 : undefined)
          "
          @input="setScaled(field, $event.target.value)"
        />
        <span v-if="field.unit" class="tagger-params-unit">{{
          field.unit
        }}</span>
      </div>

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
  gap: var(--space-4);
}

.tagger-params-empty {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.55);
}

.tagger-params-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.tagger-params-label {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-surface), 0.75);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.tagger-params-input {
  background: rgba(var(--v-theme-surface-variant), 0.5);
  border: 1px solid rgba(var(--v-theme-on-surface), 0.18);
  border-radius: var(--radius-sm);
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
  outline: none;
  width: 100%;
  box-sizing: border-box;
}

.tagger-params-input:focus {
  border-color: rgb(var(--v-theme-primary));
}

.tagger-params-scaled-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.tagger-params-unit {
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.tagger-params-textarea {
  resize: vertical;
  min-height: 60px;
}

.tagger-params-checkbox-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
  cursor: pointer;
}

.tagger-params-help {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), 0.5);
  margin-top: var(--space-1);
}
</style>
