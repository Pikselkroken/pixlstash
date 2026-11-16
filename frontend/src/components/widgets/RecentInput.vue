<template>
  <label class="recent-input">
    <FieldLabel>{{ label }}</FieldLabel>
    <BaseModelInput
      :model-value="modelValue"
      class="recent-input__field"
      :options="options"
      :list-label="`Recent: ${label}`"
      :placeholder="placeholder"
      @update:model-value="emit('update:modelValue', $event)"
      @mousedown="onMousedown"
      @focus="$event.target.select()"
      @mouseup="onMouseup"
    />
  </label>
</template>

<script setup>
/**
 * A text field that offers what was typed in it before: AppInput's look, with
 * BaseModelInput's completion menu over `options`, newest first.
 *
 * Its value is selected on entering it, so a remembered value is replaced by
 * the first key typed and kept by none. A click would put the caret back
 * where it landed on mouseup, so the mouseup of the click that focused it is
 * swallowed; later clicks place the caret as usual.
 */
import BaseModelInput from "./BaseModelInput.vue";
import FieldLabel from "./FieldLabel.vue";

defineProps({
  modelValue: { type: String, default: "" },
  label: { type: String, required: true },
  placeholder: { type: String, default: "" },
  options: { type: Array, default: () => [] },
});
const emit = defineEmits(["update:modelValue"]);

let focusingClick = false;

function onMousedown(event) {
  focusingClick = document.activeElement !== event.target;
}

function onMouseup(event) {
  if (focusingClick) event.preventDefault();
  focusingClick = false;
}
</script>

<style scoped>
.recent-input {
  display: block;
}

/* `:deep()`: BaseModelInput has two roots, so this scope never reaches its
   input. AppInput's box and type, drawn on the input itself. */
.recent-input :deep(.recent-input__field) {
  display: block;
  width: 100%;
  height: var(--control-h);
  padding: 0 var(--space-3);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  outline: none;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-base);
}

.recent-input :deep(.recent-input__field:focus-visible) {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: var(--focus-offset);
  box-shadow: var(--focus-glow);
}

.recent-input :deep(.recent-input__field::placeholder) {
  color: rgba(var(--v-theme-on-surface), 0.45);
}
</style>
