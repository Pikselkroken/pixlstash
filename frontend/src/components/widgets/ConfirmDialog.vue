<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { VCheckbox, VIcon } from "vuetify/components";

import {
  activeConfirm,
  registerConfirmHost,
  resolveConfirm,
  unregisterConfirmHost,
} from "../../composables/useConfirm";
import AppButton from "./AppButton.vue";
import AppDialog from "./AppDialog.vue";

const primaryButton = ref(null);
const dontShowAgain = ref(false);
let returnFocusTarget = null;

watch(
  () => activeConfirm.value?.id,
  async (id) => {
    if (!id) return;
    dontShowAgain.value = false;
    returnFocusTarget =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    await nextTick();
    primaryButton.value?.focus?.();
  },
);

async function settle(result) {
  if (!activeConfirm.value) return;
  const target = returnFocusTarget;
  returnFocusTarget = null;
  resolveConfirm(result, dontShowAgain.value);
  await nextTick();
  target?.focus?.();
}

onMounted(registerConfirmHost);
onBeforeUnmount(unregisterConfirmHost);
</script>

<template>
  <AppDialog
    :open="Boolean(activeConfirm)"
    :title="activeConfirm?.options.title ?? ''"
    @close="settle(false)"
    @accept="settle(true)"
  >
    <p class="confirm-dialog__message">
      {{ activeConfirm?.options.message }}
    </p>
    <p v-if="activeConfirm?.options.warning" class="confirm-dialog__warning">
      <v-icon size="18" aria-hidden="true">mdi-link-variant-off</v-icon>
      <span>{{ activeConfirm.options.warning }}</span>
    </p>

    <template #footer>
      <v-checkbox
        v-if="activeConfirm?.options.onDontShowAgain"
        v-model="dontShowAgain"
        label="Don't show this again"
        density="compact"
        hide-details
        class="confirm-dialog__dont-show"
      />
      <AppButton
        variant="secondary"
        key-hint="esc"
        @click="settle(false)"
      >
        {{ activeConfirm?.options.cancelLabel ?? "Cancel" }}
      </AppButton>
      <AppButton
        ref="primaryButton"
        :variant="activeConfirm?.options.danger ? 'danger' : 'primary'"
        key-hint="enter"
        @click="settle(true)"
      >
        {{ activeConfirm?.options.confirmLabel ?? "Confirm" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.confirm-dialog__message {
  margin: 0;
  max-width: 70ch;
  font-size: var(--text-base);
  line-height: var(--leading-body);
}

.confirm-dialog__warning {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border: 1px solid rgb(var(--v-theme-warning));
  border-radius: var(--radius-md);
  color: rgb(var(--v-theme-on-surface));
  background: rgba(var(--v-theme-warning), 0.1);
  font-size: var(--text-sm);
  line-height: var(--leading-body);
}

/* Same footer shape as SnapshotsWithDeletedDialog: checkbox left, actions right. */
.confirm-dialog__dont-show {
  margin-right: auto;
}

.confirm-dialog__dont-show :deep(.v-label) {
  font-size: var(--text-sm);
}
</style>
