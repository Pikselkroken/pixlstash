<template>
  <div
    ref="wrapEl"
    class="tbo-wrap"
    :class="{ 'tbo-wrap--attention': attention }"
    @keydown.esc.stop.prevent="close()"
  >
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
      <!-- The app-wide activity light SURVIVES the stats toggle folding in
           here: shown only while the toolbar container is narrow enough that
           the stats button itself is hidden (the shared ≤600 step), so the
           dot is never displayed twice. -->
      <span v-if="attention" class="tbo-activity" aria-hidden="true"></span>
    </button>
    <div
      v-if="open"
      class="tbm tbo-panel"
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
// exactly when their toolbar button folds. The fold is CSS both ways — every
// foldable control exists as a bar button AND as a slotted row with the same
// v-if, and the container queries flip which one is visible — so there is no
// ResizeObserver and no JS measurement anywhere.
//
// The trigger itself stays hidden until the host's first fold step (the host
// owns that rule; it knows its own ladder). Escape closes back to the
// trigger, a pointer press outside dismisses, and the rows use the global
// `.tbm-action` recipe.

import { onBeforeUnmount, onMounted, ref } from "vue";

defineProps({
  // The app-wide task-activity light, shown while the stats toggle is folded
  // in here. The host passes `tasksStore.hasActiveTasks`; the CSS decides
  // WHEN it may show (only under the shared ≤600 toolbar step).
  attention: { type: Boolean, default: false },
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

defineExpose({ close });
</script>

<style scoped>
.tbo-wrap {
  position: relative;
  display: none;
}

/* The trigger appears with the host's FIRST fold step; the host raises this
   flag class from its own ladder (`selbar ≤700`, `dqbar ≤720`), because only
   the host knows when its first control folds. */
.tbo-wrap--folding {
  display: flex;
}

/* Mirrors `.bar-btn` from the hosts' bars (their scoped styles cannot cross
   the component boundary — same note as UndoControl carries). */
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
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  box-sizing: border-box;
  font-family: inherit;
  cursor: pointer;
}

.tbo-trigger:hover {
  background: rgba(var(--v-theme-toolbar-text), 0.1);
}

.tbo-trigger:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.bar-btn--open {
  border-color: rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-panel));
}

/* The in-place panel: the dq-tier-wrap positioning, the shared .tbm chrome. */
.tbo-panel {
  position: absolute;
  top: calc(100% + var(--space-2));
  right: 0;
  z-index: var(--z-dropdown);
  min-width: 220px;
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

/* The activity light, identical values to TbGlobalActions' — scoped styles
   cannot share keyframes across components, so the recipe is repeated, not
   reinvented. Hidden until the stats toggle has actually folded (the shared
   ≤600 toolbar step): above that width the dot lives on the stats button and
   must never show twice. */
.tbo-activity {
  display: none;
  position: absolute;
  top: 7px;
  right: 7px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 5px rgba(var(--v-theme-primary), 0.7);
  animation: tbo-attention-pulse 1.4s ease-in-out infinite;
  pointer-events: none;
}

@container toolbar (max-width: 600px) {
  .tbo-activity {
    display: block;
  }
}

@keyframes tbo-attention-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.4;
    transform: scale(0.7);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tbo-activity {
    animation: none;
  }
}
</style>
