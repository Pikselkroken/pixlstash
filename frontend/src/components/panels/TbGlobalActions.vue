<template>
  <span v-if="separator" class="bar-separator" aria-hidden="true"></span>
  <!-- Settings lives in the title bar (#2158): one control, the same place in
       every workspace. -->
  <!-- ── Right-rail toggle ─────────────────────────────────────────────── -->
  <!-- One glyph on every screen, `dock-right`: it says where something will
       appear, not what is in it, because the rail holds stats on the Library,
       an inspector on Workflows and models on Models. Open is the pressed
       state (`active` + `aria-pressed`), never a second glyph. -->
  <!-- No activity light here: running work is the title bar's Tasks pill,
       so the rail toggle no longer pulses while tasks run. -->
  <!-- `--nudge-a`/`--nudge-b`: the closed-inspector flash (visual-language
       "Closed inspector"). Two classes with identical keyframes, alternated,
       so a second nudge restarts the animation instead of being ignored. -->
  <AppBarButton
    class="tb-stats-btn"
    :class="{ [`tb-stats-btn--nudge-${nudgeParity}`]: nudgeParity }"
    icon="dock-right"
    :active="railOpen"
    :aria-pressed="railOpen ? 'true' : 'false'"
    :tooltip="railName"
    @click="toggleRail"
  />
</template>

<script setup>
// The app-wide chrome that must survive a change of destination: the
// right-rail toggle (Settings moved to the title bar, #2158). The grid's
// toolbar and the duplicates queue both mount this SAME component, which is
// what keeps it pixel-identical in every view - the styles live here, not in
// either host.
//
// It acts on global state (the stats rail in the sidebar store), so the
// component takes no data props. The ones it does take are about the HOST: an
// optional separator for a bar that does not already draw one, and what the
// rail is called on that screen.

import { computed, ref, watch } from "vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
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
  // A fixed name with no Show/Hide: `aria-pressed` says which way it is.
  railName: { type: String, default: "Inspector" },
  // WHICH rail the toggle drives. The Workflows inspector has its own open
  // flag (it defaults open; the stats sidebar defaults closed), so the host
  // says which one is on its screen.
  rail: {
    type: String,
    default: "stats",
    validator: (value) =>
      ["stats", "workflows", "models", "lightbox"].includes(value),
  },
});

const sidebarStore = useSidebarStore();

const railOpen = computed(() =>
  props.rail === "workflows"
    ? sidebarStore.workflowInspectorOpen
    : props.rail === "models"
      ? sidebarStore.modelsRailOpen
      : props.rail === "lightbox"
        ? sidebarStore.lightboxRailOpen
        : sidebarStore.statsOpen,
);

function toggleRail() {
  if (props.rail === "workflows") sidebarStore.toggleWorkflowInspector();
  else if (props.rail === "models") sidebarStore.toggleModelsRail();
  else if (props.rail === "lightbox") sidebarStore.toggleLightboxRail();
  else sidebarStore.toggleStats();
}

// A new selection while the rail is closed: the glyph turns olive for
// `--dur-attention` and fades back. Nothing is cleared on a timer: the class
// stays until the next nudge swaps it for its twin, and a one-shot animation
// that has finished paints nothing.
const nudgeParity = ref("");
watch(
  () => sidebarStore.inspectorNudge,
  () => {
    if (railOpen.value) return;
    nudgeParity.value = nudgeParity.value === "a" ? "b" : "a";
  },
);
</script>

<style scoped>
/* The `.bar-*` family this component's buttons use lives unscoped in App.css.
   It used to be duplicated here and in Toolbar.vue, under a comment asking
   whoever changed one to remember the other; the five rules were still
   byte-identical, and now there is one copy. */

/* Glyph colour only: no background, ring or border, so it cannot be read as
   the button's pressed state. */
.tb-stats-btn--nudge-a :deep(.v-icon) {
  animation: tb-stats-nudge-a var(--dur-attention) var(--ease-standard);
}

.tb-stats-btn--nudge-b :deep(.v-icon) {
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
  .tb-stats-btn--nudge-a :deep(.v-icon),
  .tb-stats-btn--nudge-b :deep(.v-icon) {
    animation: none;
  }
}

/* No collapse rule here on purpose (amendment #2 in
   docs/design/toolbar-responsive-decisions.md): Settings and Stats never
   fold - a burger may only collapse controls from its own visual group, and
   these are the app-wide tail's. */
</style>
