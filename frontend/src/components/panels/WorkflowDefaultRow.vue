<template>
  <div class="wfdef">
    <!-- `AppButton icon-only` rather than a hand-rolled button: it is what
         makes the tooltip the accessible NAME (docs/design/buttons.md). A
         bare button holding a glyph and a `Tooltip` gets `aria-describedby`,
         which is a description and not a name, so a screen reader announces
         "button, pressed" and nothing else. -->
    <AppButton
      class="wfdef-pin"
      size="sm"
      variant="ghost"
      icon-only
      :icon-left="row.pinned ? 'pin' : 'pin-outline'"
      :tooltip="
        row.pinned ? `Unpin ${row.label}` : `Pin ${row.label} to the Run popup`
      "
      :aria-pressed="row.pinned ? 'true' : 'false'"
      :disabled="busy"
      @click="emit('toggle-pin')"
    />
    <span class="wfdef-name">
      {{ row.label }}
      <!-- Every value says where it came from, always: a row that only
           annotates the edited ones leaves the reader guessing what the
           silent ones are, which is the question this line exists for. -->
      <span class="wfdef-prov">{{ PROVENANCE[row.provenance] || row.provenance }}</span>
    </span>
    <span class="wfdef-value">
      <span class="wfdef-text num">{{ row.value }}</span>
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
  </div>
</template>

<script setup>
// One parameter of a workflow's Defaults section (implementation plan §F3):
// the pin, what it is called and where its value came from, and the value.
//
// Split out of `WorkflowTab.vue` because the same row is drawn twice — once
// above "All N parameters" and once inside it — and a copy of it would be two
// places for the reset to go wrong.

import AppButton from "../widgets/AppButton.vue";

/** What the server's three provenances are called on screen. */
const PROVENANCE = {
  best: "from your best pictures",
  all: "from its pictures",
  edited: "edited by you",
};

defineProps({
  /** `{label, slot_label, input_name, value, provenance, pinned}`. */
  row: { type: Object, required: true },
  /** A write for this row is out; both its controls wait for it. */
  busy: { type: Boolean, default: false },
});

const emit = defineEmits(["toggle-pin", "reset"]);
</script>

<style scoped>
/* Local track widths, like the tab's own 96px label column: the pin is one
   `--control-h-sm` control and the value column is the same 96px, and no
   other pane pairs the three, so neither is a token. */
.wfdef {
  display: grid;
  grid-template-columns: var(--control-h-sm) 1fr minmax(0, 96px);
  align-items: center;
  gap: var(--space-2);
}

/* `--control-h-sm` is the WCAG 2.2 pointer floor the token ramp names, and
   `AppButton size="sm"` is already that tall; this only squares it. */
.wfdef-pin {
  width: var(--control-h-sm);
}

.wfdef-pin[aria-pressed="true"] {
  color: rgb(var(--v-theme-on-surface));
}

.wfdef-name {
  display: flex;
  flex-direction: column;
  min-width: 0;
  font-size: var(--text-xs);
}

.wfdef-prov {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.wfdef-value {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
  height: var(--control-h);
  padding: 0 var(--space-2);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-on-surface), 0.04);
  font-size: var(--text-sm);
}

.wfdef-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wfdef-reset {
  flex: none;
  width: var(--control-h-sm);
}
</style>
