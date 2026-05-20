<template>
  <div
    class="plugin-ui-root"
    :class="{
      'plugin-ui--dark': tone === 'dark',
      'plugin-ui--auto': tone !== 'dark',
    }"
  >
    <div v-if="!plugin" class="plugin-ui-note">No plugin selected.</div>
    <div v-else>
      <div
        v-if="showDescription && plugin.description"
        class="plugin-ui-description"
      >
        {{ plugin.description }}
      </div>
      <div v-if="!parameterFields.length" class="plugin-ui-note">
        This plugin has no parameters.
      </div>
      <div
        v-for="(field, index) in parameterFields"
        :key="field.name"
        class="plugin-ui-field"
        :class="{
          'plugin-ui-field--after-description':
            index === 0 && showDescription && !!plugin.description,
        }"
      >
        <label :class="['plugin-ui-label', labelClass]">{{
          field.label || field.name
        }}</label>

        <select
          v-if="
            field.type === 'string' &&
            Array.isArray(field.enum) &&
            field.enum.length
          "
          v-model="formValues[field.name]"
          :class="inputClass ? [inputClass] : ['plugin-ui-input']"
        >
          <option v-for="choice in field.enum" :key="choice" :value="choice">
            {{ (field.enumLabels && field.enumLabels[choice]) || choice }}
          </option>
        </select>

        <input
          v-else-if="field.type === 'number' || field.type === 'integer'"
          v-model.number="formValues[field.name]"
          type="number"
          :class="inputClass ? [inputClass] : ['plugin-ui-input']"
        />

        <label
          v-else-if="field.type === 'boolean'"
          class="plugin-ui-checkbox-row"
        >
          <input v-model="formValues[field.name]" type="checkbox" />
          <span>Enabled</span>
        </label>

        <input
          v-else
          v-model="formValues[field.name]"
          type="text"
          :class="inputClass ? [inputClass] : ['plugin-ui-input']"
        />

        <div v-if="field.description" class="plugin-ui-help">
          {{ field.description }}
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue";

const props = defineProps({
  plugin: { type: Object, default: null },
  modelValue: { type: Object, default: () => ({}) },
  showDescription: { type: Boolean, default: true },
  tone: { type: String, default: "auto" },
  inputClass: { type: String, default: "" },
  labelClass: { type: String, default: "" },
});

const emit = defineEmits(["update:modelValue"]);

const formValues = reactive({});
const isSyncingFromProps = ref(false);
const lastEmittedSignature = ref("");

const parameterFields = computed(() => {
  if (!props.plugin || !Array.isArray(props.plugin.parameters)) return [];
  return props.plugin.parameters.filter((field) => field && field.name);
});

function coerceDefault(field) {
  if (field.default !== undefined) return cloneParameterValue(field.default);
  if (field.type === "boolean") return false;
  if (field.type === "number" || field.type === "integer") return 0;
  if (Array.isArray(field.enum) && field.enum.length) return field.enum[0];
  return "";
}

function cloneParameterValue(value) {
  if (value == null) return value;
  if (typeof value !== "object") return value;
  try {
    return structuredClone(value);
  } catch (_) {
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (_) {
      return value;
    }
  }
}

