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
      <v-icon size="16" class="optrow__radio">
        {{
          o.id === modelValue ? "mdi-radiobox-marked" : "mdi-radiobox-blank"
        }}
      </v-icon>
      <v-icon v-if="o.icon" size="16" class="optrow__icon">{{
        iconName(o.icon)
      }}</v-icon>
      <span class="optrow__label">{{ o.label }}</span>
      <slot name="meta" :option="o" />
    </button>
  </div>
</template>

<script setup>
/**
 * Pick one from a long or server-supplied list: sort by, and the filter menu's
 * pick-one lists. Two to five short options are Segmented.
 *
 * NO FILL. A fill means "press me", so every row carries an aligned radio
 * indicator and the selected one is marked in olive. The medium-weight label
 * remains a second cue: olive marks, words stay text. Hover is the ink wash,
 * which follows the pointer, not the value.
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

// `pick` fires for a click (pointer, Enter or Space) and not for an arrow, so a
// menu can close on a deliberate choice and stay open while arrows browse.
const emit = defineEmits(["update:modelValue", "pick"]);

const tabStop = computed(() => tabStopId(props.options, props.modelValue));

function iconName(icon) {
  return icon.startsWith("mdi-") ? icon : `mdi-${icon}`;
}

function select(option) {
  if (props.disabled || option.disabled) return;
  if (option.id !== props.modelValue) emit("update:modelValue", option.id);
  emit("pick", option.id);
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

.optrow__radio {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

/* One olive mark per row: the radio. The option's own icon stays ink. */
.optrow--on .optrow__radio {
  color: var(--selected-ink);
}

.optrow__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
