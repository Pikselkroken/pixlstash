<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from "vue";
import { storeToRefs } from "pinia";
import { VDialog, VIcon, VProgressCircular } from "vuetify/components";

import {
  useLibrarySwitchStore,
} from "../../stores/useLibrariesStore";
import AppButton from "../widgets/AppButton.vue";
import { inertSiblingOverlays } from "../../utils/inertBackground";

const switchStore = useLibrarySwitchStore();
const { phase, targetLibrary, currentLibrary, error, overlayOpen } =
  storeToRefs(switchStore);
const panel = ref(null);
const stayButton = ref(null);
let restoreOverlayInertness = null;

watch(
  [overlayOpen, phase],
  async ([isOpen, nextPhase]) => {
    if (!isOpen) {
      restoreOverlayInertness?.();
      restoreOverlayInertness = null;
      return;
    }
    await nextTick();
    if (!restoreOverlayInertness) {
      restoreOverlayInertness = inertSiblingOverlays(panel.value);
    }
    if (nextPhase === "failed") stayButton.value?.focus?.();
    else panel.value?.focus?.();
  },
);

onBeforeUnmount(() => restoreOverlayInertness?.());

function blockEscape(event) {
  if (event.key !== "Escape") return;
  event.preventDefault();
  event.stopPropagation();
}
</script>

<template>
  <v-dialog
    :model-value="overlayOpen"
    class="library-switch-modal"
    persistent
    :scrim="true"
    :max-width="520"
    @keydown="blockEscape"
  >
    <section
      ref="panel"
      class="library-switch-overlay"
      role="alertdialog"
      aria-modal="true"
      aria-live="assertive"
      aria-atomic="true"
      tabindex="-1"
    >
      <template v-if="phase === 'switching'">
        <v-progress-circular indeterminate size="32" width="3" />
        <div>
          <h2>Switching to {{ targetLibrary?.name }}…</h2>
          <p>
            PixlStash is finishing or cancelling work, then it will reload this
            window. Keep it open.
          </p>
        </div>
      </template>

      <template v-else-if="phase === 'failed'">
        <v-icon class="library-switch-overlay__error" size="32" aria-hidden="true">
          mdi-alert-circle-outline
        </v-icon>
        <div class="library-switch-overlay__failure">
          <h2>Could not switch to {{ targetLibrary?.name }}</h2>
          <p>
            PixlStash is still using
            <strong>{{ currentLibrary?.name ?? "the current library" }}</strong>.
          </p>
          <p class="library-switch-overlay__detail">{{ error }}</p>
          <div class="library-switch-overlay__actions">
            <AppButton
              ref="stayButton"
              variant="primary_green"
              @click="switchStore.stayOnCurrent()"
            >
              Stay on {{ currentLibrary?.name ?? "current library" }}
            </AppButton>
          </div>
        </div>
      </template>
    </section>
  </v-dialog>
</template>

<style scoped>
.library-switch-overlay {
  display: flex;
  align-items: flex-start;
  gap: var(--space-5);
  padding: var(--space-6);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-lg);
  background: rgb(var(--v-theme-surface));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-4);
  outline: none;
}

.library-switch-overlay h2 {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-tight);
}

.library-switch-overlay p {
  margin: var(--space-3) 0 0;
  font-size: var(--text-base);
  line-height: var(--leading-body);
}

.library-switch-overlay__error {
  flex: 0 0 auto;
  padding: var(--space-2);
  border-radius: 50%;
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
}

.library-switch-overlay__failure {
  flex: 1;
  min-width: 0;
}

.library-switch-overlay__detail {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-error));
  color: rgb(var(--v-theme-on-error));
  overflow-wrap: anywhere;
}

.library-switch-overlay__actions {
  display: flex;
  justify-content: flex-end;
  margin-top: var(--space-5);
}
</style>
