<template>
  <section class="mrs" :aria-label="title">
    <div class="mrs-head">
      <button
        class="mrs-toggle"
        type="button"
        :aria-expanded="isOpen ? 'true' : 'false'"
        :aria-controls="bodyId"
        :data-testid="`mrs-toggle-${sectionKey}`"
        @click="toggle"
      >
        <v-icon
          class="mrs-chevron"
          :class="{ 'mrs-chevron--shut': !isOpen }"
          size="12"
          aria-hidden="true"
          >mdi-chevron-down</v-icon
        >
        <span class="section-label mrs-label">{{ title }}</span>
        <span v-if="count != null" class="mrs-count num">{{ count }}</span>
      </button>
      <div v-if="$slots.end" class="mrs-end">
        <slot name="end" />
      </div>
    </div>
    <div v-if="isOpen" :id="bodyId" class="mrs-body">
      <slot />
    </div>
  </section>
</template>

<script setup>
/**
 * One collapsible section of the Models rail's inspector (#2168): a chevron,
 * the uppercase label, a count, and the section's verb at the right edge.
 *
 * Open or shut is remembered per `sectionKey` by the rail that holds the
 * sections, so a set's *Models* stays shut when the reader moves to another
 * set. It is injected, not passed, so every section reads the same memory.
 */
import { computed, inject } from "vue";
import { VIcon } from "vuetify/components";

import { RAIL_SECTIONS } from "../../utils/modelsInspector";

const props = defineProps({
  /** The key the open or shut state is remembered under. */
  sectionKey: { type: String, required: true },
  /** The uppercase label, as the design writes it. */
  title: { type: String, required: true },
  /** The count drawn after the label, when there is one. */
  count: { type: [Number, String], default: null },
});

const sections = inject(RAIL_SECTIONS, {
  isShut: () => false,
  toggle: () => {},
});

const isOpen = computed(() => !sections.isShut(props.sectionKey));
const bodyId = computed(() => `mrs-body-${props.sectionKey}`);

function toggle() {
  sections.toggle(props.sectionKey);
}
</script>

<style scoped>
.mrs {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) 0;
  border-top: 1px solid rgba(var(--v-theme-on-surface), 0.12);
}

.mrs-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 24px;
}

.mrs-toggle {
  display: inline-flex;
  flex: 1;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  padding: 0;
  border: 0;
  background: none;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.mrs-chevron {
  transition: transform var(--dur-1) var(--ease-standard);
}

.mrs-chevron--shut {
  transform: rotate(-90deg);
}

.mrs-label {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mrs-count {
  font-size: var(--text-2xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
}

.mrs-end {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.mrs-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-size: var(--text-xs);
}
</style>

<style>
/* The primitives every rail section body draws with (set, model). Unscoped so
   the two bodies share one set of rows, facts and links. */
.mrs-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 24px;
}

.mrs-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: rgb(var(--v-theme-on-surface));
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mrs-warn {
  color: rgb(var(--v-theme-warning));
}

.mrs-num {
  flex-shrink: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-variant-numeric: tabular-nums;
}

.mrs-hint {
  margin: 0;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
  font-size: var(--text-xs);
}

.mrs-link {
  color: rgb(var(--v-theme-on-surface));
  text-decoration: none;
}

.mrs-link:hover {
  text-decoration: underline;
}

.mrs-link-btn,
.mrs-icon {
  border: 0;
  background: none;
  color: rgb(var(--v-theme-on-surface));
  font: inherit;
  font-size: var(--text-xs);
  cursor: pointer;
}

.mrs-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mrs-facts {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
}

.mrs-fact {
  display: flex;
  gap: var(--space-3);
}

.mrs-fact dt {
  width: 80px;
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mrs-fact dd {
  margin: 0;
  color: rgb(var(--v-theme-on-surface));
}
</style>
