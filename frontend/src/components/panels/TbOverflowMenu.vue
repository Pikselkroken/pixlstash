<template>
  <div ref="wrapEl" class="tbo-wrap" @keydown.esc.stop.prevent="close()">
    <button
      ref="triggerEl"
      type="button"
      class="bar-btn bar-btn--icon tbo-trigger"
      :class="{ 'bar-btn--open': open }"
      title="More actions"
      aria-haspopup="true"
      :aria-expanded="open ? 'true' : 'false'"
      @click="toggle"
    >
      <v-icon size="20">mdi-dots-horizontal</v-icon>
    </button>
    <div
      v-if="open"
      class="tbm tbo-panel"
      :class="`tbo-panel--${align}`"
      role="menu"
      aria-label="More actions"
    >
      <slot :close="close" />
    </div>
  </div>
</template>

<script setup>
// The toolbar's ⋯ overflow: where foldable controls land when the bar runs
// out of width.
//
// The panel is IN-PLACE (absolute inside the bar, the dq-tier-wrap pattern),
// deliberately NOT a teleported v-menu: teleport escapes the container, and
// the rows rely on the bar's `@container toolbar (…)` queries to appear
// exactly when their toolbar button folds. The fold is CSS both ways - every
// foldable control exists as a bar button AND as a slotted row with the same
// v-if, and the container queries flip which one is visible - so there is no
// ResizeObserver and no JS measurement anywhere.
//
// The trigger itself stays hidden until the host's first fold step (the host
// owns that rule; it knows its own ladder). Escape closes back to the
// trigger, a pointer press outside dismisses, and the rows use the global
// `.tbm-action` recipe.

import { onBeforeUnmount, onMounted, ref } from "vue";

defineProps({
  /**
   * Which edge the panel hangs from. `end` (the default) opens leftward and
   * suits a trigger near the bar's right side; `start` opens rightward, which
   * is what a trigger sitting near the LEFT edge needs - a 220px panel
   * right-anchored to it would open off-screen (the Duplicates bar's ⋯,
   * amendment #4).
   */
  align: {
    type: String,
    default: "end",
    validator: (value) => ["start", "end"].includes(value),
  },
});

const open = ref(false);
const wrapEl = ref(null);
const triggerEl = ref(null);

function toggle() {
  open.value ? close({ focusTrigger: false }) : (open.value = true);
}

/**
 * Dismiss the panel. Escape (and a row's own close) return focus to the
 * trigger so the keyboard never has to hunt for where it went; the trigger's
 * own toggle click keeps focus where the click put it.
 */
function close({ focusTrigger = true } = {}) {
  if (!open.value) return;
  open.value = false;
  if (focusTrigger) triggerEl.value?.focus?.();
}

/** A pointer press anywhere outside the wrap dismisses the panel. */
function onDocumentPointerDown(event) {
  if (!open.value) return;
  if (wrapEl.value?.contains?.(event.target)) return;
  open.value = false;
}

onMounted(() => {
  if (typeof document === "undefined") return;
  document.addEventListener("mousedown", onDocumentPointerDown);
});

onBeforeUnmount(() => {
  if (typeof document === "undefined") return;
  document.removeEventListener("mousedown", onDocumentPointerDown);
});

/**
 * The trigger element, for a host whose folded row opens a DIALOG rather than
 * toggling something: the dialog has to be told where to put focus back, and
 * below the fold the bar button that normally answers for that is
 * `display: none`, which cannot take focus.
 *
 * @returns {HTMLElement|null}
 */
function trigger() {
  return triggerEl.value ?? null;
}

/**
 * Whether the panel is showing. A host whose surface owns the keyboard needs
 * this to tell "a key pressed inside my open menu" from "a key pressed with
 * the closed trigger focused" - the second one still belongs to the host.
 *
 * @returns {boolean}
 */
function isOpen() {
  return open.value;
}

defineExpose({ close, isOpen, trigger });
</script>

<style scoped>
.tbo-wrap {
  position: relative;
  display: none;
}

/* The trigger appears with the host's FIRST fold step, and the host owns that
   rule from its own ladder (`.tb-overflow` at selbar ≤700, `.dq-overflow` at
   dqbar ≤1180): only the host knows when its first control folds. The rule
   lives there rather than here because the breakpoint differs per bar. */

/* Mirrors `.bar-btn` from the hosts' bars (their scoped styles cannot cross
   the component boundary - same note as UndoControl carries). */
.tbo-trigger {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  flex-shrink: 0;
  position: relative;
  color: rgb(var(--v-theme-toolbar-text));
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  box-sizing: border-box;
  font-family: inherit;
}

.tbo-trigger:hover {
  background: rgba(var(--v-theme-toolbar-text), 0.1);
}

.bar-btn--open {
  border-color: rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-panel));
}

/* The in-place panel: the dq-tier-wrap positioning, the shared .tbm chrome. */
.tbo-panel {
  position: absolute;
  top: calc(100% + var(--space-2));
  z-index: var(--z-dropdown);
  min-width: 220px;
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

/* Which edge the panel hangs from; see the `align` prop. */
.tbo-panel--end {
  right: 0;
}

.tbo-panel--start {
  left: 0;
}
</style>
