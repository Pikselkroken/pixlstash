<template>
  <!-- Reusable presentational toolbar shell. Owns only the chrome (bar surface,
       Close button, icon-button look) copied from ImageOverlay's .overlay-topbar so
       every overlay reads as the same toolbar. No store, no business logic: a Close
       button (emits "close") plus #title / default / #actions slots. The #actions slot
       styles its icon-buttons like .overlay-icon-btn, so per-overlay controls inherit
       the look. Optional `hidden` prop fades the whole bar (ImageOverlay's chrome-hidden
       behaviour); default off so a consumer keeps the bar visible. -->
  <header class="overlay-toolbar" :class="{ 'overlay-toolbar--hidden': hidden }">
    <button
      class="overlay-toolbar-close"
      type="button"
      aria-label="Close (ESC)"
      title="Close (ESC)"
      @click="emit('close')"
    >
      <v-icon size="18">mdi-close</v-icon>
      <span>Close</span>
    </button>

    <div class="overlay-toolbar-title">
      <slot name="title" />
    </div>

    <slot />

    <div class="overlay-toolbar-actions">
      <slot name="actions" />
    </div>
  </header>
</template>

<script setup>
defineProps({
  // Fade the whole bar out (opacity 0 + pointer-events none), mirroring
  // ImageOverlay's chrome-hidden auto-hide. Idle detection is the consumer's job;
  // this only applies the fade class.
  hidden: { type: Boolean, default: false },
});

const emit = defineEmits(["close"]);
</script>

<style scoped>
/* Bar surface, copied from ImageOverlay's .overlay-topbar. Laid out in-flow so a
   consumer can drop it at the top of a column-flex shell (the review overlay) or
   position it absolutely over content (a future migration of ImageOverlay) without
   the shell owning the look. */
.overlay-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 10px;
  min-height: 40px;
  background: rgba(var(--v-theme-dark-surface), 0.9);
  color: rgb(var(--v-theme-on-dark-surface));
  transition: opacity 0.2s ease;
  flex-wrap: wrap;
}

.overlay-toolbar--hidden {
  opacity: 0;
  pointer-events: none;
}

/* Close button — identical styling to ImageOverlay's .overlay-close. */
.overlay-toolbar-close {
  border: none;
  background: rgba(var(--v-theme-primary), 0.7);
  color: rgb(var(--v-theme-on-primary));
  padding: 6px 14px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
  font-size: 1em;
  font-weight: 600;
}
.overlay-toolbar-close:hover {
  background: rgba(var(--v-theme-accent), 0.85);
}

/* Title area — copies .overlay-title (grows to fill, truncates). */
.overlay-toolbar-title {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}

/* Right-aligned actions — copies .overlay-top-actions. */
.overlay-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

/* Style any icon-button a consumer puts in #actions like ImageOverlay's
   .overlay-icon-btn, so per-overlay icon buttons inherit the toolbar look without
   re-declaring it. :deep pierces scoping into slotted content. */
.overlay-toolbar-actions :deep(.overlay-icon-btn) {
  border: none;
  background: none;
  color: rgb(var(--v-theme-on-dark-surface));
  height: 32px;
  padding: 6px 14px;
  min-width: 32px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  font-size: 1em;
}
.overlay-toolbar-actions :deep(.overlay-icon-btn:hover:not(:disabled)) {
  background: rgba(var(--v-theme-primary), 0.6);
}
.overlay-toolbar-actions :deep(.overlay-icon-btn:disabled) {
  opacity: 0.5;
  cursor: not-allowed;
}
.overlay-toolbar-actions :deep(.overlay-icon-btn--active) {
  background: rgba(var(--v-theme-primary), 0.25);
  color: rgb(var(--v-theme-primary));
}
</style>
