<template>
  <aside
    class="inspector"
    :class="{ 'inspector--collapsed': !open, 'inspector--lightbox': lightbox }"
    :aria-label="label"
    :aria-hidden="open ? undefined : 'true'"
  >
    <!-- The inspector: one component for every right-edge detail pane (shell
         contract rule 8). The grid's stats, the workflow shelf's inspector,
         the lightbox's picture pane and, as they are built, a model's detail
         and the duplicates evidence pane are this box with different
         content. The box, the collapse, the tab band and the key/value grid
         live here; a pane owns only what it says. -->
    <!-- The lightbox keeps its pane mounted while closed: the panels hold
         editing state and refs the overlay reaches into as it opens. -->
    <div v-if="open || lightbox" v-show="open" class="inspector-content">
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
          <!-- While a tab is busy its count IS the thing to say, so it takes
               the button's own description rather than hanging off the dot:
               a bare span is not something a reader can reach. -->
          <Tooltip
            :text="(tab.busy ? tab.busyTooltip : tab.tooltip) || ''"
            activator="parent"
          />
          <v-icon
            v-if="tab.icon"
            size="12"
            :class="{ 'inspector-tab-icon--busy': tab.busy }"
            >{{ tab.icon }}</v-icon
          >
          {{ tab.label }}
          <!-- `busy` is the tab's own live-work light: the Tasks tab wears it
               while anything is running, in every inspector that offers the
               tab, so the behaviour cannot drift between the two. -->
          <span
            v-if="tab.busy"
            class="inspector-tab-pulse"
            aria-hidden="true"
          ></span>
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
  /**
   * `[{ value, label, icon?, disabled?, tooltip?, busy?, busyTooltip? }]`.
   * `busy` pulses the tab's icon and adds an accent dot beside its label;
   * `busyTooltip` then replaces `tooltip` as the button's description.
   */
  tabs: { type: Array, default: () => [] },
  /** The active tab's `value`. */
  modelValue: { type: String, default: "" },
  /**
   * The image overlay's pane: dark in both themes, and a fixed-height column
   * whose sections bound and scroll themselves instead of the pane scrolling.
   * Its tab band carries the on-dark inks below, added when the lightbox
   * became the first pane here to need `tabs` (#1313).
   */
  lightbox: { type: Boolean, default: false },
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
    width var(--dur-2) var(--ease-standard),
    min-width var(--dur-2) var(--ease-standard),
    max-width var(--dur-2) var(--ease-standard),
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

/* The lightbox is dark in both themes, so its pane is a translucent
   dark-surface over the picture rather than the theme's chrome. */
.inspector--lightbox {
  /* Amber on a dark panel keeps the bright amber in both themes (#1413). */
  --v-theme-accent: var(--v-theme-dark-surface-accent);
  background: rgba(var(--v-theme-dark-surface), 0.6);
  border-left-color: rgba(var(--v-theme-on-dark-surface), 0.12);
  color: rgb(var(--v-theme-on-dark-surface));
}

.inspector--lightbox.inspector--collapsed {
  border-left-color: transparent;
  background: transparent;
}

.inspector--lightbox .inspector-content {
  overflow: hidden;
}

.inspector--lightbox .inspector-body {
  flex: 1;
  min-height: 0;
}

.inspector--lightbox .inspector-body :deep(.inspector-kv dt) {
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
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

/* Live work wears the accent, never the olive: olive is the selection mark the
   active tab's underline carries, and an attention light is accent
   (visual-language §12). */
.inspector-tab-icon--busy {
  color: rgb(var(--v-theme-accent));
  animation: inspector-tab-pulse 1.4s ease-in-out infinite;
}

.inspector-tab-pulse {
  width: var(--badge-size-dot);
  height: var(--badge-size-dot);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-accent));
  box-shadow: 0 0 5px rgba(var(--v-theme-accent), 0.6);
  animation: inspector-tab-pulse 1.4s ease-in-out infinite;
}

@keyframes inspector-tab-pulse {
  0%,
  100% {
    opacity: 1;
    transform: scale(1);
  }
  50% {
    opacity: 0.45;
    transform: scale(0.78);
  }
}

@media (prefers-reduced-motion: reduce) {
  .inspector-tab-icon--busy,
  .inspector-tab-pulse {
    animation: none;
  }
}

/* The lightbox's tab band. Inked separately because the pane is dark in BOTH
   themes: `--active-bar` and `--active-text` are per-theme, so in light mode
   they would paint a light-surface olive onto a dark surface. The dark-surface
   olive is the one that belongs here, which is the same reasoning the picture
   panels' own labels follow. */
.inspector--lightbox .inspector-tabs {
  border-bottom-color: rgba(var(--v-theme-on-dark-surface), 0.12);
}

.inspector--lightbox .inspector-tab {
  color: rgba(var(--v-theme-on-dark-surface), var(--opacity-text-secondary));
}

.inspector--lightbox .inspector-tab:hover:not(:disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
  color: rgb(var(--v-theme-on-dark-surface));
}

.inspector--lightbox .inspector-tab--active {
  color: rgb(var(--v-theme-on-dark-surface));
  border-bottom-color: rgb(var(--v-theme-dark-surface-primary));
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
