<template>
  <span v-if="separator" class="bar-separator" aria-hidden="true"></span>
  <!-- ── Settings ──────────────────────────────────────────────────────── -->
  <AppBarButton
    icon="cog-outline"
    title="Settings"
    aria-label="Settings"
    @click="emit('open-settings')"
  />
  <!-- ── Stats toggle ──────────────────────────────────────────────────── -->
  <!-- App-wide activity light (`--busy`, drawn as ::after): pulses whenever
       the task manager has any active work, so background tasks are visible
       without opening the stats sidebar. A pseudo-element rather than a
       slotted span, because a slotted button is a labelled one and would lose
       its icon-only size. -->
  <AppBarButton
    class="tb-stats-btn"
    :class="{ 'tb-stats-btn--busy': tasksStore.hasActiveTasks }"
    icon="chart-bar"
    :active="sidebarStore.statsOpen"
    :title="statsTitle"
    :aria-label="statsTitle"
    @click="sidebarStore.toggleStats()"
  />
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

import { computed } from "vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import AppBarButton from "../widgets/AppBarButton.vue";

defineProps({
  // Draw the toolbar's vertical rule ahead of the pair. The grid toolbar
  // already has one before its actions group; the duplicates toolbar does not.
  separator: { type: Boolean, default: false },
});

const emit = defineEmits(["open-settings"]);

const sidebarStore = useSidebarStore();
const tasksStore = useTasksStore();

const statsTitle = computed(() =>
  tasksStore.hasActiveTasks
    ? `${tasksStore.activeCount} active task${tasksStore.activeCount === 1 ? "" : "s"} running`
    : sidebarStore.statsOpen
      ? "Hide stats sidebar"
      : "Show stats sidebar",
);
</script>

<style scoped>
/* The `.bar-*` family this component's buttons use lives unscoped in App.css.
   It used to be duplicated here and in Toolbar.vue, under a comment asking
   whoever changed one to remember the other; the five rules were still
   byte-identical, and now there is one copy. */

/* App-wide task-activity light on the stats toggle (`.bar-btn` is already
   `position: relative`). */
.tb-stats-btn--busy::after {
  content: "";
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
  .tb-stats-btn--busy::after {
    animation: none;
  }
}

/* No collapse rule here on purpose (amendment #2 in
   docs/design/toolbar-responsive-decisions.md): Settings and Stats never
   fold - a burger may only collapse controls from its own visual group, and
   these are the app-wide tail's. The activity dot stays first-class on the
   Stats button at every width. */
</style>
