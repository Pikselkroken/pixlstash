<template>
  <v-dialog
    :model-value="open"
    :max-width="width"
    :scrim="true"
    :persistent="persistent"
    transition="dialog-bottom-transition"
    @update:model-value="(v) => !v && emit('close')"
    @click:outside="emit('close')"
  >
    <div class="app-dialog" :style="{ width: width + 'px' }">
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

defineProps({
  open: { type: Boolean, default: false },
  title: { type: String, default: "" },
  subtitle: { type: String, default: "" },
  // Numeric pixel width — the proposal sizes dialogs at fixed widths.
  width: { type: Number, default: 480 },
  // When false the body is flush (no padding) — used by the two-pane Settings
  // dialog where the nav rail and content own their own padding.
  padBody: { type: Boolean, default: true },
  persistent: { type: Boolean, default: false },
});

const emit = defineEmits(["close"]);
</script>

<style scoped>
/* A dialog is the highest elevation in the app: the --surface fill, --elevation-4
   shadow, --radius-lg corners, over the v-dialog scrim. The title bar is a real
   header row — title left, actions + an inline ghost close button right — never a
   floating circular FAB. Same chrome language as the toolbar popovers, one
   elevation level up. */
.app-dialog {
  display: flex;
  flex-direction: column;
  max-height: 100%;
  overflow: hidden;
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
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.6);
  cursor: pointer;
  transition:
    background var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard);
}

.app-dialog__close:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

.app-dialog__close:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.app-dialog__body {
  overflow-y: auto;
  padding: var(--space-6);
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
