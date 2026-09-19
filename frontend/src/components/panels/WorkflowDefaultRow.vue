<template>
  <div class="wfdef">
    <button
      class="wfdef-pin"
      type="button"
      :aria-pressed="row.pinned ? 'true' : 'false'"
      :disabled="busy"
      @click="emit('toggle-pin')"
    >
      <Tooltip
        :text="row.pinned ? `Unpin ${row.label}` : `Pin ${row.label} to the Run popup`"
        activator="parent"
      />
      <v-icon size="14">{{ row.pinned ? "mdi-pin" : "mdi-pin-outline" }}</v-icon>
    </button>
    <span class="wfdef-name">
      {{ row.label }}
      <!-- Every value says where it came from, always: a row that only
           annotates the edited ones leaves the reader guessing what the
           silent ones are, which is the question this line exists for. -->
      <span class="wfdef-prov">{{ PROVENANCE[row.provenance] || row.provenance }}</span>
    </span>
    <span class="wfdef-value">
      <span class="wfdef-text num">{{ row.value }}</span>
      <button
        v-if="row.provenance === 'edited'"
        class="wfdef-reset"
        type="button"
        :disabled="busy"
        @click="emit('reset')"
      >
        <Tooltip
          :text="`Put ${row.label} back to what your pictures say`"
          activator="parent"
        />
        <v-icon size="14">mdi-restore</v-icon>
      </button>
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

import { VIcon } from "vuetify/components";

import Tooltip from "../widgets/Tooltip.vue";

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
.wfdef {
  display: grid;
  grid-template-columns: 20px 1fr minmax(0, 96px);
  align-items: center;
  gap: var(--space-2);
}

.wfdef-pin {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
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
  display: inline-flex;
  flex: none;
  align-items: center;
  padding: 0;
  border: 0;
  background: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
}
</style>
