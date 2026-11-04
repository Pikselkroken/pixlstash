<template>
  <!-- #2558: Start on a run that fits only with ComfyUI quit. Two answers,
       Queue (focused, the safe default) and Quit; Escape and the close button
       go back to the Train dialog without starting. Enter does nothing here:
       no @accept, so the dialog's Enter-accepts contract never fires. -->
  <AppDialog
    :open="open"
    :title="`${fitLabel} needs ComfyUI closed`"
    size="sm"
    @close="emit('close')"
  >
    <div class="cq-body">
      <p v-if="comfyui.busy" class="cq-text" data-testid="cq-text">
        ComfyUI is running jobs PixlStash didn't submit, so the run queues until
        they finish either way. ComfyUI is never quit mid-job.
      </p>
      <p v-else class="cq-text" data-testid="cq-text">
        This run needs {{ gbTenths(needMb) }} GB. With ComfyUI's models unloaded,
        {{ gbTenths(needMb - comfyui.short_mb) }} GB is free. ComfyUI's last
        {{ gbTenths(comfyui.kept_mb) }} GB is released only when it quits.
      </p>

      <table v-if="!comfyui.busy" class="cq-cmp">
        <tbody>
          <tr>
            <td>
              <b>Quit ComfyUI</b>
              <small>Training starts now.</small>
            </td>
            <td>{{ runTime ? `about ${runTime}` : "starts now" }}</td>
          </tr>
          <tr>
            <td>
              <b>Queue the run</b>
              <small
                >ComfyUI keeps running. The run starts by itself once you close
                ComfyUI.</small
              >
            </td>
            <td>starts later</td>
          </tr>
        </tbody>
      </table>

      <p class="cq-notice" role="note">
        <v-icon size="16">mdi-alert-outline</v-icon>
        <span
          >PixlStash didn't start ComfyUI and can't start it again. If it quits,
          start it yourself after the run. A notice says when.</span
        >
      </p>

      <button
        v-if="comfyui.beside"
        type="button"
        class="cq-link"
        data-testid="cq-beside"
        @click="emit('beside', comfyui.beside.fit)"
      >
        Use {{ FIT_LABELS[comfyui.beside.fit] }} instead: fits beside
        ComfyUI{{ besideTime ? `, about ${besideTime}` : "" }}
      </button>
      <span class="cq-hint">
        A queued run can still be forced to start from Tasks with
        <i>Run anyway</i>. At {{ gbTenths(comfyui.short_mb) }} GB short it will
        likely run out of GPU memory.
      </span>
    </div>

    <template #footer>
      <AppButton ref="queueButton" data-testid="cq-queue" @click="emit('queue')">
        {{ comfyui.busy ? "Queue until I close it" : "Queue the run" }}
      </AppButton>
      <AppButton
        variant="primary"
        icon-left="power"
        data-testid="cq-quit"
        @click="emit('quit')"
      >
        {{ comfyui.busy ? "Quit it once its jobs are done" : "Quit ComfyUI and train" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import { VIcon } from "vuetify/components";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { FIT_LABELS, formatDuration, gbTenths } from "../../utils/trainingPlan";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The Fit choice's name: "Best quality". */
  fitLabel: { type: String, default: "" },
  /** MB the run's profile peaks at. */
  needMb: { type: Number, default: 0 },
  /** The plan's `comfyui`: {kept_mb, short_mb, can_quit, busy, beside}. */
  comfyui: { type: Object, required: true },
  /** The run's measured estimate, seconds; null when not known. */
  seconds: { type: Number, default: null },
});

const emit = defineEmits(["close", "queue", "quit", "beside"]);

const queueButton = ref(null);

const runTime = computed(() => (props.seconds != null ? formatDuration(props.seconds) : ""));
const besideTime = computed(() => {
  const estimate = props.comfyui.beside?.estimate;
  return estimate?.basis === "measured" && estimate.seconds != null
    ? formatDuration(estimate.seconds)
    : "";
});

// Queue holds focus: the answer that changes nothing on the machine.
watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    await nextTick();
    queueButton.value?.focus?.();
  },
);
</script>

<style scoped>
.cq-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.cq-text {
  margin: 0;
  font-size: var(--text-sm);
}

.cq-cmp {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--text-sm);
}

.cq-cmp td {
  padding: var(--space-3);
  border-top: 1px solid rgb(var(--v-theme-divider));
  vertical-align: top;
}

.cq-cmp tr:first-child td {
  border-top: none;
}

.cq-cmp td:last-child {
  text-align: right;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  font-size: var(--text-xs);
}

.cq-cmp small {
  display: block;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.cq-notice {
  display: flex;
  gap: var(--space-3);
  align-items: flex-start;
  margin: 0;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  background: rgb(var(--v-theme-input-background));
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
}

.cq-notice .v-icon {
  color: rgb(var(--v-theme-surface-warning));
}

.cq-link {
  align-self: flex-start;
  background: none;
  border: none;
  padding: 0;
  color: inherit;
  font: inherit;
  font-size: var(--text-xs);
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
}

.cq-hint {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
