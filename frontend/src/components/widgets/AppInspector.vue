<template>
  <aside
    class="inspector"
    :class="{ 'inspector--collapsed': !open }"
    :aria-label="label"
    :aria-hidden="open ? undefined : 'true'"
  >
    <!-- The inspector: one component for every right-edge detail pane (shell
         contract rule 8). The grid's stats, the workflow shelf's inspector
         and, as they are built, a model's detail and the duplicates evidence
         pane are this box with different content. The box, the collapse, the
         tab band and the key/value grid live here; a pane owns only what it
         says. -->
    <div v-if="open" class="inspector-content">
      <!-- The tab band is pinned to the toolbar height, so the sidebar tabs,
           the toolbar and this band share one bottom rule across the window.
           Tabs name the SUBJECT of the pane, never the view. -->
      <div v-if="tabs.length" class="inspector-tabs">
        <button
          v-for="tab in tabs"
          :key="tab.value"
          class="inspector-tab"
          :class="{ 'inspector-tab--active': tab.value === modelValue }"
          type="button"
          :aria-pressed="tab.value === modelValue ? 'true' : 'false'"
          :disabled="tab.disabled"
          @click="emit('update:modelValue', tab.value)"
        >
          <Tooltip :text="tab.tooltip || ''" activator="parent" />
          <slot name="tab" :tab="tab">
            <v-icon v-if="tab.icon" size="12">{{ tab.icon }}</v-icon>
            {{ tab.label }}
          </slot>
        </button>
      </div>
      <div class="inspector-body">
        <slot />
      </div>
    </div>
  </aside>
</template>

<script setup>
import { VIcon } from "vuetify/components";
import Tooltip from "./Tooltip.vue";

defineProps({
  /** Open or collapsed to zero width. The pane keeps its place in the row. */
  open: { type: Boolean, default: true },
  /** The accessible name of the pane ("Stats", "Inspector"). */
  label: { type: String, required: true },
  /** `[{ value, label, icon?, disabled?, tooltip? }]`. */
  tabs: { type: Array, default: () => [] },
  /** The active tab's `value`. */
  modelValue: { type: String, default: "" },
});

const emit = defineEmits(["update:modelValue"]);
</script>

<style scoped>
.inspector {
  position: relative;
  width: var(--stats-panel-w);
  min-width: var(--stats-panel-w);
  max-width: var(--stats-panel-w);
  height: 100%;
  display: flex;
  flex-shrink: 0;
  /* Mirrors the sidebar's border-right, so both rails present the same edge
     onto the canvas. */
  border-left: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-sidebar));
  transition:
    width var(--dur-1) var(--ease-standard),
    min-width var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
  overflow: hidden;
}

/* Docked at every width and never taken out of flow: the toolbar ends where the
   pane begins, so the pane can never cover the toggle that closes it. A zero
   width pane must not let its content bleed over the canvas. */
.inspector--collapsed {
  width: 0;
  min-width: 0;
  max-width: 0;
  border-left-color: transparent;
  background: transparent;
}

.inspector-content {
  flex: 1;
  min-width: 0;
  overflow-y: auto;
  overflow-x: hidden;
  display: flex;
  flex-direction: column;
}

.inspector-tabs {
  display: flex;
  height: var(--toolbar-height);
  flex-shrink: 0;
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

/* An inspector tab speaks the section label's voice. Olive marks, words stay
   ink: the underline carries the selection. */
.inspector-tab {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  height: 100%;
  padding: 0 var(--space-3);
  background: none;
  border: 0;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  font-family: inherit;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  cursor: pointer;
  transition:
    color var(--dur-1) var(--ease-standard),
    background var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
}

.inspector-tab:hover:not(:disabled) {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.inspector-tab--active {
  color: var(--active-text);
  border-bottom-color: var(--active-bar);
}

/* Dimmed rather than removed: the pane keeps its shape. */
.inspector-tab:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}

.inspector-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  /* --space-3 inline, not the kit's --space-4: the stats charts are drawn at a
     fixed 260px and must still fit beside a classic scrollbar. */
  padding: var(--space-4) var(--space-3) var(--space-5);
}

/* A group inside the pane, named by the shared `.section-label`. Groups are
   told apart by space, not by rules: a label is enough to start one. */
.inspector-body :deep(.inspector-section) {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.inspector-body :deep(.inspector-section + .inspector-section) {
  padding-top: var(--space-4);
}

/* Values: a two-column grid, name over value. */
.inspector-body :deep(.inspector-kv) {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4) var(--space-3);
  margin: 0;
}

.inspector-body :deep(.inspector-kv dt) {
  margin: 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.inspector-body :deep(.inspector-kv dd) {
  margin: 0;
  font-size: var(--text-sm);
  font-variant-numeric: tabular-nums;
}
</style>
