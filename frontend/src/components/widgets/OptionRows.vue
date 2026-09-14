<template>
  <div
    role="radiogroup"
    class="optrows"
    :class="{ 'optrows--2col': columns === 2 }"
    :aria-label="ariaLabel || undefined"
    @keydown="onKeydown"
  >
    <button
      v-for="o in options"
      :key="String(o.id)"
      type="button"
      role="radio"
      class="optrow"
      :class="{ 'optrow--on': o.id === modelValue }"
      :aria-checked="o.id === modelValue ? 'true' : 'false'"
      :tabindex="o.id === tabStop ? 0 : -1"
      :disabled="disabled || o.disabled"
      :data-testid="o.testid"
      @click="select(o)"
    >
      <v-icon v-if="o.icon" size="16" class="optrow__icon">{{
        iconName(o.icon)
      }}</v-icon>
      <span class="optrow__label">{{ o.label }}</span>
      <slot name="meta" :option="o" />
      <v-icon v-if="o.id === modelValue" size="16" class="optrow__check"
        >mdi-check</v-icon
      >
    </button>
  </div>
</template>

<script setup>
/**
 * Pick one from a long or server-supplied list: sort by, and only sort by
 * (docs/design/buttons.md). Two to five short options are Segmented.
 *
 * NO FILL. A fill means "press me", so the selected row carries a trailing
 * olive check and a medium-weight label in ink: olive marks, words stay
 * text. Hover is the ink wash, which follows the pointer, not the value.
 */
import { computed } from "vue";
import { VIcon } from "vuetify/components";
import { arrowStep, tabStopId } from "../../utils/radioGroup.js";

const props = defineProps({
  // [{ id, label, icon?, disabled?, testid? }]
  options: { type: Array, required: true },
  modelValue: { type: [String, Number, Boolean, null], default: null },
  columns: { type: Number, default: 1 },
  disabled: { type: Boolean, default: false },
  ariaLabel: { type: String, default: "" },
});

const emit = defineEmits(["update:modelValue"]);

const tabStop = computed(() => tabStopId(props.options, props.modelValue));

function iconName(icon) {
  return icon.startsWith("mdi-") ? icon : `mdi-${icon}`;
}

function select(option) {
  if (props.disabled || option.disabled) return;
  if (option.id !== props.modelValue) emit("update:modelValue", option.id);
}

function onKeydown(event) {
  if (props.disabled) return;
  const id = arrowStep(event, props.options, props.modelValue);
  if (id !== undefined && id !== props.modelValue) {
    emit("update:modelValue", id);
  }
}
</script>

<style scoped>
.optrows {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.optrows--2col {
  display: grid;
  grid-template-columns: 1fr 1fr;
}

.optrow {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  min-width: 0;
  height: var(--control-h);
  padding: 0 var(--space-3);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-regular);
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.optrow:not(:disabled):hover {
  background: var(--hover-wash);
}

.optrow:disabled {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

/* Weight is the second cue, so the answer survives without colour. */
.optrow--on {
  font-weight: var(--weight-medium);
}

.optrow__icon {
  flex-shrink: 0;
  opacity: 0.55;
}

.optrow--on .optrow__icon,
.optrow:not(:disabled):hover .optrow__icon {
  opacity: 1;
}

.optrow--on .optrow__icon,
.optrow__check {
  color: var(--selected-ink);
}

.optrow__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.optrow__check {
  flex-shrink: 0;
}
</style>
