<template>
  <!-- An inspector section's header, one pattern for every rail (#2161): a
       32px row, the chevron that collapses the section, the uppercase label, a
       quiet count, and the section's own verbs at the right edge. The whole
       row toggles; the chevron is the button that names it for the keyboard,
       and the verbs stop their clicks so they never toggle. -->
  <div
    class="section-head"
    :class="{ 'section-head--collapsed': collapsed }"
    @click="toggle"
  >
    <button
      type="button"
      class="section-head__chevron"
      :aria-expanded="collapsed ? 'false' : 'true'"
      :aria-label="`${collapsed ? 'Expand' : 'Collapse'} ${label}`"
      @click.stop="toggle"
    >
      <v-icon size="16">{{
        collapsed ? "mdi-chevron-right" : "mdi-chevron-down"
      }}</v-icon>
    </button>
    <span
      class="section-head__label section-label"
      :class="{ 'section-label--on-dark': onDark }"
    >
      <slot name="label">{{ label }}</slot>
    </span>
    <span v-if="count != null && count !== ''" class="section-head__count">{{
      typeof count === "number" ? count.toLocaleString() : count
    }}</span>
    <span v-if="$slots.actions" class="section-head__actions" @click.stop>
      <slot name="actions" />
    </span>
  </div>
</template>

<script setup>
const props = defineProps({
  /** The section's name, also the chevron's accessible name. */
  label: { type: String, required: true },
  /** A quiet count after the label; absent when null. */
  count: { type: [Number, String], default: null },
  collapsed: { type: Boolean, default: false },
  /** On the always-dark lightbox surface: the label takes its ink. */
  onDark: { type: Boolean, default: false },
});

const emit = defineEmits(["update:collapsed"]);

function toggle() {
  emit("update:collapsed", !props.collapsed);
}
</script>

<style scoped>
.section-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: var(--control-h-bar);
  cursor: pointer;
  user-select: none;
}

/* The ink follows the surface the section sits on (the rail's, or the
   lightbox's dark one), so one header serves both. */
.section-head__chevron {
  flex: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  margin-left: calc(-1 * var(--space-2));
  padding: 0;
  border: 0;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  opacity: var(--opacity-text-secondary);
  cursor: pointer;
}

.section-head:hover .section-head__chevron {
  opacity: 1;
}

.section-head__chevron:focus-visible {
  outline: var(--focus-width) solid var(--focus-stroke);
  outline-offset: calc(-1 * var(--focus-width));
}

.section-head__label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.section-head__count {
  flex: none;
  font-size: var(--text-2xs);
  font-variant-numeric: tabular-nums;
  opacity: var(--opacity-text-secondary);
}

.section-head__actions {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  cursor: default;
}
</style>
