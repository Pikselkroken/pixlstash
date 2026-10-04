<template>
  <div :class="['wfdef', { 'wfdef--fixed': fixed }]">
    <span class="wfdef-name">
      {{ row.label }}
      <!-- Only the exception is marked (#1653): where the computed values
           come from is drawn once, in the tab's head, and "Yours" is the one
           provenance the owner can act on (↺). -->
      <span v-if="row.provenance === 'edited'" class="wfdef-prov">Yours</span>
    </span>
    <span class="wfdef-value">
      <!-- Set each run: the box is the field, so it edits the workflow's
           value, which locking then keeps. Fixed: read-only text. -->
      <span v-if="fixed" class="wfdef-text num">{{ row.value }}</span>
      <input
        v-else
        ref="input"
        class="wfdef-text wfdef-input num"
        :value="String(row.value)"
        :aria-label="row.label"
        :disabled="busy"
        data-testid="wfdef-input"
        @change="onChange"
        @keydown.stop
      />
      <AppButton
        v-if="row.provenance === 'edited'"
        class="wfdef-reset"
        size="sm"
        variant="ghost"
        icon-only
        icon-left="restore"
        :tooltip="`Put ${row.label} back to what your pictures say`"
        :disabled="busy"
        @click="emit('reset')"
      />
    </span>
    <!-- A toggle with one constant name, pressed when fixed. `AppButton
         icon-only` rather than a hand-rolled button: it is what makes the
         tooltip the accessible NAME (docs/design/buttons.md). Open: a quiet
         ghost lock. Fixed: the closed lock in a bordered button, so the one
         control on a row whose value has lost its box still reads as one. -->
    <AppButton
      class="wfdef-lock"
      size="sm"
      :variant="fixed ? 'secondary' : 'ghost'"
      icon-only
      :icon-left="fixed ? 'lock-outline' : 'lock-open-variant-outline'"
      :tooltip="`Fix ${row.label} for this workflow`"
      :aria-pressed="fixed ? 'true' : 'false'"
      :disabled="busy"
      data-testid="wfdef-lock"
      @click="emit('toggle-pin')"
    />
  </div>
</template>

<script setup>
// One parameter of the Workflow tab's Parameters panel: its name, its value,
// and the lock that says whether the Run form asks for it each run (open) or
// every run uses the workflow's value (fixed). `row.pinned` is "set each run";
// the stored pin list keeps its shape.

import { computed, ref, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";

const props = defineProps({
  /** `{label, slot_label, input_name, value, provenance, pinned}`. */
  row: { type: Object, required: true },
  /** A write for this row is out; both its controls wait for it. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["toggle-pin", "reset", "edit"]);

const fixed = computed(() => !props.row.pinned);
const input = ref(null);

// A write ending shows what was stored: the new value, or on a failed or
// dropped write the old one, which no re-render would otherwise put back.
watch(
  () => props.busy,
  (busy) => {
    if (!busy && input.value) input.value.value = String(props.row.value);
  },
);

/**
 * The typed text as the value's own type, or undefined when it is not one:
 * a number field takes a number, a boolean `true`/`false`, a string anything
 * but empty.
 */
function parse(text) {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  if (typeof props.row.value === "number") {
    const number = Number(trimmed);
    return Number.isFinite(number) ? number : undefined;
  }
  if (typeof props.row.value === "boolean") {
    return trimmed === "true" ? true : trimmed === "false" ? false : undefined;
  }
  return trimmed;
}

/** On blur or Enter: a new value is emitted, anything else is put back. */
function onChange(event) {
  const value = parse(event.target.value);
  if (value === undefined || value === props.row.value) {
    event.target.value = String(props.row.value);
    return;
  }
  emit("edit", value);
}
</script>

<style scoped>
/* Name and value take equal halves, so the value grows with the rail instead
   of sitting in a fixed track; the lock is one `--control-h-sm` square. */
.wfdef {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) var(--control-h-sm);
  align-items: center;
  gap: var(--space-3);
}

.wfdef-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  overflow-wrap: anywhere;
}

/* "Yours": the one exception mark, a step above the label through weight and
   full ink, never hue (design §2.1, shared with the default-recipe rows). */
.wfdef-prov {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
}

.wfdef-value {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
  height: var(--control-h);
  padding: 0 var(--space-3);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.06);
  font-size: var(--text-sm);
}

/* Fixed: the value is not asked for, so it loses its field box and drops to
   secondary ink. */
.wfdef--fixed .wfdef-value {
  padding-left: 0;
  border-color: transparent;
  background: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfdef-value:focus-within {
  box-shadow: var(--focus-ring-inset);
}

/* Bare: the value cell above is the field's box. */
.wfdef-input {
  padding: 0;
  border: 0;
  outline: none;
  background: none;
  color: inherit;
  font: inherit;
}

.wfdef-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* `--control-h-sm` is the WCAG 2.2 pointer floor the token ramp names, and
   `AppButton size="sm"` is already that tall; this only squares it. */
.wfdef-reset,
.wfdef-lock {
  flex: none;
  width: var(--control-h-sm);
}

.wfdef-lock[aria-pressed="false"] {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
