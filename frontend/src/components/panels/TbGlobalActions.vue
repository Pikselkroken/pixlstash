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
// every view - the styles live here, not in either host.
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
/* The `.bar-*` family this component's buttons use lives unscoped in App.css.
   It used to be duplicated here and in Toolbar.vue, under a comment asking
   whoever changed one to remember the other; the five rules were still
   byte-identical, and now there is one copy. */

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

/* No collapse rule here on purpose (amendment #2 in
   docs/design/toolbar-responsive-decisions.md): Settings and Stats never
   fold - a burger may only collapse controls from its own visual group, and
   these are the app-wide tail's. The activity dot stays first-class on the
   Stats button at every width. */
</style>
