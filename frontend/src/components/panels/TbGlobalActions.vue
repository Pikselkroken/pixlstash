<template>
  <span v-if="separator" class="bar-separator" aria-hidden="true"></span>
  <!-- ── Settings ──────────────────────────────────────────────────────── -->
  <AppBarButton
    icon="cog-outline"
    tooltip="Settings"
    @click="emit('open-settings')"
  />
  <!-- ── Stats toggle ──────────────────────────────────────────────────── -->
  <!-- App-wide activity light (`--busy`): the whole glyph pulses amber
       whenever the task manager has any active work, so background tasks are
       visible without opening the stats sidebar. A corner dot was too subtle
       to notice (#1343). -->
  <!-- `--nudge-a`/`--nudge-b`: the closed-inspector flash (visual-language
       "Closed inspector"). Two classes with identical keyframes, alternated,
       so a second nudge restarts the animation instead of being ignored. -->
  <AppBarButton
    class="tb-stats-btn"
    :class="{
      'tb-stats-btn--busy': tasksStore.hasActiveTasks,
      [`tb-stats-btn--nudge-${nudgeParity}`]: nudgeParity,
    }"
    icon="chart-bar"
    :active="railOpen"
    :tooltip="statsTitle"
    @click="toggleRail"
  />
</template>

<script setup>
// The app-wide chrome that must survive a change of destination: Settings and
// the stats sidebar toggle. The grid's toolbar and the duplicates queue both
// mount this SAME component, which is what keeps the pair pixel-identical in
// every view - the styles live here, not in either host.
//
// Both buttons act on global state (the settings dialog lives in App.vue, the
// stats rail in the sidebar store), so the component takes no data props. The
// two it does take are about the HOST: an optional separator for a bar that
// does not already draw one, and what the rail is called on that screen.

import { computed, ref, watch } from "vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import AppBarButton from "../widgets/AppBarButton.vue";

const props = defineProps({
  // Draw the toolbar's vertical rule ahead of the pair. The grid toolbar
  // already has one before its actions group; the duplicates toolbar does not.
  separator: { type: Boolean, default: false },
  // What the right rail IS on this screen, because the toggle's tooltip is
  // also its accessible name (AppBarButton promotes an icon-only button's
  // tooltip to `aria-label`). One rail, more than one panel: on the Workflows
  // grid it carries the workflow inspector, so a reader there was offered
  // "Show stats sidebar" for a control that opens the inspector (#1415).
  railName: { type: String, default: "stats sidebar" },
  // WHICH rail the toggle drives. The Workflows inspector has its own open
  // flag (it defaults open; the stats sidebar defaults closed), so the host
  // says which one is on its screen.
  rail: {
    type: String,
    default: "stats",
    validator: (value) => ["stats", "workflows"].includes(value),
  },
});

const emit = defineEmits(["open-settings"]);

const sidebarStore = useSidebarStore();
const tasksStore = useTasksStore();

const railOpen = computed(() =>
  props.rail === "workflows"
    ? sidebarStore.workflowInspectorOpen
    : sidebarStore.statsOpen,
);

function toggleRail() {
  if (props.rail === "workflows") sidebarStore.toggleWorkflowInspector();
  else sidebarStore.toggleStats();
}

// A new selection while the rail is closed: the glyph turns olive for
// `--dur-attention` and fades back. Skipped while tasks run, because the amber
// busy pulse owns the glyph then. Nothing is cleared on a timer: the class
// stays until the next nudge swaps it for its twin, and a one-shot animation
// that has finished paints nothing.
const nudgeParity = ref("");
watch(
  () => sidebarStore.inspectorNudge,
  () => {
    if (railOpen.value || tasksStore.hasActiveTasks) return;
    nudgeParity.value = nudgeParity.value === "a" ? "b" : "a";
  },
);

const statsTitle = computed(() =>
  tasksStore.hasActiveTasks
    ? `${tasksStore.activeCount} active task${tasksStore.activeCount === 1 ? "" : "s"} running`
    : railOpen.value
      ? `Hide ${props.railName}`
      : `Show ${props.railName}`,
);
</script>

<style scoped>
/* The `.bar-*` family this component's buttons use lives unscoped in App.css.
   It used to be duplicated here and in Toolbar.vue, under a comment asking
   whoever changed one to remember the other; the five rules were still
   byte-identical, and now there is one copy. */

/* App-wide task-activity light on the stats toggle. Accent, not the olive
   the active (open) state gives the glyph: live work is not selection. */
.tb-stats-btn--busy :deep(.v-icon) {
  color: rgb(var(--v-theme-accent));
  animation: tb-stats-pulse 1.4s ease-in-out infinite;
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

/* Glyph colour only: no background, ring or border, so it cannot be read as
   the button's pressed state. */
.tb-stats-btn--nudge-a:not(.tb-stats-btn--busy) :deep(.v-icon) {
  animation: tb-stats-nudge-a var(--dur-attention) var(--ease-standard);
}

.tb-stats-btn--nudge-b:not(.tb-stats-btn--busy) :deep(.v-icon) {
  animation: tb-stats-nudge-b var(--dur-attention) var(--ease-standard);
}

@keyframes tb-stats-nudge-a {
  0%,
  40% {
    color: var(--selected-ink);
  }
}

@keyframes tb-stats-nudge-b {
  0%,
  40% {
    color: var(--selected-ink);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tb-stats-btn--busy :deep(.v-icon),
  .tb-stats-btn--nudge-a :deep(.v-icon),
  .tb-stats-btn--nudge-b :deep(.v-icon) {
    animation: none;
  }
}

/* No collapse rule here on purpose (amendment #2 in
   docs/design/toolbar-responsive-decisions.md): Settings and Stats never
   fold - a burger may only collapse controls from its own visual group, and
   these are the app-wide tail's. The activity light stays first-class on the
   Stats button at every width. */
</style>
