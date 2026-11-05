<template>
  <!-- Start on a run that does not fit beside what ComfyUI is known to keep
       (#2558). Clear (focused, the key action) has ComfyUI unload its models
       and stay open; Quit, only where PixlStash can end its process, is the
       fallback; Cancel, Escape and the close button go back to the Train
       dialog without starting. Both answers wait for ComfyUI's jobs and close
       this: the run then waits in Tasks, which shows each phase. No @accept:
       Enter presses whichever answer has focus, Clear on open. -->
  <AppDialog
    :open="open"
    :title="`${fitLabel} needs ComfyUI's memory`"
    size="md"
    @close="emit('close')"
  >
    <div class="cq-body">
      <p class="cq-text" data-testid="cq-text">
        This run needs <b>{{ gbTenths(needMb) }} GB</b>; {{ gbTenths(freeMb) }} GB
        is free. ComfyUI holds {{ gbTenths(comfyui.held_mb) }} GB and
        <template v-if="jobs">is running <b>{{ jobsText }}</b>.</template>
        <template v-else>is idle.</template>
      </p>

      <table class="cq-cmp">
        <tbody>
          <tr>
            <td>
              <b>Clear ComfyUI's memory</b>
              <small>Unloads its models; ComfyUI stays open.</small>
            </td>
            <td>{{ when }}</td>
          </tr>
          <tr v-if="comfyui.can_quit">
            <td>
              <b>Quit ComfyUI</b>
              <small>Start it again yourself after the run.</small>
            </td>
            <td>{{ when }}</td>
          </tr>
        </tbody>
      </table>

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
        {{ comfyui.can_quit ? "Either way this" : "This" }} closes and the run
        waits in Tasks, where it can still be cancelled.
        {{
          comfyui.can_quit
            ? "If clearing frees too little, Tasks asks what to do next."
            : "If clearing frees too little, it waits until ComfyUI is closed."
        }}
      </span>
    </div>

    <template #footer>
      <AppButton variant="ghost" data-testid="cq-cancel" @click="emit('close')">
        Cancel
      </AppButton>
      <AppButton
        v-if="comfyui.can_quit"
        icon-left="power"
        data-testid="cq-quit"
        @click="emit('quit')"
      >
        {{ jobs ? "Quit after its jobs" : "Quit ComfyUI and train" }}
      </AppButton>
      <AppButton
        ref="clearButton"
        variant="primary"
        icon-left="broom"
        data-testid="cq-clear"
        @click="emit('clear')"
      >
        {{ jobs ? "Clear after its jobs" : "Clear and train" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import { FIT_LABELS, formatDuration, gbTenths } from "../../utils/trainingPlan";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** The Fit choice's name: "Best quality". */
  fitLabel: { type: String, default: "" },
  /** MB the run's profile peaks at. */
  needMb: { type: Number, default: 0 },
  /**
   * The plan's `comfyui`: {kept_mb, held_mb, short_mb, can_quit, busy, jobs,
   * beside}.
   */
  comfyui: { type: Object, required: true },
});

const emit = defineEmits(["close", "clear", "quit", "beside"]);

const clearButton = ref(null);

const jobs = computed(() => Number(props.comfyui.jobs) || 0);
const jobsText = computed(() => `${jobs.value} ${jobs.value === 1 ? "job" : "jobs"}`);
const when = computed(() => (jobs.value ? `after ${jobsText.value}` : "starts now"));
// short = need - (room - kept), so what is free now, ComfyUI's memory and
// all, is room - held.
const freeMb = computed(() =>
  Math.max(
    0,
    props.needMb -
      (Number(props.comfyui.short_mb) || 0) +
      (Number(props.comfyui.kept_mb) || 0) -
      (Number(props.comfyui.held_mb) || 0),
  ),
);
const besideTime = computed(() => {
  const estimate = props.comfyui.beside?.estimate;
  return estimate?.basis === "measured" && estimate.seconds != null
    ? formatDuration(estimate.seconds)
    : "";
});

// Clear holds focus: the key action, and it never ends ComfyUI.
watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    await nextTick();
    clearButton.value?.focus?.();
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