function stableStringify(value) {
  if (value === null || value === undefined) return String(value);
  if (typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) {
    return `[${value.map((entry) => stableStringify(entry)).join(",")}]`;
  }
  const keys = Object.keys(value).sort();
  return `{${keys
    .map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`)
    .join(",")}}`;
}

function currentPayloadFromForm() {
  const payload = {};
  for (const field of parameterFields.value) {
    payload[field.name] = formValues[field.name];
  }
  return payload;
}

function syncModelValueIntoForm() {
  isSyncingFromProps.value = true;
  const incoming =
    props.modelValue && typeof props.modelValue === "object"
      ? props.modelValue
      : {};

  const fieldNames = new Set(parameterFields.value.map((field) => field.name));

  for (const key of Object.keys(formValues)) {
    if (!fieldNames.has(key)) {
      delete formValues[key];
    }
  }

  for (const field of parameterFields.value) {
    const name = field.name;
    const nextValue = Object.prototype.hasOwnProperty.call(incoming, name)
      ? cloneParameterValue(incoming[name])
      : coerceDefault(field);
    const currentSignature = stableStringify(formValues[name]);
    const nextSignature = stableStringify(nextValue);
    if (currentSignature !== nextSignature) {
      formValues[name] = nextValue;
    }
  }

  lastEmittedSignature.value = stableStringify(currentPayloadFromForm());
  isSyncingFromProps.value = false;
}

function resetFormFromPlugin() {
  syncModelValueIntoForm();
}

watch(
  () => props.plugin,
  () => {
    resetFormFromPlugin();
  },
  { immediate: true },
);

watch(
  () => props.modelValue,
  () => {
    syncModelValueIntoForm();
  },
);

watch(
  formValues,
  () => {
    if (isSyncingFromProps.value) return;
    emitValue();
  },
  { deep: true },
);

function emitValue() {
  const payload = currentPayloadFromForm();
  const signature = stableStringify(payload);
  if (signature === lastEmittedSignature.value) return;
  lastEmittedSignature.value = signature;
  emit("update:modelValue", payload);
}
</script>

<style scoped>
.plugin-ui-root {
  display: grid;
  gap: 8px;
}

.plugin-ui--auto {
  color: rgb(var(--v-theme-on-surface));
}

.plugin-ui--dark {
  color: rgb(var(--v-theme-on-dark-surface));
}

.plugin-ui-description {
  font-size: 0.8rem;
  opacity: 0.75;
}

.plugin-ui-note {
  opacity: 0.8;
}

.plugin-ui-field {
  display: grid;
  gap: 4px;
}

.plugin-ui-field--after-description {
  margin-top: 10px;
}

.plugin-ui-label {
  display: block;
  font-size: 0.82rem;
  margin-bottom: 4px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: inherit;
}

.plugin-menu-label {
  display: block;
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  margin-bottom: 4px;
  opacity: 0.9;
}

.overlay-comfy-field-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: rgba(var(--v-theme-on-dark-surface), 0.6);
}

.plugin-ui-input {
  width: 100%;
  min-height: 32px;
  padding: 0 8px;
  border-radius: 4px;
}

.plugin-run-select {
  width: 100%;
  height: 32px;
  border-radius: 4px;
  border: 1px solid rgba(var(--v-theme-primary), 0.4);
  background: rgba(var(--v-theme-background), 0.7);
  color: rgb(var(--v-theme-on-background));
  padding: 0 8px;
}

.overlay-comfy-select {
  width: 100%;
  background: rgba(var(--v-theme-shadow), 0.45);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  color: rgb(var(--v-theme-on-dark-surface));
  border-radius: 8px;
  padding: 6px 8px;
  font-size: 0.8rem;
}

.plugin-ui--auto .plugin-ui-input {
  border: 1px solid rgba(var(--v-theme-on-surface), 0.25);
  background: rgba(var(--v-theme-surface), 0.9);
  color: rgb(var(--v-theme-on-surface));
}

.plugin-ui--dark .plugin-ui-input {
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.18);
  background: rgba(var(--v-theme-shadow), 0.45);
  color: rgb(var(--v-theme-on-dark-surface));
}

.plugin-ui-checkbox-row {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.plugin-ui--auto .plugin-ui-checkbox-row {
  color: rgb(var(--v-theme-on-surface));
}

.plugin-ui--dark .plugin-ui-checkbox-row {
  color: rgb(var(--v-theme-on-dark-surface));
}

.plugin-ui-help {
  font-size: 0.8rem;
  opacity: 0.75;
}

.plugin-ui--auto .plugin-ui-help,
.plugin-ui--auto .plugin-ui-description,
.plugin-ui--auto .plugin-ui-note {
  color: rgba(var(--v-theme-on-surface), 0.78);
}

.plugin-ui--dark .plugin-ui-help,
.plugin-ui--dark .plugin-ui-description,
.plugin-ui--dark .plugin-ui-note {
  color: rgba(var(--v-theme-on-dark-surface), 0.72);
}
</style>
