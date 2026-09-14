<template>
  <label class="app-input">
    <FieldLabel v-if="label">{{ label }}</FieldLabel>
    <div
      class="app-input__wrap"
      :class="{
        'app-input__wrap--error': !!error,
        'app-input__wrap--mono': mono,
      }"
    >
      <v-icon v-if="icon" size="18" class="app-input__icon"
        >mdi-{{ icon }}</v-icon
      >
      <input
        ref="inputRef"
        class="app-input__field"
        :type="type"
        :value="modelValue"
        :placeholder="placeholder"
        :disabled="disabled"
        :readonly="readonly"
        :autofocus="autofocus"
        :min="min === '' ? undefined : min"
        :max="max === '' ? undefined : max"
        :aria-label="ariaLabel || undefined"
        :aria-invalid="error ? 'true' : undefined"
        @input="emit('update:modelValue', $event.target.value)"
        @blur="emit('blur')"
        @keydown.enter="emit('enter')"
      />
    </div>
    <span v-if="typeof error === 'string' && error" class="app-input__error">{{
      error
    }}</span>
  </label>
</template>

<script setup>
/**
 * The text field, on the control tier with AppButton: --control-h at
 * --radius-sm, so a field and the button beside it are one height by
 * construction. The label is the caption above, never inside the box
 * (docs/design/buttons.md, "Fields").
 */
import { onMounted, ref } from "vue";
import { VIcon } from "vuetify/components";
import FieldLabel from "./FieldLabel.vue";

const props = defineProps({
  modelValue: { type: [String, Number, null], default: "" },
  label: { type: String, default: "" },
  placeholder: { type: String, default: "" },
  icon: { type: String, default: "" },
  type: { type: String, default: "text" },
  disabled: { type: Boolean, default: false },
  readonly: { type: Boolean, default: false },
  autofocus: { type: Boolean, default: false },
  // A path, hash, port or id is read character by character, so it takes the
  // mono face. The height never changes with the kind of value.
  mono: { type: Boolean, default: false },
  // true marks the field; a string also renders as the message below it.
  error: { type: [Boolean, String], default: false },
  // The accessible name for a field with no visible label.
  ariaLabel: { type: String, default: "" },
  min: { type: [String, Number], default: "" },
  max: { type: [String, Number], default: "" },
});

const emit = defineEmits(["update:modelValue", "enter", "blur"]);

const inputRef = ref(null);

// The native attribute only fires on page load, not when a dialog mounts.
onMounted(() => {
  if (props.autofocus) inputRef.value?.focus();
});

defineExpose({
  focus: () => inputRef.value?.focus(),
  select: () => inputRef.value?.select(),
});
</script>

<style scoped>
.app-input {
  display: block;
}

.app-input__wrap {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  padding: 0 var(--space-3);
  height: var(--control-h);
}

.app-input__wrap--error {
  border-color: rgb(var(--v-theme-error));
}

/* The field inside is borderless and outline-free, so the wrap draws the
   global ink ring for it. */
.app-input__wrap:has(.app-input__field:focus-visible) {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
}

.app-input__icon {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), 0.5);
}

.app-input__field {
  flex: 1;
  min-width: 0;
  height: 100%;
  border: none;
  outline: none;
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
}

.app-input__wrap--mono .app-input__field {
  font-family: var(--font-mono);
  font-size: var(--text-sm);
}

.app-input__field::placeholder {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.app-input__field:disabled {
  color: rgba(var(--v-theme-on-surface), 0.45);
}

.app-input__error {
  display: block;
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: rgb(var(--v-theme-error));
}
</style>
