<template>
  <span v-if="separator" class="bar-separator" aria-hidden="true"></span>
  <!-- ── Settings ──────────────────────────────────────────────────────── -->
  <button
    class="bar-btn bar-btn--icon"
    type="button"
    title="Settings"
    @click="emit('open-settings')"
  >
    <v-icon size="20">mdi-cog-outline</v-icon>
  </button>
  <!-- ── Stats toggle ──────────────────────────────────────────────────── -->
  <button
    class="bar-btn bar-btn--icon tb-stats-btn"
    :class="{ 'bar-btn--active': sidebarStore.statsOpen }"
    type="button"
    :title="
      tasksStore.hasActiveTasks
        ? `${tasksStore.activeCount} active task${tasksStore.activeCount === 1 ? '' : 's'} running`
        : sidebarStore.statsOpen
          ? 'Hide stats sidebar'
          : 'Show stats sidebar'
    "
    @click="sidebarStore.toggleStats()"
  >
    <v-icon size="20">mdi-chart-bar</v-icon>
    <!-- App-wide activity light: pulses whenever the task manager has any
         active work, so background tasks are visible without opening the
         stats sidebar. -->
    <span v-if="tasksStore.hasActiveTasks" class="tb-stats-activity"></span>
  </button>
</template>

<script setup>
// The app-wide chrome that must survive a change of destination: Settings and
// the stats sidebar toggle. The grid's toolbar and the duplicates queue both
// mount this SAME component, which is what keeps the pair pixel-identical in
// every view — the styles live here, not in either host.
//
// Both buttons act on global state (the settings dialog lives in App.vue, the
// stats rail in the sidebar store), so the component takes no data props; the
// optional separator is for hosts whose bar does not already draw one.

import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";

defineProps({
  // Draw the toolbar's vertical rule ahead of the pair. The grid toolbar
  // already has one before its actions group; the duplicates toolbar does not.
  separator: { type: Boolean, default: false },
});

const emit = defineEmits(["open-settings"]);

const sidebarStore = useSidebarStore();
const tasksStore = useTasksStore();
</script>

<style scoped>
/* The rules below are the toolbar's own treatment for these controls, moved
   here with the markup (scoped styles cannot cross the component boundary).
   Toolbar.vue keeps its copy of .bar-btn for its remaining buttons; a change
   to the shared look belongs in both places. */

.bar-separator {
  width: 1px;
  height: 24px;
  background: rgba(var(--v-theme-on-background), 0.2);
  margin: 0 var(--space-2);
  align-self: center;
  flex-shrink: 0;
}

.bar-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: 0 var(--space-3);
  cursor: pointer;
  font-size: var(--text-base);
  font-family: inherit;
  /* Icons and labels take the sidebar's treatment: the toolbar-text token
     (identical to sidebar-text) at the sidebar's muted alpha, brightening on
     hover/active — so the toolbar and sidebar chrome read as one strip. */
  color: rgb(var(--v-theme-toolbar-text));
  background: transparent;
  /* A transparent 1px border is reserved so the open state (which colours the
     border) does not change the box size and make the button jump. */
  border: 1px solid transparent;
  box-sizing: border-box;
  height: 32px;
  white-space: nowrap;
  position: relative;
}

.bar-btn:hover {
  background: rgba(var(--v-theme-toolbar-text), 0.1);
  color: rgb(var(--v-theme-toolbar-text));
}

.bar-btn--active {
  color: rgb(var(--v-theme-primary));
}

/* Icon-only bar button */
.bar-btn--icon {
  width: 32px;
  height: 32px;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

/* App-wide task-activity light on the stats toggle. */
.tb-stats-btn {
  position: relative;
}

.tb-stats-activity {
  position: absolute;
  top: 7px;
  right: 7px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: rgb(var(--v-theme-primary));
  box-shadow: 0 0 5px rgba(var(--v-theme-primary), 0.7);
  animation: tb-stats-pulse 1.4s ease-in-out infinite;
  pointer-events: none;
}

@keyframes tb-stats-pulse {
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
  .tb-stats-activity {
    animation: none;
  }
}

/* ── Shared toolbar collapse (docs/design/toolbar-responsive-decisions.md).
   At the shared ≤600 step both buttons fold into the hosts' ⋯ overflow
   ("Settings…" / "Stats sidebar" rows), and the activity dot moves to the ⋯
   trigger so background work stays visible. ─────────────────────────────── */
@container toolbar (max-width: 600px) {
  .bar-btn {
    display: none;
  }
}
</style>
