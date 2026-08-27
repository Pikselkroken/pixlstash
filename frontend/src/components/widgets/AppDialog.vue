<template>
  <v-dialog
    :model-value="open"
    :max-width="fullscreen ? undefined : width"
    :scrim="true"
    :persistent="persistent"
    transition="dialog-bottom-transition"
    @update:model-value="(v) => !v && emit('close')"
    @click:outside="emit('close')"
  >
    <div
      class="app-dialog"
      :class="{ 'app-dialog--fullscreen': fullscreen }"
      :style="fullscreen ? undefined : { width: width + 'px' }"
      @keydown="onKeydown"
    >
      <header class="app-dialog__header">
        <div class="app-dialog__titlewrap">
          <h2 class="app-dialog__title">{{ title }}</h2>
          <span v-if="subtitle" class="app-dialog__subtitle">{{
            subtitle
          }}</span>
        </div>
        <div class="app-dialog__actions">
          <slot name="header-right" />
          <button
            type="button"
            class="app-dialog__close"
            title="Close"
            @click="emit('close')"
          >
            <v-icon size="20">mdi-close</v-icon>
          </button>
        </div>
      </header>
      <div
        :class="['app-dialog__body', { 'app-dialog__body--flush': !padBody }]"
      >
        <slot />
      </div>
      <footer v-if="$slots.footer" class="app-dialog__footer">
        <slot name="footer" />
      </footer>
    </div>
  </v-dialog>
</template>

<script setup>
import { VDialog, VIcon } from "vuetify/components";

const props = defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: "" },
  subtitle: { type: String, default: "" },
  // Numeric pixel width - the proposal sizes dialogs at fixed widths.
  width: { type: Number, default: 480 },
  // When false the body is flush (no padding) - used by the two-pane Settings
  // dialog where the nav rail and content own their own padding.
  padBody: { type: Boolean, default: true },
  persistent: { type: Boolean, default: false },
  // Near-viewport-sized dialog for working surfaces (Compare) where the
  // content is the point and a fixed width would waste the screen.
  fullscreen: { type: Boolean, default: false },
});

const emit = defineEmits(["close", "accept"]);

// Enter is inert wherever the key already means something: multiline fields,
// buttons and links (native activation must win, so Enter on Cancel cancels),
// selects, disclosure summaries, and ARIA text boxes.
const ENTER_EXEMPT =
  "textarea, select, button, a[href], summary, [contenteditable='true'], [role='textbox']";

/**
 * The dialog keyboard contract (owner decision, 2026-07-29 - see
 * docs/frontend_architecture.md "App* design-system primitives"): Escape
 * dismisses, plain Enter accepts. Handled here, on the dialog's own subtree,
 * so every AppDialog gets it and no page-level Escape owner is consulted
 * first. `accept` only fires for dialogs that listen for it.
 */
function onKeydown(e) {
  if (e.key === "Escape") {
    if (props.persistent) return;
    e.stopPropagation();
    emit("close");
    return;
  }
  if (e.key !== "Enter" || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
  // A descendant that handled Enter itself (e.g. a role="radio" row) has
  // already preventDefault-ed; respect that instead of double-acting.
  if (e.defaultPrevented) return;
  const el = e.target instanceof Element ? e.target : null;
  if (el && el.closest(ENTER_EXEMPT)) return;
  e.preventDefault();
  emit("accept");
}
</script>

<style scoped>
/* A dialog is the highest elevation in the app: the --surface fill, --elevation-4
   shadow, --radius-lg corners, over the v-dialog scrim. The title bar is a real
   header row - title left, actions + an inline ghost close button right - never a
   floating circular FAB. Same chrome language as the toolbar popovers, one
   elevation level up. */
.app-dialog {
  display: flex;
  flex-direction: column;
  max-height: 100%;
  overflow: hidden;
  max-width: 100%;
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  box-shadow: var(--elevation-4);
}

.app-dialog__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-5);
  flex-shrink: 0;
  padding: var(--space-4) var(--space-4) var(--space-4) var(--space-6);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.app-dialog__titlewrap {
  display: flex;
  align-items: baseline;
  gap: var(--space-4);
  min-width: 0;
}

.app-dialog__title {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.01em;
  line-height: var(--leading-tight);
}

.app-dialog__subtitle {
  font-size: var(--text-xs);
  font-family: var(--font-mono);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.app-dialog__actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.app-dialog__close {
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-md);
  color: rgba(var(--v-theme-on-surface), 0.6);
  transition:
    background var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard);
}

.app-dialog__close:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.app-dialog__body {
  overflow-y: auto;
  padding: var(--space-6);
}

/* A working surface, not a form: take (nearly) the whole viewport and let the
   body flex, so the content decides its own internal scrolling. */
.app-dialog--fullscreen {
  width: min(1800px, 96vw);
  height: 94vh;
}

.app-dialog--fullscreen .app-dialog__body {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.app-dialog__body--flush {
  padding: 0;
  overflow: hidden;
  display: flex;
  min-height: 0;
}

.app-dialog__footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-4);
  flex-shrink: 0;
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid rgb(var(--v-theme-divider));
}
</style>
