<template>
  <div :class="['wfdef', { 'wfdef--fixed': fixed, 'wfdef--choice': choosing }]">
    <span class="wfdef-name">
      {{ name }}
      <!-- The setting alone fits the half rail; its node (which of two
           settings of one name this is) and its whole name are the tip. -->
      <Tooltip v-if="tip" :text="tip" activator="parent" />
      <!-- Only the exception is marked (#1653): where the computed values
           come from is drawn once, in the tab's head, and "Yours" is the one
           provenance the owner can act on (↺). -->
      <span v-if="row.provenance === 'edited'" class="wfdef-prov">Yours</span>
    </span>
    <span class="wfdef-value">
      <!-- Set each run: the box is the field, so it edits the workflow's
           value, which locking then keeps. Fixed: read-only text. A value
           ComfyUI lists the choices of is picked from them. -->
      <span v-if="fixed" class="wfdef-text num">{{ row.value }}</span>
      <AppSelect
        v-else-if="choosing"
        class="wfdef-select"
        :model-value="String(row.value)"
        :label="fieldName"
        hide-label
        :options="options"
        :disabled="busy"
        data-testid="wfdef-select"
        @update:model-value="(value) => emit('edit', parse(String(value)))"
      />
      <input
        v-else
        class="wfdef-text wfdef-input num"
        :value="String(row.value)"
        :aria-label="fieldName"
        :disabled="busy"
        data-testid="wfdef-input"
        @change="onChange"
        @keydown.stop
      />
      <!-- An exposed parameter has no computed value to go back to: clearing
           the owner's value takes the row away, and the button says so. -->
      <AppButton
        v-if="row.provenance === 'edited'"
        class="wfdef-reset"
        size="sm"
        variant="ghost"
        icon-only
        :icon-left="row.exposed ? 'close' : 'restore'"
        :tooltip="
          row.exposed
            ? `Remove ${row.label} from the parameters`
            : `Put ${row.label} back to what your pictures say`
        "
        :disabled="busy"
        data-testid="wfdef-reset"
        @click="emit('reset')"
      />
    </span>
    <!-- Each run or Pinned: the pin toggle's own path, as the app's pick-one
         control. The dialog is wide enough for the full Segmented, so this is
         not a compact site (docs/design/buttons.md, "Pick one"). Segmented
         owns the roving tab stop and moves focus before it asks, so focus
         follows the arrow whenever the parent flips the row. -->
    <Segmented
      class="wfdef-seg"
      :options="segOptions"
      :model-value="fixed ? 'pinned' : 'each'"
      :aria-label="`${fieldName}: when it is set`"
      data-testid="wfdef-seg"
      @update:model-value="emit('toggle-pin')"
    />
  </div>
</template>

<script setup>
// One parameter in the Edit parameters dialog: its name, its value, and the
// Each run | Pinned switch that says whether the Run form asks for it each run
// or every run uses the workflow's value. `row.pinned` means set each run; the
// switch's Pinned side is its inverse, `fixed`.

import { computed } from "vue";

import { parameterTitle } from "../../utils/workflowPins";
import AppButton from "../widgets/AppButton.vue";
import AppSelect from "../widgets/AppSelect.vue";
import Segmented from "../widgets/Segmented.vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  /** `{label, slot_label, input_name, value, provenance, pinned, exposed, name}`. */
  row: { type: Object, required: true },
  /** What ComfyUI offers for this value, when it is a choice there. */
  options: { type: Array, default: null },
  /** The node an exposed parameter is a setting of, as the dialog named it. */
  node: { type: String, default: "" },
  /** A write for this row is out; both its controls wait for it. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["toggle-pin", "reset", "edit"]);

const name = computed(() => parameterTitle(props.row));
/** `Node · whole.name`, or "" where the row already says all of it. */
const tip = computed(() =>
  props.node || name.value !== props.row.label
    ? [props.node, props.row.label].filter(Boolean).join(" · ")
    : "",
);
/** The field's accessible name: the name on screen, then what the tip says. */
const fieldName = computed(() =>
  tip.value ? `${name.value} (${tip.value})` : name.value,
);
const fixed = computed(() => !props.row.pinned);

/** The switch's two sides; a write out for the row holds both. */
const segOptions = computed(() => [
  { id: "each", label: "Each run", disabled: props.busy, testid: "wfdef-each-run" },
  { id: "pinned", label: "Pinned", disabled: props.busy, testid: "wfdef-pinned" },
]);

const choosing = computed(() => !fixed.value && Boolean(props.options?.length));

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
   of sitting in a fixed track; the switch keeps its own column. */
.wfdef {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
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
  /* AppInput's ground and border, so the dialog's fields read as one family. */
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
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

/* A choice takes the row's whole width on a line of its own, under its name:
   half the rail clips a sampler's name to its first letters. The switch keeps
   its column, so it still lines up with the rows around it. */
.wfdef--choice {
  grid-template-columns: minmax(0, 1fr) auto;
  row-gap: var(--space-2);
}

.wfdef--choice .wfdef-seg {
  grid-area: 1 / 2;
}

/* It brings its own field box, so the cell gives up its own. */
.wfdef--choice .wfdef-value {
  grid-area: 2 / 1 / auto / -1;
  padding: 0;
  border: 0;
  background: none;
}

.wfdef-select {
  flex: 1;
  min-width: 0;
}

.wfdef-value:has(.wfdef-input:focus-visible) {
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
.wfdef-reset {
  flex: none;
  width: var(--control-h-sm);
}

/* The switch is the app's Segmented; the row only keeps it from stretching. */
.wfdef-seg {
  flex: none;
}
</style>
