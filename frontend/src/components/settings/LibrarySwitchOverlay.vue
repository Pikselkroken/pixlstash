<script setup>
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import { storeToRefs } from "pinia";
import { VIcon, VProgressCircular } from "vuetify/components";

import {
  useLibrarySwitchStore,
} from "../../stores/useLibrariesStore";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { inertSiblingOverlays } from "../../utils/inertBackground";

const switchStore = useLibrarySwitchStore();
const { phase, targetLibrary, currentLibrary, error, overlayOpen } =
  storeToRefs(switchStore);
const panel = ref(null);
const stayButton = ref(null);
let restoreOverlayInertness = null;

// AppDialog names the dialog from its title, so the name follows the phase
// instead of going stale in one of them. The error paragraph exists in the
// failed phase only; naming it while it is absent would leave a dangling IDREF.
const title = computed(() =>
  phase.value === "failed"
    ? `Could not switch to ${targetLibrary.value?.name ?? ""}`
    : `Switching to ${targetLibrary.value?.name ?? ""}…`,
);
const descriptionId = useId();
const errorId = useId();
const describedBy = computed(() =>
  phase.value === "failed"
    ? `${descriptionId} ${errorId}`
    : descriptionId,
);

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

// While switching, Escape must not reach any page-level handler either.
function blockEscape(event) {
  if (event.key !== "Escape" || phase.value !== "switching") return;
  event.preventDefault();
  event.stopPropagation();
}

function onClose() {
  if (phase.value === "failed") switchStore.stayOnCurrent();
}
</script>

<template>
  <!--
    `role` and `aria-describedby` fall through AppDialog to Vuetify's overlay
    root, which already carries aria-modal; AppDialog names it from the title.
    The switch in flight has no sane close, so the dialog is persistent then and
    close is a no-op; once it has failed, closing means staying.
  -->
  <AppDialog
    :open="overlayOpen"
    class="library-switch-modal"
    :title="title"
    size="md"
    :persistent="phase === 'switching'"
    role="alertdialog"
    :aria-describedby="describedBy"
    @keydown="blockEscape"
    @close="onClose"
    @accept="onClose"
  >
    <section
      ref="panel"
      class="library-switch-overlay"
      aria-live="assertive"
      aria-atomic="true"
      tabindex="-1"
    >
      <template v-if="phase === 'switching'">
        <v-progress-circular indeterminate size="32" width="3" />
        <p :id="descriptionId">
          PixlStash is finishing or cancelling work, then it will reload this
          window. Keep it open.
        </p>
      </template>

      <template v-else-if="phase === 'failed'">
        <v-icon class="library-switch-overlay__error" size="32" aria-hidden="true">
          mdi-alert-circle-outline
        </v-icon>
        <div class="library-switch-overlay__failure">
          <p :id="descriptionId">
            PixlStash is still using
            <strong>{{ currentLibrary?.name ?? "the current library" }}</strong>.
          </p>
          <p :id="errorId" class="library-switch-overlay__detail">
            {{ error }}
          </p>
        </div>
      </template>
    </section>

    <template v-if="phase === 'failed'" #footer>
      <AppButton
        ref="stayButton"
        variant="primary"
        @click="switchStore.stayOnCurrent()"
      >
        Stay on {{ currentLibrary?.name ?? "current library" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.library-switch-overlay {
  display: flex;
  align-items: flex-start;
  gap: var(--space-5);
  outline: none;
}

.library-switch-overlay p {
  margin: 0;
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
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
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
</style>
